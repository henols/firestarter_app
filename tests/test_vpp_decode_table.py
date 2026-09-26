"""
Tests for tools/build_db.py::VPP_MV and its exact-match low-byte set
(Phase 198 Plan 01 -- VOLT-01/VOLT-02, D-02).

Defect class this closes: `VPP_MV.get(voltages & 0xF0, 0)` collapses the two
NMOS-derived indices `0xF1` (25000 mV) and `0xF2` (21000 mV) onto `0xF0`
(18000 mV), silently under-reporting the rail by 7000 mV or 3000 mV for any
future row that carries either index. D-02 completes the table from
upstream's `xg_vpp_voltages[]` and fixes the decode to exact-match those two
indices before falling back to the mask. No filtered row carries either
index today (RESEARCH F-1/Pitfall 1), so this module is presently this
decode branch's only coverage.

Coverage:
  1. `test_vpp_mv_carries_the_two_new_indices` -- `VPP_MV[0xF1] == 25000` and
     `VPP_MV[0xF2] == 21000`, alongside the sixteen pre-existing entries.
  2. `test_exact_match_set_is_derived_from_the_table` -- the module-level
     exact-match constant equals the set of `VPP_MV` keys whose low nibble
     is non-zero, computed independently here rather than asserted against
     a literal pair, so the derivation itself is pinned, not merely its
     current output.
  3. `test_exact_match_set_and_masked_domain_do_not_overlap` -- no low byte
     is both in the exact-match set and reachable through the `& 0xF0` mask
     onto a real key, which is the property that makes exact-match-before-
     mask well-defined rather than ambiguous.
"""

import importlib.util
from pathlib import Path

_BUILD_DB_PATH = Path(__file__).resolve().parent.parent / "tools" / "build_db.py"
_spec = importlib.util.spec_from_file_location("build_db_module", _BUILD_DB_PATH)
build_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_db)


def test_vpp_mv_carries_the_two_new_indices() -> None:
    assert build_db.VPP_MV[0xF1] == 25000, build_db.VPP_MV.get(0xF1)
    assert build_db.VPP_MV[0xF2] == 21000, build_db.VPP_MV.get(0xF2)
    assert len(build_db.VPP_MV) == 18, len(build_db.VPP_MV)


def test_exact_match_set_is_derived_from_the_table() -> None:
    derived = {k for k in build_db.VPP_MV if k & 0x0F}
    assert build_db._VPP_EXACT_LOW_BYTES == derived, (
        "the exact-match low-byte set does not equal the VPP_MV keys with a "
        f"non-zero low nibble: constant={sorted(build_db._VPP_EXACT_LOW_BYTES)}, "
        f"derived={sorted(derived)}"
    )
    assert derived == {0xF1, 0xF2}, sorted(hex(x) for x in derived)


def test_exact_match_set_and_masked_domain_do_not_overlap() -> None:
    masked_domain = {k & 0xF0 for k in build_db.VPP_MV if not (k & 0x0F)}
    overlap = build_db._VPP_EXACT_LOW_BYTES & masked_domain
    assert not overlap, (
        "the exact-match set and the masked-lookup domain overlap on "
        f"{sorted(overlap)} -- exact-match-before-mask is ambiguous there"
    )
