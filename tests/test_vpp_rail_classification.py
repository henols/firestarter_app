"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 199 (RAIL-02) -- the 30-row VPP-rail classification, as a test.

Coverage:
  1. ROW TOTAL AND VOLTAGE HISTOGRAM -- filtering the generated database for
     rows whose electrical.vpp_mv is at or above 18000 yields exactly 30
     rows, at exactly 21 rows at 18000, 3 at 21000 and 6 at 25000 -- the
     POST-override state (plan 199-01 moved one FUJITSU row from the 18000
     bucket to the 21000 bucket).
  2. PATH HISTOGRAM, NO UNMAPPED ALGORITHM -- mapping every row's
     programming.algorithm through the mirrored path constant below yields
     exactly 10 rows on the drop-resistor path and 20 on the direct-VPE
     path, and no row's algorithm falls outside the mapping.
  3. ALL SUPPORTED -- every one of the 30 rows carries support_status ==
     "supported". Nothing in this phase moves a support_status; this is the
     assertion that would catch it if something did.
  4. PER-PATH SHAPE -- every drop-resistor row is algorithm 0x07 with
     pin_count 28, and every direct-VPE row is algorithm 0x0B with
     pin_count 24, true across all 30 rows regardless of voltage. See THE
     DROP-RESISTOR EXCEPTION below: the per-path voltage sub-histogram is
     asserted here too, and it does not put every drop-resistor row at
     18000 or every above-floor row on the direct-VPE path.
  5. BOUNDARY -- the filter is at-or-above 18000, not strictly above: a row
     one millivolt below the floor is excluded, and a row sitting exactly
     on the floor is counted.
  6. NON-VACUITY, INJECTED ROW -- a synthetic 31st row injected into an
     in-memory deep copy at or above the floor shifts the measured total
     to 31, proving the total assertion can actually fail.
  7. NON-VACUITY, REWRITTEN ALGORITHM -- rewriting one drop-resistor row's
     algorithm to the direct-VPE algorithm, in an in-memory deep copy,
     shifts the path histogram, proving the path assertion can fail.
  8. NON-VACUITY, REWRITTEN STATUS -- rewriting one row's support_status in
     an in-memory deep copy breaks the all-supported claim, proving that
     assertion can fail too.
  9. SOURCE-SHAPE GUARD -- reads this module's own source and asserts every
     census and non-vacuity test exists by name, that the load-bearing
     count definitions have not been quietly relaxed, and that none of a
     named list of weakening idioms has crept in.

None of the three non-vacuity mutations ever touches chip_database.json on
disk -- each loads the database once and mutates a `copy.deepcopy` of it,
following the same discipline test_wire_dict_equivalence.py's non-vacuity
legs use for the delta layers.

The algorithm-to-VPP-path mapping is a deliberate mirror of the firmware's
protocol-keyed parameter table. The host has no access to that table -- the
concept of a VPP delivery path appears nowhere in the shipped database, the
wire dictionary, the constants module, or any other test in this package --
so the mapping below is hand-transcribed once, as a single named constant,
rather than derived. Because of that, THIS TEST DETECTS A DATABASE-SIDE
CHANGE ONLY: if a future firmware change repoints an algorithm onto a
different rail, nothing here will notice.

A firmware-scanning alternative was considered and refused. This project's
CI checks out only the host repository, with no submodule checkout step, so
a test that read the firmware source tree would pass in a devcontainer
where that tree happens to sit as a sibling directory and prove nothing at
all in CI -- a gate that fails open, which is worse than no gate.

No row among the 30 carries algorithm 0x08. The mirrored mapping's middle
entry is therefore never exercised by this census; a later reader should
not read that absence as an omission in the mapping.

THE DROP-RESISTOR EXCEPTION -- of the nine rows above the 18000 mV floor,
eight are unrelated to this phase's override: six are unsourced NMOS
override entries carried over from an earlier phase, all on the direct-VPE
path, and two are hardcoded in the non-upstream supplement file, also on
the direct-VPE path -- none of the eight is upstream-attested. The ninth
row is different in every way that matters here: it is the one row plan
199-01's datasheet-backed override moved out of the 18000 bucket, it sits
on the DROP-RESISTOR path rather than direct-VPE, and its 21000 mV figure
IS datasheet-backed rather than unsourced. That is why the drop-resistor
path is not uniformly at 18000 mV (nine of its ten rows are; the tenth is
the moved row) and why the direct-VPE path does not hold every row above
the floor (it holds eight of the nine; the ninth is the drop-resistor
exception).

Loads chip_database.json directly, the way test_build_db_inclusion.py and
test_b15_page_size_corroboration.py do, because the claim is about what the
generator emits rather than about what EpromDatabase resolves. Keeps no
part-number list -- every row property below is read from the loaded
database.
"""

import collections
import copy
import json
from pathlib import Path

_FA_DIR = Path(__file__).parent.parent
_DB_FILE = _FA_DIR / "firestarter" / "data" / "chip_database.json"

_VPP_CENSUS_FLOOR_MV = 18000

_ALGORITHM_TO_VPP_PATH = {
    0x07: "drop-resistor",
    0x08: "drop-resistor",
    0x0B: "direct-vpe",
}

_UNMAPPED_PATH_LABEL = "unmapped"

_EXPECTED_TOTAL_ROWS = 30
_EXPECTED_VOLTAGE_HISTOGRAM = {18000: 21, 21000: 3, 25000: 6}
_EXPECTED_PATH_HISTOGRAM = {"drop-resistor": 10, "direct-vpe": 20}


def _load_db() -> dict:
    return json.loads(_DB_FILE.read_text(encoding="utf-8"))


def _all_chips(db: dict):
    for mfg, chips in db.items():
        for chip in chips:
            yield mfg, chip


def _census(db: dict):
    """Return `(rows, voltage_histogram, path_histogram, status_histogram)`
    for every `(mfg, chip)` pair whose `electrical.vpp_mv` is at or above
    `_VPP_CENSUS_FLOOR_MV`. The one census helper every test in this module
    calls, so a failure names which property moved rather than which
    reimplementation disagreed with which.
    """
    rows = [
        (mfg, chip)
        for mfg, chip in _all_chips(db)
        if chip.get("electrical", {}).get("vpp_mv", 0) >= _VPP_CENSUS_FLOOR_MV
    ]
    voltage_histogram = dict(
        collections.Counter(chip["electrical"]["vpp_mv"] for _, chip in rows)
    )
    path_histogram = dict(
        collections.Counter(
            _ALGORITHM_TO_VPP_PATH.get(
                chip["programming"]["algorithm"], _UNMAPPED_PATH_LABEL
            )
            for _, chip in rows
        )
    )
    status_histogram = dict(
        collections.Counter(chip.get("support_status") for _, chip in rows)
    )
    return rows, voltage_histogram, path_histogram, status_histogram


def test_row_total_and_voltage_histogram_match_post_override_state() -> None:
    """The live generated database yields exactly 30 rows at or above the
    18000 mV floor, with a voltage histogram of exactly 21 at 18000, 3 at
    21000 and 6 at 25000 -- the POST-override state (plan 199-01's override
    is why this reads 21 / 3 / 6 rather than the pre-override 22 / 2 / 6).
    """
    rows, voltage_histogram, _, _ = _census(_load_db())

    assert len(rows) == _EXPECTED_TOTAL_ROWS, (
        f"expected exactly {_EXPECTED_TOTAL_ROWS} rows at or above "
        f"{_VPP_CENSUS_FLOOR_MV} mV, found {len(rows)}: a row arrived, left, "
        f"or moved across the floor"
    )
    assert voltage_histogram == _EXPECTED_VOLTAGE_HISTOGRAM, (
        f"expected the voltage histogram {_EXPECTED_VOLTAGE_HISTOGRAM}, "
        f"measured {voltage_histogram} -- either plan 199-01's override did "
        f"not apply or an upstream voltage moved"
    )


def test_path_histogram_has_no_unmapped_algorithm() -> None:
    """Mapping every row's algorithm through `_ALGORITHM_TO_VPP_PATH` yields
    exactly 10 drop-resistor rows and 20 direct-VPE rows, with no row's
    algorithm falling outside the mirrored mapping.
    """
    _, _, path_histogram, _ = _census(_load_db())

    assert _UNMAPPED_PATH_LABEL not in path_histogram, (
        f"an algorithm outside the mirrored mapping reached the census: "
        f"{path_histogram}"
    )
    assert path_histogram == _EXPECTED_PATH_HISTOGRAM, (
        f"expected the path histogram {_EXPECTED_PATH_HISTOGRAM}, measured "
        f"{path_histogram}"
    )


def test_every_row_in_the_census_is_supported() -> None:
    """D-01: every one of the 30 rows carries support_status ==
    "supported". Nothing in this phase moves a support_status; this
    assertion is what would catch it if something did.
    """
    rows, _, _, status_histogram = _census(_load_db())

    assert status_histogram == {"supported": len(rows)}, (
        f"expected every row 'supported', measured {status_histogram}"
    )


def test_per_path_algorithm_and_pin_count_are_uniform() -> None:
    """Every drop-resistor row is algorithm 0x07 with pin_count 28, and
    every direct-VPE row is algorithm 0x0B with pin_count 24 -- true across
    all 30 rows regardless of voltage. The per-path voltage sub-histogram is
    asserted here too: see the module docstring's DROP-RESISTOR EXCEPTION.
    """
    rows, _, _, _ = _census(_load_db())

    drop_resistor = [
        chip
        for _, chip in rows
        if _ALGORITHM_TO_VPP_PATH.get(chip["programming"]["algorithm"])
        == "drop-resistor"
    ]
    direct_vpe = [
        chip
        for _, chip in rows
        if _ALGORITHM_TO_VPP_PATH.get(chip["programming"]["algorithm"]) == "direct-vpe"
    ]

    assert len(drop_resistor) == 10
    assert all(chip["programming"]["algorithm"] == 0x07 for chip in drop_resistor)
    assert all(chip["electrical"]["pin_count"] == 28 for chip in drop_resistor)
    drop_resistor_mv = dict(
        collections.Counter(chip["electrical"]["vpp_mv"] for chip in drop_resistor)
    )
    assert drop_resistor_mv == {18000: 9, 21000: 1}, (
        f"expected the drop-resistor voltage sub-histogram {{18000: 9, "
        f"21000: 1}} (nine rows at the floor plus the one row plan 199-01 "
        f"moved), measured {drop_resistor_mv}"
    )

    assert len(direct_vpe) == 20
    assert all(chip["programming"]["algorithm"] == 0x0B for chip in direct_vpe)
    assert all(chip["electrical"]["pin_count"] == 24 for chip in direct_vpe)
    direct_vpe_mv = dict(
        collections.Counter(chip["electrical"]["vpp_mv"] for chip in direct_vpe)
    )
    assert direct_vpe_mv == {18000: 12, 21000: 2, 25000: 6}, (
        f"expected the direct-VPE voltage sub-histogram {{18000: 12, "
        f"21000: 2, 25000: 6}} (eight of the nine above-floor rows -- the "
        f"ninth is the drop-resistor exception), measured {direct_vpe_mv}"
    )


def test_boundary_is_at_or_above_not_strictly_above() -> None:
    """The census filter is at-or-above the floor, not strictly above: a
    synthetic row one millivolt below the floor is excluded, and a
    synthetic row sitting exactly on the floor is counted.
    """
    below_floor = {
        "SYNTH": [
            {
                "part_number": "SYNTH-BELOW",
                "support_status": "supported",
                "electrical": {"vpp_mv": _VPP_CENSUS_FLOOR_MV - 1, "pin_count": 28},
                "programming": {"algorithm": 0x07},
            }
        ]
    }
    at_floor = {
        "SYNTH": [
            {
                "part_number": "SYNTH-AT",
                "support_status": "supported",
                "electrical": {"vpp_mv": _VPP_CENSUS_FLOOR_MV, "pin_count": 28},
                "programming": {"algorithm": 0x07},
            }
        ]
    }

    below_rows, _, _, _ = _census(below_floor)
    at_rows, _, _, _ = _census(at_floor)

    assert below_rows == [], (
        f"a row one millivolt below the {_VPP_CENSUS_FLOOR_MV} mV floor must "
        f"be excluded, found {below_rows}"
    )
    assert len(at_rows) == 1, (
        f"a row exactly at the {_VPP_CENSUS_FLOOR_MV} mV floor must be "
        f"counted, found {at_rows}"
    )


def test_injecting_a_synthetic_row_makes_the_total_go_to_31() -> None:
    """Non-vacuity, defect class closed: a census helper that silently
    matched anything -- an empty row list, a swallowed exception, a filter
    that never selected -- would pass every other test in this module.
    Injecting a synthetic row at the floor into an in-memory deep copy of
    the loaded database must shift the measured total from 30 to 31. The
    original database on disk is never written to.
    """
    db = _load_db()
    mutated = copy.deepcopy(db)
    manufacturer = next(iter(sorted(mutated)))
    mutated[manufacturer].append(
        {
            "part_number": "SYNTHETIC-THIRTY-FIRST-ROW",
            "support_status": "supported",
            "electrical": {"vpp_mv": _VPP_CENSUS_FLOOR_MV, "pin_count": 28},
            "programming": {"algorithm": 0x07},
        }
    )

    baseline_rows, _, _, _ = _census(db)
    mutated_rows, _, _, _ = _census(mutated)

    assert len(baseline_rows) == 30
    assert len(mutated_rows) == 31, (
        f"injecting one synthetic row at the floor into an in-memory deep "
        f"copy must shift the total from 30 to 31, measured "
        f"{len(mutated_rows)}"
    )


def test_rewriting_an_algorithm_shifts_the_path_histogram() -> None:
    """Non-vacuity, defect class closed: a path split that could never
    disagree with the mirrored mapping would pass even if the mapping were
    deleted entirely. Rewriting one drop-resistor row's algorithm to the
    direct-VPE algorithm, in an in-memory deep copy, must shift the path
    histogram from 10/20 to 9/21.
    """
    db = _load_db()
    mutated = copy.deepcopy(db)
    rows, _, _, _ = _census(mutated)
    drop_resistor_chip = next(
        chip
        for _, chip in rows
        if _ALGORITHM_TO_VPP_PATH.get(chip["programming"]["algorithm"])
        == "drop-resistor"
    )
    drop_resistor_chip["programming"]["algorithm"] = 0x0B

    _, _, mutated_path_histogram, _ = _census(mutated)

    assert mutated_path_histogram == {"drop-resistor": 9, "direct-vpe": 21}, (
        f"rewriting one drop-resistor row's algorithm to the direct-VPE "
        f"algorithm, in an in-memory deep copy, must shift the path "
        f"histogram to 9 and 21, measured {mutated_path_histogram}"
    )


def test_rewriting_support_status_breaks_the_all_supported_claim() -> None:
    """Non-vacuity, defect class closed: an all-supported claim that could
    never disagree would pass even over a database where D-01 no longer
    holds. Rewriting one row's support_status, in an in-memory deep copy,
    must break the all-30-supported claim.
    """
    db = _load_db()
    mutated = copy.deepcopy(db)
    rows, _, _, _ = _census(mutated)
    some_chip = rows[0][1]
    some_chip["support_status"] = "adapter-required"

    mutated_rows, _, _, mutated_status_histogram = _census(mutated)

    assert mutated_status_histogram != {"supported": len(mutated_rows)}, (
        f"rewriting one row's support_status, in an in-memory deep copy, "
        f"must break the all-supported claim, measured "
        f"{mutated_status_histogram}"
    )
    assert mutated_status_histogram.get("adapter-required") == 1


_REQUIRED_TEST_NAMES = (
    "test_row_total_and_voltage_histogram_match_post_override_state",
    "test_path_histogram_has_no_unmapped_algorithm",
    "test_every_row_in_the_census_is_supported",
    "test_per_path_algorithm_and_pin_count_are_uniform",
    "test_boundary_is_at_or_above_not_strictly_above",
    "test_injecting_a_synthetic_row_makes_the_total_go_to_31",
    "test_rewriting_an_algorithm_shifts_the_path_histogram",
    "test_rewriting_support_status_breaks_the_all_supported_claim",
)

_DEFINITION_GUARD_COUNTS = {
    "_EXPECTED_TOTAL_ROWS" + " = 30": 1,
    "_EXPECTED_VOLTAGE_HISTOGRAM" + " = {18000: 21, 21000: 3, 25000: 6}": 1,
    "_EXPECTED_PATH_HISTOGRAM" + ' = {"drop-resistor": 10, "direct-vpe": 20}': 1,
    "=" + "= 30": 1,
    "=" + "= 31": 1,
}

_WEAKENING_IDIOMS = (
    "x" + "fail",
    "pytest.mark." + "skip",
    "issub" + "set",
    "firestarter" + "_fw",
    ">" + "= 30",
    ">" + "= 10",
    ">" + "= 20",
)


def test_module_source_shape_guards_against_weakening() -> None:
    """Source-shape guard: a later reader cannot quietly relax an exact
    count to a floor, delete a non-vacuity leg, or reach for an
    expected-failure marker instead of fixing a real regression, without
    this test noticing. Reads this module's own source rather than
    re-deriving the census, because the claim is about the FILE, not the
    database.

    Every entry in `_DEFINITION_GUARD_COUNTS` and `_WEAKENING_IDIOMS` above
    is assembled from two or more string fragments joined with `+`, rather
    than written as one contiguous literal. A guard written as one
    contiguous literal would count or match itself: `_DEFINITION_GUARD_COUNTS`
    would inflate every count by one for its own definition line, and
    `_WEAKENING_IDIOMS` would report its own list as containing every
    idiom it exists to forbid.
    """
    source = Path(__file__).read_text(encoding="utf-8")

    for name in _REQUIRED_TEST_NAMES:
        needle = "def " + name + "("
        assert needle in source, f"expected test {name!r} to exist by name"

    for literal, expected_count in _DEFINITION_GUARD_COUNTS.items():
        actual_count = source.count(literal)
        assert actual_count == expected_count, (
            f"expected {literal!r} to occur exactly {expected_count} "
            f"time(s) in the module source, found {actual_count} -- a "
            f"load-bearing count may have been relaxed"
        )

    for idiom in _WEAKENING_IDIOMS:
        assert idiom not in source, f"weakening idiom {idiom!r} found in module source"
