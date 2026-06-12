"""
GATE-02: Per-chip diff of the regenerated chip_database.json against the
pinned pre-milestone baseline (chip_database.baseline.json, 734 chips,
commit f92873d).

Loads both JSONs, builds composite-keyed indexes (one key per record, so
duplicate part_numbers are never shadowed — CR-01), classifies every changed
chip by a cited root-cause rule (grouped by cause per D-01), reports new chips
and any missing chips, and exits with the following codes:

Exit codes:
  0 — all changed chips explained by a cited root-cause rule; N new chips
      confirmed (Rule 1 unblock); 0 chips missing from baseline.
  1 — at least one chip has an unexplained diff OR at least one chip present in
      the baseline is absent from the current DB (D-03 BLOCK: investigate
      build_db.py, correct the logic, re-regen, re-diff).
  2 — infrastructure error: a required input file could not be loaded or parsed
      (missing/malformed baseline or current DB). Distinct from 1 so a CI
      consumer does not confuse a missing input with a real diff BLOCK (WR-04).
"""

import json
import os
import sys

# ---------------------------------------------------------------------------
# Module-top env-overridable path constants (mirrors check_dispatch.py lines 24-29)
# ---------------------------------------------------------------------------
_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "firestarter", "data")
_BASELINE_DIR = os.path.join(os.path.dirname(__file__), "baseline")

DB_FILE = os.environ.get(
    "FIRESTARTER_DB_FILE",
    os.path.join(_DATA_DIR, "chip_database.json"),
)
BASELINE_FILE = os.environ.get(
    "FIRESTARTER_BASELINE_FILE",
    os.path.join(_BASELINE_DIR, "chip_database.baseline.json"),
)

# ---------------------------------------------------------------------------
# Root-cause labels and their grouped rationale strings
# Each string embeds a [VERIFIED: minipro ...] citation per Phase 56 D-05/D-06.
# Permalink base: https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc/src/database.c
# ---------------------------------------------------------------------------
_RATIONALES = {
    "RULE_ALGO": (
        "Algorithm correction (Rule 1/2/3) — Phase 57/58 principled re-derivation.\n"
        "  Rule 1: variant_lo=0x10 chips (24-pin EEPROM family) now get DIP24_2816 + algo=0x0D\n"
        "    instead of being skipped. Previously blocked by the 24-pin safety skip.\n"
        "  Rule 2: WARNING-5 override — DIP28_2764 Flash/EEPROM chips flipped 0x07->0x0D\n"
        "    to route through configure_eeprom28c (5V-only, no VPP assertion).\n"
        "    [CITED: .planning/v1.0-MILESTONE-AUDIT.md §WARNING-5]\n"
        "  Rule 3: fm1608 FRAM chips corrected to algo=0x29 (SRAM_512K_1M).\n"
        "    [VERIFIED: minipro database.c @ a8efaedc —\n"
        "     https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc/src/database.c]"
    ),
    "BUG2_AND_BUG3": (
        "BUG-2 timing fix AND BUG-3 vcc/vdd label swap — both applied to same record.\n"
        "  BUG-2: interpret_timing x100 multiplier removed for proto 0x07/0x0B.\n"
        "    pulse_duration is microseconds for ALL protocols; no multiplier.\n"
        "    [VERIFIED: minipro database.c#L866 @ a8efaedc —\n"
        "     https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc/src/database.c#L866]\n"
        "  BUG-3: vcc/vdd field labels inverted in original decode.\n"
        "    bits 11-8 = vcc (VCC supply voltage), bits 15-12 = vdd (VDD programming voltage).\n"
        "    [VERIFIED: minipro database.c#L921-L923 @ a8efaedc —\n"
        "     https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc/src/database.c#L921]"
    ),
    "BUG2_TIMING": (
        "BUG-2 timing fix only — interpret_timing x100 multiplier removed for proto 0x07/0x0B.\n"
        "  pulse_duration is microseconds for ALL protocols; no per-protocol multiplier.\n"
        "  [VERIFIED: minipro database.c#L866 @ a8efaedc —\n"
        "   https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc/src/database.c#L866]"
    ),
    "BUG3_VCC_VDD": (
        "BUG-3 vcc/vdd label swap only — inverted field labels corrected.\n"
        "  bits 11-8 = vcc (VCC supply voltage), bits 15-12 = vdd (VDD programming voltage).\n"
        "  Previously the decode had these reversed.\n"
        "  [VERIFIED: minipro database.c#L921-L923 @ a8efaedc —\n"
        "   https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc/src/database.c#L921]"
    ),
    "SRAM_PINOUT": (
        "SRAM pinout re-derivation — Phase 58 principled resolve_pinout_key rewrite.\n"
        "  28-pin SRAM chips (pm_idx=0) previously mapped to wrong DIP28_2764 guess-table\n"
        "  entry; now routed to correct JEDEC SRAM layout (DIP28_JEDEC_SRAM_8K or DIP28_28C256)\n"
        "  based on mem_size. The old DIP28_VARIANT_MAP guess table was deleted.\n"
        "  [CITED: Phase 58 principled resolve_pinout_key — pm_idx=0 28-pin SRAM chips]"
    ),
}


# ---------------------------------------------------------------------------
# Helper: build composite-keyed (mfg, part_number[, i]) -> (mfg, chip) index
# ---------------------------------------------------------------------------
def _raw_total(db):
    """Total number of chip records across all manufacturers (raw count)."""
    return sum(len(v) for v in db.values() if isinstance(v, list))


def _make_index(db):
    """Build a 1:1 composite-key -> (mfg_name, chip_record) index.

    CR-01: part_number is NOT unique across the database (65-69 records share
    a part_number with another record), so keying on part_number alone silently
    shadows ~9% of the records and makes the gate blind to them. The natural
    unique composite is (mfg_name, part_number); where even that collides (the
    same manufacturer lists the same part_number twice), we disambiguate by the
    record's positional index within that manufacturer's list.

    The returned index is therefore strictly 1:1 with chip records — every
    record gets exactly one key. The caller asserts len(idx) == _raw_total(db)
    so a future regression that reintroduces a collision fails loudly instead
    of silently shadowing records.

    Do NOT parse/split comma-separated alias lists (Pitfall 1: both files were
    produced by the same build_db.py normalization, so exact-string match on
    the stored part_number is correct).
    """
    idx = {}
    for mfg, chips in db.items():
        if not isinstance(chips, list):
            continue
        for i, chip in enumerate(chips):
            pn = chip.get("part_number", "")
            key = (mfg, pn)
            if key in idx:  # same mfg + pn appears twice — disambiguate
                key = (mfg, pn, i)
            idx[key] = (mfg, chip)
    return idx


def _pn(key):
    """Project the displayed part_number from a composite index key."""
    return key[1]


# ---------------------------------------------------------------------------
# Helper: classify a changed chip by root-cause rule
# ---------------------------------------------------------------------------
# Field paths each root-cause rule is allowed to "explain". A diff is only
# fully explained when EVERY differing field path is claimed by the matched
# rule(s) (WR-02). Anything outside these sets routes to "unexplained" (D-03).
#
# electrical.type is a DERIVED field: build_db.py's Pass-2 protocol-aware
# re-derivation recomputes electrical.type purely from the (possibly
# overridden) algorithm/pinout. It therefore only ever changes as a side-effect
# of an algorithm change (RULE_ALGO) or an SRAM pinout/algorithm re-route
# (SRAM_PINOUT / Rule 3) — verified: 0 records change electrical.type without a
# co-occurring algorithm or pinout change. So electrical.type is claimed by
# exactly those two dispatch-changing rules; a type change with no algo/pinout
# delta would (correctly) remain unexplained.
_RULE_FIELD_PATHS = {
    "RULE_ALGO": {
        ("programming", "algorithm"),
        ("pinout",),  # Rule 1 re-routes 24-pin EEPROMs to DIP24_2816
        ("electrical", "type"),  # derived from algorithm (Pass-2)
    },
    # BUG-2 timing + BUG-3 vcc/vdd label swap, applied to the same record.
    "BUG2_AND_BUG3": {
        ("programming", "pulse_duration"),
        ("electrical", "vcc"),
        ("electrical", "vdd"),
    },
    "BUG2_TIMING": {("programming", "pulse_duration")},
    "BUG3_VCC_VDD": {("electrical", "vcc"), ("electrical", "vdd")},
    "SRAM_PINOUT": {
        ("pinout",),
        ("electrical", "type"),  # SRAM re-route re-derives type (Pass-2)
        ("programming", "algorithm"),  # Rule 3 SRAM override flips algorithm
    },
}


def _diff_field_paths(bl_chip, cu_chip, prefix=()):
    """Deep-diff two chip records → set of differing field-path tuples.

    Recurses into nested dicts so e.g. a change to electrical.vpp surfaces as
    ("electrical", "vpp"). Non-dict values (and lists) are compared by equality
    at their path. Used by _classify_diff to prove that every differing field is
    attributable to a known rule (WR-02).
    """
    paths = set()
    keys = set(bl_chip) | set(cu_chip)
    for k in keys:
        bv = bl_chip.get(k)
        cv = cu_chip.get(k)
        if bv == cv:
            continue
        if isinstance(bv, dict) and isinstance(cv, dict):
            paths |= _diff_field_paths(bv, cv, prefix + (k,))
        else:
            paths.add(prefix + (k,))
    return paths


def _classify_diff(bl_chip, cu_chip):
    """Classify a changed chip → (label, extra_paths) or (None, diff_paths).

    Returns a tuple:
      - (label, extra_paths): label is the primary root-cause rule; extra_paths
        is the (possibly empty) set of differing field paths NOT explained by
        that rule's allowed-field set. A non-empty extra_paths means a compound
        change (WR-01) — the secondary deltas are surfaced by the caller, and if
        any of them is outside ALL known rules' field sets the chip is escalated
        to unexplained (WR-02).
      - (None, diff_paths): no rule matched at all — fully unexplained, the
        caller treats this as a D-03 BLOCK.

    The COMBINED case (BUG2_AND_BUG3) MUST be tested before the single-bug
    buckets — chips with both timing and vcc/vdd changes (Pitfall 2 in
    59-RESEARCH.md) would otherwise mis-route. RULE_ALGO is the primary dispatch
    key, but (unlike the old early-return) compound algo+other changes now keep
    their secondary deltas visible.

    Priority order:
      1. RULE_ALGO     — algorithm changed (primary dispatch key)
      2. BUG2_AND_BUG3 — timing + voltage changed (combined fix, precedes singles)
      3. BUG2_TIMING   — timing changed only
      4. BUG3_VCC_VDD  — voltage (vcc/vdd) changed only
      5. SRAM_PINOUT   — pinout changed only
      -> None          — no rule matched (UNEXPLAINED = D-03 BLOCK)
    """
    bl_prog = bl_chip.get("programming", {})
    cu_prog = cu_chip.get("programming", {})
    bl_elec = bl_chip.get("electrical", {})
    cu_elec = cu_chip.get("electrical", {})

    timing_diff = bl_prog.get("pulse_duration") != cu_prog.get("pulse_duration")
    algo_diff = bl_prog.get("algorithm") != cu_prog.get("algorithm")
    vcc_diff = bl_elec.get("vcc") != cu_elec.get("vcc")
    vdd_diff = bl_elec.get("vdd") != cu_elec.get("vdd")
    pinout_diff = bl_chip.get("pinout") != cu_chip.get("pinout")

    voltage_diff = vcc_diff or vdd_diff

    # Pick the primary rule using the same priority as before.
    label = None
    if algo_diff:
        label = "RULE_ALGO"
    elif timing_diff and voltage_diff:
        label = "BUG2_AND_BUG3"
    elif timing_diff and not voltage_diff and not pinout_diff:
        label = "BUG2_TIMING"
    elif voltage_diff and not timing_diff and not algo_diff:
        label = "BUG3_VCC_VDD"
    elif pinout_diff and not algo_diff and not timing_diff:
        label = "SRAM_PINOUT"

    diff_paths = _diff_field_paths(bl_chip, cu_chip)

    if label is None:
        return None, diff_paths

    # WR-01/WR-02: a diff is fully explained only when every differing field
    # path is claimed by SOME known rule. The primary label explains its own
    # field set; any remaining differing paths are "extra" (secondary) deltas.
    explained = set(_RULE_FIELD_PATHS[label])
    extra_paths = diff_paths - explained
    return label, extra_paths


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def _load_db(path, label):
    """Load a chip-database JSON, exiting 2 (infra error) on any load failure.

    WR-04: a missing/malformed input is an infrastructure problem, NOT a diff
    BLOCK — it must use a distinct exit code (2) so a CI consumer keying on the
    exit status does not misreport it as a real gate failure (exit 1).
    """
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: cannot load {label} {path}: {e}", file=sys.stderr)
        sys.exit(2)


def main():
    """Load both JSONs, diff, classify, report, exit with contract codes."""
    bl_db = _load_db(BASELINE_FILE, "baseline")
    cu_db = _load_db(DB_FILE, "current DB")

    bl_idx = _make_index(bl_db)
    cu_idx = _make_index(cu_db)

    # Raw record counts.
    bl_total = _raw_total(bl_db)
    cu_total = _raw_total(cu_db)

    # CR-01/IN-01: the composite-key index is strictly 1:1 with chip records.
    # Assert it so a future collision regression fails loudly here instead of
    # silently shadowing records (and so the printed header count matches what
    # was actually diffed).
    assert len(bl_idx) == bl_total, (
        f"baseline index size {len(bl_idx)} != raw record count {bl_total} "
        "(composite key collision — records would be silently shadowed)"
    )
    assert len(cu_idx) == cu_total, (
        f"current index size {len(cu_idx)} != raw record count {cu_total} "
        "(composite key collision — records would be silently shadowed)"
    )

    # Union of every field path that SOME known rule can explain. Used to decide
    # whether a compound change's secondary deltas are benign-but-known (WR-01,
    # just surface them) or truly outside all rules (WR-02, escalate to
    # unexplained).
    _all_rule_paths = set()
    for _paths in _RULE_FIELD_PATHS.values():
        _all_rule_paths |= _paths

    # Partition chips into buckets. Keys are composite (mfg, pn[, i]); the
    # displayed value is the projected part_number via _pn().
    changed_by_cause: dict[str, list] = {k: [] for k in _RATIONALES}
    compound_notes: list[str] = []  # WR-01: surfaced secondary deltas
    unexplained: list = []
    new_chips: list = []
    missing_chips: list = []

    for key, (_mfg, cu_chip) in cu_idx.items():
        if key not in bl_idx:
            new_chips.append(key)
        else:
            bl_chip = bl_idx[key][1]
            if bl_chip != cu_chip:
                cause, extra_paths = _classify_diff(bl_chip, cu_chip)
                if cause is None:
                    unexplained.append(key)
                elif extra_paths - _all_rule_paths:
                    # WR-02: at least one differing field is outside ALL known
                    # rules — the diff is not fully attributable. Escalate.
                    unexplained.append(key)
                else:
                    changed_by_cause[cause].append(key)
                    if extra_paths:
                        # WR-01: compound change — primary cause plus secondary
                        # deltas that ARE explained by other rules. Surface them
                        # so a co-bundled (benign-but-real) change is visible.
                        secondary = ", ".join(".".join(p) for p in sorted(extra_paths))
                        compound_notes.append(
                            f"{_pn(key)} [{cause}] + secondary: {secondary}"
                        )

    for key in bl_idx:
        if key not in cu_idx:
            missing_chips.append(key)

    total_changed = sum(len(v) for v in changed_by_cause.values())

    # -----------------------------------------------------------------------
    # Report: grouped-by-cause (D-01) with embedded citations
    # -----------------------------------------------------------------------
    print("=" * 72)
    print("GATE-02 Per-chip Diff Report")
    # IN-01: index is now 1:1 with records, so the diffed key count equals the
    # raw record count — print both to make the reconciliation explicit.
    print(f"  Baseline: {BASELINE_FILE}  ({bl_total} chips, {len(bl_idx)} diffed)")
    print(f"  Current:  {DB_FILE}  ({cu_total} chips, {len(cu_idx)} diffed)")
    print("=" * 72)

    print(f"\n--- CHANGED chips ({total_changed} total) ---\n")
    for cause, chips in changed_by_cause.items():
        if not chips:
            continue
        print(f"[{cause}] ({len(chips)} chips)")
        for line in _RATIONALES[cause].splitlines():
            print(f"  {line}")
        print(f"  Affected part_numbers ({len(chips)}):")
        for key in sorted(chips):
            print(f"    {_pn(key)}")
        print()

    # WR-01: surface compound changes — chips whose primary cause is accompanied
    # by a secondary (but rule-explained) field delta.
    if compound_notes:
        print(f"--- COMPOUND changes ({len(compound_notes)}) — algo+other deltas ---\n")
        print(
            "  These chips have a primary cause PLUS a secondary field delta that\n"
            "  is itself explained by a known rule. Both are surfaced so a\n"
            "  co-bundled change is not silently masked by the primary rationale.\n"
        )
        for note in sorted(compound_notes):
            print(f"  {note}")
        print()

    # WR-03: verify (don't just assert) the Rule 1 unblock claim per new chip.
    print(
        f"--- NEW chips ({len(new_chips)}) — expected Rule 1 unblock (DIP24_2816 + algo=0x0D) ---\n"
    )
    for key in sorted(new_chips):
        pn = _pn(key)
        c = cu_idx[key][1]
        c_pinout = c.get("pinout")
        c_algo = c.get("programming", {}).get("algorithm")
        if not (c_pinout == "DIP24_2816" and c_algo == 0x0D):
            algo_str = f"{c_algo:#x}" if isinstance(c_algo, int) else repr(c_algo)
            print(
                f"  WARN: new chip {pn} is NOT a Rule 1 unblock "
                f"(pinout={c_pinout}, algo={algo_str})"
            )
        else:
            print(f"  {pn}")
    if new_chips:
        print()

    print(f"--- MISSING chips ({len(missing_chips)}) ---\n")
    for key in sorted(missing_chips):
        print(f"  {_pn(key)}")
    if missing_chips:
        print()

    # -----------------------------------------------------------------------
    # Gate: unexplained diffs or missing chips = D-03 BLOCK (exit 1)
    # -----------------------------------------------------------------------
    failures = list(unexplained) + missing_chips
    if failures:
        if unexplained:
            print(f"FAIL: {len(unexplained)} chips with unexplained diffs:")
            for key in unexplained[:20]:
                print(f"  {_pn(key)}")
            if len(unexplained) > 20:
                print(f"  ... and {len(unexplained) - 20} more")
        if missing_chips:
            print(
                f"FAIL: {len(missing_chips)} chips present in baseline but absent from current DB:"
            )
            for key in missing_chips[:20]:
                print(f"  {_pn(key)}")
            if len(missing_chips) > 20:
                print(f"  ... and {len(missing_chips) - 20} more")
        sys.exit(1)

    print(
        f"PASS: all {total_changed} changed chips explained "
        f"({len(new_chips)} new chips confirmed; "
        f"{len(missing_chips)} chips removed from baseline)"
    )


if __name__ == "__main__":
    main()
