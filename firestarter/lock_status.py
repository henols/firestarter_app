"""The response-consuming half of the protection-readability split (LOCK-02,
LOCK-03, LOCK-04; D-04, D-08, D-09, D-10).

`firestarter/protection_readability.py` is the *pure*
half: `protection_gate_for_entry(entry, display_name) -> (gate_token,
reason)` decides, from the chip database alone, whether a silicon read is
even permitted -- and its signature accepts no device response, so it is
structurally incapable of returning a real state ("protected" /
"unprotected"). Plan 151-09's AST gate polices that module and asserts
those two literals never appear there in any quoting style.

THIS module is the other half: the only place in the codebase permitted to
turn an actual device response into one of D-09's eight class tokens.
`PATTERNS.md` records this split as having no analog anywhere else in the
codebase -- `sdp_capability.py` is pure-only and `sdp_honesty.py` is
prose-only, so no existing two-function pure/impure precedent exists here.

Import-purity invariant (mirrors `sdp_honesty.py:22-28`'s own invariant
comment): this module's top-level import set is a subset of
`{"__future__", "typing", "firestarter.exceptions", "firestarter.messages",
"firestarter.protection_readability", "firestarter.sdp_honesty"}`. In
particular there is deliberately no `click` anywhere in this set -- the
caller performs the echo, so a `click` dependency here would make this
module unusable from a future report layer that has no CLI context of its
own (the same reason `sdp_honesty.py` itself excludes `click`). This
module also performs no I/O of its own: it classifies a payload it is
handed, and never imports `firestarter.serial_comm` or
`firestarter.eprom_operations`.
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
# it once so both use sites (the D-04 unknown-command mapping and the
# truncated-payload guard) stay in sync with the same token.
_CLASS_FIRMWARE_OUTDATED = "firmware_outdated"
_CLASS_UNADJUDICATED_PROBE = "unadjudicated_probe"
_CLASS_NOT_READABLE = "not_readable"

# ---------------------------------------------------------------------------
# The exit-code map: a literal `str -> int` dict, never a `max()` over
# severities. This codebase already carries an exit-code precedence defect
# where `max()` picked the wrong verdict -- `dev test`'s exit precedence is
# `max(1, 2) == 2` (MARGINAL beats BAD is FALSE; a comment and a docstring
# both claim the opposite there) -- so this map is authored as an explicit
# per-token lookup instead, where every token's code is visible on its own
# line rather than derived from a comparison that can silently invert.
#
# Exactly four distinct codes are used, over all eight tokens:
#   0 -- a REAL silicon read: `protected` or `unprotected`, the only two
#        tokens `SILICON_ONLY_TOKENS` names. `$? == 0` therefore means
#        exactly "I hold a real state", nothing weaker.
#   2 -- "cannot answer": `not_readable`, `not_implemented`,
#        `undocumented_alias`, `no_mechanism` -- an honest refusal, not a
#        state and not an operational failure.
#   3 -- operational failure: `firmware_outdated` (the command was never
#        even answered -- either the firmware predates it, keyed on
#        `MSG_ERR_UNKNOWN_CMD`'s id, or the payload never arrived
#        intact at all; both are indistinguishable to a script and share
#        this one token and code).
#   4 -- `unadjudicated_probe` gets its own, distinct code. It belongs in
#        none of the other three bands: it is not a state (a
#        forced read is never a state claim), not a refusal (the sequence
#        actually ran), and not an operational failure (the command did
#        answer). A fourth band is the only honest reading, and it is
#        reachable only via an explicit `--force` past a table refusal.
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
    unclassed D-09 token silently exit non-zero-but-wrong instead of failing
    loudly the moment it is introduced.
    """
    return EXIT_BY_CLASS[class_token]


def classify_protection_response(
    gate_token: str, payload: bytes | None, *, forced: bool
) -> tuple[str, str]:
    """The only function in the codebase permitted to return
    `"protected"` or `"unprotected"`.

    Guard cascade, one early return per outcome -- order matters, and the
    forced-probe check runs BEFORE the payload is ever consulted, so a
    forced read can never become a state claim by accident even if a
    caller passes a payload that looks like a definite decode:

    1. `forced` and `gate_token != GATE_TOKEN_READ_PERMITTED` ->
       `unadjudicated_probe`. D-07: `--force` runs the sequence past a
       table refusal anyway, but the result is never a state claim,
       regardless of what the payload's decode byte says.
    2. `gate_token != GATE_TOKEN_READ_PERMITTED` (not forced) -> return
       `gate_token` itself, unchanged, and do not touch `payload` at all.
       The table already decided this part refuses before any silicon
       read was attempted.
    3. `payload` is `None` or shorter than 2 bytes -> `firmware_outdated`,
       the operational-failure token -- not a state token. This is the
       same class a dead port or a too-old-firmware unknown-command error
       maps to (see `sdp_honesty.map_unknown_cmd_to_outdated_for_operation`),
       so a truncated frame and a dead port are indistinguishable to a
       script reading `$?` -- both mean "the command was never actually
       answered".
    4. `payload[1] == DECODE_UNPROTECTED` -> `"unprotected"`;
       `payload[1] == DECODE_PROTECTED` -> `"protected"`. The only two
       branches that can produce a `SILICON_ONLY_TOKENS` member.
    5. Anything else, including `DECODE_INDETERMINATE` -> **not** a state
       token. Returns `not_readable`, naming the raw byte in the reason so
       D-03's probe can still record what silicon actually said even when
       firmware's own decode did not resolve. Never coerced into a guess.
    """
    if forced and gate_token != GATE_TOKEN_READ_PERMITTED:
        return _CLASS_UNADJUDICATED_PROBE, (
            f"--force ran the read past the table's {gate_token!r} refusal; "
            "the result is an unadjudicated probe, never a state claim "
            "(D-07)."
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
    """D-08's rendering surface: the class token first, then the prose.

    The class token is always the string's first whitespace-delimited
    field, so a caller or a test can assert on the token alone without
    matching wording. For the `not_readable` class, the caveat is composed
    by CALLING `sdp_honesty.unreadable_state_caveat()` -- never re-authored
    or re-worded, so the sentence stays byte-identical everywhere it is
    used. `raw_byte`, when not `None`, is always rendered in hex, including
    on the not-readable-because-unrecognised path: D-03's probe depends on
    the raw observation surviving even when the decode did not resolve.
    """
    body = reason
    if class_token == _CLASS_NOT_READABLE:
        body = f"{reason} {unreadable_state_caveat()}"

    rendered = f"{class_token} {body}"
    if raw_byte is not None:
        rendered = f"{rendered} (raw byte: 0x{raw_byte:02X})"
    return rendered
