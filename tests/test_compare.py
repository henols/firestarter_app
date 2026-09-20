"""Tests for `firestarter/compare.py` -- the 202-01 streaming comparison
engine (D-01/D-02/D-04/D-05/D-13/D-16).

Drives `CompareAccumulator` directly from a synthetic generator of
(address, bytes) chunk pairs -- no operator, no serial, no bench hardware.
This is deliberate: the engine is stdlib-only (see the module's own
import-set invariant, separately pinned by
`test_eprom_operations.py::TestVerifyEpromHostSideRead`'s AST check), so its
own unit tests must not need anything beyond that either.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import time
import tracemalloc
from collections.abc import Iterator

import pytest

from firestarter.compare import (
    MAX_RETAINED_RANGES,
    CompareAccumulator,
    CompareResult,
    MismatchRange,
    render_compare_lines,
)

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
