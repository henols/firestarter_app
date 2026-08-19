"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 148 Plan 06 -- DATA-01, DATA-04 (D-01/D-02/D-03).

Defect class this closes: `infoic.xml`'s VCC nibble `2` decodes FAITHFULLY to
4000 mV (`VCC_VOLTAGES[0x02]`, [VERIFIED: minipro database.c#L130-L135 @
a8efaedc -- tl866ii_vcc_voltages[]]) -- that decode is not the defect. The
defect is semantic: 4000 mV is the TL866's low-margin VCC *verify* rail, not
any part's operating supply, and firestarter surfaced it as though it were.
`build_db.py`'s post-construction margin-rail substitution
(`_VCC_MARGIN_RAIL_MV`) corrects this by substituting the chip's own
already-decoded `vdd_mv` wherever `vcc_mv` lands on the rail -- keyed on the
DECODED VALUE alone, never a part number, type, or algorithm.

Coverage:
  1. Zero-at-rail: no chip in the regenerated database has
     `electrical.vcc_mv == 4000`.
  2. Exactly 56 movers: exactly 56 chips moved `4000 -> 5000` relative to the
     (un-re-pinned, D-11) baseline, and every one of them landed on its own
     `vdd_mv`.
  3. No-decrease guard: no chip's `vcc_mv` is ever lower than its own baseline
     value -- the property the rejected type-keyed and algorithm-keyed rules
     (measured at 85 / 84 movers, each setting 16 genuinely-5V EEPROMs to
     3.3V) would have violated.
  4. DATA-04: `VCC_VOLTAGES[0x02]` still decodes to 4000 -- the decode table
     itself is never edited, only the value read from it afterward; and
     `_PAGE_SIZE_BY_PART` still has exactly 2 entries (no new
     part-number-keyed dict was introduced as a sibling).
  5. Non-vacuity (S-5): a synthetic in-memory chip carrying `vcc_mv == 4000`
     makes the SAME offender-collecting helper Test 1 calls raise -- proves
     Test 1 is capable of failing, not a vacuous always-pass check.

Criterion 1 (`firestarter info AT28C256` renders `VCC: 5.0v`) has its only
test in `tests/test_characterization.py::test_info_at28c256` -- a new pinned
snapshot, not duplicated here.
"""

import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Path seams (S-2): self-contained, not conftest.py-dependent.
# ---------------------------------------------------------------------------
_FA_DIR = Path(__file__).parent.parent
_DB_FILE = _FA_DIR / "firestarter" / "data" / "chip_database.json"

_BASELINE_FILE = Path(
    os.environ.get(
        "FIRESTARTER_BASELINE_FILE",
        str(_FA_DIR / "tools" / "baseline" / "chip_database.baseline.json"),
    )
)

# The margin rail value itself, mirrored here (not imported) because the
# comparison helper below needs to canonicalize the OLD-schema baseline's
# string voltages -- duplicating three lines of parsing is preferable to
# importing a gate tool (diff_db.py) into a test.
_VCC_MARGIN_RAIL_MV = 4000


# ---------------------------------------------------------------------------
# Shared helpers -- both the real-DB tests and the non-vacuity test call
# these, so the non-vacuity leg exercises the same code the real tests do.
# ---------------------------------------------------------------------------


def _mv(value) -> int:
    """Canonicalize a voltage value to integer millivolts.

    The current (post-Phase-148) chip_database.json already carries int
    millivolts. The baseline (tools/baseline/chip_database.baseline.json) is
    on the OLD string schema (deliberately never re-pinned, D-11) and carries
    strings like "4V" / "5.5V". This local `_mv` handles both shapes rather
    than importing diff_db.py's canonicalizer into a test.
    """
    if isinstance(value, (int, float)):
        return int(value)
    return int(round(float(str(value).rstrip("Vv")) * 1000))


def _load_db(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _iter_chips_with_keys(db: dict):
    """Yield (manufacturer, part_number, index, chip) for every chip.

    part_number is NOT unique within a manufacturer (65-69 records in this
    database share one), so `index` (the occurrence count of this exact
    (manufacturer, part_number) pair seen so far, in DB order) is part of the
    join key -- a part_number-only join would silently shadow ~9% of the
    database, merging distinct records into one.
    """
    seen_counts: dict[tuple[str, str], int] = {}
    for mfr, chips in db.items():
        for chip in chips:
            part = chip.get("part_number", "?")
            key = (mfr, part)
            idx = seen_counts.get(key, 0)
            seen_counts[key] = idx + 1
            yield mfr, part, idx, chip


def _collect_margin_rail_offenders(db: dict) -> list[str]:
    """Return a list naming every chip whose electrical.vcc_mv is still the
    4000 mV margin rail. Empty means the substitution ran to completion.

    Called by BOTH the real-DB Test 1 and the synthetic Test 5 non-vacuity
    leg -- never a parallel reimplementation.
    """
    offenders = []
    for mfr, part, idx, chip in _iter_chips_with_keys(db):
        vcc_mv = _mv(chip["electrical"]["vcc_mv"])
        if vcc_mv == _VCC_MARGIN_RAIL_MV:
            offenders.append(f"{mfr}/{part}#{idx}")
    return offenders


def _index_baseline(db: dict) -> dict[tuple[str, str, int], dict]:
    """Build a (manufacturer, part_number, index) -> chip lookup for the
    baseline database, using the same join-key discipline as
    `_iter_chips_with_keys`."""
    indexed = {}
    for mfr, part, idx, chip in _iter_chips_with_keys(db):
        indexed[(mfr, part, idx)] = chip
    return indexed


# ---------------------------------------------------------------------------
# Test 1: zero chips remain at the 4000 mV margin rail
# ---------------------------------------------------------------------------


def test_zero_chips_at_margin_rail():
    """No chip_database.json entry has electrical.vcc_mv == 4000.

    A non-empty offender list means the margin-rail substitution
    (build_db.py's post-construction mutation) failed to run, or ran
    incompletely, on at least one chip.
    """
    db = _load_db(_DB_FILE)
    offenders = _collect_margin_rail_offenders(db)
    assert not offenders, (
        "Expected zero chips at the 4000 mV VCC margin rail after the "
        f"Phase 148 DATA-01 substitution; found {len(offenders)}: {offenders}"
    )


# ---------------------------------------------------------------------------
# Test 2: exactly 56 movers, every one landing on its own vdd_mv
# ---------------------------------------------------------------------------


def test_exactly_56_chips_moved_4000_to_5000_onto_their_own_vdd():
    """Exactly 56 chips moved vcc_mv 4000 -> 5000 relative to the baseline,
    and every one of them landed on ITS OWN vdd_mv (not a fixed 5000
    literal) -- proving the substitution reads vdd_mv per-chip rather than
    hardcoding the (measured, but not guaranteed-forever) 5000 value.

    A different mover count means the margin-rail condition in build_db.py
    was widened or narrowed and must be re-measured against the four-way
    split table in 148-CONTEXT.md D-03 (55 / 16 / 12 / 1), never argued.
    """
    current_db = _load_db(_DB_FILE)
    baseline_db = _load_db(_BASELINE_FILE)
    baseline_indexed = _index_baseline(baseline_db)

    movers = []
    offenders_not_on_own_vdd = []
    for mfr, part, idx, chip in _iter_chips_with_keys(current_db):
        bl_chip = baseline_indexed.get((mfr, part, idx))
        if bl_chip is None:
            continue
        bl_vcc_mv = _mv(bl_chip["electrical"]["vcc"])
        cu_vcc_mv = _mv(chip["electrical"]["vcc_mv"])
        cu_vdd_mv = _mv(chip["electrical"]["vdd_mv"])
        if bl_vcc_mv == _VCC_MARGIN_RAIL_MV and cu_vcc_mv != _VCC_MARGIN_RAIL_MV:
            movers.append(f"{mfr}/{part}#{idx}")
            if cu_vcc_mv != cu_vdd_mv:
                offenders_not_on_own_vdd.append(
                    f"{mfr}/{part}#{idx} (vcc_mv={cu_vcc_mv}, vdd_mv={cu_vdd_mv})"
                )

    assert not offenders_not_on_own_vdd, (
        "Every margin-rail mover must land on its OWN vdd_mv, not a fixed "
        f"literal; offenders: {offenders_not_on_own_vdd}"
    )
    assert len(movers) == 56, (
        "Expected exactly 56 chips to move vcc_mv 4000 -> 5000 (measured "
        "blast radius, 148-CONTEXT.md D-03). A different count means the "
        "margin-rail condition was widened or narrowed and must be "
        f"re-measured against the four-way split table (55/16/12/1), never "
        f"argued. Found {len(movers)}: {sorted(movers)}"
    )


# ---------------------------------------------------------------------------
# Test 3: no-decrease guard
# ---------------------------------------------------------------------------


def test_no_chip_vcc_ever_decreases():
    """No chip's vcc_mv is lower than its own baseline value.

    This is the property the rejected type-keyed (85 movers) and
    algorithm-keyed (84 movers) alternatives would have violated on 16
    chips each, setting genuinely-5V Microchip EEPROMs to 3.3V. The
    margin-rail rule cannot violate this by construction (it only ever
    substitutes the higher vdd_mv for the 4000 mV rail), but this test
    pins that invariant directly against the measured data rather than
    trusting the construction argument alone.
    """
    current_db = _load_db(_DB_FILE)
    baseline_db = _load_db(_BASELINE_FILE)
    baseline_indexed = _index_baseline(baseline_db)

    offenders = []
    for mfr, part, idx, chip in _iter_chips_with_keys(current_db):
        bl_chip = baseline_indexed.get((mfr, part, idx))
        if bl_chip is None:
            continue
        bl_vcc_mv = _mv(bl_chip["electrical"]["vcc"])
        cu_vcc_mv = _mv(chip["electrical"]["vcc_mv"])
        if cu_vcc_mv < bl_vcc_mv:
            offenders.append(
                f"{mfr}/{part}#{idx} (baseline={bl_vcc_mv}, current={cu_vcc_mv})"
            )

    assert not offenders, (
        "No chip's vcc_mv may ever decrease relative to the baseline -- "
        f"the margin-rail rule cannot lower a voltage by construction. "
        f"Offenders: {offenders}"
    )


# ---------------------------------------------------------------------------
# Test 4 (DATA-04): the decode table itself is unedited
# ---------------------------------------------------------------------------


def test_vcc_voltages_table_unedited_and_no_new_part_keyed_dict():
    """VCC_VOLTAGES[0x02] still decodes to 4000 -- the margin-rail
    substitution sits AFTER the decode table, never inside it (D-01). And
    _PAGE_SIZE_BY_PART still has exactly 2 entries -- no new
    part-number-keyed sibling dict was introduced (DATA-04)."""
    from tools import build_db

    assert build_db.VCC_VOLTAGES[0x02] == 4000, (
        "VCC_VOLTAGES[0x02] must still decode to 4000 -- the decode table "
        "itself must never be edited by the margin-rail substitution"
    )
    assert build_db._VCC_MARGIN_RAIL_MV == 4000, (
        "_VCC_MARGIN_RAIL_MV must be single-sourced from VCC_VOLTAGES[0x02]"
    )
    assert len(build_db._PAGE_SIZE_BY_PART) == 2, (
        "_PAGE_SIZE_BY_PART must still have exactly 2 entries -- the "
        "margin-rail rule must not introduce a new part-number-keyed dict"
    )


# ---------------------------------------------------------------------------
# Test 5: non-vacuity proof
# ---------------------------------------------------------------------------


def test_synthetic_chip_at_margin_rail_is_flagged_non_vacuous():
    """Non-vacuity proof: a synthetic in-memory chip carrying
    vcc_mv == 4000 MUST be flagged by the SAME offender-collecting helper
    Test 1 calls against the real database.

    Proves the zero-at-rail gate is capable of failing -- not a vacuous
    always-pass check. Exercises `_collect_margin_rail_offenders` directly,
    not a parallel reimplementation.
    """
    synthetic_db = {
        "SYNTHETIC_MFR": [
            {
                "part_number": "SYNTHETIC_MARGIN_RAIL_VIOLATION",
                "electrical": {"vcc_mv": 4000, "vdd_mv": 5000},
            }
        ]
    }
    offenders = _collect_margin_rail_offenders(synthetic_db)
    assert offenders, (
        "Non-vacuity failure: the shared offender-collecting helper did not "
        "flag a synthetic chip at the 4000 mV margin rail -- the "
        "zero-at-rail gate is vacuous."
    )
