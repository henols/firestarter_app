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

The remaining eight (D-02 table 2 -- `m27c512-full-all-ok`,
`m27c512-full-blank-check-bad`, `m27c512-full-canonical-name`,
`m27c512-full-comma-joined-name`, `m27c512-full-runs-1`,
`at28c256-full-all-ok-sdp`, `sst27sf512-full-all-ok`,
`w27e257-full-all-ok`) go through the REAL `derive_plan` -> `run_plan` ->
`DiagnosticReport` path, mirroring `firestarter/cli_handlers.py:2374-2431`
-- the sole production construction site -- exactly: `chip` is the raw
CLI token, `protocol` is `str(prog["algorithm"])` read off
`_REAL_DB.convert_to_programmer(_REAL_DB.get_eprom(chip))`. These are the
shapes research actually measured a generator regeneration or a plan-shape
change (SDP-step pruning, canonical naming) would move. Each real-path
builder that is called directly is cached with `functools.cache` --
`derive_plan`/`run_plan` are paid once per shape rather than once per call
site. The two canonical-naming derivatives (`m27c512-full-canonical-name`,
`m27c512-full-comma-joined-name`) are built through
`_clone_with_chip_override`, which deep-copies the cached base's `results`
and `plan` (CR-01) rather than sharing them -- a mutation through either
derivative therefore cannot move the cached base's frozen hash, and the
derivatives themselves stay uncached so a mutation leg on one never leaks
into the other. The tracer's hand-specified builder, which IS mutated by
the planted-mutation tests, stays uncached for the same reason.
"""

from __future__ import annotations

import copy
import functools
from collections.abc import Callable
from dataclasses import replace as _dataclass_replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from firestarter.chip_test import (
    Fingerprint,
    Plan,
    StepResult,
    WriteTarget,
    derive_plan,
    run_plan,
)
from firestarter.database import EpromDatabase
from firestarter.diagnostic_report import AutoCapture, DiagnosticReport, TransportHealth

_REAL_DB = EpromDatabase(skip_local_override=True)

_HOST_VERSION = "3.0.0b10"

_OPERATOR_METHODS = [
    "check_eprom_id",
    "read_eprom",
    "check_eprom_blank",
    "write_eprom",
    "verify_eprom",
    "erase_eprom",
    "sdp_lock",
    "sdp_unlock",
]


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


def _fixed_return_operator(**returns: Any) -> Mock:
    """The fixed-return operator double, mirroring
    `tests/test_chip_test.py:_mock_operator` (defined HERE rather than
    imported from that test module -- D-03: Phase 181 must be able to
    import shapes without pulling a large test module in). Every call
    reports success with no state and no file written -- sufficient for
    every real-path row below EXCEPT the AT28C256 SDP row, which needs
    `_sdp_aware_operator` instead."""
    op = Mock(spec=_OPERATOR_METHODS)
    op.check_eprom_id.return_value = (True, 0x1234)
    op.read_eprom.return_value = True
    op.check_eprom_blank.return_value = True
    op.write_eprom.return_value = True
    op.verify_eprom.return_value = True
    op.erase_eprom.return_value = True
    op.sdp_lock.return_value = True
    op.sdp_unlock.return_value = True
    for name, value in returns.items():
        getattr(op, name).return_value = value
        getattr(op, name).side_effect = None
    return op


def _sdp_aware_operator() -> Mock:
    """A stateful, SDP-lock-AWARE operator double for
    `at28c256-full-all-ok-sdp`, modelled on
    `tests/test_chip_test.py:_sdp_leg_readback_operator` and defined HERE
    rather than imported from that test module (D-03, same reason as
    `_fixed_return_operator` above).

    `_fixed_return_operator`'s `read_eprom` returns success while writing
    NO file, so the SDP leg's read-back-equality oracle would see an empty
    read-back and report every one of the six leg steps BAD via the
    length gate -- silently reducing an all-OK assertion to a false
    negative that still reads green. This double instead maintains a
    small in-memory chip image and honours real SDP semantics: while
    `locked`, a write carrying `FLAG_SKIP_SDP_UNLOCK` (write-inhibited's
    own flag) is genuinely REJECTED -- the image is left unchanged, exactly
    what a genuinely-protecting chip does -- while every other write (no
    skip flag, or the chip unlocked) applies normally."""
    from firestarter.constants import FLAG_SKIP_SDP_UNLOCK

    state = {"image": b"", "locked": False}

    op = Mock(spec=_OPERATOR_METHODS)
    op.check_eprom_id.return_value = (True, 0x1234)
    op.check_eprom_blank.return_value = True
    op.erase_eprom.return_value = True

    def _write_eprom(name, eprom_data, source_path, flags=0, address_str=None, **_kw):
        payload = Path(source_path).read_bytes()
        if state["locked"] and (flags & FLAG_SKIP_SDP_UNLOCK):
            pass
        else:
            state["image"] = payload
        return True

    def _read_eprom(name, eprom_data, output_file=None, **kwargs):
        if output_file is not None:
            Path(output_file).write_bytes(state["image"])
        return True

    def _verify_eprom(name, eprom_data, source_path, *_args, **_kwargs):
        expected = Path(source_path).read_bytes()
        return expected == state["image"]

    def _sdp_lock(name, eprom_data):
        state["locked"] = True
        return True

    def _sdp_unlock(name, eprom_data):
        state["locked"] = False
        return True

    op.write_eprom.side_effect = _write_eprom
    op.read_eprom.side_effect = _read_eprom
    op.verify_eprom.side_effect = _verify_eprom
    op.sdp_lock.side_effect = _sdp_lock
    op.sdp_unlock.side_effect = _sdp_unlock
    return op


def _build_real_path_report(
    *, chip: str, write_scope: str, operator: Any, runs: int
) -> DiagnosticReport:
    """Real-path construction (D-02 table 2), mirroring
    `firestarter/cli_handlers.py:2374-2431` -- the SOLE production
    `DiagnosticReport` construction site -- exactly: `chip` is the raw CLI
    token, `protocol` is `str(prog["algorithm"])` read off
    `_REAL_DB.convert_to_programmer(_REAL_DB.get_eprom(chip))`, never a
    re-derivation.

    `operator.check_eprom_id` is stamped with the chip's REAL `chip-id`
    from the DB before `run_plan` -- `_dispatch_id` (`chip_test.py:2603`)
    compares the operator's detected id against `eprom_data["chip-id"]`
    and reports BAD on a mismatch, closing the destructive gate. An
    arbitrary placeholder id (harmless for at28c256, whose `chip-id` is
    the falsy `0`) would silently turn every OTHER chip's `id` step BAD
    and cascade into every destructive step reading SKIPPED under the
    gate -- none of the sixteen shapes wants a deliberate id mismatch."""
    plan = derive_plan(chip, _REAL_DB, write_scope=write_scope)
    full = _REAL_DB.get_eprom(chip)
    expected_chip_id = (full or {}).get("chip-id")
    operator.check_eprom_id.return_value = (True, expected_chip_id or 0x1234)
    results = run_plan(
        plan, operator, _REAL_DB, runs=runs, allow_single_run=(runs == 1)
    )
    prog = _REAL_DB.convert_to_programmer(full)
    auto_capture = AutoCapture(
        host_version=_HOST_VERSION,
        chip=chip,
        protocol=str(prog.get("algorithm")),
    )
    return DiagnosticReport(
        auto_capture=auto_capture,
        transport=TransportHealth(),
        plan=plan,
        results=results,
    )


def _clone_with_chip_override(
    report: DiagnosticReport, chip_override: str
) -> DiagnosticReport:
    """A clone of `report` with `auto_capture.chip` replaced -- reused by
    the two D-2 canonical-naming alternatives, which stamp a different
    `auto_capture.chip` onto a plan/results pair DERIVED from the all-OK
    real-path builder's output, rather than paying `derive_plan`/`run_plan`
    a second time for an identical plan. The clone owns its own `results`
    and `plan` via `copy.deepcopy` (CR-01): the prior shallow share meant a
    mutation leg on this clone silently wrote through to
    `m27c512-full-all-ok`'s cached `results`, moving its frozen hash. Both
    `report.results` and `report.plan` deep-copy cleanly and a report
    rebuilt from the copies fingerprints identically to the shared-object
    version, so the fix cannot move a frozen hash."""
    auto_capture = _dataclass_replace(report.auto_capture, chip=chip_override)
    return DiagnosticReport(
        auto_capture=auto_capture,
        transport=report.transport,
        plan=copy.deepcopy(report.plan),
        results=copy.deepcopy(report.results),
    )


@functools.cache
def _build_m27c512_full_all_ok() -> DiagnosticReport:
    """m27c512 is a UV part: under the fixed-return double, `write` and
    `verify` land SKIPPED because no slot satisfies the write
    monotonicity witness -- the honestly-measured shape, not a write that
    silently occurred."""
    return _build_real_path_report(
        chip="m27c512", write_scope="full", operator=_fixed_return_operator(), runs=2
    )


@functools.cache
def _build_m27c512_full_blank_check_bad() -> DiagnosticReport:
    """The UV `run_count` collapse row (RESEARCH correction C3): moves the
    hash through the `blank-check` verdict triple, NOT through
    `repeat_policy_tag` -- the collapsed write/verify steps carry
    `run_count == 0`, and `repeat_policy_tag` fires only on
    `run_count == 1`. CONTEXT.md D-12 row 4 names `repeat_policy_tag` as
    the mechanism; measurement found it does not fire, and this docstring
    records the correction so Phase 179 is measured against the mechanism
    that actually operates."""
    return _build_real_path_report(
        chip="m27c512",
        write_scope="full",
        operator=_fixed_return_operator(check_eprom_blank=False),
        runs=2,
    )


def _build_m27c512_full_canonical_name() -> DiagnosticReport:
    """D-2's rejected canonical-naming alternative: the all-OK shape with
    `auto_capture.chip` overridden to `M27C512`. Frozen so a later phase
    that accidentally normalises `parts[0]` reddens against a row that
    already names the consequence. This value REPLACES CONTEXT.md D-12
    row 3's inherited `a00791f1c2b4` -> `a6f6c6354047` pair (RESEARCH
    correction C2): neither inherited value reproduces from any m27c512
    report shape, across an exhaustive ~2.1e8-candidate pre-image sweep,
    so both were unverified priors and neither may be frozen."""
    return _clone_with_chip_override(_build_m27c512_full_all_ok(), "M27C512")


def _build_m27c512_full_comma_joined_name() -> DiagnosticReport:
    """The all-OK shape with `auto_capture.chip` overridden to the full
    comma-joined `part_number` alias list `M27C512,M27V512` -- D-2's other
    rejected canonical-naming alternative, frozen for the same reason as
    `m27c512-full-canonical-name` above."""
    return _clone_with_chip_override(_build_m27c512_full_all_ok(), "M27C512,M27V512")


@functools.cache
def _build_m27c512_full_runs_1() -> DiagnosticReport:
    """The all-OK shape run with `runs=1`: the real `repeat_policy_tag`
    fires on the read step's `run_count` of one and appends the degraded
    marker -- never a tag string appended by hand."""
    return _build_real_path_report(
        chip="m27c512", write_scope="full", operator=_fixed_return_operator(), runs=1
    )


@functools.cache
def _build_at28c256_full_all_ok_sdp() -> DiagnosticReport:
    """A genuinely all-OK AT28C256 run through the stateful SDP-aware
    double lands on `build_db_diff`'s SECOND arm with an EMPTY ladder
    state, because its SDP leg attaches `indeterminate` fingerprints in
    every arm -- the D-08 blind spot, independently reproducing
    `.planning/todos/pending/
    build-db-diff-ladder-state-community-reported-regression.md`."""
    return _build_real_path_report(
        chip="at28c256", write_scope="full", operator=_sdp_aware_operator(), runs=2
    )


@functools.cache
def _build_sst27sf512_full_all_ok() -> DiagnosticReport:
    """A non-SDP all-OK shape (D-08 requires at least one): reaches
    `build_db_diff`'s THIRD arm with ladder state `community-reported`.
    Without at least one non-SDP all-OK shape the harness cannot see the
    D-4/D-6 ladder flip at all."""
    return _build_real_path_report(
        chip="sst27sf512", write_scope="full", operator=_fixed_return_operator(), runs=2
    )


@functools.cache
def _build_w27e257_full_all_ok() -> DiagnosticReport:
    """The second non-SDP all-OK shape D-08 requires, reaching the same
    THIRD arm as `sst27sf512-full-all-ok`."""
    return _build_real_path_report(
        chip="w27e257", write_scope="full", operator=_fixed_return_operator(), runs=2
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
    "m27c512-full-all-ok": _build_m27c512_full_all_ok,
    "m27c512-full-blank-check-bad": _build_m27c512_full_blank_check_bad,
    "m27c512-full-canonical-name": _build_m27c512_full_canonical_name,
    "m27c512-full-comma-joined-name": _build_m27c512_full_comma_joined_name,
    "m27c512-full-runs-1": _build_m27c512_full_runs_1,
    "at28c256-full-all-ok-sdp": _build_at28c256_full_all_ok_sdp,
    "sst27sf512-full-all-ok": _build_sst27sf512_full_all_ok,
    "w27e257-full-all-ok": _build_w27e257_full_all_ok,
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
    "m27c512-full-all-ok": "6d3afbc52315",
    "m27c512-full-blank-check-bad": "077a32d1a5c4",
    "m27c512-full-canonical-name": "776846bf2dc8",
    "m27c512-full-comma-joined-name": "37ad34d39a19",
    "m27c512-full-runs-1": "e4838f7bb1d3",
    "at28c256-full-all-ok-sdp": "52fb759dc48c",
    "sst27sf512-full-all-ok": "4b3e52cab987",
    "w27e257-full-all-ok": "22908e2954c3",
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
