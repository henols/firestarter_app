"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Community Chip-Validation Test-Plan Engine (v1.21 Phase 108)

Pure, bench-free compute layer for `firestarter dev test <chip>` (Phase 112):
- `address_fold_byte` / `generate_pattern` / `prepass_images` — an
  address-derived write/verify pattern (PATT-01, D-01/D-02) that exposes
  stuck/shorted/aliased address lines instead of hiding them behind a fixed
  pattern.
- `classify_fingerprint` — a four-bucket byte-mismatch classifier (PATT-02,
  D-03/D-04) that names WHY a verify failed (blank/contact, address-line,
  transport) or honestly falls back to `indeterminate` rather than
  over-confidently mis-diagnosing (this project's own false-PASS history:
  Bug A, ST-vs-Winbond chip-ID mixup, AM27C020 write#1/write#2 divergence).

This module is pure compute over host-side byte arrays: it sets no VPP and
calls no operator/firmware method itself. Plan 108-04 extends this module
with `run_plan` -- the non-fatal per-step executor that composes existing
`EpromOperator` methods only (still zero new firmware dispatch, zero
VPP-set). v1.30 Phase 134 DELIBERATELY NARROWS the "builds no wire dict"
half of that claim: this module passes exactly one `operation_flags` bit
(`FLAG_SKIP_SDP_UNLOCK`, `constants.py:137`) on exactly one op (the SDP
leg's inhibited-write step, `OP_WRITE_INHIBITED`, wired by plan 134-02's
`_dispatch_sdp_leg`) -- stated explicitly here rather than silently
violating the older, broader wording.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from firestarter import sdp_honesty  # unreadable_state_caveat(), called not re-authored
from firestarter.chip_resolver import resolve_chip
from firestarter.constants import (
    FLAG_CAN_ERASE,  # 0x02 -- do NOT redefine; import
    FLAG_SKIP_SDP_UNLOCK,  # 0x100 -- passed on OP_WRITE_INHIBITED ONLY (v1.30
    # Phase 134, T-134-02, D-01). Do NOT redefine; import.
)
from firestarter.exceptions import (
    ChipNotFoundError,
    ChipNotImplementedError,
    EpromOperationError,
    FirmwareOutdatedError,
    HardwareOperationError,
    HardwareRevisionUnsupportedError,
    ProgrammerNotFoundError,
    SerialError,
)
from firestarter.sdp_capability import sdp_capability  # LEG-01/02's derivation source

# ---------------------------------------------------------------------------
# Address-derived pattern generator (PATT-01, D-01/D-02)
# ---------------------------------------------------------------------------


def address_fold_byte(addr: int) -> int:
    """XOR-fold an absolute address into a single expected byte (D-01).

    Every address line (A0..A31) contributes to the expected byte via the
    fold, so a stuck/shorted/aliased high address line changes the expected
    byte at exactly the addresses where that bit flips -- unlike a fixed
    pattern (e.g. all-0x55), which is blind to address-line faults.
    """
    return (addr ^ (addr >> 8) ^ (addr >> 16) ^ (addr >> 24)) & 0xFF


def generate_pattern(start: int, length: int) -> bytes:
    """Region-parameterized address-derived pattern (D-02).

    Derives each byte from its ABSOLUTE address (`start + i`), never from
    the offset alone -- no full-chip assumption is baked in, so this same
    function serves both a full-chip pattern and a small high-address
    region (Phase 109's UV small-region write cap).
    """
    return bytes(address_fold_byte(start + i) for i in range(length))


def generate_inhibited_pattern(start: int, length: int) -> bytes:
    """The SDP leg's inhibited-write payload B (v1.30 Phase 134, D-19, LEG-03).

    ⚠ P-01, the milestone's headline pitfall: `generate_pattern` is a PURE
    function of `(start, length)`. Deriving B by calling `generate_pattern`
    a SECOND time -- with the same region, or with a "different seed" that
    reduces to the same region -- makes A and B byte-identical, and the
    leg's central assertion ("the chip did not accept a write while locked")
    a tautology that reads as correct in review. This function instead calls
    `generate_pattern(start, length)` exactly ONCE and returns its bitwise
    complement, so B is derived FROM A rather than independently re-derived
    -- A and B are guaranteed to differ at every byte by construction, never
    by chance.

    A nonce or timestamp was rejected (D-19): it would break reproducibility
    (two runs over the same region would no longer agree on B) and re-key
    `dedup_fingerprint` (diagnostic_report.py) on every single run, since
    `StepResult.op`'s hash is stable but this function's OUTPUT is not
    otherwise consumed by that hash -- the reproducibility argument is the
    one that matters here.
    """
    a = generate_pattern(start, length)
    return bytes(~b & 0xFF for b in a)


def prepass_images(length: int) -> tuple[bytes, bytes]:
    """Cheap all-0x00 / all-0xFF pre-pass images (PATT-01).

    A cheap sanity pre-pass before the address-derived pattern: an
    all-0xFF read-back before writing anything is itself evidence of a
    blank/contact condition (see `classify_fingerprint`).
    """
    return b"\x00" * length, b"\xff" * length


# ---------------------------------------------------------------------------
# Shared byte-diff-offset helper (D-04 -- reused, not reimplemented)
# ---------------------------------------------------------------------------
#
# Mirrors the exact divergence math in `consistency_check_eprom`
# (eprom_operations.py:842-863): cmp_len / diff_offsets / pct / first
# divergence offset. This is the ONE divergence primitive `classify_fingerprint`
# consumes -- do NOT add a second parallel divergence implementation
# elsewhere in this codebase (D-04 mandate). The math is small enough to
# copy rather than import, keeping this module import-light (no dependency
# on eprom_operations.py).


def _diff_offsets(
    expected: bytes, actual: bytes
) -> tuple[int, list[int], float, int | None]:
    """Return (cmp_len, diff_offsets, pct, first) for two byte arrays.

    `cmp_len` is `min(len(expected), len(actual))` -- unequal-length inputs
    are compared only over their common prefix and never raise.
    """
    cmp_len = min(len(expected), len(actual))
    diff_offsets = [o for o in range(cmp_len) if expected[o] != actual[o]]
    pct = 100.0 * len(diff_offsets) / cmp_len if cmp_len else 0.0
    first = diff_offsets[0] if diff_offsets else None
    return cmp_len, diff_offsets, pct, first


# ---------------------------------------------------------------------------
# Four-bucket byte-mismatch fingerprint classifier (PATT-02, D-03/D-04)
# ---------------------------------------------------------------------------

# The four locked outcome labels (D-03) -- never coerce an ambiguous
# distribution into one of the first three; fall back to indeterminate.
FP_BLANK_CONTACT = "blank/contact"
FP_ADDRESS_LINE = "address-line"
FP_TRANSPORT = "transport"
FP_INDETERMINATE = "indeterminate"

# Candidate thresholds (Claude's discretion, D-04) -- direction is
# HIGH-confidence, exact numbers are tunable/bench-informed later. A wrong
# number only produces more `indeterminate`, never a false confident label.
_FF_RATIO_THRESHOLD = 0.98  # blank/contact: >= this fraction of actual == 0xFF
_BIT_CLUSTER_THRESHOLD = 0.9  # address-line: >= this fraction of mismatches
# share one polarity of one high address bit


@dataclass
class Fingerprint:
    """Verdict + raw evidence for a single expected-vs-actual byte compare."""

    total: int
    bad: int
    bad_pct: float
    classification: str
    evidence: dict = field(default_factory=dict)


def classify_fingerprint(
    expected: bytes,
    actual: bytes,
    *,
    repeat_divergent: bool | None = None,
    addr_base: int = 0,
) -> Fingerprint:
    """Classify a byte-mismatch pattern into one of four honest buckets.

    Consumes the shared `_diff_offsets` divergence primitive (D-04 -- the
    same math `consistency_check_eprom` uses for run1-vs-run2 divergence,
    here applied to expected-pattern-vs-read-back). Never writes a second
    divergence implementation.

    Classification order is LOCKED (D-04):
      1. blank/contact  -- cheapest, most common false-PASS source
      2. address-line   -- power-of-two high-bit clustering (needs addr_base
                            to map offsets to ABSOLUTE addresses, Pitfall 3)
      3. transport       -- scattered + non-repeatable across N>=2 runs
      4. indeterminate   -- fallback; NEVER coerce an ambiguous distribution
                            into a confident label (D-03).
    """
    cmp_len, diff_offsets, bad_pct, first_offset = _diff_offsets(expected, actual)
    bad = len(diff_offsets)

    ff_count = sum(1 for b in actual[:cmp_len] if b == 0xFF)
    ff_ratio = (ff_count / cmp_len) if cmp_len else 0.0

    evidence: dict = {
        "ff_ratio": ff_ratio,
        "repeat_divergent": repeat_divergent,
        "first_offset": first_offset,
        "bit_clustering": {},
    }

    # 1. blank/contact: read-back is near-all 0xFF (un-driven bus / contact
    # fault). Checked first regardless of whether there are zero mismatches
    # (a perfect verify) or the pattern never matched at all.
    if ff_ratio >= _FF_RATIO_THRESHOLD:
        return Fingerprint(
            total=cmp_len,
            bad=bad,
            bad_pct=bad_pct,
            classification=FP_BLANK_CONTACT,
            evidence=evidence,
        )

    # 2. address-line: mismatches concentrate on one polarity of a single
    # high address bit (A8+). Map each mismatch offset to its ABSOLUTE
    # address (addr_base + offset) before clustering (Pitfall 3) -- else
    # the signal is computed against the wrong bits. Candidate bits are
    # restricted to those that can actually vary within [0, cmp_len), i.e.
    # 8 <= k < ceil(log2(cmp_len)); bits at or above that never toggle
    # within the compared region and would spuriously "cluster" at 100%.
    suspected_line = None
    best_score = 0.0
    if bad and cmp_len > (1 << 8):
        max_bit = (cmp_len - 1).bit_length()
        for k in range(8, max_bit):
            mask = 1 << k
            set_count = sum(1 for o in diff_offsets if (addr_base + o) & mask)
            clear_count = bad - set_count
            score = max(set_count, clear_count) / bad
            evidence["bit_clustering"][k] = score
            if score > best_score:
                best_score = score
                suspected_line = k

    if suspected_line is not None and best_score >= _BIT_CLUSTER_THRESHOLD:
        evidence["suspected_line"] = suspected_line
        evidence["cluster_score"] = best_score
        return Fingerprint(
            total=cmp_len,
            bad=bad,
            bad_pct=bad_pct,
            classification=FP_ADDRESS_LINE,
            evidence=evidence,
        )

    # 3. transport: scattered (no dominant high bit, checked above) AND
    # non-repeatable across the N>=2 runs (caller-supplied signal from
    # run1-vs-run2 divergence -- the uno328pb signature).
    if repeat_divergent is True:
        return Fingerprint(
            total=cmp_len,
            bad=bad,
            bad_pct=bad_pct,
            classification=FP_TRANSPORT,
            evidence=evidence,
        )

    # 4. indeterminate: never coerce an ambiguous distribution (D-03).
    return Fingerprint(
        total=cmp_len,
        bad=bad,
        bad_pct=bad_pct,
        classification=FP_INDETERMINATE,
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Plan derivation (SWEEP-01, D-01/D-02) -- guard-BYPASSING derivation path
# ---------------------------------------------------------------------------
#
# derive_plan() reads the frozen DB fields via db.get_eprom() (database.py:506)
# and db.convert_to_programmer() (database.py:535) ONLY. The support-status
# guard lives exclusively inside chip_resolver.resolve_chip() (chip_resolver.py:16)
# -- derivation never calls it, so a chip whose support_status would make
# resolve_chip refuse it (e.g. "adapter-required") still yields a full plan
# (Pattern 2 / T-108-06). The guard-HONORING path is Plan 108-04's run_plan,
# which re-resolves each executed step through resolve_chip(name, db).
#
# Op inclusion is a PURE function of the frozen fields protocol-id /
# electrical-type / FLAG_CAN_ERASE -- the build-time classifier
# (tools/build_db.py) already froze its result into the DB and is never
# re-invoked at runtime here.

# Protocol 0x05 (FLASH_AMD_STD / "flash4") auto-erases per page during the
# page-write; convert_to_programmer() deliberately clears FLAG_CAN_ERASE for
# this protocol (database.py:582-595) because setting it would route a 12V
# bulk-erase onto a 5V-only part (Pitfall 6). No named constant for this
# protocol id exists in constants.py -- mirror database.py's own `algo != 5`
# check rather than introduce a new cross-module constant.
_PROTOCOL_FLASH4 = 0x05

# Protocol 0x0D (EEPROM_POLL / "28C family", firmware's configure_eeprom28c)
# has no erase operation at all -- Phase 121 D-12 clears FLAG_CAN_ERASE for
# this protocol at the source (database.py:582-...) so derive_plan's
# generic NA-erase else-branch fires for it "for free". This constant exists
# so the family-fact NA reason arm below names the protocol by symbol, not a
# bare literal.
_PROTOCOL_EEPROM_28C = 0x0D

# SRAM/FRAM electrical types and protocol ids: blank-check has no meaningful
# concept for volatile/byte-rewritable memory. derive_plan owns this NA
# decision up front (RESEARCH nuance recommendation (a)) rather than relying
# on check_eprom_blank's own short-circuit (eprom_operations.py:1656-1676),
# which the plan mirrors here for the SAME protocol-id set.
_SRAM_FRAM_ETYPES = frozenset({"SRAM", "FRAM"})
_SRAM_PROTO_IDS = frozenset({0x0E, 0x27, 0x28, 0x29})

# Ordered op vocabulary (id-check FIRST per SWEEP-03). Seven strings as of
# Phase 121 D-06/D-07 (this plan): `OP_WRITE_PARTIAL` joins the vocabulary so
# the partial-vs-full distinction is visible in the op name itself -- every
# consumer that reads `StepResult.op` (the `dedup_fingerprint` hash, the
# report renderer) sees it without learning a new field. D-07 deliberately
# stops the vocabulary here: no `verify-partial` partner exists, because a
# verify's region is definitionally the preceding write's region (D-07,
# `Step.write_region` is set equal on both steps by `derive_plan`) -- a
# partner string would encode zero new information.
OP_ID = "id"
OP_READ = "read"
OP_BLANK_CHECK = "blank-check"
OP_WRITE = "write"
OP_WRITE_PARTIAL = "write-partial"
OP_VERIFY = "verify"
OP_ERASE = "erase"

# SDP lock/unlock op strings (v1.30 Phase 133 D-02, LEG-09). Exactly two --
# Phase 133 defines only the two ops its own mechanism criteria exercise;
# Phase 134's other leg ops are deliberately NOT pre-defined here (`ruff`'s
# `F` rules do not flag unused module-level constants, so extra constants
# would be genuinely dead code for a whole phase). Engine-local op strings,
# NOT wire constants -- no `constants.py` / `firestarter.h` mirroring is
# triggered by adding these.
OP_SDP_LOCK = "sdp-lock"
OP_SDP_UNLOCK = "sdp-unlock"

# The SDP leg's four remaining op strings (v1.30 Phase 134, D-06/D-07,
# LEG-01/02/03/04/16). Engine-local op strings, NOT wire constants -- no
# `constants.py` / `firestarter.h` mirroring is triggered by adding these,
# so this phase needs no firmware lockstep and no `.hex` re-cut. Ordered in
# the leg's own D-06 step order (baseline-B, baseline-A, inhibited,
# restored) so a reader scanning top-to-bottom sees the same order the leg
# runs in.
#
# D-07 chose TWO baseline ops (`write-baseline-b` / `write-baseline-a`)
# rather than one folded `sdp-baseline` op: `DiagnosticReport.render()`'s
# terminal-facing table shows only `op` / `verdict` / `error_code` /
# `fingerprint` -- `reason` reaches only the markdown table and the JSON
# block, so a failing baseline *direction* hidden inside `reason` would be
# invisible to whoever reads the terminal, on the very step that decides
# whether a lock is emitted at all. Two op strings make the failing
# direction legible in the op string itself, mirroring the `write-partial`
# precedent above ("every consumer that reads `StepResult.op` sees it
# without learning a new field").
OP_WRITE_BASELINE_B = "write-baseline-b"
OP_WRITE_BASELINE_A = "write-baseline-a"
OP_WRITE_INHIBITED = "write-inhibited"
OP_WRITE_RESTORED = "write-restored"

# The leg's own D-06 step order, single-sourced (v1.30 Phase 134, plan
# 134-03, LEG-01/02/04). `derive_plan`'s emission below appends these six
# `Step`s in EXACTLY this order, and every count assertion downstream
# derives "six" from `len(_SDP_LEG_STEP_ORDER)` rather than restating the
# number as a literal (P-08's derive-don't-restate discipline).
#
# ⚠ CORRECTION 2, recorded here rather than silently reconciled: ROADMAP
# criterion 1, LEG-01 and LEG-02 all say the leg is a **four**-step
# sequence. That text predates LEG-04's requirement for TWO transition
# directions (a single baseline write cannot discriminate a dead write path
# from a chip already holding the target pattern), and the ROADMAP's own
# four-step enumeration omits `write-restored` entirely -- the ONLY step
# that produces evidence the part was left writable again, on a family
# whose protection state cannot be read back at all (`sdp_honesty`'s
# Evidence Ceiling). Dropping it would end every run on `sdp-unlock OK` --
# an EMISSION claim with nothing behind it (D-06). The leg implemented here
# is SIX steps. Both readings (the inherited four, and the measured six)
# are recorded in 134-03-SUMMARY.md; this is not a silent widening.
_SDP_LEG_STEP_ORDER: tuple[str, ...] = (
    OP_WRITE_BASELINE_B,
    OP_WRITE_BASELINE_A,
    OP_SDP_LOCK,
    OP_WRITE_INHIBITED,
    OP_SDP_UNLOCK,
    OP_WRITE_RESTORED,
)

# D-18's `write_scope="none"` advisory prose, in the same
# `'write_scope="none": ... omitted (D-01)'` shape the shipped write/verify/
# erase `locked_destructive` reasons already use above -- naming the SDP
# leg's own governing decision (D-18) rather than reusing D-01's tag on a
# reason it does not own.
_SDP_LOCKED_REASON = 'write_scope="none": {op} omitted (D-18)'


@dataclass
class Step:
    """A single derived operation descriptor.

    `supported=False` means the step is NA for this chip (a reason is always
    recorded); `destructive` marks steps that write/erase the part. As of
    Phase 109 (D-01, SAFE-01) a `destructive=False` call to `derive_plan`
    structurally OMITS these steps from `Plan.steps` -- see `Plan.
    locked_destructive` for where they are recorded instead.

    `write_region` (D-02, this plan) is the CONSEQUENCE of `Plan.is_uv`: set
    once by `derive_plan` as `(start, length)` on both the write step and the
    verify step (a verify's region is definitionally the preceding write's --
    D-07). `None` means "use the engine default region". The WIDTH always
    originates from a module constant (`_WRITE_REGION_LENGTH` or
    `_UV_WRITE_REGION_LENGTH`) and NEVER from a DB field (SC4 -- a malicious
    or misconfigured DB entry must not be able to widen the write window);
    `memory-size` only bounds WHERE the window is placed. `derive_plan` sets
    this field and only this field; every downstream reader (`run_plan`, the
    execution layer) may only READ it, never re-derive it.
    """

    op: str
    supported: bool
    reason: str
    destructive: bool = False
    write_region: tuple[int, int] | None = None


@dataclass
class Plan:
    """Ordered, derived test plan for a single chip (SWEEP-01).

    `locked_destructive` (D-01, SAFE-01) is an ADVISORY-ONLY field: it
    records the `(op, reason)` of write/erase steps that a `destructive=True`
    call would have added to `steps` but were omitted because the caller
    passed `destructive=False`. `run_plan` MUST NOT iterate this field --
    it exists solely so the SWEEP-05 banner / Phase-110 report can still
    count M (the steps a `--destructive` run would execute) without a
    second `derive_plan` call and without ever giving the executor a code
    path to a destructive op in a non-destructive run. As of Phase 121
    (this plan's D-02 correction) this list becomes permanently empty in
    production after plan `121-09` lands -- no CLI path will reach
    `write_scope="none"` any longer. The field and the N-of-M banner are
    nonetheless KEPT, not deleted: the banner renders unconditionally and
    still carries signal whenever the chip-ID destructive gate closes or
    `resolve_chip` refuses a step (RESEARCH C-6). Removal is an explicitly
    deferred cleanup, not this phase's work.

    `is_uv` (D-02, this plan) is THE DECISION: whether this chip is a
    UV-erasable EPROM, decided EXACTLY ONCE by `derive_plan` from the `full`
    DB dict's `electrical-type` field (the only axis that is both complete
    and exact -- 301 of 301 UV parts, 0 non-UV wrongly included; see
    `is_uv_eprom`). `run_plan` and the execution layer may only READ this
    field -- nothing downstream may re-derive UV-ness from a proxy (e.g. the
    execution-time `algorithm == 0x0B` guess, which only matches 32 of 301).
    """

    name: str
    steps: list[Step] = field(default_factory=list)
    reason: str = ""
    locked_destructive: list[tuple[str, str]] = field(default_factory=list)
    is_uv: bool = False


def is_uv_eprom(full: dict) -> bool:
    """Exact, name-keyed UV-EPROM predicate (D-02, DEVTEST-03 axis).

    Measured exact at 301/301 against the live database: every DB entry
    whose `electrical-type` is `"UV-EPROM"` and none whose isn't. Takes the
    **`full`** DB dict from `db.get_eprom(name)` -- NEVER `resolve_chip`'s
    /`convert_to_programmer`'s programmer dict, which does not carry
    `electrical-type` and is unreachable from `derive_plan`'s callers at
    execution time.

    Rejected alternatives: the execution-time `algorithm == 0x0B` proxy
    (`_write_region_for`'s pre-existing guess) matches only 32 of 301 UV
    parts -- 269 silently fall through; widening to
    `{0x07, 0x08, 0x0B}` recovers 301/301 but wrongly includes 28 non-UV
    EEPROMs (e.g. `W27C512`), forfeiting the `0x0B`-implies-UV exclusivity
    property. Neither alternative is exact; only the `electrical-type` field
    is.

    Consequence under D-01: a UV part that fails this test receives an
    UNPROMPTED FULL-DEVICE WRITE. A guess here is a chip-destroying bug, not
    a coverage gap.
    """
    return full.get("electrical-type", "") == "UV-EPROM"


_WRITE_SCOPE_NONE = "none"
_WRITE_SCOPE_PARTIAL = "partial"
_WRITE_SCOPE_FULL = "full"
_WRITE_SCOPES = frozenset({_WRITE_SCOPE_NONE, _WRITE_SCOPE_PARTIAL, _WRITE_SCOPE_FULL})


def derive_plan(name: str, db: Any, *, write_scope: str = "none") -> Plan:
    """Derive the ordered op list for `name` strictly from frozen DB fields.

    Reads `db.get_eprom(name)` then `db.convert_to_programmer(full)` --
    NEVER `chip_resolver.resolve_chip` (Pattern 1/2, T-108-06) -- so this
    works even for chips whose `support_status` would make `resolve_chip`
    refuse them. `write_scope` is read ONLY from this call's kwarg -- never
    from config or environment (SAFE-01).

    Three accepted literal values, fail-closed against anything else:

    - `"none"` -- write/verify/erase are structurally OMITTED from the
      returned `Plan.steps` and instead recorded as `(op, reason)` tuples on
      the advisory `Plan.locked_destructive` field (D-01) -- `run_plan` has
      no code path to iterate them.
    - `"full"` -- write, verify and erase are real supported steps in that
      order; `locked_destructive` is empty.
    - `"partial"` -- same step list as `"full"`, but the write/verify steps'
      `Step.write_region` is the top-anchored UV window instead of the
      engine default. Plan `121-06` will swap this scope's emitted write op
      to `OP_WRITE_PARTIAL`; here it is still `OP_WRITE`.

    An unrecognised `write_scope` raises `ValueError` naming the offending
    value and the three accepted literals -- this function never silently
    falls back to a mode that writes.

    `Plan.is_uv` is decided HERE and ONLY HERE, from `is_uv_eprom(full)` --
    the only axis that is both complete and exact (301/301). `Step.
    write_region` is likewise set HERE and ONLY HERE, on both the write step
    and the verify step (a verify's region is definitionally the preceding
    write's -- D-07); downstream code may only READ these two fields, never
    re-derive them. The write-region WIDTH always comes from a module
    constant (`_UV_WRITE_REGION_LENGTH` / `_WRITE_REGION_LENGTH`), NEVER from
    any DB field (SC4) -- `memory-size` only bounds WHERE the window sits.

    Unknown chips (no DB entry) return an empty `Plan` with `reason` set --
    there is nothing to derive.
    """
    if write_scope not in _WRITE_SCOPES:
        raise ValueError(
            f"derive_plan: unrecognised write_scope {write_scope!r} -- "
            f"must be one of {sorted(_WRITE_SCOPES)!r}"
        )

    full = db.get_eprom(name)
    if not full:
        return Plan(name=name, steps=[], reason=f"{name}: not found in database")

    prog = db.convert_to_programmer(full)
    protocol = prog.get("algorithm", full.get("protocol-id", 0))
    etype = full.get("electrical-type", "")
    can_erase = bool(prog.get("flags", 0) & FLAG_CAN_ERASE)
    chip_id = prog.get("chip-id", 0)
    is_uv = is_uv_eprom(full)
    write_execute = write_scope in (_WRITE_SCOPE_FULL, _WRITE_SCOPE_PARTIAL)

    # Region computation lives HERE, in derive_plan, computed from Plan.is_uv
    # and full["memory-size"] -- never from any DB WIDTH field (SC4). The
    # WIDTH always comes from _UV_WRITE_REGION_LENGTH / _WRITE_REGION_LENGTH.
    #
    # "full" reproduces today's execution-time _write_region_for exactly:
    # is_uv picks the top-anchored window (with its defensive fallback),
    # non-UV gets the engine default region. "partial" always applies the
    # top-anchored-window-or-fallback formula regardless of is_uv -- its
    # whole purpose is the small-region write, so it is not is_uv-gated here.
    if write_scope == _WRITE_SCOPE_FULL:
        write_region = _top_anchored_or_default(full) if is_uv else _DEFAULT_REGION
    elif write_scope == _WRITE_SCOPE_PARTIAL:
        write_region = _top_anchored_or_default(full)
    else:
        write_region = None

    steps: list[Step] = []
    locked_destructive: list[tuple[str, str]] = []

    # id-check: ALWAYS first (SWEEP-03). Supported only when the chip
    # carries a real (nonzero) chip-id to compare against -- the sentinel
    # value 0 means "no chip-id in DB entry" (Open Question 2 -> NA).
    if chip_id:
        steps.append(Step(op=OP_ID, supported=True, reason=""))
    else:
        steps.append(Step(op=OP_ID, supported=False, reason="no chip-id in DB entry"))

    # read / verify: always supported -- every protocol reads.
    steps.append(Step(op=OP_READ, supported=True, reason=""))

    # blank-check: NA for SRAM/FRAM (derive_plan owns this decision up
    # front, mirroring check_eprom_blank's own short-circuit by BOTH
    # electrical-type and protocol-id, since the programmer dict passed to
    # the operator lacks those keys -- RESEARCH § nuance recommendation a).
    if etype in _SRAM_FRAM_ETYPES or protocol in _SRAM_PROTO_IDS:
        steps.append(
            Step(
                op=OP_BLANK_CHECK,
                supported=False,
                reason=(
                    f"blank-check not applicable to {etype or 'unknown'} "
                    "(volatile/byte-rewritable, no factory-blank state)"
                ),
            )
        )
    else:
        steps.append(Step(op=OP_BLANK_CHECK, supported=True, reason=""))

    # write: always supported, always flagged destructive. When
    # write_scope="none" the step is OMITTED from the executable `steps`
    # list -- structurally absent, not skipped at exec time (D-01, SAFE-01)
    # -- and recorded on the advisory `locked_destructive` list instead.
    # write_scope="partial" emits `OP_WRITE_PARTIAL` instead of `OP_WRITE`
    # (D-06, Phase 121 Plan 06) so the partial-vs-full distinction is visible
    # in the op string itself, everywhere `StepResult.op` is read.
    if write_execute:
        write_op = OP_WRITE_PARTIAL if write_scope == _WRITE_SCOPE_PARTIAL else OP_WRITE
        steps.append(
            Step(
                op=write_op,
                supported=True,
                reason="",
                destructive=True,
                write_region=write_region,
            )
        )
    else:
        locked_destructive.append(
            (OP_WRITE, 'write_scope="none": write omitted (D-01)')
        )

    # verify: always supported, but only executable on a write-executing
    # plan -- it follows the same D-01 write/erase gating (there is no
    # preceding write on a non-executing run, so a bare verify would compare
    # a freshly-generated pattern against unrelated chip contents).
    # Positioned after write and before erase so the destructive step order
    # (write, verify, erase) is unchanged. Its write_region equals the write
    # step's -- a verify's region is definitionally the preceding write's
    # (D-07).
    if write_execute:
        steps.append(
            Step(op=OP_VERIFY, supported=True, reason="", write_region=write_region)
        )
    else:
        locked_destructive.append(
            (OP_VERIFY, 'write_scope="none": verify omitted (D-01)')
        )

    # erase: supported only if FLAG_CAN_ERASE is set AND protocol != 0x05
    # (flash4 auto-erases per page; the flag is deliberately clear for it --
    # Pitfall 6). UV-EPROM never has the flag set (electrical-type is not in
    # {EEPROM, Flash/EEPROM}) so it is NA here for the same condition.
    if can_erase and protocol != _PROTOCOL_FLASH4:
        if write_execute:
            steps.append(Step(op=OP_ERASE, supported=True, reason="", destructive=True))
        else:
            locked_destructive.append(
                (OP_ERASE, 'write_scope="none": erase omitted (D-01)')
            )
    else:
        if protocol == _PROTOCOL_FLASH4:
            reason = "flash4 (0x05) auto-erases per page; no separate erase op"
        elif etype == "UV-EPROM":
            reason = "UV-EPROM has no electrical erase (UV light only)"
        elif protocol == _PROTOCOL_EEPROM_28C:
            # Phase 121 D-12 deliberately routes protocol 0x0D through this
            # generic else (no 0x0D-local supported/unsupported branch was
            # added) -- but the generic fallback's flag-keyed wording below
            # names an internal mechanism, not a fact a community tester can
            # act on. DEVTEST-01 requires the FAMILY FACT: protocol 0x0D and
            # the 28C family simply has no erase operation, ever -- never
            # the flag name.
            reason = (
                "protocol 0x0D (28C family) has no erase operation; "
                "each page write auto-erases internally"
            )
        else:
            reason = "FLAG_CAN_ERASE not set for this chip"
        # NA erase is never a supported executable step regardless of the
        # write_scope -- there is nothing to lock/omit here (it was never
        # runnable), so it is NOT added to locked_destructive either.
        steps.append(
            Step(op=OP_ERASE, supported=False, reason=reason, destructive=True)
        )

    # SDP leg emission (v1.30 Phase 134, D-06/D-07/D-18/D-20, LEG-01/02/04).
    # Appended as a CONTIGUOUS block at the END of the step list, after the
    # erase arm -- no shipped step's index moves (the existing
    # `d_ops.index(OP_VERIFY) < d_ops.index(OP_ERASE)`-shaped comparisons
    # stay true). Derived from `sdp_capability(name, db)` -- the injected
    # decision source (LEG-01) -- never a re-implemented protocol/pinout
    # heuristic; `sdp_capability` is itself fail-closed and count-pinned at
    # 43 ALLOW / 41 REFUSE / 84 total. No new CLI option is introduced by
    # this: `derive_plan`'s signature gains no parameter, so `dev test`
    # keeps zero options (LEG-01's own constraint).
    sdp_allowed, sdp_reason = sdp_capability(name, db)
    if write_execute:
        if sdp_allowed:
            # ALLOW chip, a real `dev test` run: six real, executable steps,
            # sharing the SAME write_region the shipped write arm above
            # already computed (never re-derived -- ALLOW chips are all
            # non-UV, per D-17, so this is always `_DEFAULT_REGION`).
            for sdp_op in _SDP_LEG_STEP_ORDER:
                steps.append(
                    Step(
                        op=sdp_op,
                        supported=True,
                        reason="",
                        destructive=True,
                        write_region=write_region,
                    )
                )
        else:
            # REFUSE chip, a real `dev test` run: six NA steps carrying
            # sdp_capability()'s OWN refusal prose verbatim (LEG-02).
            # `run_plan:877-879`'s existing NA path turns each into a
            # `_skip_result(..., verdict=VERDICT_NA)` with NO operator
            # call -- zero new machinery needed for LEG-02.
            for sdp_op in _SDP_LEG_STEP_ORDER:
                steps.append(
                    Step(
                        op=sdp_op,
                        supported=False,
                        reason=sdp_reason,
                        destructive=True,
                    )
                )
    elif sdp_allowed:
        # ALLOW chip, write_scope="none": all six steps go to the advisory
        # `locked_destructive` list instead of `steps` (D-18, mirroring the
        # shipped write/verify/erase treatment above) -- these entries DO
        # count toward count_applicable's M, so N < M and the banner fires,
        # matching D-15's polarity.
        for sdp_op in _SDP_LEG_STEP_ORDER:
            locked_destructive.append((sdp_op, _SDP_LOCKED_REASON.format(op=sdp_op)))
    # else: REFUSE chip, write_scope="none" -- emit NOTHING (neither a step
    # nor a locked_destructive entry). This is a Claude's-Discretion
    # refinement of D-18, taken on four measurements (134-CONTEXT.md D-18's
    # plan-time refinement, recorded in 134-03-SUMMARY.md):
    #   (1) tests/test_chip_test_sdp_leg.py::test_empty_registry_noop is
    #       LEG-10's named evidence in REQUIREMENTS.md and asserts M8720's
    #       (a REFUSE chip) write_scope="none" plan is UNCHANGED
    #       (len(results) == 3) -- emitting NA steps here would turn that
    #       proof RED, a regression-floor breach.
    #   (2) tests/test_chip_test.py's shipped exact-equality
    #       locked_destructive/locked_ops assertions for M8720 and AM2716
    #       (both measured REFUSE chips) would break if six entries were
    #       added to locked_destructive here.
    #   (3) the house rule at tests/test_chip_test.py's NA-erase precedent:
    #       an UNSUPPORTED step must never be fabricated as a
    #       runnable/locked step -- a REFUSE chip's SDP steps are
    #       unsupported by construction, so locked_destructive (an
    #       ADVISORY-ONLY list of steps a destructive run WOULD run) is the
    #       wrong home for them.
    #   (4) write_scope="none" is UNREACHABLE from `dev test` since Phase
    #       121's reversal (`_resolve_write_scope` returns only "full"/
    #       "partial") -- so on every REACHABLE `dev test` run, REFUSE
    #       chips DO receive the six NA steps (the `write_execute` branch
    #       above), and LEG-02 is fully satisfied on the live path. This
    #       branch is library/test surface only, never a live gate.

    return Plan(
        name=name,
        steps=steps,
        reason="",
        locked_destructive=locked_destructive,
        is_uv=is_uv,
    )


def _top_anchored_or_default(full: dict) -> tuple[int, int]:
    """Top-anchored high-address window, or the engine default region (D-02).

    Always computes `(mem_size - _UV_WRITE_REGION_LENGTH,
    _UV_WRITE_REGION_LENGTH)` from `full["memory-size"]` when it is large
    enough to fit the window, with a defensive fallback to the engine
    default `(_WRITE_REGION_START, _WRITE_REGION_LENGTH)` when `memory-size`
    is missing or too small (a fallback that would otherwise produce a
    negative start). The WIDTH always comes from the `_UV_WRITE_REGION_LENGTH`
    module constant -- never from any DB field (SC4); `memory-size` only
    bounds WHERE the window sits. Never returns `None` -- both callers
    (`write_scope="full"` for a UV part, `write_scope="partial"`
    unconditionally) want a concrete region, not "use the engine default"
    deferred to a downstream reader.
    """
    mem_size = int(full.get("memory-size", 0) or 0)
    if mem_size >= _UV_WRITE_REGION_LENGTH:
        return mem_size - _UV_WRITE_REGION_LENGTH, _UV_WRITE_REGION_LENGTH
    return _DEFAULT_REGION


# ---------------------------------------------------------------------------
# Non-fatal per-step executor (SWEEP-02/03/04, RPT-03) -- guard-HONORING
# execution path
# ---------------------------------------------------------------------------
#
# run_plan() re-resolves EVERY executed step through chip_resolver.resolve_chip
# (Pattern 2 / Pitfall 2) -- it NEVER reuses derive_plan's guard-bypassing
# dict. Each step runs inside its own try/except (Pattern 6 / Pitfall 1): one
# step's BAD verdict or exception NEVER aborts the remaining steps (the
# W29C040 locked-boot-block lesson -- the surprise IS the value). The engine
# dispatches to the existing EpromOperator methods only -- it sets no VPP,
# builds no wire dict, and passes no --force.

# Verdict vocabulary (SWEEP-02). `MARGINAL` is destructive/verify-only (D-06,
# wired in Task 3) -- never forced onto read-step disagreement.
VERDICT_OK = "OK"
VERDICT_BAD = "BAD"
VERDICT_NA = "NA"
VERDICT_SKIPPED = "SKIPPED"
VERDICT_MARGINAL = "marginal"

# Ops that mutate the chip -- gated by the id-first destructive_gate (SWEEP-03)
# and run N>=2 with a `marginal`-on-disagreement policy (SWEEP-04, Task 3).
# This is the ONLY live safety use of either frozenset in this module: it is
# the exact set `run_plan`'s chip-ID destructive gate (`if step.op in
# _DESTRUCTIVE_OPS and destructive_gate_closed:`) consults before admitting a
# step. `OP_WRITE_PARTIAL` joins it here (D-06, Phase 121 Plan 06) precisely
# because a partial write is still a write -- a write-shaped op absent from
# this frozenset would write to a misidentified chip ungated by the chip-ID
# mismatch check, which is a critical-severity correctness bug, not a
# cosmetic omission.
#
# `OP_SDP_LOCK` joins it here too (v1.30 Phase 133 D-11, LEG-09): a lock
# applied to a MISIDENTIFIED chip is exactly the harm this gate exists to
# prevent, and membership is also what makes criterion 3's
# gate-closed-from-the-start case observable at all -- a lock that cannot be
# gated can never be SKIPPED. `OP_SDP_UNLOCK` is deliberately ABSENT: a
# destructive gate closing AFTER the lock succeeded must never be able to
# skip the unlock and ship a locked part. That asymmetry IS LEG-09. In Phase
# 133 this absence is forward-protection for Phase 134 (where the unlock
# becomes step 4 of the derived leg) -- it is NOT a live Phase 133 path,
# because this phase derives no SDP step; the unlock here is only reachable
# via a directly-constructed test Step or the cleanup registry (133-04).
#
# The four SDP-leg ops (v1.30 Phase 134, LEG-03) join here too: each one
# mutates the part (a baseline write, the inhibited write, or the restore
# write), so the chip-ID destructive gate must cover them exactly like any
# other write-shaped op. `OP_SDP_UNLOCK` stays the one deliberate exception
# above -- widening this set never touches that asymmetry.
_DESTRUCTIVE_OPS = frozenset(
    {
        OP_WRITE,
        OP_WRITE_PARTIAL,
        OP_ERASE,
        OP_SDP_LOCK,
        OP_WRITE_BASELINE_B,
        OP_WRITE_BASELINE_A,
        OP_WRITE_INHIBITED,
        OP_WRITE_RESTORED,
    }
)
# LIVE DISPATCH ALLOW-LIST (121-02, T-121-05/06/07). Originally documented as
# only the N>=2 disagreement-policy set (D-06: destructive/verify ONLY --
# write, erase, verify; read disagreement is a divergence metric, never a
# verdict flip) -- but RESEARCH C-5 / Open Question 4 found this frozenset had
# ZERO references anywhere in the tree before this change: `_dispatch_step`'s
# trailing `return _dispatch_multi_run(...)` was unconditional, and
# `_dispatch_multi_run`'s run loop ended in a bare `else: # OP_ERASE`, so ANY
# op string reached `operator.erase_eprom()` and reported `VERDICT_OK`
# (RESEARCH Pitfall 1a, proven empirically: an unmapped op called
# erase_eprom() twice and returned OK). This is now the dispatch allow-list
# both `_dispatch_step` and `_dispatch_multi_run` gate on -- the host mirror
# of Phase 119 D-06/D-07's firmware NULL-`main` refusal
# (`operation_utils.cpp::op_execute_stateful_operation`). Made LIVE, not
# documented dead: `OP_WRITE_PARTIAL` (Phase 121 Plan 06, D-06) is added here
# too -- any future op added to the vocabulary MUST be added to both
# frozensets in this block or it fails closed by construction (proven by a
# deliberate-break test, plan 121-06 Task 3).
#
# `OP_SDP_LOCK`/`OP_SDP_UNLOCK` are DELIBERATELY EXCLUDED here (v1.30 Phase
# 133 D-03, LEG-09) -- and the exclusion is one of plan 133-06's asserted
# parity exemptions, not an omission: running a lock twice is a second
# mutation with no comparison value, and this set's marginal-on-disagreement
# policy is meaningless for an emission whose result cannot be read back at
# all -- SDP protection state is not readable on this family (Phase 117 D-05,
# Phase 119 D-12). SDP emissions are single-run; they dispatch through
# `_dispatch_sdp` instead (`_SDP_OPS`, below).
_MULTI_RUN_OPS = frozenset({OP_WRITE, OP_WRITE_PARTIAL, OP_ERASE, OP_VERIFY})

# LIVE DISPATCH ALLOW-LIST for the SDP arm (v1.30 Phase 133 D-01/D-02,
# LEG-09). `_dispatch_sdp` refuses any op outside this frozenset. A module
# constant is used rather than a DB field because anything that widens a
# blast radius is an engine constant in this module (the
# `_WRITE_REGION_LENGTH` / `_UV_WRITE_REGION_LENGTH` precedent) -- a
# DB-supplied op string could otherwise smuggle in an op this module never
# vetted. This module's own known failure mode is a documented-but-dead
# frozenset -- `_MULTI_RUN_OPS` once shipped with ZERO references tree-wide
# (RESEARCH C-5 / Open Question 4, above) -- so `_SDP_OPS` is referenced by
# live code in `_dispatch_step`'s arm 5, and that reference is exercised by
# `tests/test_chip_test_sdp_leg.py::test_dispatch_sdp_maps_bool_to_verdict`.
_SDP_OPS = frozenset({OP_SDP_LOCK, OP_SDP_UNLOCK})

# The SDP leg's own registry (v1.30 Phase 134, T-134-01, LEG-03). A module
# constant, never a DB field, for the same reason `_SDP_OPS` and
# `_WRITE_REGION_LENGTH` are: anything that widens a blast radius lives in
# this module, never in a DB entry a malicious/misconfigured chip could
# supply. Like `_SDP_OPS` before it, this module's known failure mode is a
# documented-but-dead frozenset with zero tree-wide references -- plan
# 134-04's baseline gate (`_baseline_closes_sdp_gate`, D-08/D-20) is this
# set's live consumer.
_SDP_LEG_OPS = frozenset(
    {
        OP_WRITE_BASELINE_B,
        OP_WRITE_BASELINE_A,
        OP_WRITE_INHIBITED,
        OP_WRITE_RESTORED,
    }
)

# The baseline gate's inputs and outputs (v1.30 Phase 134, plan 134-04,
# D-08/D-20). `_SDP_BASELINE_OPS` is what `_baseline_closes_sdp_gate` is
# evaluated FROM -- the two baseline-direction steps whose own verdict
# decides whether a lock may be emitted. Disjoint from `_SDP_LEG_GATED_OPS`
# by construction: a baseline op decides the gate and always runs
# regardless of its own state (both directions must be attempted -- a
# failing `write-baseline-b` followed by a passing `write-baseline-a` must
# still leave the gate CLOSED, never reopened), while a gated op is what
# the gate, once closed, SKIPS.
_SDP_BASELINE_OPS = frozenset({OP_WRITE_BASELINE_B, OP_WRITE_BASELINE_A})

# `_SDP_LEG_GATED_OPS` -- the gate's outputs, closed by
# `_baseline_closes_sdp_gate`. `OP_SDP_UNLOCK`'s membership here is **D-20**
# (operator decision 2026-08-04), which SUPERSEDES D-08's own
# literally-written clause ("sdp-unlock is never attempted because nothing
# was locked"): that clause was measured-WRONG -- `OP_SDP_UNLOCK` is
# deliberately ABSENT from `_DESTRUCTIVE_OPS` (LEG-09), so as D-08 was
# literally written the unlock step would RUN and report OK at a part that
# was never locked (the P-06 emission-claim shape: an emission claim read
# as a state claim, on a run whose premise -- a lock was emitted -- did not
# hold). Joining `_SDP_LEG_GATED_OPS` is a DIFFERENT mechanism from
# `_DESTRUCTIVE_OPS`/the chip-ID destructive gate above: LEG-09 stays
# scoped EXCLUSIVELY to that gate (`test_unlock_exempt_from_destructive`,
# `test_lock_ran_then_gate_closes`, both unchanged and still green) -- D-20
# does not weaken it, because a *destructive*-gate closure and a
# *baseline*-gate closure are two structurally separate flags
# (`destructive_gate_closed` vs. `baseline_gate_closed`, wired
# independently in `run_plan`, below).
_SDP_LEG_GATED_OPS = frozenset(
    {OP_SDP_LOCK, OP_WRITE_INHIBITED, OP_SDP_UNLOCK, OP_WRITE_RESTORED}
)

_DESTRUCTIVE_GATE_REASON = (
    "chip-ID mismatch — destructive steps gated (chip left pristine)"
)

# The SDP leg's own gate-closure reasons (v1.30 Phase 134, D-08/D-20),
# consumed by plan 134-04's baseline gate. Both name the family FACT (the
# baseline write/read-back transition did not complete; the part is left as
# found) -- never a mechanism name, and never `_DESTRUCTIVE_GATE_REASON`'s
# chip-ID wording, which would mislead a reader into thinking chip-ID
# closed the gate when the write path did (D-08's own rejected alternative).
_SDP_BASELINE_GATE_REASON = (
    "baseline write/read-back transition did not complete — "
    "no lock was emitted (part left as found)"
)
_SDP_UNLOCK_GATE_REASON = (
    "baseline gate closed before a lock was emitted — "
    "no lock was emitted, so there is nothing to unlock"
)


@dataclass
class StepResult:
    """Outcome of executing a single `Step` (SWEEP-02/03/04, RPT-03).

    `verdict` is one of OK/BAD/NA/SKIPPED/marginal. `error_code` carries the
    exact firmware `response.id` captured off `EpromOperationError.error_code`
    (RPT-03) when the step raised; `None` otherwise. `fingerprint` is attached
    only for the write/verify step (Task 3, PATT-02 wiring). `run_count` is
    the number of times the underlying operator method was actually invoked
    for this step (1 for single-run steps; N for multi-run destructive/verify
    steps, Task 3). `divergence` carries the read-step byte-level divergence
    metric (D-06) when the step's `runs` disagreed -- a metric only, never a
    verdict flip and never `marginal` (marginal is destructive/verify-only).
    """

    op: str
    verdict: str
    reason: str = ""
    error_code: int | None = None
    fingerprint: Fingerprint | None = None
    run_count: int = 0
    divergence: dict[str, Any] | None = None


def _skip_result(op: str, reason: str, *, verdict: str = VERDICT_SKIPPED) -> StepResult:
    return StepResult(op=op, verdict=verdict, reason=reason, run_count=0)


def _resolve_or_none(
    name: str, db: Any
) -> tuple[dict[str, Any] | None, StepResult | None, str]:
    """Re-resolve `name` via the guard-HONORING `resolve_chip` (Pitfall 2).

    Returns `(eprom_data, None, "")` on success, or `(None, step_result_stub,
    reason)` when `resolve_chip` refuses -- callers fill in `op` on the stub.
    A refusal (ChipNotImplementedError / ChipNotFoundError) maps to SKIPPED
    with the reason recorded; the op was still listed by `derive_plan`, so
    the report can show "this chip's protocol supports write, but the host
    guard refuses it" (RESEARCH Pitfall 2).
    """
    try:
        eprom_data = resolve_chip(name, db=db)
    except (ChipNotImplementedError, ChipNotFoundError) as exc:
        reason = str(exc) or exc.__class__.__name__
        return None, _skip_result("", reason), reason
    return eprom_data, None, ""


# The cleanup drain's per-callable narrow exception set (v1.30 Phase 133
# D-10, LEG-10). Named exactly the same three classes `_run_step`'s D-08
# degrading clause and EpromOperationError clause catch on the step path --
# declared once as a module constant so plan 133-06's op-registry parity
# reasoning has a single named fact to point at, rather than the tuple
# being re-typed inline at the drain site. Deliberately NOT also naming
# ProgrammerNotFoundError/FirmwareOutdatedError: both are SerialError
# subclasses already covered by the first tuple element, so listing them
# again would be redundant, not narrower -- and it is precisely this
# inclusion-by-subclass that makes a run-fatal condition surfacing during
# cleanup swallowed here (a deliberate difference from the step path,
# which RE-RAISES those two -- see run_plan's finally, below).
_UNLOCK_CLEANUP_SWALLOWED = (SerialError, HardwareOperationError, EpromOperationError)


def run_plan(
    plan: Plan,
    operator: Any,
    db: Any,
    *,
    runs: int = 2,
    sampler: Any = None,
) -> list[StepResult]:
    """Execute `plan.steps` as independent, non-fatal steps (SWEEP-02).

    Each supported step re-resolves `plan.name` through `resolve_chip(name,
    db=db)` -- the guard-HONORING execution path (Pattern 2) -- and dispatches
    to the matching existing `EpromOperator` method (id -> check_eprom_id,
    read -> read_eprom, blank-check -> check_eprom_blank, write ->
    write_eprom, verify -> verify_eprom, erase -> erase_eprom). NA steps from
    `derive_plan` are recorded NA WITHOUT any operator call.

    The id-check step runs FIRST (SWEEP-03): a chip-ID mismatch -- `is_ok is
    False` OR the firmware-detected id differing from the DB's expected
    `chip-id` (Pitfall 4) -- closes a `destructive_gate` that every
    destructive step (write/erase) consults BEFORE calling its operator
    method, marking itself SKIPPED with reason (chip left pristine) instead.
    Non-destructive id/read/blank-check findings are still recorded
    regardless of the gate.

    One step's `BAD` verdict or raised exception NEVER aborts the remaining
    steps (Pitfall 1) -- each step's body is wrapped in its own try/except.
    `EpromOperationError` -> `BAD` capturing `err.error_code` (RPT-03); a
    `resolve_chip` refusal -> `SKIPPED`/`NA` with reason (Pitfall 2).

    Destructive/verify steps (write/erase/verify) run `runs` times (default
    2, D-05); when the per-run outcomes DISAGREE the step verdict is
    `marginal` -- never coerced to a confident OK/BAD (D-06, the AM27C020
    write#1 60/64 vs write#2 0/64 case made structural). `runs < 2` is
    rejected BEFORE any resolve/operator call (D-05 guard, mirrors
    `consistency_check_eprom`). Read-step disagreement across `runs` is
    reported as a byte-level divergence metric only -- NOT a verdict flip,
    NOT `marginal` (D-06). The write/verify step attaches a `Fingerprint`
    (Task 3, PATT-02 wiring) built from `generate_pattern` vs the read-back,
    with `addr_base` == the write region start (Pitfall 3).

    `sampler` (D-04, Phase 112) is an OPTIONAL opaque callable the caller
    supplies -- this engine never imports `hardware.py` and stays entirely
    sampler-agnostic. When provided, it is invoked as `sampler("before")`
    immediately before and `sampler("after")` immediately after EACH
    `operator.write_eprom(...)` call inside the OP_WRITE branch of
    `_dispatch_multi_run` ONLY (never around OP_READ/OP_VERIFY/OP_ERASE/OP_ID/
    OP_BLANK_CHECK, and never around the whole `run_plan`/step loop) so a
    write-pulse voltage droop can be told apart from a read droop. A raised
    sampler exception is swallowed (best-effort diagnostic, not part of the
    write contract) and never aborts the write step. `sampler=None` (the
    default) is a proven no-op: it adds zero calls and leaves every existing
    caller's `StepResult` list unchanged.

    A generic cleanup registry (v1.30 Phase 133 D-06, LEG-10) is drained in
    a bare `try/finally` around the whole step loop: a successful
    `OP_SDP_LOCK` step registers its matching unlock, and the drain runs it
    regardless of how the loop exits -- including on a raised exception or
    `KeyboardInterrupt`/`SystemExit`, both of which still propagate
    unchanged afterward. An EMPTY registry (every currently-shipping run,
    since this phase derives no SDP step) is a proven no-op mirroring the
    `sampler=None` claim above: it adds zero calls and leaves every
    existing caller's `StepResult` list unchanged. On the propagating path
    the report is honestly forfeited -- the caller's `results =
    run_plan(...)` assignment never completes, so there is nothing to
    render (D-07's residual).
    """
    if runs < 2:
        return [
            StepResult(
                op="__plan__",
                verdict=VERDICT_BAD,
                reason=(
                    f"runs must be >= 2 (got {runs}); a destructive/verify "
                    "step requires at least 2 runs to compare (D-05)"
                ),
                run_count=0,
            )
        ]

    results: list[StepResult] = []
    destructive_gate_closed = False
    # The SDP baseline gate (v1.30 Phase 134, plan 134-04, D-08/D-20) --
    # a SEPARATE flag from `destructive_gate_closed` above, deliberately:
    # the two gates are structurally different mechanisms (chip-ID mismatch
    # vs. a baseline write/read-back transition that did not complete), and
    # the SDP-leg's own gate-closure reasons (`_SDP_BASELINE_GATE_REASON`/
    # `_SDP_UNLOCK_GATE_REASON`) must never be confused with
    # `_DESTRUCTIVE_GATE_REASON`'s chip-ID wording. STICKY by construction
    # (only ever set True, never reset False) so a failing `write-baseline-b`
    # followed by a passing `write-baseline-a` cannot reopen it.
    baseline_gate_closed = False
    # Cleanup registry (v1.30 Phase 133 D-06, LEG-10): a plain `list` of
    # zero-argument callables, deliberately GENERIC rather than a hardcoded
    # lock-to-unlock window with the unlock written inline. The inline form
    # is literally what research P-20 prevention #2 describes and is
    # simpler -- but Phase 134's four-step leg, and any later
    # cleanup-needing op, would each have to re-open `run_plan` to widen
    # the special case, and a special case widened three times is how this
    # loop's flat shape rotted in the first place. Drained in
    # REGISTRATION order below -- deliberately NOT `contextlib.ExitStack`:
    # measured, not assumed, `ExitStack.close()` drains LIFO (reversing
    # registration order) and a raising callback makes `close()` re-raise,
    # which inside a `finally` REPLACES the in-flight exception and demotes
    # the original to `__context__` -- precisely the masking D-10 exists to
    # prevent.
    cleanup: list[Callable[[], None]] = []

    # De-registration handle (v1.30 Phase 134, plan 134-04, D-11/RESEARCH
    # §4.2). With this phase's explicit `sdp-unlock` step now a real plan
    # step, a successful lock both REGISTERS a cleanup (above) AND the plan
    # step RUNS the unlock explicitly -- two unlock emissions without this
    # handle. Holds the specific callable a successful lock registered so a
    # later successful explicit unlock can `cleanup.remove(...)` it -- by
    # VALUE, never by wiping the whole registry (which is deliberately
    # GENERIC, see the registry's own comment above). Reset to `None` once
    # removed; a FAILED explicit unlock leaves it registered so the drain
    # still retries it.
    unlock_cleanup: Callable[[], None] | None = None

    # `runs < 2` stays OUTSIDE this `try` (above): nothing is registered
    # yet, so there is nothing to drain. `results`, `destructive_gate_closed`
    # and `cleanup` are all created BEFORE the `try`. `return results` stays
    # INSIDE the `try`, textually unchanged.
    try:
        for step in plan.steps:
            if not step.supported:
                results.append(_skip_result(step.op, step.reason, verdict=VERDICT_NA))
                continue

            if step.op in _DESTRUCTIVE_OPS and destructive_gate_closed:
                results.append(_skip_result(step.op, _DESTRUCTIVE_GATE_REASON))
                continue

            # The SDP baseline gate (v1.30 Phase 134, D-08/D-20). Ordered
            # AFTER the chip-ID destructive gate above and BEFORE the
            # dispatch call below -- load-bearing: the chip-ID gate fires
            # first and renders its OWN wording, so a write-path closure is
            # never misattributed to a chip-ID mismatch. `_SDP_LEG_GATED_OPS`
            # never includes either baseline op itself (`_SDP_BASELINE_OPS`)
            # -- both baseline directions always run regardless of this
            # flag's state, because they are what DECIDE it.
            if step.op in _SDP_LEG_GATED_OPS and baseline_gate_closed:
                reason = (
                    _SDP_UNLOCK_GATE_REASON
                    if step.op == OP_SDP_UNLOCK
                    else _SDP_BASELINE_GATE_REASON
                )
                results.append(_skip_result(step.op, reason))
                continue

            result = _run_step(
                plan.name, step, operator, db, runs=runs, sampler=sampler
            )
            results.append(result)

            if step.op == OP_SDP_LOCK and result.verdict == VERDICT_OK:
                # Register the unlock ONLY on a successful lock (D-06):
                # registering after a failed lock would attempt to unlock a
                # part that was never locked. Routed through `_run_step`
                # rather than calling `_dispatch_sdp` directly because
                # `run_plan` does not have `eprom_data` in scope -- the
                # resolve happens inside `_run_step` -- and this reuses
                # the resolver, the dispatch arm, and plan 133-02's
                # exception mapping.
                #
                # A nested `def` (not a `lambda: _run_step(...)`) so the
                # returned `StepResult` is DISCARDED as a statement, not an
                # expression -- it must never reach `results` (see the
                # `finally` below) -- and so the registered callable's
                # actual inferred return type is `None`, matching
                # `cleanup`'s declared `Callable[[], None]` element type
                # (a `lambda` returning the `StepResult` expression would
                # be a real mypy `arg-type` mismatch here, not merely a
                # style choice).
                def _unlock_cleanup() -> None:
                    _run_step(
                        plan.name,
                        Step(op=OP_SDP_UNLOCK, supported=True, reason=""),
                        operator,
                        db,
                        runs=runs,
                    )

                cleanup.append(_unlock_cleanup)
                # Hold the handle so a later successful EXPLICIT unlock step
                # (below) can de-register it -- see `unlock_cleanup`'s own
                # comment above (D-11/RESEARCH §4.2).
                unlock_cleanup = _unlock_cleanup

            if (
                step.op == OP_SDP_UNLOCK
                and result.verdict == VERDICT_OK
                and unlock_cleanup is not None
            ):
                # The explicit plan-derived unlock step SUCCEEDED: the
                # registered cleanup from the matching lock above is no
                # longer needed -- remove it by VALUE (`cleanup.remove`),
                # never by wiping the whole registry, so a completed leg
                # emits exactly one `sdp_unlock` call, not two (D-11/
                # RESEARCH §4.2). A FAILED explicit unlock (non-OK verdict)
                # deliberately leaves `unlock_cleanup` registered so the
                # `finally` drain below still retries it.
                cleanup.remove(unlock_cleanup)
                unlock_cleanup = None

            if step.op == OP_ID:
                destructive_gate_closed = _id_step_closes_gate(result)

            if step.op in _SDP_BASELINE_OPS:
                # Sticky by construction (only ever ORed True, never reset
                # False): a failing `write-baseline-b` followed by a
                # passing `write-baseline-a` must leave the gate CLOSED,
                # never reopened (D-08).
                baseline_gate_closed = (
                    baseline_gate_closed or _baseline_closes_sdp_gate(result)
                )

        return results
    finally:
        # Bare `finally`, NO `except` clause of any width (criteria 1+2):
        # this is the ONE construct that reaches
        # `KeyboardInterrupt`/`SystemExit` while still letting them
        # propagate unchanged. P-20's prevention text asking for a
        # `try/finally` "wide enough to catch `BaseException`" is
        # unnecessary and self-defeating here -- an `except BaseException:`
        # would violate criterion 2 (Ctrl-C must stay Ctrl-C) and would be
        # flagged by plan 133-05's bare-except deny-rule.
        #
        # The drain below NEVER appends into `results` and NEVER
        # references it at all: `results` is returned by reference, so a
        # mutation here would be visible to the caller, and that same list
        # feeds seven consumers in `cli_handlers.py` (the `run_plan` call
        # site, `count_applicable`, the generic renderer, the JSON
        # artifact, the markdown table, `build_db_diff`, and
        # `sys.exit(max(...))`) -- `count_applicable` would render "8 of 7
        # ran". The registry is empty on every currently-shipping run in
        # this phase (no plan derives an SDP step), so this is LATENT here
        # and would DETONATE in Phase 134.
        #
        # Each callable gets its OWN narrow `try/except` (D-10) over
        # exactly `_UNLOCK_CLEANUP_SWALLOWED`, and the drain CONTINUES past
        # a caught failure rather than stranding the entries behind it in
        # the registry -- never `raise` from this `finally`. An exception
        # raised from a `finally` REPLACES the in-flight exception, so a
        # raising cleanup must never be allowed to mask the original fault
        # or the user's Ctrl-C. A run-fatal condition surfacing during
        # cleanup is a DELIBERATE difference from the step path, not an
        # oversight: `ProgrammerNotFoundError`/`FirmwareOutdatedError` are
        # `SerialError` subclasses, so the `SerialError` arm of
        # `_UNLOCK_CLEANUP_SWALLOWED` DOES catch them here, whereas
        # `_run_step`'s D-08 clause RE-RAISES those same two on the step
        # path.
        #
        # What "recorded" means here (reconciling D-10 with D-16): the
        # attempt and its outcome are observable only through the operator
        # double in Phase 133 -- `chip_test.py` has no logger and no
        # `logging` import (the bench-free pure-compute engine that emits
        # nothing), `exc.add_note()` is 3.11+ against this module's >=3.9
        # floor, and the drain must not touch `results`. The honest
        # residual: a failed unlock is NOT user-visible until Phase 134's
        # `HELD`/`NOT-RUN` report field (LEG-12).
        for cleanup_call in cleanup:
            try:
                cleanup_call()
            except _UNLOCK_CLEANUP_SWALLOWED:
                continue


def _id_step_closes_gate(result: StepResult) -> bool:
    """SWEEP-03: close the destructive gate on an id-check failure/mismatch.

    Closes on `is_ok is False` (chip-ID check failed), a detected id that
    differs from the DB's expected `chip-id` (Pitfall 4's explicit mismatch
    case), OR the step itself erroring/being skipped -- ANY id-uncertainty
    gates destructive steps shut, not just an explicit numeric mismatch.
    A `NA` id step (no expected chip-id in the DB entry, Open Question 2)
    does NOT close the gate -- there is nothing to compare, so the gate
    stays open subject to the plan's own `--destructive` annotation.
    """
    return result.verdict in (VERDICT_BAD, VERDICT_SKIPPED)


def _baseline_closes_sdp_gate(result: StepResult) -> bool:
    """D-08/D-20: close the SDP baseline gate on ANY non-OK baseline verdict.

    Mirrors `_id_step_closes_gate`'s shape immediately above -- a pure
    `StepResult -> bool` predicate `run_plan` consults after running one of
    `_SDP_BASELINE_OPS` -- but deliberately WIDER: it closes on BAD,
    `marginal`, `SKIPPED` **and** `NA`, not `_id_step_closes_gate`'s
    narrower `(VERDICT_BAD, VERDICT_SKIPPED)` tuple. A contact fault
    (`marginal`) is as disqualifying as a proven-dead write path (BAD): a
    lock must never be emitted at a part whose write path was not
    demonstrated to transition in BOTH directions (`write-baseline-b` AND
    `write-baseline-a`), so anything short of a clean OK on either baseline
    direction closes the gate. `result.verdict != VERDICT_OK` expresses
    that widening directly, rather than enumerating the four non-OK
    verdicts by name.
    """
    return result.verdict != VERDICT_OK


# LEG-12's three-valued hold-state REPORT VALUES (v1.30 Phase 134, plan
# 134-04, D-10/D-12/D-15). These are report values, NOT op strings -- they
# carry no `OP_` prefix and must never join `_ALL_OPS`/`_MULTIWORD_OP_VALUES`
# in tests/test_op_registration_parity.py; a later reader must not
# "helpfully" register them there.
SDP_HOLD_HELD = "HELD"
SDP_HOLD_NOT_HELD = "NOT-HELD"
SDP_HOLD_NOT_RUN = "NOT-RUN"


def sdp_oracle_applicable(plan: Plan) -> bool:
    """`True` iff `plan` carries a RUNNABLE `write-inhibited` entry (LEG-12).

    Derived STRUCTURALLY from the `plan` object the caller already holds --
    never a second call to `sdp_capability`, which would be a second source
    of truth that could drift from `derive_plan`'s own decision (the same
    single-source-of-truth discipline D-15 applies to `count_applicable`).

    `True` when `plan.steps` carries an `OP_WRITE_INHIBITED` `Step` with
    `supported=True` (a real `dev test` run, ALLOW chip), OR when
    `plan.locked_destructive` carries an `OP_WRITE_INHIBITED` `(op, reason)`
    pair (the `write_scope="none"` ALLOW-chip shape, D-18). `False` for a
    REFUSE chip: its `OP_WRITE_INHIBITED` step IS present in `plan.steps`
    (LEG-02's NA path), but with `supported=False` -- the oracle never runs
    for a REFUSE chip, so that presence must not count as "applicable".
    """
    for step in plan.steps:
        if step.op == OP_WRITE_INHIBITED and step.supported:
            return True
    return any(op == OP_WRITE_INHIBITED for op, _reason in plan.locked_destructive)


def sdp_hold_state(plan: Plan, results: list[StepResult]) -> str:
    """LEG-12's pure `HELD`/`NOT-HELD`/`NOT-RUN(reason)` derivation.

    Pure, no logger, no I/O (this module has neither and stays that way --
    see `_UNLOCK_CLEANUP_SWALLOWED`'s own comment above). Reads `results`
    for the `write-inhibited` `StepResult`, if any:

    - verdict OK -> `SDP_HOLD_HELD` (the inhibited write was correctly
      refused -- the part held its lock).
    - verdict BAD -> `SDP_HOLD_NOT_HELD` (the inhibited write WAS accepted
      -- the lock leaked, LEG-06's shape).
    - verdict NA / `SKIPPED` / `marginal`, OR the step entirely ABSENT from
      `results` (laundering route R6 -- a plan that never derived the step
      at all) -> `f"{SDP_HOLD_NOT_RUN}: {reason}"`, where `reason` is that
      result's own `reason` when one is present and non-empty, and
      otherwise fixed prose naming the family fact -- composed by CALLING
      `sdp_honesty.unreadable_state_caveat()`, never re-authoring its
      sentence.

    `plan` is accepted (not merely `results`) to match `count_applicable`'s
    own two-argument signature shape, and so a future caller extending the
    NOT-RUN reason with plan-derived context (e.g. distinguishing a REFUSE
    chip from an absent step) has it available without changing every call
    site; this revision derives everything it returns from `results` alone.

    Returns `str` ALWAYS -- never `True`/`False`/`None` (P-06 prevention 3:
    a JSON boolean here would read as ground truth for a state this family
    cannot report; plan `134-06` adds the committed assertion that no such
    boolean exists anywhere in `to_dict()`).
    """
    result: StepResult | None = None
    for r in results:
        if r.op == OP_WRITE_INHIBITED:
            result = r
            break

    if result is not None and result.verdict == VERDICT_OK:
        return SDP_HOLD_HELD
    if result is not None and result.verdict == VERDICT_BAD:
        return SDP_HOLD_NOT_HELD

    if result is not None and result.reason:
        reason = result.reason
    else:
        reason = (
            "the SDP inhibited-write oracle did not run for this chip. "
            f"{sdp_honesty.unreadable_state_caveat()}"
        )
    return f"{SDP_HOLD_NOT_RUN}: {reason}"


def sdp_left_writable(results: list[StepResult]) -> bool:
    """D-12's loud-form predicate (v1.30 Phase 134, plan 134-08, LEG-14):
    `True` iff `results` itself demonstrates the run confirmed the part
    still accepts a write, i.e. the `write-restored` `StepResult` is
    present AND its verdict is `VERDICT_OK`.

    Pure, no I/O, no logger -- same discipline as `sdp_hold_state` above.
    `False` when `write-restored` is absent entirely from `results`
    (laundering route R6 -- a plan that never derived the step at all) OR
    present with any verdict OTHER than OK (`BAD`/`NA`/`SKIPPED`/
    `marginal`) -- every one of those means this run did NOT itself
    demonstrate the part still writes, which is exactly the "did not
    confirm the part writable again" term `cli_handlers._sdp_recovery_line`
    keys its LOUD recovery form on.

    Lives here rather than in `cli_handlers.py` for the same three reasons
    `sdp_hold_state` does: `chip_test.py` is scanned in full (P-07), it
    sits outside the mypy strict island (`cli_handlers.py` has only 2 of
    headroom), and it keeps op-string knowledge (`OP_WRITE_RESTORED`,
    `VERDICT_OK`) out of the handler.
    """
    for r in results:
        if r.op == OP_WRITE_RESTORED:
            return r.verdict == VERDICT_OK
    return False


# Region used for the write/verify address-derived pattern fingerprint
# (Task 3, PATT-01/02 wiring). A small fixed region keeps the bench-free
# engine's write/verify step cheap and matches the region-parameterized
# generator contract (D-02). This is the NON-UV default region -- Phase 109
# (PATT-03) owns the UV-EPROM branch below via `_write_region_for`.
_WRITE_REGION_START = 0
_WRITE_REGION_LENGTH = 256

# UV-EPROM write-region WIDTH (PATT-03, SC4). This is an ENGINE MODULE
# CONSTANT, never sourced from any DB field -- a malicious/misconfigured DB
# entry must not be able to widen the write window. `memory-size` is only a
# top-anchor PLACEMENT bound (where the window sits), never a WIDTH input.
_UV_WRITE_REGION_LENGTH = 256

# The engine default region as a concrete tuple (D-02) -- consumed by
# `_top_anchored_or_default` (used by `derive_plan`'s region computation,
# defined earlier in this module; referenced here at call time only, after
# module import completes).
_DEFAULT_REGION = (_WRITE_REGION_START, _WRITE_REGION_LENGTH)


def _write_region_for(step: Step | None, eprom_data: dict[str, Any]) -> tuple[int, int]:
    """Return the write/verify region `derive_plan` already decided (D-02).

    This function READS `step.write_region` -- the value `derive_plan` set
    exactly once, from `Plan.is_uv` (`is_uv_eprom(full)`, 301/301 exact) and
    `full["memory-size"]` -- and returns it unchanged when present. It
    returns the engine default `(_WRITE_REGION_START, _WRITE_REGION_LENGTH)`
    when `step` is `None` or carries no region (`step.write_region is
    None`).

    This function must NEVER re-derive UV-ness. Before Phase 121 Plan 06 it
    guessed UV-ness at execution time from `eprom_data.get("electrical-type")
    == "UV-EPROM"` OR `eprom_data.get("algorithm") == 0x0B` -- but
    `_dispatch_multi_run`'s `eprom_data` is `resolve_chip`'s PROGRAMMER dict
    (via `convert_to_programmer`), which never carries `electrical-type`,
    and `algorithm == 0x0B` matches only 32 of 301 UV parts (measured), so
    269 UV parts silently fell through to the engine default. Under D-01, a
    missed UV part receiving a full-device write instead of the small
    top-anchored window is a chip-destroying bug, not a coverage gap -- the
    guess is deleted here, not merely bypassed. `eprom_data` is accepted for
    call-site symmetry with `_dispatch_multi_run`'s existing signature but is
    otherwise unused by this function: the WIDTH always comes from a module
    constant (`_WRITE_REGION_LENGTH` / `_UV_WRITE_REGION_LENGTH`), never from
    any DB field (SC4) -- `eprom_data`/`memory-size` play no role here
    because `derive_plan` already resolved the concrete region.
    """
    if step is not None and step.write_region is not None:
        return step.write_region
    return _DEFAULT_REGION


def _run_step(
    name: str, step: Step, operator: Any, db: Any, *, runs: int, sampler: Any = None
) -> StepResult:
    """Execute a single supported step through the guard-honoring resolver.

    Wraps only the DISPATCH half (the `_dispatch_step` call) of the step body
    in try/except (Pitfall 1) -- the resolve half above it, via
    `_resolve_or_none`, sits OUTSIDE this `try` and is covered only by that
    function's own narrower `(ChipNotImplementedError, ChipNotFoundError)`
    handler. An exception class other than those two raised during
    resolution still propagates out of `run_plan` unchanged; `resolve_chip`
    is currently a pure DB lookup plus `convert_to_programmer` transform with
    no measured path to a `SerialError`, so this is recorded as a latent
    residual (research assumption A2), not a closed gap. Reference:
    cli_handlers.py:1568 `dev_validate_family` -- the same
    `resolve_chip(name, db=...)` + operator-method compose pattern used here.

    `sampler` (D-04) is threaded through unchanged to `_dispatch_step`;
    `None` is the default and a proven no-op.
    """
    eprom_data, skip_stub, reason = _resolve_or_none(name, db)
    if skip_stub is not None or eprom_data is None:
        if skip_stub is None:
            skip_stub = _skip_result(step.op, reason)
        skip_stub.op = step.op
        return skip_stub

    try:
        return _dispatch_step(
            name, step, eprom_data, operator, runs=runs, sampler=sampler
        )
    except (
        ProgrammerNotFoundError,
        FirmwareOutdatedError,
        HardwareRevisionUnsupportedError,
    ):
        # D-08/LEG-11: these SerialError subclasses are run-fatal
        # host-setup conditions ("no programmer attached", "firmware too
        # old", "shield revision cannot safely drive this chip"), not chip
        # findings -- they belong to cli_handlers.py's
        # @map_typed_errors mapper, which already renders them as
        # ClickExceptions with stable exit codes. This clause MUST precede
        # the (SerialError, HardwareOperationError) clause below: both are
        # SerialError subclasses and Python matches the first satisfying
        # except clause. If the order were inverted, a no-board or
        # old-firmware run would degrade every remaining destructive/verify
        # step to BAD instead of escaping once, producing a six-BAD-step
        # report that reads as a broken chip when the real fault is a
        # missing/outdated host setup -- this project's documented
        # false-green no-board trap, reproduced structurally.
        raise
    except (SerialError, HardwareOperationError) as exc:
        # D-08/LEG-11: a half-seated cable or other transport-level fault
        # (SerialError itself, SerialTimeoutError, or HardwareOperationError
        # -- a sibling of Exception, not an EpromOperationError subclass, so
        # the existing `except EpromOperationError` clause below never
        # reaches it) degrades THIS ONE step to a recorded BAD result;
        # `run_plan` still returns a full report for every other step.
        # `error_code` is deliberately omitted: neither SerialError nor
        # HardwareOperationError carries that attribute -- only
        # EpromOperationError does -- so copying the existing handler
        # wholesale would raise AttributeError at the moment this handler is
        # supposed to be recovering.
        return StepResult(
            op=step.op,
            verdict=VERDICT_BAD,
            reason=str(exc),
            run_count=1,
        )
    except EpromOperationError as exc:
        return StepResult(
            op=step.op,
            verdict=VERDICT_BAD,
            reason=str(exc),
            error_code=exc.error_code,
            run_count=1,
        )
    except (ChipNotImplementedError, ChipNotFoundError) as exc:
        # Belt-and-suspenders: a resolve-time-only exception raised instead
        # during dispatch (defensive; resolve_chip already ran above).
        return _skip_result(step.op, str(exc) or exc.__class__.__name__)


def _dispatch_step(
    name: str,
    step: Step,
    eprom_data: dict[str, Any],
    operator: Any,
    *,
    runs: int,
    sampler: Any = None,
) -> StepResult:
    """Dispatch `step.op` to its matching existing `EpromOperator` method.

    id -> check_eprom_id (single run, bool/Optional[int]); blank-check ->
    single run; read -> `runs`-times with a byte-level divergence metric
    (D-06, never a verdict flip); write/verify/erase -> `runs`-times with a
    marginal-on-disagreement policy (D-05/D-06); write/verify additionally
    attach a `Fingerprint` (PATT-02 wiring, Pitfall 3 addr_base). SDP
    lock/unlock (v1.30 Phase 133 D-01/D-04, LEG-09) -> single run via
    `_dispatch_sdp`, arm 5. The SDP leg's four write-shaped ops (v1.30 Phase
    134 T-134-02, LEG-05/06/07/08/16) -> single run via `_dispatch_sdp_leg`,
    arm 6, LAST -- see below. The engine sets NO VPP, builds NO wire dict
    (except the one `FLAG_SKIP_SDP_UNLOCK` bit on `OP_WRITE_INHIBITED`, a
    deliberate D-01 narrowing), and passes NO --force -- it only calls the
    operator's existing public methods.

    `sampler` (D-04) is threaded through unchanged to `_dispatch_multi_run`,
    the only op with a bracket site (OP_WRITE); `None` is the default and a
    proven no-op for every other op.
    """
    if step.op == OP_ID:
        return _dispatch_id(name, eprom_data, operator)
    if step.op == OP_BLANK_CHECK:
        is_ok = operator.check_eprom_blank(name, eprom_data)
        return StepResult(
            op=step.op, verdict=VERDICT_OK if is_ok else VERDICT_BAD, run_count=1
        )
    if step.op == OP_READ:
        return _dispatch_read(name, eprom_data, operator, runs=runs)
    # write / verify / erase: multi-run marginal policy (D-05/D-06). Dispatch
    # ONLY when `step.op` is on the live `_MULTI_RUN_OPS` allow-list --
    # anything else refuses fail-closed (121-02, T-121-07). Before this
    # guard, this `return` was unconditional, so any op string outside
    # {OP_ID, OP_BLANK_CHECK, OP_READ} fell through to
    # `_dispatch_multi_run`'s own terminal `else` and reached
    # `operator.erase_eprom()` (RESEARCH Pitfall 1a).
    if step.op in _MULTI_RUN_OPS:
        return _dispatch_multi_run(
            step.op, name, eprom_data, operator, runs=runs, sampler=sampler, step=step
        )
    # Arm 5, LAST (v1.30 Phase 133 D-04, LEG-09) -- immediately above the
    # terminal fail-closed `return` below. The measured arm order above is
    # OP_ID -> OP_BLANK_CHECK -> OP_READ -> _MULTI_RUN_OPS -> here, so all
    # seven ops shipped before this phase return from arms 1-4 and NEVER
    # evaluate this membership test at all -- proven mechanically by
    # `tests/test_chip_test_sdp_leg.py::test_shipped_ops_never_reach_sdp_arm`
    # (D-13b's sentinel), not merely asserted. Keys on `_SDP_OPS` membership
    # of the op string rather than a new `Step.group` field (D-05) -- the op
    # string already carries the distinction, the argument this module
    # itself makes for `write-partial` above. Honest consequence, recorded
    # rather than smoothed over: ROADMAP criterion 4's clause about "an op
    # with `group=None` takes the exact pre-existing dispatch path" is then
    # satisfied VACUOUSLY -- there is no `group` field, so no op has
    # `group=None`. Criterion 4's *intent* (shipped ops behaviourally
    # unchanged at zero added branching cost) is met by arm placement plus
    # the sentinel test instead; the criterion's literal wording is not
    # something this phase tests.
    if step.op in _SDP_OPS:
        return _dispatch_sdp(step.op, name, eprom_data, operator)
    # Arm 6 (v1.30 Phase 134 T-134-02, LEG-05/06/07/08/16) -- immediately
    # after arm 5 and still above the terminal fail-closed `return` below.
    # Routes the SDP leg's four write-shaped ops to the read-back-equality
    # oracle. Placing it before arm 5 (or before arms 1-4) would break
    # tests/test_chip_test_sdp_leg.py::test_shipped_ops_never_reach_sdp_arm,
    # which proves every op shipped before this phase returns from an
    # earlier arm and never evaluates this membership test at all.
    if step.op in _SDP_LEG_OPS:
        return _dispatch_sdp_leg(step.op, name, eprom_data, operator, step=step)
    return StepResult(
        op=step.op,
        verdict=VERDICT_BAD,
        run_count=0,
        reason=(
            f"op {step.op!r} matched no dispatch arm — refused fail-closed "
            "rather than falling through to _dispatch_multi_run"
        ),
    )


def _dispatch_id(name: str, eprom_data: dict[str, Any], operator: Any) -> StepResult:
    is_ok, detected_id = operator.check_eprom_id(name, eprom_data)
    expected_id = eprom_data.get("chip-id")
    # Pitfall 4: gate on is_ok=False OR an explicit id mismatch -- a
    # detected id differing from the DB's expected chip-id closes the
    # destructive gate even when the firmware itself reported is_ok=True
    # (defensive; check_eprom_id's own is_ok already reflects this in
    # practice, but the mismatch check makes the gate condition explicit
    # and independent of firmware wording).
    mismatch = (
        is_ok and expected_id and detected_id is not None and detected_id != expected_id
    )
    verdict = VERDICT_BAD if (not is_ok or mismatch) else VERDICT_OK
    reason = ""
    if mismatch:
        reason = (
            f"chip-ID mismatch: expected 0x{expected_id:X}, detected 0x{detected_id:X}"
        )
    elif not is_ok:
        reason = "chip-ID check did not return OK"
    return StepResult(op=OP_ID, verdict=verdict, reason=reason, run_count=1)


def _dispatch_read(
    name: str, eprom_data: dict[str, Any], operator: Any, *, runs: int
) -> StepResult:
    """Run `read_eprom` `runs` times into temp files; report divergence ONLY.

    D-06: read-step disagreement across runs is a byte-level divergence
    metric on the step result, NEVER a verdict flip and NEVER `marginal`
    (marginal is destructive/verify-only). The step's own verdict is OK/BAD
    from the LAST run's return value -- disagreement across runs does not
    change it.
    """
    last_ok = True
    run_bytes: list[bytes] = []
    with tempfile.TemporaryDirectory(prefix="chip_test_read_") as tmp_dir:
        for i in range(runs):
            out_path = str(Path(tmp_dir) / f"run_{i:02d}.bin")
            last_ok = operator.read_eprom(name, eprom_data, output_file=out_path)
            try:
                run_bytes.append(Path(out_path).read_bytes())
            except OSError:
                run_bytes.append(b"")

    divergence: dict[str, Any] | None = None
    if len(run_bytes) >= 2 and any(run_bytes):
        shas = [hashlib.sha256(b).hexdigest() for b in run_bytes]
        diverged = len(set(shas)) != 1
        if diverged:
            cmp_len, diff_offsets, pct, first = _diff_offsets(
                run_bytes[0], run_bytes[1]
            )
            divergence = {
                "repeat_divergent": True,
                "cmp_len": cmp_len,
                "bad": len(diff_offsets),
                "pct": pct,
                "first_offset": first,
            }

    reason = "read runs diverged" if divergence else ""
    return StepResult(
        op=OP_READ,
        verdict=VERDICT_OK if last_ok else VERDICT_BAD,
        reason=reason,
        run_count=runs,
        divergence=divergence,
    )


def _sample(sampler: Any, phase: str) -> None:
    """Best-effort sampler invocation (D-04) -- never lets an exception
    escape (Pitfall 1 extended to the sampler: it is a diagnostic hook, not
    part of the write contract). No-op when `sampler is None`.
    """
    if sampler is None:
        return
    try:
        sampler(phase)
    except Exception:  # noqa: BLE001 -- best-effort diagnostic, swallow all
        pass


def _dispatch_multi_run(
    op: str,
    name: str,
    eprom_data: dict[str, Any],
    operator: Any,
    *,
    runs: int,
    sampler: Any = None,
    step: Step | None = None,
) -> StepResult:
    """Run a destructive/verify op `runs` times; `marginal` on disagreement.

    Collects a per-run bool outcome (the operator method's own return value)
    for write/write-partial/erase; write/write-partial/verify ALSO builds
    the expected address-derived pattern and reads back via
    `operator.verify_eprom`'s outcome plus a fresh `read_eprom` to compute
    the `Fingerprint` (PATT-02). Disagreement across the N per-run outcomes
    -> `marginal`, never coerced to a confident OK/BAD (D-06, the AM27C020
    structural case). The write/verify region is READ from `step.
    write_region` via `_write_region_for(step, eprom_data)` (D-02, Phase 121
    Plan 06) -- `derive_plan` already decided it; this function never
    re-derives UV-ness.

    `sampler` (D-04, Phase 112) is invoked as `sampler("before")` /
    `sampler("after")` tightly bracketing EACH `operator.write_eprom(...)`
    call -- in the `op in (OP_WRITE, OP_WRITE_PARTIAL)` branch (a partial
    write is still a write), never around OP_VERIFY or OP_ERASE, and never
    around the whole run loop (a write droop must stay distinguishable from
    a read droop). `sampler=None` adds zero calls.

    Fail-closed (121-02, T-121-05/06/08): `op` MUST be a member of the live
    `_MULTI_RUN_OPS` allow-list. The refusal is hoisted here, above
    `_write_region_for`/`generate_pattern` and any temp-file creation, so an
    unrecognised op creates no temp file, computes no pattern, and -- the
    load-bearing property -- never reaches ANY of `write_eprom`,
    `verify_eprom`, or `erase_eprom`. This is the host mirror of Phase 119
    D-06/D-07's generic op-layer NULL-`main` refusal
    (`firestarter/src/operation_utils.cpp::op_execute_stateful_operation`;
    read-only reference, not re-implemented here). Before this guard, this
    function's run loop ended in a bare `else: # OP_ERASE`, so an unmapped op
    called `operator.erase_eprom()` once per run and reported `VERDICT_OK`
    (RESEARCH Pitfall 1a, proven empirically: 2 runs -> 2 calls -> OK).
    """
    if op not in _MULTI_RUN_OPS:
        return StepResult(
            op=op,
            verdict=VERDICT_BAD,
            run_count=0,
            reason=(
                f"op {op!r} is not in the multi-run dispatch allow-list "
                "(_MULTI_RUN_OPS) — refused fail-closed rather than falling "
                "through to erase_eprom"
            ),
        )

    outcomes: list[bool] = []
    fingerprint: Fingerprint | None = None
    tmp_source_path: str | None = None

    region_start, region_length = _write_region_for(step, eprom_data)
    expected = generate_pattern(region_start, region_length)
    if op in (OP_WRITE, OP_WRITE_PARTIAL, OP_VERIFY):
        tmp_fh = tempfile.NamedTemporaryFile(
            prefix="chip_test_pattern_", suffix=".bin", delete=False
        )
        try:
            tmp_fh.write(expected)
        finally:
            tmp_fh.close()
        tmp_source_path = tmp_fh.name

    try:
        for _ in range(runs):
            if op in (OP_WRITE, OP_WRITE_PARTIAL):
                _sample(sampler, "before")
                outcomes.append(operator.write_eprom(name, eprom_data, tmp_source_path))
                _sample(sampler, "after")
            elif op == OP_VERIFY:
                outcomes.append(
                    operator.verify_eprom(name, eprom_data, tmp_source_path)
                )
            elif op == OP_ERASE:
                outcomes.append(operator.erase_eprom(name, eprom_data))
            else:
                # Unreachable in practice: the fail-closed `_MULTI_RUN_OPS`
                # guard at the top of this function already refused any op
                # outside {OP_WRITE, OP_WRITE_PARTIAL, OP_VERIFY, OP_ERASE}
                # before this loop could start (121-02, T-121-05; 121-06,
                # D-06). Kept explicit rather than a bare `else: # OP_ERASE`
                # -- the pre-fix shape that silently routed an unmapped op to
                # `erase_eprom()` (RESEARCH Pitfall 1a).
                raise AssertionError(
                    f"unreachable: op {op!r} passed the _MULTI_RUN_OPS guard"
                )

        if op in (OP_WRITE, OP_WRITE_PARTIAL, OP_VERIFY):
            # Readback for the fingerprint is best-effort: a readback failure
            # (e.g. the SAME boot-block-locked condition that failed the
            # write/verify runs themselves) must NOT convert an otherwise
            # successful write/verify outcome into BAD (Pitfall 1 extends to
            # this internal readback call too) -- it only means no
            # Fingerprint could be attached.
            actual = b""
            try:
                with tempfile.TemporaryDirectory(prefix="chip_test_verify_") as tmp_dir:
                    readback_path = str(Path(tmp_dir) / "readback.bin")
                    operator.read_eprom(name, eprom_data, output_file=readback_path)
                    try:
                        actual = Path(readback_path).read_bytes()
                    except OSError:
                        actual = b""
            except EpromOperationError:
                actual = b""

            if actual:
                diverged = len(set(outcomes)) != 1 if outcomes else False
                fingerprint = classify_fingerprint(
                    expected,
                    actual,
                    repeat_divergent=diverged,
                    addr_base=region_start,
                )
    finally:
        if tmp_source_path is not None:
            try:
                Path(tmp_source_path).unlink()
            except OSError:
                pass

    diverged = len(set(outcomes)) != 1 if outcomes else False
    if diverged:
        verdict = VERDICT_MARGINAL
        reason = f"{runs} runs disagreed on outcome (D-06 marginal policy)"
    else:
        verdict = VERDICT_OK if outcomes and outcomes[0] else VERDICT_BAD
        reason = ""

    return StepResult(
        op=op,
        verdict=verdict,
        reason=reason,
        run_count=runs,
        fingerprint=fingerprint,
    )


def _dispatch_sdp(
    op: str, name: str, eprom_data: dict[str, Any], operator: Any
) -> StepResult:
    """Dispatch an SDP lock/unlock op to its matching `EpromOperator` method.

    Signature is a FORWARD CONTRACT (v1.30 Phase 133 D-01, LEG-09): the same
    first four positional parameters as `_dispatch_multi_run` --
    `(op: str, name: str, eprom_data: dict[str, Any], operator: Any)` --
    because ROADMAP Phase 134's "Depends on" line names this arm verbatim
    and builds its four-step leg on it. No keyword-only parameters: SDP
    emissions are single-run (D-03, `_MULTI_RUN_OPS` exclusion above), so
    `runs` and `sampler` are deliberately absent here, not merely omitted by
    oversight.

    Structurally clones `_dispatch_multi_run`'s guard -> branch -> terminal
    `raise AssertionError` shape (D-01) rather than importing/reusing it, so
    the module gains no new idiom and criterion 5's deliberate-break test
    gets a single choke point to attack.
    """
    if op not in _SDP_OPS:
        return StepResult(
            op=op,
            verdict=VERDICT_BAD,
            run_count=0,
            reason=(
                f"op {op!r} is not in the SDP dispatch allow-list "
                "(_SDP_OPS) — refused fail-closed rather than falling "
                "through to an operator mutation method"
            ),
        )

    if op == OP_SDP_LOCK:
        is_ok = operator.sdp_lock(name, eprom_data)
    elif op == OP_SDP_UNLOCK:
        is_ok = operator.sdp_unlock(name, eprom_data)
    else:
        # Unreachable in practice: the fail-closed `_SDP_OPS` guard above
        # already refused any op outside {OP_SDP_LOCK, OP_SDP_UNLOCK} before
        # this branch could be reached. Kept as an explicit `else: raise`,
        # deliberately NOT a bare `else` -- the pre-Phase-121 shape that
        # silently routed an unmapped op to `erase_eprom()` and reported OK
        # is what this refuses to reintroduce (RESEARCH Pitfall 1a).
        # `AssertionError` is not a `SerialError`, `HardwareOperationError`,
        # or `EpromOperationError`, so `_run_step`'s D-08 except chain does
        # not catch it and it escapes loudly -- the intended behaviour,
        # proven by
        # tests/test_chip_test_sdp_leg.py::
        # test_dispatch_sdp_terminal_assertion_is_reachable_only_by_bypassing_the_guard.
        raise AssertionError(f"unreachable: op {op!r} passed the _SDP_OPS guard")

    return StepResult(op=op, verdict=VERDICT_OK if is_ok else VERDICT_BAD, run_count=1)


def _dispatch_sdp_leg(
    op: str,
    name: str,
    eprom_data: dict[str, Any],
    operator: Any,
    *,
    step: Step | None = None,
) -> StepResult:
    """Dispatch one of the SDP leg's four write-shaped ops to the
    READ-BACK-EQUALITY oracle (v1.30 Phase 134, T-134-02, D-01...D-05,
    LEG-05/06(engine half)/07/08/16).

    This is the milestone's reason to exist: the verdict comes from
    comparing the read-back bytes against what SHOULD be there, never from
    `write_eprom`'s own bool. A write that returns without error is NOT, by
    itself, evidence of anything -- see D-01 below.

    A SEPARATE dispatcher from `_dispatch_sdp` (133 D-01's frozen four-
    positional forward contract, unchanged here): these four ops need a
    source payload, a read-back, and an `operation_flags` argument that
    signature cannot carry. Structurally clones `_dispatch_sdp`'s /
    `_dispatch_multi_run`'s guard -> branch -> terminal `raise
    AssertionError` shape rather than importing/reusing either.

    ⚠ D-01 (measured, not merely designed around): the `0x86` opt-out ack
    is UNOBSERVABLE from this module. `_operation_context`'s `finally`
    calls `_disconnect_programmer()` (`eprom_operations.py:405-416`), which
    sets `self.comm = None` before `write_eprom` returns, so
    `comm.seen_message_ids` is gone by the time this function could read
    it. Research's truth-table branch 5 (the ack readable as a SEPARATE
    signal) is THEREFORE NOT IMPLEMENTABLE AS WRITTEN and is not attempted
    here. Consequence: `write_eprom`'s bool is a PRECONDITION signal only.
    `True` is reachable only when the state machine succeeded AND (for the
    inhibited-write op) the ack was observed internally by
    `eprom_operations.py`'s own check (`:1654-1662`) -- so `True` proves the
    experiment ran as designed. `False` NEVER means BAD by itself (D-01/
    D-02) -- it routes to `marginal`, naming both candidate causes (the
    opt-out not honoured by older firmware, or a transport fault).

    ⚠ D-03's full 2x2 polarity proof holds for `OP_WRITE_INHIBITED`:
    `(True, A) -> OK`, `(True, B) -> BAD` -- these two hold the bool
    CONSTANT and vary only the read-back, a STRICTLY STRONGER proof than a
    bool-driven implementation could pass, because such an implementation
    cannot produce two different verdicts from one identical bool.
    `(False, A) -> marginal`, `(False, B) -> marginal` pin the precondition
    gate in both read-back directions. P-03 prevention 4's `(False, A) ->
    OK` is OVERTURNED by D-01/D-03 and is deliberately NOT implemented here.

    ⚠ No sixth verdict status (research P-09/`ROADMAP` "no new verdict
    status"): `_verdict_code` (`cli_handlers.py`) is `.get(verdict, 0)`, so
    an unrecognised verdict string would silently exit 0. Only
    VERDICT_OK / VERDICT_BAD / VERDICT_MARGINAL are used below.
    """
    if op not in _SDP_LEG_OPS:
        return StepResult(
            op=op,
            verdict=VERDICT_BAD,
            run_count=0,
            reason=(
                f"op {op!r} is not in the SDP-leg dispatch allow-list "
                "(_SDP_LEG_OPS) — refused fail-closed rather than falling "
                "through to an operator mutation method"
            ),
        )

    region_start, region_length = _write_region_for(step, eprom_data)
    pattern_a = generate_pattern(region_start, region_length)
    pattern_b = generate_inhibited_pattern(region_start, region_length)

    # Per-op (source payload written, expected read-back, operation_flags).
    # The inhibited row's asymmetry IS the oracle: it WRITES pattern B but
    # EXPECTS to read back pattern A (unchanged) -- a leaked lock reads
    # back B instead. FLAG_SKIP_SDP_UNLOCK is set on this op ONLY: setting
    # it on write-restored would defeat that step's whole purpose -- it
    # must be allowed to auto-unlock and succeed so the part is left
    # writable (D-06's "restored" evidence).
    if op == OP_WRITE_BASELINE_B:
        source_payload, expected_readback, flags = pattern_b, pattern_b, 0
    elif op == OP_WRITE_BASELINE_A:
        source_payload, expected_readback, flags = pattern_a, pattern_a, 0
    elif op == OP_WRITE_INHIBITED:
        source_payload, expected_readback, flags = (
            pattern_b,
            pattern_a,
            FLAG_SKIP_SDP_UNLOCK,
        )
    elif op == OP_WRITE_RESTORED:
        source_payload, expected_readback, flags = pattern_a, pattern_a, 0
    else:
        # Unreachable in practice: the fail-closed `_SDP_LEG_OPS` guard
        # above already refused any op outside the four named ops before
        # this branch could be reached. Deliberately an explicit `else:
        # raise`, not a bare `else` -- the pre-Phase-121 shape this project
        # refuses to reintroduce (RESEARCH Pitfall 1a).
        raise AssertionError(f"unreachable: op {op!r} passed the _SDP_LEG_OPS guard")

    # Write, once (single-run: these ops are deliberately NOT _MULTI_RUN_OPS
    # members, D-03).
    tmp_fh = tempfile.NamedTemporaryFile(
        prefix="chip_test_sdp_leg_", suffix=".bin", delete=False
    )
    try:
        tmp_fh.write(source_payload)
    finally:
        tmp_fh.close()
    tmp_source_path = tmp_fh.name

    try:
        wrote_ok = operator.write_eprom(name, eprom_data, tmp_source_path, flags)

        # Read back. ⚠ Unlike `_dispatch_multi_run`'s read-back
        # (`:1483-1493`), this read-back is NOT best-effort decoration -- it
        # IS the verdict (D-05/LEG-05). A failed/degenerate read-back still
        # produces a verdict below (BAD via the length gate), it never
        # silently skips the Fingerprint the way the multi-run write/verify
        # step does.
        actual = b""
        try:
            with tempfile.TemporaryDirectory(
                prefix="chip_test_sdp_leg_verify_"
            ) as tmp_dir:
                readback_path = str(Path(tmp_dir) / "readback.bin")
                operator.read_eprom(name, eprom_data, output_file=readback_path)
                try:
                    actual = Path(readback_path).read_bytes()
                except OSError:
                    actual = b""
        except EpromOperationError:
            actual = b""
    finally:
        try:
            Path(tmp_source_path).unlink()
        except OSError:
            pass

    # a. LENGTH gate FIRST (D-04, P-02). Measured:
    # `classify_fingerprint(A, b"")` returns `total=0, bad=0` -- an empty
    # read-back reads as PERFECT equality, and `_diff_offsets` silently
    # truncates to the common prefix and never raises. This gate runs
    # before any `_diff_offsets`/`classify_fingerprint` call so that trap
    # cannot fire.
    if len(actual) != region_length:
        return StepResult(
            op=op,
            verdict=VERDICT_BAD,
            reason=(
                f"read-back length {len(actual)} bytes != expected region "
                f"length {region_length} bytes — the oracle had no usable "
                "input to compare (length gate, checked before any "
                "classify_fingerprint call)"
            ),
            run_count=1,
        )

    # b. CONTENT degeneracy (D-04). Correct length but degenerate content
    # (all-0x00 / all-0xFF) routes through `classify_fingerprint` and lands
    # `marginal` -- a loose socket or blank chip reads as a contact fault,
    # never a confidently-reported chip finding.
    if actual == b"\x00" * region_length or actual == b"\xff" * region_length:
        fingerprint = classify_fingerprint(
            expected_readback, actual, addr_base=region_start
        )
        return StepResult(
            op=op,
            verdict=VERDICT_MARGINAL,
            reason=(
                "correct-length but degenerate read-back content "
                f"(classification={fingerprint.classification!r}) — a "
                "loose socket or blank/unresponsive chip reads as a contact "
                "fault, not a chip finding (D-04)"
            ),
            fingerprint=fingerprint,
            run_count=1,
        )

    # c. Equality decision. Attach the Fingerprint in every arm.
    fingerprint = classify_fingerprint(
        expected_readback, actual, addr_base=region_start
    )
    equal = actual == expected_readback

    if op == OP_WRITE_INHIBITED:
        # D-03's full 2x2, on pattern A (unchanged) as the expected value.
        if wrote_ok and equal:
            verdict, reason = VERDICT_OK, ""
        elif wrote_ok and not equal:
            # LEG-06, the leg's whole value -- covers both a full change to
            # B and a PARTIAL change (LEG-07, gh#11's exact symptom).
            verdict, reason = (
                VERDICT_BAD,
                (
                    "write_eprom reported success (the state machine completed "
                    "and the 0x86 opt-out ack was observed internally) yet the "
                    "read-back changed from pattern A — the SDP lock did not "
                    "inhibit this write"
                ),
            )
        else:
            # D-01/D-02: a failed precondition is marginal in BOTH read-back
            # directions -- BAD here would manufacture a chip-fault report
            # for a community member running older firmware.
            verdict, reason = (
                VERDICT_MARGINAL,
                (
                    "write_eprom reported failure on the inhibited-write "
                    "precondition — this is a PRECONDITION signal, not the "
                    "verdict (D-01). Most likely causes: (1) the 0x86 opt-out "
                    "ack was not honoured — the connected firmware may predate "
                    "FLAG_SKIP_SDP_UNLOCK support, run `firestarter fw "
                    "--install` to update it and retry; or (2) a transport "
                    "fault. Neither is a chip finding."
                ),
            )
    else:
        # OP_WRITE_BASELINE_B / OP_WRITE_BASELINE_A / OP_WRITE_RESTORED:
        # `expected_readback` is what was written.
        if wrote_ok and equal:
            verdict, reason = VERDICT_OK, ""
        elif wrote_ok and not equal:
            verdict, reason = (
                VERDICT_BAD,
                (
                    "write_eprom reported success but the read-back does not "
                    "match what was written — the write path did not "
                    "transition (LEG-16's dead-write-path shape) or changed "
                    "only part of the region (LEG-07)"
                ),
            )
        elif (not wrote_ok) and equal:
            # P-05's idempotent-baseline shape: must never read as OK.
            verdict, reason = (
                VERDICT_MARGINAL,
                (
                    "write_eprom reported failure yet the read-back already "
                    "matches the intended pattern — the transition is not "
                    "demonstrated (P-05); this must never be reported as OK"
                ),
            )
        else:
            # No opt-out flag is set on these steps, so a failed write with
            # unchanged bytes is a plain dead write path with no host-side
            # cause to blame (gh#20's measured shape: write-baseline-b goes
            # BAD on that bench).
            verdict, reason = (
                VERDICT_BAD,
                (
                    "write_eprom reported failure and the read-back does not "
                    "match the intended pattern — a dead write path with no "
                    "host-side cause to blame"
                ),
            )

    return StepResult(
        op=op,
        verdict=verdict,
        reason=reason,
        fingerprint=fingerprint,
        run_count=1,
    )


# ---------------------------------------------------------------------------
# Applicable-only N-of-M banner DATA (SWEEP-05, Phase 109 Plan 02)
# ---------------------------------------------------------------------------
#
# DATA ONLY -- this module emits no print/render/CLI output; rendering the
# "only N of M tests ran -- pass --destructive on a scrap chip for the rest"
# banner belongs to Phase 110 (report model) / Phase 112 (dev test handler).
#
# Applicable-only counting (109-CONTEXT.md "Claude's Discretion", LOCKED by
# 109-PATTERNS.md): M excludes NA/inapplicable slots (blank-check NA on
# SRAM/FRAM, id NA when the DB's chip-id sentinel is 0, erase NA on UV /
# non-FLAG_CAN_ERASE) so the banner never inflates M with never-achievable
# slots. M is computed from the SINGLE derived `Plan` object -- its
# `steps` (already-supported, already-executable ops) PLUS the applicable
# entries on `plan.locked_destructive` (every entry there is, by 109-01's
# construction, an applicable destructive op a `--destructive` run WOULD
# execute; NA destructive ops are never placed there) -- derive_plan is
# NEVER called a second time to compute M (D-01).
#
# N counts the steps THIS run actually executed: any StepResult verdict in
# {OK, BAD, marginal} counts as "ran" (a ran-but-BAD step still counts,
# since "ran" and "verdict" are separate axes); NA and SKIPPED steps do not
# count toward N (they never reached the operator).

_RAN_VERDICTS = frozenset({VERDICT_OK, VERDICT_BAD, VERDICT_MARGINAL})


@dataclass
class BannerCounts:
    """Applicable-only N-of-M banner DATA (SWEEP-05) -- no rendering here.

    `n_ran` is the number of applicable steps THIS run executed (any
    verdict); `m_applicable` is the number of applicable steps a
    `--destructive` run would execute for this SAME chip (from the single
    `Plan` object, never a second derivation); `locked_steps` is
    `plan.locked_destructive` verbatim, for a future report/banner to name
    the specific missing ops (e.g. "write, erase").
    """

    n_ran: int
    m_applicable: int
    locked_steps: list[tuple[str, str]] = field(default_factory=list)


def count_applicable(plan: Plan, results: list[StepResult]) -> BannerCounts:
    """Compute the SWEEP-05 applicable-only N-of-M banner data.

    M = `sum(1 for s in plan.steps if s.supported)` PLUS
    `len(plan.locked_destructive)` -- both read off the ONE `plan` object
    passed in; this function never calls `derive_plan` (D-01/T-109-08).

    N = count of `results` whose verdict is in {OK, BAD, marginal} (ran);
    NA and SKIPPED results are excluded.

    For a `write_scope="none"` chip run, `locked_destructive` is non-empty
    and N < M (the banner-trigger condition). For a `write_scope="full"` (or
    `"partial"`) run, `locked_destructive` is empty and N == M (banner would
    not fire), since the previously-locked ops are now real supported
    `steps` that the run executed.
    """
    m_applicable = sum(1 for s in plan.steps if s.supported) + len(
        plan.locked_destructive
    )
    n_ran = sum(1 for r in results if r.verdict in _RAN_VERDICTS)
    return BannerCounts(
        n_ran=n_ran,
        m_applicable=m_applicable,
        locked_steps=list(plan.locked_destructive),
    )
