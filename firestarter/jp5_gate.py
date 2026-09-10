"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Socket-pin-1 destructive-operation gate (SAFE-01/02/04).

On the shipped RURP Rev 2.x shield, socket pin 1 is switched by JP5 (KiCad
value `A19_CUT`, a Bridged SolderJumper footprint -- bridged by default) onto
the same 12.75V rail the programming voltage regulator drives. A part whose
pin map places an address line at or above A19 on socket pin 1 -- the 8 Mbit
27C080/M27C801 class -- has that address line driven from the programming
rail on every write or erase while JP5 stays bridged. This module is the
policy: which parts are affected, which operations can cause the damage, and
the refusal/confirmation that stands between a damage-capable operation and
the serial link.

No I/O, no environment reads, no serial access -- the wire dict and the
operation name are the whole input, which is what makes the policy testable
without a board and keeps eprom_operations.py and cli_handlers.py free of
the reasoning.
"""

import sys
from typing import Any, Callable, Optional

from rich.prompt import Confirm

from firestarter.database import pin_conversions
from firestarter.exceptions import Pin1HazardRefusedError

SOCKET_PIN_1_BUS_LINE = pin_conversions[32][1]

GATED_ADDRESS_BIT = 19

DAMAGE_CAPABLE_OPERATIONS = frozenset({"write", "erase"})


def socket_pin1_address_bit(bus_config: Optional[dict]) -> Optional[int]:
    """Return the address-bit index socket pin 1 carries, or None.

    `bus_config["bus"]` is an ordered A0..An list of RURP bus lines, so the
    index of `SOCKET_PIN_1_BUS_LINE` within it IS the address bit socket
    pin 1 carries. Returns None for a falsy `bus_config`, a missing or empty
    `bus` list, or a `bus` list that does not carry `SOCKET_PIN_1_BUS_LINE`
    at all.
    """
    if not bus_config:
        return None
    bus = bus_config.get("bus")
    if not bus or SOCKET_PIN_1_BUS_LINE not in bus:
        return None
    return bus.index(SOCKET_PIN_1_BUS_LINE)


def is_affected(bus_config: Optional[dict]) -> bool:
    """True when socket pin 1 carries an address line at or above A19.

    The comparison is `>=`, not `==`: address-bit index is a genuinely
    ordered scale, so this also gates any future part whose pin map places
    an even higher address bit on socket pin 1.
    """
    bit = socket_pin1_address_bit(bus_config)
    return bit is not None and bit >= GATED_ADDRESS_BIT


def hazard_text(chip_name: str, operation: str, address_bit: int) -> str:
    """The operator-facing hazard message, checkable against the JP5 silkscreen."""
    return (
        f"{chip_name.upper()}: socket pin 1 on this part carries address line "
        f"A{address_bit}. A {operation} drives socket pin 1 from the 12.75V "
        f"programming rail once the address crosses A{address_bit}. JP5 "
        '(silkscreened "Cut for ROMs with A19 on P1") ships bridged by '
        "default (a Bridged SolderJumper footprint) and this tool cannot read "
        "its current state on the attached board. JP5 must be cut before this "
        f"{operation} proceeds, or the part can be damaged. The only escape "
        "is answering yes at the interactive prompt."
    )


def require_acknowledged(
    chip_name: str,
    bus_config: Optional[dict],
    operation: str,
    acknowledged: bool,
) -> None:
    """The unconditional operator-layer guard. Raises on reject, returns on pass.

    Returns immediately when `operation` is not damage-capable. Raises
    `Pin1HazardRefusedError` when `bus_config` carries no `bus` list at all --
    fail-closed, because absent evidence cannot prove socket pin 1 is safe --
    and again when the part is affected and `acknowledged` is false.
    """
    if operation not in DAMAGE_CAPABLE_OPERATIONS:
        return
    if not bus_config or not bus_config.get("bus"):
        raise Pin1HazardRefusedError(
            f"{chip_name.upper()}: refusing to {operation} -- no bus "
            "configuration is available to prove socket pin 1 is safe, and "
            "absent evidence is never treated as safe."
        )
    bit = socket_pin1_address_bit(bus_config)
    if bit is not None and bit >= GATED_ADDRESS_BIT and not acknowledged:
        raise Pin1HazardRefusedError(hazard_text(chip_name, operation, bit))


def _print(msg: str, *, console: Any = None) -> None:
    if console is not None:
        console.print(msg)
    else:
        print(msg)


def confirm_or_refuse(
    chip_name: str,
    bus_config: Optional[dict],
    operation: str,
    *,
    isatty_fn: Optional[Callable[[], bool]] = None,
    confirm_fn: Callable[..., bool] = Confirm.ask,
    console: Any = None,
) -> bool:
    """The CLI-boundary prompt. Never a default-yes.

    Returns True immediately when the operation is not damage-capable or the
    part is not affected -- no hazard text is printed on that path. Otherwise
    prints `hazard_text`, then refuses off-TTY before `confirm_fn` is ever
    reached, and asks with `default=False` when a TTY is present.
    """
    if operation not in DAMAGE_CAPABLE_OPERATIONS or not is_affected(bus_config):
        return True

    bit = socket_pin1_address_bit(bus_config)
    assert bit is not None
    _print(hazard_text(chip_name, operation, bit), console=console)

    isatty_fn = isatty_fn or (lambda: sys.stdin.isatty())
    if not isatty_fn():
        _print(
            f"{chip_name.upper()}: refusing to {operation} -- not an "
            "interactive session, so the JP5 hazard cannot be confirmed.",
            console=console,
        )
        return False

    return bool(
        confirm_fn(
            f"Cut JP5 before continuing? Answer yes only once it is cut, to "
            f"proceed with {operation} on {chip_name.upper()}",
            default=False,
        )
    )
