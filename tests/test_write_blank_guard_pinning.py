"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 203 Plan 02 -- pin the exact write-guard set (WRITE-02 / D-01 / D-02),
prove the bypass-flag and erase-exemption legs (WRITE-03 / D-03 / D-09), and
close the folded negative-write-start-address todo on its host half.

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
  7. THE FLAG LEGS -- `requires_blank_check`/`is_erase_exempt` over
     `FLAG_SKIP_BLANK_CHECK`, `FLAG_SKIP_ERASE` (D-03's re-arming), and
     `FLAG_FORCE` (D-09's non-bypass), plus the `_build_op_flags` mapping
     `-b`/`--skip-erase` actually produce, and the `dev test` UV
     write-shortcut's `write_flags` expression fed straight into the guard.
  8. THE ABSENT-EVIDENCE LEG -- `is_guarded_protocol` fails CLOSED on
     `None`/`{}`/`{"algorithm": None}`, and is False for a protocol id in
     NEITHER set (Fork A), derived as a value no shipped row carries rather
     than asserted as a literal.
  9. THE NEGATIVE START ADDRESS GATE -- `require_non_negative_address`
     raises `NegativeStartAddressError` for a negative decimal or hex
     address, is a no-op for `None`, a valid address, and an unparseable
     one, and the CLI-level refusal fires before `write_eprom` is ever
     called, on a guarded family and an unguarded one alike.
"""

from __future__ import annotations

import pytest

from firestarter.chip_resolver import resolve_chip
from firestarter.cli_handlers import _build_op_flags
from firestarter.constants import (
    FLAG_CAN_ERASE,
    FLAG_FORCE,
    FLAG_SKIP_BLANK_CHECK,
    FLAG_SKIP_ERASE,
)
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

from .test_write_blank_guard import _m27c512_data

# NOTE (Task 3, leg 9): `NegativeStartAddressError` and
# `require_non_negative_address` are imported LOCALLY inside each leg-9 test
# function below, not at module scope -- keeping them out of this module's
# import block is what let the RED phase for this task fail per-test (an
# ImportError inside the specific new test) rather than as a whole-module
# collection crash that would have also blocked every already-green leg
# 1-8 test above from running at all.

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


def test_build_op_flags_blank_check_false_sets_only_the_skip_blank_check_bit():
    """`-b`/`--no-blank-check` maps to exactly one bit."""
    flags = _build_op_flags(blank_check=False)
    assert flags & FLAG_SKIP_BLANK_CHECK
    assert not (flags & FLAG_SKIP_ERASE)


def test_build_op_flags_skip_erase_sets_only_the_skip_erase_bit():
    """`--skip-erase` maps to exactly one bit, the OTHER one."""
    flags = _build_op_flags(skip_erase=True)
    assert flags & FLAG_SKIP_ERASE
    assert not (flags & FLAG_SKIP_BLANK_CHECK)


def test_requires_blank_check_skip_erase_rearms_the_guard_on_an_erase_capable_part():
    """D-03: `--skip-erase` makes the exemption's premise -- "the erase
    immediately above the check already guarantees blank" -- false, so the
    guard re-arms even though `FLAG_CAN_ERASE` is still set."""
    erase_capable = {"algorithm": 7, "flags": FLAG_CAN_ERASE}
    assert requires_blank_check(erase_capable, 0) is False
    assert requires_blank_check(erase_capable, FLAG_SKIP_ERASE) is True
    assert (
        requires_blank_check(erase_capable, FLAG_SKIP_ERASE | FLAG_SKIP_BLANK_CHECK)
        is False
    )


def test_dev_test_uv_write_shortcut_keeps_working_and_the_unmasked_case_is_guarded():
    """D-07: `dev test`'s monotonic-masked UV write shortcut keeps working
    unchanged (the same `write_flags` expression
    `chip_test._dispatch_multi_run` actually uses, fed straight into the
    guard), and a non-masked UV target -- `write_flags == 0` -- is now
    guarded by the host, which is the case where the firmware would have
    refused the identical write anyway (RESEARCH section 11.1)."""
    from firestarter.chip_test import (
        WriteTarget,
        _is_monotonic_masked_target,
        bits_cleared_by,
        bits_retained_by,
        generate_pattern,
        mask_write_pattern,
    )

    guarded = _m27c512_data()

    region = (0x1000, 256)
    current = b"\xff" * 256
    desired = generate_pattern(*region)
    masked_target = WriteTarget(
        region=region,
        pattern=mask_write_pattern(current, desired),
        masked=True,
        bits_cleared=bits_cleared_by(current, desired),
        bits_retained=bits_retained_by(current, desired),
        current_source="probe read (tranche 1/2)",
        current=current,
        current_is_probe_read=True,
    )
    unmasked_target = WriteTarget(
        region=region,
        pattern=desired,
        masked=False,
        bits_cleared=0,
        bits_retained=0,
        current_source="test fixture",
    )

    # The exact expression chip_test.py:3219-3221 uses at its OP_WRITE /
    # OP_WRITE_PARTIAL dispatch site.
    masked_write_flags = (
        FLAG_SKIP_BLANK_CHECK if _is_monotonic_masked_target(masked_target) else 0
    )
    unmasked_write_flags = (
        FLAG_SKIP_BLANK_CHECK if _is_monotonic_masked_target(unmasked_target) else 0
    )

    assert masked_write_flags == FLAG_SKIP_BLANK_CHECK
    assert unmasked_write_flags == 0

    assert requires_blank_check(guarded, masked_write_flags) is False
    assert requires_blank_check(guarded, unmasked_write_flags) is True


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


# ---------------------------------------------------------------------------
# Leg 9: THE NEGATIVE START ADDRESS GATE
# ---------------------------------------------------------------------------


def test_require_non_negative_address_raises_for_a_negative_decimal_address():
    from firestarter.exceptions import NegativeStartAddressError
    from firestarter.write_blank_guard import require_non_negative_address

    with pytest.raises(NegativeStartAddressError):
        require_non_negative_address("m27c512", "-256")


def test_require_non_negative_address_raises_for_a_negative_hex_address():
    from firestarter.exceptions import NegativeStartAddressError
    from firestarter.write_blank_guard import require_non_negative_address

    with pytest.raises(NegativeStartAddressError):
        require_non_negative_address("m27c512", "-0x100")


def test_require_non_negative_address_is_a_no_op_for_none_and_a_valid_address():
    from firestarter.write_blank_guard import require_non_negative_address

    assert require_non_negative_address("m27c512", None) is None
    assert require_non_negative_address("m27c512", "0x100") is None


def test_require_non_negative_address_is_a_no_op_for_an_unparseable_address():
    """A malformed address stays the existing handlers' job -- this gate's
    error contract must not change it."""
    from firestarter.write_blank_guard import require_non_negative_address

    assert require_non_negative_address("m27c512", "notanumber") is None


def test_write_eprom_negative_start_address_refuses_before_operation_context(
    tmp_path,
):
    """The operator-tier half: `dev test` and `dev write-cycle` call
    `write_eprom` directly, bypassing `cli_handlers.write` entirely, so the
    gate must also fire from inside `write_eprom` itself -- mirrors
    `test_page_size_alignment_refusal.test_write_eprom_unaligned_start_refuses_before_operation_context`'s
    proven pattern."""
    from unittest.mock import patch

    from firestarter.config import ConfigManager
    from firestarter.eprom_operations import EpromOperator
    from firestarter.exceptions import NegativeStartAddressError

    payload = tmp_path / "probe.bin"
    payload.write_bytes(b"\xaa" * 4)

    operator = EpromOperator(ConfigManager())
    with patch.object(EpromOperator, "_operation_context") as ctx_mock:
        with pytest.raises(NegativeStartAddressError) as exc_info:
            operator.write_eprom(
                "m27c512", _m27c512_data(), str(payload), address_str="-256"
            )
        ctx_mock.assert_not_called()

    assert "M27C512" in str(exc_info.value)


def _assert_cli_negative_address_refusal(chip_name: str, address_arg: str) -> None:
    """The CLI-tier half: `CliRunner` + `Mock(spec=EpromOperator)`, proving
    the refusal fires -- and `write_eprom` is never reached -- before
    `app.eprom_operator.write_eprom` is ever invoked. Covers a guarded
    family (M27C512) and an unguarded one (the 28C-parallel AT28C256)
    alike, so the refusal is not accidentally scoped to the guarded set."""
    import tempfile
    from unittest.mock import Mock

    from click.testing import CliRunner

    from firestarter.cli_handlers import cli
    from firestarter.eprom_operations import EpromOperator

    from .conftest import make_app_context

    runner = CliRunner()
    eprom_operator = Mock(spec=EpromOperator)
    app = make_app_context(eprom_operator=eprom_operator)

    with tempfile.NamedTemporaryFile(suffix=".bin") as tmp:
        tmp.write(b"\xaa" * 4)
        tmp.flush()
        result = runner.invoke(
            cli, ["write", chip_name, tmp.name, "-a", address_arg], obj=app
        )

    assert result.exit_code == 1
    lines = result.output.splitlines()
    assert len(lines) == 1
    assert lines[0] == (
        f"Error: {chip_name.upper()}: refused -- the start address "
        f"{address_arg!r} is negative."
    )
    assert "Programmer error" not in result.output
    eprom_operator.write_eprom.assert_not_called()


def test_cli_write_negative_address_refuses_on_an_unguarded_family_too():
    _assert_cli_negative_address_refusal("at28c256", "-256")


def test_cli_write_negative_address_refuses_on_a_guarded_family_too():
    _assert_cli_negative_address_refusal("m27c512", "-256")
