"""
Tests for tools/build_db.py::resolve_pinout_key's proto_id=0x08 size fork
(Phase 197 Plan 01 -- D-09).

`_PGM_ON_PIN31_MAX_SIZE` was deleted and replaced with the derived boundary
`(mem_size - 1).bit_length() <= 18`. This module pins that boundary against
every real `electrical.size_bytes` value shipped in `chip_database.json`
(eleven distinct values) plus the 262144/262145 boundary pair, so a future
edit to the pin-31 fork reddens immediately instead of silently drifting.
"""

import pytest

from tools import build_db


class TestDerivedSizeBoundaryFork:
    """Covers the `proto_id == 0x08` arm of the 32-pin pinout fork when
    neither sibling `variant_lo` branch applies."""

    _PIN_COUNT = 32
    _VARIANT_NEITHER_SIBLING_FORK = 0x00
    _FLAGS_UNUSED = 0
    _PM_IDX_MIXED_FLASH_EPROM_FAMILY = 5
    _PROTO_ID = 0x08

    _REAL_SIZE_BYTES_AT_OR_UNDER_BOUNDARY = (
        512,
        2048,
        4096,
        8192,
        16384,
        32768,
        65536,
        131072,
        262144,
    )
    _REAL_SIZE_BYTES_OVER_BOUNDARY = (524288, 1048576)

    def _resolve(self, mem_size):
        return build_db.resolve_pinout_key(
            pin_count=self._PIN_COUNT,
            variant=self._VARIANT_NEITHER_SIBLING_FORK,
            flags_int=self._FLAGS_UNUSED,
            pm_idx=self._PM_IDX_MIXED_FLASH_EPROM_FAMILY,
            proto_id=self._PROTO_ID,
            mem_size=mem_size,
        )

    @pytest.mark.parametrize("mem_size", _REAL_SIZE_BYTES_AT_OR_UNDER_BOUNDARY)
    def test_real_size_bytes_at_or_under_boundary_selects_pgm_pin31(self, mem_size):
        assert self._resolve(mem_size) == "DIP32_27C020"

    @pytest.mark.parametrize("mem_size", _REAL_SIZE_BYTES_OVER_BOUNDARY)
    def test_real_size_bytes_over_boundary_selects_address_pin31(self, mem_size):
        assert self._resolve(mem_size) == "DIP32_STD"

    def test_boundary_value_262144_is_inside_the_fork(self):
        assert self._resolve(262144) == "DIP32_27C020"

    def test_boundary_value_262145_is_one_step_past_the_fork(self):
        assert self._resolve(262145) == "DIP32_STD"


class TestDip28_2764FamilyPinMapComesFromInfoicPinMap:
    """infoic.xml <maps> index 21 leaves pin 26 unbonded (2764/27C64) and
    index 22 bonds it as A13 (27128). Within pm_idx 22 only the 27011 rows
    carry pin_map 0x90000c16. The infoic pin_map alone selects the pin map;
    chip size does not."""

    _SIZES = (8192, 16384, 131072)

    def _resolve(self, pin_map_raw, mem_size):
        return build_db.resolve_pinout_key(
            pin_count=28,
            variant=0x13,
            flags_int=0,
            pm_idx=pin_map_raw & 0xFF,
            proto_id=0x07,
            mem_size=mem_size,
            pin_map_raw=pin_map_raw,
        )

    @pytest.mark.parametrize("mem_size", _SIZES)
    @pytest.mark.parametrize(
        ("pin_map_raw", "key"),
        [
            (0x00006F15, "DIP28_2764"),
            (0x00000C16, "DIP28_27128"),
            (0x90000C16, "DIP28_27011"),
        ],
    )
    def test_pin_map_selects_the_key_at_every_size(self, pin_map_raw, key, mem_size):
        assert self._resolve(pin_map_raw, mem_size) == key

    @pytest.mark.parametrize("key", sorted(build_db._2764_FAMILY))
    def test_erasable_2764_family_row_is_still_promoted_to_0x0d(self, key):
        assert build_db.classify(1, 0x07, 21, 0x10, key, 8192) == (
            "EEPROM",
            0x0D,
            key,
        )
