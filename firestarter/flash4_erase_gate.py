"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Flash4 (protocol 0x05) erase policy.

Firmware 3.2.2 added a software chip erase for protocol 0x05 (the six-cycle
AA/55/80/AA/55/10 sequence, 5 V only). Older firmware is not safe to send
that erase to:

- 3.0.0b8 to 3.0.0b26 erase a 0x05 part with the VPP regulator and VPE
  switched on: 12 V on a 5 V part.
- 3.0.0b27 to 3.2.1 answer "not supported".

So `FLAG_CAN_ERASE` stays clear for algorithm 5 in the database
(`erase_support.erase_accepted`), and `write` never carries it. Only the
`erase` command sets it, for that one command
(`with_erase_flag`), and `SerialCommunicator.setup_command` refuses the
command before the operation starts when the firmware is older than
`ERASE_MIN_FIRMWARE` (`needs_newer_firmware`).

Like `jp5_gate.py` and `sdp_capability.py`, this is a pure predicate module:
no I/O, no environment reads, no serial access.

`is_flash4` FAILS OPEN on absent evidence (a wire dict with no `algorithm`
key is "not flash4"), unlike `jp5_gate` and `sdp_capability`. Refusing every
chip this predicate cannot classify would break `erase` for chips that are
not flash4 at all.
"""

from __future__ import annotations

import re
from typing import Any, Mapping  # noqa: UP035

from firestarter.constants import COMMAND_ERASE, FLAG_CAN_ERASE

FLASH4_PROTOCOL_ID = 5

# The first firmware with the software chip erase for protocol 0x05.
ERASE_MIN_FIRMWARE = (3, 2, 2)

_FIRMWARE_REFUSAL_FORMAT = (
    "Erasing this chip needs firmware {minimum} or later; this board runs "
    "{version}. Update it with 'firestarter fw --install'."
)


def is_flash4(programmer_data: Mapping[str, Any] | None) -> bool:
    """True when the wire dict's `algorithm` value is the flash4 protocol id.

    The comparison is against `algorithm`, not `flags & FLAG_CAN_ERASE`: the
    flag is also clear for UV-EPROM and SRAM parts, so a flag-based
    predicate would widen this past its flash4-only scope.

    Returns False for a missing, `None`, or empty `programmer_data`, and for
    a dict with no `algorithm` key.
    """
    if not programmer_data:
        return False
    return programmer_data.get("algorithm") == FLASH4_PROTOCOL_ID


def with_erase_flag(programmer_data: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of `programmer_data` with `FLAG_CAN_ERASE` set, for `erase` only."""
    data = dict(programmer_data)
    data["flags"] = int(data.get("flags") or 0) | FLAG_CAN_ERASE
    return data


def _version_tuple(version: str | None) -> tuple[int, int, int] | None:
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def needs_newer_firmware(
    command: Mapping[str, Any], firmware_version: str | None
) -> bool:
    """True when `command` is a flash4 erase that `firmware_version` cannot run safely.

    Only an erase command that carries `FLAG_CAN_ERASE` for a flash4 part is
    affected; every other command returns False. A version that cannot be
    parsed counts as too old, because the 3.0.0 pre-releases with the 12 V
    erase are older than every version this check accepts. A pre-release
    or development build of 3.2.2 (`3.2.2rc1`, `3.2.2.dev5`) counts as 3.2.2.
    """
    if command.get("cmd") != COMMAND_ERASE or not is_flash4(command):
        return False
    if not int(command.get("flags") or 0) & FLAG_CAN_ERASE:
        return False
    parsed = _version_tuple(firmware_version)
    return parsed is None or parsed < ERASE_MIN_FIRMWARE


def firmware_refusal_text(firmware_version: str | None) -> str:
    """The one-line refusal for a flash4 erase on firmware that is too old."""
    return _FIRMWARE_REFUSAL_FORMAT.format(
        minimum=".".join(str(n) for n in ERASE_MIN_FIRMWARE),
        version=firmware_version or "an unknown version",
    )
