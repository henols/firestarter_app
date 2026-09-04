"""
Phase 175 Plan 01 (PRUNE-06) -- one structural claim, carried end to end: a
`Plan` never emits a write with no verify behind it.

**What this module adds that `test_op_registration_parity.py` does not
already cover.** That module is already a total, fail-closed `(op,
registry)` gate over this same 13-op vocabulary, with an import-time
`assert len(_ALL_OPS) == 13` that makes a fourteenth op fail collection of
that module outright. This module's new axis is the **verify disposition**
-- for each op, whether a write it performs needs a verify oracle behind it
and, when it does not, the named reason why. That is a distinct question
from "is this op accounted for in the dispatch/derivation registries",
which is what the parity module already answers; this module is not a
duplicate of it. Separately, `tests/test_chip_test.py:1453-1476`
(`test_cycle_block_bounds_matches_each_family_plan_shape`) already pins
`cycle_block_bounds`'s block contents for four named chips one at a time;
this module generalizes the same production helper across the whole
database rather than restating those four chips' shapes.

**The anti-vacuity rule, stated up front.** The trap this module exists to
avoid is the one `tests/test_erase_flag_invariants.py`'s own docstring
names: a selector that visits the wrong level of `db.proms`, or a sweep
whose corpus turns out to have zero rows, makes every downstream assertion
built on it pass vacuously -- the sweep reads green while proving nothing.
Every sweep in this module therefore asserts its own corpus size as an
absolute, pinned number before it asserts anything about violations, so a
sweep that silently visited zero rows cannot read green.

**Why the exemption bucket is the deliverable.** `write_verify_violations`
below is three lines; the reason each of the eleven non-write ops does not
need a verify oracle is the actual work, and it is written down in
`EXEMPT_REASONS` rather than left implicit. The load-bearing case is
`OP_WRITE_INHIBITED`: its read-back **is** its oracle, and that read-back
deliberately expects INEQUALITY, not equality --
`_dispatch_sdp_leg` sets `source_payload, expected_readback = pattern_b,
pattern_a` at `firestarter/chip_test.py:3291-3296`, meaning the step writes
one pattern but expects to read back the OTHER one (a leaked lock is what
would make it read back the pattern it wrote). A naive "every write needs a
verify" predicate does not merely over-fire on the ~40 chips carrying a
live SDP leg; if it were satisfied by attaching an `OP_VERIFY` behind
`write-inhibited`, that verify would assert the opposite of what the step
is trying to prove.

**Why the cycle-block rule is not re-implemented.** D-02 requires this
module to call the production `cycle_block_bounds` helper rather than
re-derive its own notion of "the same block". Measured directly: the
helper's own docstring lists the UV plan shape as `blank-check,
[write, verify]` -- a two-step block -- but `derive_plan` emits the erase
step for a UV chip anyway, marked `supported=False` rather than omitted,
and `cycle_block_bounds` does not filter on `Step.supported` when it walks
`_CYCLE_BLOCK_OPS`. Measured against this plan's own corpus: all 540 UV
plans (270 distinct UV part numbers, swept at both `full` and `partial`)
carry a real, three-step cycle block of `write, verify, erase(NA)`, not the
two-step shape the helper's docstring family list describes. A rule built
by reading that docstring, rather than by calling the helper, would be
wrong on all 540 of them.

**Reachability.** A gate authored before the content it guards can be
unreachable and prove nothing, so every anti-vacuity leg below was
observed to fail before this module was trusted. Nine hand-built
counter-plans (`test_planted_counter_plan_with_no_verify_is_flagged`
through `test_an_unsupported_write_is_skipped_by_decision`) prove
`write_verify_violations` is sensitive on shapes the shipped corpus cannot
reach; three mutated-corpus legs
(`test_removing_the_verify_flags_every_write_bearing_plan`,
`test_an_unsupported_verify_flags_every_write_bearing_plan`,
`test_a_region_skewed_verify_flags_every_write_bearing_plan`) prove it is
sensitive on all 1,354 shipped plans, not on one hand-picked example. On
top of both, four deliberate weakenings of `write_verify_violations`
itself (return nothing; drop the `supported` conjunct; drop the three
field-equality conjuncts; turn the out-of-block violation into a skip)
were each applied in turn and each observed to turn this module RED before
being reverted -- the raw transcript of all four, plus the restored clean
run, is committed at
`.planning/phases/175-structural-sentinel-over-derive-plan/evidence/175-01-anti-vacuity-red-green.txt`.
"""

from __future__ import annotations

import dataclasses

import firestarter.chip_test as chip_test
from tests.plan_corpus import (
    PART_NUMBERS,
    REAL_DB,
    all_rows,
    mock_operator,
    plan_corpus,
    plan_with_steps,
    step,
)

REQUIRES_VERIFY: frozenset[str] = frozenset(
    {chip_test.OP_WRITE, chip_test.OP_WRITE_PARTIAL}
)

EXEMPT_REASONS: dict[str, str] = {
    chip_test.OP_ID: (
        "read-only: it compares the chip's reported identity against an "
        "expected value and mutates nothing, so it has no write to verify."
    ),
    chip_test.OP_READ: (
        "read-only, and its own output IS the result being judged -- there "
        "is nothing downstream of it to compare against."
    ),
    chip_test.OP_BLANK_CHECK: (
        "read-only, and it is itself an oracle for a preceding erase step "
        "(D-05) -- an oracle does not need an oracle of its own."
    ),
    chip_test.OP_VERIFY: (
        "verify IS the oracle this predicate requires other ops to have; "
        "it is not itself a write and needs no second verify behind it."
    ),
    chip_test.OP_ERASE: (
        "erase clears the device toward a known state rather than staging "
        "particular bytes, so a byte-compare verify has no expected buffer "
        "to compare against. Its oracle is the blank-check step "
        "`derive_plan` relocates to sit after it (D-05's own, "
        "separately-named leg covers that pairing; this predicate does "
        "not fold it in)."
    ),
    chip_test.OP_SDP_LOCK: (
        "changes the chip's software-data-protection state, not its "
        "contents -- the step that follows it reads back and confirms the "
        "protection actually engaged, which is that step's own oracle, "
        "not a byte-compare verify of a write this op never performed."
    ),
    chip_test.OP_SDP_UNLOCK: (
        "changes the chip's software-data-protection state, not its "
        "contents -- the step that follows it (write-restored) reads back "
        "and confirms the part is writable again, not this op's own verify."
    ),
    chip_test.OP_WRITE_BASELINE_B: (
        "write-shaped, but `_dispatch_sdp_leg` folds its own read-back "
        "confirmation inline rather than emitting a separate `OP_VERIFY` "
        "step -- this is the first of the SDP leg's two baseline writes, "
        "confirmed by an equal-pattern read-back inside the same op."
    ),
    chip_test.OP_WRITE_BASELINE_A: (
        "write-shaped, but `_dispatch_sdp_leg` folds its own read-back "
        "confirmation inline -- the second baseline write, establishing "
        "the opposite pattern, also confirmed by an equal-pattern "
        "read-back inside the same op."
    ),
    chip_test.OP_WRITE_INHIBITED: (
        "write-shaped, but its oracle is an inline read-back that "
        "deliberately expects INEQUALITY: `_dispatch_sdp_leg` writes "
        "pattern B but expects to read back pattern A (unchanged) at "
        "`firestarter/chip_test.py:3291-3296`, because a leaked lock is "
        "what would make the chip read back the pattern it just wrote. An "
        "`OP_VERIFY` behind this step would assert the opposite of the "
        "step's own intent."
    ),
    chip_test.OP_WRITE_RESTORED: (
        "write-shaped, but `_dispatch_sdp_leg` folds its own read-back "
        "confirmation inline -- this step re-establishes the baseline "
        "pattern after `sdp-unlock` and confirms it by an equal-pattern "
        "read-back inside the same op, evidence the part was left "
        "writable again."
    ),
}


def module_op_constants():
    """Every `OP_*` string constant `chip_test` defines, discovered at
    runtime rather than hardcoded -- the idiom copied from
    `tests/test_chip_test_sdp_leg.py:840-846`."""
    return {
        value
        for name, value in vars(chip_test).items()
        if name.startswith("OP_") and isinstance(value, str)
    }


def assert_total_partition(discovered):
    """Assert `REQUIRES_VERIFY` and `EXEMPT_REASONS` together account for
    every op in `discovered`, with no overlap and no empty reason.

    Deliberately does NOT assert `len(discovered) == 13` -- that census
    belongs to `test_op_census_is_thirteen` alone, so a fourteenth op fails
    THIS function's partition check rather than a count check that could be
    satisfied by coincidence."""
    union = REQUIRES_VERIFY | set(EXEMPT_REASONS)
    missing = discovered - union
    extra = union - discovered
    assert not missing and not extra, (
        f"the op vocabulary is not totally partitioned -- missing from "
        f"both buckets: {sorted(missing)}; present in a bucket but not in "
        f"the discovered vocabulary: {sorted(extra)}"
    )

    overlap = REQUIRES_VERIFY & set(EXEMPT_REASONS)
    assert not overlap, (
        f"op(s) present in BOTH buckets, which is not a partition: {sorted(overlap)}"
    )

    empty_reason_ops = [
        op for op, reason in EXEMPT_REASONS.items() if not reason.strip()
    ]
    assert not empty_reason_ops, (
        f"exempt op(s) with an empty or whitespace-only reason: {empty_reason_ops}"
    )


def write_verify_violations(plan):
    """Every write step in `plan.steps` with no supported, field-matching
    verify at a higher index inside the same production-defined cycle
    block.

    Calls `chip_test.cycle_block_bounds` once and never re-derives the
    block rule (D-02). A write whose index is not inside the returned
    half-open `[start, stop)` range is a VIOLATION, never a skip --
    `cycle_block_bounds` finds only the FIRST maximal run, so
    `[write, verify, sdp-lock, write, verify]` returns `(0, 2)` and the
    second write at index 3 sits entirely outside it; treating that as a
    skip would leave a second write unguarded. The shipped corpus carries
    exactly one write per plan, so this arm is unreachable on it today and
    is exercised by a hand-built counter-plan instead.

    An unsupported write is skipped by explicit decision, not by accident:
    a step that never calls the operator cannot be "a write with no
    oracle" in any observable sense. The shipped corpus contains zero
    unsupported write steps, so this arm is currently unreachable and is
    recorded as a measured fact, not left as an unexplained branch.
    """
    bounds = chip_test.cycle_block_bounds(plan.steps)
    violations = []
    for index, candidate_step in enumerate(plan.steps):
        if candidate_step.op not in REQUIRES_VERIFY or not candidate_step.supported:
            continue
        if bounds is None or not (bounds[0] <= index < bounds[1]):
            violations.append(
                (index, candidate_step.op, "write sits outside the cycle block")
            )
            continue
        _start, stop = bounds
        oracle = next(
            (
                later_step
                for later_step in plan.steps[index + 1 : stop]
                if later_step.op == chip_test.OP_VERIFY
                and later_step.supported
                and later_step.write_region == candidate_step.write_region
                and later_step.region_policy == candidate_step.region_policy
                and later_step.cycle_payload == candidate_step.cycle_payload
            ),
            None,
        )
        if oracle is None:
            violations.append(
                (
                    index,
                    candidate_step.op,
                    "no supported, field-matching verify in the block",
                )
            )
    return violations


def test_op_census_is_thirteen():
    """The measured census baked into this module's docstring and into
    `EXEMPT_REASONS` needs re-measuring if this ever fails."""
    discovered = module_op_constants()
    assert len(discovered) == 13, (
        f"measured {len(discovered)} OP_* string constants in chip_test.py, "
        f"expected 13: {sorted(discovered)}"
    )


def test_op_vocabulary_is_totally_partitioned():
    assert_total_partition(module_op_constants())
    assert len(REQUIRES_VERIFY) == 2, sorted(REQUIRES_VERIFY)
    assert len(EXEMPT_REASONS) == 11, sorted(EXEMPT_REASONS)


def test_a_fourteenth_op_fails_the_partition_closed(monkeypatch):
    """Roadmap success criterion 3, read literally: a future operation type
    omitted from the closure list cannot silently escape the check."""
    monkeypatch.setattr(chip_test, "OP_FUTURE_WRITE", "future-write", raising=False)

    try:
        assert_total_partition(module_op_constants())
    except AssertionError:
        pass
    else:
        raise AssertionError(
            "Non-vacuity failure: adding a fourteenth OP_* constant to the "
            "chip_test module namespace did not make assert_total_partition "
            "raise -- the partition is not fail-closed."
        )


def test_corpus_census_is_pinned():
    """The anti-empty floor every later sweep in this module rests on."""
    corpus = plan_corpus()
    assert len(all_rows(REAL_DB)) == 746, len(all_rows(REAL_DB))
    assert len(PART_NUMBERS) == 677, len(PART_NUMBERS)
    assert len(corpus) == 1354, len(corpus)
    total_steps = sum(len(plan.steps) for plan in corpus.values())
    assert total_steps == 16248, total_steps
    unsupported_steps = sum(
        1 for plan in corpus.values() for s in plan.steps if not s.supported
    )
    assert unsupported_steps == 9304, unsupported_steps


def test_no_shipped_plan_emits_a_write_without_a_verify():
    """Roadmap success criterion 2."""
    corpus = plan_corpus()
    assert len(corpus) == 1354, (
        f"corpus size drifted to {len(corpus)} before this sweep could run "
        "-- a sweep that visits the wrong number of rows proves nothing"
    )
    offenders = [
        (key, violations)
        for key, plan in corpus.items()
        if (violations := write_verify_violations(plan))
    ]
    assert not offenders, (
        f"{len(offenders)} of {len(corpus)} shipped plans emit a write "
        f"with no verify behind it; first ten: {offenders[:10]}"
    )


def test_no_shipped_plan_carries_an_unsupported_write():
    """Records `write_verify_violations`'s skip-an-NA-write arm as
    measured-unreachable on the shipped corpus, rather than leaving it
    untested."""
    corpus = plan_corpus()
    unsupported_writes = sum(
        1
        for plan in corpus.values()
        for s in plan.steps
        if s.op in REQUIRES_VERIFY and not s.supported
    )
    assert unsupported_writes == 0, unsupported_writes


def test_one_chip_run_plan_alignment_smoke():
    """Proves the shared `mock_operator` double is wired correctly before
    plan 175-04 builds a 1,354-plan `run_plan` sweep on it.
    `tests/test_chip_test.py:2612` is the shipped M8720 instance of the
    same alignment claim for a different chip; this leaves that one alone
    and cites it instead of restating it. `run_plan` with `runs < 2` and no
    `allow_single_run=True` returns a single `StepResult(op="__plan__")`
    (`firestarter/chip_test.py:1631-1642`), so the default `runs=2` used
    here is deliberate, not incidental."""
    plan = chip_test.derive_plan("AT28C256", REAL_DB, write_scope="full")
    results = chip_test.run_plan(plan, mock_operator(), REAL_DB)
    assert len(results) == len(plan.steps), (
        f"run_plan returned {len(results)} results for {len(plan.steps)} "
        "steps -- a step was silently dropped or added"
    )


def test_planted_counter_plan_with_no_verify_is_flagged():
    """The first of nine hand-built counter-plans (Part A): each proves
    `write_verify_violations` is sensitive on a shape the shipped corpus
    either cannot reach at all, or reaches only by coincidence. Every one
    that has a clean sibling shape asserts the clean case is genuinely a
    premise, following the discipline `test_op_registration_parity.py:821`
    establishes.

    Roadmap success criterion 1."""
    plan = plan_with_steps(
        step(chip_test.OP_ID),
        step(chip_test.OP_READ),
        step(chip_test.OP_WRITE),
        step(chip_test.OP_ERASE),
    )
    violations = write_verify_violations(plan)
    assert len(violations) == 1, violations
    assert violations[0][2] == "no supported, field-matching verify in the block", (
        violations
    )


def test_a_second_write_outside_the_block_is_a_violation_not_a_skip():
    """`cycle_block_bounds` returns only the FIRST maximal run, so a second
    write past the end of that run must be a VIOLATION, never silently
    skipped -- this arm is unreachable on the shipped corpus, where every
    plan carries exactly one write, and is coded fail-closed for exactly
    that reason."""
    plan = plan_with_steps(
        step(chip_test.OP_WRITE),
        step(chip_test.OP_VERIFY),
        step(chip_test.OP_SDP_LOCK),
        step(chip_test.OP_WRITE),
        step(chip_test.OP_VERIFY),
    )
    bounds = chip_test.cycle_block_bounds(plan.steps)
    assert bounds == (0, 2), (
        f"fixture setup error: expected bounds (0, 2), got {bounds}"
    )

    violations = write_verify_violations(plan)
    assert violations == [
        (3, chip_test.OP_WRITE, "write sits outside the cycle block")
    ], violations


def test_a_verify_at_a_lower_index_is_not_an_oracle():
    plan = plan_with_steps(step(chip_test.OP_VERIFY), step(chip_test.OP_WRITE))
    violations = write_verify_violations(plan)
    assert len(violations) == 1, violations
    assert violations[0][0] == 1, violations


def test_a_verify_outside_the_block_is_not_an_oracle():
    """`(0, 1)` is what makes D-02's same-cycle-block reading stronger than
    anywhere-later-in-the-list: the verify here sits right after the write
    in `plan.steps`, but `sdp-lock` closes the block first."""
    plan = plan_with_steps(
        step(chip_test.OP_WRITE), step(chip_test.OP_SDP_LOCK), step(chip_test.OP_VERIFY)
    )
    bounds = chip_test.cycle_block_bounds(plan.steps)
    assert bounds == (0, 1), (
        f"fixture setup error: expected bounds (0, 1), got {bounds}"
    )

    violations = write_verify_violations(plan)
    assert len(violations) == 1, violations


def test_a_lone_write_is_flagged():
    plan = plan_with_steps(step(chip_test.OP_WRITE))
    bounds = chip_test.cycle_block_bounds(plan.steps)
    assert bounds == (0, 1), (
        f"fixture setup error: expected bounds (0, 1), got {bounds}"
    )

    violations = write_verify_violations(plan)
    assert len(violations) == 1, violations


def test_an_unsupported_verify_is_not_an_oracle():
    """D-03: a `supported=False` verify behind a live write IS a write with
    no oracle and must not pass merely because a step with the right op
    string is present."""
    plan = plan_with_steps(
        step(chip_test.OP_WRITE), step(chip_test.OP_VERIFY, supported=False)
    )
    violations = write_verify_violations(plan)
    assert len(violations) == 1, violations


def test_a_field_skewed_verify_is_not_an_oracle():
    """D-04: the verify must match the write on `write_region`,
    `region_policy` and `cycle_payload`. Three sub-cases, each a
    `write, verify` pair identical except for one field."""
    region_skewed = plan_with_steps(
        step(chip_test.OP_WRITE, write_region=(0, 10)),
        step(chip_test.OP_VERIFY, write_region=(1, 10)),
    )
    assert len(write_verify_violations(region_skewed)) == 1, (
        "write_region mismatch not flagged"
    )

    policy_skewed = plan_with_steps(
        step(chip_test.OP_WRITE, region_policy="fixed"),
        step(chip_test.OP_VERIFY, region_policy="uv-slot"),
    )
    assert len(write_verify_violations(policy_skewed)) == 1, (
        "region_policy mismatch not flagged"
    )

    payload_skewed = plan_with_steps(
        step(chip_test.OP_WRITE, cycle_payload="same"),
        step(chip_test.OP_VERIFY, cycle_payload="alternate"),
    )
    assert len(write_verify_violations(payload_skewed)) == 1, (
        "cycle_payload mismatch not flagged"
    )


def test_plans_with_no_write_are_vacuously_clean():
    empty_plan = plan_with_steps()
    assert chip_test.cycle_block_bounds(empty_plan.steps) is None
    assert write_verify_violations(empty_plan) == []

    no_write_plan = plan_with_steps(
        step(chip_test.OP_ID), step(chip_test.OP_READ), step(chip_test.OP_BLANK_CHECK)
    )
    assert chip_test.cycle_block_bounds(no_write_plan.steps) is None
    assert write_verify_violations(no_write_plan) == []

    real_plan = chip_test.derive_plan(PART_NUMBERS[0], REAL_DB, write_scope="none")
    assert not any(s.op in REQUIRES_VERIFY for s in real_plan.steps), (
        f"fixture setup error: {PART_NUMBERS[0]!r} at write_scope='none' "
        "must carry no step in REQUIRES_VERIFY"
    )
    assert chip_test.cycle_block_bounds(real_plan.steps) is None
    assert write_verify_violations(real_plan) == []


def test_an_unsupported_write_is_skipped_by_decision():
    """A decision, not an omission: a step that never calls the operator
    cannot be "a write with no oracle" in any observable sense. The shipped
    corpus carries zero unsupported write steps
    (`test_no_shipped_plan_carries_an_unsupported_write`), so this arm is
    currently unreachable on it."""
    plan = plan_with_steps(
        step(chip_test.OP_ID),
        step(chip_test.OP_READ),
        step(chip_test.OP_WRITE, supported=False),
        step(chip_test.OP_VERIFY),
        step(chip_test.OP_ERASE),
    )
    bounds = chip_test.cycle_block_bounds(plan.steps)
    assert bounds == (2, 5), (
        f"fixture setup error: expected bounds (2, 5), got {bounds}"
    )

    violations = write_verify_violations(plan)
    assert violations == [], violations


def _write_bearing_plans():
    """Shared selector for Part B's three mutated-corpus legs (D-09). Each
    of those legs proves `write_verify_violations` is sensitive on all
    1,354 shipped plans, not merely on one hand-built example, and each
    asserts this selector's size as an absolute number before mutating
    anything, so a sweep that silently visits zero rows cannot pass.

    Copy rule every one of those legs follows:
    `dataclasses.replace(plan, steps=[...])` yields a fresh list while
    sharing the untouched `Step` objects. `copy.copy(plan)` SHARES
    `plan.steps` and is forbidden -- the corpus is module-cached, so an
    in-place edit would corrupt every later test in the process. A
    per-field mutation goes through `dataclasses.replace(step, ...)`,
    never an attribute assignment on a live `Step`."""
    corpus = plan_corpus()
    return {
        key: plan
        for key, plan in corpus.items()
        if any(s.op in REQUIRES_VERIFY and s.supported for s in plan.steps)
    }


def test_removing_the_verify_flags_every_write_bearing_plan():
    write_bearing = _write_bearing_plans()
    assert len(write_bearing) == 1354, (
        f"write-bearing corpus size drifted to {len(write_bearing)} -- a "
        "sweep that visits zero rows must not pass"
    )

    unflagged = []
    for key, plan in write_bearing.items():
        mutant = dataclasses.replace(
            plan, steps=[s for s in plan.steps if s.op != chip_test.OP_VERIFY]
        )
        if not write_verify_violations(mutant):
            unflagged.append(key)
    assert not unflagged, (
        f"{len(unflagged)} of {len(write_bearing)} write-bearing plans were "
        f"NOT flagged after their verify step was removed; first ten: "
        f"{unflagged[:10]}"
    )


def test_an_unsupported_verify_flags_every_write_bearing_plan():
    """D-03, generalized across the whole corpus."""
    write_bearing = _write_bearing_plans()
    assert len(write_bearing) == 1354, (
        f"write-bearing corpus size drifted to {len(write_bearing)} -- a "
        "sweep that visits zero rows must not pass"
    )

    unflagged = []
    for key, plan in write_bearing.items():
        mutated_steps = [
            dataclasses.replace(s, supported=False)
            if s.op == chip_test.OP_VERIFY
            else s
            for s in plan.steps
        ]
        mutant = dataclasses.replace(plan, steps=mutated_steps)
        if not write_verify_violations(mutant):
            unflagged.append(key)
    assert not unflagged, (
        f"{len(unflagged)} of {len(write_bearing)} write-bearing plans were "
        f"NOT flagged after their verify step was marked unsupported; "
        f"first ten: {unflagged[:10]}"
    )


def test_a_region_skewed_verify_flags_every_write_bearing_plan():
    """D-04, generalized across the whole corpus."""
    write_bearing = _write_bearing_plans()
    assert len(write_bearing) == 1354, (
        f"write-bearing corpus size drifted to {len(write_bearing)} -- a "
        "sweep that visits zero rows must not pass"
    )

    unflagged = []
    for key, plan in write_bearing.items():
        mutated_steps = []
        for s in plan.steps:
            if s.op == chip_test.OP_VERIFY and s.write_region is not None:
                skewed_start = s.write_region[0] + 1
                mutated_steps.append(
                    dataclasses.replace(
                        s, write_region=(skewed_start, s.write_region[1])
                    )
                )
            else:
                mutated_steps.append(s)
        mutant = dataclasses.replace(plan, steps=mutated_steps)
        if not write_verify_violations(mutant):
            unflagged.append(key)
    assert not unflagged, (
        f"{len(unflagged)} of {len(write_bearing)} write-bearing plans were "
        f"NOT flagged after their verify step's write_region start was "
        f"skewed by one; first ten: {unflagged[:10]}"
    )


_28C_CARVE_OUT_REASON = (
    "protocol 0x0D (28C family) auto-erases per page during write; no "
    "step in this plan can ever leave the device blank"
)


def erase_blank_check_violations(plan):
    """Phase 175 Plan 02 (D-05): every executable erase step in
    `plan.steps` with no `OP_BLANK_CHECK` step at a higher index inside the
    same production-defined cycle block.

    Calls `chip_test.cycle_block_bounds` exactly once, precisely as
    `write_verify_violations` above does, so the two legs cannot drift on
    what a cycle block is. D-05 keeps this leg separately named and
    separately reasoned from `write_verify_violations` rather than folding
    both into one "every mutating op has an oracle" predicate, because
    Phase 177 cites this sentinel as its licence and needs a leg that maps
    1:1 onto PRUNE-06's wording.

    An erase whose index is not inside the returned half-open `[start,
    stop)` range is a VIOLATION, never a skip. The shipped corpus never
    reaches this arm -- an executable erase always follows a write, because
    `erase_is_executable` (`chip_test.py:639`) is gated by the same
    `write_execute` conjunct that gates the write step's own emission -- so
    it is exercised by two hand-built counter-plans instead
    (`test_an_erase_with_no_blank_check_at_all_is_flagged` and
    `test_a_blank_check_ahead_of_the_erase_is_not_its_oracle`).

    **This function asserts PRESENCE at a higher index, never
    supportedness, and that is deliberate.** Read literally as "an
    executable erase has a *working* blank-check behind it", the leg is
    RED on 81 chips at two scopes each -- 162 plans -- on the shipped,
    unmodified database: Phase 153 restored `FLAG_CAN_ERASE` on all 84
    algorithm-13 rows, so `erase_is_executable` is True for protocol
    `0x0D`, while `blank_check_step`'s case 3 (`chip_test.py:665-676`)
    marks the blank-check NA because that protocol auto-erases per page
    during write -- the exact reason string is pinned as
    `_28C_CARVE_OUT_REASON` above. Requiring `supported=True` is not an
    available option -- this phase cannot change product code -- so the
    leg is worded as presence, and the NA population is carved out,
    counted and reason-matched instead, by
    `test_the_28c_family_na_blank_check_carve_out_is_pinned` below.

    `tests/test_chip_test_blank_check_order.py`'s
    `test_at28c256_blank_check_moves_after_erase_but_stays_na` already
    pins this exact case for ONE chip with absolute index assertions (index
    5 and index 4). This module generalizes the pairing to the whole
    database, scoped to the production cycle block, and does not restate
    those index assertions.
    """
    bounds = chip_test.cycle_block_bounds(plan.steps)
    violations = []
    for index, candidate_step in enumerate(plan.steps):
        if candidate_step.op != chip_test.OP_ERASE or not candidate_step.supported:
            continue
        if bounds is None or not (bounds[0] <= index < bounds[1]):
            violations.append((index, "erase sits outside the cycle block"))
            continue
        _start, stop = bounds
        behind = any(
            later_step.op == chip_test.OP_BLANK_CHECK
            for later_step in plan.steps[index + 1 : stop]
        )
        if not behind:
            violations.append((index, "no blank-check behind the erase in the block"))
    return violations


def test_every_executable_erase_has_a_blank_check_behind_it_in_the_block():
    """Roadmap-adjacent D-05 leg. Generalizes
    `tests/test_chip_test_blank_check_order.py:130`
    (`test_at28c256_blank_check_moves_after_erase_but_stays_na`) from one
    hand-pinned chip's absolute Plan.steps indexes to all 677 chips at both
    scopes, scoped to the production cycle block rather than to raw
    positions. Does not restate that module's index-5/index-4 assertions."""
    corpus = plan_corpus()
    assert len(corpus) == 1354, (
        f"corpus size drifted to {len(corpus)} before this sweep could run "
        "-- a sweep that visits the wrong number of rows proves nothing"
    )
    offenders = [
        (key, violations)
        for key, plan in corpus.items()
        if (violations := erase_blank_check_violations(plan))
    ]
    assert not offenders, (
        f"{len(offenders)} of {len(corpus)} shipped plans carry a live "
        f"erase with no blank-check behind it in the block; first ten: "
        f"{offenders[:10]}"
    )


def test_live_erase_population_is_pinned():
    """The anti-empty floor the next two legs rest on."""
    corpus = plan_corpus()
    live_erase_plans = [
        key
        for key, plan in corpus.items()
        if any(s.op == chip_test.OP_ERASE and s.supported for s in plan.steps)
    ]
    assert len(live_erase_plans) == 608, (
        f"live-erase plan count drifted to {len(live_erase_plans)}, expected 608"
    )


def test_removing_the_blank_check_flags_every_live_erase_plan():
    """D-05's mutated-corpus sensitivity leg, the erase-leg counterpart to
    `test_removing_the_verify_flags_every_write_bearing_plan` above. Copy
    rule follows `_write_bearing_plans`' own docstring:
    `dataclasses.replace` on the steps list, never `copy.copy` and never an
    in-place `Step` mutation -- the corpus is module-cached and shared
    across every test in the process."""
    corpus = plan_corpus()
    live_erase = {
        key: plan
        for key, plan in corpus.items()
        if any(s.op == chip_test.OP_ERASE and s.supported for s in plan.steps)
    }
    assert len(live_erase) == 608, (
        f"live-erase plan count drifted to {len(live_erase)} -- a sweep "
        "that visits zero rows must not pass"
    )

    unflagged = []
    for key, plan in live_erase.items():
        mutant = dataclasses.replace(
            plan,
            steps=[s for s in plan.steps if s.op != chip_test.OP_BLANK_CHECK],
        )
        if not erase_blank_check_violations(mutant):
            unflagged.append(key)
    assert not unflagged, (
        f"{len(unflagged)} of {len(live_erase)} live-erase plans were NOT "
        f"flagged after their blank-check step was removed; first ten: "
        f"{unflagged[:10]}"
    )


def test_the_28c_family_na_blank_check_carve_out_is_pinned():
    """Phase 153's `FLAG_CAN_ERASE` restoration on all 84 algorithm-13 rows
    is the cause -- named here so an executor who re-derives the "obvious"
    stronger supportedness reading meets this docstring before meeting a
    162-plan RED they cannot fix. This is a pinned fact about the shipped
    database, not a defect. Pinned by absolute count AND reason string
    together, per D-05: the reason match says the population is the one we
    understand, and the absolute count is what catches a silent
    widening -- asserted as three separate assertions, not folded into
    one, so each can name its own drift."""
    corpus = plan_corpus()
    carveout_plans = []
    reasons = set()
    for key, plan in corpus.items():
        bounds = chip_test.cycle_block_bounds(plan.steps)
        if bounds is None:
            continue
        start, stop = bounds
        for index, candidate_step in enumerate(plan.steps):
            if (
                candidate_step.op != chip_test.OP_ERASE
                or not candidate_step.supported
                or not (start <= index < stop)
            ):
                continue
            behind = [
                s
                for s in plan.steps[index + 1 : stop]
                if s.op == chip_test.OP_BLANK_CHECK
            ]
            if behind and not any(s.supported for s in behind):
                carveout_plans.append(key)
                reasons.add(behind[0].reason)

    assert len(carveout_plans) == 162, (
        f"28C-family NA blank-check carve-out plan count drifted to "
        f"{len(carveout_plans)}, expected 162"
    )
    carveout_chips = {name for name, _scope in carveout_plans}
    assert len(carveout_chips) == 81, (
        f"28C-family NA blank-check carve-out chip count drifted to "
        f"{len(carveout_chips)}, expected 81"
    )
    assert reasons == {_28C_CARVE_OUT_REASON}, reasons


def test_an_erase_with_no_blank_check_at_all_is_flagged():
    plan = plan_with_steps(
        step(chip_test.OP_ID),
        step(chip_test.OP_READ),
        step(chip_test.OP_ERASE),
        step(chip_test.OP_VERIFY),
    )
    violations = erase_blank_check_violations(plan)
    assert len(violations) == 1, violations


def test_a_blank_check_ahead_of_the_erase_is_not_its_oracle():
    """The exact regression D-05 exists to catch: a future edit moving the
    blank-check back ahead of the erase. This fixture carries no write
    step, so `cycle_block_bounds` (which can only open on a write) returns
    `None` and the erase is flagged as sitting outside the cycle block --
    the same fail-closed arm `test_a_second_write_outside_the_block_is_a_
    violation_not_a_skip` exercises for the write leg, above."""
    plan = plan_with_steps(
        step(chip_test.OP_ID),
        step(chip_test.OP_READ),
        step(chip_test.OP_BLANK_CHECK),
        step(chip_test.OP_ERASE),
        step(chip_test.OP_VERIFY),
    )
    bounds = chip_test.cycle_block_bounds(plan.steps)
    assert bounds is None, (
        f"fixture setup error: expected bounds None (no write step opens "
        f"a cycle block), got {bounds}"
    )

    violations = erase_blank_check_violations(plan)
    assert violations == [(3, "erase sits outside the cycle block")], violations
