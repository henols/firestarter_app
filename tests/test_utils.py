"""Phase 42 / ERR-03 fallback coverage lift for ``firestarter.utils`` (D-14
fallback per CONTEXT). Small surface, easy targeted lifts to clear the 70%
floor.
"""

import pytest

from firestarter.utils import (
    extract_hex_to_decimal,
    format_size,
    is_valid_hex_string,
    time_formatter,
)


@pytest.mark.parametrize(
    "s, expected",
    [
        ("Error at 0x1A2B3C", 0x1A2B3C),
        ("0xff", 0xFF),
        ("prefix 0X10 suffix", 0x10),
        ("no hex here", None),
        ("", None),
    ],
)
def test_extract_hex_to_decimal(s: str, expected) -> None:
    assert extract_hex_to_decimal(s) == expected


@pytest.mark.parametrize(
    "s, expected",
    [
        ("0x1A2B3C", True),
        ("0X10", True),
        ("0xff", True),
        ("123ABC", False),
        ("0xZZ", False),
        ("", False),
    ],
)
def test_is_valid_hex_string(s: str, expected: bool) -> None:
    assert is_valid_hex_string(s) is expected


@pytest.mark.parametrize(
    "size, expected_unit",
    [
        (1023, "B"),
        (1024, "KB"),
        (1024 * 1024, "MB"),
        (1024 * 1024 * 1024, "GB"),
    ],
)
def test_format_size_picks_correct_unit(size: int, expected_unit: str) -> None:
    out = format_size(size)
    assert out is not None
    assert expected_unit in out


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0, "0s"),
        (45, "45s"),
        (60, "1m 0s"),
        (65, "1m 5s"),
        (3600, "1h 0m 0s"),
        (3665, "1h 1m 5s"),
    ],
)
def test_time_formatter(seconds: int, expected: str) -> None:
    assert time_formatter(seconds) == expected
