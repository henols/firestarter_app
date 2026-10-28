"""CLI-surface tests for `firestarter ram-test`.

Each leg drives the real Click command with `CliRunner` and a
`MagicMock(spec=EpromOperator)` at the hardware boundary:
`EpromOperator.ram_test` is the one method that would open a serial port, so
`assert_not_called()` proves a refusal opened no port.

Exit codes: 0 the RAM passed, 1 a bad cell, 2 the test could not run to the
end. Each leg checks the printed text together with the code.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from firestarter.cli_handlers import AppContext, cli
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import FirmwareOutdatedError, SerialError
from firestarter.ram_test import RamTestFailure

from .conftest import make_app_context


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _app() -> tuple[AppContext, MagicMock]:
    operator = MagicMock(spec=EpromOperator)
    operator.last_firmware_error_message = None
    return make_app_context(eprom_operator=operator), operator


def _invoke(runner: CliRunner, app: AppContext, chip: str):
    return runner.invoke(cli, ["ram-test", chip], obj=app)


def test_pass_exits_zero(runner: CliRunner) -> None:
    app, operator = _app()
    operator.ram_test.return_value = (True, None)

    result = _invoke(runner, app, "62256")

    assert result.exit_code == 0, result.output
    assert "RAM test PASSED: 62256" in result.output
    assert "32768 bytes" in result.output
    operator.ram_test.assert_called_once()


def test_bad_cell_exits_one_with_the_failure(runner: CliRunner) -> None:
    app, operator = _app()
    operator.ram_test.return_value = (
        False,
        RamTestFailure(element=3, address=0x2000, expected=0x00, actual=0x04),
    )

    result = _invoke(runner, app, "62256")

    assert result.exit_code == 1, result.output
    assert "RAM test FAILED: 62256" in result.output
    assert "0x002000" in result.output
    assert "Bad bits: 0x04" in result.output


@pytest.mark.parametrize(
    ("chip", "text"),
    [
        ("AT28C256", "not a RAM"),
        ("FM1608", "without power"),
        ("DS1225(TEST)", "without power"),
    ],
)
def test_refusal_exits_two_without_opening_the_port(
    runner: CliRunner, chip: str, text: str
) -> None:
    app, operator = _app()

    result = _invoke(runner, app, chip)

    assert result.exit_code == 2, result.output
    assert text in result.output
    operator.ram_test.assert_not_called()


def test_unknown_chip_exits_two(runner: CliRunner) -> None:
    app, operator = _app()

    result = _invoke(runner, app, "NOSUCHCHIP123")

    assert result.exit_code == 2, result.output
    operator.ram_test.assert_not_called()


def test_outdated_firmware_exits_two(runner: CliRunner) -> None:
    app, operator = _app()
    operator.ram_test.side_effect = FirmwareOutdatedError("62256: unknown command")

    result = _invoke(runner, app, "62256")

    assert result.exit_code == 2, result.output
    assert "Firmware outdated" in result.output


def test_communication_error_exits_two(runner: CliRunner) -> None:
    app, operator = _app()
    operator.ram_test.side_effect = SerialError("port gone")

    result = _invoke(runner, app, "62256")

    assert result.exit_code == 2, result.output
    assert "port gone" in result.output


def test_incomplete_test_exits_two_with_the_firmware_text(runner: CliRunner) -> None:
    app, operator = _app()
    operator.ram_test.return_value = (False, None)
    operator.last_firmware_error_message = "Command timeout"

    result = _invoke(runner, app, "62256")

    assert result.exit_code == 2, result.output
    assert "did not complete: Command timeout" in result.output
