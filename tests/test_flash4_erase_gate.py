"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Flash4 (protocol 0x05) erase gate.

Firmware 3.2.2 erases a 0x05 part with a 5 V software chip erase. Older
firmware either answers "not supported" or, in 3.0.0b8 to 3.0.0b26, erases
with VPP and VPE on: 12 V on a 5 V part.

Coverage:
  1. `erase` on a flash4 part reaches the operator with FLAG_CAN_ERASE set,
     and the database still leaves the flag clear, so `write` never has it.
  2. `needs_newer_firmware`: refused below 3.2.2 and for an unparseable
     version, accepted from 3.2.2 (and its pre-release and dev builds), and
     never applied to another command or another protocol.
  3. `setup_command` raises before the operation starts when a flash4 erase
     meets firmware older than 3.2.2.
  4. `is_flash4` over every shipped `algorithm` value, its fail-open polarity,
     and its coupling to the real database.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner

from firestarter import cli_handlers
from firestarter.chip_resolver import resolve_chip
from firestarter.cli_handlers import AppContext, cli
from firestarter.config import ConfigManager
from firestarter.constants import COMMAND_ERASE, COMMAND_WRITE, FLAG_CAN_ERASE
from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import (
    ChipNotFoundError,
    ChipNotImplementedError,
    FirmwareOutdatedError,
)
from firestarter.firmware import FirmwareManager
from firestarter.flash4_erase_gate import (
    FLASH4_PROTOCOL_ID,
    firmware_refusal_text,
    is_flash4,
    needs_newer_firmware,
)
from firestarter.hardware import HardwareManager
from firestarter.messages import MSG_OK_READY

from .conftest import build_frame

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


def test_cli_erase_on_flash4_part_sends_can_erase():
    runner = CliRunner()
    eprom_operator = Mock(spec=EpromOperator)
    eprom_operator.erase_eprom.return_value = True
    app = _cli_app_context(eprom_operator)
    with patch.object(
        cli_handlers,
        "resolve_chip",
        return_value={"algorithm": 5, "flags": 0, "bus-config": NO_PIN1_BUS_CONFIG},
    ):
        result = runner.invoke(cli, ["erase", "AE29F2008"], obj=app)

    assert result.exit_code == 0, result.output
    eprom_operator.erase_eprom.assert_called_once()
    sent = eprom_operator.erase_eprom.call_args.args[1]
    assert sent["flags"] & FLAG_CAN_ERASE


def test_database_leaves_can_erase_clear_for_flash4_writes():
    """`write` must never carry FLAG_CAN_ERASE for a flash4 part."""
    db = EpromDatabase(skip_local_override=True)
    data = resolve_chip("AE29F2008", db=db)
    assert is_flash4(data)
    assert not data["flags"] & FLAG_CAN_ERASE


def _erase(flags=FLAG_CAN_ERASE, algorithm=FLASH4_PROTOCOL_ID, cmd=COMMAND_ERASE):
    return {"cmd": cmd, "algorithm": algorithm, "flags": flags}


@pytest.mark.parametrize(
    "version",
    [None, "", "garbage", "3.0.0", "3.0.0b20", "3.1.1b2", "3.2.0", "3.2.1"],
)
def test_flash4_erase_needs_firmware_3_2_2(version):
    assert needs_newer_firmware(_erase(), version) is True


@pytest.mark.parametrize(
    "version",
    ["3.2.2", "3.2.2:uno", "3.2.2rc1:leonardo", "3.2.2.dev48", "3.2.10", "3.3.0"],
)
def test_flash4_erase_runs_on_firmware_3_2_2_and_later(version):
    assert needs_newer_firmware(_erase(), version) is False


@pytest.mark.parametrize(
    "command",
    [
        _erase(algorithm=6),
        _erase(cmd=COMMAND_WRITE),
        _erase(flags=0),
        {"state": 13},
    ],
)
def test_firmware_gate_applies_only_to_a_flagged_flash4_erase(command):
    assert needs_newer_firmware(command, "3.0.0b20") is False


def test_firmware_refusal_text_is_one_line_and_names_the_versions():
    text = firmware_refusal_text("3.2.1")
    assert "\n" not in text
    assert "3.2.2" in text and "3.2.1" in text


def _ready_frame(version: bytes) -> bytes:
    # [buffer_size u16][hw_revision u8][ver_len u8][version bytes]
    params = b"\x02\x00" + bytes([0]) + bytes([len(version)]) + version
    return build_frame(MSG_OK_READY, params)


def test_setup_command_refuses_flash4_erase_on_old_firmware(make_comm, fake_serial):
    fake_serial.feed(_ready_frame(b"3.2.1:uno"))
    comm = make_comm()
    with pytest.raises(FirmwareOutdatedError, match="3.2.2"):
        comm.setup_command(_erase(), MagicMock())


def test_setup_command_accepts_flash4_erase_on_3_2_2(make_comm, fake_serial):
    fake_serial.feed(_ready_frame(b"3.2.2:uno"))
    comm = make_comm()
    assert comm.setup_command(_erase(), MagicMock()) is True


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
    the real shipped database, is exactly the set of shipped supported rows
    whose `programming.algorithm` is 5. Both sides are derived from the
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
            if (
                chip.get("programming", {}).get("algorithm") == FLASH4_PROTOCOL_ID
                and chip.get("support_status", "supported") == "supported"
            ):
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
        result = runner.invoke(cli, ["erase", "AT28C256"], obj=app)

    assert result.exit_code == 0
    eprom_operator.erase_eprom.assert_called_once()
