"""
Tier-2 host wire round-trip tests for the EEPROM28c family (HARN-01 / D-07).

Validates — WITHOUT a serial port — that the host wire dict for the EEPROM28c
family's representative chip (sourced from tools/validation_matrix_spec.json)
carries algorithm == 13 (0x0D EEPROM_POLL) and dispatches to
``configure_eeprom28c`` through the production dispatch() logic.

Design (D-10 Don't-Hand-Roll):
  - Representative chip name is read from validation_matrix_spec.json.
  - Wire dict is built via EpromDatabase.convert_to_programmer().
  - Dispatch is via tools.check_dispatch.dispatch().
  - make_comm / fake_serial fixtures confirm the no-serial-port posture.
  - Phase 153 / ERASE-03 (D-153-04): the wire contract also pins the
    restored FLAG_CAN_ERASE capability bit, and pins that it is the ONLY
    flag the database composes -- no skip bit and nothing sector-related,
    since the 0x0D erase is device-global by construction.

Deliberately silent on: operation-time skip flags (FLAG_SKIP_ERASE,
FLAG_SKIP_BLANK_CHECK, FLAG_SKIP_SDP_UNLOCK) and sector addressing -- those
are composed per-invocation by the CLI, not by the database, and never
appear in a bare EpromDatabase.convert_to_programmer() wire dict.
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

_EEPROM28C_FAMILY = next(f for f in _SPEC["families"] if f["id"] == "eeprom28c")
_REP_CHIP = _EEPROM28C_FAMILY["rep_chip"]
_EXPECTED_PROTOCOLS = set(_EEPROM28C_FAMILY["protocols"])  # {13}
_EXPECTED_HANDLER = _EEPROM28C_FAMILY["handler"]  # "configure_eeprom28c"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_eeprom28c_rep_chip_sourced_from_spec() -> None:
    """Representative chip name comes from validation_matrix_spec.json."""
    assert _REP_CHIP, "rep_chip must be non-empty in the spec"
    assert isinstance(_REP_CHIP, str)


def test_eeprom28c_wire_dict_has_algorithm_field(make_comm, fake_serial) -> None:
    """Wire dict built via EpromDatabase.convert_to_programmer() carries algorithm key."""
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    assert "algorithm" in wire, "wire dict must have 'algorithm' key"


def test_eeprom28c_wire_dict_algorithm_is_0x0d(make_comm, fake_serial) -> None:
    """Wire dict algorithm == 13 (0x0D EEPROM_POLL) for the EEPROM28c rep chip."""
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    algo = wire.get("algorithm", 0)
    assert algo in _EXPECTED_PROTOCOLS, (
        f"wire algorithm {algo} for '{_REP_CHIP}' not in expected EEPROM28c protocols "
        f"{_EXPECTED_PROTOCOLS}"
    )


def test_eeprom28c_wire_dict_dispatches_to_configure_eeprom28c(
    make_comm, fake_serial
) -> None:
    """dispatch(algorithm, 0) returns 'configure_eeprom28c' for the EEPROM28c rep chip; wire carries no `type` key (HOST-01)."""
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


def test_eeprom28c_wire_dict_carries_flag_can_erase(make_comm, fake_serial) -> None:
    """Phase 153 / ERASE-03: the EEPROM28c family's own wire contract records
    the restored erase capability. `configure_eeprom28c` now implements the
    AN-0544B software chip erase and `CMD_ERASE` is wired to it, so the wire
    dict for the family's representative chip must carry FLAG_CAN_ERASE --
    this is the family's positive proof that a future clearing of the bit
    (e.g. a regression in database.py's algorithm-13 exclusion) fails here,
    not just at the conversion-layer unit test.
    """
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    assert wire["flags"] & FLAG_CAN_ERASE, (
        f"wire flags {wire['flags']:#04x} for '{_REP_CHIP}' must carry "
        f"FLAG_CAN_ERASE ({FLAG_CAN_ERASE:#04x})"
    )


def test_eeprom28c_wire_dict_carries_no_skip_or_sector_flags(
    make_comm, fake_serial
) -> None:
    """Per D-153-04: the 0x0D erase is a device-global chip erase by
    construction (the AN-0544B sequence erases the whole part) and it
    ignores `erase --sector-address`, which exists for the 0x06
    sector-erase protocol and has no meaning on a chip-erase-only device.
    Nothing sector-related belongs in this family's wire contract, so the
    wire dict's `flags` value must carry ONLY FLAG_CAN_ERASE and none of the
    operation-time skip bits -- those are composed per-invocation by the
    CLI, not by the database.
    """
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    assert wire["flags"] == FLAG_CAN_ERASE, (
        f"wire flags {wire['flags']:#04x} for '{_REP_CHIP}' must carry "
        f"ONLY FLAG_CAN_ERASE ({FLAG_CAN_ERASE:#04x}), no skip or sector bits"
    )
