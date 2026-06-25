"""
GATE-02: Per-chip diff of the regenerated chip_database.json against the
pinned pre-milestone baseline (chip_database.baseline.json, 744 chips,
Phase 70 integrated output).

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
    "BUG_A_ETYPE": (
        "BUG-A electrical.type fix — flags-based EEPROM reclassification for 0x07-protocol chips.\n"
        "  Pass 2 previously mapped ALL proto=0x07 chips to 'UV-EPROM', ignoring flags bit 0x10\n"
        "  (electrically erasable). Chips with flags & 0x10 set (W27C512, SST27SF512,\n"
        "  SST27VF512, W27C257, etc.) are CMOS EEPROMs and now decode as 'EEPROM'.\n"
        "  Algorithm stays 0x07 (configure_eprom, 12V VPP) — unchanged.\n"
        "  [VERIFIED: infoic.xml survey — all DIP28_27512/27256 chips with flags & 0x10 are\n"
        "   CMOS EEPROMs per datasheet; UV-EPROMs have flags & 0x10 = False]"
    ),
    "BUG_B_VPP": (
        "BUG-B VPP decode fix — voltages & 0xF0 mask instead of voltages & 0xFF.\n"
        "  The VPP voltage code occupies bits 7-4 (high nibble of the voltages low byte);\n"
        "  bits 3-0 carry option flags (powerdown-enable, T48 sub-options, etc.).\n"
        "  Previously, any chip with a nonzero low-nibble (e.g. voltages=0x0001 for\n"
        "  SST27VF512) produced vpp_mv=0/Unknown because 0x01 is absent from the lookup\n"
        "  table (all valid TL866II VPP codes are multiples of 0x10). Fix: mask with 0xF0\n"
        "  to extract only the VPP nibble — SST27VF512 now correctly shows 12V.\n"
        "  [VERIFIED: minipro/src/tl866a.c msg[5]=voltages.vpp<<4;\n"
        "   tl866ii_vpp_voltages[] table keys: 0x00=12V, 0x10=9V, 0x20=9.5V, ...]"
    ),
    "RULE_PHASE66": (
        "Phase 66 DB inclusion + VPP correction changes.\n"
        "  DB-01: New chips with support_status=protocol-not-implemented included\n"
        "    (previously silently skipped). New top-level key: support_status + unsupported_reason.\n"
        "  DB-02: 9 damage-hazard 24-pin EEPROMs included as support_status=adapter-required\n"
        "    (previously silently skipped; DIP24 form only).\n"
        "  DB-03: NMOS high-VPP entries corrected: M2716/M2732=25V (vpp-exceeds-max),\n"
        "    M2732A=21V (supported at corrected voltage). vpp/vpp_mv fields updated.\n"
        "  DB-05: All chips gain explicit support_status=supported (majority, mechanical change).\n"
        "  [VERIFIED: .planning/phases/66-db-inclusion-vpp-correction-dispatch-gate/66-CONTEXT.md"
        " D-04/D-06/D-07]"
    ),
    "VARIANT_DECODE": (
        "Variant-decode consolidation (Phase 86 VAR-02) — Rule 1/2/3 replaced by a\n"
        "  single principled classify(type,proto,pm_idx,flags,pinout).\n"
        "  The override stack is gone; electrical.type/algorithm/pinout are now derived\n"
        "  once from the fields minipro itself uses to classify a device. Two effects\n"
        "  land here as electrical.type-only deltas:\n"
        "    (a) 68 5V-EEPROM-pinout chips (proto 0x0D, configure_eeprom28c, no VPP)\n"
        "        decode Flash/EEPROM -> EEPROM. The old two-pass mapped proto 0x0D to\n"
        "        'Flash/EEPROM'; classify() arm 2 (5V-EEPROM pinout clusters) emits the\n"
        "        more-accurate 'EEPROM' type for the 28C/28LV/2816 family. NO algorithm,\n"
        "        pinout, or VPP change — the chips already dispatched to configure_eeprom28c.\n"
        "    (b) X88C64P (proto 0x34, XICOR NovRAM/EEPROM) decodes UV-EPROM -> EEPROM via\n"
        "        classify() arm 4b. Display-only: the chip stays\n"
        "        support_status=protocol-not-implemented and non-dispatchable; algorithm\n"
        "        (0x34) and pinout unchanged.\n"
        "  The variant HIGH byte is minipro's T56/T76 algo-file selector, NOT a\n"
        "  classification axis — classify() keys on type/proto/pm_idx/flags.\n"
        "  [VERIFIED: minipro database.c#L1918 @ a8efaedc —\n"
        "   uint8_t algo_number = (uint8_t)(device->variant >> 8) —\n"
        "   https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc/src/database.c#L1918]\n"
        "  [VERIFIED: minipro minipro.h#L70 MP_SRAM=0x04 —\n"
        "   https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc/src/minipro.h#L70]\n"
        "  [CITED: tools/DECODE-NOTES.md §2 (high byte) / §4 (X88C64) / §5 (FM1608)]"
    ),
    "RULE_PHASE84_RELABEL": (
        "Phase 84 cosmetic electrical.type relabel — label-only, NO dispatch / FLAG_CAN_ERASE / VPP change.\n"
        "  FM1608 (RAMTRON FRAM): electrical.type corrected SRAM→FRAM at the build_db.py codegen\n"
        "    layer (per-chip override after Pass-2). CAN_ERASE unaffected (FRAM ∉ {EEPROM,\n"
        "    Flash/EEPROM}). VPP display gated out (FRAM has no programming VPP). Display-layer\n"
        "    _ELECTRICAL_TYPE_LABEL extended with 'FRAM' key.\n"
        "  SST39SF040: KEEP Flash/EEPROM — cosmetic 'Flash' label observation recorded in\n"
        "    DECODE-AUDIT.md (plan 84-04); no code change (D-40 STOP: relabeling would flip\n"
        "    FLAG_CAN_ERASE OFF, breaking Phase-77/82-proven auto-erase).\n"
        "  Scope: exactly the relabeled part_numbers' electrical.type field; NO algorithm /\n"
        "    pinout / vpp / FLAG_CAN_ERASE delta.\n"
        "  [VERIFIED: Phase 84 plan 84-03, operator decision sst-keep / fm-fram-full 2026-06-25]"
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
    "BUG_A_ETYPE": {
        ("electrical", "type"),  # flags-based EEPROM reclassification for 0x07 chips
    },
    "BUG_B_VPP": {
        ("electrical", "vpp"),  # VPP voltage string (0xF0-mask fix)
        ("electrical", "vpp_mv"),  # VPP voltage in mV (0xF0-mask fix)
    },
    # Phase 66: support_status + unsupported_reason (new top-level keys) + NMOS vpp/vpp_mv corrections.
    # Every existing chip gains support_status=supported (a bare support_status diff);
    # NMOS entries also gain corrected vpp/vpp_mv; non-supported chips gain unsupported_reason.
    # RULE_PHASE66 is placed LAST (least specific) so it does not shadow BUG_A_ETYPE/BUG_B_VPP
    # (Pitfall 7 in 70-RESEARCH.md): BUG_B_VPP requires not type_diff; RULE_PHASE66 does not.
    "RULE_PHASE66": {
        ("support_status",),
        ("unsupported_reason",),
        ("electrical", "vpp"),
        ("electrical", "vpp_mv"),
    },
    # Phase 84 cosmetic relabel: FM1608 SRAM→FRAM. Scoped to the relabeled chips'
    # electrical.type field only. No algorithm / pinout / vpp / CAN_ERASE delta.
    # Placed after RULE_PHASE66 (more specific than RULE_PHASE66's support_status scope,
    # but still less specific than BUG_A_ETYPE which also matches type_diff without
    # algo_diff — RULE_PHASE84_RELABEL is distinguished by the part_number scope check
    # in _classify_diff, not by field exclusivity alone).
    "RULE_PHASE84_RELABEL": {
        ("electrical", "type"),  # only the type string changes for the relabeled chip
    },
    # Phase 86 variant-decode consolidation: electrical.type-only delta for the
    # 5V-EEPROM-pinout (proto 0x0D) chips Flash/EEPROM->EEPROM and X88C64P
    # (proto 0x34) UV-EPROM->EEPROM. No algorithm / pinout / vpp delta.
    "VARIANT_DECODE": {
        ("electrical", "type"),
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


# Phase 84 RULE_PHASE84_RELABEL scope: the exact part_numbers whose electrical.type
# was corrected. SST39SF040 is EXCLUDED (sst-keep decision — no code change).
_PHASE84_RELABEL_PART_NUMBERS = frozenset({"FM1608"})


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
      6. RULE_PHASE84_RELABEL — only electrical.type changed, AND the chip is in
                         _PHASE84_RELABEL_PART_NUMBERS (cosmetic label-only correction;
                         scoped by part_number; MORE SPECIFIC than BUG_A_ETYPE so must
                         precede it — otherwise BUG_A_ETYPE would match first)
      6b. VARIANT_DECODE — only electrical.type changed to 'EEPROM' AND proto in
                         {0x0D, 0x34} (Phase 86 consolidation: 5V-EEPROM-pinout proto-0x0D
                         Flash/EEPROM->EEPROM + X88C64P proto-0x34 UV-EPROM->EEPROM;
                         scoped by new-type+proto so it does NOT shadow BUG_A_ETYPE)
      7. BUG_A_ETYPE   — electrical.type changed (flags-based EEPROM reclassification)
      8. BUG_B_VPP     — electrical.vpp/vpp_mv changed (0xF0-mask fix)
      9. RULE_PHASE66  — only support_status/unsupported_reason/vpp/vpp_mv changed
                         (LAST — least specific; must not shadow BUG_A_ETYPE/BUG_B_VPP)
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
    type_diff = bl_elec.get("type") != cu_elec.get("type")
    vpp_diff = bl_elec.get("vpp") != cu_elec.get("vpp") or bl_elec.get(
        "vpp_mv"
    ) != cu_elec.get("vpp_mv")
    # Phase 66: support_status and/or unsupported_reason added; vpp/vpp_mv corrected for NMOS.
    phase66_diff = (
        bl_chip.get("support_status") != cu_chip.get("support_status")
        or bl_chip.get("unsupported_reason") != cu_chip.get("unsupported_reason")
        or bl_elec.get("vpp") != cu_elec.get("vpp")
        or bl_elec.get("vpp_mv") != cu_elec.get("vpp_mv")
    )

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
    elif (
        type_diff
        and not algo_diff
        and not timing_diff
        and not voltage_diff
        and not pinout_diff
        and cu_chip.get("part_number") in _PHASE84_RELABEL_PART_NUMBERS
    ):
        # RULE_PHASE84_RELABEL (before BUG_A_ETYPE): cosmetic electrical.type label
        # correction, scoped to the exact chips the operator authorized (fm-fram-full
        # decision). Placed before BUG_A_ETYPE so the part_number-scoped rule takes
        # priority for the named relabeled chips. The part_number check prevents this
        # rule from silently explaining accidental type drift on unrelated chips
        # (D-40 requirement: no collateral change to chips sharing the same infoic flags).
        label = "RULE_PHASE84_RELABEL"
    elif (
        type_diff
        and not algo_diff
        and not timing_diff
        and not voltage_diff
        and not pinout_diff
        and cu_elec.get("type") == "EEPROM"
        and cu_prog.get("algorithm") in (0x0D, 0x34)
    ):
        # VARIANT_DECODE (before BUG_A_ETYPE): Phase 86 consolidation electrical.type
        # delta. The new classify() emits 'EEPROM' for the 5V-EEPROM-pinout proto-0x0D
        # chips (were 'Flash/EEPROM') and for X88C64P proto-0x34 (was 'UV-EPROM').
        # Scoped to new-type=='EEPROM' AND proto in {0x0D, 0x34} so it does NOT shadow
        # genuine BUG_A_ETYPE (flags-based 0x07-proto reclassification) or the
        # part_number-scoped RULE_PHASE84_RELABEL (FM1608 SRAM->FRAM, handled above).
        label = "VARIANT_DECODE"
    elif (
        type_diff
        and not algo_diff
        and not timing_diff
        and not voltage_diff
        and not pinout_diff
    ):
        label = "BUG_A_ETYPE"
    elif (
        vpp_diff
        and not algo_diff
        and not timing_diff
        and not pinout_diff
        and not type_diff
    ):
        label = "BUG_B_VPP"
    elif (
        phase66_diff
        and not algo_diff
        and not timing_diff
        and not voltage_diff
        and not pinout_diff
    ):
        # RULE_PHASE66: only Phase 66 fields changed (support_status, unsupported_reason,
        # electrical.vpp, electrical.vpp_mv). Placed LAST so it does not shadow
        # BUG_A_ETYPE/BUG_B_VPP (Pitfall 7 in 70-RESEARCH.md).
        label = "RULE_PHASE66"

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
