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
"""

from __future__ import annotations

import firestarter.chip_test as chip_test
from tests.plan_corpus import (
    PART_NUMBERS,
    REAL_DB,
    all_rows,
    mock_operator,
    plan_corpus,
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
