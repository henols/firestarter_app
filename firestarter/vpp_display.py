"""
Project Name: Firestarter
Copyright (c) 2025 Henrik Olsson

Permission is hereby granted under MIT license.

Which database rows show `electrical.vpp_mv` as a programming VPP.

Every row in `chip_database.json` has a non-zero `vpp_mv`. On the 5 V-only protocols the value is
the voltage of the chip's write-protect (WP) pin from the datasheet. It is not a programming VPP,
and the firmware never turns on the VPP regulator for these protocols. The retired
`tools/check_dispatch.py` had the same carve-out (`git show 7ebdef8^:tools/check_dispatch.py`).

Polarity: fail closed. Only the protocols in `PROGRAMMING_VPP_PROTOCOL_IDS` show a VPP. An unknown
protocol shows no VPP. The worst case of that choice is a missing line in `info`. The worst case of
the other choice is a wrong voltage that the operator can set on the potentiometer.
"""

from __future__ import annotations

from typing import Any

EPROM_28PIN_PROTOCOL_ID = 0x07
EPROM_32PIN_PROTOCOL_ID = 0x08
EPROM_24PIN_PROTOCOL_ID = 0x0B
FLASH_INTEL_PROTOCOL_ID = 0x10

PROGRAMMING_VPP_PROTOCOL_IDS = frozenset(
    {
        EPROM_28PIN_PROTOCOL_ID,
        EPROM_32PIN_PROTOCOL_ID,
        EPROM_24PIN_PROTOCOL_ID,
        FLASH_INTEL_PROTOCOL_ID,
    }
)


def shows_programming_vpp(algorithm: Any, vpp_mv: Any) -> bool:
    """True when the row's `vpp_mv` is a programming VPP that `info` and `list` must show.

    `vpp_mv` can be a string in a user override entry, so the value is converted here.
    """
    if algorithm not in PROGRAMMING_VPP_PROTOCOL_IDS:
        return False
    try:
        return int(vpp_mv or 0) > 0
    except (TypeError, ValueError):
        return False
