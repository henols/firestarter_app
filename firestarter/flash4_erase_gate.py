"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Flash4 (protocol 0x05) erase-refusal policy.

The 0x05 firmware path does not implement a chip erase for the flash4
protocol class -- a real hardware limitation, not a database misclassification
-- so `firestarter erase` on one of these parts has never had anything to do.
Before this module, the operator only discovered that after `Connecting...
OK`, which reads like a malfunction rather than a correct refusal. This
module is the pre-connect policy: which parts fall in that class, and the
one-line refusal text for them.

Like `jp5_gate.py` and `sdp_capability.py`, this is a pure predicate: no I/O,
no environment reads, no serial access -- a wire dict is the whole input,
which is what keeps the policy testable without a board and keeps
`eprom_operations.py` and `cli_handlers.py` free of the reasoning.

The one deliberate deviation from both of those precedents: this gate FAILS
OPEN. `jp5_gate.require_acknowledged` and `sdp_capability` both refuse when
their input cannot prove a part is safe, because guessing wrong there risks
hardware damage or a corrupted write. Here, guessing wrong in the same
direction -- refusing a part this predicate cannot classify -- would instead
break `erase` for every chip whose wire dict happens to lack an `algorithm`
key, which is an availability regression, not a safety one. Absence of
evidence is treated as "not flash4" rather than as "not provably safe".

`FLASH4_PROTOCOL_ID` is the same `algorithm` value
`database.convert_to_programmer`'s `algo not in (5,)` exclusion reads when it
clears `FLAG_CAN_ERASE`, so this predicate and that flag derivation share one
source of truth and cannot drift apart.
"""

from __future__ import annotations

from typing import Any, Mapping  # noqa: UP035

FLASH4_PROTOCOL_ID = 5

_REFUSAL_FORMAT = "Erase not supported for {chip_name}"


def is_flash4(programmer_data: Mapping[str, Any] | None) -> bool:
    """True when the wire dict's `algorithm` value is the flash4 protocol id.

    The comparison is against `algorithm`, not `flags & FLAG_CAN_ERASE`. The
    flag is also clear for UV-EPROM and SRAM parts that have no erase
    command of any kind, so a flag-based predicate would silently widen this
    refusal past its flash4-only scope -- `algorithm` is the value that
    identifies the protocol class itself, and nothing else does.

    Returns False for a missing, `None`, or empty `programmer_data`, and for
    a dict carrying no `algorithm` key at all. This is deliberately the
    OPPOSITE polarity to `jp5_gate.is_affected` and `sdp_capability`, which
    both fail closed on absent evidence. Here, absent evidence means "this
    predicate cannot prove the part is flash4", and refusing every
    unprovable part would break `erase` for every chip this predicate has
    not been shown, not just flash4 parts.
    """
    if not programmer_data:
        return False
    return programmer_data.get("algorithm") == FLASH4_PROTOCOL_ID


def refusal_text(chip_name: str) -> str:
    """The one-line, cause-free refusal text.

    Built from `_REFUSAL_FORMAT` rather than assembled inline, so a test can
    assert the exact shape instead of a whole sentence. Carries no cause
    clause, no alternative command, and no mention of the `--force`
    forged-identity workaround -- an operator who reads a cause here could
    route around a correct refusal onto the wrong chip's identity, which is
    the exact harm this module exists to avoid. That statement belongs in
    the reply to the issue reporter, not in the tool's output.
    """
    return _REFUSAL_FORMAT.format(chip_name=chip_name.upper())
