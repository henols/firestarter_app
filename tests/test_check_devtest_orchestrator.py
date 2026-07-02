"""
Tests for check_devtest_orchestrator.py (SAFE-03, Phase 109 D-02/D-03).

This is the mandatory anti-hollow pairing for the SAFE-03 gate: a checker
tool with no negative-fixture test is exactly the failure mode this project
incurred with v1.12's GATE-03 (a declared-empty detector that could never
fail because nothing concrete was asserted). Every planted-violation test
below injects a REAL subprocess-level violation via the
`FIRESTARTER_DEVTEST_SRC` env-override -- never an in-process synthetic --
so a passing test suite proves the checker itself (not the test) fails the
build on a real violation.

Coverage:
  1. Clean-pass baseline: the checker exits 0 on the current, real
     chip_test.py (post-109-01/109-02 source).
  2. Planted VPP-set violation: a temp fixture calling `op.set_vpp(...)`
     flips the checker to a non-zero exit with a FAIL: summary.
  3. Planted raw-wire-dict violation: a temp fixture returning a dict literal
     carrying >=2 wire-protocol keys flips the checker non-zero.
  4. Planted --force violation: a temp fixture passing `force=True` flips
     the checker non-zero.
  5. Planted "--force" string-literal violation: a temp fixture containing
     the bare CLI flag string flips the checker non-zero.
"""

import os
import subprocess
import sys
from pathlib import Path

# Absolute path to the firestarter_app directory (cwd-independent), mirrors
# tests/test_check_dispatch_invariants.py:22.
_FA_DIR = Path(__file__).parent.parent


def _run_checker(
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_overrides or {})}
    return subprocess.run(
        [sys.executable, "tools/check_devtest_orchestrator.py"],
        cwd=str(_FA_DIR),
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Test 1: clean-pass baseline
# ---------------------------------------------------------------------------


def test_checker_exits_zero_on_clean_source() -> None:
    """python tools/check_devtest_orchestrator.py must exit 0 on the real,
    clean chip_test.py (post-109-01/109-02 source: routes every op through
    resolve_chip, sets no VPP, builds no raw wire dict, passes no --force).
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
# Test 2: planted VPP-set violation (anti-hollow contract, D-03)
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_vpp_set(tmp_path: Path) -> None:
    """A real subprocess-level VPP-set call site MUST fail the gate.

    This is the anti-hollow proof (D-03): the fixture is written to disk and
    the checker is pointed at it via the FIRESTARTER_DEVTEST_SRC env-override
    (mirrors check_dispatch.py's FIRESTARTER_DB_FILE seam) -- a real
    subprocess-level violation, not an in-process synthetic.
    """
    bad = tmp_path / "planted_vpp_set.py"
    bad.write_text(
        "def orchestrate(op):\n"
        "    op.set_vpp(12000)\n"
        "    return op.write_eprom('chip', {}, 'path')\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_SRC": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted VPP-set violation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "VPP-set" in result.stdout


# ---------------------------------------------------------------------------
# Test 3: planted raw-wire-dict violation
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_raw_wire_dict(tmp_path: Path) -> None:
    """A real subprocess-level raw wire-dict literal MUST fail the gate."""
    bad = tmp_path / "planted_raw_wire_dict.py"
    bad.write_text(
        "def build_command():\n    return {'cmd': 2, 'algorithm': 7, 'vpp_mv': 12000}\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_SRC": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted raw-wire-dict violation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "wire" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Test 4: planted force=True keyword violation
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_force_true(tmp_path: Path) -> None:
    """A real subprocess-level force=True keyword pass-through MUST fail the gate."""
    bad = tmp_path / "planted_force_true.py"
    bad.write_text(
        "def orchestrate(op):\n    return op.erase_eprom('chip', {}, force=True)\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_SRC": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted force=True violation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "force" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Test 5: planted "--force" CLI-flag string-literal violation
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_force_flag_string(tmp_path: Path) -> None:
    """A real subprocess-level '--force' string literal MUST fail the gate."""
    bad = tmp_path / "planted_force_flag.py"
    bad.write_text("ARGS = ['dev', 'test', 'chip', '--force']\n")
    result = _run_checker({"FIRESTARTER_DEVTEST_SRC": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted --force string-literal violation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "force" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Test 6: env-override seam sanity (proves the injection path itself works)
# ---------------------------------------------------------------------------


def test_env_override_points_at_a_clean_fixture_still_passes(tmp_path: Path) -> None:
    """A CLEAN fixture injected via the env-override must still pass.

    Proves the env-override seam is a faithful re-target (not itself the
    source of the non-zero exit in tests 2-5) -- a clean fixture routed
    through the same seam produces PASS:, isolating the violations above as
    the true cause of the non-zero exits.
    """
    clean = tmp_path / "planted_clean.py"
    clean.write_text(
        "def orchestrate(op, eprom_data):\n"
        "    return op.write_eprom('chip', eprom_data, 'path')\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_SRC": str(clean)})
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on a clean env-override fixture.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout
