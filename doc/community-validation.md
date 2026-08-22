<p align="left"><img src="https://raw.githubusercontent.com/henols/firestarter_app/refs/heads/main/images/firestarter_logo.png" alt="Firestarter EPROM Programmer" width="200"></p>

---

## Community Chip-Validation Graduation Ladder

`firestarter dev test <chip>` **writes to the chip** — every run runs a
capability sweep that expects a blank or scratch part. There is no prompt on
any path and no read-only mode. A UV-erasable EPROM receives one 256-byte
slot, taken from the top of the device downwards, because a UV write cannot be
undone without a lamp and the part is worth more as a reusable regression rig
than as a single thorough measurement; every other family, including this
project's own AT28C, is written in full. It then offers to file the resulting
diagnostic report as a GitHub issue.

### How the repeat works, and what it is for

The write-shaped part of the sweep runs as a **cycle**: `write → verify` (plus
`erase → blank-check` where the family has them), repeated twice by default —
not two writes followed by two verifies. Each cycle is paired so that its
verify has a write of its own to speak for.

**Each cycle is given something real to do.** A verify only proves the write
worked if the write had to *change* something; a second identical write onto
the state the first one produced does not, and on protocols `0x07`/`0x08`/`0x0B`
the firmware makes that explicit by skipping already-correct bytes before it
issues any programming pulse. So the payload per cycle depends on the family:

| Family | Rows | How each cycle gets real work |
| --- | --- | --- |
| Page-write auto-erases (`0x0D`, `0x05`) | 111 | identical bytes — the page erase is internal to every write |
| Electrically erasable | 258 | identical bytes — the erase inside the cycle blanks the part for the next write |
| SRAM / FRAM | 76 | pattern, then its complement — free on a part that rewrites both ways, and it exercises every data line in both directions |
| UV-EPROM | 301 | staged images out of one slot (below) |

**UV-EPROM staging costs no extra bits.** A UV cell only goes one way, so a
UV part is a finite regression rig and every bit spent is a run lost. The
cycle therefore splits the bits the write would have cleared *anyway* into
disjoint interleaved tranches, one per cycle: the final image is byte-for-byte
what a single unstaged write would have produced, so the part ends in exactly
the same state and lasts exactly as long. One run consumes one 256-byte slot,
top-down from the highest address — so a 64 KiB part is good for roughly 256
runs, and every run exercises the full address range from the first one.
Each report says how many slots the part has left.

**The repeat is a rig-health check, not extra firmware coverage.** Firmware is
deterministic: it cannot disagree with itself. What two cycles catch is
something *analog* moving between them — rail droop, marginal timing, a poor
socket contact. That is worth having, because a flaky rig manufactures false
verdicts about firmware, but it is not a second opinion on the programming
algorithm.

`dev test --fast` runs a single cycle instead. It is a deliberately weaker
test — with nothing to compare, nothing can be `marginal` and read
nondeterminism is not measured at all — and its reports are kept out of the
cross-report agreement count described below. On a UV part it also halves the
bits consumed, which makes it a reasonable habit while iterating on firmware
against a rig you already trust; use the default when the result is meant to
stand as evidence.

Every report states the cycle count per step, in the `Runs` column of the
results table and as `run_count` in the JSON (`schema_version` 1.7 and later).

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
`DiagnosticReport.to_dict()['db_diff']['ladder_state']` into
`to_json_block()` (the fenced JSON a `--submit` issue body embeds) — a
single source, never a second hand-maintained field list. (Quick task
260821-spg removed `db_diff`'s row from `render()`'s console table
entirely — the table no longer carries `db_diff` at all — so
`ladder_state` now reaches only the JSON surface, not the `rich` table.)

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

**Why a `--fast` run cannot poison it either.** The same mechanism, applied to
the repeat policy rather than the write region. A `--fast` sweep runs a single
cycle, so no step in it can ever report `marginal` — it is a strictly weaker
test in exactly the way a partial-region write is. `dedup_fingerprint` appends
a repeat-policy marker whenever any step's `run_count` is 1, so a `--fast`
report lands in its own agreement bucket and can only ever agree with other
`--fast` reports. Two fast runs can never promote a chip that no accurate run
has passed. The marker is appended **only** for the degraded policy, so every
fingerprint produced by a default N≥2 run is unchanged from before this
mechanism existed — no already-filed report's grouping was reset by it.

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
