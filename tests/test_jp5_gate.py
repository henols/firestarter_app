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
"""

from unittest.mock import Mock, patch

import pytest

from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase, pin_conversions
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import Pin1HazardRefusedError
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
