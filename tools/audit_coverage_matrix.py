"""
Coverage Matrix & DB Inconsistency Audit.

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

Exit codes:
  0 — clean generate, or `--check` with no new findings.
  1 — `--check` would mint a new DEFECT-COV-NN, OR DB parse error.

Idempotence contract:
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

# Module-top path constants (lifted verbatim from check_dispatch.py).
_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "firestarter", "data")
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
DEFAULT_OUTPUT_ALL = os.path.join(
    _REPO_ROOT, ".planning", "v1.3-COVERAGE-MATRIX-ALL.md"
)
DEFAULT_LEDGER_ALL = os.path.join(
    _REPO_ROOT, ".planning", "v1.3-defect-coverage-ids-all.json"
)


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


def iter_all_rows(db_raw):
    """Yield (mfg, chip) for every chip in db_raw — no algorithm filter.

    Used by the `--all-algorithms` wide-scan path. Rows with missing or
    falsy `programming.algorithm` are still yielded; the per-algo grouping
    layer assigns them to algo `0` (and surfaces that as a finding).
    """
    for mfg, chips in db_raw.items():
        if not isinstance(chips, list):
            continue
        for chip in chips:
            yield mfg, chip


def pulse_bucket(us):
    """Pulse bucketing: microseconds-integer input → label string."""
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


# ---------------------------------------------------------------------------
# BENCH chip map
# ---------------------------------------------------------------------------
#
# BENCH-01..06 are the six bench chips that v1.3 milestone close cites as the
# receipt that the algo-0x07 + algo-0x08 families generalize across the 339
# in-scope DB rows. BENCH-05 and BENCH-06 are candidate-pending — the
# selection decision is owned elsewhere. This matrix
# records the candidate set neutrally and does NOT propose a swap.
#
# Per-chip fields:
#   id:        "BENCH-NN" label (used as table-cell text + cross-references)
#   names:     list of candidate part-number strings; matched against the
#              DB row's comma-joined `part_number` field via membership.
#   algorithm: 0x07 or 0x08 (used to filter the algo-axis tables)
#   pinout:    expected pinout class — used to populate the pinout-coverage
#              table when the chip's actual DB row pulse is unavailable
#              (e.g. BENCH-05 / BENCH-06 candidates).
#   size_bytes: typical size for the candidate set (used for size-coverage
#               when the chip is "candidate" and no concrete DB row name
#               is matched).
#   selection_pending: True for BENCH-05 / BENCH-06 — marks the chip as
#                      candidate until one of the names is selected.

BENCH_CHIP_MAP = [
    {
        "id": "BENCH-01",
        "names": ["W27C512"],
        "algorithm": 0x07,
        "pinout": "DIP28_27512",
        "size_bytes": 65536,
        "selection_pending": False,
    },
    {
        "id": "BENCH-02",
        "names": ["SST27SF512"],
        "algorithm": 0x07,
        "pinout": "DIP28_27512",
        "size_bytes": 65536,
        "selection_pending": False,
    },
    {
        "id": "BENCH-03",
        "names": ["W27C020"],
        "algorithm": 0x08,
        "pinout": "DIP32_STD",
        "size_bytes": 262144,
        "selection_pending": False,
    },
    {
        "id": "BENCH-04",
        "names": ["W27E040"],
        "algorithm": 0x08,
        "pinout": "DIP32_STD",
        "size_bytes": 524288,
        "selection_pending": False,
    },
    {
        "id": "BENCH-05",
        "names": ["W27C257", "W27E257", "SST27SF256"],
        "algorithm": 0x07,
        "pinout": "DIP28_27256",
        "size_bytes": 32768,
        "selection_pending": True,
    },
    {
        "id": "BENCH-06",
        "names": ["W27C010", "W27E010", "W27L010", "SST27SF010"],
        "algorithm": 0x08,
        "pinout": "DIP32_STD",
        "size_bytes": 131072,
        "selection_pending": True,
    },
]


def _bench_chip_label(b):
    """Format a BENCH chip cell — 'BENCH-NN' or 'BENCH-NN (candidate)'."""
    if b.get("selection_pending"):
        return f"{b['id']} (candidate)"
    return b["id"]


def _bench_covered_label(b):
    """Format the Covered? cell for a BENCH chip — 'Y' or 'Y (pending selection)'."""
    if b.get("selection_pending"):
        return "Y (pending selection)"
    return "Y"


def _bench_row_for_chip(rows, names):
    """Find the first (mfg, chip) tuple from `rows` whose `part_number` comma
    list contains any of `names`. Returns None if no match."""
    for mfg, chip in rows:
        pn_list = [p.strip() for p in chip["part_number"].split(",")]
        for n in names:
            if n in pn_list:
                return mfg, chip
    return None


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
        return (
            "| "
            + " | ".join(cells[i].ljust(widths[i]) for i in range(len(cells)))
            + " |"
        )

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
    variant_count = sum(len(chip.get("part_number", "").split(",")) for _, chip in rows)

    # Per-(algo, pinout) row counts.
    pinout_by_algo = defaultdict(Counter)
    pulse_by_algo = defaultdict(Counter)
    pulse_bucket_by_algo = defaultdict(Counter)
    size_by_algo = defaultdict(Counter)
    chip_id_check_by_algo = defaultdict(Counter)

    for _mfg, chip in rows:
        algo = chip["programming"]["algorithm"]
        pinout = chip.get("pinout", "")
        pulse_us = chip["programming"]["pulse_duration_us"]
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
    parts.append(
        f"### Per-pinout class — algo 0x07 ({summary['algo_counter'][0x07]} chips)"
    )
    parts.append("")
    parts.append(md_table(["Pinout", "Row count"], pin07_rows))
    parts.append("")

    # d. Per-pinout for algo 0x08
    pin08_rows = [
        [pinout, summary["pinout_by_algo"][0x08][pinout]]
        for pinout in sorted(summary["pinout_by_algo"][0x08])
    ]
    parts.append(
        f"### Per-pinout class — algo 0x08 ({summary['algo_counter'][0x08]} chips)"
    )
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
    parts.append(md_table(["Bucket", "algo-0x07", "algo-0x08"], pulse_bucket_rows))
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

    # h. Severity-tier counts — populated by the detection pass.
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
    """Sort pulse buckets in ascending magnitude."""
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
    """Canonical row sort.

    Returns the 5-tuple `(algorithm, pinout, size_bytes, manufacturer,
    first_alias)` used to order every §3 + §5 enumeration. The first_alias
    is the leading comma-delimited variant in `part_number` (rows
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
# §3 — Full Enumeration (per-algorithm sub-tables)
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
        _md_escape(chip["programming"]["pulse_duration_us"]),
        "True" if cid_check else "False",
        _md_escape(cid_value),
        _md_escape(chip["pinout"]),
        _md_escape(chip["electrical"]["type"]),
    ]


def emit_full_enumeration(rows):
    """Return the §3 markdown block as a single string.

    Split into two per-algorithm sub-tables (CONTEXT.md "Claude's Discretion"
    + PATTERNS.md "Multi-table-stacked layout"): algo-0x07 first, then
    algo-0x08. Rows within each sub-table use the canonical sort.

    `rows` is a list of (mfg, chip) tuples from `iter_in_scope_rows`.
    """
    parts = ["## §3: Full Enumeration", ""]

    algo_07_rows = [
        (mfg, chip) for mfg, chip in rows if chip["programming"]["algorithm"] == 0x07
    ]
    algo_08_rows = [
        (mfg, chip) for mfg, chip in rows if chip["programming"]["algorithm"] == 0x08
    ]

    algo_07_rows = sorted(algo_07_rows, key=lambda mc: sort_key(*mc))
    algo_08_rows = sorted(algo_08_rows, key=lambda mc: sort_key(*mc))

    parts.append(
        f"One row per `chip_database.json` record (not per variant). "
        f"{len(algo_07_rows) + len(algo_08_rows)} total rows: "
        f"{len(algo_07_rows)} algo-0x07 + {len(algo_08_rows)} algo-0x08. "
        "Sort: (algorithm, pinout, size_bytes, manufacturer, first_alias). "
        "Per D-06."
    )
    parts.append("")

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

    §2 is regenerated from live DB on every run so future DB
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
        "**Headline:** 743 → 734 (Δ −9), 214 → 212 (algo-0x07), 341 → 339 (in-scope)."
    )
    parts.append("")
    parts.append(
        "Delta absorbed by v1.0–v1.2 overrides (WARNING-5 algo flip on "
        "DIP28_2764 EEPROM hazard + fm1608-db-mismatch FRAM tagging) plus "
        "upstream `infoic.xml` drift between v1.0 close and v1.3 start. No "
        'archaeology required — see CONTEXT.md "Claude\'s Discretion".'
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
    parts.append(md_table(["Algorithm", "Live", "Old", "Δ"], per_algo_rows))
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
# §4 — Defect findings: hashing, ledger, detection, emit
# ---------------------------------------------------------------------------
#
# Pattern C (PATTERNS.md "Stable defect-ID hash composition" + RESEARCH.md
# Pattern 4 lines 195-218): every finding has a deterministic 16-hex hash of
# its (severity, axis, signature) tuple. The hash → DEFECT-COV-NN mapping is
# persisted in `.planning/v1.3-defect-coverage-ids.json` so IDs survive DB
# regenerations (stable identity contract).


def finding_hash(severity, axis, signature):
    """Return the 16-hex stable hash of a finding's identity tuple.

    Verbatim shape from RESEARCH.md Pattern 4: sha1 over canonical JSON
    (sort_keys=True, compact separators) of `{severity, axis, signature}`,
    truncated to 16 hex chars. Truncation is intentional — 16 hex = 64 bits
    of state, more than enough to avoid collisions across the < 100 expected
    findings while keeping rendered IDs readable.

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
    """Seed `DEFECT-COV-00` into the ledger if absent (RESOLVED baseline).

    DEFECT-COV-00 is the v1.0 WARNING-5 override — the
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
        (mfg, chip)
        for mfg, chip in rows
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
        pulses = [chip["programming"]["pulse_duration_us"] for _, chip in members]
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
        # per the signature schema.
        per_sig = defaultdict(list)
        for mfg, chip in outlier_rows:
            first_alias = chip["part_number"].split(",")[0]
            per_sig[(algo, pinout, size, mfg, first_alias)].append((mfg, chip))

        for sig_tuple, sig_rows in per_sig.items():
            algo_i, pinout_s, size_b, mfg_s, alias_s = sig_tuple
            signature = (algo_i, pinout_s, size_b, mfg_s, alias_s)
            findings.append(
                {
                    "severity": "CORRECTNESS",
                    "axis": "pulse_duration_outlier",
                    "signature": signature,
                    "hash": finding_hash(
                        "CORRECTNESS", "pulse_duration_outlier", signature
                    ),
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
                }
            )

    yield from findings


def detect_variance(rows):
    """Yield VARIANCE findings: chip_id_check toggles + chip_id_value drift.

    Per the signature schema, group by (algorithm, pinout, size_bytes,
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
            bool(chip["programming"].get("chip_id_check", False)) for _, chip in members
        }
        if len(cid_checks) > 1:
            signature = (algo, pinout, size, mfg)
            findings.append(
                {
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
                }
            )

        # chip_id_value drift among members with chip_id_check=True.
        true_members = [
            (mfg2, chip)
            for mfg2, chip in members
            if bool(chip["programming"].get("chip_id_check", False))
        ]
        if len(true_members) >= 2:
            cid_values = {
                chip["programming"].get("chip_id_value") for _, chip in true_members
            }
            if len(cid_values) > 1:
                signature = (algo, pinout, size, mfg)
                findings.append(
                    {
                        "severity": "VARIANCE",
                        "axis": "chip_id_value_drift",
                        "signature": signature,
                        "hash": finding_hash(
                            "VARIANCE", "chip_id_value_drift", signature
                        ),
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
                    }
                )

    yield from findings


def emit_defects(findings, ledger, next_n_holder):
    """Render §4 as a list of markdown lines.

    Order: DEFECT-COV-00 RESOLVED baseline first, then HAZARD, then
    CORRECTNESS, then VARIANCE (severity-tier order). Within each
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

    # DEFECT-COV-00 RESOLVED block.
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
    lines.append("        and proto_id == 0x07")
    lines.append('        and _etype == "Flash/EEPROM"):')
    lines.append("    print(")
    lines.append('        f"INFO: {mfg_name}/{name} algorithm override 0x07->0x0D "')
    lines.append(
        '        f"(WARNING-5: 5V EEPROM with non-EPROM pinout — route through configure_eeprom28c)",'
    )
    lines.append("        file=sys.stderr,")
    lines.append("    )")
    lines.append("    proto_id = 0x0D")
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
        lines.append(f"### {defect_id} — {finding['severity']}: {finding['title']}")
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


# ---------------------------------------------------------------------------
# §5 — BENCH Coverage Proof
# ---------------------------------------------------------------------------
#
# Three per-axis coverage tables demonstrate that BENCH-01..06 cover
# the algo-0x07 + algo-0x08 family across the axes that matter: pinout-class,
# pulse-duration bucket, size bucket. Uncovered cells cross-reference §4
# DEFECT-COV-NN findings where the gap is structural; deliberate gaps
# are listed in the Known Gaps subsection.
#
# Forbids proposing alternative BENCH chip selections — BENCH-05 /
# BENCH-06 stay "candidate" until the selection is made.


def _findings_for_pinout(findings, ledger, pinout):
    """Return a list of DEFECT-COV-NN IDs whose signature touches `pinout`.

    HAZARD findings carry a list-of-pinout-strings as the first signature
    element; CORRECTNESS / VARIANCE carry the pinout string in position 1.
    We accept either shape and return IDs sorted ascending for stable output.
    """
    ids = []
    for f in findings:
        sig = f.get("signature")
        if sig is None:
            continue
        first = sig[0] if len(sig) > 0 else None
        # HAZARD shape: signature = (list_of_pinouts, algorithm, etype).
        if isinstance(first, (list, tuple)) and pinout in first:
            ids.append(ledger.get(f["hash"]))
            continue
        # CORRECTNESS / VARIANCE shape: (algo, pinout, size, ...).
        if len(sig) >= 2 and sig[1] == pinout:
            ids.append(ledger.get(f["hash"]))
    return sorted({i for i in ids if i is not None})


def pinout_coverage(rows, findings, ledger):
    """Build the pinout-class coverage table — one row per pinout class.

    Columns: Pinout, Row count, BENCH chip(s) exercising, Covered?, Note.
    Pinouts uncovered by the BENCH set get a `Covered? = N` + a cross-reference
    note pointing at the §4 finding IDs whose signature touches that pinout.
    """
    # Count rows per pinout.
    pinout_rows = defaultdict(list)
    for mfg, chip in rows:
        pinout_rows[chip["pinout"]].append((mfg, chip))

    table_rows = []
    for pinout in sorted(pinout_rows):
        members = pinout_rows[pinout]
        # BENCH chips that target this pinout class.
        covering = [b for b in BENCH_CHIP_MAP if b["pinout"] == pinout]
        bench_cell = (
            ", ".join(_bench_chip_label(b) for b in covering) if covering else "none"
        )

        any_pending = any(b.get("selection_pending") for b in covering)
        if covering:
            covered = "Y (pending selection)" if any_pending else "Y"
            note = "Selection lives in Phase 12 CONTEXT.md." if any_pending else ""
        else:
            covered = "N"
            cross_refs = _findings_for_pinout(findings, ledger, pinout)
            if cross_refs:
                note = "cross-ref: " + ", ".join(cross_refs)
            else:
                note = "uncovered — see Known Gaps"

        table_rows.append([pinout, len(members), bench_cell, covered, note])

    return table_rows


def _bench_pulse_bucket(rows, b):
    """Return the pulse-duration bucket label that BENCH chip `b` falls into,
    or None if no concrete DB row in `rows` matches any of `b["names"]`."""
    match = _bench_row_for_chip(rows, b["names"])
    if match is None:
        return None
    _mfg, chip = match
    us = chip["programming"]["pulse_duration_us"]
    return pulse_bucket(us)


def pulse_coverage(rows, findings, ledger, algo):
    """Build the per-algorithm pulse-duration bucket coverage table.

    Columns: Bucket, Row count, BENCH chip(s) in this bucket, Covered?, Note.
    """
    # Filter rows for the target algorithm.
    algo_rows = [(m, c) for m, c in rows if c["programming"]["algorithm"] == algo]
    bucket_rows = defaultdict(list)
    for mfg, chip in algo_rows:
        us = chip["programming"]["pulse_duration_us"]
        bucket_rows[pulse_bucket(us)].append((mfg, chip))

    # Map each BENCH chip on this algorithm to a bucket (or None if pending).
    bench_for_algo = [b for b in BENCH_CHIP_MAP if b["algorithm"] == algo]

    # For coverage decisions, we need to know which bucket each BENCH chip
    # lands in. For non-pending bench chips with a concrete DB row name, look
    # up the actual pulse_duration; for pending bench chips, attempt each
    # candidate name and report the union of buckets.
    def bench_buckets(b):
        """Return list of (bucket_label, candidate_pulse_us_or_None) tuples
        for every candidate name match against the DB."""
        out = []
        for name in b["names"]:
            for mfg, chip in algo_rows:
                pn_list = [p.strip() for p in chip["part_number"].split(",")]
                if name in pn_list:
                    us = chip["programming"]["pulse_duration_us"]
                    out.append(pulse_bucket(us))
                    break
        return out

    bench_bucket_map = {}
    for b in bench_for_algo:
        bench_bucket_map[b["id"]] = bench_buckets(b)

    table_rows = []
    # Iterate bucket order over the union of seen + pre-declared buckets.
    seen = sorted(bucket_rows, key=_pulse_bucket_sort_key)
    for bucket in seen:
        members = bucket_rows[bucket]
        chips_in_bucket = []
        for b in bench_for_algo:
            if bucket in bench_bucket_map[b["id"]]:
                chips_in_bucket.append(b)
        if chips_in_bucket:
            any_pending = any(b.get("selection_pending") for b in chips_in_bucket)
            bench_cell = ", ".join(_bench_chip_label(b) for b in chips_in_bucket)
            covered = "Y (pending selection)" if any_pending else "Y"
            note = "Selection lives in Phase 12 CONTEXT.md." if any_pending else ""
        else:
            bench_cell = "none"
            covered = "N"
            # Cross-reference §4 findings that actually have rows IN this
            # (algo, bucket) cell. For each CORRECTNESS finding (whose
            # signature is (algo, pinout, size, mfg, alias)), we check whether
            # the row corresponding to the signature's (mfg, alias) lives in
            # the same pulse bucket as the cell being annotated.
            in_bucket_part_numbers = {
                chip["part_number"].split(",")[0] for _mfg, chip in members
            }
            cross = []
            for f in findings:
                sig = f.get("signature")
                if sig is None or len(sig) < 5:
                    continue
                f_algo, _f_pinout, _f_size, _f_mfg, f_alias = sig[:5]
                if f_algo != algo:
                    continue
                if f_alias in in_bucket_part_numbers:
                    fid = ledger.get(f["hash"])
                    if fid is not None:
                        cross.append(fid)
            cross_ids = sorted(set(cross))
            note = (
                ("cross-ref: " + ", ".join(cross_ids))
                if cross_ids
                else "uncovered — see Known Gaps"
            )
        table_rows.append([bucket, len(members), bench_cell, covered, note])

    return table_rows


def size_coverage(rows, findings, ledger, algo):
    """Build the per-algorithm size-bucket coverage table.

    Columns: Size, Row count, BENCH chip(s) at this size, Covered?, Note.
    """
    algo_rows = [(m, c) for m, c in rows if c["programming"]["algorithm"] == algo]
    size_rows = defaultdict(list)
    for mfg, chip in algo_rows:
        size_rows[chip["electrical"]["size_bytes"]].append((mfg, chip))

    bench_for_algo = [b for b in BENCH_CHIP_MAP if b["algorithm"] == algo]

    table_rows = []
    for size_b in sorted(size_rows):
        members = size_rows[size_b]
        chips_at_size = [b for b in bench_for_algo if b["size_bytes"] == size_b]
        if chips_at_size:
            any_pending = any(b.get("selection_pending") for b in chips_at_size)
            bench_cell = ", ".join(_bench_chip_label(b) for b in chips_at_size)
            covered = "Y (pending selection)" if any_pending else "Y"
            note = "Selection lives in Phase 12 CONTEXT.md." if any_pending else ""
        else:
            bench_cell = "none"
            covered = "N"
            note = "uncovered — see Known Gaps"
        table_rows.append(
            [
                f"{size_b} / {size_label(size_b)}",
                len(members),
                bench_cell,
                covered,
                note,
            ]
        )

    return table_rows


def emit_bench_coverage(rows, findings, ledger):
    """Return §5 as a single markdown string.

    Three per-axis tables + Known Gaps subsection + milestone-claim closing
    prose. BENCH chip selection is observational only — swap proposals are
    forbidden; BENCH-05 / BENCH-06 stay "candidate".
    """
    in_scope_count = len(rows)
    parts = ["## §5: BENCH Coverage Proof", ""]
    parts.append(
        "Three per-axis coverage tables (D-09) demonstrating BENCH-01..06 "
        "represent the algo-0x07 + algo-0x08 family. Uncovered cells "
        "cross-reference §4 findings where the gap is structural (per D-10)."
    )
    parts.append("")
    parts.append(
        "BENCH-05 / BENCH-06 are candidate-pending — Phase 12 CONTEXT.md owns "
        "the selection decision (D-11). §5 records candidate names verbatim "
        "from REQUIREMENTS.md and is observational only."
    )
    parts.append("")

    # Pinout-class coverage
    parts.append("### Pinout-Class Coverage")
    parts.append("")
    parts.append(
        md_table(
            ["Pinout", "Row count", "BENCH chip(s)", "Covered?", "Note / Finding"],
            pinout_coverage(rows, findings, ledger),
        )
    )
    parts.append("")

    # Pulse-duration coverage — split per-algorithm
    parts.append("### Pulse-Duration Bucket Coverage (algo-0x07)")
    parts.append("")
    parts.append(
        md_table(
            ["Bucket", "Row count", "BENCH chip(s)", "Covered?", "Note / Finding"],
            pulse_coverage(rows, findings, ledger, 0x07),
        )
    )
    parts.append("")

    parts.append("### Pulse-Duration Bucket Coverage (algo-0x08)")
    parts.append("")
    parts.append(
        md_table(
            ["Bucket", "Row count", "BENCH chip(s)", "Covered?", "Note / Finding"],
            pulse_coverage(rows, findings, ledger, 0x08),
        )
    )
    parts.append("")

    # Size-bucket coverage — split per-algorithm
    parts.append("### Size Bucket Coverage (algo-0x07)")
    parts.append("")
    parts.append(
        md_table(
            [
                "Size (bytes / label)",
                "Row count",
                "BENCH chip(s)",
                "Covered?",
                "Note / Finding",
            ],
            size_coverage(rows, findings, ledger, 0x07),
        )
    )
    parts.append("")

    parts.append("### Size Bucket Coverage (algo-0x08)")
    parts.append("")
    parts.append(
        md_table(
            [
                "Size (bytes / label)",
                "Row count",
                "BENCH chip(s)",
                "Covered?",
                "Note / Finding",
            ],
            size_coverage(rows, findings, ledger, 0x08),
        )
    )
    parts.append("")

    # Known Gaps subsection
    parts.append("### Known Gaps")
    parts.append("")
    parts.append(
        "Deliberate gaps — uncovered cells whose absence from the BENCH set is "
        "an explicit v1.3 scope decision rather than a structural concern:"
    )
    parts.append("")
    parts.append(
        "- **DIP28_28C64 / DIP28_28C256 pinouts** — already raised as a "
        "HAZARD finding in §4 (DEFECT-COV-01); v1.3 explicitly does NOT "
        "bench these pinouts because the WARNING-5 override class needs "
        "extension before any 5V EEPROM-class chip can safely take 12V VPP."
    )
    parts.append(
        "- **2K / 8K / 16K size buckets on algo-0x07** — sub-density chips "
        "below the BENCH-05 32K low-end. v1.3 density-extreme strategy "
        "exercises the 32K → 512K span; sub-32K density is not bench-worthy."
    )
    parts.append(
        "- **64K / 1MB size buckets on algo-0x08** — single-chip / "
        "few-chip buckets at the algorithm's density extremes. The 64K "
        "bucket holds 1 row; the 1MB bucket holds 8 rows. Both are excluded "
        "per the density-extreme strategy (128K → 512K span suffices)."
    )
    parts.append(
        "- **100 ms-1 s pulse bucket on algo-0x07** — likely-mis-classified "
        "chips clustering at suspect pulse durations. Cross-referenced to "
        "§4 CORRECTNESS findings for v1.4 follow-up; not bench-worthy in v1.3."
    )
    parts.append("")

    # Milestone-claim closing prose (CONTEXT.md <specifics> "the matrix is the receipt")
    parts.append(
        f"These six BENCH chips (BENCH-01..06) represent N={in_scope_count} in-scope DB "
        "rows on axes pinout-class, pulse-duration bucket, and size bucket. "
        "Uncovered cells are documented above with cross-references to §4 "
        "defect candidates where the gap reflects a structural concern. "
        "After Phases 12+13 ship green on the six BENCH chips, the v1.3 "
        "milestone close (Phase 14 BENCH-RESULTS) can cite this matrix as "
        "proof that bench results generalize to the rest of the family."
    )

    return "\n".join(parts)


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
    # check. This keeps --check semantics dry-run.
    findings = (
        list(detect_hazard(rows))
        + list(detect_correctness(rows))
        + list(detect_variance(rows))
    )

    # Severity-tier counts for §1 (live detection only — DEFECT-COV-00 is
    # excluded as a static RESOLVED entry).
    severity_counts = Counter(f["severity"] for f in findings)

    # --check semantics: dry-run drift gate.
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

    # Assemble matrix body. §4 emit_defects mints DEFECT-COV-NN IDs into the
    # ledger via mint_or_reuse — must run BEFORE §5 so emit_bench_coverage
    # can cross-reference uncovered cells to live finding IDs by hash lookup.
    s1 = emit_summary(summary, severity_counts=severity_counts)
    s2 = emit_reconciliation(summary)
    s3 = emit_full_enumeration(rows)
    s4 = "\n".join(emit_defects(findings, ledger, next_n_holder)).rstrip("\n")
    s5 = emit_bench_coverage(rows, findings, ledger)
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
# All-algorithms wide-scan (the deferred follow-up from CONTEXT.md)
#
# Generates `.planning/v1.3-COVERAGE-MATRIX-ALL.md`: same audit treatment as
# the in-scope matrix but extended over every algorithm present in the DB.
# Reuses the algo-agnostic primitives (sort_key, _enum_row, md_table,
# pulse_bucket, size_label, finding_hash, detect_correctness,
# detect_variance) verbatim. Bench-coverage proof (§5) is intentionally omitted
# — only algos 0x07/0x08 have in-milestone bench chips; uncovered cells for
# the other 9 algorithms would be the entire matrix.
# ---------------------------------------------------------------------------

_FILE_HEADER_ALL = (
    "# v1.3 Coverage Matrix — All Algorithms (wide-scan extension)\n"
    "\n"
    "Sibling artifact to `v1.3-COVERAGE-MATRIX.md`. Same audit treatment\n"
    "(§1 summary + §2 enumeration + §3 inconsistencies per algorithm)\n"
    "extended over every algorithm in `chip_database.json`. BENCH coverage\n"
    "proof (§5 in the in-scope matrix) is intentionally omitted: only algos\n"
    "0x07/0x08 have in-milestone bench chips.\n"
    "\n"
    "Generated by `firestarter_app/tools/audit_coverage_matrix.py --all-algorithms`.\n"
    "Idempotent: same DB input → byte-identical output (D-02 + Pattern B).\n"
)


def _algo_label(algo):
    """Return canonical `0xNN` hex string for an algorithm integer."""
    return f"0x{algo:02X}"


def _group_rows_by_algo(rows):
    """Group rows into a dict[algo_int -> sorted list of (mfg, chip)].

    Sort within each algo by the canonical 5-tuple so output is
    deterministic. The outer dict is iterated via sorted(keys) at emit time.
    """
    groups = defaultdict(list)
    for mfg, chip in rows:
        algo = chip.get("programming", {}).get("algorithm", 0) or 0
        groups[algo].append((mfg, chip))
    for algo in groups:
        groups[algo] = sorted(groups[algo], key=lambda mc: sort_key(*mc))
    return groups


def _emit_global_overview_all(groups):
    """Return the §1 global overview markdown — total rows + per-algo histogram."""
    total = sum(len(v) for v in groups.values())
    parts = ["## §1: Global Overview", ""]
    parts.append(
        f"Total rows scanned: **{total}** across **{len(groups)}** algorithms. "
        "Per-algorithm breakdown sorted by row count (descending):"
    )
    parts.append("")
    rows_by_algo = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    parts.append(
        md_table(
            ["Algorithm", "Rows", "% of DB"],
            [
                [
                    _algo_label(algo),
                    str(len(members)),
                    f"{(100.0 * len(members) / total):.1f}%",
                ]
                for algo, members in rows_by_algo
            ],
        )
    )
    return "\n".join(parts)


def _emit_algo_summary(algo, members):
    """Return the §A.1 per-algorithm summary block (sub-histograms)."""
    parts = []
    pinout_counts = Counter(chip["pinout"] for _, chip in members)
    size_counts = Counter(chip["electrical"]["size_bytes"] for _, chip in members)
    pulse_counts = Counter()
    for _, chip in members:
        us = chip["programming"]["pulse_duration_us"]
        pulse_counts[pulse_bucket(us)] += 1
    etype_counts = Counter(
        chip["electrical"].get("type", "(missing)") for _, chip in members
    )
    cid_counts = Counter(
        "True" if chip["programming"].get("chip_id_check", False) else "False"
        for _, chip in members
    )

    parts.append(f"#### Pinout class — algo-{_algo_label(algo)} ({len(members)} chips)")
    parts.append("")
    parts.append(
        md_table(
            ["Pinout", "Count"],
            [[p, str(n)] for p, n in sorted(pinout_counts.items())],
        )
    )
    parts.append("")
    parts.append(f"#### Size bucket — algo-{_algo_label(algo)}")
    parts.append("")
    parts.append(
        md_table(
            ["Size (bytes / label)", "Count"],
            [
                [f"{s} / {size_label(s)}", str(n)]
                for s, n in sorted(size_counts.items())
            ],
        )
    )
    parts.append("")
    parts.append(f"#### Pulse-duration bucket — algo-{_algo_label(algo)}")
    parts.append("")
    pulse_rows = sorted(
        pulse_counts.items(),
        key=lambda kv: (_pulse_bucket_sort_key(kv[0]), kv[0]),
    )
    parts.append(
        md_table(
            ["Bucket", "Count"],
            [[bucket, str(n)] for bucket, n in pulse_rows],
        )
    )
    parts.append("")
    parts.append(f"#### Electrical type — algo-{_algo_label(algo)}")
    parts.append("")
    parts.append(
        md_table(
            ["Type", "Count"],
            [[t, str(n)] for t, n in sorted(etype_counts.items())],
        )
    )
    parts.append("")
    parts.append(f"#### chip_id_check distribution — algo-{_algo_label(algo)}")
    parts.append("")
    parts.append(
        md_table(
            ["chip_id_check", "Count"],
            [[v, str(n)] for v, n in sorted(cid_counts.items())],
        )
    )
    return "\n".join(parts)


def _emit_algo_enumeration(algo, members):
    """Return the §A.2 per-algorithm full enumeration table."""
    parts = [
        f"#### Full Enumeration — algo-{_algo_label(algo)} ({len(members)} rows)",
        "",
        "Sort: (algorithm, pinout, size_bytes, manufacturer, first_alias) per D-06.",
        "",
        md_table(_ENUM_HEADERS, [_enum_row(mfg, chip) for mfg, chip in members]),
    ]
    return "\n".join(parts)


def _members_with_parseable_pulse(members):
    """Subset of `members` whose `pulse_duration_us` is a non-zero integer.

    Many non-0x07/0x08 algos use `pulse_duration_us: 0` (EEPROMs that
    self-time internally — 355 rows across 8 algos in the current DB), where
    `0` means algorithm-controlled rather than unparseable — true by
    construction because `interpret_timing` (build_db.py) makes a decode
    fault fatal. `detect_correctness` compares pulse magnitudes and so
    cannot meaningfully operate on those algorithm-controlled rows.
    """
    return [
        (mfg, chip)
        for mfg, chip in members
        if chip["programming"]["pulse_duration_us"] != 0
    ]


def _detect_correctness_safe(members):
    """detect_correctness restricted to rows with parseable pulse_duration."""
    return detect_correctness(_members_with_parseable_pulse(members))


def _emit_algo_defects(algo, members, ledger, next_n_holder):
    """Return the §A.3 per-algorithm defect block.

    Reuses `detect_correctness` + `detect_variance` (both algo-agnostic —
    they group by `(algorithm, pinout, size_bytes, ...)` internally so passing
    just one algo's rows yields findings within that algo). `detect_hazard`
    is intentionally NOT called here — it is currently hardcoded to the v1.3
    in-scope HAZARD cluster and would emit nothing for non-0x07 algos.

    Findings are minted into the passed-in `ledger` via `mint_or_reuse` so
    DEFECT-COV-NN IDs persist across runs of the all-algorithms generator.
    """
    findings = list(_detect_correctness_safe(members)) + list(detect_variance(members))
    if not findings:
        return f"#### Inconsistencies — algo-{_algo_label(algo)}\n\n_No CORRECTNESS or VARIANCE findings detected._"

    parts = [f"#### Inconsistencies — algo-{_algo_label(algo)}", ""]
    by_tier = defaultdict(list)
    for f in findings:
        by_tier[f["severity"]].append(f)

    for tier in ("CORRECTNESS", "VARIANCE"):
        tier_findings = sorted(by_tier.get(tier, []), key=lambda f: f["hash"])
        if not tier_findings:
            continue
        parts.append(f"##### {tier} ({len(tier_findings)} finding(s))")
        parts.append("")
        for f in tier_findings:
            defect_id = mint_or_reuse(
                ledger,
                f["severity"],
                f["axis"],
                f["signature"],
                next_n_holder,
            )
            parts.append(f"###### {defect_id} — {f['title']}")
            parts.append("")
            parts.append(
                md_table(
                    ["Field", "Value"],
                    [
                        ["Severity", f["severity"]],
                        ["Axis", f["axis"]],
                        ["Affected chips", str(f["affected_chips"])],
                        ["Root cause hypothesis", f["root_cause_hypothesis"]],
                        ["Suggested fix venue", f["suggested_fix_venue"]],
                        ["Examples", "; ".join(f["examples"])],
                    ],
                )
            )
            parts.append("")
    return "\n".join(parts).rstrip("\n")


def generate_matrix_all(output, ledger_path, check=False):
    """Generate the all-algorithms coverage matrix + ledger.

    Returns: 0 on clean generate (or `--check` with no new findings);
             1 on DB parse error or `--check` with new findings.

    Uses a dedicated ledger (`v1.3-defect-coverage-ids-all.json` by default)
    so cross-family findings don't contaminate the v1.3 in-scope ledger.
    """
    output = str(output)
    ledger_path = str(ledger_path)

    try:
        with open(DB_FILE, encoding="utf-8") as f:
            db_raw = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: chip_database.json not found at {DB_FILE}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"ERROR: chip_database.json parse failed: {exc}", file=sys.stderr)
        return 1

    rows = list(iter_all_rows(db_raw))
    groups = _group_rows_by_algo(rows)

    try:
        ledger = load_ledger(ledger_path)
    except json.JSONDecodeError as exc:
        print(f"ERROR: ledger {ledger_path} parse failed: {exc}", file=sys.stderr)
        return 1

    # Detection pass per-algo for the dry-run drift gate.
    all_findings = []
    for algo in sorted(groups):
        members = groups[algo]
        all_findings.extend(_detect_correctness_safe(members))
        all_findings.extend(detect_variance(members))

    if check:
        for f in all_findings:
            if f["hash"] not in ledger:
                return 1
        return 0

    # Normal generate path: mint IDs for any new findings.
    existing_ns = []
    for v in ledger.values():
        try:
            existing_ns.append(int(v.split("-")[-1]))
        except (ValueError, IndexError):
            continue
    next_n_holder = [max([0] + existing_ns) + 1]

    body_parts = [
        _FILE_HEADER_ALL.rstrip("\n"),
        "",
        "---",
        "",
        _emit_global_overview_all(groups),
    ]

    for algo in sorted(groups):
        members = groups[algo]
        body_parts.append("")
        body_parts.append("---")
        body_parts.append("")
        body_parts.append(
            f"## §A-{_algo_label(algo)}: algo-{_algo_label(algo)} ({len(members)} chips)"
        )
        body_parts.append("")
        body_parts.append(_emit_algo_summary(algo, members))
        body_parts.append("")
        body_parts.append(_emit_algo_enumeration(algo, members))
        body_parts.append("")
        body_parts.append(_emit_algo_defects(algo, members, ledger, next_n_holder))

    content = "\n".join(body_parts) + "\n"
    Path(output).write_text(content, encoding="utf-8", newline="\n")
    save_ledger(ledger, ledger_path)

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate the v1.3 coverage matrix from chip_database.json "
            "(algo-0x07 + algo-0x08 in-scope rows by default). Idempotent "
            "codegen — byte-identical output across runs."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output path for the coverage matrix markdown. Default: "
            "<repo-root>/.planning/v1.3-COVERAGE-MATRIX.md "
            "(or v1.3-COVERAGE-MATRIX-ALL.md with --all-algorithms)."
        ),
    )
    parser.add_argument(
        "--ledger",
        default=None,
        help=(
            "Path to the stable defect-ID ledger JSON. Default: "
            "<repo-root>/.planning/v1.3-defect-coverage-ids.json "
            "(or v1.3-defect-coverage-ids-all.json with --all-algorithms)."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Exit 1 if generating would mint new DEFECT-COV-NN IDs; "
            "exit 0 otherwise. Dry-run drift gate (D-03)."
        ),
    )
    parser.add_argument(
        "--all-algorithms",
        action="store_true",
        help=(
            "Wide-scan mode: audit every algorithm in chip_database.json "
            "(not just algo-0x07/0x08). Emits §1 global overview + per-algo "
            "§A.1 summary + §A.2 enumeration + §A.3 defects. Skips §5 BENCH "
            "coverage (out of scope for non-0x07/0x08 families)."
        ),
    )
    args = parser.parse_args()

    if args.all_algorithms:
        output = args.output or DEFAULT_OUTPUT_ALL
        ledger = args.ledger or DEFAULT_LEDGER_ALL
        rc = generate_matrix_all(output, ledger, check=args.check)
    else:
        output = args.output or DEFAULT_OUTPUT
        ledger = args.ledger or DEFAULT_LEDGER
        rc = generate_matrix(output, ledger, check=args.check)
    sys.exit(rc)


if __name__ == "__main__":
    main()
