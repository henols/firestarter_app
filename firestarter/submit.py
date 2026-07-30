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

The `gh` tier's create argv is permission-independent by construction
(D-1, quick task 260728-ahy): it carries only the repo, title, and stdin
body -- no triage/write-gated argument -- so a community tester with only
read access on the target repo can file. Maintainer-side triage still
applies the `gsd-inbox` label post-hoc (`gh issue edit <n> --add-label
gsd-inbox`); detection continues to rely on the `[dev test]` title marker
plus the fenced-JSON `schema_version` (D-04, unchanged). A non-zero `gh`
exit and an unreachable browser both narrate their failure through the
`console` seam instead of reporting phantom success (D-2).

`SUBMIT_REPO` is a hardcoded module constant (D-01): the target repo is
NEVER inferred from cwd or a git remote, so a community tester's own fork
never receives their own report. It names `henols/firestarter_prom`, the
project-wide tracker -- deliberately NOT the repo this module lives in
(firestarter_prom#6 centralizes issue creation there and disables it on
`henols/firestarter` and `henols/firestarter_app`).
"""

from __future__ import annotations

import base64
import copy
import getpass
import json
import re
import shutil
import subprocess
import sys
import webbrowser
from typing import Any
from urllib.parse import quote, urlencode

from rich.prompt import Confirm

from firestarter.diagnostic_report import is_submittable

# ---------------------------------------------------------------------------
# Module constants (D-01, D-05)
# ---------------------------------------------------------------------------

# D-01: hardcoded, never remote-inferred. Target is the project-wide tracker,
# NOT the repo this code lives in: `henols/firestarter_prom` is the single
# repository for issue tracking per firestarter_prom#6 ("New GitHub issues must
# be allowed only in henols/firestarter_prom"; creation is to be disabled in
# `henols/firestarter` and `henols/firestarter_app`). A `dev test` report spans
# host + firmware + shield and cannot reliably attribute itself to one layer,
# so the cross-repository tracker is also the only correct destination for it.
SUBMIT_REPO = "henols/firestarter_prom"
# GSD_INBOX_LABEL is a maintainer-side triage tag ONLY (D-1, quick 260728-ahy):
# never sent on the `gh issue create` argv (that arg is triage/write-gated and a
# community tester lacks it); a maintainer applies it post-hoc via
# `gh issue edit <n> --add-label gsd-inbox`. Detection stays on the `[dev test]`
# title marker + fenced-JSON `schema_version` (D-04), unaffected by this constant.
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
    """`https://github.com/<SUBMIT_REPO>/issues/new?...` (D-01).

    Percent-encodes `title`/`body` via `urllib.parse.urlencode(quote_via=quote)`.
    Deliberately OMITS the `labels` query param (RESEARCH Pitfall 1): GitHub
    silently drops or 404s the `labels` param for community testers without
    write access on the target repo -- triage relies on the `[dev test]` title
    marker plus the fenced-JSON `schema_version` instead. This mirrors the `gh`
    tier, whose create argv is likewise permission-independent (D-1).
    """
    query = urlencode({"title": title, "body": body}, quote_via=quote)
    return f"https://github.com/{SUBMIT_REPO}/issues/new?{query}"


# ---------------------------------------------------------------------------
# gh-tier detection + shell-out (Task 3, T-113-01)
# ---------------------------------------------------------------------------


def gh_available(
    *,
    which_fn: Any = shutil.which,
    run_fn: Any = subprocess.run,
) -> bool:
    """`True` only when `gh` is on PATH AND `gh auth status` exits 0.

    Short-circuits `False` (never probes auth) when `which_fn("gh")` is
    falsy, so `run_fn` is never called with `gh` absent.
    """
    if not which_fn("gh"):
        return False
    proc = run_fn(["gh", "auth", "status"], capture_output=True, text=True, check=False)
    return proc.returncode == 0


def submit_via_gh(
    title: str, body: str, *, run_fn: Any = subprocess.run, console: Any = None
) -> str | None:
    """File the issue via `gh issue create`, body piped over stdin (no cap).

    The create argv carries only the repo, title, and stdin body -- nothing
    that requires triage/write access on the target repo, so the tier is
    permission-independent by construction: a community tester with only
    read access can file. The argv is a LIST passed to `run_fn` -- never a
    shell string, never a shell-interpreted invocation (T-113-01, the
    command-injection control). Returns the created issue URL (`proc.stdout.strip()`) on
    returncode 0, else `None` -- and on a non-zero exit, the captured
    `stderr` (or the exit status when `stderr` is blank) is printed through
    the `console` seam, so a permission failure is never silent. Narrating
    the *fallback* is deliberately left to the caller: this function does
    not decide whether a browser tier follows.
    """
    proc = run_fn(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            SUBMIT_REPO,
            "--title",
            title,
            "--body-file",
            "-",
        ],
        input=body,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == 0:
        return proc.stdout.strip()

    err = (getattr(proc, "stderr", "") or "").strip()
    if err:
        _print(f"gh issue create failed: {err}", console=console)
    else:
        _print(f"gh issue create failed (exit {proc.returncode}).", console=console)
    return None


# ---------------------------------------------------------------------------
# Dedup query + comment path (Task 1, D-09/D-10/D-11) -- both follow
# submit_via_gh's seam discipline exactly: injected run_fn, list argv never a
# shell string, explicit --repo never cwd-inferred, check=False with an
# explicit returncode branch, narration on failure.
# ---------------------------------------------------------------------------

_DEDUP_SEARCH_LIMIT = 5


def find_prior_report(
    fingerprint: str, *, run_fn: Any = subprocess.run
) -> tuple[str | None, bool]:
    """Read-only `gh issue list` dedup query, keyed on `dedup_fingerprint` (D-09).

    Returns `(url, check_ran)`: the matched prior issue's URL (or `None`
    when no match), and whether the query actually ran at all.

    THE THREE BRANCHES ARE DISTINGUISHED BY THE PARSED PAYLOAD, NEVER BY
    EXIT CODE ALONE -- `gh issue list` returns exit 0 for BOTH "duplicate
    found" and "no duplicate", so a bare `returncode == 0` check would
    silently conflate a clean run with a missing duplicate:

    - non-zero returncode (`gh` absent, unauthenticated -- exit 4, not 1 --
      or offline) -> `(None, False)`: the check could not run at all.
    - returncode 0, empty parsed JSON array -> `(None, True)`: the check
      ran and found no duplicate.
    - returncode 0, one or more rows -> `(first_row["url"], True)`: the
      check ran and found a duplicate.

    Parsing is defensive: malformed or empty stdout on a 0 returncode (a
    torn `gh` output, an unexpected shape) degrades to `(None, False)` --
    "the check could not run" -- rather than raising, because a parse
    failure carries the same epistemic status as `gh` never having run.

    The argv is a LIST passed to `run_fn` -- never a shell string -- and
    always requests an explicit `--json` field selection (`number,title,url`)
    rather than parsing `gh`'s default human-readable table.

    LIMITATION, recorded because the check is a courtesy, never a
    guarantee: GitHub's issue-search index is EVENTUALLY CONSISTENT, so an
    issue filed seconds earlier by the same tester may not yet be
    returnable by `--search`. A duplicate that slips through this window
    is not lost -- `count_agreeing` groups saved report bodies by this same
    fingerprint, so it lands visibly grouped on arrival rather than as
    noise a maintainer must separately detect.

    `gh` truly absent from PATH (as opposed to present-but-unauthenticated)
    makes a real `subprocess.run` raise `OSError`/`FileNotFoundError`
    rather than return a non-zero-returncode object -- caught here and
    folded into the same "check could not run" branch, so this function
    never raises regardless of which of the three absent/unauthed/offline
    conditions produced the failure.
    """
    try:
        proc = run_fn(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                SUBMIT_REPO,
                "--author",
                "@me",
                "--search",
                fingerprint,
                "--state",
                "all",
                "--json",
                "number,title,url",
                "--limit",
                str(_DEDUP_SEARCH_LIMIT),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None, False
    if proc.returncode != 0:
        return None, False

    stdout = getattr(proc, "stdout", "") or ""
    try:
        rows = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return None, False
    if not isinstance(rows, list):
        return None, False
    if not rows:
        return None, True

    first = rows[0]
    url = first.get("url") if isinstance(first, dict) else None
    if not url:
        return None, False
    return url, True


def comment_via_gh(
    issue_url: str, body: str, *, run_fn: Any = subprocess.run, console: Any = None
) -> str | None:
    """Comment `body` onto `issue_url` via `gh issue comment` (D-11).

    Argv is a LIST: the `gh issue comment` subcommand, the target issue,
    the explicit `--repo` argument (always `SUBMIT_REPO`, NEVER
    cwd-inferred), and `--body-file -` with the body piped on stdin (no
    length cap, no shell-quoting surface). No flag that mutates or
    hijacks is ever sent -- no `--delete-last`, no `--edit-last`, no
    `--yes`, no `--web`, no `--editor`.

    Returns the comment URL (`proc.stdout.strip()`) on returncode 0, else
    `None` -- narrating the captured `gh` stderr (or the exit status when
    stderr is blank) through the `console` seam, exactly like
    `submit_via_gh`'s failure path, so a permission failure is never
    silent.

    ASSUMPTION A1 (unverified by design -- verifying it destructively
    would post a real comment to the live tracker): commenting on a
    public repo is assumed to need only an authenticated account, never
    write access. This function does not depend on A1 being true for
    correctness: when it returns `None`, `submit_report` degrades with a
    spoken reason to the browser tier on the existing issue URL, exactly
    as the create path already degrades on a `gh` failure -- so A1's
    truth value is irrelevant to whether the tester can still file.
    """
    proc = run_fn(
        [
            "gh",
            "issue",
            "comment",
            issue_url,
            "--repo",
            SUBMIT_REPO,
            "--body-file",
            "-",
        ],
        input=body,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == 0:
        return proc.stdout.strip()

    err = (getattr(proc, "stderr", "") or "").strip()
    if err:
        _print(f"gh issue comment failed: {err}", console=console)
    else:
        _print(f"gh issue comment failed (exit {proc.returncode}).", console=console)
    return None


# ---------------------------------------------------------------------------
# Browser tier + D-05 oversize escalation (Task 1)
# ---------------------------------------------------------------------------


def _print(msg: str, *, console: Any = None) -> None:
    if console is not None:
        console.print(msg)
    else:
        print(msg)


def submit_via_browser(
    title: str,
    body: str,
    saved_json_path: Any,
    *,
    browser_open: Any = webbrowser.open,
    console: Any = None,
) -> str | None:
    """Open a prefilled `issues/new` browser URL, degrading past the D-05
    encoded-byte thresholds (RESEARCH §Oversize Handling / §Browser URL Facts).

    The byte measurement is ALWAYS on `len(url.encode("utf-8"))` of the
    fully-encoded URL (Pitfall 3) -- never the raw body char count. Past
    `_URL_ESCALATE_BYTES` (7500) the fenced JSON block is dropped from the
    body and a note pointing at the always-saved report -- naming only
    `saved_json_path.name`, never the full path (avoids leaking the
    tester's home dir into the PUBLIC issue body) -- is appended, then the
    URL is re-encoded. Past `_URL_HARD_CAP_BYTES` (8000), even after
    dropping the JSON block, the browser is NEVER opened: this prints the
    local report path plus a `gh`-tier directive to the tester's own
    console (not the issue body, so the full path is fine here) and
    returns `None`. `browser_open` is called at most once, and only when
    strictly under the hard cap.

    A falsy `browser_open` result (no browser could be launched -- e.g.
    headless environment) also returns `None` and prints an actionable
    manual-filing message carrying the full issue URL plus the full local
    report path, so the caller can never mistake an unreachable browser
    for a filed report (D-2, quick task 260728-ahy).
    """
    url = build_issue_url(title, body)
    n = len(url.encode("utf-8"))

    if n > _URL_ESCALATE_BYTES:
        table_only = body.split("\n\n```json\n", 1)[0]
        note = (
            "\n\n_Full machine-readable report saved locally as "
            f"`{saved_json_path.name}` -- attach it to this issue, or "
            "re-run with the `gh` CLI installed to file the complete "
            "report automatically._"
        )
        body = table_only + note
        url = build_issue_url(title, body)
        n = len(url.encode("utf-8"))

    if n > _URL_HARD_CAP_BYTES:
        _print(
            "Report too large for a browser-prefilled issue URL "
            f"({n} bytes encoded, over the ~8 KB GitHub server cap). "
            f"The full report is saved locally at {saved_json_path}. "
            "Install the `gh` CLI and re-run with --submit to file the "
            "complete report automatically.",
            console=console,
        )
        return None

    opened = browser_open(url)
    if not opened:
        _print(
            "Could not open a browser -- file the report manually by "
            f"pasting this URL: {url}\nThe complete report is saved "
            f"locally at {saved_json_path}.",
            console=console,
        )
        return None
    return url


# ---------------------------------------------------------------------------
# submit_report: D-03 refuse gate + D-04 TTY/off-TTY dispatch (Task 2)
# ---------------------------------------------------------------------------


def submit_report(
    report: Any,
    chip: str,
    saved_json_path: Any,
    *,
    which_fn: Any = shutil.which,
    run_fn: Any = subprocess.run,
    browser_open: Any = webbrowser.open,
    isatty_fn: Any = None,
    confirm_fn: Any = Confirm.ask,
    console: Any = None,
    find_prior_report_fn: Any = find_prior_report,
    comment_via_gh_fn: Any = comment_via_gh,
) -> None:
    """The single submission entry point (SUB-01/02) -- composes every
    Plan-02 builder over the ALREADY-COMPLETED `report`/`saved_json_path`;
    it never re-runs the sweep and never re-derives the report.

    Step 1 (D-03 refuse gate): when `is_submittable(report.auto_capture)`
    is `False`, prints the specific missing field name(s) among
    `chip`/`protocol`/`host_version` and returns WITHOUT calling
    `browser_open`, `run_fn`, `find_prior_report_fn`, or `confirm_fn`.

    Step 2: builds the sanitized body (`sanitize_dict` -> `build_body`) and
    title (`build_title`) -- the SAME sanitized body is what every
    downstream seam (preview, off-TTY print, dedup-failure line, `gh`
    create, `gh` comment, browser) receives; a PII vector present in a
    step reason never reaches a seam unscrubbed.

    Step 3 (D-09 dedup query -- runs BEFORE any ask, on EVERY reached
    path, TTY or not): `find_prior_report_fn(fingerprint, run_fn=run_fn)`
    is called immediately after Step 2 and before the TTY branch, so
    "the check runs first" (DEVTEST-05) holds universally, not merely on
    an interactive run.

    Step 4 (D-04/D-10 off-TTY): prints the sanitized body and the issue
    URL and returns WITHOUT ever calling `confirm_fn`, `submit_via_gh`, or
    `comment_via_gh_fn` -- filing nothing (v1.21 SUB-01's ban on silent
    off-TTY submission survives D-05's removal of the explicit flag). The
    Step 3 dedup outcome is included in this output: the existing issue is
    named when one was found, or an explicit line states the check could
    not run when it was not.

    Step 5 (the ask -- EVERY interactive run, DEVTEST-05): when Step 3
    found a duplicate, names it and asks whether to add this run's
    evidence as a comment (worded as "you appear to have already reported
    this", never as a certainty, per F-5's eventually-consistent search
    index caveat), explaining that the fingerprint excludes measured
    voltages/error codes/reasons so a second run can still carry new
    diagnostic detail. Otherwise asks the normal filing question -- and
    when the dedup check could not run (D-10), an explicit line says so
    before the SAME normal question, which is NEVER defaulted to decline
    and is asked exactly as it would be on a clean check.

    Step 6 (dispatch): on a duplicate-comment "yes", calls
    `comment_via_gh_fn`; a `None` return prints the failure reason and
    degrades to the browser tier pointed at the EXISTING issue (Assumption
    A1 handled by design: its truth value never affects correctness). On a
    new-issue "yes", the unchanged tier dispatch: `submit_via_gh` when
    `gh_available()`, degrading to `submit_via_browser` on a `None`
    return, echoing the created URL on success. The browser tier stays
    quiet on success in both branches: the opened tab is its own receipt,
    and nothing is filed until the tester presses Submit there.
    """
    isatty_fn = isatty_fn or (lambda: sys.stdin.isatty())

    ac = report.auto_capture
    if not is_submittable(ac):
        missing = [
            name
            for name, value in (
                ("chip", ac.chip),
                ("protocol", ac.protocol),
                ("host_version", ac.host_version),
            )
            if not value
        ]
        _print(
            f"Cannot submit -- missing required field(s): {', '.join(missing)}.",
            console=console,
        )
        return

    report_dict = report.to_dict()
    sanitized = sanitize_dict(report_dict)
    title = build_title(report, chip)
    body = build_body(sanitized, report.results, include_json=True)

    fingerprint = report_dict["dedup_fingerprint"]
    prior_url, dedup_ran = find_prior_report_fn(fingerprint, run_fn=run_fn)

    if not isatty_fn():
        url = build_issue_url(title, body)
        _print(body, console=console)
        _print(url, console=console)
        if prior_url:
            _print(
                f"Note: you appear to have already reported this -- see {prior_url}.",
                console=console,
            )
        elif not dedup_ran:
            _print(
                "Note: the duplicate check could not run (gh absent, "
                "unauthenticated, or offline).",
                console=console,
            )
        return

    _print(body, console=console)

    if prior_url:
        if not confirm_fn(
            f"You appear to have already reported this -- see {prior_url}. "
            "Add this run's evidence as a comment? (The dedup fingerprint "
            "excludes measured voltages, error codes and reasons, so a "
            "second run can still carry new diagnostic detail.)",
            default=False,
        ):
            return
        comment_url = comment_via_gh_fn(prior_url, body, run_fn=run_fn, console=console)
        if comment_url is None:
            _print(
                "The gh comment failed -- degrading to the browser tier "
                "on the existing issue.",
                console=console,
            )
            submit_via_browser(
                title,
                body,
                saved_json_path,
                browser_open=browser_open,
                console=console,
            )
        else:
            _print(f"Comment added: {comment_url}", console=console)
        return

    if not dedup_ran:
        _print(
            "Note: the duplicate check could not run (gh absent, "
            "unauthenticated, or offline) -- asking anyway.",
            console=console,
        )

    if not confirm_fn(f"Submit this report to {SUBMIT_REPO}?", default=False):
        return

    if gh_available(which_fn=which_fn, run_fn=run_fn):
        url = submit_via_gh(title, body, run_fn=run_fn, console=console)
        if url is None:
            _print(
                "The gh tier failed to file the report -- degrading to "
                "the browser tier.",
                console=console,
            )
            submit_via_browser(
                title,
                body,
                saved_json_path,
                browser_open=browser_open,
                console=console,
            )
        elif url:
            _print(f"Report filed: {url}", console=console)
        else:
            _print(f"Report filed to {SUBMIT_REPO}.", console=console)
        return

    submit_via_browser(
        title, body, saved_json_path, browser_open=browser_open, console=console
    )
