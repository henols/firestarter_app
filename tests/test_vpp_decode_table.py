"""
Tests for tools/build_db.py::decode_vpp_mv and its VPP_MV table
(Phase 198 Plan 01 -- VOLT-01/VOLT-02, D-02).

Defect class this closes: `VPP_MV.get(voltages & 0xF0, 0)` collapses the two
NMOS-derived indices `0xF1` (25000 mV) and `0xF2` (21000 mV) onto `0xF0`
(18000 mV), silently under-reporting the rail by 7000 mV or 3000 mV for any
future row that carries either index. D-02 completes the table from
upstream's `xg_vpp_voltages[]` and fixes the decode to exact-match those two
indices before falling back to the mask. No filtered row carries either
index today (RESEARCH F-1/Pitfall 1), so this module is presently this
decode branch's only coverage.
"""

import importlib.util
from pathlib import Path

import pytest

_BUILD_DB_PATH = Path(__file__).resolve().parent.parent / "tools" / "build_db.py"
_spec = importlib.util.spec_from_file_location("build_db_module", _BUILD_DB_PATH)
build_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_db)


def test_vpp_mv_carries_the_two_new_indices() -> None:
    assert build_db.VPP_MV[0xF1] == 25000, build_db.VPP_MV.get(0xF1)
    assert build_db.VPP_MV[0xF2] == 21000, build_db.VPP_MV.get(0xF2)
    assert len(build_db.VPP_MV) == 18, len(build_db.VPP_MV)


def test_only_0xf1_and_0xf2_are_whole_byte_indices() -> None:
    assert build_db._VPP_EXACT_LOW_BYTES == {0xF1, 0xF2}, sorted(
        hex(x) for x in build_db._VPP_EXACT_LOW_BYTES
    )


@pytest.mark.parametrize(
    ("voltages", "vpp_mv"),
    [
        (0x00F1, 25000),
        (0x00F2, 21000),
        (0x00F0, 18000),
        (0x00F3, 18000),
        (0x0001, 12000),  # low nibble is a VCC index, not part of the VPP
        (0x12F1, 25000),  # the VCC/VDD nibbles do not reach the VPP decode
    ],
)
def test_decode_vpp_mv(voltages: int, vpp_mv: int) -> None:
    assert build_db.decode_vpp_mv(voltages) == vpp_mv
