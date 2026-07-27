"""
Tests for check_no_log_in_sdp_window.py (TRACE-03c, Phase 116 Plan 04).

This is the mandatory anti-hollow pairing for the third planted-fault
negative (D-04's third bullet): a checker tool with no negative-fixture test
is exactly the failure mode this project incurred with v1.12's GATE-03 (a
declared-empty detector that could never fail because nothing concrete was
asserted). Every planted-violation test below injects a REAL subprocess-level
source file via the `FIRESTARTER_SDP_SRC` env-override -- never an
in-process synthetic -- so a passing test suite proves the checker itself
(not the test) fails on a real violation.

Coverage:
  1. Clean-source control: the checker exits 0 against the real, unmodified
     eeprom_28c.cpp.
  2. Planted violation (the load-bearing anti-hollow proof): the checker,
     pointed at the committed tests/fixtures/planted_log_in_window.cpp via
     FIRESTARTER_SDP_SRC, exits 1 and names the planted line.
  3. Out-of-window control: a temp variant with the logging call placed
     AFTER the completion-wait anchor exits 0 -- proves the gate
     discriminates by position, not by mere presence in the function.
  4. Comment-not-a-call control: a temp variant with a logging macro name
     appearing only inside a comment within the window exits 0 -- proves
     comment-stripping works and this is not a substring grep.
  5. Fail-closed leg: a missing source path exits non-zero.
  6. Fail-closed leg: a source with eeprom28c_write_init present but no
     emit anchor exits non-zero, naming the fix.
"""

import os
import subprocess
import sys
from pathlib import Path

# Absolute path to the firestarter_app directory (cwd-independent), mirrors
# tests/test_check_devtest_orchestrator.py:46.
_FA_DIR = Path(__file__).parent.parent
_FIXTURE = _FA_DIR / "tests" / "fixtures" / "planted_log_in_window.cpp"


def _run_checker(
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_overrides or {})}
    return subprocess.run(
        [sys.executable, "tools/check_no_log_in_sdp_window.py"],
        cwd=str(_FA_DIR),
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Test 1: clean-source control
# ---------------------------------------------------------------------------


def test_checker_exits_zero_on_clean_source() -> None:
    """python tools/check_no_log_in_sdp_window.py must exit 0 against the
    real, unmodified eeprom_28c.cpp -- the SDP timing window on today's
    tree contains no logging call."""
    result = _run_checker()
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on the real, clean source.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout, (
        f"Expected 'PASS:' in output but got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Test 2: planted violation (the anti-hollow proof, TRACE-03c)
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_committed_planted_violation() -> None:
    """The committed fixture (tests/fixtures/planted_log_in_window.cpp) MUST
    fail the gate, and the failure output must name the planted line.

    This is the load-bearing assertion of the whole task: a real,
    subprocess-level, permanently re-runnable proof that the checker can
    fail (D-04's third bullet). An exit-code-only assertion would not be
    enough on its own -- pairing it with the output-content assertion below
    is what makes this a genuine anti-hollow proof rather than a coincidence
    (e.g. the checker crashing for an unrelated reason).
    """
    assert _FIXTURE.is_file(), f"committed fixture missing: {_FIXTURE}"
    result = _run_checker({"FIRESTARTER_SDP_SRC": str(_FIXTURE)})
    assert result.returncode == 1, (
        f"checker exited {result.returncode} (expected 1) on the committed "
        f"planted violation.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "line 29" in result.stdout, (
        f"Expected the FAIL: output to name the planted line (29) but got:\n"
        f"{result.stdout}"
    )
    assert "LOG_INFO_ID" in result.stdout


# ---------------------------------------------------------------------------
# Test 3: out-of-window control -- discriminates by position, not presence
# ---------------------------------------------------------------------------


def test_checker_exits_zero_when_log_call_is_outside_window(tmp_path: Path) -> None:
    """A logging call placed AFTER the completion-wait anchor (i.e. outside
    the timing window) must NOT fail the gate -- proves the checker
    discriminates by position, not by mere presence of a LOG_* call anywhere
    in the function."""
    src = tmp_path / "out_of_window.cpp"
    src.write_text(
        "void eeprom28c_write_init(firestarter_handle_t* handle) {\n"
        "    flash_execute_command(EEPROM_SDP_DISABLE);\n"
        "    if (!eeprom28c_wait_for_write(handle, 0x5555, 0x20)) {\n"
        "        return;\n"
        "    }\n"
        "    LOG_INFO_ID(MSG_DEBUG);  // outside the window -- allowed\n"
        "}\n"
    )
    result = _run_checker({"FIRESTARTER_SDP_SRC": str(src)})
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on a log call OUTSIDE the "
        f"window.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout


# ---------------------------------------------------------------------------
# Test 4: comment-not-a-call control -- not a substring grep
# ---------------------------------------------------------------------------


def test_checker_exits_zero_when_log_macro_name_is_only_in_a_comment(
    tmp_path: Path,
) -> None:
    """A logging macro name appearing only inside a comment WITHIN the
    window must NOT fail the gate -- proves comment-stripping works and
    this checker is not a bare substring grep (Phase-109 SAFE-02,
    Phase-110 lesson)."""
    src = tmp_path / "comment_not_a_call.cpp"
    src.write_text(
        "void eeprom28c_write_init(firestarter_handle_t* handle) {\n"
        "    flash_execute_command(EEPROM_SDP_DISABLE);\n"
        "    // Do NOT call LOG_INFO_ID(...) here -- it would perturb timing.\n"
        "    /* LOG_ERROR_ID_U32(MSG_ERR_EEPROM_TIMEOUT, 0); is forbidden too */\n"
        "    if (!eeprom28c_wait_for_write(handle, 0x5555, 0x20)) {\n"
        "        return;\n"
        "    }\n"
        "}\n"
    )
    result = _run_checker({"FIRESTARTER_SDP_SRC": str(src)})
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on a log macro name that only "
        f"appears in a comment.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout


# ---------------------------------------------------------------------------
# Test 5: fail-closed leg -- missing source path
# ---------------------------------------------------------------------------


def test_checker_fails_closed_on_missing_source_path(tmp_path: Path) -> None:
    """A FIRESTARTER_SDP_SRC pointing at a nonexistent path must exit
    non-zero -- never a silent pass (T-116-04-ENVBYPASS)."""
    missing = tmp_path / "does_not_exist.cpp"
    result = _run_checker({"FIRESTARTER_SDP_SRC": str(missing)})
    assert result.returncode != 0, (
        f"checker exited 0 on a missing source path.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "ERROR:" in result.stderr


# ---------------------------------------------------------------------------
# Test 6: fail-closed leg -- function present, emit anchor absent
# ---------------------------------------------------------------------------


def test_checker_fails_closed_when_emit_anchor_is_absent(tmp_path: Path) -> None:
    """A source where eeprom28c_write_init's body exists but the
    command-emit anchor is missing must exit non-zero, with a message
    telling the maintainer to add the new anchor rather than delete the
    gate (T-116-04-RENAME)."""
    src = tmp_path / "no_emit_anchor.cpp"
    src.write_text(
        "void eeprom28c_write_init(firestarter_handle_t* handle) {\n"
        "    eeprom28c_wait_for_write(handle, 0x5555, 0x20);\n"
        "}\n"
    )
    result = _run_checker({"FIRESTARTER_SDP_SRC": str(src)})
    assert result.returncode != 0, (
        f"checker exited 0 on a source missing the emit anchor.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "add the new anchor" in result.stderr
