"""`config` and the bandgap setter refuse a value outside the firmware's band.

The firmware refuses a config command whose r1, r2 or bandgap is outside
its band. The host refuses the same values before the port opens, and a
refusal that does come back from the firmware ends with a non-zero exit and
the firmware's own text, not a timeout.
"""

import logging
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from firestarter.cli_handlers import cli
from firestarter.command_codec import CommandEncodeError
from firestarter.config import ConfigManager
from firestarter.constants import (
    CONFIG_BANDGAP_MAX_MV,
    CONFIG_BANDGAP_MIN_MV,
    CONFIG_R1_MAX,
    CONFIG_R1_MIN,
    CONFIG_R2_MAX,
    CONFIG_R2_MIN,
)
from firestarter.hardware import HardwareManager
from firestarter.messages import MSG_ERR_PARSE_CFG
from firestarter.serial_comm import SerialCommunicator

from .conftest import build_frame, make_app_context


def test_bands_match_the_firmware_defaults_plus_minus_25_percent() -> None:
    assert (CONFIG_R1_MIN, CONFIG_R1_MAX) == (270000 * 3 // 4, 270000 * 5 // 4)
    assert (CONFIG_R2_MIN, CONFIG_R2_MAX) == (44000 * 3 // 4, 44000 * 5 // 4)
    assert (CONFIG_BANDGAP_MIN_MV, CONFIG_BANDGAP_MAX_MV) == (1000, 1200)


@pytest.mark.parametrize(
    "args",
    [
        ["-r1", str(CONFIG_R1_MIN - 1)],
        ["-r1", str(CONFIG_R1_MAX + 1)],
        ["-r2", str(CONFIG_R2_MIN - 1)],
        ["-r2", str(CONFIG_R2_MAX + 1)],
        ["-r1", "0"],
    ],
)
def test_config_refuses_an_out_of_band_resistor(args: list[str]) -> None:
    hardware = Mock(spec=HardwareManager)
    app = make_app_context(hardware_manager=hardware)
    result = CliRunner().invoke(cli, ["config", *args], obj=app)
    assert result.exit_code == 2, result.output
    lo, hi = (
        (CONFIG_R1_MIN, CONFIG_R1_MAX)
        if args[0] == "-r1"
        else (CONFIG_R2_MIN, CONFIG_R2_MAX)
    )
    assert f"{lo}<=x<={hi}" in result.output
    hardware.set_hardware_config.assert_not_called()


def test_config_accepts_a_value_inside_the_band() -> None:
    hardware = Mock(spec=HardwareManager)
    hardware.set_hardware_config.return_value = True
    app = make_app_context(hardware_manager=hardware)
    result = CliRunner().invoke(
        cli, ["config", "-r1", str(CONFIG_R1_MIN), "-r2", str(CONFIG_R2_MAX)], obj=app
    )
    assert result.exit_code == 0, result.output
    args = hardware.set_hardware_config.call_args.args
    assert args[1:3] == (CONFIG_R1_MIN, CONFIG_R2_MAX)


@pytest.mark.parametrize(
    "bandgap_mv", [CONFIG_BANDGAP_MIN_MV - 1, CONFIG_BANDGAP_MAX_MV + 1]
)
def test_set_bandgap_mv_refuses_out_of_band_before_connecting(
    monkeypatch, caplog, bandgap_mv: int
) -> None:
    connect_spy = Mock()
    monkeypatch.setattr(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect", connect_spy
    )
    with caplog.at_level(logging.ERROR):
        assert HardwareManager(ConfigManager()).set_bandgap_mv(bandgap_mv) is False
    connect_spy.assert_not_called()
    assert "1000-1200 mV" in caplog.text


def test_a_firmware_config_refusal_exits_non_zero_with_its_text(
    monkeypatch, caplog, fake_serial
) -> None:
    """The firmware answers an out-of-band config with MSG_ERR_PARSE_CFG at
    setup. The command must fail with that text, not wait for a timeout."""
    fake_serial.feed(build_frame(MSG_ERR_PARSE_CFG, b""))

    def _init(self, port=None, baud_rate=None, **kwargs):
        self.connection = fake_serial
        self.port_name = port
        self.baud_rate = 250000
        self.timeout = 0.1
        self.programmer_info = None
        self._fault_inject_outgoing = None
        self.firmware_buffer_size = None
        self.firmware_max_chunk = None
        self.firmware_identity = None
        self.hw_revision = None
        self.seen_message_ids = set()
        self.setup_refusal = None

    monkeypatch.setattr(SerialCommunicator, "__init__", _init)
    # The bytes stand for the reply to the command, so the pre-send drain
    # must not eat them.
    monkeypatch.setattr(
        SerialCommunicator, "consume_remaining_input", lambda self, **_kw: None
    )
    monkeypatch.setattr(
        SerialCommunicator,
        "_list_potential_ports",
        staticmethod(lambda p=None, **_kw: ["/dev/fake0"]),
    )
    app = make_app_context(hardware_manager=HardwareManager(ConfigManager()))
    with caplog.at_level(logging.ERROR):
        result = CliRunner().invoke(cli, ["config", "-r1", "270000"], obj=app)
    assert result.exit_code == 1, result.output
    assert "Hardware configuration failed" in caplog.text
    assert "'Failed parsing config'" in caplog.text
    assert "refused the command parameters" in caplog.text
    assert "Timeout" not in caplog.text


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"rev": 256}, "revision code 256"),
        ({"rev": 254}, "revision code 254"),
        ({"rev": -2}, "revision code -2"),
        ({"r1_val": -270000}, "R1 -270000"),
        ({"r2_val": 66000}, "R2 66000"),
    ],
)
def test_set_hardware_config_refuses_raw_out_of_band_values(
    monkeypatch, caplog, kwargs: dict, reason: str
) -> None:
    """The firmware reads '-' as 0 and truncates rev to 8 bits before its own
    check, so the raw value is refused on the host."""
    connect_spy = Mock()
    monkeypatch.setattr(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect", connect_spy
    )
    with caplog.at_level(logging.ERROR):
        assert HardwareManager(ConfigManager()).set_hardware_config(**kwargs) is False
    connect_spy.assert_not_called()
    assert "Hardware configuration refused" in caplog.text
    assert reason in caplog.text


@pytest.mark.parametrize("rev", [-1, 0, 5, 0xFF])
def test_set_hardware_config_sends_accepted_revisions(monkeypatch, rev: int) -> None:
    sent = []

    def _fake_simple(self, command, name):
        sent.append(command)
        return True, "ok"

    monkeypatch.setattr(HardwareManager, "_execute_simple_command", _fake_simple)
    assert HardwareManager(ConfigManager()).set_hardware_config(rev=rev) is True
    assert sent[0]["rev"] == (0xFF if rev == -1 else rev)


def _calibration(bandgap_adc):
    return {"r1": 270000, "r2": 44000, "bandgap_mv": 1100, "bandgap_adc": bandgap_adc}


@pytest.mark.parametrize("vcc", ["0", "-5.0"])
def test_cal_refuses_a_non_positive_vcc(vcc: str) -> None:
    hardware = Mock(spec=HardwareManager)
    app = make_app_context(hardware_manager=hardware)
    result = CliRunner().invoke(cli, ["cal", "--vcc", vcc], obj=app)
    assert result.exit_code == 2, result.output
    hardware.read_calibration.assert_not_called()
    hardware.set_bandgap_mv.assert_not_called()


@pytest.mark.parametrize("vcc", ["10.001", "50.9"])
def test_cal_refuses_an_implausible_vcc_before_any_port(vcc: str) -> None:
    """A mistyped reading is a usage error, not a programmer fault."""
    hardware = Mock(spec=HardwareManager)
    app = make_app_context(hardware_manager=hardware)
    result = CliRunner().invoke(cli, ["cal", "--vcc", vcc], obj=app)
    assert result.exit_code == 2, result.output
    assert "Programmer error" not in result.output
    hardware.read_calibration.assert_not_called()
    hardware.set_bandgap_mv.assert_not_called()


def _stored(bandgap_mv):
    return {"r1": 270000, "r2": 44000, "bandgap_mv": bandgap_mv, "bandgap_adc": 206}


def test_cal_stores_the_bandgap_it_back_solves() -> None:
    """5.09 V on a board that counts 206 is a 1024 mV bandgap."""
    hardware = Mock(spec=HardwareManager)
    hardware.read_calibration.side_effect = [_calibration(206), _stored(1024)]
    hardware.set_bandgap_mv.return_value = True
    app = make_app_context(hardware_manager=hardware)
    result = CliRunner().invoke(cli, ["cal", "--vcc", "5.09"], obj=app)
    assert result.exit_code == 0, result.output
    hardware.set_bandgap_mv.assert_called_once_with(1024)
    assert hardware.read_calibration.call_count == 2
    assert "Reference: 1100 mV -> 1024 mV" in result.output


def test_cal_fails_when_the_board_stored_another_value() -> None:
    hardware = Mock(spec=HardwareManager)
    hardware.read_calibration.side_effect = [_calibration(206), _stored(1100)]
    hardware.set_bandgap_mv.return_value = True
    app = make_app_context(hardware_manager=hardware)
    result = CliRunner().invoke(cli, ["cal", "--vcc", "5.09"], obj=app)
    assert result.exit_code == 1, result.output
    assert "the board reports 1100 mV, expected 1024 mV" in result.output
    assert "Reference:" not in result.output


def test_cal_fails_when_the_value_cannot_be_read_back() -> None:
    hardware = Mock(spec=HardwareManager)
    hardware.read_calibration.side_effect = [_calibration(206), None]
    hardware.set_bandgap_mv.return_value = True
    app = make_app_context(hardware_manager=hardware)
    result = CliRunner().invoke(cli, ["cal", "--vcc", "5.09"], obj=app)
    assert result.exit_code == 1, result.output
    assert "could not read the calibration back" in result.output
    assert "Reference:" not in result.output


def test_cal_refuses_a_result_outside_the_band_without_writing() -> None:
    hardware = Mock(spec=HardwareManager)
    hardware.read_calibration.return_value = _calibration(206)
    app = make_app_context(hardware_manager=hardware)
    result = CliRunner().invoke(cli, ["cal", "--vcc", "9.0"], obj=app)
    assert result.exit_code == 1, result.output
    assert "outside 1000-1200 mV" in result.output
    hardware.set_bandgap_mv.assert_not_called()


def test_cal_exits_non_zero_when_the_firmware_refuses_the_bandgap() -> None:
    hardware = Mock(spec=HardwareManager)
    hardware.read_calibration.return_value = _calibration(206)
    hardware.set_bandgap_mv.return_value = False
    app = make_app_context(hardware_manager=hardware)
    result = CliRunner().invoke(cli, ["cal", "--vcc", "5.09"], obj=app)
    assert result.exit_code == 1, result.output
    assert "Reference:" not in result.output


def test_a_host_encode_refusal_is_not_reported_as_a_programmer_error() -> None:
    hardware = Mock(spec=HardwareManager)
    hardware.read_calibration.side_effect = CommandEncodeError(
        "'flags' must be 0..65535"
    )
    app = make_app_context(hardware_manager=hardware)
    result = CliRunner().invoke(cli, ["cal", "--vcc", "5.0"], obj=app)
    assert result.exit_code == 1, result.output
    assert "Refused before sending: 'flags' must be 0..65535" in result.output
    assert "Programmer error" not in result.output


def test_cal_refusal_names_the_board_without_calibration() -> None:
    hardware = Mock(spec=HardwareManager)
    hardware.read_calibration.return_value = _calibration(0)
    app = make_app_context(hardware_manager=hardware)
    result = CliRunner().invoke(cli, ["cal", "--vcc", "5.0"], obj=app)
    assert result.exit_code == 1, result.output
    assert "py32f071" in result.output
    hardware.set_bandgap_mv.assert_not_called()


def test_cal_refuses_firmware_that_sends_no_bandgap_count() -> None:
    hardware = Mock(spec=HardwareManager)
    hardware.read_calibration.return_value = _calibration(None)
    app = make_app_context(hardware_manager=hardware)
    result = CliRunner().invoke(cli, ["cal", "--vcc", "5.0"], obj=app)
    assert result.exit_code == 1, result.output
    assert "Update the firmware" in result.output
    hardware.set_bandgap_mv.assert_not_called()
