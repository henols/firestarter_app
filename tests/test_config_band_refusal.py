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
        self.write_block_budget_s = None
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
