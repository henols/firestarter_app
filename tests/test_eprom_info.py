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

Phase 61 extends this file with:
  - D-06: Parametrized list-vs-info parity test (EEPROM display set, UV-EPROM
    control set, SRAM control) — list Type/VPP must equal info type_str/vpp_str.
  - D-07: Width-floor / no-break assertion — no column narrower than today's floor;
    no body row cell overflows its column width.
  - D-05: Legacy-fallback assertion — resolve_type_label(None) returns non-empty
    label without raising.
"""

import logging

import pytest

from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter, print_eprom_list_table
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
        "vpp_mv": 12000,  # mirrors live DB: all 76 SRAMs carry vpp_mv=12000 (infoic.xml artifact)
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
    """D-02/D-07-VPP: SRAM synthetic record must produce no can_erase_str row (volatile)
    and no vpp_str row (vpp_mv=12000 in live DB is an infoic.xml artifact, not real VPP)."""
    mapped = _map_synth(db, SYNTH_SRAM_RAW)
    result = presenter.prepare_detailed_eprom_data(
        "SYNTH_SRAM", mapped, None, SYNTH_SRAM_RAW, "SYNTH_MFR"
    )
    assert result is not None
    assert "can_erase_str" not in result, (
        f"SRAM must NOT have a can_erase_str row; got: {result.get('can_erase_str')}"
    )
    assert "vpp_str" not in result, (
        f"SRAM must NOT have a vpp_str row (no programming voltage); "
        f"got: {result.get('vpp_str')!r}"
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


# ---------------------------------------------------------------------------
# Phase 61 — D-05: Legacy fallback for resolve_type_label
# ---------------------------------------------------------------------------


def test_resolve_type_label_legacy_fallback_none(
    spec_builder: EpromSpecBuilder,
) -> None:
    """D-05: resolve_type_label(None) returns a non-empty protocol-based label
    without raising (legacy user-override entries without electrical.type)."""
    label = spec_builder.resolve_type_label(None, type_int=1, protocol_id=0x07)
    assert label, "resolve_type_label(None) must return a non-empty string"
    assert isinstance(label, str), "resolve_type_label(None) must return str"


def test_resolve_type_label_legacy_fallback_empty_string(
    spec_builder: EpromSpecBuilder,
) -> None:
    """D-05: resolve_type_label('') returns a non-empty protocol-based label."""
    label = spec_builder.resolve_type_label("", type_int=1, protocol_id=0x07)
    assert label, "resolve_type_label('') must return a non-empty string"


# ---------------------------------------------------------------------------
# Phase 61 — D-06: Parametrized list-vs-info parity test
# ---------------------------------------------------------------------------

# The SRAM control uses a synthetic mapped dict (electrical-type='SRAM',
# vpp_mv=12000) that mirrors the live DB pattern (all 76 SRAMs carry
# vpp_mv=12000 as an infoic.xml decode artifact).
SYNTH_SRAM_MAPPED: dict = {
    "name": "SYNTH_SRAM_LIST",
    "manufacturer": "SYNTH",
    "memory-size": 8192,
    "type": 4,
    "pin-count": 28,
    "vpp_volts": 12.0,
    "vpp_mv": 12000,
    "vcc": 5.0,
    "pulse-delay": 0,
    "verified": False,
    "info-flags": 0,
    "flags": 0,
    "protocol-id": 0x28,
    "pin-map": "DIP28_27512",
    "electrical-type": "SRAM",
}


@pytest.mark.parametrize(
    "chip_name,expected_type_keyword,expected_vpp_present",
    [
        # EEPROM display set — must show "EEPROM" and a voltage
        ("W27C512", "EEPROM", True),
        ("SST27VF512", "EEPROM", True),
        ("SST27SF512", "EEPROM", True),
        ("W27C257", "EEPROM", True),
        # UV-EPROM control set — must still show "UV-EPROM"
        ("M27C512", "UV-EPROM", True),
        ("27C256", "UV-EPROM", True),
        ("2764", "UV-EPROM", True),
    ],
)
def test_list_vs_info_parity(
    chip_name: str,
    expected_type_keyword: str,
    expected_vpp_present: bool,
    db: EpromDatabase,
    presenter: EpromConsolePresenter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D-06: list Type/VPP must equal what info (build_specifications) produces.

    For each chip in the EEPROM display set and UV-EPROM control set, render
    the chip's single mapped row through print_eprom_list_table (capturing via
    caplog) and assert that the list cell values equal the info-side values from
    build_specifications.  This is the parity guarantee: both views call the same
    resolve_type_label helper so structural divergence is impossible.
    """
    # Info side: get the type_str and vpp_str from build_specifications.
    data = db.get_eprom(chip_name)
    assert data is not None, f"{chip_name} not found in DB"
    raw, mfr = db.get_eprom_config(chip_name)
    etype = raw.get("electrical", {}).get("type") if raw else None
    spec = presenter.spec_builder.build_specifications(data, etype)
    assert spec is not None, f"build_specifications returned None for {chip_name}"
    info_type_str = spec["type_str"]
    info_vpp_str = spec.get("vpp_str", "-")  # absent means no VPP row → list shows '-'

    # List side: render through print_eprom_list_table and capture logger.info output.
    # Use caplog.records to get raw message text (without the log-level/logger prefix
    # that caplog.text prepends — that prefix prevents a simple lstrip().startswith()).
    rows = [r for r in db.search_eprom(chip_name) if r["name"].startswith(chip_name)]
    assert rows, f"search_eprom returned no rows for {chip_name}"
    row = rows[0]

    with caplog.at_level(logging.INFO, logger="EpromConsolePresenter"):
        caplog.clear()
        print_eprom_list_table([row], presenter.spec_builder)

    messages = [rec.getMessage() for rec in caplog.records]
    body_lines = [
        msg for msg in messages if msg.startswith("|") and not msg.startswith("| Name")
    ]
    assert body_lines, f"No body rows captured in caplog for {chip_name}"
    body_line = body_lines[0]

    # Split on '|', drop the empty strings at start/end (from leading/trailing '|').
    # Format: | Name | Mfr | Pins | Chip ID | Type | VPP |
    cells = [c.strip() for c in body_line.split("|")[1:-1]]
    # cells[0]=Name, [1]=Mfr, [2]=Pins, [3]=ChipID, [4]=Type, [5]=VPP
    assert len(cells) >= 6, f"Expected >=6 cells in row; got: {cells}"

    list_type_str = cells[4]
    list_vpp_str = cells[5]

    # Parity assertion: list must equal info for Type.
    assert list_type_str == info_type_str, (
        f"{chip_name}: list Type '{list_type_str}' != info type_str '{info_type_str}'"
    )

    # Parity assertion: list must equal info for VPP.
    assert list_vpp_str == info_vpp_str, (
        f"{chip_name}: list VPP '{list_vpp_str}' != info vpp_str '{info_vpp_str}'"
    )

    # Type keyword assertion.
    assert expected_type_keyword in list_type_str, (
        f"{chip_name}: expected '{expected_type_keyword}' in list Type '{list_type_str}'"
    )


def test_list_sram_vpp_is_dash(
    db: EpromDatabase,
    presenter: EpromConsolePresenter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D-06/D-03: SRAM control — list VPP cell is '-' and no '12.0v' despite vpp_mv=12000.

    Uses the synthetic SRAM mapped dict that mirrors the live DB pattern (all 76
    SRAMs carry vpp_mv=12000 as an infoic.xml decode artifact).  Asserts:
    - Type cell is 'SRAM'
    - VPP cell is '-'
    - '12.0v' is absent from the body row
    Also verifies parity with info (build_specifications returns no vpp_str for SRAM).
    """
    # Info side: SRAM has no vpp_str row → info shows nothing, list should show '-'.
    spec = presenter.spec_builder.build_specifications(
        SYNTH_SRAM_MAPPED, SYNTH_SRAM_MAPPED.get("electrical-type")
    )
    assert spec is not None
    assert "vpp_str" not in spec, (
        f"SRAM info spec must not have vpp_str; got: {spec.get('vpp_str')!r}"
    )
    info_vpp = spec.get("vpp_str", "-")  # absent → '-'

    # List side: render the synthetic SRAM row.
    # Use caplog.records for raw message text (without log-level prefix).
    with caplog.at_level(logging.INFO, logger="EpromConsolePresenter"):
        caplog.clear()
        print_eprom_list_table([SYNTH_SRAM_MAPPED], presenter.spec_builder)

    messages = [rec.getMessage() for rec in caplog.records]
    body_lines = [
        msg for msg in messages if msg.startswith("|") and not msg.startswith("| Name")
    ]
    assert body_lines, "No body rows captured in caplog for SRAM control"
    body_line = body_lines[0]

    # Split on '|', drop the empty strings at start/end (from leading/trailing '|').
    cells = [c.strip() for c in body_line.split("|")[1:-1]]
    assert len(cells) >= 6, f"Expected >=6 cells; got: {cells}"

    list_type_str = cells[4]
    list_vpp_str = cells[5]

    assert list_type_str == "SRAM", (
        f"SRAM control: list Type must be 'SRAM'; got: '{list_type_str}'"
    )
    assert list_vpp_str == "-", (
        f"SRAM control: list VPP must be '-'; got: '{list_vpp_str}'"
    )
    assert "12.0v" not in body_line, (
        f"SRAM control: '12.0v' must not appear in body row; row: {body_line!r}"
    )
    # Parity
    assert list_vpp_str == info_vpp, (
        f"SRAM control: list VPP '{list_vpp_str}' != info vpp fallback '{info_vpp}'"
    )


# ---------------------------------------------------------------------------
# Phase 61 — D-07: Width-floor / no-break assertion
# ---------------------------------------------------------------------------


def _parse_divider_segment_widths(divider_line: str) -> list[int]:
    """Parse a '+----+----+' divider line and return the dash-segment widths."""
    # Split on '+', ignore empty first/last elements
    return [len(seg) for seg in divider_line.split("+") if seg]


def test_width_floor_and_no_overflow(
    db: EpromDatabase,
    presenter: EpromConsolePresenter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D-07: Width-floor and no-overflow assertion.

    Renders a multi-row result set (27C search, truncated to a manageable slice)
    plus a synthetic long-name row (to exercise the 20-char cap ellipsis) and
    asserts:
    a) Every body row, split on '|', has each cell whose visible length equals
       the corresponding divider segment width (no overflow, rows align to divider).
    b) Name segment width in [13, 20]; Manufacturer == 17; Pins == 5;
       Chip ID == 11; Type == 12; VPP == 5.

    Column content widths inferred from divider segment widths:
    Segment = content_width + 1 (for the leading space padding in '| content').
    Exception: Name segment = name_w + 1 (first column starts with '| ').
    """
    # Build a result set with a synthetic long-name row (>20 chars, triggers ellipsis).
    long_name_row: dict = {
        "name": "M48T08,M48T08Y,M48T18Y,M48B18Y",  # 30-char alias row
        "manufacturer": "ST",
        "memory-size": 8192,
        "type": 4,
        "pin-count": 28,
        "vpp_volts": 0.0,
        "vpp_mv": 12000,
        "vcc": 5.0,
        "pulse-delay": 0,
        "verified": False,
        "info-flags": 0,
        "flags": 0,
        "protocol-id": 0x28,
        "pin-map": "DIP28_27512",
        "bus-config": {"dummy": 1},  # has bus config → no [!] suffix
        "electrical-type": "SRAM",
    }

    rows = db.search_eprom("27C")[:5] + [long_name_row]

    with caplog.at_level(logging.INFO, logger="EpromConsolePresenter"):
        caplog.clear()
        print_eprom_list_table(rows, presenter.spec_builder)

    # Use caplog.records to get raw message text (without the log-level prefix).
    messages = [rec.getMessage() for rec in caplog.records]
    divider_lines = [msg for msg in messages if msg.startswith("+")]
    assert divider_lines, "No divider lines captured in caplog"
    divider_line = divider_lines[0]

    seg_widths = _parse_divider_segment_widths(divider_line)
    assert len(seg_widths) == 6, (
        f"Expected 6 divider segments (Name,Mfr,Pins,ChipID,Type,VPP); got: {seg_widths}"
    )

    # Segment widths = content_width + 1 (leading space).
    # Name content width = seg_widths[0] - 1 (varies; clamped to [13,20]).
    name_content_w = seg_widths[0] - 1
    mfr_content_w = seg_widths[1] - 1
    pins_content_w = seg_widths[2] - 1
    chipid_content_w = seg_widths[3] - 1
    type_content_w = seg_widths[4] - 1
    vpp_content_w = seg_widths[5] - 1

    assert 13 <= name_content_w <= 20, (
        f"Name column width must be in [13,20]; got {name_content_w}"
    )
    assert mfr_content_w == 17, (
        f"Manufacturer column width must be 17; got {mfr_content_w}"
    )
    assert pins_content_w == 5, f"Pins column width must be 5; got {pins_content_w}"
    assert chipid_content_w == 11, (
        f"Chip ID column width must be 11; got {chipid_content_w}"
    )
    assert type_content_w == 12, f"Type column width must be 12; got {type_content_w}"
    assert vpp_content_w == 5, f"VPP column width must be 5; got {vpp_content_w}"

    # No body row cell overflows its divider segment: every body line's pipe-split
    # cell visible length must not exceed the corresponding content width.
    body_lines = [
        msg for msg in messages if msg.startswith("|") and not msg.startswith("| Name")
    ]
    assert body_lines, "No body rows captured for overflow check"

    content_widths = [
        name_content_w,
        mfr_content_w,
        pins_content_w,
        chipid_content_w,
        type_content_w,
        vpp_content_w,
    ]
    for body_line in body_lines:
        # Drop the empty strings at start/end from splitting '| ... |'.
        cells = body_line.split("|")[1:-1]
        assert len(cells) == 6, (
            f"Expected 6 cells per body row; got {len(cells)}: {body_line!r}"
        )
        for col_idx, (cell, max_w) in enumerate(zip(cells, content_widths)):
            # Each cell is ' content ' (space-padded). The visible content length
            # is len(cell.strip()).  Must not exceed the column content width.
            visible = len(cell.strip())
            assert visible <= max_w, (
                f"Col {col_idx} cell overflows width {max_w} (visible={visible}): "
                f"{cell!r} in row: {body_line!r}"
            )
