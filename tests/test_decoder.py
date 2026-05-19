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
import struct

import pytest

from firestarter.messages import (
    CATALOG,
    MSG_OK_READY,
    MSG_OK_FW_VERSION,
    MSG_OK_REV,
    MSG_OK_CFG,
    MSG_INIT_DONE,
    MSG_MAIN_DONE,
    MSG_END_DONE,
    MSG_INFO_ADDR,
    MSG_INFO_BIT_STR,
    MSG_INFO_CMD,
    MSG_INFO_MEM_SIZE,
    MSG_ERR_WRITE_FAILED,
    MSG_DATA_PROGRESS,
    MSG_DATA_SENDING,
    MSG_DATA_CHUNK,
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

    def test_wire_format_text_catalog_id_rejected_as_id_frame(
        self, fake_serial, make_comm, caplog
    ):
        """WR-03: catalog entries flagged wire_format='text' (MSG_OK_FW_VERSION
        id=0x03) must NOT decode through the id-frame path. A buggy or
        malicious peer sending id=0x03 as a binary frame would otherwise
        bypass _probe_port's pre-v1.2 firmware-version guard, which inspects
        only the text channel. Only MSG_OK_FW_VERSION (id=0x03) carries
        wire_format='text' per LFW-05.
        """
        # Sanity-check the catalog state.
        assert CATALOG[MSG_OK_FW_VERSION].wire_format == "text"

        comm = make_comm()

        # Hand-build a well-formed id-frame body for id=0x03 with no params.
        # CRC is valid; the only reason to reject is the wire_format mismatch.
        body = bytes([MSG_OK_FW_VERSION, _crc8_ccitt(bytes([MSG_OK_FW_VERSION]))])

        with caplog.at_level(logging.WARNING, logger="SerialComm"):
            decoded = comm._decode_id_frame(frame_len=2, body=body)

        assert decoded is None, (
            "id-frame with wire_format='text' catalog entry must be rejected"
        )
        assert any(
            "wire_format='text'" in record.message
            and "MSG_OK_FW_VERSION" in record.message
            for record in caplog.records
        ), (
            f"expected wire_format-rejection warning naming MSG_OK_FW_VERSION, "
            f"got: {[r.message for r in caplog.records]}"
        )

    def test_data_chunk_body_over_253_bytes_decodes(self, fake_serial, make_comm):
        """W-04 Wave-0 gap: MSG_DATA_CHUNK with a 512-byte payload (> 253 bytes,
        which a u8 len field could not have carried) round-trips through the
        decoder. Proves the u16-only path is exercised end-to-end."""
        comm = make_comm()
        # 512-byte deterministic payload: two full 0..255 sequences.
        payload = bytes(range(256)) + bytes(range(256))
        assert len(payload) == 512
        frame = build_frame(MSG_DATA_CHUNK, payload)
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None, "512-byte MSG_DATA_CHUNK must decode successfully"
        assert response.type == "DATA"
        # The decoder formats MSG_DATA_CHUNK's bytes param as a hex string; we
        # only assert the response is non-None and carries DATA severity since
        # the exact text representation is catalog-defined.

    def test_data_chunk_body_254_bytes_at_old_u8_limit(self, fake_serial, make_comm):
        """W-04: MSG_DATA_CHUNK frame whose body (id + params + crc) is 254 bytes —
        one byte past the old u8 max of 253 inclusive — decodes correctly.
        Under a u8 len this frame was unreachable; under u16 it is valid."""
        comm = make_comm()
        # 252-byte payload: body = 1 id + 252 params + 1 crc = 254 bytes.
        payload = bytes(range(252))
        assert len(payload) == 252
        frame = build_frame(MSG_DATA_CHUNK, payload)
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None, "254-byte body MSG_DATA_CHUNK must decode (u16 len)"
        assert response.type == "DATA"

    def test_ascii_str_overrun_rejected(self, fake_serial, make_comm, caplog):
        """WR-04: a length-prefix byte in an ascii_str payload that exceeds
        the remaining buffer must raise (and the decoder must swallow it as
        a warning + return None), rather than silently truncating via Python
        slice semantics. Uses MSG_INFO_BIT_STR (id=0x54), the single-ascii_str
        catalog entry — the most direct attack surface."""
        # Pin the catalog shape — single ascii_str param.
        entry = CATALOG[MSG_INFO_BIT_STR]
        assert entry.params == (("ascii_str", "ascii_str"),)

        comm = make_comm()

        # Hand-build a body claiming ascii_str length=10 but providing only
        # 3 bytes of payload after the length prefix. The CRC is computed
        # over the actual emitted bytes so the CRC gate passes — only the
        # bounds check inside _decode_param should fail this frame.
        #     body = id | length_prefix=10 | "ABC" | crc
        # Total params_bytes len = 4 (one length byte + 3 string bytes),
        # CRC covers [id] + params_bytes.
        bad_payload = bytes([10]) + b"ABC"   # claims 10 bytes, supplies 3
        crc = _crc8_ccitt(bytes([MSG_INFO_BIT_STR]) + bad_payload)
        body = bytes([MSG_INFO_BIT_STR]) + bad_payload + bytes([crc])

        with caplog.at_level(logging.WARNING, logger="SerialComm"):
            decoded = comm._decode_id_frame(frame_len=len(body), body=body)

        assert decoded is None, (
            "ascii_str length-prefix overflow must produce None, not a "
            "silently-truncated string"
        )
        assert any(
            "ascii_str length" in record.message
            and "exceeds remaining buffer" in record.message
            for record in caplog.records
        ), (
            f"expected ascii_str overrun warning, got: "
            f"{[r.message for r in caplog.records]}"
        )

    # -----------------------------------------------------------------
    # Wave 0 gap tests: INIT/MAIN/END ID-frame decode (W-01 / W-02)
    # -----------------------------------------------------------------

    def test_init_done_arrives_as_id_frame(self, fake_serial, make_comm):
        """W-01/W-02: MSG_INIT_DONE zero-param frame → Response(type='INIT',
        message='(init done)').  Proves the host decoder routes state-machine
        acks via the catalog severity-band, not line-prefix matching."""
        comm = make_comm()
        frame = build_frame(MSG_INIT_DONE, b"")
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "INIT"
        assert response.message == "(init done)"

    def test_main_done_arrives_as_id_frame(self, fake_serial, make_comm):
        """W-01/W-02: MSG_MAIN_DONE zero-param frame → Response(type='MAIN',
        message='(main done)')."""
        comm = make_comm()
        frame = build_frame(MSG_MAIN_DONE, b"")
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "MAIN"
        assert response.message == "(main done)"

    def test_end_done_arrives_as_id_frame(self, fake_serial, make_comm):
        """W-01/W-02: MSG_END_DONE zero-param frame → Response(type='END',
        message='(end done)')."""
        comm = make_comm()
        frame = build_frame(MSG_END_DONE, b"")
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "END"
        assert response.message == "(end done)"

    # -----------------------------------------------------------------
    # Wave 0 gap tests: P-02 MSG_OK_REV sentinel rendering
    # -----------------------------------------------------------------

    def test_ok_rev_p02_with_override_decodes(self, fake_serial, make_comm):
        """P-02: MSG_OK_REV with physical=0x01, effective=0x02 (override active)
        renders 'Rev2, Override HW: Rev1'."""
        comm = make_comm()
        # params: u8 physical=0x01, u8 effective=0x02
        params = bytes([0x01, 0x02])
        frame = build_frame(MSG_OK_REV, params)
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "OK"
        assert response.message == "Rev2, Override HW: Rev1"

    def test_ok_rev_p02_no_override_decodes(self, fake_serial, make_comm):
        """P-02: MSG_OK_REV with physical=0x01, effective=0xFF sentinel renders
        'Rev1' (no override clause)."""
        comm = make_comm()
        # params: u8 physical=0x01, u8 effective=0xFF (sentinel = no override)
        params = bytes([0x01, 0xFF])
        frame = build_frame(MSG_OK_REV, params)
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "OK"
        assert response.message == "Rev1"

    # -----------------------------------------------------------------
    # Wave 0 gap tests: P-03 MSG_OK_CFG sentinel rendering
    # -----------------------------------------------------------------

    def test_ok_cfg_p03_with_override_decodes(self, fake_serial, make_comm):
        """P-03: MSG_OK_CFG with r1=10000, r2=4700, override=0x02 renders
        'R1: 10000, R2: 4700, Override HW: Rev2'."""
        comm = make_comm()
        # params: u32 r1=10000, u32 r2=4700, u8 override=0x02
        params = struct.pack(">II", 10000, 4700) + bytes([0x02])
        frame = build_frame(MSG_OK_CFG, params)
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "OK"
        assert response.message == "R1: 10000, R2: 4700, Override HW: Rev2"

    def test_ok_cfg_p03_no_override_decodes(self, fake_serial, make_comm):
        """P-03: MSG_OK_CFG with r1=10000, r2=4700, override=0xFF sentinel renders
        'R1: 10000, R2: 4700' (no override clause)."""
        comm = make_comm()
        # params: u32 r1=10000, u32 r2=4700, u8 override=0xFF (sentinel = no override)
        params = struct.pack(">II", 10000, 4700) + bytes([0xFF])
        frame = build_frame(MSG_OK_CFG, params)
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "OK"
        assert response.message == "R1: 10000, R2: 4700"

    # -----------------------------------------------------------------
    # MSG_INFO_CMD: cmd byte annotated with symbolic name from COMMAND_NAMES
    # -----------------------------------------------------------------

    def test_info_cmd_renders_with_symbolic_name(self, fake_serial, make_comm):
        """MSG_INFO_CMD with cmd=0x02 (COMMAND_WRITE) renders
        'Cmd: 0x02 (WRITE)'."""
        comm = make_comm()
        frame = build_frame(MSG_INFO_CMD, bytes([0x02]))
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "INFO"
        assert response.message == "Cmd: 0x02 (WRITE)"

    def test_info_cmd_unknown_falls_back_to_bare_hex(self, fake_serial, make_comm):
        """MSG_INFO_CMD with an unmapped cmd byte renders bare hex
        (no name annotation)."""
        comm = make_comm()
        frame = build_frame(MSG_INFO_CMD, bytes([0xFE]))
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "INFO"
        assert response.message == "Cmd: 0xfe"

    # -----------------------------------------------------------------
    # W-04 MSG_DATA_CHUNK roundtrip tests
    # -----------------------------------------------------------------

    def test_data_chunk_payload_exposed_via_response_payload_field(self, fake_serial, make_comm):
        """W-04: MSG_DATA_CHUNK frame with 256-byte payload → Response.payload
        holds the exact raw bytes; Response.type == 'DATA'."""
        comm = make_comm()
        # 256-byte deterministic payload.
        payload_bytes = bytes(range(256))
        frame = build_frame(MSG_DATA_CHUNK, payload_bytes)
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None, "MSG_DATA_CHUNK must decode successfully"
        assert response.type == "DATA"
        assert response.payload is not None, "Response.payload must be set for MSG_DATA_CHUNK"
        assert response.payload == payload_bytes, (
            "Response.payload must match the transmitted chunk bytes exactly"
        )

    def test_chip_read_loop_concatenates_multiple_chunks(self, fake_serial, make_comm):
        """W-04: chip-read loop in _main_phase_read_data concatenates N
        MSG_DATA_CHUNK frames into an in-order byte stream.

        Simulates the firmware emitting:
          MSG_DATA_SENDING (zero-param, before first chunk)
          MSG_DATA_CHUNK(chunk0: 256 bytes)
          MSG_DATA_CHUNK(chunk1: 256 bytes)
          MSG_DATA_CHUNK(chunk2: 256 bytes)
          MSG_MAIN_DONE (end of read)

        Asserts the concatenated output is 768 bytes in the correct order.
        """
        from firestarter.messages import MSG_DATA_SENDING, MSG_MAIN_DONE

        comm = make_comm()

        # Build the simulated firmware byte stream.
        chunk0 = bytes(range(256))        # 0x00..0xFF
        chunk1 = bytes(range(256))[::-1]  # 0xFF..0x00
        chunk2 = bytes([0xAA] * 256)      # all 0xAA

        stream = (
            build_frame(MSG_DATA_SENDING, b"")   # zero-param batch-start ack
            + build_frame(MSG_DATA_CHUNK, chunk0)
            + build_frame(MSG_DATA_CHUNK, chunk1)
            + build_frame(MSG_DATA_CHUNK, chunk2)
            + build_frame(MSG_MAIN_DONE, b"")
        )
        fake_serial.feed(stream)

        # Drive the read loop manually.
        from firestarter.eprom_operations import ClassProgressHandler, EpromOperationError

        collected = bytearray()
        start_addr = [0]  # mutable box for callback closure

        def _collect(address, data_chunk):
            collected.extend(data_chunk)

        # Patch comm.send_ack so the loop's send_ack calls don't fail.
        ack_calls = []
        original_send_ack = comm.send_ack
        comm.send_ack = lambda: ack_calls.append(1)

        progress = ClassProgressHandler()
        progress.start(768)

        # Manually drive _main_phase_read_data by calling get_response loop.
        # We simulate it inline since the method needs self.comm (which is comm).
        from firestarter.messages import MSG_DATA_CHUNK as _MSG_DATA_CHUNK

        while True:
            response = comm.get_response(timeout=1.0)
            if response.type == "MAIN":
                break
            if response.type == "ERROR":
                raise EpromOperationError(response.message)
            if response.type == "DATA":
                if response.payload is not None:
                    _collect(0, response.payload)
                    comm.send_ack()
                # else: MSG_DATA_SENDING — skip

        assert len(collected) == 768, f"Expected 768 bytes, got {len(collected)}"
        assert bytes(collected[:256]) == chunk0, "First chunk mismatch"
        assert bytes(collected[256:512]) == chunk1, "Second chunk mismatch"
        assert bytes(collected[512:]) == chunk2, "Third chunk mismatch"
        assert len(ack_calls) == 3, f"Expected 3 ACKs (one per chunk), got {len(ack_calls)}"
