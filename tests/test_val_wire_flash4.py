"""
Tier-2 host wire round-trip tests for the flash4 family (HARN-01 / D-07).

Validates — WITHOUT a serial port — that the host wire dict for the flash4
family's representative chip (sourced from tools/validation_matrix_spec.json)
carries an algorithm in {5, 53, 57} (0x05/0x35/0x39 FLASH_AMD_STD variants)
and dispatches to ``configure_flash4`` through the production dispatch() logic.

FIX-01a / T-93-CANERASE assertions (Phase 94 Plan 01):
  - flash4 (protocol 0x05) chips must NOT carry FLAG_CAN_ERASE (0x02) in wire flags.
    flash4 auto-erases per page; the separate 12V bulk erase is never needed.
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

from firestarter.constants import FLAG_CAN_ERASE
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

_FLASH4_FAMILY = next(f for f in _SPEC["families"] if f["id"] == "flash4")
_REP_CHIP = _FLASH4_FAMILY["rep_chip"]
_EXPECTED_PROTOCOLS = set(_FLASH4_FAMILY["protocols"])  # {5, 53, 57}
_EXPECTED_HANDLER = _FLASH4_FAMILY["handler"]  # "configure_flash4"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_flash4_rep_chip_sourced_from_spec() -> None:
    """Representative chip name comes from validation_matrix_spec.json."""
    assert _REP_CHIP, "rep_chip must be non-empty in the spec"
    assert isinstance(_REP_CHIP, str)


def test_flash4_wire_dict_has_algorithm_field(make_comm, fake_serial) -> None:
    """Wire dict built via EpromDatabase.convert_to_programmer() carries algorithm key."""
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    assert "algorithm" in wire, "wire dict must have 'algorithm' key"


def test_flash4_wire_dict_algorithm_in_family_protocols(make_comm, fake_serial) -> None:
    """Wire dict algorithm is in {5, 53, 57} (flash4 family protocols) for the rep chip."""
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    algo = wire.get("algorithm", 0)
    assert algo in _EXPECTED_PROTOCOLS, (
        f"wire algorithm {algo} for '{_REP_CHIP}' not in expected flash4 protocols "
        f"{_EXPECTED_PROTOCOLS}"
    )


def test_flash4_wire_dict_dispatches_to_configure_flash4(
    make_comm, fake_serial
) -> None:
    """dispatch(algorithm, type) returns 'configure_flash4' for the flash4 rep chip."""
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    algo = wire.get("algorithm", 0)
    mem_type = wire.get("type", 0)
    handler = dispatch(algo, mem_type)
    assert handler == _EXPECTED_HANDLER, (
        f"dispatch({algo:#04x}, {mem_type}) -> '{handler}', "
        f"expected '{_EXPECTED_HANDLER}' for '{_REP_CHIP}'"
    )


# ---------------------------------------------------------------------------
# FIX-01a / T-93-CANERASE assertions (Phase 94 Plan 01)
# ---------------------------------------------------------------------------


def test_flash4_rep_chip_no_flag_can_erase(make_comm, fake_serial) -> None:
    """FIX-01a: flash4 (protocol 0x05) rep chip wire flags must NOT carry FLAG_CAN_ERASE.

    flash4 auto-erases per page during the page-write; the separate 12V bulk erase
    is never needed and routes firmware flash4_erase_execute, asserting
    CTRL_VPP_REGULATOR_ENABLE on a 5V-only chip (T-93-CANERASE / SAFE-01 Item 2).
    """
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    flags = wire.get("flags", 0xFF)
    assert (flags & FLAG_CAN_ERASE) == 0, (
        f"FIX-01a: '{_REP_CHIP}' (protocol 0x05) wire flags {flags:#04x} must NOT "
        f"carry FLAG_CAN_ERASE ({FLAG_CAN_ERASE:#04x}); flash4 auto-erases per page "
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


def test_non_flash4_eeprom_still_has_flag_can_erase() -> None:
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


def test_flash4_eeprom_type_chip_no_flag_can_erase() -> None:
    """FIX-01a: flash4 chip with electrical-type Flash/EEPROM and protocol-id 5 yields flags==0x00.

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
        f"must NOT carry FLAG_CAN_ERASE; flash4 auto-erases per page (T-93-CANERASE)"
    )
