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
  LEGACY    -- test_legacy_*, test_mixed_schema_*  (D-06 back-compat, Phase
                                    121 Plan 07: a frozen literal `3.0.0b11`
                                    body -- schema_version "1.1", the six
                                    original op strings, no `write-partial`
                                    -- still parses and still groups by its
                                    embedded dedup_fingerprint. This module
                                    needs NO code change for the D-06
                                    schema_version 1.2 bump: schema_version
                                    is accepted by presence only.)
"""

from __future__ import annotations

import json

from firestarter.chip_test import VERDICT_OK, Plan, StepResult
from firestarter.database import EpromDatabase
from firestarter.diagnostic_report import NOT_REPORTED as _REPORT_NOT_REPORTED
from firestarter.diagnostic_report import (
    SCHEMA_VERSION,
    AutoCapture,
    DiagnosticReport,
    TransportHealth,
    build_db_diff,
)
from firestarter.submit import build_body, build_title, sanitize_dict
from tools.parse_devtest_issue import (
    _MAX_BODY_BYTES,
    _NOT_ATTRIBUTABLE,
    NOT_REPORTED,
    count_agreeing,
    extract_db_diff,
    parse_devtest_body,
    render_diff,
)

_REAL_DB = EpromDatabase(skip_local_override=True)


def _build_realistic_title_body(
    chip: str = "M8720",
    *,
    protocol: str = "0x08",
    fw_board_identity: str | None = None,
) -> tuple[str, str]:
    """Build a (title, body) pair via the REAL production builders
    (`submit.py:build_title`/`build_body`, `diagnostic_report.py`'s own
    `to_dict()`/`build_db_diff`) -- the exact shape a community tester's
    issue actually carries, never a hand-approximated stand-in.

    `fw_board_identity` defaults to `None` (the existing callers' shape,
    unchanged); PROV-06's `render_diff` tests pass an explicit populated or
    empty-string value to exercise the identity row without hand-building a
    fixture (147-05, W-2)."""
    results = [
        StepResult(
            op="id", verdict=VERDICT_OK, reason="chip id matched", fingerprint=None
        ),
        StepResult(op="read", verdict=VERDICT_OK, reason="", fingerprint=None),
    ]
    auto_capture = AutoCapture(
        host_version="3.0.0b10",
        chip=chip,
        protocol=protocol,
        fw_board_identity=fw_board_identity,
    )
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
    # This test builds a report via the CURRENT builders, so it reflects
    # the CURRENT SCHEMA_VERSION (imported, never restated as a literal --
    # a v1.30 Phase 134 plan 134-06 repair: this assertion previously
    # hardcoded "1.2" and broke the instant SCHEMA_VERSION bumped to "1.3"
    # for LEG-12's sdp_hold_state key, exactly the single-sourcing
    # discipline the rest of this test module already follows). The frozen
    # b11-shaped ("1.1") fixture lives in the LEGACY section below and is
    # never regenerated from live code.
    assert obj["schema_version"] == SCHEMA_VERSION
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
# RENDER (PROV-06, W-2) -- `render_diff` had ZERO tests anywhere in the repo
# before this plan (147-05); these are the first-ever tests for it. Every
# assertion is a direct substring check on the returned `str` -- `render_diff`
# is a pure function of `(report_obj, diff, n_agreeing=...)`, so there is no
# `_rendered_text` indirection to mirror and no CLI subprocess needed.
#
# Evidence Ceiling (binding): none of these tests may say or imply the
# `0x0D` write path is proven, that a support status changes, or that
# gh#21/#32/#11/#12 are closed. Framing is only that attribution becomes
# possible, or is explicitly refused.
# ---------------------------------------------------------------------------


def test_render_diff_labels_a_populated_firmware_identity():
    """A report whose auto_capture.fw_board_identity is populated renders a
    labelled fw_board_identity line carrying that exact value, a labelled
    host_version line, and NOT the not-attributable clause (PROV-06)."""
    title, body = _build_realistic_title_body(fw_board_identity="3.0.0b19:leonardo")
    obj = parse_devtest_body(title, body)
    diff = extract_db_diff(obj)

    rendered = render_diff(obj, diff)

    identity_line = next(
        line for line in rendered.splitlines() if "fw_board_identity" in line
    )
    assert identity_line.strip().endswith("3.0.0b19:leonardo")
    assert any("host_version" in line for line in rendered.splitlines())
    assert _NOT_ATTRIBUTABLE not in rendered


def test_render_diff_marks_an_absent_identity_not_attributable():
    """A `None` fw_board_identity AND an empty-string one (a community body
    can genuinely carry `""`) both render the marker plus the
    not-attributable clause -- never a blank (D-14/D-17/PROV-05)."""
    title_none, body_none = _build_realistic_title_body(fw_board_identity=None)
    obj_none = parse_devtest_body(title_none, body_none)
    rendered_none = render_diff(obj_none, extract_db_diff(obj_none))

    assert NOT_REPORTED in rendered_none
    assert _NOT_ATTRIBUTABLE in rendered_none

    title_empty, body_empty = _build_realistic_title_body(fw_board_identity="")
    obj_empty = parse_devtest_body(title_empty, body_empty)
    rendered_empty = render_diff(obj_empty, extract_db_diff(obj_empty))

    assert NOT_REPORTED in rendered_empty
    assert _NOT_ATTRIBUTABLE in rendered_empty


def test_render_diff_omits_hw_revision():
    """No `hw_revision` label appears in `render_diff`'s output -- a
    deliberate omission (D-15), not an oversight: a write-path finding is
    attributable only when host AND firmware are both known, and
    `hw_revision` is a coarse silkscreen bucket that cannot discriminate
    the operator's Rev 2.2 / Rev 2.0 / modified Rev 0 boards, so a line
    naming it would look authoritative while answering nothing."""
    title, body = _build_realistic_title_body(fw_board_identity="3.0.0b19:leonardo")
    obj = parse_devtest_body(title, body)

    rendered = render_diff(obj, extract_db_diff(obj))

    assert "hw_revision" not in rendered


def test_render_diff_still_labels_n_agreeing_as_a_maintainer_decision_input():
    """Non-regression pin on `render_diff`'s pre-existing `n_agreeing`
    clause -- this function had zero tests before this plan, so its
    existing contract is pinned here in the same pass that adds to it."""
    title, body = _build_realistic_title_body()
    obj = parse_devtest_body(title, body)

    rendered = render_diff(obj, extract_db_diff(obj), n_agreeing=3)

    assert "3" in rendered
    assert "maintainer decision input" in rendered
    assert "NEVER an auto-promotion trigger" in rendered


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


# ---------------------------------------------------------------------------
# LEGACY (D-06 back-compat, Phase 121 Plan 07) -- a frozen `3.0.0b11` body
# shape, hand-written and NEVER regenerated from current code. `3.0.0b11`
# predates the `write-partial` op (Phase 121 Plan 06) and the 1.1 -> 1.2
# schema_version bump (Plan 07) -- this fixture pins exactly what a tester's
# machine still in the wild on that release produces, and proves this
# module needs zero change to keep accepting it: `schema_version` is
# checked by PRESENCE only (`_extract_fenced_report`), never an exact-value
# match, and `count_agreeing` groups purely by the embedded
# `dedup_fingerprint`, never by `schema_version`.
# ---------------------------------------------------------------------------

_B11_TITLE = "[dev test] M8720 — PASS (b11deadbeef)"

# Frozen `3.0.0b11` artifact shape -- schema_version "1.1", the six original
# op strings only (id/read/blank-check/write/verify/erase), NO
# "write-partial". Must never be regenerated from live `to_dict()` output --
# the whole point is pinning a shape this codebase can no longer produce.
_B11_BODY = (
    "| Step | Verdict | Reason |\n"
    "| ---- | ------- | ------ |\n"
    "| id | OK | chip id matched |\n"
    "| write | OK |  |\n"
    "| verify | OK |  |\n"
    "\n```json\n"
    + json.dumps(
        {
            "schema_version": "1.1",
            "generated": "2026-05-01T12:00:00Z",
            "auto_capture": {
                "host_version": "3.0.0b11",
                "fw_board_identity": "3.0.0b11:leonardo",
                "hw_revision": "Rev 2.0-class",
                "chip": "M8720",
                "protocol": "0x08",
                "chip_id_expected": 4660,
                "chip_id_actual": 4660,
                "chip_id_mismatch_reason": None,
            },
            "transport_health": {
                "cobs_errors": "not measured",
                "crc_failures": "not measured",
                "retries": "not measured",
                "timeouts": "not measured",
                "transport_suspect": False,
            },
            "steps": [
                {
                    "op": "id",
                    "verdict": "OK",
                    "reason": "chip id matched",
                    "error_code": None,
                    "fingerprint": None,
                },
                {
                    "op": "read",
                    "verdict": "OK",
                    "reason": "",
                    "error_code": None,
                    "fingerprint": None,
                },
                {
                    "op": "blank-check",
                    "verdict": "NA",
                    "reason": "not applicable pre-write",
                    "error_code": None,
                    "fingerprint": None,
                },
                {
                    "op": "write",
                    "verdict": "OK",
                    "reason": "",
                    "error_code": None,
                    "fingerprint": None,
                },
                {
                    "op": "verify",
                    "verdict": "OK",
                    "reason": "",
                    "error_code": None,
                    "fingerprint": "clean",
                },
                {
                    "op": "erase",
                    "verdict": "NA",
                    "reason": "not applicable, EEPROM",
                    "error_code": None,
                    "fingerprint": None,
                },
            ],
            "banner": {"n_ran": 5, "m_applicable": 5, "locked_steps": []},
            "voltage": {
                "vpp_before_mv": "not measured",
                "vpp_after_mv": "not measured",
                "vpe_before_mv": "not measured",
                "vpe_after_mv": "not measured",
                "vpp_mv": "not measured",
                "vpe_mv": "not measured",
            },
            "is_submittable": True,
            "dedup_fingerprint": "b11deadbeef",
            "db_diff": {
                "current_support_status": "supported",
                "proposed_disposition": "suggests: candidate for community-reported (advisory)",
                "ladder_state": "community-reported",
            },
        },
        indent=2,
    )
    + "\n```"
)


def test_legacy_vocabulary_b11_body_still_parses():
    """A literal, hand-written `3.0.0b11` body -- schema_version "1.1", six
    original op strings, a dedup_fingerprint -- is accepted by
    parse_devtest_body with the `[dev test]` title marker, and its fields
    are readable (D-06 back-compat)."""
    obj = parse_devtest_body(_B11_TITLE, _B11_BODY)

    assert obj is not None
    assert obj["schema_version"] == "1.1"
    assert obj["auto_capture"]["chip"] == "M8720"
    assert [step["op"] for step in obj["steps"]] == [
        "id",
        "read",
        "blank-check",
        "write",
        "verify",
        "erase",
    ]
    assert "write-partial" not in [step["op"] for step in obj["steps"]]
    assert obj["dedup_fingerprint"] == "b11deadbeef"

    diff = extract_db_diff(obj)
    assert diff["ladder_state"] == "community-reported"


def test_legacy_vocabulary_b11_body_still_groups():
    """count_agreeing over a list containing one b11-shaped body and one
    current-schema body with the SAME embedded fingerprint yields a single
    group of two -- an old report and a new one describing the same run
    still aggregate (D-06 back-compat)."""
    current_body = _B11_BODY.replace(
        '"schema_version": "1.1"', '"schema_version": "1.2"'
    )

    counts = count_agreeing([_B11_BODY, current_body])

    assert counts == {"b11deadbeef": 2}


def test_mixed_schema_versions_group_independently():
    """Bodies whose embedded fingerprints differ group separately regardless
    of schema version -- the version field is never used as a grouping key
    (D-06 back-compat)."""
    other_fingerprint_body = _B11_BODY.replace(
        '"dedup_fingerprint": "b11deadbeef"', '"dedup_fingerprint": "aaaa11112222"'
    ).replace('"schema_version": "1.1"', '"schema_version": "1.2"')

    counts = count_agreeing([_B11_BODY, other_fingerprint_body])

    assert counts == {"b11deadbeef": 1, "aaaa11112222": 1}


# ---------------------------------------------------------------------------
# W-3 (PROV-04) -- a SECOND frozen fixture, this one carrying
# `fw_board_identity: null`. The `_B11_BODY` fixture above carries a
# *populated* identity ("3.0.0b11:leonardo"), which is why it cannot stand
# in for PROV-04's real-world population: the reports that motivated this
# milestone (gh#21/#32) are exactly the null-identity ones. Modeled on a
# real report shape -- schema_version "1.2", host_version "3.0.0b15",
# fw_board_identity null, a populated coarse hw_revision, chip at28c256,
# protocol 0x0D (SKILL.md's own `#32 at28c256 -- FAIL` transcript). Must
# NEVER be regenerated from live `to_dict()` output -- the whole point is
# pinning a shape this codebase can no longer produce (this milestone
# replaces the hardcoded `fw_board_identity=None` construction).
# ---------------------------------------------------------------------------

_NULL_IDENTITY_TITLE = "[dev test] at28c256 — FAIL (deadnu11id00)"

# Frozen `3.0.0b15` artifact shape -- schema_version "1.2", null
# fw_board_identity. Must never be regenerated from live `to_dict()`
# output -- the whole point is pinning a shape this codebase can no
# longer produce.
_NULL_IDENTITY_BODY = (
    "| Step | Verdict | Reason |\n"
    "| ---- | ------- | ------ |\n"
    "| id | OK | chip id matched |\n"
    "| write | BAD | verify mismatch |\n"
    "\n```json\n"
    + json.dumps(
        {
            "schema_version": "1.2",
            "generated": "2026-08-07T12:07:39Z",
            "auto_capture": {
                "host_version": "3.0.0b15",
                "fw_board_identity": None,
                "hw_revision": "Rev 2.0-class, Override HW: Rev 2.3",
                "chip": "at28c256",
                "protocol": "0x0D",
                "chip_id_expected": 6531,
                "chip_id_actual": 6531,
                "chip_id_mismatch_reason": None,
            },
            "transport_health": {
                "cobs_errors": "not measured",
                "crc_failures": "not measured",
                "retries": "not measured",
                "timeouts": "not measured",
                "transport_suspect": False,
            },
            "steps": [
                {
                    "op": "id",
                    "verdict": "OK",
                    "reason": "chip id matched",
                    "error_code": None,
                    "fingerprint": None,
                },
                {
                    "op": "write",
                    "verdict": "BAD",
                    "reason": "verify mismatch",
                    "error_code": None,
                    "fingerprint": "byte0-mismatch",
                },
            ],
            "banner": {"n_ran": 2, "m_applicable": 2, "locked_steps": []},
            "voltage": {
                "vpp_before_mv": "not measured",
                "vpp_after_mv": "not measured",
                "vpe_before_mv": "not measured",
                "vpe_after_mv": "not measured",
                "vpp_mv": "not measured",
                "vpe_mv": "not measured",
            },
            "is_submittable": True,
            "dedup_fingerprint": "deadnu11id00",
            "db_diff": {
                "current_support_status": "supported",
                "proposed_disposition": "suggests: candidate for community-fail (advisory)",
                "ladder_state": "community-fail",
            },
        },
        indent=2,
    )
    + "\n```"
)


def test_legacy_null_identity_body_still_parses_and_groups():
    """A frozen `fw_board_identity: null` body -- PROV-04's real-world
    population, not the populated `_B11_BODY` shape -- still parses, its
    `schema_version` is readable, its identity is `None`, its
    `extract_db_diff` grouping is unchanged, and feeding it to `render_diff`
    renders the marker plus the not-attributable clause (PROV-04 + PROV-06
    proved together on one realistic artifact, W-3)."""
    obj = parse_devtest_body(_NULL_IDENTITY_TITLE, _NULL_IDENTITY_BODY)

    assert obj is not None
    assert obj["schema_version"] == "1.2"
    assert obj["auto_capture"]["fw_board_identity"] is None

    diff = extract_db_diff(obj)
    assert diff["ladder_state"] == "community-fail"

    rendered = render_diff(obj, diff)
    assert NOT_REPORTED in rendered
    assert _NOT_ATTRIBUTABLE in rendered


def test_unknown_marker_string_matches_the_report_model():
    """D-11 asks for a single-sourced "not reported" constant; architecture
    forbids it -- `tools/parse_devtest_issue.py` is stdlib-only by stated
    contract and cannot import `firestarter.diagnostic_report`. This test
    module is the ONLY place in the repo that legitimately imports both
    worlds, which is exactly why a value-parity assert lives here: three
    literals (this module's `NOT_REPORTED`, the parser's `NOT_REPORTED`,
    and the devtest-triage skill script's own copy) plus this equality
    assert is the resolution, not a compromise.

    The THIRD literal -- the skill script's, in `.claude/skills/` (a
    different repo, the meta repo) -- is NOT covered by this test: an
    app-repo test reaching into `/workspaces/.claude/` would fail OPEN in
    standalone CI. That parity is covered instead by plan 147-06's
    human-verify checkpoint.
    """
    assert _REPORT_NOT_REPORTED == NOT_REPORTED


def test_parser_marker_strings_trip_no_forbidden_claim_pattern():
    """Neither `NOT_REPORTED` nor `_NOT_ATTRIBUTABLE` (this module's copies)
    matches any of `check_diagnostic_report_claims.py`'s 14 forbidden-phrase
    patterns -- closing a measured fail-open: that gate scans ONLY
    `firestarter/diagnostic_report.py`, so a clause authored in
    `tools/parse_devtest_issue.py` is covered by no gate at all. Asserts
    non-vacuity too, so this cannot pass because the import silently
    yielded nothing. Does NOT widen the gate's own target list (Phase
    152's business, not this phase's)."""
    from tools.check_diagnostic_report_claims import FORBIDDEN_PATTERNS

    assert len(FORBIDDEN_PATTERNS) >= 14

    for label, pattern in FORBIDDEN_PATTERNS:
        assert not pattern.search(NOT_REPORTED), f"NOT_REPORTED trips [{label}]"
        assert not pattern.search(_NOT_ATTRIBUTABLE), (
            f"_NOT_ATTRIBUTABLE trips [{label}]"
        )
