"""The shipped database carries `protect_on_after` and `protect_off_before` on
every row except two TEXAS INSTRUMENTS UV-EPROM rows."""

from __future__ import annotations

import json
from pathlib import Path

_DB_FILE = Path(__file__).parent.parent / "firestarter" / "data" / "chip_database.json"
# The exact algorithm id of the two TEXAS INSTRUMENTS rows missing both
# protect_* keys (UV-EPROM, 0x0B).
_ALGORITHM_UV_EPROM_0X0B = 11


def test_only_the_two_ti_uv_eprom_rows_lack_a_protect_field() -> None:
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    rows = [(mfr, chip) for mfr, chips in db.items() for chip in chips]

    missing = [
        (mfr, chip["part_number"])
        for mfr, chip in rows
        if "protect_on_after" not in chip["programming"]
        or "protect_off_before" not in chip["programming"]
    ]
    assert len(missing) == 2, f"Expected exactly 2 rows missing a key, found: {missing}"
    assert set(missing) == {
        ("TEXAS INSTRUMENTS", "2516"),
        ("TEXAS INSTRUMENTS", "2532"),
    }, f"Expected the two TEXAS INSTRUMENTS rows; measured: {missing}"
    for mfr, part_number in missing:
        chip = next(c for m, c in rows if m == mfr and c["part_number"] == part_number)
        assert chip["programming"]["algorithm"] == _ALGORITHM_UV_EPROM_0X0B
