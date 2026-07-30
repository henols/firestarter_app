"""Deliberately-violating fixture for tools/check_sdp_capability_invariants.py.

This file must never be imported. It exists only as AST-scan input for
GATE-01's Class 2 (widenable allow-set, D-14) planted-violation pytest leg
(see tests/test_check_sdp_capability.py).

It binds `SDP_CAPABLE_TOKENS` from a `frozenset(...)` call whose single
argument is a generator expression over a runtime call -- not a set/list/
tuple display of string literals -- so the allow-set could be silently
widened by whatever `_load_tokens_from_somewhere()` returns at import time
(Class 2b). It then rebinds the same name via an augmented union assignment
(Class 2c), so both halves of Class 2 are exercised by this one fixture.

Never imports anything from the `firestarter` package -- it is scannable
standalone, exactly like the real `firestarter/sdp_capability.py` target.
"""


def _load_tokens_from_somewhere():
    return ["RUNTIME_TOKEN_A", "RUNTIME_TOKEN_B"]


# Class 2b: not a frozenset(...) of string literals -- a generator expression
# over a runtime call. A future edit here could widen the allow-set without
# ever touching a reviewable string literal.
SDP_CAPABLE_TOKENS = frozenset(token for token in _load_tokens_from_somewhere())

# Class 2c: augmented union rebind -- the allow-set is mutated in place after
# its initial binding, defeating "assigned exactly once".
SDP_CAPABLE_TOKENS |= frozenset({"EXTRA_WIDENED_TOKEN"})
