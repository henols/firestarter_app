"""
Tests for check_no_community_support_status_write.py (DISP-01, Phase 114
Plan 03).

This is the mandatory anti-hollow pairing for the DISP-01 gate (D-05),
mirroring `tests/test_check_devtest_orchestrator.py`'s SAFE-03 pattern: a
checker tool with no negative-fixture test is exactly the v1.12
hollow-GATE-03 failure mode (a declared-empty detector that could never
fail because nothing concrete was asserted). Every planted-violation test
below injects a REAL subprocess-level `support_status` write via the
`FIRESTARTER_DISP01_REPORT` / `FIRESTARTER_DISP01_PARSER` env-overrides --
never an in-process synthetic -- so a passing test suite proves the checker
itself (not the test) fails the build on a real violation.

Coverage:
  1. Clean-pass baseline: the checker exits 0 on the current, real
     `diagnostic_report.py` + `parse_devtest_issue.py` source (post-114-01/
     114-02) -- also proves no false positive on `build_db_diff`'s
     `get_eprom_config` read or the `current_support_status` near-name
     field/key (Pitfall 1/T-114-08).
  2. Planted `support_status` write via FIRESTARTER_DISP01_REPORT flips the
     checker to a non-zero exit with a FAIL: summary (T-114-06).
  3. Planted `support_status` write via FIRESTARTER_DISP01_PARSER flips the
     checker to a non-zero exit with a FAIL: summary (T-114-06).
  4. Fail-closed on a missing/nonexistent scan target: the gate must NOT
     vacuously pass when a target is silently absent (T-114-07, the v1.12
     hollow-GATE-03 lesson).
  5. Env-override seam sanity (report leg): a CLEAN fixture injected via
     FIRESTARTER_DISP01_REPORT still passes -- isolates test 2's failure as
     genuinely caused by the planted violation, not the injection seam.
  6. Env-override seam sanity (parser leg): same isolation for
     FIRESTARTER_DISP01_PARSER / test 3.
  7. PASS-line-names-both-scanned-files (anti-skip): the clean-baseline
     PASS: line names BOTH `diagnostic_report.py` and
     `parse_devtest_issue.py` -- proves neither leg was silently skipped.
"""

import os
import subprocess
import sys
from pathlib import Path

# Absolute path to the firestarter_app directory (cwd-independent), mirrors
# tests/test_check_devtest_orchestrator.py:46.
_FA_DIR = Path(__file__).parent.parent


def _run_checker(
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_overrides or {})}
    return subprocess.run(
        [sys.executable, "tools/check_no_community_support_status_write.py"],
        cwd=str(_FA_DIR),
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Test 1: clean-pass baseline (also the no-false-positive proof)
# ---------------------------------------------------------------------------


def test_checker_exits_zero_on_clean_source() -> None:
    """python tools/check_no_community_support_status_write.py must exit 0
    on the real, clean report/parse path source: `build_db_diff` and
    `extract_db_diff` only READ `support_status` via `get_eprom_config` /
    `.get(...)`, and the `current_support_status` dataclass field / dict key
    is a distinct identifier that must NOT be mistaken for a
    `support_status` write (Pitfall 1, T-114-08).
    """
    result = _run_checker()
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on clean source.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout, (
        f"Expected 'PASS:' in output but got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Test 2: planted violation in the report leg (anti-hollow contract, D-05)
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_report_violation(tmp_path: Path) -> None:
    """A real subprocess-level `support_status` write in the report-shaped
    fixture MUST fail the gate (T-114-06).

    This is the anti-hollow proof: the fixture is written to disk and the
    checker is pointed at it via the FIRESTARTER_DISP01_REPORT env-override
    (mirrors check_devtest_orchestrator.py's FIRESTARTER_DEVTEST_SRC seam) --
    a real subprocess-level violation, not an in-process synthetic.
    """
    bad = tmp_path / "planted_report_write.py"
    bad.write_text(
        "def build_db_diff(name, db, results):\n"
        "    chip = {}\n"
        "    chip['support_status'] = 'community-reported'\n"
        "    return chip\n"
    )
    result = _run_checker({"FIRESTARTER_DISP01_REPORT": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted report-leg support_status write.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "support_status" in result.stdout


# ---------------------------------------------------------------------------
# Test 3: planted violation in the parser leg
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_parser_violation(tmp_path: Path) -> None:
    """A real subprocess-level `support_status` write in the parser-shaped
    fixture MUST fail the gate (T-114-06), injected via
    FIRESTARTER_DISP01_PARSER."""
    bad = tmp_path / "planted_parser_write.py"
    bad.write_text(
        "def extract_db_diff(report_obj):\n"
        "    diff = {}\n"
        "    diff['support_status'] = 'community-confirmed'\n"
        "    return diff\n"
    )
    result = _run_checker({"FIRESTARTER_DISP01_PARSER": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted parser-leg support_status write.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "support_status" in result.stdout


# ---------------------------------------------------------------------------
# Test 4: fail-closed on a missing/nonexistent scan target
# ---------------------------------------------------------------------------


def test_checker_fails_closed_on_missing_target(tmp_path: Path) -> None:
    """Pointing a scan target at a nonexistent path MUST fail closed (exit
    non-zero), never vacuously pass with a target silently skipped
    (T-114-07, the v1.12 hollow-GATE-03 lesson)."""
    missing = tmp_path / "does_not_exist.py"
    result = _run_checker({"FIRESTARTER_DISP01_REPORT": str(missing)})
    assert result.returncode != 0, (
        f"checker exited 0 with a missing scan target.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout


# ---------------------------------------------------------------------------
# Test 5: env-override seam sanity -- report leg
# ---------------------------------------------------------------------------


def test_env_override_report_points_at_clean_fixture_still_passes(
    tmp_path: Path,
) -> None:
    """A CLEAN report-shaped fixture injected via FIRESTARTER_DISP01_REPORT
    must still pass -- proves the seam is a faithful re-target (not itself
    the source of test 2's non-zero exit), isolating the planted violation
    as the true cause."""
    clean = tmp_path / "planted_report_clean.py"
    clean.write_text(
        "def build_db_diff(name, db, results):\n"
        "    raw_config, _manufacturer = db.get_eprom_config(name)\n"
        "    current = (raw_config or {}).get('support_status', 'supported')\n"
        "    return current\n"
    )
    result = _run_checker({"FIRESTARTER_DISP01_REPORT": str(clean)})
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on a clean report env-override "
        f"fixture.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout


# ---------------------------------------------------------------------------
# Test 6: env-override seam sanity -- parser leg
# ---------------------------------------------------------------------------


def test_env_override_parser_points_at_clean_fixture_still_passes(
    tmp_path: Path,
) -> None:
    """A CLEAN parser-shaped fixture injected via FIRESTARTER_DISP01_PARSER
    must still pass -- proves the seam is a faithful re-target (not itself
    the source of test 3's non-zero exit), isolating the planted violation
    as the true cause."""
    clean = tmp_path / "planted_parser_clean.py"
    clean.write_text(
        "def extract_db_diff(report_obj):\n"
        "    db_diff = report_obj.get('db_diff') or {}\n"
        "    return {\n"
        "        'current_support_status': db_diff.get(\n"
        "            'current_support_status', 'supported'\n"
        "        )\n"
        "    }\n"
    )
    result = _run_checker({"FIRESTARTER_DISP01_PARSER": str(clean)})
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on a clean parser env-override "
        f"fixture.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout


# ---------------------------------------------------------------------------
# Test 7: PASS-line names both scanned files (anti-skip, T-114-07)
# ---------------------------------------------------------------------------


def test_checker_pass_line_names_both_scanned_files() -> None:
    """The clean-baseline PASS: line must name BOTH real scan targets --
    proves neither `diagnostic_report.py` nor `parse_devtest_issue.py` was
    silently skipped (anti-skip, the v1.12 hollow-GATE-03 lesson)."""
    result = _run_checker()
    assert result.returncode == 0
    assert "diagnostic_report.py" in result.stdout, (
        f"Expected the PASS: line to name diagnostic_report.py but got:\n"
        f"{result.stdout}"
    )
    assert "parse_devtest_issue.py" in result.stdout, (
        f"Expected the PASS: line to name parse_devtest_issue.py but got:\n"
        f"{result.stdout}"
    )
