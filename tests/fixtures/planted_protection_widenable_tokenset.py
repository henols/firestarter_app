"""Deliberately-violating fixture for
tools/check_protection_readability_invariants.py.

This file must never be imported. It exists only as AST-scan input for
GATE-02's Class 2 (widenable token sets) planted-violation pytest leg (see
tests/test_check_protection_readability.py).

It plants Class 2(b) on `DOCUMENTED_READABLE_TOKENS` -- bound from a
`frozenset(...)` call whose single argument is a generator expression over
a runtime call, not a set/list/tuple display of string literals -- so that
token set could be silently widened by whatever
`_load_tokens_from_somewhere()` returns at import time. It then plants
Class 2(c) on `DOCUMENTED_NOT_READABLE_TOKENS` -- bound cleanly to a
literal frozenset, then widened with an augmented union assignment -- so
each gated name carries its own violation and the parameterisation itself
is exercised, not only the first name.

The classifier function's returns are all refusal-shaped and properly
dominated, and there is no bare `except:`, so Class 1 does NOT fire. The
reporting axes (MECHANISM_BY_TOKEN, PERMANENCE_BY_TOKEN,
AMBIGUOUS_DOC_CITATIONS) are all present and well-formed literals, so
Classes 3 and 4 do NOT fire either.

Never imports anything from the `firestarter` package -- it is scannable
standalone, exactly like the real `firestarter/protection_readability.py`
target.
"""


def _load_tokens_from_somewhere():
    return ["RUNTIME_TOKEN_A", "RUNTIME_TOKEN_B"]


# Class 2b on DOCUMENTED_READABLE_TOKENS: not a frozenset(...) of string
# literals -- a generator expression over a runtime call. A future edit
# here could widen the readable set without ever touching a reviewable
# string literal.
DOCUMENTED_READABLE_TOKENS = frozenset(token for token in _load_tokens_from_somewhere())

# Clean literal binding -- Class 2b does NOT fire on this name.
DOCUMENTED_NOT_READABLE_TOKENS = frozenset({"W29C020"})

# Class 2c on DOCUMENTED_NOT_READABLE_TOKENS: augmented union rebind --
# the token set is mutated in place after its initial clean binding,
# defeating "assigned exactly once".
DOCUMENTED_NOT_READABLE_TOKENS |= frozenset({"EXTRA_WIDENED_TOKEN"})

MECHANISM_BY_TOKEN = {
    "W29C020": "boot_block_lockout",
}

PERMANENCE_BY_TOKEN = {
    "W29C020": "permanent",
}

AMBIGUOUS_DOC_CITATIONS = {
    "W29C020": "planted fixture citation, never a real C-17 record",
}


def readability_for_token(token):
    """Refusal-shaped and properly dominated -- no bare except, no
    unconditional permit -- so Class 1 does not fire on this fixture."""
    if token in DOCUMENTED_READABLE_TOKENS:
        return "documented-readable"
    if token in DOCUMENTED_NOT_READABLE_TOKENS:
        return "documented-not-readable"
    return "undocumented"
