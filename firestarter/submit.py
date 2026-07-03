"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Community Chip-Validation Submission Flow (v1.21 Phase 113)

This module is ORCHESTRATOR-ONLY (SAFE-02, milestone non-regression
invariant): it sets no VPP, builds no wire/protocol command dict, and adds
no firmware dispatch entry. Filing a report to the maintainer's tracker --
via `gh` shell-out or a prefilled browser URL -- is a submission concern,
not a hardware path. It imports no serial-transport or hardware-manager
class and calls no `EpromOperator` method.

Two-tier `--submit` flow (SUB-01): `gh issue create` (stdin body, no
length cap) when `gh` is present on PATH and authenticated, else a
prefilled `issues/new` browser URL whose *encoded* byte length is
measured and whose fenced JSON block is dropped as it approaches
GitHub's ~8 KB server cap (D-05). Every submitted report is sanitized
(SUB-02) -- a recursive scrub of every string leaf in `to_dict()`'s
output for home-dir paths, serial device names, `/tmp` paths, and the
current username -- and carries a dedup fingerprint (SUB-03) in its
issue title.

`SUBMIT_REPO` is a hardcoded module constant (D-01): the target repo is
NEVER inferred from cwd or a git remote, so a community tester's own fork
never receives their own report.
"""

from __future__ import annotations

import base64
import copy
import getpass
import re
import shutil  # noqa: F401 -- consumed by gh_available (Task 3)
import subprocess  # noqa: F401 -- consumed by gh_available/submit_via_gh (Task 3)
import webbrowser  # noqa: F401 -- consumed by Plan 03's submit_via_browser
from typing import Any
from urllib.parse import (  # noqa: F401 -- consumed by build_issue_url (Task 2)
    quote,
    urlencode,
)

# ---------------------------------------------------------------------------
# Module constants (D-01, D-05)
# ---------------------------------------------------------------------------

SUBMIT_REPO = "henols/firestarter_app"  # D-01: hardcoded, never remote-inferred
GSD_INBOX_LABEL = "gsd-inbox"

# Encoded-URL byte thresholds (D-05): escalate (drop fenced JSON) past this,
# hard-stop (never open the browser) past the hard cap.
_URL_ESCALATE_BYTES = 7500
_URL_HARD_CAP_BYTES = 8000

# ---------------------------------------------------------------------------
# PII / path scrub regexes (SUB-02, module constants) -- backstop over the
# to_dict() field whitelist. A missed vector fails OPEN (leaks) -- every
# vector below is proven by its own test in tests/test_submit.py (A3).
# ---------------------------------------------------------------------------

_SCRUBS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"/home/[^/\s:]+"), "/home/<user>"),
    (re.compile(r"/Users/[^/\s:]+"), "/Users/<user>"),
    (re.compile(r"C:\\Users\\[^\\\s:]+", re.IGNORECASE), r"C:\\Users\\<user>"),
    (re.compile(r"/dev/tty(ACM|USB)\d+"), "/dev/tty<redacted>"),
    (re.compile(r"/dev/tty\.[\w-]+"), "/dev/tty<redacted>"),  # macOS
    (re.compile(r"\bCOM\d+\b"), "COM<redacted>"),  # Windows serial
    (re.compile(r"/tmp/[^\s:]+"), "/tmp/<redacted>"),
]


def _scrub_string(value: str, *, user_pattern: re.Pattern[str] | None) -> str:
    """Apply every `_SCRUBS` pair, then the optional username pattern."""
    for pattern, replacement in _SCRUBS:
        value = pattern.sub(replacement, value)
    if user_pattern is not None:
        value = user_pattern.sub("<user>", value)
    return value


def _scrub_value(value: Any, *, user_pattern: re.Pattern[str] | None) -> Any:
    if isinstance(value, str):
        return _scrub_string(value, user_pattern=user_pattern)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    if isinstance(value, dict):
        return {k: _scrub_value(v, user_pattern=user_pattern) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(v, user_pattern=user_pattern) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub_value(v, user_pattern=user_pattern) for v in value)
    return value


def sanitize_dict(d: dict[str, Any], *, user: str | None = None) -> dict[str, Any]:
    """Recursively deep-scrub every string leaf of `d` (SUB-02).

    Returns a NEW dict built from a deep copy -- the caller's `d` is never
    mutated. Every string leaf anywhere in the nested dict/list/tuple
    structure is scrubbed for home-dir paths, serial device names, `/tmp`
    paths, and (when `len(user) >= 3`) the current username as a whole-word
    match. A `bytes` leaf is base64-encoded to a `str` (forward-looking --
    no `bytes` field exists in `to_dict()` today).
    """
    working = copy.deepcopy(d)
    resolved_user = user if user is not None else getpass.getuser()
    user_pattern = (
        re.compile(rf"\b{re.escape(resolved_user)}\b")
        if len(resolved_user) >= 3
        else None
    )
    return _scrub_value(working, user_pattern=user_pattern)  # type: ignore[return-value]
