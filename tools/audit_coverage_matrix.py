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
import hashlib  # noqa: F401 — used by later-wave ledger minting
import json
import os
import sys
from collections import Counter, defaultdict  # noqa: F401 — defaultdict used by later waves
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

def emit_summary(summary):
    """Return the §1 markdown block as a single string."""
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

    # h. Severity-tier counts placeholder (D-12 — populated in Wave 3)
    parts.append("### Severity-tier finding counts (D-12)")
    parts.append("")
    parts.append("- HAZARD: TBD")
    parts.append("- CORRECTNESS: TBD")
    parts.append("- VARIANCE: TBD")
    parts.append("")
    parts.append("_Populated in Wave 3 (Plan 11-04 — defect-findings emit)._")

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
# §3 / §4 / §5 — Placeholder headers (populated by Waves 2-4)
# ---------------------------------------------------------------------------

def emit_placeholder_sections():
    """Return the §3 + §4 + §5 stub blocks joined with `---` separators."""
    s3 = (
        "## §3: Full Enumeration\n"
        "\n"
        "_Populated in Wave 2 (Plan 11-03 — enumeration + idempotence)._"
    )
    s4 = (
        "## §4: DB Inconsistencies / Defect Candidates\n"
        "\n"
        "_Populated in Wave 3 (Plan 11-04 — defect findings + ledger)._"
    )
    s5 = (
        "## §5: BENCH Coverage Proof\n"
        "\n"
        "_Populated in Wave 4 (Plan 11-05 — bench-coverage proof)._"
    )
    return s3, s4, s5


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

    # Stub ledger handling (Pitfall 4 cold-start). Wave 3 wires minting; for
    # Wave 1 we just persist the loaded-or-empty ledger back unchanged.
    try:
        ledger = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        ledger = {}
    except json.JSONDecodeError as exc:
        print(f"ERROR: ledger {ledger_path} parse failed: {exc}", file=sys.stderr)
        return 1

    # Assemble matrix body.
    s1 = emit_summary(summary)
    s2 = emit_reconciliation(summary)
    s3, s4, s5 = emit_placeholder_sections()
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

    # Ledger: sort_keys=True + LF newline + trailing \n (Pattern B invariants 1+3+4).
    ledger_blob = json.dumps(ledger, indent=2, sort_keys=True) + "\n"
    Path(ledger_path).write_text(ledger_blob, encoding="utf-8", newline="\n")

    # --check semantics: Wave 1 has no minting, so no new findings are
    # possible. Wave 3 will extend this to compare in-memory mint set vs
    # on-disk ledger and return 1 if a new ID would be added.
    if check:
        # TODO: Wave 3 — return 1 if mint would add new IDs vs current ledger.
        return 0

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
