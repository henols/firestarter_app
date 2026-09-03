#!/usr/bin/env python3
"""
Deterministic `to_dict()` snapshot generator for the blast-radius invariance
harness's report corpus (Phase 174, GATE-05, D-01, D-07).

Builds each named `shape_id` via `tests.fixtures.report_shapes.build_shape`,
composes a real `DbDiff` via `build_db_diff` (so `db_diff` is a populated
object rather than `None`), normalises the one volatile field `to_dict()`
produces (`generated`, a live UTC timestamp) to the fixed sentinel
`1970-01-01T00:00:00Z`, and writes the result as
`json.dumps(payload, indent=2, sort_keys=True) + "\\n"` via
`Path.write_text(encoding="utf-8", newline="\\n")` for byte-identical
output. Also stamps a `_generated_by` key naming this script -- JSON has no
comment syntax, so that key is the do-not-edit banner, and the drift test in
`tests/test_blast_radius_invariance.py` asserts it absolutely, and calls
`render_shape` directly rather than re-deriving its own copy of this
normalisation -- there is exactly one normaliser.

Mirrors `tools/gen_sdp_bus_config.py`'s shape: stdlib plus argparse only,
validate before emit, a `--check` drift mode, `sys.exit(main())`.

Exit codes:
  0 -- wrote every target (or, under --check, every target already matched
       a fresh regeneration)
  1 -- --check found drift, or a --check target does not exist
  2 -- a --shape value is not in tests.fixtures.report_shapes.SHAPE_IDS
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
_APP_ROOT = _TOOLS_DIR.parent
sys.path.insert(0, str(_APP_ROOT))

from firestarter.database import EpromDatabase
from firestarter.diagnostic_report import build_db_diff
from tests.fixtures.report_shapes import SHAPE_IDS, build_shape

_GENERATED_BY = "tools/snapshot_report_shapes.py"
_GENERATED_SENTINEL = "1970-01-01T00:00:00Z"
_TARGET_DIR_DEFAULT = _APP_ROOT / "tests" / "fixtures" / "reports"

_DB = EpromDatabase(skip_local_override=True)


def normalise_snapshot(payload: dict) -> dict:
    """Replace the live `generated` timestamp with the fixed sentinel and
    stamp `_generated_by`. The ONE normaliser both this script and the
    drift test in `tests/test_blast_radius_invariance.py` apply, by calling
    `render_shape` directly -- there must be exactly one, or the two could
    silently diverge."""
    normalised = dict(payload)
    normalised["generated"] = _GENERATED_SENTINEL
    normalised["_generated_by"] = _GENERATED_BY
    return normalised


def render_shape(shape_id: str) -> str:
    """Build `shape_id`, compose a real `DbDiff`, normalise, and render as
    the byte-identical JSON text this script writes and the drift test
    compares against."""
    report = build_shape(shape_id)
    report.db_diff = build_db_diff(report.auto_capture.chip, _DB, report.results)
    payload = normalise_snapshot(report.to_dict())
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="snapshot_report_shapes.py",
        description=(
            "Generate committed to_dict() snapshots for the blast-radius "
            "invariance harness's report corpus (GATE-05)."
        ),
    )
    p.add_argument(
        "--target-dir",
        type=Path,
        default=_TARGET_DIR_DEFAULT,
        help=(
            "Output directory for the generated snapshots "
            "(default: tests/fixtures/reports)."
        ),
    )
    p.add_argument(
        "--shape",
        action="append",
        dest="shapes",
        default=None,
        help="A shape_id to snapshot; repeatable. Default: every id in SHAPE_IDS.",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help=(
            "Regenerate every target shape and compare against the "
            "existing committed file under --target-dir. Print a drift "
            "line and return non-zero on any mismatch or missing target, "
            "without writing."
        ),
    )
    return p


def main() -> int:
    args = _build_argparser().parse_args()
    shapes = args.shapes if args.shapes else list(SHAPE_IDS)

    for shape_id in shapes:
        if shape_id not in SHAPE_IDS:
            print(f"ERROR: {shape_id!r} is not in SHAPE_IDS {SHAPE_IDS}", file=sys.stderr)
            return 2

    if args.check:
        drifted = False
        for shape_id in shapes:
            target = args.target_dir / f"{shape_id}.json"
            fresh = render_shape(shape_id)
            if not target.is_file():
                print(f"DRIFT: target does not exist: {target}", file=sys.stderr)
                drifted = True
                continue
            existing = target.read_text(encoding="utf-8")
            if existing != fresh:
                print(
                    f"DRIFT: {target} differs from a fresh regeneration "
                    f"({len(existing)} bytes committed vs {len(fresh)} bytes "
                    "regenerated)",
                    file=sys.stderr,
                )
                drifted = True
        if drifted:
            return 1
        print(
            f"OK: {len(shapes)} snapshot(s) under {args.target_dir} match a "
            "fresh regeneration"
        )
        return 0

    args.target_dir.mkdir(parents=True, exist_ok=True)
    for shape_id in shapes:
        target = args.target_dir / f"{shape_id}.json"
        target.write_text(render_shape(shape_id), encoding="utf-8", newline="\n")
        print(f"OK: wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
