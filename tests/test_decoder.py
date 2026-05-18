"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 6 Plan 03 — Host decoder acceptance suite.

Covers requirements:
  - LHOST-01: hand-crafted binary frames feed cleanly through
              SerialCommunicator._read_and_parse_lines and yield
              Response(type, message) with correctly rendered text.
  - LHOST-02: per-param render hints (u24 → 0x%06x, u32 → 0x%lx, multi-param).
  - LHOST-03: severity routing surface preserved — Response.type carries the
              severity LABEL (string), unchanged for downstream
              _log_rurp_feedback consumers.
  - LMIG-01:  text-line path coexists with binary-frame path through the
              same _read_and_parse_lines yield surface.

All tests use the BytesIO-backed fake_serial fixture + make_comm factory
from conftest.py. No real serial port is opened.
"""

import logging

import pytest

from firestarter.messages import (
    CATALOG,
    MSG_OK_READY,
    MSG_INFO_ADDR,
    MSG_INFO_MEM_SIZE,
    MSG_ERR_WRITE_FAILED,
    MSG_DATA_PROGRESS,
)
from firestarter.serial_comm import (
    LogMessage,
    MAGIC_PREAMBLE,
    Response,
    _crc8_ccitt,
)

from .conftest import build_frame


def _drive_one_response(comm, timeout: float = 1.0):
    """Pull exactly one Response off the read loop, returning None on timeout."""
    gen = comm._read_and_parse_lines(timeout=timeout)
    try:
        return next(gen)
    except StopIteration:
        return None


class TestIdFrameDecoder:
    """End-to-end binary-frame acceptance tests for the host decoder."""

    def test_zero_param_frame_decodes_as_ready(self, fake_serial, make_comm):
        """LHOST-01: zero-param MSG_OK_READY frame → Response(type='OK', message='Ready')."""
        comm = make_comm()
        frame = build_frame(MSG_OK_READY, b"")
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "OK"
        assert response.message == "Ready"

        # Direct _decode_id_frame path — body = [id, crc] = [0x01, 0x07].
        body = bytes([MSG_OK_READY, _crc8_ccitt(bytes([MSG_OK_READY]))])
        decoded = comm._decode_id_frame(frame_len=2, body=body)
        assert decoded == LogMessage(severity="OK", text="Ready", id=MSG_OK_READY)

    def test_u32_param_renders_via_format_string(self, fake_serial, make_comm):
        """LHOST-01/02: u32 param renders via printf '%lx' lowercase, no padding."""
        comm = make_comm()
        params = bytes.fromhex("00010000")  # 0x10000 / 65536
        fake_serial.feed(build_frame(MSG_INFO_MEM_SIZE, params))

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "INFO"
        assert response.message == "Memory size 0x10000"

    def test_u24_render_as_hex_addr(self, fake_serial, make_comm):
        """LHOST-02: u24 render-hint 'hex_addr' renders as 0x%06x lowercase."""
        comm = make_comm()
        params = bytes.fromhex("01F4A2")
        fake_serial.feed(build_frame(MSG_INFO_ADDR, params))

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "INFO"
        assert response.message == "Address: 0x01f4a2"

    def test_multi_param_frame(self, fake_serial, make_comm):
        """LHOST-01: multi-param (u24, u8, u16) frame renders all three positions."""
        comm = make_comm()
        # 0x01F4A2 (addr), 0x05 (retries), 0x0003 (bad bytes)
        params = bytes.fromhex("01F4A2" + "05" + "0003")
        fake_serial.feed(build_frame(MSG_ERR_WRITE_FAILED, params))

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "ERROR"
        assert (
            response.message
            == "Failed to write memory, 0x01f4a2, retries: 5, bad bytes: 3"
        )

    def test_crc_mismatch_rejected(self, fake_serial, make_comm, caplog):
        """LHOST-01: tampered CRC byte → decoder returns None, warning logged."""
        comm = make_comm()
        good = build_frame(MSG_OK_READY, b"")
        # Flip the CRC byte (second-to-last). good = magic | len | id | crc | 0x0A.
        tampered = bytearray(good)
        tampered[-2] ^= 0xFF
        fake_serial.feed(bytes(tampered))

        with caplog.at_level(logging.WARNING, logger="SerialComm"):
            response = _drive_one_response(comm, timeout=0.2)

        assert response is None, "tampered frame must not yield a Response"
        assert any(
            "CRC mismatch for ID 0x" in record.message for record in caplog.records
        ), f"expected CRC-mismatch warning, got: {[r.message for r in caplog.records]}"

    def test_unknown_id_rejected(self, fake_serial, make_comm, caplog):
        """LHOST-01 / T-06-14: unknown ID → returns None, warning logged."""
        comm = make_comm()
        # 0x77 is not in the seed catalog (verify).
        assert 0x77 not in CATALOG
        body = bytes([0x77]) + bytes([_crc8_ccitt(bytes([0x77]))])

        with caplog.at_level(logging.WARNING, logger="SerialComm"):
            decoded = comm._decode_id_frame(frame_len=2, body=body)

        assert decoded is None
        assert any(
            "Unknown message ID 0x77" in record.message for record in caplog.records
        ), f"expected unknown-ID warning, got: {[r.message for r in caplog.records]}"

    def test_severity_routing_preserves_response_shape(
        self, fake_serial, make_comm
    ):
        """LHOST-03: Response.type carries severity LABEL (string), not int."""
        # OK severity.
        comm_ok = make_comm()
        comm_ok.connection.feed(build_frame(MSG_OK_READY, b""))
        ok_response = _drive_one_response(comm_ok)
        assert ok_response is not None
        assert ok_response.type == "OK"
        assert isinstance(ok_response.type, str)

        # ERROR severity.
        comm_err = make_comm()
        params = bytes.fromhex("01F4A2" + "05" + "0003")
        comm_err.connection.feed(build_frame(MSG_ERR_WRITE_FAILED, params))
        err_response = _drive_one_response(comm_err)
        assert err_response is not None
        assert err_response.type == "ERROR"
        assert isinstance(err_response.type, str)

    def test_text_line_coexistence(self, fake_serial, make_comm):
        """LMIG-01: text-format response (OK: Hello\\n) still flows through
        _parse_response_line — the byte-stream rewrite did not regress
        legacy parsing."""
        comm = make_comm()
        fake_serial.feed(b"OK: Hello\n")

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "OK"
        assert response.message == "Hello"

    def test_text_then_binary_in_one_read(self, fake_serial, make_comm):
        """LMIG-01 + LHOST-01: text line followed immediately by a binary
        frame both flow through the same _read_and_parse_lines yield
        surface, in order."""
        comm = make_comm()
        fake_serial.feed(b"INFO: Boot\n" + build_frame(MSG_OK_READY, b""))

        gen = comm._read_and_parse_lines(timeout=1.0)
        first = next(gen)
        second = next(gen)

        assert first == Response(type="INFO", message="Boot")
        assert second == Response(type="OK", message="Ready")

    def test_data_progress_u32_pair(self, fake_serial, make_comm):
        """Extra: MSG_DATA_PROGRESS (id 0xE0, two u32 params) — exercises
        the back-to-back u32 case and DATA severity routing."""
        comm = make_comm()
        # done=0x00000001, total=0x00010000  →  '1/65536'
        params = bytes.fromhex("00000001" + "00010000")
        fake_serial.feed(build_frame(MSG_DATA_PROGRESS, params))

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "DATA"
        assert response.message == "1/65536"
