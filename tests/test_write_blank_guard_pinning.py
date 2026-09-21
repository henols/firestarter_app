"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 203 Plan 02 Task 1 -- pin the exact write-guard set (WRITE-02 / D-01 /
D-02): the set the firmware pre-flights today, and no other.

Coverage:
  1. THE EXACT SET -- `GUARDED_PROTOCOL_IDS` and `NAMED_EXEMPT_PROTOCOL_IDS`
     pinned by `==` against explicit frozensets, so removing OR adding a
     single id fails (D-02's narrows-or-widens obligation, its strongest
     direct form).
  2. DISJOINT -- the two sets never overlap, and every SRAM/FRAM id is a
     member of the named-exemption union.
  3. COUPLING TO THE REAL DATABASE -- the set of shipped part numbers for
     which `is_guarded_protocol(resolve_chip(name))` is True, derived
     independently, is exactly the set of shipped rows whose
     `programming.algorithm` is a guarded id. No literal part-number list.
  4. NOT A BLANKET REFUSAL -- the unguarded set is non-empty and covers
     flash4 (0x05), 28C parallel (0x0D), and at least one SRAM id, all
     derived from the database rather than named literally.
  5. PER-ROW CENSUS -- the guarded row count, derived live from the shipped
     database, equals the independently-filtered count for the five
     guarded ids.
  6. THE MESSAGE SHAPE -- `refusal_text` is pinned by full-string equality,
     carries no newline, and none of a forbidden-substring list built for
     THIS sentence (not copied from `flash4_erase_gate`'s, which forbids the
     word "write" -- a word this sentence legitimately contains).
  7. THE FLAG LEGS -- `requires_blank_check` over `FLAG_SKIP_BLANK_CHECK`
     and `FLAG_FORCE` (D-09's non-bypass) for a `flags: 0` guarded part.
     Extended in Task 2 with the erase-capable dict, the `_build_op_flags`
     mapping, and the `dev test` UV write-shortcut's own expression.
  8. THE ABSENT-EVIDENCE LEG -- `is_guarded_protocol` fails CLOSED on
     `None`/`{}`/`{"algorithm": None}`, and is False for a protocol id in
     NEITHER set (Fork A), derived as a value no shipped row carries rather
     than asserted as a literal.
"""

from __future__ import annotations

from firestarter.chip_resolver import resolve_chip
from firestarter.constants import FLAG_FORCE, FLAG_SKIP_BLANK_CHECK
from firestarter.database import EpromDatabase
from firestarter.exceptions import ChipNotFoundError, ChipNotImplementedError
from firestarter.flash4_erase_gate import FLASH4_PROTOCOL_ID
from firestarter.sdp_capability import SDP_PROTOCOL_ID
from firestarter.write_blank_guard import (
    GUARDED_PROTOCOL_IDS,
    NAMED_EXEMPT_PROTOCOL_IDS,
    SRAM_PROTOCOL_IDS,
    is_guarded_protocol,
    refusal_text,
    requires_blank_check,
)

# ---------------------------------------------------------------------------
# Leg 1: THE EXACT SET
# ---------------------------------------------------------------------------


def test_guarded_protocol_ids_is_exactly_the_five_firmware_pre_flighted_ids():
    """D-02: an equality, not a containment test -- fails on both narrowing
    and widening. Deliberately removing 0x0B, or adding 0x0D, must each turn
    this leg red; see the plan's own acceptance criterion for the executed
    proof (recorded in the plan summary, not left to this comment alone)."""
    assert GUARDED_PROTOCOL_IDS == frozenset({0x06, 0x07, 0x08, 0x0B, 0x10})
    assert GUARDED_PROTOCOL_IDS == frozenset({6, 7, 8, 11, 16})


def test_named_exempt_protocol_ids_is_exactly_the_six_named_ids():
    assert NAMED_EXEMPT_PROTOCOL_IDS == frozenset({0x05, 0x0D, 0x0E, 0x27, 0x28, 0x29})
    assert NAMED_EXEMPT_PROTOCOL_IDS == frozenset({5, 13, 14, 39, 40, 41})


# ---------------------------------------------------------------------------
# Leg 2: DISJOINT
# ---------------------------------------------------------------------------


def test_guarded_and_named_exempt_sets_are_disjoint():
    assert GUARDED_PROTOCOL_IDS & NAMED_EXEMPT_PROTOCOL_IDS == frozenset()


def test_every_sram_id_is_a_named_exemption():
    assert SRAM_PROTOCOL_IDS <= NAMED_EXEMPT_PROTOCOL_IDS


# ---------------------------------------------------------------------------
# Leg 3: COUPLING TO THE REAL DATABASE
# ---------------------------------------------------------------------------


def test_predicate_matches_real_database_guarded_rows_exactly():
    """Both sides derived independently from the shipped database, exactly
    as `test_flash4_erase_gate.test_predicate_matches_real_database_algorithm_5_rows_exactly`
    does for its own single algorithm -- no literal part-number list
    anywhere in this test."""
    db = EpromDatabase(skip_local_override=True)

    expected: set[str] = set()
    all_part_numbers: set[str] = set()
    for vendor_chips in db.proms.values():
        for chip in vendor_chips:
            part_number = chip.get("part_number", "")
            if not part_number:
                continue
            all_part_numbers.add(part_number)
            if chip.get("programming", {}).get("algorithm") in GUARDED_PROTOCOL_IDS:
                expected.add(part_number)

    actual: set[str] = set()
    for name in all_part_numbers:
        try:
            programmer_data = resolve_chip(name, db=db)
        except (ChipNotFoundError, ChipNotImplementedError):
            continue
        if is_guarded_protocol(programmer_data):
            actual.add(name)

    assert actual == expected, (
        f"predicate fired for {len(actual)} part number(s) against "
        f"{len(expected)} shipped guarded row(s); symmetric difference: "
        f"{sorted(actual ^ expected)}"
    )


# ---------------------------------------------------------------------------
# Leg 4: NOT A BLANKET REFUSAL
# ---------------------------------------------------------------------------


def test_unguarded_set_is_non_empty_and_covers_flash4_28c_parallel_and_an_sram_id():
    """Without this leg, a fail-closed regression that guards everything
    would pass leg 3 vacuously in one direction (`expected == actual == the
    whole database`)."""
    db = EpromDatabase(skip_local_override=True)

    unguarded_algorithms: set[int] = set()
    for vendor_chips in db.proms.values():
        for chip in vendor_chips:
            algorithm = chip.get("programming", {}).get("algorithm")
            if algorithm is not None and algorithm not in GUARDED_PROTOCOL_IDS:
                unguarded_algorithms.add(algorithm)

    assert unguarded_algorithms, "the unguarded set must be non-empty"
    assert FLASH4_PROTOCOL_ID in unguarded_algorithms
    assert SDP_PROTOCOL_ID in unguarded_algorithms
    assert unguarded_algorithms & SRAM_PROTOCOL_IDS, (
        "the unguarded set must cover at least one SRAM/FRAM id"
    )


# ---------------------------------------------------------------------------
# Leg 5: PER-ROW CENSUS
# ---------------------------------------------------------------------------


def test_guarded_row_count_equals_the_sum_of_the_five_guarded_algorithm_counts():
    """A readable number in the failure summary without hard-coding one
    that a database regeneration could silently falsify -- both counts are
    derived live from the shipped database, by two different traversals."""
    db = EpromDatabase(skip_local_override=True)

    per_algorithm_counts: dict[int, int] = {}
    for vendor_chips in db.proms.values():
        for chip in vendor_chips:
            algorithm = chip.get("programming", {}).get("algorithm")
            if algorithm is not None:
                per_algorithm_counts[algorithm] = (
                    per_algorithm_counts.get(algorithm, 0) + 1
                )
    summed_guarded_total = sum(
        per_algorithm_counts.get(algorithm, 0) for algorithm in GUARDED_PROTOCOL_IDS
    )

    directly_filtered_total = sum(
        1
        for vendor_chips in db.proms.values()
        for chip in vendor_chips
        if chip.get("programming", {}).get("algorithm") in GUARDED_PROTOCOL_IDS
    )

    assert summed_guarded_total > 0
    assert summed_guarded_total == directly_filtered_total


# ---------------------------------------------------------------------------
# Leg 6: THE MESSAGE SHAPE
# ---------------------------------------------------------------------------

# Built for THIS sentence, not copied from
# `test_flash4_erase_gate.FORBIDDEN_REFUSAL_SUBSTRINGS` -- that list forbids
# the word "write", which this refusal legitimately contains ("Refusing
# write to ...").
_FORBIDDEN_BLANK_REFUSAL_SUBSTRINGS = (
    "-b",
    "--no-blank-check",
    "bypass",
    "workaround",
    "route around",
    "instead",
    "try",
    "because",
    "reason",
    "cause",
    "alternative",
)


def test_refusal_text_message_shape_is_pinned_by_equality_and_carries_no_forbidden_content():
    text = refusal_text("m27c512", 0x008000, 0xAB)

    assert text == "Refusing write to M27C512: not blank at 0x008000, v: 0xAB."
    assert "\n" not in text

    lowered = text.lower()
    for forbidden in _FORBIDDEN_BLANK_REFUSAL_SUBSTRINGS:
        assert forbidden not in lowered, (
            f"refusal text {text!r} contains forbidden substring {forbidden!r}"
        )


# ---------------------------------------------------------------------------
# Leg 7: THE FLAG LEGS
# ---------------------------------------------------------------------------


def test_requires_blank_check_flag_legs_for_a_flags_zero_guarded_part():
    guarded = {"algorithm": 7, "flags": 0}
    assert requires_blank_check(guarded, 0) is True
    assert requires_blank_check(guarded, FLAG_SKIP_BLANK_CHECK) is False
    assert requires_blank_check(guarded, FLAG_FORCE) is True


# ---------------------------------------------------------------------------
# Leg 8: THE ABSENT-EVIDENCE LEG
# ---------------------------------------------------------------------------

# A value no shipped row carries, derived from the database rather than
# asserted as a literal -- guaranteed outside both GUARDED_PROTOCOL_IDS and
# NAMED_EXEMPT_PROTOCOL_IDS, since those are both subsets of the shipped
# algorithm values.
_SHIPPED_ALGORITHM_VALUES = {
    chip.get("programming", {}).get("algorithm")
    for vendor_chips in EpromDatabase(skip_local_override=True).proms.values()
    for chip in vendor_chips
    if chip.get("programming", {}).get("algorithm") is not None
}
_UNCLASSIFIED_ALGORITHM_ID = max(_SHIPPED_ALGORITHM_VALUES) + 1


def test_is_guarded_protocol_fails_closed_on_absent_evidence():
    assert is_guarded_protocol(None) is True
    assert is_guarded_protocol({}) is True
    assert is_guarded_protocol({"algorithm": None}) is True


def test_is_guarded_protocol_false_for_an_id_in_neither_set():
    """Fork A: an `algorithm` in NEITHER `GUARDED_PROTOCOL_IDS` nor
    `NAMED_EXEMPT_PROTOCOL_IDS` is NOT guarded -- "preserve today's
    coverage" outranks fail-closed here, because such an id is by
    definition outside what the firmware pre-flights today. Reachable only
    through a user-supplied `~/.firestarter` override; no shipped row
    carries this synthetic id."""
    assert _UNCLASSIFIED_ALGORITHM_ID not in GUARDED_PROTOCOL_IDS
    assert _UNCLASSIFIED_ALGORITHM_ID not in NAMED_EXEMPT_PROTOCOL_IDS
    assert _UNCLASSIFIED_ALGORITHM_ID not in _SHIPPED_ALGORITHM_VALUES
    assert is_guarded_protocol({"algorithm": _UNCLASSIFIED_ALGORITHM_ID}) is False
