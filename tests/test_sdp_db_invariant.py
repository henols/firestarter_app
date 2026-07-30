"""
DB invariant for the AT28C SDP `0x0D` identity gate (Phase 116 TRACE-05).

Reads firestarter/data/chip_database.json directly (not through EpromDatabase),
so this measures the shipped data rather than the loader's interpretation.

Coverage:
  1. Real-DB count: exactly 84 chip_database.json entries have
     programming.algorithm == 13 (the 0x0D / EEPROM_PARALLEL dispatch bucket).
  2. Per-element invariant: every one of those 84 entries has
     programming.chip_id_check is False (identity comparison, not merely falsy).
  3. Companion fact: every one of those 84 entries has
     programming.chip_id_value == "0x00000000" -- the field that actually
     determines firmware behaviour (eeprom_28c.cpp's identity branch only
     fires when handle->chip_id > 0).
  4. Non-vacuous proof: a synthetic in-memory DB dict with one algorithm==13
     chip carrying chip_id_check: True IS flagged by the same shared helper
     the real test calls -- proves the invariant is capable of failing, not a
     vacuous always-pass check (RESEARCH F9's "hollow in one direction"
     warning).

This module intentionally carries NO FW_ABSENT-style skip marker: it reads
only the packaged chip_database.json, which is always present in host-only
CI. Keeping this concern in its own file (separate from
test_sdp_bus_config_drift.py's FW_ABSENT-marked tests) prevents that skip
marker from leaking in here and silently making TRACE-05 vacuous in CI.
"""

import json
from pathlib import Path

# Absolute path to the firestarter_app directory (independent of cwd)
_FA_DIR = Path(__file__).parent.parent
_DB_FILE = _FA_DIR / "firestarter" / "data" / "chip_database.json"

# Upstream protocol_id / firmware dispatch key for configure_eeprom28c (0x0D).
_ALGORITHM_0X0D = 13

# ---------------------------------------------------------------------------
# Shared helpers -- both the real-DB tests and the non-vacuity test call
# these, so the non-vacuity leg exercises the same code the real test does.
# ---------------------------------------------------------------------------


def _select_0x0d_chips(db: dict) -> list[tuple[str, dict]]:
    """Select every (manufacturer, chip) pair with programming.algorithm == 13.

    The DB shape is {manufacturer: [chip, ...]}, and the fields live in a
    nested "programming" object. A top-level scan on db (rather than this
    nested per-chip access) finds nothing and would make every downstream
    assertion pass vacuously.
    """
    selected = []
    for _mfr, chips in db.items():
        for chip in chips:
            if chip["programming"]["algorithm"] == _ALGORITHM_0X0D:
                selected.append((_mfr, chip))
    return selected


def _assert_chip_id_check_false(selected: list[tuple[str, dict]]) -> None:
    """Raise AssertionError naming every offending chip if any selected
    entry's programming.chip_id_check is not exactly False."""
    offenders = [
        f"{mfr}/{chip.get('part_number', '?')}"
        for mfr, chip in selected
        if chip["programming"]["chip_id_check"] is not False
    ]
    assert not offenders, (
        "TRACE-05: every algorithm==13 (0x0D) chip must carry "
        "chip_id_check: false -- the identity gate must be provably dead "
        f"across the whole 0x0D bucket. Offending chips: {offenders}"
    )


# ---------------------------------------------------------------------------
# Test 1: real-DB count
# ---------------------------------------------------------------------------


def test_exactly_84_algorithm_0x0d_entries() -> None:
    """TRACE-05 / CLOSE-01: exactly 84 chip_database.json entries have
    programming.algorithm == 13.

    A count change means a chip was added to or removed from the 0x0D
    bucket and every trace-coverage assumption in this milestone needs
    re-checking. This is also CLOSE-01's "84-chip count unchanged" fact,
    landed six phases early.
    """
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    selected = _select_0x0d_chips(db)
    assert len(selected) == 84, (
        "TRACE-05/CLOSE-01: expected exactly 84 chip_database.json entries "
        f"with programming.algorithm == 13, found {len(selected)}. A count "
        "change means a chip was added to or removed from the 0x0D bucket "
        "-- re-check every Phase 116+ trace-coverage assumption before "
        "proceeding."
    )


# ---------------------------------------------------------------------------
# Test 2: per-element chip_id_check invariant
# ---------------------------------------------------------------------------


def test_all_0x0d_chips_have_chip_id_check_false() -> None:
    """TRACE-05: every algorithm==13 entry has chip_id_check is False.

    Identity comparison (is False), not merely falsy -- a missing key or a
    None value must not silently satisfy this check.
    """
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    selected = _select_0x0d_chips(db)
    _assert_chip_id_check_false(selected)


# ---------------------------------------------------------------------------
# Test 3: companion fact -- the field that actually gates firmware behaviour
# ---------------------------------------------------------------------------


def test_all_0x0d_chips_have_chip_id_value_zero_sentinel() -> None:
    """Pin the companion fact that makes the identity gate provably dead
    rather than merely disabled.

    Firmware (eeprom_28c.cpp:eeprom28c_write_init) only enters the identity
    branch when handle->chip_id > 0; chip_id_check: false alone doesn't
    prove that condition is unreachable for the 0x0D bucket -- this pins the
    zero sentinel that makes it so.
    """
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    selected = _select_0x0d_chips(db)
    offenders = [
        f"{mfr}/{chip.get('part_number', '?')}"
        for mfr, chip in selected
        if chip["programming"]["chip_id_value"] != "0x00000000"
    ]
    assert not offenders, (
        "TRACE-05: every algorithm==13 (0x0D) chip must carry "
        "chip_id_value: '0x00000000' -- firmware only skips the identity "
        "branch when handle->chip_id > 0 (eeprom_28c.cpp:eeprom28c_write_init). "
        f"Offending chips: {offenders}"
    )


# ---------------------------------------------------------------------------
# Test 4: non-vacuity proof
# ---------------------------------------------------------------------------


def test_synthetic_chip_id_check_true_is_flagged_non_vacuous() -> None:
    """Non-vacuity proof: a synthetic algorithm==13 chip with
    chip_id_check: True MUST make the shared helper raise.

    Proves the invariant gate is capable of failing -- not a vacuous
    always-pass check (RESEARCH F9's "hollow in one direction" warning).
    Exercises the exact same _select_0x0d_chips / _assert_chip_id_check_false
    helpers the real-DB test above calls, not a parallel reimplementation.
    """
    synthetic_db = {
        "SYNTHETIC_MFR": [
            {
                "part_number": "SYNTHETIC_0x0D_VIOLATION",
                "programming": {
                    "algorithm": 13,
                    "chip_id_check": True,
                    "chip_id_value": "0x00000000",
                },
            }
        ]
    }
    selected = _select_0x0d_chips(synthetic_db)
    assert len(selected) == 1, "Synthetic fixture setup error: expected 1 selected chip"

    try:
        _assert_chip_id_check_false(selected)
    except AssertionError:
        pass
    else:
        raise AssertionError(
            "Non-vacuity failure: the shared helper did not raise on a "
            "synthetic chip_id_check: True row -- the TRACE-05 invariant "
            "gate is vacuous."
        )
