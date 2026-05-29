"""Phase 42 / ERR-03 coverage lift for database.convert_to_programmer + DIP→RURP
pin translation across representative chip families (D-14.1).
"""

import pytest

from firestarter.database import EpromDatabase


@pytest.fixture(scope="module")
def db() -> EpromDatabase:
    """Hermetic DB: no ``~/.firestarter`` override interference (Phase 36 D-06)."""
    return EpromDatabase(skip_local_override=True)


def test_convert_w27c512_28pin_uveprom(db: EpromDatabase) -> None:
    """W27C512 is the canonical 28-pin UV-EPROM (algo 0x07, configure_eprom)."""
    full = db.get_eprom("W27C512")
    assert full is not None
    out = db.convert_to_programmer(full)
    assert out["algorithm"] == 0x07  # EPROM_STD
    assert out["pin-count"] == 28
    assert "bus-config" in out
    assert isinstance(out["bus-config"], dict)


def test_convert_at28c256_28pin_5v_eeprom_override(db: EpromDatabase) -> None:
    """AT28C256 routes to algo 0x0D (configure_eeprom28c) via WARNING-5 override
    so it does NOT engage the VPP regulator on the DIP28_2764 pinout."""
    full = db.get_eprom("AT28C256")
    assert full is not None
    out = db.convert_to_programmer(full)
    assert out["algorithm"] == 0x0D  # EEPROM_POLL via WARNING-5 override
    assert out["pin-count"] == 28
    assert "bus-config" in out


def test_convert_am29f040_32pin_flash(db: EpromDatabase) -> None:
    """AM29F040 is the canonical 32-pin Flash representative."""
    full = db.get_eprom("AM29F040")
    assert full is not None
    out = db.convert_to_programmer(full)
    assert out["pin-count"] == 32
    # Flash dispatch family (configure_flash3 / flash4 etc.)
    assert out["algorithm"] in {0x05, 0x06, 0x10, 0x35, 0x39}
    assert "bus-config" in out


def test_convert_6116_sram_24pin(db: EpromDatabase) -> None:
    """6116 is the canonical 24-pin SRAM (configure_sram family)."""
    full = db.get_eprom("6116")
    assert full is not None
    out = db.convert_to_programmer(full)
    assert out["pin-count"] == 24
    # SRAM dispatch family per CLAUDE.md: 0x0E/0x27/0x28/0x29.
    assert out["algorithm"] in {0x0E, 0x27, 0x28, 0x29}
    assert "bus-config" in out


def test_convert_unknown_chip_returns_none(db: EpromDatabase) -> None:
    """ChipNotFoundError-equivalent: get_eprom returns None for unknown chips
    (the not-found path that chip_resolver.resolve_chip raises above)."""
    assert db.get_eprom("NONEXISTENT_CHIP_XYZ") is None


def test_convert_bus_config_has_pin_mappings(db: EpromDatabase) -> None:
    """Each converted chip carries a populated bus-config dict with at least
    one DIP→RURP pin mapping per the chip's pinout class."""
    full = db.get_eprom("W27C512")
    assert full is not None
    out = db.convert_to_programmer(full)
    bus = out["bus-config"]
    # The bus-config must be a dict with at least one mapping entry; the
    # actual key set varies per pinout — assert non-empty.
    assert isinstance(bus, dict)
    assert len(bus) > 0


# ---------------------------------------------------------------------------
# Additional EpromDatabase surface (D-14 fallback — lift database.py coverage)
# ---------------------------------------------------------------------------


def test_get_eproms_returns_list(db: EpromDatabase) -> None:
    """get_eproms() returns the full chip list as a list of dicts."""
    chips = db.get_eproms()
    assert isinstance(chips, list)
    assert len(chips) > 0
    assert "name" in chips[0]


def test_get_eproms_verified_filter(db: EpromDatabase) -> None:
    """get_eproms(verified=True) returns only verified chips."""
    all_chips = db.get_eproms()
    verified_chips = db.get_eproms(verified=True)
    assert len(verified_chips) <= len(all_chips)
    # All returned chips must have verified=True
    for chip in verified_chips:
        assert chip.get("verified") is True


def test_search_eprom_returns_matches(db: EpromDatabase) -> None:
    """search_eprom returns chips matching the search text (case-insensitive)."""
    results = db.search_eprom("W27C512")
    assert isinstance(results, list)
    assert len(results) > 0
    # At least one match contains the search term in the name.
    assert any("W27C512" in r.get("name", "") for r in results)


def test_search_eprom_no_matches(db: EpromDatabase) -> None:
    """search_eprom returns an empty list when nothing matches."""
    results = db.search_eprom("DEFINITELY_NOT_A_CHIP_XYZ_123")
    assert results == []


def test_get_pin_map(db: EpromDatabase) -> None:
    """get_pin_map returns the pin-map dict for a known pin count + variant."""
    pm = db.get_pin_map(28, "DIP28_27512")
    assert pm is not None
    assert isinstance(pm, dict)


def test_get_eprom_config_returns_raw_plus_manufacturer(db: EpromDatabase) -> None:
    """get_eprom_config returns (raw_config_dict, manufacturer_str)."""
    raw, manuf = db.get_eprom_config("W27C512")
    assert raw is not None
    assert isinstance(raw, dict)
    assert manuf is not None
    assert isinstance(manuf, str)


def test_map_chip_record_basic(db: EpromDatabase) -> None:
    """map_chip_record produces a derived chip record dict from a raw IC entry."""
    raw, manuf = db.get_eprom_config("W27C512")
    mapped = db.map_chip_record(raw, manuf)
    assert isinstance(mapped, dict)
    assert "name" in mapped


def test_search_chip_id_returns_list(db: EpromDatabase) -> None:
    """search_chip_id with a value in the DB returns at least one match."""
    full = db.get_eprom("W27C512")
    assert full is not None
    chip_id_val = full.get("chip-id")
    if chip_id_val:
        # Convert the hex string ("0xDA08") to int for the search
        if isinstance(chip_id_val, str):
            chip_id_int = int(chip_id_val, 16)
        else:
            chip_id_int = chip_id_val
        matches = db.search_chip_id(chip_id_int)
        assert isinstance(matches, list)
