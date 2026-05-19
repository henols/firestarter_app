"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 11 — Coverage Matrix & DB Inconsistency Audit — Wave 0 failing-test
scaffolding for the algorithm-0x07 + algorithm-0x08 coverage-matrix tool.

This file is intentionally landed BEFORE the production tool exists so that
every later wave (1-4) has a discoverable, meaningful red test to make green.
Per .planning/phases/11-coverage-matrix-db-inconsistency-audit/11-VALIDATION.md
"Per-Task Verification Map", these 10 test names are the contract:

    Wave 1 — summary stats + CLI exit codes (D-03, D-07)
        - test_summary_stats
        - test_exit_codes
    Wave 2 — enumeration + idempotence (COV-01, D-02, D-06)
        - test_enumeration_row_count
        - test_enumeration_sort
        - test_idempotence
    Wave 3 — defect findings + ledger semantics (COV-02, D-12, D-13, D-15)
        - test_hazard_cluster_42_rows
        - test_ledger_idempotent
        - test_ledger_id_reuse
    Wave 4 — bench-coverage proof + golden-file regression
              (COV-01, COV-02, SC-03, D-09, D-10, D-11)
        - test_bench_coverage_proof
        - test_golden_file_matches

NYQUIST CONTRACT (VALIDATION.md): each `test_*` function below is collectible
by `pytest --collect-only` today and FAILS meaningfully (NotImplementedError)
when invoked, until the wave that owns it implements its body. None of these
tests use `pytest.mark.skip` — silent skips would defeat the per-task sampling
rate VALIDATION.md mandates (< 5 s per commit).

Imports of `tools.audit_coverage_matrix` are DEFERRED to inside each function
body so that pytest collection succeeds even before the tool module exists.
Wave 1 creates `firestarter_app/tools/audit_coverage_matrix.py` with the
`generate_matrix(output, ledger_path) -> int` surface declared in the plan's
<interfaces> block.

The autouse fixture below clears `FIRESTARTER_DB_FILE` so each test starts
hermetically and the production DB path resolution kicks in unless the test
explicitly sets the env var to a tmp-path fixture (mirrors
test_fwguard.py:31-42 `_clear_escape_hatch` pattern).
"""

import pytest


class TestAuditCoverageMatrix:
    """Phase 11 — coverage matrix generator + ID-stable defect ledger."""

    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch):
        """Ensure FIRESTARTER_DB_FILE is unset for every test by default.

        Tests that want a tmp-path DB call `monkeypatch.setenv(...)` AFTER this
        autouse fixture has cleared it; the per-test setenv overrides the
        delenv for that single test. Mirrors test_fwguard.py:31-42 pattern.
        """
        monkeypatch.delenv("FIRESTARTER_DB_FILE", raising=False)

    # ------------------------------------------------------------------
    # Wave 2 — enumeration (COV-01)
    # ------------------------------------------------------------------

    def test_enumeration_row_count(self, tmp_path):
        """COV-01 / D-06: §3 contains exactly 339 enumerated in-scope rows.

        Wave 2 impl: import generate_matrix, run it into tmp_path, parse §3
        body table, assert row count == 339 (= 212 algo-0x07 + 127 algo-0x08
        per the post-WARNING-5 / post-fm1608 reconciled DB histogram in
        PATTERNS.md §"Database state").
        """
        from tools.audit_coverage_matrix import generate_matrix  # noqa: F401
        raise NotImplementedError(
            "Wave 2 — see VALIDATION.md row 11-COV-01-row-count "
            "(COV-01 / D-06: §3 must enumerate exactly 339 in-scope rows)"
        )

    def test_enumeration_sort(self, tmp_path):
        """COV-01 / D-06: §3 rows sorted by

            (algorithm asc, pinout asc, size_bytes asc, manufacturer asc,
             first_alias asc)

        Wave 2 impl: parse §3 table, assert each consecutive pair of rows
        satisfies the lexicographic sort-key tuple from PATTERNS.md Pattern F.
        This invariant is load-bearing for byte-identical re-runs (Pattern B
        codegen-idempotence guarantee).
        """
        from tools.audit_coverage_matrix import generate_matrix  # noqa: F401
        raise NotImplementedError(
            "Wave 2 — see VALIDATION.md row 11-COV-01-sort "
            "(COV-01 / D-06 sort tuple: (algorithm, pinout, size_bytes, "
            "manufacturer, first_alias) ascending)"
        )

    def test_idempotence(self, tmp_path):
        """COV-01 / D-02: byte-identical matrix + unchanged ledger on re-run.

        Wave 2 impl follows RESEARCH.md §"Code Examples" lines 242-255 verbatim:

            out_a = tmp_path / "a.md"
            out_b = tmp_path / "b.md"
            ledger = tmp_path / "ids.json"
            generate_matrix(output=out_a, ledger_path=ledger)
            snap_ledger_1 = ledger.read_text()
            generate_matrix(output=out_b, ledger_path=ledger)
            assert out_a.read_bytes() == out_b.read_bytes(), "matrix not idempotent"
            assert ledger.read_text() == snap_ledger_1, "ledger mutated on second run"

        This is the canonical D-02 contract: codegen-idempotence is what lets
        the matrix be regenerated in CI without producing a noisy diff.
        """
        from tools.audit_coverage_matrix import generate_matrix  # noqa: F401
        raise NotImplementedError(
            "Wave 2 — see VALIDATION.md row 11-COV-01-idempotence "
            "(COV-01 / D-02: re-run produces byte-identical output and "
            "unchanged ledger; recipe in RESEARCH.md lines 242-255)"
        )

    # ------------------------------------------------------------------
    # Wave 3 — defect findings + ledger semantics (COV-02)
    # ------------------------------------------------------------------

    def test_hazard_cluster_42_rows(self, tmp_path):
        """COV-02 / D-12 / D-15: §4 reports the 42-row pinout/algorithm HAZARD.

        Wave 3 impl: assert §4 contains a HAZARD-tier finding whose signature
        is (("DIP28_28C64", "DIP28_28C256"), 0x07, "UV-EPROM") and whose
        `affected_chips == 42`. This is the post-re-derivation cluster — the
        WARNING-5 override predicate at build_db.py:397-423 is structurally
        unreachable for these rows because `_etype` is re-derived to
        "UV-EPROM" at build_db.py:483-484 AFTER the override fires
        (PATTERNS.md §"_etype re-derivation pattern").
        """
        from tools.audit_coverage_matrix import generate_matrix  # noqa: F401
        raise NotImplementedError(
            "Wave 3 — see VALIDATION.md row 11-COV-02-hazard-cluster "
            "(COV-02 / D-12 / D-15: §4 HAZARD finding with affected_chips=42 "
            "covering (DIP28_28C64, DIP28_28C256, 0x07, UV-EPROM) cluster)"
        )

    def test_ledger_idempotent(self, tmp_path):
        """COV-02 / D-13: ledger is byte-identical on a second run.

        Wave 3 impl: seed a ledger via a first generate_matrix() call, snapshot
        the ledger bytes, run generate_matrix() again with the same ledger
        path, assert the second-run ledger text equals the first. Defect-IDs
        must NOT be re-minted on every run — that would defeat the stable
        cross-document identity contract from CONTEXT.md D-13.
        """
        from tools.audit_coverage_matrix import generate_matrix  # noqa: F401
        raise NotImplementedError(
            "Wave 3 — see VALIDATION.md row 11-COV-02-ledger-idempotent "
            "(COV-02 / D-13: second-run ledger text byte-identical to first)"
        )

    def test_ledger_id_reuse(self, tmp_path):
        """COV-02 / D-13: existing DEFECT-COV-NN reused for same finding-hash.

        Wave 3 impl: write a ledger with a single pre-minted DEFECT-COV-07
        entry whose `finding_hash` matches one of the live findings, run
        generate_matrix() pointed at that ledger, assert the same NN (07) is
        reused for the matching hash — no new ID minted, no collision. This
        proves the hash → ID mapping is the stable contract from PATTERNS.md
        Pattern C (Stable defect-ID hash composition).
        """
        from tools.audit_coverage_matrix import generate_matrix  # noqa: F401
        raise NotImplementedError(
            "Wave 3 — see VALIDATION.md row 11-COV-02-ledger-reuse "
            "(COV-02 / D-13: seeded DEFECT-COV-NN reused for matching "
            "finding-hash on subsequent runs)"
        )

    # ------------------------------------------------------------------
    # Wave 1 — summary stats + CLI exit codes (D-03, D-07)
    # ------------------------------------------------------------------

    def test_summary_stats(self, tmp_path):
        """COV-01 / D-07: §1 reports the reconciled live-DB counts.

        Asserts §1 (Summary Statistics) carries the live-DB numbers
        post-WARNING-5 override (DIP28_2764 + 0x07 + Flash/EEPROM → 0x0D)
        and post-fm1608 override (type=4 ∧ proto_id ∈ {0x07,0x08,0x0B}
        → 0x28), plus upstream `infoic.xml` drift between v1.0 close and
        v1.3 start:

            total_chips == 734
            algo_0x07   == 212
            algo_0x08   == 127
            in_scope    == 339

        PATTERNS.md §"D-07 Planning-Doc Reconciliation" enumerates the
        planning-doc rows that quote the stale 743 / 341 / 214 numbers
        and must be Edit-tool patched to match. The matrix's §2
        carries the reconciliation narrative.
        """
        from tools.audit_coverage_matrix import generate_matrix

        out = tmp_path / "m.md"
        ledger = tmp_path / "l.json"
        rc = generate_matrix(output=out, ledger_path=ledger)
        assert rc == 0, f"generate_matrix returned non-zero rc={rc}"

        body = out.read_text(encoding="utf-8")

        # Section anchors (D-05 fixed order — §1 + §2 land in Wave 1).
        assert "## §1: Summary Statistics" in body, "§1 header missing"
        assert "## §2: DB Count Reconciliation" in body, "§2 header missing"

        # Live counts must appear in §1 — these are the regression anchors.
        assert "734" in body, "total_chips=734 missing from matrix body"
        assert "339" in body, "in_scope=339 missing from matrix body"
        assert "212" in body, "algo_0x07=212 missing from matrix body"
        assert "127" in body, "algo_0x08=127 missing from matrix body"

    def test_exit_codes(self, tmp_path):
        """D-03: CLI exit-code surface — 0 on clean run.

        Drives the real CLI via subprocess (per VALIDATION.md
        "integration (subprocess)" classification) — mirrors
        check_dispatch.py:148-190 exit-code discipline.

        Wave 1: only clean-generate (rc=0) is reachable; ledger minting
        lands in Wave 3, after which `--check` against a mutated ledger
        returns 1 if a new DEFECT-COV-NN would be minted. See TODO below.
        """
        import subprocess
        import sys
        from pathlib import Path

        firestarter_app_dir = Path(__file__).resolve().parent.parent

        out = tmp_path / "m.md"
        ledger = tmp_path / "l.json"
        result = subprocess.run(
            [
                sys.executable,
                "tools/audit_coverage_matrix.py",
                "--output", str(out),
                "--ledger", str(ledger),
            ],
            cwd=str(firestarter_app_dir),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"clean-run returncode={result.returncode}; "
            f"stderr={result.stderr!r}; stdout={result.stdout!r}"
        )
        assert out.exists(), "matrix output not written"
        assert ledger.exists(), "ledger output not written"

        # --check on the same clean state should also exit 0 (no new findings).
        result_check = subprocess.run(
            [
                sys.executable,
                "tools/audit_coverage_matrix.py",
                "--output", str(out),
                "--ledger", str(ledger),
                "--check",
            ],
            cwd=str(firestarter_app_dir),
            capture_output=True,
            text=True,
        )
        assert result_check.returncode == 0, (
            f"--check on clean ledger returncode={result_check.returncode}; "
            f"stderr={result_check.stderr!r}"
        )

        # TODO: Wave 3 — extend this test to mutate the ledger (drop an
        # entry) and assert the next --check returns 1 because the
        # missing DEFECT-COV-NN would be minted on a real generate.

    # ------------------------------------------------------------------
    # Wave 4 — bench-coverage proof + golden-file regression
    # ------------------------------------------------------------------

    def test_bench_coverage_proof(self, tmp_path):
        """COV-01 / SC-03 / D-09 / D-10 / D-11: §5 per-axis coverage proof.

        Wave 4 impl: assert §5 contains exactly three per-axis tables
        (pinout-class, pulse-bucket, size-bucket — D-09/D-10/D-11 axes) and
        that every uncovered cell cross-references at least one §4
        defect-finding ID (DEFECT-COV-NN). The §5 receipt is what lets an
        operator state, in their own words, that the six BENCH chips
        represent the 339 in-scope rows on the axes that matter.
        """
        from tools.audit_coverage_matrix import generate_matrix  # noqa: F401
        raise NotImplementedError(
            "Wave 4 — see VALIDATION.md row 11-bench-coverage "
            "(COV-01 / SC-03 / D-09, D-10, D-11: §5 contains three per-axis "
            "tables; uncovered cells cross-reference §4 finding IDs)"
        )

    def test_golden_file_matches(self, tmp_path):
        """COV-01 / COV-02: end-to-end golden-file regression.

        Wave 4 impl: seed the ledger from `.planning/v1.3-defect-coverage-ids.json`,
        invoke generate_matrix() pointed at tmp_path, diff the produced
        matrix against `firestarter_app/tests/golden/v1.3-COVERAGE-MATRIX.md`.
        The golden file is created in Wave 4 by copying the operator-approved
        matrix verbatim — it pins the entire output surface so any future
        accidental change to the renderer trips a clear diff.

        Today: this test fails because the golden fixture does not yet exist
        — Wave 4 creates `firestarter_app/tests/golden/v1.3-COVERAGE-MATRIX.md`
        alongside the operator-approved matrix commit.
        """
        from tools.audit_coverage_matrix import generate_matrix  # noqa: F401
        raise NotImplementedError(
            "Wave 4 — see VALIDATION.md row 11-golden-file "
            "(COV-01 / COV-02: end-to-end diff against "
            "firestarter_app/tests/golden/v1.3-COVERAGE-MATRIX.md)"
        )
