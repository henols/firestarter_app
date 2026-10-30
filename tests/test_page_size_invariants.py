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

Coverage (legs keep their original numbers; leg 1 was a row count):
  2. The algorithm == 13 rows that carry programming.page_size are exactly
     the named upstream-native and DIP24_2816 rows below.
  3. The named native rows split into the named 128 and 64 sets.
  4. Across the whole database, the page_size carriers are exactly the
     named native 0x0D, native 0x05 and DIP24_2816 rows -- no curated
     carrier remains, per Phase 194 D-01/D-02.
  5. Every emitted page_size anywhere in the database is a power of two in
     [1, 512] -- a shared module-level helper, so the leg-10 synthetic test
     calls the exact same code the real-DB test calls.
  6. Provenance: every row carrying programming.page_size is either one of
     the 18 named native 0x0D rows or one of the 27 named native 0x05
     rows -- nothing else. Power-of-two-ness alone is NOT sufficient (256
     is a power of two and would be wrong on a promoted row) -- a second
     shared helper.
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
 12. The 27-row two-halves table (D-08's host half): for every one of the
     27 upstream-native 0x05 rows, the emitted page_size equals that row's
     own infoic_page_size_raw, and for exactly the 18 whose old
     capacity-bracket derivation was already correct, the emitted value
     also equals what that derivation produces.

Growing either identity set below requires a fresh provenance measurement
against 149-RESEARCH.md section "D-01 Verification" -- the same
non-launderable property D-17 gives the wire golden (tests/golden/
wire_dict.json).
"""

import json
from pathlib import Path

from tests.test_lock_status_class_partition import _ALGORITHM_0X05_KEYS

# Absolute paths (independent of cwd), mirroring test_sdp_db_invariant.py /
# test_b15_page_size_corroboration.py's path idiom.
_FA_DIR = Path(__file__).parent.parent
_DB_FILE = _FA_DIR / "firestarter" / "data" / "chip_database.json"
_EXTRA_CHIPS_FILE = _FA_DIR / "tools" / "extra_chips.json"
_BASELINE_FILE = _FA_DIR / "tools" / "baseline" / "chip_database.baseline.json"

_ALGORITHM_0X0D = 13
_ALGORITHM_0X05 = 5

_NATIVE_0X05_PAGE_SIZE_IDENTITIES = frozenset(
    tuple(key.split("/", 1)) for key in _ALGORITHM_0X05_KEYS
)
assert len(_NATIVE_0X05_PAGE_SIZE_IDENTITIES) == 27, (
    "the imported 0x05 identity set must carry exactly 27 members -- a "
    "partial import from _ALGORITHM_0X05_KEYS must fail loudly, not silently"
)

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

# DIP24_2816 rows are the one promoted class that carries page_size. Their
# upstream records hold the real write granularity (1 = byte write), and the
# 0x0D handler's 64-byte floor would load bytes during a byte-write part's
# write cycle. Provenance: the upstream raw value on that layout.
_DIP24_2816_PAGE_SIZE_IDENTITIES_16 = frozenset(
    {
        ("XICOR", "X2816B,X2816C"),
        ("EXEL", "XLE28C16B,XLS28C16B"),
    }
)
_DIP24_2816_PAGE_SIZE_IDENTITIES_1 = frozenset(
    {
        ("AMD", "AM28C16A"),
        ("ATMEL", "AT28C04,AT28HC04"),
        ("ATMEL", "AT28C04E,AT28C04F"),
        ("ATMEL", "AT28C16,AT28HC16,AT28HC16L"),
        ("ATMEL", "AT28C16E,AT28C16F"),
        ("CATALYST(CSI)", "CAT28C16A,CAT28C16AI"),
        ("EXEL", "XL2804A"),
        ("EXEL", "XL2816A,XLE28C16A,XLS28C16A"),
        ("MICROCHIP memory", "2804"),
        ("MICROCHIP memory", "2816"),
        ("MICROCHIP memory", "28C04A"),
        ("MICROCHIP memory", "28C04AF"),
        ("MICROCHIP memory", "28C16A"),
        ("MICROCHIP memory", "28C16AF"),
        ("NEC", "UPD28C04"),
        ("XICOR", "X2804A,X2804AI"),
        ("XICOR", "X2816A"),
    }
)
_DIP24_2816_PAGE_SIZE_IDENTITIES = (
    _DIP24_2816_PAGE_SIZE_IDENTITIES_1 | _DIP24_2816_PAGE_SIZE_IDENTITIES_16
)

# The nine DIP24_2816 rows that were adapter-required and are now supported.
_PROMOTED_FROM_ADAPTER_REQUIRED = frozenset(
    {
        ("ATMEL", "AT28C04,AT28HC04"),
        ("ATMEL", "AT28C04E,AT28C04F"),
        ("ATMEL", "AT28C16,AT28HC16,AT28HC16L"),
        ("ATMEL", "AT28C16E,AT28C16F"),
        ("MICROCHIP memory", "28C04A"),
        ("MICROCHIP memory", "28C04AF"),
        ("MICROCHIP memory", "28C16A"),
        ("MICROCHIP memory", "28C16AF"),
        ("NEC", "UPD28C04"),
    }
)

# The twelve 3V 0x0D rows that were supported and are now refused: their VPP
# byte names a VCC below 4.5V, and the shield supplies 5V.
_REFUSED_FOR_LOW_VCC = frozenset(
    {
        ("ATMEL", "AT28BV64,AT28LV64"),
        ("ATMEL", "AT28BV64B,AT28LV64B"),
        ("ATMEL", "AT28BV256,AT28LV256"),
        ("ATMEL", "AT28LV010"),
        ("CATALYST(CSI)", "CAT28LV64,CAT28LV65"),
        ("CATALYST(CSI)", "CAT28LV256"),
        ("CYPRESS", "FM28V020"),
        ("FUJITSU", "MB85R256H"),
        ("MICROCHIP memory", "28LV64A"),
        ("SGS-THOMSON", "M28C64-xxW"),
        ("ST", "M28C64-xxW"),
        ("ST", "M28LV64"),
    }
)

_ALL_PROVENANCE_CORROBORATED_IDENTITIES = (
    _NATIVE_0X0D_PAGE_SIZE_IDENTITIES
    | _NATIVE_0X05_PAGE_SIZE_IDENTITIES
    | _DIP24_2816_PAGE_SIZE_IDENTITIES
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


# Shared helpers -- both the real-DB tests (legs 5/6) and the synthetic
# non-vacuity tests (legs 10/11) call these, so the non-vacuity legs
# exercise the exact same code the real tests do.


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
    part_number) identity is neither one of the 18 named upstream-native
    0x0D rows nor one of the 27 named upstream-native 0x05 rows. An empty
    list means the invariant holds.

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


# Leg 2: the algorithm == 13 page_size carriers are the named rows.


def test_algorithm_0x0d_page_size_carriers_are_the_named_rows() -> None:
    """18 upstream-native 0x0D rows plus the 19 DIP24_2816 rows."""
    db = _load_db(_DB_FILE)
    a13 = _select_0x0d_chips(db)
    carriers = {
        (mfr, chip.get("part_number", "?"))
        for mfr, chip in a13
        if "page_size" in chip["programming"]
    }
    expected = _NATIVE_0X0D_PAGE_SIZE_IDENTITIES | _DIP24_2816_PAGE_SIZE_IDENTITIES
    assert carriers == expected, (
        "the algorithm==13 page_size carriers drifted from the named set -- "
        f"symmetric difference: {carriers ^ expected}"
    )


def test_dip24_2816_rows_carry_their_upstream_write_granularity() -> None:
    db = _load_db(_DB_FILE)
    values = {
        (mfr, chip["part_number"]): chip["programming"].get("page_size")
        for mfr, chips in db.items()
        for chip in chips
        if chip["pinout"] == "DIP24_2816"
    }
    assert set(values) == _DIP24_2816_PAGE_SIZE_IDENTITIES
    for identity, value in values.items():
        expected = 16 if identity in _DIP24_2816_PAGE_SIZE_IDENTITIES_16 else 1
        assert value == expected, (identity, value)


# Leg 3: the native carriers split into the named 128 and 64 sets.


def test_native_carriers_split_into_the_named_128_and_64_sets() -> None:
    db = _load_db(_DB_FILE)
    a13 = _select_0x0d_chips(db)
    native = [
        (mfr, chip.get("part_number", "?"), chip["programming"]["page_size"])
        for mfr, chip in a13
        if "page_size" in chip["programming"]
        and (mfr, chip.get("part_number", "?")) in _NATIVE_0X0D_PAGE_SIZE_IDENTITIES
    ]
    at_128 = [row for row in native if row[2] == 128]
    at_64 = [row for row in native if row[2] == 64]
    assert {(m, p) for m, p, _v in at_128} == _NATIVE_0X0D_PAGE_SIZE_IDENTITIES_128
    assert {(m, p) for m, p, _v in at_64} == _NATIVE_0X0D_PAGE_SIZE_IDENTITIES_64


def test_page_size_carriers_are_exactly_the_named_rows() -> None:
    """Leg 6 refuses an unnamed carrier; this leg also refuses a named row
    that lost its page_size."""
    db = _load_db(_DB_FILE)
    carriers = {
        (mfr, chip.get("part_number", "?"))
        for mfr, chip in _select_page_size_carriers(db)
    }
    assert carriers == _ALL_PROVENANCE_CORROBORATED_IDENTITIES, (
        "page_size carriers drifted from the named rows -- symmetric "
        f"difference: {carriers ^ _ALL_PROVENANCE_CORROBORATED_IDENTITIES}"
    )


# Leg 5: every emitted page_size is a power of two in [1, 512] -- exhaustive.


def test_every_page_size_is_a_power_of_two_in_range() -> None:
    db = _load_db(_DB_FILE)
    offenders = _range_offenders(db)
    assert not offenders, (
        f"every emitted page_size must be a power of two in [1, 512]; "
        f"offenders: {offenders}"
    )


def test_every_page_size_carrier_is_curated_or_native_0x0d() -> None:
    db = _load_db(_DB_FILE)
    offenders = _provenance_offenders(db)
    assert not offenders, (
        f"every page_size carrier must be one of the 18 named "
        f"upstream-native 0x0D rows, one of the 27 named upstream-native "
        f"0x05 rows or one of the 19 DIP24_2816 rows; offenders: {offenders}"
    )


# Leg 7: AT28C256 non-change (gh#21 part -- a PROMOTED row, upstream
# protocol_id 0x07; this phase cannot change its behaviour at all).


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


# Leg 8: support_status byte-unchanged across all 84 algorithm==13 rows.


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
    changed = {
        identity for identity in live_idx if live_idx[identity] != base_idx[identity]
    }
    # The nine DIP24_2816 rows left adapter-required on purpose. Each of them
    # must now be supported. The twelve 3V rows are now refused. No other row
    # may move.
    expected = _PROMOTED_FROM_ADAPTER_REQUIRED | _REFUSED_FOR_LOW_VCC
    assert changed == expected, (
        f"support_status must be byte-unchanged for every algorithm==13 row "
        f"except the nine promoted DIP24_2816 rows and the twelve 3V rows; "
        f"symmetric difference: {changed ^ expected}"
    )
    assert all(live_idx[i] == "protocol-not-implemented" for i in _REFUSED_FOR_LOW_VCC)
    assert all(live_idx[i] == "supported" for i in _PROMOTED_FROM_ADAPTER_REQUIRED)
    assert all(
        base_idx[i] == "adapter-required" for i in _PROMOTED_FROM_ADAPTER_REQUIRED
    )


# Leg 9: extra_chips.json back door -- no authored supplement record
# carries page_size.


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


# Leg 10: synthetic non-vacuity, range.


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


# Leg 11: synthetic non-vacuity, provenance.


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


def _select_0x05_chips(db: dict) -> list[tuple[str, dict]]:
    """Select every (manufacturer, chip) pair with programming.algorithm == 5."""
    selected = []
    for mfr, chips in db.items():
        for chip in chips:
            if chip["programming"]["algorithm"] == _ALGORITHM_0X05:
                selected.append((mfr, chip))
    return selected


def _old_capacity_derived_page_size(size_bytes: int) -> int:
    """The page-size derivation flash_5v_page_page_size() used to compute
    from a chip's capacity alone, before Phase 194 D-06 deleted it from the
    firmware. Written out here because this test is now the only place it
    survives -- without it, the no-regression half of D-08 would be an
    assertion rather than a measurement.
    """
    if size_bytes <= 65536:
        return 64
    if size_bytes <= 262144:
        return 128
    return 256


def test_all_27_algorithm_5_rows_carry_their_real_page_and_18_match_the_old_derivation() -> (
    None
):
    """D-08's host half, both halves in one leg.

    Half (a): for every one of the 27 upstream-native 0x05 rows, the
    emitted page_size equals that row's own infoic_page_size_raw -- the
    part's real page, taken from the row's own upstream provenance.

    Half (b): for exactly the 18 whose old capacity-bracket derivation was
    already correct, the emitted page_size also equals what that
    derivation produces. The other 9 differ, and each differs by exactly a
    factor of two, with the emitted (real) value the larger of the pair --
    the measured shape of gh#67.
    """
    db = _load_db(_DB_FILE)
    rows = _select_0x05_chips(db)
    assert len(rows) == 27, (
        f"expected exactly 27 rows with programming.algorithm == 5, found {len(rows)}"
    )

    missing_page_size = [
        f"{mfr}/{chip.get('part_number', '?')}"
        for mfr, chip in rows
        if not chip["programming"].get("page_size")
    ]
    assert not missing_page_size, (
        f"every algorithm-5 row must carry a non-empty programming.page_size; "
        f"offenders: {missing_page_size}"
    )

    not_real_page = [
        f"{mfr}/{chip.get('part_number', '?')}: "
        f"page_size={chip['programming']['page_size']} "
        f"infoic_page_size_raw={chip['programming']['infoic_page_size_raw']}"
        for mfr, chip in rows
        if chip["programming"]["page_size"]
        != chip["programming"]["infoic_page_size_raw"]
    ]
    assert not not_real_page, (
        f"every algorithm-5 row's emitted page_size must equal its own "
        f"infoic_page_size_raw (the part's real page); offenders: {not_real_page}"
    )

    same = [
        (mfr, chip)
        for mfr, chip in rows
        if chip["programming"]["page_size"]
        == _old_capacity_derived_page_size(chip["electrical"]["size_bytes"])
    ]
    diff = [
        (mfr, chip)
        for mfr, chip in rows
        if chip["programming"]["page_size"]
        != _old_capacity_derived_page_size(chip["electrical"]["size_bytes"])
    ]
    same_names = [f"{mfr}/{chip.get('part_number', '?')}" for mfr, chip in same]
    diff_names = [f"{mfr}/{chip.get('part_number', '?')}" for mfr, chip in diff]

    assert len(same) == 18, (
        f"expected exactly 18 of the 27 rows to match the old capacity-bracket "
        f"derivation (no-regression half of D-08), found {len(same)}: {same_names}"
    )
    assert len(diff) == 9, (
        f"expected exactly 9 of the 27 rows to differ from the old "
        f"capacity-bracket derivation (the corrected half of D-08), found "
        f"{len(diff)}: {diff_names}"
    )

    not_double = [
        f"{mfr}/{chip.get('part_number', '?')}: "
        f"page_size={chip['programming']['page_size']} "
        f"old_derived={_old_capacity_derived_page_size(chip['electrical']['size_bytes'])}"
        for mfr, chip in diff
        if chip["programming"]["page_size"]
        != 2 * _old_capacity_derived_page_size(chip["electrical"]["size_bytes"])
    ]
    assert not not_double, (
        f"every one of the 9 corrected rows must differ from the old "
        f"derivation by exactly a factor of two, with the emitted value the "
        f"larger of the pair; offenders: {not_double}"
    )
