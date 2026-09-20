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
    """CMP-03 / T-202-01: peak traced allocation for a 512 KiB compare stays
    under `PEAK_ALLOCATION_CEILING_BYTES` across all four fault patterns,
    and does not grow when the simulated device size doubles."""

    @pytest.mark.parametrize(
        "pattern",
        ["all_match", "single_byte", "all_differ", "alternating"],
    )
    def test_peak_allocation_under_ceiling(self, pattern: str) -> None:
        size = 512 * 1024
        chunk_size = 1024
        acc = CompareAccumulator()

        tracemalloc.start()
        for address, expected, actual in _iter_chunks(size, chunk_size, pattern):
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
            acc = CompareAccumulator()
            tracemalloc.start()
            for address, expected, actual in _iter_chunks(
                size, chunk_size, "single_byte"
            ):
                acc.feed(address, expected, actual)
            acc.finalise()
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            return peak

        peak_128k = _peak_for(128 * 1024)
        peak_512k = _peak_for(512 * 1024)

        assert peak_512k <= peak_128k * 2
