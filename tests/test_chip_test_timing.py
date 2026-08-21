"""Per-step timing capture (schema 1.5, 2026-08-21).

The operator asked for `dev test` timings to be captured, presented and
filed. `tests/test_diagnostic_report.py` pins the presentation half (JSON
key, console cell, `steps total` row, fingerprint immunity); this module
pins the CAPTURE half in `chip_test._run_step`.

These are real measurements against a deliberately-slowed mock operator,
not plumbing assertions: a test that only checked `duration_s is not None`
would pass just as happily against a hardcoded `0.0`.
"""

from __future__ import annotations

import time
from unittest.mock import Mock

from firestarter.chip_test import (
    _RAN_VERDICTS,
    VERDICT_NA,
    Step,
    StepResult,
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
    plan = derive_plan("w29c020", _REAL_DB, write_scope="none")
    results = run_plan(plan, _operator(id_sleep=_SLEEP_S), _REAL_DB)

    id_result = next(r for r in results if r.op == "id")
    assert id_result.duration_s is not None
    assert id_result.duration_s >= _SLEEP_S


def test_fast_steps_are_not_credited_with_the_slow_step_time():
    """Each step is timed independently -- a slow `id` step must not inflate
    the `read` step that follows it. Guards against a timer anchored once at
    run start instead of per step.
    """
    plan = derive_plan("w29c020", _REAL_DB, write_scope="none")
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
    plan = derive_plan("w29c020", _REAL_DB, write_scope="none")
    results = run_plan(plan, _operator(), _REAL_DB)

    not_run = [r for r in results if r.verdict not in _RAN_VERDICTS]
    assert not_run, "expected at least one NA/SKIPPED step on this chip"
    for r in not_run:
        assert r.duration_s is None, (r.op, r.verdict, r.duration_s)


def test_every_step_that_ran_carries_a_duration():
    """No step that ran is left unmeasured -- the wrapper covers every
    return path of the timed function, not just the happy one."""
    plan = derive_plan("w29c020", _REAL_DB, write_scope="none")
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
