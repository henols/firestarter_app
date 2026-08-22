"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Pytest unit tests for the UV bit-masking, slot and region arithmetic added to
`firestarter/chip_test.py` by quick task 260821-wna (D-A/D-B/D-D/D-E).

Pure, bench-free compute-layer tests: no chip access, no operator calls, no
hardware. One assertion family per test, each named for the property it
pins.
"""

import pytest

from firestarter.chip_test import (
    _FLASH4_BOOT_BLOCK_LENGTH,  # test-internal: mirrors eprom_operations
    _MAX_FULL_DEVICE_LENGTH,  # test-internal: the D-E sanity ceiling
    _PROTOCOL_FLASH4,  # test-internal: reused protocol id constant
    _UV_MIN_CLEARED_BITS,  # test-internal
    _UV_MIN_RETAINED_BITS,  # test-internal
    _UV_WRITE_REGION_LENGTH,  # test-internal: the UV slot width
    REGION_POLICY_FIXED,  # test-internal: coverage-tag dedup wiring
    WriteTarget,
    bits_cleared_by,
    bits_retained_by,
    full_device_region,
    generate_pattern,
    mask_write_pattern,
    uv_slot_starts,
)

# ---------------------------------------------------------------------------
# mask_write_pattern (D-A)
# ---------------------------------------------------------------------------


def test_mask_write_pattern_is_bitwise_and():
    assert mask_write_pattern(b"\xf0\x0f", b"\xff\xff") == b"\xf0\x0f"


def test_mask_write_pattern_is_commutative():
    a = b"\xa5\x3c"
    b = b"\x0f\xf0"
    assert mask_write_pattern(a, b) == mask_write_pattern(b, a)


def test_mask_write_pattern_saturated_current_yields_zero():
    assert mask_write_pattern(b"\x00" * 8, b"\xff" * 8) == b"\x00" * 8


def test_mask_write_pattern_unequal_lengths_raises():
    with pytest.raises(ValueError):
        mask_write_pattern(b"\x01\x02", b"\x01")


# ---------------------------------------------------------------------------
# bits_cleared_by / bits_retained_by (D-B)
# ---------------------------------------------------------------------------


def test_bits_cleared_by_counts_current_set_desired_clear():
    # current=0b1111_0000, desired=0b0000_1111 -- current's high nibble is
    # set and desired's high nibble is clear -> 4 bits cleared.
    assert bits_cleared_by(b"\xf0", b"\x0f") == 4


def test_bits_cleared_by_saturated_slot_returns_zero():
    current = b"\x00" * 32
    desired = generate_pattern(0, 32)
    assert bits_cleared_by(current, desired) == 0


def test_bits_cleared_by_unequal_lengths_raises():
    with pytest.raises(ValueError):
        bits_cleared_by(b"\x01\x02", b"\x01")


def test_bits_retained_by_counts_bits_set_in_both():
    assert bits_retained_by(b"\xff", b"\x0f") == 4


def test_bits_retained_by_equals_popcount_of_mask():
    current = b"\xa5\x3c\xff\x00"
    desired = generate_pattern(0x1000, 4)
    masked = mask_write_pattern(current, desired)
    expected = sum(bin(b).count("1") for b in masked)
    assert bits_retained_by(current, desired) == expected


def test_bits_retained_by_unequal_lengths_raises():
    with pytest.raises(ValueError):
        bits_retained_by(b"\x01\x02", b"\x01")


# ---------------------------------------------------------------------------
# uv_slot_starts (D-B)
# ---------------------------------------------------------------------------


def test_uv_slot_starts_top_down_ordering_and_count():
    starts = uv_slot_starts(65536, 256)
    assert len(starts) == 256
    assert starts[0] == 65280
    assert starts[-1] == 0


def test_uv_slot_starts_strictly_descending():
    starts = uv_slot_starts(65536, 256)
    assert all(a > b for a, b in zip(starts, starts[1:]))


def test_uv_slot_starts_empty_when_device_too_small():
    assert uv_slot_starts(128, 256) == []


def test_uv_slot_starts_empty_on_zero_or_negative_slot_length():
    assert uv_slot_starts(65536, 0) == []
    assert uv_slot_starts(65536, -1) == []


# ---------------------------------------------------------------------------
# full_device_region (D-D/D-E)
# ---------------------------------------------------------------------------


def test_full_device_region_non_flash4_whole_device():
    assert full_device_region(32768, protocol=0x07) == (0, 32768)


def test_full_device_region_flash4_carves_out_boot_blocks():
    region = full_device_region(524288, protocol=_PROTOCOL_FLASH4)
    assert region == (16384, 491520)


def test_full_device_region_flash4_whole_device_is_boot_block_refuses():
    result = full_device_region(32768, protocol=_PROTOCOL_FLASH4)
    assert isinstance(result, str)
    assert "boot block" in result.lower()


@pytest.mark.parametrize(
    "mem_size",
    [
        0,
        None,
        300,  # not a multiple of the slot width
        1 << 40,  # far above the sanity ceiling
    ],
)
def test_full_device_region_hostile_memory_size_refuses(mem_size):
    result = full_device_region(mem_size or 0, protocol=0x07)
    assert isinstance(result, str)


def test_full_device_region_sanity_ceiling_is_exclusive_bound():
    # Exactly at the ceiling, still honoured (a multiple of the slot width).
    at_ceiling = _MAX_FULL_DEVICE_LENGTH
    assert full_device_region(at_ceiling, protocol=0x07) == (0, at_ceiling)
    # One slot-width step above the ceiling refuses.
    over_ceiling = _MAX_FULL_DEVICE_LENGTH + _UV_WRITE_REGION_LENGTH
    result = full_device_region(over_ceiling, protocol=0x07)
    assert isinstance(result, str)


def test_full_device_region_flash4_boot_block_length_matches_mirror():
    assert _FLASH4_BOOT_BLOCK_LENGTH == 0x4000


# ---------------------------------------------------------------------------
# WriteTarget.__post_init__ -- the vacuous-pass guard (D-B)
# ---------------------------------------------------------------------------


def _valid_target(
    region=(0x1000, 32), masked=False, bits_cleared=1024, bits_retained=1024
):
    start, length = region
    pattern = generate_pattern(start, length)
    return WriteTarget(
        region=region,
        pattern=pattern,
        masked=masked,
        bits_cleared=bits_cleared,
        bits_retained=bits_retained,
        current_source="test",
    )


def test_write_target_valid_construction_succeeds():
    target = _valid_target()
    assert target.pattern == generate_pattern(0x1000, 32)


def test_write_target_region_policy_defaults_to_fixed():
    """Additive field (quick-devtest-coverage-dedup, follow-up to
    260821-wna): every direct `WriteTarget(...)` construction that predates
    `region_policy` -- like every other helper in this file -- keeps
    working unchanged, landing on the pre-existing engine-default policy."""
    target = _valid_target()
    assert target.region_policy == REGION_POLICY_FIXED


def test_write_target_refuses_pattern_length_mismatch():
    # Non-vacuity: a valid target constructs first, proving the refusal leg
    # below is not passing merely because the constructor rejects everything.
    _valid_target()
    with pytest.raises(ValueError):
        WriteTarget(
            region=(0, 32),
            pattern=generate_pattern(0, 16),
            masked=False,
            bits_cleared=0,
            bits_retained=0,
            current_source="test",
        )


def test_write_target_refuses_all_zero_pattern():
    _valid_target()
    with pytest.raises(ValueError):
        WriteTarget(
            region=(0, 16),
            pattern=b"\x00" * 16,
            masked=False,
            bits_cleared=0,
            bits_retained=0,
            current_source="test",
        )


def test_write_target_refuses_all_ff_pattern():
    _valid_target()
    with pytest.raises(ValueError):
        WriteTarget(
            region=(0, 16),
            pattern=b"\xff" * 16,
            masked=False,
            bits_cleared=0,
            bits_retained=0,
            current_source="test",
        )


def test_write_target_refuses_masked_target_below_cleared_floor():
    _valid_target()
    region = (0xFF00, 256)
    pattern = generate_pattern(*region)
    with pytest.raises(ValueError):
        WriteTarget(
            region=region,
            pattern=pattern,
            masked=True,
            bits_cleared=_UV_MIN_CLEARED_BITS - 1,
            bits_retained=1024,
            current_source="probe",
        )


def test_write_target_refuses_masked_target_below_retained_floor():
    _valid_target()
    region = (0xFF00, 256)
    pattern = generate_pattern(*region)
    with pytest.raises(ValueError):
        WriteTarget(
            region=region,
            pattern=pattern,
            masked=True,
            bits_cleared=1024,
            bits_retained=_UV_MIN_RETAINED_BITS - 1,
            current_source="probe",
        )


def test_write_target_unmasked_target_ignores_bit_thresholds():
    # An unmasked (non-UV or full-device-blank) target is never subject to
    # the bit-count floors -- masked=False short-circuits both checks.
    region = (0, 256)
    pattern = generate_pattern(*region)
    target = WriteTarget(
        region=region,
        pattern=pattern,
        masked=False,
        bits_cleared=0,
        bits_retained=0,
        current_source="blank-check",
    )
    assert target.masked is False
