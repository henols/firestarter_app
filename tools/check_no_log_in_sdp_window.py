"""
TRACE-03 third negative: structural scan proving no logging call sits inside
the `0x0D` SDP command-sequence timing window.

**Window redefinition.** An earlier revision of this checker brace-matched
`eeprom28c_write_init` and scanned the span *between* the command-emit call
site and the completion-wait call site. That span never looks inside
`eeprom28c_emit_command_sequence()` -- the function whose bare `set_data`
loop *is* the real inter-byte SDP timing window Datasheet t_BLC governs.
Scanning the call-site span instead of the function bodies meant the gate
was checking a window adjacent to the real one, not the real one.

The window is now the union of two brace-matched function bodies:

  * `eeprom28c_emit_command_sequence()` -- the SDP-disable command-sequence
    emitter (the six-write loop whose inter-byte timing t_BLC bounds).
  * `eeprom28c_wait_for_sdp_completion()` -- the completion poll that
    follows it.

The observation report lines legitimately sit in the OLD between-the-
call-sites span (before the emit call and after the wait call, inside
`eeprom28c_write_init` but outside both bodies above) -- this rewrite is
what makes that placement provably legitimate rather than merely convenient.

`eeprom28c_write_init`'s body is still brace-matched and still checked for
the pre-Phase-117 / Phase-117 emit and wait anchors (see
`_EMIT_ANCHOR_PATTERNS` / `_WAIT_ANCHOR_PATTERNS` below): that check is now a
secondary rename-tripwire, not the window-resolution mechanism -- if a
future refactor moves the emitter or the poll out of `write_init` entirely,
this still fails closed instead of silently scanning nothing meaningful.

**This checker keeps exactly ONE job -- the no-logging rule.** It does
NOT also assert that `AT28C_TBLC_MAX_US` is cited in a comment anywhere.
`_strip_comments` below deliberately blanks comment spans before the
deny-list scan runs, so a citation-presence check would need a second pass
over uncleaned text -- and the runtime t_BLC budget check is
strictly stronger evidence that the constant is load-bearing than a comment
citation would be. Do not add a citation scan here or in a sibling checker.

Scans `firestarter/src/proms/eeprom_28c.cpp`'s two resolved function bodies
and asserts zero logging-macro (`LOG_*`, `firestarter/include/logging_id.h`)
call sites appear in either one.

This is a genuinely-populated structural scan, NOT a hollow declared-empty
detector -- the exact tech-debt fate this project incurred with v1.12's
a hollow checker (one that could never fail because it asserted nothing
concrete). The paired pytest (`tests/test_check_no_log_in_sdp_window.py`)
proves this checker actually flips to non-zero on a committed planted
violation (`tests/fixtures/planted_log_in_window.cpp`, re-planted inside the
emitter body by this same Phase-118 commit), injected via the
`FIRESTARTER_SDP_SRC` env-override below (mirrors
`tools/check_dispatch.py`'s `FIRESTARTER_DB_FILE` seam and `tools/
check_devtest_orchestrator.py`'s `FIRESTARTER_DEVTEST_SRC` seam) -- the
project's mandatory anti-hollow contract.

`ast` does not apply here -- the scan target is C++, not Python -- so each
window is resolved via brace-matched structural text extraction, never a
bare substring grep: `eeprom_28c.cpp` carries prose comments describing this
exact sequence and its timing (see this function's own docstring above, and
the sibling checkers' anti-false-positive lessons recorded for Phase-109
the safety rules), and a loose pattern would false-positive on comment
text mentioning a logging macro by name. Comment spans (`//` and `/* */`)
are blanked out (length- and line-preserving) before the deny-list scan
runs, so a comment mentioning a logging macro is never mistaken for a call
-- this is now load-bearing on production source, not only on a temp
fixture: `eeprom28c_wait_for_sdp_completion`'s real body carries an in-body
comment (`eeprom_28c.cpp:267-268`) that names a logging macro, and that
comment is now inside the scanned region.

Fails closed on every degenerate input -- missing/unreadable source path,
either target function not found (or not brace-balanced), or either
`eeprom28c_write_init` anchor set matching zero times -- so a later rename
(e.g. an emitter replacement, now or in future) cannot silently
hollow this gate; each failure mode names the fix (add the new anchor/name)
rather than silently passing.

Exit codes:
  0 -- both function bodies were resolved and contain zero logging-macro
       call sites (PASS: line printed, naming the resolved source path and
       both resolved line ranges).
  1 -- at least one logging-macro call site was found inside either body
       (FAIL: per-violation summary printed with line numbers), OR either
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
# without editing the real, clean eeprom_28c.cpp (anti-hollow contract).
FIRESTARTER_SDP_SRC = os.environ.get("FIRESTARTER_SDP_SRC", _DEFAULT_SDP_SRC)

# The function whose body IS the real SDP inter-byte timing window.
_EMITTER_FUNC_NAME = "eeprom28c_emit_command_sequence"
# The function whose body is the completion poll that follows the emitter.
_POLL_FUNC_NAME = "eeprom28c_wait_for_sdp_completion"
# Kept for the secondary rename-tripwire assertion only (see
# _resolve_windows) -- window resolution no longer brace-matches this
# function to compute the scanned span.
_FUNC_NAME = "eeprom28c_write_init"

# eeprom28c_emit_sdp_sequence_timed() -- the
# shared micros()-bracket-plus-report-pair helper both the SDP-disable
# (eeprom28c_write_init / eeprom28c_sdp_unlock_execute) and the new SDP-enable
# (eeprom28c_sdp_lock_execute) sequences call -- MUST NEVER be added as a
# third scanned window (a third name in _EMITTER_FUNC_NAME/_POLL_FUNC_NAME,
# or a third _find_function_body call in _resolve_windows/scan). That
# helper's body contains LOG_ID / LOG_ID_U32 / LOG_WARN_ID_U32 calls BY
# DESIGN -- it is the report/measurement wrapper AROUND the
# emit call, not the timing window itself. The real inter-byte SDP timing
# window this checker exists to protect remains exactly
# eeprom28c_emit_command_sequence's body (_EMITTER_FUNC_NAME above), which
# is still shared by both sequences and still scanned; scanning the helper
# too would turn every one of its by-design report lines into a false
# FAIL:.
#
# Two further tripwires this checker's own resolver depends on, recorded
# here so a future editor does not have to rediscover them (RESEARCH F-M):
#   1. _func_def_pattern (above) requires the return type to be literally
#      `void` -- if eeprom28c_emit_command_sequence were ever changed to
#      return e.g. `bool`, window resolution for _EMITTER_FUNC_NAME would
#      silently stop matching and _resolve_windows would fail closed with
#      a "not found (or not brace-balanced)" ValueError (the correct, safe
#      failure mode -- but worth knowing why, rather than treating it as a
#      mystery).
#   2. Step 2 of window resolution (the poll body) fails closed the same
#      way if eeprom28c_wait_for_sdp_completion is ever deleted or renamed
#      without a matching update to _POLL_FUNC_NAME.


def _func_def_pattern(func_name: str) -> re.Pattern[str]:
    """Build a function-DEFINITION-only pattern (body-opening `{`), never
    matching a forward-declaration prototype (which ends in `;`).

    Two properties are load-bearing and must be preserved by any future
    edit: the leading `\\b` sits before `void`, so this still matches a
    `static void ...` definition (both targets are `static`); and the
    trailing `\\{` is what excludes the `;`-terminated forward declarations
    a few lines above each definition in the real file. `[^)]*` tolerates
    any parameter list, including the emitter's three-argument signature.
    """
    return re.compile(r"\bvoid\s+" + re.escape(func_name) + r"\s*\([^)]*\)\s*\{")


# Command-emit anchors -- the call(s) that kick off the SDP command sequence,
# used ONLY by the secondary write_init rename-tripwire in _resolve_windows,
# not by window resolution itself. The first entry matched the pre-Phase-117
# flash_execute_command(EEPROM_SDP_DISABLE) call site. A later change
# replaced that emitter with the 0x0D-local eeprom28c_emit_command_sequence()
# driven through handle->firestarter_set_data, so the second entry matched
# eeprom28c_write_init's direct call site. A shared
# eeprom28c_emit_sdp_sequence_timed() helper was then factored out, which
# eeprom28c_write_init now calls instead of eeprom28c_emit_command_sequence
# directly (the helper itself calls the emitter), so the third entry below is
# what matches on today's tree. Per the anti-hollow contract this tuple is
# APPEND-ONLY: every superseded pattern stays so a revert or a partial
# re-introduction is still anchored, and a future rename that leaves this
# tuple matching zero times fails closed (see _resolve_windows below) rather
# than silently passing.
_EMIT_ANCHOR_PATTERNS = (
    re.compile(r"flash_execute_command\s*\(\s*EEPROM_SDP_DISABLE\s*\)"),
    re.compile(
        r"eeprom28c_emit_command_sequence\s*\(\s*handle\s*,\s*EEPROM_SDP_DISABLE\b"
    ),
    # eeprom28c_write_init's call site is now the
    # shared timed-emit helper, not the emitter directly.
    re.compile(
        r"eeprom28c_emit_sdp_sequence_timed\s*\(\s*handle\s*,\s*EEPROM_SDP_DISABLE\b"
    ),
)

# Completion-wait anchors -- same "secondary tripwire only" role as
# _EMIT_ANCHOR_PATTERNS above. The first entry matched the pre-Phase-117
# eeprom28c_wait_for_write( call site; that function was deleted
# outright (its inverted read-back was replaced with an unconditional
# t_WC wait plus a bounded DQ6 toggle poll, and the page path was split
# into eeprom28c_wait_for_page_write), so the second entry is what matches
# on today's tree. Same append-only anti-hollow contract as
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
# needing to enumerate each macro name by hand. It also matches the bare
# unconditional LOG_ID( / LOG_ID_U32( forms the report lines use, so
# no pattern change is needed for that spelling to stay in coverage.
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


def _find_function_body(cleaned_text: str, func_name: str) -> tuple[int, int] | None:
    """Locate `func_name`'s definition and brace-match its body.

    Returns `(body_start, body_end)` character offsets into `cleaned_text`
    (inclusive of both the opening and closing brace), or `None` if the
    function definition cannot be located or is not brace-balanced. `ast`
    does not apply -- the target is C++ -- so this is a brace-matched
    structural extraction, never a regex/grep over the whole file.
    """
    m = _func_def_pattern(func_name).search(cleaned_text)
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


def _resolve_windows(
    cleaned_text: str,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Resolve the two function bodies that make up the SDP timing window
: the emitter body and the completion-poll body.

    Also runs a secondary, rename-tripwire assertion: `eeprom28c_write_init`
    must still be brace-matchable and its body must still contain one
    command-emit anchor followed by one completion-wait anchor
    (`_EMIT_ANCHOR_PATTERNS` / `_WAIT_ANCHOR_PATTERNS`). Window resolution no
    longer depends on these anchors to compute the scanned span, but they
    stay in force as append-only rename tripwires: a future refactor that
    moves the emitter or the poll call out of `write_init` entirely fails
    closed here rather than silently no-oping.

    Returns `(emitter_body, poll_body)`, each an inclusive
    `(body_start, body_end)` character-offset pair. Raises `ValueError`
    (fail-closed) with a maintainer-facing message -- naming the specific
    function that could not be resolved and pointing at the fix -- on any
    unresolvable input.
    """
    emitter_body = _find_function_body(cleaned_text, _EMITTER_FUNC_NAME)
    if emitter_body is None:
        raise ValueError(
            f"{_EMITTER_FUNC_NAME}() not found (or not brace-balanced) in "
            "source -- if the emitter was renamed or replaced, add the new "
            "anchor/name for _EMITTER_FUNC_NAME in "
            "check_no_log_in_sdp_window.py rather than deleting this gate"
        )

    poll_body = _find_function_body(cleaned_text, _POLL_FUNC_NAME)
    if poll_body is None:
        raise ValueError(
            f"{_POLL_FUNC_NAME}() not found (or not brace-balanced) in "
            "source -- if the completion-wait function was renamed or "
            "replaced, add the new anchor/name for _POLL_FUNC_NAME in "
            "check_no_log_in_sdp_window.py rather than deleting this gate"
        )

    write_init_body = _find_function_body(cleaned_text, _FUNC_NAME)
    if write_init_body is None:
        raise ValueError(
            f"{_FUNC_NAME}() not found (or not brace-balanced) in source -- "
            "cannot verify the emit/wait anchors are still wired together; "
            "add the new anchor/name for _FUNC_NAME in "
            "check_no_log_in_sdp_window.py rather than deleting this gate"
        )
    wi_start, wi_end = write_init_body
    wi_text = cleaned_text[wi_start : wi_end + 1]

    emit_anchor = _find_anchor(_EMIT_ANCHOR_PATTERNS, wi_text, 0)
    if emit_anchor is None:
        raise ValueError(
            f"no command-emit anchor found inside {_FUNC_NAME}() -- if the "
            "emitter was renamed or replaced, add the new anchor to "
            "_EMIT_ANCHOR_PATTERNS in check_no_log_in_sdp_window.py rather "
            "than deleting this gate"
        )

    wait_anchor = _find_anchor(_WAIT_ANCHOR_PATTERNS, wi_text, emit_anchor.end())
    if wait_anchor is None:
        raise ValueError(
            f"no completion-wait anchor found after the command-emit anchor "
            f"inside {_FUNC_NAME}() -- if the wait call was renamed or "
            "replaced, add the new anchor to _WAIT_ANCHOR_PATTERNS in "
            "check_no_log_in_sdp_window.py rather than deleting this gate"
        )

    return emitter_body, poll_body


def scan(
    source_text: str,
) -> tuple[list[tuple[int, str]], tuple[int, int], tuple[int, int]]:
    """Resolve the SDP timing window (the emitter body plus the
    completion-poll body) in `source_text` and scan both for
    logging-macro call sites.

    Returns `(violations, emitter_range, poll_range)` on success, where
    `violations` is a list of `(line_number, macro_name)` pairs (empty on a
    clean window, ordered by file position since the emitter body always
    precedes the poll body in source) and `emitter_range` / `poll_range` are
    each an inclusive `(start_line, end_line)` pair. Raises `ValueError`
    (fail-closed) with a maintainer-facing message when either window
    cannot be resolved at all.
    """
    cleaned = _strip_comments(source_text)
    emitter_body, poll_body = _resolve_windows(cleaned)

    violations: list[tuple[int, str]] = []
    ranges = []
    for body_start, body_end in (emitter_body, poll_body):
        body_text = cleaned[body_start : body_end + 1]
        for m in _LOG_CALL_PATTERN.finditer(body_text):
            abs_pos = body_start + m.start()
            macro_name = m.group(0)[:-1].rstrip()  # drop trailing '(' + whitespace
            violations.append((_line_of(source_text, abs_pos), macro_name))
        ranges.append(
            (_line_of(source_text, body_start), _line_of(source_text, body_end))
        )

    emitter_range, poll_range = ranges
    return violations, emitter_range, poll_range


def main() -> int:
    """Entry point: resolve the window in FIRESTARTER_SDP_SRC and scan it.

    Prints a PASS: line (naming the resolved path plus both resolved line
    ranges) and returns 0 on a clean window; prints a FAIL: per-violation
    summary and returns 1 on any logging-macro hit inside either body; prints
    an ERROR: message and returns 1 (fail-closed) if either window cannot be
    resolved at all.
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
        violations, emitter_range, poll_range = scan(source_text)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    range_desc = (
        f"emitter lines {emitter_range[0]}-{emitter_range[1]}, "
        f"completion-poll lines {poll_range[0]}-{poll_range[1]}"
    )

    if violations:
        print(
            f"FAIL: {len(violations)} logging call(s) found inside the SDP "
            f"timing window ({path}, {range_desc}):"
        )
        for line_no, macro in violations[:20]:
            print(f"  line {line_no}: {macro}(...)")
        if len(violations) > 20:
            print(f"  ... and {len(violations) - 20} more")
        return 1

    print(f"PASS: no logging call in SDP timing window ({path}, {range_desc})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
