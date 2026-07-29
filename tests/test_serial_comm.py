"""Phase 42 / ERR-03 fallback coverage lift for SerialCommunicator helpers
(D-14 fallback per CONTEXT).

Focuses on parsing helpers + version comparators that don't require a real
serial port: ``_is_version_sufficient``, ``_validate_firmware_version`` (negative
branches), ``_log_command_details`` (flag-decoding), and the ``_parse_response_line``
prefix-matching surface.
"""

import json as _json
import logging

import pytest

from firestarter.constants import (
    COMMAND_FW_VERSION,
    FLAG_CAN_ERASE,
    FLAG_FORCE,
    FLAG_OUTPUT_ENABLE,
)
from firestarter.exceptions import FirmwareOutdatedError
from firestarter.frame_parser import (
    Response,
    _crc8_ccitt,
    cobs_decode,
)
from firestarter.serial_comm import SerialCommunicator


@pytest.mark.parametrize(
    "current, required, expected",
    [
        ("3.1.0", "2.0.0", True),
        ("2.0.0", "2.0.0", True),
        ("1.9.9", "2.0.0", False),
        ("3.1.0x", "2.0.0", True),  # 'x' replaced with 999
        ("garbage", "2.0.0", False),
        ("", "2.0.0", False),
        ("3.1.0", "", False),
    ],
)
def test_is_version_sufficient(current: str, required: str, expected: bool) -> None:
    assert SerialCommunicator._is_version_sufficient(current, required) is expected


def test_validate_firmware_version_pre_v12_raises() -> None:
    """A pre-v1.2 firmware version (major < 3) raises FirmwareOutdatedError."""
    with pytest.raises(FirmwareOutdatedError):
        SerialCommunicator._validate_firmware_version("2.0.0")


def test_validate_firmware_version_allow_pre_v12_ok() -> None:
    """allow_pre_v12=True bypasses the major<3 guard."""
    # 2.0.0 satisfies the 2.0.0 floor; with allow_pre_v12 the major<3 check is skipped
    SerialCommunicator._validate_firmware_version("2.0.0", allow_pre_v12=True)


def test_validate_firmware_version_below_floor_raises() -> None:
    """allow_pre_v12=True still enforces the 2.0.0 floor."""
    with pytest.raises(FirmwareOutdatedError):
        SerialCommunicator._validate_firmware_version("1.9.9", allow_pre_v12=True)


def test_validate_firmware_version_strips_dev_suffix() -> None:
    """A version like '3.0.0-dev' is parsed as '3.0.0' (suffix stripped)."""
    SerialCommunicator._validate_firmware_version("3.0.0-dev")


def test_validate_firmware_version_unparseable_raises() -> None:
    """An unparseable version is treated as major=0 and rejected."""
    with pytest.raises(FirmwareOutdatedError):
        SerialCommunicator._validate_firmware_version("not-a-version")


def test_parse_response_line_picks_known_prefix(make_comm) -> None:
    """_parse_response_line returns a Response with type set to the matched prefix."""
    comm = make_comm()
    r = comm._parse_response_line(b"OK: hello world\r\n")
    assert r is not None
    assert r.type == "OK"
    assert "hello world" in r.message


def test_parse_response_line_empty_input_returns_none(make_comm) -> None:
    """Empty / non-printable input yields None."""
    comm = make_comm()
    assert comm._parse_response_line(b"") is None
    # Pure non-printable: only control bytes - filtered out, no match
    assert comm._parse_response_line(b"\x00\x01\x02") is None


def test_parse_response_line_no_known_prefix_uses_none_type(make_comm) -> None:
    """A line without a recognised prefix returns a Response with type=None."""
    comm = make_comm()
    r = comm._parse_response_line(b"some random text\n")
    assert r is not None
    assert r.type is None


def test_log_command_details_emits_flag_names(make_comm, caplog) -> None:
    """_log_command_details decodes the flags bitmask and logs flag names."""
    comm = make_comm()
    cmd = {"cmd": 2, "flags": FLAG_FORCE | FLAG_CAN_ERASE | FLAG_OUTPUT_ENABLE}
    with caplog.at_level(logging.DEBUG, logger="SerialComm"):
        comm._log_command_details(cmd)
    log_output = " ".join(r.message for r in caplog.records)
    assert "Force" in log_output
    assert "CanErase" in log_output
    assert "OutputEnable" in log_output


def test_list_potential_ports_includes_preferred(monkeypatch) -> None:
    """_list_potential_ports always includes preferred_port (even if not auto-detected)."""
    # Mock the system enumeration to return nothing
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: [])
    ports = SerialCommunicator._list_potential_ports(preferred_port="/dev/ttyACM7")
    assert "/dev/ttyACM7" in ports


def test_list_potential_ports_filters_by_manufacturer(monkeypatch) -> None:
    """_list_potential_ports picks Arduino/FTDI/CH340 hits but skips unknowns."""
    from collections import namedtuple

    Port = namedtuple("Port", ["device", "manufacturer", "description"])
    fake_ports = [
        Port("/dev/ttyACM0", "Arduino LLC", ""),
        Port("/dev/ttyUSB0", "FTDI", ""),
        Port("/dev/ttyUSB1", "CH340", ""),
        Port("/dev/ttyS0", "SomeUnknownVendor", ""),  # filtered out
    ]
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: fake_ports)
    ports = SerialCommunicator._list_potential_ports()
    assert "/dev/ttyACM0" in ports
    assert "/dev/ttyUSB0" in ports
    assert "/dev/ttyUSB1" in ports
    assert "/dev/ttyS0" not in ports


def test_send_string_routes_through_send_bytes(make_comm) -> None:
    """send_string encodes ASCII bytes via send_bytes."""
    comm = make_comm()
    n = comm.send_string("hello", encoding="ascii")
    # The fake serial reports the written byte count
    assert n == 5


def test_send_json_command_routes_through_send_string(make_comm) -> None:
    """send_json_command serialises the dict as compact JSON."""
    comm = make_comm()
    n = comm.send_json_command({"cmd": 2, "value": 42})
    # JSON output is at least len("{\"cmd\":2,\"value\":42}") bytes
    assert n > 10


# ---------------------------------------------------------------------------
# Phase-51 plan-02: COBS-framed command emission tests (FRAME-05 / T-51-04/05/06)
# ---------------------------------------------------------------------------


def test_send_json_command_emits_cobs_frame(make_comm, fake_serial) -> None:
    """send_json_command emits a valid COBS+CRC8 frame terminated by 0x00.

    Contract (FRAME-05 / T-51-05):
    - The entire write ends with b"\\x00" (the COBS delimiter).
    - The body (everything before the trailing 0x00) contains no 0x00 byte.
    - cobs_decode(body) produces payload + crc_byte where
      crc_byte == _crc8_ccitt(payload) and json.loads(payload) matches
      the original command dict (round-trip integrity).
    """
    comm = make_comm()
    cmd = {"cmd": 2, "value": 42}
    comm.send_json_command(cmd)

    # Read what was written to the fake serial
    fake_serial._buf.seek(0)
    written = fake_serial._buf.read()

    # Must end with the COBS delimiter
    assert written[-1:] == b"\x00", "Frame must end with 0x00 delimiter"

    body = written[:-1]
    assert b"\x00" not in body, "COBS body must contain no 0x00 bytes"

    # COBS decode → payload + crc_byte
    decoded = cobs_decode(body)
    assert len(decoded) >= 2, "Decoded frame must have at least payload + CRC byte"
    payload = decoded[:-1]
    crc_byte = decoded[-1]

    # CRC8 over the raw JSON bytes (BEFORE COBS encode — ADR §4.3)
    expected_crc = _crc8_ccitt(payload)
    assert crc_byte == expected_crc, (
        f"CRC8 mismatch: got 0x{crc_byte:02X}, expected 0x{expected_crc:02X}"
    )

    # Payload must be the compact JSON of the original command
    assert _json.loads(payload) == cmd


def test_send_json_command_atomic_frame(make_comm, fake_serial, monkeypatch) -> None:
    """connection.write() is called exactly once per command (SAFE-01 sub-claim B / T-51-04).

    Split-write (e.g. send_bytes(body) then send_bytes(b"\\x00")) is forbidden —
    it opens a timing window during the programmer↔communication mode transition.
    """
    write_calls: list = []
    original_write = fake_serial.write

    def _counting_write(data: bytes) -> int:
        write_calls.append(data)
        return original_write(data)

    monkeypatch.setattr(fake_serial, "write", _counting_write)

    comm = make_comm()
    comm.send_json_command({"cmd": 2, "value": 42})

    assert len(write_calls) == 1, (
        f"Expected exactly 1 write() call (atomic frame), got {len(write_calls)}"
    )
    # The single write must be the full frame including the trailing 0x00
    assert write_calls[0][-1:] == b"\x00", (
        "The single write must carry the full frame including the trailing 0x00"
    )


def test_send_json_command_version_probe_is_framed(make_comm, fake_serial) -> None:
    """CMD_FW_VERSION probe is emitted as a COBS frame, NOT as raw JSON (D-04 / T-51-06).

    No unframed send_string bypass exists — every command including the version probe
    carries CRC8.  Asserted by checking:
    - The write ends with b"\\x00" (framed).
    - The write does NOT start with b"{" (not raw JSON text).
    """
    comm = make_comm()
    comm.send_json_command({"state": COMMAND_FW_VERSION})

    fake_serial._buf.seek(0)
    written = fake_serial._buf.read()

    assert written[-1:] == b"\x00", "Version probe must end with 0x00 (framed)"
    assert not written.startswith(b"{"), (
        "Version probe must not start with '{' — it must be COBS-encoded, not raw JSON"
    )


# ---------------------------------------------------------------------------
# Phase-53 Plan 01 Task 2: RED tests for fault-inject hooks + ring-fence
#
# Four tests MUST FAIL until 53-02 adds:
#   - SerialCommunicator._fault_inject_outgoing attribute
#   - FaultInjectingSerialCommunicator subclass
# One ring-fence test is GREEN now (snapshot captured from current body).
# ---------------------------------------------------------------------------


def test_fault_inject_outgoing_none(make_comm, monkeypatch) -> None:
    """With _fault_inject_outgoing=None (the default), send_json_command emits an
    unmodified frame.

    The trailing byte must be 0x00 (COBS delimiter), and cobs_decode(frame[:-1])
    must yield a payload+CRC where CRC == _crc8_ccitt(payload).

    FAILS RED until 53-02 adds the _fault_inject_outgoing attribute to
    SerialCommunicator.__init__ so it can be inspected via getattr in send_json_command.
    This test asserts the attribute exists on the instance by checking that the hook
    path is reachable (i.e., the attribute is declared, not just dynamically set).
    """
    comm = make_comm()
    # Assert the attribute is formally declared (will fail RED until 53-02 sets it in __init__)
    assert hasattr(comm, "_fault_inject_outgoing"), (
        "_fault_inject_outgoing must be declared in SerialCommunicator.__init__ "
        "(53-02 adds it); this test is RED until then."
    )
    assert comm._fault_inject_outgoing is None, (  # type: ignore[attr-defined]
        "_fault_inject_outgoing must default to None"
    )
    sent: list = []
    monkeypatch.setattr(comm, "send_bytes", lambda b: sent.append(b) or len(b))
    comm.send_json_command({"cmd": 1})

    assert sent, "send_bytes must have been called"
    frame = sent[0]
    assert frame[-1] == 0x00, "Delimiter must be 0x00"
    body = cobs_decode(frame[:-1])
    crc = body[-1]
    payload = body[:-1]
    assert crc == _crc8_ccitt(payload), "CRC8 must be intact when hook is None"


def test_fault_inject_outgoing_corrupt_crc8(make_comm, monkeypatch) -> None:
    """With a hook flipping frame[-2], the decoded CRC no longer matches the payload.

    Hook: frame[:-2] + bytes([frame[-2] ^ 0x01]) + b"\\x00"

    FAILS RED until 53-02 adds the _fault_inject_outgoing hook path in
    send_json_command.
    """
    comm = make_comm()
    comm._fault_inject_outgoing = (  # type: ignore[attr-defined]  # RED: attribute absent until 53-02
        lambda f: f[:-2] + bytes([f[-2] ^ 0x01]) + b"\x00"
    )
    sent: list = []
    monkeypatch.setattr(comm, "send_bytes", lambda b: sent.append(b) or len(b))
    comm.send_json_command({"cmd": 1})

    assert sent, "send_bytes must have been called"
    frame = sent[0]
    body = cobs_decode(frame[:-1])
    crc = body[-1]
    payload = body[:-1]
    assert crc != _crc8_ccitt(payload), (
        "Corrupt-CRC8 hook must produce a frame where decoded CRC != _crc8_ccitt(payload)"
    )


def test_fault_inject_outgoing_drop_delimiter(make_comm, monkeypatch) -> None:
    """With a hook returning frame[:-1], the emitted bytes have no trailing 0x00.

    FAILS RED until 53-02 adds the _fault_inject_outgoing hook path in
    send_json_command.
    """
    comm = make_comm()
    comm._fault_inject_outgoing = lambda f: f[:-1]  # type: ignore[attr-defined]  # RED
    sent: list = []
    monkeypatch.setattr(comm, "send_bytes", lambda b: sent.append(b) or len(b))
    comm.send_json_command({"cmd": 1})

    assert sent, "send_bytes must have been called"
    frame = sent[0]
    assert frame[-1] != 0x00, (
        "Drop-delimiter hook must produce a frame without trailing 0x00"
    )


def test_fault_inject_incoming_subclass(make_comm) -> None:
    """FaultInjectingSerialCommunicator flips the last body byte exactly once.

    The one-shot flip fires on the first _decode_id_frame call; subsequent
    calls pass through unmodified (_fault_fired flag guards re-entry).

    FAILS RED until 53-02 adds the FaultInjectingSerialCommunicator subclass.
    """
    from firestarter.serial_comm import (  # type: ignore[attr-defined]  # RED: class absent until 53-02
        FaultInjectingSerialCommunicator,
    )

    comm = FaultInjectingSerialCommunicator.__new__(FaultInjectingSerialCommunicator)
    comm._corrupt_incoming_once = True
    comm._fault_fired = False

    # Build a minimal body (id + params + CRC) for _decode_id_frame
    from firestarter.frame_parser import _crc8_ccitt as _inner_crc8

    test_body = bytes([0x01, 0x00, 0x00])  # minimal: id + 2 params
    crc_byte = _inner_crc8(test_body)
    body_with_crc = test_body + bytes([crc_byte])

    # First call — the one-shot flip must fire and set _fault_fired
    comm._decode_id_frame(len(body_with_crc), body_with_crc)
    assert comm._fault_fired, "One-shot flag must be set after first call"

    # Second call — flip must NOT fire again; flag stays True
    comm._decode_id_frame(len(body_with_crc), body_with_crc)
    assert comm._fault_fired, "One-shot flag must remain True (no re-flip on 2nd call)"


def test_find_and_connect_threads_fault_inject_outgoing(monkeypatch) -> None:
    """53-04 fix: find_and_connect MUST forward fault_inject_outgoing to _probe_port.

    The outgoing fault has to be armed at connection time (before the setup command's
    send_json_command), since a READ sends no further framed command. This test pins
    the threading seam without opening a real serial port.
    """
    from firestarter.config import ConfigManager
    from firestarter.serial_comm import SerialCommunicator

    captured: dict = {}
    sentinel = object()

    def fake_probe(port_name, baud_rate, command_to_send, config_manager, **kwargs):
        captured["fault_inject_outgoing"] = kwargs.get("fault_inject_outgoing")
        return sentinel  # truthy -> find_and_connect returns it immediately

    monkeypatch.setattr(
        SerialCommunicator,
        "_list_potential_ports",
        staticmethod(lambda p=None: ["/dev/fake0"]),
    )
    monkeypatch.setattr(SerialCommunicator, "_probe_port", staticmethod(fake_probe))

    hook = lambda f: f[:-1]  # noqa: E731  (drop-delimiter sample)
    result = SerialCommunicator.find_and_connect(
        {"cmd": 1}, ConfigManager(), fault_inject_outgoing=hook
    )

    assert result is sentinel
    assert captured["fault_inject_outgoing"] is hook, (
        "find_and_connect must forward the outgoing fault hook to _probe_port (53-04)."
    )


def test_find_and_connect_default_no_fault_inject(monkeypatch) -> None:
    """Production default: fault_inject_outgoing is None (path byte-identical, T-53-03)."""
    from firestarter.config import ConfigManager
    from firestarter.serial_comm import SerialCommunicator

    captured: dict = {}

    def fake_probe(port_name, baud_rate, command_to_send, config_manager, **kwargs):
        captured["fault_inject_outgoing"] = kwargs.get(
            "fault_inject_outgoing", "MISSING"
        )
        return object()

    monkeypatch.setattr(
        SerialCommunicator,
        "_list_potential_ports",
        staticmethod(lambda p=None: ["/dev/fake0"]),
    )
    monkeypatch.setattr(SerialCommunicator, "_probe_port", staticmethod(fake_probe))

    SerialCommunicator.find_and_connect({"cmd": 1}, ConfigManager())
    assert captured["fault_inject_outgoing"] is None


def test_read_and_parse_lines_ringfence_unchanged() -> None:
    """Ring-fence compliance: _read_and_parse_lines body source is byte-identical
    to the GATE-1.8d pinned snapshot.

    This test is GREEN today — the snapshot is captured from the current body.
    It goes RED if ANY change is made to the generator body (per GATE-1.8d:
    any change must be flagged and deferred to v1.9 alongside binary re-validation).

    Pinned SHA-256 (2026-06-11): 6d9e4fe4b67b78c110418305113b275174f16b2ecc9e0f55fbf5d9a623398184
    (Updated from 544433068cb14ac14677939435cb4f0ea78783b503315ed645b5f88c5c44a444
     at Phase 65-01: Response now carries id=decoded.id for ProtocolNotImplementedError
     typed-raise dispatch. Change is in-scope for v1.12 host graceful handling — not
     a transport-path change, only adds id plumbing to the Response construction.)
    """
    import hashlib
    import inspect

    from firestarter.serial_comm import SerialCommunicator

    _PINNED_SHA256 = "6d9e4fe4b67b78c110418305113b275174f16b2ecc9e0f55fbf5d9a623398184"

    src = inspect.getsource(SerialCommunicator._read_and_parse_lines)
    actual_digest = hashlib.sha256(src.encode("utf-8")).hexdigest()

    assert actual_digest == _PINNED_SHA256, (
        f"GATE-1.8d VIOLATION: _read_and_parse_lines body has changed!\n"
        f"  Pinned digest:  {_PINNED_SHA256}\n"
        f"  Actual digest:  {actual_digest}\n"
        "Any change to this generator body must be flagged and deferred to v1.9 "
        "per the ring-fence protocol (see serial_comm.py header comment)."
    )


# ---------------------------------------------------------------------------
# CAP-01 (Phase 55 Plan 03 Task 2): _decode_id_frame MSG_OK_READY seam tests
#
# SC3b: pins that _decode_id_frame sets firmware_max_chunk from the 2-byte
# param region of MSG_OK_READY acks, and leaves it None when params are absent.
# Replaces the Phase 54 identity-string parse tests (now removed along with the
# fw_fields[2]/[3] block in _probe_port).
# ---------------------------------------------------------------------------


def test_decode_id_frame_sets_firmware_max_chunk_from_2_byte_param(make_comm) -> None:
    """CAP-01 SC3b: a MSG_OK_READY ack with 2-byte param=512 sets firmware_max_chunk=512.

    Body layout: [id_byte=0x01][params_bytes=\\x02\\x00][crc8]
    _decode_id_frame must extract big-endian u16 0x0200 == 512 and store it.
    """
    comm = make_comm()
    assert comm.firmware_max_chunk is None, "firmware_max_chunk must start None"

    from firestarter.frame_parser import _crc8_ccitt
    from firestarter.messages import MSG_OK_READY

    # Build a valid MSG_OK_READY body: id + 2-byte BE u16 (512 = 0x0200) + CRC8
    params = b"\x02\x00"  # big-endian 512
    msg_id_byte = bytes([MSG_OK_READY])
    crc = _crc8_ccitt(msg_id_byte + params)
    body = msg_id_byte + params + bytes([crc])
    frame_len = len(body)

    result = comm._decode_id_frame(frame_len, body)
    assert result is not None, (
        "_decode_id_frame must return a LogMessage for a valid body"
    )
    assert comm.firmware_max_chunk == 512, (
        f"Expected firmware_max_chunk=512 from 2-byte param, got {comm.firmware_max_chunk}"
    )


def test_decode_id_frame_leaves_firmware_max_chunk_none_for_0_byte_param(
    make_comm,
) -> None:
    """CAP-01 SC3b: a MSG_OK_READY ack with 0 param bytes leaves firmware_max_chunk None.

    Old firmware emits MSG_OK_READY with no params; the host must not assign
    firmware_max_chunk (graceful degradation — T-55-07).
    """
    comm = make_comm()
    assert comm.firmware_max_chunk is None, "firmware_max_chunk must start None"

    from firestarter.frame_parser import _crc8_ccitt
    from firestarter.messages import MSG_OK_READY

    # Build a valid MSG_OK_READY body with 0 param bytes: id + CRC8 only
    msg_id_byte = bytes([MSG_OK_READY])
    crc = _crc8_ccitt(msg_id_byte)
    body = msg_id_byte + bytes([crc])
    frame_len = len(body)

    result = comm._decode_id_frame(frame_len, body)
    assert result is not None, (
        "_decode_id_frame must return a LogMessage for a valid body"
    )
    assert comm.firmware_max_chunk is None, (
        f"Expected firmware_max_chunk=None for 0-byte param, got {comm.firmware_max_chunk}"
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        (b"\x00\x00", None),  # 0 -> below floor, rejected (T-55-06)
        (b"\x00\x01", 1),  # 1 -> accepted (lower boundary)
        (b"\x10\x00", 4096),  # 4096 -> accepted (upper boundary)
        (b"\x10\x01", None),  # 4097 -> above ceiling, rejected (T-55-06)
        (b"\xff\xff", None),  # 65535 -> hostile over-large, rejected (T-55-06)
    ],
)
def test_decode_id_frame_clamps_implausible_max_chunk(make_comm, raw, expected) -> None:
    """CAP-01 V5 (T-55-05/T-55-06): the [1, 4096] plausibility clamp rejects
    out-of-range advertised capacities so a hostile/corrupt MSG_OK_READY ack
    cannot over-size firmware_max_chunk; out-of-range values leave it unset
    (so _calculate_buffer_size falls back to the 512 Uno floor).

    This pins the only defensive control on the ack-sourced buffer size — a
    future refactor that widens or drops the clamp must fail here.
    """
    comm = make_comm()
    assert comm.firmware_max_chunk is None, "firmware_max_chunk must start None"

    from firestarter.frame_parser import _crc8_ccitt
    from firestarter.messages import MSG_OK_READY

    msg_id_byte = bytes([MSG_OK_READY])
    crc = _crc8_ccitt(msg_id_byte + raw)
    body = msg_id_byte + raw + bytes([crc])

    result = comm._decode_id_frame(len(body), body)
    assert result is not None, (
        "_decode_id_frame must return a LogMessage for a valid body"
    )
    assert comm.firmware_max_chunk == expected, (
        f"param {raw!r}: expected firmware_max_chunk={expected}, "
        f"got {comm.firmware_max_chunk} (clamp [1, 4096])"
    )


# --- D-09 / HOST-05 / F-120-02: INFO-band promotion in _log_rurp_feedback ---
#
# Before Plan 120-03's `elif response.type == "INFO"` arm, the whole INFO
# band fell through to the `logging.DEBUG` initialiser in
# `_log_rurp_feedback`, while `_setup_logging` sets the root logger to
# `logging.INFO` unless `-v` is passed. That silently discarded every Phase
# 118/119 SDP report line at default verbosity: a two-repo requirement that
# passed its own phase's verification and was still false end to end.
#
# The fix makes SIX previously-invisible ids visible at default verbosity,
# not five: `0x5E` (MSG_INFO_SDP_UNLOCK), `0x5F` (MSG_INFO_SDP_UNLOCK_DONE_US),
# `0x60` (MSG_INFO_SDP_LOCK), `0x61` (MSG_INFO_SDP_LOCK_DONE_US), `0x62`
# (MSG_INFO_PAGE_LOAD_WORST_US) — plus `0x5B` MSG_INFO_HW, which is emitted
# unconditionally through the `LOG_WARN_ID_U8` alias at
# `rurp_hw_rev_utils.h:96` despite a catalog severity of INFO. `0x5B`'s
# unconditional site is Phase 35's CR-02 hard-fail-loud revision warning, so
# this fix is also a partial fix for a second, older observability defect
# unrelated to SDP.
#
# A search of this whole suite for logger-level assertions and record-count
# assertions on the `RURP` logger (`rurp_logger`) — `caplog.at_level(...,
# logger="RURP")`, `not caplog.records`, `len(caplog.records) ==`,
# `caplog.records == []` — found zero hits. So zero existing tests needed to
# move; every test below is purely additive.


def test_info_band_frame_is_promoted_to_logging_info(make_comm, caplog) -> None:
    """D-09 / HOST-05 / F-120-02: an INFO-typed Response now logs at
    logging.INFO on the RURP logger.

    Before this arm, the record landed at logging.DEBUG and was therefore
    invisible at default verbosity (root defaults to INFO unless -v) — every
    Phase 118/119 SDP report line was discarded by the host for a whole
    phase.
    """
    comm = make_comm()
    response = Response(
        type="INFO", message="SDP unlock complete", payload=None, id=None
    )

    with caplog.at_level(logging.INFO, logger="RURP"):
        comm._log_rurp_feedback(response)

    records = [r for r in caplog.records if r.name == "RURP"]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert "SDP unlock complete" in records[0].message


@pytest.mark.parametrize("label", ["OK", "INIT", "MAIN", "END", "DATA"])
def test_non_info_protocol_phase_labels_still_log_at_debug(
    make_comm, caplog, label: str
) -> None:
    """Negative / scoped-promotion leg (anti-hollow contract): protocol-phase
    labels (OK, INIT, MAIN, END, DATA) still log at logging.DEBUG, proving
    the INFO promotion is scoped rather than a blanket level change.

    Promoting these labels to INFO would flood default-verbosity output —
    they are per-phase protocol frames emitted on every operation, not
    one-off report lines.
    """
    comm = make_comm()
    response = Response(
        type=label, message=f"{label} frame body", payload=None, id=None
    )

    # Bound at INFO: nothing should be captured for a DEBUG-level record.
    with caplog.at_level(logging.INFO, logger="RURP"):
        comm._log_rurp_feedback(response)
    assert [r for r in caplog.records if r.name == "RURP"] == []

    caplog.clear()

    # Bound at DEBUG: the same call now IS captured, confirming it logs at
    # DEBUG (not that it was dropped for an unrelated reason).
    with caplog.at_level(logging.DEBUG, logger="RURP"):
        comm._log_rurp_feedback(response)
    records = [r for r in caplog.records if r.name == "RURP"]
    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG


def test_warn_and_error_severity_arms_are_unchanged(make_comm, caplog) -> None:
    """Regression leg: the new INFO arm does not reorder or shadow the
    pre-existing WARN/ERROR arms — WARN still yields logging.WARNING and
    ERROR still yields logging.ERROR."""
    comm = make_comm()

    warn_response = Response(type="WARN", message="warn body", payload=None, id=None)
    error_response = Response(type="ERROR", message="error body", payload=None, id=None)

    with caplog.at_level(logging.DEBUG, logger="RURP"):
        comm._log_rurp_feedback(warn_response)
        comm._log_rurp_feedback(error_response)

    records = [r for r in caplog.records if r.name == "RURP"]
    assert len(records) == 2
    assert records[0].levelno == logging.WARNING
    assert records[1].levelno == logging.ERROR
