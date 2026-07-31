"""
Tests for tools/check_no_exists_proxy.py (D-09's recurrence lint against the
module-level absence-proxy idiom, Phase 123 Plan 09).

This is the mandatory anti-hollow pairing for a new source-scanning gate: a
checker tool with no negative-fixture test is exactly the failure mode this
project incurred with v1.12's GATE-03 (a declared-empty detector that could
never fail because nothing concrete was asserted). Every planted-violation
test below injects a REAL subprocess-level source file via the
`FIRESTARTER_PROXY_LINT_TARGETS` env-override -- invoked with a LIST argv,
never `shell=True`, and never an import -- the fixture's module-level proxy
constants bind at import time, so `monkeypatch` against them would be inert
(mirrors `tests/fw_presence.py`'s own "import-time binding" warning).

Coverage:
  1. Clean control: the checker exits 0 against the real, post-rekey
     `tests/` default target set, printing a `PASS:` line naming the
     scanned files.
  2. Planted simple proxy fails, naming the offending constant and line
     (derived from the committed fixture at test time, not hardcoded, so a
     future re-plant cannot silently desync the assertion from the
     fixture).
  3. Planted compound proxy fails, naming the offending constant.
  4. The legitimate in-function existence check in the same fixture is NOT
     among the reported violations -- proves the lint discriminates by
     scope, not by mere presence of the substring "exists".
  5. Never-vacuous: the seam set to the empty string exits non-zero with the
     never-vacuous message and prints no `PASS:` line.
  6. Fail-closed: a nonexistent target path exits non-zero, naming the
     missing path.
  7. Precedence pin: the seam points at the violating fixture while
     positional argv names a clean file, and the run passes -- pins the
     documented precedence (argv wins) against a future silent inversion.
  8. A syntax error in a scanned file exits 2, not 1 and not 0.

Every failing case asserts BOTH the non-zero exit AND a distinctive
substring in stdout/stderr -- an exit-code-only assertion would not by
itself prove the checker failed for the RIGHT reason.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_FA_DIR = Path(__file__).parent.parent
_CHECKER = _FA_DIR / "tools" / "check_no_exists_proxy.py"
_FIXTURE = _FA_DIR / "tests" / "fixtures" / "planted_no_exists_proxy.py"


def _run_checker(
    argv: list[str] | None = None,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **(env_overrides or {})}
    cmd = [sys.executable, str(_CHECKER), *(argv or [])]
    return subprocess.run(
        cmd, cwd=str(_FA_DIR), capture_output=True, text=True, env=env
    )


def _line_number_of_marker(text: str, marker: str) -> int:
    """Return the 1-indexed line number of the first line containing
    `marker`, or raise if not found -- derives the expected planted line
    from the fixture at test time rather than hardcoding a second literal
    that a future re-plant could silently desync."""
    for i, line in enumerate(text.splitlines(), start=1):
        if marker in line:
            return i
    raise AssertionError(f"marker {marker!r} not found in source text")


# ---------------------------------------------------------------------------
# Test 1: clean-source control -- the real, post-rekey tests/ tree
# ---------------------------------------------------------------------------


def test_checker_exits_zero_on_real_default_tree() -> None:
    """python tools/check_no_exists_proxy.py must exit 0 against this
    project's real, post-Phase-123-Plan-08-rekey `tests/` default target
    set, printing a PASS: line naming the scanned files."""
    result = _run_checker()
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on the real, clean tree.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout, (
        f"Expected 'PASS:' in output but got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Test 2: planted simple-shape violation (the anti-hollow proof)
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_simple_proxy() -> None:
    """The committed fixture's simple absence-proxy constant
    (SIMPLE_ABSENCE_PROXY) MUST fail the gate, naming the constant and the
    line it was planted on."""
    assert _FIXTURE.is_file(), f"committed fixture missing: {_FIXTURE}"
    fixture_text = _FIXTURE.read_text(encoding="utf-8")
    planted_line = _line_number_of_marker(fixture_text, "SIMPLE_ABSENCE_PROXY =")

    result = _run_checker(
        env_overrides={"FIRESTARTER_PROXY_LINT_TARGETS": str(_FIXTURE)}
    )
    assert result.returncode == 1, (
        f"checker exited {result.returncode} (expected 1) on the committed "
        f"planted violation.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "SIMPLE_ABSENCE_PROXY" in result.stdout
    assert f":{planted_line}:" in result.stdout, (
        f"Expected the FAIL: output to name the planted line ({planted_line}) "
        f"but got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Test 3: planted compound-shape violation
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_compound_proxy() -> None:
    """The committed fixture's compound absence-proxy constant
    (COMPOUND_ABSENCE_PROXY, a `not` over a boolean AND of two `.exists()`
    calls) MUST also fail the gate, naming the constant."""
    result = _run_checker(
        env_overrides={"FIRESTARTER_PROXY_LINT_TARGETS": str(_FIXTURE)}
    )
    assert result.returncode == 1
    assert "FAIL:" in result.stdout
    assert "COMPOUND_ABSENCE_PROXY" in result.stdout


# ---------------------------------------------------------------------------
# Test 4: the legitimate in-function check is NOT reported
# ---------------------------------------------------------------------------


def test_legitimate_in_function_check_is_not_reported() -> None:
    """The fixture's `legitimate_in_function_check` function contains an
    ordinary in-function existence check -- it must NOT appear among the
    reported violations, proving the lint discriminates by scope (module
    level vs. function body), not by mere presence of the substring
    "exists"."""
    result = _run_checker(
        env_overrides={"FIRESTARTER_PROXY_LINT_TARGETS": str(_FIXTURE)}
    )
    assert result.returncode == 1  # still fails on the two module-level proxies
    assert "legitimate_in_function_check" not in result.stdout, (
        f"the legitimate in-function existence check was wrongly reported:\n"
        f"{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Test 5: never-vacuous -- empty seam fails closed with no PASS:
# ---------------------------------------------------------------------------


def test_never_vacuous_empty_seam_fails_closed() -> None:
    """FIRESTARTER_PROXY_LINT_TARGETS explicitly set to the empty string
    must exit non-zero with the never-vacuous message and print no PASS:
    line -- proves the zero-targets guard fires even though it is hoisted
    above (and would otherwise be shadowed by a vacuously-satisfied)
    missing-target guard."""
    result = _run_checker(env_overrides={"FIRESTARTER_PROXY_LINT_TARGETS": ""})
    assert result.returncode != 0, (
        f"checker exited 0 on an explicitly-emptied target seam.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "no scan targets resolved" in result.stdout
    assert "PASS:" not in result.stdout


# ---------------------------------------------------------------------------
# Test 6: fail-closed -- a nonexistent target path
# ---------------------------------------------------------------------------


def test_missing_target_fails_closed() -> None:
    """A target path that does not exist on disk must exit non-zero, naming
    the missing path -- the gate must never silently skip a target it
    cannot find."""
    result = _run_checker(
        env_overrides={"FIRESTARTER_PROXY_LINT_TARGETS": "tests/no_such_file.py"}
    )
    assert result.returncode != 0, (
        f"checker exited 0 on a missing target path.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "no_such_file.py" in result.stdout


# ---------------------------------------------------------------------------
# Test 7: precedence pin -- argv wins over the env seam
# ---------------------------------------------------------------------------


def test_precedence_argv_wins_over_env_seam() -> None:
    """Positional argv naming a clean file must win over the env seam
    pointed at the violating fixture -- pins the documented precedence
    (argv > env seam > defaults) against a future silent inversion."""
    result = _run_checker(
        argv=["tests/conftest.py"],
        env_overrides={"FIRESTARTER_PROXY_LINT_TARGETS": str(_FIXTURE)},
    )
    assert result.returncode == 0, (
        f"checker exited {result.returncode} (expected 0) -- argv should "
        f"have taken precedence over the env seam pointed at the violating "
        f"fixture.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout


# ---------------------------------------------------------------------------
# Test 8: syntax error exits 2, not 1 and not 0
# ---------------------------------------------------------------------------


def test_syntax_error_in_scanned_file_exits_2(tmp_path: Path) -> None:
    """A scanned file that is not valid Python must exit 2 (tool/config
    error) -- a broken/unparseable source file must never be mistaken for
    either a clean tree (0) or a real violation (1)."""
    bad_syntax = tmp_path / "not_valid_python.py"
    bad_syntax.write_text("def broken(:\n    pass\n")

    result = _run_checker(argv=[str(bad_syntax)])
    assert result.returncode == 2, (
        f"checker exited {result.returncode} (expected 2) on an unparseable "
        f"source file.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "ERROR:" in result.stderr
