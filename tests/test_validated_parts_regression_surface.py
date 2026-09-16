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
`dev test` default write region's consequence counted over all 27 rows
whose algorithm is the flash4 protocol id.

Every database read here goes directly to the generated JSON as data, or
through EpromDatabase with the no-local-override convention -- never a
fabricated fixture -- so this module's regression surface is measured, not
transcribed.
"""

from __future__ import annotations

import json
from pathlib import Path

from firestarter.database import EpromDatabase
from firestarter.flash4_erase_gate import FLASH4_PROTOCOL_ID

_FA_DIR = Path(__file__).parent.parent
_DB_FILE = _FA_DIR / "firestarter" / "data" / "chip_database.json"

_DEV_TEST_REGION_LENGTH = 256

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


def _all_flash4_raw_rows() -> list[dict]:
    raw = _load_raw_database()
    rows = []
    for chips in raw.values():
        for row in chips:
            if row.get("programming", {}).get("algorithm") == FLASH4_PROTOCOL_ID:
                rows.append(row)
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


def test_dev_test_region_matrix_over_all_27_protocol_0x05_rows():
    """D-12, as a count rather than a sentence: dev test's 256-byte write
    region at address 0 is a whole number of pages for 25 of the 27
    protocol 0x05 rows and a refused partial page for exactly the
    remaining 2 -- both of which record page size 512 -- and none of the
    three protocol 0x05 validated parts is among the refused two."""
    rows = _all_flash4_raw_rows()
    assert len(rows) == 27

    aligned = []
    refused = []
    for row in rows:
        page_size = row["programming"]["page_size"]
        if _DEV_TEST_REGION_LENGTH % page_size == 0:
            aligned.append(row)
        else:
            refused.append(row)

    assert len(aligned) == 25
    assert len(refused) == 2
    assert all(row["programming"]["page_size"] == 512 for row in refused)

    refused_part_numbers = {row["part_number"] for row in refused}
    for validated_part_number in (
        _W29C020_PART_NUMBER,
        _W29C040_PART_NUMBER,
        _AE29F2008_PART_NUMBER,
    ):
        assert validated_part_number not in refused_part_numbers


def test_module_reads_the_database_with_the_no_local_override_convention():
    """This module's own regression surface must not be able to absorb a
    row from the user's own configuration file -- the same discipline
    every other gate on this path already follows."""
    db = EpromDatabase(skip_local_override=True)
    assert db.get_eprom("W29C020") is not None
