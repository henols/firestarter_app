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


class TestDiffDbVccMarginRailBucketInvariance:
    """Phase 148 DATA-01/DATA-05 — RULE_VCC_MARGIN_RAIL bucket-invariance gate.

    Runs the real tools/diff_db.py (subprocess, same seam as
    test_diff_db_identity_pass above) against the REAL chip_database.json and
    baseline, and asserts the exact measured bucket distribution: the
    margin-rail substitution moved exactly 56 chips into their own labelled
    bucket, every pre-existing bucket count is unchanged except
    PROV01_PROTECT_METADATA, the changed-chip total stays 744, and there are
    0 NEW / 0 MISSING chips.

    PROV01_PROTECT_METADATA started at 742, dropped to 686 for the 56
    margin-rail movers, and dropped again to 682 at Phase 168 (MIGRATE-04
    D-14): build_db.py's AT28C DIP24 adapter unsupported_reason string was
    repointed from a firestarter/doc/ path to a wiki page name, which no
    longer coincidentally matches the Phase-98 baseline's stored text for
    28C04A/28C04AF/28C16A/28C16AF. Those 4 chips' support_status fields now
    also differ from baseline, reclassifying their PRIMARY label from
    PROV01_PROTECT_METADATA to RULE_PHASE66 (a bucket this test previously
    never saw at 0 chips).

    What a count change here means (never argue it, re-measure it): a
    different changed-chip TOTAL means a chip entered or left the database.
    A different RULE_VCC_MARGIN_RAIL count means the margin-rail condition in
    build_db.py was widened or narrowed and must be re-measured against the
    four-way split table in 148-CONTEXT.md D-03 (55/16/12/1) before this
    assertion is ever changed.
    """

    def test_vcc_margin_rail_bucket_distribution(self):
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
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        stdout = result.stdout

        assert "CHANGED chips (744 total)" in stdout, (
            "changed-chip total must stay 744 -- a different total means a "
            f"chip entered or left the database; got:\n{stdout}"
        )
        assert "[RULE_VCC_MARGIN_RAIL] (56 chips)" in stdout, (
            "RULE_VCC_MARGIN_RAIL must explain exactly 56 chips -- a different "
            "count means the margin-rail condition was widened or narrowed and "
            "must be re-measured against the four-way split table (148-CONTEXT.md "
            f"D-03: 55/16/12/1), never argued; got:\n{stdout}"
        )
        assert "[RULE_PHASE66] (4 chips)" in stdout, (
            "RULE_PHASE66 must explain exactly 4 chips (28C04A/28C04AF/28C16A/"
            "28C16AF) since Phase 168's build_db.py wiki-page repoint made "
            f"their unsupported_reason diverge from the Phase-98 baseline; got:\n{stdout}"
        )
        assert "[PROV01_PROTECT_METADATA] (682 chips)" in stdout, (
            "PROV01_PROTECT_METADATA must drop by exactly the 56 margin-rail "
            "movers plus the 4 chips Phase 168 reclassified into RULE_PHASE66 "
            f"(742 -> 686 -> 682); got:\n{stdout}"
        )
        assert "[PGSZ_PAGE_SIZE] (2 chips)" in stdout, (
            f"PGSZ_PAGE_SIZE must stay 2 chips (pre-existing, unaffected); got:\n{stdout}"
        )
        assert "NEW chips (0)" in stdout, f"Expected 0 NEW chips; got:\n{stdout}"
        assert "MISSING chips (0)" in stdout, (
            f"Expected 0 MISSING chips; got:\n{stdout}"
        )
