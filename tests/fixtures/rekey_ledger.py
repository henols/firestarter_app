"""
Append-only re-key ledger for the blast-radius invariance harness (Phase
174, D-09, D-11, D-12, D-13).

`LEDGER` is a tuple of plain four-tuples `(shape_id, before_hash,
after_hash, ledger_id)` -- `str`, `str`, `str | None`, `str` -- and NOTHING
ELSE. No dataclass, no enum, no computed expression: the meta-side checker
(`tools/rekey/check_rekey_ledger.py`) parses this file with `ast.parse` plus
`ast.literal_eval` on the `LEDGER` assignment's value rather than importing
it, so every row must be reachable by `ast.literal_eval` (D-13). This module
is deliberately free of executable logic for the same reason D-11 requires a
declared re-key to touch only an `after_hash` and its `MILESTONES.md`
counterpart in one commit -- logic here would drag a second concern into
that commit.

Append, never edit (D-09): `after_hash` is `None` until a phase declares the
re-key, and the assertion elsewhere in this tree is `current == after_hash
if after_hash is not None else before_hash`. The original `before_hash`
never leaves the tree.

`ledger_id` grammar: `RK-174-<NN>-<owner>-<slug>`, where `<owner>` is
`p<phase-number>` for a row an owning phase will be measured against, or the
literal `rejected` for a re-key the milestone has decided not to take.

Row provenance:

  RK-174-01-p177-readback-gating -- shape_id `sst27sf512-six-step`. Owner:
  Phase 177. Mechanism: gating the fingerprint read-back on step failure
  empties the `write`/`verify` steps' `indeterminate` fingerprint
  classification. Measured PROJECTED `after_hash` (not yet declared):
  `60a031573aab`. A projected value belongs here in prose and in
  `MILESTONES.md`'s prose, never in the `after_hash` column -- a filled
  `after_hash` means "declared", and Phase 177 has not landed.
"""

LEDGER = (
    ("sst27sf512-six-step", "4dc282a5d596", None, "RK-174-01-p177-readback-gating"),
)
