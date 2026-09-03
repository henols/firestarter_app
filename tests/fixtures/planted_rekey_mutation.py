"""
Fixture for tools/rekey/check_rekey_ledger.py -- NOT wired into any CLI
surface, never imported by production code, never imported by any pytest
module (anti-vacuity Leg 2, Phase 174 Task 3, T-174-01).

Deliberately-violating fixture: a standalone, syntactically-valid Python
module -- NOT a copy of the real rekey_ledger.py -- declaring one LEDGER row
whose after_hash (`000000000000`, plainly fictional) has no counterpart row
in .planning/MILESTONES.md, i.e. a declared re-key nobody recorded. This
file must never be imported; it exists only as ast.literal_eval input for
the paired pytest (tests/test_rekey_ledger.py), fed to the checker via its
`--ledger` path-override seam -- a real subprocess-level planted violation,
not an in-process synthetic.
"""

LEDGER = (
    (
        "sst27sf512-six-step",
        "4dc282a5d596",
        "000000000000",
        "RK-174-99-planted-undeclared",
    ),
)
