"""
Tests for check_is_memory_cmd_no_ifdef.py (LOCK-03's textual oracle, Phase
119 Plan 03, D-04's second half).

This is the mandatory anti-hollow pairing for a new firmware-source-scanning
gate: a checker tool with no negative-fixture test is exactly the failure
mode this project incurred with v1.12's GATE-03 (a declared-empty detector
that could never fail because nothing concrete was asserted). Every
planted-violation test below injects a REAL subprocess-level source file via
the `FIRESTARTER_CMD_ADMISSION_SRC` env-override -- never an in-process
synthetic -- so a passing test suite proves the checker itself (not the
test) fails on a real violation.

Coverage:
  1. Clean control: the checker exits 0 against the real, unmodified
     firestarter.h -- is_memory_cmd()'s body carries no preprocessor
     conditional and enumerates exactly the eight expected commands.
  2. Committed planted violation -- the load-bearing anti-hollow proof: the
     checker, pointed at the committed
     tests/fixtures/planted_ifdef_in_predicate.h via
     FIRESTARTER_CMD_ADMISSION_SRC, exits 1 and names the planted line
     (derived from the fixture at test time via _line_number_of_marker, not
     a hardcoded literal, so a future re-plant cannot silently desync the
     assertion from the fixture).
  3. Out-of-body control: a temp header with a legitimate build-configuration
     conditional OUTSIDE the predicate body (mirroring the real
     firestarter.h, where the CMD_DEV_* pair is conditionally defined a few
     lines above the predicate) exits 0 -- proves the gate discriminates by
     position, not by mere presence of a conditional anywhere in the file.
  4. Comment-not-a-violation control: a temp header whose predicate body is
     clean but whose rationale comment inside the body mentions the
     conditional by name exits 0 -- pins _strip_comments as load-bearing,
     mirroring the real predicate's own rationale comment.
  5. Wrong command set: a temp header with a conditional-free body that
     omits one expected command exits 1 and names the offending command in
     stdout -- proves assertion (b) is real and independent of assertion (a).
  6. Fail-closed inputs: two sub-assertions in one case -- a nonexistent
     FIRESTARTER_CMD_ADMISSION_SRC path returns 1 with ERROR: on stderr; and
     a temp header with no is_memory_cmd definition at all returns 1 with
     ERROR: on stderr naming the fix. Neither may pass.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Absolute path to the firestarter_app directory (cwd-independent), mirrors
# tests/test_check_no_log_in_sdp_window.py:59.
_FA_DIR = Path(__file__).parent.parent
_FIXTURE = _FA_DIR / "tests" / "fixtures" / "planted_ifdef_in_predicate.h"
_FIRESTARTER_H = _FA_DIR.parent / "firestarter" / "include" / "firestarter.h"

# The firmware sub-repo may be absent in standalone CI (firestarter_app
# checked out alone -- beta-release.yml has no sibling firestarter checkout).
# Mirrors the FW_ABSENT skip pattern in test_sdp_table_parity.py /
# test_revision_constants_parity.py / test_gen_validation_header.py. Only
# the clean-source control below touches the real firmware file; every
# other case in this module drives a fixture or temp file via
# FIRESTARTER_CMD_ADMISSION_SRC and needs no firmware checkout.
_FW_ABSENT = not _FIRESTARTER_H.exists()
_requires_fw = pytest.mark.skipif(
    _FW_ABSENT,
    reason="firestarter firmware checkout absent (firestarter.h)",
)


def _run_checker(
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_overrides or {})}
    return subprocess.run(
        [sys.executable, "tools/check_is_memory_cmd_no_ifdef.py"],
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


@_requires_fw
def test_checker_exits_zero_on_clean_source() -> None:
    """python tools/check_is_memory_cmd_no_ifdef.py must exit 0 against the
    real, unmodified firestarter.h -- is_memory_cmd()'s body on today's tree
    carries no preprocessor conditional and enumerates exactly the eight
    expected commands."""
    result = _run_checker()
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on the real, clean source.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout, (
        f"Expected 'PASS:' in output but got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Test 2: planted violation (the anti-hollow proof, LOCK-03)
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_committed_planted_violation() -> None:
    """The committed fixture (tests/fixtures/planted_ifdef_in_predicate.h)
    MUST fail the gate, and the failure output must name the planted line.

    This is the load-bearing assertion of the whole task: a real,
    subprocess-level, permanently re-runnable proof that the checker can
    fail (D-04's textual oracle). An exit-code-only assertion would not be
    enough on its own -- pairing it with the output-content assertion below
    is what makes this a genuine anti-hollow proof rather than a coincidence
    (e.g. the checker crashing for an unrelated reason). The expected line
    number is derived from the fixture itself, not hardcoded, so a future
    re-plant cannot silently desync this assertion from the fixture.
    """
    assert _FIXTURE.is_file(), f"committed fixture missing: {_FIXTURE}"
    fixture_text = _FIXTURE.read_text(encoding="utf-8")
    planted_line = _line_number_of_marker(fixture_text, "PLANTED VIOLATION")

    result = _run_checker({"FIRESTARTER_CMD_ADMISSION_SRC": str(_FIXTURE)})
    assert result.returncode == 1, (
        f"checker exited {result.returncode} (expected 1) on the committed "
        f"planted violation.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert f"line {planted_line}" in result.stdout, (
        f"Expected the FAIL: output to name the planted line ({planted_line}) "
        f"but got:\n{result.stdout}"
    )
    assert "DEV_TOOLS" in result.stdout


# ---------------------------------------------------------------------------
# Test 3: out-of-body control -- discriminates by POSITION, not presence
# ---------------------------------------------------------------------------


def test_checker_exits_zero_when_conditional_is_outside_predicate_body(
    tmp_path: Path,
) -> None:
    """A legitimate build-configuration conditional OUTSIDE the predicate
    body -- mirroring the real firestarter.h, where the CMD_DEV_* pair is
    conditionally defined a few lines above is_memory_cmd() -- must NOT fail
    the gate. Without this case the gate could be a whole-file grep and
    nobody would know."""
    src = tmp_path / "out_of_body.h"
    src.write_text(
        "#include <stdint.h>\n"
        "\n"
        "#define CMD_READ 1\n"
        "#define CMD_WRITE 2\n"
        "#define CMD_ERASE 3\n"
        "#define CMD_BLANK_CHECK 4\n"
        "#define CMD_CHECK_CHIP_ID 5\n"
        "#define CMD_VERIFY 6\n"
        "\n"
        "#ifdef DEV_TOOLS\n"
        "#define CMD_DEV_ADDRESS 7\n"
        "#define CMD_DEV_REGISTER 8\n"
        "#endif\n"
        "\n"
        "#define CMD_SDP_UNLOCK 9\n"
        "#define CMD_SDP_LOCK 10\n"
        "\n"
        "static inline bool is_memory_cmd(uint8_t cmd) {\n"
        "    switch (cmd) {\n"
        "        case CMD_READ:\n"
        "        case CMD_WRITE:\n"
        "        case CMD_ERASE:\n"
        "        case CMD_BLANK_CHECK:\n"
        "        case CMD_CHECK_CHIP_ID:\n"
        "        case CMD_VERIFY:\n"
        "        case CMD_SDP_UNLOCK:\n"
        "        case CMD_SDP_LOCK:\n"
        "            return true;\n"
        "        default:\n"
        "            return false;\n"
        "    }\n"
        "}\n"
    )
    result = _run_checker({"FIRESTARTER_CMD_ADMISSION_SRC": str(src)})
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on a conditional OUTSIDE the "
        f"predicate body.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout


# ---------------------------------------------------------------------------
# Test 4: comment-not-a-violation control -- not a substring grep
# ---------------------------------------------------------------------------


def test_checker_exits_zero_when_conditional_is_only_named_in_a_comment(
    tmp_path: Path,
) -> None:
    """A rationale comment INSIDE the predicate body that names a
    build-configuration conditional (e.g. explaining why one is no longer
    needed) must NOT fail the gate -- proves comment-stripping still holds
    and this checker is not a bare substring grep. This is no longer purely
    hypothetical: the real predicate's own rationale comment (immediately
    above its definition in firestarter.h) names the #ifdef DEV_TOOLS
    conditional it replaced."""
    src = tmp_path / "comment_not_a_violation.h"
    src.write_text(
        "#include <stdint.h>\n"
        "\n"
        "#define CMD_READ 1\n"
        "#define CMD_WRITE 2\n"
        "#define CMD_ERASE 3\n"
        "#define CMD_BLANK_CHECK 4\n"
        "#define CMD_CHECK_CHIP_ID 5\n"
        "#define CMD_VERIFY 6\n"
        "#define CMD_SDP_UNLOCK 9\n"
        "#define CMD_SDP_LOCK 10\n"
        "\n"
        "static inline bool is_memory_cmd(uint8_t cmd) {\n"
        "    // Replaces the old #ifdef DEV_TOOLS-conditional ordinal guard --\n"
        "    // this predicate names no conditional and no DEV_TOOLS-only macro.\n"
        "    /* the string '#ifdef DEV_TOOLS' appears only in prose here */\n"
        "    switch (cmd) {\n"
        "        case CMD_READ:\n"
        "        case CMD_WRITE:\n"
        "        case CMD_ERASE:\n"
        "        case CMD_BLANK_CHECK:\n"
        "        case CMD_CHECK_CHIP_ID:\n"
        "        case CMD_VERIFY:\n"
        "        case CMD_SDP_UNLOCK:\n"
        "        case CMD_SDP_LOCK:\n"
        "            return true;\n"
        "        default:\n"
        "            return false;\n"
        "    }\n"
        "}\n"
    )
    result = _run_checker({"FIRESTARTER_CMD_ADMISSION_SRC": str(src)})
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on a conditional named only in "
        f"a comment.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout


# ---------------------------------------------------------------------------
# Test 5: wrong command set -- proves assertion (b) independent of (a)
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_wrong_command_set(tmp_path: Path) -> None:
    """A conditional-free body that omits one expected command
    (CMD_VERIFY) must fail the gate, and the offending name must appear in
    stdout -- proves assertion (b) fires independently of assertion (a)."""
    src = tmp_path / "wrong_command_set.h"
    src.write_text(
        "#include <stdint.h>\n"
        "\n"
        "#define CMD_READ 1\n"
        "#define CMD_WRITE 2\n"
        "#define CMD_ERASE 3\n"
        "#define CMD_BLANK_CHECK 4\n"
        "#define CMD_CHECK_CHIP_ID 5\n"
        "#define CMD_VERIFY 6\n"
        "#define CMD_SDP_UNLOCK 9\n"
        "#define CMD_SDP_LOCK 10\n"
        "\n"
        "static inline bool is_memory_cmd(uint8_t cmd) {\n"
        "    switch (cmd) {\n"
        "        case CMD_READ:\n"
        "        case CMD_WRITE:\n"
        "        case CMD_ERASE:\n"
        "        case CMD_BLANK_CHECK:\n"
        "        case CMD_CHECK_CHIP_ID:\n"
        "        case CMD_SDP_UNLOCK:\n"
        "        case CMD_SDP_LOCK:\n"
        "            return true;\n"
        "        default:\n"
        "            return false;\n"
        "    }\n"
        "}\n"
    )
    result = _run_checker({"FIRESTARTER_CMD_ADMISSION_SRC": str(src)})
    assert result.returncode == 1, (
        f"checker exited {result.returncode} (expected 1) on a body missing "
        f"CMD_VERIFY.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "CMD_VERIFY" in result.stdout, (
        f"Expected the offending command name in stdout but got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Test 6: fail-closed inputs -- neither may pass
# ---------------------------------------------------------------------------


def test_checker_fails_closed_on_unresolvable_inputs(tmp_path: Path) -> None:
    """Two sub-assertions, both fail-closed (T-119-03-SEAM / T-119-03-RENAME):

    (a) FIRESTARTER_CMD_ADMISSION_SRC pointed at a nonexistent path must
        exit non-zero with ERROR: on stderr.
    (b) A source with no is_memory_cmd definition at all must exit non-zero
        with ERROR: on stderr, naming the fix rather than silently passing.
    """
    missing = tmp_path / "does_not_exist.h"
    result_missing = _run_checker({"FIRESTARTER_CMD_ADMISSION_SRC": str(missing)})
    assert result_missing.returncode != 0, (
        f"checker exited 0 on a missing source path.\n"
        f"stdout:\n{result_missing.stdout}\nstderr:\n{result_missing.stderr}"
    )
    assert "ERROR:" in result_missing.stderr

    no_predicate = tmp_path / "no_predicate.h"
    no_predicate.write_text(
        "#include <stdint.h>\n\n#define CMD_READ 1\n// is_memory_cmd() was renamed or removed\n"
    )
    result_absent = _run_checker({"FIRESTARTER_CMD_ADMISSION_SRC": str(no_predicate)})
    assert result_absent.returncode != 0, (
        f"checker exited 0 on a source missing the predicate definition.\n"
        f"stdout:\n{result_absent.stdout}\nstderr:\n{result_absent.stderr}"
    )
    assert "ERROR:" in result_absent.stderr
    assert "rather than deleting this gate" in result_absent.stderr
