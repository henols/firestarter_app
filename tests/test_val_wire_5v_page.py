"""
Tier-2 host wire round-trip tests for the 5v_page family (HARN-01 / D-07).

Validates — WITHOUT a serial port — that the host wire dict for the 5v_page
family's representative chip (sourced from tools/validation_matrix_spec.json)
carries an algorithm in {5, 53, 57} (0x05/0x35/0x39 FLASH_AMD_STD variants)
and dispatches to ``configure_flash_5v_page`` through the production dispatch() logic.

FIX-01a / T-93-CANERASE assertions (Phase 94 Plan 01):
  - 5v_page (protocol 0x05) chips must NOT carry FLAG_CAN_ERASE (0x02) in wire flags.
    5v_page auto-erases per page; the separate 12V bulk erase is never needed.
  - Non-0x05 EEPROM chips (e.g. W27C512, protocol 0x07) MUST still carry
    FLAG_CAN_ERASE (behaviour preserved).

Design (D-10 Don't-Hand-Roll):
  - Representative chip name is read from validation_matrix_spec.json.
  - Wire dict is built via EpromDatabase.convert_to_programmer().
  - Dispatch is via tools.check_dispatch.dispatch().
  - make_comm / fake_serial fixtures confirm the no-serial-port posture.
"""

import json
import sys
from pathlib import Path

from firestarter.constants import FLAG_CAN_ERASE, JSON_KEY_PAGE_SIZE
from firestarter.database import EpromDatabase

# Add tools directory so check_dispatch is importable
_TOOLS_DIR = Path(__file__).parent.parent / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from check_dispatch import dispatch  # noqa: E402

# ---------------------------------------------------------------------------
# Spec loading
# ---------------------------------------------------------------------------

_SPEC_PATH = Path(__file__).parent.parent / "tools" / "validation_matrix_spec.json"
_SPEC = json.loads(_SPEC_PATH.read_text(encoding="utf-8"))

_5V_PAGE_FAMILY = next(f for f in _SPEC["families"] if f["id"] == "5v_page")
_REP_CHIP = _5V_PAGE_FAMILY["rep_chip"]
_EXPECTED_PROTOCOLS = set(_5V_PAGE_FAMILY["protocols"])  # {5, 53, 57}
_EXPECTED_HANDLER = _5V_PAGE_FAMILY["handler"]  # "configure_flash_5v_page"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_5v_page_rep_chip_sourced_from_spec() -> None:
    """Representative chip name comes from validation_matrix_spec.json."""
    assert _REP_CHIP, "rep_chip must be non-empty in the spec"
    assert isinstance(_REP_CHIP, str)


def test_5v_page_wire_dict_has_algorithm_field(make_comm, fake_serial) -> None:
    """Wire dict built via EpromDatabase.convert_to_programmer() carries algorithm key."""
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    assert "algorithm" in wire, "wire dict must have 'algorithm' key"


def test_5v_page_wire_dict_algorithm_in_family_protocols(
    make_comm, fake_serial
) -> None:
    """Wire dict algorithm is in {5, 53, 57} (5v_page family protocols) for the rep chip."""
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    algo = wire.get("algorithm", 0)
    assert algo in _EXPECTED_PROTOCOLS, (
        f"wire algorithm {algo} for '{_REP_CHIP}' not in expected 5v_page protocols "
        f"{_EXPECTED_PROTOCOLS}"
    )


def test_5v_page_wire_dict_dispatches_to_configure_flash_5v_page(
    make_comm, fake_serial
) -> None:
    """dispatch(algorithm, 0) returns 'configure_flash_5v_page' for the 5v_page rep chip; wire carries no `type` key (HOST-01)."""
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    assert "type" not in wire
    algo = wire.get("algorithm", 0)
    handler = dispatch(algo, 0)
    assert handler == _EXPECTED_HANDLER, (
        f"dispatch({algo:#04x}, 0) -> '{handler}', "
        f"expected '{_EXPECTED_HANDLER}' for '{_REP_CHIP}'"
    )


# ---------------------------------------------------------------------------
# FIX-01a / T-93-CANERASE assertions (Phase 94 Plan 01)
# ---------------------------------------------------------------------------


def test_5v_page_rep_chip_no_flag_can_erase(make_comm, fake_serial) -> None:
    """FIX-01a: 5v_page (protocol 0x05) rep chip wire flags must NOT carry FLAG_CAN_ERASE.

    5v_page auto-erases per page during the page-write; the separate 12V bulk erase
    is never needed and routes firmware flash_5v_page_erase_execute, asserting
    CTRL_VPP_REGULATOR_ENABLE on a 5V-only chip (T-93-CANERASE / SAFE-01 Item 2).
    """
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    flags = wire.get("flags", 0xFF)
    assert (flags & FLAG_CAN_ERASE) == 0, (
        f"FIX-01a: '{_REP_CHIP}' (protocol 0x05) wire flags {flags:#04x} must NOT "
        f"carry FLAG_CAN_ERASE ({FLAG_CAN_ERASE:#04x}); 5v_page auto-erases per page "
        f"and needs no 12V bulk erase (T-93-CANERASE)"
    )


def test_w29c040_no_flag_can_erase() -> None:
    """FIX-01a: W29C040 (protocol 0x05) wire flags must be 0x00 — no FLAG_CAN_ERASE.

    Direct assertion on the chip that triggered the T-93-CANERASE discovery.
    """
    db = EpromDatabase()
    chip = db.get_eprom("W29C040")
    assert chip is not None, "W29C040 must be present in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    flags = wire.get("flags", 0xFF)
    assert flags == 0x00, (
        f"FIX-01a: W29C040 wire flags must be 0x00, got {flags:#04x}; "
        f"FLAG_CAN_ERASE must not be set for protocol 0x05 chips (T-93-CANERASE)"
    )


def test_non_5v_page_eeprom_still_has_flag_can_erase() -> None:
    """FIX-01a: non-0x05 EEPROM (W27C512, protocol 0x07) must STILL carry FLAG_CAN_ERASE.

    Confirms the fix is scoped to algorithm==5 only and does not disturb the
    0x07 EE-EPROM path where FLAG_CAN_ERASE is correct and required.
    """
    db = EpromDatabase()
    chip = db.get_eprom("W27C512")
    assert chip is not None, "W27C512 must be present in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    algo = wire.get("algorithm", 0)
    assert algo != 5, f"W27C512 must not be protocol 0x05; got {algo}"
    flags = wire.get("flags", 0)
    assert (flags & FLAG_CAN_ERASE) != 0, (
        f"FIX-01a regression: W27C512 (protocol {algo:#04x}) wire flags {flags:#04x} "
        f"must carry FLAG_CAN_ERASE ({FLAG_CAN_ERASE:#04x}); non-0x05 EEPROM behaviour "
        f"must be unchanged"
    )


def test_5v_page_eeprom_type_chip_no_flag_can_erase() -> None:
    """FIX-01a: 5v_page chip with electrical-type Flash/EEPROM and protocol-id 5 yields flags==0x00.

    Verifies that a Flash/EEPROM-typed chip on protocol 0x05 does not get
    FLAG_CAN_ERASE even though the electrical-type would normally trigger it.
    """
    db = EpromDatabase()
    chip = db.get_eprom("W29C040")
    assert chip is not None, "W29C040 must be present in EpromDatabase"
    # Confirm the chip has the Flash/EEPROM electrical type (the old hazard path)
    elec_type = chip.get("electrical-type", "")
    assert elec_type in ("EEPROM", "Flash/EEPROM"), (
        f"W29C040 electrical-type expected EEPROM or Flash/EEPROM, got '{elec_type}'"
    )
    wire = db.convert_to_programmer(chip)
    assert wire.get("algorithm") == 5, "W29C040 algorithm must be 5"
    flags = wire.get("flags", 0xFF)
    assert (flags & FLAG_CAN_ERASE) == 0, (
        f"FIX-01a: W29C040 (Flash/EEPROM type, protocol 0x05) wire flags {flags:#04x} "
        f"must NOT carry FLAG_CAN_ERASE; 5v_page auto-erases per page (T-93-CANERASE)"
    )


# ---------------------------------------------------------------------------
# PGSZ-01/03 assertions (Phase 94 Plan 02 — CR-01 wire field)
# ---------------------------------------------------------------------------


def test_w29c040_wire_dict_carries_page_size_256() -> None:
    """PGSZ-01/03: W29C040 wire dict must carry page-size == 256.

    The DB carries page_size=256 [CITED: W29C040.pdf §6.2], and
    convert_to_programmer() emits the JSON_KEY_PAGE_SIZE wire key when present.
    """
    db = EpromDatabase()
    chip = db.get_eprom("W29C040")
    assert chip is not None, "W29C040 must be present in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    assert JSON_KEY_PAGE_SIZE in wire, (
        f"PGSZ-03: W29C040 wire dict must carry '{JSON_KEY_PAGE_SIZE}' key; "
        f"got keys: {list(wire.keys())}"
    )
    assert wire[JSON_KEY_PAGE_SIZE] == 256, (
        f"PGSZ-01: W29C040 page-size must be 256 (datasheet §6.2), "
        f"got {wire[JSON_KEY_PAGE_SIZE]!r}"
    )


def test_w29c020_wire_dict_carries_page_size_128() -> None:
    """PGSZ-01/03: W29C020 wire dict must carry page-size == 128.

    The DB carries page_size=128 [CITED: W29C020.pdf §6.2], and
    convert_to_programmer() emits the JSON_KEY_PAGE_SIZE wire key when present.
    """
    db = EpromDatabase()
    chip = db.get_eprom("W29C020")
    assert chip is not None, "W29C020 must be present in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    assert JSON_KEY_PAGE_SIZE in wire, (
        f"PGSZ-03: W29C020 wire dict must carry '{JSON_KEY_PAGE_SIZE}' key; "
        f"got keys: {list(wire.keys())}"
    )
    assert wire[JSON_KEY_PAGE_SIZE] == 128, (
        f"PGSZ-01: W29C020 page-size must be 128 (datasheet §6.2), "
        f"got {wire[JSON_KEY_PAGE_SIZE]!r}"
    )


def test_heuristic_family_chip_omits_page_size() -> None:
    """PGSZ-01/03: a 5v_page chip NOT in _PAGE_SIZE_BY_PART must NOT carry page-size.

    Chips without a [CITED:] datasheet entry have no page_size in the DB;
    convert_to_programmer() must omit the key so the firmware uses its
    flash_5v_page_page_size(mem_size) heuristic fallback.

    AT29C010A (5v_page family, no in-repo datasheet citation) is the test chip.
    """
    db = EpromDatabase()
    chip = db.get_eprom("AT29C010A")
    assert chip is not None, "AT29C010A must be present in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    assert wire.get("algorithm") == 5, "AT29C010A algorithm must be 5 (5v_page)"
    assert JSON_KEY_PAGE_SIZE not in wire, (
        f"PGSZ-01: AT29C010A (no datasheet citation) must NOT carry '{JSON_KEY_PAGE_SIZE}'; "
        f"it should use the firmware heuristic fallback. Got wire keys: {list(wire.keys())}"
    )


# ---------------------------------------------------------------------------
# Proactive boot-block detect — host --force flag + message catalog
# ---------------------------------------------------------------------------


def test_write_force_flag_sets_flag_force_in_wire_flags() -> None:
    """Phase 95: --force on write sets FLAG_FORCE (0x01) in the operation flags.

    The proactive §6.6 boot-block detect uses FLAG_FORCE to decide whether to
    abort (no force → ERROR) or warn-and-proceed (force → WARNING). This test
    confirms that the CLI --force path reaches FLAG_FORCE on the wire so the
    firmware proactive check behaves correctly when the operator passes --force.

    Implemented via build_flags(force=True), which is the same path used by
    the Click write command's _build_op_flags(force=True) call.
    """
    from firestarter.constants import FLAG_FORCE
    from firestarter.eprom_operations import build_flags

    flags_with_force = build_flags(force=True)
    flags_without_force = build_flags(force=False)

    assert (flags_with_force & FLAG_FORCE) != 0, (
        f"build_flags(force=True) must set FLAG_FORCE ({FLAG_FORCE:#04x}) in the result; "
        f"got flags={flags_with_force:#04x}. The proactive boot-block detect uses FLAG_FORCE "
        f"to decide abort vs warn-and-proceed (Phase 95 / section 6.6 W29C040)"
    )
    assert (flags_without_force & FLAG_FORCE) == 0, (
        f"build_flags(force=False) must NOT set FLAG_FORCE ({FLAG_FORCE:#04x}); "
        f"got flags={flags_without_force:#04x}"
    )


def test_warn_fl4_boot_block_locked_in_catalog() -> None:
    """Phase 95: MSG_WARN_FL4_BOOT_BLOCK_LOCKED (0x85) exists in the host message catalog.

    The new warning message is emitted by flash_5v_page_write_init when FLAG_FORCE is
    set and the proactive §6.6 boot-block detect returns locked. The host must
    recognise and decode it as a WARN-severity message (not ERROR), display it
    via the standard warning path (logger.warning()), and continue rather than
    raising EpromOperationError.
    """
    from firestarter.messages import (
        CATALOG,
        MSG_WARN_FL4_BOOT_BLOCK_LOCKED,
        SEVERITY_WARN,
    )

    assert MSG_WARN_FL4_BOOT_BLOCK_LOCKED == 0x85, (
        f"MSG_WARN_FL4_BOOT_BLOCK_LOCKED must be 0x85, got {MSG_WARN_FL4_BOOT_BLOCK_LOCKED:#04x}"
    )
    assert MSG_WARN_FL4_BOOT_BLOCK_LOCKED in CATALOG, (
        "MSG_WARN_FL4_BOOT_BLOCK_LOCKED must be present in CATALOG"
    )
    entry = CATALOG[MSG_WARN_FL4_BOOT_BLOCK_LOCKED]
    assert entry.severity == SEVERITY_WARN, (
        f"MSG_WARN_FL4_BOOT_BLOCK_LOCKED severity must be SEVERITY_WARN ({SEVERITY_WARN:#04x}), "
        f"got {entry.severity:#04x} — host must treat this as a warning, not an error"
    )
    # Verify the fixed "section 6.6" text (no "ss6.6" mangling)
    assert "ss6.6" not in entry.format, (
        f"MSG_WARN_FL4_BOOT_BLOCK_LOCKED format must not contain 'ss6.6' mangling; "
        f"got: {entry.format!r}"
    )
    assert "section 6.6" in entry.format, (
        f"MSG_WARN_FL4_BOOT_BLOCK_LOCKED format must contain 'section 6.6'; "
        f"got: {entry.format!r}"
    )


def test_err_fl4_boot_block_locked_section_6_6_text() -> None:
    """Phase 95: MSG_ERR_FL4_BOOT_BLOCK_LOCKED (0xBC) format text fixed — no 'ss6.6'.

    The existing error message had a cosmetic bug: 'ss6.6' instead of 'section 6.6'.
    This test pins the corrected format so the fix cannot regress via codegen drift.
    """
    from firestarter.messages import CATALOG, MSG_ERR_FL4_BOOT_BLOCK_LOCKED

    assert MSG_ERR_FL4_BOOT_BLOCK_LOCKED == 0xBC, (
        f"MSG_ERR_FL4_BOOT_BLOCK_LOCKED must be 0xBC, got {MSG_ERR_FL4_BOOT_BLOCK_LOCKED:#04x}"
    )
    assert MSG_ERR_FL4_BOOT_BLOCK_LOCKED in CATALOG, (
        "MSG_ERR_FL4_BOOT_BLOCK_LOCKED must be present in CATALOG"
    )
    entry = CATALOG[MSG_ERR_FL4_BOOT_BLOCK_LOCKED]
    assert "ss6.6" not in entry.format, (
        f"MSG_ERR_FL4_BOOT_BLOCK_LOCKED format must not contain 'ss6.6' mangling "
        f"(cosmetic fix Phase 95); got: {entry.format!r}"
    )
    assert "section 6.6" in entry.format, (
        f"MSG_ERR_FL4_BOOT_BLOCK_LOCKED format must contain 'section 6.6'; "
        f"got: {entry.format!r}"
    )
