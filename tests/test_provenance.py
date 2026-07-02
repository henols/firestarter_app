"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Pytest unit tests for the `Provenance` model + `prompt_provenance()` +
`is_submittable()` added to `firestarter/diagnostic_report.py` (v1.21
Phase 110 Plan 02, RPT-04).

Bench-free: `ask`/`confirm` are `Mock(side_effect=[...])` callables so no
TTY is ever touched (mirrors the `_mock_operator` Mock(spec=[...]) seam in
test_chip_test.py:570 / test_diagnostic_report.py).

Test taxonomy:

  Injectable prompt seam + submittability (RPT-04, D-05)
    test_provenance_submittable        -> fully-answered Provenance is
                                           submittable; blank shield_rev is not
    test_not_sure_is_submittable       -> "not sure" counts as FILLED (D-05)
    test_blank_shield_not_submittable  -> "" on a required field is NOT filled

  UV-eraser conditional prompt (D-06)
    test_uv_eraser_prompt_only_when_uv -> eraser Confirm asked only when
                                           is_uv=True; owns_eraser stays None
                                           and unasked when is_uv=False

  No hw_revision auto-derive (D-05, SAFE-02)
    test_shield_rev_not_autoderived    -> structural source scan: no
                                           "hw_revision" read, no
                                           HardwareManager/SerialCommunicator
                                           import

References:
  - .planning/phases/110-diagnostic-report-model-dual-output-provenance-prompts/110-02-PLAN.md
  - .planning/phases/110-diagnostic-report-model-dual-output-provenance-prompts/110-RESEARCH.md
  - .planning/phases/110-diagnostic-report-model-dual-output-provenance-prompts/110-PATTERNS.md
"""

from __future__ import annotations

import inspect
from unittest.mock import Mock

from firestarter.diagnostic_report import (
    SHIELD_REV_CHOICES,
    Provenance,
    is_submittable,
    prompt_provenance,
)

# ---------------------------------------------------------------------------
# Injectable prompt seam + submittability (RPT-04, D-05)
# ---------------------------------------------------------------------------


def test_provenance_submittable():
    # Prompt order (is_uv=False): shield_rev, chip_origin, pot_touched,
    # pot_note (asked only because pot_touched is True here).
    ask = Mock(side_effect=["Rev 2.2", "new/blank", "adjusted R41"])
    confirm = Mock(side_effect=[True])  # pot_touched

    prov = prompt_provenance(is_uv=False, ask=ask, confirm=confirm)

    assert prov.shield_rev == "Rev 2.2"
    assert prov.chip_origin == "new/blank"
    assert prov.pot_touched is True
    assert prov.pot_note == "adjusted R41"
    assert prov.owns_eraser is None
    assert is_submittable(prov) is True

    # A Provenance with shield_rev="" is NOT submittable.
    blank = Provenance(shield_rev="", chip_origin="new/blank", pot_touched=False)
    assert is_submittable(blank) is False


def test_not_sure_is_submittable():
    prov = Provenance(shield_rev="not sure", chip_origin="new/blank", pot_touched=False)
    assert is_submittable(prov) is True


def test_blank_shield_not_submittable():
    prov = Provenance(shield_rev="", chip_origin="new/blank", pot_touched=False)
    assert is_submittable(prov) is False


# ---------------------------------------------------------------------------
# UV-eraser conditional prompt (D-06)
# ---------------------------------------------------------------------------


def test_uv_eraser_prompt_only_when_uv():
    # is_uv=True: shield_rev, chip_origin, owns_eraser (confirm), pot_touched
    # (confirm, False here so pot_note is not asked).
    ask_uv = Mock(side_effect=["not sure", "pulled/used"])
    confirm_uv = Mock(side_effect=[True, False])  # owns_eraser, pot_touched

    prov_uv = prompt_provenance(is_uv=True, ask=ask_uv, confirm=confirm_uv)

    assert isinstance(prov_uv.owns_eraser, bool)
    assert prov_uv.owns_eraser is True
    assert confirm_uv.call_count == 2  # eraser question WAS asked

    # is_uv=False: eraser question is NOT asked; owns_eraser stays None.
    ask_no_uv = Mock(side_effect=["not sure", "pulled/used"])
    confirm_no_uv = Mock(side_effect=[False])  # pot_touched only

    prov_no_uv = prompt_provenance(is_uv=False, ask=ask_no_uv, confirm=confirm_no_uv)

    assert prov_no_uv.owns_eraser is None
    assert confirm_no_uv.call_count == 1  # only pot_touched was asked


# ---------------------------------------------------------------------------
# No hw_revision auto-derive (D-05, SAFE-02)
# ---------------------------------------------------------------------------


def test_shield_rev_not_autoderived():
    """Structural scan: the module reads no hardware-revision byte and
    imports no HardwareManager/SerialCommunicator (D-05, the Bug A lesson)."""
    import ast

    import firestarter.diagnostic_report as diagnostic_report_mod

    src = inspect.getsource(diagnostic_report_mod)
    assert "hw_revision" not in src

    tree = ast.parse(src)
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)

    assert "HardwareManager" not in imported_names
    assert "SerialCommunicator" not in imported_names

    # SHIELD_REV_CHOICES sanity: community-tolerant, includes the explicit
    # "not sure" and "other" escapes (D-06).
    assert "not sure" in SHIELD_REV_CHOICES
    assert "other" in SHIELD_REV_CHOICES
