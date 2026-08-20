"""Deliberately-violating fixture for
tools/check_protection_readability_invariants.py.

This file must never be imported. It exists only as AST-scan input for
GATE-02's Class 1 (permit-by-default) planted-violation pytest leg (see
tests/test_check_protection_readability.py), and it doubles as plan
151-12's required D-12 leg 4 fixture: a real proof that a return of a
silicon-only class token from the pure path is caught.

It plants a classifier-shaped function named `protection_gate_for_entry`,
preserving the real module's `(class_token, reason)` tuple return shape,
whose sole return is `("unprotected", ...)` -- one of the two class tokens
that must never be returned from the pure module -- with NO membership
test against either gated token-set name earlier in the body (the permit
is the unconditional default path, Class 1a). It also wraps predicate work
in a bare exception handler (Class 1b) -- a bare `except:` here would
silently swallow a refusal-shaped error into the same unconditional permit.

Both gated token-set names are bound cleanly (a one-element literal
frozenset each) so Class 2 does NOT fire, and the reporting axes
(MECHANISM_BY_TOKEN, PERMANENCE_BY_TOKEN, AMBIGUOUS_DOC_CITATIONS) are all
present and well-formed so Classes 3 and 4 do NOT fire either -- the whole
point is that exactly one class is planted here, so a test failure names
the planted cause rather than a side effect.

Never imports anything from the `firestarter` package -- it is scannable
standalone, exactly like the real `firestarter/protection_readability.py`
target.
"""

DOCUMENTED_READABLE_TOKENS = frozenset({"W29C020C"})
DOCUMENTED_NOT_READABLE_TOKENS = frozenset({"W29C020"})

MECHANISM_BY_TOKEN = {
    "W29C020C": "boot_block_lockout",
    "W29C020": "boot_block_lockout",
}

PERMANENCE_BY_TOKEN = {
    "W29C020C": "permanent",
    "W29C020": "permanent",
}

AMBIGUOUS_DOC_CITATIONS = {
    "W29C020": "planted fixture citation, never a real C-17 record",
}


def protection_gate_for_entry(entry, display_name):
    """Permit-by-default predicate (Class 1): the tuple return below is
    unconditional -- no membership test against either gated token-set
    name appears anywhere earlier in this function body to dominate it,
    and its first element is a silicon-only class token that must never
    be returned from this pure module at all."""
    try:
        name = entry.get("name", display_name)
    except:  # noqa: E722 -- planted bare except (Class 1b), never fixed here
        name = display_name
    return "unprotected", f"{name.upper()}: permitted unconditionally"
