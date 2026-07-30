"""
Tests for check_no_log_in_sdp_window.py (TRACE-03c, Phase 116 Plan 04; window
redefined by Phase 118 Plan 01, D-06).

This is the mandatory anti-hollow pairing for the third planted-fault
negative (D-04's third bullet): a checker tool with no negative-fixture test
is exactly the failure mode this project incurred with v1.12's GATE-03 (a
declared-empty detector that could never fail because nothing concrete was
asserted). Every planted-violation test below injects a REAL subprocess-level
source file via the `FIRESTARTER_SDP_SRC` env-override -- never an
in-process synthetic -- so a passing test suite proves the checker itself
(not the test) fails on a real violation.

D-06 redefined the checker's scanned window from the span between the
command-emit and completion-wait call sites to the union of two brace-matched
function bodies: `eeprom28c_emit_command_sequence()` and
`eeprom28c_wait_for_sdp_completion()`. That redefinition broke four of the
original six cases (2, 3, 4, 6) -- each is repaired here by name, not as a
footnote -- and adds a new poll-body negative (case 7) so the union's second
half is actually proven scanned, not merely assumed.

Coverage:
  1. Clean-source control: the checker exits 0 against the real, unmodified
     eeprom_28c.cpp.
  2. Planted violation (the load-bearing anti-hollow proof): the checker,
     pointed at the committed tests/fixtures/planted_log_in_window.cpp via
     FIRESTARTER_SDP_SRC, exits 1 and names the planted line (derived from
     the fixture at test time, not a hardcoded literal, so a future re-plant
     cannot silently desync the assertion from the fixture).
  3. Out-of-window control: a temp variant with both the emitter and
     completion-poll bodies present and the logging call placed in
     `eeprom28c_write_init` BETWEEN the two calls (i.e. outside both bodies
     -- Phase 118's report lines occupy exactly this span) exits 0 -- proves
     the gate discriminates by position, not by mere presence.
  4. Comment-not-a-call control: a temp variant with both bodies present and
     a logging macro name appearing only inside a comment within the
     emitter body exits 0 -- proves comment-stripping still holds under the
     new window (load-bearing on production source too: the real
     `eeprom28c_wait_for_sdp_completion` body carries an in-body comment
     naming a logging macro, `eeprom_28c.cpp:267-268`).
  5. Fail-closed leg: a missing source path exits non-zero.
  6. Fail-closed leg: a source with `eeprom28c_write_init` and a completion-
     poll body present but the emitter body absent exits non-zero, naming
     the fix (renamed from `test_checker_fails_closed_when_emit_anchor_is_
     absent`, since D-06 resolves the emitter by function body, not anchor).
  7. Completion-poll-body negative (new): a logging call planted inside
     `eeprom28c_wait_for_sdp_completion`'s body (emitter clean) exits 1 and
     names the poll-body line -- proves the union's second half is actually
     scanned, not only the emitter half.
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


def _line_number_of_marker(text: str, marker: str) -> int:
    """Return the 1-indexed line number of the first line containing
    `marker` in `text`, or raise if not found. Used to derive the expected
    planted-violation line number directly from a fixture/temp source at
    test time, rather than hardcoding a second literal that a future
    re-plant could silently desync from."""
    for i, line in enumerate(text.splitlines(), start=1):
        if marker in line:
            return i
    raise AssertionError(f"marker {marker!r} not found in source text")


# ---------------------------------------------------------------------------
# Test 1: clean-source control
# ---------------------------------------------------------------------------


def test_checker_exits_zero_on_clean_source() -> None:
    """python tools/check_no_log_in_sdp_window.py must exit 0 against the
    real, unmodified eeprom_28c.cpp -- neither the emitter body nor the
    completion-poll body on today's tree contains a logging call."""
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
    (e.g. the checker crashing for an unrelated reason). The expected line
    number is derived from the fixture itself (D-06's re-plant moved it into
    the emitter body), not hardcoded, so a future re-plant cannot silently
    desync this assertion from the fixture.
    """
    assert _FIXTURE.is_file(), f"committed fixture missing: {_FIXTURE}"
    fixture_text = _FIXTURE.read_text(encoding="utf-8")
    planted_line = _line_number_of_marker(fixture_text, "PLANTED VIOLATION")

    result = _run_checker({"FIRESTARTER_SDP_SRC": str(_FIXTURE)})
    assert result.returncode == 1, (
        f"checker exited {result.returncode} (expected 1) on the committed "
        f"planted violation.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert f"line {planted_line}" in result.stdout, (
        f"Expected the FAIL: output to name the planted line ({planted_line}) "
        f"but got:\n{result.stdout}"
    )
    assert "LOG_INFO_ID" in result.stdout


# ---------------------------------------------------------------------------
# Test 3: out-of-window control -- discriminates by position, not presence
# ---------------------------------------------------------------------------


def test_checker_exits_zero_when_log_call_is_outside_window(tmp_path: Path) -> None:
    """A logging call placed in `eeprom28c_write_init` BETWEEN the emit and
    wait calls -- i.e. outside both the emitter body and the completion-poll
    body, exactly the span Phase 118's report lines occupy -- must NOT fail
    the gate. Both bodies are present (post-D-06 the resolver fails closed
    with no bodies at all, which is a different, correctly-non-vacuous
    failure mode covered by test 6); this case proves the gate discriminates
    by position, not by mere presence of a LOG_* call anywhere in the file.
    """
    src = tmp_path / "out_of_window.cpp"
    src.write_text(
        "static void eeprom28c_emit_command_sequence(firestarter_handle_t* handle, const byte_flip_t* sequence, size_t length) {\n"
        "    for (size_t i = 0; i < length; i++) {\n"
        "        handle->firestarter_set_data(handle, sequence[i].address, sequence[i].byte);\n"
        "    }\n"
        "}\n"
        "\n"
        "static void eeprom28c_wait_for_sdp_completion(firestarter_handle_t* handle) {\n"
        "    delay(10);\n"
        "}\n"
        "\n"
        "void eeprom28c_write_init(firestarter_handle_t* handle) {\n"
        "    eeprom28c_emit_command_sequence(handle, EEPROM_SDP_DISABLE, 6);\n"
        "    LOG_INFO_ID(MSG_DEBUG);  // outside both windows -- allowed\n"
        "    eeprom28c_wait_for_sdp_completion(handle);\n"
        "}\n"
    )
    result = _run_checker({"FIRESTARTER_SDP_SRC": str(src)})
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on a log call OUTSIDE both "
        f"windows.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout


# ---------------------------------------------------------------------------
# Test 4: comment-not-a-call control -- not a substring grep
# ---------------------------------------------------------------------------


def test_checker_exits_zero_when_log_macro_name_is_only_in_a_comment(
    tmp_path: Path,
) -> None:
    """A logging macro name appearing only inside a comment WITHIN the
    emitter body must NOT fail the gate -- proves comment-stripping still
    holds under D-06's new window and this checker is not a bare substring
    grep (Phase-109 SAFE-02, Phase-110 lesson). This is no longer purely
    hypothetical: the real `eeprom28c_wait_for_sdp_completion` body carries
    an in-body comment naming a logging macro (`eeprom_28c.cpp:267-268`),
    which is now inside the scanned region on production source too."""
    src = tmp_path / "comment_not_a_call.cpp"
    src.write_text(
        "static void eeprom28c_emit_command_sequence(firestarter_handle_t* handle, const byte_flip_t* sequence, size_t length) {\n"
        "    // Do NOT call LOG_INFO_ID(...) here -- it would perturb timing.\n"
        "    /* LOG_ERROR_ID_U32(MSG_ERR_EEPROM_TIMEOUT, 0); is forbidden too */\n"
        "    for (size_t i = 0; i < length; i++) {\n"
        "        handle->firestarter_set_data(handle, sequence[i].address, sequence[i].byte);\n"
        "    }\n"
        "}\n"
        "\n"
        "static void eeprom28c_wait_for_sdp_completion(firestarter_handle_t* handle) {\n"
        "    delay(10);\n"
        "}\n"
        "\n"
        "void eeprom28c_write_init(firestarter_handle_t* handle) {\n"
        "    eeprom28c_emit_command_sequence(handle, EEPROM_SDP_DISABLE, 6);\n"
        "    eeprom28c_wait_for_sdp_completion(handle);\n"
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
# Test 6: fail-closed leg -- write_init + poll body present, emitter body absent
# ---------------------------------------------------------------------------


def test_checker_fails_closed_when_emitter_body_is_absent(tmp_path: Path) -> None:
    """A source where `eeprom28c_write_init` and `eeprom28c_wait_for_sdp_
    completion`'s body both exist but the `eeprom28c_emit_command_sequence`
    DEFINITION is missing must exit non-zero, with a message telling the
    maintainer to add the new anchor/name rather than delete the gate
    (T-118-01-RENAME). Renamed from
    `test_checker_fails_closed_when_emit_anchor_is_absent`: D-06 resolves
    the emitter by brace-matched function body, not by anchor pattern, so
    the old name (and the old anchor-absent-only fixture shape) no longer
    describes what this case proves."""
    src = tmp_path / "no_emitter_body.cpp"
    src.write_text(
        "static void eeprom28c_wait_for_sdp_completion(firestarter_handle_t* handle) {\n"
        "    delay(10);\n"
        "}\n"
        "\n"
        "void eeprom28c_write_init(firestarter_handle_t* handle) {\n"
        "    eeprom28c_wait_for_sdp_completion(handle);\n"
        "}\n"
    )
    result = _run_checker({"FIRESTARTER_SDP_SRC": str(src)})
    assert result.returncode != 0, (
        f"checker exited 0 on a source missing the emitter body.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "add the new anchor" in result.stderr


# ---------------------------------------------------------------------------
# Test 7 (new): completion-poll-body negative -- proves the union's second
# half is actually scanned, not just the emitter half
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_log_planted_in_completion_poll_body(
    tmp_path: Path,
) -> None:
    """A logging call planted inside `eeprom28c_wait_for_sdp_completion`'s
    body (with a clean emitter) must fail the gate and the failure output
    must name the poll-body line. Without this case D-06's two-body union
    could silently collapse to "scan the emitter only" and nothing would
    catch it (T-118-01-HALFWINDOW)."""
    src = tmp_path / "log_in_poll_body.cpp"
    src.write_text(
        "static void eeprom28c_emit_command_sequence(firestarter_handle_t* handle, const byte_flip_t* sequence, size_t length) {\n"
        "    for (size_t i = 0; i < length; i++) {\n"
        "        handle->firestarter_set_data(handle, sequence[i].address, sequence[i].byte);\n"
        "    }\n"
        "}\n"
        "\n"
        "static void eeprom28c_wait_for_sdp_completion(firestarter_handle_t* handle) {\n"
        "    delay(10);\n"
        "    LOG_INFO_ID(MSG_DEBUG);  // PLANTED VIOLATION -- inside the poll body\n"
        "}\n"
        "\n"
        "void eeprom28c_write_init(firestarter_handle_t* handle) {\n"
        "    eeprom28c_emit_command_sequence(handle, EEPROM_SDP_DISABLE, 6);\n"
        "    eeprom28c_wait_for_sdp_completion(handle);\n"
        "}\n"
    )
    src_text = src.read_text(encoding="utf-8")
    planted_line = _line_number_of_marker(src_text, "PLANTED VIOLATION")

    result = _run_checker({"FIRESTARTER_SDP_SRC": str(src)})
    assert result.returncode == 1, (
        f"checker exited {result.returncode} (expected 1) on a log call "
        f"planted inside the completion-poll body.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert f"line {planted_line}" in result.stdout, (
        f"Expected the FAIL: output to name the poll-body planted line "
        f"({planted_line}) but got:\n{result.stdout}"
    )
    assert "LOG_INFO_ID" in result.stdout
