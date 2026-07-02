"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Community Chip-Validation Diagnostic Report Model (v1.21 Phase 110)

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
and adds zero firmware dispatch entries. `AutoCapture.fw_board_identity` is
RECEIVED as threaded-in input (Phase 112 captures `version:board` off the
transient per-operation `comm` and passes it in) -- this module never
fetches it and never opens a serial connection (RESEARCH Pitfall 1).
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from typing import Any

from rich.prompt import Confirm, Prompt

from firestarter.chip_test import BannerCounts, Plan, StepResult

# ---------------------------------------------------------------------------
# Module constants (D-02, D-03) -- single sources of truth
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.0"  # D-02: single-sourced, baked into to_dict() output
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
    per-operation `comm.programmer_info`) -- this dataclass and this module
    NEVER fetch it themselves and NEVER import the serial-transport class
    (Pitfall 1). `host_version` is the caller-supplied
    `firestarter.__version__` string (read at the call site, not stored as a
    class default, so a future version bump is always live).
    """

    host_version: str
    fw_board_identity: str | None = None
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
# Provenance (RPT-04, D-04/D-05/D-06) -- human-asked, never auto-derived
# ---------------------------------------------------------------------------

# Community-tolerant enumerated shield-revision options -- NOT a closed
# hardware whitelist. The trailing "other" (free-text escape) and "not sure"
# entries are load-bearing: this module never reads a hardware-revision byte
# to fill this field (D-05, the Bug A lesson -- a hardware revision byte
# cannot distinguish Rev 2.2 / Rev 2.0 / modified Rev 0).
SHIELD_REV_CHOICES = ["Rev 2.2", "Rev 2.0", "modified Rev 0", "other", "not sure"]

_CHIP_ORIGIN_CHOICES = ["new/blank", "pulled/used"]


@dataclass
class Provenance:
    """Tester-supplied provenance the firmware cannot self-report (RPT-04).

    Every field defaults blank/`None` so an un-filled report is not
    submittable (`is_submittable`). `shield_rev` is ALWAYS human-asked --
    this dataclass and `prompt_provenance` below never read a
    hardware-revision byte to populate it (D-05). `owns_eraser` stays `None`
    when the chip under test is not UV-EPROM (only asked when relevant,
    D-06); `pot_note` is optional free text collected only when
    `pot_touched` is `True`.
    """

    shield_rev: str = ""
    chip_origin: str = ""
    owns_eraser: bool | None = None
    pot_touched: bool | None = None
    pot_note: str = ""


def prompt_provenance(
    is_uv: bool,
    *,
    ask: Any = Prompt.ask,
    confirm: Any = Confirm.ask,
) -> Provenance:
    """Collect provenance BEFORE the sweep via injectable callables (RPT-04).

    `ask`/`confirm` default to `rich.prompt.Prompt.ask`/`Confirm.ask`
    (mirroring `firmware.py`'s existing `Confirm.ask(..., default=...)`
    usage) but are overridable parameters so this function never blocks a
    unit test on a TTY (Pitfall 4) -- tests pass `Mock(side_effect=[...])`
    instead. Prompt order: shield_rev (free-text follow-up on "other"),
    chip_origin, owns_eraser (ONLY when `is_uv`), pot_touched, pot_note
    (ONLY when `pot_touched`).

    This function reads no hardware-revision byte and imports no
    serial-transport or hardware-manager class -- `is_uv` is the ONLY
    hardware-adjacent input, and it is received as a plain bool the caller
    (Phase 112) derives; this function never fetches it itself (D-05).
    """
    shield_rev = ask(
        "Shield revision",
        choices=SHIELD_REV_CHOICES,
        default="not sure",
    )
    if shield_rev == "other":
        # A blank free-text answer stays "" (falls through to is_submittable
        # as blank) rather than silently becoming the literal "other".
        shield_rev = ask("Describe shield revision", default="") or ""

    chip_origin = ask(
        "Chip origin",
        choices=_CHIP_ORIGIN_CHOICES,
        default="new/blank",
    )

    owns_eraser: bool | None = None
    if is_uv:
        owns_eraser = confirm("Do you own a UV eraser?", default=False)

    pot_touched = confirm("Did you touch/adjust the VPP/VPE pot?", default=False)

    pot_note = ""
    if pot_touched:
        pot_note = ask("Pot adjustment note (optional)", default="") or ""

    return Provenance(
        shield_rev=shield_rev,
        chip_origin=chip_origin,
        owns_eraser=owns_eraser,
        pot_touched=pot_touched,
        pot_note=pot_note,
    )


def is_submittable(p: Provenance) -> bool:
    """True iff every REQUIRED provenance field is filled (RPT-04, D-05).

    `"not sure"` on `shield_rev` is a valid, FILLED answer (truthy string)
    -- only a blank `""`/`None` on a required field fails. `pot_note` is
    always optional and never gates submittability.
    """
    return bool(p.shield_rev) and bool(p.chip_origin) and p.pot_touched is not None


# ---------------------------------------------------------------------------
# DbDiff (RPT-05, D-07) -- read-only advisory triage text, never a DB write
# ---------------------------------------------------------------------------

_DISPOSITION_COMMUNITY_FAIL = (
    "suggests: community-fail signal (advisory -- human triage required)"
)
_DISPOSITION_CANDIDATE = "suggests: candidate for community-reported (advisory)"
_DISPOSITION_INCONCLUSIVE = "inconclusive -- needs N>=2 agreement (advisory)"
_DISPOSITION_NO_CHANGE = "no change suggested (advisory)"


@dataclass
class DbDiff:
    """Current DB `support_status` beside an ADVISORY proposed-disposition
    (RPT-05, D-07).

    `proposed_disposition` is always plainly-labeled descriptive triage
    text -- it is NEVER a concrete `support_status` value and this module
    NEVER writes it back to the database. It exists to inform a human
    maintainer; the Phase-113/114 taxonomy state-machine and N>=2 promotion
    rule are explicitly out of scope here (D-07).
    """

    current_support_status: str = "supported"
    proposed_disposition: str = ""


def build_db_diff(name: str, db: Any, results: list[StepResult]) -> DbDiff:
    """Read-only transform: current `support_status` + an advisory
    proposed-disposition string derived purely from sweep verdicts
    (RPT-05, D-07).

    Reads `support_status` via `db.get_eprom_config(name)` -- mirroring the
    exact `chip_resolver.py:54` read site -- and NEVER calls any write/set
    method on `db`. `get_eprom_config` returns a `(config_dict, manufacturer)`
    tuple; only the config dict is used, defensively handling a `None`/absent
    config. The verdict-to-string mapping never yields a concrete
    `support_status` value -- every branch is advisory descriptive text.
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
    elif "marginal" in verdicts or has_indeterminate_fingerprint:
        proposed = _DISPOSITION_INCONCLUSIVE
    elif "OK" in verdicts and verdicts <= {"OK", "NA", "SKIPPED"}:
        proposed = _DISPOSITION_CANDIDATE
    else:
        proposed = _DISPOSITION_NO_CHANGE

    return DbDiff(current, proposed)


# ---------------------------------------------------------------------------
# DiagnosticReport (RPT-01, RPT-02, XPORT-01) -- single-source dual render
# ---------------------------------------------------------------------------


@dataclass
class DiagnosticReport:
    """The single source object every `dev test` run produces (D-01).

    Composes the Phase-108/109 `Plan`, `list[StepResult]`, and `BannerCounts`
    objects (never redefined here, never recomputed) plus the new
    `AutoCapture`/`TransportHealth` sub-objects. `vpp_vpe_mv` is a `None`
    slot left for Phase 111's measured-voltage sampler.

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
    vpp_vpe_mv: int | None = None
    provenance: Provenance | None = None
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

    def _provenance_dict(self) -> dict[str, Any] | None:
        p = self.provenance
        if p is None:
            return None
        return {
            "shield_rev": p.shield_rev,
            "chip_origin": p.chip_origin,
            "owns_eraser": p.owns_eraser,
            "pot_touched": p.pot_touched,
            "pot_note": p.pot_note,
        }

    def _db_diff_dict(self) -> dict[str, Any] | None:
        dd = self.db_diff
        if dd is None:
            return None
        return {
            "current_support_status": dd.current_support_status,
            "proposed_disposition": dd.proposed_disposition,
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
            "vpp_vpe_mv": self.vpp_vpe_mv,
            "provenance": self._provenance_dict(),
            "is_submittable": (
                is_submittable(self.provenance)
                if self.provenance is not None
                else False
            ),
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

        prov = d["provenance"]
        if prov is not None:
            table.add_row("provenance: shield_rev", str(prov["shield_rev"]))
            table.add_row("provenance: chip_origin", str(prov["chip_origin"]))
            table.add_row("provenance: owns_eraser", str(prov["owns_eraser"]))
            table.add_row("provenance: pot_touched", str(prov["pot_touched"]))
            table.add_row("provenance: pot_note", str(prov["pot_note"]))
        else:
            table.add_row("provenance", "not collected")
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
        else:
            table.add_row("db_diff", "not computed")

        if console is not None:
            console.print(table)
        return table

    def to_json_block(self) -> str:
        """Fenced ```json block for the self-contained issue body (RPT-01)."""
        return "```json\n" + json.dumps(self.to_dict(), indent=2) + "\n```"
