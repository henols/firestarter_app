"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Community `dev test` Issue Triage Parser

Stdlib-only CLI a maintainer runs during `gsd-inbox` triage against a
community `dev test` GitHub issue. It does NOT edit the installed
`.claude/gsd-core/workflows/inbox.md` -- that workflow's job is
fetching/labeling issues; this module's job is understanding the ONE
issue shape `firestarter/submit.py` produces and is INVOKED standalone,
e.g.:

    gh issue view <n> --json title -q .title   # feed to --title
    gh issue view <n> --json body  -q .body    # feed to --body-file/stdin
    python tools/parse_devtest_issue.py --title "$TITLE" --body-file body.txt

Detection requires BOTH markers, defensive against a stray fenced
block anywhere else in an issue: the `[dev test]` title marker
(`submit.py:build_title`) AND a fenced ```json block whose parsed object
carries a `schema_version` key (`diagnostic_report.py:to_json_block`).
`schema_version` is accepted by PRESENCE (any value), not an exact
string match, so this parser survives a future schema bump (e.g. the
Phase-114 1.0 -> 1.1 `ladder_state` addition) without a code change.

Untrusted input (T-114-03/T-114-04, RESEARCH Pitfall 6): every issue body
is community-authored and MUST be treated as hostile. This module never
calls `eval`/`exec`, never shells out, never interpolates body content
into a command, bounds the body size before parsing, and wraps every
`json.loads` in a `JSONDecodeError` guard. Every extraction function
fails SOFT (returns `None` / skips the body) -- it never raises out to
the caller.

Cross-report agreement: `count_agreeing` groups SAVED
issue bodies by their ALREADY-EMBEDDED `dedup_fingerprint`
(`diagnostic_report.py:dedup_fingerprint`, never re-hashed here). This is
the cross-report N>=2 human-decision signal -- explicitly distinct from
Phase-108's internal per-run N>=2 (a single sweep's own repeat-run
agreement). Nothing in this module writes `support_status`; it is a
a scan target and is read-only by construction, matching
`diagnostic_report.py`'s own `db.get_eprom_config` read-only discipline.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Module constants -- the two detection markers + a defensive size bound
# ---------------------------------------------------------------------------

_DEV_TEST_MARKER = "[dev test]"

# Mirrors diagnostic_report.py:to_json_block()'s exact fence
# ("```json\n" + json.dumps(...) + "\n```") and submit.py:build_body()'s
# identical fence when it appends the JSON block.
_FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)

# Defensive size bound (T-114-03): well above a real report's JSON payload,
# comfortably below a pathological/oversized hostile body. GitHub's own
# issue-body character cap is ~65536; this generously doubles it in bytes.
_MAX_BODY_BYTES = 131_072


# ---------------------------------------------------------------------------
# Detection + defensive extraction
# ---------------------------------------------------------------------------


def _extract_fenced_report(body: str | None) -> dict[str, Any] | None:
    """Body-only extraction: fenced ```json block -> dict with schema_version.

    No title check here (`count_agreeing` only ever has bodies, never
    titles) -- `parse_devtest_body` layers the title-marker check on top
    of this. Never raises: an oversized body, a missing fence, malformed
    JSON, a non-dict payload, or a dict missing `schema_version` all
    return `None`.
    """
    if not body:
        return None
    if len(body.encode("utf-8", errors="ignore")) > _MAX_BODY_BYTES:
        return None
    match = _FENCE.search(body)
    if not match:
        return None
    try:
        obj = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if "schema_version" not in obj:  # detection marker, presence-only
        return None
    return obj


def parse_devtest_body(title: str | None, body: str | None) -> dict[str, Any] | None:
    """Both markers required: `[dev test]` in `title` AND a fenced
    ```json block in `body` whose parsed object carries `schema_version`.

    Returns the parsed report dict, or `None` when either marker is
    absent, the body is oversized, or the JSON is malformed/not a dict.
    Never raises on hostile input (T-114-03/T-114-04).
    """
    if not title or _DEV_TEST_MARKER not in title:
        return None
    return _extract_fenced_report(body)


# ---------------------------------------------------------------------------
# DB-diff surface -- current-vs-proposed + ladder_state
# ---------------------------------------------------------------------------


def extract_db_diff(report_obj: dict[str, Any]) -> dict[str, Any]:
    """Read-only current-vs-proposed DB-diff surface from an already-parsed
    report dict (defensive `.get` throughout).

    Tolerant of a missing `db_diff` (`None`, an older/degenerate report)
    and of a missing `ladder_state` key (schema 1.0, pre-Phase-114) --
    both default to `""`/`"supported"` rather than raising `KeyError`.
    """
    db_diff = report_obj.get("db_diff") or {}
    return {
        "current_support_status": db_diff.get("current_support_status", "supported"),
        "proposed_disposition": db_diff.get("proposed_disposition", ""),
        "ladder_state": db_diff.get("ladder_state", ""),
        "dedup_fingerprint": report_obj.get("dedup_fingerprint", ""),
    }


def _read_live_support_status(chip: str | None) -> str | None:
    """OPTIONAL read-only live re-check via `EpromDatabase.get_eprom_config`
    (`--live-db`) -- never a write, mirrors `diagnostic_report.build_db_diff`'s
    own read site exactly.

    Lazily imported so the stdlib-only detection/DB-diff path never
    requires the `firestarter` package to be importable; a missing chip
    name or import failure returns `None` (fails soft, never raises).
    """
    if not chip:
        return None
    try:
        from firestarter.database import EpromDatabase
    except ImportError:
        return None
    db = EpromDatabase(skip_local_override=True)
    raw_config, _manufacturer = db.get_eprom_config(chip)
    return (raw_config or {}).get("support_status", "supported")


# ---------------------------------------------------------------------------
# Cross-report N-agreeing -- dedup_fingerprint grouping ONLY
# ---------------------------------------------------------------------------


def count_agreeing(bodies: list[str]) -> dict[str, int]:
    """Group SAVED issue bodies by their embedded `dedup_fingerprint` and
    return `{fingerprint: count}` (the cross-report N>=2 signal).

    Reuses the ALREADY-EMBEDDED `dedup_fingerprint` from each body's fenced
    JSON -- never re-hashes and never reads a per-step run count (that
    would conflate this with Phase-108's internal per-run N>=2, RESEARCH
    Pitfall 5). A body that is not a dev test report (no fenced JSON, no
    `schema_version`, malformed, oversized) is silently skipped, never
    raises.
    """
    counts: dict[str, int] = {}
    for body in bodies:
        obj = _extract_fenced_report(body)
        if obj is None:
            continue
        fingerprint = obj.get("dedup_fingerprint")
        if not fingerprint:
            continue
        counts[fingerprint] = counts.get(fingerprint, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Rendering (plain-text; no third-party dependency)
# ---------------------------------------------------------------------------

# Literal #2 of three. Identical in
# VALUE to `firestarter/diagnostic_report.py`'s `NOT_REPORTED`, but defined
# separately here rather than imported: this module is a stdlib-only CLI
# (module docstring above, `:9-11`) whose module-level imports are exactly
# `__future__`, `argparse`, `json`, `re`, `sys`, `pathlib`, `typing` -- a
# module-level import naming that package would make the default
# triage path depend on the app package, and `tools/` is outside both ruff
# and mypy scope, so nothing else would catch that drift. The one existing
# `firestarter` import in this file (`_read_live_support_status`, above) is
# function-local and pre-existing -- it stays exactly as it is. The
# substitute for an import-time guarantee is a value-parity assert in
# `tests/test_parse_devtest_issue.py::test_unknown_marker_string_matches_the_report_model`
# (RESEARCH Pattern 3 / P-4), the only module in this repo that legitimately
# imports both worlds.
NOT_REPORTED = "not reported"

# One action-oriented clause, true under EITHER reading of a
# `None` identity (an old report whose host build never captured it, or a
# post-bump report where capture failed) -- so no schema-version ordering
# logic is needed here or anywhere else (both parsers accept
# `schema_version` by presence only, and a live fixture carries
# `schema_version: "9.9-future"` that any ordering comparison would have to
# survive). Pre-checked against `check_diagnostic_report_claims.py`'s
# 14-entry `FORBIDDEN_PATTERNS` table and clean -- proven, not assumed, by
# `test_parser_marker_strings_trip_no_forbidden_claim_pattern`. No claim
# gate scans this file today (P-5); that test is the only enforcement.
_NOT_ATTRIBUTABLE = (
    "NOT attributable to a firmware version -- ask the reporter for a "
    "fresh dev test run on a current host build"
)


def _steps_total_cell(report_obj: dict) -> str:
    """Summed per-step `duration_s` for triage -- spots an abnormally slow
    part at a glance.

    Schema 1.5 added `duration_s`; a pre-1.5 report has no such key on any
    step and renders `(not reported)` rather than a misleading `0.00s`.
    Non-numeric or negative values are ignored rather than raising -- this
    tool parses UNTRUSTED issue bodies, so a hostile payload must not be
    able to crash triage.
    """
    total = 0.0
    seen = False
    steps = report_obj.get("steps")
    if not isinstance(steps, list):
        return "(not reported)"
    for step in steps:
        if not isinstance(step, dict):
            continue
        value = step.get("duration_s")
        if value is None or isinstance(value, bool):
            continue
        try:
            parsed = float(value)
        except (ValueError, TypeError):
            continue
        if parsed < 0:
            continue
        total += parsed
        seen = True
    if not seen:
        return "(not reported)"
    return f"{total:.2f}s" if total < 10 else f"{total:.1f}s"


def render_diff(
    report_obj: dict[str, Any],
    diff: dict[str, Any],
    *,
    n_agreeing: int | None = None,
) -> str:
    """Plain-text current-vs-proposed DB-diff render (no third-party
    import). Explicitly labels any `n_agreeing` value a maintainer decision
    input, never an auto-promotion trigger.

    Also carries the provenance identity a triager needs before any
    firmware-version claim can rest on this report (PROV-06): a labelled
    `host_version` row and a labelled `fw_board_identity` row that folds in
    the `_NOT_ATTRIBUTABLE` clause when the identity is absent. No
    `hw_revision` row -- a write-path finding is attributable only
    when host AND firmware are both known, and `hw_revision` is a coarse
    silkscreen bucket that cannot discriminate the operator's Rev 2.2 /
    Rev 2.0 / modified Rev 0 boards, so a line naming it would look
    authoritative while answering nothing. No derived `attributable`
    boolean either -- dead data with no consumer.
    """
    auto_capture = report_obj.get("auto_capture") or {}
    chip = auto_capture.get("chip", "?")

    # Explicit two-clause condition, never a single `value or FALLBACK`
    # coalescing expression -- such an expression also fires on other falsy
    # values with no decision behind them, and a community-authored body can
    # genuinely carry `""`, which must render the marker, never a blank
    # (mirrors `diagnostic_report.py`'s `_identity_cell`).
    host_version = auto_capture.get("host_version")
    host_version_cell = (
        NOT_REPORTED
        if host_version is None or host_version == ""
        else str(host_version)
    )

    fw_board_identity = auto_capture.get("fw_board_identity")
    identity_absent = fw_board_identity is None or fw_board_identity == ""
    fw_identity_cell = (
        f"{NOT_REPORTED} -- {_NOT_ATTRIBUTABLE}"
        if identity_absent
        else str(fw_board_identity)
    )

    lines = [
        f"dev test triage -- {chip}",
        f"  schema_version:          {report_obj.get('schema_version', '?')}",
        f"  host_version:            {host_version_cell}",
        f"  fw_board_identity:       {fw_identity_cell}",
        f"  dedup_fingerprint:       {diff.get('dedup_fingerprint', '')}",
        f"  current_support_status: {diff.get('current_support_status', '')}",
        f"  proposed_disposition:   {diff.get('proposed_disposition', '')}",
        f"  ladder_state:           {diff.get('ladder_state') or '(none)'}",
        f"  steps total:            {_steps_total_cell(report_obj)}",
    ]
    if n_agreeing is not None:
        lines.append(
            f"  N agreeing reports:     {n_agreeing} "
            "(maintainer decision input -- NEVER an auto-promotion trigger)"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI (argparse, stdlib-only)
# ---------------------------------------------------------------------------


def _run_single_mode(title: str, body_file: Path | None, *, live_db: bool) -> int:
    if body_file is not None:
        try:
            body = body_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(
                f"error: could not read --body-file {body_file}: {exc}", file=sys.stderr
            )
            return 2
    else:
        body = sys.stdin.read()

    report_obj = parse_devtest_body(title, body)
    if report_obj is None:
        print(
            "Not a dev test report -- missing the '[dev test]' title marker "
            "and/or a fenced ```json block carrying a schema_version key."
        )
        return 1

    diff = extract_db_diff(report_obj)
    if live_db:
        chip = (report_obj.get("auto_capture") or {}).get("chip")
        live_status = _read_live_support_status(chip)
        if live_status is not None:
            diff["current_support_status"] = live_status

    print(render_diff(report_obj, diff))
    return 0


def _run_agreeing_mode(directory: Path, pattern: str) -> int:
    if not directory.is_dir():
        print(f"error: --dir {directory} is not a directory", file=sys.stderr)
        return 2

    bodies: list[str] = []
    for path in sorted(directory.glob(pattern)):
        if not path.is_file():
            continue
        try:
            bodies.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue

    counts = count_agreeing(bodies)
    if not counts:
        print("No dev test reports (fenced JSON with schema_version) found.")
        return 1

    for fingerprint, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        flag = (
            "  <-- N>=2 agreement (maintainer decision input, NEVER an "
            "auto-promotion trigger)"
            if n >= 2
            else ""
        )
        print(f"{fingerprint}: {n} agreeing report(s){flag}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="parse_devtest_issue.py",
        description=(
            "INBOX-01 stdlib triage parser for a community `dev test` GitHub "
            "issue: detects the report via the '[dev test]' title marker plus "
            "a fenced JSON block carrying schema_version, surfaces the "
            "current-vs-proposed DB-diff, and (given saved issue bodies) "
            "counts matching dedup_fingerprints for the maintainer's N>=2 "
            "cross-report agreement signal -- advisory only, never an "
            "auto-promotion trigger."
        ),
    )
    parser.add_argument(
        "--title",
        default="",
        help=(
            "Issue title, e.g. from `gh issue view <n> --json title -q .title`. "
            "Required for single-body detection (the '[dev test]' marker)."
        ),
    )
    parser.add_argument(
        "--body-file",
        type=Path,
        default=None,
        help=(
            "Path to the issue body, e.g. from "
            "`gh issue view <n> --json body -q .body`. Reads stdin if omitted."
        ),
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help=(
            "Directory of saved issue-body text files to compute the "
            "N-agreeing dedup_fingerprint count across (title not required)."
        ),
    )
    parser.add_argument(
        "--glob",
        default="*.txt",
        help="Glob pattern within --dir (default: *.txt).",
    )
    parser.add_argument(
        "--live-db",
        action="store_true",
        help=(
            "Re-read current_support_status live via "
            "EpromDatabase.get_eprom_config (read-only) instead of trusting "
            "the report's own embedded db_diff snapshot."
        ),
    )
    args = parser.parse_args(argv)

    if args.dir is not None:
        return _run_agreeing_mode(args.dir, args.glob)

    return _run_single_mode(args.title, args.body_file, live_db=args.live_db)


if __name__ == "__main__":
    sys.exit(main())
