#!/usr/bin/env python3
"""tools/check_mypy_watermark.py — CI mypy gate (D-10, Phase 37; hardened Phase 131 GATE-01..04).

Run mypy, count errors, fail if error count exceeds the watermark.
Watermark is stored as a comment in [tool.mypy] in pyproject.toml:
    # mypy_error_watermark = 44

Exit codes:
  0 — error count is at or below the watermark (gate passes)
  1 — error count exceeds the watermark (new errors introduced; gate fails)
  2 — the run cannot be trusted as a complete, well-formed mypy run. Any of:
      - `result.returncode` is neither 0 nor 1 (a mypy crash/abort)
      - mypy printed a config-rejection diagnostic (`: [mypy...]: `) — a
        rejected config value is never a clean run, even if mypy proceeded
        and returned 0 or 1 anyway
      - the output carries no parseable completion clause at all (neither
        `Success: no issues found in N source files` nor `Found N errors in
        M files (checked K source files)`) — this is the truncated-run
        shape, which emits `(errors prevented further checking)` and no
        `checked` clause
      - the completion clause reports fewer than `MIN_CHECKED_SOURCE_FILES`
        checked files — a run that silently checked a subset of the tree
        wears a plausible-looking error count

Guard order (in `classify_mypy_result`) is load-bearing, mirroring
`check_no_exists_proxy.py`'s never-vacuous-before-missing-target discipline:
each guard is hoisted above the guard it would otherwise be vacuously
satisfied by. Returncode is consulted BEFORE any error-count regex — this
single reordering is GATE-01/GATE-02's fix. See `.planning/research/STACK.md`
§1e for the bug this replaces.
"""

import re
import subprocess
import sys
from pathlib import Path

# Resolve the repo root from this file's location so the gate behaves
# identically regardless of the caller's working directory (CR-WR-03).
# Layout: <repo>/tools/check_mypy_watermark.py -> repo root is two parents up.
REPO_ROOT = Path(__file__).resolve().parent.parent

# GATE-03: a floor to be raised when the tree grows, never lowered to
# accommodate a smaller run. If a legitimate deletion drops the tree below
# it, lower it in the same commit as that deletion, with the new measured
# number. Prior value: none -- first value, set by Phase 131 plan 131-01
# from the measured 120 of the CI-replica run.
MIN_CHECKED_SOURCE_FILES = 120

# Matches mypy's completion summary line for an errors-found run, e.g.
# "Found 69 errors in 17 files (checked 120 source files)". Requiring the
# "(checked N source files)" clause is what makes the truncated-run shape
# ("... (errors prevented further checking)", no checked clause) unparseable
# rather than silently matching on "Found N errors" alone (GATE-02).
_FOUND_RE = re.compile(
    r"^Found (\d+) errors? in \d+ files? \(checked (\d+) source files?\)$",
    re.MULTILINE,
)

# Matches mypy's completion summary line for a genuinely clean run, e.g.
# "Success: no issues found in 120 source files".
_CLEAN_RE = re.compile(
    r"^Success: no issues found in (\d+) source files?$",
    re.MULTILINE,
)

# Matches mypy's config-diagnostic prefix, e.g.
# "pyproject.toml: [mypy]: python_version: 3.9 is not supported (must be
# 3.10 or higher)". This is a distinct guard, not implied by the returncode
# guard: measured, a rejected python_version in a config *file* is a
# non-fatal note, so the run proceeds and can reach exit 1 with a perfectly
# well-formed completion clause.
_CONFIG_REJECTION_RE = re.compile(
    r"^.*: \[mypy[^\]]*\]: .*$",
    re.MULTILINE,
)

# GATE-04: reworded so the remedy is conditional on a verified-complete run,
# never an unconditional invitation to bypass the gate by lowering the
# watermark (the fail-open the previous wording invited).
_INFO_TEMPLATE = (
    "INFO: {count} errors -- {below} below watermark ({watermark}). The "
    "watermark may be lowered to {count}, but only if this run is complete: "
    "this run's mypy invocation passed both the completion-clause guard and "
    "the MIN_CHECKED_SOURCE_FILES coverage floor, which is the evidence of "
    "completeness. Lower it in the same commit as the fixes that reduced "
    "the count -- never to make a failing gate pass."
)


def get_watermark() -> int:
    """Read the mypy_error_watermark integer from pyproject.toml."""
    text = (REPO_ROOT / "pyproject.toml").read_text()
    # Anchor to a comment line (optional leading whitespace then '#') so the
    # watermark is read from its declaration, not an incidental match (WR-04).
    m = re.search(r"^\s*#\s*mypy_error_watermark\s*=\s*(\d+)", text, flags=re.MULTILINE)
    if not m:
        print(
            "ERROR: mypy_error_watermark comment not found in [tool.mypy]",
            file=sys.stderr,
        )
        sys.exit(2)
    return int(m.group(1))


def mypy_argv() -> list[str]:
    """The exact argv mypy is invoked with.

    GATE-04: `sys.executable -m mypy`, never a bare `mypy` resolved from
    `PATH` -- binds the checker to the interpreter running the gate, so
    ambient PATH order can never silently swap which mypy (and therefore
    which error population) runs. Built at call time so `sys.executable` is
    never frozen at import.
    """
    return [sys.executable, "-m", "mypy", "firestarter/", "tests/"]


def run_mypy() -> subprocess.CompletedProcess:
    """Thin runner: invoke mypy and return the raw CompletedProcess.

    Deliberately no env-var seam overriding this argv (D-01): an env var
    that could swap which program runs here would be a bypass added to the
    one gate whose entire sin was being bypassable.
    """
    return subprocess.run(
        mypy_argv(),
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def classify_mypy_result(returncode: int, output: str) -> int:
    """Pure classifier: decide whether a mypy run is trustworthy and, if so,
    return its error count. No subprocess, no file I/O, no environment reads.

    Guards run in this order, each hoisted above the guard it would
    otherwise be vacuously satisfied by (mirrors
    `check_no_exists_proxy.py:305-334`'s never-vacuous-before-missing-target
    ordering):

      1. `returncode` not in (0, 1) -- a mypy crash/abort. exit 2.
      2. a config-rejection diagnostic is present -- exit 2, independent of
         both the returncode and the completion clause.
      3. `Success: no issues found in N source files` -- count 0.
      4. `Found N errors in M files (checked K source files)` -- count N.
      5. neither completion clause matched -- exit 2 (the truncated-run
         shape: `(errors prevented further checking)`, no `checked` clause).
      6. `checked` below MIN_CHECKED_SOURCE_FILES -- exit 2.
      7. print the coverage line and return the count.
    """
    if returncode not in (0, 1):
        print(
            f"ERROR: mypy exited {returncode}, which is neither the "
            "clean-run (0) nor errors-found (1) exit code. Treating as a "
            "tool/config failure, not a clean tree.\n" + output.strip(),
            file=sys.stderr,
        )
        sys.exit(2)

    config_match = _CONFIG_REJECTION_RE.search(output)
    if config_match:
        print(
            "ERROR: mypy rejected a config value -- "
            f"{config_match.group(0).strip()!r}. A rejected config value "
            "means this is not a clean, fully-configured run, even though "
            "the run completed with a well-formed exit code. Fix the "
            "config; don't ignore the note.\n" + output.strip(),
            file=sys.stderr,
        )
        sys.exit(2)

    clean_match = _CLEAN_RE.search(output)
    if clean_match:
        count = 0
        checked = int(clean_match.group(1))
    else:
        found_match = _FOUND_RE.search(output)
        if found_match:
            count = int(found_match.group(1))
            checked = int(found_match.group(2))
        else:
            print(
                "ERROR: mypy produced no parseable completion clause -- "
                "neither 'Success: no issues found in N source files' nor "
                "'Found N errors in M files (checked K source files)' is "
                "present. This is exactly the truncated-run shape (e.g. "
                "'(errors prevented further checking)' with no completion "
                "clause) wearing a plausible exit code. Treating as a "
                "tool/config failure, not a clean tree.\n" + output.strip(),
                file=sys.stderr,
            )
            sys.exit(2)

    if checked < MIN_CHECKED_SOURCE_FILES:
        print(
            f"ERROR: mypy checked only {checked} source file(s), below the "
            f"MIN_CHECKED_SOURCE_FILES floor of {MIN_CHECKED_SOURCE_FILES}. "
            "A run that checked far fewer files than the tree contains is a "
            "truncated run wearing a plausible error count. Raise the floor "
            "only when the tree legitimately grows; never lower it to "
            "accommodate a smaller run.\n" + output.strip(),
            file=sys.stderr,
        )
        sys.exit(2)

    print(f"checked {checked} source files")
    return count


def enforce_watermark(count: int, watermark: int) -> None:
    """Pure: compare an already-classified error count to the watermark."""
    print(f"mypy errors: {count} (watermark: {watermark})")
    if count > watermark:
        print(
            f"FAIL: {count} errors exceeds watermark {watermark}. New errors introduced."
        )
        sys.exit(1)
    elif count < watermark:
        print(
            _INFO_TEMPLATE.format(
                count=count, below=watermark - count, watermark=watermark
            )
        )
    else:
        print("OK: error count at watermark.")


def main() -> None:
    """Entry point: run mypy, classify the result, compare to the watermark."""
    watermark = get_watermark()
    result = run_mypy()
    count = classify_mypy_result(result.returncode, result.stdout + result.stderr)
    enforce_watermark(count, watermark)


if __name__ == "__main__":
    main()
