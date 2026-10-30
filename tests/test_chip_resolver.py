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

import copy
from unittest.mock import patch

import pytest

from firestarter.chip_resolver import resolve_chip
from firestarter.database import EpromDatabase
from firestarter.exceptions import ChipNotFoundError, ChipNotImplementedError

from .conftest import ADAPTER_REQUIRED_TEST_CHIP, adapter_required_db


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
    # INTEL's M2716 row: the bare name also names the SGS-THOMSON and ST rows.
    result = resolve_chip("M2716@INTEL", db=db)
    assert isinstance(result, dict)
    assert result.get("memory-size", 0) > 0
    # 25V chips dispatch to configure_eprom via protocol 0x0B (EPROM_LEGACY).
    assert result.get("vpp_mv") == 25000
    assert result.get("algorithm") == 11


def test_resolve_chip_adapter_required_raises_not_implemented():
    """An adapter-required row must raise ChipNotImplementedError.

    Proves the guard is driven by support_status, not the incidental etype
    string: the synthetic row is a copy of a supported EEPROM row, and only its
    support_status differs. The shipped database has no adapter-required row.
    """
    with pytest.raises(ChipNotImplementedError):
        resolve_chip(ADAPTER_REQUIRED_TEST_CHIP, db=adapter_required_db())


def test_the_promoted_dip24_2816_rows_resolve(db):
    """AT28C04 and AT28C16 were adapter-required. They now resolve on 0x0D."""
    for chip in ("AT28C04", "AT28C16", "28C16A", "UPD28C04"):
        wire = resolve_chip(chip, db=db)
        assert wire["algorithm"] == 0x0D
        assert wire["page-size"] == 1


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


def test_the_27011_rows_are_not_supported(db):
    """The 27011 is page-addressed: 128 KB through 14 address lines. Reading it
    would repeat the first 16 KB eight times (henols/firestarter#136). The
    generator gives the rows a status that says so, and resolve_chip refuses
    them on it."""
    for name in ("AT27C011", "D27011", "D27C011"):
        raw, _ = db.get_eprom_config(name)
        assert raw["support_status"] == "protocol-not-implemented"
        with pytest.raises(ChipNotImplementedError, match="14 address lines"):
            resolve_chip(name, db)


def test_resolve_chip_refuses_a_pin_map_narrower_than_the_chip(db):
    """A local override can mark a narrow row supported. The bus-width guard
    still refuses it."""
    raw, _ = db.get_eprom_config("D27011")
    raw["support_status"] = "supported"
    del raw["unsupported_reason"]
    with pytest.raises(
        ChipNotImplementedError, match="its pin map has 14 address lines"
    ):
        resolve_chip("D27011", db)


@pytest.mark.parametrize("name", ["2532", "MBM27C1000"])
def test_resolve_chip_refuses_an_address_line_on_a_ce_or_oe_pin(db, name):
    """2532 has A11 on pin 18 and the 27C301 layout has A16 on pin 24: the
    shield drives those pins as CE and OE. The rows are adapter-required; an
    override that marks one supported is still refused before any wire."""
    raw, _ = db.get_eprom_config(name)
    assert raw["support_status"] == "adapter-required"
    raw["support_status"] = "supported"
    del raw["unsupported_reason"]
    with pytest.raises(ChipNotImplementedError, match="drives as CE or OE"):
        resolve_chip(name, db)


@pytest.mark.parametrize("name", ["SST28SF040", "SST28LF040", "SST28VF040"])
def test_the_sst28_rows_are_not_supported(db, name):
    """Algorithm 0x10 puts 12V on socket pin 1, which is A18 on these parts
    (henols/firestarter#154). The generator gives the rows a status that says
    so, and resolve_chip refuses them on it."""
    raw, _ = db.get_eprom_config(name)
    assert raw["support_status"] == "protocol-not-implemented"
    with pytest.raises(ChipNotImplementedError, match="12V on socket pin 1"):
        resolve_chip(name, db)


def test_resolve_chip_refuses_an_intel_28f_chip_with_a_bus_line_on_pin_1(db):
    """A local override can mark an SST28SF040 row supported. The 0x10 guard
    still refuses it before any wire."""
    raw, _ = db.get_eprom_config("SST28SF040")
    raw["support_status"] = "supported"
    del raw["unsupported_reason"]
    with pytest.raises(ChipNotImplementedError, match="uses pin 1 as an address"):
        resolve_chip("SST28SF040", db)


def test_resolve_chip_refuses_pin_1_inside_an_18_line_bus(db):
    """The guard checks which pins the bus uses, not how many lines it has:
    an override map that puts A17 on pin 1 is refused for an 18-line chip."""
    pins = copy.deepcopy(db.pin_maps["DIP32_STD"]["pins"])
    pins["address-bus-pins"][17] = 1
    del pins["vpp-pin"]
    db.pin_maps["TEST_A17_ON_PIN_1"] = {"pins": pins}
    raw, _ = db.get_eprom_config("P28F020")
    raw["pinout"] = "TEST_A17_ON_PIN_1"
    with pytest.raises(ChipNotImplementedError, match="uses pin 1 as an address"):
        resolve_chip("P28F020", db)


def test_the_0x10_guard_refuses_exactly_the_rows_the_generator_refuses(db):
    """The generator and resolve_chip each encode the 0x10 pin-1 rule. With
    every 0x10 row forced to supported, resolve_chip refuses a row on the
    pin-1 rule exactly when the generator gave that row the pin-1 reason."""
    rows = [
        (mfg, chip)
        for mfg, chips in db.proms.items()
        for chip in chips
        if chip["programming"]["algorithm"] == 0x10
    ]
    assert rows
    for mfg, chip in rows:
        flagged = "socket pin 1" in chip.get("unsupported_reason", "")
        chip["support_status"] = "supported"
        chip.pop("unsupported_reason", None)
        name = f"{chip['part_number'].split(',')[0]}@{mfg}"
        try:
            resolve_chip(name, db)
            refused = False
        except ChipNotImplementedError as exc:
            assert "socket pin 1" in str(exc), name
            refused = True
        assert refused == flagged, name


def test_resolve_chip_accepts_an_18_line_intel_28f_chip(db):
    data = resolve_chip("P28F020", db)
    assert data["algorithm"] == 0x10
    assert len(data["bus-config"]["bus"]) == 18


def test_resolve_chip_accepts_a_bus_that_covers_the_chip(db):
    assert len(resolve_chip("AM27C010", db)["bus-config"]["bus"]) == 17
