"""
Phase 175 Plan 01 -- the shared corpus surface every sentinel module in this
phase imports from, rather than each building its own database instance or
its own operator double.

**REAL_DB and D-07.** One module-level `EpromDatabase(skip_local_override=True)`,
copied verbatim from `test_erase_flag_invariants.py:98` -- never
`EpromDatabase()`, which silently reads a developer's `~/.firestarter/database.json`
override and makes every count in this module machine-dependent.
D-07 fixes the sweep domain to the shipped database only: this module, and
every sentinel built on it, makes no claim about user-override entries.

**all_rows and the two-level trap.** `db.proms` maps a manufacturer key to a
*list* of chip records. A selector that scans the top level only
(`for row in db.proms: ...`) iterates manufacturer strings, never a chip
record, and every downstream filter built on it returns an empty set and
passes vacuously regardless of whether the real invariant holds. `all_rows`
descends two levels for exactly this reason and is copied verbatim from
`test_erase_flag_invariants.py:113-132`, including its
`-> list[tuple[str, dict]]` annotation, measured error-free at the mypy
watermark.

**PART_NUMBERS.** 746 rows collapse to 677 distinct `part_number` values
because 65 part numbers appear on more than one row (multiple manufacturer
listings, or database quirks that predate this phase). `derive_plan(name, db)`
resolves every one of those duplicates to the same plan, so the corpus below
is keyed by name, not by row -- sweeping by row would derive and discard the
same plan up to several times over for no added coverage.

**The single reachable scope and D-08/D-09 (181-02, 181-04).** Every name is
swept at exactly the ONE scope `dev test` itself would actually resolve for
it: `"partial"` for a UV chip, `"full"` otherwise (`dev_test`'s inlined
scope rule, measured equivalent to `Plan.is_uv` over all 677 names in
`evidence/181-02-derive-plan-equivalence.txt`). `write_scope="none"`
structurally omitted the write step from `Plan.steps` (retired in
181-04), so the write-to-verify rule was always vacuously true across
every `"none"` plan -- sweeping it would silently inflate a "zero
violations" count with rows that could never violate anything, which is
why it was never swept even when the corpus covered two scopes per name.
Sweeping `"full"` alongside a UV chip's actually-reachable `"partial"`
scope was the same kind of vacuity one level up: `dev test` never derives
a UV chip's `"full"` plan or a non-UV chip's `"partial"` plan, so a corpus
that swept both was proving properties about plans nothing ever runs.
Plan 181-04's operator adjudication (2026-09-09) narrows `derive_plan`'s
`write_scope` keyword to these two values with no default, rather than
dropping it entirely -- dropping it would have moved the write-op selector
onto `is_uv` and re-keyed 7 of the 19 frozen dedup hashes. This corpus
already sweeps the domain that survives either shape of that deletion.

**plan_corpus caching.** `derive_plan` over the whole database measures
under a second, but every sentinel module in this phase imports this corpus,
and pytest collects every one of those modules in the same process. The
built mapping is cached in a module-level private variable on first call and
returned unchanged on every later call, so the cost is paid once per pytest
process rather than once per importing module.

**mock_operator.** `Mock(spec=OPERATOR_METHODS)` is load-bearing: without
`spec=`, an out-of-spec attribute access on the double silently returns a
truthy `Mock` instead of raising, and a dispatch bug would read as a pass.
This is the fourth copy of this exact double in this tree -- the other three
are `tests/test_chip_test.py:1009`, `tests/test_chip_test_sdp_leg.py:239`
and `tests/test_chip_test_cycle.py:26` -- named here so the drift is visible
rather than discovered later by a fifth copy. The fixed `check_eprom_id`
return value `(True, 0x1234)` mismatches most real chips' actual chip IDs,
which closes the destructive gate `run_plan` consults before every write or
erase call and makes roughly 1,383 of the corpus's 3,472 supported steps
(181-02: re-measured against the single-reachable-scope corpus) come back
SKIPPED rather than actually dispatched. A sweep built on this double
proves step-count alignment and NA-on-unsupported; it does not exercise
the write path on most chips.

**plan_with_steps and step.** `Plan` and `Step` are plain dataclasses with
no builder of their own. `plan_with_steps` assembles a `Plan` from bare
`Step` objects, following `tests/test_chip_test.py:1093`. `step` is a thin
`Step` builder because `Step.reason` is a required positional and a
hand-built counter-plan reads badly without a name for it.
"""

from __future__ import annotations

from unittest.mock import Mock

from firestarter.chip_test import Plan, Step, derive_plan, is_uv_eprom
from firestarter.database import EpromDatabase

REAL_DB = EpromDatabase(skip_local_override=True)


def all_rows(db: EpromDatabase) -> list[tuple[str, dict]]:
    """Every (manufacturer, chip_record) pair in the database, exhaustively."""
    rows = []
    for manufacturer, chips in db.proms.items():
        for chip in chips:
            rows.append((manufacturer, chip))
    return rows


PART_NUMBERS = tuple(sorted({chip["part_number"] for _mfr, chip in all_rows(REAL_DB)}))

_PLAN_CORPUS_CACHE = None


def _reachable_scope(name: str) -> str:
    """`"partial"` for a UV chip, `"full"` otherwise -- the same rule
    `dev_test` inlines at its `derive_plan` call site, measured equivalent
    to `Plan.is_uv` over all 677 names (evidence/181-02-derive-plan-equivalence.txt)."""
    full = REAL_DB.get_eprom(name)
    return "partial" if full and is_uv_eprom(full) else "full"


def plan_corpus():
    """The whole sweep domain: every `PART_NUMBERS` name, each at its own
    single reachable scope, derived once and cached for the rest of the
    pytest process."""
    global _PLAN_CORPUS_CACHE
    if _PLAN_CORPUS_CACHE is None:
        _PLAN_CORPUS_CACHE = {
            name: derive_plan(name, REAL_DB, write_scope=_reachable_scope(name))
            for name in PART_NUMBERS
        }
    return _PLAN_CORPUS_CACHE


OPERATOR_METHODS = [
    "check_eprom_id",
    "read_eprom",
    "check_eprom_blank",
    "write_eprom",
    "verify_eprom",
    "erase_eprom",
    "sdp_lock",
    "sdp_unlock",
]


def mock_operator(**returns):
    """A bench-free `EpromOperator` double. See the module docstring for
    why this is the fourth copy of this exact shape in the tree, and for
    the fixed `check_eprom_id` return value's SKIPPED-step consequence."""
    op = Mock(spec=OPERATOR_METHODS)
    op.check_eprom_id.return_value = (True, 0x1234)
    op.read_eprom.return_value = True
    op.check_eprom_blank.return_value = True
    op.write_eprom.return_value = True
    # 202-01 D-10: verify_eprom now returns an int (0 == match); the
    # multi-run dispatch's `== 0` adapter reads this as success only at 0.
    op.verify_eprom.return_value = 0
    op.erase_eprom.return_value = True
    op.sdp_lock.return_value = True
    op.sdp_unlock.return_value = True
    for name, value in returns.items():
        getattr(op, name).return_value = value
        getattr(op, name).side_effect = None
    return op


def plan_with_steps(*steps, name="M8720", is_uv=False):
    """Build a `Plan` from bare `Step`s, following `test_chip_test.py:1093`."""
    return Plan(name=name, steps=list(steps), is_uv=is_uv)


def step(op, *, supported=True, reason="", **fields):
    """A thin `Step` builder. `Step.reason` is a required positional, and a
    hand-built counter-plan reads badly without it spelled as a keyword."""
    return Step(op=op, supported=supported, reason=reason, **fields)
