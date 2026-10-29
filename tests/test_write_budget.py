"""The host's per-block write-time budget (`firestarter/write_budget.py`).

The oracle figures come from the firmware's own budget, before it moved to
the host: 106 s for 0x0B at its modal 500 us pulse on a 1024-byte block, and
3358 s for 0x07 at the widest pulse (65535 us) on a 1024-byte block.
"""

from __future__ import annotations

import pytest

from firestarter import write_budget


@pytest.mark.parametrize(
    ("protocol", "pulse_us", "block_bytes", "expected"),
    [
        (0x0B, 500, 1024, 106),
        (0x07, 65535, 1024, 3358),
        (0x07, 1000, 1024, 54),
        (0x07, 1000, 512, 28),
        (0x08, 100, 512, 6),
    ],
)
def test_block_budget_matches_the_firmware_figures(
    protocol: int, pulse_us: int, block_bytes: int, expected: int
) -> None:
    assert write_budget.block_budget_s(protocol, pulse_us, block_bytes) == expected


@pytest.mark.parametrize(
    ("protocol", "fallback_us"), [(0x07, 1000), (0x08, 100), (0x0B, 500)]
)
def test_a_zero_pulse_uses_the_configure_eprom_fallback(
    protocol: int, fallback_us: int
) -> None:
    assert write_budget.block_budget_s(
        protocol, 0, 1024
    ) == write_budget.block_budget_s(protocol, fallback_us, 1024)


def test_the_energy_cap_count_rounds_up() -> None:
    """The firmware adds to its accumulator before it tests it, so a pulse
    one us under the cap still takes two pulses."""
    assert write_budget.worst_pulses(255, 49999, 50000) == 2
    assert write_budget.worst_pulses(255, 50000, 50000) == 1
    assert write_budget.worst_pulses(25, 1000, 0) == 25


@pytest.mark.parametrize("protocol", [0x05, 0x0D, 0x0E, 0x28, 0])
def test_a_protocol_without_an_eprom_row_has_no_budget(protocol: int) -> None:
    assert write_budget.block_budget_s(protocol, 1000, 1024) is None
