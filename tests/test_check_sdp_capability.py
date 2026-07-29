"""
Tests for check_sdp_capability_invariants.py (Phase 121 Plan 03, GATE-01,
D-14).

This is the mandatory anti-hollow pairing for GATE-01: a checker tool with
no negative-fixture test is exactly the failure mode this project incurred
with v1.12's GATE-03 (a declared-empty detector that could never fail
because nothing concrete was asserted). Every planted-violation test below
injects a REAL subprocess-level violation via the
`FIRESTARTER_SDP_CAPABILITY_SRC` env-override -- never an in-process
synthetic -- so a passing test suite proves the checker itself (not the
test) fails the build on a real violation.

Coverage:
  1. Clean-pass baseline: the checker exits 0 on the real, unmodified
     `firestarter/sdp_capability.py`.
  2. Non-vacuous by path: the checker's exported default-target constant
     resolves to a file that actually exists on disk -- proves this gate is
     not aimed at a stale/renamed path (the documented cross-repo hollow-gate
     failure mode).
  3. The clean-pass PASS: line names the resolved target's basename, so the
     PASS line cannot become uninformative about what was actually scanned.
  4. Class 1 (permit-by-default) planted violation flips the checker
     non-zero.
  5. Class 2 (widenable-allow-set) planted violation flips the checker
     non-zero.
  6. The Class 1 fixture's output ALSO reports the bare exception handler,
     proving both halves of D-14 Class 1 are individually caught (one does
     not mask the other).
  7. Env-override seam sanity: a CLEAN fixture routed through the same seam
     still passes -- isolates legs 4/5/6 as caused by the violations, not by
     the seam itself.
  8. Fail-closed on a missing target path: ERROR: on stderr, non-zero exit.
  9. Fail-closed on a zero-symbol scan: a syntactically valid fixture with no
     `SDP_CAPABLE_TOKENS` binding at all still fails the gate -- it must
     never be reported as PASS just because nothing was there to violate.

Does NOT modify `tests/test_sdp_capability.py` -- its 12 existing legs
(including the AST import-purity leg) stay exactly as they are; GATE-01
adds to the capability test surface here, it does not duplicate that file.
"""

import os
import subprocess
import sys
from pathlib import Path

from tools.check_sdp_capability_invariants import _DEFAULT_SDP_CAPABILITY_SRC

# Absolute path to the firestarter_app directory (cwd-independent), mirrors
# tests/test_check_devtest_orchestrator.py:46.
_FA_DIR = Path(__file__).parent.parent

_CLASS1_FIXTURE = _FA_DIR / "tests" / "fixtures" / "planted_permit_by_default.py"
_CLASS2_FIXTURE = _FA_DIR / "tests" / "fixtures" / "planted_widenable_allowset.py"


def _run_checker(
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_overrides or {})}
    return subprocess.run(
        [sys.executable, "tools/check_sdp_capability_invariants.py"],
        cwd=str(_FA_DIR),
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Leg 1: clean-pass baseline
# ---------------------------------------------------------------------------


def test_checker_exits_zero_on_clean_source() -> None:
    """python tools/check_sdp_capability_invariants.py must exit 0 on the
    real, unmodified firestarter/sdp_capability.py."""
    result = _run_checker()
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on clean source.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout, (
        f"Expected 'PASS:' in output but got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Leg 2: non-vacuous by path
# ---------------------------------------------------------------------------


def test_default_target_resolves_to_an_existing_file() -> None:
    """GATE-01's exported default-target constant must resolve to a file
    that actually exists on disk -- a gate aimed at a missing/renamed path
    passes vacuously, the documented cross-repo hollow-gate failure mode
    (`reference_firmware_renames_break_host_source_scanning_gates`). This is
    asserted here as its own leg, not merely inferred from leg 1 passing."""
    assert Path(_DEFAULT_SDP_CAPABILITY_SRC).is_file(), (
        f"GATE-01's default target {_DEFAULT_SDP_CAPABILITY_SRC!r} does not "
        "exist on disk -- the gate would be scanning nothing."
    )


# ---------------------------------------------------------------------------
# Leg 3: the PASS line names the scanned file
# ---------------------------------------------------------------------------


def test_pass_line_names_the_scanned_file() -> None:
    """The clean-pass PASS: line must name sdp_capability.py's basename, so
    a future reader can see what was actually scanned (RESEARCH F-6)."""
    result = _run_checker()
    assert result.returncode == 0, (
        f"checker exited {result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "sdp_capability.py" in result.stdout, (
        f"Expected the PASS: line to name sdp_capability.py but got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Leg 4: Class 1 (permit-by-default) planted violation
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_permit_by_default() -> None:
    """A real subprocess-level permit-by-default predicate (D-14 Class 1)
    MUST fail the gate."""
    result = _run_checker({"FIRESTARTER_SDP_CAPABILITY_SRC": str(_CLASS1_FIXTURE)})
    assert result.returncode != 0, (
        f"checker exited 0 on the planted permit-by-default fixture.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "permit-by-default" in result.stdout


# ---------------------------------------------------------------------------
# Leg 5: Class 2 (widenable-allow-set) planted violation
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_widenable_allowset() -> None:
    """A real subprocess-level widenable allow-set (D-14 Class 2) MUST fail
    the gate."""
    result = _run_checker({"FIRESTARTER_SDP_CAPABILITY_SRC": str(_CLASS2_FIXTURE)})
    assert result.returncode != 0, (
        f"checker exited 0 on the planted widenable-allow-set fixture.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "allow-set" in result.stdout


# ---------------------------------------------------------------------------
# Leg 6: the Class 1 fixture also reports the bare-except violation
# ---------------------------------------------------------------------------


def test_planted_permit_by_default_also_reports_bare_except() -> None:
    """The Class 1 fixture plants BOTH halves of D-14 Class 1 (an
    unconditional `(True, ...)` return AND a bare `except:`). Both must be
    individually reported, so one violation cannot mask the other."""
    result = _run_checker({"FIRESTARTER_SDP_CAPABILITY_SRC": str(_CLASS1_FIXTURE)})
    assert result.returncode != 0, (
        f"checker exited 0 on the planted permit-by-default fixture.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "except" in result.stdout.lower(), (
        f"Expected the bare-except violation to also be reported but got:\n"
        f"{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Leg 7: env-override seam sanity (proves the injection path itself works)
# ---------------------------------------------------------------------------


def test_env_override_points_at_a_clean_fixture_still_passes(tmp_path: Path) -> None:
    """A CLEAN fixture injected via the env-override must still pass.

    Proves the env-override seam is a faithful re-target (not itself the
    source of the non-zero exits in legs 4-6) -- one module-level
    `SDP_CAPABLE_TOKENS` bound once from a `frozenset` of string literals,
    and a predicate whose `(True, ...)` return is dominated by a membership
    test against it, routed through the same seam, produces PASS:.
    """
    clean = tmp_path / "planted_clean.py"
    clean.write_text(
        "SDP_CAPABLE_TOKENS = frozenset({'AT28C256'})\n"
        "\n"
        "\n"
        "def sdp_capability_for_entry(entry, display_name):\n"
        "    tokens = [display_name]\n"
        "    unrecognised = [t for t in tokens if t not in SDP_CAPABLE_TOKENS]\n"
        "    if unrecognised:\n"
        "        return False, 'refused'\n"
        "    return True, 'allowed'\n"
    )
    result = _run_checker({"FIRESTARTER_SDP_CAPABILITY_SRC": str(clean)})
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on a clean env-override fixture.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout


# ---------------------------------------------------------------------------
# Leg 8: fail-closed on a missing target path
# ---------------------------------------------------------------------------


def test_fail_closed_on_missing_target(tmp_path: Path) -> None:
    """A nonexistent FIRESTARTER_SDP_CAPABILITY_SRC path must ERROR to
    stderr and exit non-zero -- never a silent PASS."""
    missing = tmp_path / "does-not-exist.py"
    result = _run_checker({"FIRESTARTER_SDP_CAPABILITY_SRC": str(missing)})
    assert result.returncode != 0, (
        f"checker exited 0 on a missing target path.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "ERROR:" in result.stderr, (
        f"Expected 'ERROR:' on stderr but got:\nstderr:\n{result.stderr}"
    )
    assert str(missing) in result.stderr


# ---------------------------------------------------------------------------
# Leg 9: fail-closed on a zero-symbol scan
# ---------------------------------------------------------------------------


def test_fail_closed_on_zero_symbol_scan(tmp_path: Path) -> None:
    """A syntactically valid fixture with NO `SDP_CAPABLE_TOKENS` binding at
    all must still fail the gate -- the gate must not vacuously PASS just
    because its subject symbol is absent."""
    no_symbol = tmp_path / "planted_no_symbol.py"
    no_symbol.write_text("def unrelated_function():\n    return 1\n")
    result = _run_checker({"FIRESTARTER_SDP_CAPABILITY_SRC": str(no_symbol)})
    assert result.returncode != 0, (
        f"checker exited 0 on a zero-symbol fixture.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" not in result.stdout
