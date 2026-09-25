"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Shield-revision gate for chips with VPP on chip pin 21.

Bus line 11 is where socket pin 21 lands on the 24-pin RURP wiring. It is the
VPP line of exactly two pin maps, DIP24_2716 and DIP24_2532. Only the
three-position JP4 of a Rev 2.2 or Rev 2.3 shield routes VPP there.

Polarity: fail closed. A false pass puts VPP on the wrong pin during a write or
an erase and can damage the chip. A false refusal only makes the operation
unavailable. Escapes: ``firestarter config --rev 2.2`` asserts the shield
revision (the firmware ADC cannot tell Rev 2.2 from Rev 2.0), and ``--force``
continues with a warning.

Unlike the pre-serial gates, this gate runs after the operation-setup ack,
because the revision comes from the firmware in that ack.
`EpromOperator._setup_operation` calls it on both the cold and the leased
connect, before it answers that ack. The firmware does not engage VPP until the
host sends its next ack, so a refusal here still leaves the rail down.
"""

from firestarter.codec import _REVISION_SILKSCREEN
from firestarter.constants import (
    COMMAND_ERASE,
    COMMAND_NAMES,
    COMMAND_WRITE,
    FLAG_FORCE,
    REVISION_2_2,
    REVISION_2_3,
)
from firestarter.exceptions import HardwareRevisionUnsupportedError

VPP_LINE_REQUIRING_REV_2_2 = 11

# ALLOWLIST, deliberately not a `>=` comparison. The REVISION_* bytes are not a
# version-ordered scale: REVISION_UNKNOWN is 0xFE, numerically ABOVE
# REVISION_2_2 (0x04), so `detected >= REVISION_2_2` would admit precisely the
# boards whose revision could not be determined. Membership fails closed for
# 0xFE, for the 0xFF override-absent sentinel, for the REVISION_2_0 broad
# bucket, and for None (firmware that sends no revision).
REVISIONS_WITH_3_POSITION_JP4 = (REVISION_2_2, REVISION_2_3)

# Only the operations that put VPP on the pin. read, blank and verify pass on
# every shield.
GATED_COMMANDS = frozenset({COMMAND_WRITE, COMMAND_ERASE})

_REFUSAL_FORMAT = (
    "{chip_name}: This chip needs VPP on chip pin 21. Only a Rev 2.2 or Rev 2.3 "
    "shield can supply VPP to that pin. The programmer reports {reported}. "
    "Refusing to {operation}. VPP on an incorrect pin can damage the chip.\n"
    "The firmware cannot detect a Rev 2.2 shield. If your shield is Rev 2.2, "
    "run 'firestarter config --rev 2.2' one time. Set JP4 as "
    "'firestarter info' shows. To {operation} on this shield at your own risk, "
    "use --force."
)

_FORCED_WARNING_FORMAT = (
    "WARNING: {chip_name}: The programmer reports {reported}, not Rev 2.2 or "
    "Rev 2.3. --force is set, so the {operation} continues. VPP on an incorrect "
    "pin can damage the chip."
)

_NO_REVISION_TEXT = "no revision (the firmware is too old)"


def _reported(detected: int | None) -> str:
    if detected is None:
        return _NO_REVISION_TEXT
    return _REVISION_SILKSCREEN.get(detected, f"revision byte 0x{detected:02X}")


def _format(
    fmt: str, chip_name: str, command_to_send: dict, detected: int | None
) -> str:
    return fmt.format(
        chip_name=chip_name.upper(),
        reported=_reported(detected),
        operation=COMMAND_NAMES.get(command_to_send.get("cmd"), "program").lower(),
    )


def is_refused(command_to_send: dict, detected: int | None) -> bool:
    """True when a write or erase routes VPP to bus line 11 on a shield that
    does not report Rev 2.2 or Rev 2.3."""
    if command_to_send.get("cmd") not in GATED_COMMANDS:
        return False
    bus_config = command_to_send.get("bus-config") or {}
    if bus_config.get("vpp-pin") != VPP_LINE_REQUIRING_REV_2_2:
        return False
    return detected not in REVISIONS_WITH_3_POSITION_JP4


def is_forced(command_to_send: dict) -> bool:
    return bool(command_to_send.get("flags", 0) & FLAG_FORCE)


def require_supported_revision(
    chip_name: str, command_to_send: dict, detected: int | None
) -> None:
    """Raise HardwareRevisionUnsupportedError when refused and not forced."""
    if is_refused(command_to_send, detected) and not is_forced(command_to_send):
        raise HardwareRevisionUnsupportedError(
            _format(_REFUSAL_FORMAT, chip_name, command_to_send, detected),
            detected=detected,
        )


def forced_warning(
    chip_name: str, command_to_send: dict, detected: int | None
) -> str | None:
    """The warning text when the check is refused but --force is set, else None."""
    if is_refused(command_to_send, detected) and is_forced(command_to_send):
        return _format(_FORCED_WARNING_FORMAT, chip_name, command_to_send, detected)
    return None
