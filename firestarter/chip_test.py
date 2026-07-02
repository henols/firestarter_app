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
