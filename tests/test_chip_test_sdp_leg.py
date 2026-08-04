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
    VERDICT_BAD,
    VERDICT_MARGINAL,
    VERDICT_OK,
    VERDICT_SKIPPED,
    Plan,
    Step,
    _dispatch_sdp,
    _dispatch_sdp_leg,
    classify_fingerprint,
    derive_plan,
    generate_inhibited_pattern,
    generate_pattern,
    run_plan,
)
from firestarter.constants import FLAG_SKIP_SDP_UNLOCK
from firestarter.database import EpromDatabase
from firestarter.exceptions import (
    ChipNotFoundError,
    ChipNotImplementedError,
    EpromOperationError,
    FirmwareOutdatedError,
    HardwareOperationError,
    ProgrammerNotFoundError,
    SerialError,
    SerialTimeoutError,
)
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
#     133-CONTEXT.md D-08 names -- it is a SUBCLASS of EpromOperationError,
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


@pytest.mark.parametrize("exc_cls", [ProgrammerNotFoundError, FirmwareOutdatedError])
def test_run_fatal_escapes(exc_cls):
    """ProgrammerNotFoundError and FirmwareOutdatedError still ESCAPE
    run_plan unchanged (D-08) -- these are run-fatal host-setup conditions
    that belong to cli_handlers.py's @map_typed_errors mapper, not chip
    findings. Escape is asserted by object IDENTITY, not just class, so a
    re-wrap cannot pass.

    Standing invariant: SerialError.__subclasses__() is pinned to the
    measured three-class census. A fourth subclass added by a later phase
    without updating _run_step's re-raise clause would silently bypass it
    and become a false-green no-board report -- this assertion is what
    would catch that."""
    assert set(SerialError.__subclasses__()) == {
        SerialTimeoutError,
        ProgrammerNotFoundError,
        FirmwareOutdatedError,
    }, (
        "SerialError gained or lost a subclass since D-08 was measured -- "
        "_run_step's (ProgrammerNotFoundError, FirmwareOutdatedError) "
        "re-raise clause is only complete against the THREE-class census "
        "D-08 names; a new subclass here would silently fall through to "
        "the (SerialError, HardwareOperationError) degrade clause instead "
        "of escaping, turning a no-board/old-firmware run into a false "
        "BAD-step report (133-CONTEXT.md D-08)."
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
