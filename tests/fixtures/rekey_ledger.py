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
  moves the `write`/`verify` steps' `indeterminate` fingerprint
  classification to `match`. DECLARED this phase: `after_hash`
  `7fb88e0b07d6`, measured in `firestarter_app/.venv311` off the re-pointed
  `_build_sst27sf512_six_step` builder. The value this row's provenance
  previously carried as the projected `after_hash`, `60a031573aab`, is
  FALSIFIED and kept here as the superseded claim rather than deleted:
  that projection modelled the fingerprint being DROPPED entirely on a
  pass (`step_specs` classification `None`), which PRUNE-03 forbids -- a
  passing write/verify step always reports a `match` fingerprint, real or
  synthesized, never an absent one. `evidence/177-01-red-capture.txt`'s
  `collapse_finding` measured that the dropped-fingerprint projection
  collapses onto this row's own re-pointed value instead of producing a
  genuinely distinct shape, which is what falsified it.

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
  verdict triple moving, NOT `repeat_policy_tag` -- the collapsed
  `write`/`verify` steps carry `run_count == 0`, not `1`, so that tag never
  fires. CONTEXT.md D-12 row 4 and the milestone research both named the
  tag; measurement found it stays empty, and this row's provenance carries
  the correction so Phase 179 is measured against the mechanism that
  actually operates. DECLARED this phase: `after_hash` `e42f1567967a`,
  measured off the committed `_build_m27c512_full_blank_check_bad` builder
  after Phase 179 landed `FLAG_SKIP_BLANK_CHECK` and the UV blank-check's
  execution-time verdict adjudication. The triple moves `BAD -> SKIPPED`,
  not the `OK -> BAD` direction this row was seeded under -- the pre-write
  UV blank-check now reports a finding rather than a chip fault, so it no
  longer spends a `BAD` verdict. `.planning/research/PITFALLS.md:186-188`
  step 2's claim that this step's `error_code` trips `hardware_refused` and
  aborts cycle 2 is FALSIFIED, recorded rather than deleted (as
  `RK-174-01`'s note keeps its own falsified `60a031573aab`): a UV plan's
  `cycle_block_bounds` is `(3, 6)` and the standalone blank-check step sits
  OUTSIDE that block, so the abort actually measured came from the WRITE
  step's own firmware refusal, not from this step at all.

  RK-174-05-p177-match-bucket-d4d6 -- shape_id `at28c256-full-all-ok-sdp`.
  Owner: Phase 177. Mechanism: D-4/D-6 add a `match` bucket to the
  fingerprint classifier, so an all-OK AT28C256 run's `indeterminate`
  classifications become `match` and the run becomes promotable. This is
  the deliberate one-time re-key REQUIREMENTS.md already declares, and
  this is the row it lands on. DECLARED this phase: `after_hash`
  `050ad3830704`, measured in `firestarter_app/.venv311` off the
  unmodified real-path builder -- the classifier's own new bucket is the
  whole mechanism, no builder edit was needed.

  RK-174-06-p178-status-axis-must-not-rekey -- shape_id
  `sst27sf512-full-all-ok`. Owner: Phase 178. The inverse of every row
  above: ATTR-04 requires the status axis to be additive and excluded from
  the hash, and ATTR-01's acceptance criterion is that this phase's oracle
  reports zero unexpected hash changes when the status axis is exercised.
  DECLARED this phase, NOT by Phase 178: `after_hash` `14d306256076`,
  because Phase 177's own match-bucket mechanism moved this shape's
  fingerprint too (it is a non-SDP all-OK run reaching the classifier's
  new bucket exactly like `at28c256-full-all-ok-sdp` above) and
  `test_every_ledger_row_shape_id_resolves_and_recomputes` asserts an
  undeclared row's `before_hash` still reproduces from a fresh build --
  leaving this row undeclared after its shape moved would make that
  assertion permanently false. This does NOT satisfy ATTR-04: Phase 177's
  re-key is a DIFFERENT mechanism (the classifier's `match` bucket) from
  the one ATTR-04 exists to gate (the status axis), so Phase 178 still has
  nothing to confirm against at the OLD anchor. `RK-174-09-p178-status-
  axis-must-not-rekey-reanchored` (below) re-anchors ATTR-04's assertion
  at this row's new `after_hash`, so Phase 178 measures from the value its
  own mechanism must not move, not from a value Phase 177 already moved
  for an unrelated reason.

  RK-174-07-p177-w27e257-all-ok-synthesized-match -- shape_id
  `w27e257-full-all-ok`. Owner: Phase 177. Not seeded by Phase 174 (no row
  existed for this shape). Mechanism: identical to RK-174-05 -- a non-SDP
  all-OK run reaching the classifier's new `match` bucket. Appended,
  never inserted out of ledger_id order, per D-09. DECLARED this phase:
  `after_hash` `3a9f95aba65e`, measured in `firestarter_app/.venv311`.

  RK-174-09-p178-status-axis-must-not-rekey-reanchored -- shape_id
  `sst27sf512-full-all-ok`. Owner: Phase 178. Re-anchors
  `RK-174-06-p178-status-axis-must-not-rekey`'s ATTR-04 assertion at the
  value Phase 177's fingerprint re-key left it at (`14d306256076`), since
  Phase 177 moved the baseline that row was anchored to for a reason
  unrelated to the status axis. `before_hash` is `RK-174-06`'s newly
  declared `after_hash`, not its original one -- Phase 178 measures
  whether exercising the status axis moves the fingerprint FROM HERE, the
  post-177 value, never from `RK-174-06`'s superseded pre-177 anchor.
  `after_hash` stays `None` until Phase 178 lands its own confirmation
  that no further re-key occurred. Two rows legitimately naming the same
  `shape_id` is expressly permitted -- `test_shape_id_ledger_id_pairs_are_
  unique`'s own docstring states a shape can be re-keyed twice by two
  different phases, and only the `ledger_id` must stay unique, which this
  row's own distinct id satisfies.

`gh47-sst27sf512-pass`'s filed hash (`f9dbc31dcd27`) is named nowhere in
this ledger and deliberately gets no row: D-177-6 rules it stays inside
D-177-3's stated re-pointing scope (the two `sst27sf512-six-step*`
builders only), its hand-specified `step_specs` are untouched by this
phase, and its `FROZEN_HASHES` entry does not move -- so there is no
re-key to declare. `177-REKEY-MAPPING.md` and `MILESTONES.md`'s
corrections table record the falsified `1f812aae49ca` projection for it
as a superseded claim, not as a declared row here.
"""

LEDGER = (
    (
        "sst27sf512-six-step",
        "4dc282a5d596",
        "7fb88e0b07d6",
        "RK-174-01-p177-readback-gating",
    ),
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
        "e42f1567967a",
        "RK-174-04-p179-uv-blank-check-abort",
    ),
    (
        "at28c256-full-all-ok-sdp",
        "52fb759dc48c",
        "050ad3830704",
        "RK-174-05-p177-match-bucket-d4d6",
    ),
    (
        "sst27sf512-full-all-ok",
        "4b3e52cab987",
        "14d306256076",
        "RK-174-06-p178-status-axis-must-not-rekey",
    ),
    (
        "w27e257-full-all-ok",
        "22908e2954c3",
        "3a9f95aba65e",
        "RK-174-07-p177-w27e257-all-ok-synthesized-match",
    ),
    (
        "sst27sf512-full-all-ok",
        "14d306256076",
        None,
        "RK-174-09-p178-status-axis-must-not-rekey-reanchored",
    ),
)
