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

from firestarter.compare import (
    MAX_RETAINED_RANGES,
    CompareAccumulator,
    CompareResult,
    MismatchRange,
    render_compare_lines,
)


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
