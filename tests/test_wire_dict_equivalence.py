"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 148 Plan 01 -- DATA-03 (D-14, D-06).

Defect class this closes: a decode or `_map_data` change that silently alters
what reaches the firmware over the wire (`convert_to_programmer`'s output) --
a wrong `vpp_mv`, `pulse-delay`, `bus-config`, `page-size` or `flags` value
energizes a rail against real silicon, or silently changes timing. GATE-03
(`tools/check_dispatch.py`'s wire-key assertion) cannot catch this: it only
asserts `vpp_mv` is present and legacy `vpp` is absent for each chip, it does
not compare the *values* against anything. This module is the byte-identity
regression gate that closes that hole across all 746 chips, taken as a
pre-Phase-148 capture (Task 1's `tests/golden/wire_dict_baseline.json`) before
any later plan in this phase touches the schema the wire dict is derived
from.

Coverage:
  1. test_live_capture_matches_golden_plus_the_149_deltas -- Phase 149
     (D-17): the live 746-chip capture equals the committed golden's
     `records` PLUS exactly the 18 named deltas in
     `tests/golden/wire_dict_expected_deltas_149.json` -- the golden itself
     (`wire_dict_baseline.json`) is preserved byte-unchanged and is NEVER
     re-captured to make this or any future phase's change disappear. Four
     assertions, in order: (a) anti-laundering -- the golden's own
     page-size-carrying record set is exactly Phase 148's original two; (b)
     non-vacuity -- every delta key exists in the golden and does not
     already carry page-size; (c) exact count -- len(deltas) == 18, not "at
     least"; (d) golden-plus-deltas equals live, reusing
     `_describe_record_diff` unchanged in the failure message.
  2. test_wire_key_union_is_exactly_nine_keys -- the union of wire keys
     across the live capture is exactly the nine measured keys; the message
     names anything added or removed. Unchanged by Phase 149 -- `page-size`
     was already in this set.
  3. test_vcc_and_vpp_volts_never_cross_the_wire -- D-06's load-bearing
     claim: neither `vcc` nor `vpp_volts` appears in any live wire dict, so a
     VCC decode change cannot alter `write` behaviour.
  4. test_describe_record_diff_is_non_vacuous -- a synthetic mutation to a
     copy of the golden records makes the SAME comparison helper
     (`_describe_record_diff`) that test 1 calls report exactly that one
     record as changed -- proves the gate is capable of failing.
  5. test_golden_file_exists -- the golden file exists; a missing golden is
     a loud, named failure, never a skip. This module deliberately has no
     skip path: `wire_dict_baseline.json` lives inside `tests/golden/` (not
     outside the sub-repo, unlike some other golden-comparison tests in this
     tree), so there is no standalone-CI case where the fixture is
     legitimately absent. A skip here would make this phase's central proof
     -- that Phase 148 changes nothing on the wire -- invisible in CI.
"""

import copy
import json
from pathlib import Path

from firestarter.database import EpromDatabase

# ---------------------------------------------------------------------------
# Path resolution (S-2) -- self-contained, not in conftest.py.
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_GOLDEN = _HERE / "golden" / "wire_dict_baseline.json"
# Phase 149 (D-17): the committed expected-delta list on top of the
# preserved golden -- see this file's module docstring and
# tests/golden/wire_dict_expected_deltas_149.json's own "meta" block.
_DELTAS_149 = _HERE / "golden" / "wire_dict_expected_deltas_149.json"

# The 2 record keys Phase 148's golden itself carries page-size for
# (the pre-existing datasheet-curated _PAGE_SIZE_BY_PART rows). This is the
# anti-laundering assertion: a future re-capture of the golden that quietly
# grows this set defeats the whole point of a committed delta list.
_GOLDEN_PAGE_SIZE_RECORD_KEYS = {
    "WINBOND|W29C020,W29C020C,W29C022|7",
    "WINBOND|W29C040,W29C042|8",
}

# ---------------------------------------------------------------------------
# The nine wire keys measured in Task 1 (RESEARCH F-8). D-06/D-14 assumed
# five; bus-config, flags and page-size are real wire fields that a
# five-key capture would have missed.
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Real DB, captured once at module level (skip_local_override=True is
# MANDATORY -- tests/test_characterization.py:501 states the rule verbatim:
# a ~/.firestarter override would leak a spurious row into this capture).
# ---------------------------------------------------------------------------
_REAL_DB = EpromDatabase(skip_local_override=True)


def _capture_wire_dicts(db: EpromDatabase) -> dict[str, dict]:
    """Reproduce the exact 746-chip capture shape used by
    `tools/check_dispatch.py:368-370` (`db.get_eprom(part)` ->
    `db.convert_to_programmer(mapped)`), keyed `f"{mfg}|{pn}|{i}"`.

    Record key is `f"{mfg}|{pn}|{i}"`, never `pn` alone -- `diff_db.py`'s
    CR-01 comment documents that 65-69 records share a `part_number`; a
    `pn`-only key would silently shadow ~9% of the database.
    """
    records: dict[str, dict] = {}
    for mfg, chips in db.proms.items():
        for i, chip in enumerate(chips):
            pn = chip.get("part_number", "")
            mapped = db.get_eprom(pn)
            wire = db.convert_to_programmer(mapped) if mapped else {}
            records[f"{mfg}|{pn}|{i}"] = wire
    return records


def _describe_record_diff(recorded: dict, live: dict) -> str:
    """Explain a wire-dict mismatch by naming added, removed and
    value-changed record keys SEPARATELY -- for a changed record, name the
    differing wire keys rather than dumping both dicts.

    Modelled on `tests/test_chip_database_field_inventory.py`'s
    `_describe_counter_diff`. Both the real comparison test and the
    non-vacuity leg call this same helper.
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


# ---------------------------------------------------------------------------
# Test 5: golden file must exist -- loud failure, never a skip.
# ---------------------------------------------------------------------------


def test_golden_file_exists() -> None:
    assert _GOLDEN.exists(), (
        f"golden fixture missing at {_GOLDEN}; "
        "Phase 148 Plan 01 Task 1 must capture this file before any other "
        "Phase 148 edit -- this is the pre-change baseline the whole phase "
        "is measured against."
    )


# ---------------------------------------------------------------------------
# Test 1 (Phase 149, D-17): golden PLUS exactly the 18 named deltas equals
# the live capture. The golden itself stays the pre-149 capture, byte-
# unchanged -- see tests/golden/wire_dict_expected_deltas_149.json.
# ---------------------------------------------------------------------------


def test_live_capture_matches_golden_plus_the_149_deltas() -> None:
    doc = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    recorded = doc["records"]

    deltas_doc = json.loads(_DELTAS_149.read_text(encoding="utf-8"))
    deltas = deltas_doc["deltas"]

    # (a) Anti-laundering: the golden's OWN page-size-carrying record set is
    # exactly Phase 148's original two. A future phase that re-captures the
    # golden to make a failure disappear breaks this assertion, keeping
    # Phase 148's own central claim ("this migration changed nothing on the
    # wire") legible in the same file forever.
    golden_page_size_keys = {
        key for key, wire in recorded.items() if "page-size" in wire
    }
    assert golden_page_size_keys == _GOLDEN_PAGE_SIZE_RECORD_KEYS, (
        "the golden's own page-size-carrying record set drifted from "
        f"Phase 148's original two -- symmetric difference: "
        f"{golden_page_size_keys ^ _GOLDEN_PAGE_SIZE_RECORD_KEYS}. "
        "A re-capture of wire_dict_baseline.json is exactly what D-17 "
        "forbids; if a legitimate wire change adds a page-size carrier, "
        "add it to wire_dict_expected_deltas_149.json instead."
    )

    # (b) Non-vacuity per delta: every delta key must exist in the golden,
    # and the golden's own record for it must NOT already carry page-size --
    # a delta naming a key that already holds the value would prove nothing.
    missing_from_golden = sorted(k for k in deltas if k not in recorded)
    already_present = sorted(
        k for k in deltas if k in recorded and "page-size" in recorded[k]
    )
    assert not missing_from_golden, (
        f"delta keys not found in the golden: {missing_from_golden}"
    )
    assert not already_present, (
        "delta keys whose golden record already carries page-size (the "
        f"delta would prove nothing): {already_present}"
    )

    # (c) Exact count: len(deltas) == 18, not "at least".
    assert len(deltas) == 18, (
        f"expected exactly 18 Phase 149 deltas, found {len(deltas)}: {sorted(deltas)}"
    )

    # (d) Golden plus exactly these deltas equals live.
    expected = copy.deepcopy(recorded)
    for key, delta_wire in deltas.items():
        expected[key].update(delta_wire)

    live = _capture_wire_dicts(_REAL_DB)
    assert expected == live, (
        "live 746-chip wire-dict capture does not equal "
        "tests/golden/wire_dict_baseline.json plus exactly the 18 named "
        "Phase 149 deltas (tests/golden/wire_dict_expected_deltas_149.json); "
        "if this is a legitimate NEW wire-value change, it must be added "
        "to the delta list deliberately, naming which chips and which keys "
        f"moved, in the commit message. Diff: {_describe_record_diff(expected, live)}"
    )


# ---------------------------------------------------------------------------
# Test 2: wire-key union is exactly the nine measured keys.
# ---------------------------------------------------------------------------


def test_wire_key_union_is_exactly_nine_keys() -> None:
    live = _capture_wire_dicts(_REAL_DB)
    keys: set[str] = set()
    for wire in live.values():
        keys |= set(wire)
    added = sorted(keys - _EXPECTED_WIRE_KEYS)
    removed = sorted(_EXPECTED_WIRE_KEYS - keys)
    assert keys == _EXPECTED_WIRE_KEYS, (
        "wire-key union changed from the nine keys measured in Task 1 "
        f"(RESEARCH F-8); added={added}; removed={removed}"
    )


# ---------------------------------------------------------------------------
# Test 3 (D-06): vcc and vpp_volts never cross the host->wire seam.
# ---------------------------------------------------------------------------


def test_vcc_and_vpp_volts_never_cross_the_wire() -> None:
    live = _capture_wire_dicts(_REAL_DB)
    offenders_vcc = [key for key, wire in live.items() if "vcc" in wire]
    offenders_vpp_volts = [key for key, wire in live.items() if "vpp_volts" in wire]
    assert not offenders_vcc, (
        "D-06 violated: 'vcc' appeared on the wire for "
        f"{offenders_vcc} -- vcc must stay inert on the host->wire seam"
    )
    assert not offenders_vpp_volts, (
        "D-06 violated: 'vpp_volts' appeared on the wire for "
        f"{offenders_vpp_volts} -- only vpp_mv may cross the seam"
    )


# ---------------------------------------------------------------------------
# Test 4: non-vacuity (S-5) -- the comparison helper must be capable of
# reporting a diff, not a vacuous always-pass check.
# ---------------------------------------------------------------------------


def test_describe_record_diff_is_non_vacuous() -> None:
    doc = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    recorded = doc["records"]
    mutated = copy.deepcopy(recorded)
    some_key = next(iter(sorted(mutated)))
    mutated[some_key]["pulse-delay"] = mutated[some_key].get("pulse-delay", 0) + 1

    diff = _describe_record_diff(recorded, mutated)

    assert diff != "(no difference detected)", (
        "non-vacuity failure: mutating one record's pulse-delay did not "
        "produce a reported diff -- _describe_record_diff is vacuous"
    )
    assert some_key in diff, (
        f"non-vacuity failure: mutated record key {some_key!r} not named "
        f"in the diff report: {diff}"
    )
    assert "pulse-delay" in diff, (
        f"non-vacuity failure: mutated wire key 'pulse-delay' not named in "
        f"the diff report: {diff}"
    )
