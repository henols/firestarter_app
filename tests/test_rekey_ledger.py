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
recorded proof. Plan 174-03's reverse-direction leg
(`test_check_rekey_ledger_orphan_milestones_row_exits_one`) was likewise
observed RED against a `MILESTONES.md` copy carrying a planted
`RK-174-97-orphan-row` before being trusted -- transcribed verbatim in
`.planning/phases/174-blast-radius-invariance-harness/evidence/
174-03-ledger-closure.txt`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_check_rekey_ledger_orphan_milestones_row_exits_one(tmp_path: Path) -> None:
    """Reverse-direction anti-vacuity leg (plan 174-03): a `MILESTONES.md`
    copy carrying an EXTRA `RK-174-` row for a `ledger_id` the app ledger
    does not have must fail, naming that orphan `ledger_id` -- proving the
    direction D-13 does not spell out (MILESTONES.md -> ledger, not just
    ledger -> MILESTONES.md) is enforced too, not merely the forward
    direction the clean-input and planted-input legs above already
    exercise."""
    real_milestones = _REPO_ROOT / ".planning" / "MILESTONES.md"
    orphan = tmp_path / "orphan.md"
    orphan.write_text(
        real_milestones.read_text(encoding="utf-8")
        + "| RK-174-97-orphan-row | sst27sf512-six-step | planted orphan | "
        "rejected | 4dc282a5d596 | 000000000001 | 2026-09-03 |\n",
        encoding="utf-8",
    )
    result = _run_checker(["--milestones", str(orphan)])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "RK-174-97-orphan-row" in result.stdout


def test_duplicate_milestones_row_for_one_ledger_id_fails_closed(
    tmp_path: Path,
) -> None:
    """CR-02 leg (a): a fabricated, fully-declared row for
    `RK-174-01-p177-readback-gating` inserted immediately BEFORE the real
    row for the same id is invisible to the pre-fix checker -- the last
    row silently wins and the fabrication is never seen. The fixed checker
    must instead collide on the duplicate and exit 2, naming the id."""
    real_milestones = _REPO_ROOT / ".planning" / "MILESTONES.md"
    src = real_milestones.read_text(encoding="utf-8")
    fabricated = (
        "| RK-174-01-p177-readback-gating | sst27sf512-six-step | "
        "fabricated declared re-key | Phase 177 | 4dc282a5d596 | "
        "ffffffffffff | 2026-09-03 |"
    )
    lines = []
    for line in src.splitlines():
        if line.startswith("| RK-174-01-p177-readback-gating |"):
            lines.append(fabricated)
        lines.append(line)
    dup = tmp_path / "dup.md"
    dup.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = _run_checker(["--milestones", str(dup)])
    assert result.returncode == 2, result.stdout + result.stderr
    assert "RK-174-01-p177-readback-gating" in result.stdout + result.stderr


def _mutated_row_01_milestones(tmp_path: Path, cells: list[str]) -> Path:
    """Rebuild row 01's line cell-by-cell off the REAL MILESTONES.md table,
    never by hand-typing a replacement row, so the mutation stays anchored
    to whatever the real row's `change` prose currently reads."""
    real_milestones = _REPO_ROOT / ".planning" / "MILESTONES.md"
    src = real_milestones.read_text(encoding="utf-8")
    row = next(
        line
        for line in src.splitlines()
        if line.startswith("| RK-174-01-p177-readback-gating |")
    )
    mutated = tmp_path / "mutated.md"
    mutated.write_text(
        src.replace(row, "| " + " | ".join(cells) + " |", 1), encoding="utf-8"
    )
    return mutated


def _row_01_cells() -> list[str]:
    real_milestones = _REPO_ROOT / ".planning" / "MILESTONES.md"
    src = real_milestones.read_text(encoding="utf-8")
    row = next(
        line
        for line in src.splitlines()
        if line.startswith("| RK-174-01-p177-readback-gating |")
    )
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def test_corrupted_undeclared_row_shape_id_exits_one(tmp_path: Path) -> None:
    """WR-01 / CR-02 leg (b): row 01's shape_id corrupted to a value that
    resolves to no builder must fail closed, naming both the corrupted
    value and the ledger's real shape_id."""
    c = _row_01_cells()
    mutated = _mutated_row_01_milestones(
        tmp_path, [c[0], "TOTALLY-WRONG-SHAPE", c[2], c[3], c[4], c[5], c[6]]
    )
    result = _run_checker(["--milestones", str(mutated)])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "TOTALLY-WRONG-SHAPE" in result.stdout
    assert "sst27sf512-six-step" in result.stdout


def test_corrupted_undeclared_row_before_hash_exits_one(tmp_path: Path) -> None:
    """WR-01 / CR-02 leg (b): row 01's before cell corrupted to an
    all-zero hash must fail closed, naming both the corrupted and the real
    before_hash."""
    c = _row_01_cells()
    mutated = _mutated_row_01_milestones(
        tmp_path, [c[0], c[1], c[2], c[3], "000000000000", c[5], c[6]]
    )
    result = _run_checker(["--milestones", str(mutated)])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "000000000000" in result.stdout
    assert "4dc282a5d596" in result.stdout


def test_uppercased_before_hash_exits_one(tmp_path: Path) -> None:
    """Comparison is case-sensitive and exact, not folded: uppercasing the
    real before_hash must still fail closed."""
    c = _row_01_cells()
    mutated = _mutated_row_01_milestones(
        tmp_path, [c[0], c[1], c[2], c[3], c[4].upper(), c[5], c[6]]
    )
    result = _run_checker(["--milestones", str(mutated)])
    assert result.returncode == 1, result.stdout + result.stderr
    assert c[4].upper() in result.stdout


@pytest.mark.parametrize(
    "after_cell",
    ["ffffffffffff", "ffffffffffffff", "4dc282a5d59", ""],
)
def test_after_cell_that_is_not_the_undeclared_literal_exits_one(
    tmp_path: Path, after_cell: str
) -> None:
    """The boundary one character either side of twelve hex characters, and
    the empty-cell edge, are all rejected: only the exact literal
    `(undeclared)` is legal for an undeclared row's after cell."""
    c = _row_01_cells()
    mutated = _mutated_row_01_milestones(
        tmp_path, [c[0], c[1], c[2], c[3], c[4], after_cell, c[6]]
    )
    result = _run_checker(["--milestones", str(mutated)])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "(undeclared)" in result.stdout


def test_milestones_with_zero_rekey_rows_exits_one(tmp_path: Path) -> None:
    """The `empty` edge: deleting the whole ledger table is not a route
    past the gate."""
    real_milestones = _REPO_ROOT / ".planning" / "MILESTONES.md"
    src = real_milestones.read_text(encoding="utf-8")
    emptied = tmp_path / "emptytable.md"
    emptied.write_text(
        "\n".join(line for line in src.splitlines() if not line.startswith("| RK-174-"))
        + "\n",
        encoding="utf-8",
    )
    result = _run_checker(["--milestones", str(emptied)])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "0 RK-174-" in result.stdout
    assert "6 row(s)" in result.stdout


def test_checker_error_output_is_order_stable(tmp_path: Path) -> None:
    """The `ordering` edge: two consecutive runs of the fixed checker over
    byte-identical inputs produce byte-identical output."""
    c = _row_01_cells()
    mutated = _mutated_row_01_milestones(
        tmp_path, [c[0], "TOTALLY-WRONG-SHAPE", c[2], c[3], c[4], c[5], c[6]]
    )
    first = _run_checker(["--milestones", str(mutated)])
    second = _run_checker(["--milestones", str(mutated)])
    assert first.stdout == second.stdout


def test_ledger_has_exactly_six_pre_seeded_rows() -> None:
    assert len(LEDGER) == 6, (
        f"LEDGER has {len(LEDGER)} rows, expected the six rows Phase 174 "
        "pre-seeded (D-12) -- a silent deletion or an unreviewed addition "
        "both change this count"
    )


def test_ledger_id_values_are_unique() -> None:
    ledger_ids = [row[3] for row in LEDGER]
    assert len(set(ledger_ids)) == len(ledger_ids), (
        f"LEDGER carries a duplicate ledger_id: {ledger_ids}"
    )


def test_shape_id_ledger_id_pairs_are_unique() -> None:
    """Two rows MAY legitimately name the same `shape_id` -- a shape can be
    re-keyed twice by two different phases -- and they must not merge;
    what must never repeat is the `ledger_id`, asserted above. This also
    checks the `(shape_id, ledger_id)` PAIR is unique, so a copy-paste row
    with a matching `shape_id` and a colliding `ledger_id` is caught by
    two independent assertions, not one."""
    pairs = [(row[0], row[3]) for row in LEDGER]
    assert len(set(pairs)) == len(pairs), (
        f"LEDGER carries a duplicate (shape_id, ledger_id) pair: {pairs}"
    )


def test_no_declared_row_has_after_hash_equal_to_before_hash() -> None:
    for shape_id, before_hash, after_hash, ledger_id in LEDGER:
        if after_hash is not None:
            assert after_hash != before_hash, (
                f"{ledger_id}: after_hash equals before_hash "
                f"({before_hash!r}) -- a declared re-key that moved "
                "nothing is a bookkeeping error, not a re-key"
            )


def test_ledger_sweep_is_well_defined_on_a_single_row_tuple() -> None:
    """Structural legality of a one-row ledger (D-09): the same sweep every
    other test in this module applies to the full six-row `LEDGER`,
    applied here to a LOCALLY-constructed single-row tuple, never to
    `LEDGER` itself."""
    from firestarter.diagnostic_report import dedup_fingerprint

    single = (LEDGER[0],)
    assert len(single) == 1
    shape_id, before_hash, after_hash, ledger_id = single[0]
    expected = after_hash if after_hash is not None else before_hash
    computed = dedup_fingerprint(build_shape(shape_id))
    assert computed == expected, (
        f"{ledger_id}: single-row sweep expected {expected}, got {computed}"
    )


def test_undeclared_after_hash_routes_to_before_hash_and_never_abstains() -> None:
    """An `after_hash` of `None` is the UN-DECLARED case, not a missing
    value -- it asserts against `before_hash` and never abstains. Every
    row in the pre-seeded ledger is undeclared today, so this sweeps all
    six rows rather than constructing a synthetic one."""
    from firestarter.diagnostic_report import dedup_fingerprint

    for shape_id, before_hash, after_hash, ledger_id in LEDGER:
        assert after_hash is None, (
            f"{ledger_id} is declared; this test only covers the "
            "undeclared case -- update it when a row is first declared"
        )
        computed = dedup_fingerprint(build_shape(shape_id))
        assert computed == before_hash, (
            f"{ledger_id}: undeclared row's before_hash {before_hash!r} "
            f"no longer matches a fresh build ({computed!r})"
        )


def test_ledger_id_order_is_ascending() -> None:
    ledger_ids = [row[3] for row in LEDGER]
    assert ledger_ids == sorted(ledger_ids), (
        f"LEDGER's ledger_id order is not ascending: {ledger_ids} != "
        f"{sorted(ledger_ids)} -- append-only order is specified and "
        "stable, so a row inserted out of sequence must fail here"
    )
