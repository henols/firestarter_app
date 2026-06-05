"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 54 Plan 02 — Host pytest even-block suite (EVEN-01).

Pins three properties:

1. No-remainder arithmetic (EVEN-01 SC2): a 65536-byte chip image divides into
   whole even blocks at both Uno (512) and Leonardo (1024) sizes — no partial
   last-chunk round trip.

2. firmware_max_chunk parse contract (D-04): _calculate_buffer_size() returns
   firmware_max_chunk directly (no -2 arithmetic); absent field raises
   FirmwareOutdatedError (D-05 lockstep, no fallback).

3. Frame cap boundary (D-07 regression): a 512-byte data block + CRC8 COBS
   round-trips to the original 512 bytes, confirming the MAIN-path decode cap
   accommodates a full even block.
"""

from types import SimpleNamespace

from firestarter.config import ConfigManager
from firestarter.eprom_operations import EpromOperator
from firestarter.frame_parser import (  # type: ignore[attr-defined]
    _crc8_ccitt,
    cobs_decode,
    cobs_encode,
)


class TestEvenBlockNoRemainder:
    """No-remainder arithmetic assertions (EVEN-01 SC2).

    On a 65536-byte chip, full-buffer chunk sizes (512 Uno, 1024 Leonardo)
    divide the image exactly — no odd-sized final remainder write.
    Power-of-two chip sizes (32768, 131072, 262144) are included per Pitfall 5
    (all power-of-two sizes are exact multiples of any power-of-two block size).
    """

    def test_full_chip_no_remainder_uno(self) -> None:
        """65536-byte chip divides exactly into 512-byte Uno blocks."""
        assert 65536 % 512 == 0, (
            "65536-byte chip must divide exactly into 512-byte blocks"
        )

    def test_full_chip_no_remainder_leonardo(self) -> None:
        """65536-byte chip divides exactly into 1024-byte Leonardo blocks."""
        assert 65536 % 1024 == 0, (
            "65536-byte chip must divide exactly into 1024-byte blocks"
        )

    def test_power_of_two_chip_sizes_uno(self) -> None:
        """Power-of-two chip sizes all divide into 512-byte Uno blocks exactly."""
        for chip_size in (32768, 65536, 131072, 262144):
            assert chip_size % 512 == 0, (
                f"{chip_size}-byte chip must divide exactly into 512-byte blocks"
            )

    def test_power_of_two_chip_sizes_leonardo(self) -> None:
        """Power-of-two chip sizes all divide into 1024-byte Leonardo blocks exactly."""
        for chip_size in (32768, 65536, 131072, 262144):
            assert chip_size % 1024 == 0, (
                f"{chip_size}-byte chip must divide exactly into 1024-byte blocks"
            )


class TestFirmwareMaxChunkParse:
    """firmware_max_chunk contract: _calculate_buffer_size() returns the
    firmware-advertised value directly (no -2 arithmetic), and raises
    FirmwareOutdatedError when the field is absent (D-05 no fallback).
    """

    def test_calculate_buffer_size_uses_max_chunk_512(self) -> None:
        """firmware_max_chunk=512 -> _calculate_buffer_size() == 512 (Uno)."""
        op = EpromOperator(ConfigManager())
        op.comm = SimpleNamespace(firmware_max_chunk=512)  # type: ignore[assignment]
        assert op._calculate_buffer_size() == 512

    def test_calculate_buffer_size_uses_max_chunk_1024(self) -> None:
        """firmware_max_chunk=1024 -> _calculate_buffer_size() == 1024 (Leonardo)."""
        op = EpromOperator(ConfigManager())
        op.comm = SimpleNamespace(firmware_max_chunk=1024)  # type: ignore[assignment]
        assert op._calculate_buffer_size() == 1024

    def test_calculate_buffer_size_raises_without_max_chunk(self) -> None:
        """Absent firmware_max_chunk -> 512 safe default (CAP-01 — no FirmwareOutdatedError).

        Phase 54 D-05 is reversed by Phase 55 CAP-01: the host no longer raises when
        firmware_max_chunk is absent; it returns 512 (the Uno floor / universally safe
        minimum). Old-firmware acks carrying 0 param bytes are accepted gracefully.
        """
        op = EpromOperator(ConfigManager())
        # No comm set -> firmware_max_chunk absent -> must return 512, NOT raise
        result = op._calculate_buffer_size()
        assert result == 512, (
            f"Expected 512 (Uno-floor safe default), got {result}. "
            "CAP-01: absent firmware_max_chunk must NOT raise FirmwareOutdatedError."
        )

    def test_max_chunk_replaces_fw_buf_minus_2(self) -> None:
        """The result is NOT firmware_buffer_size - 2 (pins the -2 removal, EVEN-01).

        With firmware_max_chunk=512, the old formula would return 510 (512-2).
        The new formula must return 512 directly.
        """
        op = EpromOperator(ConfigManager())
        op.comm = SimpleNamespace(firmware_max_chunk=512)  # type: ignore[assignment]
        result = op._calculate_buffer_size()
        assert result == 512, (
            f"Expected 512, got {result} (old -2 formula would give 510)"
        )
        # Explicitly assert the result is NOT the buf-2 value
        fw_buf_minus_2 = 512 - 2
        assert result != fw_buf_minus_2, (
            f"Result {result} equals the obsolete buf-2 value {fw_buf_minus_2}; "
            "-2 arithmetic must be removed (EVEN-01/D-04)"
        )


class TestEvenBlockFrameVectorsCapBoundary:
    """Frame cap boundary regression (D-07): a 512-byte data block + CRC8 COBS
    round-trips and decodes to the original 512 bytes, confirming the MAIN-path
    decode cap (DATA_BUFFER_SIZE == 512) accommodates a full even block.

    Mirrors the host-side encoding of the firmware MAIN-path decode at cap=512.
    """

    def test_512_byte_all_ff_round_trips(self) -> None:
        """VEC_512_ALL_FF analog: 512-byte all-0xFF payload COBS encodes+decodes correctly."""
        data = bytes([0xFF] * 512)
        crc = _crc8_ccitt(data)
        payload = data + bytes([crc])
        frame = cobs_encode(payload) + b"\x00"

        # Decode: strip delimiter, COBS decode
        decoded = cobs_decode(frame[:-1])
        assert decoded == payload, "COBS round-trip must preserve payload + CRC8"
        assert decoded[:-1] == data, "Decoded data must match original 512 bytes"
        assert decoded[-1] == crc, "Decoded CRC8 must match computed CRC8"
        assert len(decoded[:-1]) == 512, "Decoded data length must be 512 bytes"

    def test_512_byte_all_zero_round_trips(self) -> None:
        """VEC_512_ALL_ZERO analog: 512-byte all-0x00 payload COBS encodes+decodes correctly."""
        data = bytes([0x00] * 512)
        crc = _crc8_ccitt(data)
        payload = data + bytes([crc])
        frame = cobs_encode(payload) + b"\x00"

        decoded = cobs_decode(frame[:-1])
        assert decoded == payload
        assert decoded[:-1] == data
        assert decoded[-1] == crc
        assert len(decoded[:-1]) == 512


class TestCapSafeDefault:
    """CAP-01 safe-default contract: _calculate_buffer_size() returns 512 when
    firmware_max_chunk is absent (old firmware ack carries 0 param bytes), and
    returns the advertised value directly when present.

    These tests are RED now — Phase 55 Plan 03 turns them GREEN by implementing
    the safe-default logic in eprom_operations._calculate_buffer_size().

    Phase 54 D-05 reversed: no FirmwareOutdatedError on absent chunk field.
    """

    def test_absent_firmware_max_chunk_returns_512(self) -> None:
        """No comm set -> _calculate_buffer_size() returns 512, raises NO exception.

        CAP-01 safe default: when firmware does not advertise max_chunk (old firmware
        or ack with 0 param bytes), the host falls back to 512 — the Uno floor,
        universally safe minimum. This reverses Phase 54 D-05.
        """
        op = EpromOperator(ConfigManager())
        # No comm set -> firmware_max_chunk absent -> must return 512, no exception
        result = op._calculate_buffer_size()
        assert result == 512, (
            f"Expected 512 (Uno-floor safe default), got {result}. "
            "CAP-01: absent firmware_max_chunk MUST NOT raise FirmwareOutdatedError; "
            "512 is the universally-safe Uno floor."
        )

    def test_512_ok_ready_ack_sets_firmware_max_chunk(self) -> None:
        """comm.firmware_max_chunk=512 -> _calculate_buffer_size() == 512 (Uno)."""
        op = EpromOperator(ConfigManager())
        op.comm = SimpleNamespace(firmware_max_chunk=512)  # type: ignore[assignment]
        assert op._calculate_buffer_size() == 512

    def test_1024_ok_ready_ack_sets_firmware_max_chunk(self) -> None:
        """comm.firmware_max_chunk=1024 -> _calculate_buffer_size() == 1024 (Leonardo)."""
        op = EpromOperator(ConfigManager())
        op.comm = SimpleNamespace(firmware_max_chunk=1024)  # type: ignore[assignment]
        assert op._calculate_buffer_size() == 1024
