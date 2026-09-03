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


_BUILDERS: dict[str, Callable[[], DiagnosticReport]] = {
    "sst27sf512-six-step": _build_sst27sf512_six_step,
}

SHAPE_IDS: tuple[str, ...] = tuple(sorted(_BUILDERS))

FROZEN_HASHES: dict[str, str] = {
    "sst27sf512-six-step": "4dc282a5d596",
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
