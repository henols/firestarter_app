"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 65-02: Production-path integration tests for ProtocolNotImplementedError.

This file is the production-path complement to tests/test_protocol_not_implemented.py
(which uses _execute_phase directly + side_effect injection). Every test here drives
the REAL find_and_connect / _probe_port / _setup_operation path — NO _execute_phase
shortcut, NO operator.read_eprom side_effect injection for the connect-boundary cases.

Tests A-E cover:
  Test A: PRIMARY proof — fed 0xBB frame at probe time -> CliRunner firestarter read ->
           "Unsupported protocol: ...", exit 1, NOT "No compatible programmer found"
  Test B: expect_ack unit — raises ProtocolNotImplementedError on fed 0xBB frame
  Test C: _probe_port propagation — raises ProtocolNotImplementedError (not None)
  Test D: MAIN-phase WR-02 — _main_phase_read_data and _main_phase_send_data raise
           ProtocolNotImplementedError on a 0xBB ERROR, not bare EpromOperationError
  Test E: negative control — non-0xBB ERROR at probe time -> generic path
           (ProgrammerNotFoundError / "No compatible programmer found")
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from firestarter.cli_handlers import AppContext, cli
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.eprom_operations import ClassProgressHandler, EpromOperator
from firestarter.exceptions import ProtocolNotImplementedError
from firestarter.firmware import FirmwareManager
from firestarter.hardware import HardwareManager
from firestarter.messages import MSG_ERR_NOT_SUPPORTED, MSG_ERR_PROTOCOL_NOT_IMPLEMENTED
from firestarter.serial_comm import SerialCommunicator

from .conftest import _FakeSerial, build_frame

# ---------------------------------------------------------------------------
# Shared test helper (mirrors test_cli_handlers.make_app_context)
# ---------------------------------------------------------------------------

PROTOCOL_VALUE = 0x0B  # example rejected protocol byte (maps to algorithm 0x0B)
FW_VERSION_MSG = "FW: 3.0.0, HW: Rev2, Cmd: 0x0d"


def make_app_context(**manager_overrides) -> AppContext:
    """Construct an AppContext for in-process CliRunner tests.

    Defaults to a real EpromDatabase(skip_local_override=True) plus
    Mock-spec'd managers; overrides substitute specific managers.
    """
    db = manager_overrides.pop("db", None)
    if db is None:
        db = EpromDatabase(skip_local_override=True)
    config_manager = manager_overrides.pop("config_manager", None)
    if config_manager is None:
        config_manager = ConfigManager()
    return AppContext(
        db=db,
        config_manager=config_manager,
        eprom_operator=manager_overrides.pop(
            "eprom_operator", MagicMock(spec=EpromOperator)
        ),
        hardware_manager=manager_overrides.pop(
            "hardware_manager", MagicMock(spec=HardwareManager)
        ),
        firmware_manager=manager_overrides.pop(
            "firmware_manager", MagicMock(spec=FirmwareManager)
        ),
        eprom_presenter=manager_overrides.pop(
            "eprom_presenter", MagicMock(spec=EpromConsolePresenter)
        ),
    )


@pytest.fixture
def runner() -> CliRunner:
    """Fresh CliRunner per test — mix_stderr=True so stderr+stdout flow into result.output."""
    return CliRunner()


def _make_fake_comm(fake_ser: _FakeSerial) -> SerialCommunicator:
    """Build a SerialCommunicator wired to a _FakeSerial, bypassing __init__."""
    comm = SerialCommunicator.__new__(SerialCommunicator)
    comm.connection = fake_ser
    comm.port_name = "/dev/fake"
    comm.baud_rate = 250000
    comm.timeout = 0.1
    comm.programmer_info = None
    comm._fault_inject_outgoing = None
    comm.firmware_buffer_size = None
    comm.firmware_max_chunk = None
    comm.seen_message_ids = set()
    return comm


def _feed_fw_handshake_then_0xbb(fake_ser: _FakeSerial) -> None:
    """Pre-load the fake serial with the FW-version handshake acks followed by a 0xBB ERROR.

    The probe sequence in _probe_port:
      1. setup-complete ack: "OK: Ready"   (init_programmer OK ack)
      2. FW version ack:    "OK: FW: 3.0.0, ..."
      3. user-command ack:   0xBB ERROR id-frame  <- triggers ProtocolNotImplementedError
    """
    fake_ser.feed(b"OK: Ready\n")
    fake_ser.feed(f"OK: {FW_VERSION_MSG}\n".encode())
    fake_ser.feed(
        build_frame(MSG_ERR_PROTOCOL_NOT_IMPLEMENTED, bytes([PROTOCOL_VALUE]))
    )


# ---------------------------------------------------------------------------
# Test B: expect_ack unit — raises ProtocolNotImplementedError on 0xBB frame
# ---------------------------------------------------------------------------


def test_b_expect_ack_raises_protocol_not_implemented_on_0xbb() -> None:
    """SC#2 — expect_ack() raises ProtocolNotImplementedError (not a 2-tuple)
    when a 0xBB MSG_ERR_PROTOCOL_NOT_IMPLEMENTED frame is the next response.

    Proves Option B: the typed exception is raised inside expect_ack before
    returning (False, msg), so callers never see a flattened 2-tuple for 0xBB.
    """
    fake_ser = _FakeSerial()
    fake_ser.feed(
        build_frame(MSG_ERR_PROTOCOL_NOT_IMPLEMENTED, bytes([PROTOCOL_VALUE]))
    )

    comm = _make_fake_comm(fake_ser)

    with pytest.raises(ProtocolNotImplementedError) as exc_info:
        comm.expect_ack(timeout=1.0)

    assert "Protocol 0x0b not implemented" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test C: _probe_port propagation — raises ProtocolNotImplementedError (not None)
# ---------------------------------------------------------------------------


def test_c_probe_port_propagates_protocol_not_implemented_error() -> None:
    """SC#2 — _probe_port raises ProtocolNotImplementedError when the user-command
    ack is a 0xBB ERROR frame. Does NOT return None (the pre-Task-1 behaviour).

    The fake serial delivers:
      - setup-complete OK ack
      - FW version OK ack  (passes the version gate)
      - 0xBB ERROR id-frame (the user-command ack — raises ProtocolNotImplementedError)

    consume_remaining_input is mocked to a no-op so it does not drain the 0xBB frame.
    send_json_command is mocked to a no-op (no real serial write).
    disconnect is mocked to a no-op (avoids interacting with the fake serial on error).
    """
    fake_ser = _FakeSerial()
    _feed_fw_handshake_then_0xbb(fake_ser)

    def mock_init(self, port=None, baud_rate=None, **kwargs):
        self.connection = fake_ser
        self.port_name = port or "/dev/fake"
        self.baud_rate = baud_rate or 250000
        self.timeout = 0.1
        self.programmer_info = None
        self._fault_inject_outgoing = None
        self.firmware_buffer_size = None
        self.firmware_max_chunk = None
        self.seen_message_ids = set()

    with (
        patch.object(SerialCommunicator, "__init__", mock_init),
        patch.object(SerialCommunicator, "send_json_command", return_value=None),
        patch.object(SerialCommunicator, "consume_remaining_input", return_value=None),
        patch.object(SerialCommunicator, "disconnect", return_value=None),
    ):
        with pytest.raises(ProtocolNotImplementedError):
            SerialCommunicator._probe_port(
                port_name="/dev/fake",
                baud_rate=250000,
                command_to_send={"state": 1},
                config_manager=MagicMock(),
            )


# ---------------------------------------------------------------------------
# Test D: MAIN-phase WR-02 — _main_phase_read_data + _main_phase_send_data
# ---------------------------------------------------------------------------


def test_d_main_phase_read_data_raises_protocol_not_implemented_on_0xbb() -> None:
    """WR-02 — _main_phase_read_data routes a 0xBB ERROR frame through
    _raise_for_error_response, raising ProtocolNotImplementedError (not bare
    EpromOperationError).
    """
    fake_ser = _FakeSerial()
    fake_ser.feed(
        build_frame(MSG_ERR_PROTOCOL_NOT_IMPLEMENTED, bytes([PROTOCOL_VALUE]))
    )

    config = ConfigManager()
    operator = EpromOperator(config)
    operator.comm = _make_fake_comm(fake_ser)

    progress = ClassProgressHandler()

    with pytest.raises(ProtocolNotImplementedError):
        operator._main_phase_read_data(
            progress=progress,
            start_addr=0,
            end_addr=0x10000,
            process_data_chunk_callback=lambda addr, data: None,
        )


def test_d_main_phase_send_data_raises_protocol_not_implemented_on_0xbb() -> None:
    """WR-02 — _main_phase_send_data routes a 0xBB ERROR frame through
    _raise_for_error_response, raising ProtocolNotImplementedError (not bare
    EpromOperationError).

    The ERROR is injected as the first response so the loop hits it before
    reading any data chunk.
    """
    fake_ser = _FakeSerial()
    fake_ser.feed(
        build_frame(MSG_ERR_PROTOCOL_NOT_IMPLEMENTED, bytes([PROTOCOL_VALUE]))
    )

    config = ConfigManager()
    operator = EpromOperator(config)
    operator.comm = _make_fake_comm(fake_ser)

    progress = ClassProgressHandler()

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        input_path = f.name
        f.write(b"\xff" * 512)

    try:
        with pytest.raises(ProtocolNotImplementedError):
            operator._main_phase_send_data(
                progress=progress,
                input_file_path=input_path,
                buffer_size=512,
            )
    finally:
        os.unlink(input_path)


# ---------------------------------------------------------------------------
# Test E: negative control — non-0xBB ERROR at probe time -> generic path
# ---------------------------------------------------------------------------


def test_e_non_0xbb_error_at_probe_time_surfaces_as_generic_path() -> None:
    """Negative control — a non-0xBB ERROR at probe time (e.g. MSG_ERR_NOT_SUPPORTED)
    still triggers the generic connect-failure path (ProgrammerNotFoundError),
    NOT ProtocolNotImplementedError.

    Proves that the 0xBB discrimination in expect_ack is specific and did not
    accidentally widen to cover all ERROR responses.
    """
    from firestarter.exceptions import ProgrammerNotFoundError

    # Feed: version handshake OK, then a generic (non-0xBB) ERROR
    fake_ser = _FakeSerial()
    fake_ser.feed(b"OK: Ready\n")
    fake_ser.feed(f"OK: {FW_VERSION_MSG}\n".encode())
    # MSG_ERR_NOT_SUPPORTED (0xa5) — generic error, NOT 0xBB
    fake_ser.feed(build_frame(MSG_ERR_NOT_SUPPORTED, bytes([0x00])))

    def mock_init(self, port=None, baud_rate=None, **kwargs):
        self.connection = fake_ser
        self.port_name = port or "/dev/fake"
        self.baud_rate = baud_rate or 250000
        self.timeout = 0.1
        self.programmer_info = None
        self._fault_inject_outgoing = None
        self.firmware_buffer_size = None
        self.firmware_max_chunk = None
        self.seen_message_ids = set()

    with (
        patch.object(SerialCommunicator, "__init__", mock_init),
        patch.object(SerialCommunicator, "send_json_command", return_value=None),
        patch.object(SerialCommunicator, "consume_remaining_input", return_value=None),
        patch.object(SerialCommunicator, "disconnect", return_value=None),
        patch.object(
            SerialCommunicator, "_list_potential_ports", return_value=["/dev/fake"]
        ),
    ):
        with pytest.raises(ProgrammerNotFoundError) as exc_info:
            SerialCommunicator.find_and_connect(
                command_to_send={"state": 1},
                config_manager=MagicMock(),
            )
        # Must NOT raise ProtocolNotImplementedError; must be generic not-found
        assert "No compatible programmer found" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test A: PRIMARY proof — fed 0xBB frame -> CLI "Unsupported protocol:", exit 1
# ---------------------------------------------------------------------------


def test_a_cli_read_0xbb_at_probe_time_surfaces_unsupported_protocol(
    runner: CliRunner,
) -> None:
    """PRIMARY production-path proof (SC#2 + SC#3, HOST-01 + HOST-02).

    Drives the REAL path:
      fed 0xBB frame -> expect_ack -> _probe_port -> find_and_connect ->
      _setup_operation -> read_eprom -> map_typed_errors -> CLI output

    Invokes `firestarter read W27C512 <out>` via CliRunner with a REAL
    EpromOperator (NOT a Mock, NO read_eprom side_effect injection).

    Asserts:
      - exit_code == 1
      - output contains "Unsupported protocol"
      - output contains the verbatim "Protocol 0x0b not implemented"
      - output does NOT contain "No compatible programmer found"
      - output does NOT contain "Programmer error"
    """
    fake_ser = _FakeSerial()
    _feed_fw_handshake_then_0xbb(fake_ser)

    def mock_init(self, port=None, baud_rate=None, **kwargs):
        self.connection = fake_ser
        self.port_name = port or "/dev/fake"
        self.baud_rate = baud_rate or 250000
        self.timeout = 0.1
        self.programmer_info = None
        self._fault_inject_outgoing = None
        self.firmware_buffer_size = None
        self.firmware_max_chunk = None
        self.seen_message_ids = set()

    config = ConfigManager()
    real_operator = EpromOperator(config)
    app = make_app_context(eprom_operator=real_operator)

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        out_path = f.name

    try:
        with (
            patch.object(SerialCommunicator, "__init__", mock_init),
            patch.object(SerialCommunicator, "send_json_command", return_value=None),
            patch.object(
                SerialCommunicator, "consume_remaining_input", return_value=None
            ),
            patch.object(SerialCommunicator, "disconnect", return_value=None),
            patch.object(
                SerialCommunicator,
                "_list_potential_ports",
                return_value=["/dev/fake"],
            ),
        ):
            result = runner.invoke(cli, ["read", "W27C512", out_path], obj=app)
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)

    # Primary assertions (the two MANDATORY negative assertions are last)
    assert result.exit_code == 1, (
        f"Expected exit_code=1, got {result.exit_code}. Output:\n{result.output}"
    )
    assert "Unsupported protocol" in result.output, (
        f"Expected 'Unsupported protocol' in output. Output:\n{result.output}"
    )
    assert "Protocol 0x0b not implemented" in result.output, (
        f"Expected verbatim 'Protocol 0x0b not implemented'. Output:\n{result.output}"
    )
    # MANDATORY negative assertions — these distinguish the production path from the
    # pre-Task-1 generic-failure path
    assert "No compatible programmer found" not in result.output, (
        f"Got generic connect-failure message instead of typed error. Output:\n{result.output}"
    )
    assert "Programmer error" not in result.output, (
        f"Got generic programmer-error message instead of typed error. Output:\n{result.output}"
    )
