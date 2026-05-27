"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Wire-frame primitives: CRC8-CCITT, parameter decoding, and structured
response types. Stdlib + typing only — no package-internal imports.
"""

import struct
from collections import namedtuple
from typing import Any, Tuple  # noqa: UP035

# Define a structured object for responses to improve clarity over tuples.
# `payload` carries raw bytes for MSG_DATA_CHUNK frames (W-04); None otherwise.
Response = namedtuple("Response", ["type", "message", "payload"], defaults=[None])

# Phase 6: ID-encoded wire frame primitives. MAGIC_PREAMBLE locked by
# CONTEXT §D-02; LogMessage is the decoded-frame value type per D-06.
# `payload` carries raw bytes for MSG_DATA_CHUNK (W-04); None for all others.
LogMessage = namedtuple(
    "LogMessage", ["severity", "text", "id", "payload"], defaults=[None]
)
MAGIC_PREAMBLE: bytes = b"\xaa\x55\xaa\x55"


def _build_crc8_table() -> bytes:
    """Precompute the 256-byte CRC8-CCITT lookup table.

    Algorithm pinned by CONTEXT §D-03: polynomial 0x07, seed 0x00,
    no reflection, no final XOR. Same algorithm the firmware Unity suite
    asserts (firestarter test_messages/test_rurp_log_id.cpp).
    """
    table = bytearray(256)
    for byte in range(256):
        crc = byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
        table[byte] = crc
    return bytes(table)


_CRC8_CCITT_TABLE: bytes = _build_crc8_table()


def _crc8_ccitt(data: bytes) -> int:
    """Compute CRC8-CCITT (poly 0x07, seed 0x00) over `data` via lookup table."""
    crc = 0
    for byte in data:
        crc = _CRC8_CCITT_TABLE[crc ^ byte]
    return crc


def _decode_param(ptype: str, buf: bytes, cursor: int) -> Tuple[Any, int]:  # noqa: UP006
    """Decode one MSB-first parameter starting at `buf[cursor]`.

    Returns `(value, new_cursor)`. Raises ValueError on unknown ptype or
    out-of-range cursor.

    Param types match the canonical catalog grammar (see catalog/codegen.py
    validator rule 5): u8, i8, u16, i16, u24, u32, i32, ascii_str.

    ascii_str is a 1-byte length prefix followed by N data bytes; decoded
    with `errors='replace'` so a tampered/truncated string surfaces visibly
    rather than crashing the read loop.
    """
    if ptype == "u8":
        return buf[cursor], cursor + 1
    if ptype == "i8":
        return struct.unpack_from(">b", buf, cursor)[0], cursor + 1
    if ptype == "u16":
        return struct.unpack_from(">H", buf, cursor)[0], cursor + 2
    if ptype == "i16":
        return struct.unpack_from(">h", buf, cursor)[0], cursor + 2
    if ptype == "u24":
        b0, b1, b2 = buf[cursor], buf[cursor + 1], buf[cursor + 2]
        return (b0 << 16) | (b1 << 8) | b2, cursor + 3
    if ptype == "u32":
        return struct.unpack_from(">I", buf, cursor)[0], cursor + 4
    if ptype == "i32":
        return struct.unpack_from(">i", buf, cursor)[0], cursor + 4
    if ptype == "ascii_str":
        # WR-04: bounds-check the length prefix against the remaining buffer
        # BEFORE slicing. Python slicing silently truncates when end >
        # len(buf), which would advance the cursor past the end of the buffer
        # and leave a mangled string in the rendered output if ascii_str is
        # the last param in a catalog entry. The CRC check upstream catches
        # truncated wire frames; this guards against a malformed length-prefix
        # byte inside an otherwise-correctly-CRC'd payload.
        length = buf[cursor]
        start = cursor + 1
        end = start + length
        if end > len(buf):
            raise ValueError(
                f"ascii_str length {length} exceeds remaining buffer "
                f"({len(buf) - start} bytes available at cursor={cursor})"
            )
        return buf[start:end].decode("ascii", errors="replace"), end
    if ptype == "bytes":
        # Variable-length raw payload (W-04 MSG_DATA_CHUNK): consume all
        # remaining bytes from cursor to end of params_bytes. No length-prefix
        # wire encoding — the frame's u16 len field already delimits the body.
        raw = buf[cursor:]
        return raw, len(buf)
    raise ValueError(f"Unknown param type: {ptype}")
