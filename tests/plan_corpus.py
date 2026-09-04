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

**SWEEP_SCOPES and D-08.** Only `"full"` and `"partial"` are swept.
`write_scope="none"` structurally omits the write step from `Plan.steps`
(`derive_plan`'s own docstring), so the write-to-verify rule is vacuously
true across every `"none"` plan -- sweeping it would silently inflate a
"zero violations" count with rows that could never violate anything.
`dev test` itself only ever produces `"full"` (non-UV) or `"partial"` (UV
and non-UV alike), so full+partial is already a strict superset of the
reachable domain.

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
erase call and makes roughly 2,545 of the corpus's 6,944 supported steps
come back SKIPPED rather than actually dispatched. A sweep built on this
double proves step-count alignment and NA-on-unsupported; it does not
exercise the write path on most chips.

**plan_with_steps and step.** `Plan` and `Step` are plain dataclasses with
no builder of their own. `plan_with_steps` assembles a `Plan` from bare
`Step` objects, following `tests/test_chip_test.py:1093`. `step` is a thin
`Step` builder because `Step.reason` is a required positional and a
hand-built counter-plan reads badly without a name for it.
"""

from __future__ import annotations

from unittest.mock import Mock

from firestarter.chip_test import Plan, Step, derive_plan
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

SWEEP_SCOPES = ("full", "partial")

_PLAN_CORPUS_CACHE = None


def plan_corpus():
    """The whole sweep domain: every `PART_NUMBERS` name at every
    `SWEEP_SCOPES` scope, derived once and cached for the rest of the
    pytest process."""
    global _PLAN_CORPUS_CACHE
    if _PLAN_CORPUS_CACHE is None:
        _PLAN_CORPUS_CACHE = {
            (name, scope): derive_plan(name, REAL_DB, write_scope=scope)
            for name in PART_NUMBERS
            for scope in SWEEP_SCOPES
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
    op.verify_eprom.return_value = True
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
