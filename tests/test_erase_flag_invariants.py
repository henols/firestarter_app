"""Phase 153 Plan 12 (ERASE-03) -- exhaustive host-side proof that the
FLAG_CAN_ERASE restoration for algorithm 13 (0x0D / EEPROM_POLL) is SCOPED.

Plans 08 through 11 inverted the old algorithm-13 exclusion tests -- their
green runs prove the old assertions that used to clear FLAG_CAN_ERASE for
this family are gone. They do NOT prove the change stayed inside its
intended boundary. This module is that proof: it walks every one of the
746 rows in the generated database and asserts that exactly the 84
algorithm-13 rows gained the bit and nothing else moved.

**Anti-vacuity rule, stated up front (the same trap
`test_page_size_invariants.py`'s own selector docstring documents):** the
generated database (`EpromDatabase.proms`) is a mapping of manufacturer to
a *list* of chip records, never a flat list of chips. A selector that scans
the top level only (`for row in db.proms: ...`) iterates manufacturer
*keys* (strings) or, at best, the per-manufacturer *lists* -- it never
reaches a chip record, so every downstream filter on it returns an empty
set and every assertion built on that set passes vacuously, regardless of
whether the real invariant holds. Every leg below goes through
`_select_algorithm_13_rows`, which descends two levels for exactly this
reason.

Rows are keyed by `part_number`. There is **no** `name` key in the
generated database (that key exists only in the older full-data view
`EpromDatabase.get_eprom` returns, not in the raw `proms` structure this
module reads).

The capability bit is derived at *conversion* time
(`EpromDatabase.convert_to_programmer`), not stored as a raw JSON field --
`_map_data` produces the intermediate `electrical-type` full-data view,
and `convert_to_programmer` is what actually computes `flags &
FLAG_CAN_ERASE`. Every leg below therefore converts each row through both
calls rather than reading `chip["programming"]` directly; reading raw JSON
would miss the derivation entirely and could not detect a regression in
`convert_to_programmer` itself.

One module-level `EpromDatabase(skip_local_override=True)` instance is
used throughout, so a developer's own `~/.firestarter/database.json`
override can never change any of the counts asserted here.

Coverage (6 legs):
  1. Exactly 84 of the 746 total rows carry `programming.algorithm == 13`.
  2. Every one of those 84 rows carries the erase capability bit after
     conversion.
  3. Every row that is NOT algorithm 13 has a bit value matching the
     pre-change rule (set when electrical type is erasable AND algorithm
     is not 5, clear otherwise) -- the scope proof, expressed from the
     rule rather than a snapshot.
  4. Algorithm-5 rows (a distinct, non-empty population) still never carry
     the bit -- a named hardware-damage guard, not a duplicate of leg 3.
  5. AT28C256's `write_scope="full"` plan shape is pinned.
  6. AT28C256's `write_scope="none"` plan shape is pinned -- the one shape
     this phase changed that no committed test previously watched.

Reachability (each leg was observed to fail against a deliberate,
temporary local mutation before being trusted -- see
153-12-SUMMARY.md "Reachability evidence" for the transcribed failures and
confirmation that every mutation was reverted with an empty
`git diff --quiet -- firestarter/database.py`):
  - Legs 2, 5 and 6 fail against a revert of plan 07's tuple edit
    (`if algo not in (5,):` reverted to `if algo not in (5, 13):`), which
    reproduces the pre-Phase-153 state where algorithm 13 never carried
    the bit.
  - Legs 3 and 4 are INVARIANT across that particular revert (it only
    touches algorithm 13, and both legs scan algorithm != 13 rows); they
    were instead observed failing against dropping the exclusion
    entirely (`if algo not in (5,):` -> `if True:`), which flips all 27
    algorithm-5 rows to bit=True and is caught by both legs.
  - Leg 1 does not depend on FLAG_CAN_ERASE at all (it only counts rows by
    `programming.algorithm`), so neither mutation touches it. Its
    reachability is instead the anti-vacuity property itself: a
    (deliberately not committed) top-level-only scan was run by hand and
    confirmed to return 0 pairs where the real two-level selector returns
    84, which is exactly the vacuous-pass failure mode this module's
    docstring warns about.
"""

from firestarter.chip_test import (
    OP_BLANK_CHECK,
    OP_ERASE,
    OP_ID,
    OP_READ,
    OP_SDP_LOCK,
    OP_SDP_UNLOCK,
    OP_VERIFY,
    OP_WRITE,
    OP_WRITE_BASELINE_A,
    OP_WRITE_BASELINE_B,
    OP_WRITE_INHIBITED,
    OP_WRITE_RESTORED,
    derive_plan,
)
from firestarter.constants import FLAG_CAN_ERASE
from firestarter.database import EpromDatabase

# One shared, real, on-disk database instance -- no ~/.firestarter override,
# no serial, deterministic across developer machines.
_REAL_DB = EpromDatabase(skip_local_override=True)

_ALGORITHM_0X0D = 13
_ALGORITHM_FLASH4 = 5

_ERASABLE_ELECTRICAL_TYPES = ("EEPROM", "Flash/EEPROM")

_AT28C256_CHIP_NAME = "AT28C256"


def _select_algorithm_13_rows(db: EpromDatabase) -> list[tuple[str, dict]]:
    """Select every (manufacturer, chip_record) pair with
    `programming.algorithm == 13`.

    `db.proms` is `{manufacturer: [chip_record, ...]}` -- a top-level scan
    over `db.proms` (rather than this nested per-chip descent) iterates
    manufacturer keys or per-manufacturer lists, never a chip record, and
    would make every downstream assertion pass vacuously. See the module
    docstring's anti-vacuity rule.
    """
    selected = []
    for manufacturer, chips in db.proms.items():
        for chip in chips:
            if chip.get("programming", {}).get("algorithm") == _ALGORITHM_0X0D:
                selected.append((manufacturer, chip))
    return selected


def _all_rows(db: EpromDatabase) -> list[tuple[str, dict]]:
    """Every (manufacturer, chip_record) pair in the database, exhaustively."""
    rows = []
    for manufacturer, chips in db.proms.items():
        for chip in chips:
            rows.append((manufacturer, chip))
    return rows


def _erase_capability_bit(db: EpromDatabase, manufacturer: str, chip: dict) -> bool:
    """Convert `chip` through the real `_map_data` -> `convert_to_programmer`
    pipeline and return whether the erase capability bit is set on the wire.

    This deliberately does not read `chip["programming"]` or
    `chip["electrical"]` directly -- the bit is a DERIVED value, computed
    only at `convert_to_programmer` time, and reading raw JSON would miss a
    regression in that derivation entirely.
    """
    full = db._map_data(chip, manufacturer)
    wire = db.convert_to_programmer(full)
    return bool(wire["flags"] & FLAG_CAN_ERASE)


# ---------------------------------------------------------------------------
# Leg 1: exactly 84 of the 746 total rows are algorithm 13.
# ---------------------------------------------------------------------------


def test_exactly_84_algorithm_13_rows_across_all_746_rows() -> None:
    """The 746 total is asserted in the same test as the 84 subset count --
    a change in either number invalidates every other leg in this module."""
    total_rows = _all_rows(_REAL_DB)
    assert len(total_rows) == 746, (
        f"expected 746 total database rows, found {len(total_rows)}"
    )

    algo13_rows = _select_algorithm_13_rows(_REAL_DB)
    assert len(algo13_rows) == 84, (
        f"expected exactly 84 rows with programming.algorithm == 13, "
        f"found {len(algo13_rows)}: "
        f"{[(m, c.get('part_number', '?')) for m, c in algo13_rows]}"
    )


# ---------------------------------------------------------------------------
# Leg 2: every one of the 84 algorithm-13 rows carries the erase bit.
# ---------------------------------------------------------------------------


def test_every_algorithm_13_row_carries_the_erase_capability_bit() -> None:
    algo13_rows = _select_algorithm_13_rows(_REAL_DB)
    offenders = [
        f"{mfr}/{chip.get('part_number', '?')}"
        for mfr, chip in algo13_rows
        if not _erase_capability_bit(_REAL_DB, mfr, chip)
    ]
    assert not offenders, (
        f"every algorithm-13 row must carry the erase capability bit after "
        f"conversion; rows missing it: {offenders}"
    )


# ---------------------------------------------------------------------------
# Leg 3: no non-algorithm-13 row's bit moved -- the scope proof.
# ---------------------------------------------------------------------------


def test_no_non_algorithm_13_row_gained_the_erase_capability_bit() -> None:
    """For every row that is NOT algorithm 13, the bit must match the
    PRE-CHANGE rule: set when electrical type is erasable AND algorithm is
    not 5, clear otherwise. Expressed from the rule itself (not from a
    recaptured snapshot of today's output), so this leg states the
    invariant rather than merely freezing whatever the code currently does.
    """
    offenders = []
    for mfr, chip in _all_rows(_REAL_DB):
        algo = chip.get("programming", {}).get("algorithm")
        if algo == _ALGORITHM_0X0D:
            continue
        etype = chip.get("electrical", {}).get("type", "")
        expected = etype in _ERASABLE_ELECTRICAL_TYPES and algo != _ALGORITHM_FLASH4
        actual = _erase_capability_bit(_REAL_DB, mfr, chip)
        if actual != expected:
            offenders.append(
                f"{mfr}/{chip.get('part_number', '?')}: algorithm={algo} "
                f"electrical_type={etype!r} expected_bit={expected} "
                f"actual_bit={actual}"
            )
    assert not offenders, (
        f"non-algorithm-13 rows must match the pre-change erase-capability "
        f"rule exactly; offenders: {offenders}"
    )


# ---------------------------------------------------------------------------
# Leg 4: algorithm-5 hazard guard, named separately from leg 3.
# ---------------------------------------------------------------------------


def test_algorithm_5_rows_still_do_not_carry_the_erase_capability_bit() -> None:
    """Setting the erase capability bit for algorithm 5 (flash4) routes the
    firmware write-init into an erase that energises the VPP boost
    regulator (CTRL_VPP_REGULATOR_ENABLE) on a 5V-only part -- a
    hardware-damage hazard, not merely a scope violation. This is
    deliberately its own named leg rather than folded into leg 3's generic
    scope proof, and it asserts the algorithm-5 population is non-empty
    FIRST so it cannot silently pass over an empty set.
    """
    algo5_rows = [
        (mfr, chip)
        for mfr, chip in _all_rows(_REAL_DB)
        if chip.get("programming", {}).get("algorithm") == _ALGORITHM_FLASH4
    ]
    assert len(algo5_rows) > 0, (
        "expected a non-zero population of algorithm-5 (flash4) rows in the "
        "database -- an empty set here would make the assertion below pass "
        "vacuously"
    )

    offenders = [
        f"{mfr}/{chip.get('part_number', '?')}"
        for mfr, chip in algo5_rows
        if _erase_capability_bit(_REAL_DB, mfr, chip)
    ]
    assert not offenders, (
        f"algorithm-5 (flash4) rows must NEVER carry the erase capability "
        f"bit -- setting it routes eeprom28c-style erase into a 5V-only "
        f"part's VPP regulator; offenders: {offenders}"
    )


# ---------------------------------------------------------------------------
# Leg 5: AT28C256 write_scope="full" plan shape, pinned.
# ---------------------------------------------------------------------------

# Measured live in this session (see module docstring "Reachability" and
# 153-12-SUMMARY.md): id, read, write, verify, erase, blank-check, then the
# six-op SDP leg, in that exact order.
_AT28C256_FULL_EXPECTED_OP_ORDER = [
    OP_ID,
    OP_READ,
    OP_WRITE,
    OP_VERIFY,
    OP_ERASE,
    OP_BLANK_CHECK,
    OP_WRITE_BASELINE_B,
    OP_WRITE_BASELINE_A,
    OP_SDP_LOCK,
    OP_WRITE_INHIBITED,
    OP_SDP_UNLOCK,
    OP_WRITE_RESTORED,
]


def test_at28c256_full_plan_shape_is_pinned() -> None:
    """Pin the measured write_scope="full" plan for AT28C256: twelve steps
    in the exact order above; the erase step supported and destructive; the
    blank-check step unsupported (D-153-04 family fact: each page write
    auto-erases internally, so no step can ever leave the device blank);
    and blank-check's index strictly after erase's. Asserted as a list
    equality on the op sequence so an inserted or reordered step fails
    loudly rather than being missed by a membership check.
    """
    plan = derive_plan(_AT28C256_CHIP_NAME, _REAL_DB, write_scope="full")
    ops = [step.op for step in plan.steps]

    assert ops == _AT28C256_FULL_EXPECTED_OP_ORDER, (
        f"AT28C256 write_scope='full' op order drifted from the pinned "
        f"shape; expected {_AT28C256_FULL_EXPECTED_OP_ORDER}, got {ops}"
    )

    erase_step = plan.steps[ops.index(OP_ERASE)]
    assert erase_step.supported is True, "AT28C256 erase must be a supported step"
    assert erase_step.destructive is True, "AT28C256 erase must be destructive"

    blank_check_step = plan.steps[ops.index(OP_BLANK_CHECK)]
    assert blank_check_step.supported is False, (
        "AT28C256 blank-check must remain unsupported (NA) -- D-153-04: "
        "each page write auto-erases internally, so no step can ever leave "
        "the device blank"
    )

    erase_index = ops.index(OP_ERASE)
    blank_check_index = ops.index(OP_BLANK_CHECK)
    assert blank_check_index > erase_index, (
        f"blank-check (index {blank_check_index}) must sit strictly after "
        f"erase (index {erase_index})"
    )


# ---------------------------------------------------------------------------
# Leg 6: AT28C256 write_scope="none" plan shape, pinned -- the shape this
# phase changed with no prior committed assertion.
# ---------------------------------------------------------------------------

# Measured live in this session: three steps only (id, read, blank-check).
_AT28C256_NONE_EXPECTED_OP_ORDER = [OP_ID, OP_READ, OP_BLANK_CHECK]

# The nine ops that go to the advisory locked_destructive list instead of
# steps: write, verify, erase, and the six-op SDP leg.
_AT28C256_NONE_EXPECTED_LOCKED_OPS = {
    OP_WRITE,
    OP_VERIFY,
    OP_ERASE,
    OP_WRITE_BASELINE_B,
    OP_WRITE_BASELINE_A,
    OP_SDP_LOCK,
    OP_WRITE_INHIBITED,
    OP_SDP_UNLOCK,
    OP_WRITE_RESTORED,
}


def test_at28c256_write_scope_none_shape_is_pinned() -> None:
    """Pin the measured write_scope="none" plan for AT28C256.

    THIS SHAPE CHANGED in this phase: before ERASE-03 restored
    FLAG_CAN_ERASE for algorithm 13, erase was NA (unsupported) at every
    write_scope, so it was never added to `locked_destructive` (an NA step
    was never runnable in the first place, so there was nothing to lock).
    Now that erase is a real, supported, destructive step, write_scope="none"
    demotes it to `locked_destructive` alongside write/verify/the SDP leg --
    dropping the executable `steps` list from four entries (id, read,
    erase-as-NA-with-a-different-reason, blank-check) to three (id, read,
    blank-check). No committed test asserted this shape before this plan,
    so it would otherwise have been an unnoticed behavioural change.
    """
    plan = derive_plan(_AT28C256_CHIP_NAME, _REAL_DB, write_scope="none")
    ops = [step.op for step in plan.steps]

    assert ops == _AT28C256_NONE_EXPECTED_OP_ORDER, (
        f"AT28C256 write_scope='none' op order drifted from the pinned "
        f"shape; expected {_AT28C256_NONE_EXPECTED_OP_ORDER}, got {ops}"
    )

    blank_check_step = plan.steps[ops.index(OP_BLANK_CHECK)]
    assert blank_check_step.supported is True, (
        "AT28C256 blank-check must be supported at write_scope='none' -- "
        "with no write executing, blank-check has no auto-erase side "
        "effect in flight to make it NA"
    )

    locked_ops = {op for op, _reason in plan.locked_destructive}
    assert locked_ops == _AT28C256_NONE_EXPECTED_LOCKED_OPS, (
        f"AT28C256 write_scope='none' locked_destructive set drifted; "
        f"expected {_AT28C256_NONE_EXPECTED_LOCKED_OPS}, got {locked_ops}"
    )
