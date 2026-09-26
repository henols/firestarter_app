"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 38 — address_parser unit tests (STRUCT-03).

Covers parse_address and parse_size: hex (0x prefix), decimal, None input,
and invalid inputs that must raise ValueError.
"""

import pytest

from firestarter.address_parser import parse_address, parse_size


class TestParseAddress:
    def test_hex_0x_prefix(self):
        assert parse_address("0x10000") == 65536

    def test_hex_uppercase_prefix(self):
        assert parse_address("0X1A2B") == 6699

    def test_decimal(self):
        assert parse_address("512") == 512

    def test_none_input(self):
        assert parse_address(None) is None

    def test_invalid_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_address("not_a_number")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_address("")


class TestParseSize:
    def test_hex(self):
        assert parse_size("0x8000") == 32768

    def test_decimal(self):
        assert parse_size("1024") == 1024

    def test_none_input(self):
        assert parse_size(None) is None

    def test_invalid_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_size("abc")
