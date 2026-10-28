"""
Tests for `electrical.non_volatile`, the SRAM-class retention field.

infoic.xml has no flag for retention. `tools/build_db.py` derives it from the
protocol of the row: a read/write row is non-volatile, and a RAM-test row is
non-volatile only when the same part also has a read/write row. See
tools/DECODE-NOTES.md §12.
"""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from tools import build_db

_DB = (
    Path(__file__).resolve().parent.parent
    / "firestarter"
    / "data"
    / "chip_database.json"
)


def _rows():
    db = json.loads(_DB.read_text(encoding="utf-8"))
    return [row for rows in db.values() for row in rows]


def test_read_write_row_is_non_volatile():
    for proto in (0x07, 0x0B, 0x0E):
        assert build_db.sram_non_volatile(proto, "DS1225(RW)", set()) is True


def test_ram_test_row_without_a_read_write_row_is_volatile():
    assert build_db.sram_non_volatile(0x28, "61256,62256", {"DS1225"}) is False


def test_ram_test_row_with_a_read_write_row_is_non_volatile():
    parts = {"DS1230AB", "DS1230Y"}
    assert (
        build_db.sram_non_volatile(0x28, "DS1230AB(TEST),DS1230Y(TEST)", parts) is True
    )
    assert build_db.sram_non_volatile(0x29, "DS1245W(TEST3.3V)", {"DS1245W"}) is True


def test_base_part_names_drop_package_and_mode():
    assert build_db._base_part_names(
        "FM1808@DIP28,FM1808B@SOIC28,BQ4011LYMA(RW3.3V)"
    ) == {
        "FM1808",
        "FM1808B",
        "BQ4011LYMA",
    }


def test_collect_reads_only_infoic2plus_type_4_non_test_rows():
    root = ET.fromstring(
        "<root>"
        "<database type='INFOIC2PLUS'><manufacturer name='M'>"
        "<ic name='DS1225(RW)' type='0x4' protocol_id='0x07'/>"
        "<ic name='DS1225(TEST)' type='0x4' protocol_id='0x28'/>"
        "<ic name='62256' type='0x4' protocol_id='0x28'/>"
        "<ic name='27C256' type='0x1' protocol_id='0x07'/>"
        "</manufacturer></database>"
        "<database type='INFOIC'><manufacturer name='M'>"
        "<ic name='OTHER(RW)' type='0x4' protocol_id='0x07'/>"
        "</manufacturer></database>"
        "</root>"
    )
    assert build_db.collect_read_write_sram_parts(root) == {"DS1225"}


def test_every_sram_row_and_only_sram_rows_carry_the_field():
    for row in _rows():
        electrical = row["electrical"]
        if electrical["type"] == "SRAM":
            assert isinstance(electrical.get("non_volatile"), bool), row["part_number"]
        else:
            assert "non_volatile" not in electrical, row["part_number"]


def test_shipped_database_splits_the_two_groups():
    by_name = {}
    for row in _rows():
        if row["electrical"]["type"] == "SRAM":
            by_name.setdefault(row["part_number"], set()).add(
                row["electrical"]["non_volatile"]
            )

    assert by_name["61256,62256"] == {False}
    assert by_name["6116"] == {False}
    assert by_name["DS1225(RW)"] == {True}
    assert by_name["DS1225(TEST)"] == {True}
    assert by_name["FM1808,FM1808B,FM18W08"] == {True}
    # Protocol 0x0E has only read/write rows.
    assert all(
        row["electrical"]["non_volatile"]
        for row in _rows()
        if row["programming"]["algorithm"] == 0x0E
    )
