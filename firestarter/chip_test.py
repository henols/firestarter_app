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

This module is pure compute over host-side byte arrays: it sets no VPP,
builds no wire dict, and calls no operator/firmware method. Plan 108-04
extends this module with `run_plan` -- the non-fatal per-step executor that
composes existing `EpromOperator` methods only (still zero new firmware
dispatch, zero VPP-set, zero raw wire dict).
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from firestarter.chip_resolver import resolve_chip
from firestarter.constants import FLAG_CAN_ERASE  # 0x02 -- do NOT redefine; import
from firestarter.exceptions import (
    ChipNotFoundError,
    ChipNotImplementedError,
    EpromOperationError,
)

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
        else:
            reason = "FLAG_CAN_ERASE not set for this chip"
        # NA erase is never a supported executable step regardless of the
        # write_scope -- there is nothing to lock/omit here (it was never
        # runnable), so it is NOT added to locked_destructive either.
        steps.append(
            Step(op=OP_ERASE, supported=False, reason=reason, destructive=True)
        )

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
_DESTRUCTIVE_OPS = frozenset({OP_WRITE, OP_WRITE_PARTIAL, OP_ERASE})
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
_MULTI_RUN_OPS = frozenset({OP_WRITE, OP_WRITE_PARTIAL, OP_ERASE, OP_VERIFY})

_DESTRUCTIVE_GATE_REASON = (
    "chip-ID mismatch — destructive steps gated (chip left pristine)"
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

    for step in plan.steps:
        if not step.supported:
            results.append(_skip_result(step.op, step.reason, verdict=VERDICT_NA))
            continue

        if step.op in _DESTRUCTIVE_OPS and destructive_gate_closed:
            results.append(_skip_result(step.op, _DESTRUCTIVE_GATE_REASON))
            continue

        result = _run_step(plan.name, step, operator, db, runs=runs, sampler=sampler)
        results.append(result)

        if step.op == OP_ID:
            destructive_gate_closed = _id_step_closes_gate(result)

    return results


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

# UV detection at EXECUTION time: `_dispatch_multi_run`'s `eprom_data` is
# `resolve_chip`'s PROGRAMMER dict (via `convert_to_programmer`), which does
# NOT carry `electrical-type` -- only `derive_plan`'s guard-bypassing `full`
# dict does. Algorithm 0x0B (EPROM_LEGACY/protocol-id 11) IS carried through
# to the programmer dict as `algorithm`, and is UV-EPROM-exclusive across the
# whole chip database (verified: no non-UV chip uses protocol-id 0x0B) --
# this is the execution-time UV signal `_write_region_for` uses. `full`-style
# dicts (bench-free unit tests, or any future caller with the richer dict)
# are also honored via the `electrical-type` field when present.
_PROTOCOL_UV_EPROM = 0x0B


def _write_region_for(eprom_data: dict[str, Any]) -> tuple[int, int]:
    """Choose the (start, length) write/verify region for `eprom_data`.

    UV-EPROM chips get a small, top-anchored, high-address window
    `[mem_size - _UV_WRITE_REGION_LENGTH, mem_size)` (PATT-03): the
    high-address base (all high bits set) makes `generate_pattern`'s
    address-XOR-fold exercise the upper-address decode -- the Bug-A
    upper-address read-path fault surface -- and the tiny window lets an
    eraser-less tester safely retry. The WIDTH always comes from the
    `_UV_WRITE_REGION_LENGTH` module constant, NEVER from any DB field
    (SC4: a bad DB entry cannot widen it); `memory-size` only bounds where
    the window is placed. Non-UV chips (and UV chips whose memory-size is
    missing/too small to fit the window) get the engine default region.

    UV detection accepts EITHER `electrical-type == "UV-EPROM"` (the `full`
    DB dict, used by bench-free unit tests) OR `algorithm ==
    _PROTOCOL_UV_EPROM` (the programmer dict `_dispatch_multi_run` actually
    sees at execution time, via `resolve_chip`/`convert_to_programmer`,
    which drops `electrical-type`).
    """
    is_uv = (
        eprom_data.get("electrical-type", "") == "UV-EPROM"
        or eprom_data.get("algorithm") == _PROTOCOL_UV_EPROM
    )
    if is_uv:
        mem_size = int(eprom_data.get("memory-size", 0) or 0)
        if mem_size >= _UV_WRITE_REGION_LENGTH:
            return mem_size - _UV_WRITE_REGION_LENGTH, _UV_WRITE_REGION_LENGTH
        # Defensive fallback: mem_size missing/too small to fit the window
        # would produce a negative start -- use the engine default instead.
    return _WRITE_REGION_START, _WRITE_REGION_LENGTH


def _run_step(
    name: str, step: Step, operator: Any, db: Any, *, runs: int, sampler: Any = None
) -> StepResult:
    """Execute a single supported step through the guard-honoring resolver.

    Wraps the ENTIRE step body (resolve + dispatch) in try/except so no
    exception escapes to the `run_plan` loop (Pitfall 1). Reference:
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
    attach a `Fingerprint` (PATT-02 wiring, Pitfall 3 addr_base). The engine
    sets NO VPP, builds NO wire dict, and passes NO --force -- it only calls
    the operator's existing public methods.

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
            step.op, name, eprom_data, operator, runs=runs, sampler=sampler
        )
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
) -> StepResult:
    """Run a destructive/verify op `runs` times; `marginal` on disagreement.

    Collects a per-run bool outcome (the operator method's own return value)
    for write/erase; write/verify ALSO builds the expected address-derived
    pattern and reads back via `operator.verify_eprom`'s outcome plus a
    fresh `read_eprom` to compute the `Fingerprint` (PATT-02). Disagreement
    across the N per-run outcomes -> `marginal`, never coerced to a
    confident OK/BAD (D-06, the AM27C020 structural case). The write/verify
    region is chosen per-chip by `_write_region_for` (PATT-03) -- UV-EPROM
    chips get a small top-anchored high-address window; other chips keep
    the engine default region.

    `sampler` (D-04, Phase 112) is invoked as `sampler("before")` /
    `sampler("after")` tightly bracketing EACH `operator.write_eprom(...)`
    call -- ONLY in the `op == OP_WRITE` branch, never around OP_VERIFY or
    OP_ERASE, and never around the whole run loop (a write droop must stay
    distinguishable from a read droop). `sampler=None` adds zero calls.

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

    region_start, region_length = _write_region_for(eprom_data)
    expected = generate_pattern(region_start, region_length)
    if op in (OP_WRITE, OP_VERIFY):
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
            if op == OP_WRITE:
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
                # outside {OP_WRITE, OP_VERIFY, OP_ERASE} before this loop
                # could start (121-02, T-121-05). Kept explicit rather than a
                # bare `else: # OP_ERASE` -- the pre-fix shape that silently
                # routed an unmapped op to `erase_eprom()` (RESEARCH
                # Pitfall 1a).
                raise AssertionError(
                    f"unreachable: op {op!r} passed the _MULTI_RUN_OPS guard"
                )

        if op in (OP_WRITE, OP_VERIFY):
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
