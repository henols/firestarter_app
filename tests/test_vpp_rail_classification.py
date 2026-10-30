"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 199 (RAIL-02) -- the VPP-rail classification, as a test.

Coverage:
  1. ROW TOTAL AND VOLTAGE HISTOGRAM -- filtering the generated database for
     rows whose electrical.vpp_mv is at or above 18000 yields exactly
     `_EXPECTED_TOTAL_ROWS` rows, with a voltage histogram of exactly
     `_EXPECTED_VOLTAGE_HISTOGRAM` -- the POST-override state.
  2. PATH HISTOGRAM, NO UNMAPPED ALGORITHM -- mapping every row's
     programming.algorithm through the mirrored path constant below yields
     exactly `_EXPECTED_PATH_HISTOGRAM`, and no row's algorithm falls
     outside the mapping.
  3. ALL SUPPORTED -- every row in the census carries support_status ==
     "supported". Nothing in this phase moves a support_status; this is the
     assertion that would catch it if something did.
  4. PER-PATH SHAPE -- every drop-resistor row is algorithm 0x07 with
     pin_count 28, and every direct-VPE row is algorithm 0x0B with
     pin_count 24, true across the whole census regardless of voltage. The
     per-path voltage sub-histograms are asserted here too. They do not put
     every drop-resistor row at 18000 or every above-floor row on the
     direct-VPE path: see THE DROP-RESISTOR EXCEPTION below.
  5. DROP-RESISTOR EXCEPTION -- every drop-resistor row above the floor
     holds its electrical.vpp_mv through a datasheet-cited entry in
     tools/datasheet_overrides.json, or is a non-upstream supplement row
     that cites a datasheet.
  6. BOUNDARY -- the filter is at-or-above 18000, not strictly above: a row
     one millivolt below the floor is excluded, and a row sitting exactly
     on the floor is counted.
  7. NON-VACUITY, INJECTED ROW -- a synthetic row injected into an
     in-memory deep copy at or above the floor adds one to the measured
     total, proving the total assertion can actually fail.
  8. NON-VACUITY, REWRITTEN ALGORITHM -- rewriting one drop-resistor row's
     algorithm to the direct-VPE algorithm, in an in-memory deep copy,
     shifts the path histogram, proving the path assertion can fail.
  9. NON-VACUITY, REWRITTEN STATUS -- rewriting one row's support_status in
     an in-memory deep copy breaks the all-supported claim, proving that
     assertion can fail too.
  10. SOURCE-SHAPE GUARD -- reads this module's own source and asserts every
      census and non-vacuity test exists by name, that the load-bearing
      count definitions have not been quietly relaxed, and that none of a
      named list of weakening idioms has crept in.

The two per-path voltage sub-histograms are the only literal counts in this
module. The total, the voltage histogram, the path histogram and every
non-vacuity expectation are computed from them, so a database change that
moves a row across the floor or between buckets edits those two literals
and nothing else.

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

No row in the census carries algorithm 0x08. The mirrored mapping's middle
entry is therefore never exercised by this census; a later reader should
not read that absence as an omission in the mapping.

THE DROP-RESISTOR EXCEPTION -- the rows above the 18000 mV floor come from
two different sources:

  - On the direct-VPE path, every above-floor row is either an UNSOURCED
    NMOS override entry carried over from an earlier phase or a row
    hardcoded in the non-upstream supplement file. None is
    upstream-attested.
  - On the DROP-RESISTOR path, every above-floor row is either a
    datasheet-backed override that moved a row out of the 18000 bucket or
    a supplement row in tools/extra_chips.json that cites a datasheet. Its
    figure IS datasheet-backed, not unsourced.

That is why the drop-resistor path is not uniformly at 18000 mV, and why
the direct-VPE path does not hold every row above the floor. Test 5 asserts
the drop-resistor half of this split against the override file and the
supplement rows' own provenance fields, so the list of parts lives in those
two files and not here.

Loads chip_database.json and datasheet_overrides.json directly, the way
test_build_db_inclusion.py and test_b15_page_size_corroboration.py load the
database, because the claim is about what the generator emits rather than
about what EpromDatabase resolves. Keeps no part-number list -- every row
property below is read from the loaded files.
"""

import collections
import copy
import json
from pathlib import Path

_FA_DIR = Path(__file__).parent.parent
_DB_FILE = _FA_DIR / "firestarter" / "data" / "chip_database.json"
_OVERRIDES_FILE = _FA_DIR / "tools" / "datasheet_overrides.json"

_VPP_CENSUS_FLOOR_MV = 18000

_ALGORITHM_TO_VPP_PATH = {
    0x07: "drop-resistor",
    0x08: "drop-resistor",
    0x0B: "direct-vpe",
}

_UNMAPPED_PATH_LABEL = "unmapped"

_SUPPLEMENT_SOURCE = "non-upstream-supplement"

_EXPECTED_DROP_RESISTOR_MV_HISTOGRAM = {18000: 9, 21000: 5}
_EXPECTED_DIRECT_VPE_MV_HISTOGRAM = {18000: 12, 21000: 2, 25000: 6}

_EXPECTED_PATH_HISTOGRAM = {
    "drop-resistor": sum(_EXPECTED_DROP_RESISTOR_MV_HISTOGRAM.values()),
    "direct-vpe": sum(_EXPECTED_DIRECT_VPE_MV_HISTOGRAM.values()),
}
_EXPECTED_VOLTAGE_HISTOGRAM = dict(
    collections.Counter(_EXPECTED_DROP_RESISTOR_MV_HISTOGRAM)
    + collections.Counter(_EXPECTED_DIRECT_VPE_MV_HISTOGRAM)
)
_EXPECTED_TOTAL_ROWS = sum(_EXPECTED_PATH_HISTOGRAM.values())


def _load_db() -> dict:
    return json.loads(_DB_FILE.read_text(encoding="utf-8"))


def _load_overrides() -> dict:
    return json.loads(_OVERRIDES_FILE.read_text(encoding="utf-8"))


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


def _rows_on_path(rows, path: str) -> list:
    return [
        (mfg, chip)
        for mfg, chip in rows
        if _ALGORITHM_TO_VPP_PATH.get(chip["programming"]["algorithm"]) == path
    ]


def test_row_total_and_voltage_histogram_match_post_override_state() -> None:
    """The live generated database yields exactly `_EXPECTED_TOTAL_ROWS`
    rows at or above the 18000 mV floor, with a voltage histogram of
    exactly `_EXPECTED_VOLTAGE_HISTOGRAM` -- the POST-override state.
    """
    rows, voltage_histogram, _, _ = _census(_load_db())

    assert len(rows) == _EXPECTED_TOTAL_ROWS, (
        f"expected exactly {_EXPECTED_TOTAL_ROWS} rows at or above "
        f"{_VPP_CENSUS_FLOOR_MV} mV, found {len(rows)}: a row arrived, left, "
        f"or moved across the floor"
    )
    assert voltage_histogram == _EXPECTED_VOLTAGE_HISTOGRAM, (
        f"expected the voltage histogram {_EXPECTED_VOLTAGE_HISTOGRAM}, "
        f"measured {voltage_histogram} -- either an override did not apply "
        f"or an upstream voltage moved"
    )


def test_path_histogram_has_no_unmapped_algorithm() -> None:
    """Mapping every row's algorithm through `_ALGORITHM_TO_VPP_PATH` yields
    exactly `_EXPECTED_PATH_HISTOGRAM`, with no row's algorithm falling
    outside the mirrored mapping.
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


# The 2532: the shield drives its pin 18 (A11) as CE.
_EXPECTED_NOT_SUPPORTED = {"adapter-required": 1}


def test_every_row_in_the_census_is_supported() -> None:
    """D-01: every row in the census carries support_status ==
    "supported", except the `_EXPECTED_NOT_SUPPORTED` rows. This assertion
    is what catches a moved support_status.
    """
    rows, _, _, status_histogram = _census(_load_db())

    expected = {
        "supported": len(rows) - sum(_EXPECTED_NOT_SUPPORTED.values()),
        **_EXPECTED_NOT_SUPPORTED,
    }
    assert status_histogram == expected, (
        f"expected the status histogram {expected}, measured {status_histogram}"
    )


def test_per_path_algorithm_and_pin_count_are_uniform() -> None:
    """Every drop-resistor row is algorithm 0x07 with pin_count 28, and
    every direct-VPE row is algorithm 0x0B with pin_count 24 -- true across
    the whole census regardless of voltage. The per-path voltage
    sub-histograms are asserted here too: see the module docstring's
    DROP-RESISTOR EXCEPTION.
    """
    rows, _, _, _ = _census(_load_db())

    drop_resistor = [chip for _, chip in _rows_on_path(rows, "drop-resistor")]
    direct_vpe = [chip for _, chip in _rows_on_path(rows, "direct-vpe")]

    assert all(chip["programming"]["algorithm"] == 0x07 for chip in drop_resistor)
    assert all(chip["electrical"]["pin_count"] == 28 for chip in drop_resistor)
    drop_resistor_mv = dict(
        collections.Counter(chip["electrical"]["vpp_mv"] for chip in drop_resistor)
    )
    assert drop_resistor_mv == _EXPECTED_DROP_RESISTOR_MV_HISTOGRAM, (
        f"expected the drop-resistor voltage sub-histogram "
        f"{_EXPECTED_DROP_RESISTOR_MV_HISTOGRAM}, measured {drop_resistor_mv}"
    )

    assert all(chip["programming"]["algorithm"] == 0x0B for chip in direct_vpe)
    assert all(chip["electrical"]["pin_count"] == 24 for chip in direct_vpe)
    direct_vpe_mv = dict(
        collections.Counter(chip["electrical"]["vpp_mv"] for chip in direct_vpe)
    )
    assert direct_vpe_mv == _EXPECTED_DIRECT_VPE_MV_HISTOGRAM, (
        f"expected the direct-VPE voltage sub-histogram "
        f"{_EXPECTED_DIRECT_VPE_MV_HISTOGRAM}, measured {direct_vpe_mv}"
    )


def _is_cited_supplement_row(chip: dict) -> bool:
    return chip.get("source") == _SUPPLEMENT_SOURCE and chip.get("datasheet") not in (
        None,
        "",
        "UNSOURCED",
    )


def test_every_drop_resistor_row_above_the_floor_is_datasheet_backed() -> None:
    """THE DROP-RESISTOR EXCEPTION, as an assertion: every drop-resistor row
    strictly above the floor matches a `datasheet_overrides.json` key whose
    `fields` map touches `electrical.vpp_mv` with a non-UNSOURCED
    datasheet, or is a non-upstream supplement row whose own `datasheet`
    field is set and not UNSOURCED. A `part_number` field can hold several
    comma-joined aliases, so each alias is matched separately.
    """
    overrides = _load_overrides()
    rows, _, _, _ = _census(_load_db())

    cited_keys = {
        key
        for key, entry in overrides.items()
        if "electrical.vpp_mv" in entry.get("fields", {})
        and entry.get("datasheet") != "UNSOURCED"
    }
    above_floor = [
        (mfg, chip)
        for mfg, chip in _rows_on_path(rows, "drop-resistor")
        if chip["electrical"]["vpp_mv"] > _VPP_CENSUS_FLOOR_MV
    ]
    unbacked = [
        f"{mfg}/{chip['part_number']}"
        for mfg, chip in above_floor
        if not _is_cited_supplement_row(chip)
        and not any(
            f"{mfg}/{alias.strip()}" in cited_keys
            for alias in chip["part_number"].split(",")
        )
    ]

    expected_above_floor = sum(
        count
        for mv, count in _EXPECTED_DROP_RESISTOR_MV_HISTOGRAM.items()
        if mv > _VPP_CENSUS_FLOOR_MV
    )
    assert len(above_floor) == expected_above_floor, (
        f"expected {expected_above_floor} drop-resistor rows above the floor, "
        f"found {len(above_floor)}"
    )
    assert unbacked == [], (
        f"drop-resistor rows above the floor with no datasheet-cited "
        f"electrical.vpp_mv override or cited supplement entry: {unbacked}"
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


def test_injecting_a_synthetic_row_adds_one_to_the_total() -> None:
    """Non-vacuity, defect class closed: a census helper that silently
    matched anything -- an empty row list, a swallowed exception, a filter
    that never selected -- would pass every other test in this module.
    Injecting a synthetic row at the floor into an in-memory deep copy of
    the loaded database must add one to the measured total. The original
    database on disk is never written to.
    """
    db = _load_db()
    mutated = copy.deepcopy(db)
    manufacturer = next(iter(sorted(mutated)))
    mutated[manufacturer].append(
        {
            "part_number": "SYNTHETIC-INJECTED-ROW",
            "support_status": "supported",
            "electrical": {"vpp_mv": _VPP_CENSUS_FLOOR_MV, "pin_count": 28},
            "programming": {"algorithm": 0x07},
        }
    )

    baseline_rows, _, _, _ = _census(db)
    mutated_rows, _, _, _ = _census(mutated)

    assert len(baseline_rows) == _EXPECTED_TOTAL_ROWS
    assert len(mutated_rows) == _EXPECTED_TOTAL_ROWS + 1, (
        f"injecting one synthetic row at the floor into an in-memory deep "
        f"copy must shift the total from {_EXPECTED_TOTAL_ROWS} to "
        f"{_EXPECTED_TOTAL_ROWS + 1}, measured {len(mutated_rows)}"
    )


def test_rewriting_an_algorithm_shifts_the_path_histogram() -> None:
    """Non-vacuity, defect class closed: a path split that could never
    disagree with the mirrored mapping would pass even if the mapping were
    deleted entirely. Rewriting one drop-resistor row's algorithm to the
    direct-VPE algorithm, in an in-memory deep copy, must move one row from
    the drop-resistor count to the direct-VPE count.
    """
    db = _load_db()
    mutated = copy.deepcopy(db)
    rows, _, _, _ = _census(mutated)
    _, drop_resistor_chip = _rows_on_path(rows, "drop-resistor")[0]
    drop_resistor_chip["programming"]["algorithm"] = 0x0B

    _, _, mutated_path_histogram, _ = _census(mutated)

    expected_path_histogram = {
        "drop-resistor": _EXPECTED_PATH_HISTOGRAM["drop-resistor"] - 1,
        "direct-vpe": _EXPECTED_PATH_HISTOGRAM["direct-vpe"] + 1,
    }
    assert mutated_path_histogram == expected_path_histogram, (
        f"rewriting one drop-resistor row's algorithm to the direct-VPE "
        f"algorithm, in an in-memory deep copy, must shift the path "
        f"histogram to {expected_path_histogram}, measured "
        f"{mutated_path_histogram}"
    )


def test_rewriting_support_status_breaks_the_all_supported_claim() -> None:
    """Non-vacuity, defect class closed: an all-supported claim that could
    never disagree would pass even over a database where D-01 no longer
    holds. Rewriting one row's support_status, in an in-memory deep copy,
    must break the all-supported claim.
    """
    db = _load_db()
    mutated = copy.deepcopy(db)
    rows, _, _, before = _census(mutated)
    some_chip = next(c for _, c in rows if c["support_status"] == "supported")
    some_chip["support_status"] = "adapter-required"

    _, _, _, mutated_status_histogram = _census(mutated)

    assert mutated_status_histogram != before, (
        f"rewriting one row's support_status, in an in-memory deep copy, "
        f"must move the status histogram, measured {mutated_status_histogram}"
    )
    assert (
        mutated_status_histogram.get("adapter-required")
        == before.get("adapter-required", 0) + 1
    )
