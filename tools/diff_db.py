"""
Per-chip diff of the regenerated chip_database.json against the
pinned pre-milestone baseline (chip_database.baseline.json, 744 chips,
integrated output).

Loads both JSONs, builds composite-keyed indexes (one key per record, so
duplicate part_numbers are never shadowed — CR-01), classifies every changed
chip by a cited root-cause rule (grouped by cause), reports new chips
and any missing chips, and exits with the following codes:

Exit codes:
  0 — all changed chips explained by a cited root-cause rule; N new chips
      confirmed (Rule 1 unblock); 0 chips missing from baseline.
  1 — at least one chip has an unexplained diff OR at least one chip present in
      the baseline is absent from the current DB (BLOCK: investigate
      build_db.py, correct the logic, re-regen, re-diff).
  2 — infrastructure error: a required input file could not be loaded or parsed
      (missing/malformed baseline or current DB). Distinct from 1 so a CI
      consumer does not confuse a missing input with a real diff BLOCK.
"""

import copy
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
# Each string embeds a [VERIFIED: minipro ...] citation.
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
    "RULE_VCC_MARGIN_RAIL": (
        "Phase 148 DATA-01 (D-01/D-02/D-03) — VCC margin-rail substitution.\n"
        "  infoic.xml's VCC nibble 2 (VCC_VOLTAGES[0x02] = 4000 mV) is decoded FAITHFULLY —\n"
        "  this is not a decode repair. The defect is semantic: minipro's vcc is the TL866's\n"
        "  low-margin VCC *verify* rail, and firestarter surfaced it as the chip's operating\n"
        "  supply. The substitution targets the already-decoded vdd_mv (itself an\n"
        "  infoic.xml-decoded value, so nothing is invented) whenever vcc_mv lands on this\n"
        "  rail: build_db.py::_VCC_MARGIN_RAIL_MV, applied post-construction.\n"
        "  No other delta: exactly 56 chips move, every one 4000 -> 5000 mV, and no chip's\n"
        "  vcc_mv is ever lowered by this rule (Test 3's no-decrease guard,\n"
        "  tests/test_vcc_margin_rail.py).\n"
        "  [VERIFIED: minipro database.c#L130-L135 @ a8efaedc —\n"
        "   tl866ii_vcc_voltages[] —\n"
        "   https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc/src/database.c#L130]\n"
        "  [CITED: .planning/phases/148-numeric-database-values-the-at28c-vcc-decode/148-DB-DIFF.md]"
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
    "EXTRA_CHIPS_SUPPLEMENT": (
        "Non-upstream chip supplement (Phase 86 VAR-05 / D-10) — 2516 + 2532.\n"
        "  These are physically-real 24-pin UV-EPROM oddballs that are ABSENT from\n"
        "  minipro's infoic.xml entirely (no upstream record at all). They ship\n"
        "  first-class in chip_database.json via the curated, provenance-cited\n"
        "  supplement tools/extra_chips.json, which build_db.py merges AFTER the\n"
        "  infoic.xml decode loop (NOT routed through classify()/resolve_pinout_key —\n"
        "  they arrive fully-specified). Each supplement record carries a\n"
        "  source='non-upstream-supplement' marker + a datasheet citation (D-11).\n"
        "  They appear here as NEW chips against the OLD baseline because the baseline\n"
        "  re-pin is Plan 86-03 (runs AFTER this plan); their presence — with the\n"
        "  non-upstream source marker — is the explanation, NOT a Rule 1 unblock.\n"
        "    2516: algorithm 0x0B, DIP24_2716, UV-EPROM, vpp_mv 25000, 2048 B —\n"
        "      wire values verbatim from the v1.15 user-override (SAFE-04, unmoved);\n"
        "      verification_status UNVERIFIED (resolvable, not write-graduated; FUT-03).\n"
        "    2532: algorithm 0x0B, DIP24_2532 (non-JEDEC, VPP=pin 21), UV-EPROM,\n"
        "      4096 B; UNVERIFIED (no on-hand silicon).\n"
        "  [CITED: tools/extra_chips.json provenance fields + 2516_EPROM.pdf datasheet;\n"
        "   .planning/phases/86-variant-decode-correct-db-regen/86-CONTEXT.md D-10/D-11;\n"
        "   .planning/v1.15/DECODE-AUDIT.md (2516 user-override wire values)]"
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
    "PGSZ_PAGE_SIZE": (
        "Phase 94 PGSZ-01 / CR-01 — datasheet-sourced per-chip page_size field added.\n"
        "  Generalizes flash4 page sizing from the firmware capacity heuristic\n"
        "  (flash4_page_size(mem_size)) to a DB-supplied per-chip value (emit-when-present).\n"
        "  Only chips with a [CITED:] datasheet entry in build_db.py _PAGE_SIZE_BY_PART\n"
        "  get this field. Chips without a cited datasheet continue using the heuristic.\n"
        "    W29C040,W29C042: page_size=256 added.\n"
        "      [CITED: firestarter/datasheets/0x05-FLASH-AMD-STD/W29C040.pdf §6.2\n"
        "              'Every page contains 256 bytes of data.']\n"
        "    W29C020,W29C020C,W29C022: page_size=128 added.\n"
        "      [CITED: firestarter/datasheets/0x05-FLASH-AMD-STD/W29C020.pdf §6.2\n"
        "              'Every page contains 128 bytes of data.' + FEATURES '128 bytes per page']\n"
        "  No other fields changed. No dispatch / algorithm / VPP delta.\n"
        "  [VERIFIED: Phase 94 Plan 02 — PGSZ-01/02/03 requirements + 94-RESEARCH.md A1/A2]"
    ),
    "RC1_DIP32_27C020": (
        "Phase 98 RC-1 fix — DIP32_27C020 scoped pinout for 0x08 ≤256K 32-pin chips.\n"
        "  Root cause RC-1: pin 31 was modeled as address line A18 (DIP32_STD) for all\n"
        "  0x08/32-pin chips, but for ≤256K chips (27C010/27C020 class), pin 31 is PGM\n"
        "  (program enable, active-LOW), not A18. A18 = bit 18 = mask 0x40000; at ≤256K\n"
        "  (mem_size ≤ 262144) address bit 18 is never set, so pin 31 is never a real A18\n"
        "  line — DIP32_STD modeled it incorrectly for this sub-class.\n"
        "  Fix: resolve_pinout_key in build_db.py routes proto_id==0x08 && mem_size<=262144\n"
        "  chips to DIP32_27C020 (pin 31 OFF the address bus; VPP on pin 1 retained).\n"
        "  The 512K AM27C040 (524288) and 1M AM27C080 (1048576) legitimately use pin 31=A18\n"
        "  and stay on DIP32_STD (host-side D-04 alias guard).\n"
        "  Scope: 88 chips across 128K and 256K (65K/128K/256K sizes, proto 0x08, 32-pin).\n"
        "  No algorithm / VPP / electrical.type delta — pinout field only.\n"
        "  Q1 RESOLVED (2026-06-30): static-high-pins ruled out (drives HIGH; PGM=VIL).\n"
        "  PGM program-active assert is Plan 02's firmware branch, not this pinout.\n"
        "  [CITED: firestarter/datasheets/0x08-EPROM-QUICK/AM27C020.pdf — pin 31=PGM, VPP=pin 1]\n"
        "  [CITED: Phase 98 Plan 01 FIX-03 — D-02/D-04 scoped pinout variant]\n"
        "  [CITED: .planning/phases/98-fix-correct-the-0x08-32-pin-write-vpp-path/98-01-PLAN.md]"
    ),
    "PROV01_PROTECT_METADATA": (
        "Phase 136.1 PROV-01 — flags bit 14/15 + raw page_size decode added to the\n"
        "  programming block. Three new keys, decoded directly from each <ic> element's\n"
        "  own flags/page_size attributes (never a cross-reference or token match):\n"
        "    protect_off_before: bool(flags & 0x4000) — MP_OFF_PROTECT_BEFORE.\n"
        "    protect_on_after:   bool(flags & 0x8000) — MP_PROTECT_AFTER (the same bit\n"
        "      sdp_capability.py's SDP_CAPABLE_TOKENS transcription encodes, now\n"
        "      committed as an explicit per-chip field for the first time).\n"
        "    infoic_page_size_raw: the raw, un-curated upstream page_size attribute —\n"
        "      PROV-06's corroborating axis only, NOT the same field as the existing\n"
        "      datasheet-curated programming.page_size (PGSZ_PAGE_SIZE rule above), and\n"
        "      not consulted by any ALLOW/REFUSE decision anywhere in this codebase.\n"
        "  Universal: every upstream-decoded chip gains all three keys; the two\n"
        "  tools/extra_chips.json supplement entries (2516/2532) do NOT, since they\n"
        "  bypass this decode loop entirely (VAR-05 post-decode merge).\n"
        "  Metadata only — no algorithm / pinout / vpp / electrical.type delta; the\n"
        "  84/43/41 SDP ALLOW/REFUSE partition (tests/test_sdp_db_invariant.py) is\n"
        "  unchanged.\n"
        "  [VERIFIED: minipro src/database.c#L39-L50 @ a8efaedc236c1d9718bd28299dfbb99536b010ff —\n"
        "   https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L39]\n"
        "  [CITED: doc/infoic-field-dictionary.md CONFIRMED bit 14/15 row;\n"
        "   .planning/phases/136.1-sdp-partition-provenance/136.1-01-PLAN.md;\n"
        "   .planning/phases/136.1-sdp-partition-provenance/136.1-01-BLAST-RADIUS.md]"
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
# rule(s). Anything outside these sets routes to "unexplained".
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
        ("programming", "pulse_duration_us"),
        ("electrical", "vcc_mv"),
        ("electrical", "vdd_mv"),
    },
    "BUG2_TIMING": {("programming", "pulse_duration_us")},
    # RULE_VCC_MARGIN_RAIL: scope is exactly the one key the margin-rail
    # substitution ever touches — electrical.vcc_mv — and nothing else.
    # vdd_mv is read by the rule, never written; it is not part of this
    # rule's explained field set.
    "RULE_VCC_MARGIN_RAIL": {("electrical", "vcc_mv")},
    "BUG3_VCC_VDD": {("electrical", "vcc_mv"), ("electrical", "vdd_mv")},
    "SRAM_PINOUT": {
        ("pinout",),
        ("electrical", "type"),  # SRAM re-route re-derives type (Pass-2)
        ("programming", "algorithm"),  # Rule 3 SRAM override flips algorithm
    },
    "BUG_A_ETYPE": {
        ("electrical", "type"),  # flags-based EEPROM reclassification for 0x07 chips
    },
    "BUG_B_VPP": {
        ("electrical", "vpp_mv"),  # VPP voltage in mV (0xF0-mask fix)
    },
    # support_status + unsupported_reason (new top-level keys) + NMOS vpp/vpp_mv corrections.
    # Every existing chip gains support_status=supported (a bare support_status diff);
    # NMOS entries also gain corrected vpp/vpp_mv; non-supported chips gain unsupported_reason.
    # RULE_PHASE66 is placed LAST (least specific) so it does not shadow BUG_A_ETYPE/BUG_B_VPP
    # (Pitfall 7 in 70-RESEARCH.md): BUG_B_VPP requires not type_diff; RULE_PHASE66 does not.
    "RULE_PHASE66": {
        ("support_status",),
        ("unsupported_reason",),
        ("electrical", "vpp_mv"),
    },
    # Cosmetic relabel: FM1608 SRAM→FRAM. Scoped to the relabeled chips'
    # electrical.type field only. No algorithm / pinout / vpp / CAN_ERASE delta.
    # Placed after RULE_PHASE66 (more specific than RULE_PHASE66's support_status scope,
    # but still less specific than BUG_A_ETYPE which also matches type_diff without
    # algo_diff — RULE_PHASE84_RELABEL is distinguished by the part_number scope check
    # in _classify_diff, not by field exclusivity alone).
    "RULE_PHASE84_RELABEL": {
        ("electrical", "type"),  # only the type string changes for the relabeled chip
    },
    # Variant-decode consolidation: electrical.type-only delta for the
    # 5V-EEPROM-pinout (proto 0x0D) chips Flash/EEPROM->EEPROM and X88C64P
    # (proto 0x34) UV-EPROM->EEPROM. No algorithm / pinout / vpp delta.
    "VARIANT_DECODE": {
        ("electrical", "type"),
    },
    # Per-chip page_size field added to programming block. Only chips with a
    # [CITED:] datasheet entry in build_db.py _PAGE_SIZE_BY_PART get this field.
    # No other field changes. Scoped to programming.page_size additions only.
    "PGSZ_PAGE_SIZE": {
        ("programming", "page_size"),
    },
    # RC-1 fix: DIP32_27C020 pinout assigned to 0x08 ≤256K chips. Pinout-only
    # change — no algorithm / VPP / electrical.type delta.
    "RC1_DIP32_27C020": {
        ("pinout",),
    },
    # Flags bit 14/15 + raw page_size decode added. Scoped to exactly these
    # three new programming.* keys — no other field changes.
    "PROV01_PROTECT_METADATA": {
        ("programming", "protect_off_before"),
        ("programming", "protect_on_after"),
        ("programming", "infoic_page_size_raw"),
    },
}


def _diff_field_paths(bl_chip, cu_chip, prefix=()):
    """Deep-diff two chip records → set of differing field-path tuples.

    Recurses into nested dicts so e.g. a change to electrical.vpp surfaces as
    ("electrical", "vpp"). Non-dict values (and lists) are compared by equality
    at their path. Used by _classify_diff to prove that every differing field is
    attributable to a known rule.
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


# RULE_PHASE84_RELABEL scope: the exact part_numbers whose electrical.type
# was corrected. SST39SF040 is EXCLUDED (sst-keep decision — no code change).
_PHASE84_RELABEL_PART_NUMBERS = frozenset({"FM1608"})


def _classify_diff(bl_chip, cu_chip):
    """Classify a changed chip → (label, extra_paths) or (None, diff_paths).

    Returns a tuple:
      - (label, extra_paths): label is the primary root-cause rule; extra_paths
        is the (possibly empty) set of differing field paths NOT explained by
        that rule's allowed-field set. A non-empty extra_paths means a compound
        change — the secondary deltas are surfaced by the caller, and if
        any of them is outside ALL known rules' field sets the chip is escalated
        to unexplained.
      - (None, diff_paths): no rule matched at all — fully unexplained, the
        caller treats this as a BLOCK.

    The COMBINED case (BUG2_AND_BUG3) MUST be tested before the single-bug
    buckets — chips with both timing and vcc/vdd changes (Pitfall 2 in
    59-RESEARCH.md) would otherwise mis-route. RULE_ALGO is the primary dispatch
    key, but (unlike the old early-return) compound algo+other changes now keep
    their secondary deltas visible.

    Priority order:
      1. RULE_ALGO     — algorithm changed (primary dispatch key)
      2. BUG2_AND_BUG3 — timing + voltage changed (combined fix, precedes singles)
      3. BUG2_TIMING   — timing changed only
      4. RULE_VCC_MARGIN_RAIL — margin-rail substitution: baseline
                         vcc_mv was the 4000 mV verify rail, current vcc_mv now equals
                         current vdd_mv (value-scoped, before BUG3_VCC_VDD — otherwise a
                         mover would be misattributed to the vcc/vdd label-swap rationale)
      5. BUG3_VCC_VDD  — voltage (vcc/vdd) changed only
      6a. RC1_DIP32_27C020 — pinout changed to DIP32_27C020 (before SRAM_PINOUT)
      6b. SRAM_PINOUT  — pinout changed only (other pinout re-routes)
      7. RULE_PHASE84_RELABEL — only electrical.type changed, AND the chip is in
                         _PHASE84_RELABEL_PART_NUMBERS (cosmetic label-only correction;
                         scoped by part_number; MORE SPECIFIC than BUG_A_ETYPE so must
                         precede it — otherwise BUG_A_ETYPE would match first)
      7b. VARIANT_DECODE — only electrical.type changed to 'EEPROM' AND proto in
                         {0x0D, 0x34} (consolidation: 5V-EEPROM-pinout proto-0x0D
                         Flash/EEPROM->EEPROM + X88C64P proto-0x34 UV-EPROM->EEPROM;
                         scoped by new-type+proto so it does NOT shadow BUG_A_ETYPE)
      8. BUG_A_ETYPE   — electrical.type changed (flags-based EEPROM reclassification)
      9. BUG_B_VPP     — electrical.vpp/vpp_mv changed (0xF0-mask fix)
      10. RULE_PHASE66 — only support_status/unsupported_reason/vpp/vpp_mv changed
                         (LAST — least specific; must not shadow BUG_A_ETYPE/BUG_B_VPP)
      -> None          — no rule matched (UNEXPLAINED = BLOCK)
    """
    bl_prog = bl_chip.get("programming", {})
    cu_prog = cu_chip.get("programming", {})
    bl_elec = bl_chip.get("electrical", {})
    cu_elec = cu_chip.get("electrical", {})

    timing_diff = bl_prog.get("pulse_duration_us") != cu_prog.get("pulse_duration_us")
    algo_diff = bl_prog.get("algorithm") != cu_prog.get("algorithm")
    vcc_diff = bl_elec.get("vcc_mv") != cu_elec.get("vcc_mv")
    vdd_diff = bl_elec.get("vdd_mv") != cu_elec.get("vdd_mv")
    pinout_diff = bl_chip.get("pinout") != cu_chip.get("pinout")
    type_diff = bl_elec.get("type") != cu_elec.get("type")
    vpp_diff = bl_elec.get("vpp_mv") != cu_elec.get("vpp_mv")
    # support_status and/or unsupported_reason added; vpp_mv corrected for NMOS.
    phase66_diff = (
        bl_chip.get("support_status") != cu_chip.get("support_status")
        or bl_chip.get("unsupported_reason") != cu_chip.get("unsupported_reason")
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
    elif (
        bl_elec.get("vcc_mv") == 4000
        and cu_elec.get("vcc_mv") == cu_elec.get("vdd_mv")
        and cu_elec.get("vcc_mv") != 4000
        and not algo_diff
        and not timing_diff
        and not pinout_diff
        and not type_diff
    ):
        # RULE_VCC_MARGIN_RAIL (before BUG3_VCC_VDD):
        # margin-rail substitution. Scoped on the DECODED VALUES themselves —
        # baseline vcc_mv was the 4000 mV TL866 verify-margin rail (mirrors
        # build_db.py's _VCC_MARGIN_RAIL_MV = VCC_VOLTAGES[0x02]); current
        # vcc_mv now equals the chip's own current vdd_mv; current vcc_mv is
        # no longer 4000 — rather than any part-number/type/algorithm key, so
        # this branch's scope is ENFORCED here (not just asserted in prose): a
        # compound change (algo/timing/pinout/type also differing) falls
        # through to a more generic rule instead of being silently absorbed.
        # Placed BEFORE BUG3_VCC_VDD: otherwise a mover whose only other delta
        # is a secondary field would be misattributed to the earlier
        # BUG-3 vcc/vdd label-swap rationale, which this substitution is not
        # (the vcc/vdd labels are correct; only the margin-rail value
        # is being substituted).
        label = "RULE_VCC_MARGIN_RAIL"
    elif voltage_diff and not timing_diff and not algo_diff:
        label = "BUG3_VCC_VDD"
    elif (
        pinout_diff
        and not algo_diff
        and not timing_diff
        and not voltage_diff
        and not type_diff
        and not vpp_diff
        and cu_chip.get("pinout") == "DIP32_27C020"
    ):
        # RC1_DIP32_27C020 (before SRAM_PINOUT): 0x08 ≤256K chips
        # reassigned from DIP32_STD to DIP32_27C020. Scoped to the new pinout value so
        # SRAM_PINOUT (which handles 28-pin pm_idx=0 re-routes) is not masked.
        # Pinout-only scope is now ENFORCED here (not just asserted in
        # prose) — a co-occurring voltage/type/vpp change on a DIP32_27C020 chip falls
        # through to a more specific/generic rule instead of being absorbed silently.
        label = "RC1_DIP32_27C020"
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
        # (no collateral change to chips sharing the same infoic flags).
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
        # VARIANT_DECODE (before BUG_A_ETYPE): consolidation electrical.type
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
        # RULE_PHASE66: only the support-status fields changed
        # (support_status, unsupported_reason,
        # electrical.vpp, electrical.vpp_mv). Placed LAST so it does not shadow
        # BUG_A_ETYPE/BUG_B_VPP (Pitfall 7 in 70-RESEARCH.md).
        label = "RULE_PHASE66"
    elif (
        bl_prog.get("page_size") != cu_prog.get("page_size")
        and not algo_diff
        and not timing_diff
        and not voltage_diff
        and not pinout_diff
        and not type_diff
        and not vpp_diff
    ):
        # PGSZ_PAGE_SIZE — only programming.page_size changed
        # (datasheet-sourced per-chip page size added for W29C040=256 / W29C020=128).
        # No other field changes. Placed LAST (most specific scope: programming.page_size
        # only) to avoid shadowing any compound changes detected by prior rules.
        label = "PGSZ_PAGE_SIZE"
    elif (
        (
            bl_prog.get("protect_off_before") != cu_prog.get("protect_off_before")
            or bl_prog.get("protect_on_after") != cu_prog.get("protect_on_after")
            or bl_prog.get("infoic_page_size_raw")
            != cu_prog.get("infoic_page_size_raw")
        )
        and not algo_diff
        and not timing_diff
        and not voltage_diff
        and not pinout_diff
        and not type_diff
        and not vpp_diff
        and bl_prog.get("page_size") == cu_prog.get("page_size")
    ):
        # PROV01_PROTECT_METADATA — only the three new
        # protect_off_before/protect_on_after/infoic_page_size_raw keys changed
        # (added). No other field changes, including the curated page_size (kept
        # distinct from infoic_page_size_raw by the explicit page_size equality
        # check above). Placed LAST (most specific: exactly these three new keys)
        # to avoid shadowing any compound changes detected by prior rules.
        label = "PROV01_PROTECT_METADATA"

    diff_paths = _diff_field_paths(bl_chip, cu_chip)

    if label is None:
        return None, diff_paths

    # A diff is fully explained only when every differing field
    # path is claimed by SOME known rule. The primary label explains its own
    # field set; any remaining differing paths are "extra" (secondary) deltas.
    explained = set(_RULE_FIELD_PATHS[label])
    extra_paths = diff_paths - explained
    return label, extra_paths


# ---------------------------------------------------------------------------
# Schema-normalizing comparator
# ---------------------------------------------------------------------------
def _voltage_str_to_mv(value):
    """Parse a voltage string like "4V" or "3.3V" -> integer millivolts.

    Narrow except (TypeError, ValueError) only (T-148-05) — an unparseable
    value maps to the documented 0 sentinel rather than crashing the gate.
    """
    try:
        return int(round(float(str(value).rstrip("V")) * 1000))
    except (TypeError, ValueError):
        return 0


def _pulse_str_to_us(value):
    """Parse a pulse-duration string like "100 us" -> integer microseconds.

    "Algorithm Controlled" and any other unparseable value map to the
    documented 0 sentinel (T-148-05), mirroring build_db.py's generator.
    """
    try:
        return int(round(float(str(value).split()[0])))
    except (TypeError, ValueError, IndexError):
        return 0


def _canonicalize_db(db):
    """Return a normalized copy of a chip database on the numeric schema.

    A pure-representation migration (string voltage/timing fields ->
    numeric mv/us fields) must produce zero additional diff rows.
    This function normalizes both the pre-migration string schema and the
    post-migration numeric schema to the same shape before comparison, so
    `_classify_diff`/`_diff_field_paths` always see one schema regardless of
    which side of the migration either input database is on (the
    pinned baseline is never re-pinned).

    Per-chip normalization rules (electrical.* / programming.* blocks):
      - electrical.vcc (str, e.g. "4V") -> electrical.vcc_mv (int); vcc removed.
      - electrical.vdd (str) -> electrical.vdd_mv (int); vdd removed.
      - electrical.vpp is dropped entirely — the migration deletes this key
        outright; vpp_mv already carries the value and its name/type do not
        change.
      - programming.pulse_duration (str, e.g. "100 us" / "Algorithm
        Controlled") -> programming.pulse_duration_us (int); "Algorithm
        Controlled" and any other unparseable value map to 0.
      - A chip already carrying vcc_mv / vdd_mv / pulse_duration_us (i.e. no
        old-schema key present) is passed through unchanged for that field —
        this is what makes the function idempotent and correct on both
        schemas.
    """
    out = copy.deepcopy(db)
    for chips in out.values():
        if not isinstance(chips, list):
            continue
        for chip in chips:
            elec = chip.get("electrical")
            if isinstance(elec, dict):
                if "vcc" in elec:
                    elec["vcc_mv"] = _voltage_str_to_mv(elec.pop("vcc"))
                if "vdd" in elec:
                    elec["vdd_mv"] = _voltage_str_to_mv(elec.pop("vdd"))
                elec.pop("vpp", None)
            prog = chip.get("programming")
            if isinstance(prog, dict) and "pulse_duration" in prog:
                prog["pulse_duration_us"] = _pulse_str_to_us(prog.pop("pulse_duration"))
    return out


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def _load_db(path, label):
    """Load a chip-database JSON, exiting 2 (infra error) on any load failure.

    A missing/malformed input is an infrastructure problem, NOT a diff
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

    # Canonicalize both databases to the numeric schema before any
    # comparison, so the gate classifies identically whether either side is on
    # the old string schema or the new numeric schema.
    bl_db = _canonicalize_db(bl_db)
    cu_db = _canonicalize_db(cu_db)

    bl_idx = _make_index(bl_db)
    cu_idx = _make_index(cu_db)

    # Raw record counts.
    bl_total = _raw_total(bl_db)
    cu_total = _raw_total(cu_db)

    # The composite-key index is strictly 1:1 with chip records.
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
    # whether a compound change's secondary deltas are benign-but-known (just
    # surface them) or truly outside all rules (escalate to
    # unexplained).
    _all_rule_paths = set()
    for _paths in _RULE_FIELD_PATHS.values():
        _all_rule_paths |= _paths

    # Partition chips into buckets. Keys are composite (mfg, pn[, i]); the
    # displayed value is the projected part_number via _pn().
    changed_by_cause: dict[str, list] = {k: [] for k in _RATIONALES}
    compound_notes: list[str] = []  # surfaced secondary deltas
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
                    # At least one differing field is outside ALL known
                    # rules — the diff is not fully attributable. Escalate.
                    unexplained.append(key)
                else:
                    changed_by_cause[cause].append(key)
                    if extra_paths:
                        # Compound change — primary cause plus secondary
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
    # Report: grouped-by-cause with embedded citations
    # -----------------------------------------------------------------------
    print("=" * 72)
    print("GATE-02 Per-chip Diff Report")
    # The index is 1:1 with records, so the diffed key count equals the
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

    # Surface compound changes — chips whose primary cause is accompanied
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

    # New chips fall into two explained categories:
    #   (a) EXTRA_CHIPS_SUPPLEMENT: records carrying the
    #       source="non-upstream-supplement" marker (2516/2532). Their presence —
    #       with the cited source marker — IS the explanation (they are absent from
    #       both infoic.xml and the OLD baseline because of the re-pin).
    #   (b) Rule 1 unblock (DIP24_2816 + algo=0x0D): the original new-chip class.
    # A new chip that is NEITHER is surfaced as a WARN.
    supplement_new = [
        key
        for key in new_chips
        if cu_idx[key][1].get("source") == "non-upstream-supplement"
    ]
    other_new = [key for key in new_chips if key not in set(supplement_new)]

    if supplement_new:
        print(
            f"--- NEW chips: non-upstream supplement ({len(supplement_new)}) "
            f"— Phase 86 VAR-05 / D-10 (cited) ---\n"
        )
        for line in _RATIONALES["EXTRA_CHIPS_SUPPLEMENT"].splitlines():
            print(f"  {line}")
        print()
        for key in sorted(supplement_new):
            c = cu_idx[key][1]
            ds = c.get("datasheet", "<no datasheet>")
            ver = c.get("verification_status", "")
            ver_str = f", {ver}" if ver else ""
            print(
                f"  {_pn(key)} [non-upstream-supplement] "
                f"(pinout={c.get('pinout')}, "
                f"algo={c.get('programming', {}).get('algorithm'):#x}, "
                f"datasheet={ds}{ver_str})"
            )
        print()

    # Verify (don't just assert) the Rule 1 unblock claim per remaining new chip.
    print(
        f"--- NEW chips ({len(other_new)}) — expected Rule 1 unblock (DIP24_2816 + algo=0x0D) ---\n"
    )
    for key in sorted(other_new):
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
    if other_new:
        print()

    print(f"--- MISSING chips ({len(missing_chips)}) ---\n")
    for key in sorted(missing_chips):
        print(f"  {_pn(key)}")
    if missing_chips:
        print()

    # -----------------------------------------------------------------------
    # Gate: unexplained diffs or missing chips = BLOCK (exit 1)
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
