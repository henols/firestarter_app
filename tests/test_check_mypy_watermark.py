"""
Tests for tools/check_mypy_watermark.py (GATE-01..04/06, Phase 131 Plan 02).

This is the checker's FIRST-EVER paired pytest. Its absence was exactly the
failure mode this project incurred with v1.12's GATE-03 (a declared-empty
detector that could never fail because nothing concrete was asserted): a
checker with no negative-fixture test is a checker nobody has ever watched
fail for the right reason.

Coverage:
  1. `test_truncated_run_exits_2` -- a truncated mypy run (the measured
     devcontainer shape: a `[syntax]` error line then "Found 1 error in 1
     file (errors prevented further checking)", no `checked` clause) is
     proven to exit 2, and the printed reason names the exit code.
  2. `test_config_rejection_exits_2` -- a well-formed, complete completion
     clause at returncode 1 is STILL rejected if a config-diagnostic line is
     present -- proves the config guard is independent of both the
     returncode guard and the completion-clause guard.
  3. `test_over_watermark_exits_1` -- an error count above the watermark
     exits 1, with the exact `mypy errors: N (watermark: M)` first line.
  4. `test_below_coverage_floor_exits_2` -- a plausible-looking count on a
     truncated file set (fewer than MIN_CHECKED_SOURCE_FILES) exits 2, and
     the message names both the checked count and the floor.
  5. `test_mypy_argv_is_sys_executable_dash_m` -- GATE-04's positive,
     fail-provable proof: with `subprocess.run` monkeypatched INSIDE the
     checker's own module namespace (a call-argument probe, not an
     environment simulation -- adds no production seam, per D-01),
     `run_mypy()`'s captured argv equals, by WHOLE-LIST equality,
     `[sys.executable, "-m", "mypy", "firestarter/", "tests/"]`.
  6. `test_end_to_end_terminal_shape_is_legible` -- running the real checker
     as a subprocess, from both the app root and a foreign `tmp_path` cwd,
     lands in exactly one of two legible terminal shapes: the complete shape
     (a `mypy errors: N (watermark: M)` line) or the incomplete shape (an
     `ERROR:` diagnostic naming the tool/config failure) -- never both, and
     an exit-2 run never carries the count line (correction F-05: D-02's
     original layer-3 wording is unsatisfiable in this devcontainer, since
     the hardened gate exits 2 from `classify_mypy_result` BEFORE any count
     is printed, and printing a count there would be exactly the fail-open
     shape this phase removes). Both cwds must land in the SAME shape,
     proving `REPO_ROOT`'s `Path(__file__).resolve()` anchoring survives a
     foreign working directory (the `_HERE` trap).
  7. `test_complete_error_run_returns_count_without_raising` (control) --
     a complete, well-formed 69-error run returns 69 and does not raise.
  8. `test_clean_run_returns_zero_without_raising` (control) -- a clean,
     complete run returns 0 and does not raise.

Controls 7-8 exist so that legs 1-4 are not passing merely because
`classify_mypy_result` raises unconditionally -- without them, four raising
legs prove nothing about which inputs are supposed to raise.

Adaptations from the house `test_check_*` shape (documented, not
accidental): legs 1-4 are IN-PROCESS calls against the pure classifier
(`pytest.raises(SystemExit)` + `.value.code`), not subprocess calls -- no
existing `test_check_*` module does this, because no other checker in this
repo splits a pure classifier out from its subprocess runner. Every
fail-closed leg asserts the MESSAGE, not only the code -- an exit-code-only
assertion would not prove the checker failed for the RIGHT reason (a
recorded lesson of this project). Leg 5's argv assertion is a whole-list
equality, not a membership check (membership-only argv assertions miss the
negative -- also a recorded lesson).

This module adds no `os.environ` seam to the production checker (D-01) and
depends on no fake `mypy` earlier on `PATH` (GATE-04 exists specifically to
remove `PATH` resolution from this gate).
"""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Absolute path to the firestarter_app directory (cwd-independent), mirrors
# tests/test_check_devtest_orchestrator.py:47.
_FA_DIR = Path(__file__).parent.parent
_CHECKER_SCRIPT = _FA_DIR / "tools" / "check_mypy_watermark.py"

# ---------------------------------------------------------------------------
# Canned mypy output. Lives here as module-level string constants, not under
# tests/fixtures/, because it is canned STDOUT TEXT a pure function is called
# against in-process -- not source a subprocess must read from disk. (See the
# module docstring's "Adaptations" note; tests/fixtures/ is reserved for the
# latter and is ruff `extend-exclude`d for exactly that reason.)
# ---------------------------------------------------------------------------

# Measured live in this devcontainer, 2026-08-03: `python3 -m mypy firestarter/
# tests/` truncates on an ambient numpy PEP-695 stub, mypy itself exits 2, and
# the output carries NO `(checked N source files)` clause -- the exact
# truncated-run shape GATE-02's completion-clause requirement exists to catch.
TRUNCATED_OUTPUT = (
    "/usr/local/lib/python3.12/site-packages/numpy/__init__.pyi:737: error: "
    "Type statement is only supported in Python 3.12 and greater  [syntax]\n"
    "Found 1 error in 1 file (errors prevented further checking)\n"
)

# A well-formed, COMPLETE completion clause (69 errors, 120 checked files) at
# returncode 1 -- but carrying mypy's own config-diagnostic prefix. Proves the
# config guard fires independently of both the returncode guard (1 is a
# legitimate errors-found code) and the completion-clause guard (this clause
# parses cleanly).
CONFIG_REJECTION_OUTPUT = (
    "pyproject.toml: [mypy]: python_version: 3.9 is not supported "
    "(must be 3.10 or higher)\n"
    "Found 69 errors in 17 files (checked 120 source files)\n"
)

# A plausible-looking count on a truncated file set: the completion clause
# parses cleanly, but `checked` (4) is far below MIN_CHECKED_SOURCE_FILES
# (120) -- a run that silently checked a subset of the tree wearing a
# plausible error count.
UNDER_FLOOR_OUTPUT = "Found 3 errors in 2 files (checked 4 source files)\n"

# A complete, well-formed errors-found run, well above any real watermark --
# used to drive the over-watermark leg through both classify_mypy_result AND
# enforce_watermark, tying the two functions together in one leg.
OVER_WATERMARK_OUTPUT = "Found 200 errors in 30 files (checked 130 source files)\n"

# A complete, clean run -- the "Success" completion clause shape.
CLEAN_OUTPUT = "Success: no issues found in 120 source files\n"


def _load_checker():
    """Load tools/check_mypy_watermark.py in-process. `tests/__init__.py`
    exists, so the repo root is on `sys.path` and `tools` resolves as a
    namespace package -- the same import path already proven at
    tests/test_check_devtest_orchestrator.py:395-397."""
    return importlib.import_module("tools.check_mypy_watermark")


def _run_checker(cwd: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the real checker as a subprocess from an arbitrary cwd. The
    script path itself is always absolute (`_CHECKER_SCRIPT`), so only `cwd`
    varies -- this is what proves `REPO_ROOT`'s `Path(__file__).resolve()`
    anchoring is independent of the caller's working directory."""
    return subprocess.run(
        [sys.executable, str(_CHECKER_SCRIPT)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Leg 1: truncated run -> exit 2
# ---------------------------------------------------------------------------


def test_truncated_run_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    """A truncated mypy run (returncode 2, no completion clause) must raise
    SystemExit(2) via the returncode guard, naming the offending exit code --
    not merely raising, but raising for the stated reason."""
    mod = _load_checker()

    with pytest.raises(SystemExit) as exc:
        mod.classify_mypy_result(2, TRUNCATED_OUTPUT)
    assert exc.value.code == 2

    captured = capsys.readouterr()
    assert "mypy exited 2" in captured.err, (
        f"expected the failure message to name exit code 2, got:\n{captured.err}"
    )


# ---------------------------------------------------------------------------
# Leg 2: config rejection -> exit 2, independent of returncode and clause
# ---------------------------------------------------------------------------


def test_config_rejection_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    """A config-diagnostic line must raise SystemExit(2) even when the
    returncode is 1 (a legitimate errors-found code) and the completion
    clause is well-formed -- the config guard cannot be shadowed by either
    of the other two guards passing."""
    mod = _load_checker()

    with pytest.raises(SystemExit) as exc:
        mod.classify_mypy_result(1, CONFIG_REJECTION_OUTPUT)
    assert exc.value.code == 2

    captured = capsys.readouterr()
    assert "rejected a config value" in captured.err
    assert "python_version" in captured.err, (
        f"expected the failure message to name the offending config line, "
        f"got:\n{captured.err}"
    )


# ---------------------------------------------------------------------------
# Leg 3: over watermark -> exit 1
# ---------------------------------------------------------------------------


def test_over_watermark_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    """A complete, well-formed run whose error count exceeds the watermark
    must raise SystemExit(1), with the first printed line exactly
    'mypy errors: 200 (watermark: 35)'. Drives classify_mypy_result first
    (so OVER_WATERMARK_OUTPUT's canned completion clause is exercised, not
    bypassed) then enforce_watermark on the resulting count."""
    mod = _load_checker()

    count = mod.classify_mypy_result(1, OVER_WATERMARK_OUTPUT)
    assert count == 200
    capsys.readouterr()  # discard classify_mypy_result's own "checked N" line

    with pytest.raises(SystemExit) as exc:
        mod.enforce_watermark(count, 35)
    assert exc.value.code == 1

    captured = capsys.readouterr()
    first_line = captured.out.splitlines()[0]
    assert first_line == "mypy errors: 200 (watermark: 35)", (
        f"expected the exact first printed line, got: {first_line!r}"
    )


# ---------------------------------------------------------------------------
# Leg 4: below coverage floor -> exit 2
# ---------------------------------------------------------------------------


def test_below_coverage_floor_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    """A plausible-looking count (3 errors in 2 files) on a run that checked
    only 4 files -- far below MIN_CHECKED_SOURCE_FILES -- must raise
    SystemExit(2), and the message must name BOTH the observed checked count
    (4) and the floor (MIN_CHECKED_SOURCE_FILES), not merely the verdict."""
    mod = _load_checker()

    with pytest.raises(SystemExit) as exc:
        mod.classify_mypy_result(1, UNDER_FLOOR_OUTPUT)
    assert exc.value.code == 2

    captured = capsys.readouterr()
    assert "checked only 4" in captured.err
    assert str(mod.MIN_CHECKED_SOURCE_FILES) in captured.err, (
        f"expected the failure message to name the {mod.MIN_CHECKED_SOURCE_FILES} "
        f"floor, got:\n{captured.err}"
    )


# ---------------------------------------------------------------------------
# Leg 5: argv proof -- GATE-04's positive, fail-provable evidence
# ---------------------------------------------------------------------------


def test_mypy_argv_is_sys_executable_dash_m(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_mypy()'s subprocess.run is monkeypatched INSIDE the checker's own
    module namespace -- a call-argument probe, never an environment
    simulation, adding no production seam (D-01). The captured argv must
    equal, by WHOLE-LIST equality (not membership -- a membership-only argv
    assertion misses the negative), [sys.executable, "-m", "mypy",
    "firestarter/", "tests/"]."""
    mod = _load_checker()
    captured_calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_calls.append(list(argv))
        return subprocess.CompletedProcess(
            argv,
            returncode=0,
            stdout="Success: no issues found in 120 source files\n",
            stderr="",
        )

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    mod.run_mypy()

    assert len(captured_calls) == 1, (
        f"expected exactly one subprocess.run call, got {len(captured_calls)}"
    )
    assert captured_calls[0] == [
        sys.executable,
        "-m",
        "mypy",
        "firestarter/",
        "tests/",
    ], f"argv did not match by whole-list equality: {captured_calls[0]!r}"


# ---------------------------------------------------------------------------
# Leg 6: end-to-end runner proof -- two legible terminal shapes
# ---------------------------------------------------------------------------


def test_end_to_end_terminal_shape_is_legible(tmp_path: Path) -> None:
    """Running the real checker as a subprocess, from both the app root and
    a foreign tmp_path cwd, must land in exactly one of two legible terminal
    shapes (correction F-05, replacing D-02 layer 3's original
    count-asserting wording, which is unsatisfiable in this devcontainer):

      - the COMPLETE shape: a `mypy errors: N (watermark: M)` line; or
      - the INCOMPLETE shape: an `ERROR:` diagnostic naming the tool/config
        failure, with NO `mypy errors:` line at all -- an exit-2 run that
        also prints a count is exactly the fail-open shape this phase
        removes.

    Both cwds must land in the SAME shape, proving REPO_ROOT's
    Path(__file__).resolve() anchoring survives a foreign working directory
    (the `_HERE` trap). No count is asserted -- the count is
    environment-dependent, and that dependence is this phase's whole
    subject."""
    count_line_re = re.compile(r"^mypy errors: \d+ \(watermark: \d+\)$", re.MULTILINE)
    observed_shapes: list[tuple[int, bool]] = []

    for cwd in (_FA_DIR, tmp_path):
        result = _run_checker(cwd)
        assert result.returncode in (0, 1, 2), (
            f"unexpected exit code {result.returncode} from cwd={cwd}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        output = result.stdout + result.stderr
        has_count_line = bool(count_line_re.search(output))

        if result.returncode == 2:
            assert not has_count_line, (
                f"exit-2 run from cwd={cwd} ALSO printed a mypy errors: line "
                f"-- this is exactly the fail-open shape this phase exists "
                f"to remove.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
            assert "ERROR:" in result.stderr, (
                f"exit-2 run from cwd={cwd} printed no ERROR: diagnostic "
                f"naming the tool/config failure.\nstdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )

        if has_count_line:
            assert result.returncode in (0, 1), (
                f"a mypy errors: line was present from cwd={cwd} but the "
                f"exit code was neither 0 nor 1: {result.returncode}"
            )

        observed_shapes.append((result.returncode, has_count_line))

    assert observed_shapes[0] == observed_shapes[1], (
        f"the two invocations (app root vs. foreign tmp_path cwd) reached "
        f"different terminal shapes: {observed_shapes} -- REPO_ROOT's "
        f"anchoring did not survive the foreign cwd."
    )


# ---------------------------------------------------------------------------
# Controls: the classifier does not simply raise on everything
# ---------------------------------------------------------------------------


def test_complete_error_run_returns_count_without_raising() -> None:
    """A complete, well-formed 69-error run must return 69 WITHOUT raising --
    proves the four fail-closed legs above are not passing merely because
    classify_mypy_result raises unconditionally."""
    mod = _load_checker()
    count = mod.classify_mypy_result(
        1, "Found 69 errors in 17 files (checked 120 source files)"
    )
    assert count == 69


def test_clean_run_returns_zero_without_raising() -> None:
    """A clean, complete run must return 0 without raising."""
    mod = _load_checker()
    count = mod.classify_mypy_result(0, CLEAN_OUTPUT)
    assert count == 0
