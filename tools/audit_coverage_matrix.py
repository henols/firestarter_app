"""
Phase 11 — Coverage Matrix & DB Inconsistency Audit (Wave 1 — tool skeleton).

Emits `.planning/v1.3-COVERAGE-MATRIX.md`: a single-source coverage map of
every algorithm-0x07 (28-pin DIP CMOS UV-EPROM) and algorithm-0x08 (32-pin
DIP CMOS UV-EPROM) chip in `chip_database.json`.

Wave 1 lands §1 (Summary Statistics) + §2 (DB Count Reconciliation).
§3/§4/§5 are placeholder headers populated by Waves 2-4 (Plans 11-03 / 04 / 05).

Run from any cwd:

    python tools/audit_coverage_matrix.py
    python tools/audit_coverage_matrix.py --output /tmp/m.md --ledger /tmp/l.json
    python tools/audit_coverage_matrix.py --check        # exit 1 if new findings

DB path resolution mirrors `tools/check_dispatch.py`:
the live DB is `<repo-root>/firestarter_app/firestarter/data/chip_database.json`
unless `FIRESTARTER_DB_FILE` env-var overrides.

Output defaults to `<repo-root>/.planning/v1.3-COVERAGE-MATRIX.md` (absolute,
computed from `__file__` per RESEARCH.md Pitfall 6 — robust against the
operator's cwd).

Exit codes (D-03):
  0 — clean generate, or `--check` with no new findings.
  1 — `--check` would mint a new DEFECT-COV-NN, OR DB parse error.

Idempotence contract (D-02 / Pattern B):
  - Sorted iteration on every dict.items()
  - No timestamps in output (no datetime.now())
  - Path.write_text(..., encoding="utf-8", newline="\\n")
  - JSON: sort_keys=True, indent=2, trailing newline.
  Two consecutive runs MUST produce byte-identical output.
"""
import argparse
import hashlib
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from firestarter.database import EpromDatabase  # noqa: F401 — singleton kept available for §3/§4 lookups

# Module-top path constants (lifted verbatim from check_dispatch.py:23-30 per D-01).
_DATA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "firestarter", "data"
)
DB_FILE = os.environ.get(
    "FIRESTARTER_DB_FILE",
    os.path.join(_DATA_DIR, "chip_database.json"),
)

# Pitfall 6 defense: derive repo root from __file__ so the default --output
# path is absolute and robust against operator cwd. The tool lives at
# <repo-root>/firestarter_app/tools/audit_coverage_matrix.py, so the repo
# root is three dirname() hops up.
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
DEFAULT_OUTPUT = os.path.join(_REPO_ROOT, ".planning", "v1.3-COVERAGE-MATRIX.md")
DEFAULT_LEDGER = os.path.join(_REPO_ROOT, ".planning", "v1.3-defect-coverage-ids.json")


# ---------------------------------------------------------------------------
# Helpers (Patterns D + E + F from PATTERNS.md)
# ---------------------------------------------------------------------------

def iter_in_scope_rows(db_raw):
    """Yield (mfg, chip) for every algo-0x07 or algo-0x08 chip in db_raw.

    Pattern A (check_dispatch.py:86-105) shape: skip non-list manufacturer
    values, then filter `chip["programming"]["algorithm"] in (0x07, 0x08)`.
    """
    for mfg, chips in db_raw.items():
        if not isinstance(chips, list):
            continue
        for chip in chips:
            proto = chip.get("programming", {}).get("algorithm", 0) or 0
            if proto in (0x07, 0x08):
                yield mfg, chip


def parse_pulse_us(s):
    """'10000 us' -> 10000. Raise on shape mismatch (Pitfall 3 fail-fast)."""
    if not isinstance(s, str) or not s.endswith(" us"):
        raise ValueError(f"Unexpected pulse_duration shape: {s!r}")
    return int(s[:-3])


def pulse_bucket(us):
    """D-09 bucketing: microseconds-integer input → label string."""
    if us < 100:
        return "< 100 us"
    if us < 1000:
        return "100-999 us"
    if us < 10_000:
        return "1-9 ms"
    if us < 100_000:
        return "10-99 ms"
    return "100 ms-1 s"


_SIZE_LABELS = {
    2048: "2K",
    8192: "8K",
    16384: "16K",
    32768: "32K",
    65536: "64K",
    131072: "128K",
    262144: "256K",
    524288: "512K",
    1048576: "1 MB",
}


def size_label(size_bytes):
    """Human-readable size label for the matrix output."""
    return _SIZE_LABELS.get(size_bytes, str(size_bytes))


def md_table(headers, rows):
    """Pipe-style markdown table; per-column ljust to max cell width.

    Pattern D (08-MEASUREMENT.md:233-236) — pipe-fenced rows, hyphen
    separator row, left-justified padding.
    """
    str_headers = [str(h) for h in headers]
    str_rows = [[str(c) for c in r] for r in rows]
    widths = [
        max(
            len(str_headers[i]),
            max((len(r[i]) for r in str_rows), default=0),
        )
        for i in range(len(str_headers))
    ]

    def line(cells):
        return "| " + " | ".join(
            cells[i].ljust(widths[i]) for i in range(len(cells))
        ) + " |"

    header_line = line(str_headers)
    sep_line = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    return "\n".join([header_line, sep_line] + [line(r) for r in str_rows])


# ---------------------------------------------------------------------------
# Summary computation
# ---------------------------------------------------------------------------

def compute_summary(rows, db_raw):
    """Build the summary dict the §1 + §2 emitters consume.

    rows: list of (mfg, chip) tuples from iter_in_scope_rows.
    db_raw: full DB dict (needed for total_chips count).
    """
    total_chips = 0
    algo_counter = Counter()
    for mfg, chips in db_raw.items():
        if not isinstance(chips, list):
            continue
        for chip in chips:
            total_chips += 1
            proto = chip.get("programming", {}).get("algorithm", 0) or 0
            algo_counter[proto] += 1

    in_scope_count = len(rows)
    variant_count = sum(
        len(chip.get("part_number", "").split(",")) for _, chip in rows
    )

    # Per-(algo, pinout) row counts.
    pinout_by_algo = defaultdict(Counter)
    pulse_by_algo = defaultdict(Counter)
    pulse_bucket_by_algo = defaultdict(Counter)
    size_by_algo = defaultdict(Counter)
    chip_id_check_by_algo = defaultdict(Counter)

    for _mfg, chip in rows:
        algo = chip["programming"]["algorithm"]
        pinout = chip.get("pinout", "")
        pulse_us = parse_pulse_us(chip["programming"]["pulse_duration"])
        size = chip.get("electrical", {}).get("size_bytes", 0)
        cid = bool(chip.get("programming", {}).get("chip_id_check", False))

        pinout_by_algo[algo][pinout] += 1
        pulse_by_algo[algo][pulse_us] += 1
        pulse_bucket_by_algo[algo][pulse_bucket(pulse_us)] += 1
        size_by_algo[algo][size] += 1
        chip_id_check_by_algo[algo][cid] += 1

    return {
        "total_chips": total_chips,
        "algo_counter": algo_counter,
        "in_scope_count": in_scope_count,
        "variant_count": variant_count,
        "row_count": in_scope_count,
        "pinout_by_algo": pinout_by_algo,
        "pulse_by_algo": pulse_by_algo,
        "pulse_bucket_by_algo": pulse_bucket_by_algo,
        "size_by_algo": size_by_algo,
        "chip_id_check_by_algo": chip_id_check_by_algo,
    }


# ---------------------------------------------------------------------------
# §1 — Summary Statistics
# ---------------------------------------------------------------------------

def emit_summary(summary, severity_counts=None):
    """Return the §1 markdown block as a single string.

    `severity_counts` (if provided) is a dict like
    `{"HAZARD": 1, "CORRECTNESS": 4, "VARIANCE": 12}` from the Wave 3
    defect-detection pass; used to replace the Wave 1 TBD placeholder.
    """
    parts = ["## §1: Summary Statistics", ""]

    # a. Top-level counts
    top_rows = [
        ["Total chips", summary["total_chips"]],
        ["In-scope (algo 0x07 + 0x08)", summary["in_scope_count"]],
        ["Row count (in-scope)", summary["row_count"]],
        ["Variant count (in-scope, alias-expanded)", summary["variant_count"]],
    ]
    parts.append("### Top-level counts")
    parts.append("")
    parts.append(md_table(["Field", "Value"], top_rows))
    parts.append("")

    # b. Per-algorithm histogram (full DB)
    algo_rows = [
        [f"0x{algo:02X}", summary["algo_counter"][algo]]
        for algo in sorted(summary["algo_counter"])
    ]
    parts.append("### Per-algorithm histogram (full DB)")
    parts.append("")
    parts.append(md_table(["Algorithm", "Row count"], algo_rows))
    parts.append("")

    # c. Per-pinout for algo 0x07
    pin07_rows = [
        [pinout, summary["pinout_by_algo"][0x07][pinout]]
        for pinout in sorted(summary["pinout_by_algo"][0x07])
    ]
    parts.append("### Per-pinout class — algo 0x07 (212 chips)")
    parts.append("")
    parts.append(md_table(["Pinout", "Row count"], pin07_rows))
    parts.append("")

    # d. Per-pinout for algo 0x08
    pin08_rows = [
        [pinout, summary["pinout_by_algo"][0x08][pinout]]
        for pinout in sorted(summary["pinout_by_algo"][0x08])
    ]
    parts.append("### Per-pinout class — algo 0x08 (127 chips)")
    parts.append("")
    parts.append(md_table(["Pinout", "Row count"], pin08_rows))
    parts.append("")

    # e. Per-pulse-bucket (both algorithms in one table)
    all_buckets = sorted(
        set(summary["pulse_bucket_by_algo"][0x07])
        | set(summary["pulse_bucket_by_algo"][0x08]),
        key=_pulse_bucket_sort_key,
    )
    pulse_bucket_rows = [
        [
            bucket,
            summary["pulse_bucket_by_algo"][0x07].get(bucket, 0),
            summary["pulse_bucket_by_algo"][0x08].get(bucket, 0),
        ]
        for bucket in all_buckets
    ]
    parts.append("### Per-pulse-bucket distribution")
    parts.append("")
    parts.append(
        md_table(["Bucket", "algo-0x07", "algo-0x08"], pulse_bucket_rows)
    )
    parts.append("")

    # f. Per-size-bucket
    all_sizes = sorted(
        set(summary["size_by_algo"][0x07]) | set(summary["size_by_algo"][0x08])
    )
    size_rows = [
        [
            f"{size} / {size_label(size)}",
            summary["size_by_algo"][0x07].get(size, 0),
            summary["size_by_algo"][0x08].get(size, 0),
        ]
        for size in all_sizes
    ]
    parts.append("### Per-size distribution")
    parts.append("")
    parts.append(
        md_table(["Size (bytes / label)", "algo-0x07", "algo-0x08"], size_rows)
    )
    parts.append("")

    # g. chip_id_check distribution
    cid_rows = []
    for algo in (0x07, 0x08):
        true_n = summary["chip_id_check_by_algo"][algo].get(True, 0)
        false_n = summary["chip_id_check_by_algo"][algo].get(False, 0)
        cid_rows.append([f"0x{algo:02X}", true_n, false_n])
    parts.append("### chip_id_check distribution")
    parts.append("")
    parts.append(md_table(["Algorithm", "True", "False"], cid_rows))
    parts.append("")

    # h. Severity-tier counts (D-12) — populated by Wave 3 detection pass.
    parts.append("### Severity-tier finding counts (D-12)")
    parts.append("")
    if severity_counts is None:
        parts.append("- HAZARD: TBD")
        parts.append("- CORRECTNESS: TBD")
        parts.append("- VARIANCE: TBD")
        parts.append("")
        parts.append("_Populated in Wave 3 (Plan 11-04 — defect-findings emit)._")
    else:
        parts.append(f"- HAZARD: {severity_counts.get('HAZARD', 0)}")
        parts.append(f"- CORRECTNESS: {severity_counts.get('CORRECTNESS', 0)}")
        parts.append(f"- VARIANCE: {severity_counts.get('VARIANCE', 0)}")
        parts.append("")
        parts.append(
            "_DEFECT-COV-00 is the v1.0 Phase 13 RESOLVED baseline (D-15) and "
            "is not counted in the live-detection totals above._"
        )

    return "\n".join(parts)


def _pulse_bucket_sort_key(bucket):
    """Sort pulse buckets in ascending magnitude (matches D-09 order)."""
    order = {
        "< 100 us": 0,
        "100-999 us": 1,
        "1-9 ms": 2,
        "10-99 ms": 3,
        "100 ms-1 s": 4,
    }
    return order.get(bucket, 99)


# ---------------------------------------------------------------------------
# Pattern F — Sort key for deterministic enumeration (§3 + §5)
# ---------------------------------------------------------------------------

def sort_key(mfg, chip):
    """Pattern F (PATTERNS.md lines 593-601 + RESEARCH.md 564-575) — D-06 sort.

    Returns the 5-tuple `(algorithm, pinout, size_bytes, manufacturer,
    first_alias)` used to order every §3 + §5 enumeration. The first_alias
    is the leading comma-delimited variant in `part_number` (per D-06: rows
    cover the variant set verbatim, but sort by the first alias).

    This tuple is the load-bearing contract for byte-identical re-runs
    (Pattern B codegen-idempotence guarantee).
    """
    return (
        chip["programming"]["algorithm"],
        chip["pinout"],
        chip["electrical"]["size_bytes"],
        mfg,
        chip["part_number"].split(",")[0],
    )


# ---------------------------------------------------------------------------
# §3 — Full Enumeration (per-algorithm sub-tables, D-06 sort)
# ---------------------------------------------------------------------------

_ENUM_HEADERS = [
    "Manufacturer",
    "Part Number(s)",
    "Pin Count",
    "Size (bytes)",
    "Pulse Duration",
    "Chip ID Check",
    "Chip ID Value",
    "Pinout",
    "Electrical Type",
]


def _md_escape(s):
    """Escape `|` inside markdown table cells (defensive — DB rows are not
    known to contain `|`, but rendering must remain robust)."""
    return str(s).replace("|", r"\|")


def _enum_row(mfg, chip):
    """Build one §3 markdown-row payload from (mfg, chip).

    Field access patterns are documented in the plan's <interfaces> block.
    chip_id_check renders as Python `str(bool)` ("True"/"False");
    chip_id_value renders verbatim because the live DB stores it as a string
    (`"0x00000108"` etc.) — confirmed via `chip_database.json` inspection.
    """
    cid_check = bool(chip["programming"]["chip_id_check"])
    cid_value = chip["programming"]["chip_id_value"]
    return [
        _md_escape(mfg),
        _md_escape(chip["part_number"]),
        _md_escape(chip["electrical"]["pin_count"]),
        _md_escape(chip["electrical"]["size_bytes"]),
        _md_escape(chip["programming"]["pulse_duration"]),
        "True" if cid_check else "False",
        _md_escape(cid_value),
        _md_escape(chip["pinout"]),
        _md_escape(chip["electrical"]["type"]),
    ]


def emit_full_enumeration(rows):
    """Return the §3 markdown block as a single string.

    Split into two per-algorithm sub-tables (CONTEXT.md "Claude's Discretion"
    + PATTERNS.md "Multi-table-stacked layout"): algo-0x07 first, then
    algo-0x08. Rows within each sub-table are sorted by Pattern F (D-06).

    `rows` is a list of (mfg, chip) tuples from `iter_in_scope_rows`.
    """
    parts = ["## §3: Full Enumeration", ""]
    parts.append(
        "One row per `chip_database.json` record (not per variant). "
        "339 total rows: 212 algo-0x07 + 127 algo-0x08. "
        "Sort: (algorithm, pinout, size_bytes, manufacturer, first_alias). "
        "Per D-06."
    )
    parts.append("")

    algo_07_rows = [
        (mfg, chip) for mfg, chip in rows
        if chip["programming"]["algorithm"] == 0x07
    ]
    algo_08_rows = [
        (mfg, chip) for mfg, chip in rows
        if chip["programming"]["algorithm"] == 0x08
    ]

    algo_07_rows = sorted(algo_07_rows, key=lambda mc: sort_key(*mc))
    algo_08_rows = sorted(algo_08_rows, key=lambda mc: sort_key(*mc))

    parts.append(f"### algo-0x07 ({len(algo_07_rows)} rows)")
    parts.append("")
    parts.append(
        md_table(
            _ENUM_HEADERS,
            [_enum_row(mfg, chip) for mfg, chip in algo_07_rows],
        )
    )
    parts.append("")
    parts.append(f"### algo-0x08 ({len(algo_08_rows)} rows)")
    parts.append("")
    parts.append(
        md_table(
            _ENUM_HEADERS,
            [_enum_row(mfg, chip) for mfg, chip in algo_08_rows],
        )
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# §2 — DB Count Reconciliation
# ---------------------------------------------------------------------------

def emit_reconciliation(summary):
    """Return the §2 markdown block as a single string.

    Per D-08, §2 is regenerated from live DB on every run so future DB
    regenerations keep §2 honest.
    """
    parts = ["## §2: DB Count Reconciliation", ""]

    parts.append(
        "Live DB counts vs the planning-document claims quoted in "
        "`.planning/PROJECT.md`, `.planning/ROADMAP.md`, "
        "`.planning/REQUIREMENTS.md`, `.planning/STATE.md` at v1.3 milestone "
        "start (2026-05-19):"
    )
    parts.append("")
    parts.append(
        "**Headline:** 743 → 734 (Δ −9), 214 → 212 (algo-0x07), 341 → 339 "
        "(in-scope)."
    )
    parts.append("")
    parts.append(
        "Delta absorbed by v1.0–v1.2 overrides (WARNING-5 algo flip on "
        "DIP28_2764 EEPROM hazard + fm1608-db-mismatch FRAM tagging) plus "
        "upstream `infoic.xml` drift between v1.0 close and v1.3 start. No "
        "archaeology required — see CONTEXT.md \"Claude's Discretion\"."
    )
    parts.append("")

    # Top-level drift table
    live_total = summary["total_chips"]
    live_07 = summary["algo_counter"][0x07]
    live_08 = summary["algo_counter"][0x08]
    live_in_scope = summary["in_scope_count"]
    drift_rows = [
        ["Total chips", live_total, 743, live_total - 743],
        ["algo-0x07", live_07, 214, live_07 - 214],
        ["algo-0x08", live_08, 127, live_08 - 127],
        ["In-scope (0x07 + 0x08)", live_in_scope, 341, live_in_scope - 341],
    ]
    parts.append("### Top-level drift")
    parts.append("")
    parts.append(md_table(["Field", "Live", "Old", "Δ"], drift_rows))
    parts.append("")

    # Per-algorithm drift
    old_per_algo = {
        0x05: 27,
        0x06: 190,
        0x07: 214,
        0x08: 127,
        0x0B: 53,
        0x0D: 41,
        0x0E: 20,
        0x10: 39,
        0x27: 2,
        0x28: 10,
        0x29: 20,
    }
    per_algo_rows = []
    for algo in sorted(set(old_per_algo) | set(summary["algo_counter"])):
        live = summary["algo_counter"].get(algo, 0)
        old = old_per_algo.get(algo, 0)
        per_algo_rows.append([f"0x{algo:02X}", live, old, live - old])
    parts.append("### Per-algorithm drift vs PROJECT.md:150")
    parts.append("")
    parts.append(
        md_table(["Algorithm", "Live", "Old", "Δ"], per_algo_rows)
    )
    parts.append("")
    parts.append(
        "**Notable shifts:** 0x0B (53→40, −13) + 0x0D (41→23, −18) + 0x28 "
        "(10→34, +24) — explained by the fm1608-db-mismatch override at "
        "`build_db.py:425-468` flipping `type=4 ∧ proto_id ∈ {0x07, 0x08, "
        "0x0B}` chips into 0x28, plus upstream `infoic.xml` drift for the "
        "0x0D bucket."
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# §4 — Defect findings: hashing, ledger, detection, emit (Wave 3 / Plan 11-04)
# ---------------------------------------------------------------------------
#
# Pattern C (PATTERNS.md "Stable defect-ID hash composition" + RESEARCH.md
# Pattern 4 lines 195-218): every finding has a deterministic 16-hex hash of
# its (severity, axis, signature) tuple. The hash → DEFECT-COV-NN mapping is
# persisted in `.planning/v1.3-defect-coverage-ids.json` so IDs survive DB
# regenerations (D-13 stable identity contract).


def finding_hash(severity, axis, signature):
    """Return the 16-hex stable hash of a finding's identity tuple.

    Verbatim shape from RESEARCH.md Pattern 4: sha1 over canonical JSON
    (sort_keys=True, compact separators) of `{severity, axis, signature}`,
    truncated to 16 hex chars. Truncation is intentional — 16 hex = 64 bits
    of state, more than enough to avoid collisions across the < 100 expected
    findings while keeping rendered IDs readable (D-13).

    `signature` may be a tuple or list; it is coerced to a list for JSON
    serializability (tuples are not JSON-native).
    """
    payload = {
        "severity": severity,
        "axis": axis,
        "signature": list(signature),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:16]


def load_ledger(ledger_path):
    """Read `ledger_path` as JSON dict; return `{}` if missing (Pitfall 4 cold-start).

    A parse error is surfaced (not swallowed) so a corrupted ledger fails
    fast rather than silently overwriting itself.
    """
    try:
        return json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def save_ledger(ledger, ledger_path):
    """Write the ledger as `json.dumps(..., indent=2, sort_keys=True) + \"\\n\"`.

    LF-only newlines, UTF-8, trailing newline — Pattern B invariants 1+3+4.
    """
    blob = json.dumps(ledger, indent=2, sort_keys=True) + "\n"
    Path(ledger_path).write_text(blob, encoding="utf-8", newline="\n")


def mint_or_reuse(ledger, severity, axis, signature, next_n_holder):
    """Return the DEFECT-COV-NN ID for this finding; mint a new one if absent.

    `next_n_holder` is a single-element list used as a mutable counter so
    repeated calls within one generate_matrix() pass share the next-N
    increment (RESEARCH.md Code Examples lines 609-619).

    IDs are formatted with 2-digit zero-padding (`DEFECT-COV-07`) until
    NN >= 100, at which point Python's natural width takes over
    (RESEARCH.md Open Question 2 — current corpus is well below 100).
    """
    h = finding_hash(severity, axis, signature)
    if h in ledger:
        return ledger[h]
    new_id = f"DEFECT-COV-{next_n_holder[0]:02d}"
    ledger[h] = new_id
    next_n_holder[0] += 1
    return new_id


def detect_resolved_baseline(ledger, next_n_holder):
    """Seed `DEFECT-COV-00` into the ledger if absent (D-15 RESOLVED baseline).

    DEFECT-COV-00 is the v1.0 Phase 13 WARNING-5 override — the
    `DIP28_2764` HAZARD that was caught and fixed in v1.0. Including it
    in the ledger makes the matrix's §4 narrative complete (the audit
    starts from the last known-good state) and reserves NN=00 forever so
    new mints start at NN=01.

    Signature: severity=HAZARD, axis=pinout_vs_algorithm,
    signature=(["DIP28_2764","DIP28_28C256"], 0x07, "Flash/EEPROM").
    Note this is the PRE-rederive _etype value because at WARNING-5
    predicate time `_etype == "Flash/EEPROM"` (per build_db.py:415-417).
    """
    h = finding_hash(
        "HAZARD",
        "pinout_vs_algorithm",
        (["DIP28_2764", "DIP28_28C256"], 0x07, "Flash/EEPROM"),
    )
    if h not in ledger:
        ledger[h] = "DEFECT-COV-00"


def _examples_for(rows_subset, limit=3):
    """Return up to `limit` 'MFG/first_alias' strings sorted by sort_key."""
    sorted_rows = sorted(rows_subset, key=lambda mc: sort_key(*mc))
    out = []
    for mfg, chip in sorted_rows[:limit]:
        first_alias = chip["part_number"].split(",")[0]
        out.append(f"{mfg}/{first_alias}")
    return out


def detect_hazard(rows):
    """Yield HAZARD findings.

    Currently yields the single new 42-row cluster: pinout in
    {DIP28_28C64, DIP28_28C256} AND algorithm == 0x07. After
    build_db.py:481-486 re-derivation, every algo-0x07 row has
    `electrical.type == "UV-EPROM"`, so the WARNING-5 override predicate
    (build_db.py:415-423) which requires `_etype == "Flash/EEPROM"` is
    structurally unreachable for these rows.
    """
    cluster = [
        (mfg, chip) for mfg, chip in rows
        if chip["pinout"] in ("DIP28_28C64", "DIP28_28C256")
        and chip["programming"]["algorithm"] == 0x07
    ]
    if not cluster:
        return

    affected = len(cluster)
    signature = (["DIP28_28C64", "DIP28_28C256"], 0x07, "UV-EPROM")
    yield {
        "severity": "HAZARD",
        "axis": "pinout_vs_algorithm",
        "signature": signature,
        "hash": finding_hash("HAZARD", "pinout_vs_algorithm", signature),
        "affected_chips": affected,
        "title": (
            f"DIP28_28C64 + DIP28_28C256 on algorithm 0x07 — "
            f"WARNING-5 override structurally unreachable for {affected} rows"
        ),
        "root_cause_hypothesis": (
            "Upstream infoic.xml lacks the EEPROM flag bit for these chips; "
            "WARNING-5 predicate gates on _etype == Flash/EEPROM which is "
            "False at predicate time, then post-override re-derivation "
            "rewrites all 0x07 chips to UV-EPROM (build_db.py:481-486), "
            "making the predicate structurally unreachable."
        ),
        "suggested_fix_venue": (
            "v1.4 build_db.py override (extend WARNING-5 predicate to drop "
            "the _etype == Flash/EEPROM clause OR add a new override class "
            "keyed on pinout in {DIP28_28C64, DIP28_28C256} and proto_id == 0x07)"
        ),
        "examples": _examples_for(cluster, 3),
    }


def detect_correctness(rows):
    """Yield CORRECTNESS findings: pulse_duration outliers per cluster.

    Group rows by (algorithm, pinout, size_bytes). Within each group with
    >= 2 distinct pulse values, compute the cluster median; flag rows whose
    pulse differs from the median by >= 10x (max/min ratio). One finding
    per (cluster, outlier-pulse-bucket) pair.
    """
    clusters = defaultdict(list)
    for mfg, chip in rows:
        algo = chip["programming"]["algorithm"]
        pinout = chip["pinout"]
        size = chip["electrical"]["size_bytes"]
        clusters[(algo, pinout, size)].append((mfg, chip))

    findings = []
    for (algo, pinout, size), members in clusters.items():
        pulses = [parse_pulse_us(chip["programming"]["pulse_duration"]) for _, chip in members]
        if len(set(pulses)) < 2:
            continue
        median = statistics.median(pulses)
        if median <= 0:
            continue

        # Identify outlier rows: any pulse whose max/min ratio vs the median is >= 10x.
        outlier_rows = []
        for (mfg, chip), pulse in zip(members, pulses):
            if pulse <= 0:
                continue
            ratio = max(pulse, median) / min(pulse, median)
            if ratio >= 10:
                outlier_rows.append((mfg, chip))

        if not outlier_rows:
            continue

        # One finding per (algo, pinout, size, manufacturer, first_alias) outlier
        # so multi-manufacturer clusters split into per-manufacturer findings
        # per D-14 signature schema.
        per_sig = defaultdict(list)
        for mfg, chip in outlier_rows:
            first_alias = chip["part_number"].split(",")[0]
            per_sig[(algo, pinout, size, mfg, first_alias)].append((mfg, chip))

        for sig_tuple, sig_rows in per_sig.items():
            algo_i, pinout_s, size_b, mfg_s, alias_s = sig_tuple
            signature = (algo_i, pinout_s, size_b, mfg_s, alias_s)
            findings.append({
                "severity": "CORRECTNESS",
                "axis": "pulse_duration_outlier",
                "signature": signature,
                "hash": finding_hash("CORRECTNESS", "pulse_duration_outlier", signature),
                "affected_chips": len(sig_rows),
                "title": (
                    f"{mfg_s}/{alias_s} pulse_duration outlier (>=10x median) "
                    f"in algo-0x{algo_i:02X} / {pinout_s} / {size_b}B cluster"
                ),
                "root_cause_hypothesis": (
                    "Pulse duration deviates by at least 10x from cluster "
                    "median; possible upstream infoic.xml mis-classification "
                    "or real-chip variance — verify against datasheet."
                ),
                "suggested_fix_venue": "awaiting bench data",
                "examples": _examples_for(sig_rows, 3),
            })

    yield from findings


def detect_variance(rows):
    """Yield VARIANCE findings: chip_id_check toggles + chip_id_value drift.

    Per D-14 signature schema, group by (algorithm, pinout, size_bytes,
    manufacturer). Two axes:
      (a) chip_id_check_toggle — different members of a cluster have
          different `chip_id_check` boolean values
      (b) chip_id_value_drift — multiple members have chip_id_check=True
          but their `chip_id_value` strings differ
    """
    clusters = defaultdict(list)
    for mfg, chip in rows:
        algo = chip["programming"]["algorithm"]
        pinout = chip["pinout"]
        size = chip["electrical"]["size_bytes"]
        clusters[(algo, pinout, size, mfg)].append((mfg, chip))

    findings = []
    for (algo, pinout, size, mfg), members in clusters.items():
        if len(members) < 2:
            continue

        cid_checks = {
            bool(chip["programming"].get("chip_id_check", False))
            for _, chip in members
        }
        if len(cid_checks) > 1:
            signature = (algo, pinout, size, mfg)
            findings.append({
                "severity": "VARIANCE",
                "axis": "chip_id_check_toggle",
                "signature": signature,
                "hash": finding_hash("VARIANCE", "chip_id_check_toggle", signature),
                "affected_chips": len(members),
                "title": (
                    f"{mfg} on algo-0x{algo:02X} / {pinout} / {size}B: "
                    "chip_id_check toggles between members"
                ),
                "root_cause_hypothesis": (
                    "Cluster members disagree on whether chip_id readout is "
                    "supported; likely upstream infoic.xml drift across "
                    "die revisions or pin-compatible aliases."
                ),
                "suggested_fix_venue": "documentation-only",
                "examples": _examples_for(members, 3),
            })

        # chip_id_value drift among members with chip_id_check=True.
        true_members = [
            (mfg2, chip) for mfg2, chip in members
            if bool(chip["programming"].get("chip_id_check", False))
        ]
        if len(true_members) >= 2:
            cid_values = {chip["programming"].get("chip_id_value") for _, chip in true_members}
            if len(cid_values) > 1:
                signature = (algo, pinout, size, mfg)
                findings.append({
                    "severity": "VARIANCE",
                    "axis": "chip_id_value_drift",
                    "signature": signature,
                    "hash": finding_hash("VARIANCE", "chip_id_value_drift", signature),
                    "affected_chips": len(true_members),
                    "title": (
                        f"{mfg} on algo-0x{algo:02X} / {pinout} / {size}B: "
                        "chip_id_value differs across members with chip_id_check=True"
                    ),
                    "root_cause_hypothesis": (
                        "Legitimate die-revision identity drift OR upstream "
                        "infoic.xml carries different signature bytes for "
                        "pin-compatible aliases; expected for some Atmel / "
                        "ST / SST families."
                    ),
                    "suggested_fix_venue": "documentation-only",
                    "examples": _examples_for(true_members, 3),
                })

    yield from findings


def emit_defects(findings, ledger, next_n_holder):
    """Render §4 as a list of markdown lines.

    Order: DEFECT-COV-00 RESOLVED baseline first, then HAZARD, then
    CORRECTNESS, then VARIANCE (D-12 severity-tier order). Within each
    tier, sort by finding hash ascending for stable output.
    """
    lines = ["## §4: DB Inconsistencies / Defect Candidates", ""]
    lines.append(
        "Per D-12 severity tiers: HAZARD (potential hardware damage), "
        "CORRECTNESS (likely DB-data bug, no damage path), VARIANCE "
        "(legitimate diversity captured for completeness). IDs are stable "
        "across DB regenerations — the on-disk ledger at "
        "`.planning/v1.3-defect-coverage-ids.json` maps each finding's "
        "16-hex hash to its DEFECT-COV-NN."
    )
    lines.append("")

    # DEFECT-COV-00 RESOLVED block (D-15).
    lines.append("### DEFECT-COV-00 — RESOLVED in v1.0 Phase 13 (WARNING-5)")
    lines.append("")
    lines.append(
        "Baseline RESOLVED entry — the v1.0 audit caught and fixed the "
        "`DIP28_2764` 5V-EEPROM HAZARD. Predicate quoted verbatim from "
        "`firestarter_app/tools/build_db.py:415-423`:"
    )
    lines.append("")
    lines.append("```python")
    lines.append('if (pinout_key in ("DIP28_2764", "DIP28_28C256")')
    lines.append('        and proto_id == 0x07')
    lines.append('        and _etype == "Flash/EEPROM"):')
    lines.append('    print(')
    lines.append('        f"INFO: {mfg_name}/{name} algorithm override 0x07->0x0D "')
    lines.append('        f"(WARNING-5: 5V EEPROM with non-EPROM pinout — route through configure_eeprom28c)",')
    lines.append('        file=sys.stderr,')
    lines.append('    )')
    lines.append('    proto_id = 0x0D')
    lines.append("```")
    lines.append("")
    resolved_table_rows = [
        ["severity", "HAZARD"],
        ["affected_chips", "~23 chips (pre-override; resolved)"],
        ["axis", "pinout_vs_algorithm"],
        [
            "root_cause_hypothesis",
            "DIP28_2764 + 0x07 EPROM_STD path asserted 12V P1_VPP_ENABLE on "
            "socket pin 1 which is A14 on 28C-family 5V EEPROMs.",
        ],
        ["suggested_fix_venue", "documentation-only (resolved)"],
        [
            "examples",
            "AT28C256, AT28C64, M28256, UPD28C64, X28C256",
        ],
    ]
    lines.append(md_table(["Field", "Value"], resolved_table_rows))
    lines.append("")

    # Tier-first order, then hash-ascending within tier.
    tier_order = {"HAZARD": 0, "CORRECTNESS": 1, "VARIANCE": 2}
    sorted_findings = sorted(
        findings,
        key=lambda f: (tier_order.get(f["severity"], 99), f["hash"]),
    )

    for finding in sorted_findings:
        defect_id = mint_or_reuse(
            ledger,
            finding["severity"],
            finding["axis"],
            finding["signature"],
            next_n_holder,
        )
        lines.append(
            f"### {defect_id} — {finding['severity']}: {finding['title']}"
        )
        lines.append("")
        table_rows = [
            ["severity", finding["severity"]],
            ["affected_chips", str(finding["affected_chips"])],
            ["axis", finding["axis"]],
            ["root_cause_hypothesis", finding["root_cause_hypothesis"]],
            ["suggested_fix_venue", finding["suggested_fix_venue"]],
            ["examples", ", ".join(finding["examples"])],
        ]
        lines.append(md_table(["Field", "Value"], table_rows))
        lines.append("")

    return lines


def emit_placeholder_sections():
    """Return the §5 stub block. §4 is now wired (Wave 3 / Plan 11-04)."""
    s5 = (
        "## §5: BENCH Coverage Proof\n"
        "\n"
        "_Populated in Wave 4 (Plan 11-05 — bench-coverage proof)._"
    )
    return s5


# ---------------------------------------------------------------------------
# Top-level generate_matrix entry point
# ---------------------------------------------------------------------------

_FILE_HEADER = (
    "# v1.3 Coverage Matrix\n"
    "\n"
    "Generated by `tools/audit_coverage_matrix.py` — DO NOT EDIT BY HAND. "
    "Re-run the tool when `chip_database.json` regenerates.\n"
)


def generate_matrix(output, ledger_path, check=False):
    """Generate the coverage matrix + ledger.

    Returns: 0 on clean generate (or `--check` with no new findings);
             1 on DB parse error or `--check` with new findings (Wave 1: only
             the DB parse-error case can return 1; ledger minting lands in
             Wave 3).
    """
    output = str(output)
    ledger_path = str(ledger_path)

    # Load DB (Pitfall 3 — surface parse errors).
    try:
        with open(DB_FILE, encoding="utf-8") as f:
            db_raw = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: chip_database.json not found at {DB_FILE}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"ERROR: chip_database.json parse failed: {exc}", file=sys.stderr)
        return 1

    rows = list(iter_in_scope_rows(db_raw))
    summary = compute_summary(rows, db_raw)

    # Load ledger (Pitfall 4 cold-start handled by load_ledger).
    try:
        ledger = load_ledger(ledger_path)
    except json.JSONDecodeError as exc:
        print(f"ERROR: ledger {ledger_path} parse failed: {exc}", file=sys.stderr)
        return 1

    # Run detection pass: gather all findings BEFORE deciding to mint or
    # check. This keeps --check semantics dry-run (D-03).
    findings = (
        list(detect_hazard(rows))
        + list(detect_correctness(rows))
        + list(detect_variance(rows))
    )

    # Severity-tier counts for §1 (live detection only — DEFECT-COV-00 is
    # excluded as a static RESOLVED entry per D-15).
    severity_counts = Counter(f["severity"] for f in findings)

    # --check semantics (D-03): dry-run drift gate.
    # 1. Seed the RESOLVED baseline into a copy of the on-disk ledger.
    # 2. Compute hash for each detected finding; if any hash is absent from
    #    that ledger (including the post-seed baseline), exit 1.
    # 3. Do NOT mutate the on-disk ledger.
    if check:
        check_ledger = dict(ledger)
        detect_resolved_baseline(check_ledger, [1])
        new_finding_seen = False
        for f in findings:
            if f["hash"] not in check_ledger:
                new_finding_seen = True
                break
        # A baseline that was missing from the on-disk ledger also counts as
        # drift — the operator must regenerate to seed DEFECT-COV-00.
        if "DEFECT-COV-00" not in ledger.values():
            new_finding_seen = True
        return 1 if new_finding_seen else 0

    # Normal generate path: seed RESOLVED baseline, then mint IDs for new
    # findings. next_n_holder starts at max(existing_NN, 0) + 1 (DEFECT-COV-00
    # is reserved — minting starts at 01).
    detect_resolved_baseline(ledger, [1])
    existing_ns = []
    for v in ledger.values():
        try:
            existing_ns.append(int(v.split("-")[-1]))
        except (ValueError, IndexError):
            continue
    start_n = max([0] + existing_ns) + 1
    if start_n < 1:
        start_n = 1
    next_n_holder = [start_n]

    # Assemble matrix body.
    s1 = emit_summary(summary, severity_counts=severity_counts)
    s2 = emit_reconciliation(summary)
    s3 = emit_full_enumeration(rows)
    s4 = "\n".join(emit_defects(findings, ledger, next_n_holder)).rstrip("\n")
    s5 = emit_placeholder_sections()
    body_parts = [
        _FILE_HEADER.rstrip("\n"),
        "",
        "---",
        "",
        s1,
        "",
        "---",
        "",
        s2,
        "",
        "---",
        "",
        s3,
        "",
        "---",
        "",
        s4,
        "",
        "---",
        "",
        s5,
    ]
    content = "\n".join(body_parts) + "\n"

    # Pattern B invariant 3: LF-only, UTF-8, trailing newline.
    Path(output).write_text(content, encoding="utf-8", newline="\n")

    # Persist ledger (sort_keys=True + LF + trailing \n).
    save_ledger(ledger, ledger_path)

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate the v1.3 coverage matrix from chip_database.json "
            "(algo-0x07 + algo-0x08 in-scope rows). Idempotent codegen — "
            "byte-identical output across runs."
        ),
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=(
            "Output path for the coverage matrix markdown. Default: "
            "<repo-root>/.planning/v1.3-COVERAGE-MATRIX.md (absolute)."
        ),
    )
    parser.add_argument(
        "--ledger",
        default=DEFAULT_LEDGER,
        help=(
            "Path to the stable defect-ID ledger JSON. Default: "
            "<repo-root>/.planning/v1.3-defect-coverage-ids.json (absolute)."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Exit 1 if generating would mint new DEFECT-COV-NN IDs (Wave 3+); "
            "exit 0 otherwise. Wave 1: only DB parse errors can cause exit 1."
        ),
    )
    args = parser.parse_args()
    rc = generate_matrix(args.output, args.ledger, check=args.check)
    sys.exit(rc)


if __name__ == "__main__":
    main()
