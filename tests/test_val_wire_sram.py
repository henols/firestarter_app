"""
Tier-2 host wire round-trip tests for the SRAM family (HARN-01 / D-07).

Validates — WITHOUT a serial port — that the host wire dict for the SRAM
family's representative chip (sourced from tools/validation_matrix_spec.json)
carries an algorithm in {14, 39, 40, 41} (0x0E/0x27/0x28/0x29) and dispatches
to ``configure_sram`` — and critically NEVER to ``configure_eprom``.

The safety-critical assertion (T-71-SRAM-EPROM / BLOCKER-2):
  configure_eprom asserts the 12V VPP boost regulator on every write pulse.
  An SRAM chip is a 5V part — 12V on VPP is electrical destruction.
  The SRAM rep chip MUST dispatch to configure_sram, and MUST NOT dispatch
  to configure_eprom, regardless of any algorithm edge-case.

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

from check_dispatch import _SRAM_PROTOCOLS, dispatch  # noqa: E402

# ---------------------------------------------------------------------------
# Spec loading
# ---------------------------------------------------------------------------

_SPEC_PATH = Path(__file__).parent.parent / "tools" / "validation_matrix_spec.json"
_SPEC = json.loads(_SPEC_PATH.read_text(encoding="utf-8"))

_SRAM_FAMILY = next(f for f in _SPEC["families"] if f["id"] == "sram")
_REP_CHIP = _SRAM_FAMILY["rep_chip"]
_EXPECTED_PROTOCOLS = set(_SRAM_FAMILY["protocols"])  # {14, 39, 40, 41}
_EXPECTED_HANDLER = _SRAM_FAMILY["handler"]  # "configure_sram"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sram_rep_chip_sourced_from_spec() -> None:
    """Representative chip name comes from validation_matrix_spec.json."""
    assert _REP_CHIP, "rep_chip must be non-empty in the spec"
    assert isinstance(_REP_CHIP, str)


def test_sram_wire_dict_has_algorithm_field(make_comm, fake_serial) -> None:
    """Wire dict built via EpromDatabase.convert_to_programmer() carries algorithm key."""
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    assert "algorithm" in wire, "wire dict must have 'algorithm' key"


def test_sram_wire_dict_algorithm_in_sram_protocols(make_comm, fake_serial) -> None:
    """Wire dict algorithm is in {14, 39, 40, 41} (SRAM family protocols) for the rep chip."""
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    algo = wire.get("algorithm", 0)
    assert algo in _EXPECTED_PROTOCOLS, (
        f"wire algorithm {algo} for '{_REP_CHIP}' not in expected SRAM protocols "
        f"{_EXPECTED_PROTOCOLS}"
    )


def test_sram_wire_dict_dispatches_to_configure_sram(make_comm, fake_serial) -> None:
    """dispatch(algorithm, 0) returns 'configure_sram' for the SRAM rep chip; wire carries no `type` key (HOST-01)."""
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


def test_sram_wire_dict_never_dispatches_to_configure_eprom(
    make_comm, fake_serial
) -> None:
    """
    SAFETY-CRITICAL (T-71-SRAM-EPROM / BLOCKER-2):
    SRAM rep chip must NEVER reach configure_eprom.

    configure_eprom asserts the 12V VPP boost regulator on write — a 5V SRAM
    part would be electrically destroyed if routed here.
    """
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    assert "type" not in wire
    algo = wire.get("algorithm", 0)
    handler = dispatch(algo, 0)
    assert handler != "configure_eprom", (
        f"BLOCKER-2 SAFETY VIOLATION: SRAM rep chip '{_REP_CHIP}' "
        f"(algo={algo:#04x}) dispatches to configure_eprom — "
        f"12V VPP on a 5V SRAM part causes electrical destruction"
    )


def test_sram_algorithm_is_in_sram_protocols_set(make_comm, fake_serial) -> None:
    """Wire algorithm for the SRAM rep chip is in check_dispatch._SRAM_PROTOCOLS."""
    db = EpromDatabase()
    chip = db.get_eprom(_REP_CHIP)
    assert chip is not None, f"rep_chip '{_REP_CHIP}' not found in EpromDatabase"
    wire = db.convert_to_programmer(chip)
    algo = wire.get("algorithm", 0)
    assert algo in _SRAM_PROTOCOLS, (
        f"SRAM rep chip '{_REP_CHIP}' algorithm {algo:#04x} not in "
        f"check_dispatch._SRAM_PROTOCOLS {_SRAM_PROTOCOLS!r}"
    )
