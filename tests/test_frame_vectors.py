"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 52 Plan 03 — Host pytest vector suite: both-legs COBS contract + CRC8 KAT.

Pins the byte-level COBS frame contract against the frozen golden vectors generated
in Plan 01 (firestarter.frame_vectors.FRAME_VECTORS).

Each vector drives BOTH legs (D-02):
  - Encode leg (Leg 1, LOCK-01): cobs_encode(payload + CRC8(payload)) + b"\\x00" == vec.frame
  - Decode leg (Leg 2, LOCK-01): cobs_decode(vec.frame[:-1])[:-1] == payload, CRC match

Decode leg is capped at 511-byte payloads (CR-01 firmware decode cap, per RESEARCH
Pitfall 5). Vectors with payloads >511 bytes (VEC_512_* and VEC_1024_*) are
encoder-only — their decode leg is skipped.

CRC8 KAT (D-06/SC4): _crc8_ccitt(bytes([0x01])) == 0x07 (poly value itself) and
_crc8_ccitt(b"") == 0x00 (seed). Cross-checked against the table-free _ref_crc8_ccitt.

No negative cases (corrupt-CRC, truncated frames) — those stay in test_cobs.py (D-03).

Requirements: LOCK-01, LOCK-02
"""

from firestarter.frame_parser import (  # type: ignore[attr-defined]
    _crc8_ccitt,
    cobs_decode,
    cobs_encode,
)
from firestarter.frame_vectors import FRAME_VECTORS


def _ref_crc8_ccitt(data: bytes) -> int:
    """Table-free CRC8 reference — poly 0x07, seed 0x00, no reflection, no final XOR.

    Used to cross-check _crc8_ccitt independently of the production lookup table.
    If the production _crc8_ccitt table regresses to a different polynomial, this
    reference catches it.
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
# Leg 1: Encode leg (LOCK-01) — encode(payload) == frozen frame bytes
# ---------------------------------------------------------------------------


class TestFrameVectorsEncodeLeg:
    """Encode leg: cobs_encode(payload + CRC8) + 0x00 == frozen frame for every vector.

    Tests ALL 12 golden vectors including 512/1024-byte ones — the encoder has no
    cap (only the decoder is capped at 511 bytes per CR-01).
    """

    def test_all_vectors_encode(self) -> None:
        """Every golden vector: encode(payload + crc) + 0x00 == frozen frame bytes.

        Asserts the host cobs_encode output is byte-exact against the frozen contract.
        A single divergent byte in the production encoder will fail this assertion.
        """
        for vec in FRAME_VECTORS:
            crc = _crc8_ccitt(vec.payload)
            encoded = cobs_encode(vec.payload + bytes([crc]))
            actual_frame = encoded + b"\x00"
            assert actual_frame == vec.frame, (
                f"Vector {vec.name} (id=0x{vec.id:02X}): encode leg failed — "
                f"got {actual_frame.hex()!r}, expected {vec.frame.hex()!r}"
            )


# ---------------------------------------------------------------------------
# Leg 2: Decode leg (LOCK-01) — decode(frozen frame) == payload + CRC match
# ---------------------------------------------------------------------------


class TestFrameVectorsDecodeLeg:
    """Decode leg: cobs_decode(frame[:-1])[:-1] == payload for vectors within cap.

    Vectors with payloads >511 bytes are skipped (encoder-only — mirrors the
    firmware CR-01 511 B decode cap per RESEARCH Pitfall 5). In the test comments
    these are called 'encoder-only' vectors.
    """

    def test_all_vectors_decode(self) -> None:
        """Every decode-eligible vector: decoded payload and CRC match frozen values.

        Skips VEC_512_* and VEC_1024_* (payload >511 bytes, encoder-only).
        Asserts both the payload bytes and the trailing CRC byte independently.
        """
        for vec in FRAME_VECTORS:
            if len(vec.payload) > 511:
                # Encoder-only per CR-01 — firmware decoder caps at 511 B
                continue
            body = vec.frame[:-1]  # strip the 0x00 delimiter
            decoded = cobs_decode(body)
            assert decoded[:-1] == vec.payload, (
                f"Vector {vec.name} (id=0x{vec.id:02X}): decode leg payload mismatch — "
                f"got {decoded[:-1].hex()!r}, expected {vec.payload.hex()!r}"
            )
            assert decoded[-1] == _crc8_ccitt(vec.payload), (
                f"Vector {vec.name} (id=0x{vec.id:02X}): decode leg CRC mismatch — "
                f"got 0x{decoded[-1]:02X}, expected 0x{_crc8_ccitt(vec.payload):02X}"
            )

    def test_encoder_only_vectors_are_skipped(self) -> None:
        """Confirm that at least the expected encoder-only vectors exist in the catalog.

        Documents the decode-cap boundary: vectors VEC_512_ALL_FF, VEC_512_ALL_ZERO,
        VEC_1024_ALL_FF, VEC_1024_ALL_ZERO have payload >511 bytes and are skipped
        on the decode leg. This test guards that the catalog still contains those
        vectors (so the skip is intentional, not because they were removed).
        """
        encoder_only = [v for v in FRAME_VECTORS if len(v.payload) > 511]
        assert len(encoder_only) >= 4, (
            f"Expected at least 4 encoder-only vectors (>511 B payload), "
            f"found {len(encoder_only)}: {[v.name for v in encoder_only]}"
        )
        names = {v.name for v in encoder_only}
        assert "VEC_512_ALL_FF" in names
        assert "VEC_512_ALL_ZERO" in names
        assert "VEC_1024_ALL_FF" in names
        assert "VEC_1024_ALL_ZERO" in names


# ---------------------------------------------------------------------------
# CRC8 KAT (D-06/SC4) — pin poly 0x07, seed 0x00 via table-free reference
# ---------------------------------------------------------------------------


class TestCrc8KnownAnswer:
    """D-06/SC4: known-answer test pins CRC8 poly 0x07, seed 0x00.

    Uses both the production _crc8_ccitt (lookup-table) and the table-free
    _ref_crc8_ccitt reference. A regression in the production table that changes
    the polynomial or seed will diverge from the table-free reference.
    """

    def test_crc8_of_0x01_is_0x07(self) -> None:
        """CRC8([0x01]) == 0x07 — the polynomial value itself (pins poly)."""
        assert _crc8_ccitt(bytes([0x01])) == 0x07

    def test_crc8_of_empty_is_seed(self) -> None:
        """CRC8([]) == 0x00 — empty payload returns the seed (pins seed)."""
        assert _crc8_ccitt(b"") == 0x00

    def test_crc8_cross_check_ref_0x01(self) -> None:
        """Production _crc8_ccitt([0x01]) matches table-free reference.

        Cross-check catches polynomial regression in the production lookup table.
        """
        assert _crc8_ccitt(bytes([0x01])) == _ref_crc8_ccitt(bytes([0x01]))

    def test_crc8_cross_check_ref_empty(self) -> None:
        """Production _crc8_ccitt(b"") matches table-free reference for empty input."""
        assert _crc8_ccitt(b"") == _ref_crc8_ccitt(b"")

    def test_crc8_cross_check_ref_json_payload(self) -> None:
        """Production _crc8_ccitt matches reference on VEC_JSON_STATE13 payload.

        Uses a real catalog payload for an integration-level cross-check.
        """
        vec = next(v for v in FRAME_VECTORS if v.name == "VEC_JSON_STATE13")
        assert _crc8_ccitt(vec.payload) == _ref_crc8_ccitt(vec.payload)


class TestHostChunkFitsFirmwareDecodeCap:
    """Phase 53 (LOCK-02 regression): the host's MAX DATA chunk must DECODE within
    the firmware's 511-byte cap.

    The vector decode leg above SKIPS payloads >511 bytes (VEC_512_*), so it never
    caught that the host was still sending full 512-byte write/verify chunks — a
    513-byte payload (512 data + CRC8) the firmware decoder rejects with
    "Data error: -2" (rurp_communication_read_data commits at most
    DATA_BUFFER_SIZE-1 = 511 bytes; CR-01 NUL-slot reservation). Bench-confirmed on
    BOTH Uno and Leonardo (Phase 53). Fix: host MAX_DATA_CHUNK = BUFFER_SIZE - 2.
    These tests pin the host chunk size to the firmware decode cap so write/verify
    never overflow.
    """

    # rurp_communication_read_data commits at most DATA_BUFFER_SIZE-1 payload bytes.
    FW_DECODE_CAP = 511

    def test_max_data_chunk_payload_fits_firmware_decode_cap(self) -> None:
        """data_chunk + CRC8 must fit the 511-byte firmware decode cap."""
        from firestarter.constants import MAX_DATA_CHUNK

        payload_len = MAX_DATA_CHUNK + 1  # + CRC8
        assert payload_len <= self.FW_DECODE_CAP, (
            f"MAX_DATA_CHUNK={MAX_DATA_CHUNK} -> payload {payload_len} exceeds firmware "
            f"decode cap {self.FW_DECODE_CAP}; write/verify will overflow with Data error: -2"
        )

    def test_calculate_buffer_size_respects_decode_cap(self) -> None:
        """EpromOperator._calculate_buffer_size() must never exceed the cap-safe size."""
        from firestarter.config import ConfigManager
        from firestarter.eprom_operations import EpromOperator

        op = EpromOperator(ConfigManager())
        assert op._calculate_buffer_size() + 1 <= self.FW_DECODE_CAP

    def test_max_chunk_decode_leg_round_trips(self) -> None:
        """The decode leg the old suite skipped: a full MAX_DATA_CHUNK + CRC8 frame
        COBS round-trips and its decoded payload is within the firmware cap."""
        from firestarter.constants import MAX_DATA_CHUNK

        data = bytes((i * 7 + 3) & 0xFF for i in range(MAX_DATA_CHUNK))
        crc = _crc8_ccitt(data)
        payload = data + bytes([crc])
        assert len(payload) <= self.FW_DECODE_CAP
        frame = cobs_encode(payload) + b"\x00"
        decoded = cobs_decode(frame[:-1])
        assert decoded == payload
        assert decoded[:-1] == data
        assert decoded[-1] == crc
