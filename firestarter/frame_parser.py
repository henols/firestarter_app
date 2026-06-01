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


def cobs_encode(payload: bytes) -> bytes:
    """Encode ``payload`` using Consistent Overhead Byte Stuffing (COBS).

    Returns the encoded body **without** a trailing ``0x00`` delimiter.
    The caller is responsible for appending the delimiter so that the
    full atomic frame can be assembled as::

        b"#" + cobs_encode(payload + bytes([crc8])) + b"\\x00"

    Algorithm (ADR §4.1):
    - Scan for runs of ≤ 254 non-zero bytes.
    - Emit a run-length+1 code byte followed by the run bytes.
    - A zero byte in the input is represented by a code byte of ``0x01``
      (zero-length run) with no subsequent data byte.
    - A run of exactly 254 non-zero bytes emits code ``0xFF`` and the
      254 bytes but does NOT consume an implicit zero (Pitfall 2 /
      254-run phantom-zero edge).

    The output contains no ``0x00`` byte by construction (FRAME-04).
    """
    out = bytearray()
    i = 0
    n = len(payload)
    while i <= n:
        # Find the end of the next non-zero run (or end of payload)
        run_start = i
        while i < n and payload[i] != 0x00 and (i - run_start) < 254:
            i += 1
        run_len = i - run_start
        # Emit the code byte and the run bytes
        out.append(run_len + 1)
        out.extend(payload[run_start:i])
        if run_len == 254:
            # 254-run: no implicit zero — loop continues without consuming a zero
            # The next iteration starts a new run from the same position
            pass
        elif i < n and payload[i] == 0x00:
            # Consumed the zero; move past it
            i += 1
        else:
            # End of payload reached; we're done
            break
    return bytes(out)


def cobs_decode(encoded: bytes) -> bytes:
    """Decode a COBS body (NO trailing ``0x00`` delimiter).

    Raises ``ValueError`` on malformed input:
    - A ``0x00`` byte inside the body (the delimiter must not appear in the body).
    - A run length that would read beyond the end of the encoded buffer.

    This implements the bounded-decode control (ADR §4.1 / T-50-02):
    callers should treat any ``ValueError`` as a resync signal — drain to
    the next ``0x00`` delimiter and attempt the next frame.
    """
    out = bytearray()
    i, n = 0, len(encoded)
    while i < n:
        code = encoded[i]
        if code == 0:
            raise ValueError("0x00 inside COBS body")
        i += 1
        end = i + code - 1
        if end > n:
            raise ValueError("COBS run exceeds buffer")
        out.extend(encoded[i:end])
        i = end
        if code < 0xFF and i < n:
            out.append(0)  # implicit zero, except after a 254-run or at stream end
    return bytes(out)


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
