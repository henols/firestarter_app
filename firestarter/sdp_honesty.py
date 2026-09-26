"""Shared honesty carrier for SDP (Software Data Protection) wording.

Holds the unreadable-state caveat and the unknown-command-to-outdated-firmware
mapping. Neither can live in the operation layer: `serial_comm.get_response()`
filters the entire INFO band, so that layer never sees the firmware duration
frame the caveat exists to disclaim.

`unreadable_state_caveat()`'s TEXT is load-bearing at seven call sites and is
pinned by tests -- extend this module additively rather than re-authoring what
is here.
"""

from __future__ import annotations

# Import-set invariant (checked by an AST-based test):
# this module's top-level import set is a subset of
# {"__future__", "firestarter.exceptions", "firestarter.messages"}, both leaf
# modules (`exceptions.py` has zero top-level imports; `messages.py` imports
# only `dataclasses`). In particular, no `click` -- the caller performs the
# echo, so a `click` dependency here would make this module unusable from the
# report layer, which has no CLI context of its own.
from firestarter.exceptions import EpromOperationError, FirmwareOutdatedError
from firestarter.messages import MSG_ERR_UNKNOWN_CMD


def unreadable_state_caveat() -> str:
    """Return the caveat clause alone, byte-identical to the wording
    formerly composed inline in `cli_handlers.py`'s retired `dev sdp`
    subcommand. Exposed separately from `emission_summary` because
    Report rows need the clause without the emission preamble.
    """
    return (
        "The resulting protection state cannot be read back on this chip "
        "family, so this is not a claim about the chip's actual state."
    )


def emission_summary(mode: str, chip_name: str) -> str:
    """Return the full single-line summary for an emitted SDP sequence.

    Honest and symmetric on both directions: the claim is that the
    sequence was EMITTED, never that the resulting state was verified. No
    duration figure appears here -- this is mechanically enforced, not
    merely a discipline: `get_response()` filters the entire INFO band out
    at `serial_comm.py:424`, so the operation layer literally cannot see the
    firmware's `0x5F`/`0x61` duration frame to plumb one through. No
    lock/unlock state boolean appears either -- the honesty floor.

    Composed by calling `unreadable_state_caveat()` rather than duplicating
    its wording, so the two can never drift. `chip_name` is uppercased here
    so a caller cannot forget to. Never appends a newline --
    the caller echoes.
    """
    chip_upper = chip_name.upper()
    return (
        f"SDP {mode} sequence for {chip_upper} was emitted. {unreadable_state_caveat()}"
    )


def map_unknown_cmd_to_outdated(
    exc: EpromOperationError, mode: str, chip_name: str
) -> FirmwareOutdatedError | None:
    """Map an unknown-command error to a `FirmwareOutdatedError`, or `None`.

    An `EpromOperationError` whose `error_code` is
    `MSG_ERR_UNKNOWN_CMD` means the attached firmware predates
    CMD_SDP_LOCK/CMD_SDP_UNLOCK and does not recognise this
    command at all. This exploits the one real asymmetry in the wire surface:
    an unknown COMMAND produces an error and is therefore
    detectable after the fact, whereas an unknown flag BIT produces silence.
    Keyed on the message **id**, never the message text.

    Returns a constructed (not raised) `FirmwareOutdatedError` when
    `exc.error_code == MSG_ERR_UNKNOWN_CMD`, and `None` for any other
    `error_code` (including `None`) -- returning rather than raising keeps
    the caller in control of exception chaining (`raise ... from exc`).
    """
    if exc.error_code != MSG_ERR_UNKNOWN_CMD:
        return None
    chip_upper = chip_name.upper()
    return FirmwareOutdatedError(
        f"{chip_upper}: attached firmware does not implement SDP "
        f"{mode} (unknown command) -- upgrade with "
        "'firestarter fw --install'."
    )


def map_unknown_cmd_to_outdated_for_operation(
    exc: EpromOperationError, operation_label: str, chip_name: str
) -> FirmwareOutdatedError | None:
    """Generalised sibling of `map_unknown_cmd_to_outdated`: same contract, but
    the message names whatever `operation_label`
    it is given instead of the hard-coded literal `"SDP"`.

    Added rather than folding into `map_unknown_cmd_to_outdated` itself,
    because that function's returned message is byte-identical-pinned at
    multiple call sites and this module's own extension discipline (C-4)
    is strictly additive -- no existing function's signature or wording
    changes here. `dev lock-status` is this sibling's first caller;
    it is not itself SDP, so wording the message around a caller-supplied
    label rather than a fixed protocol name is the honest generalisation.

    Same keying, same return-not-raise contract, same closing sentence as
    the sibling this generalises: keyed on `exc.error_code !=
    MSG_ERR_UNKNOWN_CMD` (the message **id**, never its text); returns a
    constructed (not raised) `FirmwareOutdatedError` when the firmware
    reported an unknown command, and `None` for any other `error_code`
    (including `None`) -- returning rather than raising keeps the caller
    in control of exception chaining (`raise ... from exc`).
    """
    if exc.error_code != MSG_ERR_UNKNOWN_CMD:
        return None
    chip_upper = chip_name.upper()
    return FirmwareOutdatedError(
        f"{chip_upper}: attached firmware does not implement "
        f"{operation_label} (unknown command) -- upgrade with "
        "'firestarter fw --install'."
    )
