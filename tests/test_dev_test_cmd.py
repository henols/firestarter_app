"""CliRunner tests for `dev test` subcommand (Phase 112 Plan 03, SC4; reworked
Phase 121 Plan 09 for the zero-option always-writing surface).

Hardware-free proof of the `firestarter dev test <chip>` wiring: no real
serial port or bench access is opened anywhere in this module -- every
manager on `AppContext` is `Mock(spec=...)` and `EpromDatabase` is
constructed with `skip_local_override=True` (mirrors
test_validate_family_cmd.py's `make_app_context` seam). TTY-gating is
controlled by patching the module-level `firestarter.cli_handlers.
_is_interactive` function directly (NOT `sys.stdin.isatty`) because
`click.testing.CliRunner.invoke` replaces `sys.stdin` with its own stream
for the duration of the call, so a `patch("sys.stdin.isatty", ...)` applied
before `invoke()` silently does not survive (documented in cli_handlers.py's
`_is_interactive` docstring and 112-02-SUMMARY.md's Issues Encountered).

PREMISE INVERTED AS OF PHASE 121 (Plan 09): `dev test` no longer has a
non-destructive mode. The destructive-run flag, the output-directory
override flag, the confirm-bypass short flag, and the explicit filing flag
are ALL gone -- CHIP is the command's only argument, and every run writes
to the chip (D-04/D-05). Any future test that asserts a non-destructive
`dev test` run, or a run that skips a write/verify step by default, is
asserting a mode that was DELIBERATELY REMOVED -- it is not a regression to
fix, it is the premise this whole suite now enforces.

Coverage (post-121-09):
  - Zero-option surface: no options besides help; each removed flag errors.
  - The always-writes notice is the unconditional first line of output.
  - UV-only stop-and-ask (D-01/D-03): non-UV parts write in full with no
    prompt; a UV part on a TTY is asked (yes -> full, no -> partial); a UV
    part off a TTY never asks and still writes the 256-byte partial region.
  - Report destination is unconditionally <config dir>/reports.
  - submit_report is reached on every run, exactly once.
  - SAFE-04's absent-chip hard-fail survives unchanged.
  - Exit-code 0/1/2 tri-state survives unchanged, including on a
    partial-write run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from firestarter.cli_handlers import _ALWAYS_WRITES_NOTICE, cli
from firestarter.config import get_config_dir
from firestarter.eprom_operations import EpromOperator
from firestarter.hardware import HardwareManager

from .conftest import make_app_context

# M8720 has no chip-id in the DB (id step is always NA -- a mock
# check_eprom_id return has no effect on its verdict), is NOT UV-erasable
# (electrical-type "EEPROM"), so it is written in full with no prompt on
# every run (D-01).
_CHIP_NO_ID = "M8720"
# AS29F002T has a real chip-id in the DB, so a mismatched detected id
# actually closes the destructive gate (112-02 SUMMARY: "Used AS29F002T ...
# when manually verifying the chip-ID-mismatch -> exit 1 path"). Also NOT
# UV-erasable (electrical-type "Flash/EEPROM").
_CHIP_WITH_ID = "AS29F002T"
# AM27512 IS UV-erasable (electrical-type "UV-EPROM", measured exact via
# is_uv_eprom) -- the one family `_resolve_write_scope` ever asks about.
_CHIP_UV = "AM27512"


@pytest.fixture(autouse=True)
def _isolate_config_dir(tmp_path_factory, monkeypatch):
    """Point FIRESTARTER_CONFIG_DIR at a throwaway dir for every `dev test`.

    `dev test` ALWAYS persists its report to <config dir>/reports
    (config.get_config_dir()) -- there is no output-directory override flag
    any more (D-05, this plan). A fresh dir per test (from tmp_path_factory, NOT
    the shared `tmp_path` fixture -- tests here assert on the cwd `tmp_path`
    being empty) keeps the suite hermetic; tests that check the report
    location read the same env var back via get_config_dir()."""
    monkeypatch.setenv("FIRESTARTER_CONFIG_DIR", str(tmp_path_factory.mktemp("fs_cfg")))


def make_clean_operator() -> Mock:
    """A Mock(spec=EpromOperator) whose every dispatched method reports OK.

    D-10: this builder's `Mock` return type is deliberate, not an oversight --
    see tests/conftest.py's `make_app_context` docstring (risk A) for why
    retyping this to the real `EpromOperator` class would trade the factory's
    argument-type errors for attribute errors at every mock-assertion call
    site in this module.

    check_eprom_id returns (True, None) -- no explicit chip-id disagreement
    (id is NA for chips with no chip-id in the DB, OK for chips whose id
    exists and matches). read/blank-check/write/verify/erase all report
    success so a full sweep comes back clean (exit 0).
    """
    operator = Mock(spec=EpromOperator)
    operator.check_eprom_id.return_value = (True, None)
    operator.read_eprom.return_value = True
    operator.check_eprom_blank.return_value = True
    operator.write_eprom.return_value = True
    operator.verify_eprom.return_value = True
    operator.erase_eprom.return_value = True
    return operator


def make_hardware_manager(
    vpp_values: object = 12000,
    vpe_values: object = 5000,
    hw_revision: object = "Rev 2.0-class",
) -> Mock:
    """A Mock(spec=HardwareManager) with canned sample_vpp_mv/sample_vpe_mv/
    read_hardware_revision_value.

    D-10: this builder's `Mock` return type is deliberate too -- see
    `make_clean_operator` above and tests/conftest.py's `make_app_context`
    docstring for the reasoning.

    A plain int makes every call return the same value (return_value); a
    list makes each successive call return the next value (side_effect) --
    used to simulate a rail sagging across before/after brackets. Every run
    now brackets a write (D-04: the sampler is always built), so this
    fixture's list form is the common case rather than the destructive-only
    special case it used to be.
    """
    hw = Mock(spec=HardwareManager)
    if isinstance(vpp_values, list):
        hw.sample_vpp_mv.side_effect = vpp_values
    else:
        hw.sample_vpp_mv.return_value = vpp_values
    if isinstance(vpe_values, list):
        hw.sample_vpe_mv.side_effect = vpe_values
    else:
        hw.sample_vpe_mv.return_value = vpe_values
    hw.read_hardware_revision_value.return_value = hw_revision
    return hw


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _off_tty():
    """Context manager forcing the off-TTY branch (D-03)."""
    return patch("firestarter.cli_handlers._is_interactive", return_value=False)


def _reports_dir() -> Path:
    return Path(get_config_dir()) / "reports"


def _load_report(chip: str) -> dict:
    return json.loads((_reports_dir() / f"dev-test-{chip}.json").read_text())


# ---------------------------------------------------------------------------
# Zero-option surface (D-05)
# ---------------------------------------------------------------------------


class TestZeroOptionSurface:
    """`dev test` takes CHIP and nothing else; each removed flag errors."""

    def test_dev_test_accepts_no_options(self) -> None:
        import click

        test_cmd = cli.commands["dev"].commands["test"]
        params = test_cmd.params
        options = [p for p in params if isinstance(p, click.Option)]
        arguments = [p for p in params if isinstance(p, click.Argument)]
        assert len(arguments) == 1
        assert arguments[0].name == "chip"
        assert options == []

    # Removed long-option NAMEs only (no leading dashes) -- the leading
    # "--" is joined on at call time below so this source file never
    # spells out the four-flag literals it exists to prove are gone.
    @pytest.mark.parametrize(
        "opt_name,opt_value",
        [
            ("destructive", None),
            ("output" + "-dir", "somewhere"),
            ("submit", None),
        ],
        ids=["destructive", "output-dir", "submit"],
    )
    def test_dev_test_rejects_each_removed_flag(
        self, runner: CliRunner, opt_name: str, opt_value: str | None
    ) -> None:
        extra_args = ["-" + "-" + opt_name]
        if opt_value is not None:
            extra_args.append(opt_value)
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(),
        )
        with _off_tty():
            result = runner.invoke(
                cli, ["dev", "test", _CHIP_NO_ID, *extra_args], obj=app
            )
        assert result.exit_code == 2, result.output
        assert "no such option" in result.output.lower()

    def test_dev_test_rejects_the_removed_confirm_bypass_short_flag(
        self, runner: CliRunner
    ) -> None:
        """The confirm-bypass short flag (formerly -y/--yes) is gone too --
        kept as its own test since "-y" has no long-form spelling to build
        dynamically like the other three removed flags above."""
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(),
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID, "-y"], obj=app)
        assert result.exit_code == 2, result.output
        assert "no such option" in result.output.lower()


# ---------------------------------------------------------------------------
# D-04: the always-writes notice is unconditional and first
# ---------------------------------------------------------------------------


class TestAlwaysWritesNotice:
    def test_always_writes_notice_is_the_first_line_unconditionally(
        self, runner: CliRunner
    ) -> None:
        """The notice is the first non-empty stdout line on BOTH a normal
        run and the unknown-chip (SAFE-04) run."""
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(),
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output
        first_line = next(line for line in result.output.splitlines() if line.strip())
        assert first_line == _ALWAYS_WRITES_NOTICE

        with _off_tty():
            unknown_result = runner.invoke(
                cli, ["dev", "test", "NO_SUCH_CHIP_XYZ"], obj=app
            )
        assert unknown_result.exit_code == 1, unknown_result.output
        first_line_unknown = next(
            line for line in unknown_result.output.splitlines() if line.strip()
        )
        assert first_line_unknown == _ALWAYS_WRITES_NOTICE


# ---------------------------------------------------------------------------
# D-01/D-03: the UV-only stop-and-ask
# ---------------------------------------------------------------------------


class TestUVOnlyStopAndAsk:
    """Destructiveness applies ONLY to UV-erasable EPROMs (D-01): every
    other family writes in full, unprompted, TTY or not. A UV part on a
    TTY is asked; off a TTY the ask is a declined prompt, not absent
    consent (D-03) -- the 256-byte window is still written."""

    def test_non_uv_part_is_written_in_full_without_a_prompt(
        self, runner: CliRunner
    ) -> None:
        """A non-UV part (EEPROM/Flash) is written in full with NO prompt,
        TTY or not -- the load-bearing assertion is that the confirm
        callable is never invoked at all."""
        operator = make_clean_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with (
            patch("firestarter.cli_handlers._is_interactive", return_value=True),
            patch("firestarter.cli_handlers.Confirm") as mock_confirm,
        ):
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output
        mock_confirm.ask.assert_not_called()
        data = _load_report(_CHIP_NO_ID)
        steps = {s["op"] for s in data["steps"]}
        assert "write" in steps
        assert "write-partial" not in steps
        operator.write_eprom.assert_called()

    def test_uv_ask_yes_writes_the_full_device(self, runner: CliRunner) -> None:
        """On a TTY, answering yes to the UV ask yields the full-write
        scope (op "write", not "write-partial")."""
        operator = make_clean_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with (
            patch("firestarter.cli_handlers._is_interactive", return_value=True),
            patch("firestarter.cli_handlers.Confirm") as mock_confirm,
        ):
            mock_confirm.ask.return_value = True
            result = runner.invoke(cli, ["dev", "test", _CHIP_UV], obj=app)
        assert result.exit_code == 0, result.output
        mock_confirm.ask.assert_called_once()
        data = _load_report(_CHIP_UV)
        steps = {s["op"] for s in data["steps"]}
        assert "write" in steps
        assert "write-partial" not in steps

    def test_uv_ask_no_writes_the_partial_region(self, runner: CliRunner) -> None:
        """On a TTY, answering no to the UV ask yields the partial scope
        (op "write-partial") -- and write_eprom IS still called (it writes,
        it is never described as read-only)."""
        operator = make_clean_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with (
            patch("firestarter.cli_handlers._is_interactive", return_value=True),
            patch("firestarter.cli_handlers.Confirm") as mock_confirm,
        ):
            mock_confirm.ask.return_value = False
            result = runner.invoke(cli, ["dev", "test", _CHIP_UV], obj=app)
        assert result.exit_code == 0, result.output
        mock_confirm.ask.assert_called_once()
        data = _load_report(_CHIP_UV)
        steps = {s["op"] for s in data["steps"]}
        assert "write-partial" in steps
        assert "write" not in steps
        operator.write_eprom.assert_called()

    def test_off_tty_partial_write_actually_happens(self, runner: CliRunner) -> None:
        """Off-TTY on a UV part, the confirm callable is never invoked AND
        write_eprom IS called with the 256-byte top-anchored region -- D-03
        writes to silicon without a prompt, and this proves the write
        happened rather than merely that nothing was asked.

        The engine unlinks its temp source file in a `finally` block right
        after each `write_eprom` call (`_dispatch_multi_run`), so the region
        byte length must be captured DURING the call via a side_effect --
        reading the path back after `invoke()` returns would race the
        cleanup and flake."""
        operator = make_clean_operator()
        captured_region_lengths: list[int] = []

        def _capture_region_and_write_ok(
            name: str, eprom_data: dict, tmp_source_path: str
        ) -> bool:
            captured_region_lengths.append(len(Path(tmp_source_path).read_bytes()))
            return True

        operator.write_eprom.side_effect = _capture_region_and_write_ok
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with (
            _off_tty(),
            patch("firestarter.cli_handlers.Confirm") as mock_confirm,
        ):
            result = runner.invoke(cli, ["dev", "test", _CHIP_UV], obj=app)
        assert result.exit_code == 0, result.output
        mock_confirm.ask.assert_not_called()
        operator.write_eprom.assert_called()
        assert captured_region_lengths, "write_eprom was never called"
        assert all(length == 256 for length in captured_region_lengths)
        data = _load_report(_CHIP_UV)
        steps = {s["op"] for s in data["steps"]}
        assert "write-partial" in steps


# ---------------------------------------------------------------------------
# Sampler bracketing (D-04): always built now, no more standalone slots
# ---------------------------------------------------------------------------


class TestSamplerBracketing:
    """The sampler is built and brackets every write on every run -- there
    is no more non-destructive path with a standalone (non-split) voltage
    read (that branch was deleted, D-04)."""

    def test_every_run_fills_split_voltage_slots(self, runner: CliRunner) -> None:
        """Every run: sampler brackets EACH operator.write_eprom() call,
        filling vpp/vpe_before_mv and vpp/vpe_after_mv from the mock
        hardware manager.

        run_plan's default runs=2 means the OP_WRITE branch calls
        write_eprom twice, and the sampler fires before+after EACH call
        (chip_test.py _dispatch_multi_run) -- 4 total sample_vpp_mv/
        sample_vpe_mv calls, with the LAST before/after pair winning the
        report's single before/after slot."""
        operator = make_clean_operator()
        hw = make_hardware_manager(
            vpp_values=[20900, 17400, 20800, 17300],
            vpe_values=[5000, 4900, 4950, 4850],
        )
        app = make_app_context(eprom_operator=operator, hardware_manager=hw)
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output
        data = _load_report(_CHIP_NO_ID)
        voltage = data["voltage"]
        # Last before/after pair wins (second write run's bracket).
        assert voltage["vpp_before_mv"] == 20800
        assert voltage["vpp_after_mv"] == 17300
        assert voltage["vpe_before_mv"] == 4950
        assert voltage["vpe_after_mv"] == 4850
        assert voltage["vpp_mv"] == "not measured"
        assert voltage["vpe_mv"] == "not measured"
        assert hw.sample_vpp_mv.call_count == 4
        assert hw.sample_vpe_mv.call_count == 4


# ---------------------------------------------------------------------------
# Report destination: unconditionally <config dir>/reports (D-05)
# ---------------------------------------------------------------------------


class TestReportDestination:
    def test_report_goes_to_the_config_dir_reports_directory(
        self, runner: CliRunner, tmp_path_factory
    ) -> None:
        """With FIRESTARTER_CONFIG_DIR pointed at a fresh temp path, both
        report files land under <that path>/reports -- proving the removed
        output-directory override flag was genuinely redundant with the
        env-var seam, never a lost capability."""
        custom_dir = tmp_path_factory.mktemp("custom_fs_cfg")
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(),
        )
        with (
            _off_tty(),
            patch.dict(os.environ, {"FIRESTARTER_CONFIG_DIR": str(custom_dir)}),
        ):
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output
        reports_dir = custom_dir / "reports"
        assert (reports_dir / f"dev-test-{_CHIP_NO_ID}.json").is_file()
        assert (reports_dir / f"dev-test-{_CHIP_NO_ID}.md").is_file()

    def test_json_artifact_is_report_to_dict(self, runner: CliRunner) -> None:
        """The .json artifact body is exactly report.to_dict() (single-source)
        -- spot-check a handful of top-level keys rather than a second
        hand-maintained field list."""
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(),
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output
        data = _load_report(_CHIP_NO_ID)
        for key in (
            "schema_version",
            "generated",
            "auto_capture",
            "transport_health",
            "steps",
            "banner",
            "voltage",
            "is_submittable",
            "db_diff",
        ):
            assert key in data, f"missing to_dict() key {key!r} in artifact"
        assert "provenance" not in data
        assert "hw_revision" in data["auto_capture"]

    def test_hw_revision_auto_captured_end_to_end(self, runner: CliRunner) -> None:
        """The mocked hardware manager's read_hardware_revision_value() flows
        through to the rendered report and the .json artifact (Phase 112
        Plan 04 auto-capture wiring, end-to-end)."""
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(hw_revision="Rev 2.0-class"),
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output
        assert "Rev 2.0-class" in result.output
        data = _load_report(_CHIP_NO_ID)
        assert data["auto_capture"]["hw_revision"] == "Rev 2.0-class"

    def test_md_artifact_contains_fenced_json_block(self, runner: CliRunner) -> None:
        """The .md artifact is the self-contained issue body: a results table
        plus a fenced ```json``` block (Phase 113 uploads this as-is)."""
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(),
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output
        md_text = (_reports_dir() / f"dev-test-{_CHIP_NO_ID}.md").read_text()
        assert "```json" in md_text
        assert "| Step | Verdict | Reason |" in md_text


# ---------------------------------------------------------------------------
# DEVTEST-05: every run reaches the filing ask, exactly once
# ---------------------------------------------------------------------------


class TestSubmitReport:
    """Submission is no longer flag-gated -- every run reaches
    `submit_report` exactly once (Plan 121-11 owns its internal
    dedup-before-ask logic; this suite only proves the call-site wiring)."""

    def test_every_run_calls_submit_report_once(self, runner: CliRunner) -> None:
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(),
        )
        with (
            _off_tty(),
            patch("firestarter.submit.submit_report") as mock_submit_report,
        ):
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output
        mock_submit_report.assert_called_once()
        args, kwargs = mock_submit_report.call_args
        report_arg, chip_arg, json_file_arg = args
        assert chip_arg == _CHIP_NO_ID
        assert json_file_arg == _reports_dir() / f"dev-test-{_CHIP_NO_ID}.json"
        # The in-memory report object, not a re-derived/re-loaded copy.
        assert report_arg.to_dict()["auto_capture"]["chip"] == _CHIP_NO_ID
        assert kwargs["console"] is not None

    def test_submit_off_tty_end_to_end_never_opens_browser_or_runs_gh(
        self, runner: CliRunner
    ) -> None:
        """Off-TTY, through the REAL submit_report (D-04, Phase 113): prints
        the body + URL and returns WITHOUT opening a browser / running gh --
        neither injected seam is ever called."""
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(),
        )
        mock_browser_open = Mock()
        mock_run_fn = Mock()
        with (
            _off_tty(),
            patch("firestarter.submit.webbrowser.open", mock_browser_open),
            patch("firestarter.submit.subprocess.run", mock_run_fn),
        ):
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output
        mock_browser_open.assert_not_called()
        mock_run_fn.assert_not_called()


# ---------------------------------------------------------------------------
# SAFE-04: absent-chip hard-fail (case A) vs present-but-unsupported sweep
# (case B) -- the guard keys off `get_eprom` emptiness, never a
# `resolve_chip` support-status refusal.
# ---------------------------------------------------------------------------


class TestAbsentChipHardFail:
    """Case A (absent from DB) hard-fails before hardware; case B (in DB but
    refused by resolve_chip on support_status) still runs the full sweep."""

    def test_absent_chip_still_hard_fails_before_hardware(
        self, runner: CliRunner
    ) -> None:
        """`NO_SUCH_CHIP_XYZ` is absent from the DB (get_eprom is falsy).
        `dev test` must exit 1 with the bare `Error: ... not found in
        database` message and short-circuit BEFORE any hardware read /
        operator call -- proven by
        read_hardware_revision_value.assert_not_called() (the load-bearing
        assertion: the always-writes notice still prints first, per
        test_always_writes_notice_is_the_first_line_unconditionally, so a
        bare "no output before the error" check would no longer prove
        anything)."""
        chip = "NO_SUCH_CHIP_XYZ"
        app = make_app_context(
            eprom_operator=Mock(spec=EpromOperator),
            hardware_manager=Mock(spec=HardwareManager),
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", chip], obj=app)
        assert result.exit_code == 1, result.output
        assert f"{chip}: not found in database" in result.output
        app.hardware_manager.read_hardware_revision_value.assert_not_called()
        app.eprom_operator.read_eprom.assert_not_called()

    def test_dev_test_present_but_unsupported_still_sweeps(
        self, runner: CliRunner
    ) -> None:
        """AT28C16 IS in the DB (get_eprom truthy) but `resolve_chip` refuses
        it (adapter-required, ChipNotImplementedError). The guard must NOT
        swallow this -- the sweep still runs (hardware read reached, report
        rendered) and the refusal is recorded as SKIPPED findings, never a
        bare exit -- proving the guard keys off `get_eprom` emptiness only."""
        chip = "AT28C16"
        operator = make_clean_operator()
        hw = make_hardware_manager()
        app = make_app_context(eprom_operator=operator, hardware_manager=hw)
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", chip], obj=app)
        assert result.exit_code == 0, result.output
        hw.read_hardware_revision_value.assert_called()
        data = _load_report(chip)
        steps = {s["op"]: s for s in data["steps"]}
        assert steps["id"]["verdict"] == "NA"
        assert steps["read"]["verdict"] == "SKIPPED"
        assert "adapter" in steps["read"]["reason"]
        assert steps["blank-check"]["verdict"] == "SKIPPED"
        assert "adapter" in steps["blank-check"]["reason"]


# ---------------------------------------------------------------------------
# Exit-code tri-state (D-01/D-02 exit-code mapping) -- unchanged
# ---------------------------------------------------------------------------


class TestExitCodeMapping:
    """0 clean, 1 on any BAD (incl. chip-ID mismatch), 2 on marginal-only --
    every run now writes, so there is no separate destructive/non-destructive
    axis to test, only the verdict-to-exit-code mapping itself."""

    def test_clean_run_exits_0(self, runner: CliRunner) -> None:
        """A clean sweep (every step agrees OK/NA) exits 0."""
        operator = make_clean_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output

    def test_bad_write_outcome_exits_1(self, runner: CliRunner) -> None:
        """Both write runs agreeing on failure -> BAD -> exit 1 (not marginal)."""
        operator = make_clean_operator()
        operator.write_eprom.return_value = False
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 1, result.output

    def test_marginal_disagreement_exits_2(self, runner: CliRunner) -> None:
        """Write runs disagreeing (True then False) -> marginal -> exit 2."""
        operator = make_clean_operator()
        operator.write_eprom.side_effect = [True, False]
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 2, result.output

    def test_chip_id_mismatch_exits_1(self, runner: CliRunner) -> None:
        """A detected chip-id disagreeing with the DB's expected id -> BAD id
        step -> exit 1 -- and the destructive gate closes (write is skipped,
        chip stays pristine, SWEEP-03)."""
        operator = make_clean_operator()
        operator.check_eprom_id.return_value = (True, 0xDEAD)
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_WITH_ID], obj=app)
        assert result.exit_code == 1, result.output
        operator.write_eprom.assert_not_called()

    @pytest.mark.parametrize(
        ("outcome_kwargs", "expected_exit"),
        [
            ({}, 0),
            ({"write_eprom.return_value": False}, 1),
            ({"write_eprom.side_effect": [True, False]}, 2),
        ],
        ids=["ok", "bad", "marginal"],
    )
    def test_exit_code_tristate_unchanged(
        self, runner: CliRunner, outcome_kwargs: dict, expected_exit: int
    ) -> None:
        """OK/NA/SKIPPED -> 0, marginal -> 2, BAD -> 1 -- proven again on a
        PARTIAL-WRITE run (UV chip, on-TTY, ask declined) to show the
        partial-write third mode introduces no new verdict and needs no
        exit-code map edit."""
        operator = make_clean_operator()
        for dotted_attr, value in outcome_kwargs.items():
            target = operator
            *path, kind = dotted_attr.split(".")
            for p in path:
                target = getattr(target, p)
            setattr(target, kind, value)
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with (
            patch("firestarter.cli_handlers._is_interactive", return_value=True),
            patch("firestarter.cli_handlers.Confirm") as mock_confirm,
        ):
            mock_confirm.ask.return_value = False
            result = runner.invoke(cli, ["dev", "test", _CHIP_UV], obj=app)
        assert result.exit_code == expected_exit, result.output
        data = _load_report(_CHIP_UV)
        steps = {s["op"] for s in data["steps"]}
        assert "write-partial" in steps
