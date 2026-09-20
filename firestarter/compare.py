"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

The one streaming comparison engine, shared by every host-side EPROM compare.

Separate from `eprom_operations.py` and `chip_test.py` on purpose (D-01).
`eprom_operations.py` is 2502 lines and pulls `serial_comm`; `chip_test.py` is
3774 lines and pulls `chip_resolver` into `database`. Either home would force
the other module to import it -- `eprom_operations.verify_eprom` (and,
later, `check_eprom_blank`) need this engine, `chip_test.classify_fingerprint`
needs it too, and phase 203's write guard needs it a third time. A home
inside either existing module recreates exactly the import cycle
`chip_test.py:113-117`'s "one divergence implementation" rule exists to
prevent.

Import-set invariant (checked by an AST-based test): this module's top-level
import set is a subset of {"__future__", "dataclasses", "typing"}. A
function-local lazy import of `click`, `firestarter.eprom_operations`,
`firestarter.chip_test` or `firestarter.serial_comm` would defeat D-01 just
as thoroughly as a top-level one -- either one drags this module back into
the cycle it exists to avoid, and would make it unusable from
`chip_test.py`, which imports it for `Fingerprint`/`classify_fingerprint`
support, or from phase 203's write guard.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_RETAINED_RANGES: int = 64
"""D-16's cap on retained mismatch ranges. Past this many coalesced ranges,
`CompareAccumulator` stops retaining new `MismatchRange` objects but keeps
counting exactly -- `extra_ranges` and `extra_bytes` come from running
counters, never from a list length, so the totals stay honest arbitrarily far
past the cap. The alternating worst case (every other byte differs) on a
512 KiB part produces roughly 262144 coalesced ranges; the cap is what keeps
`--full`'s output bounded at 65 lines regardless of device size.
"""

# The four locked outcome labels -- never coerce an ambiguous distribution
# into one of the first three; fall back to indeterminate. Moved from
# `chip_test.py` (D-01): `classify_fingerprint` there imports these back.
FP_BLANK_CONTACT = "blank/contact"
FP_ADDRESS_LINE = "address-line"
FP_TRANSPORT = "transport"
FP_INDETERMINATE = "indeterminate"
FP_MATCH = "match"

# Candidate thresholds (Claude's discretion) -- direction is HIGH-confidence,
# exact numbers are tunable/bench-informed later. A wrong number only
# produces more `indeterminate`, never a false confident label. Moved from
# `chip_test.py` (D-01), values unchanged.
_FF_RATIO_THRESHOLD = 0.98  # blank/contact: >= this fraction of actual == 0xFF
_BIT_CLUSTER_THRESHOLD = 0.9  # address-line: >= this fraction of mismatches
# share one polarity of one high address bit


@dataclass
class Fingerprint:
    """Verdict + raw evidence for a single expected-vs-actual byte compare.

    Moved from `chip_test.py` (D-01), fields unchanged. `chip_test.py`
    re-exports this name so every existing importer keeps resolving it from
    `chip_test` with zero test churn.
    """

    total: int
    bad: int
    bad_pct: float
    classification: str
    evidence: dict = field(default_factory=dict)


@dataclass
class MismatchRange:
    """One coalesced run of consecutive mismatching offsets.

    `start` and `end` are absolute chip addresses, both inclusive.
    """

    start: int
    end: int
    count: int


@dataclass
class CompareResult:
    """The structured verdict produced by `CompareAccumulator.finalise()`.

    D-05: compare and render are separated from the start, because phase 203's
    write guard and phase 206's `dev test` both need this structured result
    without an echoed report -- this is not speculative widening, 203 is the
    very next phase.
    """

    total: int
    """The region length the caller asked to compare, in bytes. Defaulted by
    `finalise()` to `compared`; a caller that knows the declared region size
    up front (e.g. `verify_eprom`'s file length) overrides it after the
    call, since this is a plain, caller-mutable dataclass."""

    compared: int
    """Bytes actually compared. Equal to `total` on a complete compare;
    smaller when the compare was aborted (D-06..D-09) or stopped early."""

    compared_start: int
    """Absolute address of the first byte actually compared."""

    compared_end: int
    """Absolute address of the last byte actually compared, inclusive.
    Equals `compared_start - 1` when nothing was compared."""

    bad: int
    """Exact count of mismatching bytes -- never capped, unlike `ranges`."""

    ranges: list[MismatchRange]
    """Coalesced mismatch ranges, retained up to the accumulator's
    `max_ranges`. A fresh list on every `finalise()` call, so mutating it
    cannot reach back into the accumulator."""

    extra_ranges: int
    """Count of further coalesced ranges beyond the retained list, from a
    running counter -- never derived from `len(ranges)`. D-16."""

    extra_bytes: int
    """Bytes belonging to those unretained ranges, from a running counter,
    exact past the cap. D-16."""

    aborted: bool
    """True when the compare stopped before reaching `total` ON PURPOSE
    (D-06..D-09's host-initiated stop), as distinct from simply having
    compared fewer bytes than declared for some other reason."""

    fingerprint: Fingerprint | None = None
    """Populated by `CompareAccumulator.finalise()` via `classify_streamed`
    (202-03) -- every `finalise()` call sets this, clean or mismatching, so a
    caller never has to ask separately."""

    ff_count: int = 0
    """Count of actual bytes equal to `0xFF` among the bytes actually
    compared -- `classify_streamed`'s blank/contact ratio numerator
    (202-03). A running counter, not recomputed from `ranges`."""

    first_offset: int | None = None
    """Offset of the first mismatching byte, relative to the accumulator's
    `addr_base` -- `None` when `bad == 0`. Matches the batch
    `classify_fingerprint`'s `evidence["first_offset"]` exactly (202-03)."""

    bit_set_counts: dict[int, int] = field(default_factory=dict)
    """Per absolute-address bit index, the running count of mismatching
    bytes whose address has that bit set. Populated online for every
    candidate bit `CompareAccumulator.feed()` could see so far -- the
    correct candidate-bit RANGE (`8 <= k < (compared_length - 1).bit_length()`)
    is only knowable at `finalise()`, so `classify_streamed` is what filters
    this dict down to the keys the batch classifier would have emitted
    (202-03)."""


class CompareAccumulator:
    """The single streaming divergence implementation D-02 requires.

    `feed()` is called once per chunk as bytes arrive off the wire (see
    `verify_eprom`'s `process_data_chunk_callback`). Three-tier per-chunk
    cost, cheapest first: a running-counter update (`ff_count`, running
    compared total) done at C level; an equality fast path that closes any
    open range and returns without touching a single byte in Python; and,
    only for a chunk that actually differs, a per-chunk list of differing
    offsets whose length is bounded by the chunk size and which goes out of
    scope at the end of `feed`.

    This shape is load-bearing and is the plan's answer to a trap
    RESEARCH.md measured. A literal reading of D-02 that avoids lists
    entirely produces a per-byte, per-bit nested loop costing 19.8 to 21.4
    seconds over 512 KiB, in the test suite and in production. D-02 forbids
    a device-sized offset list; a per-chunk list bounded by the chunk size
    and discarded at the chunk boundary is a different object, and it is
    eleven times faster. The equality fast path takes a clean 512 KiB
    compare to 0.002 seconds. Do not write the version that avoids lists.
    """

    def __init__(
        self, *, addr_base: int = 0, max_ranges: int = MAX_RETAINED_RANGES
    ) -> None:
        self._addr_base = addr_base
        self._max_ranges = max_ranges
        self._compared = 0
        self._compared_start: int | None = None
        self._compared_end: int | None = None
        self._bad = 0
        self._ff_count = 0
        self._first_offset: int | None = None
        self._set_count: dict[int, int] = {}
        self._ranges: list[MismatchRange] = []
        self._open_range: MismatchRange | None = None
        self._extra_ranges = 0
        self._extra_bytes = 0

    @property
    def has_mismatch(self) -> bool:
        """True once at least one mismatching byte has been fed."""
        return self._bad > 0

    def feed(self, address: int, expected: bytes, actual: bytes) -> None:
        """Fold one chunk into the running totals.

        `address` is the absolute chip address of `actual[0]`. `expected`
        and `actual` are assumed the same length -- the pull callback that
        builds `expected` is what guarantees that by construction.
        """
        chunk_len = len(actual)
        if chunk_len == 0:
            return

        # Tier 1: running-counter updates, both at C level.
        self._ff_count += actual.count(0xFF)
        self._compared += chunk_len
        if self._compared_start is None:
            self._compared_start = address
        self._compared_end = address + chunk_len - 1

        # Tier 2: equality fast path -- a clean chunk never touches a
        # single byte in Python beyond the comparison itself.
        if expected == actual:
            self._close_open_range()
            return

        # Tier 3: a per-chunk offset list, bounded by chunk_len and
        # discarded at the end of this call -- NOT a device-sized list.
        # Rejected alternative: a counter-only shape that avoids per-chunk
        # lists entirely (an int-XOR-and-scan or a per-offset accumulation
        # with no list at all). RESEARCH.md measured that shape at 21.4s
        # against this list-based path's 1.9s for the worst-case 512 KiB
        # all-differing chunk stream -- a ~79 KB smaller peak (7347 B vs
        # 86430 B) bought at roughly 11x the runtime. The list is kept
        # because the runtime cost of avoiding it is not worth the memory
        # saved, and both shapes are already far under the peak-allocation
        # ceiling (see tests/test_compare.py's PEAK_ALLOCATION_CEILING_BYTES).
        offs = [o for o in range(chunk_len) if expected[o] != actual[o]]
        if not offs:
            self._close_open_range()
            return

        if self._first_offset is None:
            self._first_offset = address + offs[0] - self._addr_base
        self._bad += len(offs)

        # Per-bit clustering evidence for 202-03's classify_streamed. The
        # bit loop is outside the offset loop, never nested per offset.
        top_addr = address + chunk_len - 1
        max_bit = max(top_addr.bit_length(), 9)
        for k in range(8, max_bit):
            mask = 1 << k
            self._set_count[k] = self._set_count.get(k, 0) + sum(
                1 for o in offs if (address + o) & mask
            )

        for o in offs:
            abs_addr = address + o
            if self._open_range is not None and abs_addr == self._open_range.end + 1:
                self._open_range.end = abs_addr
                self._open_range.count += 1
            else:
                self._close_open_range()
                self._open_range = MismatchRange(start=abs_addr, end=abs_addr, count=1)

    def _close_open_range(self) -> None:
        if self._open_range is None:
            return
        # D-16's cap bounds what is SHOWN (the retained `_ranges` list),
        # never what is COUNTED: a range past `_max_ranges` still adds to
        # `_extra_ranges`/`_extra_bytes` exactly, so `render_compare_lines`'s
        # tail line and any consumer reading these counters directly stay
        # honest arbitrarily far past the cap. This is the standing
        # prohibition this module carries -- a count or classification must
        # never read as covering more of the device than was actually
        # counted, and the retained-range cap must never be the thing that
        # silently narrows what got counted.
        if len(self._ranges) < self._max_ranges:
            self._ranges.append(self._open_range)
        else:
            self._extra_ranges += 1
            self._extra_bytes += self._open_range.count
        self._open_range = None

    def finalise(
        self, *, repeat_divergent: bool | None = None, aborted: bool = False
    ) -> CompareResult:
        """Close any open range and return a fresh, immutable-to-us
        `CompareResult`, with `fingerprint` already populated via
        `classify_streamed` (202-03 Task 3) -- clean or mismatching, every
        `finalise()` call classifies, so no caller has to ask separately.
        `repeat_divergent` forwards straight through to `classify_streamed`.

        `total` defaults to `compared` -- a caller that knows the declared
        region size up front overrides `result.total` afterwards, since
        `CompareResult` is a plain, caller-mutable dataclass. That override
        happens AFTER this call, so `classify_streamed` (and the bucket line
        `render_compare_lines` derives from it) always reasons about
        `compared`, never a region size the accumulator was never told.
        """
        self._close_open_range()
        compared_start = (
            self._compared_start
            if self._compared_start is not None
            else self._addr_base
        )
        compared_end = (
            compared_start - 1 if self._compared_end is None else self._compared_end
        )
        result = CompareResult(
            total=self._compared,
            compared=self._compared,
            compared_start=compared_start,
            compared_end=compared_end,
            bad=self._bad,
            ranges=list(self._ranges),
            extra_ranges=self._extra_ranges,
            extra_bytes=self._extra_bytes,
            aborted=aborted,
            ff_count=self._ff_count,
            first_offset=self._first_offset,
            bit_set_counts=dict(self._set_count),
        )
        result.fingerprint = classify_streamed(
            result, repeat_divergent=repeat_divergent
        )
        return result


# ---------------------------------------------------------------------------
# Shared byte-diff-offset primitive -- owned here, imported by both sides
# ---------------------------------------------------------------------------
#
# `diff_summary` is the ONE divergence primitive `classify_fingerprint`
# (chip_test.py, via `classify_streamed` below) and `_dispatch_read`'s
# multi-run consistency check both consume -- do NOT add a second parallel
# divergence implementation elsewhere in this codebase. The math used to
# live in `chip_test.py`, copied rather than imported so that module stayed
# import-light (no dependency on this one). D-02 moves it here instead:
# `classify_fingerprint` now delegates to the streaming accumulator this
# module owns, so a second copy of the same math sitting in `chip_test.py`
# would be exactly the "second implementation" this rule forbids.
# `chip_test.py` imports this function; it does not reimplement it.


@dataclass
class DiffSummary:
    """The four values `chip_test._diff_offsets` used to return, minus the
    offset list itself (202-03, D-02) -- the one caller that consumed
    `len(diff_offsets)` gets `bad` directly instead."""

    cmp_len: int
    bad: int
    pct: float
    first_offset: int | None


def diff_summary(expected: bytes, actual: bytes) -> DiffSummary:
    """Byte-diff two buffers over their common prefix; never raises.

    Replaces `chip_test._diff_offsets` (202-03, D-02): feeds a single
    `CompareAccumulator` rather than materialising a list of every
    mismatching offset. `pct` matches `_diff_offsets`' exact expression --
    `100.0 * bad / cmp_len` when `cmp_len` is non-zero, `0.0` otherwise --
    and an empty or unequal-length pair compares over the shorter buffer
    without raising, exactly as `_diff_offsets` did.
    """
    cmp_len = min(len(expected), len(actual))
    acc = CompareAccumulator()
    acc.feed(0, expected[:cmp_len], actual[:cmp_len])
    result = acc.finalise()
    pct = 100.0 * result.bad / cmp_len if cmp_len else 0.0
    return DiffSummary(
        cmp_len=cmp_len,
        bad=result.bad,
        pct=pct,
        first_offset=result.first_offset,
    )


def classify_streamed(
    result: CompareResult, *, repeat_divergent: bool | None = None
) -> Fingerprint:
    """Classify a streamed `CompareResult` into one of five honest buckets.

    D-02: this is the divergence classifier `chip_test.classify_fingerprint`
    delegates to -- there is exactly one implementation of this math, here.
    Reproduces `chip_test.classify_fingerprint`'s batch output bit for bit
    (D-03's corpus in `tests/test_compare.py` proves it), including its two
    traps:

    - The candidate bit-clustering range is `8 <= k < (cmp_len - 1).bit_length()`
      where `cmp_len` is `result.compared` -- the number of bytes actually
      compared, NOT an absolute address. `result.bit_set_counts` was
      accumulated online using each chunk's own absolute top address as an
      upper bound (a superset when `addr_base` is non-zero, since the base
      itself inflates that bound), so this function is what narrows the
      final `evidence["bit_clustering"]` dict down to exactly the keys the
      batch classifier would have emitted -- computing the correct range
      any earlier is impossible, since the total compared length is only
      known once the compare ends.
    - `suspected_line` selection scans `k` ascending from 8 and keeps a
      STRICTLY greater score, so a tie resolves to the lowest bit -- the
      `range(8, max_bit)` iteration order below preserves that.

    Classification order is LOCKED, identical to the batch classifier:
      1. blank/contact  -- cheapest, most common false-PASS source
      2. address-line   -- power-of-two high-bit clustering
      3. match          -- zero mismatches, checked AFTER buckets 1 and 2 so
                            an all-0xFF perfect compare stays blank/contact
                            rather than silently re-keying that population.
      4. transport       -- scattered + non-repeatable across N>=2 runs
      5. indeterminate   -- fallback; NEVER coerce an ambiguous distribution
                            into a confident label.
    """
    cmp_len = result.compared
    bad = result.bad
    bad_pct = 100.0 * bad / cmp_len if cmp_len else 0.0

    ff_ratio = (result.ff_count / cmp_len) if cmp_len else 0.0

    evidence: dict = {
        "ff_ratio": ff_ratio,
        "repeat_divergent": repeat_divergent,
        "first_offset": result.first_offset,
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
    # high address bit (A8+). Candidate bits are restricted to those that
    # can actually vary within [0, cmp_len), i.e. 8 <= k < (cmp_len-1).bit_length();
    # bits at or above that never toggle within the compared region and
    # would spuriously "cluster" at 100% (see this function's docstring).
    suspected_line = None
    best_score = 0.0
    if bad and cmp_len > (1 << 8):
        max_bit = (cmp_len - 1).bit_length()
        for k in range(8, max_bit):
            set_count = result.bit_set_counts.get(k, 0)
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

    if bad == 0:
        return Fingerprint(
            total=cmp_len,
            bad=bad,
            bad_pct=bad_pct,
            classification=FP_MATCH,
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


def render_compare_lines(result: CompareResult) -> list[str]:
    """Render `result` into the D-13/D-16/D-14 report lines.

    Returns, in order: one string per retained range in exactly the form
    `Mismatch 0xSTART-0xEND (N bytes)` with START and END as six-digit
    uppercase hex (D-13); then, when `result.extra_ranges` is non-zero, one
    tail line built from `extra_ranges` and `extra_bytes` (D-16); then, when
    `result.fingerprint` is populated (finalise() always populates it --
    202-03), exactly one bucket summary line in the form `{classification},
    {bad} bad of {compared} compared of {total} (0xSTART-0xEND)` (D-14),
    always last. Emits no expected value and no actual value anywhere --
    D-13 is an operator decision taken twice, and D-15 records that CMP-04
    and ROADMAP criterion 3 were already amended to match (commit
    81414f98). Performs no echo and imports no `click`, so `compare.py`
    stays usable from phases 203 and 206 (D-05).

    D-09's reason for printing the span alongside the bucket: a compare
    that stopped early only ever saw a prefix, and the blank/contact bucket
    fires on a blank ratio at or above 0.98 -- so a short mostly-blank
    prefix could otherwise read as a confident whole-chip verdict. The span
    makes a truncated sample unmistakable.
    """
    lines = [
        f"Mismatch 0x{r.start:06X}-0x{r.end:06X} ({r.count} bytes)"
        for r in result.ranges
    ]
    if result.extra_ranges:
        lines.append(
            f"… and {result.extra_ranges} more ranges, {result.extra_bytes} bytes"
        )
    if result.fingerprint is not None:
        fp = result.fingerprint
        lines.append(
            f"{fp.classification}, {fp.bad} bad of {result.compared} compared "
            f"of {result.total} "
            f"(0x{result.compared_start:06X}-0x{result.compared_end:06X})"
        )
    return lines
