"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 195 Plan 03 -- criterion 4's regression surface (D-07, D-12), measured
from the shipped database rather than transcribed from the roadmap.

The roadmap names four validated parts: W29C020, W29C040, SST39SF020 and
AE29F2008. Measured against the shipped database, one of the four --
SST39SF020 -- is not a protocol 0x05 part at all: it is algorithm 6
(PROTO_FLASH_NOR_UNLOCK), handled by a different firmware file, so it never
traverses the protocol 0x05 write path and cannot regress from this phase.
This module asserts that correction in code, so a future reader who puts
SST39SF020 back into this phase's blast radius meets a red test rather than
running a bench pass that could not have proved anything.

The remaining three legs measure the rest of criterion 4's real shape: the
page sizes the native suite must cover, the AE29F2008/W29C020 two-name
silicon identity (a whole-row comparison, not a field list), and the
page-alignment gate's verdict on the `dev test` write region of each of the
27 rows whose algorithm is the flash4 protocol id, per write scope.

Every database read here goes directly to the generated JSON as data, or
through EpromDatabase with the no-local-override convention -- never a
fabricated fixture -- so this module's regression surface is measured, not
transcribed.
"""

from __future__ import annotations

import json
from pathlib import Path

from firestarter import page_size_gate
from firestarter.chip_test import OP_WRITE, OP_WRITE_PARTIAL, derive_plan
from firestarter.database import EpromDatabase
from firestarter.exceptions import PageAlignmentError
from firestarter.flash4_erase_gate import FLASH4_PROTOCOL_ID

_FA_DIR = Path(__file__).parent.parent
_DB_FILE = _FA_DIR / "firestarter" / "data" / "chip_database.json"

_W29C020_PART_NUMBER = "W29C020,W29C020C,W29C022"
_W29C040_PART_NUMBER = "W29C040,W29C042"
_AE29F2008_PART_NUMBER = "AE29F2008"
_SST39SF020_PART_NUMBER = "SST39SF020,SST39SF020A"


def _load_raw_database() -> dict:
    with open(_DB_FILE) as f:
        return json.load(f)


def _raw_row(manufacturer: str, part_number: str) -> dict:
    """The shipped row for (manufacturer, part_number), read directly from
    the generated JSON as data -- never through EpromDatabase's mapping --
    so the two-name identity comparison below is over shipped data rather
    than a wire-dict conversion."""
    raw = _load_raw_database()
    for row in raw[manufacturer]:
        if row["part_number"] == part_number:
            return row
    raise AssertionError(
        f"{manufacturer}/{part_number} not found in the shipped database"
    )


def _all_flash4_raw_rows() -> list[tuple[str, dict]]:
    raw = _load_raw_database()
    rows = []
    for manufacturer, chips in raw.items():
        for row in chips:
            if row.get("programming", {}).get("algorithm") == FLASH4_PROTOCOL_ID:
                rows.append((manufacturer, row))
    return rows


def test_sst39sf020_is_not_flash4_and_the_other_three_validated_parts_are():
    sst39sf020 = _raw_row("SST", _SST39SF020_PART_NUMBER)
    assert sst39sf020["programming"]["algorithm"] != FLASH4_PROTOCOL_ID

    for manufacturer, part_number in (
        ("ASD", _AE29F2008_PART_NUMBER),
        ("WINBOND", _W29C020_PART_NUMBER),
        ("WINBOND", _W29C040_PART_NUMBER),
    ):
        row = _raw_row(manufacturer, part_number)
        assert row["programming"]["algorithm"] == FLASH4_PROTOCOL_ID


def test_validated_parts_recorded_page_sizes():
    w29c020 = _raw_row("WINBOND", _W29C020_PART_NUMBER)
    w29c040 = _raw_row("WINBOND", _W29C040_PART_NUMBER)
    ae29f2008 = _raw_row("ASD", _AE29F2008_PART_NUMBER)
    sst39sf020 = _raw_row("SST", _SST39SF020_PART_NUMBER)

    assert w29c020["programming"]["page_size"] == 128
    assert w29c040["programming"]["page_size"] == 256
    assert ae29f2008["programming"]["page_size"] == 128
    assert "page_size" not in sst39sf020["programming"]


def test_ae29f2008_and_w29c020_are_the_same_silicon_under_two_names():
    """The measured form of the ledger's own Notes finding: the two rows
    are equal on every field, including the chip id -- except part_number,
    which is the name itself and is definitionally different -- so a
    second bench pass on AE29F2008 would be redundant, not additional
    evidence. Compared as whole dictionaries rather than a field list, so a
    future divergence on a field nobody thought to name still turns this
    leg red."""
    ae29f2008 = dict(_raw_row("ASD", _AE29F2008_PART_NUMBER))
    w29c020 = dict(_raw_row("WINBOND", _W29C020_PART_NUMBER))

    assert (
        ae29f2008["programming"]["chip_id_value"]
        == w29c020["programming"]["chip_id_value"]
    )

    ae29f2008.pop("part_number")
    w29c020.pop("part_number")
    assert ae29f2008 == w29c020


def _write_step_refusals(write_scope: str, tmp_path: Path) -> dict[str, int]:
    """Run each protocol 0x05 row's `dev test` write region, as `derive_plan`
    decides it, through the pre-connect page-alignment gate that
    `write_eprom` applies. Returns {qualified name: page size} for the rows
    the gate refuses."""
    db = EpromDatabase(skip_local_override=True)
    payload = tmp_path / f"{write_scope}.bin"
    refused = {}
    for manufacturer, row in _all_flash4_raw_rows():
        name = f"{row['part_number'].split(',')[0]}@{manufacturer}"
        programmer_data = db.convert_to_programmer(db.get_eprom(name))
        assert programmer_data["algorithm"] == FLASH4_PROTOCOL_ID, name
        plan = derive_plan(name, db, write_scope=write_scope)
        (write_step,) = [
            step for step in plan.steps if step.op in (OP_WRITE, OP_WRITE_PARTIAL)
        ]
        start, length = write_step.write_region
        payload.write_bytes(b"\xff" * length)
        try:
            page_size_gate.require_page_alignment(
                name, programmer_data, "write", hex(start), str(payload)
            )
        except PageAlignmentError:
            refused[name] = row["programming"]["page_size"]
    return refused


def test_dev_test_full_write_region_passes_the_alignment_gate_on_every_0x05_row(
    tmp_path: Path,
):
    assert len(_all_flash4_raw_rows()) == 27
    assert _write_step_refusals("full", tmp_path) == {}


def test_dev_test_partial_write_region_is_refused_on_exactly_two_0x05_rows(
    tmp_path: Path,
):
    """D-12, as a count rather than a sentence: the partial scope writes 256
    bytes at the top of the device. That is a whole number of pages for 25 of
    the 27 protocol 0x05 rows, and the gate refuses the remaining 2, both with
    page size 512. None of the three protocol 0x05 validated parts is among
    them."""
    refused = _write_step_refusals("partial", tmp_path)
    assert refused == {"AT29BV040@ATMEL": 512, "AT29C040@ATMEL": 512}
