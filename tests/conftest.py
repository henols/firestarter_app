"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Shared pytest fixtures for the firestarter_app test suite.

This file is the host sub-repo's pytest entry point (first-ever pytest
infrastructure landed by Phase 6 Plan 03). It exposes:

    MAGIC_PREAMBLE_REF  — independent 4-byte magic preamble reference.
    _ref_crc8_ccitt     — table-free CRC8 reference (poly 0x07, seed 0x00).
    build_frame         — helper that assembles an ID-encoded wire frame.
    fake_serial         — fixture: BytesIO-backed serial port stand-in.
    make_comm           — fixture: factory for a SerialCommunicator bypassing
                          real serial I/O (uses __new__ + injected fake serial).

The reference CRC implementation here is deliberately table-free (the
production code in firestarter.serial_comm uses a 256-byte lookup table).
A regression that mutates the production table off-spec — different
polynomial, wrong seed, accidental reflection — will mismatch this
reference and fail the test suite.
"""

import io
import struct

import pytest

# Module-level reference constants — independent of firestarter.serial_comm
# so tests do not pass tautologically on a bug in the production constant.
MAGIC_PREAMBLE_REF: bytes = b"\xaa\x55\xaa\x55"


def _ref_crc8_ccitt(data: bytes) -> int:
    """Reference CRC8 — poly 0x07, seed 0x00, no reflection, no final XOR.

    Table-FREE so tests catch a regression in the production lookup table.
    """
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def build_frame(msg_id: int, params: bytes) -> bytes:
    """Assemble a wire frame: magic | len_u16 | id | params | crc | 0x0A.

    `len` (u16, big-endian) counts (id + params + crc) per Phase 8 W-04.
    The trailing 0x0A is a re-sync anchor (not a delimiter — length is
    authoritative).
    """
    body = bytes([msg_id]) + params
    crc = _ref_crc8_ccitt(body)
    length = len(body) + 1  # id + params + crc
    return MAGIC_PREAMBLE_REF + struct.pack(">H", length) + body + bytes([crc, 0x0A])


class _FakeSerial:
    """BytesIO-backed stand-in for a `serial.Serial` instance.

    Implements only the surface that `SerialCommunicator._read_and_parse_lines`
    consumes: `read(n)` returning up to n bytes (b'' on empty — matches pyserial
    timeout-empty semantics), `is_open`, `in_waiting`, `port`, `timeout`,
    `write(...)`, `flush()`, and `close()`.
    """

    def __init__(self) -> None:
        self._buf = io.BytesIO()
        self._read_pos = 0
        self._write_pos = 0
        self.is_open = True
        self.port = "/dev/null"
        self.timeout = 0.1

    # --- read side ---
    def read(self, n: int = 1) -> bytes:
        self._buf.seek(self._read_pos)
        data = self._buf.read(n)
        self._read_pos = self._buf.tell()
        return data

    def readline(self) -> bytes:
        self._buf.seek(self._read_pos)
        data = self._buf.readline()
        self._read_pos = self._buf.tell()
        return data

    @property
    def in_waiting(self) -> int:
        end = self._write_pos
        return max(0, end - self._read_pos)

    # --- write side (unused by decoder tests, but kept for completeness) ---
    def write(self, data: bytes) -> int:
        self._buf.seek(self._write_pos)
        n = self._buf.write(data)
        self._write_pos = self._buf.tell()
        return n

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False

    # --- test-side helper ---
    def feed(self, data: bytes) -> None:
        """Append bytes to the readable buffer (test-side injection)."""
        self._buf.seek(self._write_pos)
        self._buf.write(data)
        self._write_pos = self._buf.tell()


@pytest.fixture
def fake_serial() -> _FakeSerial:
    """Return a fresh BytesIO-backed fake serial port."""
    return _FakeSerial()


@pytest.fixture
def make_comm(fake_serial):
    """Factory: build a SerialCommunicator wired to the fake serial port.

    Uses `__new__` to bypass `__init__` (which would try to open a real
    serial.Serial). Per PATTERNS §"firestarter_app/tests/test_decoder.py".
    """
    from firestarter.serial_comm import SerialCommunicator

    def _factory():
        instance = SerialCommunicator.__new__(SerialCommunicator)
        instance.connection = fake_serial
        instance.port_name = "/dev/null"
        instance.baud_rate = 250000
        instance.timeout = 0.1
        instance.programmer_info = None
        # Phase-53: mirror SerialCommunicator.__init__ attribute (T-53-03 default)
        instance._fault_inject_outgoing = None
        # Phase-53: firmware-advertised DATA_BUFFER_SIZE (None until probed)
        instance.firmware_buffer_size = None
        # Phase-54 (EVEN-01): firmware-advertised MAIN-path decode capacity (None until probed)
        instance.firmware_max_chunk = None
        return instance

    return _factory
