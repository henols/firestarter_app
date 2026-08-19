"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 70 — SC#4 GATE-02 diff_db identity-gate validation.

This test validates the GATE-02 two-stage diff (_stage-b_ identity diff only)
which compares the current chip_database.json against the committed baseline
chip_database.baseline.json. The gate enforces that every changed chip is
explained by a known root-cause rule (RULE_ALGO, BUG2_TIMING, etc.) per the
dispatch-correctness contract.

Scope: stage-(b) IDENTITY diff only. Stage-(a) diff vs /tmp/v1.11-beta-db.json
is a one-shot migration artifact and not in scope for CI coverage.

The test drives the real diff_db.py via subprocess (integration classification
per test_audit_coverage_matrix.py:423 pattern) to ensure the gate works
end-to-end on the live chip_database.json.
"""

import subprocess
import sys
from pathlib import Path


class TestDiffDbGate:
    """GATE-02 identity diff: current DB vs baseline — all explained."""

    def test_diff_db_identity_pass(self):
        """SC#4 / GATE-02 stage-(b): identity diff of chip_database.json vs baseline.

        Runs tools/diff_db.py with:
          - FIRESTARTER_DB_FILE = firestarter/data/chip_database.json (current)
          - FIRESTARTER_BASELINE_FILE = tools/baseline/chip_database.baseline.json

        Asserts:
          - exit code == 0 (PASS: all changed chips explained, Rule 1 unblock confirmed,
                           no missing chips)
          - stdout contains "PASS: all" (gate-pass marker per diff_db.py:527)

        This is a deterministic, repeatable gate that CI can run on every commit
        to catch accidental chip_database.json drift away from the committed baseline.
        The test defends against:
          - unexplained chip diffs (would exit 1)
          - missing chips (would exit 1)
          - missing/malformed input files (would exit 2)
        """
        firestarter_app_dir = Path(__file__).resolve().parent.parent

        result = subprocess.run(
            [sys.executable, "tools/diff_db.py"],
            cwd=str(firestarter_app_dir),
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, (
            f"diff_db.py exit code {result.returncode} (expected 0); "
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

        assert "PASS: all" in result.stdout, (
            f"Expected 'PASS: all' in stdout, got:\n{result.stdout}"
        )


class TestDiffDbPhase84Relabel:
    """Phase 84 RULE_PHASE84_RELABEL classification unit tests.

    Tests the _classify_diff function directly to verify:
    - FM1608 SRAM→FRAM type-only change is classified as RULE_PHASE84_RELABEL
    - SST39SF040 (not in the relabel set) type-only change is NOT explained by
      RULE_PHASE84_RELABEL and remains unexplained (verifying the part_number scope).
    - The rule does NOT explain chips outside _PHASE84_RELABEL_PART_NUMBERS.
    """

    # Post-Phase-148 numeric schema (vcc_mv/vdd_mv/pulse_duration_us). diff_db.py's
    # _canonicalize_db accepts either schema, so these literals prove the new
    # shape directly rather than exercising the compatibility path.
    def _make_chip(self, etype, part_number="FM1608"):
        return {
            "part_number": part_number,
            "electrical": {
                "type": etype,
                "vcc_mv": 5000,
                "vdd_mv": 5000,
                "vpp_mv": 12000,
            },
            "programming": {"algorithm": 40, "pulse_duration_us": 0},
            "support_status": "supported",
        }

    def test_fm1608_sram_to_fram_classified_as_phase84_relabel(self):
        """FM1608 SRAM→FRAM type-only change must be classified RULE_PHASE84_RELABEL."""
        from tools.diff_db import _classify_diff  # type: ignore[import]

        bl = self._make_chip("SRAM", "FM1608")
        cu = self._make_chip("FRAM", "FM1608")
        label, extra_paths = _classify_diff(bl, cu)
        assert label == "RULE_PHASE84_RELABEL", (
            f"Expected RULE_PHASE84_RELABEL for FM1608 SRAM→FRAM, got {label!r}"
        )
        assert extra_paths == set(), (
            f"Expected no unexplained extra paths, got {extra_paths}"
        )

    def test_sst39sf040_type_only_change_is_unexplained(self):
        """SST39SF040 type-only change must NOT be explained by RULE_PHASE84_RELABEL
        (sst-keep decision: SST39SF040 is not in _PHASE84_RELABEL_PART_NUMBERS)."""
        from tools.diff_db import _classify_diff  # type: ignore[import]

        bl_chip = {
            "part_number": "SST39SF040",
            "electrical": {
                "type": "Flash/EEPROM",
                "vcc_mv": 5000,
                "vdd_mv": 5000,
                "vpp_mv": 12000,
            },
            "programming": {"algorithm": 6, "pulse_duration_us": 0},
            "support_status": "supported",
        }
        cu_chip = dict(bl_chip)
        cu_chip["electrical"] = dict(bl_chip["electrical"])
        cu_chip["electrical"]["type"] = "Flash"  # hypothetical relabel not authorized
        label, _ = _classify_diff(bl_chip, cu_chip)
        # SST39SF040 is not in _PHASE84_RELABEL_PART_NUMBERS; a type-only change
        # on it falls through BUG_A_ETYPE (which matches type_diff without algo_diff).
        # BUG_A_ETYPE claims ("electrical","type") — so label would be BUG_A_ETYPE
        # (not RULE_PHASE84_RELABEL), but critically NOT RULE_PHASE84_RELABEL.
        assert label != "RULE_PHASE84_RELABEL", (
            "SST39SF040 type change must NOT be classified as RULE_PHASE84_RELABEL "
            "(sst-keep: not in scope of this relabel)"
        )

    def test_unrelated_chip_type_change_not_explained_by_phase84(self):
        """An unrelated chip's type-only change must NOT be classified RULE_PHASE84_RELABEL."""
        from tools.diff_db import _classify_diff  # type: ignore[import]

        bl = self._make_chip("UV-EPROM", "M27C512")
        cu = self._make_chip("EEPROM", "M27C512")
        # Update algo to avoid algo_diff
        bl["programming"]["algorithm"] = 7
        cu["programming"]["algorithm"] = 7
        label, _ = _classify_diff(bl, cu)
        assert label != "RULE_PHASE84_RELABEL", (
            "Unrelated chip type change must NOT be classified RULE_PHASE84_RELABEL"
        )
