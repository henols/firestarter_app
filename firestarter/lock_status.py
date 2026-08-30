"""The response-consuming half of the protection-readability split.

`protection_readability.py` is the PURE half: it decides from the chip database
alone whether a silicon read is even permitted, and its signature accepts no
device response, so it is structurally incapable of returning a real state. An
AST gate asserts the literals "protected"/"unprotected" never appear there.

THIS module is the only place permitted to turn an actual device response into
one of the eight class tokens.

Import purity: no `click` anywhere -- the caller performs the echo, so a click
dependency would make this unusable from a report layer with no CLI context.
No I/O either: it classifies a payload it is handed and imports neither
serial_comm nor eprom_operations.
"""

from __future__ import annotations

from typing import Mapping  # noqa: UP035

from firestarter.protection_readability import GATE_TOKEN_READ_PERMITTED
from firestarter.sdp_honesty import unreadable_state_caveat

# ---------------------------------------------------------------------------
# The eight class tokens, frozen. `GATE_TOKEN_READ_PERMITTED`
# ("read_permitted") is a **gate** token from `protection_readability.py` --
# it means "proceed to the silicon read" and is never printed to a user --
# so it is deliberately absent from this tuple. The other four gate tokens
# that module can return (`no_mechanism`, `not_implemented`, `not_readable`,
# `undocumented_alias`) share their literal string values with four of the
# eight tokens below by design: a refusal decided before any silicon read
# passes straight through `classify_protection_response` unchanged.
PROTECTION_CLASSES: tuple[str, ...] = (
    "protected",
    "unprotected",
    "not_readable",
    "not_implemented",
    "undocumented_alias",
    "no_mechanism",
    "firmware_outdated",
    "unadjudicated_probe",
)

# The two tokens producible ONLY from a device response -- never from
# `protection_readability.py`, whose signature accepts none. Plan 151-09's
# AST gate asserts these two literals never appear as a return value in
# that module; this module (`lock_status.py`) is the only place they may be
# produced, and `classify_protection_response` below is the only function
# in it permitted to return either one.
SILICON_ONLY_TOKENS: frozenset[str] = frozenset({"protected", "unprotected"})

# The three firmware decode codes from `151-DESIGN.md` §1. Byte 1 of the
# two-byte `MSG_DATA_PROTECTION_STATUS` payload. `0xFF` reuses
# `hw_get_version`'s established sentinel convention
# (`firestarter/src/hardware_operations.cpp:105-114`, "no override active")
# for "indeterminate/not-obtainable" -- it is not a third state of its own,
# just firmware's way of saying the decode did not resolve.
DECODE_UNPROTECTED = 0x00
DECODE_PROTECTED = 0x01
DECODE_INDETERMINATE = 0xFF

# The operational-failure class also covers a truncated or missing payload
# (see `classify_protection_response` step 3 below) -- this constant names
# it once so both use sites (the unknown-command mapping and the
# truncated-payload guard) stay in sync with the same token.
_CLASS_FIRMWARE_OUTDATED = "firmware_outdated"
_CLASS_UNADJUDICATED_PROBE = "unadjudicated_probe"
_CLASS_NOT_READABLE = "not_readable"

# ---------------------------------------------------------------------------
# The exit-code map: a literal str -> int dict, NEVER a max() over severities.
# This codebase already carries an exit-code precedence defect where max()
# picked the wrong verdict, so every token's code is visible on its own line
# rather than derived from a comparison that can silently invert.
#
# Four codes over eight tokens:
#   0 -- a REAL silicon read: protected or unprotected. $? == 0 means "I hold
#        a real state", nothing weaker.
#   2 -- cannot answer: an honest refusal, not a state and not a failure.
#   3 -- operational failure: the command was never answered at all.
#   4 -- unadjudicated_probe, its own band. Not a state (a forced read is never
#        a state claim), not a refusal (the sequence ran), not a failure (the
#        command answered). Reachable only via an explicit --force.
EXIT_BY_CLASS: Mapping[str, int] = {
    "protected": 0,
    "unprotected": 0,
    "not_readable": 2,
    "not_implemented": 2,
    "undocumented_alias": 2,
    "no_mechanism": 2,
    "firmware_outdated": 3,
    "unadjudicated_probe": 4,
}


def exit_code_for_class(class_token: str) -> int:
    """Look up `class_token`'s exit code. Raises `KeyError` on an unknown
    token -- never `.get(token, 1)`. A silent default here would let a new,
    unclassed token silently exit non-zero-but-wrong instead of failing
    loudly the moment it is introduced.
    """
    return EXIT_BY_CLASS[class_token]


def classify_protection_response(
    gate_token: str, payload: bytes | None, *, forced: bool
) -> tuple[str, str]:
    """The only function permitted to return "protected" or "unprotected".

    Guard cascade, one early return per outcome. ORDER MATTERS -- the
    forced-probe check runs BEFORE the payload is consulted, so a forced read
    can never become a state claim even if the payload looks like a definite
    decode:

    1. forced, and the gate did not permit the read -> `unadjudicated_probe`.
       --force runs the sequence past a table refusal, but the result is never
       a state claim regardless of what the decode byte says.
    2. gate did not permit (not forced) -> return the gate token unchanged and
       do not touch `payload` at all.
    3. payload absent or shorter than 2 bytes -> `firmware_outdated`, an
       operational-failure token, NOT a state token. A truncated frame and a
       dead port are indistinguishable to a script reading $?.
    4. the decode byte -> "unprotected" / "protected". The only two branches
       that can produce a silicon-only token.
    5. anything else, including an indeterminate decode -> `not_readable`,
       naming the raw byte in the reason. Never coerced into a guess.
    """
    if forced and gate_token != GATE_TOKEN_READ_PERMITTED:
        return _CLASS_UNADJUDICATED_PROBE, (
            f"--force ran the read past the table's {gate_token!r} refusal; "
            "the result is an unadjudicated probe, never a state claim."
        )

    if gate_token != GATE_TOKEN_READ_PERMITTED:
        return gate_token, (
            f"the readability table resolved this part to {gate_token!r} "
            "before any silicon read was attempted; the payload, if any, "
            "was not consulted."
        )

    if payload is None or len(payload) < 2:
        return _CLASS_FIRMWARE_OUTDATED, (
            "no usable two-byte payload was returned for this read -- a "
            "truncated frame and a dead port are indistinguishable here, "
            "so this is reported as an operational failure, never a state."
        )

    raw_byte, decode_byte = payload[0], payload[1]

    if decode_byte == DECODE_UNPROTECTED:
        return "unprotected", (
            f"raw byte 0x{raw_byte:02X} decoded as unprotected "
            f"(decode byte 0x{decode_byte:02X})."
        )
    if decode_byte == DECODE_PROTECTED:
        return "protected", (
            f"raw byte 0x{raw_byte:02X} decoded as protected "
            f"(decode byte 0x{decode_byte:02X})."
        )

    return _CLASS_NOT_READABLE, (
        f"the read completed and returned raw byte 0x{raw_byte:02X}, but "
        f"decode byte 0x{decode_byte:02X} is not a value this codebase "
        "recognises as a definite state; never coerced into a guess."
    )


def render_lock_status(class_token: str, reason: str, raw_byte: int | None) -> str:
    """The rendering surface: the class token first, then the prose.

    The class token is always the string's first whitespace-delimited
    field, so a caller or a test can assert on the token alone without
    matching wording. For the `not_readable` class, the caveat is composed
    by CALLING `sdp_honesty.unreadable_state_caveat()` -- never re-authored
    or re-worded, so the sentence stays byte-identical everywhere it is
    used. `raw_byte`, when not `None`, is always rendered in hex, including
    on the not-readable-because-unrecognised path: the probe depends on
    the raw observation surviving even when the decode did not resolve.
    """
    body = reason
    if class_token == _CLASS_NOT_READABLE:
        body = f"{reason} {unreadable_state_caveat()}"

    rendered = f"{class_token} {body}"
    if raw_byte is not None:
        rendered = f"{rendered} (raw byte: 0x{raw_byte:02X})"
    return rendered
