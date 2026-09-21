"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Host-side pre-write blank-guard policy (WRITE-01/WRITE-06, Phase 203).

`firestarter write` used to depend entirely on the firmware's own write-init
pre-flight to refuse a write onto a non-blank part -- the operator only
learned that a UV-EPROM slot was already programmed after the port was
opened and a real programming pulse was already in flight for the first
non-blank byte. This module is the host-side policy that decides, before any
serial byte is sent, whether a given write needs that guarantee proven ahead
of time: which protocols the firmware itself has always pre-flighted, which
parts are exempt because an erase immediately above the write already
guarantees blank, and the one-line refusal text for the parts that are not.

Like `jp5_gate.py`, `flash4_erase_gate.py`, `sdp_capability.py` and
`page_size_gate.py`, this is a pure predicate: no I/O, no environment reads,
no serial access -- a wire dict and the operation's flags are the whole
input, which is what keeps the policy testable without a board and keeps
`eprom_operations.py` free of the reasoning.

The one deliberate deviation from `flash4_erase_gate.is_flash4`'s polarity,
the same deviation `page_size_gate` and `jp5_gate` already make for their
own cases: this gate FAILS CLOSED. `flash4_erase_gate` guards an
*availability* property -- refusing an unclassifiable part there breaks
`erase` for it, an availability regression, not a safety one. This guard is
the whole safety net after Phase 205 removes the firmware's own write-init
pre-flight, so guessing wrong here in the safe direction -- guarding a part
this predicate cannot classify -- costs an unnecessary read and a refusal
the operator clears with `-b`/`--no-blank-check`, the documented escape.
Guessing wrong in the other direction risks an irreversible overwrite of a
UV-EPROM part that can never be un-programmed. Absence of evidence is
treated as "not provably safe", never as "probably fine".
"""

from __future__ import annotations

from typing import Any, Mapping  # noqa: UP035

from firestarter.address_parser import parse_address
from firestarter.constants import (
    FLAG_CAN_ERASE,
    FLAG_SKIP_BLANK_CHECK,
    FLAG_SKIP_ERASE,
)
from firestarter.exceptions import NegativeStartAddressError
from firestarter.flash4_erase_gate import FLASH4_PROTOCOL_ID
from firestarter.sdp_capability import SDP_PROTOCOL_ID

# D-01: this set is defined by what the firmware pre-flights TODAY, not by
# `FLAG_CAN_ERASE` alone -- a literal `FLAG_CAN_ERASE`-only exemption would
# make the host start refusing writes on protocol 0x05 and on every
# SRAM/FRAM part, families the firmware has never blank-checked.
GUARDED_PROTOCOL_IDS: frozenset[int] = frozenset({0x06, 0x07, 0x08, 0x0B, 0x10})
"""The protocol ids whose firmware write-init path blank-checks today:

- `0x07` / `0x08` / `0x0B` -- UV-EPROM, region-scoped, `eprom.cpp:144-145`.
- `0x06` -- NOR unlock, whole-device, after an erase, `flash_nor_unlock.cpp:104-105`.
- `0x10` -- Intel flash, whole-device, after an erase, `flash_intel.cpp:94-95`.

A part whose erase actually ran (`is_erase_exempt` below) is exempt from
this set's guard even though its protocol id is a member."""

SRAM_PROTOCOL_IDS: frozenset[int] = frozenset({0x0E, 0x27, 0x28, 0x29})
"""SRAM/FRAM -- a NAMED exemption, not an accident of a flag test. These
parts are volatile or byte-rewritable and have no factory-blank state at
all; the firmware has no blank-check op for them (`check_eprom_blank`'s own
SRAM/FRAM short-circuit, above this module, answers the same question for
the read-back compare path)."""

NAMED_EXEMPT_PROTOCOL_IDS: frozenset[int] = SRAM_PROTOCOL_IDS | {
    FLASH4_PROTOCOL_ID,
    SDP_PROTOCOL_ID,
}
"""Documentation and the pinning test's other half -- the union of every
named exemption. `0x05` (flash4) auto-erases per page, and its
`FLAG_CAN_ERASE` is deliberately CLEARED for a hardware-safety reason
(`database.py:575-580`: setting it would route a 12V bulk erase onto a
5V-only part), so it can never be read as a blank-state signal -- it must be
named here instead.

`0x0D` (28C parallel / SDP, `SDP_PROTOCOL_ID`) also auto-erases per page
(`eeprom_28c.cpp:379-384`) and never blank-checks at write-init. It carries
84 shipped rows across 15 vendors -- the single largest exemption by row
count, larger than every guarded protocol's own row count. D-01's prose
names only SRAM/FRAM and flash4 by name; its own firmware table lists this
family as unguarded too, and the row count is exactly why it is named here
explicitly rather than left to be inferred from the table -- an omission
this large reads as an oversight unless it is spelled out.

Membership here is documentation only: `is_guarded_protocol` below decides
guarded-ness from `GUARDED_PROTOCOL_IDS` alone, never from this set."""

_REFUSAL_FORMAT = (
    "Refusing write to {chip_name}: not blank at 0x{address:06X}, v: 0x{value:02X}."
)


def effective_flags(
    programmer_data: Mapping[str, Any] | None, operation_flags: int
) -> int:
    """The same effective-flags quantity `_setup_operation` computes at its
    `command_dict["flags"] = eprom_data_dict.get("flags", 0) | operation_flags`
    line (`eprom_operations.py`) -- reproduced here rather than shared,
    because that expression assumes a non-`None` dict and this predicate
    must not. A comment at that site names this function, and this
    docstring names that site, because two expressions for one quantity is
    how drift starts.

    `flags` is a key `convert_to_programmer` always emits; defaulting it to
    0 for a missing, `None`, or falsy `programmer_data` is the fail-closed
    direction -- no erase capability claimed means no exemption.
    """
    return int((programmer_data or {}).get("flags", 0) or 0) | operation_flags


def is_guarded_protocol(programmer_data: Mapping[str, Any] | None) -> bool:
    """True when the wire dict's `algorithm` names a protocol the firmware
    pre-flights today (D-01).

    Absent, `None`, or a falsy `programmer_data`, and a dict carrying no
    `algorithm` key or a `None` `algorithm` value, all return True --
    fail-closed (D-05), following `jp5_gate.require_acknowledged` and
    `sdp_capability`, deliberately NOT `flash4_erase_gate.is_flash4`'s
    opposite polarity. The reason the polarities differ: `flash4_erase_gate`
    guards an availability property, this guard is the whole safety net
    after Phase 205, and its worst case is an unnecessary read plus a
    refusal the operator clears with the documented bypass flag.

    An `algorithm` in neither `GUARDED_PROTOCOL_IDS` nor
    `NAMED_EXEMPT_PROTOCOL_IDS` is NOT guarded (Fork A). This is the one
    place D-01 outranks D-05: "preserve today's coverage" is the operator's
    load-bearing instruction, and a protocol id the firmware has never
    blank-checked is by definition outside today's coverage. Such an id is
    only reachable through a user-supplied `~/.firestarter` database
    override -- every shipped row resolves to one of the twelve ids this
    module and its siblings between them account for.
    """
    if not programmer_data:
        return True
    algorithm = programmer_data.get("algorithm")
    if algorithm is None:
        return True
    return algorithm in GUARDED_PROTOCOL_IDS


def is_erase_exempt(
    programmer_data: Mapping[str, Any] | None, operation_flags: int
) -> bool:
    """True when this invocation's effective flags claim erase capability
    that actually ran: `FLAG_CAN_ERASE` set and `FLAG_SKIP_ERASE` clear.

    This is the static, per-invocation form of D-01's "a part whose erase
    actually ran is exempt", derived from the firmware's own
    `if FLAG_CAN_ERASE and not FLAG_SKIP_ERASE` erase gate
    (`flash_nor_unlock.cpp`, `flash_intel.cpp`). `dev write-cycle` reaches
    the same conclusion through this flag proxy rather than by observing
    the erase's own result.
    """
    flags = effective_flags(programmer_data, operation_flags)
    return bool(flags & FLAG_CAN_ERASE) and not bool(flags & FLAG_SKIP_ERASE)


def requires_blank_check(
    programmer_data: Mapping[str, Any] | None, operation_flags: int
) -> bool:
    """The top-level verdict: must this write's target region be proven
    blank before any programming command reaches the wire?

    False when `FLAG_SKIP_BLANK_CHECK` is set in the effective flags
    (WRITE-03 / D-09: the documented bypass is the blank-check flag,
    `FLAG_FORCE` is deliberately not consulted here), False when
    `is_erase_exempt`, False when not `is_guarded_protocol`, True
    otherwise.
    """
    flags = effective_flags(programmer_data, operation_flags)
    if flags & FLAG_SKIP_BLANK_CHECK:
        return False
    if is_erase_exempt(programmer_data, operation_flags):
        return False
    return is_guarded_protocol(programmer_data)


# The module reads ONLY the `algorithm` and `flags` keys of the wire dict --
# never `electrical-type`, never `protocol-id`. The programmer wire dict
# `convert_to_programmer` produces carries neither of those two keys, which
# is exactly why `check_eprom_blank`'s SRAM short-circuit (keyed on
# `electrical-type`/`protocol-id`) is inert in production today -- the dict
# it is handed never carries the keys it reads. This predicate must never
# join that failure class.


def refusal_text(chip_name: str, address: int, value: int) -> str:
    """The one-line, host-voiced refusal text (D-10/D-12).

    Built from `_REFUSAL_FORMAT` rather than assembled at the raise site,
    following `flash4_erase_gate.refusal_text`'s shape, so a test can
    assert the exact sentence instead of a substring of a log line.

    D-10's three properties: it names the HOST as the refuser, so an
    operator cannot mistake it for a programmer fault; it carries the first
    non-blank address and the byte value read there, the same evidence the
    firmware's own `MSG_ERR_NOT_BLANK` has always carried; and it carries
    NO remedy clause -- no mention of `-b`/`--no-blank-check`. An operator
    who reads the way out at the refusal site can route around a correct
    refusal.
    """
    return _REFUSAL_FORMAT.format(
        chip_name=chip_name.upper(), address=address, value=value
    )


_NEGATIVE_ADDRESS_REFUSAL_FORMAT = (
    "{chip_name}: refused -- the start address {address_str!r} is negative."
)


def require_non_negative_address(chip_name: str, address_str: str | None) -> None:
    """The fail-closed, pre-connect guard against a signed write start
    address (folded todo `2026-09-16-reject-negative-write-start-address.md`,
    host half only).

    Both this guard's own region and `--verify`'s region are derived from
    the same `address_str` this write uses, and a negative start would make
    the host compute a region it never actually wrote or read: the
    firmware's own JSON parser (`simple_strtoul`) consumes only `[0-9]`, so a
    leading `-` makes the parse loop never run and the wire address silently
    becomes 0 -- turning a latent wrong-destination defect into a
    wrong-evidence one, because the host would then compare bytes it read
    from `[0, ...)` against a region it labels `[address_str, ...)`. Lives in
    this module, rather than `page_size_gate.py`, precisely because that
    module's `require_page_alignment` early-returns for every non-0x05 part
    (`page_size_gate.py:155-158`) and would miss every family this guard
    protects.

    Raises `NegativeStartAddressError` for a start address that parses as
    negative -- decimal or hex, `-256` and `-0x100` alike. Returns `None`
    for a `None` address, a non-negative address, and -- deliberately -- an
    address that fails to parse at all: a malformed address stays the
    existing handlers' job (`EpromOperator._setup_operation`'s own
    `parse_address`/`ValueError` handling, `page_size_gate`'s own parse
    handler), and this gate must not change that established error
    contract.

    Unlike `refusal_text` above, this refusal MAY carry a cause clause: it
    is an input-validation refusal, not a safety refusal an operator could
    route around by omitting evidence, so D-10's no-remedy rule does not
    apply here. The two refusal styles living in one module are a deliberate
    difference in kind, not an inconsistency.
    """
    try:
        address = parse_address(address_str)
    except ValueError:
        return
    if address is not None and address < 0:
        raise NegativeStartAddressError(
            _NEGATIVE_ADDRESS_REFUSAL_FORMAT.format(
                chip_name=chip_name.upper(), address_str=address_str
            )
        )
