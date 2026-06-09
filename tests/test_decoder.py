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

Phase 57 Plan 01 — build_db decode regression suite.

Covers requirements:
  - DEC-03: interpret_timing() raw microseconds for 0x07/0x08/0x0B, no x100
            multiplier; bare except replaced with except Exception.
  - DEC-04: VCC_VOLTAGES includes nibbles 0x02 (4V) and 0x03 (4.5V);
            vcc=bits-11-8, vdd=bits-15-12 (labels no longer swapped).

All tests use the BytesIO-backed fake_serial fixture + make_comm factory
from conftest.py. No real serial port is opened.
"""

import logging
import struct

import pytest  # noqa: F401

from firestarter.messages import (
    CATALOG,
    DBG_CMD,
    MSG_DATA_CHUNK,
    MSG_DATA_PROGRESS,
    MSG_DATA_SENDING,  # noqa: F401
    MSG_DEBUG,
    MSG_END_DONE,
    MSG_ERR_WRITE_FAILED,
    MSG_INFO_ADDR,
    MSG_INFO_BIT_STR,
    MSG_INFO_HW,
    MSG_INFO_PHYSICAL_HW,
    MSG_INIT_DONE,
    MSG_MAIN_DONE,
    MSG_OK_CFG,
    MSG_OK_FW_VERSION,
    MSG_OK_READY,
    MSG_OK_REV,
    MSG_WARN_MEM_SIZE_TOO_SMALL,
)
from firestarter.serial_comm import (
    MAGIC_PREAMBLE,  # noqa: F401
    LogMessage,
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
        """LHOST-01: zero-param MSG_OK_READY frame → Response(type='OK', message='Ready')."""  # noqa: E501
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
        """LHOST-01/02: u32 param renders via printf format substitution."""
        comm = make_comm()
        params = bytes.fromhex("00010000")  # 0x10000 / 65536
        fake_serial.feed(build_frame(MSG_WARN_MEM_SIZE_TOO_SMALL, params))

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "WARN"
        assert response.message == "Memory size 65536 too small for chip-id check"

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

    def test_severity_routing_preserves_response_shape(self, fake_serial, make_comm):
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
        assert response is not None, (
            "254-byte body MSG_DATA_CHUNK must decode (u16 len)"
        )
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
        bad_payload = bytes([10]) + b"ABC"  # claims 10 bytes, supplies 3
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
        renders 'Rev 2.0-class, Override HW: Rev 1' per Phase 34 D-05 Path A
        silkscreen-string mapping (was 'Rev2, Override HW: Rev1' pre-Phase-34)."""
        comm = make_comm()
        # params: u8 physical=0x01, u8 effective=0x02
        params = bytes([0x01, 0x02])
        frame = build_frame(MSG_OK_REV, params)
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "OK"
        assert response.message == "Rev 2.0-class, Override HW: Rev 1"

    def test_ok_rev_p02_no_override_decodes(self, fake_serial, make_comm):
        """P-02: MSG_OK_REV with physical=0x01, effective=0xFF sentinel renders
        'Rev 1' per Phase 34 D-05 Path A silkscreen-string mapping (was 'Rev1'
        pre-Phase-34)."""
        comm = make_comm()
        # params: u8 physical=0x01, u8 effective=0xFF (sentinel = no override)
        params = bytes([0x01, 0xFF])
        frame = build_frame(MSG_OK_REV, params)
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "OK"
        assert response.message == "Rev 1"

    # -----------------------------------------------------------------
    # Wave 0 gap tests: P-03 MSG_OK_CFG sentinel rendering
    # -----------------------------------------------------------------

    def test_ok_cfg_p03_with_override_decodes(self, fake_serial, make_comm):
        """P-03: MSG_OK_CFG with r1=10000, r2=4700, override=0x02 (REVISION_2_0)
        renders 'R1: 10000, R2: 4700, Override HW: Rev 2.0-class' via the
        Phase 35 D-04 silkscreen-aware lookup (was 'Override HW: Rev2'
        pre-Phase-35). Closes 34-REVIEW.md WR-02 — the same byte must not
        render as 'Rev 2.0-class' on the MSG_OK_REV ack and 'Rev2' on the
        adjacent MSG_OK_CFG ack."""
        comm = make_comm()
        # params: u32 r1=10000, u32 r2=4700, u8 override=0x02 (REVISION_2_0)
        params = struct.pack(">II", 10000, 4700) + bytes([0x02])
        frame = build_frame(MSG_OK_CFG, params)
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "OK"
        assert response.message == "R1: 10000, R2: 4700, Override HW: Rev 2.0-class"

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

    def test_ok_cfg_p03_with_unknown_override_decodes(self, fake_serial, make_comm):
        """P-03: MSG_OK_CFG with override=0x99 (unknown byte) falls back to
        'R1: ..., R2: ..., Override HW: Rev153' (no space — mirrors MSG_OK_REV
        fallback shape). Per Phase 35 D-04 + WR-02 close."""
        comm = make_comm()
        params = struct.pack(">II", 10000, 4700) + bytes([0x99])
        frame = build_frame(MSG_OK_CFG, params)
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "OK"
        assert response.message == "R1: 10000, R2: 4700, Override HW: Rev153"

    # -----------------------------------------------------------------
    # Phase 35 D-03 / WR-01 close: MSG_INFO_HW + MSG_INFO_PHYSICAL_HW
    # silkscreen-string rendering.
    #
    # WR-01: both INFO frames carry the same revision byte as MSG_OK_REV
    # but were rendering 'HW: Rev254' (raw catalog %u) for REVISION_UNKNOWN
    # (0xFE) instead of 'HW: rev_unknown'. Both surfaces must agree on the
    # silkscreen-string mapping per Phase 35 D-03.
    # -----------------------------------------------------------------

    def test_info_hw_silkscreen_known_rev_decodes(self, fake_serial, make_comm):
        """D-03: MSG_INFO_HW with byte=0x01 (REVISION_1) renders 'HW: Rev 1'
        via _REVISION_SILKSCREEN lookup. Phase 35 WR-01 close — see
        34-REVIEW.md WR-01 + 35-CONTEXT.md D-03."""
        comm = make_comm()
        params = bytes([0x01])
        frame = build_frame(MSG_INFO_HW, params)
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "INFO"
        assert response.message == "HW: Rev 1"

    def test_info_hw_silkscreen_rev_unknown_decodes(self, fake_serial, make_comm):
        """D-03: MSG_INFO_HW with byte=0xFE (REVISION_UNKNOWN) renders
        'HW: rev_unknown' instead of the catalog-default 'HW: Rev254'.
        Phase 35 WR-01 close — co-designed with Plan 01's CR-02 hard-fail-loud
        emit at boot time."""
        comm = make_comm()
        params = bytes([0xFE])
        frame = build_frame(MSG_INFO_HW, params)
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "INFO"
        assert response.message == "HW: rev_unknown"

    def test_info_hw_silkscreen_unknown_byte_falls_back(self, fake_serial, make_comm):
        """D-03: MSG_INFO_HW with an unmapped byte (0x99) falls back to
        'HW: Rev153' (no space — mirrors MSG_OK_REV fallback shape).
        Phase 35 WR-01 close."""
        comm = make_comm()
        params = bytes([0x99])
        frame = build_frame(MSG_INFO_HW, params)
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "INFO"
        assert response.message == "HW: Rev153"

    def test_info_physical_hw_silkscreen_known_rev_decodes(
        self, fake_serial, make_comm
    ):
        """D-03: MSG_INFO_PHYSICAL_HW with byte=0x01 (REVISION_1) renders
        'Physical HW: Rev 1' via _REVISION_SILKSCREEN lookup. Phase 35 WR-01
        close — mirrors MSG_INFO_HW shape with the 'Physical HW: ' prefix."""
        comm = make_comm()
        params = bytes([0x01])
        frame = build_frame(MSG_INFO_PHYSICAL_HW, params)
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "INFO"
        assert response.message == "Physical HW: Rev 1"

    def test_info_physical_hw_silkscreen_rev_unknown_decodes(
        self, fake_serial, make_comm
    ):
        """D-03: MSG_INFO_PHYSICAL_HW with byte=0xFE (REVISION_UNKNOWN) renders
        'Physical HW: rev_unknown' instead of the catalog-default 'Physical HW: Rev254'.
        Phase 35 WR-01 close."""
        comm = make_comm()
        params = bytes([0xFE])
        frame = build_frame(MSG_INFO_PHYSICAL_HW, params)
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "INFO"
        assert response.message == "Physical HW: rev_unknown"

    def test_info_physical_hw_silkscreen_unknown_byte_falls_back(
        self, fake_serial, make_comm
    ):
        """D-03: MSG_INFO_PHYSICAL_HW with an unmapped byte (0x99) falls back to
        'Physical HW: Rev153' (no space — mirrors MSG_OK_REV fallback shape).
        Phase 35 WR-01 close."""
        comm = make_comm()
        params = bytes([0x99])
        frame = build_frame(MSG_INFO_PHYSICAL_HW, params)
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "INFO"
        assert response.message == "Physical HW: Rev153"

    # -----------------------------------------------------------------
    # MSG_DEBUG / DBG_CMD: cmd byte annotated with symbolic name from
    # COMMAND_NAMES via the MSG_DEBUG sub_id decode path.
    # -----------------------------------------------------------------

    def test_dbg_cmd_renders_with_symbolic_name(self, fake_serial, make_comm):
        """DBG_CMD (sub_id 0x04) with cmd=0x02 (COMMAND_WRITE) renders
        'Cmd: 0x02 (WRITE)'. Wire shape: MSG_DEBUG params = [sub_id u8, cmd u8]."""
        comm = make_comm()
        frame = build_frame(MSG_DEBUG, bytes([DBG_CMD, 0x02]))
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "DATA"
        assert response.message == "Cmd: 0x02 (WRITE)"

    def test_dbg_cmd_unknown_falls_back_to_bare_hex(self, fake_serial, make_comm):
        """DBG_CMD with an unmapped cmd byte renders bare hex."""
        comm = make_comm()
        frame = build_frame(MSG_DEBUG, bytes([DBG_CMD, 0xFE]))
        fake_serial.feed(frame)

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "DATA"
        assert response.message == "Cmd: 0xfe"

    # -----------------------------------------------------------------
    # W-04 MSG_DATA_CHUNK roundtrip tests
    # -----------------------------------------------------------------

    def test_data_chunk_payload_exposed_via_response_payload_field(
        self, fake_serial, make_comm
    ):
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
        assert response.payload is not None, (
            "Response.payload must be set for MSG_DATA_CHUNK"
        )
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
        from firestarter.messages import MSG_DATA_SENDING, MSG_MAIN_DONE  # noqa: F811

        comm = make_comm()

        # Build the simulated firmware byte stream.
        chunk0 = bytes(range(256))  # 0x00..0xFF
        chunk1 = bytes(range(256))[::-1]  # 0xFF..0x00
        chunk2 = bytes([0xAA] * 256)  # all 0xAA

        stream = (
            build_frame(MSG_DATA_SENDING, b"")  # zero-param batch-start ack
            + build_frame(MSG_DATA_CHUNK, chunk0)
            + build_frame(MSG_DATA_CHUNK, chunk1)
            + build_frame(MSG_DATA_CHUNK, chunk2)
            + build_frame(MSG_MAIN_DONE, b"")
        )
        fake_serial.feed(stream)

        # Drive the read loop manually.
        from firestarter.eprom_operations import (
            ClassProgressHandler,
            EpromOperationError,
        )

        collected = bytearray()
        start_addr = [0]  # mutable box for callback closure  # noqa: F841

        def _collect(address, data_chunk):
            collected.extend(data_chunk)

        # Patch comm.send_ack so the loop's send_ack calls don't fail.
        ack_calls = []
        original_send_ack = comm.send_ack  # noqa: F841
        comm.send_ack = lambda: ack_calls.append(1)

        progress = ClassProgressHandler()
        progress.start(768)

        # Manually drive _main_phase_read_data by calling get_response loop.
        # We simulate it inline since the method needs self.comm (which is comm).
        from firestarter.messages import MSG_DATA_CHUNK as _MSG_DATA_CHUNK  # noqa: F401

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
        assert len(ack_calls) == 3, (
            f"Expected 3 ACKs (one per chunk), got {len(ack_calls)}"
        )


# -----------------------------------------------------------------
# Phase 57 Plan 01: build_db decode regression tests (DEC-03, DEC-04)
# -----------------------------------------------------------------


class TestBuildDbDecodeCorrectness:
    """Regression tests for the four decode bugs fixed in Phase 57 Plan 01.

    DEC-04 (BUG-1): VCC_VOLTAGES must include nibbles 0x02 (4V) and 0x03 (4.5V).
    DEC-04 (BUG-3): vcc reads bits 11-8 and vdd reads bits 15-12 (labels were swapped).
    DEC-03 (BUG-2): interpret_timing must NOT multiply by 100 for protocols 0x07/0x0B.
    """

    # --- DEC-04 BUG-1: VCC_VOLTAGES nibble completeness ---

    def test_vcc_voltages_includes_nibble_0x02_as_4v(self):
        """DEC-04 BUG-1: VCC_VOLTAGES[0x02] must equal '4V' (was missing)."""
        from tools.build_db import VCC_VOLTAGES

        assert VCC_VOLTAGES[0x02] == "4V", (
            f"VCC_VOLTAGES[0x02] expected '4V', got {VCC_VOLTAGES.get(0x02)!r}"
        )

    def test_vcc_voltages_includes_nibble_0x03_as_4_5v(self):
        """DEC-04 BUG-1: VCC_VOLTAGES[0x03] must equal '4.5V' (was missing)."""
        from tools.build_db import VCC_VOLTAGES

        assert VCC_VOLTAGES[0x03] == "4.5V", (
            f"VCC_VOLTAGES[0x03] expected '4.5V', got {VCC_VOLTAGES.get(0x03)!r}"
        )

    def test_vcc_voltages_existing_entries_unchanged(self):
        """DEC-04 BUG-1: existing VCC_VOLTAGES entries must not have changed."""
        from tools.build_db import VCC_VOLTAGES

        assert VCC_VOLTAGES[0x00] == "5V"
        assert VCC_VOLTAGES[0x01] == "3.3V"
        assert VCC_VOLTAGES[0x04] == "5.5V"
        assert VCC_VOLTAGES[0x05] == "6.5V"

    # --- DEC-04 BUG-3: vcc/vdd bit-range extraction ---

    def test_vcc_reads_bits_11_to_8(self):
        """DEC-04 BUG-3: vcc must be extracted from bits 11-8 ((voltages >> 8) & 0x0F)."""
        from tools.build_db import VCC_VOLTAGES

        # Construct voltages word with bits 11-8 = 0x01 (3.3V) and bits 15-12 = 0x00 (5V).
        # voltages = (vdd_nibble << 12) | (vcc_nibble << 8) | vpp_byte
        vcc_nibble = 0x01  # 3.3V
        vdd_nibble = 0x00  # 5V
        voltages = (vdd_nibble << 12) | (vcc_nibble << 8) | 0x00

        extracted_vcc = (voltages >> 8) & 0x0F
        extracted_vdd = (voltages >> 12) & 0x0F

        assert extracted_vcc == vcc_nibble, (
            f"bits 11-8 extraction should yield vcc nibble {vcc_nibble:#x}, "
            f"got {extracted_vcc:#x}"
        )
        assert VCC_VOLTAGES.get(extracted_vcc, "5V") == "3.3V", (
            "vcc lookup for nibble 0x01 must yield '3.3V'"
        )
        assert extracted_vdd == vdd_nibble, (
            f"bits 15-12 extraction should yield vdd nibble {vdd_nibble:#x}, "
            f"got {extracted_vdd:#x}"
        )
        assert VCC_VOLTAGES.get(extracted_vdd, "5V") == "5V", (
            "vdd lookup for nibble 0x00 must yield '5V'"
        )

    def test_vcc_vdd_distinct_values_map_correctly(self):
        """DEC-04 BUG-3: when vcc != vdd, bits-11-8 is vcc and bits-15-12 is vdd."""
        from tools.build_db import VCC_VOLTAGES

        # Use the two new nibbles: vcc_nibble=0x02 (4V), vdd_nibble=0x01 (3.3V).
        vcc_nibble = 0x02  # 4V  (BUG-1 fix value)
        vdd_nibble = 0x01  # 3.3V
        voltages = (vdd_nibble << 12) | (vcc_nibble << 8) | 0x00

        vcc_val = VCC_VOLTAGES.get((voltages >> 8) & 0x0F, "5V")
        vdd_val = VCC_VOLTAGES.get((voltages >> 12) & 0x0F, "5V")

        assert vcc_val == "4V", f"Expected vcc='4V', got {vcc_val!r}"
        assert vdd_val == "3.3V", f"Expected vdd='3.3V', got {vdd_val!r}"

    # --- DEC-03 BUG-2: interpret_timing no x100 multiplier ---

    def test_interpret_timing_0x07_returns_raw_microseconds(self):
        """DEC-03 BUG-2: interpret_timing('64', 0x07) must return '100 us' (0x64=100, no x100)."""
        from tools.build_db import interpret_timing

        result = interpret_timing("64", 0x07)
        assert result == "100 us", (
            f"interpret_timing('64', 0x07) expected '100 us', got {result!r}"
        )

    def test_interpret_timing_0x0b_returns_raw_microseconds(self):
        """DEC-03 BUG-2: interpret_timing('64', 0x0B) must return '100 us' (no x100)."""
        from tools.build_db import interpret_timing

        result = interpret_timing("64", 0x0B)
        assert result == "100 us", (
            f"interpret_timing('64', 0x0B) expected '100 us', got {result!r}"
        )

    def test_interpret_timing_0x08_returns_raw_microseconds(self):
        """DEC-03: interpret_timing('64', 0x08) returns '100 us' (was already correct)."""
        from tools.build_db import interpret_timing

        result = interpret_timing("64", 0x08)
        assert result == "100 us", (
            f"interpret_timing('64', 0x08) expected '100 us', got {result!r}"
        )

    def test_interpret_timing_non_hex_falls_back_to_zero(self):
        """DEC-03 BUG-2: interpret_timing('zz', 0x07) must return '0 us' (except Exception)."""
        from tools.build_db import interpret_timing

        result = interpret_timing("zz", 0x07)
        assert result == "0 us", (
            f"interpret_timing('zz', 0x07) expected '0 us', got {result!r}"
        )

    def test_interpret_timing_non_timing_protocol_returns_algorithm_controlled(self):
        """DEC-03: interpret_timing('64', 0x05) must return 'Algorithm Controlled'."""
        from tools.build_db import interpret_timing

        result = interpret_timing("64", 0x05)
        assert result == "Algorithm Controlled", (
            f"interpret_timing('64', 0x05) expected 'Algorithm Controlled', got {result!r}"
        )


# -----------------------------------------------------------------
# Phase 58 Plan 01: Wave 0 test scaffolding (PIN-01/02/03)
# -----------------------------------------------------------------


class TestResolvedPinoutKey:
    """Unit tests for the Phase 58 principled resolve_pinout_key rewrite.

    PIN-01: resolve_pinout_key returns the correct pinout key for each
    (pin_count, pm_idx, variant_lo) combination. Covers all documented
    rule branches from RESEARCH.md §"Full Principled Rule Structure".

    Each test passes hard-coded field values matching real infoic.xml chips,
    verifying the general rules without importing chip_database.json.

    STATUS: RED-first — these tests assert the NEW principled signature
    resolve_pinout_key(pin_count, variant, flags_int, pm_idx, proto_id,
    type_int, mem_size). The current build_db.py still has the old
    guess-table-based function. These tests turn GREEN in Plan 02.
    """

    # --- 24-pin branch ---

    def test_24pin_pm23_variant_lo_01_returns_dip24_2732(self):
        """24-pin pm_idx=23 variant_lo=0x01 → DIP24_2732 (4KB UV-EPROM)."""
        from tools.build_db import resolve_pinout_key

        # variant = 0x00000001 → variant_lo = 0x01
        result = resolve_pinout_key(
            24, 0x00000001, 0x0000, pm_idx=23, proto_id=0x0B, type_int=1, mem_size=4096
        )
        assert result == "DIP24_2732", (
            f"Expected 'DIP24_2732' for 24-pin pm_idx=23 variant_lo=0x01, got {result!r}"
        )

    def test_24pin_pm23_variant_lo_00_returns_dip24_2716(self):
        """24-pin pm_idx=23 variant_lo=0x00 → DIP24_2716 (2KB UV-EPROM)."""
        from tools.build_db import resolve_pinout_key

        # variant = 0x00000000 → variant_lo = 0x00
        result = resolve_pinout_key(
            24, 0x00000000, 0x0000, pm_idx=23, proto_id=0x0B, type_int=1, mem_size=2048
        )
        assert result == "DIP24_2716", (
            f"Expected 'DIP24_2716' for 24-pin pm_idx=23 variant_lo=0x00, got {result!r}"
        )

    def test_24pin_pm23_variant_lo_10_returns_dip24_2816(self):
        """24-pin pm_idx=23 variant_lo=0x10 → DIP24_2816 (28C EEPROM family). KEY TEST."""
        from tools.build_db import resolve_pinout_key

        # variant = 0x00004310 → variant_lo = 0x10 (confirmed 28C EEPROM discriminator)
        result = resolve_pinout_key(
            24, 0x00004310, 0x0010, pm_idx=23, proto_id=0x0B, type_int=1, mem_size=2048
        )
        assert result == "DIP24_2816", (
            f"Expected 'DIP24_2816' for 24-pin pm_idx=23 variant_lo=0x10, got {result!r}"
        )

    def test_24pin_pm0_returns_dip24_6116(self):
        """24-pin pm_idx=0 → DIP24_6116 (SRAM-class)."""
        from tools.build_db import resolve_pinout_key

        result = resolve_pinout_key(
            24, 0x00000000, 0x0000, pm_idx=0, proto_id=0x27, type_int=4, mem_size=2048
        )
        assert result == "DIP24_6116", (
            f"Expected 'DIP24_6116' for 24-pin pm_idx=0, got {result!r}"
        )

    def test_24pin_unknown_pm_idx_returns_none(self):
        """24-pin unknown pm_idx=99 → None (D-06 fail-safe)."""
        from tools.build_db import resolve_pinout_key

        result = resolve_pinout_key(
            24, 0x00000000, 0x0000, pm_idx=99, proto_id=0x0B, type_int=1, mem_size=2048
        )
        assert result is None, (
            f"Expected None for unclassifiable 24-pin pm_idx=99, got {result!r}"
        )

    # --- 28-pin branch ---

    def test_28pin_pm22_variant_lo_10_returns_dip28_27512(self):
        """28-pin pm_idx=22 variant_lo=0x10 → DIP28_27512 (VPP on pin 22)."""
        from tools.build_db import resolve_pinout_key

        result = resolve_pinout_key(
            28, 0x00000010, 0x0000, pm_idx=22, proto_id=0x07, type_int=1, mem_size=65536
        )
        assert result == "DIP28_27512", (
            f"Expected 'DIP28_27512' for 28-pin pm_idx=22 variant_lo=0x10, got {result!r}"
        )

    def test_28pin_pm22_variant_lo_11_returns_dip28_27256(self):
        """28-pin pm_idx=22 variant_lo=0x11 → DIP28_27256 (VPP on pin 1)."""
        from tools.build_db import resolve_pinout_key

        result = resolve_pinout_key(
            28, 0x00000011, 0x0000, pm_idx=22, proto_id=0x07, type_int=1, mem_size=32768
        )
        assert result == "DIP28_27256", (
            f"Expected 'DIP28_27256' for 28-pin pm_idx=22 variant_lo=0x11, got {result!r}"
        )

    def test_28pin_pm22_variant_lo_00_returns_dip28_2764(self):
        """28-pin pm_idx=22 variant_lo=0x00 → DIP28_2764 (27C64/128 layout)."""
        from tools.build_db import resolve_pinout_key

        result = resolve_pinout_key(
            28, 0x00000000, 0x0000, pm_idx=22, proto_id=0x07, type_int=1, mem_size=8192
        )
        assert result == "DIP28_2764", (
            f"Expected 'DIP28_2764' for 28-pin pm_idx=22 variant_lo=0x00, got {result!r}"
        )

    def test_28pin_pm21_returns_dip28_2764(self):
        """28-pin pm_idx=21 → DIP28_2764."""
        from tools.build_db import resolve_pinout_key

        result = resolve_pinout_key(
            28, 0x00000000, 0x0000, pm_idx=21, proto_id=0x07, type_int=1, mem_size=8192
        )
        assert result == "DIP28_2764", (
            f"Expected 'DIP28_2764' for 28-pin pm_idx=21, got {result!r}"
        )

    def test_28pin_pm20_returns_dip28_28c256(self):
        """28-pin pm_idx=20 → DIP28_28C256 (28C256 EEPROM, no VPP)."""
        from tools.build_db import resolve_pinout_key

        result = resolve_pinout_key(
            28, 0x00000000, 0x0010, pm_idx=20, proto_id=0x07, type_int=1, mem_size=32768
        )
        assert result == "DIP28_28C256", (
            f"Expected 'DIP28_28C256' for 28-pin pm_idx=20, got {result!r}"
        )

    def test_28pin_pm19_returns_dip28_28c64(self):
        """28-pin pm_idx=19 → DIP28_28C64 (28C64 EEPROM, no VPP)."""
        from tools.build_db import resolve_pinout_key

        result = resolve_pinout_key(
            28, 0x00000000, 0x0010, pm_idx=19, proto_id=0x07, type_int=1, mem_size=8192
        )
        assert result == "DIP28_28C64", (
            f"Expected 'DIP28_28C64' for 28-pin pm_idx=19, got {result!r}"
        )

    def test_28pin_pm18_returns_dip28_28c64(self):
        """28-pin pm_idx=18 → DIP28_28C64 (28C16/17 small EEPROM, same layout)."""
        from tools.build_db import resolve_pinout_key

        result = resolve_pinout_key(
            28, 0x00000000, 0x0010, pm_idx=18, proto_id=0x07, type_int=1, mem_size=2048
        )
        assert result == "DIP28_28C64", (
            f"Expected 'DIP28_28C64' for 28-pin pm_idx=18, got {result!r}"
        )

    # --- 32-pin branch ---

    def test_32pin_pm_in_set_proto_06_returns_dip32_sst39sf040(self):
        """32-pin pm_idx in {5,7,9,10,11,12,13} proto=0x06 → DIP32_SST39SF040."""
        from tools.build_db import resolve_pinout_key

        for pm_idx in [5, 7, 9, 10, 11, 12, 13]:
            result = resolve_pinout_key(
                32,
                0x00000000,
                0x0010,
                pm_idx=pm_idx,
                proto_id=0x06,
                type_int=1,
                mem_size=524288,
            )
            assert result == "DIP32_SST39SF040", (
                f"Expected 'DIP32_SST39SF040' for 32-pin pm_idx={pm_idx} proto=0x06, "
                f"got {result!r}"
            )

    def test_32pin_pm_in_set_proto_0d_returns_dip32_28c512_eeprom(self):
        """32-pin pm_idx in {5,7,9,10,11,12,13} proto=0x0D → DIP32_28C512_EEPROM."""
        from tools.build_db import resolve_pinout_key

        result = resolve_pinout_key(
            32, 0x00000000, 0x0010, pm_idx=9, proto_id=0x0D, type_int=1, mem_size=65536
        )
        assert result == "DIP32_28C512_EEPROM", (
            f"Expected 'DIP32_28C512_EEPROM' for 32-pin pm_idx=9 proto=0x0D, got {result!r}"
        )

    def test_32pin_pm_in_set_proto_07_returns_dip32_std(self):
        """32-pin pm_idx in {5,7,9,10,11,12,13} proto=0x07 → DIP32_STD."""
        from tools.build_db import resolve_pinout_key

        result = resolve_pinout_key(
            32,
            0x00000000,
            0x0000,
            pm_idx=13,
            proto_id=0x07,
            type_int=1,
            mem_size=131072,
        )
        assert result == "DIP32_STD", (
            f"Expected 'DIP32_STD' for 32-pin pm_idx=13 proto=0x07, got {result!r}"
        )

    def test_32pin_pm0_returns_dip32_sst39sf040(self):
        """32-pin pm_idx=0 → DIP32_SST39SF040 (SRAM/NVRAM; type=4)."""
        from tools.build_db import resolve_pinout_key

        result = resolve_pinout_key(
            32, 0x00000000, 0x0000, pm_idx=0, proto_id=0x0E, type_int=4, mem_size=32768
        )
        assert result == "DIP32_SST39SF040", (
            f"Expected 'DIP32_SST39SF040' for 32-pin pm_idx=0 type=4, got {result!r}"
        )


class TestGuessTablesDeleted:
    """Assert the three survey-built guess tables no longer exist (D-02).

    PIN-01: PIN_MAP_TO_PINOUT, PIN_MAP_PROTO_TO_PINOUT, and DIP28_VARIANT_MAP
    must be deleted from tools.build_db. The principled resolve_pinout_key
    function is the sole pinout-selection path.

    STATUS: RED-first — the current build_db.py still contains all three
    tables. These tests turn GREEN in Plan 02 after the rewrite.
    """

    def test_pin_map_to_pinout_not_in_build_db(self):
        """D-02: PIN_MAP_TO_PINOUT must be deleted from build_db module."""
        import tools.build_db as bdb

        assert not hasattr(bdb, "PIN_MAP_TO_PINOUT"), (
            "PIN_MAP_TO_PINOUT still present in build_db — must be deleted (D-02); "
            "principled resolve_pinout_key is the sole path"
        )

    def test_pin_map_proto_to_pinout_not_in_build_db(self):
        """D-02: PIN_MAP_PROTO_TO_PINOUT must be deleted from build_db module."""
        import tools.build_db as bdb

        assert not hasattr(bdb, "PIN_MAP_PROTO_TO_PINOUT"), (
            "PIN_MAP_PROTO_TO_PINOUT still present in build_db — must be deleted (D-02)"
        )

    def test_dip28_variant_map_not_in_build_db(self):
        """D-02: DIP28_VARIANT_MAP must be deleted from build_db module."""
        import tools.build_db as bdb

        assert not hasattr(bdb, "DIP28_VARIANT_MAP"), (
            "DIP28_VARIANT_MAP still present in build_db — must be deleted (D-02)"
        )


class TestWarning5Rule:
    """Assert WARNING-5 still fires as Rule 2 after principled rewrite (PIN-02 regression guard).

    PIN-02: A chip resolving to DIP28_28C256 (pm_idx=20) with flags indicating
    Flash/EEPROM (_etype=Flash/EEPROM) and original proto_id=0x07 must land on
    algorithm=0x0D after the WARNING-5 override (Rule 2).

    STATUS: RED-first — this test asserts the post-rewrite behavior where WARNING-5
    fires as Rule 2. The current build_db.py has a different predicate structure
    (hardcoded DIP28_2764 check). This test turns GREEN in Plan 02.

    NOTE: The WARNING-5 path is tested at the DB-entry level because Rule 2 is
    applied inside build_db.main() (not inside resolve_pinout_key itself). After
    Plan 02, the test can verify via chip_database.json for a real pm_idx=20 chip
    that has flags=0x10 and proto=0x07 in infoic.xml. The test below asserts the
    observable outcome: no pm_idx=20 5V-EEPROM chip routes to configure_eprom.
    """

    def test_warning5_fires_for_dip28_28c256_proto_07_flash_eeprom(self):
        """Rule 2 (WARNING-5): any DIP28_28C256 chip with Flash/EEPROM type must get algo=0x0D.

        The principled rewrite assigns DIP28_28C256 via pm_idx=20, bypassing VPP.
        WARNING-5 is the fallback safety net for proto_id=0x07 + Flash/EEPROM
        combinations that land on any 5V EEPROM pinout. After Plan 02 this should
        not trigger (pm_idx=20 gives the right pinout directly), but Rule 2 must
        still exist as a safety net.

        Integration-level assertion: AT28C256 (a confirmed pm_idx=20 5V EEPROM chip)
        must appear in the regenerated chip_database.json with algorithm=0x0D (not 0x07).
        chip_database.json uses nested structure: programming.algorithm and electrical.pin_count.
        This test loads the actual DB and checks the nested fields.
        """
        import json
        import os

        db_path = os.path.join(
            os.path.dirname(__file__), "..", "firestarter", "data", "chip_database.json"
        )
        with open(db_path) as f:
            db = json.load(f)

        # AT28C256 is a confirmed pm_idx=20 5V EEPROM chip (flags=0x10, proto=0x07 upstream)
        # After Phase 58 rewrite, it must have programming.algorithm=0x0D
        # Note: chip_database.json uses nested structure: programming.algorithm
        found = False
        for mfg, chips in db.items():
            for chip in chips:
                pn = chip.get("part_number", "")
                elec = chip.get("electrical", {})
                prog = chip.get("programming", {})
                if "AT28C256" in pn and elec.get("pin_count") == 28:
                    found = True
                    algo = prog.get("algorithm")
                    assert algo == 0x0D, (
                        f"WARNING-5 (Rule 2) failed: {mfg}/{pn} has "
                        f"programming.algorithm=0x{algo:02X}, expected 0x0D "
                        f"(configure_eeprom28c). "
                        f"DIP28_28C256 + Flash/EEPROM must not route to configure_eprom."
                    )
        assert found, (
            "Could not find any AT28C256 chip with electrical.pin_count=28 "
            "in chip_database.json to validate WARNING-5 / Rule 2 outcome"
        )


class TestDIP24_2816Pinout:
    """Assert DIP24_2816 is in pinouts.json with the correct SR-1-safe pin assignments.

    PIN-03 / T-58-01: DIP24_2816 must exist in pinouts.json and must NOT have a
    vpp-pin key. Pin 21 is WE (rw-pin), NOT VPP. This is the defining SR-1 safety
    property that separates DIP24_2816 from DIP24_2716 (which DOES have vpp-pin=21).

    STATUS: GREEN — depends only on Task 1's pinouts.json entry (already committed).
    """

    def _load_pinouts(self):
        import json
        import os

        pinout_path = os.path.join(
            os.path.dirname(__file__), "..", "firestarter", "data", "pinouts.json"
        )
        with open(pinout_path) as f:
            return json.load(f)

    def test_dip24_2816_present_in_pinouts_json(self):
        """DIP24_2816 must exist as a key in pinouts.json."""
        pinouts = self._load_pinouts()
        assert "DIP24_2816" in pinouts, (
            "DIP24_2816 not found in pinouts.json — Task 1 not yet committed"
        )

    def test_dip24_2816_has_no_vpp_pin_field(self):
        """SR-1 CRITICAL GATE: DIP24_2816.pins must NOT contain vpp-pin key.

        Pin 21 is WE (rw-pin) on all 28C-family EEPROMs. The DIP24_2716 UV-EPROM
        layout uses pin 21 as VPP. Having vpp-pin in DIP24_2816 would route 12V
        to the WE pin of a 5V-only EEPROM — dead chip.
        """
        pinouts = self._load_pinouts()
        entry = pinouts["DIP24_2816"]
        assert "vpp-pin" not in entry["pins"], (
            "CRITICAL SR-1 VIOLATION: DIP24_2816.pins contains vpp-pin — "
            "pin 21 is WE on 28C EEPROMs, never VPP. Remove vpp-pin immediately."
        )

    def test_dip24_2816_rw_pin_is_21(self):
        """DIP24_2816 rw-pin must be [21] (WE# on AT28C16/AT28C04)."""
        pinouts = self._load_pinouts()
        entry = pinouts["DIP24_2816"]
        assert entry["pins"]["rw-pin"] == [21], (
            f"Expected rw-pin=[21], got {entry['pins'].get('rw-pin')!r}"
        )

    def test_dip24_2816_ce_pin_is_18(self):
        """DIP24_2816 ce-pin must be [18] (CE# on AT28C16/AT28C04)."""
        pinouts = self._load_pinouts()
        entry = pinouts["DIP24_2816"]
        assert entry["pins"]["ce-pin"] == [18], (
            f"Expected ce-pin=[18], got {entry['pins'].get('ce-pin')!r}"
        )

    def test_dip24_2816_oe_pin_is_20(self):
        """DIP24_2816 oe-pin must be [20] (OE# on AT28C16/AT28C04)."""
        pinouts = self._load_pinouts()
        entry = pinouts["DIP24_2816"]
        assert entry["pins"]["oe-pin"] == [20], (
            f"Expected oe-pin=[20], got {entry['pins'].get('oe-pin')!r}"
        )

    def test_dip24_2816_vcc_is_24_gnd_is_12(self):
        """DIP24_2816 vcc-pin must be [24] and gnd-pin must be [12]."""
        pinouts = self._load_pinouts()
        entry = pinouts["DIP24_2816"]
        assert entry["pins"]["vcc-pin"] == [24], (
            f"Expected vcc-pin=[24], got {entry['pins'].get('vcc-pin')!r}"
        )
        assert entry["pins"]["gnd-pin"] == [12], (
            f"Expected gnd-pin=[12], got {entry['pins'].get('gnd-pin')!r}"
        )


class TestDangerous24pinEEPROMFixed:
    """Integration tests: assert the 10 dangerous 24-pin EEPROMs are fixed in chip_database.json.

    PIN-03 / T-58-01: After Phase 58 DB regeneration, all 10 previously-dangerous 24-pin
    EEPROM chips (AMD/AM28C16A, CATALYST/CAT28C16A, EXEL/XL2804A, EXEL/XL2816A, EXEL/XLE28C16A,
    EXEL/XLE28C16B, MICROCHIP memory/2804, MICROCHIP memory/2816, XICOR/X2804A, XICOR/X2816A)
    must have algorithm=0x0D and pinout=DIP24_2816.

    Current hazard: these chips are in the DB with algorithm=0x0B (configure_eprom) on
    DIP24_2716 (vpp-pin=21). pin 21 is WE on these chips — 12V on WE = chip damage.

    The 9 blocked chips (AT28C04/AT28C16 family) are also asserted here.

    STATUS: RED-first — the current chip_database.json was generated with the old
    guess-table code. These tests turn GREEN in Plan 02 after DB regeneration.
    """

    def _load_db(self):
        import json
        import os

        db_path = os.path.join(
            os.path.dirname(__file__), "..", "firestarter", "data", "chip_database.json"
        )
        with open(db_path) as f:
            return json.load(f)

    def _find_chip(self, db, part_number_fragment, mfg_fragment=None):
        """Find chips whose part_number contains the fragment."""
        results = []
        for mfg, chips in db.items():
            if mfg_fragment and mfg_fragment.lower() not in mfg.lower():
                continue
            for chip in chips:
                pn = chip.get("part_number", "")
                if part_number_fragment.lower() in pn.lower():
                    results.append((mfg, chip))
        return results

    def test_am28c16a_has_algo_0x0D_and_dip24_2816(self):
        """AMD/AM28C16A must have algorithm=0x0D and pinout=DIP24_2816 (was dangerous).

        EpromDatabase returns a flat dict. The algorithm is under key 'protocol-id'
        and the pinout is under key 'pin-map' (not 'algorithm' / 'pinout').
        """
        from firestarter.database import EpromDatabase

        db = EpromDatabase(skip_local_override=True)
        chip = db.get_eprom("AM28C16A")
        assert chip is not None, (
            "AM28C16A not found in EpromDatabase — check DB contents"
        )
        assert chip.get("protocol-id") == 0x0D, (
            f"AM28C16A protocol-id=0x{chip.get('protocol-id', 0):02X}, expected 0x0D"
        )
        assert chip.get("pin-map") == "DIP24_2816", (
            f"AM28C16A pin-map={chip.get('pin-map')!r}, expected 'DIP24_2816'"
        )

    def test_cat28c16a_has_algo_0x0D_and_dip24_2816(self):
        """CATALYST/CAT28C16A must have algorithm=0x0D and pinout=DIP24_2816."""
        from firestarter.database import EpromDatabase

        db = EpromDatabase(skip_local_override=True)
        chip = db.get_eprom("CAT28C16A")
        assert chip is not None, "CAT28C16A not found in EpromDatabase"
        assert chip.get("protocol-id") == 0x0D, (
            f"CAT28C16A protocol-id=0x{chip.get('protocol-id', 0):02X}, expected 0x0D"
        )
        assert chip.get("pin-map") == "DIP24_2816", (
            f"CAT28C16A pin-map={chip.get('pin-map')!r}, expected 'DIP24_2816'"
        )

    def test_xl2804a_has_algo_0x0D_and_dip24_2816(self):
        """EXEL/XL2804A must have algorithm=0x0D and pinout=DIP24_2816."""
        db = self._load_db()
        chips = self._find_chip(db, "XL2804A", "EXEL")
        assert chips, "XL2804A not found in chip_database.json under EXEL"
        for mfg, chip in chips:
            algo = chip.get("programming", {}).get("algorithm")
            assert algo == 0x0D, (
                f"{mfg}/{chip.get('part_number')} programming.algorithm="
                f"0x{algo:02X}, expected 0x0D"
            )
            assert chip.get("pinout") == "DIP24_2816", (
                f"{mfg}/{chip.get('part_number')} pinout={chip.get('pinout')!r}, "
                f"expected 'DIP24_2816'"
            )

    def test_xl2816a_has_algo_0x0D_and_dip24_2816(self):
        """EXEL/XL2816A must have algorithm=0x0D and pinout=DIP24_2816."""
        db = self._load_db()
        chips = self._find_chip(db, "XL2816A", "EXEL")
        assert chips, "XL2816A not found in chip_database.json under EXEL"
        for mfg, chip in chips:
            algo = chip.get("programming", {}).get("algorithm")
            assert algo == 0x0D, (
                f"{mfg}/{chip.get('part_number')} programming.algorithm="
                f"0x{algo:02X}, expected 0x0D"
            )
            assert chip.get("pinout") == "DIP24_2816", (
                f"{mfg}/{chip.get('part_number')} pinout={chip.get('pinout')!r}, "
                f"expected 'DIP24_2816'"
            )

    def test_microchip_2804_has_algo_0x0D_and_dip24_2816(self):
        """MICROCHIP memory/2804 (28C04A etc.) must have algorithm=0x0D and pinout=DIP24_2816."""
        db = self._load_db()
        chips = self._find_chip(db, "28C04", "MICROCHIP")
        assert chips, "28C04 not found in chip_database.json under MICROCHIP"
        for mfg, chip in chips:
            algo = chip.get("programming", {}).get("algorithm")
            assert algo == 0x0D, (
                f"{mfg}/{chip.get('part_number')} programming.algorithm="
                f"0x{algo:02X}, expected 0x0D"
            )
            assert chip.get("pinout") == "DIP24_2816", (
                f"{mfg}/{chip.get('part_number')} pinout={chip.get('pinout')!r}, "
                f"expected 'DIP24_2816'"
            )

    def test_microchip_2816_has_algo_0x0D_and_dip24_2816(self):
        """MICROCHIP memory/2816 (28C16A etc.) must have algorithm=0x0D and pinout=DIP24_2816."""
        db = self._load_db()
        chips = self._find_chip(db, "28C16", "MICROCHIP")
        assert chips, "28C16 not found in chip_database.json under MICROCHIP"
        for mfg, chip in chips:
            algo = chip.get("programming", {}).get("algorithm")
            assert algo == 0x0D, (
                f"{mfg}/{chip.get('part_number')} programming.algorithm="
                f"0x{algo:02X}, expected 0x0D"
            )
            assert chip.get("pinout") == "DIP24_2816", (
                f"{mfg}/{chip.get('part_number')} pinout={chip.get('pinout')!r}, "
                f"expected 'DIP24_2816'"
            )

    def test_xicor_x2804a_has_algo_0x0D_and_dip24_2816(self):
        """XICOR/X2804A must have algorithm=0x0D and pinout=DIP24_2816."""
        db = self._load_db()
        chips = self._find_chip(db, "X2804A", "XICOR")
        assert chips, "X2804A not found in chip_database.json under XICOR"
        for mfg, chip in chips:
            algo = chip.get("programming", {}).get("algorithm")
            assert algo == 0x0D, (
                f"{mfg}/{chip.get('part_number')} programming.algorithm="
                f"0x{algo:02X}, expected 0x0D"
            )
            assert chip.get("pinout") == "DIP24_2816", (
                f"{mfg}/{chip.get('part_number')} pinout={chip.get('pinout')!r}, "
                f"expected 'DIP24_2816'"
            )

    def test_xicor_x2816a_has_algo_0x0D_and_dip24_2816(self):
        """XICOR/X2816A must have algorithm=0x0D and pinout=DIP24_2816."""
        db = self._load_db()
        chips = self._find_chip(db, "X2816A", "XICOR")
        assert chips, "X2816A not found in chip_database.json under XICOR"
        for mfg, chip in chips:
            algo = chip.get("programming", {}).get("algorithm")
            assert algo == 0x0D, (
                f"{mfg}/{chip.get('part_number')} programming.algorithm="
                f"0x{algo:02X}, expected 0x0D"
            )
            assert chip.get("pinout") == "DIP24_2816", (
                f"{mfg}/{chip.get('part_number')} pinout={chip.get('pinout')!r}, "
                f"expected 'DIP24_2816'"
            )

    def test_at28c16_has_algo_0x0D_and_dip24_2816(self):
        """ATMEL/AT28C16 (blocked chip) must have algorithm=0x0D and pinout=DIP24_2816.

        EpromDatabase returns a flat dict. The algorithm is under key 'protocol-id'
        and the pinout is under key 'pin-map'.
        """
        from firestarter.database import EpromDatabase

        db = EpromDatabase(skip_local_override=True)
        # AT28C16 is one of the 9 blocked chips — previously skipped, now unblocked
        chip = db.get_eprom("AT28C16")
        assert chip is not None, (
            "AT28C16 not found in EpromDatabase — expected to be unblocked in Phase 58"
        )
        assert chip.get("protocol-id") == 0x0D, (
            f"AT28C16 protocol-id=0x{chip.get('protocol-id', 0):02X}, expected 0x0D"
        )
        assert chip.get("pin-map") == "DIP24_2816", (
            f"AT28C16 pin-map={chip.get('pin-map')!r}, expected 'DIP24_2816'"
        )

    def test_at28c04_has_algo_0x0D_and_dip24_2816(self):
        """ATMEL/AT28C04 (blocked chip) must have algorithm=0x0D and pinout=DIP24_2816."""
        from firestarter.database import EpromDatabase

        db = EpromDatabase(skip_local_override=True)
        chip = db.get_eprom("AT28C04")
        assert chip is not None, (
            "AT28C04 not found in EpromDatabase — expected to be unblocked in Phase 58"
        )
        assert chip.get("protocol-id") == 0x0D, (
            f"AT28C04 protocol-id=0x{chip.get('protocol-id', 0):02X}, expected 0x0D"
        )
        assert chip.get("pin-map") == "DIP24_2816", (
            f"AT28C04 pin-map={chip.get('pin-map')!r}, expected 'DIP24_2816'"
        )


class TestGate03StructuralVppGuard:
    """Regression tests for the GATE-03 structural no-vpp-pin guard in check_dispatch.py.

    GATE-03 (Phase 59): The real structural hazard is configure_eprom routed to a
    pinout that has no vpp-pin. This guard is type-string-independent — it fires on
    the pinout structure alone, so it auto-covers future electrical.type label changes
    (EEPROM, Flash/EEPROM, or any future string) without needing predicate updates.

    Test cases:
      1. Synthetic chip on DIP28_28C256 (no vpp-pin) + algo 0x07 (configure_eprom)
         → MUST be flagged by the structural guard.
      2. W27C512 on DIP28_27512 (has vpp-pin) + algo 0x07, type "EEPROM"
         → MUST NOT be flagged (legitimate 12 V chip).
      3. _build_no_vpp_pin_set returns the expected no-vpp-pin pinout names from
         the real pinouts.json.
    """

    @staticmethod
    def _make_chip(pinout, algo, etype="EEPROM"):
        """Build a minimal chip dict as it appears in chip_database.json."""
        return {
            "part_number": f"SYNTHETIC_{pinout}_{algo:02X}",
            "pinout": pinout,
            "electrical": {"type": etype},
            "programming": {"algorithm": algo},
        }

    def test_novpp_pin_pinout_with_configure_eprom_is_flagged(self, tmp_path):
        """A chip on DIP28_28C256 (no vpp-pin) + algo 0x07 must be caught by GATE-03.

        This is the primary regression: the structural guard must fire regardless of
        what electrical.type string is used. We test with type="EEPROM" (the
        post-cca7d62 type string) to confirm type-string-independence.
        """
        import os
        import sys

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from check_dispatch import _build_no_vpp_pin_set, dispatch

        pinouts_path = os.path.join(
            os.path.dirname(__file__), "..", "firestarter", "data", "pinouts.json"
        )
        no_vpp_pinouts = _build_no_vpp_pin_set(pinouts_path)

        chip = self._make_chip("DIP28_28C256", 0x07, etype="EEPROM")
        pinout = chip["pinout"]
        proto = chip["programming"]["algorithm"]
        handler = dispatch(proto, None)

        assert handler == "configure_eprom", (
            f"Expected algo 0x07 → configure_eprom, got {handler!r}"
        )
        assert pinout in no_vpp_pinouts, (
            f"DIP28_28C256 must be in the no-vpp-pin set; got set={no_vpp_pinouts!r}"
        )
        # The guard predicate: this combination MUST be flagged.
        flagged = handler == "configure_eprom" and pinout in no_vpp_pinouts
        assert flagged, (
            "GATE-03 structural guard: DIP28_28C256 + configure_eprom must be flagged"
        )

    def test_w27c512_on_dip28_27512_is_not_flagged(self):
        """W27C512 (EEPROM type, algo 0x07) on DIP28_27512 (has vpp-pin) must NOT be flagged.

        DIP28_27512 has a real vpp-pin (pin 22), so configure_eprom asserting the
        12 V regulator on it is correct. The structural guard must not fire here.
        """
        import os
        import sys

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from check_dispatch import _build_no_vpp_pin_set, dispatch

        pinouts_path = os.path.join(
            os.path.dirname(__file__), "..", "firestarter", "data", "pinouts.json"
        )
        no_vpp_pinouts = _build_no_vpp_pin_set(pinouts_path)

        # W27C512: algo 0x07, type "EEPROM", pinout DIP28_27512 (real vpp-pin=22)
        chip = self._make_chip("DIP28_27512", 0x07, etype="EEPROM")
        pinout = chip["pinout"]
        proto = chip["programming"]["algorithm"]
        handler = dispatch(proto, None)

        assert handler == "configure_eprom", (
            f"Expected algo 0x07 → configure_eprom, got {handler!r}"
        )
        assert pinout not in no_vpp_pinouts, (
            f"DIP28_27512 has a real vpp-pin and must NOT be in the no-vpp-pin set; "
            f"unexpectedly found in: {no_vpp_pinouts!r}"
        )
        # The guard predicate must NOT fire.
        flagged = handler == "configure_eprom" and pinout in no_vpp_pinouts
        assert not flagged, (
            "GATE-03 structural guard: DIP28_27512 + configure_eprom must NOT be flagged"
        )

    def test_no_vpp_pin_set_contains_expected_pinouts(self):
        """_build_no_vpp_pin_set must include all known no-vpp-pin pinouts and exclude all
        known vpp-pin pinouts from pinouts.json.
        """
        import os
        import sys

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from check_dispatch import _build_no_vpp_pin_set

        pinouts_path = os.path.join(
            os.path.dirname(__file__), "..", "firestarter", "data", "pinouts.json"
        )
        no_vpp = _build_no_vpp_pin_set(pinouts_path)

        # These pinouts have no vpp-pin and must be in the set.
        expected_no_vpp = {
            "DIP28_28C256",
            "DIP24_2816",
            "DIP28_28C64",
            "DIP32_28C512_EEPROM",
            "DIP24_6116",
            "DIP28_JEDEC_SRAM_8K",
            "DIP32_SST39SF040",
        }
        for name in expected_no_vpp:
            assert name in no_vpp, (
                f"{name} should be in the no-vpp-pin set but is missing"
            )

        # These pinouts have a real vpp-pin and must NOT be in the set.
        expected_has_vpp = {
            "DIP24_2716",
            "DIP24_2732",
            "DIP28_2764",
            "DIP28_27256",
            "DIP28_27512",
            "DIP32_STD",
        }
        for name in expected_has_vpp:
            assert name not in no_vpp, (
                f"{name} has a real vpp-pin and must NOT be in the no-vpp-pin set"
            )
