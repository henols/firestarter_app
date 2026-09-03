"""
Blast-radius invariance harness -- absolute-hash, ladder, schema-key-list
and snapshot-drift gates for the frozen report-shape corpus (Phase 174,
GATE-01, GATE-02, GATE-03, GATE-05, D-07, D-10).

Anti-vacuity rule, stated up front: a relational comparison of two computed
fingerprints -- asserting `dedup_fingerprint(a)` agrees with
`dedup_fingerprint(b)` rather than pinning either one to a literal -- passes
vacuously through ANY change to the hash function itself, since both sides
move together. Eleven such relational comparisons already ship in
`tests/test_diagnostic_report.py`, and that is exactly why this module
exists: every assertion below compares a real `dedup_fingerprint` output
against an ABSOLUTE, pre-declared literal, never against a second computed
value, so a re-key is visible here instead of silently forking every
historical `count_agreeing` promotion group a maintainer has already
promoted a chip through.

Reachability: every gate in this module was observed to fail against a
deliberate, temporary planted mutation before it was trusted -- see
`.planning/phases/174-blast-radius-invariance-harness/evidence/
174-01-anti-vacuity-red-green.txt` for the transcribed RED output and
174-01-SUMMARY.md for the recorded proof. Concretely: clearing the write
step's `fingerprint` on `sst27sf512-six-step` (below,
`test_planted_mutation_clearing_write_fingerprint_reddens_the_gate`) was
observed to move `dedup_fingerprint`'s output away from `4dc282a5d596`
before this test was trusted, and lowering the same shape's `auto_capture`
chip name to `sst27sf512` (below,
`test_planted_mutation_lowering_chip_name_reddens_the_gate`) was
independently observed to move it too -- proving the gate is sensitive on
both axes `dedup_fingerprint` reads, not merely coincidentally correct on
the frozen shape as built. The three `tools/rekey/check_rekey_ledger.py`
fail-closed legs (missing ledger, missing MILESTONES.md, unparsable ledger)
and its one planted-mismatch leg were likewise observed RED in
`tests/test_rekey_ledger.py`'s subprocess legs before being trusted -- see
the same evidence file for all five checker invocations' transcribed
output.

D-07's seven element-wise `to_dict()` key-list pins (plan 174-03) got their
own anti-vacuity leg,
`test_to_dict_key_list_pins_are_sensitive_to_added_and_removed_keys`:
deleting `voltage` from a real `to_dict()` mapping and separately adding a
`canonical_part_number` key were both observed to move the sorted top-level
key list away from `_TO_DICT_KEYS` before these seven pins were trusted --
transcribed verbatim in
`.planning/phases/174-blast-radius-invariance-harness/evidence/
174-03-schema-pins.txt`.

D-10's four-way `shape_id` closure (plan 174-03,
`test_shape_ids_closure_is_sensitive_to_removed_and_added_entries`) was
likewise observed RED both ways before being trusted: popping the last
entry from a local copy of the committed `tests/fixtures/shape_ids.json`
anchor moved it away from `sorted(SHAPE_IDS)` (16 entries -> 15, no longer
equal), and appending a synthetic `bogus-added-shape-id` entry moved it
away the other direction (16 entries -> 17, no longer equal) -- neither
mutation touched the committed file on disk.

CR-01's two regression legs
(`test_build_shape_never_shares_results_or_plan_between_shape_ids`,
`test_mutation_through_a_derived_shape_does_not_move_the_base_shapes_frozen_hash`,
plan 174-06) were likewise observed RED against the pinned pre-fix
`report_shapes.py` blob `dc7e40a` before being trusted: the aliasing
comparison read `True` and a poke through `m27c512-full-canonical-name`
moved `m27c512-full-all-ok`'s fingerprint from `6d3afbc52315` to
`e9df6ca4627c` -- transcribed verbatim in
`.planning/phases/174-blast-radius-invariance-harness/evidence/
174-06-shape-aliasing-red-green.txt`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace as _dataclass_replace
from pathlib import Path

import pytest

from .fixtures.report_shapes import (
    FROZEN_HASHES,
    RESERVED_SHAPE_IDS,
    SHAPE_IDS,
    build_shape,
    build_shape_from_step_specs,
)

_HEX12_RE = re.compile(r"^[0-9a-f]{12}$")

"""D-07's first pin: `DiagnosticReport.to_dict()`
(`firestarter/diagnostic_report.py:771-790`) emits eleven top-level keys
today."""
_TO_DICT_KEYS = [
    "auto_capture",
    "banner",
    "db_diff",
    "dedup_fingerprint",
    "generated",
    "is_submittable",
    "schema_version",
    "sdp_hold_state",
    "steps",
    "transport_health",
    "voltage",
]

"""D-07's second pin: `_voltage_dict()` (`:619-640`) emits six keys today.
`vpp_mv` and `vpe_mv` are RPT-B1's two deletes -- present now, pinned now,
so Phase 181's deletion has to argue with a gate that predates it rather
than landing in the same phase as its own gate."""
_VOLTAGE_KEYS = [
    "vpe_after_mv",
    "vpe_before_mv",
    "vpe_mv",
    "vpp_after_mv",
    "vpp_before_mv",
    "vpp_mv",
]

"""D-07's third pin: `_banner_dict()` (`:731-738`) emits three keys today.
`locked_steps` is RPT-B2's delete -- present now, pinned now."""
_BANNER_KEYS = [
    "locked_steps",
    "m_applicable",
    "n_ran",
]

"""D-07's fourth pin: `_auto_capture_dict()` (`:592-603`) emits eight keys
today, with NO `canonical_part_number` key -- RPT-F1 adds one. The
absence is pinned as an absence: an addition surfaces here as a pin
failure on this exact list, not as a silent widening."""
_AUTO_CAPTURE_KEYS = [
    "chip",
    "chip_id_actual",
    "chip_id_expected",
    "chip_id_mismatch_reason",
    "fw_board_identity",
    "host_version",
    "hw_revision",
    "protocol",
]

"""D-07's fifth pin: `_transport_dict()` (`:605-617`) emits five keys
today."""
_TRANSPORT_HEALTH_KEYS = [
    "cobs_errors",
    "crc_failures",
    "retries",
    "timeouts",
    "transport_suspect",
]

"""D-07's sixth pin: `_db_diff_dict()` (`:740-748`) emits three keys today,
populated only after `build_db_diff` is composed onto a report -- `None`
on a bare `build_shape()` result. The paired test below composes it the
same way `tools/snapshot_report_shapes.py:render_shape` does, so there is
exactly one place that composition happens, not two independently
maintained copies."""
_DB_DIFF_KEYS = [
    "current_support_status",
    "ladder_state",
    "proposed_disposition",
]

"""D-07's seventh pin: `_step_dict()` (`:667-729`) emits thirteen keys per
step, UNCONDITIONALLY -- taken from `sst27sf512-six-step`'s first (`id`)
step, whose five `write_*` fields stay `None` because `id` carries no
write target, but all thirteen KEYS are present regardless. The pin is
over the key SET, not over which values are non-`None`."""
_STEPS_ELEMENT_0_KEYS = [
    "duration_s",
    "error_code",
    "fingerprint",
    "op",
    "reason",
    "run_count",
    "verdict",
    "write_bits_cleared",
    "write_bits_retained",
    "write_coverage",
    "write_current_source",
    "write_region_length",
    "write_region_start",
]

_TRACER_SHAPE_ID = "sst27sf512-six-step"
_TRACER_CANONICAL = (
    "SST27SF512|7|id=OK:|read=OK:|write=OK:indeterminate|"
    "verify=OK:indeterminate|erase=OK:|blank-check=OK:"
)

"""The literal disposition strings, per `firestarter/diagnostic_report.py`'s
four `_DISPOSITION_*` constants -- kept as bare literals here (not
imports) so LADDER_PINS is a plain module-level constant, matching the
style of FROZEN_HASHES/_PINNED_SHAPE_ID_SET above. The paired test below
imports the constants themselves and asserts the triple equality
(computed == constant == literal), the idiom at
`tests/test_diagnostic_report.py:734`."""
_DISPOSITION_COMMUNITY_FAIL_LITERAL = (
    "suggests: community-fail signal (advisory -- human triage required)"
)
_DISPOSITION_CANDIDATE_LITERAL = "suggests: candidate for community-reported (advisory)"
_DISPOSITION_INCONCLUSIVE_LITERAL = "inconclusive -- needs N>=2 agreement (advisory)"
_DISPOSITION_NO_CHANGE_LITERAL = "no change suggested (advisory)"

"""GATE-03/D-08: `build_db_diff`'s (proposed_disposition, ladder_state) pair
for every one of the sixteen frozen shapes, measured this session. All
four `build_db_diff` arms appear -- the coverage sentinel below asserts
exactly four distinct pairs, so neither a shape addition that widens the
table nor a deletion that empties an arm can pass silently (D-08, D-10's
element-wise idiom applied to arm coverage)."""
LADDER_PINS: dict[str, tuple[str, str]] = {
    "at28c256-full-all-ok-sdp": (_DISPOSITION_INCONCLUSIVE_LITERAL, ""),
    "gh20-at28c256-fail": (_DISPOSITION_COMMUNITY_FAIL_LITERAL, "community-fail"),
    "gh23-w27e257-fail": (_DISPOSITION_COMMUNITY_FAIL_LITERAL, "community-fail"),
    "gh28-m27c512-fail": (_DISPOSITION_COMMUNITY_FAIL_LITERAL, "community-fail"),
    "gh47-sst27sf512-pass": (_DISPOSITION_INCONCLUSIVE_LITERAL, ""),
    "m27c512-full-all-ok": (_DISPOSITION_CANDIDATE_LITERAL, "community-reported"),
    "m27c512-full-blank-check-bad": (
        _DISPOSITION_COMMUNITY_FAIL_LITERAL,
        "community-fail",
    ),
    "m27c512-full-canonical-name": (
        _DISPOSITION_CANDIDATE_LITERAL,
        "community-reported",
    ),
    "m27c512-full-comma-joined-name": (
        _DISPOSITION_CANDIDATE_LITERAL,
        "community-reported",
    ),
    "m27c512-full-runs-1": (_DISPOSITION_CANDIDATE_LITERAL, "community-reported"),
    "sst27sf512-full-all-ok": (_DISPOSITION_CANDIDATE_LITERAL, "community-reported"),
    "sst27sf512-six-step": (_DISPOSITION_INCONCLUSIVE_LITERAL, ""),
    "sst27sf512-six-step-readback-gated": (
        _DISPOSITION_CANDIDATE_LITERAL,
        "community-reported",
    ),
    "synthetic-arm4-empty-results": (_DISPOSITION_NO_CHANGE_LITERAL, ""),
    "synthetic-arm4-no-ok": (_DISPOSITION_NO_CHANGE_LITERAL, ""),
    "w27e257-full-all-ok": (_DISPOSITION_CANDIDATE_LITERAL, "community-reported"),
}


@pytest.mark.parametrize("shape_id,expected", sorted(FROZEN_HASHES.items()))
def test_dedup_fingerprint_is_frozen(shape_id: str, expected: str) -> None:
    """A GATE, not a claim. Pinning the literal -- rather than asserting two
    shapes agree with each other -- is what makes a re-key visible instead
    of silently forking every historical count_agreeing group."""
    from firestarter.diagnostic_report import dedup_fingerprint

    report = build_shape(shape_id)
    computed = dedup_fingerprint(report)
    assert computed == expected, (
        f"{shape_id} re-keyed: expected {expected}, got {computed}. If "
        "deliberate, declare it in tests/fixtures/rekey_ledger.py and "
        ".planning/MILESTONES.md in a SEPARATE commit (D-11)."
    )


def test_frozen_hashes_are_twelve_lowercase_hex_chars() -> None:
    for shape_id, expected in FROZEN_HASHES.items():
        assert _HEX12_RE.match(expected), (
            f"{shape_id}'s frozen hash {expected!r} is not twelve lowercase "
            "hex characters"
        )


def test_dedup_fingerprint_truncation_is_a_plain_slice() -> None:
    """The truncation is a plain character slice with no rounding and no
    tie-breaking: `dedup_fingerprint` returns the first twelve characters of
    `hashlib.sha256` over the recovered canonical pre-image, and the whole
    returned value is compared here, never a prefix of it."""
    from firestarter.diagnostic_report import dedup_fingerprint

    report = build_shape(_TRACER_SHAPE_ID)
    expected = hashlib.sha256(_TRACER_CANONICAL.encode("utf-8")).hexdigest()[:12]
    computed = dedup_fingerprint(report)
    assert computed == expected
    assert computed == FROZEN_HASHES[_TRACER_SHAPE_ID]


def test_dedup_fingerprint_moves_one_step_either_side_of_the_frozen_shape() -> None:
    from firestarter.diagnostic_report import dedup_fingerprint

    frozen = FROZEN_HASHES[_TRACER_SHAPE_ID]

    shorter = build_shape_from_step_specs(
        chip="SST27SF512",
        protocol="7",
        step_specs=[
            ("id", "OK", None, ""),
            ("read", "OK", None, ""),
            ("write", "OK", "indeterminate", ""),
            ("verify", "OK", "indeterminate", ""),
            ("erase", "OK", None, ""),
        ],
    )
    shorter_fp = dedup_fingerprint(shorter)
    assert shorter_fp != frozen

    longer = build_shape_from_step_specs(
        chip="SST27SF512",
        protocol="7",
        step_specs=[
            ("id", "OK", None, ""),
            ("read", "OK", None, ""),
            ("write", "OK", "indeterminate", ""),
            ("verify", "OK", "indeterminate", ""),
            ("erase", "OK", None, ""),
            ("blank-check", "OK", None, ""),
            ("read", "OK", None, ""),
        ],
    )
    longer_fp = dedup_fingerprint(longer)
    assert longer_fp != frozen


def test_build_db_diff_ladder_pin_for_tracer_shape() -> None:
    from firestarter.database import EpromDatabase
    from firestarter.diagnostic_report import _DISPOSITION_INCONCLUSIVE, build_db_diff

    db = EpromDatabase(skip_local_override=True)
    report = build_shape(_TRACER_SHAPE_ID)
    diff = build_db_diff(report.auto_capture.chip, db, report.results)
    assert diff.proposed_disposition == _DISPOSITION_INCONCLUSIVE
    assert (
        diff.proposed_disposition == "inconclusive -- needs N>=2 agreement (advisory)"
    )


@pytest.mark.parametrize("shape_id,expected", sorted(LADDER_PINS.items()))
def test_build_db_diff_ladder_pin_for_all_shapes(
    shape_id: str, expected: tuple[str, str]
) -> None:
    """GATE-03/D-08's new work beside the pre-existing
    `test_ladder_state_verdict_mapping` (`tests/test_diagnostic_report.py:715`,
    which already pins all four `ladder_state` values absolutely against
    the module constants): the `proposed_disposition` TEXT, bound to a
    `shape_id`. Never restates the ladder-state-only assertions that test
    already makes."""
    from firestarter.database import EpromDatabase
    from firestarter.diagnostic_report import (
        _DISPOSITION_CANDIDATE,
        _DISPOSITION_COMMUNITY_FAIL,
        _DISPOSITION_INCONCLUSIVE,
        _DISPOSITION_NO_CHANGE,
        build_db_diff,
    )

    constants_by_literal = {
        _DISPOSITION_COMMUNITY_FAIL_LITERAL: _DISPOSITION_COMMUNITY_FAIL,
        _DISPOSITION_INCONCLUSIVE_LITERAL: _DISPOSITION_INCONCLUSIVE,
        _DISPOSITION_CANDIDATE_LITERAL: _DISPOSITION_CANDIDATE,
        _DISPOSITION_NO_CHANGE_LITERAL: _DISPOSITION_NO_CHANGE,
    }
    expected_disposition, expected_ladder = expected
    constant = constants_by_literal[expected_disposition]

    db = EpromDatabase(skip_local_override=True)
    report = build_shape(shape_id)
    diff = build_db_diff(report.auto_capture.chip, db, report.results)

    assert diff.proposed_disposition == expected_disposition == constant, (
        f"{shape_id}'s proposed_disposition drifted: expected "
        f"{expected_disposition!r}, got {diff.proposed_disposition!r}"
    )
    assert diff.ladder_state == expected_ladder, (
        f"{shape_id}'s ladder_state drifted: expected {expected_ladder!r}, "
        f"got {diff.ladder_state!r}"
    )


def test_ladder_pins_cover_all_four_build_db_diff_arms() -> None:
    """A coverage sentinel: the set of distinct (disposition, ladder_state)
    pairs across LADDER_PINS must have exactly four members, so a future
    shape addition cannot quietly leave an arm unexercised and a shape
    deletion cannot quietly drop the last member of an arm."""
    distinct = set(LADDER_PINS.values())
    assert len(distinct) == 4, (
        f"LADDER_PINS covers {len(distinct)} distinct (disposition, "
        f"ladder_state) pairs, expected all four build_db_diff arms; got "
        f"{sorted(distinct)}"
    )


def test_to_dict_top_level_key_list_is_pinned() -> None:
    report = build_shape(_TRACER_SHAPE_ID)
    keys = sorted(report.to_dict())
    assert keys == _TO_DICT_KEYS, (
        f"to_dict() top-level keys drifted from the pinned D-07 shape; "
        f"expected {_TO_DICT_KEYS}, got {keys}"
    )


def _to_dict_with_db_diff(shape_id: str) -> dict:
    """`db_diff` is `None` on a bare `build_shape()` report -- populated
    only after `build_db_diff` is composed onto it, exactly as
    `tools/snapshot_report_shapes.py:render_shape` does. The D-07 db_diff
    key-list pin needs a populated `db_diff`, so this helper mirrors that
    composition rather than maintaining a second copy of it."""
    from firestarter.database import EpromDatabase
    from firestarter.diagnostic_report import build_db_diff

    report = build_shape(shape_id)
    db = EpromDatabase(skip_local_override=True)
    composed = _dataclass_replace(
        report,
        db_diff=build_db_diff(report.auto_capture.chip, db, report.results),
    )
    return composed.to_dict()


def test_to_dict_voltage_key_list_is_pinned() -> None:
    d = build_shape(_TRACER_SHAPE_ID).to_dict()
    keys = sorted(d["voltage"])
    assert keys == _VOLTAGE_KEYS, (
        f"to_dict()['voltage'] keys drifted from the pinned D-07 shape; "
        f"expected {_VOLTAGE_KEYS}, got {keys}"
    )


def test_to_dict_banner_key_list_is_pinned() -> None:
    d = build_shape(_TRACER_SHAPE_ID).to_dict()
    keys = sorted(d["banner"])
    assert keys == _BANNER_KEYS, (
        f"to_dict()['banner'] keys drifted from the pinned D-07 shape; "
        f"expected {_BANNER_KEYS}, got {keys}"
    )


def test_to_dict_auto_capture_key_list_is_pinned() -> None:
    d = build_shape(_TRACER_SHAPE_ID).to_dict()
    keys = sorted(d["auto_capture"])
    assert keys == _AUTO_CAPTURE_KEYS, (
        f"to_dict()['auto_capture'] keys drifted from the pinned D-07 shape; "
        f"expected {_AUTO_CAPTURE_KEYS}, got {keys}"
    )
    assert "canonical_part_number" not in keys, (
        "auto_capture gained canonical_part_number -- RPT-F1 landed; "
        "update _AUTO_CAPTURE_KEYS deliberately in the same commit"
    )


def test_to_dict_transport_health_key_list_is_pinned() -> None:
    d = build_shape(_TRACER_SHAPE_ID).to_dict()
    keys = sorted(d["transport_health"])
    assert keys == _TRANSPORT_HEALTH_KEYS, (
        f"to_dict()['transport_health'] keys drifted from the pinned D-07 "
        f"shape; expected {_TRANSPORT_HEALTH_KEYS}, got {keys}"
    )


def test_to_dict_db_diff_key_list_is_pinned() -> None:
    d = _to_dict_with_db_diff(_TRACER_SHAPE_ID)
    keys = sorted(d["db_diff"])
    assert keys == _DB_DIFF_KEYS, (
        f"to_dict()['db_diff'] keys drifted from the pinned D-07 shape; "
        f"expected {_DB_DIFF_KEYS}, got {keys}"
    )


def test_to_dict_steps_element_0_key_list_is_pinned() -> None:
    d = build_shape(_TRACER_SHAPE_ID).to_dict()
    keys = sorted(d["steps"][0])
    assert keys == _STEPS_ELEMENT_0_KEYS, (
        f"to_dict()['steps'][0] keys drifted from the pinned D-07 shape; "
        f"expected {_STEPS_ELEMENT_0_KEYS}, got {keys}"
    )


def test_schema_version_is_pinned() -> None:
    """The triple-equality idiom (`tests/test_diagnostic_report.py:734`):
    the imported constant, the literal, and the value `to_dict()` actually
    bakes in, all in one expression, so a constant rename and a value
    change are both caught. Phase 181 moves this to `2.0` per D-3/RPT-E1,
    and it has to move this line to do it."""
    from firestarter.diagnostic_report import SCHEMA_VERSION

    report = build_shape(_TRACER_SHAPE_ID)
    baked = report.to_dict()["schema_version"]
    assert SCHEMA_VERSION == "1.7" == baked, (
        f"SCHEMA_VERSION drifted: constant={SCHEMA_VERSION!r}, baked="
        f"{baked!r}, expected '1.7' (RPT-E1 moves this to '2.0' in Phase 181)"
    )


def test_to_dict_key_list_pins_are_sensitive_to_added_and_removed_keys() -> None:
    """The anti-vacuity leg for the seven D-07 pins above: an in-process
    mutation of a real `to_dict()` mapping, never a change to production
    code. Deleting one key must move the sorted key list away from the
    pinned constant, and adding one must too -- proving the element-wise
    comparisons above are sensitive in both directions, not merely
    coincidentally correct on the shape as built. Observed output
    transcribed in
    `.planning/phases/174-blast-radius-invariance-harness/evidence/
    174-03-schema-pins.txt`."""
    report = build_shape(_TRACER_SHAPE_ID)
    d = report.to_dict()
    assert sorted(d) == _TO_DICT_KEYS

    removed = dict(d)
    del removed["voltage"]
    assert sorted(removed) != _TO_DICT_KEYS, (
        "deleting a top-level key did not move the sorted key list away "
        "from the pinned constant -- the pin is vacuous"
    )

    added = dict(d)
    added["canonical_part_number"] = None
    assert sorted(added) != _TO_DICT_KEYS, (
        "adding a top-level key did not move the sorted key list away "
        "from the pinned constant -- the pin is vacuous"
    )


_PINNED_SHAPE_ID_SET = [
    "at28c256-full-all-ok-sdp",
    "gh20-at28c256-fail",
    "gh23-w27e257-fail",
    "gh28-m27c512-fail",
    "gh47-sst27sf512-pass",
    "m27c512-full-all-ok",
    "m27c512-full-blank-check-bad",
    "m27c512-full-canonical-name",
    "m27c512-full-comma-joined-name",
    "m27c512-full-runs-1",
    "sst27sf512-full-all-ok",
    "sst27sf512-six-step",
    "sst27sf512-six-step-readback-gated",
    "synthetic-arm4-empty-results",
    "synthetic-arm4-no-ok",
    "w27e257-full-all-ok",
]


def test_shape_id_set_is_pinned_and_disjoint_from_reserved() -> None:
    assert sorted(SHAPE_IDS) == _PINNED_SHAPE_ID_SET, (
        f"SHAPE_IDS drifted from the D-10 pinned set; expected "
        f"{_PINNED_SHAPE_ID_SET}, got {sorted(SHAPE_IDS)}"
    )
    assert not (set(SHAPE_IDS) & RESERVED_SHAPE_IDS)


def test_gh20_shape_reproduces_the_shared_three_issue_fingerprint() -> None:
    """`00e121446ceb` is a REAL three-member dedup group in the wild --
    gh#20, gh#21 and gh#32, all at28c256 -- and `count_agreeing`
    (`tools/parse_devtest_issue.py:164`) reads the embedded hash and NEVER
    re-hashes. A re-key of `gh20-at28c256-fail` therefore does not merely
    change a string; it resets that group's promotion count permanently.
    This deserves to fail by name rather than as one row of the frozen
    table."""
    from firestarter.diagnostic_report import dedup_fingerprint

    report = build_shape("gh20-at28c256-fail")
    assert (
        dedup_fingerprint(report)
        == "00e121446ceb"
        == FROZEN_HASHES["gh20-at28c256-fail"]
    ), (
        "gh20-at28c256-fail no longer reproduces the fingerprint shared by "
        "gh#20, gh#21 and gh#32 -- a re-key here resets that three-member "
        "dedup group's count_agreeing promotion count permanently"
    )


@pytest.mark.parametrize("shape_id", sorted(SHAPE_IDS))
def test_committed_snapshot_matches_a_fresh_regeneration(shape_id: str) -> None:
    """WR-01: every committed snapshot is byte-compared against a fresh
    regeneration, not just the tracer. The other fifteen were previously
    checked for filename-stem existence only, so a builder or `to_dict()`
    change moving a non-`dedup_fingerprint` field on any of them went
    undetected under `pytest` -- `snapshot_report_shapes.py --check` did catch
    it, but nothing in the suite or in CI invoked that script."""
    from tools.snapshot_report_shapes import render_shape

    target = Path(__file__).parent / "fixtures" / "reports" / f"{shape_id}.json"
    committed = json.loads(target.read_text(encoding="utf-8"))
    assert committed["_generated_by"] == "tools/snapshot_report_shapes.py"
    assert committed["generated"] == "1970-01-01T00:00:00Z"
    fresh = json.loads(render_shape(shape_id))
    assert committed == fresh, (
        f"committed snapshot for {shape_id!r} drifted from a fresh "
        f"regeneration; re-run tools/snapshot_report_shapes.py if the change "
        f"is declared, or revert the builder change if it is not"
    )


def test_planted_mutation_clearing_write_fingerprint_reddens_the_gate() -> None:
    """Leg 2 of the anti-vacuity contract, in-process axis 1: clearing the
    write step's fingerprint is the exact change Phase 177 will make when it
    gates the read-back on failure, so this is a rehearsal of the declared
    re-key, not a synthetic poke. Asserts inequality against the frozen
    literal, never against a second computed value."""
    from firestarter.diagnostic_report import dedup_fingerprint

    report = build_shape(_TRACER_SHAPE_ID)
    write_result = next(r for r in report.results if r.op == "write")
    write_result.fingerprint = None
    assert dedup_fingerprint(report) != FROZEN_HASHES[_TRACER_SHAPE_ID]


def test_planted_mutation_lowering_chip_name_reddens_the_gate() -> None:
    """Leg 2, in-process axis 2: mutating `auto_capture.chip` shows the gate
    sensitive on the OTHER axis `dedup_fingerprint` reads, not only the
    per-step axis the sibling test above exercises. Asserts inequality
    against the frozen literal, never against a second computed value."""
    from firestarter.diagnostic_report import dedup_fingerprint

    report = build_shape(_TRACER_SHAPE_ID)
    report.auto_capture.chip = "sst27sf512"
    assert dedup_fingerprint(report) != FROZEN_HASHES[_TRACER_SHAPE_ID]


_ALIASED_SHAPE_IDS = (
    "m27c512-full-all-ok",
    "m27c512-full-canonical-name",
    "m27c512-full-comma-joined-name",
)


@pytest.mark.parametrize("shape_id", sorted(SHAPE_IDS))
def test_composing_a_db_diff_never_leaks_onto_a_cached_build_shape(
    shape_id: str,
) -> None:
    """CR-01, second aliasing path: composing a `DbDiff` must land on a copy,
    never on the object `build_shape()` returns. Six builders are
    `functools.cache`-decorated, so that object is shared by every call for
    the same id -- an in-place `report.db_diff =` assignment persists into
    every later `build_shape(shape_id)` and breaks the documented
    `db_diff is None`-on-a-bare-build invariant. The ordering is the whole
    point: both composing call sites run FIRST, then a bare build is
    inspected, which is exactly the sequence that previously masked the bug
    behind test ordering. Swept over all sixteen shape ids rather than the
    six currently cached, so the leg cannot go stale if a cache decorator is
    added to or removed from a builder."""
    from tools.snapshot_report_shapes import render_shape

    render_shape(shape_id)
    _to_dict_with_db_diff(shape_id)
    assert build_shape(shape_id).db_diff is None, (
        f"composing a DbDiff leaked onto the cached build_shape({shape_id!r}) "
        f"object; a bare build must always carry db_diff=None"
    )


def test_build_shape_never_shares_results_or_plan_between_shape_ids() -> None:
    """CR-01: `_clone_with_chip_override` (used by the two D-2
    canonical-naming alternatives) must not hand two different
    `shape_id`s the same `results` or `plan` object -- a mutation leg on
    one shape must not be silently writing to another. Swept over every
    ordered pair of the three affected shape_ids on both attributes."""
    reports = {sid: build_shape(sid) for sid in _ALIASED_SHAPE_IDS}
    for sid_a in _ALIASED_SHAPE_IDS:
        for sid_b in _ALIASED_SHAPE_IDS:
            if sid_a == sid_b:
                continue
            assert reports[sid_a].results is not reports[sid_b].results, (
                f"{sid_a!r} and {sid_b!r} share the same results object"
            )
            assert reports[sid_a].plan is not reports[sid_b].plan, (
                f"{sid_a!r} and {sid_b!r} share the same plan object"
            )


def test_mutation_through_a_derived_shape_does_not_move_the_base_shapes_frozen_hash() -> (
    None
):
    """CR-01's collateral false-RED risk: mutating a freshly built
    `m27c512-full-canonical-name` clone's first `StepResult.verdict` must
    not move `m27c512-full-all-ok`'s frozen hash. Mutates only through the
    UNCACHED derivative, never through the cached base builder itself, and
    reads the expected literal off FROZEN_HASHES rather than hand-typing
    it."""
    from firestarter.diagnostic_report import dedup_fingerprint

    clone = build_shape("m27c512-full-canonical-name")
    clone.results[0].verdict = "BAD"
    assert (
        dedup_fingerprint(build_shape("m27c512-full-all-ok"))
        == FROZEN_HASHES["m27c512-full-all-ok"]
    )


_SHAPE_IDS_JSON = Path(__file__).parent / "fixtures" / "shape_ids.json"


def _committed_shape_ids() -> list[str]:
    return json.loads(_SHAPE_IDS_JSON.read_text(encoding="utf-8"))["shape_ids"]


def test_shape_ids_committed_anchor_matches_the_registry() -> None:
    """D-10's element-wise pin against the committed sorted anchor in
    `tests/fixtures/shape_ids.json` -- LIST equality, not set equality, so
    a DUPLICATE entry in the committed list is also caught, the same
    reason `test_to_dict_top_level_key_list_is_pinned` never uses a
    membership check."""
    committed = _committed_shape_ids()
    assert committed == sorted(SHAPE_IDS), (
        f"tests/fixtures/shape_ids.json drifted from SHAPE_IDS; expected "
        f"{sorted(SHAPE_IDS)}, committed anchor has {committed}"
    )


def test_shape_ids_frozen_hashes_ladder_pins_and_snapshots_agree() -> None:
    """D-10's four-way closure, modelled on
    `tests/test_chip_test_sdp_leg.py:827`
    (`test_shipped_ops_never_reach_sdp_arm`): the committed anchor pinned
    above is the hand-written enumeration half; these three set-equality
    checks are the derivations -- copying BOTH halves of that idiom,
    since either alone is escapable. Seven of this milestone's eight
    phases re-key something, which makes deleting a row the cheapest
    route past a RED; this test is what makes that route closed. Each
    assertion's message names the symmetric difference in both
    directions, so it says which names were added and which were
    dropped, not merely that the sets differ."""
    reports_dir = Path(__file__).parent / "fixtures" / "reports"
    snapshot_stems = {p.stem for p in reports_dir.glob("*.json")}
    shape_id_set = set(SHAPE_IDS)

    assert shape_id_set == set(FROZEN_HASHES), (
        "SHAPE_IDS and FROZEN_HASHES disagree; symmetric difference: "
        f"{sorted(shape_id_set.symmetric_difference(FROZEN_HASHES))}"
    )
    assert shape_id_set == set(LADDER_PINS), (
        "SHAPE_IDS and LADDER_PINS disagree; symmetric difference: "
        f"{sorted(shape_id_set.symmetric_difference(LADDER_PINS))}"
    )
    assert shape_id_set == snapshot_stems, (
        "SHAPE_IDS and the committed snapshot filenames under "
        "tests/fixtures/reports/ disagree; symmetric difference: "
        f"{sorted(shape_id_set.symmetric_difference(snapshot_stems))}"
    )


def test_build_shape_raises_for_every_reserved_shape_id() -> None:
    """A reserved name must not silently return an empty report -- that is
    how a placeholder becomes a frozen value by accident (D-04). Probed
    against all three `RESERVED_SHAPE_IDS` names, not just one."""
    for reserved_id in sorted(RESERVED_SHAPE_IDS):
        with pytest.raises(KeyError):
            build_shape(reserved_id)


def test_shape_ids_closure_is_sensitive_to_removed_and_added_entries() -> None:
    """The anti-vacuity leg for D-10's four-way closure: an in-process
    mutation of a local copy of the committed anchor, never a change to
    the committed file on disk. Removing one entry and separately adding
    one must both move the mutated list away from `sorted(SHAPE_IDS)`."""
    committed = _committed_shape_ids()

    removed = list(committed)
    removed.pop()
    assert removed != sorted(SHAPE_IDS), (
        "removing one entry from a copy of the committed anchor did not "
        "move it away from sorted(SHAPE_IDS) -- the closure pin is vacuous"
    )

    added = sorted([*committed, "bogus-added-shape-id"])
    assert added != sorted(SHAPE_IDS), (
        "appending one entry to a copy of the committed anchor did not "
        "move it away from sorted(SHAPE_IDS) -- the closure pin is vacuous"
    )
