"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 39 Wave 1 — chip_resolver unit tests (DATA-01).
Phase 66 Plan 05 — runtime-boundary tests for the support_status guard (D-12).

Tests use EpromDatabase(skip_local_override=True) to pin against the packaged
chip_database.json only, ignoring ~/.firestarter/database.json.

MANDATORY: every data-asserting test constructs
EpromDatabase(skip_local_override=True). Bare EpromDatabase() in tests that
assert specific chip data is forbidden — it would merge
~/.firestarter/database.json if present, causing CI/bench divergence.
"""

from unittest.mock import patch

import pytest

from firestarter.chip_resolver import resolve_chip
from firestarter.database import EpromDatabase
from firestarter.exceptions import ChipNotFoundError, ChipNotImplementedError


@pytest.fixture
def db():
    """Database pinned to the packaged chip_database.json (no local override)."""
    return EpromDatabase(skip_local_override=True)


def test_resolve_chip_hit_returns_dict(db):
    """A known chip resolves to its programmer-config dict (W27C512 = 64KB)."""
    result = resolve_chip("W27C512", db=db)
    assert result["memory-size"] == 65536


def test_resolve_chip_hit_has_required_programmer_keys(db):
    """The resolved dict carries the keys the firmware command builders expect."""
    result = resolve_chip("W27C512", db=db)
    for key in ("memory-size", "type", "algorithm", "pin-count", "vpp_mv", "flags"):
        assert key in result, f"Missing required key: {key}"


def test_resolve_chip_miss_raises(db):
    """An unknown chip name raises ChipNotFoundError."""
    with pytest.raises(ChipNotFoundError):
        resolve_chip("NOTACHIP_XYZ_DOESNOTEXIST", db=db)


def test_resolve_chip_conversion_correctness(db):
    """resolve_chip is the round-trip identity of get_eprom + convert_to_programmer."""
    expected = db.convert_to_programmer(db.get_eprom("W27C512"))
    assert resolve_chip("W27C512", db=db) == expected


# ---------------------------------------------------------------------------
# Phase 66 Plan 05 — runtime-boundary tests (D-12, T-66-01)
# resolve_chip must refuse non-supported chips (support_status != "supported")
# BEFORE any wire dict is built or serial byte emitted.
# ---------------------------------------------------------------------------


def test_resolve_chip_vpp_exceeds_max_raises_not_implemented(db):
    """M2716 (vpp-exceeds-max UV-EPROM, 25V VPP) must raise ChipNotImplementedError.

    This is the exact 12V-VPP hardware-damage path closed by D-12: the host guard
    fires before convert_to_programmer builds any wire dict, so configure_eprom is
    never reached on the firmware side.
    """
    with pytest.raises(ChipNotImplementedError):
        resolve_chip("M2716", db=db)


def test_resolve_chip_adapter_required_raises_not_implemented(db):
    """AT28C04 (adapter-required 24-pin EEPROM) must raise ChipNotImplementedError.

    Proves the guard is driven by support_status, not the incidental etype string:
    AT28C04 is Flash/EEPROM (etype-based mem_type=2, no configure_eprom) yet the host
    still refuses because support_status='adapter-required'. The guard is universal.
    """
    with pytest.raises(ChipNotImplementedError):
        resolve_chip("AT28C04", db=db)


def test_resolve_chip_supported_still_resolves(db):
    """W27C512 (supported UV-EPROM) must still resolve to a programmer dict (no regression)."""
    result = resolve_chip("W27C512", db=db)
    assert isinstance(result, dict)
    assert result.get("memory-size", 0) > 0


def test_resolve_chip_not_found_still_raises_chip_not_found(db):
    """A genuinely missing chip name still raises ChipNotFoundError (not ChipNotImplementedError).

    not-found must take precedence over the support_status guard: an absent chip
    cannot have a support_status, so the guard must never mask a genuine miss.
    """
    with pytest.raises(ChipNotFoundError):
        resolve_chip("NOTACHIP_XYZ_DOESNOTEXIST", db=db)


def test_resolve_chip_guard_fires_before_convert_to_programmer(db):
    """No serial bytes (wire dict) are produced when resolve_chip raises for a non-supported chip.

    Patches db.convert_to_programmer to detect if it is called.  If the support_status
    guard fires correctly (BEFORE convert_to_programmer), the mock must never be called
    when ChipNotImplementedError is raised.
    """
    with patch.object(db, "convert_to_programmer") as mock_convert:
        with pytest.raises(ChipNotImplementedError):
            resolve_chip("M2716", db=db)
        mock_convert.assert_not_called()
