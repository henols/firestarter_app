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
builds no wire dict, and calls no operator/firmware method. Plans 108-03
(derive_plan) and 108-04 (run_plan) extend this module with the
orchestration engine in later waves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
