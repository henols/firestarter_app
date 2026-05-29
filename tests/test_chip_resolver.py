"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 39 Wave 1 — chip_resolver unit tests (DATA-01).

Tests use EpromDatabase(skip_local_override=True) to pin against the packaged
chip_database.json only, ignoring ~/.firestarter/database.json.

MANDATORY: every data-asserting test constructs
EpromDatabase(skip_local_override=True). Bare EpromDatabase() in tests that
assert specific chip data is forbidden — it would merge
~/.firestarter/database.json if present, causing CI/bench divergence.
"""

import pytest

from firestarter.chip_resolver import resolve_chip
from firestarter.database import EpromDatabase
from firestarter.exceptions import ChipNotFoundError


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
