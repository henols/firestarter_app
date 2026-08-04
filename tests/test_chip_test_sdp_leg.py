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

References:
  - .planning/phases/133-sdp-leg-mechanism/133-01-PLAN.md
  - .planning/phases/133-sdp-leg-mechanism/133-CONTEXT.md D-08 (exception
    precedence), D-13 (no-op regression test), D-15 (this module)
  - .planning/phases/133-sdp-leg-mechanism/133-PATTERNS.md
    §tests/test_chip_test_sdp_leg.py
  - tests/test_chip_test.py :287, :793-825 (the harness this module copies)
  - tests/test_sdp_table_parity.py :300-341 (the non-vacuity idiom)
"""

from unittest.mock import Mock

from firestarter.chip_test import (
    OP_BLANK_CHECK,
    Plan,
    Step,
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

# CURRENT expectation -- byte-identical to _PRE_EDIT_PRECEDENCE_MATRIX at
# this commit. Plan 133-02 (D-08) is the one permitted to edit THIS
# constant, for exactly the rows D-08 intends to change (widening
# `_run_step`'s catch to include SerialError/HardwareOperationError while
# re-raising ProgrammerNotFoundError/FirmwareOutdatedError first) -- and
# 133-02 must add the changed row names to _INTENDED_PRECEDENCE_DELTA in
# the SAME commit, or test_precedence_matrix_delta_is_exactly_intended
# below turns RED.
_EXPECTED_PRECEDENCE_MATRIX = dict(_PRE_EDIT_PRECEDENCE_MATRIX)

# Starts EMPTY (133-CONTEXT.md D-08; 133-01-PLAN.md must_haves). Any row
# that changes between _PRE_EDIT_PRECEDENCE_MATRIX and
# _EXPECTED_PRECEDENCE_MATRIX without its exception-class name appearing
# here turns the suite RED -- this is the mechanism that proves the seven
# shipped ops' exception handling is unchanged rather than merely assumed.
_INTENDED_PRECEDENCE_DELTA: frozenset[str] = frozenset()


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
