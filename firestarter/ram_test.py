"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

RAM test (March C-) policy and result decoding.

The firmware runs the test (`CMD_RAM_TEST`, march.cpp) on the RAM-test
protocols 0x27, 0x28 and 0x29 and stops at the first cell that does not hold
the value written to it. It reports that cell in `MSG_ERR_RAM_TEST`.

The test overwrites every cell, many times. That is harmless on a volatile
SRAM, which loses its contents at power-off anyway. It destroys the data on a
non-volatile part (battery-backed NVRAM, timekeeper RAM or FRAM), so the host
refuses every `non-volatile` row before it opens the port.

Like `flash4_erase_gate.py`, this is a pure module: no I/O, no environment
reads, no serial access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping  # noqa: UP035

RAM_TEST_PROTOCOLS = frozenset({0x27, 0x28, 0x29})

# March C-, one entry per element, as the firmware runs it (march.cpp).
MARCH_C_MINUS_ELEMENTS = (
    "up (w0)",
    "up (r0, w1)",
    "up (r1, w0)",
    "down (r0, w1)",
    "down (r1, w0)",
    "up (r0)",
)

# The rendered MSG_ERR_RAM_TEST text. The frame payload does not reach the
# operator (`Response.payload` is filled only for MSG_DATA_CHUNK), so the
# fields come back from the text, as `_LOCK_STATUS_RE` does.
_FAILURE_RE = re.compile(
    r"March element (\d+) at 0x([0-9a-fA-F]+): "
    r"expected 0x([0-9a-fA-F]{2}), read 0x([0-9a-fA-F]{2})"
)


@dataclass(frozen=True)
class RamTestFailure:
    """The first cell that failed: where, and what it read."""

    element: int
    address: int
    expected: int
    actual: int

    @property
    def bad_bits(self) -> int:
        return self.expected ^ self.actual


def refusal_reason(entry: Mapping[str, Any], chip_name: str) -> str | None:
    """Why `ram-test` must not run on this database record, or None.

    `entry` is the full record from `EpromDatabase.get_eprom` (it carries
    `protocol-id` and `non-volatile`; the wire dict does not).
    """
    name = chip_name.upper()
    if entry.get("protocol-id") not in RAM_TEST_PROTOCOLS:
        return f"{name} is not a RAM. The RAM test runs only on SRAM parts."
    if entry.get("non-volatile"):
        return (
            f"{name} keeps its data without power (NVRAM, timekeeper RAM or "
            "FRAM). The RAM test overwrites every cell, so it does not run "
            "on a non-volatile part."
        )
    return None


def parse_failure(message: str | None) -> RamTestFailure | None:
    """Decode the rendered MSG_ERR_RAM_TEST text, or None if it is not one."""
    match = _FAILURE_RE.search(message or "")
    if not match:
        return None
    return RamTestFailure(
        element=int(match.group(1)),
        address=int(match.group(2), 16),
        expected=int(match.group(3), 16),
        actual=int(match.group(4), 16),
    )


def element_text(element: int) -> str:
    """`'4 of 6, down (r0, w1)'` for a 0-based element index."""
    total = len(MARCH_C_MINUS_ELEMENTS)
    if 0 <= element < total:
        return f"{element + 1} of {total}, {MARCH_C_MINUS_ELEMENTS[element]}"
    return f"{element + 1} of {total}"


def render_failure(chip_name: str, failure: RamTestFailure) -> str:
    """The FAIL report: which element, which address, which bits."""
    return "\n".join(
        [
            f"RAM test FAILED: {chip_name.upper()} (March C-)",
            f"  Element:  {element_text(failure.element)}",
            f"  Address:  0x{failure.address:06X}",
            f"  Expected: 0x{failure.expected:02X}",
            f"  Read:     0x{failure.actual:02X}",
            f"  Bad bits: 0x{failure.bad_bits:02X}",
        ]
    )
