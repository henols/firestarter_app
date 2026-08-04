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

References:
  - .planning/phases/133-sdp-leg-mechanism/133-01-PLAN.md
  - .planning/phases/133-sdp-leg-mechanism/133-CONTEXT.md D-08 (exception
    precedence), D-13 (no-op regression test), D-15 (this module)
  - .planning/phases/133-sdp-leg-mechanism/133-PATTERNS.md
    §tests/test_chip_test_sdp_leg.py
  - tests/test_chip_test.py :287, :793-825 (the harness this module copies)
  - tests/test_sdp_table_parity.py :300-341 (the non-vacuity idiom)
"""

from unittest.mock import ANY, Mock

import pytest

from firestarter.chip_test import (
    _DESTRUCTIVE_OPS,
    _MULTI_RUN_OPS,
    _SDP_OPS,
    OP_BLANK_CHECK,
    OP_ERASE,
    OP_ID,
    OP_READ,
    OP_SDP_LOCK,
    OP_SDP_UNLOCK,
    OP_VERIFY,
    OP_WRITE,
    OP_WRITE_PARTIAL,
    VERDICT_BAD,
    VERDICT_OK,
    Plan,
    Step,
    _dispatch_sdp,
    derive_plan,
    run_plan,
)
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
    _SDP_OPS), so a future eighth shipped op cannot silently escape this
    sentinel by omission.

    If this fails, a shipped op reached the new arm -- the arm was placed
    wrongly and criterion 4's zero-added-branching-cost claim is false
    (133-CONTEXT.md D-04)."""
    import firestarter.chip_test as chip_test_mod

    module_op_constants = {
        value
        for name, value in vars(chip_test_mod).items()
        if name.startswith("OP_") and isinstance(value, str)
    }
    shipped_op_set = module_op_constants - _SDP_OPS
    assert set(_SHIPPED_OP_STRINGS) == shipped_op_set, (
        f"_SHIPPED_OP_STRINGS {sorted(_SHIPPED_OP_STRINGS)} does not equal "
        f"the module's shipped op set {sorted(shipped_op_set)} (all OP_* "
        "constants minus _SDP_OPS) -- a shipped op was added to "
        "chip_test.py without extending this sentinel's enumeration "
        "(133-CONTEXT.md D-13b)"
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
