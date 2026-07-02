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

from dataclasses import dataclass, field
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

# Ordered op vocabulary (id-check FIRST per SWEEP-03).
OP_ID = "id"
OP_READ = "read"
OP_BLANK_CHECK = "blank-check"
OP_WRITE = "write"
OP_VERIFY = "verify"
OP_ERASE = "erase"


@dataclass
class Step:
    """A single derived operation descriptor.

    `supported=False` means the step is NA for this chip (a reason is always
    recorded); `destructive` marks steps that write/erase the part. This
    plan (108-03) only ANNOTATES `destructive` -- it does not strip
    write/erase from the plan when the caller passes `destructive=False`.
    The plan-construction `--destructive` gate is Phase 109.
    """

    op: str
    supported: bool
    reason: str
    destructive: bool = False


@dataclass
class Plan:
    """Ordered, derived test plan for a single chip (SWEEP-01)."""

    name: str
    steps: list[Step] = field(default_factory=list)
    reason: str = ""


def derive_plan(name: str, db: Any, *, destructive: bool = False) -> Plan:
    """Derive the ordered op list for `name` strictly from frozen DB fields.

    Reads `db.get_eprom(name)` then `db.convert_to_programmer(full)` --
    NEVER `chip_resolver.resolve_chip` (Pattern 1/2, T-108-06) -- so this
    works even for chips whose `support_status` would make `resolve_chip`
    refuse them. `destructive` only annotates write/erase steps; it never
    removes them from the returned plan (Task 2 `done` criterion).

    Unknown chips (no DB entry) return an empty `Plan` with `reason` set --
    there is nothing to derive.
    """
    full = db.get_eprom(name)
    if not full:
        return Plan(name=name, steps=[], reason=f"{name}: not found in database")

    prog = db.convert_to_programmer(full)
    protocol = prog.get("algorithm", full.get("protocol-id", 0))
    etype = full.get("electrical-type", "")
    can_erase = bool(prog.get("flags", 0) & FLAG_CAN_ERASE)
    chip_id = prog.get("chip-id", 0)

    steps: list[Step] = []

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

    # write: always supported, always flagged destructive. Listed
    # regardless of the `destructive` kwarg (annotate-only, Task 2 `done`).
    steps.append(Step(op=OP_WRITE, supported=True, reason="", destructive=True))

    steps.append(Step(op=OP_VERIFY, supported=True, reason=""))

    # erase: supported only if FLAG_CAN_ERASE is set AND protocol != 0x05
    # (flash4 auto-erases per page; the flag is deliberately clear for it --
    # Pitfall 6). UV-EPROM never has the flag set (electrical-type is not in
    # {EEPROM, Flash/EEPROM}) so it is NA here for the same condition.
    if can_erase and protocol != _PROTOCOL_FLASH4:
        steps.append(Step(op=OP_ERASE, supported=True, reason="", destructive=True))
    else:
        if protocol == _PROTOCOL_FLASH4:
            reason = "flash4 (0x05) auto-erases per page; no separate erase op"
        elif etype == "UV-EPROM":
            reason = "UV-EPROM has no electrical erase (UV light only)"
        else:
            reason = "FLAG_CAN_ERASE not set for this chip"
        steps.append(
            Step(op=OP_ERASE, supported=False, reason=reason, destructive=True)
        )

    return Plan(name=name, steps=steps, reason="")


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
_DESTRUCTIVE_OPS = frozenset({OP_WRITE, OP_ERASE})
# Steps whose per-run outcome is compared for the N>=2 disagreement policy
# (D-06: destructive/verify ONLY -- write, erase, verify; read disagreement is
# a divergence metric, never a verdict flip).
_MULTI_RUN_OPS = frozenset({OP_WRITE, OP_ERASE, OP_VERIFY})

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
    steps, Task 3).
    """

    op: str
    verdict: str
    reason: str = ""
    error_code: int | None = None
    fingerprint: Fingerprint | None = None
    run_count: int = 0


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
) -> list[StepResult]:
    """Execute `plan.steps` as independent, non-fatal steps (SWEEP-02).

    Each supported step re-resolves `plan.name` through `resolve_chip(name,
    db=db)` -- the guard-HONORING execution path (Pattern 2) -- and dispatches
    to the matching existing `EpromOperator` method (id -> check_eprom_id,
    read -> read_eprom, blank-check -> check_eprom_blank, write ->
    write_eprom, verify -> verify_eprom, erase -> erase_eprom). NA steps from
    `derive_plan` are recorded NA WITHOUT any operator call.

    The id-check step runs FIRST (SWEEP-03): a chip-ID mismatch closes a
    `destructive_gate` that every destructive step (write/erase) consults
    BEFORE calling its operator method, marking itself SKIPPED with reason
    (chip left pristine) instead. Non-destructive id/read/blank-check findings
    are still recorded regardless of the gate.

    One step's `BAD` verdict or raised exception NEVER aborts the remaining
    steps (Pitfall 1) -- each step's body is wrapped in its own try/except.
    `EpromOperationError` -> `BAD` capturing `err.error_code` (RPT-03); a
    `resolve_chip` refusal -> `SKIPPED`/`NA` with reason (Pitfall 2).

    Task 3 wires `runs>=2` on destructive/verify steps (marginal-on-
    disagreement, D-05/D-06) and the write/verify Fingerprint. This task
    (Task 1) executes each step exactly once; the `runs` parameter is
    threaded through for Task 3 and does not change Task 1 behavior beyond
    accepting the kwarg.
    """
    results: list[StepResult] = []
    destructive_gate_closed = False

    for step in plan.steps:
        if not step.supported:
            results.append(_skip_result(step.op, step.reason, verdict=VERDICT_NA))
            continue

        if step.op in _DESTRUCTIVE_OPS and destructive_gate_closed:
            results.append(_skip_result(step.op, _DESTRUCTIVE_GATE_REASON))
            continue

        result = _run_step(plan.name, step, operator, db, runs=runs)
        results.append(result)

        if step.op == OP_ID:
            destructive_gate_closed = _id_step_closes_gate(result)

    return results


def _id_step_closes_gate(result: StepResult) -> bool:
    """SWEEP-03: close the destructive gate on an id-check failure/mismatch.

    `is_ok is False` (chip-ID check failed) OR the step itself errored (`BAD`)
    both close the gate -- Pitfall 4 requires ANY id-uncertainty to gate
    destructive steps shut, not just an explicit numeric mismatch.
    """
    return result.verdict == VERDICT_BAD


def _run_step(
    name: str, step: Step, operator: Any, db: Any, *, runs: int
) -> StepResult:
    """Execute a single supported step through the guard-honoring resolver.

    Wraps the ENTIRE step body (resolve + dispatch) in try/except so no
    exception escapes to the `run_plan` loop (Pitfall 1). Reference:
    cli_handlers.py:1568 `dev_validate_family` -- the same
    `resolve_chip(name, db=...)` + operator-method compose pattern used here.
    """
    eprom_data, skip_stub, reason = _resolve_or_none(name, db)
    if skip_stub is not None:
        skip_stub.op = step.op
        return skip_stub

    try:
        return _dispatch_step(name, step, eprom_data, operator)
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
    name: str, step: Step, eprom_data: dict[str, Any], operator: Any
) -> StepResult:
    """Dispatch `step.op` to its matching existing `EpromOperator` method.

    id -> check_eprom_id (bool, Optional[int]); all others -> a single bool.
    The engine sets NO VPP, builds NO wire dict, and passes NO --force -- it
    only calls the operator's existing public methods.
    """
    if step.op == OP_ID:
        is_ok, _detected_id = operator.check_eprom_id(name, eprom_data)
        return StepResult(
            op=step.op,
            verdict=VERDICT_OK if is_ok else VERDICT_BAD,
            run_count=1,
        )

    method = {
        OP_READ: operator.read_eprom,
        OP_BLANK_CHECK: operator.check_eprom_blank,
        OP_WRITE: operator.write_eprom,
        OP_VERIFY: operator.verify_eprom,
        OP_ERASE: operator.erase_eprom,
    }[step.op]
    is_ok = method(name, eprom_data)
    return StepResult(
        op=step.op,
        verdict=VERDICT_OK if is_ok else VERDICT_BAD,
        run_count=1,
    )
