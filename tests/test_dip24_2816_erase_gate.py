"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Erase refusal for the DIP24_2816 pin map.

These 24-pin 5V EEPROMs have no command decoder, so the 0x0D software erase
would store its command bytes as data and report success. The refusal lives
in `erase_support.erase_accepted`, so the erase flag, `info`, `dev test` and
`erase` agree.

Coverage:
  1. THE PREDICATE -- True for DIP24_2816 only; False for every other pin map
     in the database and for an absent key (fail open).
  2. COUPLING TO THE DATABASE -- the rows refused by this rule are exactly the
     19 DIP24_2816 rows, and every other 0x0D row keeps its erase.
  3. THE CLI -- `erase` on a DIP24_2816 part prints one refusal line and never
     reaches the operator or the hardware; `--ignore-unsupported` exits 0 with
     the same output; a 0x0D part on another pin map still reaches the
     operator.
  4. DEV TEST -- no erase step is planned for a DIP24_2816 part, and the
     reason says why; a 0x0D part on another pin map keeps its erase step.
"""

from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from firestarter import chip_test, dip24_2816_erase_gate
from firestarter.cli_handlers import cli
from firestarter.database import EpromDatabase
from firestarter.eprom_operations import EpromOperator
from firestarter.erase_support import erase_accepted
from firestarter.hardware import HardwareManager

from .conftest import make_app_context

_DB = EpromDatabase(skip_local_override=True)


def _rows():
    return [(m, c) for m, cs in _DB.proms.items() for c in cs]


# 1. The predicate


def test_predicate_is_true_for_dip24_2816_only():
    pinouts = {c["pinout"] for _, c in _rows()}
    assert "DIP24_2816" in pinouts
    for pinout in pinouts:
        assert dip24_2816_erase_gate.is_affected(pinout) is (pinout == "DIP24_2816")


@pytest.mark.parametrize("absent", [None, "", "dip24_2816"])
def test_predicate_fails_open_on_absent_or_other_keys(absent):
    assert dip24_2816_erase_gate.is_affected(absent) is False


# 2. Coupling to the database


def test_the_refused_rows_are_exactly_the_19_dip24_2816_rows():
    refused = {
        (m, c["part_number"])
        for m, c in _rows()
        if c["electrical"]["type"] in ("EEPROM", "Flash/EEPROM")
        and c["programming"]["algorithm"] == 0x0D
        and not erase_accepted(
            c["electrical"]["type"], c["programming"]["algorithm"], c["pinout"]
        )
    }
    dip24 = {(m, c["part_number"]) for m, c in _rows() if c["pinout"] == "DIP24_2816"}
    assert len(dip24) == 19
    assert refused == dip24


# 3. The CLI


def _app():
    operator = Mock(spec=EpromOperator)
    operator.erase_eprom.return_value = True
    hardware = Mock(spec=HardwareManager)
    return make_app_context(db=_DB, eprom_operator=operator, hardware_manager=hardware)


def test_cli_erase_on_a_dip24_2816_part_refuses_before_any_hardware_call():
    app = _app()
    result = CliRunner().invoke(cli, ["erase", "AT28C16"], obj=app)
    assert result.exit_code == 1
    assert result.output.splitlines() == [dip24_2816_erase_gate.refusal_text("AT28C16")]
    app.eprom_operator.erase_eprom.assert_not_called()
    assert app.hardware_manager.method_calls == []


def test_cli_erase_ignore_unsupported_exits_0_with_the_same_line():
    refused = CliRunner().invoke(cli, ["erase", "X2816A"], obj=_app())
    ignored_app = _app()
    ignored = CliRunner().invoke(
        cli, ["erase", "X2816A", "--ignore-unsupported"], obj=ignored_app
    )
    assert refused.exit_code == 1
    assert ignored.exit_code == 0
    assert ignored.output == refused.output
    ignored_app.eprom_operator.erase_eprom.assert_not_called()


def test_cli_erase_on_a_0x0d_part_on_another_pin_map_reaches_the_operator():
    app = _app()
    result = CliRunner().invoke(cli, ["erase", "AT28C256"], obj=app)
    assert result.exit_code == 0, result.output
    app.eprom_operator.erase_eprom.assert_called_once()


def test_refusal_text_names_the_chip_and_the_way_to_clear_it():
    text = dip24_2816_erase_gate.refusal_text("at28c16")
    assert "\n" not in text
    assert "AT28C16" in text
    assert "0xFF" in text


# 4. Dev test


def _erase_step(name):
    plan = chip_test.derive_plan(name, _DB, write_scope="full")
    steps = [s for s in plan.steps if s.op == chip_test.OP_ERASE]
    assert len(steps) == 1, [s.op for s in plan.steps]
    return steps[0]


def test_dev_test_plans_no_erase_for_a_dip24_2816_part():
    step = _erase_step("AT28C16")
    assert step.supported is False
    assert "no erase command" in step.reason


def test_dev_test_keeps_the_erase_for_a_0x0d_part_on_another_pin_map():
    assert _erase_step("AT28C256").supported is True
