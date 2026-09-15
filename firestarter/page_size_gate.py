"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Flash4 (protocol 0x05) page-size write-refusal policy.

The 0x05 write path commits a whole physical page at a time. A wrong page
size is not a degraded mode in either direction: too small and a second
page-write cycle erases the first; too large and the device's own load
window auto-commits mid-load, past the point the firmware polls. Only an
exact, database-sourced page size is safe, and there is no fallback value
that is ever safe to guess.

Like `flash4_erase_gate.py` and `jp5_gate.py`, this is a pure predicate: no
I/O, no environment reads, no serial access -- an already-resolved wire dict
is the whole input, which is what lets both call sites run this before
anything touches hardware.

The polarity here is the deliberate OPPOSITE of `flash4_erase_gate.is_flash4`.
That gate fails open on absent evidence, because guessing wrong there merely
breaks availability. Here, guessing wrong risks silently destroying an
operator's chip, so absent or zero evidence about the page size is treated
as "not provably safe", never as "probably fine".

`FLASH4_PROTOCOL_ID` is imported from `flash4_erase_gate` rather than
re-declared, so the protocol-0x05 predicate stays single-sourced across both
gates.
"""

from __future__ import annotations

from typing import Any, Mapping  # noqa: UP035

from firestarter.constants import JSON_KEY_PAGE_SIZE
from firestarter.exceptions import PageSizeUnavailableError
from firestarter.flash4_erase_gate import FLASH4_PROTOCOL_ID

_WRITE_OPERATIONS = frozenset({"write"})

_REFUSAL_FORMAT = (
    "{chip_name}: no page size is recorded for this chip, and a protocol "
    "0x05 write is refused. A guessed page size can silently destroy data "
    "in either direction on this protocol."
)


def requires_page_size(programmer_data: Mapping[str, Any] | None) -> bool:
    """True when the wire dict's `algorithm` value is the flash4 protocol id.

    Keyed on `algorithm`, not on a capability flag: a flag-based predicate
    would also be clear for parts with no erase command at all, silently
    widening this refusal past the protocol-0x05 parts this phase examined.

    Returns False for a missing, `None`, or empty `programmer_data`.
    """
    if not programmer_data:
        return False
    return programmer_data.get("algorithm") == FLASH4_PROTOCOL_ID


def require_page_size(
    chip_name: str,
    programmer_data: Mapping[str, Any] | None,
    operation: str,
) -> None:
    """The fail-closed, pre-connect guard. Raises on refusal, returns on pass.

    Returns immediately when `operation` is not a write, and again when
    `programmer_data` does not describe a protocol 0x05 part. Otherwise
    raises `PageSizeUnavailableError` unless `programmer_data` carries a
    truthy page-size value under the wire key `constants.JSON_KEY_PAGE_SIZE`.

    This is the fail-CLOSED direction, the opposite of
    `flash4_erase_gate.is_flash4`'s documented fail-open polarity: absent
    evidence that a real page size exists means the part is not provably
    safe to write.
    """
    if operation not in _WRITE_OPERATIONS:
        return
    if not requires_page_size(programmer_data):
        return
    page_size = (programmer_data or {}).get(JSON_KEY_PAGE_SIZE)
    if not page_size:
        raise PageSizeUnavailableError(
            _REFUSAL_FORMAT.format(chip_name=chip_name.upper())
        )
