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
