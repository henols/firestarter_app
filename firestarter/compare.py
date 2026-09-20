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
    """Populated by `classify_streamed` (202-03). Always `None` in this
    plan -- 202-01 does not classify, only compares."""


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
        `CompareResult`. `repeat_divergent` is accepted for forward
        compatibility with `classify_streamed` (202-03); this plan does not
        populate `fingerprint`.

        `total` defaults to `compared` -- a caller that knows the declared
        region size up front overrides `result.total` afterwards, since
        `CompareResult` is a plain, caller-mutable dataclass.
        """
        _ = repeat_divergent  # unused until 202-03's classify_streamed
        self._close_open_range()
        compared_start = (
            self._compared_start
            if self._compared_start is not None
            else self._addr_base
        )
        compared_end = (
            compared_start - 1 if self._compared_end is None else self._compared_end
        )
        return CompareResult(
            total=self._compared,
            compared=self._compared,
            compared_start=compared_start,
            compared_end=compared_end,
            bad=self._bad,
            ranges=list(self._ranges),
            extra_ranges=self._extra_ranges,
            extra_bytes=self._extra_bytes,
            aborted=aborted,
            fingerprint=None,
        )


def render_compare_lines(result: CompareResult) -> list[str]:
    """Render `result` into the D-13/D-16 report lines.

    Returns, in order, one string per retained range in exactly the form
    `Mismatch 0xSTART-0xEND (N bytes)` with START and END as six-digit
    uppercase hex (D-13); then, when `result.extra_ranges` is non-zero, one
    tail line built from `extra_ranges` and `extra_bytes` (D-16). Emits no
    expected value and no actual value anywhere -- D-13 is an operator
    decision taken twice, and D-15 records that CMP-04 and ROADMAP criterion
    3 were already amended to match (commit 81414f98). Performs no echo and
    imports no `click`, so `compare.py` stays usable from phases 203 and 206
    (D-05).
    """
    lines = [
        f"Mismatch 0x{r.start:06X}-0x{r.end:06X} ({r.count} bytes)"
        for r in result.ranges
    ]
    if result.extra_ranges:
        lines.append(
            f"… and {result.extra_ranges} more ranges, {result.extra_bytes} bytes"
        )
    return lines
