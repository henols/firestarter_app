#!/usr/bin/env python3
"""
Generator for the 26-row filed `[dev test]` issue corpus (Phase 174, D-06,
GATE-05, plan 174-04 task 1).

Enumerates `henols/firestarter_prom` issues via the `gh` CLI, filters by the
bracketed `[dev test]` TITLE prefix (never the `dev-test` label -- measured
to cover only 15 of 26), parses each matching title with exactly one
anchored regular expression, and extracts each issue body's fenced JSON
diagnostic-report block. Every row's `steps`, `run_counts` and `coverage_tag`
are committed as DATA -- no issue body is parsed at test time and no report
deserializer is written (D-01 stands; this script is a generator, not a
loader).

The load-bearing extraction detail: a step's `fingerprint` value in the
fenced JSON is a BARE CLASSIFICATION STRING, not an object with a
classification key. Read as a string, all 26 rows reproduce their filed
`dedup_fingerprint` through the real hash function; read as an object, 0 of
26 do.

Tag handling. `repeat_policy_tag` is DERIVED: the body's per-step
`run_count` values (when every step in the body carries the key) are stamped
onto a real `DiagnosticReport` via
`tests.fixtures.report_shapes.build_shape_from_step_specs`, and the real
`firestarter.chip_test.repeat_policy_tag` is read off the built report.
`coverage_tag` is SOLVED: neither schema stores a plain full-device boolean,
so both candidate values (absent, and the full-device marker) are tried
against the real `dedup_fingerprint` and the one that reproduces the filed
hash is recorded, with `coverage_tag_source` set to `"solved"`.

Validate before emit: every row's recomputed hash must equal its filed hash,
the row count must be exactly 26, every filed hash must be twelve lowercase
hex characters, and no two rows may share an issue number. A non-reproducing
row raises and exits non-zero rather than being silently dropped.

Exit codes:
  0 -- derivation valid, corpus emitted successfully (or --check found no
       drift)
  1 -- derived rows failed validation (a row does not reproduce its filed
       hash, the row count is not 26, or --check found the committed
       artifact stale)
  2 -- the `gh` CLI failed, no titled issue's body carried a parseable fenced
       JSON report, or a `[dev test]`-prefixed title failed the anchored
       parse
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).parent
_APP_ROOT = _TOOLS_DIR.parent
_TARGET_DEFAULT = _APP_ROOT / "tests" / "fixtures" / "devtest_issue_corpus.json"
_REPO_DEFAULT = "henols/firestarter_prom"

sys.path.insert(0, str(_APP_ROOT))

_TITLE_PREFIX = "[dev test]"
_TITLE_RE = re.compile(
    r"^\[dev test\]\s+(\S+)\s+—\s+(PASS|FAIL|INCONCLUSIVE)\s+\(([0-9a-f]{12})\)$"
)
_FENCED_JSON_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


class CorpusError(Exception):
    """A derivation or extraction step failed (exit code 2)."""


class CorpusValidationError(Exception):
    """A derived row (or the whole corpus) failed validation (exit code 1)."""


def _fetch_issues(repo: str) -> list[dict]:
    proc = subprocess.run(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--limit",
            "300",
            "--json",
            "number,title,state,body",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise CorpusError(f"gh issue list failed: {proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CorpusError(f"gh issue list returned unparsable JSON: {exc}") from exc


def _parse_title(title: str, issue_number: int) -> tuple[str, str, str]:
    match = _TITLE_RE.match(title)
    if match is None:
        raise CorpusError(
            f"issue #{issue_number}: title starts with {_TITLE_PREFIX!r} but "
            f"failed the anchored parse: {title!r}"
        )
    raw_token, verdict, filed_hash = match.groups()
    return raw_token, verdict, filed_hash


def _extract_report(body: str, issue_number: int) -> dict:
    found = None
    for candidate in _FENCED_JSON_RE.finditer(body):
        try:
            obj = json.loads(candidate.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "dedup_fingerprint" in obj:
            found = obj
    if found is None:
        raise CorpusError(
            f"issue #{issue_number}: no fenced JSON report block carrying "
            f"a dedup_fingerprint key was found in the body"
        )
    return found


def _step_triples(report: dict) -> list[tuple[str, str, str | None]]:
    return [(s["op"], s["verdict"], s.get("fingerprint")) for s in report["steps"]]


def _run_counts(report: dict) -> dict[str, int] | None:
    steps = report["steps"]
    if not steps or not all("run_count" in s for s in steps):
        return None
    return {s["op"]: s["run_count"] for s in steps}


def _solve_row(
    *,
    chip: str,
    protocol: str,
    steps: list[tuple[str, str, str | None]],
    run_counts: dict[str, int] | None,
    filed_hash: str,
    issue_number: int,
) -> tuple[str, str, str]:
    """Solve `coverage_tag` by trial, derive `repeat_policy_tag` for real,
    return `(repeat_policy_tag, coverage_tag, recomputed_hash)`.

    Raises `CorpusValidationError` naming the issue if neither coverage
    candidate reproduces the filed hash.
    """
    from firestarter.chip_test import (
        COVERAGE_TAG_FULL_DEVICE,
        REGION_POLICY_FULL_DEVICE,
        repeat_policy_tag as real_repeat_policy_tag,
    )
    from firestarter.diagnostic_report import dedup_fingerprint
    from tests.fixtures.report_shapes import build_shape_from_step_specs

    step_specs = [(op, verdict, cls, "") for op, verdict, cls in steps]

    for coverage_policy, coverage_tag_value in (
        (None, ""),
        (REGION_POLICY_FULL_DEVICE, COVERAGE_TAG_FULL_DEVICE),
    ):
        report = build_shape_from_step_specs(
            chip=chip,
            protocol=protocol,
            step_specs=step_specs,
            run_counts=run_counts,
            coverage_policy=coverage_policy,
        )
        recomputed = dedup_fingerprint(report)
        if recomputed == filed_hash:
            return real_repeat_policy_tag(report.results), coverage_tag_value, recomputed

    raise CorpusValidationError(
        f"issue #{issue_number}: neither coverage candidate reproduces the "
        f"filed hash {filed_hash!r} for chip {chip!r}"
    )


def derive_rows(repo: str) -> list[dict]:
    issues = _fetch_issues(repo)
    rows = []
    for issue in issues:
        title = issue["title"]
        if not title.startswith(_TITLE_PREFIX):
            continue
        issue_number = int(issue["number"])
        raw_token, verdict, filed_hash = _parse_title(title, issue_number)
        report = _extract_report(issue["body"], issue_number)
        steps = _step_triples(report)
        run_counts = _run_counts(report)
        auto_capture = report["auto_capture"]
        chip = auto_capture["chip"]
        protocol = str(auto_capture["protocol"])
        repeat_policy_value, coverage_value, recomputed_hash = _solve_row(
            chip=chip,
            protocol=protocol,
            steps=steps,
            run_counts=run_counts,
            filed_hash=filed_hash,
            issue_number=issue_number,
        )
        row = {
            "issue": issue_number,
            "state": issue["state"],
            "raw_token": raw_token,
            "verdict": verdict,
            "filed_hash": filed_hash,
            "schema_version": report["schema_version"],
            "host_version": auto_capture["host_version"],
            "chip": chip,
            "protocol": protocol,
            "steps": [list(triple) for triple in steps],
            "repeat_policy_tag": repeat_policy_value,
            "coverage_tag": coverage_value,
            "coverage_tag_source": "solved",
            "recomputed_hash": recomputed_hash,
        }
        if run_counts is not None:
            row["run_counts"] = run_counts
        rows.append(row)
    rows.sort(key=lambda r: r["issue"])
    return rows


_HEX12_RE = re.compile(r"^[0-9a-f]{12}$")


def validate_rows(rows: list[dict]) -> None:
    if len(rows) != 26:
        raise CorpusValidationError(f"expected 26 rows, derived {len(rows)}")

    issue_numbers = [r["issue"] for r in rows]
    if len(set(issue_numbers)) != len(issue_numbers):
        raise CorpusValidationError("two or more rows share the same issue number")

    for row in rows:
        if not _HEX12_RE.match(row["filed_hash"]):
            raise CorpusValidationError(
                f"issue #{row['issue']}: filed_hash {row['filed_hash']!r} is not "
                f"twelve lowercase hex characters"
            )
        if row["recomputed_hash"] != row["filed_hash"]:
            raise CorpusValidationError(
                f"issue #{row['issue']}: recomputed_hash {row['recomputed_hash']!r} "
                f"!= filed_hash {row['filed_hash']!r}"
            )


def render(rows: list[dict], repo: str) -> str:
    payload = {
        "_generated_by": (
            f"tools/build_devtest_issue_corpus.py, via "
            f"'gh issue list --repo {repo} --state all --limit 300 "
            f"--json number,title,state,body'"
        ),
        "rows": rows,
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_devtest_issue_corpus.py",
        description=(
            "Derive, validate and emit the 26-row filed [dev test] issue "
            "corpus from henols/firestarter_prom via the gh CLI."
        ),
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=_TARGET_DEFAULT,
        help=(
            "Output path for the corpus JSON (default: "
            "tests/fixtures/devtest_issue_corpus.json)."
        ),
    )
    parser.add_argument(
        "--repo",
        default=_REPO_DEFAULT,
        help=f"GitHub repo to enumerate (default: {_REPO_DEFAULT}).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Derive, validate, render, and compare against the existing "
            "--target file. Return non-zero on mismatch without writing."
        ),
    )
    return parser


def main() -> int:
    args = _build_argparser().parse_args()

    try:
        rows = derive_rows(args.repo)
    except CorpusError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        validate_rows(rows)
    except CorpusValidationError as exc:
        print(f"ERROR: derivation validation failed: {exc}", file=sys.stderr)
        return 1

    output = render(rows, args.repo)

    if args.check:
        if not args.target.is_file():
            print(f"DRIFT: target does not exist: {args.target}", file=sys.stderr)
            return 1
        existing = args.target.read_text(encoding="utf-8")
        if existing != output:
            print(
                f"DRIFT: {args.target} differs from a fresh regeneration "
                f"({len(existing)} bytes committed vs {len(output)} bytes "
                f"regenerated)",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {args.target} matches a fresh regeneration")
        return 0

    args.target.parent.mkdir(parents=True, exist_ok=True)
    args.target.write_text(output, encoding="utf-8", newline="\n")
    print(f"OK: wrote {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
