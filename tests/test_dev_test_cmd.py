"""CliRunner tests for `dev test` subcommand (Phase 112 Plan 03, SC4).

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

Coverage (D-01..D-05, SC4):
  - Exit-code mapping: 0 clean, 1 on any BAD (incl. chip-ID mismatch), 2 on
    marginal-only, 0 on a non-destructive N<M clean run.
  - TTY vs off-TTY prompt gating (D-02).
  - --destructive confirm + -y/--yes bypass (D-03).
  - Sampler bracketing on --destructive vs standalone read on non-destructive
    (D-04).
  - Dual-artifact write only under --output-dir (D-05).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from firestarter.cli_handlers import AppContext, cli
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.eprom_operations import EpromOperator
from firestarter.firmware import FirmwareManager
from firestarter.hardware import HardwareManager

# M8720 has no chip-id in the DB (id step is always NA -- a mock
# check_eprom_id return has no effect on its verdict) but DOES have a
# supported write/verify/erase set once --destructive is passed (112-02
# SUMMARY: "M8720 ... sampler test seam's default chip from 112-01").
_CHIP_NO_ID = "M8720"
# AS29F002T has a real chip-id in the DB, so a mismatched detected id
# actually closes the destructive gate (112-02 SUMMARY: "Used AS29F002T ...
# when manually verifying the chip-ID-mismatch -> exit 1 path").
_CHIP_WITH_ID = "AS29F002T"


def make_app_context(**overrides: object) -> AppContext:
    """Construct a minimal, hardware-free AppContext for `dev test` tests.

    Mirrors test_validate_family_cmd.py's make_app_context: EpromDatabase
    uses skip_local_override=True and every manager is Mock(spec=...) unless
    the caller overrides it. No real serial port or bench access is ever
    opened (SC4).
    """
    db = overrides.pop("db", None)
    if db is None:
        db = EpromDatabase(skip_local_override=True)
    config_manager = overrides.pop("config_manager", None)
    if config_manager is None:
        config_manager = ConfigManager()
    return AppContext(
        db=db,
        config_manager=config_manager,
        eprom_operator=overrides.pop("eprom_operator", Mock(spec=EpromOperator)),
        hardware_manager=overrides.pop("hardware_manager", Mock(spec=HardwareManager)),
        firmware_manager=overrides.pop("firmware_manager", Mock(spec=FirmwareManager)),
        eprom_presenter=overrides.pop(
            "eprom_presenter", Mock(spec=EpromConsolePresenter)
        ),
    )


def make_clean_operator() -> Mock:
    """A Mock(spec=EpromOperator) whose every dispatched method reports OK.

    check_eprom_id returns (True, None) -- no explicit chip-id disagreement
    (id is NA for chips with no chip-id in the DB, OK for chips whose id
    exists and matches). read/blank-check/write/verify/erase all report
    success so a full sweep (destructive or not) comes back clean (D-01
    exit 0).
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

    A plain int makes every call return the same value (return_value); a
    list makes each successive call return the next value (side_effect) --
    used to simulate a rail sagging across before/after brackets (D-04).
    `read_hardware_revision_value` defaults to a canned coarse-bucket string
    (Phase 112 Plan 04 auto-capture wiring) -- `Mock(spec=HardwareManager)`
    picks it up because the real class now defines the method.
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
    """Context manager forcing the off-TTY branch (D-02)."""
    return patch("firestarter.cli_handlers._is_interactive", return_value=False)


# ---------------------------------------------------------------------------
# D-01: exit-code mapping
# ---------------------------------------------------------------------------


class TestExitCodeMapping:
    """0 clean, 1 on any BAD (incl. chip-ID mismatch), 2 on marginal-only,
    0 on a non-destructive N<M clean run."""

    def test_clean_non_destructive_run_exits_0(self, runner: CliRunner) -> None:
        """A clean non-destructive sweep (OK/NA only) exits 0."""
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(),
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output

    def test_clean_destructive_run_exits_0(self, runner: CliRunner) -> None:
        """A clean --destructive sweep (all steps agree OK) exits 0."""
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(),
        )
        with _off_tty():
            result = runner.invoke(
                cli, ["dev", "test", _CHIP_NO_ID, "--destructive"], obj=app
            )
        assert result.exit_code == 0, result.output

    def test_bad_write_outcome_exits_1(self, runner: CliRunner) -> None:
        """Both write runs agreeing on failure -> BAD -> exit 1 (not marginal)."""
        operator = make_clean_operator()
        operator.write_eprom.return_value = False
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(
                cli, ["dev", "test", _CHIP_NO_ID, "--destructive"], obj=app
            )
        assert result.exit_code == 1, result.output

    def test_marginal_disagreement_exits_2(self, runner: CliRunner) -> None:
        """Write runs disagreeing (True then False) -> marginal -> exit 2."""
        operator = make_clean_operator()
        operator.write_eprom.side_effect = [True, False]
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(
                cli, ["dev", "test", _CHIP_NO_ID, "--destructive"], obj=app
            )
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
            result = runner.invoke(
                cli, ["dev", "test", _CHIP_WITH_ID, "--destructive"], obj=app
            )
        assert result.exit_code == 1, result.output
        operator.write_eprom.assert_not_called()

    def test_non_destructive_n_less_than_m_still_exits_0(
        self, runner: CliRunner
    ) -> None:
        """A non-destructive run (write/erase locked, N < M) with a clean
        result set still exits 0 -- fewer tests ran, but none failed."""
        operator = make_clean_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output
        operator.write_eprom.assert_not_called()
        operator.erase_eprom.assert_not_called()

    def test_non_destructive_run_never_dispatches_verify(
        self, runner: CliRunner
    ) -> None:
        """112-05 SC2/SWEEP-05 regression: a non-destructive run must never
        reach operator.verify_eprom. Removes make_clean_operator()'s usual
        `verify_eprom.return_value = True` masking and replaces it with a
        side_effect that raises if verify is ever dispatched -- under the
        pre-fix (4-step) plan this test fails (verify runs -> AssertionError
        -> BAD -> exit 1); under the fix it passes (verify structurally
        absent from the non-destructive plan -> unreachable -> exit 0)."""
        operator = make_clean_operator()
        operator.verify_eprom.side_effect = AssertionError(
            "verify must not run on a non-destructive plan"
        )
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output
        operator.verify_eprom.assert_not_called()


# ---------------------------------------------------------------------------
# D-02/D-03 (reworked Phase 112 Plan 04): --destructive safety confirm only
# ---------------------------------------------------------------------------
#
# REVERSAL: this class previously tested the interactive tester-input
# collector function alongside the --destructive confirm. That collector is
# gone (operator-approved descope, 112-UAT.md test 2); the ONLY interactive
# input left in the handler is the --destructive safety confirm (SAFE-03),
# which is NOT provenance and is preserved unchanged below.


class TestPromptGating:
    """Off-TTY: no confirm prompt, sweep runs unattended. On-TTY: the
    --destructive confirm gates a destructive run; -y/--yes bypasses it."""

    def test_off_tty_no_confirm_prompt(self, runner: CliRunner, tmp_path: Path) -> None:
        """Off-TTY: --destructive itself is consent -- no confirm is invoked,
        the sweep runs, and the report has no provenance key at all."""
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(),
        )
        with (
            _off_tty(),
            patch("firestarter.cli_handlers.Confirm") as mock_confirm,
        ):
            result = runner.invoke(
                cli,
                [
                    "dev",
                    "test",
                    _CHIP_NO_ID,
                    "--destructive",
                    "--output-dir",
                    str(tmp_path),
                ],
                obj=app,
            )
        assert result.exit_code == 0, result.output
        mock_confirm.ask.assert_not_called()
        data = json.loads((tmp_path / f"dev-test-{_CHIP_NO_ID}.json").read_text())
        assert "provenance" not in data
        assert data["is_submittable"] is True

    def test_on_tty_destructive_confirm_gates(self, runner: CliRunner) -> None:
        """On-TTY, --destructive, confirm accepted: Confirm.ask IS called and
        the sweep proceeds (write is invoked) -- there is no provenance
        prompt to call."""
        operator = make_clean_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with (
            patch("firestarter.cli_handlers._is_interactive", return_value=True),
            patch("firestarter.cli_handlers.Confirm") as mock_confirm,
        ):
            mock_confirm.ask.return_value = True
            result = runner.invoke(
                cli, ["dev", "test", _CHIP_NO_ID, "--destructive"], obj=app
            )
        assert result.exit_code == 0, result.output
        mock_confirm.ask.assert_called_once()
        operator.write_eprom.assert_called()

    def test_on_tty_declining_confirm_aborts_before_write(
        self, runner: CliRunner
    ) -> None:
        """On-TTY, --destructive, confirm declined: command aborts (exit 0,
        chip left untouched) before any operator write call (SAFE-03)."""
        operator = make_clean_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with (
            patch("firestarter.cli_handlers._is_interactive", return_value=True),
            patch("firestarter.cli_handlers.Confirm") as mock_confirm,
        ):
            mock_confirm.ask.return_value = False
            result = runner.invoke(
                cli, ["dev", "test", _CHIP_NO_ID, "--destructive"], obj=app
            )
        assert result.exit_code == 0, result.output
        operator.write_eprom.assert_not_called()

    def test_yes_bypasses_confirm_on_a_tty(self, runner: CliRunner) -> None:
        """-y/--yes on a TTY skips the destructive confirm entirely and the
        write proceeds -- there is no longer any provenance prompt to
        reason about."""
        operator = make_clean_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with (
            patch("firestarter.cli_handlers._is_interactive", return_value=True),
            patch("firestarter.cli_handlers.Confirm") as mock_confirm,
        ):
            result = runner.invoke(
                cli,
                ["dev", "test", _CHIP_NO_ID, "--destructive", "-y"],
                obj=app,
            )
        assert result.exit_code == 0, result.output
        mock_confirm.ask.assert_not_called()
        operator.write_eprom.assert_called()


# ---------------------------------------------------------------------------
# D-04: sampler bracketing on destructive vs standalone read on non-destructive
# ---------------------------------------------------------------------------


class TestSamplerBracketing:
    """Sampler fires around OP_WRITE on a --destructive run; a standalone
    single VPP/VPE read fills the non-split slots on a non-destructive run."""

    def test_destructive_run_fills_split_voltage_slots(self, runner: CliRunner) -> None:
        """--destructive: sampler brackets EACH operator.write_eprom() call,
        filling vpp/vpe_before_mv and vpp/vpe_after_mv from the mock hardware
        manager; the standalone (non-split) slots stay unfilled.

        run_plan's default runs=2 means the OP_WRITE branch calls
        write_eprom twice, and the sampler fires before+after EACH call
        (chip_test.py _dispatch_multi_run) -- 4 total sample_vpp_mv/
        sample_vpe_mv calls, with the LAST before/after pair winning the
        report's single before/after slot (each sampler("before")/
        sampler("after") invocation overwrites the prior one)."""
        operator = make_clean_operator()
        hw = make_hardware_manager(
            vpp_values=[20900, 17400, 20800, 17300],
            vpe_values=[5000, 4900, 4950, 4850],
        )
        app = make_app_context(eprom_operator=operator, hardware_manager=hw)
        with tempfile_output_dir() as out_dir:
            with _off_tty():
                result = runner.invoke(
                    cli,
                    [
                        "dev",
                        "test",
                        _CHIP_NO_ID,
                        "--destructive",
                        "--output-dir",
                        str(out_dir),
                    ],
                    obj=app,
                )
            assert result.exit_code == 0, result.output
            data = json.loads((out_dir / f"dev-test-{_CHIP_NO_ID}.json").read_text())
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

    def test_non_destructive_run_fills_standalone_voltage_slots(
        self, runner: CliRunner
    ) -> None:
        """Non-destructive: no sampler is threaded through run_plan (no write
        step exists to bracket); a standalone single VPP/VPE read fills
        vpp_mv/vpe_mv instead, with before/after left NOT_MEASURED."""
        operator = make_clean_operator()
        hw = make_hardware_manager(vpp_values=12000, vpe_values=5000)
        app = make_app_context(eprom_operator=operator, hardware_manager=hw)
        with tempfile_output_dir() as out_dir:
            with _off_tty():
                result = runner.invoke(
                    cli,
                    ["dev", "test", _CHIP_NO_ID, "--output-dir", str(out_dir)],
                    obj=app,
                )
            assert result.exit_code == 0, result.output
            data = json.loads((out_dir / f"dev-test-{_CHIP_NO_ID}.json").read_text())
        voltage = data["voltage"]
        assert voltage["vpp_mv"] == 12000
        assert voltage["vpe_mv"] == 5000
        assert voltage["vpp_before_mv"] == "not measured"
        assert voltage["vpp_after_mv"] == "not measured"
        assert hw.sample_vpp_mv.call_count == 1
        assert hw.sample_vpe_mv.call_count == 1
        operator.write_eprom.assert_not_called()


# ---------------------------------------------------------------------------
# D-05: dual-artifact write only under --output-dir
# ---------------------------------------------------------------------------


class TestDualArtifactWrite:
    """--output-dir writes exactly dev-test-<chip>.json + .md; no
    --output-dir writes nothing but still renders to stdout."""

    def test_output_dir_writes_exactly_two_hyphenated_files(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(),
        )
        with _off_tty():
            result = runner.invoke(
                cli,
                [
                    "dev",
                    "test",
                    _CHIP_NO_ID,
                    "--output-dir",
                    str(tmp_path),
                ],
                obj=app,
            )
        assert result.exit_code == 0, result.output
        assert sorted(os.listdir(tmp_path)) == [
            f"dev-test-{_CHIP_NO_ID}.json",
            f"dev-test-{_CHIP_NO_ID}.md",
        ]

    def test_no_output_dir_writes_no_files_but_renders_stdout(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(),
        )
        with _off_tty():
            os.chdir(tmp_path)
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output
        assert os.listdir(tmp_path) == []
        assert "dev test" in result.output

    def test_json_artifact_is_report_to_dict(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """The .json artifact body is exactly report.to_dict() (single-source,
        D-05) -- spot-check a handful of top-level keys rather than a second
        hand-maintained field list."""
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(),
        )
        with _off_tty():
            result = runner.invoke(
                cli,
                ["dev", "test", _CHIP_NO_ID, "--output-dir", str(tmp_path)],
                obj=app,
            )
        assert result.exit_code == 0, result.output
        data = json.loads((tmp_path / f"dev-test-{_CHIP_NO_ID}.json").read_text())
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

    def test_hw_revision_auto_captured_end_to_end(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """The mocked hardware manager's read_hardware_revision_value() flows
        through to the rendered report and the .json artifact (Phase 112
        Plan 04 auto-capture wiring, end-to-end)."""
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(hw_revision="Rev 2.0-class"),
        )
        with _off_tty():
            result = runner.invoke(
                cli,
                ["dev", "test", _CHIP_NO_ID, "--output-dir", str(tmp_path)],
                obj=app,
            )
        assert result.exit_code == 0, result.output
        assert "Rev 2.0-class" in result.output
        data = json.loads((tmp_path / f"dev-test-{_CHIP_NO_ID}.json").read_text())
        assert data["auto_capture"]["hw_revision"] == "Rev 2.0-class"

    def test_md_artifact_contains_fenced_json_block(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """The .md artifact is the self-contained issue body: a results table
        plus a fenced ```json``` block (Phase 113 uploads this as-is)."""
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(),
        )
        with _off_tty():
            result = runner.invoke(
                cli,
                ["dev", "test", _CHIP_NO_ID, "--output-dir", str(tmp_path)],
                obj=app,
            )
        assert result.exit_code == 0, result.output
        md_text = (tmp_path / f"dev-test-{_CHIP_NO_ID}.md").read_text()
        assert "```json" in md_text
        assert "| Step | Verdict | Reason |" in md_text


# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------


class tempfile_output_dir:
    """Context manager yielding a fresh tmp_path-equivalent Path, standalone
    from pytest's tmp_path fixture (used inside a test that already consumes
    tmp_path for something else, or wants an explicit `with` block).
    """

    def __enter__(self) -> Path:
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory(prefix="dev_test_cmd_")
        return Path(self._tmpdir.name)

    def __exit__(self, *exc: object) -> None:
        self._tmpdir.cleanup()
