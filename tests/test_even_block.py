"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 54 Plan 02 — Host pytest even-block suite (EVEN-01).

Pins two properties:

1. No-remainder write (EVEN-01 SC2): a 65536-byte image is sent as whole
   blocks of the firmware-advertised chunk size, at both Uno (512) and
   Leonardo (1024) sizes -- no partial last block.

2. firmware_max_chunk parse contract (CAP-01): _calculate_buffer_size() returns
   firmware_max_chunk directly (no -2 arithmetic); absent field returns 512
   (Uno-floor safe default — Phase 54 D-05 reversed, NO FirmwareOutdatedError).
"""

from types import SimpleNamespace

import pytest

from firestarter.chip_resolver import resolve_chip
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_operations import EpromOperator
from firestarter.frame_parser import cobs_decode
from firestarter.messages import (
    MSG_END_DONE,
    MSG_INIT_DONE,
    MSG_MAIN_DONE,
    MSG_OK_REQ_DATA,
)

from .conftest import build_frame, connect_via, fresh_serial_and_comm


class TestEvenBlockWriteChunks:
    """No-remainder write (EVEN-01 SC2): a 64 KiB image goes to the
    firmware as whole blocks of `firmware_max_chunk` bytes, at both the Uno
    (512) and the Leonardo (1024) size. Drives the genuine
    `EpromOperator.write_eprom` and decodes every data frame it sends."""

    @pytest.mark.parametrize("max_chunk", [512, 1024])
    def test_a_64k_write_sends_only_full_blocks(self, tmp_path, max_chunk) -> None:
        image = bytes(i & 0xFF for i in range(65536))
        input_file = tmp_path / "image.bin"
        input_file.write_bytes(image)
        blocks = len(image) // max_chunk

        serial, comm_factory = fresh_serial_and_comm()
        serial.feed(build_frame(MSG_INIT_DONE, b""))
        for _ in range(blocks):
            serial.feed(build_frame(MSG_OK_REQ_DATA, b""))
        serial.feed(build_frame(MSG_MAIN_DONE, b""))
        serial.feed(build_frame(MSG_END_DONE, b""))

        def advertise(comm) -> None:
            comm.firmware_max_chunk = max_chunk

        operator = EpromOperator(ConfigManager())
        with connect_via(comm_factory, on_connect=advertise):
            ok = operator.write_eprom(
                "m27c512",
                resolve_chip("m27c512", db=EpromDatabase(skip_local_override=True)),
                str(input_file),
                blank_check_requested=False,
            )

        assert ok is True
        sent = [
            cobs_decode(frame[1:-1])[:-1]
            for frame in serial.written
            if frame.startswith(b"#") and frame.endswith(b"\x00")
        ]
        assert [len(block) for block in sent] == [max_chunk] * blocks
        assert b"".join(sent) == image


class TestFirmwareMaxChunkParse:
    """firmware_max_chunk contract (CAP-01): _calculate_buffer_size() returns the
    firmware-advertised value directly (no -2 arithmetic), and returns 512 (the
    Uno-floor safe default) when the field is absent — Phase 54 D-05 reversed,
    NO FirmwareOutdatedError.
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

    def test_calculate_buffer_size_returns_512_without_max_chunk(self) -> None:
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
