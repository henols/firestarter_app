"""Parameters the firmware refuses at setup.

The firmware refuses some command parameters at setup (a negative address,
a memory size of 0, a chip entry with 0 for a required field) with an ERROR
frame instead of an ack. The host refuses them first, before the port opens,
and the port walk reports a refusal with its text instead of only as old
firmware.
"""

from unittest.mock import MagicMock, Mock

import pytest
from click.testing import CliRunner

from firestarter.chip_resolver import resolve_chip
from firestarter.cli_handlers import cli
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import ChipNotImplementedError, ProgrammerNotFoundError
from firestarter.messages import MSG_ERR_BAD_JSON, MSG_ERR_PARSE_CFG
from firestarter.serial_comm import SerialCommunicator

from .conftest import build_frame, make_app_context


def test_setup_command_records_the_refusal_text(make_comm, fake_serial) -> None:
    fake_serial.feed(build_frame(MSG_ERR_BAD_JSON, b""))
    comm = make_comm()
    assert comm.setup_command({"cmd": 1}, MagicMock()) is False
    assert comm.setup_refusal == "Bad JSON"


def test_probe_port_hands_the_refusal_to_the_caller(monkeypatch) -> None:
    def _init(self, port=None, baud_rate=None, **kwargs):
        self.connection = MagicMock()
        self.port_name = port
        self.setup_refusal = None

    def _refusing_setup(self, command_to_send, config_manager, **kwargs):
        self.setup_refusal = "Failed parsing config"
        return False

    monkeypatch.setattr(SerialCommunicator, "__init__", _init)
    monkeypatch.setattr(
        SerialCommunicator, "consume_remaining_input", lambda self: None
    )
    monkeypatch.setattr(SerialCommunicator, "setup_command", _refusing_setup)
    monkeypatch.setattr(SerialCommunicator, "disconnect", lambda self: None)

    refusals: list[str] = []
    comm = SerialCommunicator._probe_port(
        "/dev/fake0", 250000, {"state": 14}, MagicMock(), refusals=refusals
    )
    assert comm is None
    assert refusals == ["Failed parsing config"]


def _walk_with_refusal(monkeypatch, text: str) -> None:
    def fake_probe(port_name, baud_rate, command_to_send, config_manager, **kwargs):
        kwargs["refusals"].append(text)
        return None

    monkeypatch.setattr(
        SerialCommunicator,
        "_list_potential_ports",
        staticmethod(lambda p=None, **_kw: ["/dev/fake0"]),
    )
    monkeypatch.setattr(SerialCommunicator, "_probe_port", staticmethod(fake_probe))


def test_named_port_message_names_a_parameter_refusal(monkeypatch) -> None:
    _walk_with_refusal(monkeypatch, "Bad JSON")
    with pytest.raises(ProgrammerNotFoundError) as exc:
        SerialCommunicator.find_and_connect(
            {"cmd": 1},
            ConfigManager(),
            preferred_port="/dev/fake0",
            restrict_to_port=True,
        )
    text = str(exc.value)
    assert "No compatible programmer answered on /dev/fake0." in text
    assert "It answered 'Bad JSON'." in text
    assert "refused the command parameters" in text
    # The reflash advice stays, as the second explanation.
    assert "fw --board <board> --install" in text


def test_any_port_message_names_a_parameter_refusal(monkeypatch) -> None:
    _walk_with_refusal(monkeypatch, "Failed parsing config")
    with pytest.raises(ProgrammerNotFoundError) as exc:
        SerialCommunicator.find_and_connect(
            {"state": 14}, ConfigManager(), restrict_to_port=False
        )
    text = str(exc.value)
    assert "on any port" in text
    assert "'Failed parsing config'" in text
    assert "refused the command parameters" in text


def test_silent_walk_keeps_the_plain_message(monkeypatch) -> None:
    monkeypatch.setattr(
        SerialCommunicator,
        "_list_potential_ports",
        staticmethod(lambda p=None, **_kw: ["/dev/fake0"]),
    )
    monkeypatch.setattr(
        SerialCommunicator, "_probe_port", staticmethod(lambda *a, **k: None)
    )
    with pytest.raises(ProgrammerNotFoundError) as exc:
        SerialCommunicator.find_and_connect(
            {"cmd": 1}, ConfigManager(), restrict_to_port=False
        )
    assert str(exc.value) == "No compatible programmer found on any port."


def test_parse_cfg_frame_renders_readable_text(make_comm, fake_serial) -> None:
    fake_serial.feed(build_frame(MSG_ERR_PARSE_CFG, b""))
    comm = make_comm()
    assert comm.setup_command({"state": 14}, MagicMock()) is False
    assert comm.setup_refusal == "Failed parsing config"


# --- Host refusals before the port opens -----------------------------------


def _invoke_without_port(args: list[str]):
    operator = Mock(spec=EpromOperator)
    app = make_app_context(eprom_operator=operator)
    with pytest.MonkeyPatch.context() as mp:
        connect_spy = Mock()
        mp.setattr(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect", connect_spy
        )
        result = CliRunner().invoke(cli, args, obj=app)
    connect_spy.assert_not_called()
    return result, operator


@pytest.mark.parametrize("address", ["-0x10", "-16"])
@pytest.mark.parametrize("command", ["read", "verify", "blank", "dev read"])
def test_negative_address_is_refused_before_the_port_opens(
    tmp_path, command: str, address: str
) -> None:
    args = command.split() + ["W27C512"]
    if command == "verify":
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(b"\x01")
        args.append(str(input_file))
    args += ["-a", address]
    result, operator = _invoke_without_port(args)
    assert result.exit_code == 2, result.output
    assert f"W27C512: refused -- the start address {address!r} is negative." in (
        result.output
    )
    assert operator.method_calls == []


@pytest.mark.parametrize("size", ["0", "0x0", "-5"])
@pytest.mark.parametrize("command", ["read", "verify", "blank", "dev read"])
def test_size_below_one_byte_is_refused_before_the_port_opens(
    tmp_path, command: str, size: str
) -> None:
    args = command.split() + ["W27C512"]
    if command == "verify":
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(b"\x01")
        args.append(str(input_file))
    args += ["-s", size]
    result, operator = _invoke_without_port(args)
    assert result.exit_code == 2, result.output
    assert f"W27C512: refused -- the size {size!r} is less than 1 byte." in (
        result.output
    )
    assert operator.method_calls == []


def test_verify_refuses_an_empty_input_file(tmp_path) -> None:
    input_file = tmp_path / "empty.bin"
    input_file.write_bytes(b"")
    result, operator = _invoke_without_port(["verify", "W27C512", str(input_file)])
    assert result.exit_code == 2, result.output
    assert "W27C512: refused -- the input file is empty." in result.output
    assert operator.method_calls == []


def test_erase_refuses_a_negative_sector_address() -> None:
    result, operator = _invoke_without_port(["erase", "SST39SF040", "-s", "-0x1000"])
    assert result.exit_code == 2, result.output
    assert "the sector address '-0x1000' is negative." in result.output
    assert operator.method_calls == []


def test_read_with_a_valid_region_still_reaches_the_operator() -> None:
    operator = Mock(spec=EpromOperator)
    operator.read_eprom.return_value = True
    app = make_app_context(eprom_operator=operator)
    result = CliRunner().invoke(
        cli, ["read", "W27C512", "-a", "0x100", "-s", "0x10"], obj=app
    )
    assert result.exit_code == 0, result.output
    operator.read_eprom.assert_called_once()


@pytest.mark.parametrize("field", ["memory-size", "pin-count", "vpp_mv"])
def test_resolve_chip_refuses_a_zero_wire_field(monkeypatch, field: str) -> None:
    db = EpromDatabase(skip_local_override=True)
    real_get_eprom = db.get_eprom

    def _zeroed(name):
        full = real_get_eprom(name)
        full[field] = 0
        return full

    monkeypatch.setattr(db, "get_eprom", _zeroed)
    with pytest.raises(ChipNotImplementedError) as exc:
        resolve_chip("W27C512", db=db)
    assert f"gives 0 for {field}" in str(exc.value)


def test_no_shipped_chip_has_a_zero_wire_field() -> None:
    """Every shipped row still resolves: the refusal is for user overrides."""
    db = EpromDatabase(skip_local_override=True)
    for rows in db.proms.values():
        for row in rows:
            data = db.convert_to_programmer(db.map_chip_record(row, "x"))
            assert data["memory-size"] and data["pin-count"] and data["vpp_mv"], row[
                "part_number"
            ]
