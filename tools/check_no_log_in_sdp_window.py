"""
TRACE-03 third negative: structural scan proving no logging call sits inside
the `0x0D` SDP command-sequence timing window (Phase 116 Plan 04, D-04 third
bullet).

Scans `firestarter/src/proms/eeprom_28c.cpp`'s `eeprom28c_write_init`
function and asserts zero logging-macro (`LOG_*`, `firestarter/include/
logging_id.h`) call sites appear between the command-emit anchor
(`eeprom28c_emit_command_sequence(handle, EEPROM_SDP_DISABLE, ...)`) and the
completion-wait anchor (`eeprom28c_wait_for_sdp_completion(...)`) that
immediately follows it. Both anchor tuples below are append-only and still
carry their pre-Phase-117 predecessors (`flash_execute_command(
EEPROM_SDP_DISABLE)` and `eeprom28c_wait_for_write(...)`). Phase 118's
OBS-01 will add report lines around this sequence -- this gate is what keeps
them *around* it rather than *inside* it (a log call issued mid-sequence
would itself perturb the inter-byte timing window the SDP-disable write
cycle depends on).

This is a genuinely-populated structural scan, NOT a hollow declared-empty
detector -- the exact tech-debt fate this project incurred with v1.12's
GATE-03 (a checker that could never fail because it asserted nothing
concrete). The paired pytest (`tests/test_check_no_log_in_sdp_window.py`)
proves this checker actually flips to non-zero on a committed planted
violation (`tests/fixtures/planted_log_in_window.cpp`), injected via the
`FIRESTARTER_SDP_SRC` env-override below (mirrors
`tools/check_dispatch.py`'s `FIRESTARTER_DB_FILE` seam and `tools/
check_devtest_orchestrator.py`'s `FIRESTARTER_DEVTEST_SRC` seam) -- the
project's mandatory anti-hollow contract.

`ast` does not apply here -- the scan target is C++, not Python -- so the
window is resolved via brace-matched structural text extraction, never a
bare substring grep: `eeprom_28c.cpp` carries prose comments describing this
exact sequence and its timing (see this function's own docstring above, and
the sibling checkers' anti-false-positive lessons recorded for Phase-109
SAFE-02 and Phase-110), and a loose pattern would false-positive on comment
text mentioning a logging macro by name. Comment spans (`//` and `/* */`)
are blanked out (length- and line-preserving) before the deny-list scan runs,
so a comment mentioning a logging macro is never mistaken for a call.

Fails closed on every degenerate input -- missing/unreadable source path,
`eeprom28c_write_init` not found, or either anchor set matching zero times --
so a later rename (e.g. Phase 117's emitter replacement) cannot silently
hollow this gate; each failure mode names the fix (add the new anchor to the
relevant tuple below) rather than silently passing.

Exit codes:
  0 -- the SDP timing window was resolved and contains zero logging-macro
       call sites (PASS: line printed, naming the resolved source path and
       line range).
  1 -- at least one logging-macro call site was found inside the window
       (FAIL: per-violation summary printed with line numbers), OR the
       window could not be resolved at all (ERROR: message printed to
       stderr) -- fail-closed, never a silent pass.
"""

import os
import re
import sys

# Module-top path constant (mirrors tools/check_dispatch.py:24-33's
# env-overridable path-constant idiom, and tools/check_devtest_orchestrator.py's
# FIRESTARTER_DEVTEST_SRC seam).
_HERE = os.path.dirname(__file__)
_DEFAULT_SDP_SRC = os.path.join(
    _HERE, "..", "..", "firestarter", "src", "proms", "eeprom_28c.cpp"
)

# Env-override seam: lets the paired pytest point this checker at a
# deliberately-violating fixture file (tests/fixtures/planted_log_in_window.cpp)
# without editing the real, clean eeprom_28c.cpp (anti-hollow contract, D-04).
FIRESTARTER_SDP_SRC = os.environ.get("FIRESTARTER_SDP_SRC", _DEFAULT_SDP_SRC)

_FUNC_NAME = "eeprom28c_write_init"

# Matches only the function DEFINITION (body-opening `{`), never the
# forward-declaration prototype a few lines above it in the real file (which
# ends in `;`).
_FUNC_DEF_PATTERN = re.compile(r"\bvoid\s+" + _FUNC_NAME + r"\s*\([^)]*\)\s*\{")

# Command-emit anchors -- the call(s) that kick off the SDP command sequence.
# The first entry matched the pre-Phase-117 flash_execute_command(
# EEPROM_SDP_DISABLE) call site. Phase 117 (FIX-01) replaced that emitter with
# the 0x0D-local eeprom28c_emit_command_sequence() driven through
# handle->firestarter_set_data, so the second entry is what matches on today's
# tree. Per the anti-hollow contract this tuple is APPEND-ONLY: the superseded
# pattern stays so a revert or a partial re-introduction is still anchored, and
# a future rename that leaves this tuple matching zero times fails closed (see
# _resolve_window below) rather than silently passing.
_EMIT_ANCHOR_PATTERNS = (
    re.compile(r"flash_execute_command\s*\(\s*EEPROM_SDP_DISABLE\s*\)"),
    re.compile(
        r"eeprom28c_emit_command_sequence\s*\(\s*handle\s*,\s*EEPROM_SDP_DISABLE\b"
    ),
)

# Completion-wait anchors -- the call that blocks until the SDP-disable write
# cycle completes, immediately following the emit anchor. The first entry
# matched the pre-Phase-117 eeprom28c_wait_for_write( call site; Phase 117
# deleted that function outright (FIX-02 replaced its inverted read-back with
# an unconditional t_WC wait plus a bounded DQ6 toggle poll, and FIX-06 split
# the page path into eeprom28c_wait_for_page_write), so the second entry is
# what matches on today's tree. Same append-only anti-hollow contract as
# _EMIT_ANCHOR_PATTERNS above.
_WAIT_ANCHOR_PATTERNS = (
    re.compile(r"eeprom28c_wait_for_write\s*\("),
    re.compile(r"eeprom28c_wait_for_sdp_completion\s*\("),
)

# Deny list: every logging-call macro this codebase defines
# (firestarter/include/logging_id.h). Every one of them shares the `LOG_`
# prefix followed immediately by an uppercase-or-underscore identifier and an
# opening paren (LOG_ID*, LOG_INFO_ID*, LOG_ERROR_ID*, LOG_WARN_ID*,
# LOG_OK_ID*, LOG_INIT_ID*, LOG_MAIN_ID*, LOG_END_ID*, LOG_DATA_ID*,
# LOG_DEBUG_ID_SUB*) -- this single pattern matches every one of them without
# needing to enumerate each macro name by hand.
_LOG_CALL_PATTERN = re.compile(r"\bLOG_[A-Z][A-Z0-9_]*\s*\(")


def _strip_comments(text: str) -> str:
    """Blank out `//` and `/* */` comment spans, preserving both string
    length and newline positions so every character offset (and therefore
    every computed line number) in the returned string maps 1:1 onto
    `text`. A bare substring grep is explicitly wrong for this scan (module
    docstring): eeprom_28c.cpp's own prose comments describe this exact
    sequence, so the deny-list regex must never see comment text as a call
    site, and the brace-matcher must never see a brace that only exists
    inside a comment.
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        two = text[i : i + 2]
        if two == "//":
            j = text.find("\n", i)
            if j == -1:
                j = n
            out.append(" " * (j - i))
            i = j
        elif two == "/*":
            j = text.find("*/", i + 2)
            if j == -1:
                j = n
            else:
                j += 2
            out.append("".join(c if c == "\n" else " " for c in text[i:j]))
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _find_function_body(cleaned_text: str) -> tuple[int, int] | None:
    """Locate `eeprom28c_write_init`'s definition and brace-match its body.

    Returns `(body_start, body_end)` character offsets into `cleaned_text`
    (inclusive of both the opening and closing brace), or `None` if the
    function definition cannot be located or is not brace-balanced. `ast`
    does not apply -- the target is C++ -- so this is a brace-matched
    structural extraction, never a regex/grep over the whole file.
    """
    m = _FUNC_DEF_PATTERN.search(cleaned_text)
    if m is None:
        return None
    depth = 0
    body_start = m.end() - 1  # position of the matched '{'
    i = body_start
    n = len(cleaned_text)
    while i < n:
        ch = cleaned_text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return body_start, i
        i += 1
    return None


def _find_anchor(patterns: tuple, text: str, start: int):
    """Return the earliest match of any regex in `patterns` at or after
    `start` in `text`, or `None` if none of them match."""
    best = None
    for pattern in patterns:
        m = pattern.search(text, start)
        if m is not None and (best is None or m.start() < best.start()):
            best = m
    return best


def scan(source_text: str) -> tuple[list[tuple[int, str]], int, int]:
    """Resolve the SDP timing window in `source_text` and scan it for
    logging-macro call sites.

    Returns `(violations, window_start_line, window_end_line)` on success,
    where `violations` is a list of `(line_number, macro_name)` pairs (empty
    on a clean window). Raises `ValueError` (fail-closed) with a
    maintainer-facing message when the window cannot be resolved at all.
    """
    cleaned = _strip_comments(source_text)
    body = _find_function_body(cleaned)
    if body is None:
        raise ValueError(
            f"{_FUNC_NAME}() not found (or not brace-balanced) in source -- "
            "cannot resolve the SDP timing window"
        )
    body_start, body_end = body
    body_text = cleaned[body_start : body_end + 1]

    emit_match = _find_anchor(_EMIT_ANCHOR_PATTERNS, body_text, 0)
    if emit_match is None:
        raise ValueError(
            f"no command-emit anchor found inside {_FUNC_NAME}() -- if the "
            "emitter was renamed or replaced, add the new anchor to "
            "_EMIT_ANCHOR_PATTERNS in check_no_log_in_sdp_window.py rather "
            "than deleting this gate"
        )

    wait_match = _find_anchor(_WAIT_ANCHOR_PATTERNS, body_text, emit_match.end())
    if wait_match is None:
        raise ValueError(
            f"no completion-wait anchor found after the command-emit anchor "
            f"inside {_FUNC_NAME}() -- if the wait call was renamed or "
            "replaced, add the new anchor to _WAIT_ANCHOR_PATTERNS in "
            "check_no_log_in_sdp_window.py rather than deleting this gate"
        )

    window_start = body_start + emit_match.start()
    window_end = body_start + wait_match.start()
    window_text = cleaned[window_start:window_end]

    violations = []
    for m in _LOG_CALL_PATTERN.finditer(window_text):
        abs_pos = window_start + m.start()
        macro_name = m.group(0)[:-1].rstrip()  # drop trailing '(' + whitespace
        violations.append((_line_of(source_text, abs_pos), macro_name))

    window_start_line = _line_of(source_text, window_start)
    window_end_line = _line_of(source_text, window_end)
    return violations, window_start_line, window_end_line


def main() -> int:
    """Entry point: resolve the window in FIRESTARTER_SDP_SRC and scan it.

    Prints a PASS: line (naming the resolved path + line range) and returns
    0 on a clean window; prints a FAIL: per-violation summary and returns 1
    on any logging-macro hit inside the window; prints an ERROR: message and
    returns 1 (fail-closed) if the window cannot be resolved at all.
    """
    path = FIRESTARTER_SDP_SRC
    if not os.path.isfile(path):
        print(f"ERROR: source file not found: {path}", file=sys.stderr)
        return 1
    try:
        with open(path, encoding="utf-8") as f:
            source_text = f.read()
    except OSError as e:
        print(f"ERROR: could not read source file {path}: {e}", file=sys.stderr)
        return 1

    try:
        violations, start_line, end_line = scan(source_text)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if violations:
        print(
            f"FAIL: {len(violations)} logging call(s) found inside the SDP "
            f"timing window ({path}, lines {start_line}-{end_line}):"
        )
        for line_no, macro in violations[:20]:
            print(f"  line {line_no}: {macro}(...)")
        if len(violations) > 20:
            print(f"  ... and {len(violations) - 20} more")
        return 1

    print(
        f"PASS: no logging call in SDP timing window ({path}, "
        f"lines {start_line}-{end_line})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
