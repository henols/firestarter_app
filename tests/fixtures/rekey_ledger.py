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

  RK-174-02-rejected-sdp-step-pruning -- shape_id `m27c512-full-all-ok`.
  Owner: `rejected` -- dropping unsupported SDP steps from `Plan.steps` is
  Out of Scope at milestone level. Seeded anyway: the ledger records the
  blast radius that EXISTS, not only the one being taken, so a later phase
  that accidentally prunes the six unsupported SDP steps reddens against a
  row that already names the consequence for 637 of 677 chips.

  RK-174-03-p181-canonical-naming-avoided -- shape_id
  `m27c512-full-canonical-name`. Owner: Phase 181. Mechanism: normalising
  the raw CLI token in `parts[0]` to the database `part_number` spelling.
  D-2 makes canonical naming additive, so this re-key is avoided -- the row
  exists so that avoidance is checkable rather than merely asserted.

  RK-174-04-p179-uv-blank-check-abort -- shape_id
  `m27c512-full-blank-check-bad`. Owner: Phase 179. Mechanism, stated
  correctly (RESEARCH correction C3): the collapse rides the `blank-check`
  verdict triple moving from OK to BAD. It does NOT ride
  `repeat_policy_tag` -- the collapsed `write`/`verify` steps carry
  `run_count == 0`, not `1`, so that tag never fires. CONTEXT.md D-12 row 4
  and the milestone research both named the tag; measurement found it
  stays empty, and this row's provenance carries the correction so Phase
  179 is measured against the mechanism that actually operates.

  RK-174-05-p177-match-bucket-d4d6 -- shape_id `at28c256-full-all-ok-sdp`.
  Owner: Phase 177. Mechanism: D-4/D-6 add a `match` bucket to the
  fingerprint classifier, so an all-OK AT28C256 run's `indeterminate`
  classifications become `match` and the run becomes promotable. This is
  the deliberate one-time re-key REQUIREMENTS.md already declares, and
  this is the row it lands on.

  RK-174-06-p178-status-axis-must-not-rekey -- shape_id
  `sst27sf512-full-all-ok`. Owner: Phase 178. The inverse of every row
  above: ATTR-04 requires the status axis to be additive and excluded from
  the hash, and ATTR-01's acceptance criterion is that this phase's oracle
  reports zero unexpected hash changes when the status axis is exercised.
  An `after_hash` of `None` on a row Phase 178 owns IS the assertion
  "Phase 178 declared no re-key here" -- the same shape D-09's un-declared
  case already gives, used deliberately here rather than incidentally.
  Without this row, ATTR-04 would have nothing to confirm against, which
  is the reason D-05 rejected the roadmap's floor of four rows.
"""

LEDGER = (
    ("sst27sf512-six-step", "4dc282a5d596", None, "RK-174-01-p177-readback-gating"),
    (
        "m27c512-full-all-ok",
        "6d3afbc52315",
        None,
        "RK-174-02-rejected-sdp-step-pruning",
    ),
    (
        "m27c512-full-canonical-name",
        "776846bf2dc8",
        None,
        "RK-174-03-p181-canonical-naming-avoided",
    ),
    (
        "m27c512-full-blank-check-bad",
        "077a32d1a5c4",
        None,
        "RK-174-04-p179-uv-blank-check-abort",
    ),
    (
        "at28c256-full-all-ok-sdp",
        "52fb759dc48c",
        None,
        "RK-174-05-p177-match-bucket-d4d6",
    ),
    (
        "sst27sf512-full-all-ok",
        "4b3e52cab987",
        None,
        "RK-174-06-p178-status-axis-must-not-rekey",
    ),
)
