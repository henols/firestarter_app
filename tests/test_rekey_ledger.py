"""
Append-only re-key ledger sweep (Phase 174, D-09, D-11, D-12, D-13).

Anti-vacuity rule: a bare `assert row[2] == expected` without first proving
`row[0]` resolves to a real, registered builder would let a hand-typed hash
literal that nothing in the tree can compute survive silently -- exactly
what D-09's "no hash literal that no committed builder can compute"
prohibition forbids. Every leg below resolves `row[0]` through `SHAPE_IDS`
before recomputing, so an unresolvable `shape_id` fails the FIRST assertion
rather than passing a fingerprint comparison vacuously.

Reachability: see
`.planning/phases/174-blast-radius-invariance-harness/evidence/
174-01-anti-vacuity-red-green.txt` for the transcribed RED output of the
meta-side checker's fail-closed legs, and 174-01-SUMMARY.md for the
recorded proof.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .fixtures.rekey_ledger import LEDGER
from .fixtures.report_shapes import SHAPE_IDS, build_shape

_LEDGER_ID_RE = re.compile(r"^RK-174-\d+-[A-Za-z0-9]+-[A-Za-z0-9-]+$")

_REPO_ROOT = Path(__file__).parent.parent.parent
_CHECKER = _REPO_ROOT / "tools" / "rekey" / "check_rekey_ledger.py"
_PLANTED_LEDGER = Path(__file__).parent / "fixtures" / "planted_rekey_mutation.py"


def _run_checker(extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Invoke the meta-side checker as a real subprocess -- the same
    env-override/subprocess seam `check_diagnostic_report_claims.py`'s own
    paired test uses -- resolving the repository root explicitly rather than
    relying on the checker's own cwd default."""
    args = [sys.executable, str(_CHECKER), "--repo-root", str(_REPO_ROOT)]
    if extra_args:
        args.extend(extra_args)
    return subprocess.run(args, capture_output=True, text=True)


def test_every_ledger_row_is_a_well_formed_four_tuple() -> None:
    for row in LEDGER:
        assert isinstance(row, tuple)
        assert len(row) == 4
        shape_id, before_hash, after_hash, ledger_id = row
        assert isinstance(shape_id, str)
        assert isinstance(before_hash, str)
        assert after_hash is None or isinstance(after_hash, str)
        assert isinstance(ledger_id, str)


def test_every_ledger_row_shape_id_resolves_and_recomputes() -> None:
    from firestarter.diagnostic_report import dedup_fingerprint

    for shape_id, before_hash, after_hash, ledger_id in LEDGER:
        assert shape_id in SHAPE_IDS, (
            f"{ledger_id} names shape_id {shape_id!r}, which is not in "
            f"SHAPE_IDS {SHAPE_IDS}"
        )
        expected = after_hash if after_hash is not None else before_hash
        computed = dedup_fingerprint(build_shape(shape_id))
        assert computed == expected, (
            f"{ledger_id}: expected {expected} "
            f"({'after' if after_hash is not None else 'before'}_hash), got "
            f"{computed} from a freshly-built {shape_id!r}"
        )


def test_every_ledger_row_ledger_id_matches_grammar() -> None:
    for _shape_id, _before_hash, _after_hash, ledger_id in LEDGER:
        assert ledger_id, "ledger_id must be non-empty"
        assert _LEDGER_ID_RE.match(ledger_id), (
            f"ledger_id {ledger_id!r} does not match the "
            "RK-174-<NN>-<owner>-<slug> grammar"
        )


def test_check_rekey_ledger_clean_input_exits_zero() -> None:
    """Leg 1 of the anti-vacuity contract: the checker on the real ledger and
    the real MILESTONES.md succeeds, proving the gate is not accidentally
    always-red."""
    result = _run_checker()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK:" in result.stdout


def test_check_rekey_ledger_planted_input_exits_one() -> None:
    """Leg 2: pointing the checker at a ledger declaring an undeclared
    re-key nobody recorded in MILESTONES.md must fail, naming the offending
    ledger_id."""
    result = _run_checker(["--ledger", str(_PLANTED_LEDGER)])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "RK-174-99-planted-undeclared" in result.stdout


def test_check_rekey_ledger_fails_closed_on_missing_ledger(tmp_path: Path) -> None:
    """Leg 3a: a nonexistent --ledger path must fail closed, never silently
    pass with a target quietly skipped."""
    result = _run_checker(["--ledger", str(tmp_path / "nope.py")])
    assert result.returncode == 2, result.stdout + result.stderr


def test_check_rekey_ledger_fails_closed_on_missing_milestones(tmp_path: Path) -> None:
    """Leg 3b: a nonexistent --milestones path must fail closed."""
    result = _run_checker(["--milestones", str(tmp_path / "nope.md")])
    assert result.returncode == 2, result.stdout + result.stderr


def test_check_rekey_ledger_fails_closed_on_unparsable_ledger(tmp_path: Path) -> None:
    """Leg 3c: a ledger file that parses as valid Python but declares no
    LEDGER assignment must fail closed, not silently produce zero rows."""
    bad = tmp_path / "noledger.py"
    bad.write_text("OTHER = 1\n", encoding="utf-8")
    result = _run_checker(["--ledger", str(bad)])
    assert result.returncode == 2, result.stdout + result.stderr
