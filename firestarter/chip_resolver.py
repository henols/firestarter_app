"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Single chokepoint between CLI dispatch and the EPROM database lookup/conversion.
"""

from typing import Any

from firestarter.database import EpromDatabase
from firestarter.exceptions import ChipNotFoundError, ChipNotImplementedError


def resolve_chip(name: str, db: EpromDatabase | None = None) -> dict[str, Any]:
    """Resolve a chip name to its programmer-config dict.

    Looks ``name`` up in the EPROM database and converts the full record to the
    concise programmer format (the dict the firmware command builders consume).

    Raises ``ChipNotFoundError`` when the name resolves to no chip — both a
    missing record (``get_eprom_config`` returns ``(None, None)``) and an empty
    conversion (``convert_to_programmer`` returns ``{}``) are treated as the
    not-found condition.

    Raises ``ChipNotImplementedError`` when the chip exists in the database but
    its ``support_status`` is not ``"supported"`` (covers protocol-not-implemented,
    adapter-required, and vpp-exceeds-max).  This guard fires BEFORE any wire dict
    is built or serial byte emitted — the host will not drive hardware for a
    non-supported chip (D-12 / T-66-01).  The ``info``/``list``/``id`` display
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

    # Support-status guard (D-12 / T-66-01): refuse every program-capable operation
    # for non-supported chips BEFORE convert_to_programmer builds any wire dict.
    # Driven by support_status, not the incidental electrical.type string.
    support_status = raw_config.get("support_status", "supported")
    if support_status != "supported":
        reason = raw_config.get("unsupported_reason", "unsupported on this hardware")
        raise ChipNotImplementedError(f"{name}: {reason}")

    full = db.get_eprom(name)
    data = db.convert_to_programmer(full) if full else None
    if not data:
        raise ChipNotFoundError(name)
    return data
