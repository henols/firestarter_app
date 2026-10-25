"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Single chokepoint between CLI dispatch and the EPROM database lookup/conversion.
"""

from typing import Any

from firestarter.database import EpromDatabase
from firestarter.exceptions import (
    AmbiguousChipNameError,
    ChipNotFoundError,
    ChipNotImplementedError,
)

_NON_ZERO_WIRE_FIELDS = ("memory-size", "pin-count", "vpp_mv")


def _qualified_choices(name: str, db: EpromDatabase) -> list[str]:
    """The row `name` selects first, then each other row it selects that
    programs differently, all as qualified names. Empty when one row, or rows
    that program the same, are selected."""
    alternatives = db.ambiguous_alternatives(name)
    if not alternatives:
        return []
    raw_config, manufacturer = db.get_eprom_config(name)
    return [db.qualified_chip_name(name, raw_config, manufacturer), *alternatives]


def refuse_if_ambiguous(name: str, db: EpromDatabase) -> None:
    """Refuse a name that selects chips that program differently: guessing
    one would drive the wrong VPP, algorithm or timing."""
    choices = _qualified_choices(name, db)
    if choices:
        listed = ", ".join(f"'{c}'" for c in choices)
        raise AmbiguousChipNameError(
            f"'{name}' names chips that program differently. Name one: {listed}."
        )


def describe_ambiguity(name: str, db: EpromDatabase) -> str | None:
    """For display commands, which use the first row: the other rows `name`
    selects, or None."""
    choices = _qualified_choices(name, db)
    if not choices:
        return None
    others = ", ".join(f"'{c}'" for c in choices[1:])
    return (
        f"'{name}' also names chips that program differently: {others}. "
        f"This shows '{choices[0]}'."
    )


def resolve_chip(name: str, db: EpromDatabase | None = None) -> dict[str, Any]:
    """Resolve a chip name to its programmer-config dict.

    Looks ``name`` up in the EPROM database and converts the full record to the
    concise programmer format (the dict the firmware command builders consume).

    Raises ``ChipNotFoundError`` when the name resolves to no chip — both a
    missing record (``get_eprom_config`` returns ``(None, None)``) and an empty
    conversion (``convert_to_programmer`` returns ``{}``) are treated as the
    not-found condition.

    Raises ``AmbiguousChipNameError`` (a ``ChipNotFoundError``) when the name
    selects rows that program differently; the message names each of them.

    Raises ``ChipNotImplementedError`` when the chip exists in the database but
    its ``support_status`` is not ``"supported"`` (covers protocol-not-implemented,
    adapter-required, and vpp-exceeds-max).  This guard fires BEFORE any wire dict
    is built or serial byte emitted — the host will not drive hardware for a
    non-supported chip.  The ``info``/``list``/``id`` display
    paths bypass ``resolve_chip`` entirely and are unaffected.

    The ``db`` parameter is a dependency-injection seam: tests pass
    ``EpromDatabase(skip_local_override=True)`` so no ``~/.firestarter`` overrides
    or serial I/O are involved. In production ``db`` is ``None`` and a default
    ``EpromDatabase()`` is constructed, which DOES honor ``~/.firestarter``
    overrides (bench/prod parity).
    """
    if db is None:
        db = EpromDatabase()

    # Read the raw config to access support_status (not carried through _map_data).
    raw_config, _manufacturer = db.get_eprom_config(name)

    # not-found takes priority over the support_status guard: an absent chip cannot
    # have a support_status, so raise ChipNotFoundError immediately.
    if raw_config is None:
        raise ChipNotFoundError(name)
    refuse_if_ambiguous(name, db)

    # Support-status guard: refuse every program-capable operation
    # for non-supported chips BEFORE convert_to_programmer builds any wire dict.
    # Driven by support_status, not the incidental electrical.type string.
    support_status = raw_config.get("support_status", "supported")
    if support_status != "supported":
        reason = raw_config.get("unsupported_reason", "unsupported on this hardware")
        raise ChipNotImplementedError(f"{name}: {reason}")

    # Algorithm-presence guard: mirrors the firmware's
    # fail-closed protocol==0 -> 0xBB dispatch refusal. A chip entry
    # (built-in or user-override) whose programming.algorithm is absent or 0 has
    # no usable protocol to dispatch on and must be refused here, BEFORE
    # convert_to_programmer builds any wire dict or a serial byte is sent. A
    # non-zero-but-unknown algorithm is deliberately NOT refused here —
    # it falls through to the firmware's own fail-closed dispatch.
    algorithm = raw_config.get("programming", {}).get("algorithm", 0)
    if not algorithm:
        raise ChipNotImplementedError(
            f"{name}: no usable programming algorithm (protocol) defined — "
            "a chip entry must specify a non-zero 'algorithm' to be programmed"
        )

    full = db.get_eprom(name)
    data = db.convert_to_programmer(full) if full else None
    if not data:
        raise ChipNotFoundError(name)

    # The firmware refuses a memory command whose memory-size, pin-count or
    # vpp_mv is 0, with the same 'Bad JSON' answer that old firmware gives.
    # No shipped row has a 0 here; a user database override can.
    zero_fields = [key for key in _NON_ZERO_WIRE_FIELDS if not data.get(key)]
    if zero_fields:
        raise ChipNotImplementedError(
            f"{name}: the chip entry gives 0 for {', '.join(zero_fields)}. "
            "The programmer cannot use a value of 0 there."
        )

    # A pin map with fewer address lines than the chip's size needs would
    # repeat one window of the chip across the whole operation, silently.
    bus = data.get("bus-config", {}).get("bus")
    if bus and data["memory-size"] > 1 << len(bus):
        raise ChipNotImplementedError(
            f"{name}: its pin map has {len(bus)} address lines, which reach "
            f"{1 << len(bus)} of its {data['memory-size']} bytes. The programmer "
            "cannot address the rest of the chip."
        )
    return data
