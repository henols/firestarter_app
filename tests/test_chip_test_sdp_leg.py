"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

SDP-leg unit for `firestarter/chip_test.py` (v1.30 Phase 133, LEG-09/10/11).

This module is the phase's evidence-capture step: it carries the two
pre-edit baselines that ROADMAP criterion 4 ("the seven shipped ops are
behaviourally unchanged") is provable against, plus the operator-double
harness later plans (133-02..133-07) extend. It is host-and-engine-local
(no serial I/O, no hardware) and carries **no** skip marker and **no**
firmware-sibling-conditional decorator (unlike this phase's parity-test
sibling module, which does carry one against its own firmware-sourced
comparison) -- everything here runs against the pure `chip_test.py`
compute layer and the real, committed `chip_database.json`, so it must run
in standalone CI. It also adds **no** syrupy fixture capture of its own:
that plugin (version 5.5.3) fails the whole session on an unused capture,
and Phase 132 D-13 already documented that trap for a module with no live
use of it.

Test taxonomy:

  Operator-double harness (copied from tests/test_chip_test.py, D-15)
    _REAL_DB, _OPERATOR_METHODS, _mock_operator, _plan_with_steps, _result
      -- the SDP extension: `_OPERATOR_METHODS` adds "sdp_lock" and
      "sdp_unlock" to the six-name allow-list, so a later plan's dispatch
      test does not AttributeError against the Mock(spec=[...]) double.

  Shipped-ops before-image (criterion 4's first baseline, D-13a)
    test_shipped_ops_sequence_unchanged -> the exact derived op-string
      sequence and per-step (verdict, run_count) list for "M8720" at this
      commit, asserted against an in-test literal (_SHIPPED_OPS_SEQUENCE)
      measured by actually running derive_plan + run_plan, never predicted.

  (Plan 133-01 Task 2 appends the exception-precedence before-image below
  this taxonomy note, in a separate commit.)

References:
  - .planning/phases/133-sdp-leg-mechanism/133-01-PLAN.md
  - .planning/phases/133-sdp-leg-mechanism/133-CONTEXT.md D-13 (no-op
    regression test), D-15 (this module)
  - .planning/phases/133-sdp-leg-mechanism/133-PATTERNS.md
    §tests/test_chip_test_sdp_leg.py
  - tests/test_chip_test.py :287, :793-825 (the harness this module copies)
"""

from unittest.mock import Mock

from firestarter.chip_test import (
    Plan,
    derive_plan,
    run_plan,
)
from firestarter.database import EpromDatabase

# ---------------------------------------------------------------------------
# Operator-double harness -- copied verbatim from tests/test_chip_test.py
# (:287, :793-825), NOT imported (these names are module-private in a
# 1958-line module). "M8720" is the house chip (protocol 0x08, EEPROM,
# chip-id sentinel 0, resolves for every step against _REAL_DB) -- reused
# here rather than picking a new one.
# ---------------------------------------------------------------------------

_REAL_DB = EpromDatabase(skip_local_override=True)

# The one mandatory change vs tests/test_chip_test.py's allow-list: adding
# "sdp_lock" and "sdp_unlock" here is inert at this commit (nothing in
# chip_test.py calls them yet), and is why THIS task -- not a later one --
# owns the harness. tests/test_chip_test.py's own six-name Mock(spec=[...])
# list omits both, so without this extension every later plan's dispatch
# test would raise AttributeError against the double instead of exercising
# the new arm (133-CONTEXT.md D-15's forward note; key_links in
# 133-01-PLAN.md).
_OPERATOR_METHODS = [
    "check_eprom_id",
    "read_eprom",
    "check_eprom_blank",
    "write_eprom",
    "verify_eprom",
    "erase_eprom",
    "sdp_lock",
    "sdp_unlock",
]


def _mock_operator(**returns):
    op = Mock(spec=_OPERATOR_METHODS)
    op.check_eprom_id.return_value = (True, 0x1234)
    op.read_eprom.return_value = True
    op.check_eprom_blank.return_value = True
    op.write_eprom.return_value = True
    op.verify_eprom.return_value = True
    op.erase_eprom.return_value = True
    for name, value in returns.items():
        getattr(op, name).return_value = value
        getattr(op, name).side_effect = None
    return op


def _plan_with_steps(*steps):
    return Plan(name="M8720", steps=list(steps))


def _result(results, op):
    for r in results:
        if r.op == op:
            return r
    raise AssertionError(f"no result for op {op!r} in {[r.op for r in results]}")


# ---------------------------------------------------------------------------
# Baseline 1: the shipped op-string sequence + per-step (verdict, run_count)
# (criterion 4, D-13a). Measured by actually running derive_plan("M8720",
# _REAL_DB) (default write_scope="none") and run_plan(...) against a fresh
# _mock_operator() at this commit -- transcribed from that run, never
# predicted. write_scope="none" structurally omits write/verify/erase from
# Plan.steps (D-01, SAFE-01), so this literal covers only {id, read,
# blank-check}; D-13b's sentinel test (plan 133-03) is what covers the
# remaining shipped op strings via the fail-closed dispatch-arm proof, not
# this literal.
# ---------------------------------------------------------------------------

_SHIPPED_OPS_SEQUENCE = {
    "op_sequence": ["id", "read", "blank-check"],
    # (verdict, run_count) per step, same order as op_sequence above.
    # "id" is NA/run_count=0: M8720's chip-id sentinel is 0 (no real id in
    # the DB entry), so derive_plan marks the id step unsupported.
    "verdict_run_count": [("NA", 0), ("OK", 2), ("OK", 1)],
    "len_results": 3,
}


def test_shipped_ops_sequence_unchanged():
    """Criterion 4's before-image: the engine's behaviour for a
    write_scope="none" run on "M8720" is frozen against a literal measured
    at this commit, BEFORE chip_test.py is touched by any later plan in
    this phase.

    If this fails, a shipped op's behaviour changed: either the derived op
    sequence itself moved (derive_plan), or a step's verdict/run_count
    changed (run_plan / _dispatch_step), or a step was silently added or
    removed (len(results) -- criterion (c) below, not redundant with (a)/
    (b): a silently *added* step would still pass a prefix comparison of
    the first three).
    """
    plan = derive_plan("M8720", _REAL_DB)
    operator = _mock_operator()
    results = run_plan(plan, operator, _REAL_DB)

    op_sequence = [r.op for r in results]
    assert op_sequence == _SHIPPED_OPS_SEQUENCE["op_sequence"], (
        f"derive_plan('M8720', _REAL_DB)'s derived op sequence changed: "
        f"measured {op_sequence!r}, frozen baseline "
        f"{_SHIPPED_OPS_SEQUENCE['op_sequence']!r} (133-01 before-image, "
        "criterion 4)"
    )

    verdict_run_count = [(r.verdict, r.run_count) for r in results]
    assert verdict_run_count == _SHIPPED_OPS_SEQUENCE["verdict_run_count"], (
        f"run_plan's per-step (verdict, run_count) for 'M8720' changed: "
        f"measured {verdict_run_count!r}, frozen baseline "
        f"{_SHIPPED_OPS_SEQUENCE['verdict_run_count']!r} (133-01 "
        "before-image, criterion 4)"
    )

    assert len(results) == _SHIPPED_OPS_SEQUENCE["len_results"], (
        f"run_plan returned {len(results)} step results for 'M8720', "
        f"expected {_SHIPPED_OPS_SEQUENCE['len_results']} -- a step was "
        "silently added or removed (133-01 before-image, criterion 4)"
    )
