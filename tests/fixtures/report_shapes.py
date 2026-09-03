"""
Frozen `DiagnosticReport` shape builders for the blast-radius invariance
harness (Phase 174, GATE-01/02/05, D-01 through D-04).

Every shape in `SHAPE_IDS` is built by a plain function registered in
`_BUILDERS`, dispatched through `build_shape(shape_id)`. Each builder
constructs a REAL `DiagnosticReport` -- no dict-shaped stand-in, no
reimplementation of `dedup_fingerprint` -- so the hash asserted in
`FROZEN_HASHES` is always taken off the same object production hashes (D-01:
there is no deserializer in this module or anywhere else in the tree; the
committed `to_dict()` JSON snapshots beside this module are output-only).

`sst27sf512-six-step` is HAND-SPECIFIED, not `derive_plan`-derived (D-02
table 1). Research recovered its full canonical pre-image --
`SST27SF512|7|id=OK:|read=OK:|write=OK:indeterminate|verify=OK:indeterminate|
erase=OK:|blank-check=OK:` -- and the chip name in it is the UPPERCASE
`SST27SF512`, not the lowercase raw CLI token `sst27sf512`, so it does not
match any real `run_plan` output for this chip. It pins the hash FUNCTION
itself, immune to `chip_database.json` regeneration, at the frozen literal
`4dc282a5d596`.

`RESERVED_SHAPE_IDS` claims three `shape_id` names ahead of the phases that
will freeze them -- `prune03-synthesized-fingerprint-match` (Phase 177),
`attr01-status-axis-transport-fault` (Phase 178), `uv-slot-write-pass`
(Phase 179) -- without giving any of them a hash yet. The module-level
assertion below keeps that reservation from ever silently colliding with a
frozen `shape_id`; D-10's completeness pin over `SHAPE_IDS` stays exact
because a reserved name never enters that set.

`_REAL_DB` is `EpromDatabase(skip_local_override=True)`: without
`skip_local_override=True`, a developer's own `~/.firestarter/database.json`
override could move a frozen hash on one machine and nowhere else. No
builder in this task reads it, but every later phase's `derive_plan`-based
builder (D-02 table 2) shares this one instance.

Phase 174 plan 174-02 expands the tracer's one shape into the full
sixteen-name namespace. The first seven additions
(`sst27sf512-six-step-readback-gated`, `gh47-sst27sf512-pass`,
`gh28-m27c512-fail`, `gh20-at28c256-fail`, `gh23-w27e257-fail`,
`synthetic-arm4-no-ok`, `synthetic-arm4-empty-results`) are hand-specified
(D-02 table 1), directly constructed via `build_shape_from_step_specs` for
the same reason as the tracer -- either the shape pins the hash function
against a chip name production never emits, or reproduces a filed
community hash whose exact verdict/classification vector is transcribed
from an issue body rather than parsed, or exercises the one
`build_db_diff` arm no real `derive_plan` sweep reaches.
"""

from __future__ import annotations

from collections.abc import Callable

from firestarter.chip_test import Fingerprint, Plan, StepResult, WriteTarget
from firestarter.database import EpromDatabase
from firestarter.diagnostic_report import AutoCapture, DiagnosticReport, TransportHealth

_REAL_DB = EpromDatabase(skip_local_override=True)

_HOST_VERSION = "3.0.0b10"


def build_shape_from_step_specs(
    *,
    chip: str,
    protocol: str,
    step_specs: list[tuple[str, str, str | None, str]],
    run_counts: dict[str, int] | None = None,
    coverage_policy: str | None = None,
) -> DiagnosticReport:
    """General builder: a directly-constructed `DiagnosticReport`,
    generalizing `tests/test_diagnostic_report.py`'s `_minimal_report` and
    `_coverage_report`.

    `step_specs` is `(op, verdict, fingerprint_classification, reason)`
    tuples; `fingerprint_classification=None` means no `Fingerprint` is
    attached -- the non-destructive/graceful-degradation shape.

    `run_counts` is an optional `{op: run_count}` mapping stamped onto the
    built `StepResult.run_count` values AFTER construction -- the same
    post-construction seam `_coverage_report` uses for `write_target` -- so
    the real `repeat_policy_tag` fires. `StepResult.run_count` defaults to
    `0`, so an unstamped shape produces no repeat-policy tag at all.

    `coverage_policy` is an optional `region_policy` string; when given, a
    real `WriteTarget` is stamped onto the first `"write"` step's result so
    the real `coverage_tag` fires. Neither tag is ever appended by hand --
    both come out of `firestarter.chip_test.repeat_policy_tag` and
    `firestarter.chip_test.coverage_tag` exactly as production computes
    them.
    """
    results: list[StepResult] = []
    for op, verdict, cls, reason in step_specs:
        fingerprint = (
            Fingerprint(total=10, bad=0, bad_pct=0.0, classification=cls)
            if cls is not None
            else None
        )
        results.append(
            StepResult(op=op, verdict=verdict, reason=reason, fingerprint=fingerprint)
        )

    if run_counts:
        for result in results:
            if result.op in run_counts:
                result.run_count = run_counts[result.op]

    if coverage_policy is not None:
        write_result = next((r for r in results if r.op == "write"), None)
        if write_result is not None:
            write_result.write_target = WriteTarget(
                region=(0xFF00, 256),
                pattern=b"\xa5" * 256,
                masked=False,
                bits_cleared=0,
                bits_retained=0,
                current_source="tests.fixtures.report_shapes",
                region_policy=coverage_policy,
            )

    auto_capture = AutoCapture(
        host_version=_HOST_VERSION,
        chip=chip,
        protocol=protocol,
    )
    return DiagnosticReport(
        auto_capture=auto_capture,
        transport=TransportHealth(),
        plan=Plan(name=chip),
        results=results,
    )


def _build_sst27sf512_six_step() -> DiagnosticReport:
    """The hand-specified tracer shape (D-02 table 1). See the module
    docstring for its recovered canonical pre-image and why it is not
    `derive_plan`-derived."""
    return build_shape_from_step_specs(
        chip="SST27SF512",
        protocol="7",
        step_specs=[
            ("id", "OK", None, ""),
            ("read", "OK", None, ""),
            ("write", "OK", "indeterminate", ""),
            ("verify", "OK", "indeterminate", ""),
            ("erase", "OK", None, ""),
            ("blank-check", "OK", None, ""),
        ],
    )


def _build_sst27sf512_six_step_readback_gated() -> DiagnosticReport:
    """The PROJECTED after-shape of `RK-174-01-p177-readback-gating`
    (`tests/fixtures/rekey_ledger.py`) -- Phase 177 gates the fingerprint
    read-back on step failure, which empties the write/verify steps'
    `indeterminate` classification. Hand-specified for the same reason as
    the tracer shape it is paired with: freezing both halves of the pair
    means Phase 177's target is pinned from both sides, so a change that
    lands on neither value is as visible as one that lands on the wrong
    one. This is NOT yet the declared after_hash -- the ledger row's
    `after_hash` stays `None` until Phase 177 actually lands (D-11)."""
    return build_shape_from_step_specs(
        chip="SST27SF512",
        protocol="7",
        step_specs=[
            ("id", "OK", None, ""),
            ("read", "OK", None, ""),
            ("write", "OK", None, ""),
            ("verify", "OK", None, ""),
            ("erase", "OK", None, ""),
            ("blank-check", "OK", None, ""),
        ],
    )


def _build_gh47_sst27sf512_pass() -> DiagnosticReport:
    """Hand-transcribed from `henols/firestarter_prom` issue gh#47's fenced
    step vector (D-06), reproducing the filed dedup fingerprint
    `f9dbc31dcd27` through the real `dedup_fingerprint` rather than
    inheriting the literal. `steps[].fingerprint` serialises as a bare
    classification STRING, not an object -- transcribing it as an object
    silently drops every classification and the hash will not reproduce.
    Carries both discriminator tags: `run_counts` stamps the six real ops
    to 1 and every SDP-leg op to 0 so `repeat_policy_tag` fires for real,
    and `coverage_policy` stamps a real `WriteTarget` on the write step so
    `coverage_tag` fires for real. Neither tag is ever appended by hand."""
    return build_shape_from_step_specs(
        chip="sst27sf512",
        protocol="7",
        step_specs=[
            ("id", "OK", None, ""),
            ("read", "OK", None, ""),
            ("write", "OK", "indeterminate", ""),
            ("verify", "OK", "indeterminate", ""),
            ("erase", "OK", None, ""),
            ("blank-check", "OK", None, ""),
            ("write-baseline-b", "NA", None, ""),
            ("write-baseline-a", "NA", None, ""),
            ("sdp-lock", "NA", None, ""),
            ("write-inhibited", "NA", None, ""),
            ("sdp-unlock", "NA", None, ""),
            ("write-restored", "NA", None, ""),
        ],
        run_counts={
            "id": 1,
            "read": 1,
            "write": 1,
            "verify": 1,
            "erase": 1,
            "blank-check": 1,
            "write-baseline-b": 0,
            "write-baseline-a": 0,
            "sdp-lock": 0,
            "write-inhibited": 0,
            "sdp-unlock": 0,
            "write-restored": 0,
        },
        coverage_policy="full-device",
    )


def _build_gh28_m27c512_fail() -> DiagnosticReport:
    """Hand-transcribed from gh#28's fenced step vector (D-06), reproducing
    the filed fingerprint `31547956e56b`. Filed under schema 1.2 by host
    3.0.0b15, before `repeat_policy_tag`/`coverage_tag` existed -- neither
    tag is stamped."""
    return build_shape_from_step_specs(
        chip="m27c512",
        protocol="7",
        step_specs=[
            ("id", "OK", None, ""),
            ("read", "OK", None, ""),
            ("blank-check", "OK", None, ""),
            ("write", "BAD", "indeterminate", ""),
            ("verify", "BAD", "indeterminate", ""),
            ("erase", "NA", None, ""),
        ],
    )


def _build_gh20_at28c256_fail() -> DiagnosticReport:
    """Hand-transcribed from gh#20's fenced step vector (D-06), reproducing
    the filed fingerprint `00e121446ceb`. No tags. This fingerprint is
    shared by gh#20, gh#21 and gh#32 -- a real THREE-member dedup group
    `count_agreeing` (`tools/parse_devtest_issue.py:164`) reads off the
    embedded hash and never re-hashes, so a re-key of this shape resets
    that group's promotion count permanently. See the dedicated test
    `test_gh20_shape_reproduces_the_shared_three_issue_fingerprint` in
    `tests/test_blast_radius_invariance.py`."""
    return build_shape_from_step_specs(
        chip="at28c256",
        protocol="13",
        step_specs=[
            ("id", "NA", None, ""),
            ("read", "OK", None, ""),
            ("blank-check", "BAD", None, ""),
            ("write", "BAD", "indeterminate", ""),
            ("verify", "BAD", "indeterminate", ""),
            ("erase", "NA", None, ""),
        ],
    )


def _build_gh23_w27e257_fail() -> DiagnosticReport:
    """Hand-transcribed from gh#23's fenced step vector (D-06), reproducing
    the filed fingerprint `7a89fcea856a`. No tags. The two BAD steps carry
    the `blank/contact` classification bucket, NOT `indeterminate` -- the
    single most likely transcription error against gh#28's and gh#20's
    shapes above, which both carry `indeterminate` on their BAD steps."""
    return build_shape_from_step_specs(
        chip="w27e257",
        protocol="7",
        step_specs=[
            ("id", "OK", None, ""),
            ("read", "OK", None, ""),
            ("blank-check", "OK", None, ""),
            ("write", "BAD", "blank/contact", ""),
            ("verify", "BAD", "blank/contact", ""),
            ("erase", "OK", None, ""),
        ],
    )


def _build_synthetic_arm4_no_ok() -> DiagnosticReport:
    """`build_db_diff`'s fourth (fallback) arm fires when no step reports
    OK, and no real `derive_plan` output reaches it -- measurement found
    zero of 2031 swept plans with every step unsupported -- so this
    synthetic shape is the only way to pin it (`shape_id` prefix says so,
    per the `_coverage_report` direct-construction precedent at
    `tests/test_diagnostic_report.py:1312-1326`). No tags."""
    return build_shape_from_step_specs(
        chip="M8720",
        protocol="0x08",
        step_specs=[
            ("read", "NA", None, ""),
            ("write", "SKIPPED", None, ""),
        ],
    )


def _build_synthetic_arm4_empty_results() -> DiagnosticReport:
    """The same fallback arm reached with an EMPTY results list. Kept on
    chip `M8720`/protocol `0x08` for the hash itself -- `build_db_diff`
    reads `support_status` off a name argument that is independent of
    this builder's `auto_capture.chip` and derives disposition/ladder
    purely from the results list, so the acceptance check below calls
    `build_db_diff` with the chip name `m27c512` (one the shipped database
    actually carries) while keeping these synthetic (empty) results; that
    substitution is a verification-time convenience only and is not
    reflected in the frozen hash, which is computed off `M8720`/`0x08`."""
    return build_shape_from_step_specs(
        chip="M8720",
        protocol="0x08",
        step_specs=[],
    )


_BUILDERS: dict[str, Callable[[], DiagnosticReport]] = {
    "sst27sf512-six-step": _build_sst27sf512_six_step,
    "sst27sf512-six-step-readback-gated": _build_sst27sf512_six_step_readback_gated,
    "gh47-sst27sf512-pass": _build_gh47_sst27sf512_pass,
    "gh28-m27c512-fail": _build_gh28_m27c512_fail,
    "gh20-at28c256-fail": _build_gh20_at28c256_fail,
    "gh23-w27e257-fail": _build_gh23_w27e257_fail,
    "synthetic-arm4-no-ok": _build_synthetic_arm4_no_ok,
    "synthetic-arm4-empty-results": _build_synthetic_arm4_empty_results,
}

SHAPE_IDS: tuple[str, ...] = tuple(sorted(_BUILDERS))

FROZEN_HASHES: dict[str, str] = {
    "sst27sf512-six-step": "4dc282a5d596",
    "sst27sf512-six-step-readback-gated": "60a031573aab",
    "gh47-sst27sf512-pass": "f9dbc31dcd27",
    "gh28-m27c512-fail": "31547956e56b",
    "gh20-at28c256-fail": "00e121446ceb",
    "gh23-w27e257-fail": "7a89fcea856a",
    "synthetic-arm4-no-ok": "f90dfe1a44f7",
    "synthetic-arm4-empty-results": "8d6208d00be7",
}

RESERVED_SHAPE_IDS: frozenset[str] = frozenset(
    {
        "prune03-synthesized-fingerprint-match",
        "attr01-status-axis-transport-fault",
        "uv-slot-write-pass",
    }
)

assert not (set(SHAPE_IDS) & RESERVED_SHAPE_IDS), (
    "a shape_id was frozen under a name D-04 reserved for a later phase; "
    f"collision: {set(SHAPE_IDS) & RESERVED_SHAPE_IDS}"
)


def build_shape(shape_id: str) -> DiagnosticReport:
    """Dispatch `shape_id` to its builder. Raises `KeyError` naming
    `SHAPE_IDS` on an unregistered id, rather than returning `None`."""
    if shape_id not in _BUILDERS:
        raise KeyError(f"unknown shape_id {shape_id!r}; known: {SHAPE_IDS}")
    return _BUILDERS[shape_id]()
