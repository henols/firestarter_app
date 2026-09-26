"""Tests for the `info` VPP line (INFO-01) and the "Can be erased" line (INFO-02).

The VPP line shows only a programming VPP. On the 5 V-only protocols, `vpp_mv` is a WP-pin voltage.
The "Can be erased" line uses the same rule that sets FLAG_CAN_ERASE, so it agrees with `erase`.
"""

from __future__ import annotations

import pytest

from firestarter import flash4_erase_gate
from firestarter.constants import FLAG_CAN_ERASE
from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.erase_support import erase_accepted
from firestarter.ic_layout import (
    CAN_ERASE_NOT_SUPPORTED,
    CAN_ERASE_UV_ONLY,
    CAN_ERASE_YES,
    EpromSpecBuilder,
)
from firestarter.vpp_display import shows_programming_vpp

ROW_COUNT = 746
WP_PIN_PROTOCOLS = (0x05, 0x06, 0x0D)


@pytest.fixture(scope="module")
def db() -> EpromDatabase:
    return EpromDatabase(skip_local_override=True)


@pytest.fixture(scope="module")
def rows(db: EpromDatabase) -> list[tuple[dict, dict, dict]]:
    """(raw record, mapped record, build_specifications output) for every shipped row."""
    spec_builder = EpromSpecBuilder(db)
    out = []
    for manufacturer, ics in db.proms.items():
        for ic in ics:
            mapped = db.map_chip_record(ic, manufacturer)
            spec = spec_builder.build_specifications(
                mapped, electrical_type=ic["electrical"].get("type")
            )
            assert spec is not None
            out.append((ic, mapped, spec))
    assert len(out) == ROW_COUNT
    return out


# --- INFO-01 ----------------------------------------------------------------------------------


def test_wp_pin_rows_show_no_vpp(rows: list[tuple[dict, dict, dict]]) -> None:
    wp_rows = [s for _, m, s in rows if m["protocol-id"] in WP_PIN_PROTOCOLS]
    assert len(wp_rows) == 301
    assert all("vpp_str" not in s for s in wp_rows)


def test_exact_count_of_rows_that_show_vpp(rows: list[tuple[dict, dict, dict]]) -> None:
    shown = [m for _, m, s in rows if "vpp_str" in s]
    assert len(shown) == 368
    assert {m["protocol-id"] for m in shown} == {0x07, 0x08, 0x0B, 0x10}


@pytest.mark.parametrize(
    ("algorithm", "vpp_mv", "expected"),
    [
        (0x07, 12000, True),
        (0x0B, "25000", True),
        (0x10, 12000, True),
        (0x0D, 12000, False),
        (0x05, 12000, False),
        (0x0E, 12000, False),
        (0x99, 12000, False),
        (None, 12000, False),
        (0x08, 0, False),
        (0x08, None, False),
        (0x08, "twelve", False),
    ],
)
def test_shows_programming_vpp(
    algorithm: object, vpp_mv: object, expected: bool
) -> None:
    assert shows_programming_vpp(algorithm, vpp_mv) is expected


# --- INFO-02 ----------------------------------------------------------------------------------


def test_can_erase_line_agrees_with_the_wire_flag_for_every_row(
    db: EpromDatabase, rows: list[tuple[dict, dict, dict]]
) -> None:
    for ic, mapped, spec in rows:
        wire = db.convert_to_programmer(mapped)
        flag_set = bool(wire["flags"] & FLAG_CAN_ERASE)
        assert (spec.get("can_erase_str") == CAN_ERASE_YES) is flag_set, ic[
            "part_number"
        ]


def test_flash4_rows_say_erase_is_not_supported(
    db: EpromDatabase, rows: list[tuple[dict, dict, dict]]
) -> None:
    flash4 = [
        s
        for _, m, s in rows
        if flash4_erase_gate.is_flash4(db.convert_to_programmer(m))
    ]
    assert len(flash4) == 27
    assert all(s["can_erase_str"] == CAN_ERASE_NOT_SUPPORTED for s in flash4)


def test_uv_rows_say_uv_erase_only(rows: list[tuple[dict, dict, dict]]) -> None:
    uv = [s for ic, _, s in rows if ic["electrical"]["type"] == "UV-EPROM"]
    assert len(uv) == 32 + 163 + 106
    assert all(s["can_erase_str"] == CAN_ERASE_UV_ONLY for s in uv)


@pytest.mark.parametrize(
    ("electrical_type", "algorithm", "expected"),
    [
        ("EEPROM", 0x0D, True),
        ("Flash/EEPROM", 0x06, True),
        ("Flash/EEPROM", flash4_erase_gate.FLASH4_PROTOCOL_ID, False),
        ("UV-EPROM", 0x07, False),
        ("SRAM", 0x0E, False),
        ("", 0x0D, False),
        (None, 0x0D, False),
    ],
)
def test_erase_accepted(
    electrical_type: object, algorithm: object, expected: bool
) -> None:
    assert erase_accepted(electrical_type, algorithm) is expected


@pytest.mark.parametrize("chip", ["AT28C16", "X88C64P"])
def test_not_supported_erasable_chip_does_not_say_yes(
    db: EpromDatabase, chip: str
) -> None:
    eprom = db.get_eprom(chip)
    raw, manufacturer = db.get_eprom_config(chip)
    assert eprom is not None and raw is not None
    assert raw["support_status"] != "supported"
    data = EpromConsolePresenter(db).prepare_detailed_eprom_data(
        chip, eprom, None, raw, manufacturer
    )
    assert data is not None
    assert data["can_erase_str"] == CAN_ERASE_NOT_SUPPORTED


def test_supported_erasable_chip_says_yes(db: EpromDatabase) -> None:
    eprom = db.get_eprom("AT28C256")
    raw, manufacturer = db.get_eprom_config("AT28C256")
    data = EpromConsolePresenter(db).prepare_detailed_eprom_data(
        "AT28C256", eprom, None, raw, manufacturer
    )
    assert data is not None
    assert data["can_erase_str"] == CAN_ERASE_YES
