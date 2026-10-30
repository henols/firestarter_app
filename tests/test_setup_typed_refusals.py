"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

A typed refusal at operation setup reaches the caller as itself.

Firmware that cannot read the binary command frame answers the setup frame
with MSG_ERR_BAD_FRAME, and setup raises FirmwareOutdatedError. A chip
operation must not turn that into a transport failure: the CLI shows
"Firmware outdated: ..." and `dev test` stops the run, instead of a
could-not-write verdict or a report of a broken chip.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from firestarter.chip_resolver import resolve_chip
from firestarter.cli_handlers import cli
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import (
    FirmwareOutdatedError,
    HardwareRevisionUnsupportedError,
)
from firestarter.messages import MSG_ERR_BAD_FRAME

from .conftest import build_frame, make_app_context


@pytest.fixture
def at28c256() -> dict:
    return resolve_chip("at28c256", db=EpromDatabase(skip_local_override=True))


@pytest.fixture
def json_era_board(make_comm, fake_serial):
    """find_and_connect runs the real setup against a board that answers
    every binary frame with MSG_ERR_BAD_FRAME."""
    fake_serial.feed(build_frame(MSG_ERR_BAD_FRAME, b""))

    def _connect(command_dict, config, **_kwargs):
        comm = make_comm()
        comm.setup_command(command_dict, config)
        return comm

    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=_connect,
    ):
        yield


def test_read_raises_firmware_outdated(json_era_board, at28c256, tmp_path):
    operator = EpromOperator(ConfigManager())
    with pytest.raises(FirmwareOutdatedError, match="binary command frames"):
        operator.read_eprom("at28c256", at28c256, str(tmp_path / "out.bin"))
    assert operator.comm is None


def test_write_raises_firmware_outdated(json_era_board, at28c256, tmp_path):
    data = tmp_path / "in.bin"
    data.write_bytes(b"\xaa" * 8)
    operator = EpromOperator(ConfigManager())
    with pytest.raises(FirmwareOutdatedError):
        operator.write_eprom("at28c256", at28c256, str(data))
    assert operator.last_write_attempt_verdict != 2
    assert operator.comm is None


def test_a_revision_refusal_is_raised_too(at28c256, tmp_path):
    def _refuse(command_dict, config, **_kwargs):
        raise HardwareRevisionUnsupportedError("needs a Rev 2.2 shield")

    operator = EpromOperator(ConfigManager())
    with (
        patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_refuse,
        ),
        pytest.raises(HardwareRevisionUnsupportedError),
    ):
        operator.read_eprom("at28c256", at28c256, str(tmp_path / "out.bin"))


def _invoke(args: list[str]):
    app = make_app_context(eprom_operator=EpromOperator(ConfigManager()))
    return CliRunner().invoke(cli, args, obj=app)


def test_cli_read_reports_the_outdated_firmware(json_era_board, tmp_path):
    result = _invoke(["read", "at28c256", str(tmp_path / "out.bin")])
    assert result.exit_code == 1, result.output
    assert "Firmware outdated:" in result.output


def test_cli_write_verify_reports_the_outdated_firmware(json_era_board, tmp_path):
    data = tmp_path / "in.bin"
    data.write_bytes(b"\xaa" * 8)
    result = _invoke(["write", "at28c256", str(data), "--verify"])
    assert result.exit_code == 1, result.output
    assert "Firmware outdated:" in result.output
    assert "could not" not in result.output.lower()
