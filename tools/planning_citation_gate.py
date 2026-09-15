#!/usr/bin/env python3
"""Fail the build when GSD planning commentary appears in product source.

/workspaces/CLAUDE.md states, as a hard rule, that no planning-process
commentary may appear in product source: no phase numbers, no plan numbers,
no decision or requirement ids, no `.planning/` paths. The reader of this
pip package does not have the planning directory and never will, so those
identifiers resolve to nothing for them.

WHAT THIS SCANS

Comment text only. The scanner consumes string and character literals before
it looks for comments, so a URL in a string, or a fixture holding comment-like
bytes, can never read as a comment. Python docstrings are string expressions,
not comments, and are never scanned: Click command docstrings are the user's
`--help` text.

WHY IT IS ANCHORED THE WAY IT IS

An earlier detector bound its token list directly to the comment marker
(`(//|/\\*|^\\s*\\*|#)\\s*(Phase|Plan|D-\\d|...)`). Deleting the leading label
is the normal repair, and that repair pushes any remaining citation on the
same line out of marker-adjacency -- so the repair removed lines from the
detector's view without removing the citations. The detector then reported
success as a consequence of its own partial repair. Measured: it saw 345 of
718 citation lines before the repair and 94 of 357 after, reporting 73%
removal against an actual 50%.

This scanner therefore matches anywhere inside the comment text and never
binds a token to a marker.

EXIT CODES
  0  clean
  1  at least one citation found
  2  the scan was vacuous (zero files matched) -- a fail-closed guard
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

C_LIKE = {".c", ".cpp", ".cc", ".h", ".hpp", ".inc", ".ino"}
PY_LIKE = {".py"}
SCAN_EXT = C_LIKE | PY_LIKE

SKIP_DIRS = {
    ".git", ".pio", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "build", "dist",
}

RULES = (
    # A reference to the planning tree, in any form.
    ("planning-path", re.compile(r"\.planning\b")),
    # A GSD artifact filename -- a phase document, or a top-level planning
    # document such as the roadmap or the requirements file.
    ("gsd-artifact", re.compile(
        r"\b\d{2,3}-(?:\d{2}-)?"
        r"(?:CONTEXT|RESEARCH|PLAN|SUMMARY|VERIFICATION|VALIDATION|PATTERNS"
        r"|DISCUSSION-LOG|LEARNINGS|SPEC|NONREGRESSION|EVAL-REVIEW)\b"
        r"|\b(?:ROADMAP|REQUIREMENTS|STATE|PROJECT|BACKLOG)\.md\b")),
    # A phase, plan, task or milestone reference by number.
    # Two digits minimum: a GSD phase number is zero-padded, while a
    # one-digit "phase 1" is ordinary protocol prose (INIT / MAIN / END).
    ("phase-or-plan-ref", re.compile(
        r"\b[Pp]hases?\s+\d{2,3}\b"
        r"|\b[Pp]lans?\s+\d{2,3}(?:-\d{2})?\b"
        r"|\b[Tt]asks?\s+\d{2,3}-\d{2}\b"
        r"|\b[Mm]ilestones?\s+v?\d+\.\d{2}\b")),
    # A GSD decision, finding, constraint or lock id.
    ("decision-id", re.compile(
        r"(?<![A-Za-z0-9_])(?:D|C|F|L|S|OD|WR|LOCK)-\d{1,2}(?![0-9A-Za-z_])")),
    # A GSD requirement id: an all-caps prefix, a hyphen, two digits.
    ("requirement-id", re.compile(
        r"(?<![A-Za-z0-9_-])[A-Z]{2,12}-\d{2}(?![0-9A-Za-z_])")),
)

# Technical vocabulary that the id patterns above would otherwise claim.
# Every entry is a published standard or a hardware byte sequence, never a
# planning identifier. Adding an entry is a reviewed change to this gate --
# there is deliberately no in-source escape comment, because an escape
# comment is itself the thing the rule forbids.
TECHNICAL_VOCABULARY = frozenset({
    "AA-55",      # the SDP / flash unlock byte pair 0xAA 0x55
    "CRC-8", "CRC-16", "CRC-32", "CRC-64",
    "SHA-1", "SHA-256", "SHA-512", "MD-5",
    "RS-232", "RS-422", "RS-485", "EIA-232",
    "UTF-8", "UTF-16", "UTF-32", "ISO-8859",
    "IEEE-754", "USB-2", "USB-3", "PY32-07",
})


def c_comments(src: str):
    """Yield (1-based line, comment text) for every C-family comment."""
    i, n, line = 0, len(src), 1
    while i < n:
        ch = src[i]
        if ch == "\n":
            line += 1
            i += 1
        elif ch in "\"'":
            quote, i = ch, i + 1
            while i < n:
                if src[i] == "\\":
                    if i + 1 < n and src[i + 1] == "\n":
                        line += 1
                    i += 2
                    continue
                if src[i] == "\n":
                    line += 1
                    i += 1
                    break
                if src[i] == quote:
                    i += 1
                    break
                i += 1
        elif ch == "/" and src.startswith("//", i):
            end = src.find("\n", i)
            end = n if end < 0 else end
            yield line, src[i:end]
            i = end
        elif ch == "/" and src.startswith("/*", i):
            end = src.find("*/", i + 2)
            end = n if end < 0 else end + 2
            block = src[i:end]
            for offset, text in enumerate(block.split("\n")):
                yield line + offset, text
            line += block.count("\n")
            i = end
        else:
            i += 1


def py_comments(src: str):
    """Yield (1-based line, comment text) for every Python `#` comment.

    Docstrings are skipped whole: they are string expressions, and Click
    command docstrings are user-facing `--help` text.
    """
    i, n, line = 0, len(src), 1
    while i < n:
        ch = src[i]
        if ch == "\n":
            line += 1
            i += 1
        elif ch in "\"'":
            triple = src[i:i + 3]
            if triple in ('"""', "'''"):
                end = src.find(triple, i + 3)
                end = n if end < 0 else end + 3
                line += src[i:end].count("\n")
                i = end
                continue
            quote, i = ch, i + 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "\n":
                    line += 1
                    i += 1
                    break
                if src[i] == quote:
                    i += 1
                    break
                i += 1
        elif ch == "#":
            end = src.find("\n", i)
            end = n if end < 0 else end
            yield line, src[i:end]
            i = end
        else:
            i += 1


def citations(text: str):
    """Return the rule names that fire on one comment line."""
    fired = []
    for name, pattern in RULES:
        for match in pattern.finditer(text):
            if match.group(0) in TECHNICAL_VOCABULARY:
                continue
            fired.append((name, match.group(0)))
            break
    return fired


def scan(path: Path):
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    reader = py_comments if path.suffix in PY_LIKE else c_comments
    found = []
    for lineno, text in reader(src):
        fired = citations(text)
        if fired:
            found.append((lineno, fired[0][0], fired[0][1], text.strip()))
    return found


def walk(roots, excludes):
    for root in roots:
        if root.is_file():
            if root.suffix in SCAN_EXT:
                yield root
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix not in SCAN_EXT or not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if any(pattern in str(path) for pattern in excludes):
                continue
            yield path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fail when GSD planning commentary appears in source.")
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--exclude", action="append", default=[],
                        help="substring of a path to skip; repeatable")
    parser.add_argument("--min-files", type=int, default=1,
                        help="fail with exit 2 below this many scanned files")
    args = parser.parse_args(argv)

    self_path = str(Path(__file__).resolve())
    files = [f for f in walk(args.roots, args.exclude)
             if str(f.resolve()) != self_path]
    if len(files) < args.min_files:
        print(f"VACUOUS: scanned {len(files)} files, expected at least "
              f"{args.min_files}. The gate proves nothing. Check the paths.",
              file=sys.stderr)
        return 2

    total, dirty = 0, 0
    for path in files:
        hits = scan(path)
        if not hits:
            continue
        dirty += 1
        for lineno, rule, token, text in hits:
            total += 1
            print(f"{path}:{lineno}: [{rule}: {token}] {text[:160]}")

    if total:
        print(f"\nFAIL: {total} planning-citation comment lines in {dirty} of "
              f"{len(files)} scanned files.\nCLAUDE.md forbids planning "
              f"commentary in product source. Delete the comment, or restate "
              f"it without the identifier.", file=sys.stderr)
        return 1
    print(f"OK: {len(files)} files scanned, no planning citations.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
