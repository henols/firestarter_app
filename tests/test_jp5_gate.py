"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 182 — socket-pin-1 (JP5/A19) destructive-operation gate (SAFE-01/02/04).

A part whose pin map places an address line at or above A19 on socket pin 1
shares that pin with the RURP shield's JP5-switched 12.75V programming rail.
Four things are proved here:

  1. `socket_pin1_address_bit` / `is_affected` / `require_acknowledged` -- the
     pure policy, covering every case a bus-config can present.
  2. The gate's coupling to the REAL database -- that `DIP32_27C801` resolves
     to the exact 20-line bus this phase's data change is supposed to produce,
     with no dedicated `vpp-pin` key (it collides with `/OE` and is dropped).
  3. Integration through `EpromOperator.write_eprom` -- the refusal fires
     before `_operation_context` is ever entered.
  4. The off-TTY leg of `confirm_or_refuse` -- `confirm_fn` is never reached
     without a TTY.
  5. The affected-part set is DERIVED from `pinouts.json`, never hand-listed
     (SAFE-01, success criterion 2) -- a synthetic pin map merged with no
     source edit appears in the set exactly when its own pin map earns it,
     and the module carries no literal 8 Mbit part-number list.
"""

from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from firestarter import cli_handlers
from firestarter.cli_handlers import AppContext, cli
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase, pin_conversions
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import Pin1HazardRefusedError
from firestarter.firmware import FirmwareManager
from firestarter.hardware import HardwareManager
from firestarter.jp5_gate import (
    DAMAGE_CAPABLE_OPERATIONS,
    GATED_ADDRESS_BIT,
    SOCKET_PIN_1_BUS_LINE,
    confirm_or_refuse,
    is_affected,
    require_acknowledged,
    socket_pin1_address_bit,
)

AFFECTED_BUS_CONFIG = {
    "bus": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 20, 22, 21]
}
A18_BUS_CONFIG = {"bus": list(range(19))}
NO_PIN1_BUS_CONFIG = {"bus": [0, 1, 2]}


def test_socket_pin_1_bus_line_is_derived_not_literal():
    assert SOCKET_PIN_1_BUS_LINE == pin_conversions[32][1]


def test_socket_pin1_address_bit_20_line_bus_returns_19():
    assert socket_pin1_address_bit(AFFECTED_BUS_CONFIG) == GATED_ADDRESS_BIT


def test_socket_pin1_address_bit_19_line_bus_with_no_bus_line_21_returns_none():
    assert socket_pin1_address_bit(A18_BUS_CONFIG) is None


@pytest.mark.parametrize(
    "bus_config",
    [{}, None, {"bus": []}, NO_PIN1_BUS_CONFIG],
)
def test_socket_pin1_address_bit_absent_evidence_returns_none(bus_config):
    assert socket_pin1_address_bit(bus_config) is None


def test_is_affected_true_at_bit_19():
    assert is_affected(AFFECTED_BUS_CONFIG) is True


def test_is_affected_false_at_bit_18():
    assert is_affected(A18_BUS_CONFIG) is False


def test_is_affected_false_when_bit_is_none():
    assert is_affected({}) is False


def test_require_acknowledged_affected_and_not_acknowledged_raises():
    with pytest.raises(Pin1HazardRefusedError):
        require_acknowledged("DIP32_27C801", AFFECTED_BUS_CONFIG, "write", False)


def test_require_acknowledged_affected_and_acknowledged_returns_none():
    assert (
        require_acknowledged("DIP32_27C801", AFFECTED_BUS_CONFIG, "write", True) is None
    )


def test_require_acknowledged_not_affected_returns_none_regardless_of_acknowledged():
    assert require_acknowledged("W27C512", A18_BUS_CONFIG, "write", False) is None
    assert require_acknowledged("W27C512", A18_BUS_CONFIG, "write", True) is None


def test_require_acknowledged_no_bus_key_raises_fail_closed():
    with pytest.raises(Pin1HazardRefusedError):
        require_acknowledged("X", None, "write", True)
    with pytest.raises(Pin1HazardRefusedError):
        require_acknowledged("X", {}, "write", True)


@pytest.mark.parametrize("operation", ["read", "verify", "blank", "id"])
def test_require_acknowledged_non_damage_capable_operation_never_raises(operation):
    assert operation not in DAMAGE_CAPABLE_OPERATIONS
    assert (
        require_acknowledged("DIP32_27C801", AFFECTED_BUS_CONFIG, operation, False)
        is None
    )
    assert require_acknowledged("X", None, operation, False) is None


def test_confirm_or_refuse_off_tty_returns_false_and_never_calls_confirm_fn():
    isatty_fn = Mock(return_value=False)
    confirm_fn = Mock()
    result = confirm_or_refuse(
        "DIP32_27C801",
        AFFECTED_BUS_CONFIG,
        "write",
        isatty_fn=isatty_fn,
        confirm_fn=confirm_fn,
    )
    assert result is False
    confirm_fn.assert_not_called()


def test_confirm_or_refuse_tty_decline_returns_false_and_calls_confirm_fn_once():
    isatty_fn = Mock(return_value=True)
    confirm_fn = Mock(return_value=False)
    result = confirm_or_refuse(
        "DIP32_27C801",
        AFFECTED_BUS_CONFIG,
        "write",
        isatty_fn=isatty_fn,
        confirm_fn=confirm_fn,
    )
    assert result is False
    confirm_fn.assert_called_once()


def test_confirm_or_refuse_tty_accept_returns_true():
    isatty_fn = Mock(return_value=True)
    confirm_fn = Mock(return_value=True)
    result = confirm_or_refuse(
        "DIP32_27C801",
        AFFECTED_BUS_CONFIG,
        "write",
        isatty_fn=isatty_fn,
        confirm_fn=confirm_fn,
    )
    assert result is True


def test_dip32_27c801_resolves_to_the_expected_bus_with_no_vpp_pin():
    db = EpromDatabase(skip_local_override=True)
    bus_config = db.get_bus_config(32, "DIP32_27C801")
    assert bus_config is not None
    assert bus_config["bus"] == [
        0,
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        20,
        22,
        21,
    ]
    assert bus_config["bus"][GATED_ADDRESS_BIT] == pin_conversions[32][1]
    assert "vpp-pin" not in bus_config


def _make_operator():
    return EpromOperator(ConfigManager())


def test_write_eprom_on_affected_chip_refuses_before_operation_context():
    operator = _make_operator()
    eprom_data = {"bus-config": AFFECTED_BUS_CONFIG}
    with patch.object(EpromOperator, "_operation_context") as ctx_mock:
        with pytest.raises(Pin1HazardRefusedError):
            operator.write_eprom("DIP32_27C801", eprom_data, "in.bin")
        ctx_mock.assert_not_called()


def test_erase_eprom_on_affected_chip_refuses_before_operation_context():
    operator = _make_operator()
    eprom_data = {"bus-config": AFFECTED_BUS_CONFIG}
    with patch.object(EpromOperator, "_operation_context") as ctx_mock:
        with pytest.raises(Pin1HazardRefusedError):
            operator.erase_eprom("DIP32_27C801", eprom_data)
        ctx_mock.assert_not_called()


def test_erase_eprom_acknowledged_reaches_operation_context():
    operator = _make_operator()
    eprom_data = {"bus-config": AFFECTED_BUS_CONFIG}
    with patch.object(EpromOperator, "_operation_context") as ctx_mock:
        ctx_mock.return_value.__enter__ = Mock(return_value=(None, 0, "erase"))
        ctx_mock.return_value.__exit__ = Mock(return_value=False)
        result = operator.erase_eprom(
            "DIP32_27C801", eprom_data, pin1_hazard_acknowledged=True
        )
        ctx_mock.assert_called_once()
        assert result is False


def test_erase_eprom_unaffected_reaches_operation_context_regardless_of_acknowledged():
    operator = _make_operator()
    for eprom_data in (
        {"bus-config": A18_BUS_CONFIG},
        {"bus-config": NO_PIN1_BUS_CONFIG},
    ):
        with patch.object(EpromOperator, "_operation_context") as ctx_mock:
            ctx_mock.return_value.__enter__ = Mock(return_value=(None, 0, "erase"))
            ctx_mock.return_value.__exit__ = Mock(return_value=False)
            operator.erase_eprom("W27C512", eprom_data, pin1_hazard_acknowledged=False)
            ctx_mock.assert_called_once()


def test_confirm_or_refuse_erase_off_tty_returns_false_and_never_calls_confirm_fn():
    isatty_fn = Mock(return_value=False)
    confirm_fn = Mock()
    result = confirm_or_refuse(
        "DIP32_27C801",
        AFFECTED_BUS_CONFIG,
        "erase",
        isatty_fn=isatty_fn,
        confirm_fn=confirm_fn,
    )
    assert result is False
    confirm_fn.assert_not_called()


@pytest.mark.parametrize("operation", ["read", "verify", "blank", "id"])
def test_confirm_or_refuse_d06_scope_returns_true_with_no_hazard_printed(operation):
    confirm_fn = Mock()
    console = Mock()
    result = confirm_or_refuse(
        "DIP32_27C801",
        AFFECTED_BUS_CONFIG,
        operation,
        isatty_fn=Mock(return_value=False),
        confirm_fn=confirm_fn,
        console=console,
    )
    assert result is True
    confirm_fn.assert_not_called()
    console.print.assert_not_called()


def _cli_app_context(eprom_operator):
    return AppContext(
        db=Mock(),
        config_manager=ConfigManager(),
        eprom_operator=eprom_operator,
        hardware_manager=Mock(spec=HardwareManager),
        firmware_manager=Mock(spec=FirmwareManager),
        eprom_presenter=Mock(spec=EpromConsolePresenter),
    )


def test_cli_erase_with_force_on_affected_part_still_refuses_off_tty():
    runner = CliRunner()
    eprom_operator = Mock(spec=EpromOperator)
    app = _cli_app_context(eprom_operator)
    with patch.object(
        cli_handlers,
        "resolve_chip",
        return_value={"bus-config": AFFECTED_BUS_CONFIG},
    ):
        result = runner.invoke(cli, ["erase", "DIP32_27C801", "-f"], obj=app)
    assert result.exit_code == 1
    eprom_operator.erase_eprom.assert_not_called()


A18_REMAINDER_PINOUTS = {"DIP32_SST39SF040"}


def _derived_sets(db: EpromDatabase) -> tuple[set[str], set[str]]:
    structural = set()
    gated = set()
    for key in db.pin_maps:
        pin_count = int(key.split("_")[0].removeprefix("DIP"))
        bus_config = db.get_bus_config(pin_count, key)
        if socket_pin1_address_bit(bus_config) is not None:
            structural.add(key)
        if is_affected(bus_config):
            gated.add(key)
    return structural, gated


def test_structural_set_over_the_real_shipped_data_is_exactly_two_keys():
    db = EpromDatabase(skip_local_override=True)
    structural, _gated = _derived_sets(db)
    assert structural == {"DIP32_SST39SF040", "DIP32_27C801"}


def test_gated_set_over_the_real_shipped_data_is_exactly_dip32_27c801():
    db = EpromDatabase(skip_local_override=True)
    _structural, gated = _derived_sets(db)
    assert gated == {"DIP32_27C801"}


def test_socket_pin1_address_bit_for_the_two_structural_pinouts():
    db = EpromDatabase(skip_local_override=True)
    assert socket_pin1_address_bit(db.get_bus_config(32, "DIP32_SST39SF040")) == 18
    assert socket_pin1_address_bit(db.get_bus_config(32, "DIP32_27C801")) == 19


def test_a18_remainder_is_recorded_and_deliberately_not_gated():
    db = EpromDatabase(skip_local_override=True)
    structural, gated = _derived_sets(db)
    assert structural - gated == A18_REMAINDER_PINOUTS

    shipped_rows = sum(
        1
        for chips in db.proms.values()
        for chip in chips
        if chip.get("pinout") == "DIP32_SST39SF040"
    )
    assert shipped_rows == 255


def test_synthetic_pin_map_with_pin1_at_index19_appears_in_the_affected_set():
    db = EpromDatabase(skip_local_override=True)
    synthetic_key = "DIP32_SYNTHETIC_A19"
    db._merge_pin_maps(
        db.pin_maps,
        {
            synthetic_key: {
                "name": "Synthetic fixture -- pin 1 at address bit 19",
                "pins": {
                    "vcc-pin": [32],
                    "gnd-pin": [16],
                    "data-bus-pins": db.pin_maps["DIP32_27C801"]["pins"][
                        "data-bus-pins"
                    ],
                    "ce-pin": [22],
                    "oe-pin": [24],
                    "address-bus-pins": [
                        12,
                        11,
                        10,
                        9,
                        8,
                        7,
                        6,
                        5,
                        27,
                        26,
                        23,
                        25,
                        4,
                        28,
                        29,
                        3,
                        2,
                        30,
                        31,
                        1,
                    ],
                },
            }
        },
    )

    _structural, gated = _derived_sets(db)
    assert synthetic_key in gated


def test_synthetic_pin_map_with_pin1_at_index18_does_not_appear_in_the_affected_set():
    db = EpromDatabase(skip_local_override=True)
    synthetic_key = "DIP32_SYNTHETIC_A18"
    db._merge_pin_maps(
        db.pin_maps,
        {
            synthetic_key: {
                "name": "Synthetic fixture -- pin 1 at address bit 18",
                "pins": {
                    "vcc-pin": [32],
                    "gnd-pin": [16],
                    "data-bus-pins": db.pin_maps["DIP32_27C801"]["pins"][
                        "data-bus-pins"
                    ],
                    "ce-pin": [22],
                    "oe-pin": [24],
                    "address-bus-pins": [
                        12,
                        11,
                        10,
                        9,
                        8,
                        7,
                        6,
                        5,
                        27,
                        26,
                        23,
                        25,
                        4,
                        28,
                        29,
                        3,
                        2,
                        30,
                        1,
                    ],
                },
            }
        },
    )

    structural, gated = _derived_sets(db)
    assert synthetic_key in structural
    assert synthetic_key not in gated


def test_get_bus_config_unknown_pinout_returns_none():
    db = EpromDatabase(skip_local_override=True)
    assert db.get_bus_config(32, "DIP32_DOES_NOT_EXIST") is None


def test_require_acknowledged_fails_closed_through_a_real_unknown_pinout_lookup():
    db = EpromDatabase(skip_local_override=True)
    unresolved_bus_config = db.get_bus_config(32, "DIP32_DOES_NOT_EXIST")
    with pytest.raises(Pin1HazardRefusedError):
        require_acknowledged(
            "DIP32_DOES_NOT_EXIST", unresolved_bus_config, "write", True
        )
    with pytest.raises(Pin1HazardRefusedError):
        require_acknowledged("DIP32_DOES_NOT_EXIST", {}, "write", True)
