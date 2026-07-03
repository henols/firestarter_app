"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Pytest unit tests for the auto-capture-only submittability model in
`firestarter/diagnostic_report.py` (v1.21 Phase 112 Plan 04).

REVERSAL: this module previously tested the interactive tester-input
collector (RPT-04, D-04/D-05/D-06) -- a collector function, a human-input
dataclass, and enumerated choice-list constants for shield revision and
chip origin. Per the operator-approved descope recorded in `112-UAT.md`
(test 2), that entire model is gone: those choice strings contained a
path-separator character that collided with the third-party prompt
library's own separator-rendered choice display, rejecting natural inputs
like `new`/`used`/`2.0`. Every question that model asked is now either
firmware/DB auto-captured (`hw_revision`, `fw_board_identity`, `protocol`)
or dropped as self-reported-and-unverifiable/redundant (chip origin,
UV-eraser ownership, pot-touched).

Bench-free: no TTY, no serial -- `AutoCapture`/`DiagnosticReport` are plain
dataclasses assembled directly (mirrors the `test_diagnostic_report.py`
seam).

Test taxonomy:

  Auto-captured hw_revision, single-source flow-through (RPT-01, RPT-02)
    test_hw_revision_auto_captured   -> hw_revision flows through
                                         to_dict()["auto_capture"] and the
                                         rendered table
    test_hw_revision_none_is_honest  -> None surfaces as None, never a
                                         fabricated value

  Auto-capture-only submittability (Phase 112 Plan 04)
    test_is_submittable_auto_capture_only -> True on complete auto-capture,
                                              False when a required
                                              auto-captured field is blank

  Reintroduction guard
    test_no_interactive_provenance_symbols -> the deleted collector
                                               function, dataclass, and
                                               choice-list constants no
                                               longer exist

References:
  - .planning/phases/112-dev-test-handler-wiring/112-UAT.md (test 2, root_cause)
  - .planning/phases/112-dev-test-handler-wiring/112-04-PLAN.md
"""

from __future__ import annotations

import inspect

from firestarter.chip_test import BannerCounts, Plan
from firestarter.diagnostic_report import (
    AutoCapture,
    DiagnosticReport,
    TransportHealth,
    is_submittable,
)

# ---------------------------------------------------------------------------
# Auto-captured hw_revision, single-source flow-through (RPT-01, RPT-02)
# ---------------------------------------------------------------------------


def _minimal_report(**auto_capture_kwargs) -> DiagnosticReport:
    ac_defaults = {"host_version": "3.0.0", "chip": "M8720", "protocol": "8"}
    ac_defaults.update(auto_capture_kwargs)
    return DiagnosticReport(
        auto_capture=AutoCapture(**ac_defaults),
        transport=TransportHealth(),
        plan=Plan(name=ac_defaults["chip"], steps=[], locked_destructive=[]),
        banner=BannerCounts(n_ran=0, m_applicable=0, locked_steps=[]),
    )


def test_hw_revision_auto_captured():
    report = _minimal_report(hw_revision="Rev 2.0-class")

    d = report.to_dict()
    assert d["auto_capture"]["hw_revision"] == "Rev 2.0-class"

    table = report.render()
    rendered_values = [str(cell) for column in table.columns for cell in column.cells]
    assert "Rev 2.0-class" in rendered_values


def test_hw_revision_none_is_honest():
    report = _minimal_report(hw_revision=None)

    d = report.to_dict()
    assert d["auto_capture"]["hw_revision"] is None


# ---------------------------------------------------------------------------
# Auto-capture-only submittability (Phase 112 Plan 04)
# ---------------------------------------------------------------------------


def test_is_submittable_auto_capture_only():
    complete = AutoCapture(host_version="3.0.0", chip="M8720", protocol="8")
    assert is_submittable(complete) is True

    missing_chip = AutoCapture(host_version="3.0.0", chip="", protocol="8")
    assert is_submittable(missing_chip) is False

    missing_protocol = AutoCapture(host_version="3.0.0", chip="M8720", protocol=None)
    assert is_submittable(missing_protocol) is False

    missing_host_version = AutoCapture(host_version="", chip="M8720", protocol="8")
    assert is_submittable(missing_host_version) is False

    # hw_revision / fw_board_identity are informational-best-effort and never
    # gate submittability -- a report with neither measured is still
    # submittable as long as the objective identity is present.
    honest_none = AutoCapture(
        host_version="3.0.0",
        chip="M8720",
        protocol="8",
        hw_revision=None,
        fw_board_identity=None,
    )
    assert is_submittable(honest_none) is True


# ---------------------------------------------------------------------------
# Reintroduction guard
# ---------------------------------------------------------------------------


def test_no_interactive_provenance_symbols():
    """Structural assert: the deleted interactive-provenance surface never
    reappears (guards against reintroduction)."""
    import firestarter.diagnostic_report as diagnostic_report_mod

    assert not hasattr(diagnostic_report_mod, "prompt_provenance")
    assert not hasattr(diagnostic_report_mod, "Provenance")
    assert not hasattr(diagnostic_report_mod, "SHIELD_REV_CHOICES")
    assert not hasattr(diagnostic_report_mod, "_CHIP_ORIGIN_CHOICES")

    src = inspect.getsource(diagnostic_report_mod)
    assert "def prompt_provenance" not in src
    assert "class Provenance" not in src
