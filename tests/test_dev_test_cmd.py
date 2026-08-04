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

import inspect
import json
import os
import re
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from firestarter.chip_test import (
    _SDP_LEG_OPS,
    OP_WRITE,
    OP_WRITE_PARTIAL,
    SDP_HOLD_HELD,
    SDP_HOLD_NOT_HELD,
    SDP_HOLD_NOT_RUN,
    VERDICT_OK,
    StepResult,
    derive_plan,
    run_plan,
)
from firestarter.cli_handlers import (
    _ALWAYS_WRITES_NOTICE,
    _ALWAYS_WRITES_PASS_COUNT,
    _SDP_RECOVERY_LOUD,
    _SDP_RECOVERY_NEUTRAL,
    SDP_RECOVERY_CONSTANT_NAMES,
    _dev_test_exit_code,
    cli,
)
from firestarter.config import get_config_dir
from firestarter.constants import FLAG_SKIP_SDP_UNLOCK
from firestarter.database import EpromDatabase
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import ChipNotFoundError
from firestarter.hardware import HardwareManager

from .conftest import make_app_context

# A real, on-disk-database instance (skip_local_override=True: no
# ~/.firestarter override, no serial) -- the same module-level idiom
# test_chip_test.py/test_chip_test_sdp_leg.py already use for a
# derive_plan(...)-against-real-data proof (v1.30 Phase 134, plan 134-08).
_REAL_DB = EpromDatabase(skip_local_override=True)

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
# AT28C256 is one of the v1.30 milestone's 43 measured SDP-ALLOW chips
# (sdp_capability() returns True) -- verified at plan time to resolve
# through resolve_chip as algorithm 13 (SDP_PROTOCOL_ID), chip-id 0,
# memory-size 32768. NOT UV-erasable (electrical-type "EEPROM"), so it
# writes in full with no prompt, same as _CHIP_NO_ID/_CHIP_WITH_ID above --
# the only difference that matters here is that derive_plan appends the
# six-step SDP leg (D-06) after the four shipped ops for an ALLOW chip.
_CHIP_ALLOW = "AT28C256"


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


def make_leaked_lock_operator(
    *,
    write_outcomes: list[bool] | None = None,
    sdp_lock_ok: bool = True,
    sdp_unlock_ok: bool = True,
) -> Mock:
    """A read-back-capable ALLOW-chip operator (v1.30 Phase 134 plan 134-05,
    LEG-06) whose `write_eprom` genuinely PERSISTS the bytes it is given and
    whose `read_eprom` returns whatever was most recently persisted --
    `Mock(spec=EpromOperator)` (the REAL class), so `sdp_lock`/`sdp_unlock`
    exist automatically and every `assert_not_called()`/`assert_called()`
    site needs no builder change (D-10's reasoning, same as
    `make_clean_operator` above).

    This is a SEPARATE double from both `make_clean_operator` above (whose
    `read_eprom` writes no file at all -- every oracle-adjacent assertion
    would silently degrade to the length gate, 134-02-SUMMARY.md's own
    finding) and `test_chip_test_sdp_leg.py::_readback_operator` (whose
    read-back is a single STATIC payload for every call -- unusable here
    because the six-step leg's own baseline steps legitimately expect
    DIFFERENT read-backs, B then A, before write-inhibited's own
    expectation of "unchanged" -- A -- is tested). Tracking the actual
    write state instead makes every step's read-back correct by
    construction, regardless of call count or step order.

    `sdp_lock`/`sdp_unlock` are pure bookkeeping here -- nothing in this
    double actually enforces a lock, so a `write_eprom` call issued AFTER a
    successful `sdp_lock` still lands for real. Driving the real CLI end to
    end against this operator is what reproduces LEG-06's exact hazard: the
    lock reports HELD (`sdp_lock` returns `sdp_lock_ok`), yet the inhibited
    write still landed -- no fixture in this milestone can simulate a
    genuinely locked die (the Evidence Ceiling), so this is the closest
    honest proxy: a write path nothing actually gates.

    `write_outcomes`, if given, overrides `write_eprom`'s own RETURN VALUE
    by call index (0-based) while STILL persisting the bytes -- used only
    to manufacture the shipped `write` step's own marginal disagreement
    (the mixed BAD+marginal pin) without disturbing the state-tracking
    read-back the SDP leg's own verdicts depend on.
    """
    state: dict[str, bytes] = {"data": b""}
    calls = {"write": 0}

    def _write(name, eprom_data, source_path, flags=0):
        idx = calls["write"]
        calls["write"] += 1
        state["data"] = Path(source_path).read_bytes()
        if write_outcomes is not None and idx < len(write_outcomes):
            return write_outcomes[idx]
        return True

    def _read(name, eprom_data, output_file=None, **kwargs):
        if output_file is not None:
            Path(output_file).write_bytes(state["data"])
        return True

    operator = Mock(spec=EpromOperator)
    operator.check_eprom_id.return_value = (True, None)
    operator.check_eprom_blank.return_value = True
    operator.verify_eprom.return_value = True
    operator.erase_eprom.return_value = True
    operator.write_eprom.side_effect = _write
    operator.read_eprom.side_effect = _read
    operator.sdp_lock.return_value = sdp_lock_ok
    operator.sdp_unlock.return_value = sdp_unlock_ok
    return operator


def make_held_lock_operator(
    *,
    sdp_lock_ok: bool = True,
    sdp_unlock_ok: bool = True,
) -> Mock:
    """A read-back-capable ALLOW-chip operator simulating a GENUINELY HELD
    SDP lock (v1.30 Phase 134 plan 134-07, LEG-12's HELD case): every
    `write_eprom` call persists the bytes it is given EXCEPT the one call
    carrying `FLAG_SKIP_SDP_UNLOCK` (`_dispatch_sdp_leg`'s `OP_WRITE_INHIBITED`
    arm sets this flag on that call ONLY, per D-01) -- that call returns
    `True` (the state machine completed and the ack was observed, D-01's
    precondition signal) but does NOT persist, simulating a die that refuses
    the write internally. `read_eprom` always returns whatever was most
    recently persisted, so the inhibited step's own read-back stays pattern
    A (unchanged) -- the oracle's `HELD` verdict, per D-03's `(True, A) ->
    OK` arm.

    A SEPARATE double from `make_leaked_lock_operator` above (whose
    `write_eprom` persists EVERY call unconditionally, including the
    inhibited one -- LEG-06's `NOT-HELD` shape). No fixture in this
    milestone can simulate a genuinely locked die (the Evidence Ceiling,
    `.planning/REQUIREMENTS.md`); this is the closest honest proxy for the
    opposite outcome from `make_leaked_lock_operator`.
    """
    state: dict[str, bytes] = {"data": b""}

    def _write(name, eprom_data, source_path, flags=0):
        payload = Path(source_path).read_bytes()
        if flags & FLAG_SKIP_SDP_UNLOCK:
            # The inhibited-write call (D-01's one narrowing): the part
            # refuses the write internally -- state stays unchanged -- but
            # the state machine still completes and the ack is observed.
            return True
        state["data"] = payload
        return True

    def _read(name, eprom_data, output_file=None, **kwargs):
        if output_file is not None:
            Path(output_file).write_bytes(state["data"])
        return True

    operator = Mock(spec=EpromOperator)
    operator.check_eprom_id.return_value = (True, None)
    operator.check_eprom_blank.return_value = True
    operator.verify_eprom.return_value = True
    operator.erase_eprom.return_value = True
    operator.write_eprom.side_effect = _write
    operator.read_eprom.side_effect = _read
    operator.sdp_lock.return_value = sdp_lock_ok
    operator.sdp_unlock.return_value = sdp_unlock_ok
    return operator


def make_clean_notrun_operator() -> Mock:
    """An ALLOW-chip operator whose `write_eprom` raises `ChipNotFoundError`
    on every call (v1.30 Phase 134 plan 134-07, D-15 item 1's fixture): the
    ONE route that puts the SDP oracle into `NOT-RUN` with ZERO `BAD`/
    `marginal` verdicts ANYWHERE in the run, so D-15's exit floor's own
    contribution is observable in isolation from D-14's BAD-outranks-
    marginal precedence (the BAD+NOT-RUN pin below exercises that
    interaction separately).

    `ChipNotFoundError` (unlike `ChipNotImplementedError`, ALSO raiseable
    here but a subclass of `EpromOperationError` and so caught by that
    EARLIER, BAD-mapping `except` clause first -- measured, not assumed)
    is a bare `Exception` subclass, so `_run_step`'s belt-and-suspenders
    `except (ChipNotImplementedError, ChipNotFoundError)` clause -- written
    for "a resolve-time-only exception raised instead during dispatch" --
    is the one reached, mapping the step to `SKIPPED` via `_skip_result`,
    never `BAD`. Every `write_eprom`-dispatched step (the shipped `write`,
    both baseline directions) SKIPs the same way; `_baseline_closes_sdp_gate`
    treats `SKIPPED` as gate-closing exactly like `BAD`/`marginal` (D-08's
    "a contact fault is as disqualifying as a dead write path"), so the
    four `_SDP_LEG_GATED_OPS` -- including `write-inhibited` -- SKIP too,
    all with ZERO `BAD`/`marginal` in the whole run. `read`/`verify`
    (neither dispatches through `write_eprom`) stay `OK`.
    """
    operator = Mock(spec=EpromOperator)
    operator.check_eprom_id.return_value = (True, None)
    operator.check_eprom_blank.return_value = True
    operator.write_eprom.side_effect = ChipNotFoundError(
        "simulated: operation not implemented on this host build (test fixture)"
    )
    operator.verify_eprom.return_value = True
    operator.erase_eprom.return_value = True
    operator.read_eprom.return_value = True
    return operator


def make_restore_failed_operator() -> Mock:
    """A read-back-capable ALLOW-chip operator whose write_eprom persists
    genuinely for every call EXCEPT the LAST one (v1.30 Phase 134 plan
    134-08, D-12's LOUD form, trigger 2): the lock IS emitted (the leg's
    baseline/inhibited/unlock steps all dispatch for real, same as
    `make_leaked_lock_operator`), but the run does NOT itself confirm the
    part writable again -- `write-restored`'s own read-back stays whatever
    the second-to-last call left behind, never pattern A.

    A full ALLOW-chip run makes exactly `_ALWAYS_WRITES_PASS_COUNT`
    `write_eprom` calls in a fixed order (the shipped `write` step's two
    runs, then the leg's four single-run writes: baseline-b, baseline-a,
    inhibited, restored -- the SAME order `_ALWAYS_WRITES_PASS_COUNT`'s
    own derivation proves) -- so the LAST call by GLOBAL index is always
    `write-restored`, regardless of chip. This is a SEPARATE double from
    `make_leaked_lock_operator` (whose every call, including the last,
    persists for real -- so its `write-restored` step genuinely succeeds
    and its recovery line is NEUTRAL, not LOUD; this is the case a
    whole-run "lock leaked" fixture cannot itself produce).
    """
    state: dict[str, bytes] = {"data": b""}
    calls = {"write": 0}

    def _write(name, eprom_data, source_path, flags=0):
        idx = calls["write"]
        calls["write"] += 1
        if idx < _ALWAYS_WRITES_PASS_COUNT - 1:
            state["data"] = Path(source_path).read_bytes()
        # else: the LAST (write-restored) call does not persist -- the
        # part's read-back stays at whatever the second-to-last call left.
        return True

    def _read(name, eprom_data, output_file=None, **kwargs):
        if output_file is not None:
            Path(output_file).write_bytes(state["data"])
        return True

    operator = Mock(spec=EpromOperator)
    operator.check_eprom_id.return_value = (True, None)
    operator.check_eprom_blank.return_value = True
    operator.verify_eprom.return_value = True
    operator.erase_eprom.return_value = True
    operator.write_eprom.side_effect = _write
    operator.read_eprom.side_effect = _read
    operator.sdp_lock.return_value = True
    operator.sdp_unlock.return_value = True
    return operator


def _normalize_console_text(output: str) -> str:
    """Strip Rich's box-drawing borders and collapse whitespace so a
    substring assertion survives BOTH column padding and Rich's own
    word-wrapping of a long cell (e.g. `sdp_hold_state`'s `NOT-RUN` reason,
    which wraps across three console lines at the default width) into one
    logical line. Presence-only -- never used for a byte-exact assertion.
    """
    stripped = re.sub(r"[│┃┏┓┗┛┡┩┳┻╇━┌┐└┘├┤┬┴┼─]", " ", output)
    return " ".join(stripped.split())


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


# ---------------------------------------------------------------------------
# D-14 / LEG-06 -- BAD outranks marginal in the exit code, end to end
# (v1.30 Phase 134 plan 134-05). `pytest -k "exit"` selects this class per
# 134-VALIDATION.md's LEG-06 command; `-k "lock_leaked"` selects the
# discharging test specifically.
# ---------------------------------------------------------------------------


class TestExitPrecedenceLeg06:
    """Before D-14, `dev test`'s exit computation was a bare numeric maximum
    over each step's exit-code contribution. Because `_VERDICT_EXIT_CODES`
    maps `marginal -> 2` and `BAD -> 1`, that maximum picked 2 whenever both
    verdicts were present in one run -- marginal's code is numerically
    larger than BAD's, so the milestone's headline finding (a leaked SDP
    lock) could arrive wearing the inconclusive exit code. `_overall_exit_code`
    (D-14) replaces it with explicit precedence: BAD outranks marginal
    outranks clean, proven here end to end through the real CLI, never by
    calling the helper directly."""

    def test_leaked_lock_exits_1(self, runner: CliRunner) -> None:
        """LEG-06's discharging test (134-02 proved the engine half; this is
        the exit-code half). A write that unexpectedly succeeds after the
        SDP lock reports BAD on `write-inhibited` and exits 1 -- never
        SKIPPED, NA, or OK.

        The exit-code assertion ALONE would not discharge LEG-06: a
        laundering implementation could satisfy `exit_code == 1` via an
        unrelated BAD step (e.g. a mismatched chip ID) while quietly
        reporting the leaked write itself as SKIPPED/NA/OK. The verdict
        assertion on `write-inhibited`'s own JSON artifact entry is what
        closes that route -- and the `sdp_unlock` assertion proves the part
        is not left locked even though the lock leaked.
        """
        operator = make_leaked_lock_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        assert result.exit_code == 1, result.output
        data = _load_report(_CHIP_ALLOW)
        steps = {s["op"]: s for s in data["steps"]}
        assert steps["write-inhibited"]["verdict"] == "BAD", steps["write-inhibited"]
        operator.sdp_unlock.assert_called()

    def test_mixed_bad_and_marginal_exits_1_not_2(self, runner: CliRunner) -> None:
        """D-14's own acceptance criterion: a run containing BOTH a BAD step
        (the leaked lock, `write-inhibited`) and a `marginal` step (the
        shipped `write` op's two runs disagreeing, the SAME mechanism
        `test_marginal_disagreement_exits_2` uses) exits 1, never 2.

        Driven end to end through the real CLI/`run_plan` wiring -- not by
        calling `_overall_exit_code` directly -- so this pin covers the
        wiring, not just the helper. Before D-14 (a bare numeric maximum
        over per-step exit codes), this exact run exited 2: marginal's
        code (2) is numerically larger than BAD's (1).
        """
        operator = make_leaked_lock_operator(write_outcomes=[True, False])
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        assert result.exit_code == 1, result.output
        data = _load_report(_CHIP_ALLOW)
        steps = {s["op"]: s for s in data["steps"]}
        assert steps["write"]["verdict"] == "marginal", steps["write"]
        assert steps["write-inhibited"]["verdict"] == "BAD", steps["write-inhibited"]

    def test_baseline_steps_stay_ok_around_the_leaked_lock(
        self, runner: CliRunner
    ) -> None:
        """Companion assertion to `test_leaked_lock_exits_1`: the leaked-lock
        operator's state-tracking read-back must make BOTH baseline
        directions (`write-baseline-b` then `write-baseline-a`) report OK,
        never closing the baseline gate (D-08) -- otherwise `write-inhibited`
        would be SKIPPED instead of genuinely dispatched, and the BAD verdict
        this test suite depends on would not be evidence of anything."""
        operator = make_leaked_lock_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        data = _load_report(_CHIP_ALLOW)
        verdicts = {s["op"]: s["verdict"] for s in data["steps"]}
        assert verdicts["write-baseline-b"] == "OK", verdicts
        assert verdicts["write-baseline-a"] == "OK", verdicts
        assert verdicts["sdp-lock"] == "OK", verdicts
        assert verdicts["sdp-unlock"] == "OK", verdicts
        assert verdicts["write-restored"] == "OK", verdicts


# ---------------------------------------------------------------------------
# LEG-12 (v1.30 Phase 134 plan 134-07): the HELD/NOT-HELD/NOT-RUN(reason)
# hold state, both surfaces, end to end through the real CLI. Evidence
# Ceiling (`.planning/REQUIREMENTS.md`): every fixture below pins the host's
# RESPONSE to a scripted read-back -- a locked die is unrepresentable in
# either repo's stubs, so the causal claim "the lock inhibited the write" is
# NOT provable this milestone. These tests prove the REPORTED value reaches
# both surfaces correctly, never that a real part was physically inhibited.
# ---------------------------------------------------------------------------


class TestHoldStateLeg12:
    """`report.sdp_hold_state = sdp_hold_state(plan, results)` (the
    derive-in-engine / assign-in-handler seam this plan wires) reaches BOTH
    the console (`render()`'s own row, D-07) and the JSON artifact
    (`to_dict()`'s `sdp_hold_state` key, plan 134-06) for every one of the
    three values. Each assertion checks the JSON artifact with a STRICT
    equality against the imported `SDP_HOLD_*` constant (never a retyped
    literal) and the console with a substring check on the NORMALIZED text
    (`_normalize_console_text` strips Rich's box-drawing borders and
    collapses word-wrapping) -- `NOT-HELD` contains the substring `HELD`,
    so the console checks below assert the `sdp_hold_state ` PREFIX
    together with the value, never a bare `"HELD" in output` that a
    NOT-HELD run would also satisfy."""

    def test_hold_state_held_reaches_both_surfaces(self, runner: CliRunner) -> None:
        """HELD: the inhibited write is correctly refused (read-back stays
        pattern A, D-03's `(True, A) -> OK` arm) -- `sdp_hold_state` reads
        `HELD` in both surfaces, and the run exits 0 (no floor applies)."""
        operator = make_held_lock_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        assert result.exit_code == 0, result.output
        data = _load_report(_CHIP_ALLOW)
        assert data["sdp_hold_state"] == SDP_HOLD_HELD, data["sdp_hold_state"]
        normalized = _normalize_console_text(result.output)
        assert "sdp_hold_state HELD" in normalized, normalized

    def test_hold_state_not_held_reaches_both_surfaces(self, runner: CliRunner) -> None:
        """NOT-HELD: the leaked-lock operator's inhibited write genuinely
        lands (read-back equals B, D-03's `(True, B) -> BAD` arm) --
        `sdp_hold_state` reads `NOT-HELD` in both surfaces (LEG-06's own
        shape, now proven at the hold-state field too, not just the step
        verdict `test_leaked_lock_exits_1` already pins)."""
        operator = make_leaked_lock_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        data = _load_report(_CHIP_ALLOW)
        assert data["sdp_hold_state"] == SDP_HOLD_NOT_HELD, data["sdp_hold_state"]
        normalized = _normalize_console_text(result.output)
        assert "sdp_hold_state NOT-HELD" in normalized, normalized

    def test_hold_state_not_run_reason_reaches_both_surfaces(
        self, runner: CliRunner
    ) -> None:
        """NOT-RUN: the dead-write-path operator (`make_clean_operator`'s
        `read_eprom` never persists real bytes, so the baseline read-back
        length-gates BAD) closes D-08's baseline gate before `write-
        inhibited` is ever dispatched. `sdp_hold_state` reads
        `NOT-RUN: <reason>` in BOTH surfaces, with the REASON itself
        surviving into both -- LEG-12 says `NOT-RUN(reason)`, and a reason
        reaching only the JSON is half the requirement. Also demonstrates
        the phase's central safety property end to end
        (`operator.sdp_lock.assert_not_called()`) and the banner's dropped
        ratio (`n_ran < m_applicable`, LEG-13's own mechanism, D-15)."""
        operator = make_clean_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        data = _load_report(_CHIP_ALLOW)
        hold_state = data["sdp_hold_state"]
        assert hold_state.startswith(f"{SDP_HOLD_NOT_RUN}:"), hold_state
        reason = hold_state.split(":", 1)[1].strip()
        assert reason, hold_state  # non-empty reason, never a bare "NOT-RUN:"
        normalized = _normalize_console_text(result.output)
        assert f"sdp_hold_state {hold_state}" in normalized, normalized
        operator.sdp_lock.assert_not_called()
        banner = data["banner"]
        assert banner["n_ran"] < banner["m_applicable"], banner


# ---------------------------------------------------------------------------
# D-15 (v1.30 Phase 134 plan 134-07): the exit-floor composition, pinned in
# every order assumption A3 names. `pytest -k "exit"` selects these
# alongside `TestExitPrecedenceLeg06` above.
# ---------------------------------------------------------------------------


class TestExitFloorD15:
    """`_dev_test_exit_code`'s ALLOW-only floor: a NOT-RUN oracle on an
    ALLOW chip can no longer exit 0, but the floor never outranks a BAD
    step (D-14's precedence stays on top), and a REFUSE chip's legitimate
    NOT-RUN is never floored at all."""

    def test_clean_notrun_floors_to_2(self, runner: CliRunner) -> None:
        """ALLOW chip, oracle NOT-RUN, NO BAD and NO marginal anywhere in
        the run -- exit 2, purely from D-15's floor (without it, the
        codes-observed set would be `{0}` and this run would exit 0, the
        exact P-04 shape this milestone exists to stop: `firestarter dev
        test at28c256` returning 0 and being filed as PASS)."""
        operator = make_clean_notrun_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        data = _load_report(_CHIP_ALLOW)
        verdicts = {s["verdict"] for s in data["steps"]}
        assert "BAD" not in verdicts, verdicts
        assert "marginal" not in verdicts, verdicts
        assert data["sdp_hold_state"].startswith(f"{SDP_HOLD_NOT_RUN}:")
        assert result.exit_code == 2, result.output

    def test_bad_and_notrun_exits_1_not_2(self, runner: CliRunner) -> None:
        """ALLOW chip, oracle NOT-RUN AND a BAD step (the dead-write-path
        operator's baseline BAD closes the gate) -- exit 1, never 2. A
        naive `max(code, 2)` would return 2 here (`max(1, 2) == 2`),
        re-creating exactly the laundering D-14 removed; composing the
        floor as a precedence CANDIDATE instead keeps BAD's rank intact."""
        operator = make_clean_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        data = _load_report(_CHIP_ALLOW)
        verdicts = {s["verdict"] for s in data["steps"]}
        assert "BAD" in verdicts, verdicts
        assert data["sdp_hold_state"].startswith(f"{SDP_HOLD_NOT_RUN}:")
        assert result.exit_code == 1, result.output

    def test_marginal_and_notrun_exits_2(self, runner: CliRunner) -> None:
        """ALLOW chip, oracle NOT-RUN and a `marginal` step (an all-zero
        baseline read-back: correct length, degenerate content, D-04's
        content-degeneracy arm) -- exit 2, same as the clean-floor case
        above, but this run reaches 2 via `_EXIT_CODE_PRECEDENCE`'s
        `marginal` code directly, not solely via the floor -- both routes
        to 2 must agree."""

        def _all_zero_read(name, eprom_data, output_file=None, **kwargs):
            if output_file is not None:
                Path(output_file).write_bytes(b"\x00" * 256)
            return True

        operator = Mock(spec=EpromOperator)
        operator.check_eprom_id.return_value = (True, None)
        operator.check_eprom_blank.return_value = True
        operator.write_eprom.return_value = True
        operator.verify_eprom.return_value = True
        operator.erase_eprom.return_value = True
        operator.read_eprom.side_effect = _all_zero_read
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        data = _load_report(_CHIP_ALLOW)
        verdicts = {s["verdict"] for s in data["steps"]}
        assert "BAD" not in verdicts, verdicts
        assert "marginal" in verdicts, verdicts
        assert data["sdp_hold_state"].startswith(f"{SDP_HOLD_NOT_RUN}:")
        assert result.exit_code == 2, result.output

    def test_refuse_chip_notrun_exits_0(self, runner: CliRunner) -> None:
        """A REFUSE chip (`_CHIP_NO_ID`, `sdp_capability() -> False`) whose
        hold state reads NOT-RUN, with no BAD or marginal step, exits 0 --
        the floor is `sdp_oracle_applicable(plan)`-gated, so a REFUSE
        chip's legitimate NOT-RUN (the oracle was never applicable to
        begin with) is never floored."""
        operator = make_clean_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        data = _load_report(_CHIP_NO_ID)
        verdicts = {s["verdict"] for s in data["steps"]}
        assert "BAD" not in verdicts, verdicts
        assert "marginal" not in verdicts, verdicts
        assert data["sdp_hold_state"].startswith(f"{SDP_HOLD_NOT_RUN}:")
        assert result.exit_code == 0, result.output

    def test_identical_verdict_multiset_differing_exit_code(self) -> None:
        """D-15's stated cost, made mechanical: `dev test`'s exit code
        stops being a PURE function of step verdicts. Two calls to
        `_dev_test_exit_code` against the EXACT SAME `results` list (so
        the verdict multiset is not merely equal but IDENTICAL) differ
        ONLY in `sdp_oracle_not_run` -- and their exit codes differ (0 vs
        2). Unit-level (not CLI-driven): no real ALLOW-chip fixture can
        hold the verdict multiset constant while varying only the hold
        state through the actual engine (every engine-level route to a
        NOT-RUN oracle changes at least one step's verdict too, per the
        other tests in this class), so this is the direct, honest pin of
        the composition rule itself."""
        results = [
            StepResult(op="read", verdict=VERDICT_OK, run_count=1),
            StepResult(op="write-baseline-b", verdict=VERDICT_OK, run_count=1),
        ]
        exit_clean = _dev_test_exit_code(results, sdp_oracle_not_run=False)
        exit_notrun = _dev_test_exit_code(results, sdp_oracle_not_run=True)
        assert exit_clean == 0, exit_clean
        assert exit_notrun == 2, exit_notrun
        assert exit_clean != exit_notrun


# ---------------------------------------------------------------------------
# D-09 (v1.30 Phase 134, plan 134-08): the notice's write-pass count is
# DERIVED from a live `derive_plan` result, never restated as a literal.
# `pytest -k "notice"` selects this class.
# ---------------------------------------------------------------------------


class TestAlwaysWritesNoticeDerivedCountD09:
    """P-08's own headline finding: the shipped notice-first pin
    (`test_always_writes_notice_is_the_first_line_unconditionally`, above)
    asserts identity against the IMPORTED `_ALWAYS_WRITES_NOTICE` constant
    and therefore checks NO CONTENT at all -- a content-free rewrite would
    keep it green. This class is what makes the notice's number TRUE."""

    def test_pass_count_is_derived_from_a_live_plan_never_a_literal(self) -> None:
        """Derive the plan for AT28C256 (the module's own ALLOW chip) at
        `write_scope="full"` -- the scope `dev test` itself resolves for a
        non-UV chip -- and compute the number of write passes FROM the
        plan: `run_plan`'s own `runs` default for the shipped multi-run
        write step (`OP_WRITE`/`OP_WRITE_PARTIAL`), plus one pass for each
        of the leg's four single-run write ops (`_SDP_LEG_OPS`) that are
        `supported=True`. Never restate `6` (or any other number) as a
        literal here -- if this ever measures a different number than
        `_ALWAYS_WRITES_PASS_COUNT`, the constant is wrong, not this test.
        """
        plan = derive_plan(_CHIP_ALLOW, _REAL_DB, write_scope="full")
        runs = inspect.signature(run_plan).parameters["runs"].default

        write_passes = 0
        for step in plan.steps:
            if not step.supported:
                continue
            if step.op in (OP_WRITE, OP_WRITE_PARTIAL):
                write_passes += runs
            elif step.op in _SDP_LEG_OPS:
                write_passes += 1

        assert write_passes == _ALWAYS_WRITES_PASS_COUNT, (
            write_passes,
            _ALWAYS_WRITES_PASS_COUNT,
        )
        assert str(_ALWAYS_WRITES_PASS_COUNT) in _ALWAYS_WRITES_NOTICE

    def test_notice_names_sdp_lock_completed_run_and_rewrite_recovery(self) -> None:
        """Content pins for D-09/D-12's must-haves: the notice names the
        SDP lock in prose, states the completed-run outcome, and gives
        the aborted-run recovery in the word "rewrite" -- and the shipped
        notice-first ordering pin stays untouched and green (verified by
        `git diff` showing no edit to that test, per the plan's own
        acceptance criterion)."""
        notice_lower = _ALWAYS_WRITES_NOTICE.lower()
        assert "sdp lock" in notice_lower
        assert "rewrite" in notice_lower
        assert "completed" in notice_lower
        assert "unlocked" in notice_lower

    def test_notice_contains_no_sdp_leg_op_literal(self) -> None:
        """No hyphenated op literal anywhere in the notice (T-134-33):
        `_SDP_LEG_OPS`'s four strings all auto-join
        `_MULTIWORD_OP_VALUES`, and `_ALWAYS_WRITES_NOTICE` is a declared
        non-registry measured by `test_op_registration_parity.py`'s own
        live substring test (`test_non_registry_still_has_no_ops`) -- this
        is the same claim, pinned here too so a regression is caught by
        two independent modules."""
        assert not any(op in _ALWAYS_WRITES_NOTICE for op in _SDP_LEG_OPS)


# ---------------------------------------------------------------------------
# D-12 (v1.30 Phase 134, plan 134-08): the two recovery forms, proven
# behaviourally through the real CLI. `pytest -k "recovery"` selects this
# class. Evidence Ceiling (`.planning/REQUIREMENTS.md`): every fixture below
# pins the host's RESPONSE to a scripted read-back -- a locked die is
# unrepresentable in either repo's stubs, so no fixture here simulates real
# inhibition, and the causal claim "the lock inhibited the write" is NOT
# provable this milestone.
# ---------------------------------------------------------------------------


class TestSdpRecoveryFormsD12:
    """`_sdp_recovery_line`'s two named forms, each proven to print (or not
    print) in the right case, asserted against the imported constants --
    never a retyped literal."""

    def test_happy_path_prints_neutral_not_loud(self, runner: CliRunner) -> None:
        """Happy path: the leg completes and `write-restored` reports OK
        (`make_held_lock_operator`'s genuinely-held lock, same fixture
        `TestHoldStateLeg12::test_hold_state_held_reaches_both_surfaces`
        uses) -- the NEUTRAL form prints, the LOUD form does not."""
        operator = make_held_lock_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        data = _load_report(_CHIP_ALLOW)
        assert data["sdp_hold_state"] == SDP_HOLD_HELD, data["sdp_hold_state"]
        steps = {s["op"]: s for s in data["steps"]}
        assert steps["write-restored"]["verdict"] == "OK", steps["write-restored"]
        assert _SDP_RECOVERY_NEUTRAL in result.output, result.output
        assert _SDP_RECOVERY_LOUD not in result.output, result.output

    def test_lock_emitted_and_not_confirmed_writable_prints_loud(
        self, runner: CliRunner
    ) -> None:
        """Lock emitted, part NOT confirmed writable again:
        `make_restore_failed_operator`'s every write_eprom call persists
        genuinely EXCEPT the last one (`write-restored`), so that step's
        own read-back reports non-OK even though the leg's earlier steps
        (baseline, inhibited, unlock) all genuinely dispatched -- the LOUD
        form prints, the NEUTRAL form does not."""
        operator = make_restore_failed_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        data = _load_report(_CHIP_ALLOW)
        steps = {s["op"]: s for s in data["steps"]}
        assert steps["write-restored"]["verdict"] != "OK", steps["write-restored"]
        assert not data["sdp_hold_state"].startswith(f"{SDP_HOLD_NOT_RUN}:"), data[
            "sdp_hold_state"
        ]
        assert _SDP_RECOVERY_LOUD in result.output, result.output
        assert _SDP_RECOVERY_NEUTRAL not in result.output, result.output

    def test_gated_run_never_locked_prints_neutral(self, runner: CliRunner) -> None:
        """Gated run: the baseline gate closes before a lock is ever
        emitted (`make_clean_operator`'s file-less read-back length-gates
        BAD, D-08) -- nothing was locked, so the NEUTRAL form prints
        (never LOUD), and `sdp_lock` is never called."""
        operator = make_clean_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        data = _load_report(_CHIP_ALLOW)
        assert data["sdp_hold_state"].startswith(f"{SDP_HOLD_NOT_RUN}:")
        operator.sdp_lock.assert_not_called()
        assert _SDP_RECOVERY_NEUTRAL in result.output, result.output
        assert _SDP_RECOVERY_LOUD not in result.output, result.output

    def test_recovery_constant_names_resolve_to_the_two_real_constants(self) -> None:
        """`SDP_RECOVERY_CONSTANT_NAMES` -- LEG-14's scan target for plan
        134-09's scoped pytest -- resolves to exactly the two constants
        this class exercises above, each non-empty."""
        assert set(SDP_RECOVERY_CONSTANT_NAMES) == {
            "_SDP_RECOVERY_LOUD",
            "_SDP_RECOVERY_NEUTRAL",
        }
        import firestarter.cli_handlers as cli_handlers_mod

        for name in SDP_RECOVERY_CONSTANT_NAMES:
            value = getattr(cli_handlers_mod, name)
            assert isinstance(value, str) and value, (name, value)


# ---------------------------------------------------------------------------
# 133 D-07's residual, inherited unchanged by D-12 (v1.30 Phase 134, plan
# 134-08): RECORDED here, not closed. Recording it as a truthful test rather
# than only a comment, per the plan's own instruction.
# ---------------------------------------------------------------------------


class TestCtrlCResidualNotClosedD12:
    """After a Ctrl-C mid-leg, `results = run_plan(...)` (cli_handlers.py)
    never returns, so NEITHER recovery form ever prints and there is NO
    report at all. The mitigation is Task 1's rewritten up-front notice
    (guaranteed to already have printed before anything could raise), NOT
    a `finally` handler -- this plan deliberately does not add one, and
    this test asserts the OBSERVED behaviour truthfully rather than a
    behaviour the code does not have.

    MEASURED (not assumed): Click's `BaseCommand.main` (standalone mode,
    which `CliRunner.invoke` uses) catches `KeyboardInterrupt` itself,
    prints "Aborted!" to stderr, and converts it to `sys.exit(1)` -- so
    `KeyboardInterrupt` never propagates OUT of `runner.invoke()` here; it
    surfaces as an ordinary exit code 1. What this test proves instead is
    the thing that actually matters for D-12's residual: neither recovery
    constant ever reaches `result.output`, and no report file is ever
    written -- run_plan raised before `report.render()`, the JSON/markdown
    writes, `submit_report`, and the recovery echo could run.
    """

    def test_keyboard_interrupt_mid_run_plan_leaves_no_report_and_no_recovery_line(
        self, runner: CliRunner
    ) -> None:
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(),
        )
        report_path = _reports_dir() / f"dev-test-{_CHIP_ALLOW}.json"
        assert not report_path.exists()
        with (
            patch("firestarter.cli_handlers.run_plan", side_effect=KeyboardInterrupt),
            _off_tty(),
        ):
            result = runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        # No report was ever written -- run_plan raised before
        # report.render(), the JSON/markdown writes, submit_report, and the
        # recovery echo all run, so none of them ever executed.
        assert not report_path.exists()
        assert _SDP_RECOVERY_LOUD not in result.output, result.output
        assert _SDP_RECOVERY_NEUTRAL not in result.output, result.output
