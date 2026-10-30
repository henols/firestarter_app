"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 140 Plan 03 -- TABLE-05 (D-12)

Requirements: TABLE-05

Defect class this closes: a new chip_database.json field entering through
the generator (tools/build_db.py's `chip_entry` construction, or the
VAR-05/D-10 tools/extra_chips.json supplement it merges in) or through a
subset of chips, invisible to a names-only assertion or one that can be
silenced by regenerating tools/baseline/chip_database.baseline.json.

Coverage:
  1. test_top_level_field_inventory_matches -- live top-level per-key
     occurrence counts equal the frozen golden exactly; on mismatch the
     message names added, removed and count-changed keys separately.
  2. test_programming_field_inventory_matches -- same, for the nested
     `programming` object.
  3. test_electrical_field_inventory_matches -- same, for the nested
     `electrical` object.
  4. test_27c_protocol_chip_counts_match -- programming.algorithm counts
     for the three 27C protocols (7, 8, 11) equal the golden's
     `protocol_chip_counts`.
  5. test_inventory_is_non_vacuous -- the manufacturer and chip totals
     (both sourced from the golden's own `totals`, never hardcoded a second
     time), chip count > 0, every recorded per-key count >= 1, and the
     loaded database is a non-empty mapping.

The environment seam FIRESTARTER_CHIP_DB_JSON overrides the database target.
It binds at import (module-level Path resolution below), so it must be set
in a child process environment, never monkeypatched.
"""

import json
import os
from collections import Counter
from pathlib import Path
from typing import NamedTuple

_HERE = Path(__file__).resolve().parent
_APP_ROOT = _HERE.parent
_DB_REL = "firestarter/data/chip_database.json"
_GOLDEN = _HERE / "golden" / "chip_database_field_inventory.json"
_DB_PATH = Path(os.environ.get("FIRESTARTER_CHIP_DB_JSON", str(_APP_ROOT / _DB_REL)))


class _WalkCounts(NamedTuple):
    """Two-level {manufacturer: [chip, ...]} scan result."""

    manufacturers: int
    chips: int
    top: "Counter[str]"
    programming: "Counter[str]"
    electrical: "Counter[str]"
    algorithm: "Counter[int]"


def _walk(db: object) -> _WalkCounts:
    """Two-level traversal over {manufacturer: [chip, ...]}.

    A top-level `for key in db` enumerates MANUFACTURERS, not chips, and
    makes every downstream assertion pass vacuously (T-140-11) -- this
    walk descends into each manufacturer's chip list before counting
    anything. It must raise, never silently return empty counters, if the
    loaded object is not a mapping of manufacturer name to a list of chip
    objects.
    """
    if not isinstance(db, dict):
        raise TypeError(
            "chip_database.json must be a top-level dict of "
            f"{{manufacturer: [chip, ...]}}, got {type(db).__name__}"
        )
    manufacturers = 0
    chips = 0
    top: Counter[str] = Counter()
    programming: Counter[str] = Counter()
    electrical: Counter[str] = Counter()
    algorithm: Counter[int] = Counter()
    for mfr_name, chip_list in db.items():
        if not isinstance(chip_list, list):
            raise TypeError(
                f"manufacturer {mfr_name!r} must map to a list of chip "
                f"objects, got {type(chip_list).__name__} -- a top-level "
                "`for key in db` (T-140-11) must never be tolerated silently"
            )
        manufacturers += 1
        for chip in chip_list:
            if not isinstance(chip, dict):
                raise TypeError(
                    f"chip entry under {mfr_name!r} must be a dict, got "
                    f"{type(chip).__name__}"
                )
            chips += 1
            top.update(chip.keys())
            programming.update(chip["programming"].keys())
            electrical.update(chip["electrical"].keys())
            algorithm[chip["programming"]["algorithm"]] += 1
    return _WalkCounts(manufacturers, chips, top, programming, electrical, algorithm)


def _load_db() -> object:
    return json.loads(_DB_PATH.read_text(encoding="utf-8"))


def _load_golden() -> dict:
    return json.loads(_GOLDEN.read_text(encoding="utf-8"))


def _describe_counter_diff(recorded: dict, live: dict) -> str:
    """Explain a level-inventory mismatch by naming added, removed and
    count-changed keys SEPARATELY, each with its counts, rather than a bare
    "dicts differ"."""
    rec_keys = set(recorded)
    live_keys = set(live)
    added = {k: live[k] for k in sorted(live_keys - rec_keys)}
    removed = {k: recorded[k] for k in sorted(rec_keys - live_keys)}
    changed = {
        k: {"recorded": recorded[k], "live": live[k]}
        for k in sorted(rec_keys & live_keys)
        if recorded[k] != live[k]
    }
    parts = []
    if added:
        parts.append(f"added={added}")
    if removed:
        parts.append(f"removed={removed}")
    if changed:
        parts.append(f"count_changed={changed}")
    return "; ".join(parts) if parts else "(no difference detected)"


def test_top_level_field_inventory_matches() -> None:
    """TABLE-05: chip_database.json's top-level field inventory (names AND
    per-key occurrence counts) is frozen. A count change means a field was
    added to, or removed from, some subset of chips -- exactly the class of
    schema drift a names-only assertion cannot see."""
    golden = _load_golden()
    live = _walk(_load_db()).top
    recorded = golden["levels"]["top"]
    assert dict(live) == recorded, (
        "chip_database.json top-level field inventory diverged from the "
        f"frozen golden -- {_describe_counter_diff(recorded, dict(live))}"
    )


def test_programming_field_inventory_matches() -> None:
    golden = _load_golden()
    live = _walk(_load_db()).programming
    recorded = golden["levels"]["programming"]
    assert dict(live) == recorded, (
        "chip_database.json programming-level field inventory diverged "
        f"from the frozen golden -- "
        f"{_describe_counter_diff(recorded, dict(live))}"
    )


def test_electrical_field_inventory_matches() -> None:
    golden = _load_golden()
    live = _walk(_load_db()).electrical
    recorded = golden["levels"]["electrical"]
    assert dict(live) == recorded, (
        "chip_database.json electrical-level field inventory diverged from "
        f"the frozen golden -- {_describe_counter_diff(recorded, dict(live))}"
    )


def test_27c_protocol_chip_counts_match() -> None:
    """TABLE-05 / F-140-04: programming.algorithm chip counts for the three
    27C protocols are frozen. A count change invalidates every per-protocol
    figure this milestone cites -- F-140-04's pulse distribution and the
    D-09 citation scope clauses both assume these counts."""
    golden = _load_golden()
    live_algorithm = _walk(_load_db()).algorithm
    recorded = {int(k): v for k, v in golden["protocol_chip_counts"].items()}
    live = {proto: live_algorithm[proto] for proto in recorded}
    assert live == recorded, (
        "27C protocol chip counts (programming.algorithm in {7, 8, 11}) "
        f"diverged from the frozen golden -- recorded={recorded} live={live}. "
        "A count change invalidates every per-protocol figure this "
        "milestone cites, including F-140-04's pulse distribution and the "
        "D-09 citation scope clauses."
    )


def test_inventory_is_non_vacuous() -> None:
    """Non-vacuity floor (D-15 / T-140-11): the scan must have actually
    walked something. Every message reports the scanned counts so a
    zero-scan (a vacuous top-level `for key in db`, or a `{}` target) can
    never read as a pass."""
    golden = _load_golden()
    db = _load_db()
    assert isinstance(db, dict) and len(db) > 0, (
        f"non-vacuous guard: chip_database.json must load as a non-empty "
        f"dict, got {type(db).__name__!r}"
    )
    counts = _walk(db)
    expected_manufacturers = golden["totals"]["manufacturers"]
    expected_chips = golden["totals"]["chips"]
    assert counts.manufacturers == expected_manufacturers, (
        f"non-vacuous guard: expected {expected_manufacturers} "
        f"manufacturers (frozen), scanned {counts.manufacturers}"
    )
    assert counts.chips == expected_chips and counts.chips > 0, (
        f"non-vacuous guard: expected {expected_chips} chips (frozen) and "
        f"> 0, scanned {counts.chips}"
    )
    for level_name, counter in (
        ("top", counts.top),
        ("programming", counts.programming),
        ("electrical", counts.electrical),
    ):
        for key, count in counter.items():
            assert count >= 1, (
                f"non-vacuous guard: {level_name}.{key} recorded a scanned "
                f"count of {count}, expected >= 1"
            )
