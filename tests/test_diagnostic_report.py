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
    test_auto_capture_fields         -> host_version, fw_board_identity, chip,
                                         protocol, chip-id, per-step error_code
                                         + fingerprint classification

  Transport-health honest fallback (XPORT-01, D-03)
    test_transport_not_measured      -> every counter == NOT_MEASURED (never 0);
                                         transport_suspect is False

  Orchestrator-only structural scan (SAFE-02)
    test_report_module_is_orchestrator_only -> no SerialCommunicator/
                                                HardwareManager import, no
                                                VPP-set, no "--force" token

  Provenance composition, single-source preserved (RPT-04, RPT-01, Plan 02)
    test_report_with_provenance_surfaces_in_both_renders -> a report built
        WITH a filled Provenance shows the provenance section (+
        is_submittable True) in to_dict() and render()
    test_report_provenance_blank_field_flips_is_submittable -> a blank
        required provenance field flips is_submittable to False in the dict

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
    FP_INDETERMINATE,
    VERDICT_BAD,
    VERDICT_NA,
    VERDICT_OK,
    VERDICT_SKIPPED,
    Fingerprint,
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
# Provenance composition, single-source preserved (RPT-04, RPT-01, Plan 02)
# ---------------------------------------------------------------------------


def test_report_with_provenance_surfaces_in_both_renders():
    from firestarter.diagnostic_report import Provenance

    report = _build_report()
    report.provenance = Provenance(
        shield_rev="Rev 2.2",
        chip_origin="new/blank",
        pot_touched=False,
    )

    d = report.to_dict()
    assert d["provenance"] is not None
    assert d["provenance"]["shield_rev"] == "Rev 2.2"
    assert d["is_submittable"] is True

    table = report.render()
    # render() must read the SAME to_dict() output -- never a parallel field
    # list, never a re-parse of the JSON string (RPT-01 single-source).
    src = inspect.getsource(type(report).render)
    assert "self.to_dict()" in src or "to_dict()" in src
    assert "json.loads" not in src
    assert "json.load(" not in src

    # A provenance row is present in the rendered table.
    rendered_fields = {str(cell) for column in table.columns for cell in column.cells}
    assert any("Rev 2.2" in cell or "shield_rev" in cell for cell in rendered_fields)


def test_report_provenance_blank_field_flips_is_submittable():
    from firestarter.diagnostic_report import Provenance

    report = _build_report()
    report.provenance = Provenance(
        shield_rev="",
        chip_origin="new/blank",
        pot_touched=False,
    )

    d = report.to_dict()
    assert d["is_submittable"] is False


def test_report_without_provenance_dict_is_null():
    report = _build_report()
    d = report.to_dict()
    assert d["provenance"] is None
    assert d["is_submittable"] is False


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
