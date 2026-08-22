"""The firmware's own failure explanation must reach the diagnostic report.

Debug session w27c512-devtest-all-bad (henols/firestarter_prom#41).

**The defect these tests pin.** `EpromOperator._run_state_machine` catches
`EpromOperationError` -- the exception that carries the firmware's
`response.id` as `error_code` -- and returns `(False, str(e))`.
`write_eprom` and `verify_eprom` then throw the string away
(`is_ok, _ = ...`); `erase_eprom` and `check_eprom_blank` keep it but only
log it on SUCCESS. All four return a bare bool. So
`chip_test._run_step`'s `except EpromOperationError: ... error_code=
exc.error_code` handler is STRUCTURALLY UNREACHABLE for those four ops, and
every failing write/verify/erase/blank-check step in a `dev test` report
came out carrying `error_code: null` and `reason: ""`.

That is how issue #41 came to read as four independent faults with no
evidence attached. The firmware had in fact already named the cause: for a
blank-check failure `mem_util_blank_check` emits `MSG_ERR_NOT_BLANK` (0xB0)
with the offending three-byte ADDRESS and the byte VALUE it read -- the
single most useful datum in a `dev test` failure, and the host was dropping
it on the floor.

**Why an operator attribute rather than a signature change.** The four
methods' `-> bool` contract is relied on directly by `cli_handlers.py`'s
`write` / `verify` / `blank` / `erase` commands (each ends in
`sys.exit(0 if ok else 1)` on the bare bool -- verified against
`origin/beta`), by `chip_test.py`, and by a large body of test doubles.
`EpromOperator` therefore records the pair on itself and `chip_test`
reads it back. `_run_state_machine` clears both on entry, so a value can
never be stale.

Coverage:
  1. the state machine records id + text off a real firmware ERROR frame
  2. a SUCCESSFUL operation leaves both cleared (no stale carry-over)
  3. a failing blank-check step carries the id and the text
  4. a failing erase step carries them
  5. a failing write step carries them
  6. an OK step carries neither
  7. the D-06 `marginal` wording is NOT overwritten, but the code still
     attaches
  8. an operator double lacking the attributes degrades to the exact
     pre-existing behaviour -- `(None, "")`, never an AttributeError
  9. the end-to-end serialised report carries both fields
 10. `dedup_fingerprint` is byte-identical with and without the new text --
     no historical issue group is re-keyed by this change
"""

from __future__ import annotations

from unittest.mock import Mock

from firestarter import chip_test as ct
from firestarter.chip_resolver import resolve_chip
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.diagnostic_report import (
    AutoCapture,
    DiagnosticReport,
    TransportHealth,
    dedup_fingerprint,
)
from firestarter.eprom_operations import EpromOperator
from firestarter.messages import MSG_ERR_NOT_BLANK

from .conftest import build_frame

_REAL_DB = EpromDatabase(skip_local_override=True)
_CHIP = "W27C512"

# MSG_ERR_NOT_BLANK (0xB0) params are (addr_hi, addr_mid, addr_lo, value) --
# the four bytes mem_util_blank_check pushes through LOG_ERROR_ID_BYTES. This
# payload is "address 0x000000 read back as 0xAB", i.e. exactly the byte-0
# shape a partially-erased W27C512 produces.
_NOT_BLANK_PARAMS = bytes([0x00, 0x00, 0x00, 0xAB])

_OPERATOR_METHODS = [
    "check_eprom_id",
    "read_eprom",
    "check_eprom_blank",
    "write_eprom",
    "verify_eprom",
    "erase_eprom",
    "sdp_lock",
    "sdp_unlock",
]


def _failing_operator(*, failing: str, code: int = MSG_ERR_NOT_BLANK, text: str):
    """A chip double whose `failing` method returns False and which carries
    the two recorded-firmware-error attributes a real `EpromOperator` sets.

    Deliberately NOT `Mock(spec=...)`: a spec'd Mock refuses unlisted
    attributes, which is the case covered separately by test 8. This double
    is the "real operator shape" arm.
    """
    operator = Mock()
    operator.check_eprom_id.return_value = (True, 0xDA08)
    operator.read_eprom.return_value = True
    for method in ("check_eprom_blank", "write_eprom", "verify_eprom", "erase_eprom"):
        getattr(operator, method).return_value = method != failing
    operator.last_firmware_error_code = code
    operator.last_firmware_error_message = text
    return operator


def _eprom_data():
    return resolve_chip(_CHIP, db=_REAL_DB)


# ---------------------------------------------------------------------------
# 1-2: the recording seam in eprom_operations.py
# ---------------------------------------------------------------------------


def test_state_machine_records_the_firmware_id_and_text_off_an_error_frame(
    fake_serial, make_comm
) -> None:
    """Coverage 1. A real 0xB0 ERROR frame driven through the state machine
    leaves `last_firmware_error_code` == 0xB0 and a non-empty message, while
    still returning the historic `(False, msg)` tuple -- the bool contract
    every existing caller depends on is unchanged."""
    operator = EpromOperator(ConfigManager())
    operator.comm = make_comm()
    fake_serial.feed(build_frame(MSG_ERR_NOT_BLANK, _NOT_BLANK_PARAMS))

    is_ok, final_msg = operator._run_state_machine("erase")

    assert is_ok is False
    assert operator.last_firmware_error_code == MSG_ERR_NOT_BLANK
    assert operator.last_firmware_error_message
    # The recorded text is the same string the tuple already carried -- this
    # adds a route, it does not invent a second rendering.
    assert operator.last_firmware_error_message == final_msg


def test_a_cleared_slot_cannot_carry_a_previous_operations_failure(
    fake_serial, make_comm
) -> None:
    """Coverage 2. `_run_state_machine` clears both attributes on entry, so a
    second call that does NOT hit an ERROR frame cannot report the first
    call's failure. Without the clear, every step after the first failure
    would inherit its error code -- a worse defect than the one being
    fixed."""
    operator = EpromOperator(ConfigManager())
    operator.comm = make_comm()
    fake_serial.feed(build_frame(MSG_ERR_NOT_BLANK, _NOT_BLANK_PARAMS))
    operator._run_state_machine("erase")
    assert operator.last_firmware_error_code == MSG_ERR_NOT_BLANK

    # Second call: no ERROR frame fed. Whatever it returns, the slot must be
    # empty -- the assertion under test is the CLEAR, not the outcome.
    operator.comm = make_comm()
    operator._run_state_machine("erase")

    assert operator.last_firmware_error_code is None
    assert operator.last_firmware_error_message is None


# ---------------------------------------------------------------------------
# 3-7: the consuming seam in chip_test.py
# ---------------------------------------------------------------------------


def test_failing_blank_check_step_carries_the_firmware_id_and_text() -> None:
    """Coverage 3 -- the step where it matters most. `MSG_ERR_NOT_BLANK`
    names the offending address and the byte read there; before this fix the
    blank-check row said only `BAD`."""
    text = "Blank check failed at 0x000000, read 0xAB"
    operator = _failing_operator(failing="check_eprom_blank", text=text)
    step = ct.Step(op=ct.OP_BLANK_CHECK, supported=True, reason="")

    result = ct._dispatch_step(_CHIP, step, _eprom_data(), operator, runs=1)

    assert result.verdict == ct.VERDICT_BAD
    assert result.error_code == MSG_ERR_NOT_BLANK
    assert result.reason == text


def test_failing_erase_step_carries_the_firmware_id_and_text() -> None:
    """Coverage 4. The erase step is the one the w27c512 root cause actually
    fails at -- the firmware's CMD_ERASE END phase is a full-device blank
    check, so a partial erase surfaces here as MSG_ERR_NOT_BLANK."""
    text = "Blank check failed at 0x000000, read 0xAB"
    operator = _failing_operator(failing="erase_eprom", text=text)
    step = ct.Step(op=ct.OP_ERASE, supported=True, reason="", destructive=True)

    result = ct._dispatch_step(_CHIP, step, _eprom_data(), operator, runs=1)

    assert result.verdict == ct.VERDICT_BAD
    assert result.error_code == MSG_ERR_NOT_BLANK
    assert result.reason == text


def test_failing_write_step_carries_the_firmware_id_and_text() -> None:
    """Coverage 5. `eprom_write_init` runs erase-then-blank-check, so the
    write step fails with the SAME firmware error as the erase step -- which
    is precisely the evidence that made four BAD verdicts one cause rather
    than four."""
    text = "Blank check failed at 0x000000, read 0xAB"
    operator = _failing_operator(failing="write_eprom", text=text)
    step = ct.Step(
        op=ct.OP_WRITE,
        supported=True,
        reason="",
        destructive=True,
        write_region=(0, 256),
        region_policy=ct.REGION_POLICY_FIXED,
    )

    result = ct._dispatch_step(
        _CHIP, step, _eprom_data(), operator, runs=1, collect_fingerprint=False
    )

    assert result.verdict == ct.VERDICT_BAD
    assert result.error_code == MSG_ERR_NOT_BLANK
    assert result.reason == text


def test_an_ok_step_carries_no_error_code_and_no_reason() -> None:
    """Coverage 6. The attach is gated on a non-OK verdict, so a passing step
    cannot pick up a code -- not even one an earlier operation left on the
    operator (belt and suspenders with Coverage 2's clear)."""
    operator = _failing_operator(failing="none-of-them", text="stale text")
    step = ct.Step(op=ct.OP_ERASE, supported=True, reason="", destructive=True)

    result = ct._dispatch_step(_CHIP, step, _eprom_data(), operator, runs=1)

    assert result.verdict == ct.VERDICT_OK
    assert result.error_code is None
    assert result.reason == ""


def test_marginal_keeps_its_policy_wording_but_still_reports_the_code() -> None:
    """Coverage 7. The D-06 `marginal` reason states a POLICY decision this
    module made; a per-run firmware detail must not overwrite it. The
    `error_code` is a separate field and never competes, so it still
    attaches -- a marginal step has at least one failed run and its code is
    just as diagnostic as a BAD one's."""
    operator = Mock()
    operator.erase_eprom.side_effect = [True, False]
    operator.last_firmware_error_code = MSG_ERR_NOT_BLANK
    operator.last_firmware_error_message = "Blank check failed at 0x000000, read 0xAB"
    step = ct.Step(op=ct.OP_ERASE, supported=True, reason="", destructive=True)

    result = ct._dispatch_step(_CHIP, step, _eprom_data(), operator, runs=2)

    assert result.verdict == ct.VERDICT_MARGINAL
    assert "disagreed on outcome" in result.reason
    assert result.error_code == MSG_ERR_NOT_BLANK


def test_an_operator_without_the_attributes_degrades_to_the_old_behaviour() -> None:
    """Coverage 8. `_firmware_error` uses `getattr` with defaults precisely so
    a stand-in that never grew these attributes -- which is every `Mock(spec=
    ...)` double in this suite -- keeps producing the exact pre-fix
    `StepResult`, rather than raising inside a failure path."""
    operator = Mock(spec=_OPERATOR_METHODS)
    operator.erase_eprom.return_value = False
    step = ct.Step(op=ct.OP_ERASE, supported=True, reason="", destructive=True)

    assert ct._firmware_error(operator) == (None, "")

    result = ct._dispatch_step(_CHIP, step, _eprom_data(), operator, runs=1)
    assert result.verdict == ct.VERDICT_BAD
    assert result.error_code is None
    assert result.reason == ""


# ---------------------------------------------------------------------------
# 9-10: the report surface
# ---------------------------------------------------------------------------


def _report_with(results: list[ct.StepResult]) -> DiagnosticReport:
    return DiagnosticReport(
        auto_capture=AutoCapture(host_version="test", chip=_CHIP.lower(), protocol="7"),
        transport=TransportHealth(),
        plan=ct.Plan(name=_CHIP, steps=[]),
        results=results,
    )


def test_the_serialised_report_carries_both_fields() -> None:
    """Coverage 9 -- the end of the pipe. This is the assertion that would
    have made issue #41 triageable in one pass instead of a debug session."""
    text = "Blank check failed at 0x000000, read 0xAB"
    result = ct.StepResult(
        op=ct.OP_ERASE,
        verdict=ct.VERDICT_BAD,
        reason=text,
        error_code=MSG_ERR_NOT_BLANK,
        run_count=2,
    )
    exported = _report_with([result]).to_dict()

    (step_dict,) = exported["steps"]
    assert step_dict["error_code"] == MSG_ERR_NOT_BLANK
    assert step_dict["reason"] == text


def test_dedup_fingerprint_is_unchanged_by_the_new_text() -> None:
    """Coverage 10 -- the no-re-key property, and the reason this fix is safe
    to land mid-ladder. `dedup_fingerprint` hashes only chip, protocol, and
    per-step `op`/`verdict`/`classification` (plus the repeat-policy tag), and
    documents `error_code` and `reason` as deliberately EXCLUDED. Populating
    them therefore cannot move a single historical issue group or reset a
    promotion count. Asserted, not trusted."""
    bare = ct.StepResult(op=ct.OP_ERASE, verdict=ct.VERDICT_BAD, run_count=2)
    enriched = ct.StepResult(
        op=ct.OP_ERASE,
        verdict=ct.VERDICT_BAD,
        reason="Blank check failed at 0x000000, read 0xAB",
        error_code=MSG_ERR_NOT_BLANK,
        run_count=2,
    )

    assert dedup_fingerprint(_report_with([bare])) == dedup_fingerprint(
        _report_with([enriched])
    )
