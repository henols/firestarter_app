"""Phase 42 / ERR-03 fallback coverage lift for ``firestarter.utils`` (D-14
fallback per CONTEXT). Small surface, easy targeted lifts to clear the 70%
floor.
"""

import pytest

from firestarter.utils import extract_hex_to_decimal, print_to


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


def test_print_to_uses_the_console_when_given(capsys) -> None:
    printed = []

    class _Console:
        def print(self, msg: str) -> None:
            printed.append(msg)

    print_to("hello", console=_Console())
    assert printed == ["hello"]
    assert capsys.readouterr().out == ""


def test_print_to_falls_back_to_stdout(capsys) -> None:
    print_to("hello")
    assert capsys.readouterr().out == "hello\n"
