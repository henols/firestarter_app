"""Phase 42 / ERR-03 fallback coverage lift for ``EpromConsolePresenter`` (D-14
fallback per CONTEXT — eprom_info.py at 19% is the largest gap).

Targets the pure helper methods (``_json_output_formatted``,
``_clean_config_for_export``, ``_prepare_export_configuration_data``) and the
not-found path through ``prepare_detailed_eprom_data``.

Phase 60 extends this file with:
  - D-07: ``_interpret_flags`` unit tests (bit 0x10 semantic reconciliation)
  - D-04: Synthetic per-electrical.type fixtures covering D-01/D-02/D-05/D-07-VPP
  - D-04: Parametrized real-DB smoke set (EEPROM display set + UV-EPROM control set)

Note (Phase 60): The GATE-1.8b vpp-pin list-vs-int TypeError is confirmed mitigated
by ``_first_pin`` at ic_layout.py L123.  The full ``prepare_detailed_eprom_data``
happy path is safe to exercise and is now covered by the smoke tests below.
"""

import pytest

from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.ic_layout import EpromSpecBuilder


@pytest.fixture(scope="module")
def db() -> EpromDatabase:
    return EpromDatabase(skip_local_override=True)


@pytest.fixture(scope="module")
def presenter(db: EpromDatabase) -> EpromConsolePresenter:
    return EpromConsolePresenter(db)


def test_prepare_detailed_returns_none_for_missing_eprom(
    presenter: EpromConsolePresenter,
) -> None:
    """prepare_detailed_eprom_data returns None when eprom_details is None."""
    result = presenter.prepare_detailed_eprom_data(
        "MISSING_CHIP",
        None,
        None,
        None,
        None,
    )
    assert result is None


def test_json_output_formatted_compacts_number_lists(
    presenter: EpromConsolePresenter,
) -> None:
    """_json_output_formatted compacts lists of integers onto a single line."""
    payload = {"bus-config": {"data": [1, 2, 3, 4, 5]}}
    out = presenter._json_output_formatted(payload)
    assert "[1, 2, 3, 4, 5]" in out


def test_clean_config_for_export_strips_vdd(
    presenter: EpromConsolePresenter,
) -> None:
    """_clean_config_for_export drops the 'vdd' voltage but keeps 'vcc' / 'vpp'."""
    raw = {
        "name": "Test",
        "voltages": {"vdd": 5.0, "vcc": 5.0, "vpp": 12.0},
        "has-chip-id": True,
        "chip-id": "0x42",
    }
    cleaned = presenter._clean_config_for_export(raw)
    assert "vdd" not in cleaned["voltages"]
    assert cleaned["voltages"]["vcc"] == 5.0
    assert cleaned["voltages"]["vpp"] == 12.0
    assert cleaned["chip-id"] == "0x42"


def test_clean_config_without_chip_id_strips_chip_id_key(
    presenter: EpromConsolePresenter,
) -> None:
    """When has-chip-id is False the chip-id key is removed."""
    raw = {
        "name": "Test",
        "voltages": {},
        "has-chip-id": False,
    }
    cleaned = presenter._clean_config_for_export(raw)
    assert "chip-id" not in cleaned


def test_clean_config_falls_back_to_variant_pin_map(
    presenter: EpromConsolePresenter,
) -> None:
    """When pin-map is absent, pin-map falls back to 'variant', then to 'default'."""
    # Variant fallback
    raw = {"name": "X", "voltages": {}, "has-chip-id": False, "variant": "v1"}
    cleaned = presenter._clean_config_for_export(raw)
    assert cleaned["pin-map"] == "v1"

    # Default fallback when neither is present
    raw_default = {"name": "Y", "voltages": {}, "has-chip-id": False}
    cleaned_default = presenter._clean_config_for_export(raw_default)
    assert cleaned_default["pin-map"] == "default"


def test_prepare_export_configuration_with_missing_inputs_returns_none(
    presenter: EpromConsolePresenter,
) -> None:
    """_prepare_export_configuration_data returns None when raw_config / manufacturer is missing."""
    assert presenter._prepare_export_configuration_data(None, "X", "name") is None
    assert presenter._prepare_export_configuration_data({}, None, "name") is None


def test_present_eprom_details_none_returns_early(
    presenter: EpromConsolePresenter, capsys
) -> None:
    """present_eprom_details with None chip_data short-circuits without crashing."""
    presenter.present_eprom_details(None)
    # No assertion on output — just that it does not raise.


# ---------------------------------------------------------------------------
# D-07: _interpret_flags unit tests (bit 0x10 semantic reconciliation)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def spec_builder(db: EpromDatabase) -> EpromSpecBuilder:
    return EpromSpecBuilder(db)


def test_interpret_flags_0x10_describes_electrical_erasability(
    spec_builder: EpromSpecBuilder,
) -> None:
    """D-07: _interpret_flags(0x10) must describe electrical erasability.

    After D-07 the bit 0x10 must mean 'electrically erasable', NOT the old
    'Needs software write-enable/unlock sequence' label.
    """
    props = spec_builder._interpret_flags(0x10)
    text = " ".join(props).lower()
    assert "erase" in text or "erasable" in text, (
        f"_interpret_flags(0x10) must contain erasable/erase text; got: {props}"
    )
    assert "write-enable" not in text, (
        f"_interpret_flags(0x10) must NOT contain 'write-enable'; got: {props}"
    )
    assert "unlock" not in text, (
        f"_interpret_flags(0x10) must NOT contain 'unlock'; got: {props}"
    )


def test_interpret_flags_0x20_describes_readable_id(
    spec_builder: EpromSpecBuilder,
) -> None:
    """D-07: _interpret_flags(0x20) must still describe readable chip/device ID."""
    props = spec_builder._interpret_flags(0x20)
    text = " ".join(props).lower()
    assert "id" in text or "identifier" in text, (
        f"_interpret_flags(0x20) must describe readable ID; got: {props}"
    )


def test_interpret_flags_dead_entries_absent(
    spec_builder: EpromSpecBuilder,
) -> None:
    """D-07: dead flag entries (0x08/0x40/0x80/0x200/0x4000/0x8000/0x400000) must produce no output."""
    dead_bits = 0x08 | 0x40 | 0x80 | 0x200 | 0x4000 | 0x8000 | 0x400000
    props = spec_builder._interpret_flags(dead_bits)
    assert props == [], f"Dead flag entries must produce no properties; got: {props}"


# ---------------------------------------------------------------------------
# D-04: Synthetic per-electrical.type fixture tests (D-01/D-02/D-05/D-07-VPP)
# ---------------------------------------------------------------------------

# Minimal raw_config shapes matching the live DB structure
SYNTH_EEPROM_RAW = {
    "electrical": {
        "pin_count": 28,
        "size_bytes": 65536,
        "type": "EEPROM",
        "vcc": "5V",
        "vpp": "12V",
        "vpp_mv": 12000,
    },
    "part_number": "SYNTH_EEPROM",
    "pinout": "DIP28_27512",
    "programming": {"algorithm": 7, "chip_id_check": True},
}

SYNTH_UV_EPROM_RAW = {
    "electrical": {
        "pin_count": 28,
        "size_bytes": 65536,
        "type": "UV-EPROM",
        "vcc": "5V",
        "vpp": "12V",
        "vpp_mv": 12000,
    },
    "part_number": "SYNTH_UV_EPROM",
    "pinout": "DIP28_27512",
    "programming": {"algorithm": 7, "chip_id_check": False},
}

SYNTH_FLASH_RAW = {
    "electrical": {
        "pin_count": 32,
        "size_bytes": 131072,
        "type": "Flash/EEPROM",
        "vcc": "5V",
        "vpp": "0V",
        "vpp_mv": 0,
    },
    "part_number": "SYNTH_FLASH",
    "pinout": "DIP32_STD",
    "programming": {"algorithm": 5, "chip_id_check": True},
}

SYNTH_SRAM_RAW = {
    "electrical": {
        "pin_count": 28,
        "size_bytes": 8192,
        "type": "SRAM",
        "vcc": "5V",
        "vpp": "0V",
        "vpp_mv": 0,
    },
    "part_number": "SYNTH_SRAM",
    "pinout": "DIP28_27512",
    "programming": {"algorithm": 0x28, "chip_id_check": False},
}


def _map_synth(db: EpromDatabase, raw: dict, manufacturer: str = "SYNTH_MFR") -> dict:
    """Call _map_data on a synthetic raw record via the internal API."""
    return db._map_data(raw, manufacturer)


def test_synthetic_eeprom_type_label(
    db: EpromDatabase, presenter: EpromConsolePresenter
) -> None:
    """D-01: Synthetic EEPROM record with proto=0x07 must show 'EEPROM' type, not UV-EPROM."""
    mapped = _map_synth(db, SYNTH_EEPROM_RAW)
    result = presenter.prepare_detailed_eprom_data(
        "SYNTH_EEPROM", mapped, None, SYNTH_EEPROM_RAW, "SYNTH_MFR"
    )
    assert result is not None
    assert "EEPROM" in result["type_str"], (
        f"Synthetic EEPROM type_str must contain 'EEPROM'; got: {result['type_str']}"
    )
    assert "UV-EPROM" not in result["type_str"], (
        f"Synthetic EEPROM type_str must NOT contain 'UV-EPROM'; got: {result['type_str']}"
    )


def test_synthetic_eeprom_can_erase_electrically(
    db: EpromDatabase, presenter: EpromConsolePresenter
) -> None:
    """D-02: Synthetic EEPROM record must show electrically erasable (no 'uv')."""
    mapped = _map_synth(db, SYNTH_EEPROM_RAW)
    result = presenter.prepare_detailed_eprom_data(
        "SYNTH_EEPROM", mapped, None, SYNTH_EEPROM_RAW, "SYNTH_MFR"
    )
    assert result is not None
    can_erase = result.get("can_erase_str", "")
    assert "erase" in can_erase.lower() or "erasable" in can_erase.lower(), (
        f"EEPROM can_erase_str must contain erase/erasable; got: {can_erase!r}"
    )
    assert "uv" not in can_erase.lower(), (
        f"EEPROM can_erase_str must NOT contain 'uv'; got: {can_erase!r}"
    )


def test_synthetic_uv_eprom_type_label(
    db: EpromDatabase, presenter: EpromConsolePresenter
) -> None:
    """D-01: Synthetic UV-EPROM record must show 'UV-EPROM' type label."""
    mapped = _map_synth(db, SYNTH_UV_EPROM_RAW)
    result = presenter.prepare_detailed_eprom_data(
        "SYNTH_UV_EPROM", mapped, None, SYNTH_UV_EPROM_RAW, "SYNTH_MFR"
    )
    assert result is not None
    assert "UV-EPROM" in result["type_str"], (
        f"Synthetic UV-EPROM type_str must contain 'UV-EPROM'; got: {result['type_str']}"
    )


def test_synthetic_uv_eprom_can_erase_uv_only(
    db: EpromDatabase, presenter: EpromConsolePresenter
) -> None:
    """D-02: Synthetic UV-EPROM must show UV-only erase."""
    mapped = _map_synth(db, SYNTH_UV_EPROM_RAW)
    result = presenter.prepare_detailed_eprom_data(
        "SYNTH_UV_EPROM", mapped, None, SYNTH_UV_EPROM_RAW, "SYNTH_MFR"
    )
    assert result is not None
    can_erase = result.get("can_erase_str", "")
    assert "uv" in can_erase.lower(), (
        f"UV-EPROM can_erase_str must contain 'uv'; got: {can_erase!r}"
    )


def test_synthetic_sram_no_can_erase_row(
    db: EpromDatabase, presenter: EpromConsolePresenter
) -> None:
    """D-02: SRAM synthetic record must produce no can_erase_str row (SRAM is volatile)."""
    mapped = _map_synth(db, SYNTH_SRAM_RAW)
    result = presenter.prepare_detailed_eprom_data(
        "SYNTH_SRAM", mapped, None, SYNTH_SRAM_RAW, "SYNTH_MFR"
    )
    assert result is not None
    assert "can_erase_str" not in result, (
        f"SRAM must NOT have a can_erase_str row; got: {result.get('can_erase_str')}"
    )


def test_synthetic_eeprom_vpp_shown(
    db: EpromDatabase, presenter: EpromConsolePresenter
) -> None:
    """D-07-VPP: Synthetic EEPROM with vpp_mv=12000 must show VPP in output_data."""
    mapped = _map_synth(db, SYNTH_EEPROM_RAW)
    result = presenter.prepare_detailed_eprom_data(
        "SYNTH_EEPROM", mapped, None, SYNTH_EEPROM_RAW, "SYNTH_MFR"
    )
    assert result is not None
    assert "vpp_str" in result, (
        f"Synthetic EEPROM (vpp_mv=12000) must have vpp_str in output_data; got keys: {list(result.keys())}"
    )
    assert "12" in result["vpp_str"], (
        f"vpp_str must show 12V; got: {result['vpp_str']!r}"
    )


def test_synthetic_output_no_not_verified_marker(
    db: EpromDatabase, presenter: EpromConsolePresenter
) -> None:
    """D-05: output_data must never contain '-- NOT VERIFIED --'."""
    for raw in (SYNTH_EEPROM_RAW, SYNTH_UV_EPROM_RAW, SYNTH_FLASH_RAW, SYNTH_SRAM_RAW):
        mapped = _map_synth(db, raw)
        result = presenter.prepare_detailed_eprom_data(
            raw["part_number"], mapped, None, raw, "SYNTH_MFR"
        )
        assert result is not None
        for key, val in result.items():
            if isinstance(val, str):
                assert "NOT VERIFIED" not in val, (
                    f"output_data[{key!r}] must not contain 'NOT VERIFIED'; got: {val!r}"
                )


# ---------------------------------------------------------------------------
# D-04: Parametrized real-DB smoke set (EEPROM display set + UV-EPROM controls)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "chip_name,expected_type_keyword,expect_erasable",
    [
        ("W27C512", "EEPROM", True),
        ("SST27VF512", "EEPROM", True),
        ("SST27SF512", "EEPROM", True),
        ("W27C257", "EEPROM", True),
        ("M27C512", "UV-EPROM", False),
        ("27C256", "UV-EPROM", False),
        ("2764", "UV-EPROM", False),
    ],
)
def test_type_label_and_erase_smoke(
    chip_name: str,
    expected_type_keyword: str,
    expect_erasable: bool,
    db: EpromDatabase,
    presenter: EpromConsolePresenter,
) -> None:
    """D-04 parametrized smoke: EEPROM set shows EEPROM; UV-EPROM control set unregressed."""
    data = db.get_eprom(chip_name)
    assert data is not None, f"{chip_name} not found in DB"
    raw, mfr = db.get_eprom_config(chip_name)
    result = presenter.prepare_detailed_eprom_data(chip_name, data, None, raw, mfr)
    assert result is not None, (
        f"prepare_detailed_eprom_data returned None for {chip_name}"
    )

    # D-01: type label
    assert expected_type_keyword in result["type_str"], (
        f"{chip_name}: type_str must contain {expected_type_keyword!r}; got: {result['type_str']!r}"
    )

    # D-02: can_erase
    can_erase = result.get("can_erase_str", "")
    if expect_erasable:
        assert "uv" not in can_erase.lower(), (
            f"{chip_name}: EEPROM can_erase_str must not say 'uv'; got: {can_erase!r}"
        )
        assert "erase" in can_erase.lower() or "erasable" in can_erase.lower(), (
            f"{chip_name}: EEPROM can_erase_str must mention erase; got: {can_erase!r}"
        )
    else:
        assert "uv" in can_erase.lower(), (
            f"{chip_name}: UV-EPROM can_erase_str must say 'uv'; got: {can_erase!r}"
        )

    # D-05: no NOT VERIFIED marker anywhere
    for key, val in result.items():
        if isinstance(val, str):
            assert "NOT VERIFIED" not in val, (
                f"{chip_name}: output_data[{key!r}] must not contain 'NOT VERIFIED'; got: {val!r}"
            )
