"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Pytest unit tests for `firestarter/diagnostic_report.py` (v1.21 Phase 110
RPT-01/02, XPORT-01).

Bench-free: real `EpromDatabase(skip_local_override=True)` DB reads +
`Mock(spec=[...])` operator (the `dev validate-family` / `test_chip_test.py`
seam). No serial I/O, no hardware, no `~/.firestarter` overrides.

Test taxonomy:

  Single-source dual-render (RPT-01, D-01)
    test_dual_render_single_source   -> render()/to_dict() share step rows
    test_json_block_parseable        -> fenced ```json round-trips, schema_version

  Auto-capture (RPT-02)
    test_auto_capture_fields         -> host_version, fw_board_identity,
                                         hw_revision, chip, protocol, chip-id,
                                         per-step error_code + fingerprint
                                         classification

  Transport-health honest fallback (XPORT-01, D-03)
    test_transport_not_measured      -> every counter == NOT_MEASURED (never 0);
                                         transport_suspect is False

  Orchestrator-only structural scan (SAFE-02)
    test_report_module_is_orchestrator_only -> no SerialCommunicator/
                                                HardwareManager import, no
                                                VPP-set, no "--force" token

  Auto-capture-only submittability, no human-input gate (Phase 112 Plan 04)
    test_is_submittable_derived_from_auto_capture -> is_submittable is True
        on a complete AutoCapture and False when a required auto-captured
        field is blank -- to_dict() has no "provenance" key at all

  Read-only advisory DB-diff (RPT-05, D-07, Plan 03)
    test_db_diff_readonly -> build_db_diff reads support_status from a
        write-method-less Mock DB (no write ever attempted)
    test_db_diff_verdict_mapping -> BAD/PASS-only/marginal verdicts map to
        the correct advisory proposed_disposition string
    test_db_diff_real_db_read -> against the real EpromDatabase, the
        current_support_status matches the live DB config, read-only
    test_module_never_writes_support_status -> structural scan: no
        "support_status =" assignment / ".write" / "set_" DB-mutation call
        anywhere in the module source

References:
  - .planning/phases/110-diagnostic-report-model-dual-output-provenance-prompts/110-01-PLAN.md
  - .planning/phases/110-diagnostic-report-model-dual-output-provenance-prompts/110-RESEARCH.md
  - .planning/phases/110-diagnostic-report-model-dual-output-provenance-prompts/110-PATTERNS.md
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import Mock

import firestarter
from firestarter.chip_test import (
    FP_ADDRESS_LINE,
    FP_BLANK_CONTACT,
    FP_INDETERMINATE,
    SDP_HOLD_HELD,
    SDP_HOLD_NOT_HELD,
    SDP_HOLD_NOT_RUN,
    VERDICT_BAD,
    VERDICT_NA,
    VERDICT_OK,
    VERDICT_SKIPPED,
    Fingerprint,
    Plan,
    StepResult,
    derive_plan,
    run_plan,
)
from firestarter.database import EpromDatabase

# Real chip pulled from the shipped chip_database.json (same seam as
# test_chip_test.py:282) -- M8720, protocol 0x08 (EEPROM), chip-id sentinel 0.
_REAL_DB = EpromDatabase(skip_local_override=True)

_OPERATOR_METHODS = [
    "check_eprom_id",
    "read_eprom",
    "check_eprom_blank",
    "write_eprom",
    "verify_eprom",
    "erase_eprom",
]


def _mock_operator(**returns):
    op = Mock(spec=_OPERATOR_METHODS)
    op.check_eprom_id.return_value = (True, 0x1234)
    op.read_eprom.return_value = True
    op.check_eprom_blank.return_value = True
    op.write_eprom.return_value = True
    op.verify_eprom.return_value = True
    op.erase_eprom.return_value = True
    for name, value in returns.items():
        getattr(op, name).return_value = value
        getattr(op, name).side_effect = None
    return op


def _build_report(chip_name: str = "M8720"):
    """Shared helper: derive a real plan + run it against a mock operator,
    then compose a DiagnosticReport from genuine StepResult objects."""
    from firestarter.chip_test import count_applicable
    from firestarter.diagnostic_report import (
        AutoCapture,
        DiagnosticReport,
        TransportHealth,
    )

    plan = derive_plan(chip_name, _REAL_DB)
    operator = _mock_operator()
    results = run_plan(plan, operator, _REAL_DB, runs=2)
    banner = count_applicable(plan, results)

    auto_capture = AutoCapture(
        host_version=firestarter.__version__,
        fw_board_identity="3.0.0b10:leonardo",
        hw_revision="Rev 2.0-class",
        chip=chip_name,
        protocol="0x08",
        chip_id_expected=0x1234,
        chip_id_actual=0x1234,
    )
    report = DiagnosticReport(
        auto_capture=auto_capture,
        transport=TransportHealth(),
        plan=plan,
        results=results,
        banner=banner,
    )
    return report


def _minimal_report(
    *,
    chip: str = "M8720",
    protocol: str = "0x08",
    host_version: str = "3.0.0b10",
    step_specs: list[tuple[str, str, str | None, str]] | None = None,
    vpp_before_mv: int | None = None,
    vpe_before_mv: int | None = None,
):
    """Directly-constructed DiagnosticReport (no derive_plan/run_plan) for
    precise dedup_fingerprint test control over step shape.

    `step_specs` is a list of `(op, verdict, fingerprint_classification,
    reason)` tuples; `fingerprint_classification=None` means no Fingerprint
    is attached (the non-destructive/graceful-degradation shape)."""
    from firestarter.diagnostic_report import (
        AutoCapture,
        DiagnosticReport,
        TransportHealth,
    )

    step_specs = step_specs or [
        ("id", VERDICT_OK, None, "chip id matched"),
        ("read", VERDICT_OK, None, ""),
    ]
    results = []
    for op, verdict, cls, reason in step_specs:
        fp = (
            Fingerprint(total=10, bad=0, bad_pct=0.0, classification=cls)
            if cls is not None
            else None
        )
        results.append(
            StepResult(op=op, verdict=verdict, reason=reason, fingerprint=fp)
        )

    auto_capture = AutoCapture(
        host_version=host_version,
        chip=chip,
        protocol=protocol,
    )
    return DiagnosticReport(
        auto_capture=auto_capture,
        transport=TransportHealth(),
        plan=Plan(name=chip),
        results=results,
        vpp_before_mv=vpp_before_mv,
        vpe_before_mv=vpe_before_mv,
    )


# ---------------------------------------------------------------------------
# Dedup fingerprint (SUB-03, D-02) -- deterministic, volatile-field-free
# ---------------------------------------------------------------------------


def test_dedup_fingerprint_is_12_char_lowercase_hex():
    from firestarter.diagnostic_report import dedup_fingerprint

    report = _minimal_report()
    fp = dedup_fingerprint(report)

    assert isinstance(fp, str)
    assert len(fp) == 12
    assert fp == fp.lower()
    int(fp, 16)  # raises ValueError if not valid hex


def test_dedup_fingerprint_deterministic_same_shape():
    from firestarter.diagnostic_report import dedup_fingerprint

    report_a = _minimal_report()
    report_b = _minimal_report()

    assert dedup_fingerprint(report_a) == dedup_fingerprint(report_b)


def test_dedup_fingerprint_excludes_volatile_fields():
    """Reports differing ONLY in host_version and measured vpp/vpe mV must
    hash equal -- the hash is computed at two DIFFERENT wall-clock moments
    (via a fresh DiagnosticReport each call) to also implicitly prove the
    `generated` timestamp (never read by dedup_fingerprint) cannot leak in."""
    from firestarter.diagnostic_report import dedup_fingerprint

    report_a = _minimal_report(
        host_version="3.0.0b10", vpp_before_mv=20900, vpe_before_mv=23900
    )
    report_b = _minimal_report(
        host_version="3.0.0b99", vpp_before_mv=17400, vpe_before_mv=11000
    )

    assert dedup_fingerprint(report_a) == dedup_fingerprint(report_b)


def test_dedup_fingerprint_excludes_reason_and_error_code():
    from firestarter.diagnostic_report import dedup_fingerprint

    report_a = _minimal_report(
        step_specs=[("id", VERDICT_OK, None, "chip id matched exactly")]
    )
    report_b = _minimal_report(
        step_specs=[("id", VERDICT_OK, None, "totally different text")]
    )

    assert dedup_fingerprint(report_a) == dedup_fingerprint(report_b)


def test_dedup_fingerprint_sensitive_to_verdict_change():
    from firestarter.diagnostic_report import dedup_fingerprint

    report_ok = _minimal_report(step_specs=[("read", VERDICT_OK, None, "")])
    report_bad = _minimal_report(step_specs=[("read", VERDICT_BAD, None, "")])

    assert dedup_fingerprint(report_ok) != dedup_fingerprint(report_bad)


def test_dedup_fingerprint_sensitive_to_classification_change():
    from firestarter.diagnostic_report import dedup_fingerprint

    report_blank = _minimal_report(
        step_specs=[("verify", VERDICT_BAD, FP_BLANK_CONTACT, "")]
    )
    report_addr = _minimal_report(
        step_specs=[("verify", VERDICT_BAD, FP_ADDRESS_LINE, "")]
    )

    assert dedup_fingerprint(report_blank) != dedup_fingerprint(report_addr)


def test_dedup_fingerprint_non_destructive_graceful_degradation():
    """A non-destructive run (no write/verify Fingerprint on any step)
    gracefully collapses to chip + protocol + ordered verdicts and stays
    stable across two identical-shaped runs (D-02)."""
    from firestarter.diagnostic_report import dedup_fingerprint

    step_specs = [
        ("id", VERDICT_OK, None, ""),
        ("read", VERDICT_OK, None, ""),
        ("blank", VERDICT_NA, None, "SRAM/FRAM"),
    ]
    report_a = _minimal_report(step_specs=step_specs)
    report_b = _minimal_report(step_specs=step_specs)

    fp_a = dedup_fingerprint(report_a)
    fp_b = dedup_fingerprint(report_b)

    assert fp_a == fp_b
    assert len(fp_a) == 12


def test_dedup_fingerprint_in_to_dict_single_source():
    from firestarter.diagnostic_report import dedup_fingerprint

    report = _minimal_report()
    d = report.to_dict()

    assert d["dedup_fingerprint"] == dedup_fingerprint(report)


def test_dedup_fingerprint_graceful_degradation_via_to_dict():
    """A fingerprint-less (non-destructive-shaped) report yields a stable,
    repeatable id through to_dict() across two identical-verdict runs."""
    step_specs = [
        ("id", VERDICT_OK, None, ""),
        ("read", VERDICT_OK, None, ""),
    ]
    report_a = _minimal_report(step_specs=step_specs)
    report_b = _minimal_report(step_specs=step_specs)

    d_a = report_a.to_dict()
    d_b = report_b.to_dict()

    assert d_a["dedup_fingerprint"] == d_b["dedup_fingerprint"]
    assert len(d_a["dedup_fingerprint"]) == 12


def test_dedup_fingerprint_in_json_block():
    report = _minimal_report()
    block = report.to_json_block()

    inner = block.strip()[len("```json\n") :].rsplit("```", 1)[0]
    parsed = json.loads(inner)

    assert "dedup_fingerprint" in parsed
    assert parsed["dedup_fingerprint"] == report.to_dict()["dedup_fingerprint"]


# ---------------------------------------------------------------------------
# Partial-vs-full-write fingerprint differentiation (D-06/D-08, Phase 121
# Plan 07). This is D-06/D-08's proof, not merely its argument: the GRAD-01
# no-auto-graduate lock (Phase 114) holds end to end THROUGH THE FINGERPRINT
# -- because `dedup_fingerprint` hashes `result.op` per step, a partial run
# (`OP_WRITE_PARTIAL = "write-partial"`) and a full run (`OP_WRITE = "write"`)
# of the same chip never hash equal and therefore never land in the same
# `count_agreeing` group -- NOT through the `ladder_state` tag, which
# `build_db_diff` assigns identically for both run shapes with zero code
# change. A future reader who drops the op name from `dedup_fingerprint`'s
# inputs should watch `test_fingerprint_differs_for_partial_versus_full_write`
# and `test_partial_and_full_runs_never_cross_agree` below go RED.
# ---------------------------------------------------------------------------


def test_fingerprint_differs_for_partial_versus_full_write():
    """Two reports identical in chip, protocol, verdicts and classifications,
    differing ONLY in the write step's op (`write` vs `write-partial`),
    produce DIFFERENT dedup_fingerprint values (D-06)."""
    from firestarter.diagnostic_report import dedup_fingerprint

    full_specs = [
        ("id", VERDICT_OK, None, ""),
        ("write", VERDICT_OK, None, ""),
        ("verify", VERDICT_OK, None, ""),
    ]
    partial_specs = [
        ("id", VERDICT_OK, None, ""),
        ("write-partial", VERDICT_OK, None, ""),
        ("verify", VERDICT_OK, None, ""),
    ]
    report_full = _minimal_report(step_specs=full_specs)
    report_partial = _minimal_report(step_specs=partial_specs)

    assert dedup_fingerprint(report_full) != dedup_fingerprint(report_partial)


def test_fingerprint_is_stable_for_identical_partial_runs():
    """Two independently-built reports with identical partial-run content
    produce the SAME fingerprint -- proving the differentiation above is a
    genuine op-name signal, not hash noise (D-06)."""
    from firestarter.diagnostic_report import dedup_fingerprint

    partial_specs = [
        ("id", VERDICT_OK, None, ""),
        ("write-partial", VERDICT_OK, None, ""),
        ("verify", VERDICT_OK, None, ""),
    ]
    report_a = _minimal_report(step_specs=partial_specs)
    report_b = _minimal_report(step_specs=partial_specs)

    assert dedup_fingerprint(report_a) == dedup_fingerprint(report_b)


def test_partial_run_still_tags_community_reported():
    """`build_db_diff` over an all-OK result set whose write step is
    `write-partial` yields the SAME ladder_state as the equivalent full run
    -- `community-reported` -- with NO code change to `build_db_diff` (D-08).
    Also asserts the human-gated `community-confirmed` state is never
    produced, partial run or otherwise (T-121-26)."""
    from firestarter.diagnostic_report import (
        _LADDER_COMMUNITY_CONFIRMED,
        build_db_diff,
    )

    partial_results = [
        StepResult(op="id", verdict=VERDICT_OK, reason="", fingerprint=None),
        StepResult(op="write-partial", verdict=VERDICT_OK, reason="", fingerprint=None),
        StepResult(op="verify", verdict=VERDICT_OK, reason="", fingerprint=None),
    ]
    full_results = [
        StepResult(op="id", verdict=VERDICT_OK, reason="", fingerprint=None),
        StepResult(op="write", verdict=VERDICT_OK, reason="", fingerprint=None),
        StepResult(op="verify", verdict=VERDICT_OK, reason="", fingerprint=None),
    ]

    diff_partial = build_db_diff("M8720", _REAL_DB, partial_results)
    diff_full = build_db_diff("M8720", _REAL_DB, full_results)

    assert diff_partial.ladder_state == "community-reported"
    assert diff_partial.ladder_state == diff_full.ladder_state
    assert diff_partial.ladder_state != _LADDER_COMMUNITY_CONFIRMED
    assert diff_full.ladder_state != _LADDER_COMMUNITY_CONFIRMED


def test_partial_and_full_runs_never_cross_agree():
    """Feeding count_agreeing two saved bodies -- one partial, one full --
    for the SAME chip yields TWO groups of one each, never one group of two
    (D-06/D-08). This is the GRAD-01 lock's mechanical proof: a 256-byte
    partial run can never count toward a full run's N>=2 promotion.

    Built via the REAL pipeline (`sanitize_dict(report.to_dict())` into
    `build_body`, `firestarter/submit.py`) so the fenced JSON `count_agreeing`
    parses is the real artifact shape, not a hand-rolled blob.
    """
    from firestarter.diagnostic_report import (
        AutoCapture,
        DiagnosticReport,
        TransportHealth,
    )
    from firestarter.submit import build_body, build_title, sanitize_dict
    from tools.parse_devtest_issue import count_agreeing

    def _saved_body(op: str) -> str:
        results = [
            StepResult(op="id", verdict=VERDICT_OK, reason="", fingerprint=None),
            StepResult(op=op, verdict=VERDICT_OK, reason="", fingerprint=None),
            StepResult(op="verify", verdict=VERDICT_OK, reason="", fingerprint=None),
        ]
        auto_capture = AutoCapture(
            host_version="3.0.0b10", chip="M8720", protocol="0x08"
        )
        report = DiagnosticReport(
            auto_capture=auto_capture,
            transport=TransportHealth(),
            plan=Plan(name="M8720"),
            results=results,
        )
        sanitized = sanitize_dict(report.to_dict())
        build_title(report, "M8720")  # exercised for realism; title unused here
        return build_body(sanitized, report.results, include_json=True)

    body_partial = _saved_body("write-partial")
    body_full = _saved_body("write")

    counts = count_agreeing([body_partial, body_full])

    assert len(counts) == 2
    assert sorted(counts.values()) == [1, 1]


# ---------------------------------------------------------------------------
# Single-source dual-render (RPT-01, D-01)
# ---------------------------------------------------------------------------


def test_dual_render_single_source():
    report = _build_report()

    d = report.to_dict()
    table = report.render()

    # The dict is the canonical source: every step op appears in it.
    dict_ops = {row["op"] for row in d["steps"]}
    assert dict_ops, "to_dict() steps list must not be empty for a real chip"

    # render() must be built from the SAME to_dict() output -- adding a step
    # to results must appear in both without editing a second list. Assert
    # render() literally calls self.to_dict() (never a parallel field list,
    # never a re-parse of the JSON string).
    src = inspect.getsource(type(report).render)
    assert "self.to_dict()" in src or "to_dict()" in src
    assert "json.loads" not in src
    assert "json.load(" not in src

    # The rendered rich Table has one row per step (identity/banner rows are
    # additional, but every op must be represented).
    assert table.row_count >= len(dict_ops)


# ---------------------------------------------------------------------------
# JSON block round-trip (RPT-01, D-02)
# ---------------------------------------------------------------------------


def test_json_block_parseable():
    from firestarter.diagnostic_report import SCHEMA_VERSION

    report = _build_report()
    block = report.to_json_block()

    assert block.startswith("```json\n")
    assert block.rstrip().endswith("```")

    inner = block.strip()
    inner = inner[len("```json\n") :]
    inner = inner.rsplit("```", 1)[0]

    parsed = json.loads(inner)
    assert parsed["schema_version"] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Auto-capture fields (RPT-02)
# ---------------------------------------------------------------------------


def test_auto_capture_fields():
    report = _build_report()
    d = report.to_dict()

    ac = d["auto_capture"]
    assert ac["host_version"] == firestarter.__version__
    assert ac["fw_board_identity"] == "3.0.0b10:leonardo"
    assert ac["hw_revision"] == "Rev 2.0-class"
    assert ac["chip"] == "M8720"
    assert ac["protocol"] == "0x08"
    assert ac["chip_id_expected"] == 0x1234
    assert ac["chip_id_actual"] == 0x1234

    # Every step dict carries error_code + fingerprint classification read
    # straight off the Phase-108 StepResult (never re-derived).
    for step_row in d["steps"]:
        assert "error_code" in step_row
        assert "fingerprint" in step_row


# ---------------------------------------------------------------------------
# Transport-health honest fallback (XPORT-01, D-03)
# ---------------------------------------------------------------------------


def test_transport_not_measured():
    from firestarter.diagnostic_report import NOT_MEASURED

    report = _build_report()
    d = report.to_dict()

    transport = d["transport_health"]
    for key in ("cobs_errors", "crc_failures", "retries", "timeouts"):
        assert transport[key] == NOT_MEASURED
        assert transport[key] != 0

    assert transport["transport_suspect"] is False


# ---------------------------------------------------------------------------
# Orchestrator-only structural scan (SAFE-02)
# ---------------------------------------------------------------------------


def test_report_module_is_orchestrator_only():
    """AST-based structural scan (mirrors the Phase-109 SAFE-02 lesson: a raw
    substring grep false-positives on docstring prose describing the safety
    property itself, e.g. "imports no SerialCommunicator"). This test parses
    the module's AST and asserts no import statement names either forbidden
    symbol, and that no string literal in the source equals "--force"
    (a real CLI-flag token, never legitimately embedded in this module)."""
    import ast

    import firestarter.diagnostic_report as diagnostic_report_mod

    src = inspect.getsource(diagnostic_report_mod)
    tree = ast.parse(src)

    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)

    assert "SerialCommunicator" not in imported_names
    assert "HardwareManager" not in imported_names

    # No literal "--force" token anywhere as an actual string constant (a
    # real CLI-flag pass-through would appear as a string literal, not prose).
    force_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "--force" not in force_literals


# ---------------------------------------------------------------------------
# Auto-capture-only submittability, no human-input gate (Phase 112 Plan 04)
# ---------------------------------------------------------------------------
#
# REVERSAL: this section previously tested composing a filled/blank
# Provenance into a DiagnosticReport (RPT-04, D-04/D-05/D-06). That
# interactive tester-input model is gone (operator-approved descope,
# 112-UAT.md test 2) -- is_submittable is now derived purely from
# auto_capture, and to_dict() no longer has a "provenance" key at all.


def test_is_submittable_derived_from_auto_capture():
    from firestarter.diagnostic_report import is_submittable

    report = _build_report()  # _build_report's AutoCapture is complete
    d = report.to_dict()
    assert "provenance" not in d
    assert d["is_submittable"] is True
    assert is_submittable(report.auto_capture) is True

    report.auto_capture.protocol = None
    d_incomplete = report.to_dict()
    assert d_incomplete["is_submittable"] is False


# ---------------------------------------------------------------------------
# Read-only advisory DB-diff (RPT-05, D-07, Plan 03)
# ---------------------------------------------------------------------------


def _mock_db(support_status: str = "adapter-required"):
    """Write-method-less Mock DB (RPT-05, D-07): the spec exposes ONLY the
    three read methods `resolve_chip`/`derive_plan` are allowed to touch. It
    has NO write/set method at all, so any accidental write attempt inside
    build_db_diff raises AttributeError -- read-only proven by construction."""
    db = Mock(spec=["get_eprom", "get_eprom_config", "convert_to_programmer"])
    db.get_eprom_config.return_value = ({"support_status": support_status}, "MFR")
    return db


def test_db_diff_readonly():
    from firestarter.diagnostic_report import build_db_diff

    db = _mock_db(support_status="adapter-required")
    results = [StepResult(op="id", verdict=VERDICT_OK)]

    diff = build_db_diff("SOME-CHIP", db, results)

    db.get_eprom_config.assert_called_once_with("SOME-CHIP")
    assert diff.current_support_status == "adapter-required"
    # The mock has no write/set method -- nothing but the three spec'd read
    # methods can even be called on it. Confirm no unexpected call was made.
    db.get_eprom.assert_not_called()
    db.convert_to_programmer.assert_not_called()


def test_db_diff_verdict_mapping():
    from firestarter.diagnostic_report import build_db_diff

    db = _mock_db()

    # Any BAD verdict -> community-fail signal (advisory).
    bad_results = [
        StepResult(op="id", verdict=VERDICT_OK),
        StepResult(op="read", verdict=VERDICT_BAD),
    ]
    diff_bad = build_db_diff("X", db, bad_results)
    assert "community-fail" in diff_bad.proposed_disposition
    assert "advisory" in diff_bad.proposed_disposition
    assert (
        diff_bad.proposed_disposition != "community-fail"
    )  # descriptive text, not a bare value

    # PASS-only (OK + NA/SKIPPED, no BAD) -> candidate for community-reported (advisory).
    pass_results = [
        StepResult(op="id", verdict=VERDICT_OK),
        StepResult(op="blank", verdict=VERDICT_NA),
        StepResult(op="write", verdict=VERDICT_SKIPPED),
    ]
    diff_pass = build_db_diff("X", db, pass_results)
    assert "community-reported" in diff_pass.proposed_disposition
    assert "advisory" in diff_pass.proposed_disposition
    assert diff_pass.proposed_disposition != "community-reported"

    # marginal verdict -> inconclusive, needs N>=2 (advisory).
    marginal_results = [
        StepResult(op="id", verdict=VERDICT_OK),
        StepResult(op="verify", verdict="marginal"),
    ]
    diff_marginal = build_db_diff("X", db, marginal_results)
    assert "inconclusive" in diff_marginal.proposed_disposition
    assert (
        "N>=2" in diff_marginal.proposed_disposition
        or "N≥2" in diff_marginal.proposed_disposition
    )
    assert "advisory" in diff_marginal.proposed_disposition

    # A StepResult carrying an "indeterminate" fingerprint classification also
    # routes to the inconclusive branch, even without a bare "marginal" verdict.
    indeterminate_results = [
        StepResult(
            op="verify",
            verdict=VERDICT_OK,
            fingerprint=Fingerprint(
                total=10, bad=3, bad_pct=0.3, classification=FP_INDETERMINATE
            ),
        ),
    ]
    diff_indeterminate = build_db_diff("X", db, indeterminate_results)
    assert "inconclusive" in diff_indeterminate.proposed_disposition


def test_ladder_state_verdict_mapping():
    """GRAD-01 (Phase 114, D-01): build_db_diff derives a report-side
    ladder_state tag purely from sweep verdicts -- BAD -> community-fail;
    all-OK (subset of {OK,NA,SKIPPED}, at least one OK) -> community-reported;
    marginal / indeterminate-fingerprint / no-change -> "" (no community-*
    tag). community-confirmed is the human-only target and must never be
    emitted here (D-01/D-02)."""
    from firestarter.diagnostic_report import (
        _LADDER_COMMUNITY_CONFIRMED,
        _LADDER_COMMUNITY_FAIL,
        _LADDER_COMMUNITY_REPORTED,
        _LADDER_NONE,
        build_db_diff,
    )

    db = _mock_db()

    bad_results = [
        StepResult(op="id", verdict=VERDICT_OK),
        StepResult(op="read", verdict=VERDICT_BAD),
    ]
    diff_bad = build_db_diff("X", db, bad_results)
    assert diff_bad.ladder_state == _LADDER_COMMUNITY_FAIL == "community-fail"

    pass_results = [
        StepResult(op="id", verdict=VERDICT_OK),
        StepResult(op="blank", verdict=VERDICT_NA),
        StepResult(op="write", verdict=VERDICT_SKIPPED),
    ]
    diff_pass = build_db_diff("X", db, pass_results)
    assert diff_pass.ladder_state == _LADDER_COMMUNITY_REPORTED == "community-reported"

    marginal_results = [
        StepResult(op="id", verdict=VERDICT_OK),
        StepResult(op="verify", verdict="marginal"),
    ]
    diff_marginal = build_db_diff("X", db, marginal_results)
    assert diff_marginal.ladder_state == _LADDER_NONE == ""

    indeterminate_results = [
        StepResult(
            op="verify",
            verdict=VERDICT_OK,
            fingerprint=Fingerprint(
                total=10, bad=3, bad_pct=0.3, classification=FP_INDETERMINATE
            ),
        ),
    ]
    diff_indeterminate = build_db_diff("X", db, indeterminate_results)
    assert diff_indeterminate.ladder_state == _LADDER_NONE == ""

    # No-change branch (e.g. empty results) also yields no community-* tag.
    diff_no_change = build_db_diff("X", db, [])
    assert diff_no_change.ladder_state == _LADDER_NONE == ""

    # community-confirmed is NEVER emitted by build_db_diff for any verdict
    # combination exercised above -- it is the human-gated target only.
    for diff in (
        diff_bad,
        diff_pass,
        diff_marginal,
        diff_indeterminate,
        diff_no_change,
    ):
        assert diff.ladder_state != _LADDER_COMMUNITY_CONFIRMED
        assert diff.ladder_state != "community-confirmed"


def test_ladder_state_single_source_in_to_dict():
    """GRAD-01 (Phase 114): to_dict()['db_diff']['ladder_state'] equals
    report.db_diff.ladder_state -- single-source, added once (Pattern 3)."""
    from firestarter.diagnostic_report import build_db_diff

    report = _build_report()
    db = _mock_db(support_status="adapter-required")
    report.db_diff = build_db_diff("SOME-CHIP", db, report.results)

    d = report.to_dict()
    assert d["db_diff"]["ladder_state"] == report.db_diff.ladder_state


def test_db_diff_real_db_read():
    from firestarter.diagnostic_report import build_db_diff

    name = "AT28C04,AT28HC04"
    raw_config, _manufacturer = _REAL_DB.get_eprom_config(name)
    expected = raw_config.get("support_status", "supported")
    assert (
        expected == "adapter-required"
    )  # sanity: known fixture from test_chip_test.py

    results = [StepResult(op="id", verdict=VERDICT_OK)]
    diff = build_db_diff(name, _REAL_DB, results)

    assert diff.current_support_status == expected


def test_module_never_writes_support_status():
    import re

    import firestarter.diagnostic_report as diagnostic_report_mod

    src = inspect.getsource(diagnostic_report_mod)
    lines = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
    joined = "\n".join(lines)

    # A bare `support_status = ...` write/assignment -- NOT a `==` comparison
    # (reading it is legitimate), NOT `current_support_status=...` (a kwarg /
    # dataclass field name that merely CONTAINS "support_status" as a
    # suffix), and NOT a dataclass field declaration
    # (`current_support_status: str = ...`). A real write site would assign
    # to the bare dict key/attribute name `support_status` itself.
    assert (
        re.search(r"(?<![a-zA-Z0-9_])support_status\s*(?<!=)=(?!=)\s*\S", joined)
        is None
    )
    assert ".write(" not in joined
    assert re.search(r"\bset_[a-z_]+\(", joined) is None


# ---------------------------------------------------------------------------
# DbDiff composed into DiagnosticReport (RPT-05, RPT-01, Plan 03)
# ---------------------------------------------------------------------------


def test_report_composes_db_diff_from_single_source():
    """RPT-01 single-source: `render()` reads `self.to_dict()`, never a
    parallel field list or a re-parse of the JSON string. Quick task
    260821-spg removed `db_diff`'s console row entirely (it now reaches
    only `to_json_block()`, doc'd in `doc/community-validation.md`), so
    this test no longer asserts `db_diff` content appears in the RENDERED
    table -- it asserts the payload (`to_dict()`) and the single-source
    mechanism (`render()`'s own source calling `to_dict()`, never
    `json.load(s)`), which is what the claim actually needs."""
    from firestarter.diagnostic_report import build_db_diff

    report = _build_report()
    db = _mock_db(support_status="adapter-required")
    report.db_diff = build_db_diff("SOME-CHIP", db, report.results)

    d = report.to_dict()
    assert d["db_diff"] is not None
    assert d["db_diff"]["current_support_status"] == "adapter-required"
    assert d["db_diff"]["proposed_disposition"] == report.db_diff.proposed_disposition

    report.render()
    # render() must read the SAME to_dict() output -- never a parallel field
    # list, never a re-parse of the JSON string (RPT-01 single-source).
    src = inspect.getsource(type(report).render)
    assert "self.to_dict()" in src or "to_dict()" in src
    assert "json.loads" not in src
    assert "json.load(" not in src


def test_report_without_db_diff_is_null():
    report = _build_report()
    d = report.to_dict()
    assert d["db_diff"] is None


def test_full_report_all_sub_objects_single_source():
    """End-to-end: a full DiagnosticReport (auto_capture + transport +
    db_diff + real plan/results/banner) surfaces every sub-object section
    from one to_dict() and one render() -- the phase gate proving RPT-01's
    single-source contract holds with db_diff added (no provenance
    sub-object since the Phase 112 Plan 04 descope -- auto_capture alone
    drives is_submittable)."""
    from firestarter.diagnostic_report import SCHEMA_VERSION, build_db_diff

    report = _build_report()
    db = _mock_db(support_status="supported")
    report.db_diff = build_db_diff("M8720", db, report.results)

    d = report.to_dict()
    assert d["schema_version"] == SCHEMA_VERSION
    assert d["auto_capture"] is not None
    assert "provenance" not in d
    assert d["transport_health"] is not None
    assert d["db_diff"] is not None

    block = report.to_json_block()
    inner = block.strip()[len("```json\n") :].rsplit("```", 1)[0]
    parsed = json.loads(inner)
    assert parsed["db_diff"]["current_support_status"] == "supported"

    report.render()  # must not raise


# ---------------------------------------------------------------------------
# Wave-0 RED scaffold (v1.21 Phase 111, VOLT-01 / D-01) -- measured-voltage
# split fields on DiagnosticReport.
#
# `vpp_before_mv` / `vpp_after_mv` / `vpe_before_mv` / `vpe_after_mv` /
# `vpp_mv` / `vpe_mv` and the nested `to_dict()["voltage"]` sub-dict do NOT
# exist yet -- the current slot is the single `vpp_vpe_mv: int | None` field
# (Plan 03 replaces it). This test is EXPECTED to fail (TypeError on the
# unknown dataclass kwargs / KeyError on "voltage") until then; that RED
# state is the Wave-0 deliverable (111-VALIDATION.md). Do NOT add the split
# fields to the dataclass here.
# ---------------------------------------------------------------------------


def test_voltage_split_fields_serialize():
    from firestarter.diagnostic_report import NOT_MEASURED, DiagnosticReport

    # (a) destructive-run shape: before/after pairs populated, standalone
    # vpp_mv/vpe_mv left None -> both must serialize to NOT_MEASURED, never 0.
    report_destructive = _build_report()
    report_destructive.vpp_before_mv = 20900
    report_destructive.vpp_after_mv = 17400
    report_destructive.vpe_before_mv = 23900
    report_destructive.vpe_after_mv = 23800

    d_destructive = report_destructive.to_dict()
    voltage_destructive = d_destructive["voltage"]
    assert voltage_destructive["vpp_before_mv"] == 20900
    assert voltage_destructive["vpp_after_mv"] == 17400
    assert voltage_destructive["vpe_before_mv"] == 23900
    assert voltage_destructive["vpe_after_mv"] == 23800
    assert voltage_destructive["vpp_mv"] == NOT_MEASURED
    assert voltage_destructive["vpe_mv"] == NOT_MEASURED

    # (b) non-destructive standalone shape: vpp_mv/vpe_mv populated, all four
    # before/after pairs left None -> all four must serialize to
    # NOT_MEASURED, never a false 0 (D-04 honest-fallback).
    report_standalone = _build_report()
    report_standalone.vpp_mv = 20900
    report_standalone.vpe_mv = 23900

    d_standalone = report_standalone.to_dict()
    voltage_standalone = d_standalone["voltage"]
    assert voltage_standalone["vpp_mv"] == 20900
    assert voltage_standalone["vpe_mv"] == 23900
    for key in ("vpp_before_mv", "vpp_after_mv", "vpe_before_mv", "vpe_after_mv"):
        assert voltage_standalone[key] == NOT_MEASURED

    # (c) single-source assertion: render() must expose a voltage row
    # consistent with to_dict()["voltage"] -- proving render() sources from
    # to_dict() rather than maintaining a second field list (D-01).
    assert isinstance(report_destructive, DiagnosticReport)
    table = report_destructive.render()
    rendered_cells = [str(cell) for column in table.columns for cell in column.cells]
    rendered_text = " ".join(rendered_cells)
    assert "20900" in rendered_text
    assert table.row_count > 0


# ---------------------------------------------------------------------------
# LEG-12's carriage half (v1.30 Phase 134, plan 134-06, D-10/D-11) --
# `sdp_hold_state`, its no-boolean gate, the schema bump, and the D-11 re-key
# cost.
#
# ⚠ Evidence Ceiling (`.planning/REQUIREMENTS.md`): none of the assertions
# below claim anything about a real die's protection state. A locked die is
# unrepresentable in either repo's stubs, so these fixtures pin the host's
# scripted RESPONSE (a `StepResult.verdict` this test constructs directly)
# to a chosen value -- never a real chip. The causal claim "the lock
# inhibited the write" is NOT provable this milestone; these tests prove
# only that whatever `chip_test.sdp_hold_state()` returns is carried,
# verbatim and un-fabricated, into both report surfaces.
# ---------------------------------------------------------------------------


def _rendered_text(table) -> str:
    cells = [str(cell) for column in table.columns for cell in column.cells]
    return " ".join(cells)


def test_hold_state_held_reaches_both_surfaces():
    """`SDP_HOLD_HELD` (the inhibited write was correctly refused) appears
    verbatim in to_dict()["sdp_hold_state"] AND in render()'s output text --
    LEG-12 requires both surfaces, and D-07 is why: render()'s per-step row
    never shows `reason`, so the hold state needs its own row to be visible
    to a terminal reader at all."""
    report = _minimal_report()
    report.sdp_hold_state = SDP_HOLD_HELD

    d = report.to_dict()
    assert d["sdp_hold_state"] == SDP_HOLD_HELD

    table = report.render()
    assert SDP_HOLD_HELD in _rendered_text(table)


def test_hold_state_not_held_reaches_both_surfaces():
    """`SDP_HOLD_NOT_HELD` (the lock leaked, LEG-06's shape) appears
    verbatim in both surfaces -- the report's last word about a leaked lock
    must be legible on the console, not buried in JSON only."""
    report = _minimal_report()
    report.sdp_hold_state = SDP_HOLD_NOT_HELD

    d = report.to_dict()
    assert d["sdp_hold_state"] == SDP_HOLD_NOT_HELD

    table = report.render()
    assert SDP_HOLD_NOT_HELD in _rendered_text(table)


def test_hold_state_not_run_reason_rides_the_json_but_not_the_console():
    """The `NOT-RUN: <reason>` REASON survives verbatim in
    `to_dict()["sdp_hold_state"]` -- so the saved JSON/markdown artifact and
    the filed issue body all still carry it -- while `render()` shows only
    the bare `NOT-RUN` token.

    This RETARGETS the former
    `test_hold_state_not_run_reason_reaches_both_surfaces`, which required
    the reason to be console-visible too (D-07/LEG-12). The operator
    superseded that console leg on 2026-08-21: on a non-0x0D part the
    reason is a full sentence that Rich wraps across three lines of the
    result box, and it was judged noise there. LEG-12's carriage
    requirement is untouched -- the assertions below still pin the reason
    as un-fabricated and verbatim on the JSON surface, which is what makes
    a filed report actionable."""
    reason_text = "the SDP inhibited-write oracle did not run for this chip"
    hold_value = f"{SDP_HOLD_NOT_RUN}: {reason_text}"

    report = _minimal_report()
    report.sdp_hold_state = hold_value

    d = report.to_dict()
    assert d["sdp_hold_state"] == hold_value
    assert reason_text in d["sdp_hold_state"]

    rendered = _rendered_text(report.render())
    assert SDP_HOLD_NOT_RUN in rendered
    assert reason_text not in rendered
    assert ":" not in rendered.split(SDP_HOLD_NOT_RUN, 1)[1][:2]


def test_hold_state_no_boolean_under_lock_or_protect_key_anywhere_in_to_dict():
    """P-06 prevention 3 (D-10): a JSON `true` on a key like `locked` or
    `protection_enabled` would be read as ground truth for a protection
    state this chip family CANNOT report -- the report would be making a
    claim the milestone's Evidence Ceiling explicitly forbids. Walks the
    WHOLE to_dict() output recursively (not just the new field) so a future
    field named e.g. `sdp_locked` or `write_protect_active` trips this the
    instant it is added as a bool, anywhere in the tree."""

    def _walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = key.lower()
                if isinstance(value, bool) and (
                    "lock" in lowered or "protect" in lowered
                ):
                    raise AssertionError(
                        f"bool under a lock/protect-named key at "
                        f"{path}.{key} -- P-06 prevention 3 violation"
                    )
                _walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]")

    for hold_value in (
        SDP_HOLD_HELD,
        SDP_HOLD_NOT_HELD,
        f"{SDP_HOLD_NOT_RUN}: reason",
    ):
        report = _minimal_report()
        report.sdp_hold_state = hold_value
        _walk(report.to_dict())


def test_hold_state_is_str_never_bool():
    """`to_dict()["sdp_hold_state"]` is a `str` instance and NOT a `bool`
    (in Python `bool` is a subclass of `int`, never of `str`, but this pins
    the field's own type directly rather than relying on that fact) --
    D-10's field is three-valued STRING, never a boolean, so a JSON `true`
    can never be misread as ground truth for an unreadable protection
    state."""
    report = _minimal_report()
    report.sdp_hold_state = SDP_HOLD_HELD

    value = report.to_dict()["sdp_hold_state"]
    assert isinstance(value, str)
    assert not isinstance(value, bool)


def test_schema_version_1_6_single_sourced():
    """`to_dict()["schema_version"]` equals the IMPORTED `SCHEMA_VERSION`
    (never a literal restated here), and the production module bumps the
    constant to its new value in exactly ONE place (single-sourced, D-10) --
    this is the only line in this test file that restates the quoted
    literal, to keep this file's own count at the plan's required "at most
    one". Renamed from `test_schema_version_1_3_single_sourced` (v1.32 Phase
    147 plan 03, D-09): the 1.3 -> 1.4 bump would otherwise leave this test's
    own literal-count assertion asserting a now-absent quoted string. Renamed
    again for the 1.5 -> 1.6 bump (quick task 260821-wna), which added the
    additive per-step `write_region_start`/`write_region_length`/
    `write_bits_cleared`/`write_bits_retained`/`write_current_source` keys."""
    import inspect

    from firestarter import diagnostic_report as dr_mod

    report = _minimal_report()
    assert report.to_dict()["schema_version"] == dr_mod.SCHEMA_VERSION

    source = inspect.getsource(dr_mod)
    assert source.count('"1.6"') == 1


def test_dedup_fingerprint_sensitive_to_sdp_step_verdict_change():
    """D-11's re-key proof: two reports whose step lists differ ONLY in an
    SDP step's verdict produce DIFFERENT dedup_fingerprint values -- this is
    the property that would have been destroyed by excluding the SDP steps
    from the hash (a leaked lock would then group with a held one, blinding
    the mechanism that decides which reports get triaged). The ACCEPTED
    converse cost (recorded beside `dedup_fingerprint` in the production
    module and in this plan's SUMMARY, D-11): every ALLOW chip re-keys when
    these steps are added, so b14/b15-era reports stop grouping with
    v1.30-era ones and their N>=2 promotion counts reset."""
    from firestarter.diagnostic_report import dedup_fingerprint

    report_held = _minimal_report(
        step_specs=[("write-inhibited", VERDICT_OK, None, "")]
    )
    report_leaked = _minimal_report(
        step_specs=[("write-inhibited", VERDICT_BAD, None, "")]
    )

    assert dedup_fingerprint(report_held) != dedup_fingerprint(report_leaked)


# ---------------------------------------------------------------------------
# Explicit unknown identity marker (PROV-05, D-10/D-12/D-13(a), v1.32 Phase
# 147 plan 03) -- proves the marker present-when-absent and absent-when-
# populated so `_identity_cell` can neither under- nor over-fire, and that
# the fenced JSON keeps typed `null` throughout.
# ---------------------------------------------------------------------------


def test_absent_identity_renders_the_explicit_marker_in_both_rows():
    """`_minimal_report()`'s `AutoCapture` never sets `fw_board_identity` or
    `hw_revision`, so both default to `None` -- the render must show the
    explicit `NOT_REPORTED` marker in BOTH rows, never a blank and never the
    bare four-character rendering of a null value. Checked against the EXACT
    `Value` cell for each named row (not a whole-table substring scan)
    because the deliberately-untouched `chip_id (expected/actual)` row
    legitimately renders `None / None` on this same minimal report (D-12) --
    a blanket "no None anywhere" scan would false-positive on that row."""
    from firestarter.diagnostic_report import NOT_REPORTED

    report = _minimal_report()
    table = report.render()
    field_col, value_col = table.columns
    rows = dict(zip(field_col.cells, value_col.cells))

    assert rows["fw_board_identity"] == NOT_REPORTED
    assert rows["hw_revision"] == NOT_REPORTED
    assert rows["fw_board_identity"] != "None"
    assert rows["hw_revision"] != "None"

    # Non-vacuous count check over the WHOLE rendered text: the marker
    # appears exactly twice -- once per identity row, and nowhere else.
    rendered = _rendered_text(table)
    assert rendered.count(NOT_REPORTED) == 2


def test_absent_identity_stays_typed_null_in_to_dict():
    """D-10: the fenced report JSON keeps typed `null` for an absent
    identity -- `to_dict()` (and the JSON block built from it) must never
    substitute the render-only marker, keeping PROV-04's backward-
    compatibility story to ONE case (`is None`) instead of two."""
    from firestarter.diagnostic_report import NOT_REPORTED

    report = _minimal_report()
    d = report.to_dict()

    assert d["auto_capture"]["fw_board_identity"] is None
    assert d["auto_capture"]["hw_revision"] is None

    serialised = report.to_json_block()
    assert NOT_REPORTED not in serialised


def test_populated_identity_rows_render_the_value_verbatim():
    """A report whose two identity fields are populated renders both values
    VERBATIM and the marker ZERO times -- the leg that stops
    `_identity_cell` from over-firing on a genuinely-present value."""
    from firestarter.diagnostic_report import NOT_REPORTED

    report = _minimal_report()
    report.auto_capture.fw_board_identity = "3.0.0b19:leonardo"
    report.auto_capture.hw_revision = "Rev 2.0-class"

    table = report.render()
    field_col, value_col = table.columns
    rows = dict(zip(field_col.cells, value_col.cells))

    assert rows["fw_board_identity"] == "3.0.0b19:leonardo"
    assert rows["hw_revision"] == "Rev 2.0-class"
    assert NOT_REPORTED not in _rendered_text(table)


def test_schema_version_is_one_six():
    """PROV-04: the imported constant equals `"1.6"`, and a freshly built
    report's `to_dict()["schema_version"]` equals the IMPORTED constant --
    never a restated literal in the second assertion. This is the only
    place in the suite that pins WHICH version this phase shipped; every
    other site (including `test_schema_version_1_6_single_sourced` above)
    keeps importing the constant. 1.6 (quick task 260821-wna) added the
    additive per-step write-coverage keys read off `StepResult.write_target`
    -- pre-1.6 consumers ignore them."""
    from firestarter.diagnostic_report import SCHEMA_VERSION

    assert SCHEMA_VERSION == "1.6"

    report = _minimal_report()
    assert report.to_dict()["schema_version"] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Hex-render protocol / chip IDs, noise-row removal (quick task 260821-spg)
#
# `_hex_cell` does not exist yet when these tests are first run -- they are
# added ahead of the implementation (RED) so the helper's contract is pinned
# by a failing test before it exists, then implemented to GREEN. All render
# assertions use the `dict(zip(field_col.cells, value_col.cells))` idiom
# already used above (e.g. test_absent_identity_renders_the_explicit_marker_
# in_both_rows) rather than a whole-table substring scan, so a row that
# happens to contain a forbidden field NAME as a substring of its VALUE
# cannot false-positive the noise-row checks.
# ---------------------------------------------------------------------------


def test_hex_cell_protocol_from_production_decimal_string():
    """`protocol="13"` (production shape: `str(prog.get("algorithm"))`,
    cli_handlers.py) renders `0x0D` -- the console reader sees the SAME base
    firmware dispatch reads, not a decimal integer disconnected from the
    protocol table."""
    report = _minimal_report(protocol="13")

    table = report.render()
    field_col, value_col = table.columns
    rows = dict(zip(field_col.cells, value_col.cells))

    assert rows["protocol"] == "0x0D"


def test_hex_cell_protocol_hex_shape_is_idempotent():
    """`protocol="0x0D"` (test-fixture shape, already hex) renders
    unchanged -- the formatter must not double-convert an already-hex
    string."""
    report = _minimal_report(protocol="0x0D")

    table = report.render()
    field_col, value_col = table.columns
    rows = dict(zip(field_col.cells, value_col.cells))

    assert rows["protocol"] == "0x0D"


def test_hex_cell_protocol_none_renders_none_without_raising():
    report = _minimal_report(protocol=None)

    table = report.render()
    field_col, value_col = table.columns
    rows = dict(zip(field_col.cells, value_col.cells))

    assert rows["protocol"] == "None"


def test_hex_cell_protocol_non_numeric_renders_verbatim_without_raising():
    report = _minimal_report(protocol="banana")

    table = report.render()
    field_col, value_col = table.columns
    rows = dict(zip(field_col.cells, value_col.cells))

    assert rows["protocol"] == "banana"


def test_chip_id_one_sided_row_when_no_mismatch_was_recorded():
    """`chip_id_actual is None` (a clean/NA/SKIPPED id step) collapses to a
    ONE-sided `chip_id` row -- no `/ None` tail.

    RETARGETED 2026-08-21 (was `test_hex_cell_chip_id_partial_is_none_safe`,
    which pinned `"0x00A4 / None"`). `chip_id_actual` is populated ONLY on a
    mismatch: on a passing id check the firmware's OK reply carries no id,
    so `check_eprom_id` returns the host's own expected value echoed back
    and `_chip_id_fields` discards it rather than present a never-measured
    number as a measurement. Printing the resulting `None` beside a real
    expected id read as a FAILED read, which is what the operator queried.
    The `None`-safety the original test guarded still holds -- `_hex_cell`
    is unchanged and its own None/unparseable cases are covered by
    test_hex_cell_returns_str_value_unchanged_for_none_and_unparseable."""
    report = _minimal_report()
    report.auto_capture.chip_id_expected = 0x00A4
    report.auto_capture.chip_id_actual = None

    table = report.render()
    field_col, value_col = table.columns
    rows = dict(zip(field_col.cells, value_col.cells))

    assert rows["chip_id"] == "0x00A4"
    assert "chip_id (expected/actual)" not in rows


def test_chip_id_two_sided_row_only_when_a_mismatch_was_recorded():
    """The expected/actual pair appears ONLY when there is a real
    disagreement to show -- the mismatch is the whole reason the row is
    two-sided, so it must survive (2026-08-21)."""
    report = _minimal_report()
    report.auto_capture.chip_id_expected = 0x00A4
    report.auto_capture.chip_id_actual = 0x1234

    table = report.render()
    field_col, value_col = table.columns
    rows = dict(zip(field_col.cells, value_col.cells))

    assert rows["chip_id (expected/actual)"] == "0x00A4 / 0x1234"
    assert "chip_id" not in rows


def test_hex_cell_chip_id_both_populated_is_4_digit_upper_hex():
    report = _minimal_report()
    report.auto_capture.chip_id_expected = 0x1234
    report.auto_capture.chip_id_actual = 0x1234

    table = report.render()
    field_col, value_col = table.columns
    rows = dict(zip(field_col.cells, value_col.cells))

    assert rows["chip_id (expected/actual)"] == "0x1234 / 0x1234"


_NOISE_ROW_FIELDS = (
    "transport_health",
    "is_submittable",
    "db_diff",
    "db_diff: current_support_status",
    "db_diff: proposed_disposition",
    "db_diff: ladder_state",
)


def test_render_has_no_noise_rows_when_db_diff_is_populated():
    """No `transport_health`, `is_submittable` or `db_diff*` row -- checked
    against a report whose `db_diff` IS populated, so the populated-branch
    code path (that used to emit three extra rows) is exercised too."""
    from firestarter.diagnostic_report import build_db_diff

    report = _minimal_report()
    db = _mock_db(support_status="adapter-required")
    report.db_diff = build_db_diff("SOME-CHIP", db, report.results)

    table = report.render()
    field_col, _ = table.columns
    fields = set(field_col.cells)

    for forbidden in _NOISE_ROW_FIELDS:
        assert forbidden not in fields


def test_render_has_no_noise_rows_when_db_diff_is_none():
    """Same assertion against the OLD `not computed` fallback path -- a
    report whose `db_diff` is `None` (the default) must not print that
    fallback row either."""
    report = _minimal_report()
    assert report.db_diff is None

    table = report.render()
    field_col, _ = table.columns
    fields = set(field_col.cells)

    for forbidden in _NOISE_ROW_FIELDS:
        assert forbidden not in fields


def test_step_row_value_is_bare_verdict_no_error_code_or_fingerprint_suffix():
    """A step carrying a non-`None` `error_code` and a `Fingerprint` still
    renders a Value cell equal to the verdict string exactly -- the
    `err=.../fingerprint=...` suffix is gone."""
    report = _minimal_report(
        step_specs=[("write", VERDICT_BAD, FP_ADDRESS_LINE, "some reason")]
    )
    report.results[0].error_code = 42

    table = report.render()
    field_col, value_col = table.columns
    rows = dict(zip(field_col.cells, value_col.cells))

    assert rows["step: write"] == VERDICT_BAD


def test_render_keeps_the_surviving_rows():
    """The rows that stay are still present, by Field name."""
    report = _minimal_report()

    table = report.render()
    field_col, _ = table.columns
    fields = set(field_col.cells)

    for expected in (
        "host_version",
        "fw_board_identity",
        "hw_revision",
        "protocol",
        # One-sided on a minimal report: `chip_id_actual` is only set on a
        # mismatch (2026-08-21). The two-sided label is pinned by
        # test_chip_id_two_sided_row_only_when_a_mismatch_was_recorded.
        "chip_id",
        "banner",
        "sdp_hold_state",
        # One row per rail since 2026-08-21 -- the single six-value
        # `voltage` row repeated the `vpp_mv`/`vpe_mv` standalone slots
        # that no code path assigns, so it always carried two dead
        # `not measured` fields beside the real bracket numbers.
        "vpp (before/after)",
        "vpe (before/after)",
    ):
        assert expected in fields
    assert "voltage" not in fields
    assert any(f.startswith("step: ") for f in fields)


def test_to_dict_payload_unchanged_by_the_render_trim():
    """The removed console rows' DATA is still in `to_dict()` -- this is
    the non-vacuity proof that only the console changed. Every key that
    fed a removed row is still present, and every step dict still carries
    `error_code` and `fingerprint`."""
    from firestarter.diagnostic_report import build_db_diff

    report = _minimal_report(
        step_specs=[("write", VERDICT_BAD, FP_ADDRESS_LINE, "some reason")]
    )
    report.results[0].error_code = 42
    db = _mock_db(support_status="adapter-required")
    report.db_diff = build_db_diff("SOME-CHIP", db, report.results)

    d = report.to_dict()

    assert "transport_health" in d
    assert "is_submittable" in d
    assert "db_diff" in d and d["db_diff"] is not None
    assert "dedup_fingerprint" in d
    assert "sdp_hold_state" in d
    assert "voltage" in d
    for step_row in d["steps"]:
        assert "error_code" in step_row
        assert "fingerprint" in step_row
    assert d["steps"][0]["error_code"] == 42
    assert d["steps"][0]["fingerprint"] == FP_ADDRESS_LINE


# ---------------------------------------------------------------------------
# Per-step timings (schema 1.5, 2026-08-21): the operator asked for timings
# captured, presented in the box, and carried to GitHub. These pin the
# report-side half; `tests/test_chip_test_timing.py` pins the capture half.
# ---------------------------------------------------------------------------


def test_step_duration_reaches_json_and_console():
    """`duration_s` is in `to_dict()["steps"]` AND appended to the step's
    console cell -- both surfaces, because a timing only in the JSON does
    not answer "where did this run spend 3 minutes" at the terminal."""
    report = _minimal_report(step_specs=[("read", VERDICT_OK, None, "")])
    report.results[0].duration_s = 41.875

    d = report.to_dict()
    assert d["steps"][0]["duration_s"] == 41.875

    rows = dict(zip(*[c.cells for c in report.render().columns]))
    assert rows["step: read"] == "OK  41.9s"


def test_step_with_no_duration_renders_bare_verdict():
    """A step carrying no duration renders exactly as before -- no trailing
    separator, no `None`. Guards the NA/SKIPPED shape and any pre-1.5
    `StepResult` replayed through `render()`."""
    report = _minimal_report(step_specs=[("write", VERDICT_BAD, None, "")])
    assert report.results[0].duration_s is None

    rows = dict(zip(*[c.cells for c in report.render().columns]))
    assert rows["step: write"] == VERDICT_BAD


def test_steps_total_row_sums_only_steps_that_ran():
    """The `steps total` row sums the per-step durations present, skipping
    `None`. It is a RENDER-only derivation: `to_dict()` gains no total key,
    since a consumer can re-add the per-step values itself."""
    report = _minimal_report(
        step_specs=[
            ("read", VERDICT_OK, None, ""),
            ("write", VERDICT_OK, None, ""),
            ("erase", "NA", None, ""),
        ]
    )
    report.results[0].duration_s = 41.875
    report.results[1].duration_s = 0.09

    rows = dict(zip(*[c.cells for c in report.render().columns]))
    # 41.875 + 0.09 = 41.965 -> "42.0s"; the NA step contributes nothing.
    assert rows["steps total"] == "42.0s"
    assert "steps_total" not in report.to_dict()
    assert "total" not in report.to_dict()


def test_durations_do_not_perturb_dedup_fingerprint():
    """Two reports identical except for their step durations MUST produce
    the SAME `dedup_fingerprint`.

    This is the load-bearing property: the fingerprint deliberately excludes
    every volatile field so a second run of the same chip still groups with
    the first. Wall-clock timings are the most volatile field yet added, so
    a fingerprint that read them would make every single run unique and
    silently destroy duplicate detection."""
    from firestarter.diagnostic_report import dedup_fingerprint

    fast = _minimal_report(step_specs=[("read", VERDICT_OK, None, "")])
    fast.results[0].duration_s = 0.5

    slow = _minimal_report(step_specs=[("read", VERDICT_OK, None, "")])
    slow.results[0].duration_s = 987.654

    assert dedup_fingerprint(fast) == dedup_fingerprint(slow)


def test_duration_cell_formatting_boundaries():
    """Two decimals under 10 s so a 0.03 s id check is not rounded away to
    `0.0s`; one decimal at and above 10 s. `None` and unparseable render
    `""` so the caller can omit the suffix entirely."""
    from firestarter.diagnostic_report import _duration_cell

    assert _duration_cell(0.03) == "0.03s"
    assert _duration_cell(9.994) == "9.99s"
    assert _duration_cell(10) == "10.0s"
    assert _duration_cell(41.875) == "41.9s"
    assert _duration_cell(None) == ""
    assert _duration_cell("not-a-number") == ""
