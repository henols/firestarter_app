"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Per-block worst-case write time for the three 27C EPROM protocols.

The host sets its MAIN-phase response timeout for a write from this value.
The firmware used to calculate it and send it in the `MSG_OK_READY` ack. The
host already knows every input, so the calculation moved here and the field
left the ack.

Firmware sync: `firestarter_fw/src/proms/eprom_params.cpp` (`max_pulses`,
`energy_cap_us`) and the pulse-width fallbacks in `configure_eprom()`
(`src/proms/eprom.cpp`). Change both sides together.

Like `ram_test.py`, this is a pure module: no I/O, no serial access.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EpromRow:
    """The columns of one EPROM parameter row that bound the write time."""

    max_pulses: int
    # Accumulated per-byte program time in us. 0 means uncapped.
    energy_cap_us: int
    # The width configure_eprom() uses when the command's pulse-delay is 0.
    fallback_pulse_us: int


EPROM_ROWS: dict[int, EpromRow] = {
    0x07: EpromRow(max_pulses=25, energy_cap_us=0, fallback_pulse_us=1000),
    0x08: EpromRow(max_pulses=25, energy_cap_us=0, fallback_pulse_us=100),
    0x0B: EpromRow(max_pulses=255, energy_cap_us=50000, fallback_pulse_us=500),
}


def worst_pulses(max_pulses: int, pulse_us: int, energy_cap_us: int) -> int:
    """Worst-case pulse count for one byte.

    min(max_pulses, ceil(energy_cap_us / pulse_us)). The ceil matters: the
    firmware adds to its accumulator before it tests it, so at
    pulse_us=49999 against a 50000 us cap a byte takes two pulses.
    """
    if energy_cap_us == 0 or pulse_us == 0:
        return max_pulses
    return min(max_pulses, -(-energy_cap_us // pulse_us))


def block_budget_s(protocol: int, pulse_us: int, block_bytes: int) -> int | None:
    """Worst-case time for one write block, in whole seconds, padded.

    padded = ceil(block_bytes x per-byte pulse time / 1e6) x 2 + 2

    The doubling covers the overhead that scales with the pulse count: the
    settle and verify strobe per pulse, the address latches, the final
    full-block verify on 0x07/0x08 and the serial transfer. The + 2 is a
    floor. Returns None for a protocol that is not a 27C EPROM row, so the
    caller uses its fallback.
    """
    row = EPROM_ROWS.get(protocol)
    if row is None:
        return None
    pulse = pulse_us or row.fallback_pulse_us
    per_byte_us = worst_pulses(row.max_pulses, pulse, row.energy_cap_us) * pulse
    raw_s = -(-(per_byte_us * block_bytes) // 1_000_000)
    return raw_s * 2 + 2
