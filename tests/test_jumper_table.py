"""Tests for the per-pin-map jumper table (jumper_table.py) and the `info` jumper blocks.

Every test calls production code with shipped data (`pinouts.json`, `chip_database.json`) or with a
synthetic pin map. No test reads source text. No test reads a file outside this repository: the
Rev 0/1 values below were transcribed by hand from `firestarter_fw/document/rurp_schematics_rev1.pdf`.
"""

from __future__ import annotations

from typing import Any

import pytest

from firestarter import jumper_table
from firestarter.database import EpromDatabase
from firestarter.ic_layout import EpromSpecBuilder
from firestarter.jumper_table import (
    JUMPER_TABLE,
    NO_ENTRY_FORMAT,
    PROBE_PENDING_TEXT,
    REV01_KEY,
    REV20_KEY,
    REV22_KEY,
    VPP_UNREACHABLE_TEXT,
    Jp1,
    Jp2,
    Jp3,
    Jp4Rev20,
    Jp4Rev22,
    VppLanding,
    derive_pin1_signal,
    derive_vpp_landing,
)

ROW_COUNT = 746
PIN_MAP_COUNT = 16


@pytest.fixture(scope="module")
def db() -> EpromDatabase:
    return EpromDatabase(skip_local_override=True)


@pytest.fixture(scope="module")
def spec_builder(db: EpromDatabase) -> EpromSpecBuilder:
    return EpromSpecBuilder(db)


@pytest.fixture(scope="module")
def rendered(
    db: EpromDatabase, spec_builder: EpromSpecBuilder
) -> list[tuple[str, dict]]:
    """(pin map, build_specifications output) for every row of the shipped database."""
    rows = []
    for manufacturer, ics in db.proms.items():
        for ic in ics:
            mapped = db.map_chip_record(ic, manufacturer)
            spec = spec_builder.build_specifications(
                mapped, electrical_type=mapped.get("electrical-type")
            )
            assert spec is not None
            rows.append((mapped["pin-map"], spec))
    return rows


def _pin_count(pin_map_key: str) -> int:
    return int(pin_map_key.split("_")[0].removeprefix("DIP"))


def _block_text(block: dict[str, Any]) -> list[str]:
    text = list(block["notes"])
    for jumper in block["jumpers"].values():
        text.extend(jumper.values())
    return text


# --- JMP-02: every pin map has an entry --------------------------------------------------------


def test_every_shipped_pin_map_has_a_table_entry(db: EpromDatabase) -> None:
    assert len(db.pin_maps) == PIN_MAP_COUNT
    assert set(db.pin_maps) == set(JUMPER_TABLE)


def test_every_entry_renders_all_three_revision_blocks() -> None:
    for key, entry in JUMPER_TABLE.items():
        blocks = jumper_table.render_blocks(entry)
        assert list(blocks) == [REV01_KEY, REV20_KEY, REV22_KEY], key
        assert set(blocks[REV01_KEY]["jumpers"]) == {"jp1", "jp2", "jp3"}, key
        assert set(blocks[REV20_KEY]["jumpers"]) == {"jp4"}, key
        assert set(blocks[REV22_KEY]["jumpers"]) == {"jp4"}, key


# --- JMP-01: the recorded facts agree with pinouts.json ---------------------------------------


def test_recorded_vpp_landing_matches_pinouts(db: EpromDatabase) -> None:
    for key, pin_map in db.pin_maps.items():
        derived = derive_vpp_landing(pin_map["pins"], _pin_count(key))
        assert JUMPER_TABLE[key].vpp_lands is derived, key


def test_recorded_pin1_signal_matches_pinouts(db: EpromDatabase) -> None:
    for key, pin_map in db.pin_maps.items():
        assert JUMPER_TABLE[key].pin1_signal == derive_pin1_signal(pin_map["pins"]), key


@pytest.mark.parametrize(
    ("pins", "pin_count", "expected"),
    [
        ({"oe-pin": [22]}, 28, VppLanding.NONE),
        ({"vpp-pin": [22], "oe-pin": [22]}, 28, VppLanding.OE),
        ({"vpp-pin": [1], "oe-pin": [24]}, 32, VppLanding.SOCKET_1),
        ({"vpp-pin": [1], "oe-pin": [22]}, 28, VppLanding.SOCKET_3),
        ({"vpp-pin": [21], "oe-pin": [20]}, 24, VppLanding.SOCKET_25),
        ({"vpp-pin": [21]}, 24, VppLanding.SOCKET_25),
        # VPP on a pin that reaches a socket pin the table has no class for.
        ({"vpp-pin": [2], "oe-pin": [22]}, 28, None),
        # VPP on the OE line of a different pin map: not a socket bus line.
        ({"vpp-pin": [22], "oe-pin": [20]}, 28, None),
        # VPP on a pin with no shield bus line (GND).
        ({"vpp-pin": [14], "oe-pin": [22]}, 28, None),
        ({"vpp-pin": [1]}, 40, None),
    ],
)
def test_derive_vpp_landing_each_branch(
    pins: dict, pin_count: int, expected: VppLanding | None
) -> None:
    assert derive_vpp_landing(pins, pin_count) is expected


def test_a_pin_map_that_moves_vpp_no_longer_matches_its_entry(
    db: EpromDatabase,
) -> None:
    moved = dict(db.pin_maps["DIP28_2764"]["pins"])
    moved["vpp-pin"] = moved["oe-pin"]
    assert derive_vpp_landing(moved, 28) is not JUMPER_TABLE["DIP28_2764"].vpp_lands


@pytest.mark.parametrize(
    ("pins", "expected"),
    [
        ({"vpp-pin": [1], "address-bus-pins": [10, 9]}, "VPP"),
        ({"nc-pin": [1, 26]}, "NC"),
        ({"address-bus-pins": [12, 11, 1]}, "A2"),
        ({"address-bus-pins": [12, 11]}, "UNASSIGNED"),
        ({}, "UNASSIGNED"),
    ],
)
def test_derive_pin1_signal_each_branch(pins: dict, expected: str) -> None:
    assert derive_pin1_signal(pins) == expected


# --- Fail closed on an unknown pin map (D-E) ------------------------------------------------


def test_lookup_returns_none_for_an_unknown_or_absent_pin_map() -> None:
    assert jumper_table.lookup("DIP28_NOT_IN_TABLE") is None
    assert jumper_table.lookup(None) is None
    assert jumper_table.lookup("") is None


def test_an_unknown_pin_map_shows_no_jumper_setting(
    db: EpromDatabase, spec_builder: EpromSpecBuilder
) -> None:
    eprom = dict(db.get_eprom("AM27C64"))
    eprom["pin-map"] = "DIP28_USER_MAP"
    spec = spec_builder.build_specifications(eprom, electrical_type="UV-EPROM")
    assert spec is not None
    assert spec["jumpers"] == {}
    assert spec["jumper_note"] == NO_ENTRY_FORMAT.format(pin_map="DIP28_USER_MAP")


def test_a_known_pin_map_has_no_fail_closed_note(
    db: EpromDatabase, spec_builder: EpromSpecBuilder
) -> None:
    spec = spec_builder.build_specifications(
        db.get_eprom("AM27C64"), electrical_type="UV-EPROM"
    )
    assert spec is not None
    assert "jumper_note" not in spec
    assert list(spec["jumpers"]) == [REV01_KEY, REV20_KEY, REV22_KEY]


# --- JMP-03 / JMP-04: rendered output for every row ------------------------------------------


def test_every_row_gets_the_three_revision_blocks(
    rendered: list[tuple[str, dict]],
) -> None:
    assert len(rendered) == ROW_COUNT
    for pin_map, spec in rendered:
        assert list(spec["jumpers"]) == [REV01_KEY, REV20_KEY, REV22_KEY], pin_map


def test_no_row_shows_the_two_state_silkscreen_clause_or_jp3_labels_on_jp4(
    rendered: list[tuple[str, dict]],
) -> None:
    for pin_map, spec in rendered:
        for block in spec["jumpers"].values():
            for text in _block_text(block):
                assert "Open for 32 pin" not in text, pin_map
                assert "Closed for 28 pin" not in text, pin_map
        for key in (REV20_KEY, REV22_KEY):
            jp4 = spec["jumpers"][key]["jumpers"]["jp4"]
            for text in jp4.values():
                assert "28pin" not in text, pin_map
                assert "32pin" not in text, pin_map
            assert jp4["choices"] == '"Only for ROMs with VPP on P1"', pin_map


# --- JMP-05: the known wrong outputs are corrected -------------------------------------------


def test_vpp_on_oe_maps_get_no_jp3_vpp_position_and_no_closed_jp4(
    rendered: list[tuple[str, dict]],
) -> None:
    rows = [s for p, s in rendered if p in ("DIP28_27512", "DIP32_27C801")]
    assert len(rows) == 45 + 8
    for spec in rows:
        rev01 = spec["jumpers"][REV01_KEY]["jumpers"]
        assert rev01["jp3"]["selected_label"] == Jp3.NO_JUMPER.value
        rev20 = spec["jumpers"][REV20_KEY]["jumpers"]
        assert rev20["jp4"]["selected_label"] == Jp4Rev20.OPEN.value


def test_32pin_vpp_on_pin1_maps_get_jp4_open_and_no_jumper(
    rendered: list[tuple[str, dict]],
) -> None:
    rows = [s for p, s in rendered if p in ("DIP32_STD", "DIP32_27C020")]
    assert len(rows) == 70 + 88
    for spec in rows:
        rev20 = spec["jumpers"][REV20_KEY]["jumpers"]["jp4"]
        rev22 = spec["jumpers"][REV22_KEY]["jumpers"]["jp4"]
        assert rev20["selected_label"] == Jp4Rev20.OPEN.value
        assert rev22["selected_label"] == Jp4Rev22.NO_JUMPER.value


# --- JMP-06: VPP on socket pin 25 ------------------------------------------------------------


def test_2716_rows_state_vpp_unreachable_before_rev_2_2(
    rendered: list[tuple[str, dict]],
) -> None:
    rows = [s for p, s in rendered if p == "DIP24_2716"]
    assert len(rows) == 15
    for spec in rows:
        assert VPP_UNREACHABLE_TEXT in spec["jumpers"][REV01_KEY]["notes"]
        assert VPP_UNREACHABLE_TEXT in spec["jumpers"][REV20_KEY]["notes"]
        rev22 = spec["jumpers"][REV22_KEY]
        assert VPP_UNREACHABLE_TEXT not in rev22["notes"]
        assert rev22["jumpers"]["jp4"]["selected_label"] == Jp4Rev22.POLE_24.value


def test_only_socket_25_maps_state_vpp_unreachable(
    rendered: list[tuple[str, dict]],
) -> None:
    with_note = {
        p
        for p, s in rendered
        if any(VPP_UNREACHABLE_TEXT in b["notes"] for b in s["jumpers"].values())
    }
    assert with_note == {"DIP24_2716", "DIP24_2532"}


# --- JMP-07: "does not matter" only where the table says so ----------------------------------


def test_does_not_matter_never_appears_on_rev_2_2(
    rendered: list[tuple[str, dict]],
) -> None:
    for pin_map, spec in rendered:
        for text in _block_text(spec["jumpers"][REV22_KEY]):
            assert "does not matter" not in text, pin_map


def test_rev_2_0_does_not_matter_only_on_24pin_rows(
    rendered: list[tuple[str, dict]],
) -> None:
    rows = [
        p
        for p, s in rendered
        if s["jumpers"][REV20_KEY]["jumpers"]["jp4"]["selected_label"]
        == Jp4Rev20.DOES_NOT_MATTER.value
    ]
    assert len(rows) == 15 + 1 + 16 + 19 + 7
    assert {p.split("_")[0] for p in rows} == {"DIP24"}


def test_probe_pending_rows_are_marked(rendered: list[tuple[str, dict]]) -> None:
    marked = [
        p for p, s in rendered if PROBE_PENDING_TEXT in s["jumpers"][REV20_KEY]["notes"]
    ]
    assert len(marked) == 58 + 58 + 67
    assert set(marked) == {k for k, e in JUMPER_TABLE.items() if e.probe_pending}
    for pin_map, spec in rendered:
        assert PROBE_PENDING_TEXT not in spec["jumpers"][REV01_KEY]["notes"], pin_map
        assert PROBE_PENDING_TEXT not in spec["jumpers"][REV22_KEY]["notes"], pin_map


# --- JMP-08: Rev 0/1 matches rurp_schematics_rev1.pdf ----------------------------------------
#
# Transcribed from firestarter_fw/document/rurp_schematics_rev1.pdf (title block "Rev: 0"):
#   JP1 "24pin ROM VCC": common = socket 28; pin 1 = A13 net; pin 3 = +5 V.
#   JP2 ">=SST39SF020 & 28C512 need A17": common = socket 30; pin 1 = +5 V; pin 3 = A17 net.
#   JP3 "W27C010/AT27C010 needs p1 VPE/VPP (32 pin)": common = Q8 collector;
#       pin 1 = socket 3; pin 3 = socket 1.
# A 24-pin chip has VCC at socket 28, and a 28-pin chip has VCC at socket 30.

EXPECTED_REV01 = {
    "DIP24_2716": (Jp1.VCC, Jp2.DOES_NOT_MATTER, Jp3.DOES_NOT_MATTER),
    "DIP24_2532": (Jp1.VCC, Jp2.DOES_NOT_MATTER, Jp3.DOES_NOT_MATTER),
    "DIP24_2732": (Jp1.VCC, Jp2.DOES_NOT_MATTER, Jp3.NO_JUMPER),
    "DIP24_2816": (Jp1.VCC, Jp2.DOES_NOT_MATTER, Jp3.NO_JUMPER),
    "DIP24_6116": (Jp1.VCC, Jp2.DOES_NOT_MATTER, Jp3.NO_JUMPER),
    "DIP28_2764": (Jp1.A13, Jp2.VCC, Jp3.PIN28),
    "DIP28_27256": (Jp1.A13, Jp2.VCC, Jp3.PIN28),
    "DIP28_27512": (Jp1.A13, Jp2.VCC, Jp3.NO_JUMPER),
    "DIP28_28C256": (Jp1.A13, Jp2.VCC, Jp3.NO_JUMPER),
    "DIP28_28C64": (Jp1.A13, Jp2.VCC, Jp3.NO_JUMPER),
    "DIP28_JEDEC_SRAM_8K": (Jp1.A13, Jp2.VCC, Jp3.NO_JUMPER),
    "DIP32_STD": (Jp1.A13, Jp2.A17, Jp3.PIN32),
    "DIP32_27C020": (Jp1.A13, Jp2.A17, Jp3.PIN32),
    "DIP32_27C801": (Jp1.A13, Jp2.A17, Jp3.NO_JUMPER),
    "DIP32_SST39SF040": (Jp1.A13, Jp2.A17, Jp3.NO_JUMPER),
    "DIP32_28C512_EEPROM": (Jp1.A13, Jp2.A17, Jp3.NO_JUMPER),
}


def test_rev01_values_match_the_rev1_schematic() -> None:
    assert set(EXPECTED_REV01) == set(JUMPER_TABLE)
    for key, (jp1, jp2, jp3) in EXPECTED_REV01.items():
        entry = JUMPER_TABLE[key]
        assert (entry.jp1, entry.jp2, entry.jp3) == (jp1, jp2, jp3), key


def test_jp3_selects_a_vpp_position_only_where_vpp_is_on_chip_pin_1() -> None:
    for key, entry in JUMPER_TABLE.items():
        if entry.jp3 is Jp3.PIN28:
            assert entry.vpp_lands is VppLanding.SOCKET_3, key
        elif entry.jp3 is Jp3.PIN32:
            assert entry.vpp_lands is VppLanding.SOCKET_1, key
        else:
            assert entry.vpp_lands not in (VppLanding.SOCKET_1, VppLanding.SOCKET_3), (
                key
            )


def test_jp5_note_only_where_pin_1_carries_a19() -> None:
    with_note = {
        k for k, e in JUMPER_TABLE.items() if jumper_table.Note.JP5_A19 in e.rev20_notes
    }
    assert with_note == {k for k, e in JUMPER_TABLE.items() if e.pin1_signal == "A19"}
    assert with_note == {"DIP32_27C801"}


# --- Rev 2.2/2.3 JP4 glyph --------------------------------------------------------------------
#
# The three pads make an L (shield-rev2.2-jp4-jp5-jp6-jp9.jpg, with the board title upright): the
# square common pad at the corner, the 28-pin pole to its left and the 24-pin pole below it. "( )" is
# a horizontal cap and the box is a vertical cap.

_CAP_CHARS = set("()┌┐└┘")


def _rev22_lines(setting: Jp4Rev22) -> tuple[str, str]:
    jp4 = jumper_table._rev22_jp4(setting)
    return jp4["display"], jp4["display_below"]


def _bridged_pads(top: str, below: str) -> set[str]:
    """Read the drawing back: return the pads that sit inside a cap."""
    corner = top.index("■")
    left = top.index("●")
    lower = below.index("●")
    assert top.count("●") == 1 and below.count("●") == 1
    assert left < corner
    assert lower == corner, "the 24-pin pad must sit under the common pad"
    bridged = set()
    if top[left - 1] == "(" and top[corner + 1] == ")":
        bridged |= {"corner", "left"}
    if (top[corner - 1], top[corner + 1]) == ("┌", "┐") and (
        below[lower - 1],
        below[lower + 1],
    ) == ("└", "┘"):
        bridged |= {"corner", "lower"}
    return bridged


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        (Jp4Rev22.NO_JUMPER, set()),
        (Jp4Rev22.POLE_28, {"corner", "left"}),
        (Jp4Rev22.POLE_24, {"corner", "lower"}),
    ],
)
def test_rev22_jp4_glyph_caps_exactly_the_selected_pads(
    setting: Jp4Rev22, expected: set[str]
) -> None:
    top, below = _rev22_lines(setting)
    assert _bridged_pads(top, below) == expected
    if not expected:
        assert not _CAP_CHARS & set(top + below)


def test_rev22_jp4_glyph_has_the_width_of_the_other_glyphs() -> None:
    for setting in Jp4Rev22:
        top, below = _rev22_lines(setting)
        assert len(top) == len(jumper_table._THREE_PIN_OFF), setting
        assert below == below.rstrip(), setting


def test_rev22_jp4_prints_the_second_line_under_the_first(
    db: EpromDatabase,
    spec_builder: EpromSpecBuilder,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from firestarter.eprom_info import EpromConsolePresenter

    eprom = db.get_eprom("AM27C256")
    assert eprom is not None
    spec = spec_builder.build_specifications(
        eprom, electrical_type=eprom.get("electrical-type")
    )
    assert spec is not None
    with caplog.at_level("INFO", logger="EpromConsolePresenter"):
        EpromConsolePresenter(db).present_eprom_details({"jumpers": spec["jumpers"]})
    lines = [r.getMessage() for r in caplog.records]
    start = lines.index("Jumper config (Rev 2.2 & 2.3):")
    top, below = lines[start + 1], lines[start + 2]
    assert top.startswith("  JP4: (● ■)")
    assert f"= {Jp4Rev22.POLE_28.value})" in top
    assert below == "          ●"
    assert below.index("●") == top.index("■")
