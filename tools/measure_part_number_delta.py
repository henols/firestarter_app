#!/usr/bin/env python3
"""
Generator for the raw-CLI-token -> `part_number` delta artifact (Phase 174,
D-14/D-15/D-16, GATE-04, plan 174-04 task 3).

Measures the delta through the path the CLI actually takes:
`firestarter.chip_resolver.resolve_chip` for the support-status verdict, and
`firestarter.database.EpromDatabase.get_eprom_config`'s raw config for the
resolved `part_number` -- `resolve_chip` itself returns
`convert_to_programmer`'s output, whose keys are hyphenated and which does
NOT carry a `part_number` key. Every alias is resolved through
`EpromDatabase(skip_local_override=True)`, never a developer's own
`~/.firestarter/database.json` override, so the measurement is the same
machine to machine.

`db.proms` is descended two levels -- manufacturer key to a list of chip
records -- because a single-level scan iterates manufacturer names and every
downstream count would pass vacuously.

Exit codes:
  0 -- derivation valid, artifact emitted successfully (or --check found no
       drift)
  1 -- derived aggregate failed validation, or --check found the committed
       artifact stale or missing
  2 -- the --issues input path is missing or unparsable
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).parent
_APP_ROOT = _TOOLS_DIR.parent
_TARGET_DEFAULT = _APP_ROOT / "tests" / "fixtures" / "part_number_delta.json"
_ISSUES_DEFAULT = _APP_ROOT / "tests" / "fixtures" / "devtest_issue_corpus.json"

sys.path.insert(0, str(_APP_ROOT))


class DerivationError(Exception):
    """The --issues input could not be read (exit code 2)."""


class ValidationError(Exception):
    """A derived aggregate failed a validate-before-emit invariant (exit
    code 1)."""


def _load_filed_issues(issues_path: Path) -> list[dict]:
    if not issues_path.is_file():
        raise DerivationError(f"issues corpus not found: {issues_path}")
    try:
        payload = json.loads(issues_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DerivationError(f"issues corpus is not valid JSON: {exc}") from exc
    try:
        return payload["rows"]
    except (KeyError, TypeError) as exc:
        raise DerivationError(f"issues corpus has no 'rows' key: {exc}") from exc


def derive(issues_path: Path) -> dict:
    from firestarter.chip_resolver import resolve_chip
    from firestarter.database import EpromDatabase
    from firestarter.exceptions import ChipNotFoundError, ChipNotImplementedError

    filed_rows = _load_filed_issues(issues_path)

    db = EpromDatabase(skip_local_override=True)

    rows = 0
    vendors = set()
    part_numbers = set()
    part_numbers_with_comma = 0
    lowercase_form_differs = 0
    for manufacturer, ics in db.proms.items():
        vendors.add(manufacturer)
        for ic in ics:
            rows += 1
            part_number = ic.get("part_number", "")
            part_numbers.add(part_number)
            if "," in part_number:
                part_numbers_with_comma += 1
            if part_number != part_number.lower():
                lowercase_form_differs += 1

    aliases: set[str] = set()
    for part_number in part_numbers:
        for alias in part_number.split(","):
            aliases.add(alias.strip())

    alias_rows = []
    differ_count = 0
    match_count = 0
    comma_joined_count = 0
    not_implemented_count = 0
    not_found_count = 0
    for alias in aliases:
        token = alias.lower()
        cfg, _manufacturer = db.get_eprom_config(token)
        resolved = (cfg or {}).get("part_number")
        differs = token != resolved
        if differs:
            differ_count += 1
        else:
            match_count += 1
        comma_joined = bool(resolved) and "," in resolved
        if comma_joined:
            comma_joined_count += 1

        status = "ok"
        try:
            resolve_chip(token, db)
        except ChipNotImplementedError:
            status = "not-implemented"
            not_implemented_count += 1
        except ChipNotFoundError:
            status = "not-found"
            not_found_count += 1

        alias_rows.append(
            {
                "token": token,
                "resolved_part_number": resolved,
                "differs": differs,
                "resolves_to_comma_joined": comma_joined,
                "resolve_status": status,
            }
        )
    alias_rows.sort(key=lambda r: r["token"])

    filed_issue_rows = []
    for row in filed_rows:
        raw_token = row["raw_token"]
        token = raw_token.lower()
        cfg, _manufacturer = db.get_eprom_config(token)
        resolved = (cfg or {}).get("part_number")
        filed_issue_rows.append(
            {
                "issue": row["issue"],
                "raw_token": raw_token,
                "resolved_part_number": resolved,
                "differs": token != resolved,
            }
        )
    filed_issue_rows.sort(key=lambda r: r["issue"])

    aggregate = {
        "rows": rows,
        "vendors": len(vendors),
        "distinct_part_numbers": len(part_numbers),
        "part_numbers_with_comma": part_numbers_with_comma,
        "distinct_aliases": len(aliases),
        "aliases_token_differs_from_part_number": differ_count,
        "aliases_token_equals_part_number": match_count,
        "aliases_resolving_to_comma_joined": comma_joined_count,
        "aliases_chip_not_implemented": not_implemented_count,
        "aliases_chip_not_found": not_found_count,
        "part_numbers_not_lowercase_published_proxy": lowercase_form_differs,
    }

    return {
        "_generated_by": (
            f"tools/measure_part_number_delta.py, via chip_resolver.resolve_chip + "
            f"EpromDatabase.get_eprom_config over the shipped chip_database.json, "
            f"with --issues {issues_path.name}"
        ),
        "aggregate": aggregate,
        "aliases": alias_rows,
        "filed_issues": filed_issue_rows,
    }


def validate(payload: dict) -> None:
    aggregate = payload["aggregate"]
    aliases = payload["aliases"]
    filed_issues = payload["filed_issues"]

    if len(aliases) != aggregate["distinct_aliases"]:
        raise ValidationError(
            f"alias array length {len(aliases)} != aggregate "
            f"distinct_aliases {aggregate['distinct_aliases']}"
        )

    status_sum = (
        aggregate["aliases_chip_not_implemented"]
        + aggregate["aliases_chip_not_found"]
        + sum(1 for a in aliases if a["resolve_status"] == "ok")
    )
    if status_sum != len(aliases):
        raise ValidationError(
            f"the three resolve-status counts sum to {status_sum}, expected "
            f"{len(aliases)}"
        )

    if len(filed_issues) != 26:
        raise ValidationError(f"expected 26 filed_issues rows, got {len(filed_issues)}")

    for row in filed_issues:
        if row["resolved_part_number"] is None:
            raise ValidationError(
                f"filed issue #{row['issue']}: raw_token {row['raw_token']!r} does "
                f"not resolve to any chip in the shipped database"
            )

    differ_plus_match = (
        aggregate["aliases_token_differs_from_part_number"]
        + aggregate["aliases_token_equals_part_number"]
    )
    if differ_plus_match != aggregate["distinct_aliases"]:
        raise ValidationError(
            f"differ+match {differ_plus_match} != distinct_aliases "
            f"{aggregate['distinct_aliases']}"
        )


def render(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="measure_part_number_delta.py",
        description=(
            "Measure the raw-CLI-token -> part_number delta across the "
            "shipped chip database, through chip_resolver.resolve_chip."
        ),
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=_TARGET_DEFAULT,
        help=(
            "Output path for the delta artifact (default: "
            "tests/fixtures/part_number_delta.json)."
        ),
    )
    parser.add_argument(
        "--issues",
        type=Path,
        default=_ISSUES_DEFAULT,
        help=(
            "Path to the filed dev-test issue corpus JSON (default: "
            "tests/fixtures/devtest_issue_corpus.json). Fails closed when "
            "the given path is missing -- never silently falls back to the "
            "real file."
        ),
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
        payload = derive(args.issues)
    except DerivationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        validate(payload)
    except ValidationError as exc:
        print(f"ERROR: derivation validation failed: {exc}", file=sys.stderr)
        return 1

    output = render(payload)

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
