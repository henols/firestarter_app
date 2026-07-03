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
import json
import re
import shutil  # noqa: F401 -- consumed by gh_available (Task 3)
import subprocess  # noqa: F401 -- consumed by gh_available/submit_via_gh (Task 3)
import webbrowser  # noqa: F401 -- consumed by Plan 03's submit_via_browser
from typing import Any
from urllib.parse import quote, urlencode

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


# ---------------------------------------------------------------------------
# Overall verdict (title-legibility ordering, D-02) + builders (Task 2)
# ---------------------------------------------------------------------------


def overall_verdict(results: Any) -> str:
    """FAIL-dominant title verdict (D-02) -- NOT the handler's exit-code
    `max()` ordering (`cli_handlers.py`, where `marginal=2 > BAD=1`).

    `FAIL` if any step verdict is `BAD`; else `INCONCLUSIVE` if any is
    `marginal`; else `PASS`. Human-legible ordering for the issue title.
    """
    verdicts = {r.verdict for r in results}
    if "BAD" in verdicts:
        return "FAIL"
    if "marginal" in verdicts:
        return "INCONCLUSIVE"
    return "PASS"


def build_title(report: Any, chip: str) -> str:
    """`[dev test] <chip> — <PASS/FAIL/INCONCLUSIVE> (<shorthash>)` (D-02, SUB-03).

    The dedup shorthash is read from `report.to_dict()["dedup_fingerprint"]`
    (the Plan-01 field) -- this is the single-source link between the report
    model and the issue title.
    """
    d = report.to_dict()
    shorthash = d["dedup_fingerprint"]
    verdict = overall_verdict(report.results)
    return f"[dev test] {chip} — {verdict} ({shorthash})"


def build_body(
    sanitized_dict: dict[str, Any], results: Any, *, include_json: bool = True
) -> str:
    """Markdown body: a human results table, then (optionally) the fenced
    JSON block -- both derived from the SAME sanitized dict (SUB-02).

    Mirrors the `dev-test-<chip>.md` table shape (`cli_handlers.py`:
    `| Step | Verdict | Reason |`), but sources the reason cells from
    `sanitized_dict["steps"]` so PII stays scrubbed even when `results`
    (the unsanitized `StepResult` objects) is also passed in for shaping.
    """
    lines = ["| Step | Verdict | Reason |", "| ---- | ------- | ------ |"]
    for step in sanitized_dict.get("steps", []):
        reason = step.get("reason") or "-"
        lines.append(f"| {step.get('op')} | {step.get('verdict')} | {reason} |")
    body = "\n".join(lines)
    if include_json:
        body += "\n\n```json\n" + json.dumps(sanitized_dict, indent=2) + "\n```"
    return body


def build_issue_url(title: str, body: str) -> str:
    """`https://github.com/henols/firestarter_app/issues/new?...` (D-01).

    Percent-encodes `title`/`body` via `urllib.parse.urlencode(quote_via=quote)`.
    Deliberately OMITS the `labels` query param (RESEARCH Pitfall 1): GitHub
    silently drops or 404s the `labels` param for community testers without
    write access on `henols/firestarter_app` -- triage relies on the
    `[dev test]` title marker plus the fenced-JSON `schema_version` instead.
    Server-side template-based labeling is deferred to Phase 114.
    """
    query = urlencode({"title": title, "body": body}, quote_via=quote)
    return f"https://github.com/{SUBMIT_REPO}/issues/new?{query}"
