"""Shared honesty carrier for SDP (Software Data Protection) wording.

Phase 132 (RETIRE-03), D-01/D-02: `firestarter dev sdp` -- the only production
carrier of the honesty caveat (D-10) and the D-14 unknown-command-to-outdated-
firmware mapping -- is being retired. Neither piece of wording has anywhere
else to live: `eprom_operations.py`'s `sdp_lock`/`sdp_unlock` cannot carry the
caveat because `serial_comm.py`'s `get_response()` filters the entire INFO
band at `:424`, so the operation layer never sees the firmware's `0x5F`/`0x61`
duration frame the caveat exists to disclaim. This module relocates both
pieces of wording into a shared, standalone production helper authored in
this phase, so the four honesty tests retarget onto a real SUT instead of a
scanning gate.

Forward contract (D-02, D-01): Phase 134's leg-report rows and Phase 135's
`write --sdp-relock` path are the intended future callers of this module.
Its API is named for what it carries -- the honesty wording -- not for the
`dev sdp` subcommand being retired.
"""

from __future__ import annotations

# Import-set invariant (checked by an AST-based test added in plan 132-03):
# this module's top-level import set is a subset of
# {"__future__", "firestarter.exceptions", "firestarter.messages"}, both leaf
# modules (`exceptions.py` has zero top-level imports; `messages.py` imports
# only `dataclasses`). In particular, no `click` -- the caller performs the
# echo, so a `click` dependency here would make this module unusable from
# Phase 134's report layer, which has no CLI context of its own.
from firestarter.exceptions import EpromOperationError, FirmwareOutdatedError
from firestarter.messages import MSG_ERR_UNKNOWN_CMD


def unreadable_state_caveat() -> str:
    """Return the caveat clause alone, byte-identical to the wording
    formerly composed inline in `cli_handlers.py`'s retired `dev sdp`
    subcommand (D-10). Exposed separately from `emission_summary` because
    Phase 134's report rows need the clause without the emission preamble.
    """
    return (
        "The resulting protection state cannot be read back on this chip "
        "family, so this is not a claim about the chip's actual state."
    )


def emission_summary(mode: str, chip_name: str) -> str:
    """Return the full single-line summary for an emitted SDP sequence.

    Honest and symmetric on both directions (D-10): the claim is that the
    sequence was EMITTED, never that the resulting state was verified. No
    duration figure appears here -- this is mechanically enforced, not
    merely a discipline: `get_response()` filters the entire INFO band out
    at `serial_comm.py:424`, so the operation layer literally cannot see the
    firmware's `0x5F`/`0x61` duration frame to plumb one through. No
    lock/unlock state boolean appears either -- HOST-05's honesty floor.

    Composed by calling `unreadable_state_caveat()` rather than duplicating
    its wording, so the two can never drift. `chip_name` is uppercased here
    so a Phase 134/135 caller cannot forget to. Never appends a newline --
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

    D-14: an `EpromOperationError` whose `error_code` is
    `MSG_ERR_UNKNOWN_CMD` means the attached firmware predates
    CMD_SDP_LOCK/CMD_SDP_UNLOCK (Phase 119) and does not recognise this
    command at all. This exploits the one real asymmetry in the wire surface
    (HOST-06): an unknown COMMAND produces an error and is therefore
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
