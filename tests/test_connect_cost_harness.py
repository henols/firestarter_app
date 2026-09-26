"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Pytest unit tests for the connect-cost harness (Phase 176 plan 04, MEAS-01
instrument half): `EpromOperator._summarize_connect_samples`,
`EpromOperator.measure_connect_cost`, and the `dev fault-inject
--mode connect-cost` CLI dispatch.

Every test here runs with no board attached. Tests 1-4 call
`_summarize_connect_samples` directly with hand-built sample lists. Test 5
constructs an operator whose config resolves no port. Test 6 drives the CLI
through a mocked operator, exactly like `test_dev_fault_inject_pass` /
`test_dev_fault_inject_fail` in `tests/test_cli_handlers.py`. Test 7 pins
`channel.BETA_ONLY_DEV_COMMANDS` unchanged -- no new `dev` subcommand.
"""

from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from firestarter import channel
from firestarter.cli_handlers import cli
from firestarter.config import ConfigManager
from firestarter.eprom_operations import EpromOperator
from firestarter.serial_comm import SerialCommunicator

from .conftest import make_app_context


class TestSummarizeConnectSamples:
    def test_odd_sample_count_reports_min_median_max_no_mean(self) -> None:
        summary = EpromOperator._summarize_connect_samples([2.6, 2.5, 2.9])
        assert summary["min"] == "2.500s"
        assert summary["median"] == "2.600s"
        assert summary["max"] == "2.900s"
        assert summary["structural_floor"] == "2.500s"
        assert not any("mean" in key for key in summary)

    def test_even_sample_count_reports_lower_median_not_average(self) -> None:
        summary = EpromOperator._summarize_connect_samples([1.0, 2.0, 3.0, 4.0])
        assert summary["median"] == "2.000s", (
            "median_low must report the lower of the two middle values "
            "(2.0), never the interpolated average (2.5) -- every reported "
            "figure must be one that was actually observed."
        )

    def test_empty_sample_list_reports_unmeasured_not_a_number(self) -> None:
        summary = EpromOperator._summarize_connect_samples([])
        assert summary["samples"] == "0"
        assert summary["min"] == "unmeasured"
        assert summary["median"] == "unmeasured"
        assert summary["max"] == "unmeasured"
        assert summary["remainder"] == "unmeasured"

    def test_structural_floor_is_two_point_five_seconds_and_remainder_subtracts_it(
        self,
    ) -> None:
        summary = EpromOperator._summarize_connect_samples([2.6, 2.5, 2.9])
        assert summary["structural_floor"] == "2.500s"
        assert summary["remainder"] == "0.100s"


class TestMeasureConnectCostRefusesWithoutAPort:
    def test_no_port_returns_false_and_opens_nothing(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(ConfigManager, "get_value", lambda self, *a, **kw: None)
        connect_attempted = Mock()
        monkeypatch.setattr(SerialCommunicator, "find_and_connect", connect_attempted)
        op = EpromOperator(ConfigManager())
        result = op.measure_connect_cost(
            samples=3,
            port=None,
            output_dir=str(tmp_path / "connect_cost_noport"),
        )
        assert result is False
        connect_attempted.assert_not_called()


class TestDevFaultInjectConnectCostDispatch:
    @pytest.fixture
    def runner(self) -> CliRunner:
        return CliRunner()

    def test_connect_cost_mode_dispatches_and_exits_zero_on_true(
        self, runner: CliRunner
    ) -> None:
        operator = Mock(spec=EpromOperator)
        operator.measure_connect_cost.return_value = True  # type: ignore[attr-defined]
        app = make_app_context(eprom_operator=operator)
        result = runner.invoke(
            cli, ["dev", "fault-inject", "W27C512", "--mode", "connect-cost"], obj=app
        )
        assert result.exit_code == 0, (
            f"Expected exit 0 (connect-cost measured), got {result.exit_code}. "
            f"Output: {result.output!r}"
        )
        operator.measure_connect_cost.assert_called_once()  # type: ignore[attr-defined]

    def test_connect_cost_mode_dispatches_and_exits_one_on_false(
        self, runner: CliRunner
    ) -> None:
        operator = Mock(spec=EpromOperator)
        operator.measure_connect_cost.return_value = False  # type: ignore[attr-defined]
        app = make_app_context(eprom_operator=operator)
        result = runner.invoke(
            cli, ["dev", "fault-inject", "W27C512", "--mode", "connect-cost"], obj=app
        )
        assert result.exit_code == 1, (
            f"Expected exit 1 (connect-cost unmeasured), got {result.exit_code}. "
            f"Output: {result.output!r}"
        )


class TestNoNewDevSubcommandRegistered:
    def test_beta_only_dev_commands_tuple_is_byte_identical_to_its_pin(self) -> None:
        assert channel.BETA_ONLY_DEV_COMMANDS == (
            "reg",
            "addr",
            "consistency-check",
            "write-cycle",
            "fault-inject",
            "validate-family",
            "lock-status",
        )
        assert len(channel.BETA_ONLY_DEV_COMMANDS) == 7
