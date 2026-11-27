"""page_size is emitted from infoic for every row, with no per-part exception.

Reads the generated firestarter/data/chip_database.json directly. The rule:
`programming.page_size` equals the row's `infoic_page_size_raw`, and is omitted
only when infoic gives no page size (0). See tools/DECODE-NOTES.md section 13.2.
"""

import json
from pathlib import Path

_FA_DIR = Path(__file__).parent.parent
_DB_FILE = _FA_DIR / "firestarter" / "data" / "chip_database.json"
_EXTRA_CHIPS_FILE = _FA_DIR / "tools" / "extra_chips.json"


def _generated_rows():
    db = json.loads(_DB_FILE.read_text())
    extra = json.loads(_EXTRA_CHIPS_FILE.read_text())
    extra_keys = {
        (mfr, chip["part_number"])
        for mfr, chips in (extra.items() if isinstance(extra, dict) else [])
        for chip in chips
    }
    for mfr, chips in db.items():
        for chip in chips:
            if (mfr, chip["part_number"]) not in extra_keys:
                yield mfr, chip


def test_page_size_equals_infoic_for_every_generated_row() -> None:
    checked = 0
    for mfr, chip in _generated_rows():
        prog = chip["programming"]
        raw = prog["infoic_page_size_raw"]
        assert prog.get("page_size", 0) == raw, f"{mfr}/{chip['part_number']}"
        checked += 1
    assert checked > 500


def test_every_page_size_is_a_power_of_two_in_range() -> None:
    db = json.loads(_DB_FILE.read_text())
    for mfr, chips in db.items():
        for chip in chips:
            size = chip["programming"].get("page_size")
            if size is not None:
                assert 1 <= size <= 512 and size & (size - 1) == 0, (
                    f"{mfr}/{chip['part_number']}: {size}"
                )
