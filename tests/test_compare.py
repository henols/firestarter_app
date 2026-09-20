"""Tests for `firestarter/compare.py` -- the 202-01/202-02/202-03 streaming
comparison engine (D-01/D-02/D-03/D-04/D-05/D-13/D-14/D-16).

Drives `CompareAccumulator` directly from a synthetic generator of
(address, bytes) chunk pairs -- no operator, no serial, no bench hardware.
This is deliberate: the engine is stdlib-only (see the module's own
import-set invariant, separately pinned by an AST scan in this file), so
most of this file's tests need nothing beyond that either. The one
deliberate exception is the D-03 corpus (`TestClassifyFingerprintCorpus`),
which imports `firestarter.chip_test` and a fixture from
`tests/test_chip_test.py` on purpose -- proving the STREAMED path and the
DELEGATING `chip_test.classify_fingerprint` agree is the entire point of
that class, so it cannot stay import-light the way the rest of this file
does.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import time
import tracemalloc
from collections.abc import Iterator

import pytest

# The only two names in this file that reach outside `firestarter.compare`
# and stdlib -- used exclusively by the D-03 corpus (`TestClassifyFingerprintCorpus`)
# below, nowhere else in this file. `_SCATTERED_OFFSETS` is reused verbatim
# from `tests/test_chip_test.py` rather than re-derived (per the plan): it
# is the one hand-tuned input in the suite already measured to sit below
# the 0.9 clustering threshold (~0.81) -- a fresh set of offsets risks
# landing in address-line by accident.
from firestarter.chip_test import classify_fingerprint
from firestarter.compare import (
    FP_ADDRESS_LINE,
    FP_BLANK_CONTACT,
    FP_INDETERMINATE,
    FP_MATCH,
    FP_TRANSPORT,
    MAX_RETAINED_RANGES,
    CompareAccumulator,
    CompareResult,
    Fingerprint,
    MismatchRange,
    classify_streamed,
    diff_summary,
    render_compare_lines,
)
from tests.test_chip_test import _SCATTERED_OFFSETS

# CMP-03 / T-202-01 peak-allocation ceiling (RESEARCH.md "Measured: the
# mechanism works, with enormous margin"): the streaming shape peaked at
# 4231 B clean and 86738 B on the all-differing 512 KiB case; the
# materialised shape (a device-sized offset list -- exactly what D-02
# forbids) peaked at 22571672 B; the tracemalloc noise floor is 88 B. A
# 1 MiB ceiling sits ~12x above the worst measured streaming peak and ~21x
# below the materialised one, so it cannot realistically flake.
PEAK_ALLOCATION_CEILING_BYTES = 1_048_576


def _iter_chunks(
    size: int, chunk_size: int, pattern: str, *, differ_offset: int = 0x400
) -> Iterator[tuple[int, bytes, bytes]]:
    """Yield `(address, expected, actual)` chunk triples for a simulated
    device of `size` bytes, delivered `chunk_size` bytes at a time -- the
    same shape `_main_phase_read_data` hands to a real callback. Only ever
    materialises one chunk's worth of bytes at a time, so the generator
    itself does not contribute a device-sized allocation to a traced peak.

    `pattern` is one of "all_match" (byte-identical), "single_byte" (one
    differing byte at `differ_offset`), "all_differ" (every byte differs)
    or "alternating" (every other byte differs -- the pattern that
    maximises the coalesced range count, per D-16).
    """
    address = 0
    while address < size:
        length = min(chunk_size, size - address)
        expected = bytes((address + i) & 0xFF for i in range(length))
        if pattern == "all_match":
            actual = expected
        elif pattern == "single_byte":
            if address <= differ_offset < address + length:
                mutated = bytearray(expected)
                mutated[differ_offset - address] ^= 0xFF
                actual = bytes(mutated)
            else:
                actual = expected
        elif pattern == "all_differ":
            actual = bytes(b ^ 0xFF for b in expected)
        elif pattern == "alternating":
            actual = bytes(
                (b ^ 0xFF) if (address + i) % 2 == 0 else b
                for i, b in enumerate(expected)
            )
        else:
            raise ValueError(f"unknown pattern {pattern!r}")
        yield address, expected, actual
        address += length


class TestCompareAccumulatorCleanCompare:
    """A byte-identical compare produces zero bad bytes and zero ranges."""

    def test_clean_compare_zero_bad_zero_ranges(self) -> None:
        pattern = bytes((i * 7) & 0xFF for i in range(256))
        acc = CompareAccumulator()
        acc.feed(0, pattern, pattern)
        acc.feed(256, pattern, pattern)

        result = acc.finalise()

        assert result.bad == 0
        assert result.ranges == []
        assert result.extra_ranges == 0
        assert result.extra_bytes == 0
        assert result.compared == 512
        assert not acc.has_mismatch

    def test_clean_compare_total_defaults_to_compared(self) -> None:
        """`total` is not known to the accumulator; `finalise()` defaults it
        to `compared` so a caller that never overrides it still gets an
        internally-consistent result (compared == total on a full clean
        pass)."""
        acc = CompareAccumulator()
        acc.feed(0, b"\x00" * 16, b"\x00" * 16)

        result = acc.finalise()

        assert result.total == result.compared == 16


class TestCompareAccumulatorSingleMismatch:
    """A single differing byte at a known offset produces exactly one
    MismatchRange whose start equals its end and whose count is 1."""

    def test_single_byte_mismatch_produces_one_range(self) -> None:
        expected = bytearray(b"\xff" * 64)
        actual = bytearray(expected)
        actual[10] = 0x5A  # absolute address 10 (chunk starts at 0)

        acc = CompareAccumulator()
        acc.feed(0, bytes(expected), bytes(actual))
        result = acc.finalise()

        assert result.bad == 1
        assert len(result.ranges) == 1
        rng = result.ranges[0]
        assert rng.start == rng.end == 10
        assert rng.count == 1
        assert acc.has_mismatch

    def test_contiguous_mismatches_coalesce_into_one_range(self) -> None:
        expected = bytearray(b"\x00" * 32)
        actual = bytearray(expected)
        for i in range(5, 9):  # addresses 5,6,7,8 -- contiguous
            actual[i] = 0xFF

        acc = CompareAccumulator()
        acc.feed(0, bytes(expected), bytes(actual))
        result = acc.finalise()

        assert result.bad == 4
        assert len(result.ranges) == 1
        rng = result.ranges[0]
        assert (rng.start, rng.end, rng.count) == (5, 8, 4)

    def test_mismatch_across_chunk_boundary_still_coalesces(self) -> None:
        """The open range spans two `feed()` calls -- the accumulator must
        extend it across the chunk boundary, not start a fresh range."""
        acc = CompareAccumulator()
        # Chunk 1: addresses 0..7, mismatch at the last two (6, 7).
        e1 = bytes(8)
        a1 = bytearray(e1)
        a1[6] = 1
        a1[7] = 1
        acc.feed(0, e1, bytes(a1))
        # Chunk 2: addresses 8..15, mismatch continues at the first byte (8).
        e2 = bytes(8)
        a2 = bytearray(e2)
        a2[0] = 1
        acc.feed(8, e2, bytes(a2))

        result = acc.finalise()

        assert result.bad == 3
        assert len(result.ranges) == 1
        assert (result.ranges[0].start, result.ranges[0].end) == (6, 8)

    def test_non_contiguous_mismatches_produce_two_ranges(self) -> None:
        expected = bytearray(b"\x00" * 32)
        actual = bytearray(expected)
        actual[2] = 1
        actual[20] = 1

        acc = CompareAccumulator()
        acc.feed(0, bytes(expected), bytes(actual))
        result = acc.finalise()

        assert result.bad == 2
        assert len(result.ranges) == 2
        assert (result.ranges[0].start, result.ranges[0].end) == (2, 2)
        assert (result.ranges[1].start, result.ranges[1].end) == (20, 20)


class TestCompareAccumulatorRangeCap:
    """D-16: past `max_ranges` retained ranges, further ranges roll into
    `extra_ranges`/`extra_bytes`, computed from running counters, never from
    a list length."""

    def test_default_max_ranges_retains_up_to_the_module_constant(self) -> None:
        acc = CompareAccumulator()
        for offset in range(0, MAX_RETAINED_RANGES * 4, 4):
            expected = bytes(1)
            actual = b"\x01"
            acc.feed(offset, expected, actual)

        result = acc.finalise()

        assert len(result.ranges) == MAX_RETAINED_RANGES
        assert result.extra_ranges == 0

    def test_ranges_beyond_cap_counted_not_retained(self) -> None:
        acc = CompareAccumulator(max_ranges=2)
        # Three isolated single-byte mismatches, each its own range.
        for offset in (0, 10, 20):
            expected = bytes(4)
            actual = bytearray(expected)
            actual[0] = 1
            acc.feed(offset, bytes(expected), bytes(actual))

        result = acc.finalise()

        assert result.bad == 3
        assert len(result.ranges) == 2
        assert result.extra_ranges == 1
        assert result.extra_bytes == 1


class TestRenderCompareLines:
    """D-13's exact one-line-per-range format; D-16's tail line."""

    def test_single_range_exact_text(self) -> None:
        result = CompareResult(
            total=1,
            compared=1,
            compared_start=0x000400,
            compared_end=0x000400,
            bad=1,
            ranges=[MismatchRange(start=0x000400, end=0x000400, count=1)],
            extra_ranges=0,
            extra_bytes=0,
            aborted=False,
        )

        lines = render_compare_lines(result)

        assert lines == ["Mismatch 0x000400-0x000400 (1 bytes)"]

    def test_no_expected_or_actual_value_ever_printed(self) -> None:
        """D-13/D-15: never print a byte value, only start/end/count."""
        result = CompareResult(
            total=2,
            compared=2,
            compared_start=0,
            compared_end=1,
            bad=2,
            ranges=[MismatchRange(start=0, end=1, count=2)],
            extra_ranges=0,
            extra_bytes=0,
            aborted=False,
        )

        lines = render_compare_lines(result)

        assert lines == ["Mismatch 0x000000-0x000001 (2 bytes)"]

    def test_clean_compare_renders_no_lines(self) -> None:
        result = CompareResult(
            total=10,
            compared=10,
            compared_start=0,
            compared_end=9,
            bad=0,
            ranges=[],
            extra_ranges=0,
            extra_bytes=0,
            aborted=False,
        )

        assert render_compare_lines(result) == []

    def test_extra_ranges_tail_line(self) -> None:
        result = CompareResult(
            total=100,
            compared=100,
            compared_start=0,
            compared_end=99,
            bad=10,
            ranges=[MismatchRange(start=0, end=0, count=1)],
            extra_ranges=3,
            extra_bytes=9,
            aborted=False,
        )

        lines = render_compare_lines(result)

        assert len(lines) == 2
        assert lines[1].startswith("…")
        assert "3" in lines[1]
        assert "9" in lines[1]


class TestCompareAccumulatorPeakAllocation:
    """CMP-03 / T-202-01: peak traced allocation stays under
    `PEAK_ALLOCATION_CEILING_BYTES` across all four fault patterns, and
    does not grow when the simulated device size doubles.

    Deviation from the plan's literal "512 KiB" wording, disclosed in
    SUMMARY.md: this class traces at 128 KiB, not 512 KiB, for all four
    patterns. Measured reason -- `tracemalloc`'s own per-allocation tracing
    overhead (not the algorithm; the untraced runtime test two classes
    below proves the algorithm itself takes well under 1s at 512 KiB) made
    a full 512 KiB trace of the all-differing and alternating patterns take
    5-12s depending on machine load, occasionally exceeding this file's own
    <verify>-mandated 10-second-per-test ceiling. 128 KiB reproduces the
    same peak (measured: ~35 KB at 128 KiB vs ~37 KB at 512 KiB for
    all-differ -- a ~6% difference, consistent with `_peak_for` below
    showing the accumulator's footprint does not grow with device size) in
    a stable ~1-2s. `test_peak_allocation_flat_in_device_size` below is
    what actually exercises 512 KiB directly (via the cheap single-byte
    pattern, unaffected by this timing pressure) and proves the peak does
    not grow between 128 KiB and 512 KiB -- so the ceiling assertion at
    128 KiB combined with that flatness proof still covers the full 512 KiB
    claim: 128 KiB's peak sits ~30x under the ceiling, and even doubling it
    (the flatness test's own bound) leaves ~15x of margin.
    """

    @pytest.mark.parametrize(
        "pattern",
        ["all_match", "single_byte", "all_differ", "alternating"],
    )
    def test_peak_allocation_under_ceiling(self, pattern: str) -> None:
        size = 128 * 1024
        chunk_size = 1024
        # Chunks are materialised before tracing starts (mirrors the timing
        # tests' fixture-outside-the-timed-region discipline below): the
        # traced peak should reflect the accumulator's own footprint, not
        # this synthetic generator's chunk construction.
        chunks = list(_iter_chunks(size, chunk_size, pattern))
        acc = CompareAccumulator()

        tracemalloc.start()
        for address, expected, actual in chunks:
            acc.feed(address, expected, actual)
        acc.finalise()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert peak < PEAK_ALLOCATION_CEILING_BYTES

    def test_peak_allocation_flat_in_device_size(self) -> None:
        """A materialised device-sized offset list would grow ~4x when the
        device size quadruples; the streaming shape does not grow at all.
        This turns CMP-03's 'bounded independently of device size' into a
        measured property rather than a single threshold, and it is the
        assertion that survives a machine with different allocator
        behaviour than the one RESEARCH.md measured on."""
        chunk_size = 1024

        def _peak_for(size: int) -> int:
            chunks = list(_iter_chunks(size, chunk_size, "single_byte"))
            acc = CompareAccumulator()
            tracemalloc.start()
            for address, expected, actual in chunks:
                acc.feed(address, expected, actual)
            acc.finalise()
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            return peak

        peak_128k = _peak_for(128 * 1024)
        peak_512k = _peak_for(512 * 1024)

        assert peak_512k <= peak_128k * 2


class TestCompareAccumulatorRuntime:
    """T-202-05 / D-02's unflagged runtime trap: a literal per-byte,
    per-bit reading of D-02 passes every memory assertion above and still
    costs 19.8-21.4s over 512 KiB (RESEARCH.md). These two timing tests
    plus the structural companion below are what make that regression a
    failing test instead of a shipped one."""

    def test_all_matching_512kib_runtime_under_one_second(self) -> None:
        """RESEARCH.md measured 0.002s for the equality fast path (Tier 2)
        and 19.8s for a naive per-byte loop over the same all-matching
        512 KiB input -- the 1.0s bound sits comfortably between the two.
        A failure here does not mean 'the machine was slow'; it means the
        inner loop stopped short-circuiting on a chunk that compared equal
        and descended to per-byte (or per-bit) work instead."""
        size = 512 * 1024
        chunk_size = 1024
        chunks = list(_iter_chunks(size, chunk_size, "all_match"))
        acc = CompareAccumulator()

        start = time.perf_counter()
        for address, expected, actual in chunks:
            acc.feed(address, expected, actual)
        acc.finalise()
        elapsed = time.perf_counter() - start

        assert elapsed < 1.0

    def test_all_differing_512kib_runtime_under_eight_seconds(self) -> None:
        """RESEARCH.md measured 1.9s for the per-chunk offset list with a
        per-bit sum hoisted outside the per-offset loop, and 21.4s for the
        nested per-offset per-bit loop over the same all-differing 512 KiB
        input -- the 8.0s bound sits comfortably between the two. A
        failure here does not mean 'the machine was slow'; it means the
        per-bit sum got nested inside the per-offset loop instead of being
        computed once per bit across all offsets."""
        size = 512 * 1024
        chunk_size = 1024
        chunks = list(_iter_chunks(size, chunk_size, "all_differ"))
        acc = CompareAccumulator()

        start = time.perf_counter()
        for address, expected, actual in chunks:
            acc.feed(address, expected, actual)
        acc.finalise()
        elapsed = time.perf_counter() - start

        assert elapsed < 8.0


def _feed_function_ast() -> ast.FunctionDef:
    """Parse `CompareAccumulator.feed`'s own source into its `ast.FunctionDef`
    node, for the structural fast-path test below. `inspect.getsource`
    returns method-indented source, so it must be dedented before
    `ast.parse` accepts it."""
    source = textwrap.dedent(inspect.getsource(CompareAccumulator.feed))
    module = ast.parse(source)
    func = module.body[0]
    assert isinstance(func, ast.FunctionDef)
    return func


class TestCompareAccumulatorFastPathStructure:
    """A timing test tells you the shape regressed; this one tells you
    where -- it does not depend on machine speed at all, so it catches a
    regression a timing bound might not discriminate on unusually fast
    hardware."""

    def test_fast_path_precedes_per_offset_loop(self) -> None:
        func = _feed_function_ast()

        first_for_lineno: int | None = None
        for node in ast.walk(func):
            if isinstance(node, ast.For):
                if first_for_lineno is None or node.lineno < first_for_lineno:
                    first_for_lineno = node.lineno

        fast_path_lineno: int | None = None
        for stmt in func.body:
            if not isinstance(stmt, ast.If):
                continue
            test = stmt.test
            if not (
                isinstance(test, ast.Compare)
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.Eq)
            ):
                continue
            names = {
                side.id
                for side in (test.left, *test.comparators)
                if isinstance(side, ast.Name)
            }
            if names != {"expected", "actual"}:
                continue
            if any(isinstance(inner, ast.Return) for inner in stmt.body):
                fast_path_lineno = stmt.lineno
                break

        assert fast_path_lineno is not None, (
            "no whole-chunk `expected == actual` comparison with an early "
            "return found in feed()'s top-level body"
        )
        assert first_for_lineno is not None, (
            "no per-offset loop found in feed() to compare the fast path against"
        )
        assert fast_path_lineno < first_for_lineno, (
            "the equality fast path must precede any per-offset loop, or "
            "a clean chunk pays per-offset cost it should have skipped"
        )


class TestCompareAccumulatorAdjacencyAndOverlap:
    """CMP-03/CMP-05: a span straddling a chunk boundary coalesces into one
    range; two spans separated by exactly one matching byte stay two
    ranges; two touching spans merge into one; and no two retained ranges
    ever touch or overlap."""

    def test_span_straddling_chunk_boundary_is_one_range(self) -> None:
        """`eprom_operations.py:905-912`'s chunk loop hands `feed()`
        monotonically increasing absolute addresses -- a span can
        genuinely straddle a chunk boundary, and the open range must
        survive across the two `feed()` calls rather than closing early."""
        acc = CompareAccumulator()
        e1 = bytes(1024)
        a1 = bytearray(e1)
        a1[1020:1024] = b"\x01\x01\x01\x01"  # addresses 1020-1023
        acc.feed(0, e1, bytes(a1))
        e2 = bytes(1024)
        a2 = bytearray(e2)
        a2[0:4] = b"\x01\x01\x01\x01"  # addresses 1024-1027, contiguous
        acc.feed(1024, e2, bytes(a2))

        result = acc.finalise()

        assert len(result.ranges) == 1
        rng = result.ranges[0]
        assert (rng.start, rng.end, rng.count) == (1020, 1027, 8)

    def test_two_spans_separated_by_one_matching_byte_stay_two_ranges(self) -> None:
        expected = bytearray(b"\x00" * 10)
        actual = bytearray(expected)
        actual[3] = 1
        actual[4] = 1
        # byte 5 matches -- the one separating byte.
        actual[6] = 1
        actual[7] = 1

        acc = CompareAccumulator()
        acc.feed(0, bytes(expected), bytes(actual))
        result = acc.finalise()

        assert len(result.ranges) == 2
        assert (result.ranges[0].start, result.ranges[0].end) == (3, 4)
        assert (result.ranges[1].start, result.ranges[1].end) == (6, 7)

    def test_two_touching_spans_merge_into_one_range(self) -> None:
        """Two runs of mismatches with no matching byte between them must
        coalesce into a single `MismatchRange`, not two."""
        expected = bytearray(b"\x00" * 10)
        actual = bytearray(expected)
        actual[3] = 1
        actual[4] = 1
        actual[5] = 1  # touches -- no matching byte between 4 and 5
        actual[6] = 1

        acc = CompareAccumulator()
        acc.feed(0, bytes(expected), bytes(actual))
        result = acc.finalise()

        assert len(result.ranges) == 1
        assert (
            result.ranges[0].start,
            result.ranges[0].end,
            result.ranges[0].count,
        ) == (
            3,
            6,
            4,
        )

    def test_no_two_retained_ranges_touch_or_overlap(self) -> None:
        acc = CompareAccumulator()
        for offset in range(0, MAX_RETAINED_RANGES * 8, 8):
            expected = bytes(4)
            actual = bytearray(expected)
            actual[0] = 1
            actual[1] = 1
            acc.feed(offset, bytes(expected), bytes(actual))

        result = acc.finalise()

        for left, right in zip(result.ranges, result.ranges[1:]):
            assert left.end + 1 < right.start


class TestCompareAccumulatorOrdering:
    """CMP-03/CMP-05: `ranges` is sorted ascending by `start`, and the same
    input produces the same ordering on every run."""

    def test_ranges_sorted_ascending_by_start(self) -> None:
        expected = bytearray(b"\x00" * 32)
        actual = bytearray(expected)
        actual[20] = 1
        actual[2] = 1
        actual[27] = 1

        acc = CompareAccumulator()
        acc.feed(0, bytes(expected), bytes(actual))
        result = acc.finalise()

        starts = [r.start for r in result.ranges]
        assert starts == sorted(starts)
        assert len(result.ranges) == 3

    def test_identical_feed_sequence_produces_equal_range_lists(self) -> None:
        def _drive() -> CompareResult:
            acc = CompareAccumulator()
            for address, expected, actual in _iter_chunks(
                64 * 1024, 1024, "alternating"
            ):
                acc.feed(address, expected, actual)
            return acc.finalise()

        result_a = _drive()
        result_b = _drive()

        assert result_a.ranges == result_b.ranges
        assert result_a == result_b


class TestCompareAccumulatorEmptyAndSingleByte:
    """CMP-03 empty case: a zero-length read-back compares as perfect
    equality byte-for-byte -- only a length gate (owned by the caller, not
    this module) distinguishes it from a genuine clean pass. The engine's
    job is to report that honestly (a well-formed, zero-valued
    `CompareResult`) rather than defensively raising or inventing a
    nonzero total."""

    def test_no_feed_calls_returns_well_formed_empty_result(self) -> None:
        acc = CompareAccumulator()

        result = acc.finalise()

        assert result.total == 0
        assert result.compared == 0
        assert result.bad == 0
        assert result.ranges == []
        assert result.extra_ranges == 0
        assert result.extra_bytes == 0
        assert result.compared_end == result.compared_start - 1

    def test_single_matching_byte_chunk(self) -> None:
        acc = CompareAccumulator()
        acc.feed(0x100, b"\x42", b"\x42")

        result = acc.finalise()

        assert result.compared == 1
        assert result.bad == 0
        assert result.ranges == []

    def test_single_differing_byte_chunk(self) -> None:
        acc = CompareAccumulator()
        acc.feed(0x100, b"\x42", b"\x43")

        result = acc.finalise()

        assert result.compared == 1
        assert result.bad == 1
        assert len(result.ranges) == 1
        assert (
            result.ranges[0].start,
            result.ranges[0].end,
            result.ranges[0].count,
        ) == (
            0x100,
            0x100,
            1,
        )


class TestCompareAccumulatorRangeCapBoundary:
    """D-16: the retained-range cap at exactly `MAX_RETAINED_RANGES - 1`,
    `MAX_RETAINED_RANGES` and `MAX_RETAINED_RANGES + 1` coalesced ranges,
    using the module's real default cap -- unlike
    `TestCompareAccumulatorRangeCap` above, which overrides `max_ranges`
    to a small custom value."""

    @staticmethod
    def _feed_n_isolated_ranges(acc: CompareAccumulator, n: int) -> None:
        for i in range(n):
            offset = i * 4
            expected = bytes(2)
            actual = bytearray(expected)
            actual[0] = 1
            acc.feed(offset, bytes(expected), bytes(actual))

    def test_one_below_cap_retains_everything(self) -> None:
        acc = CompareAccumulator()
        self._feed_n_isolated_ranges(acc, MAX_RETAINED_RANGES - 1)
        result = acc.finalise()

        assert len(result.ranges) == MAX_RETAINED_RANGES - 1
        assert result.extra_ranges == 0
        assert result.extra_bytes == 0

    def test_exactly_at_cap_retains_everything(self) -> None:
        acc = CompareAccumulator()
        self._feed_n_isolated_ranges(acc, MAX_RETAINED_RANGES)
        result = acc.finalise()

        assert len(result.ranges) == MAX_RETAINED_RANGES
        assert result.extra_ranges == 0
        assert result.extra_bytes == 0

    def test_one_beyond_cap_retains_exactly_the_cap(self) -> None:
        acc = CompareAccumulator()
        self._feed_n_isolated_ranges(acc, MAX_RETAINED_RANGES + 1)
        result = acc.finalise()

        assert len(result.ranges) == MAX_RETAINED_RANGES
        assert result.extra_ranges == 1
        assert result.extra_bytes == 1  # the one dropped range is 1 byte wide


class TestCompareAccumulatorAlternatingPrecision:
    """D-16's stated worst case: the alternating pattern over a 512 KiB
    device produces ~262144 coalesced single-byte ranges. The retained
    list caps at `MAX_RETAINED_RANGES` but the tail counters must stay
    exact -- the standing prohibition this plan carries (`must_haves.
    prohibitions`): the cap may bound what is SHOWN, never what is
    COUNTED."""

    def test_extra_ranges_and_extra_bytes_exact_for_512kib_alternating(self) -> None:
        size = 512 * 1024
        chunk_size = 1024
        chunks = list(_iter_chunks(size, chunk_size, "alternating"))
        acc = CompareAccumulator()

        for address, expected, actual in chunks:
            acc.feed(address, expected, actual)
        result = acc.finalise()

        # Every even absolute address differs, every odd one matches, so
        # every mismatch is an isolated 1-byte range: the true range count
        # is exactly half the device size.
        true_total_range_count = size // 2

        assert len(result.ranges) == MAX_RETAINED_RANGES
        assert result.extra_ranges + MAX_RETAINED_RANGES == true_total_range_count
        assert result.extra_bytes + sum(r.count for r in result.ranges) == result.bad
        assert isinstance(result.bad, int)
        assert all(isinstance(r.count, int) for r in result.ranges)


class TestClassifyStreamed:
    """202-03 (D-02/D-03): `classify_streamed`'s own direct unit coverage,
    one test per bucket plus the two traps this task's acceptance criteria
    name explicitly (the 256/257 clustering boundary, and the tie-break to
    the lowest bit index). The D-03 EQUALITY corpus against a transcribed
    batch reference lives separately, in `TestClassifyStreamedCorpus` below
    (Task 2) -- these tests instead pin `classify_streamed`'s own behaviour
    in isolation, independent of that comparison."""

    def test_blank_contact_bucket(self) -> None:
        length = 256
        expected = bytes((i * 7) & 0xFF for i in range(length))
        actual = b"\xff" * length
        acc = CompareAccumulator()
        acc.feed(0, expected, actual)
        fp = classify_streamed(acc.finalise())

        assert fp.classification == FP_BLANK_CONTACT
        assert fp.total == length
        assert fp.evidence["ff_ratio"] >= 0.98

    def test_address_line_bucket(self) -> None:
        length = 0x400
        expected = bytes(length)
        actual = bytearray(expected)
        for i in range(length):
            if i & 0x100:
                actual[i] = 0x01
        acc = CompareAccumulator()
        acc.feed(0, bytes(expected), bytes(actual))
        fp = classify_streamed(acc.finalise())

        assert fp.classification == FP_ADDRESS_LINE
        assert fp.evidence["suspected_line"] == 8

    def test_match_bucket(self) -> None:
        pattern = bytes((i * 3) & 0xFF for i in range(64))
        acc = CompareAccumulator()
        acc.feed(0, pattern, pattern)
        fp = classify_streamed(acc.finalise())

        assert fp.classification == FP_MATCH
        assert fp.bad == 0

    def test_transport_bucket(self) -> None:
        # FP_TRANSPORT is unreachable through the real `verify`/`dev test`
        # call path today (`repeat_divergent=True` never flows from a
        # single-run compare) -- filed as CMP-F2, deliberately DEFERRED, not
        # fixed here. Constructed directly, as the flagged measured project
        # fact requires, rather than through any production call site.
        length = 1024
        expected = bytes((i * 11) & 0xFF for i in range(length))
        actual = bytearray(expected)
        for o in _SCATTERED_TEST_OFFSETS:
            actual[o] ^= 0x01
        acc = CompareAccumulator()
        acc.feed(0, expected, bytes(actual))
        fp = classify_streamed(acc.finalise(), repeat_divergent=True)

        assert fp.classification == FP_TRANSPORT
        assert fp.evidence["repeat_divergent"] is True

    def test_indeterminate_bucket(self) -> None:
        length = 1024
        expected = bytes((i * 11) & 0xFF for i in range(length))
        actual = bytearray(expected)
        for o in _SCATTERED_TEST_OFFSETS:
            actual[o] ^= 0x01
        acc = CompareAccumulator()
        acc.feed(0, expected, bytes(actual))
        fp = classify_streamed(acc.finalise(), repeat_divergent=False)

        assert fp.classification == FP_INDETERMINATE

    def test_zero_length_is_match_not_raise(self) -> None:
        acc = CompareAccumulator()
        fp = classify_streamed(acc.finalise())

        assert fp.total == 0
        assert fp.bad == 0
        assert fp.classification == FP_MATCH
        assert fp.evidence["ff_ratio"] == 0.0

    def test_evidence_key_set_non_address_line_path(self) -> None:
        pattern = bytes(64)
        acc = CompareAccumulator()
        acc.feed(0, pattern, pattern)
        fp = classify_streamed(acc.finalise())

        assert set(fp.evidence) == {
            "ff_ratio",
            "repeat_divergent",
            "first_offset",
            "bit_clustering",
        }

    def test_evidence_key_set_address_line_path_has_two_more_keys(self) -> None:
        length = 0x400
        expected = bytes(length)
        actual = bytearray(expected)
        for i in range(length):
            if i & 0x100:
                actual[i] = 0x01
        acc = CompareAccumulator()
        acc.feed(0, bytes(expected), bytes(actual))
        fp = classify_streamed(acc.finalise())

        assert set(fp.evidence) == {
            "ff_ratio",
            "repeat_divergent",
            "first_offset",
            "bit_clustering",
            "suspected_line",
            "cluster_score",
        }

    def test_256_byte_region_bit_clustering_stays_empty(self) -> None:
        """`cmp_len > (1 << 8)` is a strict `>` -- exactly 256 never enters
        clustering. `tests/test_chip_test_sdp_leg.py` already depends on
        this for the batch classifier; this pins it for the streamed one."""
        length = 256
        expected = bytes(length)
        actual = bytearray(expected)
        actual[0] = 0x01
        acc = CompareAccumulator()
        acc.feed(0, bytes(expected), bytes(actual))
        fp = classify_streamed(acc.finalise())

        assert fp.evidence["bit_clustering"] == {}

    def test_257_byte_region_bit_clustering_nonempty(self) -> None:
        length = 257
        expected = bytes(length)
        actual = bytearray(expected)
        actual[0] = 0x01
        acc = CompareAccumulator()
        acc.feed(0, bytes(expected), bytes(actual))
        fp = classify_streamed(acc.finalise())

        assert fp.evidence["bit_clustering"] != {}

    def test_tie_between_two_bit_indices_resolves_to_the_lower_one(self) -> None:
        """Every mismatch sits where BOTH bit 8 (0x100) and bit 9 (0x200)
        are set, so both candidate bits score 1.0 -- a genuine tie.
        `classify_streamed` must scan ascending and keep only a STRICTLY
        greater score, so bit 8 wins."""
        length = 1024
        expected = bytes(length)
        actual = bytearray(expected)
        for addr in range(0x300, 0x400):  # bits 8 and 9 both set here
            actual[addr] = 0x01
        acc = CompareAccumulator()
        acc.feed(0, bytes(expected), bytes(actual))
        fp = classify_streamed(acc.finalise())

        assert fp.classification == FP_ADDRESS_LINE
        assert fp.evidence["suspected_line"] == 8


_SCATTERED_TEST_OFFSETS = [
    3,
    17,
    40,
    77,
    101,
    130,
    190,
    220,
    300,
    350,
    410,
    470,
    500,
    550,
    600,
    650,
]
"""16 hand-picked offsets in a 1024-byte region, none clustered on any one
high bit (mirrors `tests/test_chip_test.py`'s own `_SCATTERED_OFFSETS`,
defined independently here so this module keeps no dependency on
`chip_test`'s test module -- verified: max clustering ~0.81, below the 0.9
threshold)."""


class TestDiffSummary:
    """202-03 (D-02): `diff_summary` replaces `chip_test._diff_offsets` --
    same four values, minus the materialised offset list."""

    def test_equal_arrays_zero_bad(self) -> None:
        a = bytes([1, 2, 3, 4])
        b = bytes([1, 2, 3, 4])

        summary = diff_summary(a, b)

        assert summary.cmp_len == 4
        assert summary.bad == 0
        assert summary.pct == 0.0
        assert summary.first_offset is None

    def test_known_positions(self) -> None:
        a = bytes(8)
        b = bytearray(a)
        b[2] = 0xFF
        b[5] = 0xFF

        summary = diff_summary(a, bytes(b))

        assert summary.cmp_len == 8
        assert summary.bad == 2
        assert summary.first_offset == 2
        assert summary.pct == 100.0 * 2 / 8

    def test_unequal_length_compares_common_prefix_and_never_raises(self) -> None:
        a = bytes([1, 2, 3, 4, 5])
        b = bytes([1, 2, 9])

        summary = diff_summary(a, b)

        assert summary.cmp_len == 3
        assert summary.bad == 1
        assert summary.first_offset == 2

    def test_zero_length_returns_zero_total_and_never_raises(self) -> None:
        summary = diff_summary(b"", b"")

        assert summary.cmp_len == 0
        assert summary.bad == 0
        assert summary.pct == 0.0
        assert summary.first_offset is None

    def test_result_carries_a_bad_count_not_an_offset_list(self) -> None:
        """D-02: `diff_summary` must never expose a materialised list of
        every mismatching offset -- `DiffSummary` has no such field."""
        summary = diff_summary(b"\x00" * 4, b"\xff" * 4)

        assert not hasattr(summary, "diff_offsets")
        assert summary.bad == 4


def _batch_classify_fingerprint_reference(
    expected: bytes,
    actual: bytes,
    *,
    repeat_divergent: bool | None = None,
    addr_base: int = 0,
) -> Fingerprint:
    """A transcription of `chip_test.classify_fingerprint`'s PRE-refactor
    batch body (as it stood before 202-03 D-02), held here so the D-03
    corpus below compares two INDEPENDENT computations rather than one
    against itself. Deliberately duplicated, not imported or delegated --
    delegating to `classify_streamed` here would make the whole corpus
    vacuous (both sides would be the same code)."""
    cmp_len = min(len(expected), len(actual))
    diff_offsets = [o for o in range(cmp_len) if expected[o] != actual[o]]
    bad = len(diff_offsets)
    bad_pct = 100.0 * bad / cmp_len if cmp_len else 0.0
    first_offset = diff_offsets[0] if diff_offsets else None

    ff_count = sum(1 for b in actual[:cmp_len] if b == 0xFF)
    ff_ratio = (ff_count / cmp_len) if cmp_len else 0.0

    evidence: dict = {
        "ff_ratio": ff_ratio,
        "repeat_divergent": repeat_divergent,
        "first_offset": first_offset,
        "bit_clustering": {},
    }

    if ff_ratio >= 0.98:
        return Fingerprint(
            total=cmp_len,
            bad=bad,
            bad_pct=bad_pct,
            classification=FP_BLANK_CONTACT,
            evidence=evidence,
        )

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

    if suspected_line is not None and best_score >= 0.9:
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

    if repeat_divergent is True:
        return Fingerprint(
            total=cmp_len,
            bad=bad,
            bad_pct=bad_pct,
            classification=FP_TRANSPORT,
            evidence=evidence,
        )

    return Fingerprint(
        total=cmp_len,
        bad=bad,
        bad_pct=bad_pct,
        classification=FP_INDETERMINATE,
        evidence=evidence,
    )


def _corpus_pattern(length: int, multiplier: int = 7) -> bytes:
    """A cheap non-0xFF, non-address-derived byte pattern for corpus rows
    that don't care about content, only about mismatch POSITIONS -- kept
    local so this file adds no dependency on `chip_test.generate_pattern`."""
    return bytes((i * multiplier) & 0xFF for i in range(length))


# D-03's corpus: (name, expected, actual, kwargs) rows, each with a comment
# recording why it is in the table. Spans all five buckets (rows 1-6) and
# the four traps RESEARCH.md named (rows 7-12): the 256/257 clustering
# boundary, a bit-index tie, and zero-/unequal-length inputs.
_CORPUS: list[tuple[str, bytes, bytes, dict]] = []

# 1. blank/contact: read-back is near-all 0xFF despite a non-blank expected
# pattern -- every byte "differs" from `expected`, but the ff_ratio gate
# fires before that mismatch count is ever consulted.
_near_all_ff_expected = _corpus_pattern(256)
_CORPUS.append(("near_all_ff_blank_contact", _near_all_ff_expected, b"\xff" * 256, {}))

# 2. address-line, addr_base=0: a clean power-of-two high-bit clustering
# fault -- the baseline Pitfall-3 comparison pairs with row 3 below.
_al0_len = 0x400
_al0_expected = bytes(_al0_len)
_al0_actual = bytearray(_al0_expected)
for _i in range(_al0_len):
    if _i & 0x100:
        _al0_actual[_i] = 0x01
_CORPUS.append(
    ("address_line_addr_base_zero", _al0_expected, bytes(_al0_actual), {"addr_base": 0})
)

# 3. address-line, addr_base=0x8000: same fault pattern, non-zero base --
# proves clustering keys on the ABSOLUTE address (addr_base + offset), not
# the raw offset (Pitfall 3), and that the candidate-bit RANGE bound stays
# keyed to the compared LENGTH, not the addr_base-inflated absolute address.
_al_base = 0x8000
_al1_len = 0x400
_al1_expected = bytes(_al1_len)
_al1_actual = bytearray(_al1_expected)
for _i in range(_al1_len):
    if (_al_base + _i) & 0x100:
        _al1_actual[_i] = 0x01
_CORPUS.append(
    (
        "address_line_addr_base_nonzero",
        _al1_expected,
        bytes(_al1_actual),
        {"addr_base": _al_base},
    )
)

# 4/5. Reuses the suite's one hand-tuned scattered-offset list (measured
# clustering ~0.81, below the 0.9 threshold): repeat_divergent=True ->
# transport, repeat_divergent=False -> indeterminate. Same fault pattern,
# opposite verdicts -- pins that the divergent-across-runs SIGNAL, not the
# byte pattern, is what discriminates buckets 4 and 5.
_scattered_len = 1024
_scattered_expected = _corpus_pattern(_scattered_len, multiplier=11)
_scattered_actual = bytearray(_scattered_expected)
for _o in _SCATTERED_OFFSETS:
    _scattered_actual[_o] ^= 0x01
_CORPUS.append(
    (
        "scattered_transport",
        _scattered_expected,
        bytes(_scattered_actual),
        {"repeat_divergent": True},
    )
)
_CORPUS.append(
    (
        "scattered_indeterminate",
        _scattered_expected,
        bytes(_scattered_actual),
        {"repeat_divergent": False},
    )
)

# 6. match: a byte-identical compare, zero mismatches.
_clean_pattern = _corpus_pattern(128, multiplier=3)
_CORPUS.append(("clean_match", _clean_pattern, _clean_pattern, {}))

# 7. A compared length that is not a power of two (300), with several
# scattered mismatches -- exercises the clustering range at a length whose
# bit_length() boundary doesn't line up with a round number.
_npot_len = 300
_npot_expected = _corpus_pattern(_npot_len, multiplier=5)
_npot_actual = bytearray(_npot_expected)
for _o in (10, 50, 90, 130, 170, 210, 250, 290):
    _npot_actual[_o] ^= 0x01
_CORPUS.append(("non_power_of_two_length", _npot_expected, bytes(_npot_actual), {}))

# 8. Exactly 256 bytes: `cmp_len > (1 << 8)` is a strict `>`, so 256 never
# enters clustering at all -- `tests/test_chip_test_sdp_leg.py` already
# depends on this for the batch classifier.
_len256_expected = bytes(256)
_len256_actual = bytearray(_len256_expected)
_len256_actual[0] = 0x01
_CORPUS.append(("exactly_256_bytes", _len256_expected, bytes(_len256_actual), {}))

# 9. Exactly 257 bytes: one byte over the boundary, clustering now runs.
_len257_expected = bytes(257)
_len257_actual = bytearray(_len257_expected)
_len257_actual[0] = 0x01
_CORPUS.append(("exactly_257_bytes", _len257_expected, bytes(_len257_actual), {}))

# 10. A tie between two candidate bit indices (8 and 9): every mismatch
# sits where BOTH bits are set, so both score 1.0 -- the classifier must
# resolve to the LOWER index (8).
_tie_len = 1024
_tie_expected = bytes(_tie_len)
_tie_actual = bytearray(_tie_expected)
for _addr in range(0x300, 0x400):
    _tie_actual[_addr] = 0x01
_CORPUS.append(("bit_index_tie", _tie_expected, bytes(_tie_actual), {}))

# 11. Zero-length: both buffers empty -- must return a zero total, zero bad
# count and zero blank ratio, never raise.
_CORPUS.append(("zero_length", b"", b"", {}))

# 12. Unequal-length: compares over the common prefix only, never raises.
_CORPUS.append(("unequal_length", b"\x01\x02\x03\x04\x05", b"\x01\x02\x09", {}))


class TestClassifyFingerprintCorpus:
    """D-03: the streamed path (`chip_test.classify_fingerprint`, delegating
    to `classify_streamed`) and an independently transcribed batch
    reference must agree on the WHOLE `Fingerprint` object, not a
    field-by-field subset -- a whole-object comparison catches a field the
    refactor forgot to populate, which a hand-written field list would also
    forget to check."""

    @pytest.mark.parametrize(
        "name,expected,actual,kwargs",
        _CORPUS,
        ids=[row[0] for row in _CORPUS],
    )
    def test_streamed_and_batch_reference_agree(
        self, name: str, expected: bytes, actual: bytes, kwargs: dict
    ) -> None:
        reference = _batch_classify_fingerprint_reference(expected, actual, **kwargs)
        streamed = classify_fingerprint(expected, actual, **kwargs)

        assert streamed == reference, (
            f"corpus row {name!r}: classify_fingerprint (streamed, delegating) "
            f"disagrees with the independently transcribed batch reference"
        )

    def test_corpus_covers_all_five_buckets(self) -> None:
        buckets = {
            _batch_classify_fingerprint_reference(
                expected, actual, **kwargs
            ).classification
            for _name, expected, actual, kwargs in _CORPUS
        }

        assert buckets == {
            FP_BLANK_CONTACT,
            FP_ADDRESS_LINE,
            FP_MATCH,
            FP_TRANSPORT,
            FP_INDETERMINATE,
        }

    def test_corpus_has_at_least_twelve_rows(self) -> None:
        assert len(_CORPUS) >= 12
