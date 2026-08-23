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
    for key in ("memory-size", "algorithm", "pin-count", "vpp_mv", "flags"):
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
# runtime-boundary tests (D-12, T-66-01)
# resolve_chip must refuse non-supported chips (support_status != "supported")
# BEFORE any wire dict is built or serial byte emitted.
# ---------------------------------------------------------------------------


def test_resolve_chip_non_supported_raises_not_implemented(db):
    """X88C64P (protocol-not-implemented, 0x34) must raise ChipNotImplementedError.

    The host guard fires before convert_to_programmer builds any wire dict, so the
    unimplemented firmware handler is never reached. Re-anchored from M2716 in
    Phase 79: M2716 graduated to 'supported' (NMOS-02), so the 'vpp-exceeds-max'
    category is now empty — X88C64P is the still-non-supported exemplar.
    """
    with pytest.raises(ChipNotImplementedError):
        resolve_chip("X88C64P", db=db)


def test_resolve_chip_nmos_graduated_resolves(db):
    """Phase 79 (NMOS-02/03 host path): M2716 resolves with NO exception after the
    25V ceiling raise graduated it to 'supported'.

    Non-vacuous positive control for the graduation — this test FAILS on the
    pre-Phase-79 DB (M2716 was 'vpp-exceeds-max' and raised ChipNotImplementedError).
    Graduation is best-effort per CONTEXT D-07.
    """
    result = resolve_chip("M2716", db=db)
    assert isinstance(result, dict)
    assert result.get("memory-size", 0) > 0
    # 25V chips dispatch to configure_eprom via protocol 0x0B (EPROM_LEGACY).
    assert result.get("vpp_mv") == 25000
    assert result.get("algorithm") == 11


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
            resolve_chip("X88C64P", db=db)
        mock_convert.assert_not_called()


# ---------------------------------------------------------------------------
# HOST-04 algorithm-presence guard (D-01/D-02, SC#4).
# A support_status=="supported" entry whose programming.algorithm is absent
# or 0 must still be refused, BEFORE any wire dict is built or serial byte
# emitted. Mirrors the firmware's protocol==0 -> 0xBB fail-close.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "broken_programming",
    [
        {},  # algorithm key absent entirely
        {"algorithm": 0},  # algorithm present but unusable (0)
    ],
)
def test_resolve_chip_refuses_missing_algorithm_before_convert_to_programmer(
    db, broken_programming
):
    """A supported-but-algorithm-less entry raises ChipNotImplementedError with
    convert_to_programmer never called (no wire dict, no serial byte).

    Constructs a deliberately-broken synthetic raw record (support_status=
    "supported" yet programming.algorithm missing/0) via patch.object on
    get_eprom_config, so the NEW algorithm guard fires -- not the pre-existing
    support_status guard.
    """
    broken_raw_config = {
        "support_status": "supported",
        "programming": broken_programming,
    }
    with patch.object(
        db, "get_eprom_config", return_value=(broken_raw_config, "TESTMFG")
    ):
        with patch.object(db, "convert_to_programmer") as mock_convert:
            with pytest.raises(ChipNotImplementedError):
                resolve_chip("BROKEN_OVERRIDE_CHIP", db=db)
            mock_convert.assert_not_called()
