#!/usr/bin/env python3
"""tools/check_mypy_watermark.py — CI mypy gate (D-10, Phase 37).

Run mypy, count errors, fail if error count exceeds the watermark.
Watermark is stored as a comment in [tool.mypy] in pyproject.toml:
    # mypy_error_watermark = 44

Exit codes:
  0 — error count is at or below the watermark (gate passes)
  1 — error count exceeds the watermark (new errors introduced; gate fails)
  2 — configuration error (watermark comment not found in pyproject.toml)
"""
import re
import subprocess
import sys
from pathlib import Path


def get_watermark() -> int:
    """Read the mypy_error_watermark integer from pyproject.toml."""
    text = Path("pyproject.toml").read_text()
    m = re.search(r"#\s*mypy_error_watermark\s*=\s*(\d+)", text)
    if not m:
        print(
            "ERROR: mypy_error_watermark comment not found in [tool.mypy]",
            file=sys.stderr,
        )
        sys.exit(2)
    return int(m.group(1))


def count_mypy_errors() -> int:
    """Run mypy on the project and return the error count. Returns 0 on success."""
    result = subprocess.run(
        ["mypy", "firestarter/", "tests/"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    m = re.search(r"Found (\d+) errors?", output)
    return int(m.group(1)) if m else 0


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
