"""Tests for check_diagnostic_report_claims.py (CLOSE-03, v1.30 Phase 137
plan 137-02).

This is the mandatory anti-hollow pairing for the CLOSE-03 gate, mirroring
`tests/test_check_no_community_support_status_write.py`'s subprocess-based
pattern: a checker tool with no negative-fixture test is exactly the v1.12
hollow-GATE-03 failure mode. Every planted-violation test below injects a
REAL subprocess-level violation via the `FIRESTARTER_DIAGREPORT_SRC`
env-override -- never an in-process synthetic -- so a passing test suite
proves the checker itself (not the test) fails the build on a real
violation.

Coverage:
  1. Clean-pass baseline: the checker exits 0 on the current, real
     `diagnostic_report.py` source (the control leg proving the gate is not
     accidentally always-red).
  2. Planted violation via FIRESTARTER_DIAGREPORT_SRC flips the checker to a
     non-zero exit with a `FAIL:` summary naming the
     `dev-test-proves-unqualified` label.
  3. Fail-closed on a missing/nonexistent scan target: the gate must NOT
     vacuously pass when a target is silently absent.
  4. Fail-closed on an unparsable (syntax-error) scan target: the gate must
     NOT silently skip a file it cannot parse.
"""

import os
import subprocess
import sys
from pathlib import Path

# Absolute path to the firestarter_app directory (cwd-independent), mirrors
# tests/test_check_no_community_support_status_write.py:45.
_FA_DIR = Path(__file__).parent.parent


def _run_checker(
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_overrides or {})}
    return subprocess.run(
        [sys.executable, "tools/check_diagnostic_report_claims.py"],
        cwd=str(_FA_DIR),
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Test 1: clean-pass baseline (also the no-false-positive proof)
# ---------------------------------------------------------------------------


def test_scanner_exits_zero_on_real_diagnostic_report() -> None:
    """python tools/check_diagnostic_report_claims.py must exit 0 on the
    real, clean `diagnostic_report.py` source -- proving the gate is not
    accidentally always-red."""
    result = _run_checker()
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on clean source.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout, (
        f"Expected 'PASS:' in output but got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Test 2: planted violation (anti-hollow contract)
# ---------------------------------------------------------------------------


def test_planted_violation_flips_checker_to_failure() -> None:
    """FIRESTARTER_DIAGREPORT_SRC pointed at the committed planted-violation
    fixture MUST flip the gate to a non-zero exit, naming the
    `dev-test-proves-unqualified` label in its FAIL: summary."""
    fixture = _FA_DIR / "tests" / "fixtures" / "planted_diagnostic_report_claim.py"
    result = _run_checker({"FIRESTARTER_DIAGREPORT_SRC": str(fixture)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted diagnostic_report.py-shaped claim "
        f"violation.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "dev-test-proves-unqualified" in result.stdout


# ---------------------------------------------------------------------------
# Test 3: fail-closed on a missing/nonexistent scan target
# ---------------------------------------------------------------------------


def test_fail_closed_on_nonexistent_target(tmp_path: Path) -> None:
    """Pointing the scan target at a nonexistent path MUST fail closed (exit
    non-zero), never vacuously pass with a target silently skipped."""
    missing = tmp_path / "does_not_exist.py"
    result = _run_checker({"FIRESTARTER_DIAGREPORT_SRC": str(missing)})
    assert result.returncode != 0, (
        f"checker exited 0 with a missing scan target.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "not found" in result.stdout


# ---------------------------------------------------------------------------
# Test 4: fail-closed on an unparsable (syntax-error) scan target
# ---------------------------------------------------------------------------


def test_fail_closed_on_unparsable_source() -> None:
    """FIRESTARTER_DIAGREPORT_SRC pointed at a deliberately-unparsable
    fixture (a genuine Python SyntaxError) MUST fail closed, never silently
    skip a file it cannot parse."""
    fixture = _FA_DIR / "tests" / "fixtures" / "planted_unparsable.py"
    result = _run_checker({"FIRESTARTER_DIAGREPORT_SRC": str(fixture)})
    assert result.returncode != 0, (
        f"checker exited 0 on an unparsable scan target.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "could not parse" in result.stdout
