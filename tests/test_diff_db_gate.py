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
