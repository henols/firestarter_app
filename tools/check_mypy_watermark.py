#!/usr/bin/env python3
"""tools/check_mypy_watermark.py — CI mypy gate (D-10, Phase 37).

Run mypy, count errors, fail if error count exceeds the watermark.
Watermark is stored as a comment in [tool.mypy] in pyproject.toml:
    # mypy_error_watermark = 44

Exit codes:
  0 — error count is at or below the watermark (gate passes)
  1 — error count exceeds the watermark (new errors introduced; gate fails)
  2 — configuration error (watermark comment not found) OR mypy failed to
      produce a parseable result (a broken type checker must fail the gate,
      never be mistaken for a clean tree)
"""
import re
import subprocess
import sys
from pathlib import Path

# Resolve the repo root from this file's location so the gate behaves
# identically regardless of the caller's working directory (CR-WR-03).
# Layout: <repo>/tools/check_mypy_watermark.py -> repo root is two parents up.
REPO_ROOT = Path(__file__).resolve().parent.parent


def get_watermark() -> int:
    """Read the mypy_error_watermark integer from pyproject.toml."""
    text = (REPO_ROOT / "pyproject.toml").read_text()
    # Anchor to a comment line (optional leading whitespace then '#') so the
    # watermark is read from its declaration, not an incidental match (WR-04).
    m = re.search(
        r"^\s*#\s*mypy_error_watermark\s*=\s*(\d+)", text, flags=re.MULTILINE
    )
    if not m:
        print(
            "ERROR: mypy_error_watermark comment not found in [tool.mypy]",
            file=sys.stderr,
        )
        sys.exit(2)
    return int(m.group(1))


def count_mypy_errors() -> int:
    """Run mypy and return the error count.

    Distinguishes three outcomes so a broken type checker can never be mistaken
    for a clean tree (gate-bypass guard, CR-01):
      - a 'Found N errors' line     -> N  (mypy's errors-found path, exit 1)
      - exit 0 / 'Success'          -> 0  (genuinely clean tree)
      - anything else               -> mypy ran but crashed or changed its
                                       output; sys.exit(2) (tool/config error)
                                       rather than silently reporting 0 errors
                                       and passing the gate.
    """
    result = subprocess.run(
        ["mypy", "firestarter/", "tests/"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    output = result.stdout + result.stderr
    m = re.search(r"Found (\d+) errors?", output)
    if m:
        return int(m.group(1))
    if result.returncode == 0 or "Success: no issues found" in output:
        return 0
    print(
        "ERROR: mypy did not report a parseable error count "
        f"(exit {result.returncode}). Treating as a tool/config failure, "
        "not a clean tree.\n" + output.strip(),
        file=sys.stderr,
    )
    sys.exit(2)


def main() -> None:
    """Entry point: compare mypy error count to watermark and exit accordingly."""
    watermark = get_watermark()
    count = count_mypy_errors()
    print(f"mypy errors: {count} (watermark: {watermark})")
    if count > watermark:
        print(
            f"FAIL: {count} errors exceeds watermark {watermark}. New errors introduced."
        )
        sys.exit(1)
    elif count < watermark:
        print(
            f"INFO: {count} errors — {watermark - count} below watermark. "
            "Lower watermark in pyproject.toml."
        )
    else:
        print("OK: error count at watermark.")


if __name__ == "__main__":
    main()
