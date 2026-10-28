"""Per-step timing capture (schema 1.5, 2026-08-21).

The operator asked for `dev test` timings to be captured, presented and
filed. `tests/test_diagnostic_report.py` pins the presentation half (JSON
key, console cell, `steps total` row, fingerprint immunity); this module
pins the CAPTURE half in `chip_test._run_step`.

These tests pin the stamping rules: which steps carry a duration, and
that `_aggregate_cycle_results` folds the per-cycle durations to their
mean (RPT-D1). No test reads a real clock or sleeps.
"""

from __future__ import annotations

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
# cannot change what this module tests -- matches
# tests/test_chip_test_sdp_leg.py's own `_REAL_DB`.
_REAL_DB = EpromDatabase(skip_local_override=True)


def _operator() -> Mock:
    """A clean-path operator."""
    op = Mock(spec=EpromOperator)
    op.check_eprom_id.return_value = (True, None)
    # 202-05 D-10: check_eprom_blank now returns an int (0 == blank).
    op.check_eprom_blank.return_value = 0
    op.read_eprom.return_value = True
    # 202-01 D-10: verify_eprom now returns an int (0 == match).
    op.verify_eprom.return_value = 0
    op.erase_eprom.return_value = True
    op.write_eprom.return_value = True
    op.sdp_lock.return_value = True
    op.sdp_unlock.return_value = True
    return op


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


def test_a_not_run_verdict_is_not_stamped(monkeypatch):
    """The gate is the VERDICT, not the clock: a step that reports `NA`
    gets no duration, because the row it would feed is hidden and the
    total must not include it."""

    def na(*_args: object, **_kwargs: object) -> StepResult:
        return StepResult(op="erase", verdict=VERDICT_NA)

    import firestarter.chip_test as ct

    monkeypatch.setattr(ct, "_run_step_untimed", na)
    result = _run_step(
        "w29c020",
        Step(op="erase", supported=True, reason=""),
        _operator(),
        _REAL_DB,
        runs=1,
    )

    assert result.duration_s is None


def test_the_fold_reports_the_mean_not_the_sum():
    """Two per-cycle durations fold to their mean, not their sum."""
    a = StepResult(op=OP_READ, verdict="OK", run_count=1, duration_s=1.0)
    b = StepResult(op=OP_READ, verdict="OK", run_count=1, duration_s=3.0)
    folded = _aggregate_cycle_results([a, b], OP_READ)
    assert folded.duration_s == 2.0


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
