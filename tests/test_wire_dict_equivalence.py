"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Wire-dict regression gate: what `convert_to_programmer` sends to the firmware
for every row of the shipped database.

Defect class this closes: a decode or `_map_data` change that silently alters
what reaches the firmware over the wire -- a wrong `vpp_mv`, `pulse-delay`,
`bus-config`, `page-size` or `flags` value energizes a rail against real
silicon, or silently changes timing. `tools/check_dispatch.py` only checks
that `vpp_mv` is present; this module compares the values of every row
against `tests/golden/wire_dict.json`.

Every row is mapped from its own record (`map_chip_record`), never looked up
again by name: about 100 rows share a name or alias with an earlier row, and
a name lookup would encode the earlier row in their place.

A legitimate wire change re-records the golden deliberately:

    python tests/test_wire_dict_equivalence.py

and the commit message says which chips and which keys moved.
"""

import copy
import json
from pathlib import Path

from firestarter.database import EpromDatabase

_HERE = Path(__file__).resolve().parent
_GOLDEN = _HERE / "golden" / "wire_dict.json"

_EXPECTED_WIRE_KEYS = {
    "algorithm",
    "bus-config",
    "chip-id",
    "flags",
    "memory-size",
    "page-size",
    "pin-count",
    "pulse-delay",
    "vpp_mv",
}

# skip_local_override=True: a ~/.firestarter override would leak a spurious
# row into this capture.
_REAL_DB = EpromDatabase(skip_local_override=True)


def _capture_wire_dicts(db: EpromDatabase) -> dict[str, dict]:
    """Encode every database row, keyed `f"{mfg}|{pn}|{i}"`.

    The key carries the row index because part numbers repeat across and
    within manufacturers; a `pn`-only key would silently drop rows.
    """
    records: dict[str, dict] = {}
    for mfg, chips in db.proms.items():
        for i, chip in enumerate(chips):
            pn = chip.get("part_number", "")
            mapped = db.map_chip_record(chip, mfg)
            records[f"{mfg}|{pn}|{i}"] = db.convert_to_programmer(mapped)
    return records


def _describe_record_diff(recorded: dict, live: dict) -> str:
    """Explain a wire-dict mismatch by naming added, removed and
    value-changed record keys SEPARATELY -- for a changed record, name the
    differing wire keys rather than dumping both dicts.
    """
    rec_keys = set(recorded)
    live_keys = set(live)
    added = sorted(live_keys - rec_keys)
    removed = sorted(rec_keys - live_keys)
    changed = {}
    for key in sorted(rec_keys & live_keys):
        rec_wire = recorded[key]
        live_wire = live[key]
        if rec_wire != live_wire:
            wire_keys = set(rec_wire) | set(live_wire)
            differing = sorted(
                k for k in wire_keys if rec_wire.get(k) != live_wire.get(k)
            )
            changed[key] = differing
    parts = []
    if added:
        parts.append(f"added={added}")
    if removed:
        parts.append(f"removed={removed}")
    if changed:
        parts.append(f"changed={changed}")
    return "; ".join(parts) if parts else "(no difference detected)"


def test_golden_file_exists() -> None:
    assert _GOLDEN.is_file(), (
        f"wire-dict golden missing at {_GOLDEN} -- re-record it with "
        "`python tests/test_wire_dict_equivalence.py`"
    )


def test_live_capture_matches_golden() -> None:
    recorded = json.loads(_GOLDEN.read_text(encoding="utf-8"))["records"]
    live = _capture_wire_dicts(_REAL_DB)

    assert len(live) == sum(len(chips) for chips in _REAL_DB.proms.values())
    assert live == recorded, (
        f"the host->wire contract moved: {_describe_record_diff(recorded, live)}"
    )


def test_every_row_is_encoded_from_its_own_record() -> None:
    """Rows that share a name with an earlier row still carry their own
    values: at least one of them differs on the wire from the row a name
    lookup returns, so a capture that looked rows up by name would fail
    `test_live_capture_matches_golden`."""
    shadowed_and_different = []
    for mfg, chips in _REAL_DB.proms.items():
        for chip in chips:
            own = _REAL_DB.convert_to_programmer(_REAL_DB.map_chip_record(chip, mfg))
            by_name = _REAL_DB.get_eprom(chip.get("part_number", ""))
            if by_name is not None and _REAL_DB.convert_to_programmer(by_name) != own:
                shadowed_and_different.append(f"{mfg}|{chip.get('part_number')}")
    assert shadowed_and_different, (
        "no row differs from its by-name lookup any more; this test no longer "
        "proves the capture is per-row"
    )


def test_wire_key_union_is_exactly_nine_keys() -> None:
    live = _capture_wire_dicts(_REAL_DB)
    keys: set[str] = set()
    for wire in live.values():
        keys |= set(wire)
    added = sorted(keys - _EXPECTED_WIRE_KEYS)
    removed = sorted(_EXPECTED_WIRE_KEYS - keys)
    assert keys == _EXPECTED_WIRE_KEYS, (
        f"wire-key union changed; added={added}; removed={removed}"
    )


def test_vcc_and_vpp_volts_never_cross_the_wire() -> None:
    live = _capture_wire_dicts(_REAL_DB)
    offenders_vcc = [key for key, wire in live.items() if "vcc" in wire]
    offenders_vpp_volts = [key for key, wire in live.items() if "vpp_volts" in wire]
    assert not offenders_vcc, (
        "'vcc' appeared on the wire for "
        f"{offenders_vcc} -- vcc must stay inert on the host->wire seam"
    )
    assert not offenders_vpp_volts, (
        "'vpp_volts' appeared on the wire for "
        f"{offenders_vpp_volts} -- only vpp_mv may cross the seam"
    )


def test_describe_record_diff_is_non_vacuous() -> None:
    recorded = json.loads(_GOLDEN.read_text(encoding="utf-8"))["records"]
    mutated = copy.deepcopy(recorded)
    some_key = next(iter(sorted(mutated)))
    mutated[some_key]["pulse-delay"] = mutated[some_key].get("pulse-delay", 0) + 1

    diff = _describe_record_diff(recorded, mutated)

    assert some_key in diff
    assert "pulse-delay" in diff


if __name__ == "__main__":
    _GOLDEN.write_text(
        json.dumps({"records": _capture_wire_dicts(_REAL_DB)}, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {_GOLDEN}")
