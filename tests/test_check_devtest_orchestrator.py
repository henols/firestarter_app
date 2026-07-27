"""
Tests for check_devtest_orchestrator.py (SAFE-03, Phase 109 D-02/D-03).

This is the mandatory anti-hollow pairing for the SAFE-03 gate: a checker
tool with no negative-fixture test is exactly the failure mode this project
incurred with v1.12's GATE-03 (a declared-empty detector that could never
fail because nothing concrete was asserted). Every planted-violation test
below injects a REAL subprocess-level violation via the
`FIRESTARTER_DEVTEST_SRC` / `FIRESTARTER_DEVTEST_HANDLER` env-overrides --
never an in-process synthetic -- so a passing test suite proves the checker
itself (not the test) fails the build on a real violation.

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
  6. Env-override seam sanity: a clean fixture injected via
     FIRESTARTER_DEVTEST_SRC still passes.
  7. Handler-shaped planted violation (Phase 112, anti-hollow for the
     `dev_test` handler leg specifically): a fixture defining a `dev_test`
     function containing a forbidden op, injected via
     FIRESTARTER_DEVTEST_HANDLER, flips the checker non-zero -- AND the real,
     clean `cli_handlers.py` (which the checker now actually scans, scoped to
     the `dev_test` function + its private helpers) still passes.
  8. submit.py-shaped planted violation (Phase 113, anti-hollow for the
     THIRD full-scan leg specifically): a fixture with a forbidden op
     injected via FIRESTARTER_DEVTEST_SUBMIT flips the checker non-zero --
     AND a clean fixture through the same env-override still passes -- AND
     the real, clean `submit.py` still passes with the PASS line naming it.
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


# ---------------------------------------------------------------------------
# Test 7: handler-shaped planted violation (Phase 112 anti-hollow proof)
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_handler_violation(tmp_path: Path) -> None:
    """A handler-shaped fixture with a forbidden op MUST fail the gate.

    Mimics the real `dev_test` handler's shape (a `dev_test`-named function
    calling into an operator) but plants a VPP-set call site inside it. This
    is the anti-hollow proof for the HANDLER leg specifically (Phase-109
    D-02/D-03): the fixture is written to disk and injected via
    FIRESTARTER_DEVTEST_HANDLER (a real subprocess-level violation, not an
    in-process synthetic) -- if the checker silently skipped the handler
    scan (or scanned the wrong function names), this would incorrectly pass.
    """
    bad = tmp_path / "planted_handler_violation.py"
    bad.write_text(
        "def dev_test(app, chip, destructive, output_dir, assume_yes):\n"
        "    app.hardware_manager.set_vpp(12000)\n"
        "    return app.eprom_operator.write_eprom(chip, {}, 'path')\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_HANDLER": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted handler-shaped VPP-set violation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "VPP-set" in result.stdout


def test_checker_exits_nonzero_on_planted_handler_force_violation(
    tmp_path: Path,
) -> None:
    """A handler-shaped fixture passing force=True MUST fail the gate.

    Second handler-leg planted-violation shape (force pass-through rather
    than VPP-set) -- proves the scoped handler scan catches more than one
    deny bucket, not just the one the first fixture happens to hit.
    """
    bad = tmp_path / "planted_handler_force.py"
    bad.write_text(
        "def dev_test(app, chip, destructive, output_dir, assume_yes):\n"
        "    return app.eprom_operator.erase_eprom(chip, {}, force=True)\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_HANDLER": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted handler-shaped force=True violation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "force" in result.stdout.lower()


def test_checker_exits_zero_on_real_handler_now_in_scope() -> None:
    """The clean-pass baseline, re-asserted with the handler leg in scope.

    Load-bearing proof (Phase 112): the real, shipped `cli_handlers.py`
    (scoped to `dev_test` + its private helpers) is orchestrator-only --
    this is the same invocation as test_checker_exits_zero_on_clean_source
    but stated explicitly so a future reader sees the handler-in-scope
    assertion is deliberate, not incidental.
    """
    result = _run_checker()
    assert result.returncode == 0, (
        f"checker exited {result.returncode} with the real handler in scope.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout
    assert "cli_handlers.py" in result.stdout, (
        f"Expected the PASS: line to name cli_handlers.py (handler actually "
        f"scanned, not skipped) but got:\n{result.stdout}"
    )


def test_env_override_points_at_a_clean_handler_fixture_still_passes(
    tmp_path: Path,
) -> None:
    """A CLEAN handler-shaped fixture injected via the env-override still passes.

    Proves the FIRESTARTER_DEVTEST_HANDLER seam is a faithful re-target (not
    itself the source of the non-zero exit in the tests above) -- a clean
    fixture defining `dev_test` (with no forbidden ops) routed through the
    same seam produces PASS:, isolating the violations above as the true
    cause of the non-zero exits.
    """
    clean = tmp_path / "planted_handler_clean.py"
    clean.write_text(
        "def dev_test(app, chip, destructive, output_dir, assume_yes):\n"
        "    return app.eprom_operator.write_eprom(chip, {}, 'path')\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_HANDLER": str(clean)})
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on a clean handler env-override "
        f"fixture.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout


# ---------------------------------------------------------------------------
# Test 8: submit.py third full-scan leg (Phase 113, anti-hollow proof)
# ---------------------------------------------------------------------------


def test_checker_exits_zero_on_real_submit_and_pass_line_names_it() -> None:
    """The real, clean `submit.py` passes, and the PASS: line names it --
    proving the third leg actually ran (was not silently skipped, the
    v1.12 hollow-GATE-03 failure mode)."""
    result = _run_checker()
    assert result.returncode == 0, (
        f"checker exited {result.returncode} with the submit.py leg in scope.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout
    assert "submit.py" in result.stdout, (
        f"Expected the PASS: line to name submit.py (leg actually scanned, "
        f"not skipped) but got:\n{result.stdout}"
    )


def test_checker_exits_nonzero_on_planted_submit_vpp_set_violation(
    tmp_path: Path,
) -> None:
    """A submit-shaped fixture with a real VPP-set call site, injected via
    FIRESTARTER_DEVTEST_SUBMIT, MUST fail the gate (anti-hollow proof for
    the new leg, T-113-01)."""
    bad = tmp_path / "planted_submit_vpp_set.py"
    bad.write_text(
        "def submit_report(op, report, chip, saved_json_path):\n"
        "    op.set_vpp(12000)\n"
        "    return None\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_SUBMIT": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted submit-shaped VPP-set violation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "VPP-set" in result.stdout


def test_checker_exits_nonzero_on_planted_submit_force_violation(
    tmp_path: Path,
) -> None:
    """A submit-shaped fixture passing force=True, injected via
    FIRESTARTER_DEVTEST_SUBMIT, MUST fail the gate."""
    bad = tmp_path / "planted_submit_force.py"
    bad.write_text(
        "def submit_report(op, report, chip, saved_json_path):\n"
        "    return op.erase_eprom(chip, {}, force=True)\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_SUBMIT": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted submit-shaped force=True violation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "force" in result.stdout.lower()


def test_env_override_points_at_a_clean_submit_fixture_still_passes(
    tmp_path: Path,
) -> None:
    """A CLEAN submit-shaped fixture injected via the env-override still
    passes -- proves the FIRESTARTER_DEVTEST_SUBMIT seam is a faithful
    re-target (not itself the source of the non-zero exit in the two tests
    above), isolating the planted violations as the true cause."""
    clean = tmp_path / "planted_submit_clean.py"
    clean.write_text(
        "def submit_report(report, chip, saved_json_path):\n    return None\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_SUBMIT": str(clean)})
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on a clean submit env-override "
        f"fixture.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout
