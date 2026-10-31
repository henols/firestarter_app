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
    DBG_CMD,
    MSG_DATA_CHUNK,
    MSG_DATA_PROGRESS,
    MSG_DATA_SENDING,
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
from firestarter.serial_comm import LogMessage, Response

from .conftest import (
    build_frame,
    connect_via,
    fresh_serial_and_comm,
    id_frame_body,
    read_phase_frames,
)


def _drive_one_response(comm, timeout: float = 1.0):
    """Pull exactly one Response off the read loop, returning None on timeout."""
    gen = comm._read_and_parse_lines(timeout=timeout)
    try:
        return next(gen)
    except StopIteration:
        return None


def _cfg_params(override: int, bandgap_mv: int, bandgap_count: int) -> bytes:
    """MSG_OK_CFG params: u32 r1=10000, u32 r2=4700, u8 override, u16 bandgap
    in mV, u16 bandgap count."""
    return (
        struct.pack(">II", 10000, 4700)
        + bytes([override])
        + struct.pack(">HH", bandgap_mv, bandgap_count)
    )


# One frame in, one (severity label, rendered text) out. Each row names the
# catalog shape or render rule it pins.
_RENDER_CASES = [
    # LHOST-01: a zero-param frame.
    pytest.param(MSG_OK_READY, b"", "OK", "Ready", id="ok_ready-zero_param"),
    # LHOST-01/02: a u32 param through printf format substitution.
    pytest.param(
        MSG_WARN_MEM_SIZE_TOO_SMALL,
        bytes.fromhex("00010000"),
        "WARN",
        "Memory size 65536 too small for chip-id check",
        id="u32_param",
    ),
    # LHOST-02: u24 render-hint 'hex_addr' renders as 0x%06x lowercase.
    pytest.param(
        MSG_INFO_ADDR,
        bytes.fromhex("01F4A2"),
        "INFO",
        "Address: 0x01f4a2",
        id="u24_hex_addr",
    ),
    # LHOST-01: (u24 addr, u8 retries, u16 bad bytes) renders all three.
    pytest.param(
        MSG_ERR_WRITE_FAILED,
        bytes.fromhex("01F4A2" + "05" + "0003"),
        "ERROR",
        "Failed to write memory, 0x01f4a2, retries: 5, bad bytes: 3",
        id="multi_param",
    ),
    # Two back-to-back u32 params, DATA severity.
    pytest.param(
        MSG_DATA_PROGRESS,
        bytes.fromhex("00000001" + "00010000"),
        "DATA",
        "1/65536",
        id="data_progress-u32_pair",
    ),
    # W-01/W-02: state-machine acks route by the catalog severity band, not
    # by line-prefix matching.
    pytest.param(MSG_INIT_DONE, b"", "INIT", "(init done)", id="init_done"),
    pytest.param(MSG_MAIN_DONE, b"", "MAIN", "(main done)", id="main_done"),
    pytest.param(MSG_END_DONE, b"", "END", "(end done)", id="end_done"),
    # P-02: MSG_OK_REV (physical, effective). With an override, the override
    # comes first, then the revision the board detected; 0xFF is no override.
    pytest.param(
        MSG_OK_REV,
        bytes([0x01, 0x02]),
        "OK",
        "Rev 2.0-class, Detected HW: Rev 1",
        id="ok_rev-override",
    ),
    pytest.param(
        MSG_OK_REV, bytes([0x01, 0xFF]), "OK", "Rev 1", id="ok_rev-no_override"
    ),
    # P-03: the override byte renders through the same silkscreen lookup as
    # MSG_OK_REV (WR-02); an unknown byte falls back to 'Rev<n>' (no space).
    pytest.param(
        MSG_OK_CFG,
        _cfg_params(0x02, 1100, 206),
        "OK",
        "R1: 10000, R2: 4700, Override HW: Rev 2.0-class, Bandgap: 1100 mV, "
        "Bandgap count: 206",
        id="ok_cfg-override",
    ),
    pytest.param(
        MSG_OK_CFG,
        _cfg_params(0x99, 1019, 200),
        "OK",
        "R1: 10000, R2: 4700, Override HW: Rev153, Bandgap: 1019 mV, "
        "Bandgap count: 200",
        id="ok_cfg-unknown_override",
    ),
    # D-03: MSG_INFO_HW / MSG_INFO_PHYSICAL_HW use the silkscreen lookup;
    # 0xFE (REVISION_UNKNOWN) is 'rev_unknown', not 'Rev254'; an unmapped
    # byte falls back to 'Rev<n>' (no space, as MSG_OK_REV).
    pytest.param(MSG_INFO_HW, bytes([0x01]), "INFO", "HW: Rev 1", id="info_hw-rev1"),
    pytest.param(
        MSG_INFO_HW, bytes([0xFE]), "INFO", "HW: rev_unknown", id="info_hw-rev_unknown"
    ),
    pytest.param(
        MSG_INFO_HW, bytes([0x99]), "INFO", "HW: Rev153", id="info_hw-unmapped"
    ),
    pytest.param(
        MSG_INFO_PHYSICAL_HW,
        bytes([0x01]),
        "INFO",
        "Physical HW: Rev 1",
        id="info_physical_hw-rev1",
    ),
    pytest.param(
        MSG_INFO_PHYSICAL_HW,
        bytes([0xFE]),
        "INFO",
        "Physical HW: rev_unknown",
        id="info_physical_hw-rev_unknown",
    ),
    pytest.param(
        MSG_INFO_PHYSICAL_HW,
        bytes([0x99]),
        "INFO",
        "Physical HW: Rev153",
        id="info_physical_hw-unmapped",
    ),
    # MSG_DEBUG params = [sub_id u8, cmd u8]: DBG_CMD names a known command
    # from COMMAND_NAMES, and renders an unmapped one as bare hex.
    pytest.param(
        MSG_DEBUG,
        bytes([DBG_CMD, 0x02]),
        "DATA",
        "Cmd: 0x02 (WRITE)",
        id="dbg_cmd-known",
    ),
    pytest.param(
        MSG_DEBUG, bytes([DBG_CMD, 0xFE]), "DATA", "Cmd: 0xfe", id="dbg_cmd-unmapped"
    ),
]


class TestIdFrameDecoder:
    """End-to-end binary-frame acceptance tests for the host decoder."""

    @pytest.mark.parametrize(("msg_id", "params", "severity", "text"), _RENDER_CASES)
    def test_frame_renders_severity_and_text(
        self, fake_serial, make_comm, msg_id, params, severity, text
    ):
        """LHOST-01/02/03: one id frame through _read_and_parse_lines yields
        Response(type=severity label, message=rendered text)."""
        comm = make_comm()
        fake_serial.feed(build_frame(msg_id, params))

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == severity
        assert response.message == text

    def test_zero_param_body_decodes_to_log_message(self, make_comm):
        """LHOST-01: the direct _decode_id_frame path — body = [id, crc]."""
        comm = make_comm()
        body = id_frame_body(MSG_OK_READY)
        decoded = comm._decode_id_frame(frame_len=2, body=body)
        assert decoded == LogMessage(
            severity="OK", text="Ready", id=MSG_OK_READY, params=(b"",)
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
        body = id_frame_body(0x77)

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
        assert second == Response(
            type="OK", message="Ready", id=MSG_OK_READY, params=(b"",)
        )

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
        body = id_frame_body(MSG_OK_FW_VERSION)

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
        body = id_frame_body(MSG_INFO_BIT_STR, bad_payload)

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

    def test_ok_cfg_p03_no_override_decodes(self, fake_serial, make_comm):
        """P-03: MSG_OK_CFG with override=0xFF sentinel renders no override
        clause, and the decoded params keep the sentinel."""
        comm = make_comm()
        fake_serial.feed(build_frame(MSG_OK_CFG, _cfg_params(0xFF, 1100, 206)))

        response = _drive_one_response(comm)
        assert response is not None
        assert response.type == "OK"
        assert response.message == (
            "R1: 10000, R2: 4700, Bandgap: 1100 mV, Bandgap count: 206"
        )
        assert response.params == (10000, 4700, 0xFF, 1100, 206)

    # W-04 MSG_DATA_CHUNK roundtrip tests

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

    def test_read_concatenates_multiple_chunks(self, tmp_path):
        """W-04: a read writes N MSG_DATA_CHUNK frames to the output file in
        order, skips the zero-param MSG_DATA_SENDING before them, and acks
        each chunk once: three chunks cost two acks more than one chunk."""
        from firestarter.config import ConfigManager
        from firestarter.eprom_operations import EpromOperator

        chunk0 = bytes(range(256))  # 0x00..0xFF
        chunk1 = bytes(range(256))[::-1]  # 0xFF..0x00
        chunk2 = bytes([0xAA] * 256)  # all 0xAA
        payload = chunk0 + chunk1 + chunk2

        def _read(chunk: int) -> tuple[bytes, int]:
            serial, factory = fresh_serial_and_comm()
            init, *rest = read_phase_frames(payload, chunk)
            for frame in [init, build_frame(MSG_DATA_SENDING, b""), *rest]:
                serial.feed(frame)
            out = tmp_path / f"out-{chunk}.bin"
            with connect_via(factory):
                assert EpromOperator(ConfigManager()).read_eprom(
                    "W27C512", {"memory-size": len(payload), "flags": 0}, str(out)
                )
            return out.read_bytes(), serial.written.count(b"OK")

        data, acks = _read(256)
        _, one_chunk_acks = _read(len(payload))

        assert data == payload
        assert acks == one_chunk_acks + 2
