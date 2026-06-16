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
        """COV-01 / D-06: §3 contains exactly 297 enumerated in-scope rows.

        Splits the body between `## §3:` and `## §4:` headers, counts
        data rows (pipe-prefixed, not the `| Manufacturer` header row and
        not the `|---` separator row). Asserts:

            total in §3 == 297
            algo-0x07 sub-table == 170
            algo-0x08 sub-table == 127

        Post-Phase 70 integration counts: 42 chips that were previously on
        algo-0x07 (DIP28_28C64 + DIP28_28C256 pinouts) have been correctly
        reclassified to algo-0x0D via the WARNING-5 override in build_db.py
        (DIP28_2764/28C256 + 0x07 + EEPROM type → 0x0D). This is the
        correct post-WARNING-5 / post-Phase-70 DB histogram.
        """
        from tools.audit_coverage_matrix import generate_matrix

        out = tmp_path / "m.md"
        ledger = tmp_path / "l.json"
        rc = generate_matrix(output=out, ledger_path=ledger)
        assert rc == 0, f"generate_matrix returned non-zero rc={rc}"

        body = out.read_text(encoding="utf-8")

        # §3 body slice: from `## §3:` (inclusive) to `## §4:` (exclusive).
        s3_start = body.index("## §3:")
        s4_start = body.index("## §4:")
        s3_body = body[s3_start:s4_start]

        def _data_rows(text):
            return [
                line
                for line in text.split("\n")
                if line.startswith("| ")
                and not line.startswith("| Manufacturer")
                and not line.startswith("|---")
            ]

        all_rows = _data_rows(s3_body)
        assert len(all_rows) == 297, (
            f"§3 enumerated row count: expected 297, got {len(all_rows)}"
        )

        # Per-sub-table breakdown — 170 algo-0x07, 127 algo-0x08.
        algo7_start = s3_body.index("### algo-0x07")
        algo8_start = s3_body.index("### algo-0x08")
        algo7_body = s3_body[algo7_start:algo8_start]
        algo8_body = s3_body[algo8_start:]

        algo7_rows = _data_rows(algo7_body)
        algo8_rows = _data_rows(algo8_body)
        assert len(algo7_rows) == 170, (
            f"algo-0x07 sub-table row count: expected 170, got {len(algo7_rows)}"
        )
        assert len(algo8_rows) == 127, (
            f"algo-0x08 sub-table row count: expected 127, got {len(algo8_rows)}"
        )

    def test_enumeration_sort(self, tmp_path):
        """COV-01 / D-06: §3 rows sorted by

            (algorithm asc, pinout asc, size_bytes asc, manufacturer asc,
             first_alias asc)

        Parses each per-algorithm sub-table, extracts the (pinout,
        size_bytes, manufacturer, first_alias) tuple from each row, and
        asserts non-decreasing order pair-wise. Algorithm is implicit per
        sub-table so it's omitted from the comparison key.

        This invariant is load-bearing for byte-identical re-runs (Pattern B
        codegen-idempotence guarantee).
        """
        from tools.audit_coverage_matrix import generate_matrix

        out = tmp_path / "m.md"
        ledger = tmp_path / "l.json"
        rc = generate_matrix(output=out, ledger_path=ledger)
        assert rc == 0

        body = out.read_text(encoding="utf-8")
        s3_body = body[body.index("## §3:") : body.index("## §4:")]
        algo7_body = s3_body[
            s3_body.index("### algo-0x07") : s3_body.index("### algo-0x08")
        ]
        algo8_body = s3_body[s3_body.index("### algo-0x08") :]

        def _parse_rows(text):
            """Extract (pinout, size_bytes, manufacturer, first_alias) per row."""
            keys = []
            for line in text.split("\n"):
                if not line.startswith("| "):
                    continue
                if line.startswith("| Manufacturer") or line.startswith("|---"):
                    continue
                # Strip leading/trailing `|` then split.
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                # Columns per D-06: Manufacturer, Part Number(s), Pin Count,
                # Size (bytes), Pulse Duration, Chip ID Check, Chip ID Value,
                # Pinout, Electrical Type
                mfg = cells[0]
                part_first_alias = cells[1].split(",")[0].strip()
                size_bytes = int(cells[3])
                pinout = cells[7]
                keys.append((pinout, size_bytes, mfg, part_first_alias))
            return keys

        for sub_name, sub_text in (
            ("algo-0x07", algo7_body),
            ("algo-0x08", algo8_body),
        ):
            keys = _parse_rows(sub_text)
            assert keys, f"{sub_name}: no rows parsed"
            for i in range(1, len(keys)):
                assert keys[i - 1] <= keys[i], (
                    f"{sub_name} sort violation at row {i}: "
                    f"{keys[i - 1]!r} > {keys[i]!r}"
                )

    def test_idempotence(self, tmp_path):
        """COV-01 / D-02: byte-identical matrix + unchanged ledger on re-run.

        Verbatim shape from RESEARCH.md §"Code Examples" lines 242-255: two
        output paths share a single ledger; the second run must produce
        byte-identical matrix output AND a byte-identical ledger.

        Wave 2 has no defect-ID minting (Wave 3 adds it), so the ledger
        stays at `{}`/empty-blob — the test still verifies the no-mutation
        property, which is the canonical D-02 contract.
        """
        from tools.audit_coverage_matrix import generate_matrix

        out_a = tmp_path / "a.md"
        out_b = tmp_path / "b.md"
        ledger = tmp_path / "ids.json"

        # First run mints anything that needs minting; capture ledger bytes.
        rc1 = generate_matrix(output=out_a, ledger_path=ledger)
        assert rc1 == 0
        snap_ledger_1 = ledger.read_bytes()

        # Second run — must be byte-identical AND must not mint new IDs.
        rc2 = generate_matrix(output=out_b, ledger_path=ledger)
        assert rc2 == 0

        assert out_a.read_bytes() == out_b.read_bytes(), "matrix not idempotent"
        assert ledger.read_bytes() == snap_ledger_1, "ledger mutated on second run"

    # ------------------------------------------------------------------
    # Wave 3 — defect findings + ledger semantics (COV-02)
    # ------------------------------------------------------------------

    def test_hazard_cluster_42_rows(self, tmp_path):
        """COV-02 / D-12 / D-15: §4 no longer reports the DIP28_28C64 HAZARD.

        Post-Phase 70 integration: the 42-chip cluster that was previously
        HAZARD-flagged (DIP28_28C64 + DIP28_28C256 on algo 0x07) has been
        resolved. Those chips are now correctly classified as algo 0x0D
        (the WARNING-5 EEPROM-hazard override in build_db.py fires for them
        because their _etype is Flash/EEPROM before re-derivation). The
        HAZARD predicate (algo==0x07 AND pinout in {DIP28_28C64, DIP28_28C256})
        therefore returns 0 findings — the hazard is fixed, not masked.

        Asserts:
        - §4 still has CORRECTNESS findings (§4 section is populated)
        - §4 does NOT contain "DIP28_28C64" in a HAZARD-tier finding
        - HAZARD count in §1 severity-tier summary is 0
        """
        from tools.audit_coverage_matrix import generate_matrix

        out = tmp_path / "m.md"
        ledger = tmp_path / "l.json"
        rc = generate_matrix(output=out, ledger_path=ledger)
        assert rc == 0, f"generate_matrix returned non-zero rc={rc}"

        body = out.read_text(encoding="utf-8")

        # §1 severity-tier finding counts — HAZARD must be 0 post-integration.
        assert "- HAZARD: 0" in body, (
            "HAZARD count in §1 should be 0 post-Phase-70 integration; "
            "the DIP28_28C64/DIP28_28C256 cluster was resolved by the WARNING-5 "
            "override correctly classifying those chips as algo 0x0D"
        )

        # §4 body slice: from `## §4:` (inclusive) to `## §5:` (exclusive).
        s4_start = body.index("## §4:")
        s5_start = body.index("## §5:")
        s4_body = body[s4_start:s5_start]

        # §4 must still have CORRECTNESS findings (not empty).
        assert "CORRECTNESS" in s4_body, "CORRECTNESS tier missing from §4"

        # The DIP28_28C64 HAZARD must NOT appear as a HAZARD-tier finding.
        # (It may appear in DEFECT-COV-01 RESOLVED baseline prose, but not as
        # an active HAZARD finding header.)
        assert "HAZARD" not in s4_body or "RESOLVED" in s4_body, (
            "DIP28_28C64 HAZARD-tier finding should not appear in §4 "
            "post-Phase-70 integration (HAZARD was resolved)"
        )

    def test_ledger_idempotent(self, tmp_path):
        """COV-02 / D-13: ledger is byte-identical on a second run.

        Seeds a ledger via a first generate_matrix() call, snapshots the
        ledger bytes, runs generate_matrix() again with the same ledger
        path, then asserts the second-run ledger text equals the first.
        Defect-IDs must NOT be re-minted on every run — that would defeat
        the stable cross-document identity contract from CONTEXT.md D-13.
        Also asserts the ledger is JSON with sorted keys (Pattern B).
        """
        import json

        from tools.audit_coverage_matrix import generate_matrix

        ledger_path = tmp_path / "l.json"
        out1 = tmp_path / "m1.md"
        out2 = tmp_path / "m2.md"

        rc1 = generate_matrix(output=out1, ledger_path=ledger_path)
        assert rc1 == 0
        snap_1 = ledger_path.read_text(encoding="utf-8")

        rc2 = generate_matrix(output=out2, ledger_path=ledger_path)
        assert rc2 == 0
        snap_2 = ledger_path.read_text(encoding="utf-8")

        assert snap_1 == snap_2, "ledger text mutated on second run (D-13 violation)"

        parsed = json.loads(snap_2)
        assert list(parsed.keys()) == sorted(parsed.keys()), (
            "ledger keys must be sorted for byte-identical idempotence"
        )

    def test_ledger_id_reuse(self, tmp_path):
        """COV-02 / D-13: existing DEFECT-COV-NN reused for same finding-hash.

        Two checks: (1) every hash → ID mapping minted on the first run is
        preserved on the second run (no extra keys, identical values);
        (2) a hand-seeded high-NN ID (DEFECT-COV-99) for a real finding hash
        is REUSED on subsequent runs, not overwritten by a freshly-minted
        ID. This proves the hash → ID mapping is the stable contract from
        PATTERNS.md Pattern C (Stable defect-ID hash composition).
        """
        import json

        from tools.audit_coverage_matrix import (
            finding_hash,  # noqa: F401
            generate_matrix,
            iter_in_scope_rows,
        )

        ledger_path = tmp_path / "l.json"
        out1 = tmp_path / "m1.md"
        out2 = tmp_path / "m2.md"

        # Step 1: mint everything from a clean state.
        rc1 = generate_matrix(output=out1, ledger_path=ledger_path)
        assert rc1 == 0
        parsed_1 = json.loads(ledger_path.read_text(encoding="utf-8"))

        # Step 2: re-run — assert every hash → ID survives and no new keys.
        rc2 = generate_matrix(output=out2, ledger_path=ledger_path)
        assert rc2 == 0
        parsed_2 = json.loads(ledger_path.read_text(encoding="utf-8"))

        assert set(parsed_2) == set(parsed_1), "extra hash keys minted on re-run"
        for h, defect_id in parsed_1.items():
            assert parsed_2[h] == defect_id, (
                f"hash {h} re-minted: {defect_id} -> {parsed_2[h]}"
            )

        # Step 3: pre-seed scenario — seed a CORRECTNESS finding hash with
        # DEFECT-COV-99 in a fresh ledger; assert the next run reuses NN=99.
        # Post-Phase 70: the HAZARD cluster (DIP28_28C64 + DIP28_28C256 on
        # algo 0x07) is resolved — detect_hazard() returns 0 findings. Use the
        # first CORRECTNESS finding instead (always present — 18 findings).
        # Compute the hash by inspecting the real detector against the live DB
        # (Pitfall 5 — derive expected hashes from the tool surface, not from
        # hard-coded literals).
        import json as _json

        from tools.audit_coverage_matrix import detect_correctness

        with open(
            __import__("tools.audit_coverage_matrix", fromlist=["DB_FILE"]).DB_FILE,
            encoding="utf-8",
        ) as f:
            db_raw = _json.load(f)
        live_rows = list(iter_in_scope_rows(db_raw))
        correctness_findings = list(detect_correctness(live_rows))
        assert correctness_findings, "expected at least one CORRECTNESS finding"
        seed_hash = correctness_findings[0]["hash"]
        # Sanity: confirm the freshly minted ledger contains that hash.
        assert seed_hash in parsed_1

        seeded_ledger_path = tmp_path / "seeded.json"
        seeded_ledger_path.write_text(
            _json.dumps({seed_hash: "DEFECT-COV-99"}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        out3 = tmp_path / "m3.md"
        rc3 = generate_matrix(output=out3, ledger_path=seeded_ledger_path)
        assert rc3 == 0
        seeded_parsed = _json.loads(seeded_ledger_path.read_text(encoding="utf-8"))
        assert seeded_parsed[seed_hash] == "DEFECT-COV-99", (
            "pre-seeded DEFECT-COV-99 must be reused, not overwritten"
        )

    # ------------------------------------------------------------------
    # Wave 1 — summary stats + CLI exit codes (D-03, D-07)
    # ------------------------------------------------------------------

    def test_summary_stats(self, tmp_path):
        """COV-01 / D-07: §1 reports the reconciled live-DB counts.

        Asserts §1 (Summary Statistics) carries the live-DB numbers
        post-Phase-70 integration (WARNING-5 override now correctly moves
        DIP28_28C64 + DIP28_28C256 chips from 0x07 to 0x0D):

            total_chips == 744
            algo_0x07   == 170
            algo_0x08   == 127
            in_scope    == 297

        Post-Phase 70: the 42 chips on DIP28_28C64/DIP28_28C256 pinouts
        (Flash/EEPROM type) correctly receive algo 0x0D via the WARNING-5
        override in build_db.py. Previous counts (212/339) were incorrect
        because those chips should not have been on algo 0x07.
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
        assert "744" in body, "total_chips=744 missing from matrix body"
        assert "297" in body, "in_scope=297 missing from matrix body"
        assert "170" in body, "algo_0x07=170 missing from matrix body"
        assert "127" in body, "algo_0x08=127 missing from matrix body"

    def test_exit_codes(self, tmp_path):
        """D-03: CLI exit-code surface — 0 on clean run, 1 on --check drift.

        Drives the real CLI via subprocess (per VALIDATION.md
        "integration (subprocess)" classification) — mirrors
        check_dispatch.py:148-190 exit-code discipline.

        Three steps:
          1. plain mode → returncode 0
          2. --check against an empty ledger → returncode 1 (every detected
             finding plus the DEFECT-COV-00 baseline would be a new mint)
          3. --check against the full-from-step-1 ledger → returncode 0
             (no drift; all hashes already minted)
        """
        import subprocess
        import sys
        from pathlib import Path

        firestarter_app_dir = Path(__file__).resolve().parent.parent

        out = tmp_path / "m.md"
        ledger = tmp_path / "l.json"

        # Step 1: clean generate populates the ledger.
        result = subprocess.run(
            [
                sys.executable,
                "tools/audit_coverage_matrix.py",
                "--output",
                str(out),
                "--ledger",
                str(ledger),
            ],
            cwd=str(firestarter_app_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"clean-run returncode={result.returncode}; "
            f"stderr={result.stderr!r}; stdout={result.stdout!r}"
        )
        assert out.exists(), "matrix output not written"
        assert ledger.exists(), "ledger output not written"

        # Step 2: --check against an empty ledger MUST exit 1 (drift gate).
        empty_ledger = tmp_path / "empty.json"
        empty_ledger.write_text("{}\n", encoding="utf-8")
        scratch_out = tmp_path / "scratch.md"
        result_empty = subprocess.run(
            [
                sys.executable,
                "tools/audit_coverage_matrix.py",
                "--output",
                str(scratch_out),
                "--ledger",
                str(empty_ledger),
                "--check",
            ],
            cwd=str(firestarter_app_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result_empty.returncode == 1, (
            f"--check on empty ledger expected rc=1, got {result_empty.returncode}; "
            f"stderr={result_empty.stderr!r}"
        )

        # Step 3: --check against the full ledger from Step 1 MUST exit 0.
        result_full = subprocess.run(
            [
                sys.executable,
                "tools/audit_coverage_matrix.py",
                "--output",
                str(scratch_out),
                "--ledger",
                str(ledger),
                "--check",
            ],
            cwd=str(firestarter_app_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result_full.returncode == 0, (
            f"--check on full ledger expected rc=0, got {result_full.returncode}; "
            f"stderr={result_full.stderr!r}"
        )

    # ------------------------------------------------------------------
    # Wave 4 — bench-coverage proof + golden-file regression
    # ------------------------------------------------------------------

    def test_bench_coverage_proof(self, tmp_path):
        """COV-01 / SC-03 / D-09 / D-10 / D-11: §5 per-axis coverage proof.

        Asserts §5 contains the three per-axis tables (pinout-class, pulse-
        duration bucket, size bucket — D-09 axes), the Known Gaps subsection
        (D-10), every BENCH-01..06 ID, and the milestone-claim closing prose
        (the matrix-is-the-receipt framing from CONTEXT.md <specifics>).
        D-11 is honored by construction in emit_bench_coverage — BENCH-05 /
        BENCH-06 stay candidate; no swap proposals.
        """
        from tools.audit_coverage_matrix import generate_matrix

        out = tmp_path / "m.md"
        ledger = tmp_path / "l.json"
        rc = generate_matrix(output=out, ledger_path=ledger)
        assert rc == 0, f"generate_matrix returned non-zero rc={rc}"

        body = out.read_text(encoding="utf-8")

        # §5 body slice: from `## §5:` (inclusive) to end of file.
        s5_start = body.index("## §5:")
        s5_body = body[s5_start:]

        # Three per-axis table subsections (D-09).
        assert "### Pinout-Class Coverage" in s5_body, (
            "Pinout-Class Coverage subsection missing from §5"
        )
        assert "### Pulse-Duration Bucket Coverage" in s5_body, (
            "Pulse-Duration Bucket Coverage subsection missing from §5"
        )
        assert "### Size Bucket Coverage" in s5_body, (
            "Size Bucket Coverage subsection missing from §5"
        )

        # Known Gaps subsection (D-10 — deliberate gaps live here).
        assert "### Known Gaps" in s5_body, "Known Gaps subsection missing from §5"

        # All six BENCH IDs (D-09 / D-11 — candidate names per REQUIREMENTS.md).
        for bench_id in (
            "BENCH-01",
            "BENCH-02",
            "BENCH-03",
            "BENCH-04",
            "BENCH-05",
            "BENCH-06",
        ):
            assert bench_id in s5_body, (
                f"{bench_id} missing from §5 BENCH coverage proof"
            )

        # Milestone-claim closing prose (CONTEXT.md <specifics> the receipt).
        # Match any of the load-bearing phrases — the prose may evolve slightly
        # across wave authoring, but at least one must always be present.
        prose_markers = ("receipt", "N=339", "represent", "generaliz")
        assert any(m in s5_body for m in prose_markers), (
            "milestone-claim closing prose missing from §5; expected one of: "
            f"{prose_markers!r}"
        )

    def test_golden_file_matches(self, tmp_path):
        """COV-01 / COV-02: end-to-end golden-file regression.

        Seed a tmp ledger from the committed `.planning/v1.3-defect-coverage-ids.json`
        so that the freshly generated matrix is byte-identical to the golden
        snapshot in `firestarter_app/tests/golden/v1.3-COVERAGE-MATRIX.md`.
        Any future accidental change to the renderer trips a clear diff;
        any legitimate change to the matrix output requires regenerating the
        golden file alongside the matrix in one commit.

        Paths are anchored from `__file__` (resolves to the test file inside
        firestarter_app/tests/), then walked up two levels to the firestarter_app
        repo root + one more level to the meta-repo root that holds .planning/.
        """
        import json as _json
        from pathlib import Path

        from tools.audit_coverage_matrix import generate_matrix

        # Walk from this test file up to the meta-repo root:
        # firestarter_app/tests/test_audit_coverage_matrix.py
        #   .parents[0] = firestarter_app/tests
        #   .parents[1] = firestarter_app
        #   .parents[2] = meta-repo root
        meta_root = Path(__file__).resolve().parents[2]
        committed_ledger = meta_root / ".planning" / "v1.3-defect-coverage-ids.json"
        golden_file = (
            Path(__file__).resolve().parents[1]
            / "tests"
            / "golden"
            / "v1.3-COVERAGE-MATRIX.md"
        )

        # CI guard: when firestarter_app is checked out standalone (e.g. GitHub
        # Actions cloning only this sub-repo, not the meta-repo above), the
        # committed ledger from .planning/ doesn't exist. This regression
        # guard is only meaningful when run from inside the meta-repo work
        # tree — skip cleanly otherwise so standalone CI doesn't trip on it.
        if not committed_ledger.exists():
            pytest.skip(
                f"meta-repo ledger not available at {committed_ledger}; "
                "test only runs from inside meta-repo work tree (skipped in standalone CI)"  # noqa: E501
            )
        assert golden_file.exists(), (
            f"golden fixture missing at {golden_file}; "
            "Wave 4 Task 2 must snapshot the matrix to this path"
        )

        # Seed tmp ledger byte-identically (avoids re-encoding).
        tmp_ledger = tmp_path / "l.json"
        tmp_ledger.write_bytes(committed_ledger.read_bytes())

        # Sanity: the seed must parse as JSON dict so the load_ledger surface
        # can consume it (Pitfall 4 cold-start guard expects dict or {}).
        assert isinstance(_json.loads(tmp_ledger.read_text(encoding="utf-8")), dict), (
            "seeded ledger must be a JSON dict"
        )

        out = tmp_path / "m.md"
        rc = generate_matrix(output=out, ledger_path=tmp_ledger)
        assert rc == 0, f"generate_matrix returned non-zero rc={rc}"

        # Byte-identity assertion — the load-bearing regression gate.
        produced = out.read_bytes()
        golden = golden_file.read_bytes()
        assert produced == golden, (
            "regenerated matrix drifted from golden fixture; "
            f"produced {len(produced)} bytes vs golden {len(golden)} bytes; "
            "if this is a legitimate change, regenerate the golden file "
            "alongside the matrix commit"
        )
