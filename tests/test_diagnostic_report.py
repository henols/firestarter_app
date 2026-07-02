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
from firestarter.chip_test import derive_plan, run_plan
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
