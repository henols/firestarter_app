"""
26-of-26 filed `[dev test]` issue reproduction gate (Phase 174, D-06, plan
174-04 task 2).

D-06 originally scoped builder reproduction to four named chips
(`m27c512`, `sst27sf512`, `at28c256`, `w27e257`) because several of the 27
then-believed filed rows would have needed issue bodies parsed at test
time -- the loader `tests/fixtures/report_shapes.py`'s D-01 declines to
build. That cause is gone: `tools/build_devtest_issue_corpus.py` commits
each row's `steps`, `run_counts` and `coverage_tag` as DATA, so this module
parses no issue body and still writes no report deserializer. The
enabling discovery, corrected during this session and by the operator's
2026-09-03 ratification, is that a step's `fingerprint` value in a filed
issue's fenced JSON serialises as a BARE CLASSIFICATION STRING, not an
object with a classification key -- read as an object, 0 of 26 rows
reproduce; read as a string, 26 of 26 do. The corrected count is 26 filed
`[dev test]` issues, not 27 -- measured by title prefix, since the
`dev-test` label covers only 15 of them.

The widening applies to the corpus reproduction assertion only. The four
chips D-06 names keep their dedicated builder shapes in
`tests/fixtures/report_shapes.py` and `SHAPE_IDS` is unchanged by this
module.

Two properties of this module's construction are load-bearing.

First, no report deserializer is involved anywhere in this file: every
row's `steps` triples feed
`tests.fixtures.report_shapes.build_shape_from_step_specs`, the same
generalized `_minimal_report` builder every other shape in this phase uses,
so the object under test is constructed the way production constructs it
and the hash is taken off a real report by the real
`firestarter.diagnostic_report.dedup_fingerprint`.

Second, the two per-report discriminator tags are never appended as
strings. Each row records which tag value applied to it, and this module
reaches those values by stamping the row's `run_counts` and
`coverage_tag` back onto the builder's `run_counts`/`coverage_policy`
seams so the real `firestarter.chip_test.repeat_policy_tag` and
`firestarter.chip_test.coverage_tag` compute them. A test that appended
the tag text by hand would pin the corpus against itself rather than
against production.

`00e121446ceb` (gh#20, gh#21, gh#32, at28c256, N=3) and `334c3fa198bf`
(gh#39, gh#40, at28c256, N=2) are real dedup groups
`tools/parse_devtest_issue.py:count_agreeing` reads off the EMBEDDED
`dedup_fingerprint` and never re-hashes. A re-key does not merely change a
string for these groups -- it resets `count_agreeing`'s promotion count,
and the three-member group falls back to one. Both are asserted by name
below, and by a third test proving they are the only groups of size two or
more in the 26-row corpus.

Reachability (anti-vacuity, planted mutation, observed RED -- transcribed
verbatim from a real run of this module, not asserted from reasoning
alone):

  gh#47 step 0 (id) verdict flip OK -> BAD:
    filed=f9dbc31dcd27 mutated=5e555011e0d9
  gh#47 write-step classification flip indeterminate -> match:
    filed=f9dbc31dcd27 mutated=07aa99a87c4f
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from firestarter.chip_test import COVERAGE_TAG_FULL_DEVICE, REGION_POLICY_FULL_DEVICE
from firestarter.diagnostic_report import dedup_fingerprint
from tests.fixtures.report_shapes import build_shape_from_step_specs

_CORPUS_PATH = Path(__file__).parent / "fixtures" / "devtest_issue_corpus.json"


def _load_corpus(path: Path) -> list[dict]:
    """Raise on a missing or unparsable path -- never return an empty list,
    which would make the parametrized reproduction gate below collect zero
    cases and report green."""
    if not path.is_file():
        raise FileNotFoundError(f"devtest issue corpus not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["rows"]


_ROWS = _load_corpus(_CORPUS_PATH)


def _build_report_for_row(row: dict):
    step_specs = [(op, verdict, cls, "") for op, verdict, cls in row["steps"]]
    coverage_policy = (
        REGION_POLICY_FULL_DEVICE
        if row["coverage_tag"] == COVERAGE_TAG_FULL_DEVICE
        else None
    )
    return build_shape_from_step_specs(
        chip=row["chip"],
        protocol=row["protocol"],
        step_specs=step_specs,
        run_counts=row.get("run_counts"),
        coverage_policy=coverage_policy,
    )


def test_reproduction_gate_collects_all_26_cases():
    """A corpus that shrank must not pass by collecting fewer parametrized
    cases than the committed row count."""
    assert len(_ROWS) == 26


@pytest.mark.parametrize("row", _ROWS, ids=lambda r: f"gh{r['issue']}")
def test_filed_fingerprint_reproduces_through_the_real_dedup_fingerprint(row):
    """A GATE, not a claim. Reproducing the filed hash off a real report
    built from the row's committed step vector -- rather than trusting the
    embedded literal -- is what makes a re-key visible instead of silently
    forking gh#20/gh#21/gh#32's promotion count."""
    report = _build_report_for_row(row)
    got = dedup_fingerprint(report)
    assert got == row["filed_hash"], (
        f"gh#{row['issue']} ({row['chip']}) re-keyed: filed "
        f"{row['filed_hash']}, recomputed {got}. If deliberate, declare it "
        f"in the ledger in a SEPARATE commit (D-11)."
    )


_D06_NAMED_CHIP_ISSUES = {
    "m27c512": {28},
    "sst27sf512": {47},
    "at28c256": {20, 21, 32, 39, 40},
    "w27e257": {23, 24},
}


def test_d06_named_chips_are_covered_by_dedicated_corpus_rows():
    """D-06 names four chips whose builder shapes must reproduce a filed
    hash. Each must be present in the widened 26-row corpus under its
    named issue number(s), so a corpus regeneration that dropped one of
    the four reddens by chip name rather than by a generic count drift."""
    by_issue = {row["issue"]: row for row in _ROWS}
    for chip, issues in _D06_NAMED_CHIP_ISSUES.items():
        for issue in issues:
            assert issue in by_issue, f"D-06 chip {chip!r}: issue #{issue} missing from corpus"
            assert by_issue[issue]["chip"] == chip, (
                f"D-06 chip {chip!r}: issue #{issue} has chip "
                f"{by_issue[issue]['chip']!r} instead"
            )


def test_at28c256_three_member_dedup_group_00e121446ceb():
    """gh#20/gh#21/gh#32 form the real three-member `count_agreeing` group
    a re-key of the at28c256 shape resets to N=1."""
    group = sorted(row["issue"] for row in _ROWS if row["filed_hash"] == "00e121446ceb")
    assert group == [20, 21, 32]


def test_at28c256_two_member_dedup_group_334c3fa198bf():
    """gh#39/gh#40 form the real two-member `count_agreeing` group a re-key
    of the at28c256 shape resets to N=1."""
    group = sorted(row["issue"] for row in _ROWS if row["filed_hash"] == "334c3fa198bf")
    assert group == [39, 40]


def test_only_two_dedup_groups_have_n_gte_2():
    """The only groups of size two or more in the 26-row corpus are the
    three-member and two-member at28c256 groups above -- no other filed
    hash repeats."""
    counts = Counter(row["filed_hash"] for row in _ROWS)
    groups_of_2_or_more = sorted(n for n in counts.values() if n >= 2)
    assert groups_of_2_or_more == [2, 3]


def test_planted_verdict_flip_reddens_the_gate():
    """Anti-vacuity leg 1: flipping one step's verdict in an in-memory
    copy of a filed row must make its recomputation differ from
    `filed_hash`. The gate must be SEEN to go RED -- a gate authored before
    the content it guards can be unreachable and prove nothing."""
    row = copy.deepcopy(next(r for r in _ROWS if r["issue"] == 47))
    assert row["steps"][0][1] == "OK", (
        "fixture assumption stale -- gh#47 step 0 verdict is no longer "
        "'OK'; update this planted-fault value"
    )
    row["steps"][0][1] = "BAD"
    report = _build_report_for_row(row)
    mutated = dedup_fingerprint(report)
    assert mutated != row["filed_hash"], (
        f"planted verdict flip did not redden the gate: still {mutated}"
    )


def test_planted_classification_flip_reddens_the_gate():
    """Anti-vacuity leg 1, second field: flipping a step's fingerprint
    classification in an in-memory copy of a filed row must also make its
    recomputation differ, showing the gate is sensitive on both fields
    `dedup_fingerprint` reads per step."""
    row = copy.deepcopy(next(r for r in _ROWS if r["issue"] == 47))
    idx = next(i for i, s in enumerate(row["steps"]) if s[2] is not None)
    assert row["steps"][idx][2] == "indeterminate", (
        "fixture assumption stale -- gh#47's first classified step is no "
        "longer 'indeterminate'; update this planted-fault value"
    )
    row["steps"][idx][2] = "match"
    report = _build_report_for_row(row)
    mutated = dedup_fingerprint(report)
    assert mutated != row["filed_hash"], (
        f"planted classification flip did not redden the gate: still {mutated}"
    )


def test_loader_raises_for_nonexistent_path(tmp_path):
    """Fail-closed leg 1: a missing corpus path must raise, never return an
    empty row list."""
    with pytest.raises(FileNotFoundError):
        _load_corpus(tmp_path / "does-not-exist.json")


def test_loader_raises_for_non_json_file(tmp_path):
    """Fail-closed leg 2: an unparsable corpus file must raise, never
    return an empty row list."""
    bad = tmp_path / "not-json.json"
    bad.write_text("this is not { valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        _load_corpus(bad)
