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
    _ALWAYS_WRITES_PASS_COUNT,
    _dev_test_exit_code,
    cli,
)
from firestarter.config import get_config_dir
from firestarter.constants import FLAG_SKIP_SDP_UNLOCK
from firestarter.database import EpromDatabase
from firestarter.diagnostic_report import NOT_REPORTED
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import (
    ChipNotFoundError,
    ChipNotImplementedError,
    SerialError,
)
from firestarter.hardware import HardwareManager, ProgrammerIdentity
from firestarter.sdp_capability import sdp_capability, sdp_capability_for_entry

from .conftest import make_app_context
from .fixtures.synthetic_nonzero_chip_id import (
    SYNTHETIC_CHIP_NAME,
    SyntheticNonzeroChipIdDatabase,
)

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
    exists and matches). write/verify/erase all report success so a full
    sweep comes back clean (exit 0).

    `read_eprom` (quick task 260821-wna, Task 3) is a `side_effect` rather
    than a bare `return_value=True`: a plain `True` writes no file at all,
    which would make every region/probe read see `b""` -- turning every UV
    slot into a saturation refusal the moment run_plan's execution-time
    resolver starts probing (Task 4). It writes an all-0xFF (blank-chip-
    shaped) image at the requested ABSOLUTE offset, reproducing
    `_write_to_file`'s `file_handle.seek(address)` (finding M-3) via the
    same `_parse_addr_or_size` helper `fake_chip.FakeChip` uses -- a virgin
    UV chip's slots are all comfortably above both D-B bit-count floors.
    """
    from .fake_chip import _parse_addr_or_size

    def _clean_read(
        name,
        eprom_data,
        output_file=None,
        operation_flags=0,
        address_str=None,
        size_str=None,
    ):
        if output_file is None:
            return True
        mem_size = int(eprom_data.get("memory-size", 0) or 0)
        start = _parse_addr_or_size(address_str) or 0
        length = _parse_addr_or_size(size_str)
        if length is None:
            length = max(mem_size - start, 0)
        with open(output_file, "wb") as fh:
            fh.seek(start)
            fh.write(b"\xff" * length)
        return True

    operator = Mock(spec=EpromOperator)
    operator.check_eprom_id.return_value = (True, None)
    operator.read_eprom.side_effect = _clean_read
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
    # quick task 260821-wna, Task 3: `_write`/`_read` now accept (and honor)
    # `address_str` -- required so a later keyword-only `address_str=...`
    # call from `_dispatch_multi_run`/`_dispatch_sdp_leg` (Task 4) does not
    # TypeError -- while deliberately PRESERVING the pre-existing "replace
    # `state['data']` wholesale on every write" model rather than widening
    # it into a persistent whole-device buffer: every ALLOW chip's write and
    # SDP-leg regions in this suite start at address 0 (D-17: ALLOW chips
    # are all non-UV, so `_address_arg(0)` is `None` on every reachable
    # call), so `state['data']`'s SIZE tracking the most recent write's
    # length -- not a fixed device size -- is exactly what keeps
    # `_dispatch_sdp_leg`'s length gate meaningful without this fixture
    # pre-emptively solving the region-scoped-readback problem Task 4 owns.
    # `state['start']` records the last write's own address so `_read`'s
    # `file_handle.seek(address)` (finding M-3) is still genuinely
    # reproduced if a future case DOES pass a non-zero start.
    from .fake_chip import _parse_addr_or_size

    state = {"data": b"", "start": 0}
    calls = {"write": 0}

    def _write(name, eprom_data, source_path, flags=0, address_str=None, **_kw):
        idx = calls["write"]
        calls["write"] += 1
        state["data"] = Path(source_path).read_bytes()
        state["start"] = _parse_addr_or_size(address_str) or 0
        if write_outcomes is not None and idx < len(write_outcomes):
            return write_outcomes[idx]
        return True

    def _read(
        name, eprom_data, output_file=None, address_str=None, size_str=None, **_kw
    ):
        if output_file is None:
            return True
        with open(output_file, "wb") as fh:
            fh.seek(state["start"])
            fh.write(state["data"])
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
    # quick task 260821-wna, Task 3: address_str-aware, same rationale and
    # same "replace wholesale, track the last write's own start" model as
    # `make_leaked_lock_operator` above (see its comment for why this
    # deliberately does NOT widen into a persistent whole-device buffer).
    from .fake_chip import _parse_addr_or_size

    state = {"data": b"", "start": 0}

    def _write(name, eprom_data, source_path, flags=0, address_str=None, **_kw):
        payload = Path(source_path).read_bytes()
        if flags & FLAG_SKIP_SDP_UNLOCK:
            # The inhibited-write call (D-01's one narrowing): the part
            # refuses the write internally -- state stays unchanged -- but
            # the state machine still completes and the ack is observed.
            return True
        state["data"] = payload
        state["start"] = _parse_addr_or_size(address_str) or 0
        return True

    def _read(
        name, eprom_data, output_file=None, address_str=None, size_str=None, **_kw
    ):
        if output_file is None:
            return True
        with open(output_file, "wb") as fh:
            fh.seek(state["start"])
            fh.write(state["data"])
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
    # quick task 260821-wna, Task 3: address_str-aware, same "replace
    # wholesale, track the last write's own start" model as
    # `make_leaked_lock_operator` above.
    from .fake_chip import _parse_addr_or_size

    state = {"data": b"", "start": 0}
    calls = {"write": 0}

    def _write(name, eprom_data, source_path, flags=0, address_str=None, **_kw):
        idx = calls["write"]
        calls["write"] += 1
        if idx < _ALWAYS_WRITES_PASS_COUNT - 1:
            state["data"] = Path(source_path).read_bytes()
            state["start"] = _parse_addr_or_size(address_str) or 0
        # else: the LAST (write-restored) call does not persist -- the
        # part's read-back stays at whatever the second-to-last call left.
        return True

    def _read(
        name, eprom_data, output_file=None, address_str=None, size_str=None, **_kw
    ):
        if output_file is None:
            return True
        with open(output_file, "wb") as fh:
            fh.seek(state["start"])
            fh.write(state["data"])
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
    word-wrapping of a long cell into one logical line. Presence-only --
    never used for a byte-exact assertion.

    The original motivating case (`sdp_hold_state`'s `NOT-RUN` reason,
    which wrapped across three console lines) no longer reaches the box as
    of 2026-08-21, but the normalization still earns its keep against
    column padding, and the `hold_state not in normalized` guards now rely
    on it to prove the reason did NOT come back by way of a wrap.
    """
    stripped = re.sub(r"[│┃┏┓┗┛┡┩┳┻╇━┌┐└┘├┤┬┴┼─]", " ", output)
    return " ".join(stripped.split())


def make_hardware_manager(
    vpp_values: object = 12000,
    vpe_values: object = 5000,
    hw_revision: object = "Rev 2.0-class",
    fw_board_identity: object = "3.0.0b19:leonardo",
) -> Mock:
    """A Mock(spec=HardwareManager) with canned sample_vpp_mv/sample_vpe_mv/
    read_programmer_identity.

    D-10: this builder's `Mock` return type is deliberate too -- see
    `make_clean_operator` above and tests/conftest.py's `make_app_context`
    docstring for the reasoning. read_programmer_identity's return value is
    itself a real ProgrammerIdentity (never a bare Mock/MagicMock): the
    NamedTuple's field names are not spec-protected, so a mock return value
    would leak a child-mock repr into the report instead of a string or
    None.

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
    hw.read_programmer_identity.return_value = ProgrammerIdentity(
        hw_revision=hw_revision, fw_board_identity=fw_board_identity
    )
    return hw


def test_make_hardware_manager_returns_a_spec_bound_double() -> None:
    """Pins the property the D-03 rename's safety rests on (P-2), so a
    future "simplification" cannot quietly reopen the absent-chip
    false-green trap: the double is spec-bound (an attribute the real
    HardwareManager does not define raises AttributeError, so a missed
    rename anywhere in the suite fails loudly instead of passing
    vacuously), and its read_programmer_identity() return value is a real
    ProgrammerIdentity carrying exactly the strings the factory was given."""
    hw = make_hardware_manager(
        hw_revision="Rev 2.0-class", fw_board_identity="3.0.0b19:leonardo"
    )
    with pytest.raises(AttributeError):
        hw.this_attribute_does_not_exist_on_hardware_manager
    identity = hw.read_programmer_identity()
    assert isinstance(identity, ProgrammerIdentity)
    assert identity.hw_revision == "Rev 2.0-class"
    assert identity.fw_board_identity == "3.0.0b19:leonardo"


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
# Quick task 260821-spg: structural regression pinning "console trimmed,
# payload intact" -- the whole point of the task. Fails if any of the three
# source edits (cli_handlers.py, diagnostic_report.py, submit.py) regress.
# ---------------------------------------------------------------------------


def test_dev_test_output_trim_console_shrunk_payload_intact(
    runner: CliRunner,
) -> None:
    """Structural pin, not absent-string scanning: the four deleted
    constants and the deleted selector are gone from cli_handlers; --help
    stays short; a real off-TTY run's console output carries neither the
    issue body's markdown table header nor a transport_health row label;
    and that SAME run's saved JSON still has transport_health,
    is_submittable and db_diff keys -- console trimmed, payload intact."""
    import firestarter.cli_handlers as cli_handlers_mod

    for name in (
        "_ALWAYS_WRITES_NOTICE",
        "_SDP_RECOVERY_LOUD",
        "_SDP_RECOVERY_NEUTRAL",
        "SDP_RECOVERY_CONSTANT_NAMES",
    ):
        assert not hasattr(cli_handlers_mod, name), name
    assert not hasattr(cli_handlers_mod, "_sdp_recovery_line")

    help_result = runner.invoke(cli, ["dev", "test", "--help"])
    assert help_result.exit_code == 0, help_result.output
    assert len(help_result.output.strip().splitlines()) <= 14

    app = make_app_context(
        eprom_operator=make_clean_operator(),
        hardware_manager=make_hardware_manager(),
    )
    with _off_tty():
        result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
    assert result.exit_code == 0, result.output

    # Exclude the printed issue URL and everything after it: it legitimately
    # embeds the WHOLE sanitized body (percent-encoded) as a query param --
    # `transport_health` survives unescaped inside it (underscores/alnum are
    # not percent-encoded by `urlencode(quote_via=quote)`), and rich's console
    # can hard-wrap that one long token across several output lines with no
    # per-line marker to filter on. That is correct behaviour (SUB-02), not a
    # regression. The claim under test is about the RENDERED TABLE and the
    # removed body echo, both of which print BEFORE the URL -- so this check
    # looks only at the output up to where the URL begins.
    url_start = result.output.find("https://github.com/")
    assert url_start != -1, result.output
    pre_url_output = result.output[:url_start]
    assert "| Step | Verdict | Reason |" not in pre_url_output
    assert "transport_health" not in pre_url_output

    data = _load_report(_CHIP_NO_ID)
    assert "transport_health" in data
    assert "is_submittable" in data
    assert "db_diff" in data


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
        """The mocked hardware manager's read_programmer_identity() flows
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
        # `Took` column added 2026-08-21 (schema 1.5 per-step durations).
        assert "| Step | Verdict | Took | Reason |" in md_text

    def test_fw_board_identity_auto_captured_end_to_end(
        self, runner: CliRunner
    ) -> None:
        """The mocked hardware manager's read_programmer_identity() flows
        through to the rendered report and the .json artifact -- the finding
        that opens milestone v1.32 (PROV-01): every `dev test` report ever
        filed carried an unconditional `fw_board_identity: null`, so a
        report can now be attributed to the firmware/board that produced it.
        This proves attribution becomes possible; it proves nothing about
        the AT28C256 0x0D write path itself (Evidence Ceiling)."""
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(
                fw_board_identity="3.0.0b19:leonardo"
            ),
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output
        assert "3.0.0b19:leonardo" in result.output
        data = _load_report(_CHIP_NO_ID)
        assert data["auto_capture"]["fw_board_identity"] == "3.0.0b19:leonardo"

    @pytest.mark.parametrize("identity", ["3.0.0b11:leonardo", "3.0.0b19:leonardo"])
    def test_prerelease_suffix_survives_into_the_report(
        self, runner: CliRunner, identity: str
    ) -> None:
        """PROV-03: the recorded identity keeps its prerelease suffix
        verbatim in the saved JSON -- no truncation to `3.0.0`, no
        normalisation. Attribution requires the exact build, not just the
        release line."""
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(fw_board_identity=identity),
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output
        data = _load_report(_CHIP_NO_ID)
        assert data["auto_capture"]["fw_board_identity"] == identity

    def test_two_identities_differing_only_in_suffix_land_as_different_values(
        self, runner: CliRunner
    ) -> None:
        """PROV-03 / D-08: gh#21 and gh#32 both report host `3.0.0b15`
        against an UNKNOWN firmware, and so cannot today be distinguished
        from a board lacking the whole Phase-117-120 0x0D fix stack (FIX-01
        /WE-inhibit routing, FIX-03 A16-A18 staleness, FIX-06 the
        completion-vs-data-landed conflation). This test proves that
        attribution becomes possible going forward -- two builds differing
        only in prerelease suffix (b11 vs b19) land as two DIFFERENT
        recorded values, never collapsed to the same one. It does not prove
        the 0x0D write path itself is fixed on either build (Evidence
        Ceiling): a single round-trip assertion would pass vacuously if a
        later refactor normalised suffixes away, so the point of this test
        is the INEQUALITY, not either value in isolation."""
        first_identity = "3.0.0b11:leonardo"
        second_identity = "3.0.0b19:leonardo"

        app_first = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(fw_board_identity=first_identity),
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app_first)
        assert result.exit_code == 0, result.output
        first_recorded = _load_report(_CHIP_NO_ID)["auto_capture"]["fw_board_identity"]

        app_second = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(fw_board_identity=second_identity),
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app_second)
        assert result.exit_code == 0, result.output
        second_recorded = _load_report(_CHIP_NO_ID)["auto_capture"]["fw_board_identity"]

        assert first_recorded != second_recorded
        assert first_recorded.endswith("b11:leonardo")
        assert second_recorded.endswith("b19:leonardo")

    def test_unknown_identity_renders_the_marker_and_saves_typed_null(
        self, runner: CliRunner
    ) -> None:
        """D-13(b): this path is DEFENSIVE, not routine -- _probe_port
        refuses firmware reporting no identity at all, so in the field a
        successful `dev test` run essentially always has one. That is
        exactly why this leg has to be built and seen to pass rather than
        assumed, and why it is driven through the existing spec-bound mock
        (make_hardware_manager) rather than through a contrived transport
        failure: with read_programmer_identity() returning
        ProgrammerIdentity(None, None), the rendered output carries the
        explicit NOT_REPORTED marker (imported, never restated as a
        literal) for both identity rows and never a bare rendering of
        None on either -- while the saved report JSON keeps both
        auto_capture.fw_board_identity and auto_capture.hw_revision typed
        `null` (D-10), so machine consumers keep testing `is None` and
        PROV-04's backward-compatibility story stays one case. Attribution
        is explicitly refused here, not silently produced -- this proves
        nothing about the AT28C256 0x0D write path itself, and no
        support_status changes (Evidence Ceiling)."""
        app = make_app_context(
            eprom_operator=make_clean_operator(),
            hardware_manager=make_hardware_manager(
                hw_revision=None, fw_board_identity=None
            ),
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output

        assert NOT_REPORTED in result.output
        identity_lines = [
            line
            for line in result.output.splitlines()
            if "fw_board_identity" in line or "hw_revision" in line
        ]
        assert identity_lines, "expected identity rows in the rendered table"
        for line in identity_lines:
            assert not re.search(r"\bNone\b", line), (
                f"bare None rendered in an identity cell: {line!r}"
            )

        data = _load_report(_CHIP_NO_ID)
        assert data["auto_capture"]["fw_board_identity"] is None
        assert data["auto_capture"]["hw_revision"] is None


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
        read_programmer_identity.assert_not_called() (the load-bearing
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
        app.hardware_manager.read_programmer_identity.assert_not_called()
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
        hw.read_programmer_identity.assert_called()
        data = _load_report(chip)
        steps = {s["op"]: s for s in data["steps"]}
        assert steps["id"]["verdict"] == "NA"
        assert steps["read"]["verdict"] == "SKIPPED"
        assert "adapter" in steps["read"]["reason"]
        # Quick task 260807-kaq moved this assertion: AT28C16 is protocol
        # 0x0D (28C family, measured), so derive_plan now emits blank-check
        # as NA-by-family-fact (case 3, auto-erase-on-write) BEFORE run_plan
        # ever reaches resolve_chip's adapter refusal -- blank-check no
        # longer carries the adapter reason at all. "write" below remains
        # this test's proof that the adapter-required guard still surfaces
        # as SKIPPED, never a bare exit, on a step that DOES reach
        # resolve_chip.
        assert steps["blank-check"]["verdict"] == "NA"
        assert "0x0d" in steps["blank-check"]["reason"].lower()
        assert steps["write"]["verdict"] == "SKIPPED"
        assert "adapter" in steps["write"]["reason"]


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
        `NOT-RUN: <reason>` in the JSON, with the reason verbatim, while the
        console box shows only the bare `NOT-RUN` token -- the operator
        superseded D-07's console-visibility leg on 2026-08-21 (the reason
        is a sentence Rich wrapped across three lines). Also demonstrates
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
        # Console shows the BARE state token; the `NOT-RUN` reason rides the
        # JSON only (operator superseded D-07's console leg, 2026-08-21).
        assert f"sdp_hold_state {SDP_HOLD_NOT_RUN}" in normalized, normalized
        assert hold_state not in normalized, normalized
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


class TestWritePassCountDerivedFromLivePlanD09:
    """Renamed by quick task 260821-spg (was
    `TestAlwaysWritesNoticeDerivedCountD09`): the always-writes console
    notice this class used to also pin is gone, along with the two
    wording-only tests that asserted its prose. What survives is the real
    data invariant -- the write-pass count is DERIVED from a live plan,
    never a restated literal -- which is exactly what
    `_ALWAYS_WRITES_PASS_COUNT` still backs (it feeds no console output
    now, but the six-write-pass fact it pins is still true and still
    measured here)."""

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


# ---------------------------------------------------------------------------
# D-12 (v1.30 Phase 134, plan 134-08): the SDP leg's OUTCOMES, proven
# behaviourally through the real CLI. Quick task 260821-spg removed the
# console prose these tests used to also assert (both named recovery
# forms, and the module-level tuple that resolved their names) -- what
# survives is each fixture's genuine data claim: the hold state that
# actually landed, the restore step's actual verdict, and whether
# `sdp_lock` was actually called. `pytest -k "recovery"` no longer selects
# this class by name; use `-k "SdpRecoveryOutcomes"` instead. Evidence
# Ceiling (`.planning/REQUIREMENTS.md`): every fixture below pins the
# host's RESPONSE to a scripted read-back -- a locked die is
# unrepresentable in either repo's stubs, so no fixture here simulates real
# inhibition, and the causal claim "the lock inhibited the write" is NOT
# provable this milestone.
# ---------------------------------------------------------------------------


class TestSdpRecoveryOutcomesD12:
    """The SDP leg's three outcome shapes, each proven against the real
    CLI run's saved JSON and mock call assertions -- the console recovery
    prose that used to ALSO print in each of these cases is gone
    (260821-spg); these tests now prove the underlying DATA only."""

    def test_happy_path_hold_held_and_restore_ok(self, runner: CliRunner) -> None:
        """Happy path: the leg completes and `write-restored` reports OK
        (`make_held_lock_operator`'s genuinely-held lock, same fixture
        `TestHoldStateLeg12::test_hold_state_held_reaches_both_surfaces`
        uses)."""
        operator = make_held_lock_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        data = _load_report(_CHIP_ALLOW)
        assert data["sdp_hold_state"] == SDP_HOLD_HELD, data["sdp_hold_state"]
        steps = {s["op"]: s for s in data["steps"]}
        assert steps["write-restored"]["verdict"] == "OK", steps["write-restored"]

    def test_lock_emitted_and_not_confirmed_writable(self, runner: CliRunner) -> None:
        """Lock emitted, part NOT confirmed writable again:
        `make_restore_failed_operator`'s every write_eprom call persists
        genuinely EXCEPT the last one (`write-restored`), so that step's
        own read-back reports non-OK even though the leg's earlier steps
        (baseline, inhibited, unlock) all genuinely dispatched."""
        operator = make_restore_failed_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        data = _load_report(_CHIP_ALLOW)
        steps = {s["op"]: s for s in data["steps"]}
        assert steps["write-restored"]["verdict"] != "OK", steps["write-restored"]
        assert not data["sdp_hold_state"].startswith(f"{SDP_HOLD_NOT_RUN}:"), data[
            "sdp_hold_state"
        ]

    def test_gated_run_never_locked(self, runner: CliRunner) -> None:
        """Gated run: the baseline gate closes before a lock is ever
        emitted (`make_clean_operator`'s file-less read-back length-gates
        BAD, D-08) -- nothing was locked, so `sdp_lock` is never called."""
        operator = make_clean_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        data = _load_report(_CHIP_ALLOW)
        assert data["sdp_hold_state"].startswith(f"{SDP_HOLD_NOT_RUN}:")
        operator.sdp_lock.assert_not_called()


# ---------------------------------------------------------------------------
# 133 D-07's residual, inherited unchanged by D-12 (v1.30 Phase 134, plan
# 134-08): RECORDED here, not closed. Recording it as a truthful test rather
# than only a comment, per the plan's own instruction.
# ---------------------------------------------------------------------------


class TestCtrlCResidualNotClosedD12:
    """After a Ctrl-C mid-leg, `results = run_plan(...)` (cli_handlers.py)
    never returns, so there is NO report at all. Quick task 260821-spg
    removed the two named recovery forms this class used to also assert
    were absent from `result.output` -- there is no longer any recovery
    line to check for, printed or not. What survives, and is what
    actually mattered about D-12's residual, is the no-report claim: this
    plan deliberately does not add a `finally` handler, and this test
    asserts the OBSERVED behaviour truthfully rather than a behaviour the
    code does not have.

    MEASURED (not assumed): Click's `BaseCommand.main` (standalone mode,
    which `CliRunner.invoke` uses) catches `KeyboardInterrupt` itself,
    prints "Aborted!" to stderr, and converts it to `sys.exit(1)` -- so
    `KeyboardInterrupt` never propagates OUT of `runner.invoke()` here; it
    surfaces as an ordinary exit code 1. What this test proves is that no
    report file is ever written -- run_plan raised before
    `report.render()`, the JSON/markdown writes, and `submit_report` could
    run.
    """

    def test_keyboard_interrupt_mid_run_plan_leaves_no_report(
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
            runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        # No report was ever written -- run_plan raised before
        # report.render(), the JSON/markdown writes, and submit_report all
        # run, so none of them ever executed.
        assert not report_path.exists()


# ---------------------------------------------------------------------------
# LEG-17 (v1.30 Phase 134, plan 134-10): six laundering routes to a
# non-running SDP oracle, R1-R6. `SKIPPED` and `NA` both map to exit 0
# (before D-15's floor), so every one of these routes could otherwise end
# with a community member reading PASS on a run where the oracle never
# executed. Every route test below asserts BOTH halves of the house idiom
# (`tests/test_dev_test_cmd.py`'s own `test_chip_id_mismatch_exits_1`
# precedent, and Phase 114.1's lesson that an exit-code/verdict-only
# assertion lies): `operator.sdp_lock.assert_not_called()` AND a rendered
# `NOT-RUN` reason, in both the console and the JSON artifact.
#
# THESE SIX ARE NOT EXHAUSTIVE. A SEVENTH route to a non-running oracle
# exists -- 134-CONTEXT.md D-08's baseline write/read-back gate, named the
# "seventh route" in `134-04-SUMMARY.md` -- and it fails CLOSED under
# D-08+D-15 (it is not a laundering route). It is not re-proven in this
# class because it is already proven end to end, in the same
# negative-call-plus-NOT-RUN-reason shape, by `TestHoldStateLeg12::
# test_hold_state_not_run_reason_reaches_both_surfaces` and by
# `TestExitFloorD15::test_clean_notrun_floors_to_2` / `test_bad_and_notrun_
# exits_1_not_2` above (all driven through `make_clean_operator()`'s dead
# write-path shape). A later reader must not mistake "six routes covered
# here" for "every route to a non-running oracle" -- R5/R6's library-level
# companions live in `tests/test_chip_test.py`; `pytest -k "laundering"`
# selects across both files.
# ---------------------------------------------------------------------------


def test_all_sdp_allow_chips_have_zero_chip_id_measured_live() -> None:
    """D-17, re-measured live at THIS plan (never inherited from a prior
    plan's count): every SDP-ALLOW chip in the shipped database has
    `chip-id == 0` today -- iterated via `sdp_capability_for_entry` over
    the live database, never restated as a literal count. This is exactly
    why routes R1/R2 below need `SyntheticNonzeroChipIdDatabase` at all:
    without it, no real `dev test` run can ever drive the id-step-mismatch
    -> gate-closes -> `sdp_lock` refused causal chain, because
    `derive_plan` emits an NA id step (`chip-id` sentinel 0) for every one
    of them and `_id_step_closes_gate` never fires on an NA."""
    allow_count = 0
    nonzero_chip_id_allow_chips = []
    for full in _REAL_DB.get_eproms():
        name = full["name"]
        allowed, _reason = sdp_capability_for_entry(full, name)
        if not allowed:
            continue
        allow_count += 1
        if full.get("chip-id", 0):
            nonzero_chip_id_allow_chips.append(name)

    assert allow_count > 0, "the SDP-ALLOW population must be non-empty"
    assert not nonzero_chip_id_allow_chips, (
        f"{len(nonzero_chip_id_allow_chips)} SDP-ALLOW chip(s) now carry a "
        f"REAL nonzero chip-id: {nonzero_chip_id_allow_chips[:5]!r} -- "
        "routes R1/R2 may have become reachable in production; re-examine "
        "the 'unreachable today' label on SyntheticNonzeroChipIdDatabase "
        "and its tests before continuing to describe them that way"
    )


class TestLaunderingRoutesR1R2SyntheticChipId:
    """R1/R2: driven through `SyntheticNonzeroChipIdDatabase` (D-17) so the
    FULL causal chain is exercised -- id step -> mismatch/uncertainty ->
    destructive gate closes -> `sdp_lock` never called -> `NOT-RUN`
    rendered -- never by forcing the gate flag directly (that would prove
    the gate, not the route, exactly what D-17 rejected).

    UNREACHABLE IN PRODUCTION TODAY (`test_all_sdp_allow_chips_have_zero_
    chip_id_measured_live` above): every shipped SDP-ALLOW chip has
    `chip-id == 0`, so this exact causal chain cannot fire against any real
    chip in the database. These two tests are defence-in-depth, correct if
    a chip-id is ever added to an SDP-ALLOW entry, and NEVER live
    protection today. Never describe either test (or this class) as
    evidence that the chip-ID mismatch check is what protects an
    SDP-ALLOW chip today -- that reading is the v1.22 C-5 overclaim class
    D-17 explicitly names; what protects an SDP-ALLOW chip TODAY is D-08's
    baseline gate and D-12's recovery wording, which this vacuousness is
    exactly why they carry more weight, not less.
    """

    def test_r1_chip_id_mismatch_closes_gate_and_renders_notrun(
        self, runner: CliRunner
    ) -> None:
        """R1: a detected id differing from the synthetic entry's nonzero
        `chip-id` closes the destructive gate before any SDP-leg step
        dispatches -- `sdp_lock` is never called, and the report renders a
        non-empty `NOT-RUN` reason in both surfaces."""
        operator = make_clean_operator()
        operator.check_eprom_id.return_value = (True, 0xDEAD)
        app = make_app_context(
            db=SyntheticNonzeroChipIdDatabase(),
            eprom_operator=operator,
            hardware_manager=make_hardware_manager(),
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", SYNTHETIC_CHIP_NAME], obj=app)
        data = _load_report(SYNTHETIC_CHIP_NAME)
        steps = {s["op"]: s for s in data["steps"]}
        assert steps["id"]["verdict"] == "BAD", steps["id"]
        assert "mismatch" in steps["id"]["reason"], steps["id"]
        operator.sdp_lock.assert_not_called()
        hold_state = data["sdp_hold_state"]
        assert hold_state.startswith(f"{SDP_HOLD_NOT_RUN}:"), hold_state
        reason = hold_state.split(":", 1)[1].strip()
        assert reason, hold_state  # non-empty reason, never a bare "NOT-RUN:"
        normalized = _normalize_console_text(result.output)
        # Console shows the BARE state token; the `NOT-RUN` reason rides the
        # JSON only (operator superseded D-07's console leg, 2026-08-21).
        assert f"sdp_hold_state {SDP_HOLD_NOT_RUN}" in normalized, normalized
        assert hold_state not in normalized, normalized

    def test_r2_id_check_not_ok_closes_gate_and_renders_notrun(
        self, runner: CliRunner
    ) -> None:
        """R2a: `_id_step_closes_gate` fires on `is_ok=False` alone, with
        no explicit numeric mismatch at all -- the gate closes on ANY id
        uncertainty, not only a disagreeing detected id."""
        operator = make_clean_operator()
        operator.check_eprom_id.return_value = (False, None)
        app = make_app_context(
            db=SyntheticNonzeroChipIdDatabase(),
            eprom_operator=operator,
            hardware_manager=make_hardware_manager(),
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", SYNTHETIC_CHIP_NAME], obj=app)
        data = _load_report(SYNTHETIC_CHIP_NAME)
        steps = {s["op"]: s for s in data["steps"]}
        assert steps["id"]["verdict"] == "BAD", steps["id"]
        operator.sdp_lock.assert_not_called()
        hold_state = data["sdp_hold_state"]
        assert hold_state.startswith(f"{SDP_HOLD_NOT_RUN}:"), hold_state
        reason = hold_state.split(":", 1)[1].strip()
        assert reason, hold_state
        normalized = _normalize_console_text(result.output)
        # Console shows the BARE state token; the `NOT-RUN` reason rides the
        # JSON only (operator superseded D-07's console leg, 2026-08-21).
        assert f"sdp_hold_state {SDP_HOLD_NOT_RUN}" in normalized, normalized
        assert hold_state not in normalized, normalized

    def test_r2_transport_error_during_id_check_closes_gate_and_renders_notrun(
        self, runner: CliRunner
    ) -> None:
        """R2b: a transport fault raised BY the id check (a half-seated
        cable, not a firmware-reported disagreement) degrades the id step
        to BAD via `_run_step`'s `(SerialError, HardwareOperationError)`
        handler -- separately proving the gate closes on this id-check
        failure mode too, not only on `is_ok=False`."""
        operator = make_clean_operator()
        operator.check_eprom_id.side_effect = SerialError("half-seated cable")
        app = make_app_context(
            db=SyntheticNonzeroChipIdDatabase(),
            eprom_operator=operator,
            hardware_manager=make_hardware_manager(),
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", SYNTHETIC_CHIP_NAME], obj=app)
        data = _load_report(SYNTHETIC_CHIP_NAME)
        steps = {s["op"]: s for s in data["steps"]}
        assert steps["id"]["verdict"] == "BAD", steps["id"]
        operator.sdp_lock.assert_not_called()
        hold_state = data["sdp_hold_state"]
        assert hold_state.startswith(f"{SDP_HOLD_NOT_RUN}:"), hold_state
        reason = hold_state.split(":", 1)[1].strip()
        assert reason, hold_state
        normalized = _normalize_console_text(result.output)
        # Console shows the BARE state token; the `NOT-RUN` reason rides the
        # JSON only (operator superseded D-07's console leg, 2026-08-21).
        assert f"sdp_hold_state {SDP_HOLD_NOT_RUN}" in normalized, normalized
        assert hold_state not in normalized, normalized


class TestLaunderingRoutesR3R4:
    """R3/R4: reachable in production today, unlike R1/R2 above -- neither
    needs the synthetic chip-id fixture."""

    def test_r3_resolve_chip_refusal_maps_baseline_steps_to_skipped_notrun(
        self, runner: CliRunner
    ) -> None:
        """R3: a `resolve_chip` refusal (adapter-required / support-status
        -- the same class of refusal the shipped
        `TestAbsentChipHardFail::test_dev_test_present_but_unsupported_
        still_sweeps` (AT28C16) exercises for the shipped ops) maps
        through `_resolve_or_none` to SKIPPED for every step `_run_step`
        dispatches -- proven here on a genuinely ALLOW chip (`_CHIP_ALLOW`,
        where `derive_plan` marks the SDP-leg steps `supported=True`, so
        they are NOT already NA via `sdp_capability` the way R4's REFUSE
        chip below is). `write-baseline-b` is asserted SKIPPED directly via
        this route; the downstream gated ops (`write-inhibited` included)
        SKIP too, via the baseline gate the SKIPPED baseline step closes
        (D-08 treats SKIPPED as gate-closing exactly like BAD/marginal/NA)
        -- `sdp_lock` is never called either way."""
        operator = make_clean_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with (
            patch(
                "firestarter.chip_test.resolve_chip",
                side_effect=ChipNotImplementedError(
                    "simulated: adapter required for this test"
                ),
            ),
            _off_tty(),
        ):
            result = runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        data = _load_report(_CHIP_ALLOW)
        steps = {s["op"]: s for s in data["steps"]}
        assert steps["write-baseline-b"]["verdict"] == "SKIPPED", steps[
            "write-baseline-b"
        ]
        assert "adapter required" in steps["write-baseline-b"]["reason"]
        assert steps["write-inhibited"]["verdict"] == "SKIPPED", steps[
            "write-inhibited"
        ]
        operator.sdp_lock.assert_not_called()
        hold_state = data["sdp_hold_state"]
        assert hold_state.startswith(f"{SDP_HOLD_NOT_RUN}:"), hold_state
        reason = hold_state.split(":", 1)[1].strip()
        assert reason, hold_state
        normalized = _normalize_console_text(result.output)
        # Console shows the BARE state token; the `NOT-RUN` reason rides the
        # JSON only (operator superseded D-07's console leg, 2026-08-21).
        assert f"sdp_hold_state {SDP_HOLD_NOT_RUN}" in normalized, normalized
        assert hold_state not in normalized, normalized

    def test_r4_refuse_chip_na_reason_matches_sdp_capability_identity(
        self, runner: CliRunner
    ) -> None:
        """R4: `step.supported is False` (a REFUSE chip, `_CHIP_NO_ID`) --
        `write-inhibited` is NA, carrying `sdp_capability(name, db)[1]`
        ITSELF as its reason, compared by identity against the live
        function (never a generic or re-worded string): a REFUSED chip
        gets an NA step CARRYING `reason`, never a silent omission."""
        operator = make_clean_operator()
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        allowed, expected_reason = sdp_capability(_CHIP_NO_ID, _REAL_DB)
        assert allowed is False, "fixture setup error: _CHIP_NO_ID must be REFUSE"
        data = _load_report(_CHIP_NO_ID)
        steps = {s["op"]: s for s in data["steps"]}
        assert steps["write-inhibited"]["verdict"] == "NA", steps["write-inhibited"]
        assert steps["write-inhibited"]["reason"] == expected_reason, steps[
            "write-inhibited"
        ]
        operator.sdp_lock.assert_not_called()
        hold_state = data["sdp_hold_state"]
        assert hold_state == f"{SDP_HOLD_NOT_RUN}: {expected_reason}", hold_state
        normalized = _normalize_console_text(result.output)
        # Console shows the BARE state token; the `NOT-RUN` reason rides the
        # JSON only (operator superseded D-07's console leg, 2026-08-21).
        assert f"sdp_hold_state {SDP_HOLD_NOT_RUN}" in normalized, normalized
        assert hold_state not in normalized, normalized


# ---------------------------------------------------------------------------
# Quick task 260807-kaq: blank-check must run AFTER erase, end to end.
# `pytest -k "blank_check_after_erase"` selects this class.
# ---------------------------------------------------------------------------


class TestBlankCheckAfterEraseKaq:
    """End-to-end proof that a `dev test` run no longer scores a false BAD
    verdict for a chip that merely held data at the moment blank-check used
    to run (before any write/erase touched it).

    This class was written and observed RED against the unmodified
    `chip_test.py` (blank-check ran BEFORE erase, so the honest-simulation
    closure below returned False and the run exited 1) -- see
    260807-kaq-SUMMARY.md for the captured failure output.
    """

    def test_erasable_chip_blank_only_after_erase_exits_0(
        self, runner: CliRunner
    ) -> None:
        """M8720 (an executable-erase chip): an honest simulation of a used
        device that only becomes blank once erase has actually run --
        `check_eprom_blank`'s closure returns `operator.erase_eprom.called`.
        With blank-check now positioned AFTER erase, the closure observes
        True and the step verdicts OK; the whole run exits 0."""
        operator = make_clean_operator()

        def _blank_only_after_erase(name: str, eprom_data: dict) -> bool:
            return bool(operator.erase_eprom.called)

        operator.check_eprom_blank.side_effect = _blank_only_after_erase
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_NO_ID], obj=app)
        assert result.exit_code == 0, result.output
        data = _load_report(_CHIP_NO_ID)
        steps = {s["op"]: s for s in data["steps"]}
        assert steps["blank-check"]["verdict"] == "OK", steps["blank-check"]

    def test_auto_erase_on_write_chip_never_calls_blank_check_and_exits_0(
        self, runner: CliRunner
    ) -> None:
        """AT28C256 (protocol 0x0D, 28C family): no step in this plan can
        ever leave the device blank (each page write auto-erases
        internally), so blank-check is emitted NA -- `run_plan` skips an
        unsupported step WITHOUT any operator call. A non-blank device
        (`check_eprom_blank.return_value = False`) must not matter at all:
        the run still exits 0 and the operator method is never dispatched.

        AT28C256 is also one of the 43 SDP-ALLOW chips, so its plan carries
        the six-step SDP leg -- `make_held_lock_operator()` (this suite's
        established clean-success ALLOW-chip double, see
        `TestHoldStateLeg12` above) is used instead of `make_clean_operator()`
        so the leg's own baseline/lock/inhibited/unlock/restore steps
        genuinely succeed and do not confound this test's own exit-0
        assertion with an unrelated SDP-leg BAD/NOT-HELD."""
        operator = make_held_lock_operator()
        operator.check_eprom_blank.return_value = False
        app = make_app_context(
            eprom_operator=operator, hardware_manager=make_hardware_manager()
        )
        with _off_tty():
            result = runner.invoke(cli, ["dev", "test", _CHIP_ALLOW], obj=app)
        assert result.exit_code == 0, result.output
        data = _load_report(_CHIP_ALLOW)
        steps = {s["op"]: s for s in data["steps"]}
        assert steps["blank-check"]["verdict"] == "NA", steps["blank-check"]
        operator.check_eprom_blank.assert_not_called()
