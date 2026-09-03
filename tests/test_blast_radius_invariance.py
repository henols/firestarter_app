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
"""

from __future__ import annotations

import hashlib
import json
import re
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

_TRACER_SHAPE_ID = "sst27sf512-six-step"
_TRACER_CANONICAL = (
    "SST27SF512|7|id=OK:|read=OK:|write=OK:indeterminate|"
    "verify=OK:indeterminate|erase=OK:|blank-check=OK:"
)


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
    assert diff.proposed_disposition == "inconclusive -- needs N>=2 agreement (advisory)"


def test_to_dict_top_level_key_list_is_pinned() -> None:
    report = build_shape(_TRACER_SHAPE_ID)
    keys = sorted(report.to_dict())
    assert keys == _TO_DICT_KEYS, (
        f"to_dict() top-level keys drifted from the pinned D-07 shape; "
        f"expected {_TO_DICT_KEYS}, got {keys}"
    )


_PINNED_SHAPE_ID_SET = [
    "gh20-at28c256-fail",
    "gh23-w27e257-fail",
    "gh28-m27c512-fail",
    "gh47-sst27sf512-pass",
    "sst27sf512-six-step",
    "sst27sf512-six-step-readback-gated",
    "synthetic-arm4-empty-results",
    "synthetic-arm4-no-ok",
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
    assert dedup_fingerprint(report) == "00e121446ceb" == FROZEN_HASHES["gh20-at28c256-fail"], (
        "gh20-at28c256-fail no longer reproduces the fingerprint shared by "
        "gh#20, gh#21 and gh#32 -- a re-key here resets that three-member "
        "dedup group's count_agreeing promotion count permanently"
    )


def test_committed_snapshot_matches_a_fresh_regeneration() -> None:
    from tools.snapshot_report_shapes import render_shape

    target = Path(__file__).parent / "fixtures" / "reports" / f"{_TRACER_SHAPE_ID}.json"
    committed = json.loads(target.read_text(encoding="utf-8"))
    assert committed["_generated_by"] == "tools/snapshot_report_shapes.py"
    assert committed["generated"] == "1970-01-01T00:00:00Z"
    fresh = json.loads(render_shape(_TRACER_SHAPE_ID))
    assert committed == fresh


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
