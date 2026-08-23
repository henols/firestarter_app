"""
LOCK-03 textual oracle: structural scan proving firmware's `is_memory_cmd()`
admission predicate carries no build-configuration conditional in its body
and enumerates exactly the expected `CMD_*` memory commands (Phase 119
Plan 03, D-04's second half; grown from eight to nine names by Phase 151 /
LOCK-02, which added `CMD_LOCK_STATUS`).

**Why a second oracle is needed at all.** Plan 119-02 already proved
`is_memory_cmd()` *behaves* identically in two build configurations (an
exhaustive 256-value truth table run in both `[env:native]` and the
no-`DEV_TOOLS` `[env:native_nodevtools]`). That is a SEMANTIC proof: it can
never distinguish "the predicate has no conditional" from "the predicate has
a conditional whose two branches happen to agree" -- a conditional gated on
some OTHER macro (not `DEV_TOOLS`) could pass that truth table vacuously
while still defeating D-02's intent. This checker is the TEXTUAL oracle D-04
demands: it reads the predicate's actual source text and asserts the absence
of any preprocessor conditional, independent of what the predicate computes.

Scans `firestarter/include/firestarter.h`'s `static inline bool
is_memory_cmd(uint8_t cmd)` definition and asserts BOTH:

  (a) No preprocessor conditional (`#if`, `#ifdef`, `#ifndef`, `#elif`,
      `#else`, `#endif`) appears inside the predicate's body. Every kind of
      conditional is checked, not only ones naming `DEV_TOOLS` -- a narrower
      check would be evadable by conditioning on a different macro, which
      would defeat D-02's purpose just as completely.
  (b) The body's `CMD_*` identifiers, as a SET, equal exactly the frozen
      nine-name expected set (`_EXPECTED_CMD_NAMES` below). Missing and
      unexpected names are reported separately, by name. `CMD_DEV_ADDRESS`
      and `CMD_DEV_REGISTER` must never appear -- they are conditionally
      defined in the firmware header and are exactly what the predicate
      exists to NOT depend on.

This is a genuinely-populated structural scan, NOT a hollow declared-empty
detector -- the exact tech-debt fate this project incurred with v1.12's
GATE-03 (a checker that could never fail because it asserted nothing
concrete). The paired pytest (`tests/test_check_is_memory_cmd_no_ifdef.py`)
proves this checker actually flips to non-zero on a committed planted
violation (`tests/fixtures/planted_ifdef_in_predicate.h`), injected via the
`FIRESTARTER_CMD_ADMISSION_SRC` env-override below (mirrors `tools/
check_no_log_in_sdp_window.py`'s `FIRESTARTER_SDP_SRC` seam and `tools/
check_dispatch.py`'s `FIRESTARTER_DB_FILE` seam) -- the project's mandatory
anti-hollow contract.

`ast` does not apply here -- the scan target is C++, not Python -- so the
predicate's body is resolved via brace-matched structural text extraction,
never a bare substring grep: the predicate carries its own rationale comment
naming the very conditional it removed (see firestarter.h's block comment
immediately above the definition), and a loose pattern would false-positive
on that comment text. Comment spans (`//` and `/* */`) are blanked out
(length- and line-preserving) before either deny-list scan runs, so a
comment mentioning a conditional or a command name is never mistaken for the
real thing -- this is directly load-bearing on production source, not just
defensive hygiene, because that rationale comment exists specifically to
explain the conditional the predicate no longer needs.

Fails closed on every degenerate input -- missing/unreadable source path, or
the predicate definition not found (or not brace-balanced) -- so a future
rename of `is_memory_cmd()` cannot silently hollow this gate; each failure
mode names the fix (update this checker's definition pattern) rather than
silently passing.

Exit codes:
  0 -- the predicate body was resolved, contains zero preprocessor
       conditionals, and its `CMD_*` set matches the expected eight exactly
       (PASS: line printed, naming the resolved source path and the
       resolved body line range).
  1 -- a preprocessor conditional was found inside the body, OR the `CMD_*`
       set diverges from the expected eight (FAIL: per-violation summary
       printed with line numbers / offending names), OR the predicate could
       not be resolved at all (ERROR: message printed to stderr) --
       fail-closed, never a silent pass.
"""

from __future__ import annotations

import os
import re
import sys

# Module-top path constant (mirrors tools/check_no_log_in_sdp_window.py:92-112's
# env-overridable path-constant idiom, itself mirroring tools/check_dispatch.py).
_HERE = os.path.dirname(__file__)
_DEFAULT_CMD_ADMISSION_SRC = os.path.join(
    _HERE, "..", "..", "firestarter", "include", "firestarter.h"
)

# Env-override seam: lets the paired pytest point this checker at a
# deliberately-violating fixture file (tests/fixtures/planted_ifdef_in_predicate.h)
# without editing the real, clean firestarter.h (anti-hollow contract, D-04).
# This seam is FAIL-CLOSED: a path that does not exist is an ERROR, never a
# silent pass -- see main() below.
FIRESTARTER_CMD_ADMISSION_SRC = os.environ.get(
    "FIRESTARTER_CMD_ADMISSION_SRC", _DEFAULT_CMD_ADMISSION_SRC
)

# The predicate this gate reads.
_PREDICATE_FUNC_NAME = "is_memory_cmd"

# The frozen expected command set (D-02/D-04). Adding a memory command is a
# DELIBERATE act that must edit this line -- it is not auto-derived from the
# header, because the whole point of this gate is to catch an
# accidental/unreviewed enumeration drift, not just mirror it.
# CMD_DEV_ADDRESS and CMD_DEV_REGISTER must NEVER appear here: they are
# conditionally defined (#ifdef DEV_TOOLS) in the firmware header, and naming
# them in this predicate would recreate exactly the divergence is_memory_cmd()
# exists to remove.
#
# This set grew from eight names to nine, adding
# CMD_LOCK_STATUS -- the protection-status read, a memory command because it
# is issued through firestarter_get_data, set only by configure_memory().
_EXPECTED_CMD_NAMES = frozenset(
    {
        "CMD_READ",
        "CMD_WRITE",
        "CMD_ERASE",
        "CMD_BLANK_CHECK",
        "CMD_CHECK_CHIP_ID",
        "CMD_VERIFY",
        "CMD_SDP_UNLOCK",
        "CMD_SDP_LOCK",
        "CMD_LOCK_STATUS",
    }
)


def _predicate_def_pattern() -> re.Pattern[str]:
    """Build a DEFINITION-only pattern for `is_memory_cmd()`, never matching a
    `;`-terminated forward declaration.

    Cannot reuse check_no_log_in_sdp_window.py's `_func_def_pattern`: that
    pattern hardcodes a literal `void` return type, but `is_memory_cmd()`
    returns `bool`. This pattern instead pins the exact token sequence
    `static`, `inline`, `bool`, the name, a parenthesised parameter list, then
    the body-opening `{` -- with tolerant whitespace between tokens. The
    trailing `\\{` is what excludes a `;`-terminated forward declaration. The
    pattern deliberately pins `static inline` (not just any `bool` return):
    RESEARCH F-F makes header-inline placement load-bearing (a `.cpp`
    definition would not link into any native test binary, so Plan 119-02's
    two-env truth-table suite could not exist), so a predicate that lost
    `static inline` would already be a regression this gate should refuse to
    silently accept as a match.
    """
    return re.compile(
        r"\bstatic\s+inline\s+bool\s+"
        + re.escape(_PREDICATE_FUNC_NAME)
        + r"\s*\([^)]*\)\s*\{"
    )


# Preprocessor conditional deny list: any of these directives inside the
# predicate body defeats D-02's purpose, not only ones conditioned on
# DEV_TOOLS. A narrower check (e.g. matching only `#ifdef DEV_TOOLS`) would be
# evadable by conditioning on a different macro instead, so every conditional
# directive is checked, regardless of which macro (if any) it names.
_PREPROCESSOR_CONDITIONAL_PATTERN = re.compile(
    r"^[ \t]*#[ \t]*(if|ifdef|ifndef|elif|else|endif)\b.*$", re.MULTILINE
)

# Every `CMD_*` identifier occurring in the stripped body -- used to build the
# observed set compared against _EXPECTED_CMD_NAMES.
_CMD_IDENTIFIER_PATTERN = re.compile(r"\bCMD_[A-Z0-9_]+\b")


def _strip_comments(text: str) -> str:
    """Blank out `//` and `/* */` comment spans, preserving both string
    length and newline positions so every character offset (and therefore
    every computed line number) in the returned string maps 1:1 onto `text`.

    This is directly load-bearing here, not defensive hygiene: the real
    predicate carries a rationale comment (firestarter.h, immediately above
    the definition) that names the very build-configuration conditional it
    removed ("...the #ifdef DEV_TOOLS-conditional ordinal admission guard
    had to be replaced by is_memory_cmd()..."). A naive substring/regex scan
    over uncleaned text would flag that comment's own prose as a violation.
    Do not "simplify" this stripping away -- it is what keeps the scan
    honest against the predicate's own documentation.
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
    """Locate `is_memory_cmd()`'s definition and brace-match its body.

    Returns `(body_start, body_end)` character offsets into `cleaned_text`
    (inclusive of both the opening and closing brace), or `None` if the
    definition cannot be located or is not brace-balanced. `ast` does not
    apply -- the target is C++ -- so this is a brace-matched structural
    extraction, never a regex/grep over the whole file.
    """
    m = _predicate_def_pattern().search(cleaned_text)
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


def scan(
    source_text: str,
) -> tuple[list[tuple[int, str]], set[str], set[str], tuple[int, int]]:
    """Resolve `is_memory_cmd()`'s body in `source_text` and run both
    assertions over it.

    Returns `(conditional_violations, missing_commands, unexpected_commands,
    body_range)`:
      - `conditional_violations` is a list of `(line_number, directive_text)`
        pairs (empty on a clean body), one per preprocessor conditional line
        found inside the body.
      - `missing_commands` / `unexpected_commands` are the set difference
        between the body's observed `CMD_*` identifiers and
        `_EXPECTED_CMD_NAMES` (both empty on an exact match).
      - `body_range` is an inclusive `(start_line, end_line)` pair for the
        resolved predicate body.

    Raises `ValueError` (fail-closed) with a maintainer-facing message when
    the predicate cannot be resolved at all.
    """
    cleaned = _strip_comments(source_text)
    body = _find_function_body(cleaned)
    if body is None:
        raise ValueError(
            f"{_PREDICATE_FUNC_NAME}() definition not found (or not "
            "brace-balanced) in source -- if the predicate was renamed or "
            "moved, update _predicate_def_pattern() in "
            "check_is_memory_cmd_no_ifdef.py rather than deleting this gate"
        )
    body_start, body_end = body
    body_text = cleaned[body_start : body_end + 1]

    conditional_violations: list[tuple[int, str]] = []
    for m in _PREPROCESSOR_CONDITIONAL_PATTERN.finditer(body_text):
        abs_pos = body_start + m.start()
        conditional_violations.append(
            (_line_of(source_text, abs_pos), m.group(0).strip())
        )

    observed_commands = {
        m.group(0) for m in _CMD_IDENTIFIER_PATTERN.finditer(body_text)
    }
    missing_commands = _EXPECTED_CMD_NAMES - observed_commands
    unexpected_commands = observed_commands - _EXPECTED_CMD_NAMES

    body_range = (
        _line_of(source_text, body_start),
        _line_of(source_text, body_end),
    )
    return conditional_violations, missing_commands, unexpected_commands, body_range


def main() -> int:
    """Entry point: resolve the predicate in FIRESTARTER_CMD_ADMISSION_SRC and
    scan its body.

    Prints a PASS: line (naming the resolved path plus the resolved body line
    range) and returns 0 when the body is conditional-free and its CMD_* set
    matches the expected eight exactly; prints a FAIL: summary and returns 1
    on any conditional or set mismatch; prints an ERROR: message and returns
    1 (fail-closed) if the predicate cannot be resolved at all, or if the
    source path does not exist.
    """
    path = FIRESTARTER_CMD_ADMISSION_SRC
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
        conditional_violations, missing, unexpected, body_range = scan(source_text)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    range_desc = f"predicate body lines {body_range[0]}-{body_range[1]}"

    if conditional_violations or missing or unexpected:
        print(
            f"FAIL: {_PREDICATE_FUNC_NAME}() violates D-02/D-04 ({path}, {range_desc}):"
        )
        for line_no, directive_text in conditional_violations[:20]:
            print(f"  line {line_no}: {directive_text}")
        if len(conditional_violations) > 20:
            print(f"  ... and {len(conditional_violations) - 20} more")
        if missing:
            print(f"  missing expected command(s): {', '.join(sorted(missing))}")
        if unexpected:
            print(f"  unexpected command(s) found: {', '.join(sorted(unexpected))}")
        return 1

    print(
        f"PASS: {_PREDICATE_FUNC_NAME}() has no preprocessor conditional and "
        f"enumerates exactly the {len(_EXPECTED_CMD_NAMES)} expected commands "
        f"({path}, {range_desc})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
