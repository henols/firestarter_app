"""
Project Name: Firestarter
Copyright (c) 2025 Henrik Olsson

Permission is hereby granted under MIT license.

Which chips `firestarter erase` can erase.

This is the one definition. `database.convert_to_programmer` uses it to set `FLAG_CAN_ERASE`, and
the firmware refuses an erase when that flag is clear. `info` uses it for its "Can be erased" line.
So the `info` line and the `erase` result cannot disagree.

Algorithm 5 (flash4) is excluded for hardware safety. The flash4 page write erases each page
internally, and the flag would send the firmware into an erase that turns on the VPP regulator on a
5 V-only chip.
"""

from __future__ import annotations

from typing import Any

from firestarter.flash4_erase_gate import FLASH4_PROTOCOL_ID

ELECTRICALLY_ERASABLE_TYPES = frozenset({"EEPROM", "Flash/EEPROM"})


def is_electrically_erasable(electrical_type: Any) -> bool:
    """True when the chip type can be erased electrically. This is a chip property only."""
    return electrical_type in ELECTRICALLY_ERASABLE_TYPES


def erase_accepted(electrical_type: Any, algorithm: Any) -> bool:
    """True when the host sets `FLAG_CAN_ERASE`, so that the `erase` command can erase the chip."""
    return is_electrically_erasable(electrical_type) and algorithm != FLASH4_PROTOCOL_ID
