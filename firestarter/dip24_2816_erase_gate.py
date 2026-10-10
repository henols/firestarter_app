"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Erase-refusal policy for the 24-pin 5 V EEPROMs on the DIP24_2816 pin map.

These parts (AT28C04/16, X2804A/X2816A, CAT28C16A, 28C04A/16A, UPD28C04 and
their siblings) have no command decoder. The 0x0D firmware erase is a software
sequence: an SDP-disable prefix, then six command bytes at 0x5555/0x2AAA. On a
part that does not decode commands, each of those writes stores a byte as data
at a truncated address, and the firmware then reports a successful erase. The
only whole-chip clear these parts have ("Chip Clear" in the AT28C16 datasheet)
needs 12 V on OE, which the firmware never applies.

So an erase on these parts corrupts data and reports success. This module is
the policy that refuses it. `erase_support.erase_accepted` uses it, so the
`FLAG_CAN_ERASE` bit, the `info` "Can be erased" line, `dev test`'s plan and the
`erase` command agree.

The input is the pin-map key of the database row, not a part name. Every row on
this pin map has the same missing command decoder.

Polarity: fails OPEN. An absent pin-map key means "not shown to be DIP24_2816".
Every shipped row carries a pin-map key, so only a hand-written override row can
be absent, and such a row cannot build a bus config either.
"""

from __future__ import annotations

from typing import Any

DIP24_2816_PINOUT = "DIP24_2816"

_REFUSAL_FORMAT = (
    "Erase not supported for {chip_name}. This chip has no erase command: an "
    "erase would store bytes in it as data. To clear it, write a file of 0xFF "
    "bytes to it."
)


def is_affected(pinout_key: Any) -> bool:
    """True when the row's pin-map key is DIP24_2816."""
    return pinout_key == DIP24_2816_PINOUT


def refusal_text(chip_name: str) -> str:
    """The refusal line that `firestarter erase` prints for these parts."""
    return _REFUSAL_FORMAT.format(chip_name=chip_name.upper())
