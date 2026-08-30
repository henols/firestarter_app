"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Test-plan engine for `firestarter dev test <chip>`.

The write/verify pattern is ADDRESS-DERIVED rather than fixed, so stuck,
shorted and aliased address lines show up instead of being hidden.
`classify_fingerprint` names why a verify failed (blank/contact,
address-line, transport) and falls back to `indeterminate` rather than
guessing.

Pure compute over host-side byte arrays: this module sets no VPP and calls no
firmware method itself. `run_plan` composes existing `EpromOperator` methods.
The one exception to "builds no wire dict" is a single `operation_flags` bit,
FLAG_SKIP_SDP_UNLOCK, on the SDP leg's inhibited-write step.
"""

from __future__ import annotations

import hashlib
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from firestarter.chip_resolver import resolve_chip
from firestarter.constants import (
    FLAG_CAN_ERASE,  # 0x02 -- do NOT redefine; import
    FLAG_SKIP_SDP_UNLOCK,  # 0x100 -- passed on OP_WRITE_INHIBITED ONLY.
    # Do NOT redefine; import.
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
from firestarter.sdp_capability import sdp_capability  # the SDP leg's derivation source

# ---------------------------------------------------------------------------
# Address-derived pattern generator
# ---------------------------------------------------------------------------


def address_fold_byte(addr: int) -> int:
    """XOR-fold an absolute address into a single expected byte.

    Every address line (A0..A31) contributes to the expected byte via the
    fold, so a stuck/shorted/aliased high address line changes the expected
    byte at exactly the addresses where that bit flips -- unlike a fixed
    pattern (e.g. all-0x55), which is blind to address-line faults.
    """
    return (addr ^ (addr >> 8) ^ (addr >> 16) ^ (addr >> 24)) & 0xFF


def generate_pattern(start: int, length: int) -> bytes:
    """Region-parameterized address-derived pattern.

    Derives each byte from its ABSOLUTE address (`start + i`), never from
    the offset alone -- no full-chip assumption is baked in, so this same
    function serves both a full-chip pattern and a small high-address
    region (the UV small-region write cap).
    """
    return bytes(address_fold_byte(start + i) for i in range(length))


def generate_inhibited_pattern(start: int, length: int) -> bytes:
    """The SDP leg's inhibited-write payload B.

    `generate_pattern` is a PURE function of (start, length), so calling it a
    second time -- with the same region, or a "different seed" that reduces to
    the same region -- makes A and B byte-identical and turns the leg's central
    assertion ("the chip did not accept a write while locked") into a tautology
    that reads as correct in review.

    So B is the bitwise COMPLEMENT of A, derived from it rather than
    re-derived: they differ at every byte by construction, not by chance. A
    nonce would break reproducibility and re-key `dedup_fingerprint` on every
    run.
    """
    a = generate_pattern(start, length)
    return bytes(~b & 0xFF for b in a)


def prepass_images(length: int) -> tuple[bytes, bytes]:
    """Cheap all-0x00 / all-0xFF pre-pass images.

    A cheap sanity pre-pass before the address-derived pattern: an
    all-0xFF read-back before writing anything is itself evidence of a
    blank/contact condition (see `classify_fingerprint`).
    """
    return b"\x00" * length, b"\xff" * length


# ---------------------------------------------------------------------------
# Shared byte-diff-offset helper -- reused, not reimplemented
# ---------------------------------------------------------------------------
#
# Mirrors the exact divergence math in `consistency_check_eprom`
# (eprom_operations.py:842-863): cmp_len / diff_offsets / pct / first
# divergence offset. This is the ONE divergence primitive `classify_fingerprint`
# consumes -- do NOT add a second parallel divergence implementation
# elsewhere in this codebase. The math is small enough to
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
# Four-bucket byte-mismatch fingerprint classifier
# ---------------------------------------------------------------------------

# The four locked outcome labels -- never coerce an ambiguous
# distribution into one of the first three; fall back to indeterminate.
FP_BLANK_CONTACT = "blank/contact"
FP_ADDRESS_LINE = "address-line"
FP_TRANSPORT = "transport"
FP_INDETERMINATE = "indeterminate"

# Candidate thresholds (Claude's discretion) -- direction is
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

    Consumes the shared `_diff_offsets` divergence primitive (the
    same math `consistency_check_eprom` uses for run1-vs-run2 divergence,
    here applied to expected-pattern-vs-read-back). Never writes a second
    divergence implementation.

    Classification order is LOCKED:
      1. blank/contact  -- cheapest, most common false-PASS source
      2. address-line   -- power-of-two high-bit clustering (needs addr_base
                            to map offsets to ABSOLUTE addresses, Pitfall 3)
      3. transport       -- scattered + non-repeatable across N>=2 runs
      4. indeterminate   -- fallback; NEVER coerce an ambiguous distribution
                            into a confident label.
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

    # 4. indeterminate: never coerce an ambiguous distribution.
    return Fingerprint(
        total=cmp_len,
        bad=bad,
        bad_pct=bad_pct,
        classification=FP_INDETERMINATE,
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Plan derivation -- the guard-BYPASSING path.
#
# `derive_plan` reads frozen DB fields only. The support-status guard lives
# exclusively in `chip_resolver.resolve_chip`, which derivation never calls, so
# a chip whose support_status would make resolve_chip refuse it still yields a
# full plan. The guard-HONOURING path is `run_plan`, which re-resolves each
# executed step through resolve_chip.
#
# Op inclusion is a PURE function of the frozen protocol-id / electrical-type /
# FLAG_CAN_ERASE fields -- the build-time classifier is never re-invoked here.
# ---------------------------------------------------------------------------

# Protocol 0x05 (FLASH_AMD_STD / "flash4") auto-erases per page during the
# page-write; convert_to_programmer() deliberately clears FLAG_CAN_ERASE for
# this protocol (database.py:582-595) because setting it would route a 12V
# bulk-erase onto a 5V-only part (Pitfall 6). No named constant for this
# protocol id exists in constants.py -- mirror database.py's own `algo != 5`
# check rather than introduce a new cross-module constant.
_PROTOCOL_FLASH4 = 0x05

# Protocol 0x0D. Kept as a defensive fallthrough: the arm is still reachable
# for a 0x0D row whose electrical-type falls outside {EEPROM, Flash/EEPROM} --
# a user-override database shape. Routing such a row into the generic
# flag-keyed fallback would name an internal wire flag in a report a community
# tester reads, which is forbidden.
_PROTOCOL_EEPROM_28C = 0x0D

# The two protocols whose family auto-erases per page during the write, so no
# step in a full/partial plan can EVER leave the device blank. There is no
# erase op for a blank-check to sit behind, and a supported blank-check here
# would report chip state rather than tool health.
_AUTO_ERASE_ON_WRITE_PROTOCOLS = frozenset({_PROTOCOL_FLASH4, _PROTOCOL_EEPROM_28C})

# SRAM/FRAM electrical types and protocol ids: blank-check has no meaningful
# concept for volatile/byte-rewritable memory. derive_plan owns this NA
# decision up front (RESEARCH nuance recommendation (a)) rather than relying
# on check_eprom_blank's own short-circuit (eprom_operations.py:1656-1676),
# which the plan mirrors here for the SAME protocol-id set.
_SRAM_FRAM_ETYPES = frozenset({"SRAM", "FRAM"})
_SRAM_PROTO_IDS = frozenset({0x0E, 0x27, 0x28, 0x29})

# Ordered op vocabulary (id-check FIRST). Seven strings:
# `OP_WRITE_PARTIAL` is in the vocabulary so
# the partial-vs-full distinction is visible in the op name itself -- every
# consumer that reads `StepResult.op` (the `dedup_fingerprint` hash, the
# report renderer) sees it without learning a new field. The vocabulary
# deliberately stops here: no `verify-partial` partner exists, because a
# verify's region is definitionally the preceding write's region
# (`Step.write_region` is set equal on both steps by `derive_plan`) -- a
# partner string would encode zero new information.
OP_ID = "id"
OP_READ = "read"
OP_BLANK_CHECK = "blank-check"
OP_WRITE = "write"
OP_WRITE_PARTIAL = "write-partial"
OP_VERIFY = "verify"
OP_ERASE = "erase"

# SDP lock/unlock op strings. Exactly two --
# only the two ops the mechanism criteria exercise are defined here; the
# leg's other ops are deliberately NOT pre-defined (`ruff`'s
# `F` rules do not flag unused module-level constants, so extra constants
# would be genuinely dead code). Engine-local op strings,
# NOT wire constants -- no `constants.py` / `firestarter.h` mirroring is
# triggered by adding these.
OP_SDP_LOCK = "sdp-lock"
OP_SDP_UNLOCK = "sdp-unlock"

# The SDP leg's four remaining op strings. Engine-local, NOT wire constants --
# adding them triggers no firmware lockstep and no .hex re-cut. Listed in the
# leg's own step order.
#
# TWO baseline ops rather than one folded op: the terminal-facing table shows
# only op / verdict / error_code / fingerprint, and `reason` reaches only the
# markdown and JSON. A failing baseline DIRECTION hidden in `reason` would be
# invisible to whoever reads the terminal, on the very step that decides whether
# a lock is emitted at all.
OP_WRITE_BASELINE_B = "write-baseline-b"
OP_WRITE_BASELINE_A = "write-baseline-a"
OP_WRITE_INHIBITED = "write-inhibited"
OP_WRITE_RESTORED = "write-restored"

# The leg's step order, single-sourced: `derive_plan` appends these Steps in
# EXACTLY this order, and every downstream count derives from
# `len(_SDP_LEG_STEP_ORDER)` rather than restating the number.
#
# The leg is SIX steps, not the four some older text describes. Two transition
# directions are needed -- a single baseline write cannot tell a dead write path
# from a chip already holding the target pattern -- and `write-restored` is the
# only step producing evidence the part was left writable again, on a family
# whose protection state cannot be read back at all. Dropping it would end every
# run on `sdp-unlock OK`, an emission claim with nothing behind it.
_SDP_LEG_STEP_ORDER: tuple[str, ...] = (
    OP_WRITE_BASELINE_B,
    OP_WRITE_BASELINE_A,
    OP_SDP_LOCK,
    OP_WRITE_INHIBITED,
    OP_SDP_UNLOCK,
    OP_WRITE_RESTORED,
)

# The `write_scope="none"` advisory prose, in the same
# `'write_scope="none": ... omitted'` shape the shipped write/verify/
# erase `locked_destructive` reasons already use above -- naming the SDP
# leg's own governing decision rather than reusing the write-scope tag on a
# reason it does not own.
_SDP_LOCKED_REASON = 'write_scope="none": {op} omitted'

# Region-policy vocabulary. Plain
# module-level strings mirroring how this module already carries its op
# vocabulary (OP_* above) -- `Step.region_policy` is set exactly once by
# `derive_plan` and read-only downstream (`_resolve_write_target`,
# execution time). `fixed` is today's pre-existing small-region behaviour
# (both non-UV-at-partial and the SDP leg); `full-device` is a non-UV write
# whose width comes from `memory-size` after `full_device_region`'s sanity
# check; `uv-slot` is a UV part's execution-time-masked slot write.
REGION_POLICY_FIXED = "fixed"
REGION_POLICY_FULL_DEVICE = "full-device"
REGION_POLICY_UV_SLOT = "uv-slot"

# Per-cycle PAYLOAD recipe: `region_policy` says WHERE a cycle writes,
# `cycle_payload` says whether successive cycles write the SAME bytes there.
# Decided once by `derive_plan`, read-only at execution time.
#
# Each cycle must present the device with a target state that DIFFERS from its
# current state, or its verify proves nothing. Three ways to get that:
#
#   `same`      -- something else already resets state between cycles: an
#                  erase step in the cycle, or a protocol whose page writes
#                  auto-erase internally.
#   `alternate` -- freely rewritable in BOTH directions (SRAM/FRAM). Cycle n
#                  writes the pattern, n+1 its complement; free, and it also
#                  exercises the data lines the other way.
#   `uv-tranche`-- monotonic, cannot be erased at all (UV-EPROM). Each cycle
#                  clears a disjoint tranche of bits the write would have
#                  cleared anyway, so it costs no extra bits.
CYCLE_PAYLOAD_SAME = "same"
CYCLE_PAYLOAD_ALTERNATE = "alternate"
CYCLE_PAYLOAD_UV_TRANCHE = "uv-tranche"


@dataclass
class Step:
    """A single derived operation descriptor.

    `supported=False` means NA for this chip (a reason is always recorded);
    `destructive` marks steps that write or erase the part.

    `write_region`, `region_policy` and `full_device_permitted` are set ONCE by
    `derive_plan` and are READ-ONLY downstream -- nothing may re-derive them.
    A verify's region is definitionally the preceding write's. `None` means
    "use the engine default".

    Region WIDTH comes from a module constant for the `fixed` and `uv-slot`
    policies, so a malicious or misconfigured DB entry cannot widen the write
    window; `memory-size` only bounds WHERE it sits. `full-device` deliberately
    reverses that -- there the width IS memory-size-derived, after
    `full_device_region`'s sanity check.

    `full_device_permitted` is True only for `write_scope="full"`, which is
    what lets `_resolve_write_target` decide without a new parameter.
    """

    op: str
    supported: bool
    reason: str
    destructive: bool = False
    write_region: tuple[int, int] | None = None
    # The per-cycle payload recipe. Set by `derive_plan` on the write
    # and verify steps alongside `write_region`/`region_policy`; every other
    # step keeps the `same` default, which is also the pre-cycle behaviour.
    cycle_payload: str = CYCLE_PAYLOAD_SAME
    region_policy: str = REGION_POLICY_FIXED
    full_device_permitted: bool = False


@dataclass
class Plan:
    """Ordered, derived test plan for a single chip.

    `locked_destructive` is ADVISORY ONLY: the (op, reason) of write/erase
    steps a destructive run would have added. `run_plan` MUST NOT iterate it --
    it exists so the N-of-M banner can count without a second `derive_plan`
    call, and without giving the executor any path to a destructive op in a
    non-destructive run. It is empty in production; the banner still carries
    signal when the chip-ID gate closes or `resolve_chip` refuses a step.

    `is_uv` is decided EXACTLY ONCE by `derive_plan` from the DB's
    `electrical-type` -- the only axis that is both complete and exact.
    Downstream may only READ it. Nothing may re-derive UV-ness from a proxy:
    the `algorithm == 0x0B` guess matches only 32 of 301 UV parts.
    """

    name: str
    steps: list[Step] = field(default_factory=list)
    reason: str = ""
    locked_destructive: list[tuple[str, str]] = field(default_factory=list)
    is_uv: bool = False


def is_uv_eprom(full: dict) -> bool:
    """Exact, name-keyed UV-EPROM predicate.

    Takes the FULL DB dict from `db.get_eprom` -- never the programmer dict,
    which does not carry `electrical-type`.

    Do not substitute a protocol proxy: `algorithm == 0x0B` matches only 32 of
    301 UV parts, and widening to {0x07, 0x08, 0x0B} recovers all 301 but
    wrongly includes 28 non-UV EEPROMs. Only `electrical-type` is exact.

    A UV part that fails this test receives an UNPROMPTED FULL-DEVICE WRITE, so
    a guess here is a chip-destroying bug, not a coverage gap.
    """
    return full.get("electrical-type", "") == "UV-EPROM"


_WRITE_SCOPE_NONE = "none"
_WRITE_SCOPE_PARTIAL = "partial"
_WRITE_SCOPE_FULL = "full"
_WRITE_SCOPES = frozenset({_WRITE_SCOPE_NONE, _WRITE_SCOPE_PARTIAL, _WRITE_SCOPE_FULL})


def derive_plan(name: str, db: Any, *, write_scope: str = "none") -> Plan:
    """Derive the ordered op list for `name` strictly from frozen DB fields.

    Reads `db.get_eprom` then `db.convert_to_programmer`, NEVER
    `chip_resolver.resolve_chip` -- so it works even for chips whose
    `support_status` would make `resolve_chip` refuse them. `write_scope`
    comes only from this call's kwarg, never from config or environment.

    Three accepted values, fail-closed against anything else:

    - `"none"` -- write/verify/erase are structurally OMITTED from
      `Plan.steps` and recorded on the advisory `Plan.locked_destructive`
      instead. `run_plan` has no code path that iterates those.
    - `"full"` -- write, verify and erase are real steps. On a non-UV chip
      this spans the whole device (minus flash4 boot blocks) when
      `full_device_region` accepts `memory-size`; a UV chip gets the top slot.
    - `"partial"` -- same steps, but the region is always the top-anchored
      small window and `full_device_permitted` is False, so the
      full-device-if-blank outcome is unreachable regardless of chip state.

    An unrecognised value raises ValueError -- never a silent fallback to a
    mode that writes.

    `Plan.is_uv`, `Step.write_region` and `Step.region_policy` are decided
    HERE and ONLY HERE (a verify's region is definitionally the preceding
    write's). Downstream code may READ these, never re-derive them.

    Region WIDTH comes from a module constant, never from a DB field, for the
    `fixed` and `uv-slot` policies -- `memory-size` only bounds WHERE the
    window sits. `full-device` is the one exception, and only after its own
    sanity check passes; a failing check falls back to the small region, never
    to a widened one.

    The SDP leg's steps get their own region and are never widened to the full
    device.
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

    # Region computation lives HERE, from Plan.is_uv and memory-size, producing a
    # (region, policy, reason) decision -- the POLICY travels on
    # `Step.region_policy` so execution time knows what kind of region it is, not
    # just where it sits.
    #
    #   scope none  -> region None, policy fixed.
    #   UV, either  -> uv-slot policy at the first slot candidate; falls back to
    #                  top-anchored/fixed if the device cannot hold one slot. The
    #                  scope still matters and reaches the executor via
    #                  `full_device_permitted`.
    #   non-UV full -> ask `full_device_region`. A tuple gives full-device policy;
    #                  a refusal gives fixed policy and the reason is recorded on
    #                  the step so it reaches the report.
    #   non-UV part -> top-anchored, fixed.
    region_reason = ""
    full_device_permitted = write_scope == _WRITE_SCOPE_FULL
    mem_size = int(full.get("memory-size", 0) or 0)
    # The per-cycle payload recipe, decided HERE and only here, from the
    # same three facts this function already holds. Ordered UV first: a UV part
    # is never SRAM, but keying on `is_uv` before the volatile test means a
    # future electrical-type oddity cannot route a UV part to `alternate` and
    # ask it for an impossible 0->1 transition.
    if is_uv:
        cycle_payload = CYCLE_PAYLOAD_UV_TRANCHE
    elif etype in _SRAM_FRAM_ETYPES or protocol in _SRAM_PROTO_IDS:
        cycle_payload = CYCLE_PAYLOAD_ALTERNATE
    else:
        cycle_payload = CYCLE_PAYLOAD_SAME
    if write_scope == _WRITE_SCOPE_NONE:
        write_region = None
        region_policy = REGION_POLICY_FIXED
    elif is_uv:
        slot_starts = uv_slot_starts(mem_size, _UV_WRITE_REGION_LENGTH)
        if slot_starts:
            write_region = (slot_starts[0], _UV_WRITE_REGION_LENGTH)
            region_policy = REGION_POLICY_UV_SLOT
        else:
            write_region = _top_anchored_or_default(full)
            region_policy = REGION_POLICY_FIXED
    elif write_scope == _WRITE_SCOPE_FULL:
        full_result = full_device_region(mem_size, protocol)
        if isinstance(full_result, str):
            write_region = _DEFAULT_REGION
            region_policy = REGION_POLICY_FIXED
            region_reason = full_result
        else:
            write_region = full_result
            region_policy = REGION_POLICY_FULL_DEVICE
            if protocol == _PROTOCOL_FLASH4:
                # D-D: the excluded region is named in the report even on
                # a SUCCESSFUL carve-out, not only on refusal -- a stated,
                # visible reason rather than a silent narrowing.
                region_reason = (
                    f"full-device write excludes the first and last "
                    f"{_FLASH4_BOOT_BLOCK_LENGTH} bytes (flash4/protocol "
                    "0x05 boot blocks, W29C040 datasheet section 6.6 -- "
                    "permanently locked, no unlock command exists)"
                )
    else:
        write_region = _top_anchored_or_default(full)
        region_policy = REGION_POLICY_FIXED

    # The SDP leg's own region: computed by EXACTLY today's formula
    # (`_DEFAULT_REGION` at full, `_top_anchored_or_default(full)` at
    # partial/none) regardless of the policy decision above. D-D keeps the
    # leg small deliberately: it proves the lock mechanism, not coverage,
    # and AT28C256's plan alone carries six region-sized write-shaped SDP
    # ops that would otherwise become six full-device transfers per run.
    # The leg's live path is always the full scope (SDP-ALLOW chips are all
    # non-UV), so `leg_region` is `(0, 256)` on every reachable run
    # and the leg's wire behaviour is unchanged by this task.
    leg_region = (
        _DEFAULT_REGION
        if write_scope == _WRITE_SCOPE_FULL
        else _top_anchored_or_default(full)
    )

    steps: list[Step] = []
    locked_destructive: list[tuple[str, str]] = []

    # id-check: ALWAYS first. Supported only when the chip
    # carries a real (nonzero) chip-id to compare against -- the sentinel
    # value 0 means "no chip-id in DB entry" (Open Question 2 -> NA).
    if chip_id:
        steps.append(Step(op=OP_ID, supported=True, reason=""))
    else:
        steps.append(Step(op=OP_ID, supported=False, reason="no chip-id in DB entry"))

    # read / verify: always supported -- every protocol reads.
    steps.append(Step(op=OP_READ, supported=True, reason=""))

    # erase_is_executable: the SINGLE boolean deciding
    # whether the supported OP_ERASE Step below actually gets appended --
    # consumed a second time, read-only, by the blank-check placement logic
    # immediately below, so the two decisions can never drift apart. Mirrors
    # the erase arm's own supported condition (can_erase and protocol !=
    # _PROTOCOL_FLASH4) narrowed by write_execute, since an erase step that is
    # merely advisory (locked_destructive, write_scope="none") never actually
    # runs -- there is nothing for blank-check to sit behind.
    erase_is_executable = can_erase and protocol != _PROTOCOL_FLASH4 and write_execute

    # A blank-check verdict is only meaningful once SOMETHING in this plan can
    # actually leave the device blank. Built here and appended at ONE of two
    # positions, never both, so the SDP leg stays a contiguous terminal block:
    #
    #   1. SRAM/FRAM -- NA. Volatile/byte-rewritable memory has no factory-blank
    #      state at all.
    #   2. erase is executable -- supported, but appended AFTER the erase step:
    #      only once erase has run does "not blank" become a tool-health finding
    #      rather than a report of the chip's prior state.
    #   3. write executes on an auto-erase-on-write protocol -- NA here: no step
    #      can ever leave the device blank, so a supported blank-check would
    #      report chip state, not tool health.
    #   4. Everything else, including UV-EPROM -- supported, at this position. UV
    #      keeps it here deliberately: the write is irrecoverable and only UV
    #      light erases, so "not blank" is a real pre-write finding.
    if etype in _SRAM_FRAM_ETYPES or protocol in _SRAM_PROTO_IDS:
        blank_check_step = Step(
            op=OP_BLANK_CHECK,
            supported=False,
            reason=(
                f"blank-check not applicable to {etype or 'unknown'} "
                "(volatile/byte-rewritable, no factory-blank state)"
            ),
        )
    elif write_execute and protocol in _AUTO_ERASE_ON_WRITE_PROTOCOLS:
        family = (
            "0x0D (28C family)" if protocol == _PROTOCOL_EEPROM_28C else "0x05 (flash4)"
        )
        blank_check_step = Step(
            op=OP_BLANK_CHECK,
            supported=False,
            reason=(
                f"protocol {family} auto-erases per page during write; no "
                "step in this plan can ever leave the device blank"
            ),
        )
    else:
        blank_check_step = Step(op=OP_BLANK_CHECK, supported=True, reason="")

    if not erase_is_executable:
        # Cases 1/3/4 above: no erase step will run, so blank-check keeps
        # its historic position (right after read, before write).
        steps.append(blank_check_step)

    # write: always supported, always flagged destructive. When
    # write_scope="none" the step is OMITTED from the executable `steps`
    # list -- structurally absent, not skipped at exec time
    # -- and recorded on the advisory `locked_destructive` list instead.
    # write_scope="partial" emits `OP_WRITE_PARTIAL` instead of `OP_WRITE`
    # so the partial-vs-full distinction is visible
    # in the op string itself, everywhere `StepResult.op` is read.
    if write_execute:
        write_op = OP_WRITE_PARTIAL if write_scope == _WRITE_SCOPE_PARTIAL else OP_WRITE
        steps.append(
            Step(
                op=write_op,
                supported=True,
                reason=region_reason,
                destructive=True,
                write_region=write_region,
                region_policy=region_policy,
                full_device_permitted=full_device_permitted,
                cycle_payload=cycle_payload,
            )
        )
    else:
        locked_destructive.append((OP_WRITE, 'write_scope="none": write omitted'))

    # verify: always supported, but only executable on a write-executing
    # plan -- it follows the same write/erase gating (there is no
    # preceding write on a non-executing run, so a bare verify would compare
    # a freshly-generated pattern against unrelated chip contents).
    # Positioned after write and before erase so the destructive step order
    # (write, verify, erase) is UNCHANGED by this task -- but a blank-check
    # may now follow the erase step (see erase_is_executable above): once
    # something in the plan can leave the device blank, blank-check doubles
    # as that step's own oracle instead of reporting pre-existing chip
    # state. Its write_region equals the write step's -- a verify's region
    # is definitionally the preceding write's.
    if write_execute:
        steps.append(
            Step(
                op=OP_VERIFY,
                supported=True,
                reason="",
                write_region=write_region,
                region_policy=region_policy,
                full_device_permitted=full_device_permitted,
                cycle_payload=cycle_payload,
            )
        )
    else:
        locked_destructive.append((OP_VERIFY, 'write_scope="none": verify omitted'))

    # erase: supported only if FLAG_CAN_ERASE is set AND protocol != 0x05
    # (flash4 auto-erases per page; the flag is deliberately clear for it --
    # Pitfall 6). UV-EPROM never has the flag set (electrical-type is not in
    # {EEPROM, Flash/EEPROM}) so it is NA here for the same condition.
    # `erase_is_executable` (computed once, above, and reused verbatim here)
    # is exactly `can_erase and protocol != _PROTOCOL_FLASH4 and
    # write_execute` -- never re-derived, so this arm and the blank-check
    # placement decision can never drift apart.
    if can_erase and protocol != _PROTOCOL_FLASH4:
        if erase_is_executable:
            steps.append(Step(op=OP_ERASE, supported=True, reason="", destructive=True))
            # Case 2 (above): blank-check now follows the erase step it
            # doubles as an oracle for, and precedes the SDP leg block
            # appended below -- the leg stays a contiguous terminal block.
            steps.append(blank_check_step)
        else:
            locked_destructive.append((OP_ERASE, 'write_scope="none": erase omitted'))
    else:
        if protocol == _PROTOCOL_FLASH4:
            reason = "flash4 (0x05) auto-erases per page; no separate erase op"
        elif etype == "UV-EPROM":
            reason = "UV-EPROM has no electrical erase (UV light only)"
        elif protocol == _PROTOCOL_EEPROM_28C:
            # DEFENSIVE FALLTHROUGH -- see
            # `_PROTOCOL_EEPROM_28C`'s own comment above for the full
            # disposition). This arm is unreachable from the shipped
            # database now that FLAG_CAN_ERASE is restored on all 84
            # algorithm-13 rows; it fires only for a `0x0D` row whose
            # `electrical-type` falls outside {"EEPROM", "Flash/EEPROM"} --
            # a user-override shape. Delete-versus-keep was considered and
            # keep won: routing that row into the generic flag-keyed
            # fallback below would name the internal FLAG_CAN_ERASE
            # mechanism, which DEVTEST-01 forbids. The reason must instead
            # state only what is true of a row that actually reaches here --
            # its recorded electrical type does not describe an
            # electrically-erasable part, so no erase step is planned for
            # it -- expressed as a family fact, never the flag name.
            reason = (
                "electrical-type for this 0x0D (28C family) chip is not "
                "electrically erasable; no erase step is planned for it"
            )
        else:
            reason = "FLAG_CAN_ERASE not set for this chip"
        # NA erase is never a supported executable step regardless of the
        # write_scope -- there is nothing to lock/omit here (it was never
        # runnable), so it is NOT added to locked_destructive either.
        steps.append(
            Step(op=OP_ERASE, supported=False, reason=reason, destructive=True)
        )

    # SDP leg emission.
    # Appended as a CONTIGUOUS block at the END of the step list, after the
    # erase arm -- no shipped step's index moves (the existing
    # `d_ops.index(OP_VERIFY) < d_ops.index(OP_ERASE)`-shaped comparisons
    # stay true). Derived from `sdp_capability(name, db)` -- the injected
    # decision source -- never a re-implemented protocol/pinout
    # heuristic; `sdp_capability` is itself fail-closed and count-pinned at
    # 43 ALLOW / 41 REFUSE / 84 total. No new CLI option is introduced by
    # this: `derive_plan`'s signature gains no parameter, so `dev test`
    # keeps zero options.
    sdp_allowed, sdp_reason = sdp_capability(name, db)
    if write_execute:
        if sdp_allowed:
            # ALLOW chip, a real `dev test` run: six real, executable steps,
            # using `leg_region` -- computed by the SAME formula the shipped
            # write arm used before this task (never the new full-device/
            # uv-slot policy; D-D keeps the leg small deliberately). ALLOW
            # chips are all non-UV, so `leg_region` is always
            # `_DEFAULT_REGION` on every reachable run. Policy is always
            # `fixed` here -- the leg is never widened to the full device.
            for sdp_op in _SDP_LEG_STEP_ORDER:
                steps.append(
                    Step(
                        op=sdp_op,
                        supported=True,
                        reason="",
                        destructive=True,
                        write_region=leg_region,
                        region_policy=REGION_POLICY_FIXED,
                    )
                )
        else:
            # REFUSE chip, a real `dev test` run: six NA steps carrying
            # sdp_capability()'s OWN refusal prose verbatim.
            # `run_plan:877-879`'s existing NA path turns each into a
            # `_skip_result(..., verdict=VERDICT_NA)` with NO operator
            # call -- zero new machinery needed.
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
        # `locked_destructive` list instead of `steps` (mirroring the
        # shipped write/verify/erase treatment above) -- these entries DO
        # count toward count_applicable's M, so N < M and the banner fires,
        # matching its polarity.
        for sdp_op in _SDP_LEG_STEP_ORDER:
            locked_destructive.append((sdp_op, _SDP_LOCKED_REASON.format(op=sdp_op)))
    # else: a REFUSE chip at write_scope="none" emits NOTHING -- neither a step
    # nor a locked_destructive entry. An unsupported step must never be fabricated
    # as a runnable or locked one, and locked_destructive is an advisory list of
    # steps a destructive run WOULD run, so it is the wrong home for them.
    #
    # write_scope="none" is unreachable from `dev test`, so on every reachable run
    # REFUSE chips do receive the six NA steps from the branch above. This branch
    # is library and test surface only.

    return Plan(
        name=name,
        steps=steps,
        reason="",
        locked_destructive=locked_destructive,
        is_uv=is_uv,
    )


def _top_anchored_or_default(full: dict) -> tuple[int, int]:
    """Top-anchored high-address window, or the engine default region.

    Always computes `(mem_size - _UV_WRITE_REGION_LENGTH,
    _UV_WRITE_REGION_LENGTH)` from `full["memory-size"]` when it is large
    enough to fit the window, with a defensive fallback to the engine
    default `(_WRITE_REGION_START, _WRITE_REGION_LENGTH)` when `memory-size`
    is missing or too small (a fallback that would otherwise produce a
    negative start). The WIDTH always comes from the `_UV_WRITE_REGION_LENGTH`
    module constant -- never from any DB field; `memory-size` only
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
# Non-fatal per-step executor -- guard-HONORING
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

# Verdict vocabulary. `MARGINAL` is destructive/verify-only -- never forced
# onto read-step disagreement.
VERDICT_OK = "OK"
VERDICT_BAD = "BAD"
VERDICT_NA = "NA"
VERDICT_SKIPPED = "SKIPPED"
VERDICT_MARGINAL = "marginal"

# Ops that mutate the chip. This is the ONLY live safety use of either frozenset
# here: it is the exact set the chip-ID destructive gate consults before
# admitting a step. A write-shaped op missing from it would write to a
# MISIDENTIFIED chip ungated -- a correctness bug, not a cosmetic omission. So
# OP_WRITE_PARTIAL is in: a partial write is still a write.
#
# OP_SDP_LOCK is in for the same reason -- a lock applied to a misidentified
# chip is exactly the harm this gate prevents.
#
# OP_SDP_UNLOCK is DELIBERATELY ABSENT: a gate closing AFTER the lock succeeded
# must never be able to skip the unlock and ship a locked part. That asymmetry
# is the point; widening this set must never disturb it.
#
# The four SDP-leg ops are in: each mutates the part, so the gate must cover
# them like any other write-shaped op.
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
# LIVE DISPATCH ALLOW-LIST, gated on by both `_dispatch_step` and
# `_dispatch_multi_run` -- the host mirror of the firmware's NULL-main refusal.
# Before it existed, `_dispatch_multi_run`'s run loop ended in a bare
# `else: # OP_ERASE`, so ANY op string reached `operator.erase_eprom()` and
# reported OK.
#
# Any op added to the vocabulary MUST be added to both frozensets in this
# block, or it fails closed by construction.
#
# OP_SDP_LOCK/OP_SDP_UNLOCK are DELIBERATELY EXCLUDED, and that exclusion is an
# asserted parity exemption rather than an omission: running a lock twice is a
# second mutation with no comparison value, and a marginal-on-disagreement
# policy is meaningless for an emission whose result cannot be read back.
_MULTI_RUN_OPS = frozenset({OP_WRITE, OP_WRITE_PARTIAL, OP_ERASE, OP_VERIFY})

# WHAT THE WRITE REPEAT ACTUALLY MEASURES -- read this before citing it as
# coverage.
#
# On the 27-series protocols the firmware's write loop skips a byte BEFORE any
# pulse when the target is 0xFF or already reads back correct. After a
# successful write #1 every byte qualifies, so write #2 emits ZERO programming
# pulses and is a pure read pass. `marginal` there is reachable only as
# "attempt 1 failed, attempt 2 recovered" -- it structurally CANNOT catch a
# path that works once and then degrades, because attempt 2 never tries.
# Protocol 0x0D and the flash family write unconditionally, so their second
# write is real.
#
# The AM27C020 write#1/write#2 divergence this policy is usually credited with
# predates that skip rewrite, so it was measured against a different loop -- do
# not cite it as evidence that today's repeat detects a degrading write path.
#
# Read the repeat as a RIG-HEALTH check (rail droop, marginal timing, socket
# contact), not as coverage of the programming algorithm, which is
# deterministic and cannot disagree with itself.

# LIVE DISPATCH ALLOW-LIST for the SDP arm: `_dispatch_sdp` refuses any op
# outside this set. A module constant rather than a DB field, because anything
# that widens a blast radius belongs in this module -- a DB-supplied op string
# could otherwise smuggle in an op nothing here vetted.
_SDP_OPS = frozenset({OP_SDP_LOCK, OP_SDP_UNLOCK})

# The SDP leg's own registry. A module constant, never a DB field, for the same
# reason as `_SDP_OPS` above.
_SDP_LEG_OPS = frozenset(
    {
        OP_WRITE_BASELINE_B,
        OP_WRITE_BASELINE_A,
        OP_WRITE_INHIBITED,
        OP_WRITE_RESTORED,
    }
)

# The baseline gate's inputs and outputs. `_SDP_BASELINE_OPS` is what
# `_baseline_closes_sdp_gate` is
# evaluated FROM -- the two baseline-direction steps whose own verdict
# decides whether a lock may be emitted. Disjoint from `_SDP_LEG_GATED_OPS`
# by construction: a baseline op decides the gate and always runs
# regardless of its own state (both directions must be attempted -- a
# failing `write-baseline-b` followed by a passing `write-baseline-a` must
# still leave the gate CLOSED, never reopened), while a gated op is what
# the gate, once closed, SKIPS.
_SDP_BASELINE_OPS = frozenset({OP_WRITE_BASELINE_B, OP_WRITE_BASELINE_A})

# `_SDP_LEG_GATED_OPS` -- the gate's outputs, closed by
# `_baseline_closes_sdp_gate`.
#
# OP_SDP_UNLOCK is a member here deliberately. It is absent from
# `_DESTRUCTIVE_OPS`, so without this the unlock step would RUN and report OK
# at a part that was never locked -- an emission claim read as a state claim,
# on a run whose premise did not hold.
#
# This is a DIFFERENT mechanism from the chip-ID destructive gate:
# `destructive_gate_closed` and `baseline_gate_closed` are separate flags,
# wired independently in `run_plan`.
_SDP_LEG_GATED_OPS = frozenset(
    {OP_SDP_LOCK, OP_WRITE_INHIBITED, OP_SDP_UNLOCK, OP_WRITE_RESTORED}
)

_DESTRUCTIVE_GATE_REASON = (
    "chip-ID mismatch — destructive steps gated (chip left pristine)"
)

# The SDP leg's own gate-closure reasons,
# consumed by plan 134-04's baseline gate. Both name the family FACT (the
# baseline write/read-back transition did not complete; the part is left as
# found) -- never a mechanism name, and never `_DESTRUCTIVE_GATE_REASON`'s
# chip-ID wording, which would mislead a reader into thinking chip-ID
# closed the gate when the write path did -- a rejected alternative.
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
    """Outcome of executing a single `Step`.

    `verdict` is one of OK/BAD/NA/SKIPPED/marginal. `error_code` carries the
    exact firmware `response.id` captured off `EpromOperationError.error_code`
    when the step raised; `None` otherwise. `fingerprint` is attached
    only for the write/verify step. `run_count` is
    the number of times the underlying operator method was actually invoked
    for this step (1 for single-run steps; N for multi-run destructive/verify
    steps). `divergence` carries the read-step byte-level divergence
    metric when the step's `runs` disagreed -- a metric only, never a
    verdict flip and never `marginal` (marginal is destructive/verify-only).
    """

    op: str
    verdict: str
    reason: str = ""
    error_code: int | None = None
    fingerprint: Fingerprint | None = None
    run_count: int = 0
    divergence: dict[str, Any] | None = None
    # Wall-clock seconds for the whole step, stamped by `_run_step`'s timing
    # wrapper (operator asked for timings captured/presented/filed,
    # 2026-08-21). `None` for a step that never ran (NA/SKIPPED) -- a `0.0`
    # there would read as "ran, took no time" rather than "did not run".
    # Deliberately NOT part of `dedup_fingerprint`, which excludes every
    # volatile field so two runs of the same chip still dedup.
    duration_s: float | None = None
    # The write step's resolved `WriteTarget`
    # -- additive, `None` on every step that isn't a write, and `None` on a
    # write step that was SKIPPED as saturated/refused (in which case
    # `reason` names why). The verify step INHERITS this value from
    # `WriteContext` rather than re-resolving it (the seam moved to execution
    # time) -- it never appears on a verify step's OWN `StepResult` (verify
    # reads the context, it does not set this field on itself).
    write_target: WriteTarget | None = None


def _skip_result(op: str, reason: str, *, verdict: str = VERDICT_SKIPPED) -> StepResult:
    return StepResult(op=op, verdict=verdict, reason=reason, run_count=0)


# The ops whose `StepResult.run_count` is EXACTLY `run_plan`'s `runs` kwarg
# -- the multi-run destructive/verify set plus the
# read step, which `_dispatch_read` also loops `runs` times. Deliberately
# NOT every op: `_dispatch_id`, the blank-check arm and all six SDP-leg ops
# hard-set `run_count=1` BY DESIGN and would otherwise read as a degraded
# repeat policy on a perfectly normal run.
_REPEAT_POLICY_OPS = _MULTI_RUN_OPS | {OP_READ}

# The degraded-policy marker. Spelled as the kwarg
# value it describes rather than the CLI flag that produces it: `run_plan`
# owns the policy, `--fast` is merely one caller that asks for it.
REPEAT_POLICY_DEGRADED_TAG = "runs=1"


def repeat_policy_tag(results: list[StepResult]) -> str:
    """`""` for the default N>=2 repeat policy; the degraded marker otherwise.

    A single-run plan is strictly WEAKER: with one run there is nothing to
    compare, so no step can ever return `marginal` and no read divergence is
    computed. This tag keeps such a run out of the N>=2 promotion groups.

    Keyed on run_count == 1. A SKIPPED or NA step carries 0 and is ignored.

    Returning `""` for the default is load-bearing: `dedup_fingerprint` appends
    this tag only when non-empty, so an accurate run's fingerprint stays
    byte-identical to those already filed and no historical grouping resets.
    """
    for result in results:
        if result.op in _REPEAT_POLICY_OPS and result.run_count == 1:
            return REPEAT_POLICY_DEGRADED_TAG
    return ""


# The write-coverage discriminator (quick-devtest-coverage-dedup, follow-up
# to 260821-wna). Spelled as the `WriteTarget.region_policy` value it
# describes, not a bare "full-device" string repeated at call sites.
COVERAGE_TAG_FULL_DEVICE = "cov=full-device"


def coverage_tag(results: list[StepResult]) -> str:
    """`"cov=full-device"` when the run's write step resolved a full-device
    target; `""` on fixed/uv-slot, or when there is no write step.

    Why it exists: a UV part's write and a genuine full-device write both report
    `op="write"` while covering wildly different amounts of the device -- one
    256-byte slot versus the whole chip. The op string stopped tracking coverage
    once `region_policy` diverged from a 1:1 mapping with the op vocabulary.

    Locates the write step STRUCTURALLY, via `result.write_target is not None`
    -- set only on a write step's own result. This function must never compare
    `result.op` against an op-name constant.

    Returning `""` for fixed/uv-slot is load-bearing for the same reason as
    `repeat_policy_tag` above: only the strictly-stronger full-device shape gets
    tagged, so no historical grouping is re-keyed.
    """
    for result in results:
        if result.write_target is not None:
            if result.write_target.region_policy == REGION_POLICY_FULL_DEVICE:
                return COVERAGE_TAG_FULL_DEVICE
            return ""
    return ""


@dataclass
class WriteContext:
    """Execution-time state threaded through `run_plan`'s step loop.

    `derive_plan` decides the REGION and the POLICY; this carries the MASK
    decision, which can only be made at execution time because it reads the
    chip, from the write step to the verify step so verify never re-derives it.

    `chip_is_blank` is set once from the blank-check step's verdict and stays
    `None` before that step runs or when it is NA/SKIPPED. `None` is the safe
    default -- the full-device-if-blank branch is taken only on an explicit
    `True`.

    `target` is the write step's resolved target, or `None` when the write was
    refused; `refusal` then carries the reason so the verify step's SKIPPED
    result can name it rather than inventing one.
    """

    chip_is_blank: bool | None = None
    target: WriteTarget | None = None
    refusal: str = ""
    # The repeat CYCLE's per-cycle write targets and the index of the cycle
    # currently executing. Planned exactly once by
    # `_plan_cycle_targets`, before the first cycle, and thereafter READ-ONLY
    # to the dispatch layer -- the same derive-once/read-many discipline
    # `Step.region_policy` already follows. An EMPTY list is the proven no-op
    # signal: every caller that does not go through `_run_cycle_block` (the
    # SDP leg, direct `run_plan` callers in tests) leaves it empty and
    # `_dispatch_multi_run` falls back to resolving its own target exactly as
    # before.
    #
    # Load-bearing for UV parts even when every entry is identical: without
    # it, each cycle would re-probe and could land on a DIFFERENT slot once
    # cycle 1 has consumed bits from the first one -- and two cycles on two
    # different slots no longer isolate the write path from a cell defect,
    # which is the entire point of comparing them.
    cycle_targets: list[WriteTarget] = field(default_factory=list)
    cycle_index: int = 0


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


# The cleanup drain's per-callable narrow exception set. Named exactly
# the same three classes `_run_step`'s own
# degrading clause and EpromOperationError clause catch on the step path --
# declared once as a module constant so the op-registry parity
# reasoning has a single named fact to point at, rather than the tuple
# being re-typed inline at the drain site. Deliberately NOT also naming
# ProgrammerNotFoundError/FirmwareOutdatedError: both are SerialError
# subclasses already covered by the first tuple element, so listing them
# again would be redundant, not narrower -- and it is precisely this
# inclusion-by-subclass that makes a run-fatal condition surfacing during
# cleanup swallowed here (a deliberate difference from the step path,
# which RE-RAISES those two -- see run_plan's finally, below).
_UNLOCK_CLEANUP_SWALLOWED = (SerialError, HardwareOperationError, EpromOperationError)


# ---------------------------------------------------------------------------
# The repeat CYCLE: `write -> verify -> erase -> blank-check` runs as a UNIT, N
# times, instead of `write, write, verify, verify, erase, erase, ...`.
#
# A verify only proves the write worked if the write had to CHANGE something,
# and a second identical write onto the state the first produced does not.
# Cycling puts the erase BEFORE the next write, so from cycle 2 on every
# erasable family's write starts from a blank device and does real work -- and
# it makes room for a per-cycle payload on families that cannot be erased.
#
# Only cycle 1's write can start from an unknown state. Reordering the cycle to
# erase-first would fix that too, at the cost of every step-order assertion in
# the suite.

# Ops eligible to be INSIDE the cycle. Membership alone does not put a step in
# the block -- `cycle_block_bounds` requires them to be CONSECUTIVE and to
# start at a write step, which is what keeps a UV plan's pre-write blank-check
# (emitted BEFORE the write, deliberately, as a once-only operator-actionable
# finding) outside the cycle while an erasable plan's post-erase blank-check
# lands inside it.
_CYCLE_BLOCK_OPS = frozenset(
    {OP_WRITE, OP_WRITE_PARTIAL, OP_VERIFY, OP_ERASE, OP_BLANK_CHECK}
)

# The cycle can only OPEN on a write: a plan with no executable write step has
# nothing to cycle, and an erase/verify with no write in front of it is not a
# write-path test.
_CYCLE_BLOCK_START_OPS = frozenset({OP_WRITE, OP_WRITE_PARTIAL})


def cycle_block_bounds(steps: list[Step]) -> tuple[int, int] | None:
    """Half-open `(start, stop)` index range of the repeat cycle, or `None`.

    The block is the maximal run of CONSECUTIVE steps drawn from
    `_CYCLE_BLOCK_OPS` that begins at the first `_CYCLE_BLOCK_START_OPS`
    step. Measured against `derive_plan`'s actual emission order:

    * erasable  -- `id, read, [write, verify, erase, blank-check], sdp x6`
    * UV        -- `id, read, blank-check, [write, verify], sdp x6`
    * flash4    -- `id, read, blank-check(NA), [write, verify], sdp x6`
    * SRAM/FRAM -- `id, read, blank-check(NA), [write, verify], sdp x6`

    so the SDP leg is never swallowed (its six ops are outside the set) and a
    `write_scope="none"` plan -- which emits no write step at all -- returns
    `None` and takes the untouched per-step path.
    """
    start = next(
        (i for i, step in enumerate(steps) if step.op in _CYCLE_BLOCK_START_OPS),
        None,
    )
    if start is None:
        return None
    stop = start + 1
    while stop < len(steps) and steps[stop].op in _CYCLE_BLOCK_OPS:
        stop += 1
    return start, stop


def _cycle_target(write_context: WriteContext | None) -> WriteTarget | None:
    """The target planned for the cycle currently executing, or `None`.

    `None` means "no cycle plan applies" -- either there is no
    `write_context`, or its `cycle_targets` is empty (every non-cycle
    caller), or the index has run past the plan. Every one of those cases
    makes `_dispatch_multi_run` fall back to resolving its own target, which
    is exactly the pre-cycle behaviour.
    """
    if write_context is None or not write_context.cycle_targets:
        return None
    if not 0 <= write_context.cycle_index < len(write_context.cycle_targets):
        return None
    return write_context.cycle_targets[write_context.cycle_index]


def _aggregate_cycle_results(results: list[StepResult], op: str) -> StepResult:
    """Fold one step's per-cycle results into the SINGLE `StepResult` the
    report expects, so every consumer still sees exactly one row per op.

    Cycles whose verdicts differ fold to `marginal`, never to a confident
    OK/BAD.

    Field by field, each choice deliberate:
    * `verdict`  -- `marginal` on disagreement; otherwise the common verdict.
    * `run_count` -- how many cycles actually REACHED the operator (a
      SKIPPED cycle did not), so `run_count` keeps meaning "operator calls",
      which is the claim every disclosure surface makes about it.
    * `fingerprint`/`write_target` -- from the LAST cycle that produced one:
      the device's final state is the one a reader can still verify.
    * `duration_s` -- the SUM across cycles, so "steps total" stays honest.
    * `error_code`/`reason` -- the FIRST non-empty, so the earliest failure
      explains the row rather than being overwritten by a later cycle.
    """
    if not results:
        # Unreachable via `_run_cycle_block` (it appends either a pre-computed
        # skip or one result per cycle) -- kept so a future caller cannot turn
        # an empty list into an IndexError.
        return _skip_result(op, "no cycle produced a result")
    if len(results) == 1:
        return results[0]

    ran = [r for r in results if r.verdict in _RAN_VERDICTS]
    if not ran:
        first = results[0]
        first.run_count = 0
        return first

    verdicts = {r.verdict for r in ran}
    if len(verdicts) > 1:
        verdict = VERDICT_MARGINAL
        reason = f"{len(ran)} cycles disagreed on outcome"
    else:
        verdict = ran[0].verdict
        reason = next((r.reason for r in ran if r.reason), "")

    durations = [r.duration_s for r in results if r.duration_s is not None]
    return StepResult(
        op=op,
        verdict=verdict,
        reason=reason,
        error_code=next(
            (r.error_code for r in results if r.error_code is not None), None
        ),
        fingerprint=next((r.fingerprint for r in reversed(ran) if r.fingerprint), None),
        run_count=len(ran),
        divergence=next((r.divergence for r in reversed(ran) if r.divergence), None),
        duration_s=round(sum(durations), 3) if durations else None,
        write_target=next(
            (r.write_target for r in reversed(ran) if r.write_target is not None), None
        ),
    )


def _plan_cycle_targets(
    name: str,
    steps: list[Step],
    operator: Any,
    db: Any,
    *,
    cycles: int,
    write_context: WriteContext,
) -> list[WriteTarget]:
    """Resolve the N per-cycle write targets ONCE, before cycle 1 begins.

    Returns an EMPTY list on any refusal (chip unresolvable, saturated slot,
    no executable write step). Empty is not an error path that needs its own
    reason string: `_dispatch_multi_run` then falls back to resolving its own
    target and produces the SAME refusal, with the same wording, through the
    same `WriteTarget` guard -- so there is exactly one place a refusal is
    phrased, and this function never has to duplicate it.
    """
    write_step = next(
        (s for s in steps if s.op in _CYCLE_BLOCK_START_OPS and s.supported), None
    )
    if write_step is None:
        return []
    eprom_data, _stub, _reason = _resolve_or_none(name, db)
    if eprom_data is None:
        return []
    target, _refusal = _resolve_write_target(
        name,
        write_step,
        eprom_data,
        operator,
        chip_is_blank=write_context.chip_is_blank,
        cycles=cycles,
    )
    if target is None:
        return []

    if write_step.cycle_payload == CYCLE_PAYLOAD_ALTERNATE:
        return _alternating_cycle_targets(target, cycles)
    if write_step.cycle_payload == CYCLE_PAYLOAD_UV_TRANCHE and target.masked:
        staged = _uv_cycle_targets(target, cycles)
        if staged:
            return staged
        # Defensive only: `_resolve_write_target` was given the same `cycles`
        # and applied the scaled clearable floor, so any slot it returned is
        # tranche-feasible. Falling back to the single masked image keeps a
        # future threshold change from becoming a crash -- at the cost of
        # cycle 2 writing bytes the chip already holds, which is exactly the
        # behaviour this task exists to remove.
        return [target] * cycles
    # CYCLE_PAYLOAD_SAME: identical bytes every cycle, correct here because
    # something else resets the state between cycles -- the erase step inside
    # the cycle, or a protocol whose every page write auto-erases internally.
    return [target] * cycles


def _alternating_cycle_targets(target: WriteTarget, cycles: int) -> list[WriteTarget]:
    """The `alternate` recipe: pattern, complement, pattern, ...

    For SRAM/FRAM, which are freely rewritable in BOTH bit directions, so a
    differing payload costs nothing and the complement additionally exercises
    every data line in the other direction. Bit-inverting an address-derived
    pattern yields another roughly half-set image, so it can never trip
    `WriteTarget`'s degenerate all-`0x00`/all-`0xFF` refusal.
    """
    complement = WriteTarget(
        region=target.region,
        pattern=bytes(0xFF ^ b for b in target.pattern),
        masked=False,
        bits_cleared=0,
        bits_retained=0,
        current_source="address-derived pattern, bit-inverted (cycle complement)",
        # Carried through from the target being complemented, not
        # re-derived -- the complement describes the SAME region under
        # the SAME owning `Step.region_policy` (this recipe is SRAM/FRAM
        # only, never UV, so this is always `fixed` or `full-device`).
        region_policy=target.region_policy,
    )
    return [target if cycle % 2 == 0 else complement for cycle in range(cycles)]


def _uv_cycle_targets(target: WriteTarget, cycles: int) -> list[WriteTarget]:
    """The `uv-tranche` recipe: N cumulative images out of ONE slot.

    Empty list when the slot cannot be staged -- see `_plan_cycle_targets`'s
    defensive fallback for why that is unreachable through the normal path.

    `bits_cleared` on each returned target is the PER-CYCLE tranche size, not
    the slot total: it is the number that has to clear `_UV_MIN_CLEARED_BITS`
    for *this cycle* to have done real work, and it is what
    `WriteTarget.__post_init__`'s vacuous-pass floor then checks.
    `bits_retained` is the popcount AFTER this cycle, decreasing as the stages
    progress and bottoming out at the slot's own retained count.
    """
    if not target.current:
        return []
    start, length = target.region
    desired = generate_pattern(start, length)
    images = uv_tranche_images(target.current, desired, cycles)
    if not images:
        return []
    tranche_bits = uv_tranche_bit_counts(
        bits_cleared_by(target.current, desired), cycles
    )
    staged: list[WriteTarget] = []
    for cycle, (image, cleared) in enumerate(zip(images, tranche_bits), start=1):
        try:
            staged.append(
                WriteTarget(
                    region=target.region,
                    pattern=image,
                    masked=True,
                    bits_cleared=cleared,
                    bits_retained=sum(byte.bit_count() for byte in image),
                    current_source=f"{target.current_source} (tranche {cycle}/{cycles})",
                    current=target.current,
                    # Carried through from the probe target, NOT dropped: the
                    # staged copies describe the SAME slot, and these are the
                    # only targets that ever reach the report -- leaving them
                    # None made the rig-life line invisible on exactly the
                    # family it exists for (caught by running it, not by the
                    # suite).
                    slots_remaining=target.slots_remaining,
                    slots_total=target.slots_total,
                    # Carried through, same reasoning as `slots_remaining`/
                    # `slots_total` immediately above: every staged tranche
                    # describes the SAME slot the probe already resolved,
                    # under the SAME owning `Step.region_policy` (always
                    # `uv-slot` on this path).
                    region_policy=target.region_policy,
                )
            )
        except ValueError:
            return []
    return staged


def _run_cycle_block(
    name: str,
    steps: list[Step],
    operator: Any,
    db: Any,
    *,
    cycles: int,
    sampler: Any,
    write_context: WriteContext,
    gate_closed: bool,
) -> list[StepResult]:
    """Run the write-shaped block as a UNIT, `cycles` times, and fold the
    per-cycle results into one `StepResult` per step.

    Returns exactly `len(steps)` results, in the SAME order as `steps`, so
    `run_plan` can splice them in where the block sat and every downstream
    consumer keeps seeing one row per plan step.

    The two gates are applied ONCE, before any cycle: an unsupported step is
    NA and a destructive step behind a closed chip-ID gate is SKIPPED, in both
    cases with the same wording `run_plan`'s own per-step path uses. A gated
    step is simply not part of the cycle -- it is never retried per cycle,
    which would multiply one skip reason into N identical rows.

    `collect_fingerprint` is True only on the FINAL cycle. Without that the
    write and verify steps would each add a region read-back per cycle,
    turning the fingerprint's one extra read into N -- real cost on a
    full-device region, for a fingerprint that only ever describes the
    device's final state anyway.
    """
    pre: list[StepResult | None] = []
    for step in steps:
        if not step.supported:
            pre.append(_skip_result(step.op, step.reason, verdict=VERDICT_NA))
        elif gate_closed and step.op in _DESTRUCTIVE_OPS:
            pre.append(_skip_result(step.op, _DESTRUCTIVE_GATE_REASON))
        else:
            pre.append(None)

    per_step: list[list[StepResult]] = [[] if r is None else [r] for r in pre]
    live = [i for i, r in enumerate(pre) if r is None]
    # Retry guard, preserved across the move to a cycle loop: a
    # firmware ERROR response (a non-None `error_code` -- the VPP-out-of-range
    # guard refusal 0xA9 is the case that motivated it) is a FINDING, not
    # something to retry. Before the cycle loop the raised exception aborted
    # `_dispatch_multi_run`'s runs-loop, so runs 2..N never reached the
    # hardware; a naive cycle loop would re-energize a rail the firmware just
    # refused. The current cycle still finishes -- mirroring the old
    # behaviour, where `run_plan` moved on to the next STEP after the raise --
    # and no further cycle starts.
    #
    # Deliberately keyed on `error_code`, NOT on a non-OK verdict: a plain
    # `False` return from `write_eprom` (no exception, no error code) is the
    # AM27C020 shape, where cycle 2 recovering from cycle 1's failure is real
    # information and must still be collected.
    hardware_refused = False

    write_context.cycle_targets = _plan_cycle_targets(
        name, steps, operator, db, cycles=cycles, write_context=write_context
    )
    try:
        for cycle in range(cycles):
            write_context.cycle_index = cycle
            final = cycle == cycles - 1
            for i in live:
                step = steps[i]
                result = _run_step(
                    name,
                    step,
                    operator,
                    db,
                    runs=1,
                    sampler=sampler,
                    write_context=write_context,
                    collect_fingerprint=final,
                )
                per_step[i].append(result)
                # The same two context assignments `run_plan`'s per-step path
                # makes, replicated here because these steps no longer pass
                # through it. Both must happen INSIDE the cycle: the verify
                # inherits the write target of the cycle it belongs to, not
                # of some other cycle.
                if step.op == OP_BLANK_CHECK and result.verdict in (
                    VERDICT_OK,
                    VERDICT_BAD,
                ):
                    write_context.chip_is_blank = result.verdict == VERDICT_OK
                if step.op in (OP_WRITE, OP_WRITE_PARTIAL):
                    write_context.target = result.write_target
                    write_context.refusal = (
                        result.reason if result.write_target is None else ""
                    )
                if result.error_code is not None:
                    hardware_refused = True
            if hardware_refused:
                break
    finally:
        # Cleared unconditionally: the SDP leg runs AFTER this block and must
        # never pick up a stale cycle target, on the exception path too.
        write_context.cycle_targets = []
        write_context.cycle_index = 0

    return [
        _aggregate_cycle_results(per_step[i], steps[i].op) for i in range(len(steps))
    ]


def run_plan(
    plan: Plan,
    operator: Any,
    db: Any,
    *,
    runs: int = 2,
    allow_single_run: bool = False,
    sampler: Any = None,
) -> list[StepResult]:
    """Execute `plan.steps` as independent, non-fatal steps.

    Each supported step re-resolves through `resolve_chip` -- the
    guard-HONOURING path -- and dispatches to the matching `EpromOperator`
    method. NA steps are recorded without any operator call.

    The id-check runs FIRST. A chip-ID mismatch closes a `destructive_gate`
    that every write/erase step consults BEFORE calling its operator, marking
    itself SKIPPED and leaving the chip pristine. Non-destructive findings are
    still recorded regardless of the gate.

    One step's BAD verdict or raised exception NEVER aborts the rest -- each
    body has its own try/except. An `EpromOperationError` becomes BAD carrying
    its error_code; a `resolve_chip` refusal becomes SKIPPED/NA with a reason.

    Destructive and verify steps run `runs` times (default 2). When per-run
    outcomes DISAGREE the verdict is `marginal`, never coerced to a confident
    OK or BAD. `runs < 2` is rejected before any operator call unless the
    caller passes `allow_single_run=True` -- a deliberately weaker mode that
    forfeits the marginal detector and the read-divergence metric, tagged by
    `repeat_policy_tag` so it cannot join an accurate run's dedup group.
    Read-step disagreement is reported as a byte-level divergence metric only,
    never as a verdict flip.

    `sampler` is an optional opaque callable; this engine never imports
    hardware.py. It is invoked around EACH write call only -- never around
    read/verify/erase/id/blank-check, and never around the step loop -- so a
    write-pulse droop can be told apart from a read droop. A raised sampler
    exception is swallowed: it is a best-effort diagnostic, not part of the
    write contract.

    A cleanup registry is drained in a bare try/finally around the whole loop:
    a successful SDP lock registers its matching unlock, and the drain runs it
    however the loop exits, including on KeyboardInterrupt/SystemExit, both of
    which still propagate afterwards. On the propagating path the report is
    honestly forfeited -- the caller's assignment never completes.
    """
    # Fail-closed: `runs < 2` fails the WHOLE plan unless the caller explicitly
    # opted in, so an accidentally mis-wired runs=1 cannot silently cost the
    # marginal detector. `runs < 1` fails regardless.
    if runs < 1 or (runs < 2 and not allow_single_run):
        return [
            StepResult(
                op="__plan__",
                verdict=VERDICT_BAD,
                reason=(
                    f"runs must be >= 2 (got {runs}); a destructive/verify "
                    "step requires at least 2 runs to compare -- "
                    "pass allow_single_run=True to run a deliberately "
                    "weaker single-run plan"
                ),
                run_count=0,
            )
        ]

    results: list[StepResult] = []
    destructive_gate_closed = False
    # The SDP baseline gate --
    # a SEPARATE flag from `destructive_gate_closed` above, deliberately:
    # the two gates are structurally different mechanisms (chip-ID mismatch
    # vs. a baseline write/read-back transition that did not complete), and
    # the SDP-leg's own gate-closure reasons (`_SDP_BASELINE_GATE_REASON`/
    # `_SDP_UNLOCK_GATE_REASON`) must never be confused with
    # `_DESTRUCTIVE_GATE_REASON`'s chip-ID wording. STICKY by construction
    # (only ever set True, never reset False) so a failing `write-baseline-b`
    # followed by a passing `write-baseline-a` cannot reopen it.
    baseline_gate_closed = False
    # Cleanup registry: a plain `list` of
    # zero-argument callables, deliberately GENERIC rather than a hardcoded
    # lock-to-unlock window with the unlock written inline. The inline form
    # is literally what research P-20 prevention #2 describes and is
    # simpler -- but the four-step SDP leg, and any later
    # cleanup-needing op, would each have to re-open `run_plan` to widen
    # the special case, and a special case widened three times is how this
    # loop's flat shape rotted in the first place. Drained in
    # REGISTRATION order below -- deliberately NOT `contextlib.ExitStack`:
    # measured, not assumed, `ExitStack.close()` drains LIFO (reversing
    # registration order) and a raising callback makes `close()` re-raise,
    # which inside a `finally` REPLACES the in-flight exception and demotes
    # the original to `__context__` -- precisely the masking this exists to
    # prevent.
    cleanup: list[Callable[[], None]] = []

    # De-registration handle (see the SDP-unlock discussion in
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

    # WriteContext: ONE instance for the
    # whole run, created here and passed by reference to every `_run_step`
    # call below. `derive_plan` still decides the REGION and POLICY; this
    # object carries the MASK decision (necessarily execution-time) from
    # the blank-check step -> the write step -> the verify step, so the
    # verify step never re-derives either. This is the seam MOVED to
    # execution time.
    write_context = WriteContext()

    # `runs < 2` stays OUTSIDE this `try` (above): nothing is registered
    # yet, so there is nothing to drain. `results`, `destructive_gate_closed`
    # and `cleanup` are all created BEFORE the `try`. `return results` stays
    # INSIDE the `try`, textually unchanged.
    # The repeat cycle's index range, computed ONCE from the plan. The
    # loop below walks by index so the whole block can be handed to
    # `_run_cycle_block` as a unit and skipped over; every step OUTSIDE the
    # block keeps the untouched per-step path, including the SDP leg and the
    # read step (whose own N-run divergence metric is a read-repeatability
    # check, not part of the write cycle).
    cycle_block = cycle_block_bounds(plan.steps)

    try:
        index = 0
        while index < len(plan.steps):
            if cycle_block is not None and index == cycle_block[0]:
                results.extend(
                    _run_cycle_block(
                        plan.name,
                        plan.steps[cycle_block[0] : cycle_block[1]],
                        operator,
                        db,
                        cycles=runs,
                        sampler=sampler,
                        write_context=write_context,
                        gate_closed=destructive_gate_closed,
                    )
                )
                index = cycle_block[1]
                continue

            step = plan.steps[index]
            index += 1

            if not step.supported:
                results.append(_skip_result(step.op, step.reason, verdict=VERDICT_NA))
                continue

            if step.op in _DESTRUCTIVE_OPS and destructive_gate_closed:
                results.append(_skip_result(step.op, _DESTRUCTIVE_GATE_REASON))
                continue

            # The SDP baseline gate. Ordered
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
                plan.name,
                step,
                operator,
                db,
                runs=runs,
                sampler=sampler,
                write_context=write_context,
            )
            results.append(result)

            if step.op == OP_BLANK_CHECK and result.verdict in (
                VERDICT_OK,
                VERDICT_BAD,
            ):
                # D-C: the ONE place `chip_is_blank` is set, from the
                # blank-check step's OWN verdict. Left `None` (the safe
                # default) when blank-check is NA/SKIPPED for this chip.
                write_context.chip_is_blank = result.verdict == VERDICT_OK

            if step.op in (OP_WRITE, OP_WRITE_PARTIAL):
                # The verify step inherits the write's ACTUAL resolved
                # target (or refusal) -- never re-derived (moved to
                # execution time).
                write_context.target = result.write_target
                write_context.refusal = (
                    result.reason if result.write_target is None else ""
                )

            if step.op == OP_SDP_LOCK and result.verdict == VERDICT_OK:
                # Register the unlock ONLY on a successful lock:
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
                # comment above.
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
                # emits exactly one `sdp_unlock` call, not two (
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
                # never reopened.
                baseline_gate_closed = (
                    baseline_gate_closed or _baseline_closes_sdp_gate(result)
                )

        return results
    finally:
        # Bare `finally`, NO `except` of any width: this is the one construct that
        # reaches KeyboardInterrupt/SystemExit while still letting them propagate
        # unchanged. An `except BaseException:` here would swallow Ctrl-C.
        #
        # The drain NEVER appends into `results` and never references it: the list is
        # returned by reference and feeds seven consumers in cli_handlers.py, one of
        # which would then render "8 of 7 ran".
        #
        # Each callable gets its OWN narrow try/except and the drain CONTINUES past a
        # caught failure rather than stranding later entries. Never `raise` from this
        # finally -- an exception raised there REPLACES the in-flight one, masking the
        # original fault or the user's Ctrl-C.
        #
        # Deliberate difference from the step path: ProgrammerNotFoundError and
        # FirmwareOutdatedError are SerialError subclasses, so they ARE swallowed here,
        # whereas `_run_step` re-raises them.
        #
        # A failed unlock is not user-visible here -- this module has no logger and the
        # drain must not touch `results`. It surfaces via the HELD/NOT-RUN report field.
        for cleanup_call in cleanup:
            try:
                cleanup_call()
            except _UNLOCK_CLEANUP_SWALLOWED:
                continue


def _id_step_closes_gate(result: StepResult) -> bool:
    """Close the destructive gate on an id-check failure/mismatch.

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
    """Close the SDP baseline gate on ANY non-OK baseline verdict.

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


# Three-valued hold-state REPORT VALUES. These are report values, NOT op
# strings -- they
# carry no `OP_` prefix and must never join `_ALL_OPS`/`_MULTIWORD_OP_VALUES`
# in tests/test_op_registration_parity.py; a later reader must not
# "helpfully" register them there.
SDP_HOLD_HELD = "HELD"
SDP_HOLD_NOT_HELD = "NOT-HELD"
SDP_HOLD_NOT_RUN = "NOT-RUN"


def sdp_oracle_applicable(plan: Plan) -> bool:
    """`True` iff `plan` carries a RUNNABLE `write-inhibited` entry.

    Derived STRUCTURALLY from the `plan` object the caller already holds --
    never a second call to `sdp_capability`, which would be a second source
    of truth that could drift from `derive_plan`'s own decision (the same
    single-source-of-truth discipline applied to `count_applicable`).

    `True` when `plan.steps` carries an `OP_WRITE_INHIBITED` `Step` with
    `supported=True` (a real `dev test` run, ALLOW chip), OR when
    `plan.locked_destructive` carries an `OP_WRITE_INHIBITED` `(op, reason)`
    pair (the `write_scope="none"` ALLOW-chip shape). `False` for a
    REFUSE chip: its `OP_WRITE_INHIBITED` step IS present in `plan.steps`
    (the NA path), but with `supported=False` -- the oracle never runs
    for a REFUSE chip, so that presence must not count as "applicable".
    """
    for step in plan.steps:
        if step.op == OP_WRITE_INHIBITED and step.supported:
            return True
    return any(op == OP_WRITE_INHIBITED for op, _reason in plan.locked_destructive)


def sdp_hold_state(plan: Plan, results: list[StepResult]) -> str:
    """Pure HELD / NOT-HELD / NOT-RUN derivation from the `write-inhibited`
    step, if any:

    - OK  -> HELD     (the inhibited write was refused; the part held its lock)
    - BAD -> NOT_HELD (the inhibited write was accepted; the lock leaked)
    - NA / SKIPPED / marginal, or the step absent entirely -> the BARE NOT_RUN
      token, with no reason appended.

    Do NOT restore a `": {reason}"` suffix on NOT_RUN. This field was the last
    carrier of that prose after the step-scoped suppression, and stripping it
    was a deliberate instruction.

    Returns `str` ALWAYS, never True/False/None -- a JSON boolean here would
    read as ground truth for a state this family cannot report.
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

    return SDP_HOLD_NOT_RUN


def sdp_left_writable(results: list[StepResult]) -> bool:
    """True iff `results` itself demonstrates the part still accepts a write --
    the `write-restored` step present AND OK.

    False when the step is absent, or present with any other verdict: none of
    those demonstrate the part still writes, which is what the loud recovery
    form keys on.

    Lives here rather than in cli_handlers.py to keep op-string knowledge out
    of the handler.
    """
    for r in results:
        if r.op == OP_WRITE_RESTORED:
            return r.verdict == VERDICT_OK
    return False


# Region used for the write/verify address-derived pattern fingerprint
# A small fixed region keeps the bench-free
# engine's write/verify step cheap and matches the region-parameterized
# generator contract. This is the NON-UV default region --
# owns the UV-EPROM branch below via `_write_region_for`.
_WRITE_REGION_START = 0
_WRITE_REGION_LENGTH = 256

# UV-EPROM write-region WIDTH. This is an ENGINE MODULE
# CONSTANT, never sourced from any DB field -- a malicious/misconfigured DB
# entry must not be able to widen the write window. `memory-size` is only a
# top-anchor PLACEMENT bound (where the window sits), never a WIDTH input.
#
# AMENDED: this is now specifically the UV
# SLOT width -- the granularity `uv_slot_starts`/`WriteTarget` operate at --
# and it is the BOUND D-E requires on the reversal below: `full_device_region`
# derives a write WIDTH from `memory-size` for the non-UV full-device policy,
# which deliberately reverses the "width never comes from the DB" rule
# on that one path -- but only after a sanity check, and never for this slot
# width, which stays a module constant on every path.
_UV_WRITE_REGION_LENGTH = 256

# The engine default region as a concrete tuple -- consumed by
# `_top_anchored_or_default` (used by `derive_plan`'s region computation,
# defined earlier in this module; referenced here at call time only, after
# module import completes).
_DEFAULT_REGION = (_WRITE_REGION_START, _WRITE_REGION_LENGTH)


# ---------------------------------------------------------------------------
# UV bit-masking, slot arithmetic, and the full-device region (quick task
# 260821-wna, D-A/D-B/D-D/D-E). Pure, bench-free compute over host-side byte
# arrays and DB-derived integers -- no chip access, no operator calls, no
# imports beyond the stdlib already present in this module. This is the
# execution-time HALF of the D-A/D-B mechanism; `derive_plan` (above) decides
# only the REGION and the POLICY, never the mask.
# ---------------------------------------------------------------------------

# Both per-SLOT (not per-byte) thresholds, each 64 of the 2048 bits in a
# 256-byte slot (Claude's discretion). The verdict is per-slot, so a
# per-byte rule would reject slots that are serviceable in aggregate. A
# virgin slot offers 1024 clearable and 1024 retained bits (measured: the
# address-derived pattern's popcount over a 256-byte slot is exactly 1024,
# since `address_fold_byte` is an XOR-fold and averages to half its bits
# set). 64 accepts a slot with only ~6% of its virgin headroom left while
# staying far above anything a single-bit anomaly or a transport glitch
# could account for; the retained floor is what makes an all-0x00 read-back
# (or any near-degenerate image) structurally unable to satisfy `WriteTarget`.
_UV_MIN_CLEARED_BITS = 64
_UV_MIN_RETAINED_BITS = 64

# How many bytes one probe read covers when walking candidate UV slots
# top-down (16 slots per read at the 256-byte slot width) -- probe cost is
# proportional to blocks read, not slots evaluated.
_UV_PROBE_BLOCK_LENGTH = 4096

# A MIRROR of `eprom_operations._BOOT_BLOCK_SIZE` (16 KiB, W29C040 datasheet
# section 6.6's two irreversible boot blocks, first and last). Mirrored
# rather than imported: `chip_test.py` deliberately keeps no dependency on
# `eprom_operations.py` (the same reasoning `_diff_offsets`'s own comment,
# above, already records for the divergence primitive). `_PROTOCOL_FLASH4`
# (defined earlier in this module) is reused as the protocol id rather than
# adding a second constant for it.
_FLASH4_BOOT_BLOCK_LENGTH = 0x4000

# The sanity ceiling D-E demands before any DB-derived WIDTH (the
# full-device policy's region length) is honoured. The largest shipped
# device measured at plan time is 1 MiB across 8 rows; 16 MiB leaves room
# for a future part while refusing an absurd override value outright.
_MAX_FULL_DEVICE_LENGTH = 1 << 24


def mask_write_pattern(current: bytes, desired: bytes) -> bytes:
    """The D-A arithmetic: `P = C & D`, per byte.

    On a UV EPROM, programming only clears bits (1 -> 0); writing `desired`
    into a cell currently holding `current` physically yields `current &
    desired`. Raises `ValueError` on a length disagreement rather than
    silently truncating to the shorter array -- a silent truncation here is
    the empty-read-back trap (this project's absent-chip false-green
    history) in a new costume.
    """
    if len(current) != len(desired):
        raise ValueError(
            f"mask_write_pattern: length disagreement (current={len(current)} "
            f"bytes, desired={len(desired)} bytes) -- refusing rather than "
            "silently truncating"
        )
    return bytes(c & d for c, d in zip(current, desired))


def bits_cleared_by(current: bytes, desired: bytes) -> int:
    """Count bits set in `current` and clear in `desired`.

    This is how many bits the masked write `mask_write_pattern(current,
    desired)` will actually CLEAR relative to `current` -- the slot-
    saturation signal. A fully saturated slot (`current` all `0x00`) has no
    set bits to clear, so this returns 0 regardless of `desired`.
    """
    if len(current) != len(desired):
        raise ValueError(
            f"bits_cleared_by: length disagreement (current={len(current)} "
            f"bytes, desired={len(desired)} bytes)"
        )
    return sum((c & (~d & 0xFF)).bit_count() for c, d in zip(current, desired))


def uv_tranche_images(current: bytes, desired: bytes, cycles: int) -> list[bytes]:
    """Stage `current & desired` across `cycles` writes -- the UV recipe.

    Returns `cycles` CUMULATIVE images: image *n* is `current` with tranches
    0..*n* cleared, so image `cycles-1` is exactly `current & desired`.
    Empty list when the slot cannot support the staging (see the floor below);
    the caller then falls back to the single-write path.

    **This costs no extra bits.** The final image equals what a single masked
    write already produces, so the number of cells programmed is IDENTICAL --
    cycling only splits the same expenditure into stages. A UV part is a finite
    regression rig: bits spent are runs lost.

    The bits are INTERLEAVED, not blocked: tranche *n* takes every
    `cycles`-th clearable bit (`range(n, len, cycles)`) walking bytes in order
    and bits LSB-first within each byte. So every tranche spans the whole
    region and all eight bit positions rather than one corner of it, and each
    cycle's programming exercises the same address and data lines the others
    do.

    Monotonic by construction: a tranche is a subset of the bits that are
    currently `1` and are `0` in `desired`, so no cycle ever asks a UV cell for
    the impossible `0 -> 1` transition, and no arithmetic at write time can
    produce one.

    The floor is `cycles * _UV_MIN_CLEARED_BITS`, not the single-write
    `_UV_MIN_CLEARED_BITS`: staging N cycles out of a slot needs N tranches
    each big enough to be non-vacuous on its own. A slot with 64..127
    clearable bits passes today's single-write filter and CANNOT support a
    two-cycle test -- it is refused here so the caller moves to the next slot
    rather than running a cycle that clears too little to mean anything.
    """
    if cycles < 1 or len(current) != len(desired):
        return []
    clearable = [
        (index, bit)
        for index in range(len(current))
        for bit in range(8)
        if (current[index] >> bit) & 1 and not (desired[index] >> bit) & 1
    ]
    if len(clearable) < cycles * _UV_MIN_CLEARED_BITS:
        return []
    images: list[bytes] = []
    staged = bytearray(current)
    for cycle in range(cycles):
        for position in range(cycle, len(clearable), cycles):
            index, bit = clearable[position]
            staged[index] &= 0xFF ^ (1 << bit)
        images.append(bytes(staged))
    return images


def uv_tranche_bit_counts(clearable_total: int, cycles: int) -> list[int]:
    """How many bits each cycle's tranche clears, for the same interleave
    `uv_tranche_images` uses. Split out so `WriteTarget.bits_cleared` can be
    the PER-CYCLE count -- the number that makes the vacuous-pass floor mean
    "this cycle did real work" -- without re-walking the bit list.
    """
    if cycles < 1:
        return []
    return [len(range(cycle, clearable_total, cycles)) for cycle in range(cycles)]


def bits_retained_by(current: bytes, desired: bytes) -> int:
    """Count bits set in BOTH `current` and `desired`.

    Equals `popcount(mask_write_pattern(current, desired))` -- the number of
    `1` bits the masked write leaves behind, which is what makes a
    degenerate (near-all-`0x00`) read-back structurally distinguishable from
    a genuinely serviceable slot.
    """
    if len(current) != len(desired):
        raise ValueError(
            f"bits_retained_by: length disagreement (current={len(current)} "
            f"bytes, desired={len(desired)} bytes)"
        )
    return sum((c & d).bit_count() for c, d in zip(current, desired))


def uv_slot_starts(mem_size: int, slot_length: int) -> list[int]:
    """Top-down ordered candidate UV slot starts.

    Top-down is deliberate: it preserves the existing top-anchored
    convention (`_top_anchored_or_default`) and leaves the low address space
    -- where a used EPROM's real payload usually lives -- untouched longest.
    Empty when the device cannot hold even one slot.
    """
    if slot_length <= 0 or mem_size < slot_length:
        return []
    slot_count = mem_size // slot_length
    return [mem_size - (i + 1) * slot_length for i in range(slot_count)]


def full_device_region(mem_size: int, protocol: int) -> tuple[int, int] | str:
    """The non-UV full-device write region, or a refusal reason -- never both.

    `mem_size` is sanity-checked FIRST (positive, a multiple of the slot width,
    at or below the maximum) before the flash4 carve-out is considered, so a
    malformed `memory-size` never reaches the carve-out arithmetic.

    This is the ONE place in this module a write WIDTH comes from a DB field.
    Everywhere else the width is a module constant so a malicious or
    misconfigured entry cannot widen the window; the reversal applies to the
    non-UV full-device policy only, and only after that sanity check passes.

    On protocol 0x05 the first and last boot blocks are permanently locked
    (W29C040 datasheet 6.6) and are carved out. When they cover the entire
    device a full write is structurally impossible, so this returns a refusal
    naming them rather than an empty or negative region.
    """
    if not mem_size or mem_size <= 0:
        return "full-device write refused: memory-size is absent, zero, or negative"
    if mem_size % _UV_WRITE_REGION_LENGTH != 0:
        return (
            f"full-device write refused: memory-size {mem_size} is not a "
            f"multiple of {_UV_WRITE_REGION_LENGTH}"
        )
    if mem_size > _MAX_FULL_DEVICE_LENGTH:
        return (
            f"full-device write refused: memory-size {mem_size} exceeds the "
            f"sanity ceiling {_MAX_FULL_DEVICE_LENGTH} (D-E)"
        )
    if protocol == _PROTOCOL_FLASH4:
        carve = _FLASH4_BOOT_BLOCK_LENGTH
        if mem_size <= 2 * carve:
            return (
                f"full-device write refused: flash4 (protocol 0x05) boot "
                f"blocks ({carve} bytes each, first and last) cover the "
                f"entire {mem_size}-byte device -- falling back to the "
                "small fixed region"
            )
        return carve, mem_size - 2 * carve
    return 0, mem_size


@dataclass(frozen=True)
class WriteTarget:
    """The execution-time-resolved write target -- THE vacuous-pass guard.

    `__post_init__` structurally REFUSES to construct a degenerate target:
    every OK verdict downstream must be reachable only through an instance
    of this class, so a saturated slot or a dead read-back can never be
    reported as a write pass. This is the same failure family as this
    project's absent-chip false-green trap (a `Mock` that answers `True`
    with no chip attached) -- here it is a masked write that would trivially
    "pass" because there was nothing left to clear.

    `region` is `(start, length)`; `pattern` is the actual bytes to be
    written (masked or not); `masked` records whether `pattern` is a D-A
    masked image (UV) or a plain address-derived pattern (non-UV, or a blank
    UV chip under D-C); `bits_cleared`/`bits_retained` are the D-B counts
    (meaningless, and not checked, when `masked` is False); `current_source`
    names where the "current chip content" came from for a masked target
    (a probe read, or the blank-check for D-C) -- provenance for the report,

    """

    region: tuple[int, int]
    pattern: bytes
    masked: bool
    bits_cleared: int
    bits_retained: int
    current_source: str
    # The chip content the mask was taken against, kept ONLY so
    # `_plan_cycle_targets` can stage UV tranches out of it without re-probing
    # the device. Empty on every unmasked target. Deliberately absent
    # from `diagnostic_report._step_dict` -- it is working state, not
    # provenance, and a full-device image has no business in a filed issue.
    current: bytes = b""
    # Rig life: how many UV slots on this part can still support a run,
    # and how many it has in total. `None` on every non-UV target. Derived
    # from the chosen slot's INDEX in the top-down candidate list -- no extra
    # read -- and carried through onto the staged tranche copies, which are
    # the only targets that reach the report.
    slots_remaining: int | None = None
    slots_total: int | None = None
    # The region policy this target was resolved under (quick-devtest-
    # coverage-dedup, follow-up to 260821-wna): one of `REGION_POLICY_
    # FIXED` / `REGION_POLICY_FULL_DEVICE` / `REGION_POLICY_UV_SLOT`,
    # copied from the owning `Step.region_policy` at the point
    # `_resolve_write_target` resolves the target, and carried through
    # UNCHANGED by every site that derives a further `WriteTarget` from an
    # already-resolved one (`_alternating_cycle_targets`'s complement,
    # `_uv_cycle_targets`'s staged tranches) -- the same carry-through
    # discipline `current_source`/`slots_remaining`/`slots_total` already
    # follow, not a re-derivation. Additive: defaults to `REGION_POLICY_
    # FIXED`, the pre-existing engine-default policy, so every direct
    # `WriteTarget(...)` construction already in the test suite keeps
    # working unchanged. Read by `coverage_tag` (below) to tell a
    # full-device write step apart from a slot/fixed one -- see
    # `dedup_fingerprint`'s docstring (`diagnostic_report.py`) for why
    # that distinction is now load-bearing for report dedup: the op
    # string alone (`write` vs `write-partial`) stopped tracking coverage
    # once a UV part's `write_scope="full"` run and a non-UV part's
    # genuine full-device run could both report `op="write"` while
    # covering wildly different amounts of the device.
    region_policy: str = REGION_POLICY_FIXED

    def __post_init__(self) -> None:
        _start, length = self.region
        if len(self.pattern) != length:
            raise ValueError(
                f"WriteTarget: pattern length {len(self.pattern)} disagrees "
                f"with region length {length}"
            )
        if self.pattern == b"\x00" * length or self.pattern == b"\xff" * length:
            raise ValueError(
                "WriteTarget: refusing a degenerate all-0x00/all-0xFF "
                "pattern -- the vacuous-pass guard, the absent-chip "
                "false-green family wearing a new costume"
            )
        if self.masked and self.bits_cleared < _UV_MIN_CLEARED_BITS:
            raise ValueError(
                f"WriteTarget: masked target clears only {self.bits_cleared} "
                f"bits, below _UV_MIN_CLEARED_BITS ({_UV_MIN_CLEARED_BITS}) "
                "-- this slot is saturated under this pattern"
            )
        if self.masked and self.bits_retained < _UV_MIN_RETAINED_BITS:
            raise ValueError(
                f"WriteTarget: masked target retains only "
                f"{self.bits_retained} bits, below _UV_MIN_RETAINED_BITS "
                f"({_UV_MIN_RETAINED_BITS}) -- a read-back this degenerate "
                "could never be told apart from an absent chip"
            )


def _write_region_for(step: Step | None, eprom_data: dict[str, Any]) -> tuple[int, int]:
    """Return the write/verify region `derive_plan` already decided.

    This function READS `step.write_region` -- the value `derive_plan` set
    exactly once, from `Plan.is_uv` (`is_uv_eprom(full)`, 301/301 exact) and
    `full["memory-size"]` -- and returns it unchanged when present. It
    returns the engine default `(_WRITE_REGION_START, _WRITE_REGION_LENGTH)`
    when `step` is `None` or carries no region (`step.write_region is
    None`).

    This function must NEVER re-derive UV-ness. An earlier revision
    guessed UV-ness at execution time from `eprom_data.get("electrical-type")
    == "UV-EPROM"` OR `eprom_data.get("algorithm") == 0x0B` -- but
    `_dispatch_multi_run`'s `eprom_data` is `resolve_chip`'s PROGRAMMER dict
    (via `convert_to_programmer`), which never carries `electrical-type`,
    and `algorithm == 0x0B` matches only 32 of 301 UV parts (measured), so
    269 UV parts silently fell through to the engine default. A
    missed UV part receiving a full-device write instead of the small
    top-anchored window is a chip-destroying bug, not a coverage gap -- the
    guess is deleted here, not merely bypassed. `eprom_data` is accepted for
    call-site symmetry with `_dispatch_multi_run`'s existing signature but is
    otherwise unused by this function: the WIDTH always comes from a module
    constant (`_WRITE_REGION_LENGTH` / `_UV_WRITE_REGION_LENGTH`), never from
    any DB field -- `eprom_data`/`memory-size` play no role here
    because `derive_plan` already resolved the concrete region.
    """
    if step is not None and step.write_region is not None:
        return step.write_region
    return _DEFAULT_REGION


def _run_step(
    name: str,
    step: Step,
    operator: Any,
    db: Any,
    *,
    runs: int,
    sampler: Any = None,
    write_context: WriteContext | None = None,
    collect_fingerprint: bool = True,
) -> StepResult:
    """Time `_run_step_untimed` and stamp `duration_s` on its result.

    A wrapper rather than a stamp at each `return`: the timed function has
    five return paths (the resolve skip-stub plus four exception arms) and
    one `raise` arm, so stamping in one place is the only way every path
    gets a duration and the run-fatal `raise` keeps propagating untouched.

    Only a step whose verdict says it RAN gets a duration -- `_RAN_VERDICTS`
    is the same frozenset `count_applicable` uses for the banner, so a
    NA/SKIPPED step keeps `duration_s = None` instead of a `0.0` that would
    read as real measured work. `time.monotonic` (never `time.time`) so a
    wall-clock adjustment mid-read cannot produce a negative duration.

    `write_context` is threaded through
    unchanged to `_run_step_untimed`; `None` is the default (the SDP
    lock/unlock cleanup callable in `run_plan` calls this function without
    one, since neither op is write-shaped).
    """
    start = time.monotonic()
    result = _run_step_untimed(
        name,
        step,
        operator,
        db,
        runs=runs,
        sampler=sampler,
        write_context=write_context,
        collect_fingerprint=collect_fingerprint,
    )
    if result.duration_s is None and result.verdict in _RAN_VERDICTS:
        result.duration_s = round(time.monotonic() - start, 3)
    return result


def _run_step_untimed(
    name: str,
    step: Step,
    operator: Any,
    db: Any,
    *,
    runs: int,
    sampler: Any = None,
    write_context: WriteContext | None = None,
    collect_fingerprint: bool = True,
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

    `sampler` is threaded through unchanged to `_dispatch_step`;
    `None` is the default and a proven no-op. `write_context` is
    likewise threaded through unchanged.
    """
    eprom_data, skip_stub, reason = _resolve_or_none(name, db)
    if skip_stub is not None or eprom_data is None:
        if skip_stub is None:
            skip_stub = _skip_result(step.op, reason)
        skip_stub.op = step.op
        return skip_stub

    try:
        return _dispatch_step(
            name,
            step,
            eprom_data,
            operator,
            runs=runs,
            sampler=sampler,
            write_context=write_context,
            collect_fingerprint=collect_fingerprint,
        )
    except (
        ProgrammerNotFoundError,
        FirmwareOutdatedError,
        HardwareRevisionUnsupportedError,
    ):
        # These SerialError subclasses are run-fatal
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
        # A half-seated cable or other transport-level fault
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
    write_context: WriteContext | None = None,
    collect_fingerprint: bool = True,
) -> StepResult:
    """Dispatch `step.op` to its matching existing `EpromOperator` method.

    id -> check_eprom_id (single run, bool/Optional[int]); blank-check ->
    single run; read -> `runs`-times with a byte-level divergence metric
    (never a verdict flip); write/verify/erase -> `runs`-times with a
    marginal-on-disagreement policy; write/verify additionally
    attach a `Fingerprint` (addr_base-aware). SDP lock/unlock -> single run
    via `_dispatch_sdp`, arm 5. The SDP leg's four write-shaped ops -> single
    run via `_dispatch_sdp_leg`, arm 6, LAST -- see below. The engine sets NO
    VPP, builds NO wire dict
    (except the one `FLAG_SKIP_SDP_UNLOCK` bit on `OP_WRITE_INHIBITED`, a
    deliberate narrowing), and passes NO --force -- it only calls the
    operator's existing public methods.

    `sampler` is threaded through unchanged to `_dispatch_multi_run`,
    the only op with a bracket site (OP_WRITE); `None` is the default and a
    proven no-op for every other op. `write_context` is likewise threaded
    through to `_dispatch_multi_run` ONLY --
    deliberately NOT to `_dispatch_sdp`/`_dispatch_sdp_leg`, which keep
    `_write_region_for` and the fixed leg region unchanged.
    """
    if step.op == OP_ID:
        return _dispatch_id(name, eprom_data, operator)
    if step.op == OP_BLANK_CHECK:
        is_ok = operator.check_eprom_blank(name, eprom_data)
        # Debug session w27c512-devtest-all-bad: a failing blank-check now
        # carries the firmware's own id and text. This is the step where it
        # matters most -- mem_util_blank_check emits MSG_ERR_NOT_BLANK with
        # the offending 3-byte ADDRESS and the byte VALUE it read, which is
        # the single most useful datum in a `dev test` failure and was being
        # dropped on the floor.
        code, message = (None, "") if is_ok else _firmware_error(operator)
        return StepResult(
            op=step.op,
            verdict=VERDICT_OK if is_ok else VERDICT_BAD,
            reason=message,
            error_code=code,
            run_count=1,
        )
    if step.op == OP_READ:
        return _dispatch_read(name, eprom_data, operator, runs=runs)
    # write / verify / erase: multi-run marginal policy. Dispatch
    # ONLY when `step.op` is on the live `_MULTI_RUN_OPS` allow-list --
    # anything else refuses fail-closed. Before this
    # guard, this `return` was unconditional, so any op string outside
    # {OP_ID, OP_BLANK_CHECK, OP_READ} fell through to
    # `_dispatch_multi_run`'s own terminal `else` and reached
    # `operator.erase_eprom()` (RESEARCH Pitfall 1a).
    if step.op in _MULTI_RUN_OPS:
        return _dispatch_multi_run(
            step.op,
            name,
            eprom_data,
            operator,
            runs=runs,
            sampler=sampler,
            step=step,
            write_context=write_context,
            collect_fingerprint=collect_fingerprint,
        )
    # Arm 5, LAST -- immediately above the
    # terminal fail-closed `return` below. The measured arm order above is
    # OP_ID -> OP_BLANK_CHECK -> OP_READ -> _MULTI_RUN_OPS -> here, so all
    # seven ops shipped before this phase return from arms 1-4 and NEVER
    # evaluate this membership test at all -- proven mechanically by
    # `tests/test_chip_test_sdp_leg.py::test_shipped_ops_never_reach_sdp_arm`
    # (D-13b's sentinel), not merely asserted. Keys on `_SDP_OPS` membership
    # of the op string rather than a new `Step.group` field -- the op
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
    # Arm 6 -- immediately
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

    Read-step disagreement across runs is a byte-level divergence
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
    """Best-effort sampler invocation -- never lets an exception
    escape (Pitfall 1 extended to the sampler: it is a diagnostic hook, not
    part of the write contract). No-op when `sampler is None`.
    """
    if sampler is None:
        return
    try:
        sampler(phase)
    except Exception:  # noqa: BLE001 -- best-effort diagnostic, swallow all
        pass


# ---------------------------------------------------------------------------
# Execution-time mask/slot/region resolution
# ---------------------------------------------------------------------------


def _address_arg(start: int) -> str | None:
    """`None` for a zero start, else a `0x`-prefixed hex address string.

    The `None` case is load-bearing (M-1): it keeps every region-at-zero
    call byte-identical to today's wire behaviour, since `_setup_operation`
    (`eprom_operations.py`) only sets the command's `address` key when an
    argument is actually supplied.
    """
    if start == 0:
        return None
    return f"0x{start:X}"


def _size_arg(length: int) -> str:
    """The matching `0x`-prefixed size string for a region read."""
    return f"0x{length:X}"


def _read_region(
    operator: Any, name: str, eprom_data: dict[str, Any], start: int, length: int
) -> bytes:
    """One region read into a temp dir, then `[start:start+length]` off the
    file (finding M-3) -- the ONE place this slice lives; every region
    read-back in this module goes through this function.

    A region read produces a hole-padded file whose real bytes sit at the
    ABSOLUTE offset `start` (`eprom_operations._write_to_file`'s
    `file_handle.seek(address)`), never at offset 0 -- slicing anywhere
    else would silently read zero-padding instead of the requested bytes.
    Returns `b""` on any `OSError`, a missing file, or a short/wrong-length
    result -- never raises, so a probe/verify read-back failure degrades
    gracefully rather than crashing the step.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="chip_test_region_") as tmp_dir:
            out_path = str(Path(tmp_dir) / "region.bin")
            operator.read_eprom(
                name,
                eprom_data,
                output_file=out_path,
                address_str=_address_arg(start),
                size_str=_size_arg(length),
            )
            try:
                raw = Path(out_path).read_bytes()
            except OSError:
                return b""
    except EpromOperationError:
        return b""
    chunk = raw[start : start + length]
    if len(chunk) != length:
        return b""
    return chunk


def _resolve_write_target(
    name: str,
    step: Step | None,
    eprom_data: dict[str, Any],
    operator: Any,
    *,
    chip_is_blank: bool | None,
    cycles: int = 1,
) -> tuple[WriteTarget | None, str]:
    """The execution-time resolver -- the ONLY place a write mask is
    computed. Returns `(target, "")` on success, or `(None,
    reason)` on a refusal; never both.

    `derive_plan` already decided `step.write_region`/`step.region_policy`/
    `step.full_device_permitted`; this function only READS them -- it never
    re-derives the region or the policy, only the MASK.

    * `fixed` / `full-device` policy -> an UNMASKED `WriteTarget` over
      `step.write_region` (the plain address-derived pattern, unchanged
      from today for non-UV chips).
    * `uv-slot` policy, chip reported blank AND `step.full_device_permitted`
      -> a MASKED `WriteTarget` over the full-device region, with the mask
      taken as all-0xFF (D-C: the blank-check IS the "current content"
      oracle here -- the device is never read again for this branch).
    * `uv-slot` policy otherwise -> probe candidate slots top-down in
      `_UV_PROBE_BLOCK_LENGTH`-sized reads (`uv_slot_starts`), evaluating
      each slot inside the block with `bits_cleared_by`/`bits_retained_by`,
      and returning the FIRST slot whose counts satisfy both D-B floors.
      Never writes a cursor anywhere -- the probe reads ARE the state
      lookup. A block whose read comes back short/empty is skipped
      (its slots are simply unevaluable, not saturated) rather than
      raising.
    """
    start, length = _write_region_for(step, eprom_data)
    region_policy = step.region_policy if step is not None else REGION_POLICY_FIXED

    if region_policy != REGION_POLICY_UV_SLOT:
        pattern = generate_pattern(start, length)
        try:
            target = WriteTarget(
                region=(start, length),
                pattern=pattern,
                masked=False,
                bits_cleared=0,
                bits_retained=0,
                current_source="address-derived pattern (unmasked)",
                # From the owning `Step.region_policy`, already resolved
                # into the local `region_policy` above -- `fixed` for a
                # non-UV `partial`-scope (or `step is None`) run,
                # `full-device` for a non-UV `full`-scope run.
                region_policy=region_policy,
            )
        except ValueError as exc:
            return None, str(exc)
        return target, ""

    mem_size = int(eprom_data.get("memory-size", 0) or 0)

    # The full-device-if-blank branch is GONE (operator-agreed
    # 2026-08-22), and `chip_is_blank`/`full_device_permitted` no longer gate
    # anything on this path. Recorded rather than silently dropped, because it
    # reverses a decision made one day earlier:
    #
    # `dev test` validates the firmware, host and database for a chip TYPE. It
    # is not a chip-qualification tool. Writing half of a virgin UV part buys
    # no firmware coverage that a single top slot does not already give -- see
    # `uv_slot_starts`, which is TOP-DOWN, so the very first slot chosen is the
    # HIGHEST address on the device and every address line is exercised from
    # run 1 -- while costing the part's entire remaining life as a regression
    # rig. A 64 KiB part yields ~256 slot runs; the full-device branch spent
    # all of them at once.
    #
    # `chip_is_blank` is still threaded in and still set by the blank-check
    # step: it stays a reported FINDING (a UV part that is not blank is
    # operator-actionable), it is simply no longer a scope decision.

    # The slot must support the whole CYCLE, not just one write.
    # Staging `cycles` tranches out of a slot needs each tranche to clear at
    # least `_UV_MIN_CLEARED_BITS` on its own, so the slot's own floor scales
    # with the cycle count. A slot with 64..127 clearable bits passes the
    # single-write filter and cannot support a two-cycle test; requiring the
    # scaled floor HERE means every slot this function returns is
    # tranche-feasible by construction, so `uv_tranche_images` can never come
    # back empty for a slot that was already accepted.
    cleared_floor = max(cycles, 1) * _UV_MIN_CLEARED_BITS
    slot_length = length
    slots_per_block = max(_UV_PROBE_BLOCK_LENGTH // slot_length, 1)
    all_starts = uv_slot_starts(mem_size, slot_length)
    slots_total = len(all_starts)
    for i in range(0, len(all_starts), slots_per_block):
        block_slot_starts = all_starts[i : i + slots_per_block]
        # `all_starts` is top-down: the FIRST entry in this batch is the
        # highest address, the LAST is the lowest -- the block's own start
        # is that lowest address.
        block_start = block_slot_starts[-1]
        block_length = block_slot_starts[0] + slot_length - block_start
        block_data = _read_region(operator, name, eprom_data, block_start, block_length)
        if len(block_data) != block_length:
            continue
        for slot_index, slot_start in enumerate(block_slot_starts, start=i):
            offset = slot_start - block_start
            current = block_data[offset : offset + slot_length]
            desired = generate_pattern(slot_start, slot_length)
            cleared = bits_cleared_by(current, desired)
            retained = bits_retained_by(current, desired)
            if cleared < cleared_floor or retained < _UV_MIN_RETAINED_BITS:
                continue
            masked = mask_write_pattern(current, desired)
            try:
                target = WriteTarget(
                    region=(slot_start, slot_length),
                    pattern=masked,
                    masked=True,
                    bits_cleared=cleared,
                    bits_retained=retained,
                    current_source="probe read",
                    current=current,
                    # Rig life, at ZERO extra I/O. `all_starts` is
                    # top-down and this loop takes the FIRST acceptable slot,
                    # so every slot above `slot_index` is already spent and
                    # every slot below it is untouched. A run saturates
                    # exactly one slot (measured: a slot's clearable count
                    # goes 1024 -> 0 in one run, because the final staged
                    # image IS `current & desired`), so "slots left" and
                    # "runs left on this part" are the same number.
                    slots_remaining=slots_total - slot_index,
                    slots_total=slots_total,
                    # From the owning `Step.region_policy` -- always
                    # `uv-slot` on this branch (the `if` above already
                    # filtered out `fixed`/`full-device`).
                    region_policy=region_policy,
                )
            except ValueError:
                # Defensive only: cleared/retained already satisfied both
                # floors above, so __post_init__'s bit-count refusals
                # cannot fire here -- kept so a future threshold change
                # cannot silently turn into an unhandled crash.
                continue
            return target, ""

    return None, (
        f"every UV slot exhausted without clearing >= {cleared_floor} "
        f"bits and retaining >= {_UV_MIN_RETAINED_BITS} bits under this "
        "pattern -- the chip is saturated; a UV erase is required before "
        "writing further"
    )


def _firmware_error(operator: Any) -> tuple[int | None, str]:
    """The firmware's own id + text for the operation that just failed.

    Debug session w27c512-devtest-all-bad. `write_eprom`/`verify_eprom`/
    `erase_eprom`/`check_eprom_blank` all return a bare bool, and
    `eprom_operations._run_state_machine` catches the `EpromOperationError`
    that carried the firmware's `response.id` -- so `_run_step`'s
    `except EpromOperationError` handler can never fire for those four ops
    and every BAD step in a report came out with `error_code: null` and
    `reason: ""`. `EpromOperator` now records the pair on itself (see its
    `__init__`); this reads it back.

    `getattr` with defaults, not attribute access: every test double in this
    suite is a hand-rolled stand-in for `EpromOperator`, none of them carry
    these attributes, and a missing attribute must degrade to "no firmware
    error recorded" rather than raise inside a failure path. Returns
    `(None, "")` in that case, which is exactly the pre-existing behaviour.

    `_run_state_machine` clears both on entry, so a value read here always
    belongs to the call that just returned -- never a stale one from an
    earlier step.
    """
    code = getattr(operator, "last_firmware_error_code", None)
    message = getattr(operator, "last_firmware_error_message", None) or ""
    return code, message


def _dispatch_multi_run(
    op: str,
    name: str,
    eprom_data: dict[str, Any],
    operator: Any,
    *,
    runs: int,
    sampler: Any = None,
    step: Step | None = None,
    write_context: WriteContext | None = None,
    collect_fingerprint: bool = True,
) -> StepResult:
    """Run a destructive/verify op `runs` times; `marginal` on disagreement.

    Collects a per-run bool outcome (the operator method's own return value)
    for write/write-partial/erase; write/write-partial/verify ALSO builds
    the expected address-derived pattern and reads back via
    `operator.verify_eprom`'s outcome plus a fresh `read_eprom` to compute
    the `Fingerprint`. Disagreement across the N per-run outcomes
    -> `marginal`, never coerced to a confident OK/BAD (the AM27C020
    structural case). The write/verify region is READ from `step.
    write_region` via `_write_region_for(step, eprom_data)` (
    Plan 06) -- `derive_plan` already decided it; this function never
    re-derives UV-ness.

    `sampler` is invoked as `sampler("before")` /
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
    `verify_eprom`, or `erase_eprom`. This is the host mirror of the firmware's
    generic op-layer NULL-`main` refusal
    (`firestarter/src/operation_utils.cpp::op_execute_stateful_operation`;
    read-only reference, not re-implemented here). Before this guard, this
    function's run loop ended in a bare `else: # OP_ERASE`, so an unmapped op
    called `operator.erase_eprom()` once per run and reported `VERDICT_OK`
    (RESEARCH Pitfall 1a, proven empirically: 2 runs -> 2 calls -> OK).

    For `OP_WRITE`/`OP_WRITE_PARTIAL`, the
    write target (region + pattern, masked or not) is resolved HERE via
    `_resolve_write_target` -- a saturated/refused target returns SKIPPED
    with the refusal reason and `write_eprom` is NEVER called (the
    structural vacuous-pass guard extends all the way to this dispatch
    site). For `OP_VERIFY` on a non-`fixed` policy, the target is INHERITED
    from `write_context.target` (set by the preceding write step) rather
    than re-resolved -- the verify step never computes its own mask. For
    `OP_ERASE` and every `fixed`-policy write/verify, behaviour is
    unchanged from before this task. The write/verify read-back (for the
    `Fingerprint`) is now region-scoped via `_read_region` instead of a
    whole-device read (finding M-2: a whole-device read-back only "worked"
    because every prior test double wrote exactly a region-sized payload).
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
    resolved_target: WriteTarget | None = None
    region_start = 0
    region_length = 0
    expected = b""

    if op in (OP_WRITE, OP_WRITE_PARTIAL):
        # The cycle plan wins when there is one: `_run_cycle_block`
        # already resolved this cycle's target, so re-resolving here would
        # re-probe the device and could land on a different UV slot than the
        # cycle it belongs to. `None` means no cycle plan applies and the
        # pre-cycle resolve path runs unchanged.
        planned = _cycle_target(write_context)
        if planned is not None:
            resolved_target, refusal = planned, ""
        else:
            resolved_target, refusal = _resolve_write_target(
                name,
                step,
                eprom_data,
                operator,
                chip_is_blank=(
                    write_context.chip_is_blank if write_context is not None else None
                ),
            )
        if resolved_target is None:
            # The vacuous-pass guard's SKIPPED path: `write_eprom` is NEVER
            # called for a saturated/refused target -- no step reports OK
            # for a write that did not happen.
            return StepResult(
                op=op,
                verdict=VERDICT_SKIPPED,
                reason=refusal,
                run_count=0,
                write_target=None,
            )
        region_start, region_length = resolved_target.region
        expected = resolved_target.pattern
    elif op == OP_VERIFY:
        non_fixed_policy = (
            step is not None and step.region_policy != REGION_POLICY_FIXED
        )
        if non_fixed_policy:
            inherited = write_context.target if write_context is not None else None
            if inherited is None:
                refusal = (
                    write_context.refusal
                    if write_context is not None and write_context.refusal
                    else "no write target available for verify"
                )
                return StepResult(
                    op=op, verdict=VERDICT_SKIPPED, reason=refusal, run_count=0
                )
            resolved_target = inherited
            region_start, region_length = inherited.region
            expected = inherited.pattern
        else:
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
                outcomes.append(
                    operator.write_eprom(
                        name,
                        eprom_data,
                        tmp_source_path,
                        address_str=_address_arg(region_start),
                    )
                )
                _sample(sampler, "after")
            elif op == OP_VERIFY:
                outcomes.append(
                    operator.verify_eprom(
                        name,
                        eprom_data,
                        tmp_source_path,
                        address_str=_address_arg(region_start),
                    )
                )
            elif op == OP_ERASE:
                outcomes.append(operator.erase_eprom(name, eprom_data))
            else:
                # Unreachable in practice: the fail-closed `_MULTI_RUN_OPS`
                # guard at the top of this function already refused any op
                # outside {OP_WRITE, OP_WRITE_PARTIAL, OP_VERIFY, OP_ERASE}
                # before this loop could start. Kept explicit rather than a
                # bare `else: # OP_ERASE` -- the pre-fix shape that
                # silently routed an unmapped op to `erase_eprom()`.
                raise AssertionError(
                    f"unreachable: op {op!r} passed the _MULTI_RUN_OPS guard"
                )

        if collect_fingerprint and op in (OP_WRITE, OP_WRITE_PARTIAL, OP_VERIFY):
            # Readback for the fingerprint is best-effort: a readback failure
            # (e.g. the SAME boot-block-locked condition that failed the
            # write/verify runs themselves) must NOT convert an otherwise
            # successful write/verify outcome into BAD (Pitfall 1 extends to
            # this internal readback call too) -- it only means no
            # Fingerprint could be attached. Region-scoped via `_read_region`
            # (finding M-2) rather than a whole-device read.
            actual = _read_region(
                operator, name, eprom_data, region_start, region_length
            )

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
    # Debug session w27c512-devtest-all-bad: the firmware's own id + text for
    # this op, read back off the operator (see `_firmware_error`). Captured
    # BEFORE the verdict branches below so both the BAD and the `marginal`
    # arm can use it -- a `marginal` step has at least one failed run and its
    # error code is just as diagnostic as a BAD one's.
    error_code, error_message = _firmware_error(operator)
    if diverged:
        verdict = VERDICT_MARGINAL
        reason = f"{runs} runs disagreed on outcome"
    else:
        verdict = VERDICT_OK if outcomes and outcomes[0] else VERDICT_BAD
        # The firmware's text becomes the step's reason ONLY on a non-OK
        # verdict, and only when the marginal wording has not already
        # claimed the field -- that wording states a policy decision this
        # function made, which must not be overwritten by a per-run detail.
        # The CODE is attached in both cases: it is a separate field and
        # never competes with the reason text.
        reason = "" if verdict == VERDICT_OK else error_message

    return StepResult(
        op=op,
        verdict=verdict,
        reason=reason,
        error_code=None if verdict == VERDICT_OK else error_code,
        run_count=runs,
        fingerprint=fingerprint,
        write_target=resolved_target if op in (OP_WRITE, OP_WRITE_PARTIAL) else None,
    )


def _dispatch_sdp(
    op: str, name: str, eprom_data: dict[str, Any], operator: Any
) -> StepResult:
    """Dispatch an SDP lock/unlock op to its matching `EpromOperator` method.

    Signature is a FORWARD CONTRACT: the same
    first four positional parameters as `_dispatch_multi_run` --
    `(op: str, name: str, eprom_data: dict[str, Any], operator: Any)` --
    because the roadmap entry names this arm verbatim
    and builds its four-step leg on it. No keyword-only parameters: SDP
    emissions are single-run (`_MULTI_RUN_OPS` exclusion above), so
    `runs` and `sampler` are deliberately absent here, not merely omitted by
    oversight.

    Structurally clones `_dispatch_multi_run`'s guard -> branch -> terminal
    `raise AssertionError` shape rather than importing/reusing it, so
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
        # or `EpromOperationError`, so `_run_step`'s except chain does
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
    READ-BACK-EQUALITY oracle.

    This is the milestone's reason to exist: the verdict comes from
    comparing the read-back bytes against what SHOULD be there, never from
    `write_eprom`'s own bool. A write that returns without error is NOT, by
    itself, evidence of anything -- see below.

    A SEPARATE dispatcher from `_dispatch_sdp` (whose frozen four-
    positional forward contract, unchanged here): these four ops need a
    source payload, a read-back, and an `operation_flags` argument that
    signature cannot carry. Structurally clones `_dispatch_sdp`'s /
    `_dispatch_multi_run`'s guard -> branch -> terminal `raise
    AssertionError` shape rather than importing/reusing either.

    ⚠ Measured, not merely designed around: the `0x86` opt-out ack
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
    experiment ran as designed. `False` NEVER means BAD by itself -- it routes
    to `marginal`, naming both candidate causes (the
    opt-out not honoured by older firmware, or a transport fault).

    ⚠ The full 2x2 polarity proof holds for `OP_WRITE_INHIBITED`:
    `(True, A) -> OK`, `(True, B) -> BAD` -- these two hold the bool
    CONSTANT and vary only the read-back, a STRICTLY STRONGER proof than a
    bool-driven implementation could pass, because such an implementation
    cannot produce two different verdicts from one identical bool.
    `(False, A) -> marginal`, `(False, B) -> marginal` pin the precondition
    gate in both read-back directions. P-03 prevention 4's `(False, A) ->
    OK` is OVERTURNED by the two points above and is deliberately NOT
    implemented here.

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
    # writable ("restored" evidence).
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
    # members).
    tmp_fh = tempfile.NamedTemporaryFile(
        prefix="chip_test_sdp_leg_", suffix=".bin", delete=False
    )
    try:
        tmp_fh.write(source_payload)
    finally:
        tmp_fh.close()
    tmp_source_path = tmp_fh.name

    try:
        wrote_ok = operator.write_eprom(
            name,
            eprom_data,
            tmp_source_path,
            flags,
            address_str=_address_arg(region_start),
        )

        # Read back. ⚠ Unlike `_dispatch_multi_run`'s read-back
        # (`:1483-1493`), this read-back is NOT best-effort decoration -- it
        # IS the verdict. A failed/degenerate read-back still
        # produces a verdict below (BAD via the length gate), it never
        # silently skips the Fingerprint the way the multi-run write/verify
        # step does. Region-scoped via `_read_region` (quick task
        # 260821-wna, finding M-2): the length gate below was previously
        # satisfiable only by a double whose read-back happened to return
        # exactly `region_length` bytes; a region-scoped, sliced read is
        # what makes it a real gate against a whole-device read on real
        # hardware.
        actual = _read_region(operator, name, eprom_data, region_start, region_length)
    finally:
        try:
            Path(tmp_source_path).unlink()
        except OSError:
            pass

    # a. LENGTH gate FIRST (P-02). Measured:
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

    # b. CONTENT degeneracy. Correct length but degenerate content
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
                "fault, not a chip finding"
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
        # The full 2x2, on pattern A (unchanged) as the expected value.
        if wrote_ok and equal:
            verdict, reason = VERDICT_OK, ""
        elif wrote_ok and not equal:
            # The leg's whole value -- covers both a full change to
            # B and a PARTIAL change (gh#11's exact symptom).
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
            # A failed precondition is marginal in BOTH read-back
            # directions -- BAD here would manufacture a chip-fault report
            # for a community member running older firmware.
            verdict, reason = (
                VERDICT_MARGINAL,
                (
                    "write_eprom reported failure on the inhibited-write "
                    "precondition — this is a PRECONDITION signal, not the "
                    "verdict. Most likely causes: (1) the 0x86 opt-out "
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
                    "transition (a dead write path) or changed "
                    "only part of the region"
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
# Applicable-only N-of-M banner DATA
# ---------------------------------------------------------------------------
#
# DATA ONLY -- this module emits no print/render/CLI output; rendering the
# "only N of M tests ran -- pass --destructive on a scrap chip for the rest"
# banner belongs to the report model and the dev test handler.
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
# NEVER called a second time to compute M.
#
# N counts the steps THIS run actually executed: any StepResult verdict in
# {OK, BAD, marginal} counts as "ran" (a ran-but-BAD step still counts,
# since "ran" and "verdict" are separate axes); NA and SKIPPED steps do not
# count toward N (they never reached the operator).

_RAN_VERDICTS = frozenset({VERDICT_OK, VERDICT_BAD, VERDICT_MARGINAL})


@dataclass
class BannerCounts:
    """Applicable-only N-of-M banner DATA -- no rendering here.

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
    """Compute the applicable-only N-of-M banner data.

    M = `sum(1 for s in plan.steps if s.supported)` PLUS
    `len(plan.locked_destructive)` -- both read off the ONE `plan` object
    passed in; this function never calls `derive_plan`.

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
