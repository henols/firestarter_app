"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Every chip row either is refused by resolve_chip or encodes as a command
frame. A row that resolves but does not encode would fail with
CommandEncodeError at the start of every operation, while `info` shows it
as usable. The shield's CE/OE sentinels on an address line are the known
case, and resolve_chip refuses them.
"""

from __future__ import annotations

import pytest

from firestarter.chip_resolver import resolve_chip
from firestarter.command_codec import encode_command
from firestarter.constants import COMMAND_READ
from firestarter.exceptions import ChipNotImplementedError

from .plan_corpus import REAL_DB


def _qualified_names() -> list[str]:
    names = []
    for manufacturer, ics in REAL_DB.proms.items():
        for ic in ics:
            alias = ic.get("part_number", "").split(",")[0].strip()
            names.append(REAL_DB.qualified_chip_name(alias, ic, manufacturer))
    return names


@pytest.mark.parametrize("name", _qualified_names())
def test_a_resolvable_row_encodes(name: str) -> None:
    try:
        data = resolve_chip(name, db=REAL_DB)
    except ChipNotImplementedError:
        return
    encode_command({**data, "cmd": COMMAND_READ})
