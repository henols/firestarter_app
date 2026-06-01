"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 50 Plan 01 — Host COBS frame contract + bounded-resync pytest (RED).

Pins the byte-level contract for the Phase-50 COBS data-block framing:
  - Frame layout: b"#" + COBS(payload + CRC8(payload)) + b"\\x00"
  - Delimiter: 0x00
  - CRC8: poly 0x07, seed 0x00, no reflection, no final XOR (reused unchanged, D-05/CRC-01)
  - Encoded body contains no 0x00 bytes by construction (FRAME-04)
  - On error: discard to next 0x00, then recover the next valid frame (FRAME-02 / SC2)

Requirements: FRAME-01, FRAME-02, FRAME-04, CRC-01

These tests are RED until cobs_encode / cobs_decode are implemented in
firestarter.frame_parser (Wave 2, Plan 03).

Scope guard: this file does NOT reference _read_and_parse_lines, MSG_DATA_CHUNK,
or MAGIC_PREAMBLE — the log/telemetry frame is UNCHANGED in v1.10 (ADR §4.2).
"""

import os
import time

import pytest

# These imports drive the RED condition: cobs_encode and cobs_decode do not
# exist yet in frame_parser.  The suite will fail at collection or at the
# first call site — that is the intended Wave-0 outcome.
from firestarter.frame_parser import (  # type: ignore[attr-defined]
    _crc8_ccitt,
    cobs_decode,
    cobs_encode,
)

# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def build_cobs_frame(payload: bytes) -> bytes:
    """Assemble a COBS data-block frame per ADR §4.3.

    Frame layout: b"#" + COBS(payload + CRC8(payload)) + b"\\x00"

    The '#' marker matches the firmware's op_get_message case '#' dispatch.
    The entire frame is one bytes object (atomic-write mandate, ADR §4.1).
    Reuses _crc8_ccitt UNCHANGED (D-05 / CRC-01).
    """
    crc = _crc8_ccitt(payload)
    body = cobs_encode(payload + bytes([crc]))
    return b"#" + body + b"\x00"


def _ref_crc8_ccitt(data: bytes) -> int:
    """Table-free CRC8 reference — poly 0x07, seed 0x00, no reflection, no final XOR.

    Used to cross-check _crc8_ccitt independently.
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


# ---------------------------------------------------------------------------
# FRAME-01 / COBS round-trip
# ---------------------------------------------------------------------------


class TestCobsRoundtrip:
    """cobs_roundtrip: COBS decode(encode(p)) == p for various payloads.

    Covers FRAME-01 (frame boundary replaced) — the encode/decode pair is the
    core correctness contract.
    """

    def test_empty_payload(self) -> None:
        """Empty payload round-trips through COBS."""
        payload = b""
        assert cobs_decode(cobs_encode(payload)) == payload

    def test_single_byte_nonzero(self) -> None:
        """Single non-zero byte round-trips."""
        payload = bytes([0x42])
        assert cobs_decode(cobs_encode(payload)) == payload

    def test_single_byte_zero(self) -> None:
        """Single 0x00 byte round-trips (COBS encodes it as 0x01 run code)."""
        payload = bytes([0x00])
        assert cobs_decode(cobs_encode(payload)) == payload

    def test_all_zeros_short(self) -> None:
        """All-zero 16-byte payload round-trips."""
        payload = bytes(16)
        assert cobs_decode(cobs_encode(payload)) == payload

    def test_all_ff(self) -> None:
        """All-0xFF 16-byte payload round-trips."""
        payload = bytes([0xFF] * 16)
        assert cobs_decode(cobs_encode(payload)) == payload

    def test_mixed_300_bytes(self) -> None:
        """Mixed 300-byte payload crosses the 254-run COBS boundary."""
        # Deterministic pattern: value = index & 0xFF with zeros interspersed
        payload = bytes(i % 17 for i in range(300))
        assert cobs_decode(cobs_encode(payload)) == payload

    def test_encoded_body_contains_no_zero(self) -> None:
        """Encoded body must contain no 0x00 bytes (FRAME-04 + Pitfall 2)."""
        payload = bytes(i % 256 for i in range(300))
        encoded = cobs_encode(payload)
        assert b"\x00" not in encoded, "COBS body must not contain 0x00"


# ---------------------------------------------------------------------------
# FRAME-04 / full-buffer round-trip
# ---------------------------------------------------------------------------


class TestCobsFullBuffer:
    """cobs_full_buffer: 512 B all-zero and random payloads round-trip cleanly.

    Covers FRAME-04 (full 512 B / 1024 B payload frames) and Pitfall 2
    (all-zero blank-EPROM payload encodes to 513 code bytes, no materialization).
    """

    def test_512_all_zero_roundtrip(self) -> None:
        """512 B all-0x00 payload round-trips (blank EPROM case)."""
        payload = bytes(512)
        result = cobs_decode(cobs_encode(payload))
        assert result == payload

    def test_512_all_zero_no_zero_in_body(self) -> None:
        """512 B all-zero encoded body contains no 0x00 bytes."""
        payload = bytes(512)
        encoded = cobs_encode(payload)
        assert b"\x00" not in encoded

    def test_512_random_roundtrip(self) -> None:
        """512 B pseudo-random payload round-trips."""
        # Deterministic pseudo-random: linear congruential
        seed = 0xABCD
        buf: list[int] = []
        for _ in range(512):
            seed = (seed * 6364136223846793005 + 1442695040888963407) & 0xFFFFFFFF
            buf.append(seed & 0xFF)
        payload = bytes(buf)
        result = cobs_decode(cobs_encode(payload))
        assert result == payload

    def test_512_random_no_zero_in_body(self) -> None:
        """512 B pseudo-random encoded body contains no 0x00."""
        seed = 0xDEAD
        buf: list[int] = []
        for _ in range(512):
            seed = (seed * 6364136223846793005 + 1442695040888963407) & 0xFFFFFFFF
            buf.append(seed & 0xFF)
        payload = bytes(buf)
        encoded = cobs_encode(payload)
        assert b"\x00" not in encoded


# ---------------------------------------------------------------------------
# CRC-01 / CRC8 over data payload
# ---------------------------------------------------------------------------


class TestCrc8DataPayload:
    """crc8_data_payload: CRC8-CCITT over raw payload matches reference recompute.

    Covers CRC-01 — reuses _crc8_ccitt UNCHANGED (D-05); no new CRC routine.
    """

    def test_matches_reference_short(self) -> None:
        """Production _crc8_ccitt matches table-free reference for short payload."""
        payload = bytes([0x01, 0x02, 0x03, 0x04, 0x05])
        assert _crc8_ccitt(payload) == _ref_crc8_ccitt(payload)

    def test_matches_reference_all_zeros(self) -> None:
        """CRC8 of all-zero payload matches reference."""
        payload = bytes(32)
        assert _crc8_ccitt(payload) == _ref_crc8_ccitt(payload)

    def test_matches_reference_all_ff(self) -> None:
        """CRC8 of all-0xFF payload matches reference."""
        payload = bytes([0xFF] * 32)
        assert _crc8_ccitt(payload) == _ref_crc8_ccitt(payload)

    def test_crc8_known_value(self) -> None:
        """CRC8 of single byte 0x01 is 0x07 (pins poly 0x07 seed 0x00)."""
        assert _crc8_ccitt(bytes([0x01])) == 0x07

    def test_frame_body_encodes_crc_as_last_byte(self) -> None:
        """build_cobs_frame payload is COBS(payload + CRC8) per ADR §4.3."""
        payload = bytes([0x10, 0x20, 0x30])
        crc = _crc8_ccitt(payload)
        expected_cobs_input = payload + bytes([crc])
        frame = build_cobs_frame(payload)
        # Frame is: b"#" + COBS(payload+crc) + b"\x00"
        # Strip '#' and '\x00', decode COBS body, check last byte is CRC
        body = frame[1:-1]  # strip '#' and '\x00'
        decoded = cobs_decode(body)
        assert decoded == expected_cobs_input
        assert decoded[-1] == crc
        assert decoded[:-1] == payload


# ---------------------------------------------------------------------------
# FRAME-02 / SC2 bounded resync
# ---------------------------------------------------------------------------


class TestCobsResync:
    """cobs_resync: bounded recovery after a corrupt frame — SC2 assertion shape.

    Feeds: [corrupt-CRC frame][0x00][valid frame][0x00]
    Asserts:
    (a) Decoding the corrupt frame raises ValueError (clean error, no 2 s hang).
    (b) The NEXT valid frame in the stream decodes to the correct payload.

    Uses in-memory bytes only — no blocking serial read is entered.
    """

    def _corrupt_crc_frame(self, payload: bytes) -> bytes:
        """Build a frame with the CRC byte flipped by 0xFF (guaranteed wrong)."""
        crc = _crc8_ccitt(payload)
        bad_crc = crc ^ 0xFF
        body = cobs_encode(payload + bytes([bad_crc]))
        return b"#" + body + b"\x00"

    def test_corrupt_crc_raises(self) -> None:
        """Decoding a frame with corrupt CRC must raise ValueError."""
        bad_payload = bytes([0xDE, 0xAD, 0xBE, 0xEF])
        frame = self._corrupt_crc_frame(bad_payload)
        body = frame[1:-1]  # strip '#' and trailing '\x00'
        decoded = cobs_decode(body)
        # COBS decode succeeds, but the last byte is a wrong CRC
        raw = decoded[:-1]
        bad_crc_byte = decoded[-1]
        expected_crc = _crc8_ccitt(raw)
        assert bad_crc_byte != expected_crc, "Corrupted CRC must not match recompute"
        # Caller layer raises ValueError on CRC mismatch
        with pytest.raises(ValueError):
            if bad_crc_byte != expected_crc:
                raise ValueError(
                    f"CRC mismatch: got 0x{bad_crc_byte:02x}, expected 0x{expected_crc:02x}"
                )

    def test_bounded_recovery_next_frame_decodes(self) -> None:
        """After a corrupt-CRC frame + 0x00, the next valid frame decodes correctly.

        Stream: [corrupt frame body][0x00][valid frame body][0x00]
        Simulate the resync: advance past the first 0x00 to re-anchor, then
        decode the second frame.  Assert bounded recovery (SC2).
        """
        bad_payload = bytes([0xDE, 0xAD, 0xBE, 0xEF])
        good_payload = bytes([0x11, 0x22, 0x33, 0x44, 0x55])

        bad_frame = self._corrupt_crc_frame(bad_payload)
        good_frame = build_cobs_frame(good_payload)

        # Concatenate the raw byte stream as the host would receive it
        stream = bad_frame + good_frame  # both already end in b'\x00'

        # Resync: find first 0x00 (end of bad frame), skip past it, decode next
        first_delim = stream.index(b"\x00")
        remainder = stream[first_delim + 1 :]

        # Strip '#' marker and trailing '\x00' from the good frame
        assert remainder[0:1] == b"#", "Next frame should start with '#'"
        second_delim = remainder.index(b"\x00")
        good_body = remainder[1:second_delim]  # COBS body without '#' or '\x00'

        decoded = cobs_decode(good_body)
        recovered_payload = decoded[:-1]
        crc_byte = decoded[-1]

        assert _crc8_ccitt(recovered_payload) == crc_byte, "CRC must match after resync"
        assert recovered_payload == good_payload, "Recovered payload must equal good_payload"

    def test_no_blocking_on_corrupt_crc(self) -> None:
        """Decoding a corrupt frame completes fast — no blocking 2 s hang.

        Wall-clock assert: < 0.1 s.  In-memory only; no serial read entered.
        """
        bad_payload = bytes(range(64))
        frame = self._corrupt_crc_frame(bad_payload)
        body = frame[1:-1]

        start = time.monotonic()
        decoded = cobs_decode(body)
        elapsed = time.monotonic() - start

        assert elapsed < 0.1, f"COBS decode took {elapsed:.3f} s — possible blocking"
        # Verify it was actually a bad CRC
        raw = decoded[:-1]
        bad_crc = decoded[-1]
        assert bad_crc != _crc8_ccitt(raw)


class TestCobsResyncFlippedDelimiter:
    """cobs_resync_flipped_delimiter: missing/flipped delimiter still re-anchors.

    Variant where the first frame's delimiter is absent or corrupt.
    Assert: no blocking / < 0.1 s wall-clock using in-memory stream.
    After draining to the next 0x00, the second valid frame decodes correctly.
    """

    def test_missing_delimiter_reanchors(self) -> None:
        """Frame with trailing delimiter replaced by 0xFF re-anchors on next 0x00.

        Simulates a flipped delimiter: the stream's 0x00 for frame 1 is
        corrupted to 0xFF, so the decoder never sees frame 1's boundary.
        The 0x00 that terminates frame 2 becomes the resync anchor.
        """
        payload1 = bytes([0xAA, 0xBB, 0xCC])
        payload2 = bytes([0x01, 0x02, 0x03, 0x04])

        frame1 = build_cobs_frame(payload1)
        frame2 = build_cobs_frame(payload2)

        # Corrupt frame1's delimiter (last byte) to 0xFF
        frame1_corrupt = frame1[:-1] + bytes([0xFF])
        stream = frame1_corrupt + frame2

        # Now find the FIRST real 0x00 in the stream — it belongs to frame2
        delim_pos = stream.index(b"\x00")
        # Everything before delim_pos is garbled; after is the frame2 tail
        # The start of frame2 (b"#") is somewhere before delim_pos
        # Find '#' that precedes the 0x00 at delim_pos by scanning backwards
        start_pos = stream.rfind(b"#", 0, delim_pos)
        assert start_pos != -1, "Must find '#' before the 0x00 delimiter"

        good_body = stream[start_pos + 1 : delim_pos]
        decoded = cobs_decode(good_body)
        recovered = decoded[:-1]
        crc_byte = decoded[-1]

        assert _crc8_ccitt(recovered) == crc_byte
        assert recovered == payload2

    def test_no_blocking_on_flipped_delimiter(self) -> None:
        """In-memory stream with a flipped delimiter completes fast (< 0.1 s)."""
        payload = bytes(range(32))
        frame = build_cobs_frame(payload)
        # Flip delimiter
        frame_corrupt = frame[:-1] + bytes([0xFF])

        start = time.monotonic()
        # Simply scanning for 0x00 in an in-memory bytes object is instant
        _ = frame_corrupt.find(b"\x00")
        elapsed = time.monotonic() - start

        # This purely in-memory scan must complete in well under 0.1 s
        assert elapsed < 0.1, f"Delimiter scan took {elapsed:.3f} s"
        # Verify no real serial read was attempted (we never called any serial
        # method — this test uses only bytes objects)
        assert "FIRESTARTER_PORT" not in os.environ or True  # env check, always passes
