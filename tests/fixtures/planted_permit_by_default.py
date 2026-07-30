"""Deliberately-violating fixture for tools/check_sdp_capability_invariants.py.

This file must never be imported. It exists only as AST-scan input for
GATE-01's Class 1 (permit-by-default, D-14) planted-violation pytest leg
(tests/test_check_check_sdp_capability... see tests/test_check_sdp_capability.py).

It plants a capability-predicate-shaped function named
`sdp_capability_for_entry`, preserving the real module's `(allowed, reason)`
tuple return shape, whose sole return is `(True, ...)` with NO membership
test against `SDP_CAPABLE_TOKENS` lexically dominating it -- the permit is
the unconditional default path (Class 1a). It also wraps predicate work in a
bare exception handler (Class 1b) -- a bare `except:` here would silently
swallow a refusal-shaped error into the same unconditional permit.

Never imports anything from the `firestarter` package -- it is scannable
standalone, exactly like the real `firestarter/sdp_capability.py` target.
"""

SDP_CAPABLE_TOKENS = frozenset({"AT28C256"})


def sdp_capability_for_entry(entry, display_name):
    """Permit-by-default predicate (D-14 Class 1): the tuple return below is
    unconditional -- no membership test against `SDP_CAPABLE_TOKENS` appears
    anywhere earlier in this function body to dominate it."""
    try:
        name = entry.get("name", display_name)
    except:  # noqa: E722 -- planted bare except (Class 1b), never fixed here
        name = display_name
    return True, f"{name.upper()}: permitted unconditionally"
