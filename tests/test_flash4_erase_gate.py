"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Flash4 (protocol 0x05) erase-refusal gate (SAFE-06).

End-to-end proof that `firestarter erase` on a flash4 part refuses before
the serial port opens, printing exactly one line and never reaching
`EpromOperator.erase_eprom` -- the D-02 pre-connect guarantee.
"""

from unittest.mock import Mock, patch

from click.testing import CliRunner

from firestarter import cli_handlers
from firestarter.cli_handlers import AppContext, cli
from firestarter.config import ConfigManager
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.eprom_operations import EpromOperator
from firestarter.firmware import FirmwareManager
from firestarter.flash4_erase_gate import refusal_text
from firestarter.hardware import HardwareManager

NO_PIN1_BUS_CONFIG = {"bus": [0, 1, 2]}


def _cli_app_context(eprom_operator):
    return AppContext(
        db=Mock(),
        config_manager=ConfigManager(),
        eprom_operator=eprom_operator,
        hardware_manager=Mock(spec=HardwareManager),
        firmware_manager=Mock(spec=FirmwareManager),
        eprom_presenter=Mock(spec=EpromConsolePresenter),
    )


def test_cli_erase_on_flash4_part_refuses_pre_connect_with_one_line():
    runner = CliRunner()
    eprom_operator = Mock(spec=EpromOperator)
    app = _cli_app_context(eprom_operator)
    with patch.object(
        cli_handlers,
        "resolve_chip",
        return_value={"algorithm": 5, "bus-config": NO_PIN1_BUS_CONFIG},
    ):
        result = runner.invoke(cli, ["erase", "AE29F2008"], obj=app)

    assert result.exit_code == 1
    lines = result.output.splitlines()
    assert lines == [refusal_text("AE29F2008")]
    eprom_operator.erase_eprom.assert_not_called()
