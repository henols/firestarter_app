"""
Phase 149 Plan 03 (PGSZ-01 / D-07) -- exhaustive host-side proof of the
provenance-keyed page_size emit rule.

Reads firestarter/data/chip_database.json directly (not through
EpromDatabase), so this measures the shipped, GENERATED artifact rather than
the loader's interpretation -- the same discipline test_sdp_db_invariant.py
follows. This module intentionally carries NO firmware-presence skip marker:
it reads only the packaged chip_database.json and tools/extra_chips.json,
both always present in host-only CI.

**The rule under test is a claim about provenance, never about a part.**
page_size is meaningful for the algorithm that consumes it, and a record
filed upstream under 0x07/0x0B is not evidence about a 28C page buffer.
Measured (149-RESEARCH.md section "D-01 Verification"): of the 84
chip_database.json entries carrying programming.algorithm == 13
(EEPROM_POLL / 0x0D), only 18 are upstream-native 0x0D records -- the other
66 are promoted into 0x0D by build_db.py's classify() from a foreign
protocol (0x07/0x0B) and must NOT gain a page_size the promoting protocol
never corroborated.

Coverage (11 legs):
  1. Exactly 84 rows carry programming.algorithm == 13.
  2. Exactly 18 of those 84 carry programming.page_size, and their
     (manufacturer, part_number) set equals the 18 named upstream-native
     rows below.
  3. Of the 18, exactly 15 carry 128 and exactly 3 carry 64.
  4. Across all 746 rows, exactly 20 carry programming.page_size
     (18 native + 2 curated _PAGE_SIZE_BY_PART rows).
  5. Every emitted page_size anywhere in the database is a power of two in
     [1, 512] -- a shared module-level helper, so the leg-10 synthetic test
     calls the exact same code the real-DB test calls.
  6. Provenance: every row carrying programming.page_size is either one of
     the two curated rows or one of the 18 named native rows -- nothing
     else. Power-of-two-ness alone is NOT sufficient (256 is a power of two
     and would be wrong on a promoted row) -- a second shared helper.
  7. AT28C256 non-change: the gh#21 part is a PROMOTED row (upstream
     protocol_id 0x07) and this phase cannot change its behaviour at all.
  8. support_status byte-unchanged for all 84 algorithm==13 rows against
     the committed baseline -- the Evidence Ceiling made machine-checked.
  9. extra_chips.json back door: no authored supplement record carries
     page_size -- those rows bypass classify() and the emitter entirely.
 10. Synthetic non-vacuity, range: a one-chip in-memory DB with
     page_size: 96 IS flagged by the leg-5 helper.
 11. Synthetic non-vacuity, provenance: a one-chip in-memory DB with a
     PROMOTED-shaped row carrying page_size: 256 IS flagged by the leg-6
     helper.

Growing either identity set below requires a fresh provenance measurement
against 149-RESEARCH.md section "D-01 Verification" -- the same
non-launderable property D-17 gives the wire golden (tests/golden/
wire_dict_expected_deltas_149.json).
"""

import json
from pathlib import Path

# Absolute paths (independent of cwd), mirroring test_sdp_db_invariant.py /
# test_b15_page_size_corroboration.py's path idiom.
_FA_DIR = Path(__file__).parent.parent
_DB_FILE = _FA_DIR / "firestarter" / "data" / "chip_database.json"
_EXTRA_CHIPS_FILE = _FA_DIR / "tools" / "extra_chips.json"
_BASELINE_FILE = _FA_DIR / "tools" / "baseline" / "chip_database.baseline.json"

_ALGORITHM_0X0D = 13

# ---------------------------------------------------------------------------
# The two identity sets this test owns. Measured against 149-RESEARCH.md
# section "D-01 Verification" (the four-way protocol_id join across all 84
# algorithm==13 rows). DO NOT extend either set without a fresh provenance
# measurement -- see the module docstring.
# ---------------------------------------------------------------------------

# The 2 pre-existing datasheet-curated _PAGE_SIZE_BY_PART rows (both
# upstream algorithm 0x05, unrelated to the 0x0D provenance rule below).
_CURATED_PAGE_SIZE_IDENTITIES = frozenset(
    {
        ("WINBOND", "W29C020,W29C020C,W29C022"),
        ("WINBOND", "W29C040,W29C042"),
    }
)

# The 18 upstream-native protocol_id==0x0D rows (149-RESEARCH.md section
# "D-01 Verification"): 15 movers at page 128, 3 already at page 64.
_NATIVE_0X0D_PAGE_SIZE_IDENTITIES_128 = frozenset(
    {
        ("ATMEL", "AT28C010,AT28C010E"),
        ("ATMEL", "AT28C040,AT28C040E"),
        ("ATMEL", "AT28LV010"),
        ("ATMEL", "AT28MC020"),
        ("ATMEL", "AT28MC040"),
        ("CATALYST(CSI)", "CAT28C010"),
        ("CATALYST(CSI)", "CAT28C020"),
        ("CATALYST(CSI)", "CAT28C040"),
        ("CATALYST(CSI)", "CAT28C512"),
        ("MAXWELL", "28C010,28C010T,28C011,28C011T"),
        ("SGS-THOMSON", "M28010"),
        ("ST", "M28010"),
        ("WED", "WE512K8"),
        ("WED", "WME128K8"),
        ("XICOR", "X28C010"),
    }
)
_NATIVE_0X0D_PAGE_SIZE_IDENTITIES_64 = frozenset(
    {
        ("ATMEL", "AT28MC010"),
        ("WED", "WE128K8"),
        ("WED", "WE256K8"),
    }
)
_NATIVE_0X0D_PAGE_SIZE_IDENTITIES = (
    _NATIVE_0X0D_PAGE_SIZE_IDENTITIES_128 | _NATIVE_0X0D_PAGE_SIZE_IDENTITIES_64
)

_ALL_PROVENANCE_CORROBORATED_IDENTITIES = (
    _CURATED_PAGE_SIZE_IDENTITIES | _NATIVE_0X0D_PAGE_SIZE_IDENTITIES
)

_AT28C256_PART_NUMBER_PREFIX = "AT28C256,"
_AT28C256_MANUFACTURER = "ATMEL"


def _load_db(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _select_0x0d_chips(db: dict) -> list[tuple[str, dict]]:
    """Select every (manufacturer, chip) pair with programming.algorithm == 13.

    The DB shape is {manufacturer: [chip, ...]}; a top-level scan on db
    (rather than this nested per-chip access) finds nothing and would make
    every downstream assertion pass vacuously.
    """
    selected = []
    for mfr, chips in db.items():
        for chip in chips:
            if chip["programming"]["algorithm"] == _ALGORITHM_0X0D:
                selected.append((mfr, chip))
    return selected


def _select_page_size_carriers(db: dict) -> list[tuple[str, dict]]:
    """Select every (manufacturer, chip) pair carrying programming.page_size."""
    selected = []
    for mfr, chips in db.items():
        for chip in chips:
            if "page_size" in chip["programming"]:
                selected.append((mfr, chip))
    return selected


# ---------------------------------------------------------------------------
# Shared helpers -- both the real-DB tests (legs 5/6) and the synthetic
# non-vacuity tests (legs 10/11) call these, so the non-vacuity legs
# exercise the exact same code the real tests do.
# ---------------------------------------------------------------------------


def _range_offenders(db: dict) -> list[str]:
    """Return a list naming every page_size carrier NOT a power of two in
    [1, 512]. An empty list means the invariant holds.
    """
    offenders = []
    for mfr, chip in _select_page_size_carriers(db):
        v = chip["programming"]["page_size"]
        if not (1 <= v <= 512 and (v & (v - 1)) == 0):
            offenders.append(f"{mfr}/{chip.get('part_number', '?')}: page_size={v}")
    return offenders


def _provenance_offenders(db: dict) -> list[str]:
    """Return a list naming every page_size carrier whose (manufacturer,
    part_number) identity is neither one of the 2 curated rows nor one of
    the 18 named upstream-native 0x0D rows. An empty list means the
    invariant holds.

    Power-of-two-ness alone is NOT sufficient here -- 256 is a power of two
    and would still be wrong on a promoted (non-native) row.
    """
    offenders = []
    for mfr, chip in _select_page_size_carriers(db):
        identity = (mfr, chip.get("part_number", "?"))
        if identity not in _ALL_PROVENANCE_CORROBORATED_IDENTITIES:
            v = chip["programming"]["page_size"]
            offenders.append(f"{mfr}/{chip.get('part_number', '?')}: page_size={v}")
    return offenders


# ---------------------------------------------------------------------------
# Leg 1: real-DB count of the 0x0D bucket itself.
# ---------------------------------------------------------------------------


def test_exactly_84_algorithm_0x0d_entries() -> None:
    """A count change means a chip entered or left the 0x0D bucket, which
    invalidates every other leg in this module.
    """
    db = _load_db(_DB_FILE)
    selected = _select_0x0d_chips(db)
    assert len(selected) == 84, (
        f"expected exactly 84 chip_database.json entries with "
        f"programming.algorithm == 13, found {len(selected)}"
    )


# ---------------------------------------------------------------------------
# Leg 2: exactly 18 of the 84 carry page_size, and they are the named 18.
# ---------------------------------------------------------------------------


def test_exactly_18_of_84_carry_page_size_and_are_the_named_rows() -> None:
    db = _load_db(_DB_FILE)
    a13 = _select_0x0d_chips(db)
    native_carriers = [
        (mfr, chip.get("part_number", "?"))
        for mfr, chip in a13
        if "page_size" in chip["programming"]
    ]
    assert len(native_carriers) == 18, (
        f"expected exactly 18 of the 84 algorithm==13 rows to carry "
        f"page_size, found {len(native_carriers)}: {native_carriers}"
    )
    assert set(native_carriers) == _NATIVE_0X0D_PAGE_SIZE_IDENTITIES, (
        "the 18 native page_size carriers drifted from the named set -- "
        f"symmetric difference: {set(native_carriers) ^ _NATIVE_0X0D_PAGE_SIZE_IDENTITIES}"
    )


# ---------------------------------------------------------------------------
# Leg 3: of the 18, exactly 15 at 128 and 3 at 64.
# ---------------------------------------------------------------------------


def test_18_native_carriers_split_15_at_128_and_3_at_64() -> None:
    db = _load_db(_DB_FILE)
    a13 = _select_0x0d_chips(db)
    native = [
        (mfr, chip.get("part_number", "?"), chip["programming"]["page_size"])
        for mfr, chip in a13
        if "page_size" in chip["programming"]
    ]
    at_128 = [row for row in native if row[2] == 128]
    at_64 = [row for row in native if row[2] == 64]
    assert len(at_128) == 15, f"expected 15 rows at page_size 128, got {at_128}"
    assert len(at_64) == 3, f"expected 3 rows at page_size 64, got {at_64}"
    assert {(m, p) for m, p, _v in at_128} == _NATIVE_0X0D_PAGE_SIZE_IDENTITIES_128
    assert {(m, p) for m, p, _v in at_64} == _NATIVE_0X0D_PAGE_SIZE_IDENTITIES_64


# ---------------------------------------------------------------------------
# Leg 4: across all 746 rows, exactly 20 carry page_size (18 native + 2
# curated).
# ---------------------------------------------------------------------------


def test_exactly_20_page_size_carriers_across_all_746_rows() -> None:
    db = _load_db(_DB_FILE)
    total_rows = sum(len(chips) for chips in db.values())
    assert total_rows == 746, f"expected 746 total rows, found {total_rows}"
    carriers = _select_page_size_carriers(db)
    assert len(carriers) == 20, (
        f"expected exactly 20 page_size carriers (18 native + 2 curated) "
        f"across all 746 rows, found {len(carriers)}: "
        f"{[(m, c.get('part_number', '?')) for m, c in carriers]}"
    )


# ---------------------------------------------------------------------------
# Leg 5: every emitted page_size is a power of two in [1, 512] -- exhaustive.
# ---------------------------------------------------------------------------


def test_every_page_size_is_a_power_of_two_in_range() -> None:
    db = _load_db(_DB_FILE)
    offenders = _range_offenders(db)
    assert not offenders, (
        f"every emitted page_size must be a power of two in [1, 512]; "
        f"offenders: {offenders}"
    )


# ---------------------------------------------------------------------------
# Leg 6: provenance -- every carrier is curated or one of the 18 named
# native rows. Power-of-two alone is not sufficient.
# ---------------------------------------------------------------------------


def test_every_page_size_carrier_is_curated_or_native_0x0d() -> None:
    db = _load_db(_DB_FILE)
    offenders = _provenance_offenders(db)
    assert not offenders, (
        f"every page_size carrier must be either one of the 2 curated "
        f"_PAGE_SIZE_BY_PART rows or one of the 18 named upstream-native "
        f"0x0D rows; offenders: {offenders}"
    )


# ---------------------------------------------------------------------------
# Leg 7: AT28C256 non-change (gh#21 part -- a PROMOTED row, upstream
# protocol_id 0x07; this phase cannot change its behaviour at all).
# ---------------------------------------------------------------------------


def test_at28c256_is_unchanged_by_this_phase() -> None:
    """AT28C256 is the gh#21 part. It is a PROMOTED row (arrives upstream
    as protocol_id 0x07, promoted to 0x0D by build_db.py's classify()), so
    the provenance-keyed emit rule (which reads the chip's OWN upstream
    protocol_id, captured before classify() overwrites it) must never fire
    for it. This phase therefore cannot change its behaviour at all and
    explains nothing about gh#21 (Evidence Ceiling, PROJECT.md).
    """
    db = _load_db(_DB_FILE)
    baseline = _load_db(_BASELINE_FILE)

    def _find(d):
        for mfr, chips in d.items():
            for chip in chips:
                if mfr == _AT28C256_MANUFACTURER and chip.get(
                    "part_number", ""
                ).startswith(_AT28C256_PART_NUMBER_PREFIX):
                    return chip
        return None

    live = _find(db)
    base = _find(baseline)
    assert live is not None, "AT28C256 row not found in the generated database"
    assert base is not None, "AT28C256 row not found in the committed baseline"
    assert "page_size" not in live["programming"], (
        "AT28C256 (gh#21) must not gain programming.page_size -- it is a "
        "promoted row, upstream protocol_id 0x07"
    )
    assert live["programming"]["infoic_page_size_raw"] == 64, (
        "AT28C256's raw upstream page_size attribute must stay 64"
    )
    assert live["support_status"] == base["support_status"], (
        "AT28C256's support_status must be byte-unchanged from the baseline"
    )


# ---------------------------------------------------------------------------
# Leg 8: support_status byte-unchanged across all 84 algorithm==13 rows.
# ---------------------------------------------------------------------------


def test_support_status_byte_unchanged_across_all_84_0x0d_rows() -> None:
    """The Evidence Ceiling made machine-checked: this phase must not
    change any 0x0D chip's support_status or imply any part is
    write-graduated.
    """
    db = _load_db(_DB_FILE)
    baseline = _load_db(_BASELINE_FILE)

    def _index(d):
        idx = {}
        for mfr, chips in d.items():
            for chip in chips:
                if chip["programming"]["algorithm"] == _ALGORITHM_0X0D:
                    idx[(mfr, chip.get("part_number", "?"))] = chip["support_status"]
        return idx

    live_idx = _index(db)
    base_idx = _index(baseline)
    assert set(live_idx) == set(base_idx), (
        "the set of algorithm==13 (mfr, part_number) identities drifted "
        f"between the live database and the baseline: "
        f"{set(live_idx) ^ set(base_idx)}"
    )
    offenders = [
        identity for identity in live_idx if live_idx[identity] != base_idx[identity]
    ]
    assert not offenders, (
        f"support_status must be byte-unchanged for every algorithm==13 "
        f"row; changed identities: {offenders}"
    )


# ---------------------------------------------------------------------------
# Leg 9: extra_chips.json back door -- no authored supplement record
# carries page_size.
# ---------------------------------------------------------------------------


def test_extra_chips_json_carries_no_page_size() -> None:
    """tools/extra_chips.json rows bypass classify() and the emitter
    entirely, so D-01's provenance rule is unenforced against that path --
    they must never author a page_size value themselves.
    """
    extra = _load_db(_EXTRA_CHIPS_FILE)
    records = [chip for chips in extra.values() for chip in chips]
    assert records, "tools/extra_chips.json must be non-empty, or this leg is vacuous"
    offenders = [
        chip.get("part_number", "?")
        for chip in records
        if "page_size" in chip.get("programming", {})
    ]
    assert not offenders, (
        f"tools/extra_chips.json records must never carry programming.page_size "
        f"(they bypass the emit rule entirely); offenders: {offenders}"
    )


# ---------------------------------------------------------------------------
# Leg 10: synthetic non-vacuity, range.
# ---------------------------------------------------------------------------


def test_synthetic_out_of_band_page_size_is_flagged_by_range_helper() -> None:
    synthetic_db = {
        "SYNTHETIC": [
            {
                "part_number": "SYNTH-RANGE-OFFENDER",
                "programming": {"algorithm": 13, "page_size": 96},
            }
        ]
    }
    offenders = _range_offenders(synthetic_db)
    assert offenders, (
        "the range helper must flag a page_size of 96 (not a power of two)"
    )
    assert "SYNTH-RANGE-OFFENDER" in offenders[0]


# ---------------------------------------------------------------------------
# Leg 11: synthetic non-vacuity, provenance.
# ---------------------------------------------------------------------------


def test_synthetic_promoted_row_page_size_is_flagged_by_provenance_helper() -> None:
    """A synthetic PROMOTED-shaped row (algorithm 13, a part-number identity
    absent from the 18 named native rows) carrying page_size: 256 must be
    flagged -- 256 is a power of two and would pass leg 5's helper, which is
    exactly why provenance is a separate, non-optional check.
    """
    synthetic_db = {
        "SYNTHETIC": [
            {
                "part_number": "SYNTH-PROMOTED-NOT-NATIVE",
                "programming": {"algorithm": 13, "page_size": 256},
            }
        ]
    }
    # Confirm it would NOT be caught by the range helper alone.
    assert not _range_offenders(synthetic_db), (
        "256 is a power of two in range -- this synthetic row must pass "
        "the range helper, proving provenance is a genuinely separate check"
    )
    offenders = _provenance_offenders(synthetic_db)
    assert offenders, (
        "the provenance helper must flag a promoted row's page_size even "
        "though its value is a power of two in range"
    )
    assert "SYNTH-PROMOTED-NOT-NATIVE" in offenders[0]
