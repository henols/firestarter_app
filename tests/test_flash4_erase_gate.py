"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Flash4 (protocol 0x05) erase-refusal gate.

Coverage:
  1. THE PURE PREDICATE -- `is_flash4` parametrized over every `algorithm`
     value the shipped database actually contains: True for 5, False for
     every other value present.
  2. FAIL-OPEN -- a missing, `None`, or empty wire dict, or one with no
     `algorithm` key, returns False rather than raising or refusing. This
     polarity is deliberately inverted against `jp5_gate` and
     `sdp_capability`, which both fail CLOSED on absent evidence.
  3. COUPLING TO THE REAL DATABASE -- the set of
     part numbers for which the predicate fires, driven through
     `resolve_chip` against `EpromDatabase(skip_local_override=True)`, is
     exactly the set of shipped rows whose `programming.algorithm` is 5.
  4. NO PART-NUMBER LITERAL IN THE MODULE -- no shipped `part_number` string
     occurs anywhere in `flash4_erase_gate.py`'s source, docstrings
     included, proving the predicate is database-derived rather than a
     hand-kept list.
  5. THE MESSAGE SHAPE -- the rendered refusal line is exactly one
     line, names the chip, and carries none of a forbidden-substring list
     covering the cause, the alternative, and the `--force` workaround.
  6. NOT A BLANKET REFUSAL -- a part whose algorithm is not 5 still
     reaches `EpromOperator.erase_eprom`.

End-to-end proof that `firestarter erase` on a flash4 part refuses before
the serial port opens, printing exactly one line and never reaching
`EpromOperator.erase_eprom` -- the pre-connect guarantee.
"""

import inspect
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from firestarter import cli_handlers
from firestarter import flash4_erase_gate as flash4_erase_gate_mod
from firestarter.chip_resolver import resolve_chip
from firestarter.cli_handlers import AppContext, cli
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import ChipNotFoundError, ChipNotImplementedError
from firestarter.firmware import FirmwareManager
from firestarter.flash4_erase_gate import FLASH4_PROTOCOL_ID, is_flash4, refusal_text
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


def test_cli_erase_ignore_unsupported_exits_0_with_byte_identical_output():
    runner = CliRunner()

    exit1_operator = Mock(spec=EpromOperator)
    exit1_app = _cli_app_context(exit1_operator)
    with patch.object(
        cli_handlers,
        "resolve_chip",
        return_value={"algorithm": 5, "bus-config": NO_PIN1_BUS_CONFIG},
    ):
        exit1_result = runner.invoke(cli, ["erase", "AE29F2008"], obj=exit1_app)

    exit0_operator = Mock(spec=EpromOperator)
    exit0_app = _cli_app_context(exit0_operator)
    with patch.object(
        cli_handlers,
        "resolve_chip",
        return_value={"algorithm": 5, "bus-config": NO_PIN1_BUS_CONFIG},
    ):
        exit0_result = runner.invoke(
            cli, ["erase", "AE29F2008", "--ignore-unsupported"], obj=exit0_app
        )

    assert exit1_result.exit_code == 1
    assert exit0_result.exit_code == 0
    assert exit0_result.output == exit1_result.output
    exit1_operator.erase_eprom.assert_not_called()
    exit0_operator.erase_eprom.assert_not_called()


def _shipped_algorithm_values(db: EpromDatabase) -> set:
    values = set()
    for vendor_chips in db.proms.values():
        for chip in vendor_chips:
            algo = chip.get("programming", {}).get("algorithm")
            if algo is not None:
                values.add(algo)
    return values


_SHIPPED_ALGORITHM_VALUES = sorted(
    _shipped_algorithm_values(EpromDatabase(skip_local_override=True))
)


@pytest.mark.parametrize("algorithm", _SHIPPED_ALGORITHM_VALUES)
def test_is_flash4_true_only_for_algorithm_5_over_shipped_values(algorithm):
    """The pure predicate, over every `algorithm` value the shipped
    database actually contains -- not a hand-picked subset."""
    assert is_flash4({"algorithm": algorithm}) == (algorithm == FLASH4_PROTOCOL_ID)


@pytest.mark.parametrize(
    "programmer_data",
    [None, {}, {"bus-config": NO_PIN1_BUS_CONFIG}, {"algorithm": None}],
)
def test_is_flash4_fails_open_on_absent_evidence(programmer_data):
    """FAIL-OPEN, deliberately inverted against `jp5_gate.is_affected` and
    `sdp_capability`, which both fail CLOSED on absent evidence.

    Refusing every wire dict this predicate cannot classify would break
    `erase` for every chip whose wire dict happens to lack `algorithm` --
    an availability regression this predicate must never cause. This is
    the single most likely thing a later reader will "fix" back to
    fail-closed; this test is the place that survives to argue with them.
    """
    assert is_flash4(programmer_data) is False


def test_predicate_matches_real_database_algorithm_5_rows_exactly():
    """COUPLING TO THE REAL DATABASE: the set of part numbers for
    which the predicate fires, each driven through `resolve_chip` against
    the real shipped database, is exactly the set of shipped rows whose
    `programming.algorithm` is 5. Both sides are derived from the
    database -- no literal count or membership list.
    """
    db = EpromDatabase(skip_local_override=True)

    expected = set()
    all_part_numbers = set()
    for vendor_chips in db.proms.values():
        for chip in vendor_chips:
            part_number = chip.get("part_number", "")
            if not part_number:
                continue
            all_part_numbers.add(part_number)
            if chip.get("programming", {}).get("algorithm") == FLASH4_PROTOCOL_ID:
                expected.add(part_number)

    actual = set()
    for name in all_part_numbers:
        try:
            programmer_data = resolve_chip(name, db=db)
        except (ChipNotFoundError, ChipNotImplementedError):
            continue
        if is_flash4(programmer_data):
            actual.add(name)

    assert actual == expected, (
        f"predicate fired for {len(actual)} part number(s) against "
        f"{len(expected)} shipped algorithm-5 row(s); symmetric difference: "
        f"{sorted(actual ^ expected)}"
    )


def _module_source(module) -> str:
    path = inspect.getsourcefile(module)
    assert path is not None, f"could not resolve a source file for {module!r}"
    return Path(path).read_text(encoding="utf-8")


def test_module_source_contains_no_shipped_part_number_literal():
    """NO PART-NUMBER LITERAL IN THE MODULE: every shipped `part_number`
    string is checked, not a sample -- this is what makes
    "database-derived, never a hand-kept list" a checked fact rather than
    a claim.
    """
    db = EpromDatabase(skip_local_override=True)
    source_upper = _module_source(flash4_erase_gate_mod).upper()

    offending = set()
    for vendor_chips in db.proms.values():
        for chip in vendor_chips:
            part_number = chip.get("part_number", "")
            if part_number and part_number.upper() in source_upper:
                offending.add(part_number)

    assert not offending, (
        "flash4_erase_gate.py source contains shipped part-number "
        f"literal(s): {sorted(offending)}"
    )


FORBIDDEN_REFUSAL_SUBSTRINGS = (
    "force",
    "firestarter write",
    "write",
    "workaround",
    "route around",
    "because",
    "reason",
    "cause",
    "alternative",
    "instead",
    "try",
)


def test_refusal_text_is_one_line_and_carries_no_forbidden_content():
    """THE MESSAGE SHAPE: exactly one line, names the chip, and
    carries none of a forbidden-substring list covering the cause, the
    alternative, and the `--force` forged-identity workaround. A NEGATIVE
    assertion on purpose -- it is what stops a later executor quietly
    re-adding the cause clause and the alternative that were deliberately
    left out of the tool's output.
    """
    text = refusal_text("ae29f2008")

    assert "\n" not in text
    assert text == "Erase not supported for AE29F2008"

    lowered = text.lower()
    for forbidden in FORBIDDEN_REFUSAL_SUBSTRINGS:
        assert forbidden not in lowered, (
            f"refusal text {text!r} contains forbidden substring {forbidden!r}"
        )


def test_cli_erase_on_non_flash4_part_still_reaches_operator():
    """NOT A BLANKET REFUSAL: a part whose algorithm is not 5 still
    reaches `EpromOperator.erase_eprom`. Without this leg, a fail-closed
    regression in the FAIL-OPEN group would pass every other test in this
    file.
    """
    runner = CliRunner()
    eprom_operator = Mock(spec=EpromOperator)
    eprom_operator.erase_eprom.return_value = True
    app = _cli_app_context(eprom_operator)
    with patch.object(
        cli_handlers,
        "resolve_chip",
        return_value={"algorithm": 13, "bus-config": NO_PIN1_BUS_CONFIG},
    ):
        result = runner.invoke(cli, ["erase", "AM28C16A"], obj=app)

    assert result.exit_code == 0
    eprom_operator.erase_eprom.assert_called_once()
