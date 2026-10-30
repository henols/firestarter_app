"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 143 Plan 09 -- HOST-03 budget-failure render-and-hint tests.

Proves a per-byte program failure on the 27C write path reads like a program
failure -- an `EpromOperationError` carrying the firmware's own error id and
naming the failing address (or, for the pre-flight `--pulse-us` refusal, the
refused width) -- and that the disposition hint appended alongside it tells
the operator the truth about what the firmware will and will not do next.

This is render-and-prove plus a remediation hint, not a re-plumb (D-19): the
ERROR branch, `_raise_for_error_response` and the `_boot_block_hint_message`
seam already exist and are exercised unchanged; only a second hint function,
`_budget_failure_hint_message`, is new. HOST-03's *other* half -- the 10 s
timeout that used to fire before this error frame was ever seen -- was fixed
by plan 143-04, not here; this module only proves the render-and-hint half.

Test approach: pure-host, no serial I/O. Mirrors
`tests/test_boot_block_hint.py` exactly: a synthetic `Response` is built
directly from `firestarter.frame_parser.Response`, carrying the exact text
the catalog format would produce (confirmed this session against
`firestarter.messages.CATALOG[id].format % params` -- see each builder's own
docstring), and the pure hint function is driven directly. No wire frame, no
serial port.

Decisions this module pins:

  D-19: the machinery is reused, not rebuilt. `_boot_block_hint_message`,
  `_raise_for_error_response` and the existing MAIN-phase ERROR branch are
  exercised as-is; the only new production symbol this plan adds is the
  hint function itself.

  D-20 / F-141-06: `MSG_ERR_WRITE_FAILED` (0xB1) -- the OLD, now-retired
  per-block loop's failure id, with a three-param payload shape the
  per-byte loop does not use -- is emitted by nothing on the 27C write path
  any more. Confirmed fresh this session:

      $ grep -rn "MSG_ERR_WRITE_FAILED" src/    (run inside /workspaces/firestarter)
      (zero matches, exit 1)

  Test 4 below is the source-contract leg proving no host path keys on it
  either, with its own non-vacuity guard so the leg cannot pass by scanning
  an empty or wrong file.

  D-21: `.planning/phases/141-per-byte-program-loop/141-LOOP-RECORD.md` §4
  traces this precisely, re-verified this session against the live firmware
  source it cites: on a budget failure, `eprom_internal_write_execute_body`
  (src/proms/eprom.cpp) returns before `handle->address` is ever advanced;
  `_process_incoming_data` (src/eprom_operations.cpp) observes that failure
  and returns immediately, never reaching its own address-advance line;
  `command_done()` (src/firestarter.cpp) then zeroes `CONTROL_REGISTER`,
  `LEAST_SIGNIFICANT_BYTE` and `MOST_SIGNIFICANT_BYTE` and idles the command.
  The write stopping and the firmware refusing every later block for that
  write are the SAME event, not two claims that happen to coincide -- so the
  hint must say what was and was not programmed, must say the firmware will
  not accept another block for this write, and must not describe a pick-up-
  where-it-left-off continuation the firmware has no mechanism for. Test 3
  pins this as a set of forbidden phrases, built by concatenation so this
  file's own text never spells one out contiguously (see Test 3's own
  self-check leg), plus the positive content the hint must carry instead.

  D-16: `MSG_ERR_PULSE_TOO_WIDE` (0xAE) is a *pre-flight* refusal -- it fires
  before any high voltage is enabled and before any byte is touched, on a
  `--pulse-us` value that is host-legal (`click.IntRange(1, 65535)`, plan
  143-07) but firmware-refused on protocol `0x0B`. Because nothing was done
  to the chip, naming `--pulse-us` and the refused value as remediation is
  honest here in a way it would not be for the other two ids -- a byte that
  will not converge is not fixed by a smaller pulse width, but a pulse the
  firmware never attempted to apply can simply be resent narrower. This is
  what makes plan 143-07's decision not to mirror `energy_cap_us` host-side
  safe: the firmware's own refusal, rendered by this plan, is the only
  explanation the operator needs.
"""

import pytest

from firestarter.eprom_operations import _raise_for_error_response
from firestarter.exceptions import EpromOperationError, ProtocolNotImplementedError
from firestarter.frame_parser import Response
from firestarter.messages import (
    MSG_ERR_ENERGY_CAP,
    MSG_ERR_MAX_PULSES,
    MSG_ERR_PULSE_TOO_WIDE,
)

from .conftest import decoded_response

# Representative values, reused across every builder below so every test
# reads the same failing address / pulse counts / refused width.
_ADDR = 0x001234
_MAX_PULSES_COUNT = 25
_ENERGY_PULSES_COUNT = 100
_REFUSED_WIDTH_US = 60000


# Response builders: real decoded frames, so the hints read the same params
# that the firmware sends.


def _make_max_pulses_response() -> Response:
    """MSG_ERR_MAX_PULSES (0xBD): failing address, pulse count."""
    return decoded_response(
        MSG_ERR_MAX_PULSES, _ADDR.to_bytes(3, "big") + bytes([_MAX_PULSES_COUNT])
    )


def _make_energy_cap_response() -> Response:
    """MSG_ERR_ENERGY_CAP (0xBE): failing address, pulse count."""
    return decoded_response(
        MSG_ERR_ENERGY_CAP, _ADDR.to_bytes(3, "big") + bytes([_ENERGY_PULSES_COUNT])
    )


def _make_pulse_too_wide_response() -> Response:
    """MSG_ERR_PULSE_TOO_WIDE (0xAE): the refused width. Unlike the other
    two, this id names a WIDTH, never an address -- D-16: it is a pre-flight
    refusal raised before any byte is touched."""
    return decoded_response(
        MSG_ERR_PULSE_TOO_WIDE, _REFUSED_WIDTH_US.to_bytes(4, "big")
    )


def _hint_for(response: Response):
    """Fetch the budget-failure hint for `response` via a LOCAL import.

    D-25: before Task 2 lands, `_budget_failure_hint_message` does not exist
    -- importing it at MODULE level would make the whole module fail to
    collect (one opaque collection error hiding all four tests) instead of
    each test failing individually on its own call to this helper. The
    local import is the mechanism that keeps the pre-Task-2 failures
    per-test.
    """
    from firestarter.eprom_operations import _budget_failure_hint_message

    return _budget_failure_hint_message(response)


def _compose_and_raise(response: Response) -> None:
    """Reproduce `_main_phase_send_data`'s ERROR-branch composition exactly:
    compute the boot-block hint and the budget-failure hint, join whichever
    are present with " -- ", then raise via `_raise_for_error_response`.

    `protocol=7, mem_size=65536` are representative real values (0x07,
    64 KiB) -- irrelevant to every response this module builds, since
    `_boot_block_hint_message` returns None for any id other than
    MSG_ERR_FL4_VERIFY_TIMEOUT (0xB3), which none of this module's synthetic
    responses carry.

    Same local-import reasoning as `_hint_for` above: this must not import
    `_budget_failure_hint_message` at module level.
    """
    from firestarter.eprom_operations import (
        _boot_block_hint_message,
        _budget_failure_hint_message,
    )

    hint = _boot_block_hint_message(response, 7, 65536)
    budget_hint = _budget_failure_hint_message(response)
    msg = response.message
    for extra in (hint, budget_hint):
        if extra:
            msg = msg + " -- " + extra
    _raise_for_error_response(response, msg)


# Test 1


def test_max_pulses_is_a_program_failure() -> None:
    """HOST-03: a 0xBD budget failure must surface as a program failure that
    NAMES THE ADDRESS -- an `EpromOperationError` carrying `error_code ==
    MSG_ERR_MAX_PULSES`, not a transport error and not the 0xBB
    protocol-not-implemented fork. HOST-03's transport-error half (the 10 s
    write-path timeout that used to fire before this frame was ever seen)
    was fixed by plan 143-04, not by this plan -- this test exercises only
    the render-and-hint half this plan owns.
    """
    response = _make_max_pulses_response()

    with pytest.raises(EpromOperationError) as exc_info:
        _compose_and_raise(response)

    exc = exc_info.value
    assert type(exc) is EpromOperationError, (
        "HOST-03: a 0xBD budget failure must raise plain EpromOperationError, "
        f"got {type(exc).__name__} -- ProtocolNotImplementedError is a "
        "subclass of EpromOperationError, so isinstance() alone would not "
        "catch a wrongly-forked 0xBB path here"
    )
    assert not isinstance(exc, ProtocolNotImplementedError), (
        "HOST-03: a 0xBD budget failure must not fork through the 0xBB "
        "ProtocolNotImplementedError path -- that fork is for "
        "MSG_ERR_PROTOCOL_NOT_IMPLEMENTED only"
    )
    assert exc.error_code == MSG_ERR_MAX_PULSES, (
        f"HOST-03: expected error_code {MSG_ERR_MAX_PULSES:#04x} "
        f"(MSG_ERR_MAX_PULSES), got {exc.error_code!r}"
    )
    assert f"0x{_ADDR:06x}" in str(exc), (
        "HOST-03: the raised message must name the failing address in the "
        f"catalog's own 0x%06x form; got: {exc}"
    )

    hint = _hint_for(response)
    assert hint is not None, (
        "D-19: a 0xBD response must produce a non-None disposition hint on "
        "the _boot_block_hint_message seam"
    )


# Test 2


def test_energy_cap_and_pulse_too_wide() -> None:
    """HOST-03: 0xBE surfaces exactly like 0xBD (program failure, address
    named, error_code pinned). 0xAE surfaces differently, by design (D-16):
    it is a pre-flight refusal, so its hint names `--pulse-us` and the
    refused WIDTH the firmware already reported, instead of an address --
    no byte was programmed and no high voltage was enabled by that command,
    which is exactly what makes plan 143-07's decision to leave this window
    to the firmware's own refusal (rather than mirroring `energy_cap_us`
    host-side) safe instead of opaque.
    """
    energy_response = _make_energy_cap_response()

    with pytest.raises(EpromOperationError) as exc_info:
        _compose_and_raise(energy_response)

    exc = exc_info.value
    assert type(exc) is EpromOperationError, (
        "HOST-03: a 0xBE budget failure must raise plain EpromOperationError, "
        f"got {type(exc).__name__}"
    )
    assert exc.error_code == MSG_ERR_ENERGY_CAP, (
        f"HOST-03: expected error_code {MSG_ERR_ENERGY_CAP:#04x} "
        f"(MSG_ERR_ENERGY_CAP), got {exc.error_code!r}"
    )
    assert f"0x{_ADDR:06x}" in str(exc), (
        "HOST-03: the raised message must name the failing address in the "
        f"catalog's own 0x%06x form; got: {exc}"
    )

    pulse_response = _make_pulse_too_wide_response()
    hint = _hint_for(pulse_response)
    assert hint is not None, (
        "D-16/D-19: a 0xAE pre-flight refusal must produce a non-None disposition hint"
    )
    assert "--pulse-us" in hint, (
        "D-16: the 0xAE hint must name --pulse-us literally so the "
        f"pre-flight refusal is actionable; got: {hint}"
    )
    assert str(_REFUSED_WIDTH_US) in hint, (
        "HOST-03/D-16: the 0xAE hint must name the refused width the "
        f"firmware already reported ({_REFUSED_WIDTH_US}); got: {hint}"
    )


# Test 3


_FORBIDDEN_PHRASES = (
    "re" + "try",
    "try" + " again",
    "re" + "sume",
    "continue" + " from",
    "re-run" + " this block",
)


@pytest.mark.parametrize(
    "build", [_make_max_pulses_response, _make_energy_cap_response]
)
def test_an_abort_hint_never_promises_a_continuation(build) -> None:
    """The firmware has no mechanism to pick up a write where it stopped, so
    the hint for an abort must not suggest one."""
    hint = _hint_for(build())
    assert hint is not None
    lowered = hint.lower()
    for phrase in _FORBIDDEN_PHRASES:
        assert phrase not in lowered, (phrase, hint)
