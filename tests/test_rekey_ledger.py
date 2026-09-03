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

from .fixtures.rekey_ledger import LEDGER
from .fixtures.report_shapes import SHAPE_IDS, build_shape

_LEDGER_ID_RE = re.compile(r"^RK-174-\d+-[A-Za-z0-9]+-[A-Za-z0-9-]+$")


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
