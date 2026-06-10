"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 36 Plan 04 — EpromDatabase unit tests (TEST-03).

Tests use EpromDatabase(skip_local_override=True) to pin against the
packaged chip_database.json only, ignoring ~/.firestarter/database.json.
This is the production change introduced in Phase 36 plan 36-01: the
singleton guard is removed and skip_local_override is added (D-06).

Covers three D-07 surfaces — no find_and_connect and no serial I/O:
  (1) get_eprom: known chip lookup, not-found path.
  (2) convert_to_programmer: key presence + memory-size equality.
  (3) DIP->RURP pin translation: bus config fields for known chip pinouts.

MANDATORY: every data-asserting test constructs
EpromDatabase(skip_local_override=True). Bare EpromDatabase() in tests
that assert specific chip data is forbidden — it would merge
~/.firestarter/database.json if present, causing CI/bench divergence
(RESEARCH Pitfall 4).
"""

import pytest  # noqa: F401

from firestarter.database import ROM_CE, ROM_OE, EpromDatabase, pin_conversions


class TestGetEprom:
    """Tests for EpromDatabase.get_eprom() — D-07 surface 1."""

    def test_get_eprom_w27c512_is_found(self):
        """W27C512 must be present in the packaged chip_database.json."""
        db = EpromDatabase(skip_local_override=True)
        eprom = db.get_eprom("W27C512")
        assert eprom is not None

    def test_get_eprom_w27c512_memory_size(self):
        """W27C512 is a 64KB device — memory-size must equal 65536."""
        db = EpromDatabase(skip_local_override=True)
        eprom = db.get_eprom("W27C512")
        assert eprom is not None
        assert eprom["memory-size"] == 65536  # 64KB

    def test_get_eprom_w27c512_pin_count(self):
        """W27C512 is a DIP-28 package."""
        db = EpromDatabase(skip_local_override=True)
        eprom = db.get_eprom("W27C512")
        assert eprom is not None
        assert eprom["pin-count"] == 28

    def test_get_eprom_w27c512_has_bus_config(self):
        """W27C512 has a known pinout and therefore must yield a bus-config."""
        db = EpromDatabase(skip_local_override=True)
        eprom = db.get_eprom("W27C512")
        assert eprom is not None
        assert "bus-config" in eprom

    def test_get_eprom_unknown_chip_returns_none(self):
        """Querying a chip that does not exist must return None."""
        db = EpromDatabase(skip_local_override=True)
        result = db.get_eprom("NOTACHIP_XYZ_DOESNOTEXIST_9999")
        assert result is None

    def test_get_eprom_24pin_chip_am2716_found(self):
        """AM2716 is a 24-pin UV-EPROM present in the packaged database."""
        db = EpromDatabase(skip_local_override=True)
        eprom = db.get_eprom("AM2716")
        assert eprom is not None
        assert eprom["pin-count"] == 24
        assert eprom["memory-size"] == 2048  # 2KB


class TestConvertToProgrammer:
    """Tests for EpromDatabase.convert_to_programmer() — D-07 surface 2."""

    def test_convert_to_programmer_has_bus_config(self):
        """convert_to_programmer output must include the bus-config key."""
        db = EpromDatabase(skip_local_override=True)
        eprom = db.get_eprom("W27C512")
        assert eprom is not None
        config = db.convert_to_programmer(eprom)
        assert "bus-config" in config

    def test_convert_to_programmer_memory_size_matches(self):
        """memory-size in programmer config must equal chip's size in bytes."""
        db = EpromDatabase(skip_local_override=True)
        eprom = db.get_eprom("W27C512")
        assert eprom is not None
        config = db.convert_to_programmer(eprom)
        assert config["memory-size"] == 65536  # W27C512 = 64KB

    def test_convert_to_programmer_required_keys_present(self):
        """Programmer config must carry the keys the firmware expects."""
        db = EpromDatabase(skip_local_override=True)
        eprom = db.get_eprom("W27C512")
        assert eprom is not None
        config = db.convert_to_programmer(eprom)
        for key in ("memory-size", "type", "algorithm", "pin-count", "vpp_mv", "flags"):
            assert key in config, f"Missing required key: {key}"

    def test_convert_to_programmer_empty_input_returns_empty(self):
        """convert_to_programmer with None/empty input must return {}."""
        db = EpromDatabase(skip_local_override=True)
        result = db.convert_to_programmer(None)
        assert result == {}
        result_empty = db.convert_to_programmer({})
        assert result_empty == {}

    def test_convert_to_programmer_bus_config_has_bus_list(self):
        """W27C512 bus-config must contain a 'bus' list of RURP line numbers."""
        db = EpromDatabase(skip_local_override=True)
        eprom = db.get_eprom("W27C512")
        assert eprom is not None
        config = db.convert_to_programmer(eprom)
        bus_cfg = config["bus-config"]
        assert "bus" in bus_cfg
        assert isinstance(bus_cfg["bus"], list)
        assert len(bus_cfg["bus"]) > 0


class TestDipToRurpPinTranslation:
    """Tests for DIP->RURP pin translation via get_bus_config — D-07 surface 3.

    The translation is performed by database.get_bus_config(), which looks up
    the pinout's address-bus-pins and maps each through pin_conversions[pins].
    Tests assert against the real chip_database.json + pinouts.json data via
    the EpromDatabase(skip_local_override=True) construction seam.
    """

    def test_dip28_w27c512_bus_has_16_lines(self):
        """W27C512 has 16 address lines (A0-A15) — bus list must have 16 entries."""
        db = EpromDatabase(skip_local_override=True)
        eprom = db.get_eprom("W27C512")
        assert eprom is not None
        # W27C512 is 64KB = 16 address bits
        bus = eprom["bus-config"]["bus"]
        assert len(bus) == 16

    def test_dip28_w27c512_bus_lines_are_rurp_lines(self):
        """All bus entries for DIP28 W27C512 must be valid RURP line numbers.

        The DIP28_27512 address-bus-pins translate through pin_conversions[28]:
        pins [10,9,8,7,6,5,4,3,25,24,21,23,2,26,27,1] -> RURP lines [0..15].
        """
        db = EpromDatabase(skip_local_override=True)
        eprom = db.get_eprom("W27C512")
        assert eprom is not None
        bus = eprom["bus-config"]["bus"]
        # All lines must be valid RURP bus line numbers (in pin_conversions[28] values)
        valid_rurp_lines = set(pin_conversions[28].values()) - {ROM_CE, ROM_OE}
        for line in bus:
            assert line in valid_rurp_lines, (
                f"RURP line {line} not a valid DIP28 RURP bus line"
            )

    def test_dip28_w27c512_full_address_range(self):
        """DIP28_27512 maps all 16 address pins to RURP lines 0-15 contiguously."""
        db = EpromDatabase(skip_local_override=True)
        bus_config = db.get_bus_config(28, "DIP28_27512")
        assert bus_config is not None
        assert "bus" in bus_config
        # The 16 DIP28 address lines must map to RURP lines 0-15 (sorted)
        assert sorted(bus_config["bus"]) == list(range(16))

    def test_dip24_am2716_bus_has_11_lines(self):
        """AM2716 (2KB) has 11 address lines — bus list must have 11 entries."""
        db = EpromDatabase(skip_local_override=True)
        eprom = db.get_eprom("AM2716")
        assert eprom is not None
        bus = eprom["bus-config"]["bus"]
        assert len(bus) == 11  # 2KB = 2^11

    def test_dip24_am2716_has_vpp_pin_in_bus_config(self):
        """DIP24_2716 pinout has a dedicated VPP pin — bus-config must include vpp-pin."""  # noqa: E501
        db = EpromDatabase(skip_local_override=True)
        bus_config = db.get_bus_config(24, "DIP24_2716")
        assert bus_config is not None
        assert "vpp-pin" in bus_config

    def test_dip24_am2716_has_static_high_in_bus_config(self):
        """DIP24_2716 has VCC at DIP32 socket position 28 (bus line 13) — must appear in static-high."""  # noqa: E501
        db = EpromDatabase(skip_local_override=True)
        bus_config = db.get_bus_config(24, "DIP24_2716")
        assert bus_config is not None
        assert "static-high" in bus_config
        # DIP24 VCC (pin 24) maps to RURP line 13 (per pin_conversions[24])
        assert 13 in bus_config["static-high"]

    def test_pin_conversions_module_constant_dip28(self):
        """Module-level pin_conversions[28] maps DIP28 pins to RURP lines correctly."""
        conv = pin_conversions[28]
        # Key address line pin numbers and their expected RURP lines
        assert conv[10] == 0  # A0 -> RURP 0
        assert conv[9] == 1  # A1 -> RURP 1
        assert conv[1] == 15  # A15 -> RURP 15

    def test_unknown_pinout_returns_none(self):
        """get_bus_config for an unknown pinout key must return None."""
        db = EpromDatabase(skip_local_override=True)
        result = db.get_bus_config(28, "NONEXISTENT_PINOUT_XYZ")
        assert result is None


class TestErasableFlag:
    """D-03 — info-flags bit 0x10 (electrically erasable) must fire for EEPROM family.

    W27C512 has electrical.type="EEPROM" (not "Flash/EEPROM").  Before the fix
    the condition in _map_data was an exact match against "Flash/EEPROM" only, so
    W27C512 never got the erasable bit.  After the fix the condition must cover
    both "EEPROM" and "Flash/EEPROM".
    """

    def test_w27c512_info_flags_has_erasable_bit(self):
        """W27C512 (electrical.type='EEPROM') must have info-flags bit 0x10 set."""
        db = EpromDatabase(skip_local_override=True)
        eprom = db.get_eprom("W27C512")
        assert eprom is not None
        assert eprom.get("info-flags", 0) & 0x10, (
            "W27C512 info-flags bit 0x10 (electrically erasable) must be set"
        )

    def test_2764_info_flags_no_erasable_bit(self):
        """2764 (electrical.type='UV-EPROM') must NOT have info-flags bit 0x10."""
        db = EpromDatabase(skip_local_override=True)
        eprom = db.get_eprom("2764")
        assert eprom is not None
        assert not (eprom.get("info-flags", 0) & 0x10), (
            "2764 UV-EPROM info-flags bit 0x10 must NOT be set"
        )

    def test_27c256_info_flags_no_erasable_bit(self):
        """27C256 (electrical.type='UV-EPROM') must NOT have info-flags bit 0x10."""
        db = EpromDatabase(skip_local_override=True)
        eprom = db.get_eprom("27C256")
        assert eprom is not None
        assert not (eprom.get("info-flags", 0) & 0x10), (
            "27C256 UV-EPROM info-flags bit 0x10 must NOT be set"
        )


class TestConstructionSeam:
    """Tests for the skip_local_override constructor seam (D-06).

    One test constructs EpromDatabase(skip_local_override=False) (the
    production default) and asserts construction succeeds WITHOUT asserting
    specific chip values — confirming the production merge path loads cleanly
    regardless of whether ~/.firestarter/database.json is present.
    (RESEARCH § Meta-Validation "Unsampled" note.)
    """

    def test_skip_local_override_true_construction_succeeds(self):
        """EpromDatabase(skip_local_override=True) must construct without error."""
        db = EpromDatabase(skip_local_override=True)
        assert db.proms  # packaged chip_database.json must not be empty
        assert db.pin_maps  # packaged pinouts.json must not be empty

    def test_skip_local_override_false_construction_succeeds(self):
        """EpromDatabase(skip_local_override=False) must construct without error.

        This confirms the production merge path (which may read ~/.firestarter/)
        loads cleanly. Does NOT assert specific chip values because the local
        override may add/modify chips on the operator's machine.
        """
        db = EpromDatabase(skip_local_override=False)
        # Only assert the database is non-empty — not specific chip values.
        assert db.proms
        assert db.pin_maps

    def test_two_instances_are_independent(self):
        """After de-singleton, two EpromDatabase instances must be independent objects."""  # noqa: E501
        db1 = EpromDatabase(skip_local_override=True)
        db2 = EpromDatabase(skip_local_override=True)
        assert db1 is not db2
        # Mutating one must not affect the other
        original_len = len(db1.proms)
        db2.proms["_TEST_MANUFACTURER_"] = []
        assert len(db1.proms) == original_len
