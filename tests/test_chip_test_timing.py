"""Per-step timing capture (schema 1.5, 2026-08-21).

The operator asked for `dev test` timings to be captured, presented and
filed. `tests/test_diagnostic_report.py` pins the presentation half (JSON
key, console cell, `steps total` row, fingerprint immunity); this module
pins the CAPTURE half in `chip_test._run_step`.

These are real measurements against a deliberately-slowed mock operator,
not plumbing assertions: a test that only checked `duration_s is not None`
would pass just as happily against a hardcoded `0.0`.

The mean-fold tests below join this module for the same reason: they pin
`_aggregate_cycle_results`'s presentation half of `duration_s` -- the mean
over the cycles that ran (RPT-D1) -- against real per-cycle measurements
rather than hand-built `StepResult`s alone, so a fold that silently kept
summing would redden here too.
"""

from __future__ import annotations

import time
from unittest.mock import Mock

from firestarter.chip_test import (
    _RAN_VERDICTS,
    OP_READ,
    VERDICT_NA,
    Step,
    StepResult,
    _aggregate_cycle_results,
    _run_step,
    derive_plan,
    run_plan,
)
from firestarter.database import EpromDatabase
from firestarter.eprom_operations import EpromOperator

# `skip_local_override=True` so a developer's ~/.firestarter/database.json
# cannot change what this module measures -- matches
# tests/test_chip_test_sdp_leg.py's own `_REAL_DB`.
_REAL_DB = EpromDatabase(skip_local_override=True)

# Slow enough to dwarf scheduler jitter, short enough not to drag the suite.
_SLEEP_S = 0.20


def _operator(*, id_sleep: float = 0.0) -> Mock:
    """A clean-path operator whose `check_eprom_id` optionally sleeps."""

    def check_id(*_args: object, **_kwargs: object) -> tuple[bool, None]:
        if id_sleep:
            time.sleep(id_sleep)
        return True, None

    op = Mock(spec=EpromOperator)
    op.check_eprom_id.side_effect = check_id
    op.check_eprom_blank.return_value = True
    op.read_eprom.return_value = True
    op.verify_eprom.return_value = True
    op.erase_eprom.return_value = True
    op.write_eprom.return_value = True
    op.sdp_lock.return_value = True
    op.sdp_unlock.return_value = True
    return op


def test_duration_measures_real_elapsed_time():
    """A step whose operator call sleeps `_SLEEP_S` records AT LEAST that
    long -- the timer wraps the real dispatch, it does not stamp a constant.

    Asserts a lower bound only. An upper bound would make this test flaky
    on a loaded CI runner, and over-reporting is not the failure mode worth
    guarding: a broken timer reports zero, not too much.
    """
    plan = derive_plan("w29c020", _REAL_DB, write_scope="full")
    results = run_plan(plan, _operator(id_sleep=_SLEEP_S), _REAL_DB)

    id_result = next(r for r in results if r.op == "id")
    assert id_result.duration_s is not None
    assert id_result.duration_s >= _SLEEP_S


def test_fast_steps_are_not_credited_with_the_slow_step_time():
    """Each step is timed independently -- a slow `id` step must not inflate
    the `read` step that follows it. Guards against a timer anchored once at
    run start instead of per step.
    """
    plan = derive_plan("w29c020", _REAL_DB, write_scope="full")
    results = run_plan(plan, _operator(id_sleep=_SLEEP_S), _REAL_DB)

    id_result = next(r for r in results if r.op == "id")
    others = [
        r
        for r in results
        if r.op != "id" and r.verdict in _RAN_VERDICTS and r.duration_s is not None
    ]
    assert others, "expected at least one other step that ran"
    for r in others:
        assert r.duration_s < id_result.duration_s


def test_steps_that_did_not_run_have_no_duration():
    """`NA`/`SKIPPED` steps keep `duration_s is None`.

    A `0.0` there would read as "ran, took no measurable time" rather than
    "never ran", and it would be summed into the `steps total` row.
    """
    plan = derive_plan("w29c020", _REAL_DB, write_scope="full")
    results = run_plan(plan, _operator(), _REAL_DB)

    not_run = [r for r in results if r.verdict not in _RAN_VERDICTS]
    assert not_run, "expected at least one NA/SKIPPED step on this chip"
    for r in not_run:
        assert r.duration_s is None, (r.op, r.verdict, r.duration_s)


def test_every_step_that_ran_carries_a_duration():
    """No step that ran is left unmeasured -- the wrapper covers every
    return path of the timed function, not just the happy one."""
    plan = derive_plan("w29c020", _REAL_DB, write_scope="full")
    results = run_plan(plan, _operator(), _REAL_DB)

    ran = [r for r in results if r.verdict in _RAN_VERDICTS]
    assert ran, "expected at least one step to run"
    for r in ran:
        assert r.duration_s is not None, (r.op, r.verdict)
        assert r.duration_s >= 0


def test_an_already_stamped_duration_is_not_overwritten(monkeypatch):
    """The wrapper fills `duration_s` only when the timed function left it
    unset, so a future dispatcher that measures its own inner work (e.g.
    excluding connection setup) can report that instead without this
    wrapper clobbering it."""
    sentinel = 12.5

    def fake(*_args: object, **_kwargs: object) -> StepResult:
        return StepResult(op="id", verdict="OK", duration_s=sentinel)

    import firestarter.chip_test as ct

    monkeypatch.setattr(ct, "_run_step_untimed", fake)
    result = _run_step(
        "w29c020",
        Step(op="id", supported=True, reason=""),
        _operator(),
        _REAL_DB,
        runs=1,
    )

    assert result.duration_s == sentinel


def test_a_not_run_verdict_is_not_stamped_even_when_slow(monkeypatch):
    """The gate is the VERDICT, not the clock: a step that took real time
    but reports `NA` still gets no duration, because the row it would feed
    is hidden and the total must not include it."""

    def slow_na(*_args: object, **_kwargs: object) -> StepResult:
        time.sleep(_SLEEP_S)
        return StepResult(op="erase", verdict=VERDICT_NA)

    import firestarter.chip_test as ct

    monkeypatch.setattr(ct, "_run_step_untimed", slow_na)
    result = _run_step(
        "w29c020",
        Step(op="erase", supported=True, reason=""),
        _operator(),
        _REAL_DB,
        runs=1,
    )

    assert result.duration_s is None


def test_the_fold_reports_the_mean_not_the_sum():
    """Two real per-cycle durations fold to their mean, not their sum --
    a real measurement, not a hand-built `StepResult` pair, so a fold that
    silently kept summing reddens here even if a plumbing-only test would
    not notice."""
    plan = derive_plan("w29c020", _REAL_DB, write_scope="full")
    op = _operator()
    r1 = run_plan(plan, op, _REAL_DB)
    r2 = run_plan(plan, op, _REAL_DB)

    id1 = next(r for r in r1 if r.op == "id")
    id2 = next(r for r in r2 if r.op == "id")
    assert id1.duration_s is not None
    assert id2.duration_s is not None

    folded = _aggregate_cycle_results([id1, id2], "id")
    expected_mean = round((id1.duration_s + id2.duration_s) / 2, 3)
    assert folded.duration_s == expected_mean
    assert folded.duration_s < round(id1.duration_s + id2.duration_s, 3)


def test_the_mean_equals_sum_over_count_for_a_slowed_pair():
    """The mean-over-count relation, computed from durations this test
    itself measured against a deliberately-slowed mock -- not an arithmetic
    identity checked against itself."""
    plan = derive_plan("w29c020", _REAL_DB, write_scope="full")
    slow_op = _operator(id_sleep=_SLEEP_S)
    results = run_plan(plan, slow_op, _REAL_DB)
    id_slow = next(r for r in results if r.op == "id")

    fast_op = _operator()
    results_fast = run_plan(plan, fast_op, _REAL_DB)
    id_fast = next(r for r in results_fast if r.op == "id")

    folded = _aggregate_cycle_results([id_slow, id_fast], "id")
    measured = [id_slow.duration_s, id_fast.duration_s]
    assert folded.duration_s == round(sum(measured) / len(measured), 3)


def test_a_fold_with_no_measured_duration_stays_none():
    """No cycle producing a duration yields `None`, never `0.0` -- a `0.0`
    would read as "ran and took no time" rather than "unmeasured"."""
    a = StepResult(op=OP_READ, verdict="OK", run_count=1, duration_s=None)
    b = StepResult(op=OP_READ, verdict="OK", run_count=1, duration_s=None)
    folded = _aggregate_cycle_results([a, b], OP_READ)
    assert folded.duration_s is None


def test_a_single_cycle_result_is_returned_unchanged():
    """A `--fast` run's `duration_s` is that one cycle's own measured
    duration, by identity through the existing single-result early return."""
    a = StepResult(op=OP_READ, verdict="OK", run_count=1, duration_s=1.234)
    folded = _aggregate_cycle_results([a], OP_READ)
    assert folded is a


def test_cycle_order_does_not_change_the_folded_mean():
    """Reversing the order of the two cycle results yields the identical
    `duration_s` -- IEEE-754 addition of two floats is exactly commutative
    at the default cycle count."""
    a = StepResult(op=OP_READ, verdict="OK", run_count=1, duration_s=1.111)
    b = StepResult(op=OP_READ, verdict="OK", run_count=1, duration_s=2.222)
    forward = _aggregate_cycle_results([a, b], OP_READ)
    reversed_fold = _aggregate_cycle_results([b, a], OP_READ)
    assert forward.duration_s == reversed_fold.duration_s
