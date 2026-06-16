"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 65-01: ProtocolNotImplementedError typed-raise + CLI message coverage.

Covers the 4 CONTEXT.md / must_haves cases:
  SC#1 — ProtocolNotImplementedError is a subclass of EpromOperationError.
  SC#2 — A 0xBB ERROR frame fed through the state machine raises
          ProtocolNotImplementedError (not a bare EpromOperationError).
  SC#3 — A mocked CLI invocation raises ProtocolNotImplementedError whose CLI
          arm emits "Unsupported protocol: ..." containing the firmware-rendered
          protocol value; exit_code == 1.
  SC#4 — The ProtocolNotImplementedError arm fires BEFORE the EpromOperationError
          arm (ordering); a generic EpromOperationError still maps to
          "Programmer error:".
"""

from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from firestarter.cli_handlers import AppContext, cli
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import EpromOperationError, ProtocolNotImplementedError
from firestarter.firmware import FirmwareManager
from firestarter.hardware import HardwareManager
from firestarter.messages import MSG_ERR_PROTOCOL_NOT_IMPLEMENTED

from .conftest import build_frame

# ---------------------------------------------------------------------------
# Shared test helper (mirrors test_cli_handlers.make_app_context)
# ---------------------------------------------------------------------------


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
            "eprom_operator", Mock(spec=EpromOperator)
        ),
        hardware_manager=manager_overrides.pop(
            "hardware_manager", Mock(spec=HardwareManager)
        ),
        firmware_manager=manager_overrides.pop(
            "firmware_manager", Mock(spec=FirmwareManager)
        ),
        eprom_presenter=manager_overrides.pop(
            "eprom_presenter", Mock(spec=EpromConsolePresenter)
        ),
    )


@pytest.fixture
def runner() -> CliRunner:
    """Fresh CliRunner per test — mix_stderr=True so stderr+stdout flow into result.output."""
    return CliRunner()


# ---------------------------------------------------------------------------
# SC#1: subclass relationship
# ---------------------------------------------------------------------------


def test_protocol_not_implemented_is_eprom_operation_error() -> None:
    """SC#1: ProtocolNotImplementedError is a subclass of EpromOperationError.

    Pure Python assertion — no fixtures needed. This verifies the class
    hierarchy so existing EpromOperationError catchers in _run_state_machine
    still propagate the typed exception correctly.
    """
    assert issubclass(ProtocolNotImplementedError, EpromOperationError)


# ---------------------------------------------------------------------------
# SC#2 (HOST-01): 0xBB ERROR frame -> typed raise through the state machine
# ---------------------------------------------------------------------------


def test_state_machine_raises_protocol_not_implemented_on_0xbb_frame(
    fake_serial, make_comm
) -> None:
    """SC#2 / HOST-01: feeding a 0xBB ERROR frame through the state-machine
    INIT phase raises ProtocolNotImplementedError — not a bare EpromOperationError.

    The end-to-end path exercises: wire frame -> COBS/CRC decode ->
    _decode_id_frame -> Response.id -> _raise_for_error_response ->
    ProtocolNotImplementedError.

    _execute_phase is the raise site inside the state machine; _run_state_machine
    has an outer `except EpromOperationError` catch that returns (False, msg)
    to callers — so we assert on _execute_phase directly to observe the typed raise
    before the outer catch swallows it.

    MSG_ERR_PROTOCOL_NOT_IMPLEMENTED (0xBB) has params=(("u8","hex_byte"),)
    so we supply 1 param byte: the rejected protocol value (0x0B here).
    """
    from firestarter.eprom_operations import ClassProgressHandler

    config = ConfigManager()
    operator = EpromOperator(config)
    operator.comm = make_comm()

    protocol_value = 0x0B
    fake_serial.feed(
        build_frame(MSG_ERR_PROTOCOL_NOT_IMPLEMENTED, bytes([protocol_value]))
    )

    with pytest.raises(ProtocolNotImplementedError):
        operator._execute_phase("INIT", ClassProgressHandler())


# ---------------------------------------------------------------------------
# SC#3 (HOST-02): CLI arm emits actionable "Unsupported protocol:" message
# ---------------------------------------------------------------------------


def test_cli_unsupported_protocol_message_content(runner: CliRunner) -> None:
    """SC#3 / HOST-02: a CLI invocation whose operator raises
    ProtocolNotImplementedError emits a message containing:
      - the verbatim firmware-rendered text ("Protocol 0x0b not implemented")
      - the distinct prefix "Unsupported protocol"
      - exit_code == 1
    """
    operator = Mock(spec=EpromOperator)
    operator.read_eprom.side_effect = ProtocolNotImplementedError(
        "Protocol 0x0b not implemented"
    )
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["read", "W27C512", "out.bin"], obj=app)

    assert result.exit_code == 1
    assert "Protocol 0x0b not implemented" in result.output
    assert "Unsupported protocol" in result.output


# ---------------------------------------------------------------------------
# SC#4: ordering — subclass arm fires before base-class arm; generic arm unbroken
# ---------------------------------------------------------------------------


def test_map_typed_errors_ordering_subclass_not_caught_by_base(
    runner: CliRunner,
) -> None:
    """SC#4 (part 1): a ProtocolNotImplementedError must NOT be caught by the
    EpromOperationError arm — it must match the ProtocolNotImplementedError arm
    (which produces "Unsupported protocol", not "Programmer error").
    """
    operator = Mock(spec=EpromOperator)
    operator.read_eprom.side_effect = ProtocolNotImplementedError(
        "Protocol 0x0b not implemented"
    )
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["read", "W27C512", "out.bin"], obj=app)

    assert "Unsupported protocol" in result.output
    assert "Programmer error" not in result.output


def test_map_typed_errors_generic_eprom_operation_error_still_maps_to_programmer_error(
    runner: CliRunner,
) -> None:
    """SC#4 (part 2): a generic EpromOperationError (not the subclass) still
    produces "Programmer error:" output — proving the base-class arm is unbroken.
    """
    operator = Mock(spec=EpromOperator)
    operator.read_eprom.side_effect = EpromOperationError("some programmer failure")
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["read", "W27C512", "out.bin"], obj=app)

    assert result.exit_code == 1
    assert "Programmer error" in result.output
    assert "Unsupported protocol" not in result.output
