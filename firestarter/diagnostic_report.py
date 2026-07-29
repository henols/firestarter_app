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

from firestarter.chip_test import BannerCounts, Plan, StepResult

# ---------------------------------------------------------------------------
# Module constants (D-02, D-03) -- single sources of truth
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.2"  # D-02: single-sourced, baked into to_dict() output
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
NOT_MEASURED = "not measured"  # D-03: honest fallback, never a false 0

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
        return {
            "op": result.op,
            "verdict": result.verdict,
            "reason": result.reason,
            "error_code": result.error_code,
            "fingerprint": (
                result.fingerprint.classification if result.fingerprint else None
            ),
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
        }

    def render(self, console: Any = None) -> Any:
        """Human `rich` table built from the SAME dict `to_dict()` produces
        (RPT-01, D-01) -- never a second hand-maintained field list, never a
        re-parse of the JSON string produced by `to_json_block()`."""
        from rich.table import Table

        d = self.to_dict()
        ac = d["auto_capture"]
        table = Table(title=f"dev test -- {ac['chip']}")
        table.add_column("Field")
        table.add_column("Value")

        table.add_row("host_version", str(ac["host_version"]))
        table.add_row("fw_board_identity", str(ac["fw_board_identity"]))
        table.add_row("hw_revision", str(ac["hw_revision"]))
        table.add_row("protocol", str(ac["protocol"]))
        table.add_row(
            "chip_id (expected/actual)",
            f"{ac['chip_id_expected']} / {ac['chip_id_actual']}",
        )

        for step_row in d["steps"]:
            table.add_row(
                f"step: {step_row['op']}",
                f"{step_row['verdict']} (err={step_row['error_code']}, "
                f"fingerprint={step_row['fingerprint']})",
            )

        th = d["transport_health"]
        table.add_row(
            "transport_health",
            (
                f"cobs={th['cobs_errors']} crc={th['crc_failures']} "
                f"retries={th['retries']} timeouts={th['timeouts']} "
                f"suspect={th['transport_suspect']}"
            ),
        )

        banner = d["banner"]
        table.add_row("banner", f"{banner['n_ran']} of {banner['m_applicable']} ran")

        v = d["voltage"]
        table.add_row(
            "voltage",
            (
                f"vpp before/after={v['vpp_before_mv']}/{v['vpp_after_mv']} "
                f"vpe before/after={v['vpe_before_mv']}/{v['vpe_after_mv']} "
                f"vpp={v['vpp_mv']} vpe={v['vpe_mv']}"
            ),
        )

        table.add_row("is_submittable", str(d["is_submittable"]))

        db_diff = d["db_diff"]
        if db_diff is not None:
            table.add_row(
                "db_diff: current_support_status",
                str(db_diff["current_support_status"]),
            )
            table.add_row(
                "db_diff: proposed_disposition",
                str(db_diff["proposed_disposition"]),
            )
            table.add_row(
                "db_diff: ladder_state",
                str(db_diff["ladder_state"]) or "(none)",
            )
        else:
            table.add_row("db_diff", "not computed")

        if console is not None:
            console.print(table)
        return table

    def to_json_block(self) -> str:
        """Fenced ```json block for the self-contained issue body (RPT-01)."""
        return "```json\n" + json.dumps(self.to_dict(), indent=2) + "\n```"
