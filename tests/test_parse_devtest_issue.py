"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Unit tests for `tools/parse_devtest_issue.py` (v1.21 Phase 114 INBOX-01).

Bench-free, `gh`-free: every fixture is either a directly-hand-built body
string (malformed/negative cases) or a realistic body built from a REAL
`DiagnosticReport.to_json_block()` / `submit.py` title+body builder pair
(the exact shape a community tester's issue actually carries), so the
fixtures stay faithful to the shipped schema rather than a hand-guessed
approximation.

Test taxonomy (RESEARCH Test Map):
  DETECT    -- test_detect_*      (title marker + fenced schema_version,
                                    both required; missing either -> None)
  DB-DIFF   -- test_db_diff_*     (extract_db_diff surface incl. ladder_state,
                                    tolerant of a schema-1.0-shaped db_diff)
  AGREEING  -- test_agreeing_*    (count_agreeing groups by embedded
                                    dedup_fingerprint, D-03)
  MALFORMED -- test_malformed_*   (oversized / truncated / missing JSON ->
                                    None, never raises)
"""

from __future__ import annotations

from firestarter.chip_test import VERDICT_OK, Plan, StepResult
from firestarter.database import EpromDatabase
from firestarter.diagnostic_report import (
    AutoCapture,
    DiagnosticReport,
    TransportHealth,
    build_db_diff,
)
from firestarter.submit import build_body, build_title, sanitize_dict
from tools.parse_devtest_issue import (
    _MAX_BODY_BYTES,
    count_agreeing,
    extract_db_diff,
    parse_devtest_body,
)

_REAL_DB = EpromDatabase(skip_local_override=True)


def _build_realistic_title_body(
    chip: str = "M8720", *, protocol: str = "0x08"
) -> tuple[str, str]:
    """Build a (title, body) pair via the REAL production builders
    (`submit.py:build_title`/`build_body`, `diagnostic_report.py`'s own
    `to_dict()`/`build_db_diff`) -- the exact shape a community tester's
    issue actually carries, never a hand-approximated stand-in."""
    results = [
        StepResult(
            op="id", verdict=VERDICT_OK, reason="chip id matched", fingerprint=None
        ),
        StepResult(op="read", verdict=VERDICT_OK, reason="", fingerprint=None),
    ]
    auto_capture = AutoCapture(host_version="3.0.0b10", chip=chip, protocol=protocol)
    report = DiagnosticReport(
        auto_capture=auto_capture,
        transport=TransportHealth(),
        plan=Plan(name=chip),
        results=results,
    )
    report.db_diff = build_db_diff(chip, _REAL_DB, results)

    sanitized = sanitize_dict(report.to_dict())
    title = build_title(report, chip)
    body = build_body(sanitized, report.results, include_json=True)
    return title, body


# ---------------------------------------------------------------------------
# DETECT (D-04) -- both markers required
# ---------------------------------------------------------------------------


def test_detect_realistic_dev_test_body_parses():
    title, body = _build_realistic_title_body()
    obj = parse_devtest_body(title, body)

    assert obj is not None
    # 1.2 (Phase 121 Plan 07, D-06): bumped for the seventh op string
    # (write-partial); this test builds a report via the CURRENT builders,
    # so it reflects the CURRENT SCHEMA_VERSION -- the frozen b11-shaped
    # ("1.1") fixture lives in the LEGACY section below and is never
    # regenerated from live code.
    assert obj["schema_version"] == "1.2"
    assert obj["auto_capture"]["chip"] == "M8720"


def test_detect_missing_title_marker_returns_none():
    _title, body = _build_realistic_title_body()
    obj = parse_devtest_body("M8720 report, please review", body)

    assert obj is None


def test_detect_missing_fenced_block_returns_none():
    title, _body = _build_realistic_title_body()
    obj = parse_devtest_body(
        title, "| Step | Verdict |\n| ---- | ------- |\n| id | OK |"
    )

    assert obj is None


def test_detect_fence_without_schema_version_returns_none():
    title, _body = _build_realistic_title_body()
    body = '```json\n{"chip": "M8720", "verdict": "PASS"}\n```'
    obj = parse_devtest_body(title, body)

    assert obj is None


def test_detect_schema_version_matched_by_presence_not_exact_value():
    """A future schema bump (e.g. 1.0 -> 1.1 -> 1.2) must not break
    detection -- only PRESENCE of the key is checked (D-04)."""
    title, _body = _build_realistic_title_body()
    body = '```json\n{"schema_version": "9.9-future", "auto_capture": {}}\n```'
    obj = parse_devtest_body(title, body)

    assert obj is not None
    assert obj["schema_version"] == "9.9-future"


def test_detect_empty_title_and_body_return_none():
    assert parse_devtest_body("", "") is None
    assert parse_devtest_body(None, None) is None


# ---------------------------------------------------------------------------
# DB-DIFF -- extract_db_diff surface, incl. ladder_state
# ---------------------------------------------------------------------------


def test_db_diff_surface_from_realistic_report():
    title, body = _build_realistic_title_body()
    obj = parse_devtest_body(title, body)

    diff = extract_db_diff(obj)

    assert diff["current_support_status"] == "supported"
    # Both id/read OK-only -> build_db_diff's all-OK candidate branch.
    assert "candidate" in diff["proposed_disposition"]
    assert diff["ladder_state"] == "community-reported"
    assert diff["dedup_fingerprint"] == obj["dedup_fingerprint"]


def test_db_diff_bad_verdict_maps_to_community_fail_ladder_state():
    from firestarter.chip_test import VERDICT_BAD

    results = [
        StepResult(op="write", verdict=VERDICT_BAD, reason="mismatch", fingerprint=None)
    ]
    diff_obj = build_db_diff("M8720", _REAL_DB, results)

    assert diff_obj.ladder_state == "community-fail"
    assert "fail" in diff_obj.proposed_disposition


def test_db_diff_tolerates_missing_db_diff_key():
    """A degenerate report dict with no db_diff key at all (or None) must
    not raise -- defaults are supplied (D-01/D-02)."""
    diff = extract_db_diff({"schema_version": "1.0", "db_diff": None})

    assert diff["current_support_status"] == "supported"
    assert diff["proposed_disposition"] == ""
    assert diff["ladder_state"] == ""
    assert diff["dedup_fingerprint"] == ""


def test_db_diff_tolerates_schema_1_0_shape_without_ladder_state():
    """Schema 1.0 db_diff has no `ladder_state` key at all -- must default
    to "" rather than KeyError (backward compatibility)."""
    schema_1_0_report = {
        "schema_version": "1.0",
        "dedup_fingerprint": "aaaa11112222",
        "db_diff": {
            "current_support_status": "supported",
            "proposed_disposition": "suggests: candidate for community-reported (advisory)",
        },
    }
    diff = extract_db_diff(schema_1_0_report)

    assert diff["ladder_state"] == ""
    assert diff["current_support_status"] == "supported"
    assert diff["dedup_fingerprint"] == "aaaa11112222"


# ---------------------------------------------------------------------------
# AGREEING (D-03, GRAD-01) -- dedup_fingerprint grouping, distinct from
# Phase-108's per-run N>=2
# ---------------------------------------------------------------------------


def test_agreeing_two_matching_one_differing_yields_count_2():
    _title_a, body_a = _build_realistic_title_body(chip="M8720")
    _title_b, body_b = _build_realistic_title_body(
        chip="M8720"
    )  # same shape -> same fp
    _title_c, body_c = _build_realistic_title_body(
        chip="W27C512"
    )  # different chip -> diff fp

    counts = count_agreeing([body_a, body_b, body_c])

    fp_shared = None
    for fingerprint, n in counts.items():
        if n == 2:
            fp_shared = fingerprint
    assert fp_shared is not None, f"expected a fingerprint with count 2, got {counts}"
    assert len(counts) == 2  # one shared (count 2), one distinct (count 1)
    assert sorted(counts.values()) == [1, 2]


def test_agreeing_derived_from_embedded_dedup_fingerprint_not_run_counts():
    """Proves the grouping key is the report's OWN embedded
    dedup_fingerprint (never re-hashed, never a per-step run count) --
    two bodies sharing a fabricated fingerprint group together even
    though every other field differs."""
    body_a = (
        '```json\n{"schema_version": "1.1", "dedup_fingerprint": "shared0000ab", '
        '"steps": [{"op": "id", "verdict": "OK", "run_count": 1}]}\n```'
    )
    body_b = (
        '```json\n{"schema_version": "1.1", "dedup_fingerprint": "shared0000ab", '
        '"steps": [{"op": "write", "verdict": "BAD", "run_count": 5}]}\n```'
    )

    counts = count_agreeing([body_a, body_b])

    assert counts == {"shared0000ab": 2}


def test_agreeing_skips_non_dev_test_bodies():
    _title, real_body = _build_realistic_title_body()
    not_a_report = "just a regular issue comment with no JSON at all"
    malformed = '```json\n{"schema_version": "1.1", oops\n```'

    counts = count_agreeing([real_body, not_a_report, malformed])

    assert sum(counts.values()) == 1


def test_agreeing_empty_input_returns_empty_dict():
    assert count_agreeing([]) == {}


# ---------------------------------------------------------------------------
# MALFORMED (T-114-03/T-114-04, RESEARCH Pitfall 6) -- fail-soft, never raises
# ---------------------------------------------------------------------------


def test_malformed_oversized_body_returns_none():
    title, _body = _build_realistic_title_body()
    huge_json = (
        '{"schema_version": "1.1", "pad": "' + ("x" * (_MAX_BODY_BYTES + 1000)) + '"}'
    )
    body = "```json\n" + huge_json + "\n```"

    obj = parse_devtest_body(title, body)

    assert obj is None


def test_malformed_truncated_json_fence_returns_none():
    title, _body = _build_realistic_title_body()
    body = '```json\n{"schema_version": "1.1", "auto_capture": {\n```'

    obj = parse_devtest_body(title, body)

    assert obj is None


def test_malformed_no_json_at_all_returns_none():
    title, _body = _build_realistic_title_body()
    body = "This chip works great on my bench, no logs attached."

    obj = parse_devtest_body(title, body)

    assert obj is None


def test_malformed_fence_containing_a_json_list_not_dict_returns_none():
    title, _body = _build_realistic_title_body()
    body = '```json\n["schema_version", "1.1"]\n```'

    obj = parse_devtest_body(title, body)

    assert obj is None


def test_malformed_never_raises_across_all_negative_cases():
    """Explicit no-exception assertion (acceptance criteria) across the
    full negative-path matrix, run inline rather than relying on pytest's
    own uncaught-exception failure to prove intent."""
    title, _body = _build_realistic_title_body()
    negative_bodies = [
        None,
        "",
        "no json here",
        "```json\n{not valid json\n```",
        "```json\n[1, 2, 3]\n```",
        "x" * (_MAX_BODY_BYTES + 1),
        '```json\n{"no_schema_version_key": true}\n```',
    ]
    for body in negative_bodies:
        try:
            result = parse_devtest_body(title, body)
        except Exception as exc:  # noqa: BLE001 -- explicit no-raise assertion
            raise AssertionError(
                f"parse_devtest_body raised on {body!r}: {exc}"
            ) from exc
        assert result is None

    for body in negative_bodies:
        try:
            counts = count_agreeing([body] if body is not None else [""])
        except Exception as exc:  # noqa: BLE001 -- explicit no-raise assertion
            raise AssertionError(f"count_agreeing raised on {body!r}: {exc}") from exc
        assert counts == {}
