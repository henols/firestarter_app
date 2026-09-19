"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 200 (VCC-01) -- the 284-row elevated-programming-VCC census, as a test.

Coverage:
  1. ROW TOTAL AND VDD HISTOGRAM -- the live generated database holds exactly
     746 rows, of which the shipped predicate `programming_vcc_over_rail_mv`
     selects exactly 284, with a `vdd_mv` histogram of exactly `{5500: 164,
     6000: 8, 6250: 7, 6500: 105}`.
  2. ALL SUPPORTED, ALL UV-EPROM -- every one of the 284 rows carries
     `support_status == "supported"` and `electrical.type == "UV-EPROM"`,
     with zero exceptions. Nothing in this phase moves a `support_status` or
     a `type`; this is the assertion that would catch it if something did.
  3. ALGORITHM SPLIT AND VENDOR COUNT -- the 284 rows split across algorithms
     exactly `{7: 161, 8: 102, 11: 21}` and span exactly 34 distinct
     manufacturers.
  4. VOLT-03 DISJOINTNESS -- Phase 198's deliberately-untouched 28-row
     `electrical.vcc_mv == 5500` set (D-11) is fully disjoint from the
     284-row census. The overlap is exactly 0, which is the assertion that
     would catch a future decode change quietly pulling that group into the
     warned set.
  5. DATASHEET-CITED PROVENANCE -- exactly 3 of the 284 rows match a
     `tools/datasheet_overrides.json` key whose `fields` map touches
     `electrical.vdd_mv` with a non-`UNSOURCED` datasheet
     (`FUJITSU/MBM27128`, `FUJITSU/MBM27C1001`, `FUJITSU/MBM27C4001`); the
     remaining 281 carry a pure `VCC_VOLTAGES` rail-table slot. This is the
     measurement behind the 2026-09-19 amendment of D-04's verb from
     "programs at" to "decodes to".

A companion plan task extends this module with the boundary, fail-open and
non-vacuity legs, plus a source-shape guard, over the census helper defined
below.

Every count in this module is a property of the CURRENT generated database,
and a regeneration invalidates all of them -- which is the intended
behaviour, not a fragility: a silent decode change that swept new rows into
the warned set is exactly what these equalities exist to surface.

D-06's fail-open branch is unreachable from the shipped database, because
`build_db.py` decodes `vdd_mv` through a `.get(..., 5000)` default and every
one of the 746 rows therefore carries a non-zero integer. It is nonetheless
live in production, through the `~/.firestarter/database.json` override seam
that `EpromDatabase` merges over the packaged database, so a reviewer must
not delete it as dead code.

Loads chip_database.json and datasheet_overrides.json directly, the way
test_vpp_rail_classification.py loads the database, because the claims are
about what the generator emits and what the override file records rather
than about what EpromDatabase resolves. Keeps no part-number list -- every
row property below is read from the loaded files.
"""

import collections
import json
from pathlib import Path

from firestarter.eprom_info import programming_vcc_over_rail_mv

_FA_DIR = Path(__file__).parent.parent
_DB_FILE = _FA_DIR / "firestarter" / "data" / "chip_database.json"
_OVERRIDES_FILE = _FA_DIR / "tools" / "datasheet_overrides.json"

_EXPECTED_DATABASE_ROWS = 746
_EXPECTED_TOTAL_ROWS = 284
_EXPECTED_VDD_HISTOGRAM = {5500: 164, 6000: 8, 6250: 7, 6500: 105}
_EXPECTED_ALGORITHM_HISTOGRAM = {7: 161, 8: 102, 11: 21}
_EXPECTED_TYPE_HISTOGRAM = {"UV-EPROM": 284}
_EXPECTED_STATUS_HISTOGRAM = {"supported": 284}
_EXPECTED_VENDOR_COUNT = 34
_EXPECTED_DATASHEET_CITED_ROWS = 3
_EXPECTED_PURE_DECODE_ROWS = 281
_VOLT03_VCC_MV = 5500
_EXPECTED_VOLT03_ROWS = 28
_EXPECTED_DATASHEET_CITED_KEYS = (
    "FUJITSU/MBM27128",
    "FUJITSU/MBM27C1001",
    "FUJITSU/MBM27C4001",
)


def _load_db() -> dict:
    return json.loads(_DB_FILE.read_text(encoding="utf-8"))


def _load_overrides() -> dict:
    return json.loads(_OVERRIDES_FILE.read_text(encoding="utf-8"))


def _all_chips(db: dict):
    for mfg, chips in db.items():
        for chip in chips:
            yield mfg, chip


def _census(db: dict):
    """Return `(rows, vdd_histogram, algorithm_histogram, type_histogram,
    status_histogram)` for every `(mfg, chip)` pair for which the SHIPPED
    predicate `programming_vcc_over_rail_mv` is not `None`. The one census
    helper every test in this module calls, so a failure names which
    property moved rather than which reimplementation disagreed with which.

    Imports the predicate from `firestarter.eprom_info` rather than
    re-deriving `vdd_mv > 5000` locally -- that is what makes the 284-row
    count an assertion about the shipped product rather than about a copy
    of it.
    """
    rows = [
        (mfg, chip)
        for mfg, chip in _all_chips(db)
        if programming_vcc_over_rail_mv(chip) is not None
    ]
    vdd_histogram = dict(
        collections.Counter(chip["electrical"]["vdd_mv"] for _, chip in rows)
    )
    algorithm_histogram = dict(
        collections.Counter(chip["programming"]["algorithm"] for _, chip in rows)
    )
    type_histogram = dict(
        collections.Counter(chip["electrical"]["type"] for _, chip in rows)
    )
    status_histogram = dict(
        collections.Counter(chip.get("support_status") for _, chip in rows)
    )
    return rows, vdd_histogram, algorithm_histogram, type_histogram, status_histogram


def test_row_total_and_vdd_histogram_match_the_live_database() -> None:
    """The live generated database holds exactly `_EXPECTED_DATABASE_ROWS`
    rows in total, of which the shipped predicate selects exactly
    `_EXPECTED_TOTAL_ROWS`, with a `vdd_mv` histogram of exactly
    `_EXPECTED_VDD_HISTOGRAM` (D-01).
    """
    db = _load_db()
    total_db_rows = sum(len(chips) for chips in db.values())

    assert total_db_rows == _EXPECTED_DATABASE_ROWS, (
        f"expected exactly {_EXPECTED_DATABASE_ROWS} rows in the whole "
        f"database, found {total_db_rows}"
    )

    rows, vdd_histogram, _, _, _ = _census(db)

    assert len(rows) == _EXPECTED_TOTAL_ROWS, (
        f"expected exactly {_EXPECTED_TOTAL_ROWS} elevated rows, found "
        f"{len(rows)}: a row arrived, left, or moved across the rail"
    )
    assert vdd_histogram == _EXPECTED_VDD_HISTOGRAM, (
        f"expected the vdd_mv histogram {_EXPECTED_VDD_HISTOGRAM}, "
        f"measured {vdd_histogram}"
    )


def test_every_elevated_row_is_a_supported_uv_eprom() -> None:
    """D-01: all 284 rows are `support_status: supported` and all 284 are
    `electrical.type: UV-EPROM`, with zero exceptions. Nothing in this
    phase moves a `support_status` or a `type`; this is the assertion that
    would catch it if something did.
    """
    _, _, _, type_histogram, status_histogram = _census(_load_db())

    assert type_histogram == _EXPECTED_TYPE_HISTOGRAM, (
        f"expected every elevated row 'UV-EPROM', measured {type_histogram}"
    )
    assert status_histogram == _EXPECTED_STATUS_HISTOGRAM, (
        f"expected every elevated row 'supported', measured {status_histogram}"
    )


def test_algorithm_split_and_vendor_count_are_exact() -> None:
    """The 284 rows split across algorithms exactly
    `_EXPECTED_ALGORITHM_HISTOGRAM` and span exactly `_EXPECTED_VENDOR_COUNT`
    distinct manufacturers.
    """
    rows, _, algorithm_histogram, _, _ = _census(_load_db())

    assert algorithm_histogram == _EXPECTED_ALGORITHM_HISTOGRAM, (
        f"expected the algorithm histogram {_EXPECTED_ALGORITHM_HISTOGRAM}, "
        f"measured {algorithm_histogram}"
    )

    vendors = {mfg for mfg, _ in rows}
    assert len(vendors) == _EXPECTED_VENDOR_COUNT, (
        f"expected exactly {_EXPECTED_VENDOR_COUNT} distinct manufacturers "
        f"across the elevated rows, found {len(vendors)}: {sorted(vendors)}"
    )


def test_volt03_five_five_volt_set_is_disjoint_from_the_elevated_set() -> None:
    """Protects Phase 198's D-11 group: the 28 rows carrying
    `electrical.vcc_mv == 5500`, which D-03 deliberately leaves untouched by
    this phase's predicate. A future decode change could quietly pull that
    group into the warned set; this test is what would catch it. Compared
    by a `(manufacturer, part_number)` key, not by dict equality, because
    two distinct rows could otherwise compare equal.
    """
    db = _load_db()
    rows, _, _, _, _ = _census(db)

    volt03_rows = [
        (mfg, chip)
        for mfg, chip in _all_chips(db)
        if chip.get("electrical", {}).get("vcc_mv") == _VOLT03_VCC_MV
    ]
    assert len(volt03_rows) == _EXPECTED_VOLT03_ROWS, (
        f"expected exactly {_EXPECTED_VOLT03_ROWS} rows at "
        f"electrical.vcc_mv == {_VOLT03_VCC_MV}, found {len(volt03_rows)}"
    )

    census_keys = {(mfg, chip["part_number"]) for mfg, chip in rows}
    volt03_keys = {(mfg, chip["part_number"]) for mfg, chip in volt03_rows}
    overlap = census_keys & volt03_keys
    assert overlap == set(), (
        f"the VOLT-03 5500 mV set must be fully disjoint from the elevated "
        f"census, found overlap {overlap}"
    )


def test_only_three_elevated_rows_carry_a_datasheet_cited_vdd_override() -> None:
    """Pins the measurement behind the 2026-09-19 amendment of D-04's verb:
    of the 284 elevated rows, exactly 3 match a `datasheet_overrides.json`
    key whose `fields` map touches `electrical.vdd_mv` with a non-UNSOURCED
    datasheet, and the other 281 carry a pure `VCC_VOLTAGES` rail-table
    slot. A `part_number` field can hold several comma-joined aliases (e.g.
    `MBM27C1000P,MBM27C1000`), so each alias is matched separately -- an
    exact-string match on the raw field misses every multi-alias row and
    produces a wrong-but-plausible count. A fourth datasheet-cited row
    appearing here is the signal to revisit the wording, not to update the
    number.
    """
    db = _load_db()
    overrides = _load_overrides()
    rows, _, _, _, _ = _census(db)

    cited_keys = {
        key
        for key, entry in overrides.items()
        if "electrical.vdd_mv" in entry.get("fields", {})
        and entry.get("datasheet") != "UNSOURCED"
    }

    matched = set()
    for mfg, chip in rows:
        aliases = [alias.strip() for alias in chip["part_number"].split(",")]
        for alias in aliases:
            key = f"{mfg}/{alias}"
            if key in cited_keys:
                matched.add(key)

    assert len(matched) == _EXPECTED_DATASHEET_CITED_ROWS, (
        f"expected exactly {_EXPECTED_DATASHEET_CITED_ROWS} elevated rows "
        f"to carry a datasheet-cited vdd_mv override, matched {matched}"
    )
    assert matched == set(_EXPECTED_DATASHEET_CITED_KEYS), (
        f"expected the matched key set to equal "
        f"{set(_EXPECTED_DATASHEET_CITED_KEYS)}, measured {matched}"
    )
    assert len(rows) - len(matched) == _EXPECTED_PURE_DECODE_ROWS, (
        f"expected {_EXPECTED_PURE_DECODE_ROWS} pure-decode rows, measured "
        f"{len(rows) - len(matched)}"
    )
