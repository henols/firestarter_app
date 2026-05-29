"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Single chokepoint between CLI dispatch and the EPROM database lookup/conversion.
"""

from firestarter.database import EpromDatabase
from firestarter.exceptions import ChipNotFoundError


def resolve_chip(name: str, db: EpromDatabase | None = None) -> dict:
    """Resolve a chip name to its programmer-config dict.

    Looks ``name`` up in the EPROM database and converts the full record to the
    concise programmer format (the dict the firmware command builders consume).
    Raises ``ChipNotFoundError`` when the name resolves to no chip — both a
    missing record (``get_eprom`` returns ``None``) and an empty conversion
    (``convert_to_programmer`` returns ``{}``) are treated as the not-found
    condition.

    The ``db`` parameter is a dependency-injection seam: tests pass
    ``EpromDatabase(skip_local_override=True)`` so no ``~/.firestarter`` overrides
    or serial I/O are involved. In production ``db`` is ``None`` and a default
    ``EpromDatabase()`` is constructed, which DOES honor ``~/.firestarter``
    overrides (bench/prod parity).
    """
    if db is None:
        db = EpromDatabase()
    full = db.get_eprom(name)
    data = db.convert_to_programmer(full) if full else None
    if not data:
        raise ChipNotFoundError(name)
    return data
