"""
Phase 151 / LOCK-02: catalog-presence proof for MSG_DATA_PROTECTION_STATUS.

This id has no emit site yet (that lands in Plan 151-08) and no host decode
site (Plan 151-06+), so there is nothing else in the suite that would fail if
the catalog entry silently regressed. Precedent for committing a bare
presence assertion for an id with no emit site: test_val_wire_5v_page.py's
test_warn_fl4_boot_block_locked_in_catalog / test_err_fl4_boot_block_locked_section_6_6_text.

No leg here asserts the full text of any format string — a wording-only
catalog edit is legitimate (151-DESIGN.md / messages.h is codegen-generated
and ID-only) and must not turn this suite red.
"""

from firestarter.messages import (
    CATALOG,
    MSG_DATA_PROTECTION_STATUS,
    MSG_ERR_FL4_BOOT_BLOCK_LOCKED,
    MSG_WARN_FL4_BOOT_BLOCK_LOCKED,
    SEVERITY_DATA,
    SEVERITY_ERROR,
    SEVERITY_WARN,
)


def test_protection_status_id_is_0xe1() -> None:
    """MSG_DATA_PROTECTION_STATUS is importable and its value is pinned as a literal.

    The literal is written here rather than re-read from the catalog so a
    silent renumber (e.g. a future edit picking a different free DATA-band
    id) is caught by this test rather than only by whatever later plan
    hardcodes the wire value.
    """
    assert MSG_DATA_PROTECTION_STATUS == 0xE1, (
        f"MSG_DATA_PROTECTION_STATUS must be 0xE1 (the lowest free DATA-band id "
        f"measured at Plan 151-05 time), got {MSG_DATA_PROTECTION_STATUS:#04x}"
    )


def test_protection_status_in_catalog_with_data_severity() -> None:
    """The id is a real CATALOG member, and its severity is DATA (not ERROR/WARN).

    151-DESIGN.md's §1 rejected an ERROR-band id (0xBF is the band's only free
    slot) in favor of a DATA-band id delivered via the existing
    LOG_DATA_ID_BYTES macro -- this pins that outcome.
    """
    assert MSG_DATA_PROTECTION_STATUS in CATALOG, (
        "MSG_DATA_PROTECTION_STATUS must be present in CATALOG"
    )
    entry = CATALOG[MSG_DATA_PROTECTION_STATUS]
    assert entry.severity == SEVERITY_DATA, (
        f"MSG_DATA_PROTECTION_STATUS severity must be SEVERITY_DATA "
        f"({SEVERITY_DATA:#04x}), got {entry.severity:#04x}"
    )


def test_protection_status_param_shape_is_two_u8() -> None:
    """The declared parameter shape is exactly two u8 entries.

    151-DESIGN.md §1 fixes the two-byte payload: byte 0 the raw silicon byte,
    byte 1 the decode code. A future widening to three params would shift the
    host decode offsets, so the shape is pinned here rather than only at
    whatever plan lands the decode site.
    """
    param_types = [
        ptype for ptype, _render in CATALOG[MSG_DATA_PROTECTION_STATUS].params
    ]
    assert param_types == ["u8", "u8"], (
        f"MSG_DATA_PROTECTION_STATUS params must be exactly two u8 entries, "
        f"got {param_types!r}"
    )


def test_error_band_fully_spent_0xa0_through_0xbf() -> None:
    """The ERROR band (0xA0-0xBF) is now fully spent -- all 32 ids allocated.

    Replaces test_error_band_last_free_id_unspent (LOCK-02), which asserted
    that 0xBF -- the band's one-time last free id -- stayed unspent. A later
    phase deliberately spent 0xBF on a new ERROR message; that is exactly
    the event the old guard existed to catch, and it caught it. The scarce
    resource the old guard protected is now fully consumed, so this guard
    protects the fact of that exhaustion instead: the whole band is
    allocated, and any future plan needing a new ERROR-severity id must
    either extend the band past 0xBF or use a different severity band --
    a decision this test does not make and does not assume.
    """
    all_ids = set(CATALOG.keys())
    error_band_ids = set(range(0xA0, 0xC0))
    missing = sorted(error_band_ids - all_ids)
    assert not missing, (
        "the ERROR band 0xA0-0xBF must be fully spent (32 of 32) -- found "
        f"free ids {missing!r}"
    )
    wrong_severity = sorted(
        i for i in error_band_ids if CATALOG[i].severity != SEVERITY_ERROR
    )
    assert not wrong_severity, (
        "every id in 0xA0-0xBF must carry SEVERITY_ERROR -- found non-ERROR "
        f"severity at {[hex(i) for i in wrong_severity]!r}"
    )


def test_boot_block_vocabulary_undisturbed() -> None:
    """The two pre-existing boot-block ids are present, unmoved, with recorded severities.

    151-DESIGN.md's objective names MSG_WARN_FL4_BOOT_BLOCK_LOCKED (0x85) and
    MSG_ERR_FL4_BOOT_BLOCK_LOCKED (0xBC) as the preferred vocabulary for any
    boot-block outcome, emitted by nothing today -- Plan 151-08 may give them
    an emit site without minting anything. This proves this plan's catalog
    edit did not disturb that vocabulary.
    """
    assert MSG_WARN_FL4_BOOT_BLOCK_LOCKED == 0x85, (
        f"MSG_WARN_FL4_BOOT_BLOCK_LOCKED must stay 0x85, "
        f"got {MSG_WARN_FL4_BOOT_BLOCK_LOCKED:#04x}"
    )
    assert MSG_ERR_FL4_BOOT_BLOCK_LOCKED == 0xBC, (
        f"MSG_ERR_FL4_BOOT_BLOCK_LOCKED must stay 0xBC, "
        f"got {MSG_ERR_FL4_BOOT_BLOCK_LOCKED:#04x}"
    )
    assert CATALOG[MSG_WARN_FL4_BOOT_BLOCK_LOCKED].severity == SEVERITY_WARN, (
        "MSG_WARN_FL4_BOOT_BLOCK_LOCKED severity must stay SEVERITY_WARN"
    )
    assert CATALOG[MSG_ERR_FL4_BOOT_BLOCK_LOCKED].severity == SEVERITY_ERROR, (
        "MSG_ERR_FL4_BOOT_BLOCK_LOCKED severity must stay SEVERITY_ERROR"
    )
