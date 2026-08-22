"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Community Chip-Validation Diagnostic Report Model (v1.21 Phase 110, reworked
Phase 112 Plan 04)

Pure host-side data assembly for `firestarter dev test <chip>` (Phase 112):
composes the Phase-108/109 `Plan` / `StepResult` / `Fingerprint` /
`BannerCounts` objects plus new auto-capture and transport-health
sub-objects into one `DiagnosticReport`, rendered two ways -- a `rich` table
and a fenced ```json``` block -- from a SINGLE canonical `to_dict()` mapping
(RPT-01). Neither render maintains a second hand-written field list, and
neither re-parses the other's output: add a field to `to_dict()` once, both
renders pick it up.

This module is ORCHESTRATOR-ONLY (SAFE-02, milestone non-regression
invariant): it imports no serial-transport or hardware-manager class, sets
no VPP, builds no wire/protocol command dict, passes no force-override flag,
and adds zero firmware dispatch entries. `AutoCapture.fw_board_identity` and
`AutoCapture.hw_revision` are RECEIVED as threaded-in input (Phase 112
captures them host-side and passes them in) -- this module never fetches
them and never opens a serial connection (RESEARCH Pitfall 1).

REVERSAL (Phase 112 Plan 04, operator-approved per `112-UAT.md` test 2): the
entire interactive tester-input-collection model (RPT-04, D-04/D-05/D-06) is
REMOVED from this module -- its collector function, its human-input
dataclass, and its enumerated choice-list constants no longer exist. Those
choice strings contained a path-separator character that collided with the
third-party prompt library's own separator-rendered choice display, so
partial natural inputs like `new`/`used`/`2.0` were rejected. Every question
that collector asked is now either firmware/DB auto-captured (`hw_revision`,
`fw_board_identity`, `protocol`) or dropped as self-reported-and-unverifiable
/ redundant (chip origin, UV-eraser ownership, pot-touched). `is_submittable`
is now computed from auto-capture completeness ONLY -- no human-input field
gates it.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from firestarter.chip_test import (
    _RAN_VERDICTS,
    REGION_POLICY_FULL_DEVICE,
    BannerCounts,
    Plan,
    StepResult,
)

# ---------------------------------------------------------------------------
# Module constants (D-02, D-03) -- single sources of truth
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.6"  # D-02: single-sourced, baked into to_dict() output
# 1.1 (Phase 114, GRAD-01): additive db_diff.ladder_state key -- backward
# compatible, existing consumers reading current_support_status/
# proposed_disposition are unaffected.
# 1.2 (Phase 121 Plan 06/07, D-06): the bump marks the seventh op string
# (`OP_WRITE_PARTIAL = "write-partial"`, chip_test.py) entering the report
# vocabulary. It breaks no consumer: `tools/parse_devtest_issue.py` accepts
# `schema_version` by PRESENCE ONLY (see `_extract_fenced_report`), never an
# exact-value match, so this bump is invisible to that parser. Reports
# already in the wild from `3.0.0b11` carry `"1.1"` and the six-string
# vocabulary (id/read/blank-check/write/verify/erase) and MUST keep parsing
# and keep grouping -- pinned by a frozen literal fixture in
# `tests/test_parse_devtest_issue.py`.
# 1.3 (v1.30 Phase 134 plan 134-06, D-10/LEG-12): additive `sdp_hold_state`
# key -- `chip_test.sdp_hold_state()`'s three-valued `HELD`/`NOT-HELD`/
# `NOT-RUN: <reason>` string, carried verbatim (never derived here -- see
# `to_dict`). MEASURED DISCREPANCY, recorded rather than silently
# reconciled (same convention as 134-02/134-04's own measured findings):
# this plan's own PLAN.md read `to_dict`'s prior key count as nine; the
# live count on disk at plan time was already TEN (schema_version,
# generated, auto_capture, transport_health, steps, banner, voltage,
# is_submittable, dedup_fingerprint, db_diff), so `sdp_hold_state` is this
# dict's ELEVENTH key, not its tenth. This does not change the bump's own
# argument: the change is still purely additive, existing consumers reading
# any of the ten prior keys are unaffected, and `tools/parse_devtest_issue.py`
# accepts `schema_version` by PRESENCE ONLY, never an exact-value match, so
# this bump is invisible to that parser. REJECTED: a field-plus-JSON change
# with no version bump -- the artifact shape would change while its own
# version claimed it had not, in the milestone whose close phase (Phase 137)
# arms a claim gate over exactly that kind of statement.
# 1.4 (v1.32 Phase 147, D-09): marks a value-population change, not a key
# addition -- `auto_capture.fw_board_identity` already existed in
# `to_dict()`'s output and was unconditionally `null`; from this version it
# carries data whenever the connection captured one. No key is added and no
# key is removed. The 1.3 note above explicitly REJECTED "a field-plus-JSON
# change with no version bump" -- the artifact shape changing while its own
# version claimed it had not. Populating a permanently-null key is that same
# class of change, so it takes a bump too. Both `[dev test]` parsers accept
# `schema_version` by PRESENCE ONLY, never an exact-value match (a live
# fixture carries `schema_version: "9.9-future"`, `tests/test_parse_devtest_
# issue.py:138`), so this bump is invisible to them and needs no parser
# change -- and no ordering/comparison logic over this string is introduced
# anywhere as part of this bump (D-17). Reports already in the wild carry
# `fw_board_identity: null` PERMANENTLY -- the run that produced them is
# gone and unrepeatable -- and are unfixable by design; they must keep
# parsing. That is PROV-04, pinned by the frozen literal fixtures in
# `tests/test_parse_devtest_issue.py`.
# 1.6 (quick task 260821-wna): additive per-step keys -- `write_region_start`,
# `write_region_length`, `write_bits_cleared`, `write_bits_retained`,
# `write_current_source` -- read off `StepResult.write_target` (`None` on a
# step with no resolved target, i.e. every non-write/verify step and any
# write/verify step SKIPPED as saturated/refused). No top-level key is added;
# `parse_devtest_issue.py` still accepts `schema_version` by PRESENCE ONLY,
# so this bump is invisible to it.
NOT_MEASURED = "not measured"  # D-03: honest fallback, never a false 0
NOT_REPORTED = "not reported"  # D-11 (v1.32 Phase 147): honest fallback for
# an identity field that was never ASKED, not merely measured-and-empty --
# reusing NOT_MEASURED here would conflate "asked and got nothing" with
# "never asked", the exact ambiguity PROV-05 exists to remove. Pre-checked
# clean against check_diagnostic_report_claims.py's 14 forbidden patterns.

# Elevated-counter threshold for `transport_suspect` (dormant today -- no
# transport counter is reachable per RESEARCH §Transport Counter Survey; a
# future phase that adds real counters activates this without a redesign).
_SUSPECT_THRESHOLD = 5


# ---------------------------------------------------------------------------
# AutoCapture (RPT-02) -- no method fetches identity or opens serial
# ---------------------------------------------------------------------------


@dataclass
class AutoCapture:
    """Auto-captured identity/protocol fields (RPT-02) -- no tester input.

    `fw_board_identity` is `str | None` because it is RECEIVED as threaded-in
    input from Phase 112 (which captures `version:board` off the transient
    per-operation `comm.programmer_info`, when an orchestrator-safe live
    source is reachable) -- this dataclass and this module NEVER fetch it
    themselves and NEVER import the serial-transport class (Pitfall 1).
    `host_version` is the caller-supplied `firestarter.__version__` string
    (read at the call site, not stored as a class default, so a future
    version bump is always live).

    `hw_revision` is `str | None` -- the coarse silkscreen-bucket string the
    firmware/codec produce (e.g. a "Rev 2.0-class"-style label), or `None`
    when not measured. It is ALWAYS auto-captured (Phase 112 Plan 04 reverses
    the earlier D-05 "always human-asked" precision argument) -- this
    dataclass and this module never prompt a human for it, and a coarse or
    absent reading is an accepted, honest outcome rather than a gap.
    """

    host_version: str
    fw_board_identity: str | None = None
    hw_revision: str | None = None
    chip: str = ""
    protocol: str | None = None
    chip_id_expected: int | None = None
    chip_id_actual: int | None = None
    chip_id_mismatch_reason: str | None = None


# ---------------------------------------------------------------------------
# TransportHealth (XPORT-01, D-03) -- honest "not measured" fallback
# ---------------------------------------------------------------------------


@dataclass
class TransportHealth:
    """Best-effort transport-health counters (XPORT-01).

    Every counter defaults to `None` -- "not measured" -- because no
    COBS-decode-error / CRC-failure / retry / timeout counter is reachable
    from the operator or serial-transport layer today (RESEARCH §Transport
    Counter Survey: verified NONE exist). `transport_suspect` defaults
    `False` and can only be set `True` by `_is_transport_suspect` below --
    never inferred from absent data.
    """

    cobs_errors: int | None = None
    crc_failures: int | None = None
    retries: int | None = None
    timeouts: int | None = None
    transport_suspect: bool = False


def _is_transport_suspect(th: TransportHealth) -> bool:
    """True only when a counter is PRESENT (not None) AND elevated (D-03).

    Absent counters can never fabricate suspicion -- mirrors Phase 108's
    honest `indeterminate` fingerprint bucket. Since no counter is reachable
    today (RESEARCH §Transport Counter Survey), this always returns False in
    production; it exists so a future counter source activates it without a
    redesign.
    """
    for value in (th.cobs_errors, th.crc_failures, th.retries, th.timeouts):
        if value is not None and value >= _SUSPECT_THRESHOLD:
            return True
    return False


# ---------------------------------------------------------------------------
# Submittability (Phase 112 Plan 04) -- auto-capture-only, no human gate
# ---------------------------------------------------------------------------
#
# REVERSAL: this section previously held the RPT-04 / D-04/D-05/D-06
# interactive tester-input-collection model -- a collector function, a
# human-input dataclass, and enumerated choice-list constants for shield
# revision and chip origin. All deleted (operator-approved, 112-UAT.md test
# 2): the choice strings contained a path-separator character that collided
# with the third-party prompt library's own separator-rendered choice
# display, rejecting natural inputs like `new`/`used`/`2.0`; and every
# question asked was either firmware/DB-queryable (shield/hw/fw) or
# self-reported-and-unverifiable (chip origin, UV eraser ownership).


def is_submittable(ac: AutoCapture) -> bool:
    """True iff the auto-captured identity needed to act on a report is
    present (Phase 112 Plan 04) -- NO human-provenance field is involved.

    A report is submittable when the objective, machine-captured identity
    is complete: `chip` (the name under test), `protocol` (the DB-derived
    algorithm), and `host_version` (always populated by the caller) are all
    present. `hw_revision`/`fw_board_identity` are informational-best-effort
    (coarse bucket or honest `None` is acceptable) and never gate
    submittability -- gating on a field that can honestly read `None` on a
    perfectly good report would defeat the auto-capture-only intent.
    """
    return bool(ac.chip) and bool(ac.protocol) and bool(ac.host_version)


# ---------------------------------------------------------------------------
# Dedup fingerprint (SUB-03, D-02) -- deterministic, volatile-field-free
# ---------------------------------------------------------------------------


# D-11 (v1.30 Phase 134, plan 134-06): the SDP leg's ACCEPTED, RECORDED cost
# to this function -- NOT a bug, and this function's body below is left
# byte-unchanged by this plan. The SDP leg's six new steps (see
# `chip_test._SDP_LEG_STEP_ORDER` for the ordered tuple by name -- not
# spelled out literally here: this module is a declared non-registry,
# re-measured every run by `test_non_registry_still_has_no_ops`'s AST
# inversion guard for zero op vocabulary, including hyphenated op-value
# string literals) necessarily re-key every one of the 43 measured ALLOW
# chips through the hashed `op=verdict:cls` triples below -- b14/b15-era
# reports stop grouping with v1.30-era ones and their accumulated N>=2
# promotion counts reset. gh#20's orphaned id `00e121446ceb` is named
# explicitly in plan 134-11's LEG-18 finding; the outward description of
# this discontinuity is Phase 137's release notes (CLOSE-05), not this
# phase's. REJECTED: excluding the SDP steps from this hash (preserves
# continuity and the promotion ladder, but two reports differing ONLY in
# their SDP outcome would then dedup identically -- a leaked lock grouping
# with a held one, blinding the mechanism that decides which reports get
# triaged); carrying a second legacy fingerprint (preserves continuity
# without blinding dedup, but adds a field and a hash nothing else needs).
def dedup_fingerprint(report: DiagnosticReport) -> str:
    """Deterministic 12-char lowercase hex short-hash for report dedup (D-02).

    Reads ONLY `AutoCapture.chip`/`.protocol` (via `report.auto_capture`) and,
    per step in `report.results` order, `StepResult.op`/`.verdict` plus
    `StepResult.fingerprint.classification` when present (empty string
    otherwise -- the graceful-degradation case for a non-destructive run with
    no write/verify fingerprint attached). The hash deliberately EXCLUDES
    every volatile field -- `generated`, `host_version`, measured
    `vpp_*`/`vpe_*` millivolt readings, `error_code`, and the free-text
    `reason` string -- so a clean re-test of the same chip with the same
    outcome shape dedups to the SAME id, and no scrubbable-PII-bearing
    `reason` text ever influences it (T-113-02).

    This is a non-secret dedup id, not a security control (T-113-06) --
    `hashlib.sha256` is used here purely for its distribution properties,
    truncated to 12 hex characters (collision-safe at this scale, short
    enough for an issue title).

    Phase 121 D-06/D-08 depends on two properties of hashing `result.op`
    (not just `result.verdict`) into `parts`, both proven by test rather
    than argued: (1) a partial run (`OP_WRITE_PARTIAL = "write-partial"`)
    and a full run (`OP_WRITE = "write"`) of the same chip with identical
    verdicts/classifications differ here purely because the op strings
    differ -- no extra code needed. (2) because
    `tools/parse_devtest_issue.py::count_agreeing` groups SAVED report
    bodies by this ALREADY-EMBEDDED fingerprint (never re-hashing), a
    partial run can NEVER land in the same group as a full run, so it can
    never contribute to that group's N>=2 promotion count. Phase 114's
    GRAD-01 no-auto-graduate lock therefore holds end to end THROUGH THE
    FINGERPRINT -- not through the `ladder_state` tag, which is identical
    for both run shapes (`build_db_diff` below has no op-name branch at
    all).
    """
    ac = report.auto_capture
    parts = [ac.chip or "", str(ac.protocol or "")]
    for result in report.results:
        cls = result.fingerprint.classification if result.fingerprint else ""
        parts.append(f"{result.op}={result.verdict}:{cls}")
    canonical = "|".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# DbDiff (RPT-05, D-07) -- read-only advisory triage text, never a DB write
# ---------------------------------------------------------------------------

_DISPOSITION_COMMUNITY_FAIL = (
    "suggests: community-fail signal (advisory -- human triage required)"
)
_DISPOSITION_CANDIDATE = "suggests: candidate for community-reported (advisory)"
_DISPOSITION_INCONCLUSIVE = "inconclusive -- needs N>=2 agreement (advisory)"
_DISPOSITION_NO_CHANGE = "no change suggested (advisory)"

# Graduation-ladder tag names (GRAD-01, Phase 114, D-01/D-02). These are the
# formalized report-side vocabulary the ladder taxonomy documents (see
# doc/community-validation.md). `_LADDER_COMMUNITY_CONFIRMED` is the
# human-gated target reachable only after a maintainer manually promotes a
# chip (N>=2 agreeing reports, D-03) via the unchanged `build_db.py` write
# locus -- `build_db_diff` below NEVER assigns it.
_LADDER_COMMUNITY_REPORTED = "community-reported"
_LADDER_COMMUNITY_FAIL = "community-fail"
_LADDER_COMMUNITY_CONFIRMED = "community-confirmed"  # human-only; never auto-emitted
_LADDER_NONE = ""


@dataclass
class DbDiff:
    """Current DB `support_status` beside an ADVISORY proposed-disposition
    (RPT-05, D-07) plus a derived report-side `ladder_state` tag (GRAD-01,
    D-01/D-02).

    `proposed_disposition` is always plainly-labeled descriptive triage
    text -- it is NEVER a concrete `support_status` value and this module
    NEVER writes it back to the database. `ladder_state` is likewise a
    report-side-only label (one of `_LADDER_COMMUNITY_REPORTED` /
    `_LADDER_COMMUNITY_FAIL` / `_LADDER_NONE`) -- `_LADDER_COMMUNITY_CONFIRMED`
    is the human-gated target and is NEVER auto-assigned here. It exists to
    inform a human maintainer; the N>=2 promotion rule and the actual
    `support_status` write remain a manual `build_db.py` edit, entirely out
    of scope for this module (D-01/D-02/D-07).
    """

    current_support_status: str = "supported"
    proposed_disposition: str = ""
    ladder_state: str = ""


def build_db_diff(name: str, db: Any, results: list[StepResult]) -> DbDiff:
    """Read-only transform: current `support_status` + an advisory
    proposed-disposition string + a derived `ladder_state` tag, both computed
    purely from sweep verdicts (RPT-05/D-07, GRAD-01/D-01).

    Reads `support_status` via `db.get_eprom_config(name)` -- mirroring the
    exact `chip_resolver.py:54` read site -- and NEVER calls any write/set
    method on `db`. `get_eprom_config` returns a `(config_dict, manufacturer)`
    tuple; only the config dict is used, defensively handling a `None`/absent
    config. Neither the disposition text nor `ladder_state` ever yields a
    concrete `support_status` value, and `ladder_state` never becomes
    `_LADDER_COMMUNITY_CONFIRMED` -- that state is human-gated only (D-01/D-02).
    """
    raw_config, _manufacturer = db.get_eprom_config(name)
    current = (raw_config or {}).get("support_status", "supported")

    verdicts = {r.verdict for r in results}
    has_indeterminate_fingerprint = any(
        r.fingerprint is not None and r.fingerprint.classification == "indeterminate"
        for r in results
    )

    if "BAD" in verdicts:
        proposed = _DISPOSITION_COMMUNITY_FAIL
        ladder_state = _LADDER_COMMUNITY_FAIL
    elif "marginal" in verdicts or has_indeterminate_fingerprint:
        proposed = _DISPOSITION_INCONCLUSIVE
        ladder_state = _LADDER_NONE
    elif "OK" in verdicts and verdicts <= {"OK", "NA", "SKIPPED"}:
        proposed = _DISPOSITION_CANDIDATE
        ladder_state = _LADDER_COMMUNITY_REPORTED
    else:
        proposed = _DISPOSITION_NO_CHANGE
        ladder_state = _LADDER_NONE

    return DbDiff(current, proposed, ladder_state)


# ---------------------------------------------------------------------------
# Render-boundary identity helper (PROV-05, D-10/D-11/D-12) -- render() ONLY
# ---------------------------------------------------------------------------


def _identity_cell(value: object) -> str:
    """Render-only substitution for an absent identity value (D-10, D-11,
    D-12). Used ONLY inside `render()` -- never in `to_dict()`, which is
    where the `NOT_MEASURED` precedent substitutes and where D-10 requires
    the fenced report JSON to keep typed `null` (machine consumers keep
    testing `is None`, so PROV-04's backward-compatibility story stays ONE
    case instead of two).

    Returns `NOT_REPORTED` when `value` is `None` OR the empty string, and
    `str(value)` otherwise -- an explicit two-clause condition, never an
    `or`-coalescing expression, whose real sin is swallowing arbitrary
    falsy values (e.g. `0`) with no decision behind it. An identity with no
    printable content carries no evidence to preserve, and an empty cell is
    precisely the blank rendering PROV-05 forbids. A PARTIALLY mangled
    identity is different: `hardware.py`'s `_scrub_identity` (147-02, D-07)
    leaves it non-empty with `?` substituted for bad bytes, so it still
    renders here and stays visibly faulty.
    """
    if value is None or value == "":
        return NOT_REPORTED
    return str(value)


def _hex_cell(value: object, digits: int) -> str:
    """Render-only hex formatter for `render()` -- never used by `to_dict()`,
    which keeps the raw typed value (mirrors `_identity_cell`'s own
    recorded reasoning: a render-boundary substitution must never leak into
    the canonical serializable mapping).

    Parses `value` with base 0 (`int(str(value), 0)`), so both a production
    decimal-shaped string (`"13"`) and an already-hex test-fixture string
    (`"0x0D"`) resolve to the same integer and the formatter is idempotent
    on `0x`-prefixed input. Returns an upper-case `0x`-prefixed string
    zero-padded to `digits` hex digits.

    Returns `str(value)` UNCHANGED -- never `NOT_REPORTED` -- when `value`
    is `None` or cannot be parsed as an integer (catches only `ValueError`/
    `TypeError`, never a bare `Exception`). This deliberately does NOT
    reuse `_identity_cell`'s absent-value marker: a live gate
    (test_absent_identity_renders_the_explicit_marker_in_both_rows) asserts
    `NOT_REPORTED` appears exactly twice in the whole table, and D-12
    already records that the chip-ID row legitimately renders `None`/`None`
    on a minimal report -- rendering `NOT_REPORTED` here would both break
    that count and contradict that recorded rationale. The operator asked
    only that an absent/non-numeric value not crash the render, not that
    it print a specific marker.
    """
    if value is None:
        return str(value)
    try:
        parsed = int(str(value), 0)
    except (ValueError, TypeError):
        return str(value)
    return f"0x{parsed:0{digits}X}"


def _state_cell(value: object) -> str:
    """Render-only truncation of `sdp_hold_state` to its bare state token --
    never used by `to_dict()`, which keeps the full `NOT-RUN: <reason>`
    string (mirrors `_hex_cell`/`_identity_cell`: a render-boundary
    substitution must not leak into the canonical mapping).

    `chip_test.sdp_hold_state()` returns `HELD`, `NOT-HELD`, or
    `f"{SDP_HOLD_NOT_RUN}: {reason}"`. Only the third form carries prose,
    and on a non-0x0D part that prose is a full sentence naming the family
    fact, which Rich then word-wraps across three console lines. Returns
    everything before the first `":"`, stripped; `HELD`/`NOT-HELD` contain
    no colon and pass through unchanged.

    **This deliberately supersedes D-07/LEG-12's console leg** (operator,
    2026-08-21): that decision required the NOT-RUN reason to be
    console-visible because `reason` never reaches render()'s per-step row.
    The operator judged the sentence to be noise in the result box and
    asked for it gone. The reason is NOT lost -- it stays verbatim in
    `to_dict()["sdp_hold_state"]`, so the saved JSON/markdown artifact and
    the filed issue body all still carry it; only this table got shorter.
    """
    text = str(value)
    return text.split(":", 1)[0].strip()


def _rail_cell(before: object, after: object) -> str:
    """Render-only formatter for one rail's before/after bracket -- returns
    `"<before> / <after> mV"`, or a single `NOT_MEASURED` when NEITHER end
    was sampled (rather than repeating the sentinel twice).

    Renders only the bracketed pair. The `vpp_mv`/`vpe_mv` standalone
    slots are deliberately NOT shown: they exist for a non-destructive
    reading, and since D-04 made every run destructive (the sampler is
    always built) NOTHING in the code path assigns them, so they printed
    `not measured` on every single run beside real bracket numbers. They
    stay in `to_dict()` -- this only stops the box repeating two dead
    fields (operator, 2026-08-21).
    """
    b, a = str(before), str(after)
    if b == NOT_MEASURED and a == NOT_MEASURED:
        return NOT_MEASURED
    return f"{b} / {a} mV"


def _duration_cell(seconds: object) -> str:
    """Format a step's `duration_s` for display -- `""` when absent.

    Two decimals under 10 s (a 0.03 s id check must not round to `0.0s`),
    one decimal above (a 41.88 s full read reads fine as `41.9s`). Returns
    `""` for `None` so a caller can append it without a separator dance.
    """
    if seconds is None:
        return ""
    try:
        value = float(seconds)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return ""
    return f"{value:.2f}s" if value < 10 else f"{value:.1f}s"


def _write_coverage_line(step_row: dict[str, Any], policy: str | None) -> str | None:
    """Render-only D-F line: names write coverage whenever the write did
    NOT cover the full device -- `None` when nothing should be shown.

    Derived from the SAME `step_row` dict `to_dict()` already produced
    (never a second field list, never a re-parse of the JSON string) plus
    the step's `region_policy` (read off `Plan.steps`, the object `render()`
    already holds a reference to). Kept to one short, measurement-shaped
    line: the region, the byte count and the bit count, or the reason
    nothing was written.
    """
    start = step_row.get("write_region_start")
    length = step_row.get("write_region_length")
    reason = str(step_row.get("reason") or "")

    if start is None and length is None:
        # No resolved target at all: a saturated/refused write. Name the
        # reason (the SKIPPED verdict already names saturation).
        return reason or "no target resolved"

    if policy == REGION_POLICY_FULL_DEVICE:
        # A full-device write, possibly carved out (flash4 boot blocks) --
        # only a row when there IS an exclusion to disclose (derive_plan
        # records that as a non-empty `reason` even on a successful carve).
        return reason or None

    cleared = step_row.get("write_bits_cleared")
    region = f"0x{start:X} ({length} bytes)"
    if cleared is not None:
        return f"slot {region}, {cleared} bits clearable"
    return f"region {region}"


# ---------------------------------------------------------------------------
# DiagnosticReport (RPT-01, RPT-02, XPORT-01) -- single-source dual render
# ---------------------------------------------------------------------------


@dataclass
class DiagnosticReport:
    """The single source object every `dev test` run produces (D-01).

    Composes the Phase-108/109 `Plan`, `list[StepResult]`, and `BannerCounts`
    objects (never redefined here, never recomputed) plus the new
    `AutoCapture`/`TransportHealth` sub-objects. The measured-voltage slot is
    split (Phase 111, D-01/D-03/D-04) into destructive-run before/after pairs
    per rail (`vpp_before_mv`/`vpp_after_mv`/`vpe_before_mv`/`vpe_after_mv`)
    plus standalone non-destructive readings (`vpp_mv`/`vpe_mv`) -- a rail
    that sagged across a write reads very differently from a regulator that
    never reached its target, so the two shapes are never conflated into one
    field.

    `db_diff` (plan 03, RPT-05) is the advisory, read-only DB-diff -- current
    `support_status` beside a proposed-disposition string derived purely from
    the sweep verdicts. It is `None` when no `build_db_diff` call has been
    composed in yet.
    """

    auto_capture: AutoCapture
    transport: TransportHealth
    plan: Plan
    results: list[StepResult] = field(default_factory=list)
    banner: BannerCounts | None = None
    # D-01 split / D-03 destructive before-after / D-04 standalone honest-fallback
    vpp_before_mv: int | None = None
    vpp_after_mv: int | None = None
    vpe_before_mv: int | None = None
    vpe_after_mv: int | None = None
    vpp_mv: int | None = None
    vpe_mv: int | None = None
    db_diff: DbDiff | None = None
    # LEG-12 (v1.30 Phase 134, plan 134-06, D-10): the carriage half only --
    # a plain `str`, NEVER a `bool` and NEVER a key named `locked` or
    # `protection_enabled` (P-06 prevention 3: a JSON `true` on such a key
    # is read as ground truth for a protection state this chip family
    # cannot report at all). Defaults to `""` (unassigned); the VALUE is
    # assigned by `cli_handlers.py` from `chip_test.sdp_hold_state(plan,
    # results)` in plan 134-07, which closes LEG-12 -- this class only
    # carries and serialises whatever string it is given, never derives one
    # (this is a declared non-registry, re-measured every run by
    # `test_non_registry_still_has_no_ops`'s AST inversion guard to carry
    # zero op vocabulary).
    sdp_hold_state: str = ""

    def _utc_now(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    def _auto_capture_dict(self) -> dict[str, Any]:
        ac = self.auto_capture
        return {
            "host_version": ac.host_version,
            "fw_board_identity": ac.fw_board_identity,
            "hw_revision": ac.hw_revision,
            "chip": ac.chip,
            "protocol": ac.protocol,
            "chip_id_expected": ac.chip_id_expected,
            "chip_id_actual": ac.chip_id_actual,
            "chip_id_mismatch_reason": ac.chip_id_mismatch_reason,
        }

    def _transport_dict(self) -> dict[str, Any]:
        """Substitute NOT_MEASURED for any None counter -- the ONE place in
        this module that knows the sentinel string (Pitfall 3)."""
        th = self.transport
        return {
            "cobs_errors": NOT_MEASURED if th.cobs_errors is None else th.cobs_errors,
            "crc_failures": (
                NOT_MEASURED if th.crc_failures is None else th.crc_failures
            ),
            "retries": NOT_MEASURED if th.retries is None else th.retries,
            "timeouts": NOT_MEASURED if th.timeouts is None else th.timeouts,
            "transport_suspect": _is_transport_suspect(th),
        }

    def _voltage_dict(self) -> dict[str, Any]:
        """Substitute NOT_MEASURED for any None voltage field -- the ONE
        place in this module that knows the sentinel string for a voltage
        reading (mirrors `_transport_dict`, Pitfall 3). Readings land on the
        100 mV grid the sampler reports at; an absent reading is honestly
        `NOT_MEASURED`, never a fabricated `0` (D-04)."""
        return {
            "vpp_before_mv": (
                NOT_MEASURED if self.vpp_before_mv is None else self.vpp_before_mv
            ),
            "vpp_after_mv": (
                NOT_MEASURED if self.vpp_after_mv is None else self.vpp_after_mv
            ),
            "vpe_before_mv": (
                NOT_MEASURED if self.vpe_before_mv is None else self.vpe_before_mv
            ),
            "vpe_after_mv": (
                NOT_MEASURED if self.vpe_after_mv is None else self.vpe_after_mv
            ),
            "vpp_mv": NOT_MEASURED if self.vpp_mv is None else self.vpp_mv,
            "vpe_mv": NOT_MEASURED if self.vpe_mv is None else self.vpe_mv,
        }

    def _step_dict(self, result: StepResult) -> dict[str, Any]:
        # Schema 1.6 (quick task 260821-wna): the five `write_*` keys below
        # are read off `StepResult.write_target` -- `None` on every step
        # that isn't a write/verify, and `None` on a write/verify step that
        # was SKIPPED as saturated/refused (there is no resolved target to
        # report on). Additive INSIDE `steps[]` only, never a top-level key
        # -- the top-level shape is pinned elsewhere and `parse_devtest_
        # issue.py` consumes it.
        #
        # Deliberate residual, recorded here rather than silently patched:
        # `dedup_fingerprint` (above) intentionally does NOT read any of
        # these fields, so it does NOT distinguish a full-device UV run
        # from a slot run -- the chosen slot is volatile by design (D-B:
        # the chip's own content is the state), so keying it into the hash
        # would make every UV run its own group and destroy the N>=2
        # agreement `count_agreeing` depends on. The coverage is recorded
        # here as PROVENANCE instead, never as part of the dedup identity.
        target = result.write_target
        return {
            "op": result.op,
            "verdict": result.verdict,
            "reason": result.reason,
            "error_code": result.error_code,
            "fingerprint": (
                result.fingerprint.classification if result.fingerprint else None
            ),
            # Schema 1.5: wall-clock seconds for the step, or `None` when it
            # did not run. Additive -- every pre-1.5 consumer ignores it.
            "duration_s": result.duration_s,
            "write_region_start": target.region[0] if target else None,
            "write_region_length": target.region[1] if target else None,
            "write_bits_cleared": target.bits_cleared if target else None,
            "write_bits_retained": target.bits_retained if target else None,
            "write_current_source": target.current_source if target else None,
        }

    def _banner_dict(self) -> dict[str, Any]:
        if self.banner is None:
            return {"n_ran": None, "m_applicable": None, "locked_steps": []}
        return {
            "n_ran": self.banner.n_ran,
            "m_applicable": self.banner.m_applicable,
            "locked_steps": list(self.banner.locked_steps),
        }

    def _db_diff_dict(self) -> dict[str, Any] | None:
        dd = self.db_diff
        if dd is None:
            return None
        return {
            "current_support_status": dd.current_support_status,
            "proposed_disposition": dd.proposed_disposition,
            "ladder_state": dd.ladder_state,
        }

    def to_dict(self) -> dict[str, Any]:
        """CANONICAL serializable mapping -- the single source both render()
        and to_json_block() consume (RPT-01, D-01). Hand-written (NOT
        `dataclasses.asdict()` wholesale, Pitfall 3): this is the ONE place
        `schema_version` is baked in and the ONE place NOT_MEASURED is
        substituted for an absent transport counter.
        """
        return {
            "schema_version": SCHEMA_VERSION,
            "generated": self._utc_now(),
            "auto_capture": self._auto_capture_dict(),
            "transport_health": self._transport_dict(),
            "steps": [self._step_dict(r) for r in self.results],
            "banner": self._banner_dict(),
            "voltage": self._voltage_dict(),
            "is_submittable": is_submittable(self.auto_capture),
            "dedup_fingerprint": dedup_fingerprint(self),
            "db_diff": self._db_diff_dict(),
            "sdp_hold_state": self.sdp_hold_state,
        }

    def render(self, console: Any = None) -> Any:
        """Human `rich` table built from the SAME dict `to_dict()` produces
        (RPT-01, D-01) -- never a second hand-maintained field list, never a
        re-parse of the JSON string produced by `to_json_block()`.

        Quick task 260821-spg trimmed this table to what a tester actually
        needs: `protocol` and both `chip_id` sides render as hex (via
        `_hex_cell`, `None`-safe), each per-step row shows a bare verdict
        with no per-step diagnostic-code suffix, and three noisier rows
        (the raw transport counters, the submit-eligibility flag, and the
        advisory database-diff block) are gone entirely from this table,
        including the old "not computed" fallback for the last of those.

        A follow-up on the same operator pass took two more bites:
        `sdp_hold_state` renders as its BARE state token via `_state_cell`
        (the `NOT-RUN: <reason>` sentence wrapped across three lines), and
        the single six-value `voltage` row became one `_rail_cell` row per
        rail, dropping the `vpp_mv`/`vpe_mv` standalone slots that no code
        path assigns.

        `to_dict()` is unchanged throughout -- every one of those values is
        still in the JSON/markdown artifact and the filed issue body; only
        this console rendering got shorter.
        """
        from rich.table import Table

        d = self.to_dict()
        ac = d["auto_capture"]
        table = Table(title=f"dev test -- {ac['chip']}")
        table.add_column("Field")
        table.add_column("Value")

        table.add_row("host_version", str(ac["host_version"]))
        table.add_row("fw_board_identity", _identity_cell(ac["fw_board_identity"]))
        table.add_row("hw_revision", _identity_cell(ac["hw_revision"]))
        table.add_row("protocol", _hex_cell(ac["protocol"], 2))
        # `chip_id_actual` is populated ONLY on a mismatch: on a passing id
        # check the firmware's OK reply carries no id back, so
        # `check_eprom_id` returns the host's OWN expected value echoed from
        # `cmd_data["chip-id"]` and `_chip_id_fields` correctly discards it
        # rather than present a never-measured number as a measurement.
        # Rendering the resulting `None` beside a real expected id read like
        # a failed read, so the two-sided row now appears only when there IS
        # a disagreement to show (operator asked, 2026-08-21).
        if ac["chip_id_actual"] is None:
            table.add_row("chip_id", _hex_cell(ac["chip_id_expected"], 4))
        else:
            table.add_row(
                "chip_id (expected/actual)",
                f"{_hex_cell(ac['chip_id_expected'], 4)} / "
                f"{_hex_cell(ac['chip_id_actual'], 4)}",
            )

        # Only steps that actually RAN get a row (operator, 2026-08-21).
        # Gated on the engine's OWN `_RAN_VERDICTS` ({OK, BAD, marginal}) --
        # the same frozenset `count_applicable` uses for the banner's
        # `n_ran` -- so the number of step rows here is exactly the banner's
        # N by construction, never a second hand-maintained notion of "ran".
        #
        # Safe to hide: `NA` and `SKIPPED` both map to exit code 0
        # (`cli_handlers._VERDICT_EXIT_CODES`), so no nonzero-exit cause can
        # hide here. The one non-verdict exit term, D-15's not-run SDP
        # oracle floor, stays legible in the `sdp_hold_state` row above.
        # Every step keeps its full entry in `to_dict()["steps"]`, so the
        # JSON, the markdown table and the filed issue body are unchanged.
        for step_row in d["steps"]:
            if step_row["verdict"] not in _RAN_VERDICTS:
                continue
            took = _duration_cell(step_row.get("duration_s"))
            verdict = str(step_row["verdict"])
            table.add_row(
                f"step: {step_row['op']}",
                f"{verdict}  {took}".rstrip() if took else verdict,
            )

        banner = d["banner"]
        table.add_row("banner", f"{banner['n_ran']} of {banner['m_applicable']} ran")

        # Sum of the steps that ran (operator asked for timings, 2026-08-21).
        # Deliberately labelled "steps total", not "elapsed": it excludes the
        # identity read, plan derivation, report write and the submit prompt,
        # so calling it wall-clock for the whole command would overclaim. It
        # is NOT added to `to_dict()` -- a derived sum belongs to the render,
        # and the per-step `duration_s` values it comes from are all in the
        # JSON for any consumer that wants to re-add them.
        total = sum(
            float(sr["duration_s"])
            for sr in d["steps"]
            if sr.get("duration_s") is not None
        )
        if total:
            table.add_row("steps total", _duration_cell(total))

        # LEG-12: its own console row, never folded into a step's `reason`.
        # Rendered as the BARE state token via `_state_cell` -- the operator
        # superseded D-07's console leg on 2026-08-21 (the NOT-RUN reason is
        # a wrapped full sentence in the box); the reason still rides the
        # `to_dict()` string into the JSON, markdown and issue body.
        table.add_row("sdp_hold_state", _state_cell(d["sdp_hold_state"]))

        # D-F (quick task 260821-wna): one extra row, only when the write
        # did not cover the full device -- the slot range and clearable-bit
        # count for a slot write, the excluded range and reason for a
        # carved-out full-device write, or the saturation reason when
        # nothing was written. No row at all for a plain, unexcluded
        # full-device write. Adds no console call and no new helper to
        # `dev_test`'s body -- this lives entirely inside this module.
        #
        # The write step is located STRUCTURALLY, never by comparing
        # against a specific `OP_*` constant: this class is a declared
        # op-vocabulary non-registry (LEG-15,
        # `tests/test_op_registration_parity.py`) -- `to_dict()`/`render()`/
        # `_step_dict()` read `StepResult.op` generically for display and
        # must never special-case a specific op string. `plan.steps` and
        # `self.results` are the SAME length in the SAME order (`run_plan`
        # appends exactly one result per step); the shipped write/
        # write-partial step is always the FIRST step carrying BOTH
        # `destructive=True` AND a non-`None` `write_region` -- `verify`
        # never sets `destructive`, `erase` never carries `write_region`,
        # and the SDP leg's six steps (indistinguishable from a
        # fixed-policy write by these two fields alone) always come LAST.
        write_step_index = next(
            (
                i
                for i, s in enumerate(self.plan.steps)
                if s.destructive and s.write_region is not None
            ),
            None,
        )
        if write_step_index is not None and write_step_index < len(d["steps"]):
            write_step = self.plan.steps[write_step_index]
            write_step_row = d["steps"][write_step_index]
            coverage = _write_coverage_line(write_step_row, write_step.region_policy)
            if coverage:
                table.add_row("write coverage", coverage)

        v = d["voltage"]
        table.add_row(
            "vpp (before/after)", _rail_cell(v["vpp_before_mv"], v["vpp_after_mv"])
        )
        table.add_row(
            "vpe (before/after)", _rail_cell(v["vpe_before_mv"], v["vpe_after_mv"])
        )

        if console is not None:
            console.print(table)
        return table

    def to_json_block(self) -> str:
        """Fenced ```json block for the self-contained issue body (RPT-01)."""
        return "```json\n" + json.dumps(self.to_dict(), indent=2) + "\n```"
