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

  Exception-precedence before-image (criterion 4's second baseline, D-08)
    test_exception_precedence_matrix -> all nine exception classes' live
      (escaped, verdict, error_code) triple, derived by injecting each
      exception into a real run_plan() call and reading what escapes/lands.
    test_precedence_matrix_delta_is_exactly_intended -> the gate: any row
      that changes between _PRE_EDIT_PRECEDENCE_MATRIX (frozen forever) and
      _EXPECTED_PRECEDENCE_MATRIX (edited by later plans) must be named in
      _INTENDED_PRECEDENCE_DELTA, or the suite goes RED.
    test_precedence_matrix_deriver_is_non_vacuous -> proves the delta gate
      is capable of failing (a real leg proves nothing until seen to fail).

  LEG-11's four behavioural proofs (plan 133-02, D-08)
    test_serial_timeout_degrades_one_step -> a SerialTimeoutError raised by
      a "read" step's operator method degrades THAT step to BAD; a later
      step still runs and is OK -- run_plan returned, it did not abort.
    test_hardware_error_degrades_one_step -> the same shape with
      HardwareOperationError, plus asserting error_code is None (the
      observable consequence of the new clause omitting that field).
    test_run_fatal_escapes -> ProgrammerNotFoundError and
      FirmwareOutdatedError each escape run_plan by object identity (not
      merely by class), and SerialError.__subclasses__() is pinned to the
      measured three-class census so a future fourth subclass fails loudly
      instead of silently bypassing the re-raise clause.
    test_assertion_error_propagates -> the deliberate AssertionError shape
      from _dispatch_multi_run's terminal refusal still escapes run_plan,
      proving no broad `except` was introduced (criterion 2).

  SDP dispatch arm (plan 133-03, D-01/D-04/D-05/D-11, LEG-09)
    test_unlock_exempt_from_destructive -> the standing _DESTRUCTIVE_OPS
      asymmetry: OP_SDP_LOCK is a member (a lock applied to a
      misidentified chip is the harm the gate exists to prevent),
      OP_SDP_UNLOCK is deliberately absent (an unlock the gate can skip
      ships a locked part) -- that asymmetry IS LEG-09. Also asserts
      _SDP_OPS is disjoint from _MULTI_RUN_OPS (D-03).
    test_dispatch_sdp_guard_refuses_foreign_op -> _dispatch_sdp's guard
      refuses each of the seven shipped op strings with a BAD/run_count=0
      refusal naming the op and _SDP_OPS, and touches the operator double
      NOT AT ALL (operator.method_calls == []) -- a refusal, not a
      mis-dispatch.
    test_dispatch_sdp_terminal_assertion_is_reachable_only_by_bypassing_the_guard
      -> with _SDP_OPS monkeypatched to admit a foreign op, _dispatch_sdp's
      terminal `else: raise AssertionError` is a real refusal, not dead
      text (this module's own documented-but-dead failure mode).
    test_dispatch_sdp_maps_bool_to_verdict -> parametrised over
      (OP_SDP_LOCK, "sdp_lock") / (OP_SDP_UNLOCK, "sdp_unlock") x
      True/False, driven through run_plan behind a passing id step (gate
      stays OPEN): operator bool return maps to OK/BAD, run_count == 1,
      the operator method is called once with (name, ANY).
    test_shipped_ops_never_reach_sdp_arm -> D-13b's sentinel, criterion 4's
      mechanical proof: with _dispatch_sdp monkeypatched to raise on any
      call, all seven shipped op strings pass through run_plan/_dispatch_step
      without the sentinel ever firing -- proving arm 5's placement adds
      zero branching cost to the seven ops that shipped before this plan.
      Enumerates the seven strings explicitly and cross-checks them against
      the module's own OP_* constants minus _SDP_OPS, so an eighth shipped
      op added later cannot silently escape this sentinel.

  Cleanup registry drain (plan 133-04, D-06/D-07/D-09/D-10/D-16, LEG-10)
    test_finally_drains_on_exception -> a run-fatal exception escaping
      after a successful lock still leaves the registered unlock run --
      the drain reached it even though the loop unwound.
    test_keyboard_interrupt_drains_and_propagates -> KeyboardInterrupt
      escapes run_plan by object IDENTITY after draining the registered
      unlock -- proves the bare `finally` has no `except Exception`
      between the raise and the caller (KeyboardInterrupt is not an
      Exception subclass).
    test_system_exit_drains_and_propagates -> the same shape with
      SystemExit, asserting identity and the unchanged exit code.
    test_empty_registry_noop -> a plan of purely shipped ops (no SDP step)
      never touches operator.sdp_lock/sdp_unlock, and the returned
      `results` list length and content are unaffected by the drain's
      existence -- the length assertion is what would catch an accidental
      drain-time append.
    test_drain_continues_after_failure -> one failing cleanup (raising a
      class named in _UNLOCK_CLEANUP_SWALLOWED) does not strand the entry
      behind it (call_count == 2) and does not mask the original escaping
      exception (identity-asserted in the unwinding variant).
    test_drain_does_not_mutate_results -> AST-level: the empty-handler
      `Try`'s `finalbody` references the name `results` zero times --
      kept as a test (not a shell grep) so it runs in CI on every commit.

  LEG-09 criterion 3 (plan 133-04, D-11)
    test_gate_closed_from_start -> gate-closed-from-the-start: sdp_lock is
      SKIPPED, sdp_unlock is never attempted; a mirror open-gate scenario
      proves the claim is non-vacuous (both ARE called when the gate is
      open).
    test_lock_ran_then_gate_closes -> lock-ran-then-the-gate-closes: the
      registered unlock STILL runs even though a later gate closure would
      have skipped a plan-derived destructive step; also asserts the
      standing OP_SDP_UNLOCK not in _DESTRUCTIVE_OPS invariant, proven by
      a deliberate-break mutation to fail under (see 133-04-SUMMARY.md).

References:
  - .planning/phases/133-sdp-leg-mechanism/133-01-PLAN.md
  - .planning/phases/133-sdp-leg-mechanism/133-CONTEXT.md D-08 (exception
    precedence), D-13 (no-op regression test), D-15 (this module)
  - .planning/phases/133-sdp-leg-mechanism/133-PATTERNS.md
    §tests/test_chip_test_sdp_leg.py
  - tests/test_chip_test.py :287, :793-825 (the harness this module copies)
  - tests/test_sdp_table_parity.py :300-341 (the non-vacuity idiom)
"""

import ast
from pathlib import Path
from unittest.mock import ANY, Mock

import pytest

from firestarter.chip_test import (
    _DEFAULT_REGION,
    _DESTRUCTIVE_GATE_REASON,
    _DESTRUCTIVE_OPS,
    _FF_RATIO_THRESHOLD,
    _MULTI_RUN_OPS,
    _SDP_LEG_OPS,
    _SDP_LEG_STEP_ORDER,
    _SDP_OPS,
    FP_BLANK_CONTACT,
    FP_INDETERMINATE,
    FP_TRANSPORT,
    OP_BLANK_CHECK,
    OP_ERASE,
    OP_ID,
    OP_READ,
    OP_SDP_LOCK,
    OP_SDP_UNLOCK,
    OP_VERIFY,
    OP_WRITE,
    OP_WRITE_BASELINE_A,
    OP_WRITE_BASELINE_B,
    OP_WRITE_INHIBITED,
    OP_WRITE_PARTIAL,
    OP_WRITE_RESTORED,
    SDP_HOLD_HELD,
    SDP_HOLD_NOT_HELD,
    SDP_HOLD_NOT_RUN,
    VERDICT_BAD,
    VERDICT_MARGINAL,
    VERDICT_NA,
    VERDICT_OK,
    VERDICT_SKIPPED,
    Plan,
    Step,
    StepResult,
    _baseline_closes_sdp_gate,
    _dispatch_sdp,
    _dispatch_sdp_leg,
    classify_fingerprint,
    count_applicable,
    derive_plan,
    generate_inhibited_pattern,
    generate_pattern,
    run_plan,
    sdp_hold_state,
    sdp_oracle_applicable,
)
from firestarter.constants import FLAG_SKIP_SDP_UNLOCK
from firestarter.database import EpromDatabase
from firestarter.exceptions import (
    ChipNotFoundError,
    ChipNotImplementedError,
    EpromOperationError,
    FirmwareOutdatedError,
    HardwareOperationError,
    HardwareRevisionUnsupportedError,
    ProgrammerNotFoundError,
    SerialError,
    SerialTimeoutError,
)
from firestarter.sdp_capability import sdp_capability
from firestarter.sdp_honesty import unreadable_state_caveat

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


# ---------------------------------------------------------------------------
# Baseline 2: the exception-precedence triple (criterion 4, D-08). The
# three-constant triple IS the mechanism: _PRE_EDIT_PRECEDENCE_MATRIX
# (frozen forever) vs _EXPECTED_PRECEDENCE_MATRIX (edited by later plans)
# vs _INTENDED_PRECEDENCE_DELTA (the named row set). Drop any one and the
# criterion becomes an assertion about a diff nobody captured
# (133-01-PLAN.md key_links).
# ---------------------------------------------------------------------------

# The nine exception classes this matrix covers, keyed by name so a failure
# message can name the class directly. Every row is derived by RUNNING the
# real engine (_derive_precedence_row below) -- none is hand-transcribed.
_PRECEDENCE_EXCEPTION_CLASSES = {
    "SerialError": SerialError,
    "SerialTimeoutError": SerialTimeoutError,
    "ProgrammerNotFoundError": ProgrammerNotFoundError,
    "FirmwareOutdatedError": FirmwareOutdatedError,
    "EpromOperationError": EpromOperationError,
    "ChipNotImplementedError": ChipNotImplementedError,
    "ChipNotFoundError": ChipNotFoundError,
    "HardwareOperationError": HardwareOperationError,
    "AssertionError": AssertionError,
}

# Fixed error_code used only for the EpromOperationError row (the one class
# in the table whose __init__ accepts error_code at all) -- an arbitrary,
# recognisable sentinel, never read as a real firmware response.id.
_INJECTED_ERROR_CODE = 0x42


def _make_injected_exception(exc_cls):
    """Build one instance of `exc_cls` to inject via `check_eprom_blank`."""
    if exc_cls is EpromOperationError:
        return exc_cls(
            "133-01 injected precedence probe", error_code=_INJECTED_ERROR_CODE
        )
    return exc_cls("133-01 injected precedence probe")


def _derive_precedence_row(exc):
    """Inject `exc` as `check_eprom_blank`'s side_effect and observe where
    `run_plan` routes it.

    Uses OP_BLANK_CHECK deliberately (133-01-PLAN.md Task 2): its dispatch
    arm sits inside `_run_step`'s try and calls the operator directly --
    the exact position `_dispatch_multi_run`'s terminal `raise
    AssertionError` occupies relative to the handler chain, so this probe
    reaches the identical except-clause ordering AssertionError itself
    would hit.

    Returns `(escaped, verdict, error_code)`: `escaped` is the escaping
    exception's class name (and `verdict`/`error_code` are `None`) when
    `exc` propagates all the way out of `run_plan`; otherwise `escaped` is
    `None` and `verdict`/`error_code` are read off the blank-check
    `StepResult`.
    """
    operator = _mock_operator()
    operator.check_eprom_blank.side_effect = exc
    plan = _plan_with_steps(Step(op=OP_BLANK_CHECK, supported=True, reason=""))
    try:
        results = run_plan(plan, operator, _REAL_DB)
    except Exception as escaped:  # intentional: this IS the precedence probe
        return (type(escaped).__name__, None, None)
    result = _result(results, OP_BLANK_CHECK)
    return (None, result.verdict, result.error_code)


# FROZEN: never edited after plan 133-01 (133-CONTEXT.md D-08's basis;
# 133-01-PLAN.md criterion 4). This is the before-image -- a later plan
# editing it destroys the only evidence criterion 4 has that the shipped
# exception-clause ordering was ever measured pre-edit. Measured live this
# session against the unmodified `_run_step` (two clauses: `except
# EpromOperationError`, then `except (ChipNotImplementedError,
# ChipNotFoundError)`):
#
#   - SerialError/SerialTimeoutError/ProgrammerNotFoundError/
#     FirmwareOutdatedError/HardwareOperationError/AssertionError: none of
#     these is an EpromOperationError, ChipNotImplementedError, or
#     ChipNotFoundError, so all six ESCAPE run_plan entirely today.
#   - EpromOperationError: caught by the first clause -> BAD, error_code
#     preserved from the raised exception.
#   - ChipNotImplementedError: this is ALREADY the latent finding
#     D-08 names -- it is a SUBCLASS of EpromOperationError,
#     so it matches the FIRST except clause (Python matches the first
#     matching class) and lands on BAD with error_code=None, never reaching
#     the narrower second clause's SKIPPED mapping. The measurement wins
#     over the "should be SKIPPED" reading: this row records what the
#     shipped code actually does, not what would be tidier.
#   - ChipNotFoundError: NOT a subclass of EpromOperationError (a direct
#     Exception sibling), so it falls through to the second clause ->
#     SKIPPED, error_code=None (the _skip_result() helper never sets it).
_PRE_EDIT_PRECEDENCE_MATRIX = {
    "SerialError": ("SerialError", None, None),
    "SerialTimeoutError": ("SerialTimeoutError", None, None),
    "ProgrammerNotFoundError": ("ProgrammerNotFoundError", None, None),
    "FirmwareOutdatedError": ("FirmwareOutdatedError", None, None),
    "EpromOperationError": (None, "BAD", _INJECTED_ERROR_CODE),
    "ChipNotImplementedError": (None, "BAD", None),
    "ChipNotFoundError": (None, "SKIPPED", None),
    "HardwareOperationError": ("HardwareOperationError", None, None),
    "AssertionError": ("AssertionError", None, None),
}

# CURRENT expectation -- advanced by plan 133-02 (D-08) for EXACTLY the
# three rows measured to change against the edited `_run_step`:
# SerialError, SerialTimeoutError, and HardwareOperationError now escape
# ONLY as far as `_run_step`'s new `except (SerialError,
# HardwareOperationError)` clause and land on BAD/error_code=None --
# neither class carries `.error_code`. ProgrammerNotFoundError and
# FirmwareOutdatedError still ESCAPE (re-raised by the new first clause,
# unchanged from the pre-edit row). EpromOperationError,
# ChipNotImplementedError, ChipNotFoundError, and AssertionError are all
# untouched -- their rows are byte-identical to _PRE_EDIT_PRECEDENCE_MATRIX,
# proving the existing `except EpromOperationError` and `except
# (ChipNotImplementedError, ChipNotFoundError)` clauses were neither moved
# nor reworded. Measured live against the post-133-02-edit engine, never
# hand-derived.
_EXPECTED_PRECEDENCE_MATRIX = dict(_PRE_EDIT_PRECEDENCE_MATRIX)
_EXPECTED_PRECEDENCE_MATRIX["SerialError"] = (None, "BAD", None)
_EXPECTED_PRECEDENCE_MATRIX["SerialTimeoutError"] = (None, "BAD", None)
_EXPECTED_PRECEDENCE_MATRIX["HardwareOperationError"] = (None, "BAD", None)

# Named by plan 133-02 in the SAME commit as the _EXPECTED_PRECEDENCE_MATRIX
# edit above (133-CONTEXT.md D-08; 133-01-PLAN.md must_haves) -- exactly the
# three classes D-08's new degrade clause now catches. Any row that changes
# between _PRE_EDIT_PRECEDENCE_MATRIX and _EXPECTED_PRECEDENCE_MATRIX
# without its exception-class name appearing here turns the suite RED --
# this is the mechanism that proves the remaining six rows (and therefore
# the seven shipped ops' exception handling) are unchanged rather than
# merely assumed.
_INTENDED_PRECEDENCE_DELTA: frozenset[str] = frozenset(
    {"SerialError", "SerialTimeoutError", "HardwareOperationError"}
)


def _compute_precedence_delta(pre, expected):
    """The exact row-diff the delta gate polices, factored out so the
    non-vacuity leg below exercises this SAME comparison rather than a
    re-implementation of it (the `_referenced_underscore_helpers_in_dev_test`
    precedent, 133-PATTERNS.md)."""
    return {name for name in pre if pre[name] != expected[name]}


def _assert_delta_matches_intended(pre, expected, intended):
    """Shared assertion body for the real delta-gate leg and its
    non-vacuity leg. Asserts both mappings cover the identical class set
    (so a row cannot be silently DROPPED instead of changed) and that the
    computed delta equals the declared intended set."""
    assert set(pre) == set(expected), (
        f"key sets differ between _PRE_EDIT_PRECEDENCE_MATRIX "
        f"({sorted(pre)}) and _EXPECTED_PRECEDENCE_MATRIX ({sorted(expected)}) "
        "-- a row was dropped instead of changed (133-01 criterion 4)"
    )
    delta = _compute_precedence_delta(pre, expected)
    assert delta == intended, (
        f"computed precedence delta {sorted(delta)} != declared "
        f"_INTENDED_PRECEDENCE_DELTA {sorted(intended)} -- a chip_test.py "
        "exception-clause behavioural change was made without naming it "
        "in _INTENDED_PRECEDENCE_DELTA (133-CONTEXT.md D-08 mechanism)"
    )


def test_exception_precedence_matrix():
    """Derive all nine rows LIVE (never hand-transcribed) and assert
    equality with _EXPECTED_PRECEDENCE_MATRIX one row at a time, so a
    failure names the offending exception class rather than a bare dict
    diff."""
    assert set(_PRECEDENCE_EXCEPTION_CLASSES) == set(_EXPECTED_PRECEDENCE_MATRIX), (
        "the exception classes this test derives and the expectation "
        "table's keys have diverged -- both must name exactly the same "
        "nine classes"
    )
    for name, exc_cls in _PRECEDENCE_EXCEPTION_CLASSES.items():
        exc = _make_injected_exception(exc_cls)
        row = _derive_precedence_row(exc)
        assert row == _EXPECTED_PRECEDENCE_MATRIX[name], (
            f"{name}: measured (escaped, verdict, error_code)={row!r} != "
            f"expected {_EXPECTED_PRECEDENCE_MATRIX[name]!r} -- "
            "chip_test.py's exception-clause precedence for this class "
            "changed (133-01 before-image, criterion 4)"
        )


def test_precedence_matrix_delta_is_exactly_intended():
    """At this commit both matrices are equal and _INTENDED_PRECEDENCE_DELTA
    is empty, so the computed delta must also be empty."""
    _assert_delta_matches_intended(
        _PRE_EDIT_PRECEDENCE_MATRIX,
        _EXPECTED_PRECEDENCE_MATRIX,
        _INTENDED_PRECEDENCE_DELTA,
    )


def test_precedence_matrix_deriver_is_non_vacuous():
    """A pre-authored gate leg proves nothing until it is SEEN to fail
    (133-PATTERNS.md). Build an in-memory copy of
    _EXPECTED_PRECEDENCE_MATRIX with exactly one row's verdict altered,
    assert the copy differs from the original (the house fixture-setup
    assertion, so a stale edit cannot make this leg vacuous), then run the
    REAL comparison helper (_assert_delta_matches_intended, the same one
    test_precedence_matrix_delta_is_exactly_intended calls) and assert it
    raises AssertionError."""
    altered = dict(_EXPECTED_PRECEDENCE_MATRIX)
    escaped, verdict, error_code = altered["EpromOperationError"]
    assert verdict == "BAD", (
        "Fixture setup error: _EXPECTED_PRECEDENCE_MATRIX['EpromOperationError']"
        f" verdict is {verdict!r}, expected 'BAD' -- this fixture needs updating."
    )
    altered["EpromOperationError"] = (escaped, "BAD-ALTERED-FOR-TEST", error_code)
    assert altered != _EXPECTED_PRECEDENCE_MATRIX, (
        "Fixture setup error: altering one row's verdict did not change "
        "the in-memory copy -- this fixture needs updating."
    )

    try:
        _assert_delta_matches_intended(
            _PRE_EDIT_PRECEDENCE_MATRIX, altered, _INTENDED_PRECEDENCE_DELTA
        )
    except AssertionError:
        pass
    else:
        raise AssertionError(
            "Non-vacuity failure: altering one row's verdict did not make "
            "the delta-gate assertion fail -- the deriver or comparison is "
            "vacuous."
        )


# ---------------------------------------------------------------------------
# LEG-11's four behavioural proofs (plan 133-02, D-08). Unlike the
# precedence-matrix baseline above (which injects into OP_BLANK_CHECK's
# `check_eprom_blank` for a controlled probe of the handler chain), these
# tests use the shape LEG-11's own text describes: a "read" step degrading
# without aborting a later step, and the two run-fatal classes still
# escaping.
# ---------------------------------------------------------------------------


def test_serial_timeout_degrades_one_step():
    """A SerialTimeoutError raised by the "read" step's operator method
    degrades THAT ONE step to a recorded BAD result; run_plan still returns
    a full report for every other step (LEG-11, criterion 2)."""
    operator = _mock_operator()
    operator.read_eprom.side_effect = SerialTimeoutError(
        "133-02 injected half-seated-cable probe"
    )
    plan = _plan_with_steps(
        Step(op=OP_READ, supported=True, reason=""),
        Step(op=OP_BLANK_CHECK, supported=True, reason=""),
    )

    results = run_plan(plan, operator, _REAL_DB)

    read_result = _result(results, OP_READ)
    blank_check_result = _result(results, OP_BLANK_CHECK)
    assert read_result.verdict == VERDICT_BAD
    # The later step still ran -- this is what distinguishes "degraded one
    # step" from "aborted the run" (D-08, T-133-10).
    assert blank_check_result.verdict == VERDICT_OK
    operator.check_eprom_blank.assert_called()


def test_hardware_error_degrades_one_step():
    """The same shape as above with HardwareOperationError -- a sibling of
    Exception, not an EpromOperationError subclass, so the pre-existing
    `except EpromOperationError` clause never reached it before this
    plan's edit (D-08's whole basis)."""
    operator = _mock_operator()
    operator.read_eprom.side_effect = HardwareOperationError(
        "133-02 injected transport-fault probe"
    )
    plan = _plan_with_steps(
        Step(op=OP_READ, supported=True, reason=""),
        Step(op=OP_BLANK_CHECK, supported=True, reason=""),
    )

    results = run_plan(plan, operator, _REAL_DB)

    read_result = _result(results, OP_READ)
    blank_check_result = _result(results, OP_BLANK_CHECK)
    assert read_result.verdict == VERDICT_BAD
    # Observable consequence of the new clause omitting error_code: neither
    # SerialError nor HardwareOperationError carries that attribute.
    assert read_result.error_code is None
    assert blank_check_result.verdict == VERDICT_OK
    operator.check_eprom_blank.assert_called()


@pytest.mark.parametrize(
    "exc_cls",
    [ProgrammerNotFoundError, FirmwareOutdatedError, HardwareRevisionUnsupportedError],
)
def test_run_fatal_escapes(exc_cls):
    """ProgrammerNotFoundError, FirmwareOutdatedError and
    HardwareRevisionUnsupportedError still ESCAPE run_plan unchanged (D-08) --
    these are run-fatal host-setup conditions that belong to cli_handlers.py's
    @map_typed_errors mapper, not chip findings. Escape is asserted by object
    IDENTITY, not just class, so a re-wrap cannot pass.

    Standing invariant: SerialError.__subclasses__() is pinned to the
    measured census. A subclass added by a later phase without updating
    _run_step's re-raise clause would silently bypass it and become a
    false-green no-board report -- this assertion is what would catch that.

    CAP-02 grew the census from three to four. The gate fired exactly as
    designed when HardwareRevisionUnsupportedError was introduced: without the
    matching _run_step change, a shield-revision refusal would have degraded to
    a BAD step per remaining operation, reporting a damaged-looking chip when
    the real cause was a shield that cannot safely drive it."""
    assert set(SerialError.__subclasses__()) == {
        SerialTimeoutError,
        ProgrammerNotFoundError,
        FirmwareOutdatedError,
        HardwareRevisionUnsupportedError,
    }, (
        "SerialError gained or lost a subclass since D-08 was measured -- "
        "_run_step's (ProgrammerNotFoundError, FirmwareOutdatedError, "
        "HardwareRevisionUnsupportedError) re-raise clause is only complete "
        "against the FOUR-class census named here; a new subclass would "
        "silently fall through to the (SerialError, HardwareOperationError) "
        "degrade clause instead of escaping, turning a no-board/old-firmware/"
        "wrong-shield run into a false BAD-step report (133-CONTEXT.md D-08)."
    )

    operator = _mock_operator()
    injected = exc_cls("133-02 injected run-fatal probe")
    operator.read_eprom.side_effect = injected
    plan = _plan_with_steps(Step(op=OP_READ, supported=True, reason=""))

    with pytest.raises(exc_cls) as excinfo:
        run_plan(plan, operator, _REAL_DB)
    assert excinfo.value is injected, (
        "the exception that escaped run_plan is not the SAME instance that "
        "was injected -- a re-wrap would pass a bare pytest.raises(exc_cls) "
        "check but fail this identity assertion"
    )


def test_assertion_error_propagates():
    """The deliberate AssertionError _dispatch_multi_run raises from its
    terminal fail-closed refusal (`chip_test.py`'s
    "unreachable: op {op!r} passed the _MULTI_RUN_OPS guard") still escapes
    run_plan. This is the single behavioural invariant proving no broad
    `except Exception`/`except BaseException` was introduced by this plan's
    edit (criterion 2) -- a broad catch anywhere in `_run_step`'s chain
    would swallow this and turn a programmer-error signal into a silent BAD
    result instead."""
    operator = _mock_operator()
    injected = AssertionError(
        "unreachable: op 'blank-check' passed the _MULTI_RUN_OPS guard"
    )
    operator.check_eprom_blank.side_effect = injected
    plan = _plan_with_steps(Step(op=OP_BLANK_CHECK, supported=True, reason=""))

    with pytest.raises(AssertionError) as excinfo:
        run_plan(plan, operator, _REAL_DB)
    assert excinfo.value is injected, (
        "AssertionError escaped but was not the SAME instance injected -- "
        "criterion 2 requires the deliberate signal to propagate unchanged, "
        "not be re-wrapped or reconstructed"
    )


# ---------------------------------------------------------------------------
# SDP dispatch arm (plan 133-03, D-01/D-04/D-05/D-11, LEG-09). The seven
# shipped op strings, enumerated once here and cross-checked in
# test_shipped_ops_never_reach_sdp_arm against the module's own OP_*
# constants minus _SDP_OPS, so an eighth shipped op cannot silently escape
# the sentinel below.
# ---------------------------------------------------------------------------

_SHIPPED_OP_STRINGS = [
    OP_ID,
    OP_READ,
    OP_BLANK_CHECK,
    OP_WRITE,
    OP_WRITE_PARTIAL,
    OP_VERIFY,
    OP_ERASE,
]


def test_unlock_exempt_from_destructive():
    """The standing _DESTRUCTIVE_OPS asymmetry that IS LEG-09 (D-11):
    OP_SDP_LOCK is gated (a lock applied to a misidentified chip is exactly
    the harm the id-first destructive gate exists to prevent), OP_SDP_UNLOCK
    is deliberately absent (an unlock the gate can skip after a lock
    succeeded ships a locked part). Also asserts _SDP_OPS stays disjoint
    from _MULTI_RUN_OPS (D-03): running a lock twice is a second mutation
    with no comparison value on a family whose protection state cannot be
    read back at all."""
    assert OP_SDP_UNLOCK not in _DESTRUCTIVE_OPS, (
        "OP_SDP_UNLOCK must stay OUT of _DESTRUCTIVE_OPS: an unlock that CAN "
        "be gated is an unlock that can be SKIPPED after a lock already "
        "succeeded, which ships a locked part to the caller "
        "(133-CONTEXT.md D-11, LEG-09)."
    )
    assert OP_SDP_LOCK in _DESTRUCTIVE_OPS, (
        "OP_SDP_LOCK must be IN _DESTRUCTIVE_OPS: a lock applied to a "
        "MISIDENTIFIED chip is exactly the harm the id-first destructive "
        "gate exists to prevent, and gating is what makes the "
        "gate-closed-from-the-start case observable at all "
        "(133-CONTEXT.md D-11, LEG-09)."
    )
    assert _SDP_OPS.isdisjoint(_MULTI_RUN_OPS), (
        "OP_SDP_LOCK/OP_SDP_UNLOCK must stay OUT of _MULTI_RUN_OPS: running "
        "a lock twice is a second mutation with no comparison value, and "
        "the marginal-on-disagreement policy is meaningless for an "
        "emission whose result cannot be read back at all -- SDP "
        "protection state is not readable on this family "
        "(133-CONTEXT.md D-03)."
    )


def test_dispatch_sdp_guard_refuses_foreign_op():
    """_dispatch_sdp's guard refuses every one of the seven shipped op
    strings with a BAD/run_count=0 refusal naming the op and _SDP_OPS, and
    touches the operator double NOT AT ALL -- what makes it a refusal
    rather than a mis-dispatch (T-133-13)."""
    for op in _SHIPPED_OP_STRINGS:
        operator = _mock_operator()
        result = _dispatch_sdp(op, "M8720", {}, operator)
        assert result.verdict == VERDICT_BAD, (
            f"_dispatch_sdp({op!r}, ...) verdict was {result.verdict!r}, "
            "expected BAD -- the guard must refuse any op outside _SDP_OPS"
        )
        assert result.run_count == 0, (
            f"_dispatch_sdp({op!r}, ...) run_count was {result.run_count!r}, "
            "expected 0 -- a refused op must not be counted as having run"
        )
        assert op in result.reason and "_SDP_OPS" in result.reason, (
            f"_dispatch_sdp({op!r}, ...) reason {result.reason!r} does not "
            "name both the refused op and the allow-list it was refused "
            "against"
        )
        assert operator.method_calls == [], (
            f"_dispatch_sdp({op!r}, ...) called the operator double "
            f"({operator.method_calls!r}) -- the guard must refuse BEFORE "
            "touching the operator, not after a mis-dispatch"
        )


def test_dispatch_sdp_terminal_assertion_is_reachable_only_by_bypassing_the_guard(
    monkeypatch,
):
    """_dispatch_sdp's terminal `else: raise AssertionError` is a REAL
    refusal, not dead text -- this module's own documented-but-dead failure
    mode (`_MULTI_RUN_OPS` once shipped with zero references tree-wide).
    Reachable only by monkeypatching `_SDP_OPS` to admit a foreign op past
    the guard, proving the terminal raise is live code the guard's own
    completeness normally makes unreachable."""
    import firestarter.chip_test as chip_test_mod

    monkeypatch.setattr(chip_test_mod, "_SDP_OPS", frozenset({"bogus-sdp-op"}))
    operator = _mock_operator()

    with pytest.raises(AssertionError) as excinfo:
        chip_test_mod._dispatch_sdp("bogus-sdp-op", "M8720", {}, operator)
    assert "_SDP_OPS" in str(excinfo.value), (
        f"AssertionError message {str(excinfo.value)!r} does not name "
        "_SDP_OPS -- the terminal raise's message must name the allow-list "
        "it was passed despite bypassing"
    )
    assert operator.method_calls == [], (
        "the terminal raise fired before either sdp_lock or sdp_unlock was "
        "called on the operator double"
    )


@pytest.mark.parametrize("bool_return", [True, False])
@pytest.mark.parametrize(
    "op,method_name", [(OP_SDP_LOCK, "sdp_lock"), (OP_SDP_UNLOCK, "sdp_unlock")]
)
def test_dispatch_sdp_maps_bool_to_verdict(op, method_name, bool_return):
    """Drive a directly-constructed SDP Step through run_plan behind a
    passing id step (M8720's chip-id sentinel is 0, so check_eprom_id's
    default (True, 0x1234) mock return never triggers a mismatch and the
    destructive gate stays OPEN -- the lock is not SKIPPED). Asserts the
    operator's bool return maps to OK/BAD exactly as _dispatch_step's
    blank-check arm does, run_count == 1, and the corresponding operator
    method was called once with (name, ANY)."""
    operator = _mock_operator(**{method_name: bool_return})
    plan = _plan_with_steps(
        Step(op=OP_ID, supported=True, reason=""),
        Step(op=op, supported=True, reason=""),
    )

    results = run_plan(plan, operator, _REAL_DB)

    result = _result(results, op)
    assert result.verdict == (VERDICT_OK if bool_return else VERDICT_BAD), (
        f"{op!r} with {method_name}()={bool_return!r} produced verdict "
        f"{result.verdict!r}"
    )
    assert result.run_count == 1, (
        f"{op!r}'s run_count was {result.run_count!r}, expected 1 -- SDP "
        "emissions are single-run (D-03)"
    )
    getattr(operator, method_name).assert_called_once_with("M8720", ANY)


def test_shipped_ops_never_reach_sdp_arm(monkeypatch):
    """D-13b's sentinel, criterion 4's mechanical proof (D-04, LEG-09):
    _dispatch_step's arm 5 (`if step.op in _SDP_OPS: return
    _dispatch_sdp(...)`) sits LAST, immediately above the terminal
    fail-closed `return`, so all seven op strings shipped before this plan
    return from arms 1-4 and NEVER evaluate the new membership test at all.

    `_SDP_OPS` is deliberately WIDENED (monkeypatched) to also contain all
    seven shipped op strings for the duration of this test -- this is what
    makes the sentinel sensitive to ARM ORDER rather than merely to op-string
    disjointness: under the correct (position-5) placement, arms 1-4 still
    return before the widened membership test is ever reached, so the
    sentinel stays silent regardless of what _SDP_OPS now contains. If arm 5
    were instead placed ahead of arms 1-4, the widened set would match every
    shipped op immediately and the sentinel would fire -- this was SEEN to
    happen: see 133-03-SUMMARY.md's recorded mutation proof.

    Enumerates the seven shipped op strings EXPLICITLY
    (_SHIPPED_OP_STRINGS) and additionally asserts that set equals the
    module's own shipped op set (every module-level OP_* constant minus
    _SDP_OPS and minus v1.30 Phase 134's own _SDP_LEG_OPS -- the four new
    op strings plan 134-01 adds are vocabulary only at this commit, not yet
    dispatched anywhere, so they are excluded from "shipped" the same way
    _SDP_OPS's own two members are), so a future eighth SHIPPED op (one
    that actually reaches _dispatch_step's arms 1-4) cannot silently escape
    this sentinel by omission.

    If this fails, a shipped op reached the new arm -- the arm was placed
    wrongly and criterion 4's zero-added-branching-cost claim is false
    (133-CONTEXT.md D-04)."""
    import firestarter.chip_test as chip_test_mod

    module_op_constants = {
        value
        for name, value in vars(chip_test_mod).items()
        if name.startswith("OP_") and isinstance(value, str)
    }
    shipped_op_set = module_op_constants - _SDP_OPS - _SDP_LEG_OPS
    assert set(_SHIPPED_OP_STRINGS) == shipped_op_set, (
        f"_SHIPPED_OP_STRINGS {sorted(_SHIPPED_OP_STRINGS)} does not equal "
        f"the module's shipped op set {sorted(shipped_op_set)} (all OP_* "
        "constants minus _SDP_OPS and minus _SDP_LEG_OPS) -- a shipped op "
        "was added to chip_test.py without extending this sentinel's "
        "enumeration (133-CONTEXT.md D-13b)"
    )

    sentinel = Mock(
        side_effect=AssertionError(
            "sentinel: a shipped op reached _dispatch_sdp -- D-04's "
            "zero-added-branching-cost claim is false (arm 5 placed wrongly)"
        )
    )
    monkeypatch.setattr(chip_test_mod, "_dispatch_sdp", sentinel)
    # Widen _SDP_OPS to also match every shipped op -- see docstring above
    # for why this is what makes the sentinel sensitive to arm ORDER.
    monkeypatch.setattr(chip_test_mod, "_SDP_OPS", frozenset(shipped_op_set | _SDP_OPS))

    operator = _mock_operator()
    plan = _plan_with_steps(
        *(Step(op=op, supported=True, reason="") for op in _SHIPPED_OP_STRINGS)
    )

    results = run_plan(plan, operator, _REAL_DB)

    sentinel.assert_not_called()
    assert len(results) == len(_SHIPPED_OP_STRINGS), (
        f"run_plan returned {len(results)} results for "
        f"{len(_SHIPPED_OP_STRINGS)} steps -- a step was silently dropped "
        "or added while the sentinel was active"
    )


# ---------------------------------------------------------------------------
# LEG-03's five pattern assertions (v1.30 Phase 134, plan 134-01, D-19).
# Every assertion below is computed against the LIVE generators for the
# REAL region -- never against a byte literal -- and the region itself is
# derived from chip_test._DEFAULT_REGION rather than hard-coded, so a
# future region change fails this test loudly instead of silently passing.
# `pytest -k "pattern_b"` selects this whole class (134-VALIDATION.md).
#
# ⚠ P-01, the milestone's headline pitfall: `generate_pattern` is a PURE
# function of (start, length). Every assertion here exists specifically to
# make the idiomatic-but-wrong implementation (deriving B by calling
# `generate_pattern` a second time) fail loudly rather than silently ship
# a tautology that reads as correct in review.
# ---------------------------------------------------------------------------


class TestInhibitedPattern:
    def test_pattern_b_same_length_as_pattern_a(self):
        region = _DEFAULT_REGION
        a = generate_pattern(*region)
        b = generate_inhibited_pattern(*region)
        assert len(b) == len(a), (
            f"generate_inhibited_pattern{region} returned {len(b)} bytes, "
            f"generate_pattern{region} returned {len(a)} -- B must be the "
            "same length as A"
        )

    def test_pattern_b_differs_from_pattern_a_at_every_byte(self):
        # "differ at every byte" -- not "differ somewhere". A one-page lock
        # leak (a single byte failing to invert) must be detectable; a
        # weaker "differ somewhere" assertion would let exactly that leak
        # through undetected (P-01/D-19, LEG-03).
        region = _DEFAULT_REGION
        a = generate_pattern(*region)
        b = generate_inhibited_pattern(*region)
        differing = sum(1 for x, y in zip(a, b) if x != y)
        assert differing == len(a), (
            f"generate_inhibited_pattern{region} differed from "
            f"generate_pattern{region} at only {differing}/{len(a)} bytes "
            "-- expected EVERY byte to differ. A partial match here means "
            "a one-page lock leak would not be detectable (P-01/D-19)."
        )

    def test_pattern_b_is_not_pattern_a_and_not_a_second_generate_pattern_call(self):
        # The direct anti-tautology assertion: B must differ from A, AND B
        # must differ from a fresh generate_pattern() call over the SAME
        # region -- ruling out exactly the idiomatic-but-wrong
        # implementation P-01 warns about (calling generate_pattern twice).
        region = _DEFAULT_REGION
        a = generate_pattern(*region)
        b = generate_inhibited_pattern(*region)
        assert b != a, "B must not equal A"
        assert b != generate_pattern(*region), (
            "B must not equal a second generate_pattern() call over the "
            "same region -- if it did, generate_inhibited_pattern would be "
            "a tautology: generate_pattern is a PURE function of "
            "(start, length), so calling it twice for the same region "
            "always yields the same bytes (P-01, the milestone's headline "
            "pitfall)."
        )

    def test_neither_pattern_a_nor_pattern_b_is_all_zero_or_all_ff(self):
        region = _DEFAULT_REGION
        length = region[1]
        a = generate_pattern(*region)
        b = generate_inhibited_pattern(*region)
        all_zero = bytes(length)
        all_ff = b"\xff" * length
        assert a != all_zero, "A must not be all-0x00 (degenerate pattern)"
        assert a != all_ff, "A must not be all-0xFF (degenerate pattern)"
        assert b != all_zero, "B must not be all-0x00 (degenerate pattern)"
        assert b != all_ff, "B must not be all-0xFF (degenerate pattern)"

    def test_pattern_b_readback_does_not_launder_as_blank_contact(self):
        # D-05's non-laundering leg: a fully-B read-back (the shape a
        # firmware that silently ignored the SDP lock and accepted the
        # inhibited write would produce) must be classified as a real
        # divergence, never as blank/contact -- otherwise a leaked lock
        # would render as a loose socket rather than as a chip finding.
        #
        # Measured at this commit (context only, not the assertion): B's
        # ff_ratio is ~0.0039 against a live _FF_RATIO_THRESHOLD of 0.98 --
        # comfortably below the threshold, but the assertion below reads
        # the live threshold, never this comment's number.
        region = _DEFAULT_REGION
        a = generate_pattern(*region)
        b = generate_inhibited_pattern(*region)
        fingerprint = classify_fingerprint(a, b, addr_base=region[0])
        assert fingerprint.classification != FP_BLANK_CONTACT, (
            f"classify_fingerprint(A, B, addr_base={region[0]}) classified "
            f"a fully-B read-back as {fingerprint.classification!r} -- "
            "expected anything OTHER than blank/contact (D-05): a leaked "
            "lock must not be able to launder as a contact fault."
        )
        ff_ratio = fingerprint.evidence["ff_ratio"]
        assert ff_ratio < _FF_RATIO_THRESHOLD, (
            f"measured ff_ratio {ff_ratio!r} is not strictly below the "
            f"live _FF_RATIO_THRESHOLD {_FF_RATIO_THRESHOLD!r} -- B's "
            "read-back must sit clearly below the blank/contact threshold "
            "(D-05)."
        )


# ---------------------------------------------------------------------------
# The oracle's read-back-equality dispatch (v1.30 Phase 134, plan 134-02,
# D-01/D-02/D-03/D-04/D-05, LEG-05/06(engine half)/07/08/16).
#
# `_readback_operator` is a SEPARATE double from `_mock_operator` above --
# `_mock_operator`'s `read_eprom` returns True while writing no file, so the
# engine would see `actual = b""` and every oracle test would silently
# exercise only the length gate. `pytest -k "oracle_readback"` /
# `"lock_leaked"` / `"partial_readback"` (134-VALIDATION.md) select the
# tests below; `-k "degenerate"` / `"dead_write_path"` selects plan
# 134-02 Task 3's fixtures, added in a later commit.
# ---------------------------------------------------------------------------


def _readback_operator(payload: bytes, *, write_ok: bool = True, **returns):
    """Read-back-capable operator double for the SDP-leg oracle.

    `write_eprom` returns `write_ok`; `read_eprom` writes `payload` to the
    `output_file` keyword argument (mirroring `EpromOperator.read_eprom`'s
    real calling convention, `chip_test.py:_dispatch_read`'s
    `output_file=out_path` usage) and returns `True`. Extra `**returns`
    override any other named method's `.return_value`, mirroring
    `_mock_operator`'s own extension idiom.
    """
    op = Mock(spec=_OPERATOR_METHODS)
    op.check_eprom_id.return_value = (True, 0x1234)
    op.check_eprom_blank.return_value = True
    op.erase_eprom.return_value = True
    op.verify_eprom.return_value = True
    op.write_eprom.return_value = write_ok

    def _read_eprom(name, eprom_data, output_file=None, **kwargs):
        if output_file is not None:
            Path(output_file).write_bytes(payload)
        return True

    op.read_eprom.side_effect = _read_eprom

    for name, value in returns.items():
        getattr(op, name).return_value = value
        getattr(op, name).side_effect = None
    return op


def test_oracle_readback_true_a_produces_ok():
    """LEG-05/D-03's polarity pin, first half: with `write_eprom` held
    CONSTANT at `True`, a read-back equal to pattern A produces OK. Paired
    with the next test (same bool, read-back B => BAD), this pair is
    STRONGER than a bool-driven proof: an implementation that derives the
    verdict from `write_eprom`'s return value cannot produce two different
    verdicts from one identical bool -- only reading the actual bytes back
    can flip the outcome here."""
    region = _DEFAULT_REGION
    a = generate_pattern(*region)
    operator = _readback_operator(a, write_ok=True)

    result = _dispatch_sdp_leg(OP_WRITE_INHIBITED, "M8720", {}, operator)

    assert result.verdict == VERDICT_OK, (
        f"(write_ok=True, read-back=A) produced {result.verdict!r}, expected OK"
    )


def test_oracle_readback_true_b_produces_bad():
    """LEG-05/D-03's polarity pin, second half: the bool is STILL held
    constant at `True` (same as the previous test) -- only the read-back
    changes, from A to B -- yet the verdict flips to BAD. A bool-driven
    implementation cannot produce this pair from one identical bool value.

    P-03 prevention 4's `(False, A) => OK` is recorded as OVERTURNED by
    D-01/D-03 and is NOT implemented: the `0x86` opt-out ack cannot be
    observed as a separate signal from this module (see
    `_dispatch_sdp_leg`'s own docstring, D-01), so D-01 instead routes
    every failed-precondition case to `marginal`."""
    region = _DEFAULT_REGION
    b = generate_inhibited_pattern(*region)
    operator = _readback_operator(b, write_ok=True)

    result = _dispatch_sdp_leg(OP_WRITE_INHIBITED, "M8720", {}, operator)

    assert result.verdict == VERDICT_BAD, (
        f"(write_ok=True, read-back=B) produced {result.verdict!r}, expected BAD"
    )


def test_oracle_readback_false_a_produces_marginal():
    """D-03's precondition-gate pin, direction A: `write_eprom` failing is
    NEVER BAD (D-01/D-02) -- it routes to marginal regardless of which
    pattern the read-back happens to match."""
    region = _DEFAULT_REGION
    a = generate_pattern(*region)
    operator = _readback_operator(a, write_ok=False)

    result = _dispatch_sdp_leg(OP_WRITE_INHIBITED, "M8720", {}, operator)

    assert result.verdict == VERDICT_MARGINAL, (
        f"(write_ok=False, read-back=A) produced {result.verdict!r}, "
        "expected marginal (D-01/D-02: a failed precondition is never BAD)"
    )


def test_oracle_readback_false_b_produces_marginal():
    """D-03's precondition-gate pin, direction B: pinned in BOTH read-back
    directions -- a failed write_eprom must never be read as BAD even when
    the read-back happens to equal B, or D-01's whole "precondition, never
    verdict" reading collapses back into a bool-driven oracle."""
    region = _DEFAULT_REGION
    b = generate_inhibited_pattern(*region)
    operator = _readback_operator(b, write_ok=False)

    result = _dispatch_sdp_leg(OP_WRITE_INHIBITED, "M8720", {}, operator)

    assert result.verdict == VERDICT_MARGINAL, (
        f"(write_ok=False, read-back=B) produced {result.verdict!r}, "
        "expected marginal in BOTH read-back directions (D-03)"
    )


def test_lock_leaked_write_ok_true_b_readback_is_bad():
    """LEG-06 engine half: the `(True, B) => BAD` case again, asserted by
    NAME so `134-VALIDATION.md`'s `-k "lock_leaked"` selector resolves this
    leg on its own. The EXIT-CODE half of LEG-06 -- that a run containing
    this leaked-lock result overall exits 1 -- is plan `134-05`'s; this
    test ALONE does not discharge LEG-06."""
    region = _DEFAULT_REGION
    b = generate_inhibited_pattern(*region)
    operator = _readback_operator(b, write_ok=True)

    result = _dispatch_sdp_leg(OP_WRITE_INHIBITED, "M8720", {}, operator)

    assert result.verdict == VERDICT_BAD, (
        f"write_eprom reported True (the ack was observed) but the "
        f"read-back is fully pattern B -- verdict was {result.verdict!r}, "
        "expected BAD (the SDP lock leaked)"
    )


def test_partial_readback_reports_bad():
    """LEG-07, gh#11's exact symptom: a read-back that differs from
    pattern A in only the first 16 bytes -- spliced from the LIVE
    pattern-B generator, never a literal -- reports BAD on write-inhibited
    even though write_eprom reported success. Covers the "changed only
    some bytes" branch of `(True, ≠A) => BAD`, distinct from the
    fully-B case `test_lock_leaked_write_ok_true_b_readback_is_bad` covers.
    """
    region = _DEFAULT_REGION
    a = generate_pattern(*region)
    b = generate_inhibited_pattern(*region)
    partial = b[:16] + a[16:]
    assert partial != a and partial != b, (
        "fixture setup error: the spliced partial read-back must differ "
        "from both pattern A and pattern B"
    )
    operator = _readback_operator(partial, write_ok=True)

    result = _dispatch_sdp_leg(OP_WRITE_INHIBITED, "M8720", {}, operator)

    assert result.verdict == VERDICT_BAD, (
        f"partial read-back (first 16 bytes changed) produced verdict "
        f"{result.verdict!r}, expected BAD (LEG-07)"
    )


def test_inhibited_marginal_reason_names_both_causes():
    """D-01/D-02: for a `(write_ok=False, ...)` inhibited result, the
    `reason` string must name BOTH candidate causes -- the `0x86` opt-out
    ack not honoured by the firmware, and a transport fault -- plus the
    firmware-update instruction, so a future edit that reduces this to one
    cause fails here."""
    region = _DEFAULT_REGION
    a = generate_pattern(*region)
    operator = _readback_operator(a, write_ok=False)

    result = _dispatch_sdp_leg(OP_WRITE_INHIBITED, "M8720", {}, operator)

    assert "0x86 opt-out ack" in result.reason, result.reason
    assert "transport fault" in result.reason, result.reason
    assert "firestarter fw --install" in result.reason, result.reason


def test_inhibited_write_sets_flag_skip_sdp_unlock():
    """RESEARCH §4.3: `write-inhibited`'s `write_eprom` call carries
    `FLAG_SKIP_SDP_UNLOCK` as its 4th positional (`operation_flags`)
    argument."""
    region = _DEFAULT_REGION
    a = generate_pattern(*region)
    operator = _readback_operator(a, write_ok=True)

    _dispatch_sdp_leg(OP_WRITE_INHIBITED, "M8720", {}, operator)

    flags = operator.write_eprom.call_args.args[3]
    assert flags & FLAG_SKIP_SDP_UNLOCK, (
        f"write-inhibited's write_eprom call did not carry "
        f"FLAG_SKIP_SDP_UNLOCK: flags={flags!r}"
    )


@pytest.mark.parametrize(
    "op,payload_fn",
    [
        (OP_WRITE_BASELINE_B, lambda region: generate_inhibited_pattern(*region)),
        (OP_WRITE_BASELINE_A, lambda region: generate_pattern(*region)),
        (OP_WRITE_RESTORED, lambda region: generate_pattern(*region)),
    ],
)
def test_non_inhibited_writes_clear_flag_skip_sdp_unlock(op, payload_fn):
    """RESEARCH §4.3: the flag is CLEAR for write-baseline-b,
    write-baseline-a AND write-restored. The write-restored leg is the
    load-bearing case: setting the flag there would defeat that step's
    whole purpose (it must be allowed to auto-unlock and succeed so the
    part is left writable)."""
    region = _DEFAULT_REGION
    payload = payload_fn(region)
    operator = _readback_operator(payload, write_ok=True)

    _dispatch_sdp_leg(op, "M8720", {}, operator)

    flags = operator.write_eprom.call_args.args[3]
    assert not (flags & FLAG_SKIP_SDP_UNLOCK), (
        f"{op!r}'s write_eprom call carried FLAG_SKIP_SDP_UNLOCK "
        f"(flags={flags!r}) -- only write-inhibited may set this bit"
    )


# ---------------------------------------------------------------------------
# LEG-08's four degenerate read-back fixtures, LEG-16's dead-write-path
# fixture, and D-05 (v1.30 Phase 134, plan 134-02 Task 3).
#
# ⚠ Evidence Ceiling (REQUIREMENTS.md, reusing 133-RECORD.md §6's wording
# rather than authoring a new formulation): a locked die is
# unrepresentable in either repo's stubs. NO fixture in this module
# simulates real inhibition -- every fixture here pins the host's
# RESPONSE to a scripted read-back only. The causal claim "the lock
# inhibited the write" is NOT provable this milestone; real silicon is
# missing with no fallback. `pytest -k "degenerate"` selects LEG-08's four
# fixtures; `-k "dead_write_path"` selects LEG-16's.
# ---------------------------------------------------------------------------


def test_module_reuses_sdp_honesty_caveat_wording():
    """This module's Evidence Ceiling comment above states the causal
    claim "the lock inhibited the write" is NOT provable this milestone --
    reusing 133-RECORD.md §6's wording rather than authoring a new
    formulation. Calls the real production caveat
    (`sdp_honesty.unreadable_state_caveat()`) rather than re-authoring its
    sentence, so this module's own framing cannot silently drift from the
    production string a real report row would show."""
    caveat = unreadable_state_caveat()
    assert caveat, "unreadable_state_caveat() must return a non-empty string"
    assert "cannot be read back" in caveat, caveat


def test_degenerate_readback_empty_is_bad():
    """LEG-08, degenerate arm 1/4: an empty read-back. Measured trap this
    defends: `classify_fingerprint(A, b"")` returns `total=0, bad=0` -- an
    empty read-back reads as PERFECT equality; only the length gate stops
    it (P-02)."""
    operator = _readback_operator(b"", write_ok=True)

    result = _dispatch_sdp_leg(OP_WRITE_BASELINE_A, "M8720", {}, operator)

    assert result.verdict == VERDICT_BAD, (
        f"empty read-back produced verdict {result.verdict!r}, expected "
        "BAD (the length gate, checked before any classify_fingerprint "
        "call)"
    )


def test_degenerate_readback_short_is_bad():
    """LEG-08, degenerate arm 2/4: a truncated (short) read-back -- the
    first 128 of 256 bytes of the expected pattern -- also never reads as
    equality; caught by the same length gate as the empty case."""
    region = _DEFAULT_REGION
    a = generate_pattern(*region)
    short = a[:128]
    operator = _readback_operator(short, write_ok=True)

    result = _dispatch_sdp_leg(OP_WRITE_BASELINE_A, "M8720", {}, operator)

    assert result.verdict == VERDICT_BAD, (
        f"short (128/256-byte) read-back produced verdict "
        f"{result.verdict!r}, expected BAD"
    )


def test_degenerate_readback_all_zero_is_marginal():
    """LEG-08, degenerate arm 3/4: correct-length but all-0x00 content
    routes through classify_fingerprint and lands marginal (D-04) -- never
    OK, and never a confidently-reported chip finding.

    ⚠ Does NOT assert the `address-line` classification: that arm requires
    `cmp_len > 256` (`chip_test.py`'s `classify_fingerprint`) and this
    leg's region is exactly 256, so such an assertion would be
    unreachable-green -- asserts only that the classification is one of
    the labels actually reachable for a 256-byte region instead."""
    region = _DEFAULT_REGION
    length = region[1]
    operator = _readback_operator(b"\x00" * length, write_ok=True)

    result = _dispatch_sdp_leg(OP_WRITE_BASELINE_A, "M8720", {}, operator)

    assert result.verdict == VERDICT_MARGINAL, (
        f"all-0x00 read-back produced verdict {result.verdict!r}, expected marginal"
    )
    assert result.fingerprint is not None
    assert result.fingerprint.classification in (
        FP_BLANK_CONTACT,
        FP_TRANSPORT,
        FP_INDETERMINATE,
    ), (
        f"all-0x00 read-back's fingerprint classification "
        f"{result.fingerprint.classification!r} is not one of the labels "
        "classify_fingerprint can produce for a 256-byte region"
    )


def test_degenerate_readback_all_ff_is_marginal_blank_contact():
    """LEG-08, degenerate arm 4/4: correct-length all-0xFF content
    classifies as FP_BLANK_CONTACT -- a loose socket reads as a contact
    fault, not a chip finding (D-04)."""
    region = _DEFAULT_REGION
    length = region[1]
    operator = _readback_operator(b"\xff" * length, write_ok=True)

    result = _dispatch_sdp_leg(OP_WRITE_BASELINE_A, "M8720", {}, operator)

    assert result.verdict == VERDICT_MARGINAL, (
        f"all-0xFF read-back produced verdict {result.verdict!r}, expected marginal"
    )
    assert result.fingerprint is not None
    assert result.fingerprint.classification == FP_BLANK_CONTACT, (
        f"all-0xFF read-back's fingerprint classification "
        f"{result.fingerprint.classification!r} != FP_BLANK_CONTACT"
    )


def test_inhibited_full_b_readback_does_not_launder_as_blank_contact():
    """D-05's non-laundering leg, through the REAL dispatcher (distinct
    from `TestInhibitedPattern`'s version above, which calls
    `classify_fingerprint` directly): a fully-B read-back on
    write-inhibited reports BAD, and its attached fingerprint
    classification is NOT FP_BLANK_CONTACT -- a leaked lock must not be
    able to launder as a contact fault. Computed from the live generators
    and the live threshold, never a literal (measured `ff_ratio` is
    ~0.0039 against a live `_FF_RATIO_THRESHOLD` of 0.98 -- recorded here
    as context only, not the assertion)."""
    region = _DEFAULT_REGION
    b = generate_inhibited_pattern(*region)
    operator = _readback_operator(b, write_ok=True)

    result = _dispatch_sdp_leg(OP_WRITE_INHIBITED, "M8720", {}, operator)

    assert result.verdict == VERDICT_BAD
    assert result.fingerprint is not None
    assert result.fingerprint.classification != FP_BLANK_CONTACT, (
        f"a fully-B read-back on write-inhibited classified as "
        f"{result.fingerprint.classification!r} -- expected anything "
        "OTHER than blank/contact (D-05)"
    )
    ff_ratio = result.fingerprint.evidence["ff_ratio"]
    assert ff_ratio < _FF_RATIO_THRESHOLD, (
        f"measured ff_ratio {ff_ratio!r} is not strictly below the live "
        f"_FF_RATIO_THRESHOLD {_FF_RATIO_THRESHOLD!r}"
    )


def _dead_write_path_operator():
    """LEG-16's committed dead-write-path fixture (v1.30 Phase 134, plan
    134-02, D-07).

    `write_eprom` returns `True` (the write path claims success) while
    `read_eprom` ALWAYS yields pattern A, whatever was actually written --
    a fixture-level stand-in for a chip whose write path never transitions
    the die.

    Why the B direction is the ENTIRE discriminating power here (D-07,
    "Why reuse cannot satisfy LEG-16"): pattern A is already what the
    shipped `write` step writes at `runs=2` over region `(0, 256)`, so a
    chip that already holds A with a dead write path passes the shipped
    write->verify pair TODAY -- reusing that pair for the baseline could
    never detect it. `write-baseline-b` is what makes the gate real: a
    no-op write leaves A in place, so a B-direction read-back fails to
    match and the step goes BAD.

    Evidence Ceiling: this fixture pins the host's RESPONSE to a scripted
    read-back -- it does not and cannot simulate a real dead write path on
    silicon.
    """
    region = _DEFAULT_REGION
    a = generate_pattern(*region)
    operator = Mock(spec=_OPERATOR_METHODS)
    operator.check_eprom_id.return_value = (True, 0x1234)
    operator.check_eprom_blank.return_value = True
    operator.erase_eprom.return_value = True
    operator.verify_eprom.return_value = True
    operator.write_eprom.return_value = True

    def _read_eprom(name, eprom_data, output_file=None, **kwargs):
        if output_file is not None:
            Path(output_file).write_bytes(a)
        return True

    operator.read_eprom.side_effect = _read_eprom
    return operator


def test_dead_write_path_baseline_b_is_bad():
    """LEG-16: the committed dead-write-path fixture makes write-baseline-b
    report BAD -- proving the B direction is the leg's entire
    discriminating power (D-07)."""
    operator = _dead_write_path_operator()

    result = _dispatch_sdp_leg(OP_WRITE_BASELINE_B, "M8720", {}, operator)

    assert result.verdict == VERDICT_BAD, (
        f"write-baseline-b against the dead-write-path fixture produced "
        f"verdict {result.verdict!r}, expected BAD (LEG-16)"
    )


# ---------------------------------------------------------------------------
# Cleanup registry drain (plan 133-04, D-06/D-07/D-09/D-10/D-16, LEG-10).
# Absolute path to firestarter_app/, cwd-independent (mirrors
# tests/test_check_devtest_orchestrator.py's _FA_DIR pattern) -- used only by
# the AST-level results-mutation proof below, which reads the INSTALLED
# source rather than importing chip_test.py's already-compiled bytecode.
# ---------------------------------------------------------------------------

_FA_DIR = Path(__file__).parent.parent


def _run_plan_finally_node() -> ast.Try:
    """Parse firestarter/chip_test.py, locate `run_plan`, and return the
    `ast.Try` node whose `handlers` list is EMPTY -- the bare `finally` with
    no `except` clause of any width that criteria 1+2 require. Asserts
    there is EXACTLY one such node inside `run_plan`, so this helper itself
    cannot silently resolve to the wrong `Try` if a later plan adds another
    try/finally elsewhere in the function.
    """
    source = (_FA_DIR / "firestarter" / "chip_test.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="chip_test.py")
    run_plan_def = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_plan":
            run_plan_def = node
            break
    assert run_plan_def is not None, "run_plan not found in chip_test.py"

    empty_handler_tries = [
        node
        for node in ast.walk(run_plan_def)
        if isinstance(node, ast.Try) and node.handlers == [] and node.finalbody
    ]
    assert len(empty_handler_tries) == 1, (
        f"expected exactly one bare try/finally (no except clauses) inside "
        f"run_plan, found {len(empty_handler_tries)} -- the LEG-10 drain "
        "structure is no longer uniquely locatable"
    )
    return empty_handler_tries[0]


def test_finally_drains_on_exception():
    """LEG-10 criterion 1: a run-fatal exception escaping run_plan after a
    successful lock still leaves the registered unlock run -- the drain
    reached it even though the loop unwound (research P-20's abort-between-
    lock-and-unlock hazard, closed)."""
    operator = _mock_operator(sdp_lock=True)
    injected = ProgrammerNotFoundError("133-04 injected escape probe")
    operator.read_eprom.side_effect = injected
    plan = _plan_with_steps(
        Step(op=OP_ID, supported=True, reason=""),
        Step(op=OP_SDP_LOCK, supported=True, reason=""),
        Step(op=OP_READ, supported=True, reason=""),
    )

    with pytest.raises(ProgrammerNotFoundError) as excinfo:
        run_plan(plan, operator, _REAL_DB)

    assert excinfo.value is injected, (
        "the exception escaping run_plan must be the SAME instance that "
        "was injected -- a re-wrap would pass a bare pytest.raises check "
        "but violate criterion 2's identity guarantee"
    )
    operator.sdp_unlock.assert_called_once_with("M8720", ANY)


def test_keyboard_interrupt_drains_and_propagates():
    """LEG-10 criteria 1+2: KeyboardInterrupt escapes run_plan by object
    IDENTITY after the registered unlock has drained. KeyboardInterrupt is
    not an Exception subclass, so this also independently proves no broad
    `except Exception` sits between the raise and the caller."""
    operator = _mock_operator(sdp_lock=True)
    injected = KeyboardInterrupt()
    operator.read_eprom.side_effect = injected
    plan = _plan_with_steps(
        Step(op=OP_ID, supported=True, reason=""),
        Step(op=OP_SDP_LOCK, supported=True, reason=""),
        Step(op=OP_READ, supported=True, reason=""),
    )

    with pytest.raises(KeyboardInterrupt) as excinfo:
        run_plan(plan, operator, _REAL_DB)

    assert excinfo.value is injected, (
        "the KeyboardInterrupt escaping run_plan must be the SAME instance "
        "injected -- a re-wrap here would mean Ctrl-C did not stay Ctrl-C "
        "(criterion 2)"
    )
    operator.sdp_unlock.assert_called_once_with("M8720", ANY)


def test_system_exit_drains_and_propagates():
    """LEG-10 criteria 1+2: the same shape as KeyboardInterrupt, with
    SystemExit -- identity-asserted, plus the exit code attribute unchanged
    (a re-wrap could preserve the class but drop the code)."""
    operator = _mock_operator(sdp_lock=True)
    injected = SystemExit(7)
    operator.read_eprom.side_effect = injected
    plan = _plan_with_steps(
        Step(op=OP_ID, supported=True, reason=""),
        Step(op=OP_SDP_LOCK, supported=True, reason=""),
        Step(op=OP_READ, supported=True, reason=""),
    )

    with pytest.raises(SystemExit) as excinfo:
        run_plan(plan, operator, _REAL_DB)

    assert excinfo.value is injected, (
        "the SystemExit escaping run_plan must be the SAME instance injected"
    )
    assert excinfo.value.code == 7, (
        f"SystemExit.code changed to {excinfo.value.code!r}, expected 7 -- "
        "a re-wrap could preserve the class but drop the code"
    )
    operator.sdp_unlock.assert_called_once_with("M8720", ANY)


def test_empty_registry_noop():
    """LEG-10: an empty cleanup registry (every currently-shipping run,
    since no plan in this phase derives an SDP step) is a PROVEN no-op --
    zero added operator calls, and the returned `results` are unaffected by
    the drain's existence. Reuses _SHIPPED_OPS_SEQUENCE (133-01's frozen
    before-image) rather than a fresh literal: if the drain silently
    appended anything, this would diverge from that pre-133-04 baseline."""
    plan = derive_plan("M8720", _REAL_DB)
    operator = _mock_operator()

    results = run_plan(plan, operator, _REAL_DB)

    operator.sdp_lock.assert_not_called()
    operator.sdp_unlock.assert_not_called()

    op_sequence = [r.op for r in results]
    assert op_sequence == _SHIPPED_OPS_SEQUENCE["op_sequence"], (
        f"op sequence changed under an empty registry: {op_sequence!r} vs "
        f"frozen baseline {_SHIPPED_OPS_SEQUENCE['op_sequence']!r}"
    )
    verdict_run_count = [(r.verdict, r.run_count) for r in results]
    assert verdict_run_count == _SHIPPED_OPS_SEQUENCE["verdict_run_count"], (
        f"per-step (verdict, run_count) changed under an empty registry: "
        f"{verdict_run_count!r} vs frozen baseline "
        f"{_SHIPPED_OPS_SEQUENCE['verdict_run_count']!r}"
    )
    assert len(results) == _SHIPPED_OPS_SEQUENCE["len_results"], (
        f"run_plan returned {len(results)} results, expected "
        f"{_SHIPPED_OPS_SEQUENCE['len_results']} -- the drain's existence "
        "must not silently add or drop a result (this is what would catch "
        "an accidental results.append inside the finally)"
    )


def test_drain_continues_after_failure():
    """D-10: one failing cleanup (raising a class named in
    _UNLOCK_CLEANUP_SWALLOWED) does not strand the entry behind it in the
    registry -- the drain continues and both registered unlocks are
    attempted (call_count == 2), run_plan returns normally, and the failure
    is not recorded into `results` (its length still equals the plan's step
    count).

    The second, unwinding variant additionally makes the loop unwind on an
    escaping exception and asserts the escaping exception's IDENTITY is
    still the ORIGINAL one -- the whole point of D-10: a cleanup failure
    must never mask the in-flight exception.
    """
    operator = _mock_operator(sdp_lock=True)
    operator.sdp_unlock.side_effect = [
        HardwareOperationError("133-04 injected cleanup failure"),
        True,
    ]
    plan = _plan_with_steps(
        Step(op=OP_ID, supported=True, reason=""),
        Step(op=OP_SDP_LOCK, supported=True, reason=""),
        Step(op=OP_SDP_LOCK, supported=True, reason=""),
    )

    results = run_plan(plan, operator, _REAL_DB)

    assert operator.sdp_unlock.call_count == 2, (
        f"sdp_unlock was called {operator.sdp_unlock.call_count} time(s), "
        "expected 2 -- the entry registered behind the failing cleanup was "
        "stranded instead of drained (D-10)"
    )
    assert len(results) == len(plan.steps), (
        "the drain's caught cleanup failure must not be recorded into "
        "results -- results must feed only run_plan's own step loop"
    )

    # Unwinding variant: the drain's caught failure must not mask an
    # ORIGINAL escaping exception.
    operator2 = _mock_operator(sdp_lock=True)
    operator2.sdp_unlock.side_effect = [
        HardwareOperationError("133-04 injected cleanup failure (variant 2)"),
        True,
    ]
    injected = ProgrammerNotFoundError("133-04 injected original fault")
    operator2.read_eprom.side_effect = injected
    plan2 = _plan_with_steps(
        Step(op=OP_ID, supported=True, reason=""),
        Step(op=OP_SDP_LOCK, supported=True, reason=""),
        Step(op=OP_SDP_LOCK, supported=True, reason=""),
        Step(op=OP_READ, supported=True, reason=""),
    )

    with pytest.raises(ProgrammerNotFoundError) as excinfo:
        run_plan(plan2, operator2, _REAL_DB)

    assert excinfo.value is injected, (
        "a failing cleanup must not mask the ORIGINAL escaping exception "
        "by object identity -- this assertion is the whole point of D-10"
    )
    assert operator2.sdp_unlock.call_count == 2, (
        "the drain must continue past the caught cleanup failure even "
        "while the loop is unwinding on an escaping exception"
    )


def test_drain_does_not_mutate_results():
    """The drain must NEVER append into `results`, and must not reference
    it at all: `results` is returned by reference, so a mutation inside
    the `finally` IS visible to the caller, and that same list feeds seven
    consumers in cli_handlers.py (the run_plan call site, count_applicable,
    the generic renderer, the JSON artifact, the markdown table,
    build_db_diff, sys.exit(max(...))) -- count_applicable would then
    render "N greater than M" (e.g. "8 of 7 ran"). This is an AST-level
    acceptance criterion in its own right, kept as a TEST (not a shell
    grep) so it runs in CI on every commit."""
    finally_try = _run_plan_finally_node()

    results_refs = [
        node
        for node in ast.walk(ast.Module(body=finally_try.finalbody, type_ignores=[]))
        if isinstance(node, ast.Name) and node.id == "results"
    ]
    assert results_refs == [], (
        f"run_plan's cleanup-drain finally references the name 'results' "
        f"{len(results_refs)} time(s) -- it must reference it ZERO times. "
        "results is returned BY REFERENCE, so a finally-time mutation is "
        "visible to the caller and feeds seven consumers in "
        "cli_handlers.py (run_plan's call site, count_applicable, the "
        "generic renderer, the JSON artifact, the markdown table, "
        "build_db_diff, sys.exit(max(...))) -- count_applicable would "
        "render N greater than M (e.g. '8 of 7 ran')."
    )

    append_calls = [
        node
        for node in ast.walk(ast.Module(body=finally_try.finalbody, type_ignores=[]))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "results"
    ]
    assert append_calls == [], (
        "run_plan's cleanup-drain finally calls results.append(...) -- "
        "forbidden: the drain must never touch the list run_plan returns"
    )


def test_drain_swallowed_classes_match_constant():
    """The finally's per-callable wrapper's handler names EXACTLY the
    classes in `_UNLOCK_CLEANUP_SWALLOWED` -- resolved from the AST (not
    assumed), and its handler body contains no `Raise` (never re-raise
    from the finally)."""
    import firestarter.chip_test as chip_test_mod

    finally_try = _run_plan_finally_node()

    inner_tries = [
        node
        for node in ast.walk(ast.Module(body=finally_try.finalbody, type_ignores=[]))
        if isinstance(node, ast.Try)
    ]
    assert len(inner_tries) == 1, (
        f"expected exactly one nested try/except per drain iteration inside "
        f"the finally, found {len(inner_tries)}"
    )
    inner_try = inner_tries[0]
    assert len(inner_try.handlers) == 1, (
        f"expected exactly one except clause in the per-callable wrapper, "
        f"found {len(inner_try.handlers)}"
    )
    handler = inner_try.handlers[0]
    assert handler.type is not None, (
        "the per-callable wrapper's except must not be bare"
    )

    handler_type_source = ast.unparse(handler.type)
    resolved = eval(  # noqa: S307 -- trusted, static, in-repo source only
        handler_type_source, vars(chip_test_mod)
    )
    assert resolved == chip_test_mod._UNLOCK_CLEANUP_SWALLOWED, (
        f"the per-callable wrapper's except clause resolves to {resolved!r}, "
        f"expected it to name exactly _UNLOCK_CLEANUP_SWALLOWED "
        f"({chip_test_mod._UNLOCK_CLEANUP_SWALLOWED!r})"
    )

    raises_in_handler = [
        node
        for node in ast.walk(ast.Module(body=handler.body, type_ignores=[]))
        if isinstance(node, ast.Raise)
    ]
    assert raises_in_handler == [], (
        "the per-callable wrapper's except body contains a Raise -- the "
        "drain must never re-raise from the finally (D-10)"
    )


# ---------------------------------------------------------------------------
# LEG-09 criterion 3 (plan 133-04, D-11). Both cases are satisfied by
# registry behaviour: gate-closed-from-the-start -> sdp_lock is SKIPPED ->
# nothing registers -> sdp_unlock is never attempted; lock-ran-then-the-
# gate-closes -> the unlock is registered -> the drain still runs it.
# ---------------------------------------------------------------------------


def test_gate_closed_from_start():
    """LEG-09 criterion 3, case 1: an id step that CLOSES the gate (a
    chip-ID mismatch, `_id_step_closes_gate`'s real condition) leaves the
    following OP_SDP_LOCK step SKIPPED with `_DESTRUCTIVE_GATE_REASON`,
    `operator.sdp_lock` never called, and `operator.sdp_unlock` never
    called (nothing was locked, so there is nothing to unlock -- LEG-10's
    empty-registry no-op path).

    The mirror OPEN-gate scenario is what makes this non-vacuous: without
    it, "the unlock was never attempted" would be equally true of a
    mechanism that can NEVER attempt it at all (the vacuity trap this
    project's record warns about)."""
    closed_operator = _mock_operator(check_eprom_id=(False, None), sdp_lock=True)
    plan = _plan_with_steps(
        Step(op=OP_ID, supported=True, reason=""),
        Step(op=OP_SDP_LOCK, supported=True, reason=""),
    )

    results = run_plan(plan, closed_operator, _REAL_DB)

    lock_result = _result(results, OP_SDP_LOCK)
    assert lock_result.verdict == VERDICT_SKIPPED, (
        f"OP_SDP_LOCK verdict was {lock_result.verdict!r} with the gate "
        "closed from the start, expected SKIPPED"
    )
    assert lock_result.reason == _DESTRUCTIVE_GATE_REASON, (
        f"OP_SDP_LOCK's SKIPPED reason was {lock_result.reason!r}, expected "
        f"the standing _DESTRUCTIVE_GATE_REASON {_DESTRUCTIVE_GATE_REASON!r}"
    )
    closed_operator.sdp_lock.assert_not_called()
    closed_operator.sdp_unlock.assert_not_called()

    # Non-vacuity mirror: same plan, gate left OPEN -- sdp_lock IS called
    # and sdp_unlock IS called.
    open_operator = _mock_operator(sdp_lock=True)
    open_results = run_plan(plan, open_operator, _REAL_DB)
    open_lock_result = _result(open_results, OP_SDP_LOCK)
    assert open_lock_result.verdict == VERDICT_OK, (
        f"mirror (open-gate) OP_SDP_LOCK verdict was "
        f"{open_lock_result.verdict!r}, expected OK -- without this mirror "
        "the never-attempted claim above would be vacuously true of a "
        "mechanism that can never attempt it at all"
    )
    open_operator.sdp_lock.assert_called_once_with("M8720", ANY)
    open_operator.sdp_unlock.assert_called_once_with("M8720", ANY)


def test_lock_ran_then_gate_closes():
    """LEG-09 criterion 3, case 2: the lock runs FIRST and succeeds
    (registering its unlock), and a LATER id step closes the gate. The
    unlock STILL runs -- the registry drain does not consult
    `destructive_gate_closed` at all, so a later-closing gate can never
    skip an already-registered cleanup (this is exactly what LEG-10 exists
    to guarantee, and what would fail if OP_SDP_UNLOCK were ever added to
    _DESTRUCTIVE_OPS -- see the standing invariant asserted below)."""
    operator = _mock_operator(sdp_lock=True)
    # First id step: passing, so the gate stays open through the lock.
    # Second id step (after the lock): CLOSES the gate.
    operator.check_eprom_id.side_effect = [(True, 0x1234), (False, None)]
    plan = _plan_with_steps(
        Step(op=OP_ID, supported=True, reason=""),
        Step(op=OP_SDP_LOCK, supported=True, reason=""),
        Step(op=OP_ID, supported=True, reason=""),
    )

    results = run_plan(plan, operator, _REAL_DB)

    operator.sdp_lock.assert_called_once_with("M8720", ANY)
    assert operator.sdp_unlock.call_count == 1, (
        f"sdp_unlock was called {operator.sdp_unlock.call_count} time(s) "
        "after a lock-ran-then-gate-closes sequence, expected 1 -- the "
        "registered unlock must still drain even though a later gate "
        "closure would have skipped a plan-derived destructive step"
    )
    lock_result = _result(results, OP_SDP_LOCK)
    assert lock_result.verdict == VERDICT_OK, (
        f"OP_SDP_LOCK verdict was {lock_result.verdict!r}, expected OK -- "
        "the lock ran BEFORE the gate closed"
    )

    assert OP_SDP_UNLOCK not in _DESTRUCTIVE_OPS, (
        "OP_SDP_UNLOCK must stay OUT of _DESTRUCTIVE_OPS: were it a "
        "member, a closing gate could skip a plan-derived unlock step and "
        "ship a locked part to the caller (133-CONTEXT.md D-11, LEG-09)"
    )


# ---------------------------------------------------------------------------
# LEG-01/LEG-02/LEG-04 full-population proofs (v1.30 Phase 134, plan 134-03).
#
# `_allow_refuse_populations()` sources both lists from the production
# `sdp_capability_for_entry` predicate over the LIVE database -- the same
# way tests/test_sdp_db_invariant.py's own `_partition_0x0d` does -- never
# a hardcoded name list, so a future DB change that moves a chip between
# ALLOW/REFUSE is caught here too. `derive_plan`'s own emission is still
# proven against the two-argument `sdp_capability(name, db)` (the real
# derivation source, LEG-01) in the tests below -- this helper exists only
# to enumerate the populations cheaply (one DB pass, not one lookup per
# candidate name).
# ---------------------------------------------------------------------------


def _allow_refuse_populations() -> tuple[list[str], list[str]]:
    from firestarter.sdp_capability import sdp_capability_for_entry

    allow: list[str] = []
    refuse: list[str] = []
    for full in _REAL_DB.get_eproms():
        name = full["name"]
        allowed, _reason = sdp_capability_for_entry(full, name)
        (allow if allowed else refuse).append(name)
    return allow, refuse


def test_allow_refuse_populations_sum_to_full_and_allow_nonempty():
    """Population-count sanity: ALLOW and REFUSE sum to the FULL
    SDP-classified population (every chip in the live DB is classified one
    way or the other -- `sdp_capability` never refuses to answer) and
    ALLOW is non-empty -- both numbers derived from the live DB, never
    restated as literals in an assertion. Measured at plan time: 43 ALLOW /
    41 REFUSE / 84 total among the protocol-0x0D subset (comment only, not
    asserted as a literal here)."""
    all_eproms = _REAL_DB.get_eproms()
    allow, refuse = _allow_refuse_populations()

    assert len(allow) + len(refuse) == len(all_eproms), (
        f"ALLOW ({len(allow)}) + REFUSE ({len(refuse)}) != total DB entries "
        f"({len(all_eproms)}) -- some chip fell through uncategorised"
    )
    assert len(allow) > 0, "the ALLOW population must be non-empty"


def test_derive_plan_allow_population_emits_six_supported_ops():
    """LEG-01 (`-k "derive and allow"`): every ALLOW chip's
    write_scope="full" plan carries exactly the six ops of
    _SDP_LEG_STEP_ORDER, all supported=True. The count is asserted via
    len(_SDP_LEG_STEP_ORDER), never a literal 6 (P-08 -- the order tuple
    is the single source)."""
    allow, _refuse = _allow_refuse_populations()
    assert allow, "the ALLOW population must be non-empty (measured 43)"

    offenders = []
    for name in allow:
        plan = derive_plan(name, _REAL_DB, write_scope="full")
        leg_steps = [s for s in plan.steps if s.op in _SDP_LEG_STEP_ORDER]
        leg_ops = [s.op for s in leg_steps]
        if leg_ops != list(_SDP_LEG_STEP_ORDER) or not all(
            s.supported for s in leg_steps
        ):
            offenders.append((name, leg_ops, [s.supported for s in leg_steps]))

    assert not offenders, (
        f"{len(offenders)} ALLOW chip(s) did not derive the full "
        f"{len(_SDP_LEG_STEP_ORDER)}-op supported SDP leg (showing up to "
        f"5): {offenders[:5]}"
    )


def test_derive_plan_allow_adds_no_cli_option():
    """LEG-01: the SDP leg adds NO CLI option of its own.

    Asserted STRUCTURALLY by inspecting the registered Click command's
    `params` -- an exit-code-only check would pass vacuously even if an
    option were added with a harmless default.

    NARROWED (quick task 260822-aq6), recorded rather than silently
    relaxed: this assertion was written as `options == []` because at the
    time LEG-01's claim ("this leg needs no new option") and the state of
    the command ("zero options") were the same sentence. They are no longer.
    `--fast` -- unrelated to SDP, and a deliberate reversal of Phase 121
    D-05's zero-option surface -- now exists, so the absolute form would
    fail for a reason LEG-01 never claimed anything about. The assertion
    below keeps LEG-01's real content by pinning the option set EXACTLY:
    the leg still contributes nothing, and a future SDP option cannot slip
    in. The canonical home for the option surface itself is
    `tests/test_dev_test_cmd.py::TestZeroOptionSurface`.
    """
    import click

    import firestarter.cli_handlers as cli_handlers_mod

    options = [
        p for p in cli_handlers_mod.dev_test.params if isinstance(p, click.Option)
    ]
    assert [o.name for o in options] == ["fast"], (
        f"dev_test carries unexpected click.Option instance(s): "
        f"{[o.name for o in options]!r} -- LEG-01 requires the SDP leg to "
        "add no CLI option, so the only option here must be the one quick "
        "task 260822-aq6 introduced"
    )


def test_derive_plan_allow_flips_supported_when_sdp_capability_patched(monkeypatch):
    """LEG-01: `sdp_capability` is the DERIVATION source, not a coincidence
    -- patch it to return a REFUSE tuple for a chip that is REALLY ALLOW,
    and assert the derived SDP-leg steps flip to supported=False. This
    proves derivation, not coincidence: a `derive_plan` that happened to
    hardcode the same 43 names would not react to this patch at all."""
    import firestarter.chip_test as chip_test_mod

    allow, _refuse = _allow_refuse_populations()
    name = allow[0]
    allowed_real, _reason_real = sdp_capability(name, _REAL_DB)
    assert allowed_real is True, f"fixture setup error: {name} is not really ALLOW"

    monkeypatch.setattr(
        chip_test_mod,
        "sdp_capability",
        lambda chip_name, db: (False, "patched: forced REFUSE for this test"),
    )

    plan = derive_plan(name, _REAL_DB, write_scope="full")
    leg_steps = [s for s in plan.steps if s.op in _SDP_LEG_STEP_ORDER]
    assert len(leg_steps) == len(_SDP_LEG_STEP_ORDER)
    assert all(not s.supported for s in leg_steps), (
        f"{name} is really ALLOW, but with sdp_capability patched to "
        "return REFUSE, derive_plan's SDP-leg steps did not flip to "
        "supported=False -- derive_plan is not actually calling "
        "sdp_capability as its derivation source"
    )
    assert all(s.reason == "patched: forced REFUSE for this test" for s in leg_steps)


def test_derive_plan_refuse_population_emits_six_na_steps_with_reason():
    """LEG-02 (`-k "derive and refuse"`): every REFUSE chip's
    write_scope="full" plan carries the same six ops, all supported=False,
    each `reason` EQUAL TO `sdp_capability(name, db)[1]` (identity against
    the live function, never a substring match). Count pinned at
    len(_SDP_LEG_STEP_ORDER), never a literal 6."""
    _allow, refuse = _allow_refuse_populations()
    assert refuse, "the REFUSE population must be non-empty (measured 41)"

    offenders = []
    for name in refuse:
        plan = derive_plan(name, _REAL_DB, write_scope="full")
        leg_steps = [s for s in plan.steps if s.op in _SDP_LEG_STEP_ORDER]
        leg_ops = [s.op for s in leg_steps]
        _allowed, expected_reason = sdp_capability(name, _REAL_DB)
        if (
            leg_ops != list(_SDP_LEG_STEP_ORDER)
            or any(s.supported for s in leg_steps)
            or any(s.reason != expected_reason for s in leg_steps)
        ):
            offenders.append(name)

    assert not offenders, (
        f"{len(offenders)} REFUSE chip(s) did not derive six NA SDP-leg "
        f"steps carrying sdp_capability()'s own reason verbatim (showing "
        f"up to 5): {offenders[:5]}"
    )


def test_derive_plan_refuse_run_plan_reports_na_with_no_operator_call():
    """LEG-02: `run_plan` turns a REFUSE chip's six unsupported SDP-leg
    steps into VERDICT_NA with NO operator call at all -- run_plan's
    existing NA path, zero new machinery. M8720 is a measured REFUSE chip
    (protocol 0x08 != 0x0D)."""
    allowed, _reason = sdp_capability("M8720", _REAL_DB)
    assert allowed is False, "fixture setup error: M8720 is not really REFUSE"

    operator = _mock_operator()
    plan = derive_plan("M8720", _REAL_DB, write_scope="full")

    results = run_plan(plan, operator, _REAL_DB)

    leg_results = [r for r in results if r.op in _SDP_LEG_STEP_ORDER]
    assert len(leg_results) == len(_SDP_LEG_STEP_ORDER)
    assert all(r.verdict == VERDICT_NA for r in leg_results)
    operator.sdp_lock.assert_not_called()
    operator.sdp_unlock.assert_not_called()
    # M8720's write_scope="full" plan ALSO carries the shipped (non-SDP)
    # "write" step, which legitimately calls write_eprom `runs` (2) times --
    # asserting a blanket "never called" here would be wrong. The load-
    # bearing claim is that the SIX SDP-leg steps add NO further calls: the
    # write-shaped ones (write-baseline-b/a, write-inhibited, write-restored)
    # are unsupported/NA for a REFUSE chip, so they never reach
    # `_dispatch_sdp_leg`'s own `operator.write_eprom(...)` call at all.
    assert operator.write_eprom.call_count == 2, (
        f"write_eprom was called {operator.write_eprom.call_count} time(s), "
        "expected exactly 2 (the shipped write step's own runs=2) -- any "
        "more would mean an unsupported SDP-leg step reached the operator"
    )


def test_derive_plan_baseline_transition_ordering():
    """LEG-04 (`baseline_transition`): the ordering contract for an ALLOW
    chip's write_scope="full" plan -- write-baseline-b and
    write-baseline-a both appear STRICTLY BEFORE sdp-lock; write-inhibited
    appears strictly after sdp-lock and strictly before sdp-unlock;
    write-restored is the LAST step in the plan; both baseline directions
    are present (a single-direction baseline is what D-07 rejected).

    Why the B direction is load-bearing: pattern A is already what the
    shipped `write` step writes, so a chip already holding A with a DEAD
    write path would pass an A-only baseline -- the B direction is the
    entire discriminating power (proven end to end by
    test_dead_write_path_baseline_b_is_bad, above)."""
    allow, _refuse = _allow_refuse_populations()
    name = allow[0]
    plan = derive_plan(name, _REAL_DB, write_scope="full")
    ops = [s.op for s in plan.steps]

    assert OP_WRITE_BASELINE_B in ops and OP_WRITE_BASELINE_A in ops, (
        "both baseline directions must be present -- a single-direction "
        "baseline is what D-07 rejected"
    )
    assert ops.index(OP_WRITE_BASELINE_B) < ops.index(OP_SDP_LOCK)
    assert ops.index(OP_WRITE_BASELINE_A) < ops.index(OP_SDP_LOCK)
    assert ops.index(OP_WRITE_INHIBITED) > ops.index(OP_SDP_LOCK)
    assert ops.index(OP_WRITE_INHIBITED) < ops.index(OP_SDP_UNLOCK)
    assert ops[-1] == OP_WRITE_RESTORED, (
        f"write-restored must be the LAST step in the plan (D-06's whole "
        f"point -- it is the only step producing evidence the part was "
        f"left writable), got {ops[-1]!r}"
    )


# ---------------------------------------------------------------------------
# D-18's write_scope="none" proofs (v1.30 Phase 134, plan 134-03, Task 3).
# write_scope="none" is UNREACHABLE from `dev test` since Phase 121's
# reversal (`_resolve_write_scope` returns only "full"/"partial") -- these
# two tests are library/test surface, never a live gate.
# ---------------------------------------------------------------------------


def test_allow_write_scope_none_locks_six_sdp_leg_steps_and_moves_the_banner():
    """D-18: an ALLOW chip's write_scope="none" plan carries NONE of the
    six SDP-leg ops in `plan.steps` -- all six go to the advisory
    `locked_destructive` list instead (mirroring the shipped write/verify/
    erase treatment), each carrying a non-empty reason. These entries DO
    count toward `count_applicable`'s M (called here, never edited), so
    `n_ran < m_applicable` and the banner fires -- MEASURING D-18's stated
    polarity rather than merely asserting it in prose."""
    name = "AT28C256"
    allowed, _reason = sdp_capability(name, _REAL_DB)
    assert allowed is True, f"fixture setup error: {name} is not really ALLOW"

    plan = derive_plan(name, _REAL_DB, write_scope="none")
    ops = [s.op for s in plan.steps]
    leg_ops_in_steps = [op for op in ops if op in _SDP_LEG_STEP_ORDER]
    assert not leg_ops_in_steps, (
        f"write_scope='none' must OMIT the six SDP-leg ops from plan.steps "
        f"entirely (D-18); found: {leg_ops_in_steps}"
    )

    locked_leg_entries = [
        (op, reason)
        for op, reason in plan.locked_destructive
        if op in _SDP_LEG_STEP_ORDER
    ]
    assert len(locked_leg_entries) == len(_SDP_LEG_STEP_ORDER)
    assert {op for op, _r in locked_leg_entries} == set(_SDP_LEG_STEP_ORDER)
    assert all(reason for _op, reason in locked_leg_entries), (
        "every locked SDP-leg entry must carry a non-empty reason"
    )

    operator = _mock_operator()
    results = run_plan(plan, operator, _REAL_DB)
    counts = count_applicable(plan, results)
    assert counts.n_ran < counts.m_applicable, (
        f"D-18's stated polarity (the N-of-M banner fires) is not "
        f"measured: n_ran={counts.n_ran}, m_applicable={counts.m_applicable}"
    )


def test_refuse_write_scope_none_is_byte_identical_to_pre_phase134():
    """D-18 refinement (Claude's Discretion, taken on four measurements --
    134-CONTEXT.md D-18, recorded again in 134-03-SUMMARY.md): a REFUSE
    chip's write_scope="none" plan is BYTE-IDENTICAL to before this phase
    -- exactly the three shipped `locked_destructive` entries, and NO
    SDP-leg entries at all (neither a step nor a `locked_destructive`
    entry). This is the branch that keeps LEG-10's named proof
    (`test_empty_registry_noop`, above) green, and it is library/test
    surface only: `write_scope="none"` is unreachable from a real `dev
    test` run since Phase 121's reversal."""
    name = "M8720"
    allowed, _reason = sdp_capability(name, _REAL_DB)
    assert allowed is False, f"fixture setup error: {name} is not really REFUSE"

    plan = derive_plan(name, _REAL_DB, write_scope="none")
    assert [s.op for s in plan.steps] == ["id", "read", "blank-check"]
    assert plan.locked_destructive == [
        (OP_WRITE, 'write_scope="none": write omitted'),
        (OP_VERIFY, 'write_scope="none": verify omitted'),
        (OP_ERASE, 'write_scope="none": erase omitted'),
    ]


# ---------------------------------------------------------------------------
# LEG-12's pure hold-state derivation (v1.30 Phase 134, plan 134-04, Task 2,
# D-10/D-12/D-15). `pytest -k "hold"` selects every test below.
# ---------------------------------------------------------------------------


def test_hold_state_held_when_write_inhibited_is_ok():
    """`write-inhibited` verdict OK -> SDP_HOLD_HELD -- the inhibited write
    was correctly refused, so the part held its lock."""
    results = [StepResult(op=OP_WRITE_INHIBITED, verdict=VERDICT_OK, reason="")]
    value = sdp_hold_state(Plan(name="AT28C256", steps=[]), results)
    assert isinstance(value, str)
    assert value == SDP_HOLD_HELD


def test_hold_state_not_held_when_write_inhibited_is_bad():
    """`write-inhibited` verdict BAD -> SDP_HOLD_NOT_HELD -- the inhibited
    write WAS accepted; the lock leaked (LEG-06's shape)."""
    results = [
        StepResult(op=OP_WRITE_INHIBITED, verdict=VERDICT_BAD, reason="lock leaked")
    ]
    value = sdp_hold_state(Plan(name="AT28C256", steps=[]), results)
    assert isinstance(value, str)
    assert value == SDP_HOLD_NOT_HELD


@pytest.mark.parametrize("verdict", [VERDICT_NA, VERDICT_SKIPPED, VERDICT_MARGINAL])
def test_hold_state_not_run_for_na_skipped_marginal(verdict):
    """Anything short of a clean OK/BAD on `write-inhibited` renders the
    BARE `SDP_HOLD_NOT_RUN` token -- never HELD, never NOT-HELD, never a
    bare boolean, and (quick task 260822-hs, operator: "strip") never the
    result's own `reason` either, even though that `reason` stays intact
    on the in-memory `StepResult` and is non-empty here on purpose -- this
    pins that `sdp_hold_state()` does not read it at all on this route,
    not merely that it happens to be absent from the fixture."""
    reason = f"synthetic {verdict!r} reason for the hold-state test"
    results = [StepResult(op=OP_WRITE_INHIBITED, verdict=verdict, reason=reason)]
    value = sdp_hold_state(Plan(name="AT28C256", steps=[]), results)
    assert isinstance(value, str)
    assert value == SDP_HOLD_NOT_RUN, (
        f"verdict {verdict!r} produced {value!r}, expected the bare "
        f"{SDP_HOLD_NOT_RUN!r} token with no reason appended"
    )


def test_hold_state_not_run_when_step_absent_from_results():
    """Laundering route R6: the `write-inhibited` step is entirely ABSENT
    from `results` (the plan never derived it, or `run_plan` never reached
    it) -- the absence itself must still render the bare `SDP_HOLD_NOT_RUN`
    token, never raise, and never silently default to HELD. Quick task
    260822-hs stripped the reason this route used to carry (the
    fixed-prose `unreadable_state_caveat()` fallback); this exercises the
    step-absent input shape specifically, distinct from
    `test_hold_state_not_run_for_na_skipped_marginal`'s step-present
    inputs, even though both now collapse to the same bare return value."""
    value = sdp_hold_state(Plan(name="AT28C256", steps=[]), [])
    assert isinstance(value, str)
    assert value == SDP_HOLD_NOT_RUN


def test_hold_state_empty_reason_no_longer_falls_back_to_honesty_caveat():
    """When the `write-inhibited` result's own `reason` is empty (rather
    than the step being entirely absent), the return is still the bare
    `SDP_HOLD_NOT_RUN` token -- renamed from
    `test_hold_state_empty_reason_falls_back_to_honesty_caveat`: the
    fixed-prose fallback this test used to pin (composing
    `sdp_honesty.unreadable_state_caveat()`) was deleted along with the
    reason suffix by quick task 260822-hs (operator: "strip"). Kept as a
    distinct regression test from the R6/absent-step case above and the
    NA/SKIPPED/MARGINAL case with a non-empty reason: this is the
    step-present-but-empty-reason input shape, and it must not somehow
    resurrect the old fallback prose just because `reason` is falsy."""
    results = [StepResult(op=OP_WRITE_INHIBITED, verdict=VERDICT_SKIPPED, reason="")]
    value = sdp_hold_state(Plan(name="AT28C256", steps=[]), results)
    assert value == SDP_HOLD_NOT_RUN


def test_hold_state_always_returns_str_never_bool_or_none():
    """P-06 prevention 3, directly asserted: every branch's return value is
    a `str` -- never `True`/`False`/`None`, which would read as ground
    truth for a state this chip family cannot report."""
    for verdict in (
        VERDICT_OK,
        VERDICT_BAD,
        VERDICT_NA,
        VERDICT_SKIPPED,
        VERDICT_MARGINAL,
    ):
        results = [StepResult(op=OP_WRITE_INHIBITED, verdict=verdict, reason="x")]
        value = sdp_hold_state(Plan(name="AT28C256", steps=[]), results)
        assert isinstance(value, str), (
            f"verdict {verdict!r} produced a {type(value)!r}, expected str"
        )
    value = sdp_hold_state(Plan(name="AT28C256", steps=[]), [])
    assert isinstance(value, str), (
        f"the step-absent case produced a {type(value)!r}, expected str"
    )


def test_oracle_applicable_true_for_allow_chip_full_and_none_scope():
    """`sdp_oracle_applicable` is True for an ALLOW chip's plan whether the
    leg is a real supported step (write_scope="full"/"partial") or an
    advisory `locked_destructive` entry (write_scope="none", D-18)."""
    name = "AT28C256"
    allowed, _reason = sdp_capability(name, _REAL_DB)
    assert allowed is True, f"fixture setup error: {name} is not really ALLOW"

    full_plan = derive_plan(name, _REAL_DB, write_scope="full")
    assert sdp_oracle_applicable(full_plan) is True

    none_plan = derive_plan(name, _REAL_DB, write_scope="none")
    assert sdp_oracle_applicable(none_plan) is True


def test_oracle_applicable_false_for_refuse_chip_full_and_none_scope():
    """`sdp_oracle_applicable` is False for a REFUSE chip's plan -- its
    `write-inhibited` step IS present in `plan.steps` (LEG-02's NA path),
    but with `supported=False`, so it must not count as applicable."""
    name = "M8720"
    allowed, _reason = sdp_capability(name, _REAL_DB)
    assert allowed is False, f"fixture setup error: {name} is not really REFUSE"

    full_plan = derive_plan(name, _REAL_DB, write_scope="full")
    assert sdp_oracle_applicable(full_plan) is False

    none_plan = derive_plan(name, _REAL_DB, write_scope="none")
    assert sdp_oracle_applicable(none_plan) is False


# ---------------------------------------------------------------------------
# The baseline gate, cleanup de-registration, and the LEG-09 distinction
# (v1.30 Phase 134, plan 134-04, Task 3, D-08/D-11/D-20).
#
# THE SEVENTH ROUTE (LEG-17, VALIDATION.md non-vacuity obligation #6): the
# baseline gate proven below is a SEVENTH route to a non-running oracle, on
# top of research's R1-R6 (which plan 134-10 tests). Under D-08 + D-15 it
# fails CLOSED (exit 1 from the baseline BAD, or >= 2 via the NOT-RUN
# floor), so it is NOT a laundering route -- but it is tested in this same
# family and named as the seventh here so plan 134-10's six-route test does
# not read as exhaustive when it is not.
# ---------------------------------------------------------------------------


def test_baseline_gate_closes_dead_write_path_allow_chip_full_leg():
    """D-08/D-20, gh#20's shape: for AT28C256's write_scope="full" plan
    driven by `_dead_write_path_operator()`, `write-baseline-b` reports BAD
    (the write path never transitions the die) and the gate closes --
    `sdp-lock`, `write-inhibited`, `sdp-unlock` and `write-restored` all
    render SKIPPED, `operator.sdp_lock` is never called, and each SKIPPED
    reason carries the family fact (never the chip-ID gate's wording).

    THIRD-GENERATION accounting (Phase 153, ERASE-03/ERASE-04), both
    earlier generations kept visible rather than overwritten:

      Generation 1 (pre-260807-kaq, matching 134-CONTEXT.md D-20's own
      prose): blank-check was a real supported step here, counted in both
      M and N -- measured `n_ran=6, m_applicable=10`. `write-baseline-a`
      (unlike the four `_SDP_LEG_GATED_OPS` members) is NEVER itself gated
      by `baseline_gate_closed`: both baseline directions always run
      regardless of the gate's state, since they are what DECIDE it
      (D-08's own stickiness requirement). Against this fixture,
      `write-baseline-a` reports OK, so it counted as "ran" alongside the
      four shipped ops (read/blank-check/write/verify) and
      `write-baseline-b` itself: 4 + 2 = 6 ran, 4 skipped, out of 10
      applicable.

      Generation 2 (260807-kaq): blank-check flipped to NA for AT28C256
      (protocol 0x0D, case 3: no step in this plan can ever leave the
      device blank, since each page write auto-erases internally) --
      REMOVED from both the "ran" set and the applicable set.
      `m_applicable` dropped 10 -> 9 (3 shipped-supported
      [read/write/verify] + 6 SDP-leg-supported, since id, erase AND NOW
      blank-check were all NA) and `n_ran` dropped 6 -> 5
      (write-baseline-b/write-baseline-a plus the 3 shipped-supported ops
      that ran, minus blank-check).

      Generation 3 (THIS plan, Phase 153 ERASE-03/ERASE-04): restoring
      FLAG_CAN_ERASE on all 84 algorithm-13 rows makes erase a real
      supported, destructive step (index 4) that ACTUALLY RUNS and
      reports OK against `_dead_write_path_operator()` (whose
      `erase_eprom` always succeeds) -- erase is not gated by the SDP
      baseline gate at all (it precedes the SDP leg block entirely), so
      it always joins both sets regardless of the gate's state, exactly
      like `write-baseline-a` does. `m_applicable` rises 9 -> 10 (erase
      joins the applicable set) and `n_ran` rises 5 -> 6 (erase runs).
      `write-baseline-a`'s half of the accounting is UNCHANGED across all
      three generations, for the reason Generation 1 gives above.

      Recorded explicitly, not glossed: Generation 3's integers (10, 6)
      numerically COINCIDE with Generation 1's, but this is a composition
      coincidence, not a restoration -- blank-check is NA in both
      Generation 2 and 3 (never returns to either set); it is erase
      joining them, not blank-check, that produces the matching pair.
      These are the current, live-measured values, re-derived in this
      session against this commit's `chip_test.py` and
      `_dead_write_path_operator()` fixture (unchanged by this plan) --
      not a restoration of the stale 134-CONTEXT.md prose, which predates
      every generation recorded above.
    """
    name = "AT28C256"
    allowed, _reason = sdp_capability(name, _REAL_DB)
    assert allowed is True, f"fixture setup error: {name} is not really ALLOW"

    plan = derive_plan(name, _REAL_DB, write_scope="full")
    operator = _dead_write_path_operator()

    results = run_plan(plan, operator, _REAL_DB)

    baseline_b = _result(results, OP_WRITE_BASELINE_B)
    assert baseline_b.verdict == VERDICT_BAD, (
        f"fixture setup: write-baseline-b must report BAD, got {baseline_b.verdict!r}"
    )

    for gated_op in (OP_SDP_LOCK, OP_WRITE_INHIBITED, OP_SDP_UNLOCK, OP_WRITE_RESTORED):
        gated_result = _result(results, gated_op)
        assert gated_result.verdict == VERDICT_SKIPPED, (
            f"{gated_op!r} verdict was {gated_result.verdict!r}, expected "
            "SKIPPED once the baseline gate closed"
        )
        assert "no lock was emitted" in gated_result.reason, (
            f"{gated_op!r}'s SKIPPED reason {gated_result.reason!r} does "
            "not name the family fact 'no lock was emitted'"
        )
        assert _DESTRUCTIVE_GATE_REASON not in gated_result.reason, (
            f"{gated_op!r}'s SKIPPED reason wrongly contains the chip-ID "
            f"gate's own wording ({_DESTRUCTIVE_GATE_REASON!r}) -- D-08 "
            "forbids attributing a write-path closure to a chip-ID "
            "mismatch"
        )
    operator.sdp_lock.assert_not_called()

    erase_result = _result(results, OP_ERASE)
    assert erase_result.verdict == VERDICT_OK, (
        f"Phase 153: erase is now a real supported step that runs "
        f"regardless of the SDP baseline gate, got {erase_result.verdict!r}"
    )

    counts = count_applicable(plan, results)
    assert counts.n_ran == 6, (
        f"measured n_ran={counts.n_ran}, expected 6 -- Phase 153 raised "
        "this from 5 (erase is now a real supported step that runs); see "
        "this test's THIRD-GENERATION accounting docstring; do not change "
        "this to match a stale generation's figure without re-deriving "
        "the arithmetic"
    )
    assert counts.m_applicable == 10, (
        f"measured m_applicable={counts.m_applicable}, expected 10 "
        "(4 shipped-supported [read/write/verify/erase] + 6 "
        "SDP-leg-supported, since id AND blank-check are the only NA "
        "steps for this chip after Phase 153 restored FLAG_CAN_ERASE)"
    )


@pytest.mark.parametrize(
    "verdict", [VERDICT_BAD, VERDICT_MARGINAL, VERDICT_SKIPPED, VERDICT_NA]
)
def test_baseline_gate_closes_on_any_non_ok_verdict(verdict):
    """D-08: `_baseline_closes_sdp_gate` closes on ANY non-OK verdict --
    BAD, marginal, SKIPPED, NA -- strictly WIDER than
    `_id_step_closes_gate`'s narrower `(BAD, SKIPPED)` tuple. A contact
    fault (marginal) is as disqualifying as a proven-dead write path
    (BAD)."""
    result = StepResult(op=OP_WRITE_BASELINE_B, verdict=verdict, reason="synthetic")
    assert _baseline_closes_sdp_gate(result) is True, (
        f"_baseline_closes_sdp_gate did not close on verdict {verdict!r} -- "
        "the gate must be strictly wider than _id_step_closes_gate's "
        "(BAD, SKIPPED) tuple"
    )


def test_baseline_gate_stays_open_on_clean_ok():
    """Non-vacuity mirror for the parametrized closure test above: a clean
    OK baseline verdict must NOT close the gate -- without this, the
    closure claim would be equally true of a gate that always returns
    True regardless of input."""
    result = StepResult(op=OP_WRITE_BASELINE_B, verdict=VERDICT_OK, reason="")
    assert _baseline_closes_sdp_gate(result) is False


def test_baseline_gate_sticky_failing_b_then_passing_a_stays_closed():
    """D-08: sticky by construction. A failing `write-baseline-b` closes
    the gate; a SUBSEQUENT passing `write-baseline-a` must not reopen it
    -- both baseline directions always run regardless of the gate's state
    (they are what DECIDE it), so this is a genuine reopening opportunity,
    not a vacuous one."""
    operator = _dead_write_path_operator()
    plan = _plan_with_steps(
        Step(op=OP_WRITE_BASELINE_B, supported=True, reason=""),
        Step(op=OP_WRITE_BASELINE_A, supported=True, reason=""),
        Step(op=OP_SDP_LOCK, supported=True, reason=""),
    )

    results = run_plan(plan, operator, _REAL_DB)

    baseline_b = _result(results, OP_WRITE_BASELINE_B)
    baseline_a = _result(results, OP_WRITE_BASELINE_A)
    lock_result = _result(results, OP_SDP_LOCK)
    assert baseline_b.verdict == VERDICT_BAD, "fixture setup: baseline-b must fail"
    assert baseline_a.verdict == VERDICT_OK, (
        "fixture setup: baseline-a must PASS -- the whole point of this "
        "test is that a passing baseline-a does not reopen an "
        f"already-closed gate (measured verdict: {baseline_a.verdict!r})"
    )
    assert lock_result.verdict == VERDICT_SKIPPED, (
        f"sdp-lock verdict was {lock_result.verdict!r} after a failing "
        "write-baseline-b followed by a passing write-baseline-a -- the "
        "gate must stay CLOSED (sticky), never reopened (D-08)"
    )
    operator.sdp_lock.assert_not_called()


def test_leg09_destructive_gate_never_skips_the_explicit_unlock_step():
    """D-20's LEG-09 distinction, pinned as a NEW test -- NOT an edit to
    any Phase-133 named proof (`test_unlock_exempt_from_destructive`,
    `test_lock_ran_then_gate_closes`, `test_finally_drains_on_exception`,
    `test_empty_registry_noop`, `test_drain_continues_after_failure`,
    `test_drain_does_not_mutate_results` all stay byte-identical).

    LEG-09 is scoped EXCLUSIVELY to the *destructive* gate
    (`_DESTRUCTIVE_OPS` membership + `test_unlock_exempt_from_destructive`)
    -- a structurally DIFFERENT mechanism from the new *baseline* gate
    (D-08/D-20): `destructive_gate_closed` and `baseline_gate_closed` are
    two separate flags in `run_plan`, consulted by two separate guard
    clauses. D-20 widening the baseline gate to include `sdp-unlock` does
    NOT weaken LEG-09: a run where the lock ran OK and the *destructive*
    gate closes AFTER it (via a later id-check failure) must still run the
    explicit `sdp-unlock` step -- only the SEPARATE baseline gate may skip
    it, and the baseline gate is OPEN here (no baseline op ran at all)."""
    assert OP_SDP_UNLOCK not in _DESTRUCTIVE_OPS, (
        "OP_SDP_UNLOCK must stay OUT of _DESTRUCTIVE_OPS -- LEG-09's "
        "structural claim, re-pinned here alongside D-20's baseline-gate "
        "widening so the two mechanisms are never conflated"
    )

    operator = _mock_operator(sdp_lock=True, sdp_unlock=True)
    # First id step passes (gate stays open through the lock); second id
    # step (after the lock) CLOSES the *destructive* gate.
    operator.check_eprom_id.side_effect = [(True, 0x1234), (False, None)]
    plan = _plan_with_steps(
        Step(op=OP_ID, supported=True, reason=""),
        Step(op=OP_SDP_LOCK, supported=True, reason=""),
        Step(op=OP_ID, supported=True, reason=""),
        Step(op=OP_SDP_UNLOCK, supported=True, reason=""),
    )

    results = run_plan(plan, operator, _REAL_DB)

    unlock_result = _result(results, OP_SDP_UNLOCK)
    assert unlock_result.verdict == VERDICT_OK, (
        f"the explicit sdp-unlock step reported {unlock_result.verdict!r} "
        "-- a CLOSED destructive gate must never skip it (exactly what "
        "LEG-09 exists to prevent); only the SEPARATE baseline gate may "
        "skip it (D-20), and the baseline gate is OPEN in this scenario"
    )
    operator.sdp_unlock.assert_called_once_with("M8720", ANY)


def test_deregistration_completed_leg_unlocks_exactly_once():
    """RESEARCH §4.2 property 1: a completed leg (lock succeeds, explicit
    unlock step also succeeds) calls `operator.sdp_unlock` EXACTLY once --
    the registered cleanup is de-registered by the successful explicit
    unlock, so the `finally` drain does not ALSO call it (133 D-11
    rejected the both-paths shape precisely because of this double-count)."""
    operator = _mock_operator(sdp_lock=True, sdp_unlock=True)
    plan = _plan_with_steps(
        Step(op=OP_SDP_LOCK, supported=True, reason=""),
        Step(op=OP_SDP_UNLOCK, supported=True, reason=""),
    )

    run_plan(plan, operator, _REAL_DB)

    assert operator.sdp_unlock.call_count == 1, (
        f"sdp_unlock was called {operator.sdp_unlock.call_count} time(s) "
        "for a fully-completed leg, expected EXACTLY 1"
    )


def test_deregistration_interrupted_leg_still_unlocks_exactly_once_via_drain():
    """RESEARCH §4.2 property 2: a leg that raises after a successful lock
    but BEFORE the explicit unlock step (which never ran at all) still
    calls `operator.sdp_unlock` EXACTLY once, via the `finally` drain --
    mirroring Phase 133's `test_finally_drains_on_exception`, now proven
    against this plan's de-registration logic too."""
    operator = _mock_operator(sdp_lock=True)
    injected = ProgrammerNotFoundError(
        "134-04 injected escape between lock and the explicit unlock step"
    )
    operator.read_eprom.side_effect = injected
    plan = _plan_with_steps(
        Step(op=OP_SDP_LOCK, supported=True, reason=""),
        Step(op=OP_READ, supported=True, reason=""),
        Step(op=OP_SDP_UNLOCK, supported=True, reason=""),
    )

    with pytest.raises(ProgrammerNotFoundError) as excinfo:
        run_plan(plan, operator, _REAL_DB)

    assert excinfo.value is injected
    assert operator.sdp_unlock.call_count == 1, (
        f"sdp_unlock was called {operator.sdp_unlock.call_count} time(s), "
        "expected EXACTLY 1 -- the explicit unlock step never ran, so "
        "only the drain's retained registration should fire"
    )


def test_deregistration_failed_explicit_unlock_retries_via_drain_twice():
    """RESEARCH §4.2 property 3: a FAILED explicit unlock step (non-OK
    verdict) must leave the registered handle in place, so the `finally`
    drain retries it -- `operator.sdp_unlock` is called TWICE (once
    explicitly, once from the drain retry), never silently once."""
    operator = _mock_operator(sdp_lock=True)
    operator.sdp_unlock.return_value = False  # the explicit step reports BAD
    plan = _plan_with_steps(
        Step(op=OP_SDP_LOCK, supported=True, reason=""),
        Step(op=OP_SDP_UNLOCK, supported=True, reason=""),
    )

    results = run_plan(plan, operator, _REAL_DB)

    assert operator.sdp_unlock.call_count == 2, (
        f"sdp_unlock was called {operator.sdp_unlock.call_count} time(s), "
        "expected 2 -- a FAILED explicit unlock must leave the registered "
        "handle in place so the finally drain retries it"
    )
    unlock_result = _result(results, OP_SDP_UNLOCK)
    assert unlock_result.verdict == VERDICT_BAD, (
        f"the explicit sdp-unlock step's own verdict was "
        f"{unlock_result.verdict!r}, expected BAD (operator.sdp_unlock "
        "returned False)"
    )


# ---------------------------------------------------------------------------
# Region-scoped read-back (quick task 260821-wna, Task 4, finding M-2/M-3):
# `_dispatch_sdp_leg`'s length gate is only a REAL gate once the read-back is
# region-scoped and sliced -- these two legs prove it against doubles that
# behave like real hardware rather than a region-sized-by-construction Mock.
# ---------------------------------------------------------------------------


def test_sdp_leg_length_gate_passes_against_a_full_size_readback_double():
    """The length gate must pass when the double returns a FULL-SIZE image
    (finding M-2): before this task, the gate was only satisfiable by a
    double whose read-back happened to return exactly `region_length`
    bytes. `_read_region`'s absolute-offset slice is what makes a
    whole-device-sized read-back a genuine pass, not merely a double that
    conveniently matches."""
    region = _DEFAULT_REGION
    a = generate_pattern(*region)
    device_size = 32768

    def _full_size_read(name, eprom_data, output_file=None, **kwargs):
        if output_file is not None:
            # A real chip's whole-device read-back: `a` at offset 0,
            # zero-padded out to the full device size.
            payload = a + b"\x00" * (device_size - len(a))
            Path(output_file).write_bytes(payload)
        return True

    operator = Mock(spec=_OPERATOR_METHODS)
    operator.check_eprom_id.return_value = (True, 0x1234)
    operator.check_eprom_blank.return_value = True
    operator.erase_eprom.return_value = True
    operator.verify_eprom.return_value = True
    operator.write_eprom.return_value = True
    operator.read_eprom.side_effect = _full_size_read

    result = _dispatch_sdp_leg(OP_WRITE_INHIBITED, "M8720", {}, operator)

    assert result.verdict == VERDICT_OK, (
        f"expected OK against a full-size read-back double, got "
        f"{result.verdict!r}: {result.reason}"
    )


def test_sdp_leg_readback_reproduces_absolute_offset_seek():
    """A double that writes its payload at the requested ABSOLUTE offset
    (finding M-3), not offset 0, must still satisfy the oracle at a
    non-zero region start -- proving the read-back is genuinely
    region-scoped rather than merely reading from a fixed offset 0 that
    happens to coincide with every currently-shipped leg region.
    `OP_WRITE_BASELINE_A`'s oracle is the simple symmetric case (write A,
    expect A back) -- it needs no simulated SDP-lock behaviour, unlike
    `OP_WRITE_INHIBITED`, so `FakeChip`'s plain overwrite semantics satisfy
    it directly.
    """
    from .fake_chip import FakeChip

    start, length = 0x2000, 256
    chip = FakeChip.non_uv(0x4000)
    step = Step(op=OP_WRITE, supported=True, reason="", write_region=(start, length))

    result = _dispatch_sdp_leg(OP_WRITE_BASELINE_A, "M8720", {}, chip, step=step)

    assert result.verdict == VERDICT_OK, (
        f"expected OK against a real absolute-offset double at a non-zero "
        f"start, got {result.verdict!r}: {result.reason}"
    )
    read_calls = [c for c in chip.calls if c[0] == "read_eprom"]
    assert read_calls and any(c[1]["address_str"] == "0x2000" for c in read_calls), (
        read_calls
    )
