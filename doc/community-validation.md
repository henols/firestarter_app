<p align="left"><img src="https://raw.githubusercontent.com/henols/firestarter_app/refs/heads/main/images/firestarter_logo.png" alt="Firestarter EPROM Programmer" width="200"></p>

---

## Community Chip-Validation Graduation Ladder

`firestarter dev test <chip>` **writes to the chip** — every run runs a
capability sweep that expects a blank or scratch part. A UV-erasable EPROM is
stopped and asked first (yes = the full device is written; no, or no TTY at
all, still writes a small 256-byte region); every other family, including
this project's own AT28C, is written in full — twice — with no prompt at all.
It then offers to file the resulting diagnostic report as a GitHub issue.
This document defines the **graduation ladder** —
the vocabulary that describes how much trust a report has earned — and, just
as importantly, what the ladder is **not**: it is never an automatic path to
changing what the project claims a chip supports.

**Locked anti-feature (DISP-01):** no code path in this repository writes a
chip's `support_status` as a result of parsing a community report. Every
`community-*` label described below lives **only** on the report / `DbDiff`
object (`firestarter/diagnostic_report.py`) — never in `chip_database.json`.
This is machine-enforced (see §Enforcement below).

---

## The Four Ladder States

| State | Where it lives | How it is reached |
|-------|-----------------|--------------------|
| **(none)** — no community-* tag (`""`) | Report only | `build_db_diff()`'s default when the sweep is inconclusive (a `marginal` verdict, an `indeterminate` fingerprint classification) or when there is no change to suggest. Advisory text: "inconclusive -- needs N>=2 agreement" / "no change suggested". |
| **`community-reported`** | Report only (`DbDiff.ladder_state`) | **Auto-tag.** A single `dev test` run whose step verdicts are all `OK` (or `NA`/`SKIPPED`, with at least one `OK`) and contain no `BAD` verdict. This is the "looks like it works" signal from one tester. A **partial-region** write (`write-partial`, the UV stop-and-ask's decline/off-TTY branch) earns this exact same auto-tag as a full-device round-trip — see the fingerprint argument in §N≥2 below for why that is safe rather than evidence-inflating. |
| **`community-fail`** | Report only (`DbDiff.ladder_state`) | **Auto-tag.** Any step in the sweep produced a `BAD` verdict. Signals the chip likely does NOT work as configured, on this tester's hardware. |
| **`community-confirmed`** | **Never auto-assigned. Human-gated target only.** | Reached only after a maintainer manually reviews **N≥2 independent agreeing reports** (see §N≥2 below) and decides to promote the chip. There is no code path, tool, or CLI flag that assigns this value — it exists purely as a documented vocabulary term for triage conversation and issue labels a maintainer might apply by hand. |

The auto-tag derivation lives in `build_db_diff(name, db, results)`
(`firestarter/diagnostic_report.py`), mirroring the existing advisory
`proposed_disposition` branch order verdict-for-verdict:

- any `BAD` verdict present → `ladder_state = "community-fail"`
- all-`OK` candidate (verdicts subset of `{OK, NA, SKIPPED}`, at least one
  `OK`) → `ladder_state = "community-reported"`
- `marginal` verdict OR an `indeterminate` fingerprint classification
  (inconclusive) → `ladder_state = ""` (no tag)
- no-change / empty-results fallback → `ladder_state = ""` (no tag)

`ladder_state` is exposed once, on `DbDiff`, and flows through
`DiagnosticReport.to_dict()['db_diff']['ladder_state']` into **both**
`render()` (the `rich` table) and `to_json_block()` (the fenced JSON a
`--submit` issue body embeds) — a single source, never a second
hand-maintained field list.

**`community-confirmed` is never a string `build_db_diff` can produce.** It
exists in this document (and as a named-but-unused constant in
`diagnostic_report.py`, `_LADDER_COMMUNITY_CONFIRMED`) purely so the
vocabulary is complete and so a maintainer has a name to use when they
manually promote a chip.

---

## Why `community-*` never enters `chip_database.json`

Every chip-support read guard in this codebase treats `support_status !=
"supported"` as **non-dispatchable**:

```python
# firestarter/chip_resolver.py:54
support_status = raw_config.get("support_status", "supported")
if support_status != "supported":
    reason = raw_config.get("unsupported_reason", "unsupported on this hardware")
    raise ChipNotImplementedError(f"{name}: {reason}")
```

If a `community-*` value ever reached a chip's `support_status` in
`chip_database.json`, it would **silently disable that chip** — the exact
opposite of what graduation is supposed to do. This is why the ladder is
strictly report-side: `chip_database.json`'s `support_status` reaches
`"supported"` only via the unchanged, human-authored `tools/build_db.py`
pipeline (see §Manual Promotion below).

---

## The N≥2 Agreement Rule — and the Rule It Is NOT

**Cross-report agreement (this document's N≥2):** two `dev test` reports
"agree" if and only if their `dedup_fingerprint` values match. That
fingerprint (`firestarter/diagnostic_report.py`, `dedup_fingerprint()`) is a
deterministic SHA-256-derived 12-hex-character digest of the chip, protocol,
and each step's `op=verdict:fingerprint_classification` tuple — it
deliberately excludes every volatile field (timestamps, host version,
transport counters) so two clean re-runs on different hardware dedup
identically. Triage tooling counts matching `dedup_fingerprint` values
**across distinct issues** to surface an "N agreeing" count for the
maintainer during `gsd-inbox` triage.

**This is explicitly a different N≥2 from Phase 108's internal per-run
rule.** Phase 108's sweep already re-runs certain destructive operations
(write/erase/verify) multiple times **within a single report** — if those
internal re-runs disagree, the step's own verdict becomes `marginal`
(handled above, no community-* tag). That internal per-run consistency
check happens entirely inside one `dev test` invocation, before a report is
ever filed. The cross-report N≥2 described in this document only begins
once **two or more separate reports** (from potentially different testers,
different boards, different chip samples) exist and are compared during
triage. Do not conflate the two: a report whose own internal re-runs agree
is still just **one** data point toward the cross-report N≥2 that gates
`community-confirmed`.

**A single report can never trigger a state transition — of any kind.** The
"N agreeing" count is presented as triage input for a human to weigh; no
code counts, gates, or acts on it automatically.

**Why a partial-region write can never poison the N≥2 count.** A UV part's
declined (or off-TTY) sweep writes only a 256-byte region and uses the
`write-partial` op string instead of `write`; `dedup_fingerprint` hashes each
step's `op=verdict:fingerprint_classification` tuple, so a partial run's
fingerprint is **structurally different** from a full round-trip's fingerprint
on the same chip. `count_agreeing` groups strictly by that fingerprint, so a
partial run and a full run of the same chip can never land in the same
agreement bucket — a partial run can contribute at most toward N≥2 agreement
with *other partial runs*, never toward promoting a full-round-trip claim.
Phase 114's GRAD-01 no-auto-graduate lock therefore holds end to end through
the fingerprint, not through the `community-reported` tag itself.

---

## Manual Promotion Process (the only path to `supported`)

1. A maintainer reviews the `gsd-inbox` triage surface (or the raw issues)
   for `[dev test]`-titled reports and notes their `dedup_fingerprint` /
   `ladder_state` / verdict summary.
2. The maintainer identifies **N≥2 reports whose `dedup_fingerprint` values
   agree** (per §N≥2 above), all pointing toward the same chip working (or
   not working) under the exercised configuration.
3. The maintainer makes an **explicit human edit** to `tools/build_db.py` —
   the sole, unchanged `support_status` write locus (see the
   `_support_status = "supported"` default and its override sites,
   `tools/build_db.py` lines ~491–714) — and regenerates
   `chip_database.json`.
4. There is no CLI command, script, or automated job that performs step 3.
   No code in this repository reads a community report and writes
   `support_status`; the DISP-01 AST audit (`tools/` checker paired with a
   `tests/` anti-hollow test) mechanically enforces that invariant on every
   test run.

A chip can carry a `community-reported` or `community-fail` tag on any
number of individual reports indefinitely without ever becoming
`community-confirmed` or `supported` — graduation is always a deliberate,
manual maintainer decision, never an emergent property of report volume
alone.

---

## Enforcement

The no-auto-graduate lock (DISP-01) is machine-enforced by an AST-based
source scanner (mirroring the established SAFE-03 pattern in
`tools/check_devtest_orchestrator.py` / `tests/test_check_devtest_orchestrator.py`)
that scans the report/parse code path for any write of the `support_status`
key or attribute, paired with an anti-hollow test carrying planted-violation
fixtures. See the Phase 114 DISP-01 plan for the checker's exact scope.

---

## Summary Table

| Ladder state | Auto-emitted by `build_db_diff`? | Lives in `chip_database.json`? | How it changes |
|---|---|---|---|
| `""` (none) | Yes (marginal / indeterminate / no-change) | No | N/A — advisory only |
| `community-reported` | Yes (all-OK single report) | No | N/A — advisory only |
| `community-fail` | Yes (any BAD verdict) | No | N/A — advisory only |
| `community-confirmed` | **Never** | No | Documentation/vocabulary term only |
| `supported` | Never (this is a DB state, not a ladder tag) | Yes | Manual `tools/build_db.py` edit only, gated on a maintainer's N≥2 review |
