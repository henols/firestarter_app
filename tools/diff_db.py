"""
GATE-02: Per-chip diff of the regenerated chip_database.json against the
pinned pre-milestone baseline (chip_database.baseline.json, 734 chips,
commit f92873d).

Loads both JSONs, builds flat part_number-keyed indexes, classifies every
changed chip by a cited root-cause rule (grouped by cause per D-01), reports
new chips and any missing chips, and exits with the following codes:

Exit codes:
  0 — all changed chips explained by a cited root-cause rule; N new chips
      confirmed (Rule 1 unblock); 0 chips missing from baseline.
  1 — at least one chip has an unexplained diff OR at least one chip present in
      the baseline is absent from the current DB (D-03 BLOCK: investigate
      build_db.py, correct the logic, re-regen, re-diff).
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
# Helper: build flat part_number -> (mfg, chip_record) index
# ---------------------------------------------------------------------------
def _make_index(db):
    """Build a flat part_number -> (mfg_name, chip_record) index.

    Keys on the exact part_number string as stored in the JSON — do NOT
    parse/split comma-separated alias lists (Pitfall 1: both files were
    produced by the same build_db.py normalization, so exact-string match
    is correct).
    """
    idx = {}
    for mfg, chips in db.items():
        if not isinstance(chips, list):
            continue
        for chip in chips:
            pn = chip.get("part_number", "")
            idx[pn] = (mfg, chip)
    return idx


# ---------------------------------------------------------------------------
# Helper: classify a changed chip by root-cause rule
# ---------------------------------------------------------------------------
def _classify_diff(bl_chip, cu_chip):
    """Return the root-cause label for the diff, or None if unexplained (D-03 BLOCK).

    The COMBINED case (BUG2_AND_BUG3) MUST be tested before the single-bug
    buckets — 188 chips have both timing and vcc/vdd changes simultaneously
    (Pitfall 2 in 59-RESEARCH.md). Testing single-cause buckets first would
    mis-route these to None (unexplained) and trigger a false D-03 BLOCK.

    Priority order:
      1. RULE_ALGO     — algorithm changed (any other field delta is secondary)
      2. BUG2_AND_BUG3 — timing + voltage changed (combined fix, MUST precede singles)
      3. BUG2_TIMING   — timing changed only
      4. BUG3_VCC_VDD  — voltage (vcc/vdd) changed only
      5. SRAM_PINOUT   — pinout changed only
      -> None          — fallthrough = UNEXPLAINED = D-03 BLOCK
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

    # 1. Algorithm change (Rule 1/2/3 — algo is the primary dispatch key)
    if algo_diff:
        return "RULE_ALGO"
    # 2. Combined BUG-2 + BUG-3 (MUST precede single-cause checks — Pitfall 2)
    if timing_diff and voltage_diff:
        return "BUG2_AND_BUG3"
    # 3. BUG-2 timing only
    if timing_diff and not voltage_diff and not pinout_diff:
        return "BUG2_TIMING"
    # 4. BUG-3 vcc/vdd swap only
    if voltage_diff and not timing_diff and not algo_diff:
        return "BUG3_VCC_VDD"
    # 5. SRAM pinout re-derivation
    if pinout_diff and not algo_diff and not timing_diff:
        return "SRAM_PINOUT"
    # Fallthrough: unexplained — triggers D-03 BLOCK
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def main():
    """Load both JSONs, diff, classify, report, exit with contract codes."""
    with open(BASELINE_FILE, encoding="utf-8") as f:
        bl_db = json.load(f)
    with open(DB_FILE, encoding="utf-8") as f:
        cu_db = json.load(f)

    bl_idx = _make_index(bl_db)
    cu_idx = _make_index(cu_db)

    # Count total chips (including duplicates under same part_number key)
    bl_total = sum(len(v) for v in bl_db.values() if isinstance(v, list))
    cu_total = sum(len(v) for v in cu_db.values() if isinstance(v, list))

    # Partition chips into three buckets
    changed_by_cause: dict[str, list[str]] = {k: [] for k in _RATIONALES}
    unexplained: list[str] = []
    new_chips: list[str] = []
    missing_chips: list[str] = []

    for pn, (_mfg, cu_chip) in cu_idx.items():
        if pn not in bl_idx:
            new_chips.append(pn)
        else:
            bl_chip = bl_idx[pn][1]
            if bl_chip != cu_chip:
                cause = _classify_diff(bl_chip, cu_chip)
                if cause is None:
                    unexplained.append(pn)
                else:
                    changed_by_cause[cause].append(pn)

    for pn in bl_idx:
        if pn not in cu_idx:
            missing_chips.append(pn)

    total_changed = sum(len(v) for v in changed_by_cause.values())

    # -----------------------------------------------------------------------
    # Report: grouped-by-cause (D-01) with embedded citations
    # -----------------------------------------------------------------------
    print("=" * 72)
    print("GATE-02 Per-chip Diff Report")
    print(f"  Baseline: {BASELINE_FILE}  ({bl_total} chips)")
    print(f"  Current:  {DB_FILE}  ({cu_total} chips)")
    print("=" * 72)

    print(f"\n--- CHANGED chips ({total_changed} total) ---\n")
    for cause, chips in changed_by_cause.items():
        if not chips:
            continue
        print(f"[{cause}] ({len(chips)} chips)")
        for line in _RATIONALES[cause].splitlines():
            print(f"  {line}")
        print(f"  Affected part_numbers ({len(chips)}):")
        for pn in sorted(chips):
            print(f"    {pn}")
        print()

    print(
        f"--- NEW chips ({len(new_chips)}) — Rule 1 unblock via DIP24_2816 + algo=0x0D ---\n"
    )
    for pn in sorted(new_chips):
        print(f"  {pn}")
    if new_chips:
        print()

    print(f"--- MISSING chips ({len(missing_chips)}) ---\n")
    for pn in sorted(missing_chips):
        print(f"  {pn}")
    if missing_chips:
        print()

    # -----------------------------------------------------------------------
    # Gate: unexplained diffs or missing chips = D-03 BLOCK (exit 1)
    # -----------------------------------------------------------------------
    failures = list(unexplained) + missing_chips
    if failures:
        if unexplained:
            print(f"FAIL: {len(unexplained)} chips with unexplained diffs:")
            for pn in unexplained[:20]:
                print(f"  {pn}")
            if len(unexplained) > 20:
                print(f"  ... and {len(unexplained) - 20} more")
        if missing_chips:
            print(
                f"FAIL: {len(missing_chips)} chips present in baseline but absent from current DB:"
            )
            for pn in missing_chips[:20]:
                print(f"  {pn}")
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
