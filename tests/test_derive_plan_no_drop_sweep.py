"""
Phase 175 Plan 04 (PRUNE-05) -- the execution half of D-10's no-drop proof:
every one of the 1,354 shipped plans, run through the real ``run_plan`` with
a hardware-free operator double, must yield exactly one ``StepResult`` per
``Plan.steps`` entry, and every unsupported step's result must carry the
``NA`` verdict.

**What this proves and what it does not.** It proves step and result
alignment (``len(results) == len(plan.steps)``) and NA-on-unsupported across
the whole shipped database. It does NOT exercise the write path on most
chips: the shared ``mock_operator``'s ``check_eprom_id`` returns a fixed
``(True, 0x1234)`` which mismatches most real chip IDs and closes the
destructive gate ``run_plan`` consults before every write, write-partial and
erase call, so roughly 2,545 of the corpus's 6,944 supported steps come back
``SKIPPED`` -- dominated by the write/write-partial/erase steps reporting a
chip-ID mismatch and by verify steps reporting no write target available. A
SKIPPED step still yields a ``StepResult``, so PRUNE-05 is unaffected by this
limit -- but this module says it plainly rather than overclaiming what the
sweep exercises.

**Why the frozen half in `tests/test_plan_shapes_drift.py` cannot be
simplified away.** An alignment-only proof cannot catch the change PRUNE-05
forbids: if a future phase prunes unsupported steps inside ``derive_plan``,
both ``Plan.steps`` and the results list this module sweeps shrink together,
alignment still holds, and the six SDP ballast steps on 637 chips silently
vanish from the dedup hash. This module and ``tests/test_plan_shapes_drift.py``
are both required; neither substitutes for the other. Put this here in full,
because this is the module a later reader auditing PRUNE-05 will find first.

**Why `runs=2`.** ``run_plan`` returns exactly one ``StepResult`` whose ``op``
is the literal ``__plan__`` and whose verdict is ``VERDICT_BAD`` when
``runs < 1 or (runs < 2 and not allow_single_run)`` (``chip_test.py:1631-1642``),
so a bare ``runs=1`` sweep fails alignment for a reason unrelated to
PRUNE-05. The sweep below uses ``run_plan``'s default ``runs=2`` deliberately,
and ``test_a_single_run_returns_only_the_plan_guard_result`` proves the guard
is a tested fact rather than a comment.

**Cost, the escape hatch, and the shape chosen.** The whole-database sweep
costs about 37 seconds against a 302-second suite -- roughly 12 percent, not
the 3 percent the discussion assumed against an inherited 737-second suite
figure that does not reproduce. It is one module, and
``pytest tests/ --deselect tests/test_derive_plan_no_drop_sweep.py`` is a
one-flag opt-out that invents no new marker convention: no ``markers`` key
exists in ``pyproject.toml`` and no ``mark.slow`` appears anywhere in
``tests/``. Running the whole-database sweep twice (once per assertion group)
was measured at over 70 seconds combined, so
``test_every_plan_yields_one_result_per_step`` and
``test_every_unsupported_step_result_carries_the_na_verdict`` below share one
cached sweep pass (``_run_whole_database_sweep``, mirroring the
``plan_corpus()`` caching idiom in ``tests/plan_corpus.py``) rather than each
re-running ``run_plan`` over all 1,354 plans independently. Correctness is
identical either way; this shape only avoids paying the 34-second cost twice.

**Anti-vacuity note (see Task 2's additions below the guard leg).** Both
sweep predicates -- `alignment_violations` and `na_verdict_violations` --
are proven sensitive on a pinned 40-plan slice, `SENSITIVITY_SLICE`, and
three deliberate weakenings of this module were each observed to turn it RED
before being reverted; the transcript is
`.planning/phases/175-structural-sentinel-over-derive-plan/evidence/175-04-no-drop-sweep.txt`.
"""

from __future__ import annotations

import dataclasses

import firestarter.chip_test as chip_test
from tests.plan_corpus import REAL_DB, mock_operator, plan_corpus


def alignment_violations(plan, results):
    """Empty when `len(results) == len(plan.steps)`; otherwise a single
    `(plan.name, len(plan.steps), len(results))` tuple naming the mismatch --
    a step silently dropped or added."""
    if len(plan.steps) != len(results):
        return [(plan.name, len(plan.steps), len(results))]
    return []


def na_verdict_violations(plan, results):
    """`(index, op, verdict)` for every step whose `supported` is False and
    whose positionally-corresponding result's verdict is not
    `chip_test.VERDICT_NA`. Guards the zip: a results list shorter or longer
    than `plan.steps` returns a single violation naming the length mismatch
    rather than letting a positional zip silently truncate the tail, which
    would let a dropped result on an unsupported step pass unnoticed."""
    if len(plan.steps) != len(results):
        return [
            (
                -1,
                "__length_mismatch__",
                f"{len(results)} results for {len(plan.steps)} steps",
            )
        ]
    violations = []
    for index, (plan_step, result) in enumerate(zip(plan.steps, results)):
        if not plan_step.supported and result.verdict != chip_test.VERDICT_NA:
            violations.append((index, plan_step.op, result.verdict))
    return violations


@dataclasses.dataclass(frozen=True)
class _SweepReport:
    plan_count: int
    step_count: int
    result_count: int
    unsupported_count: int
    alignment_violations: tuple
    na_violations: tuple


_SWEEP_CACHE: _SweepReport | None = None


def _run_whole_database_sweep() -> _SweepReport:
    """Run every plan in `plan_corpus()` through the real `run_plan` exactly
    once per pytest process, building and discarding each plan's results as
    the loop goes so peak memory stays flat rather than holding all 16,248
    `StepResult` objects at once. Cached module-globally (see module
    docstring's "Cost, the escape hatch, and the shape chosen") so the two
    whole-database test functions below share one sweep pass."""
    global _SWEEP_CACHE
    if _SWEEP_CACHE is None:
        corpus = plan_corpus()
        step_count = 0
        result_count = 0
        unsupported_count = 0
        align_violations: list = []
        na_violations: list = []
        for plan in corpus.values():
            results = chip_test.run_plan(plan, mock_operator(), REAL_DB)
            step_count += len(plan.steps)
            result_count += len(results)
            unsupported_count += sum(1 for s in plan.steps if not s.supported)
            align_violations.extend(alignment_violations(plan, results))
            na_violations.extend(na_verdict_violations(plan, results))
        _SWEEP_CACHE = _SweepReport(
            plan_count=len(corpus),
            step_count=step_count,
            result_count=result_count,
            unsupported_count=unsupported_count,
            alignment_violations=tuple(align_violations),
            na_violations=tuple(na_violations),
        )
    return _SWEEP_CACHE


def test_every_plan_yields_one_result_per_step():
    """The alignment half of PRUNE-05, over the whole shipped database at
    the default `runs=2`: 1,354 plans, 16,248 steps, 16,248 results, zero
    plans where `len(results) != len(plan.steps)`. The corpus size is
    asserted first, as an absolute, so a corpus that silently shrank to zero
    could not read green on the violations check that follows."""
    report = _run_whole_database_sweep()
    assert report.plan_count == 1354, (
        f"plan_corpus() returned {report.plan_count} plans, expected 1354 -- "
        "a sweep over the wrong count proves nothing about the whole database"
    )
    assert report.alignment_violations == (), (
        f"{len(report.alignment_violations)} plan(s) had a mismatched "
        f"result count -- a step was silently dropped or added: "
        f"{report.alignment_violations[:10]}"
    )
    assert report.result_count == 16248, (
        f"total result count was {report.result_count}, expected 16248 for "
        f"{report.step_count} total steps"
    )


def test_every_unsupported_step_result_carries_the_na_verdict():
    """The NA-on-unsupported half of PRUNE-05, over the whole shipped
    database: 9,304 of 9,304 `supported=False` steps produce a result whose
    verdict is `VERDICT_NA`. The unsupported count is asserted as an
    absolute, non-zero floor first -- a sweep that visited no unsupported
    steps would otherwise report zero violations and pass vacuously."""
    report = _run_whole_database_sweep()
    assert report.unsupported_count == 9304, (
        f"swept {report.unsupported_count} unsupported steps, expected 9304 "
        "-- the ballast population moved and must be re-measured"
    )
    assert report.na_violations == (), (
        f"{len(report.na_violations)} unsupported step(s) had a non-NA "
        f"verdict: {report.na_violations[:10]}"
    )


def test_a_single_run_returns_only_the_plan_guard_result():
    """Pitfall 4, as a tested fact rather than a comment: `run_plan(..., runs=1)`
    without `allow_single_run` returns exactly one `StepResult` whose `op` is
    the literal `__plan__` and whose `verdict` is `chip_test.VERDICT_BAD` --
    never one result per step. `allow_single_run=True` restores the normal
    one-result-per-step shape. This is why the sweep above uses the default
    `runs=2` rather than the cheaper `runs=1`."""
    plan = plan_corpus()[("M8720", "full")]

    guarded = chip_test.run_plan(plan, mock_operator(), REAL_DB, runs=1)
    assert len(guarded) == 1, (
        f"runs=1 without allow_single_run returned {len(guarded)} results, "
        "expected exactly 1 (the __plan__ guard result)"
    )
    assert guarded[0].op == "__plan__", (
        f"the single runs=1 result's op was {guarded[0].op!r}, expected "
        "the literal '__plan__'"
    )
    assert guarded[0].verdict == chip_test.VERDICT_BAD, (
        f"the single runs=1 result's verdict was {guarded[0].verdict!r}, "
        "expected VERDICT_BAD"
    )

    allowed = chip_test.run_plan(
        plan, mock_operator(), REAL_DB, runs=1, allow_single_run=True
    )
    assert len(allowed) == len(plan.steps), (
        f"runs=1, allow_single_run=True returned {len(allowed)} results for "
        f"{len(plan.steps)} steps -- expected one result per step"
    )
