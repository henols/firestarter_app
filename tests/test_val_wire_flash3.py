"""
Tier-2 host wire round-trip tests for the flash3 family (HARN-01 / D-07).

Validates — WITHOUT a serial port — that the host wire dict for the flash3
family's representative chip (sourced from tools/validation_matrix_spec.json)
carries algorithm == 6 (0x06 FLASH_AMD_ALT) and dispatches to
``configure_flash3`` through the production dispatch() logic.

Design (D-10 Don't-Hand-Roll):
  - Representative chip name is read from validation_matrix_spec.json.
  - Wire dict is built via EpromDatabase.convert_to_programmer().
  - Dispatch is via tools.check_dispatch.dispatch().
  - make_comm / fake_serial fixtures confirm the no-serial-port posture.
"""

import json
import sys
from pathlib import Path

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

_FLASH3_FAMILY = next(f for f in _SPEC["families"] if f["id"] == "flash3")
_REP_CHIP = _FLASH3_FAMILY["rep_chip"]
_EXPECTED_PROTOCOLS = set(_FLASH3_FAMILY["protocols"])  # {6}
_EXPECTED_HANDLER = _FLASH3_FAMILY["handler"]  # "configure_flash3"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_flash3_rep_chip_sourced_from_spec() -> None:
    """Representative chip name comes from validation_matrix_spec.json."""
    assert _REP_CHIP, "rep_chip must be non-empty in the spec"
    assert isinstance(_REP_CHIP, str)


def test_flash3_wire_dict_has_algorithm_field(make_comm, fake_serial) -> None:
    """Wire dict built via EpromDatabase.convert_to_programmer() carries algorithm key."""
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    assert "algorithm" in wire, "wire dict must have 'algorithm' key"


def test_flash3_wire_dict_algorithm_is_0x06(make_comm, fake_serial) -> None:
    """Wire dict algorithm == 6 (0x06 FLASH_AMD_ALT) for the flash3 rep chip."""
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    algo = wire.get("algorithm", 0)
    assert algo in _EXPECTED_PROTOCOLS, (
        f"wire algorithm {algo} for '{_REP_CHIP}' not in expected flash3 protocols "
        f"{_EXPECTED_PROTOCOLS}"
    )


def test_flash3_wire_dict_dispatches_to_configure_flash3(
    make_comm, fake_serial
) -> None:
    """dispatch(algorithm, type) returns 'configure_flash3' for the flash3 rep chip."""
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
