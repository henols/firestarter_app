"""
AST-based no-auto-graduate lock gate for the report/parse path (DISP-01,
Phase 114 Plan 03).

Scans `firestarter/diagnostic_report.py` (the Phase-110/112/114-01
`DiagnosticReport`/`DbDiff` model) and `tools/parse_devtest_issue.py` (the
Phase-114-02 INBOX-01 triage parser) and DENIES any WRITE of a chip's
`support_status` -- the invariant this gate machine-enforces (D-01/D-02):
graduation is flag-only and human-gated, so NO code path that parses a
community `dev test` report may ever write `support_status`. Per D-02, the
report/parse path only ever READS `support_status` (via
`EpromDatabase.get_eprom_config` / a plain dict `.get(...)`); the sole
allowed write locus stays the human-authored `tools/build_db.py:714`
(`"support_status": _support_status,`), which this checker deliberately
does NOT scan.

Deny rule: an `ast.Assign` / `ast.AnnAssign` / `ast.AugAssign` whose target
is `support_status` -- i.e. `x.support_status = ...`
(`ast.Attribute.attr == "support_status"`) OR `x["support_status"] = ...`
(`ast.Subscript` with an `ast.Constant` slice exactly equal to the string
`"support_status"`). A READ (`.get("support_status")`, `x["support_status"]`
in a `Load` context, an attribute access, a dict-literal key) is never
flagged -- only an assignment TARGET matches. The near-name identifier
`current_support_status` (the `DbDiff` dataclass field / dict key used
throughout `diagnostic_report.py` and `parse_devtest_issue.py`) does NOT
match: the comparison is an exact-string match against `"support_status"`,
so `current_support_status` -- 4 characters longer -- is never mistaken for
it (Pitfall 1 / T-114-08). `firestarter/eprom_info.py:150`'s
`combined_data["support_status"] = ss` (a display-dict copy of a value
already READ from the DB) is likewise never flagged, because
`eprom_info.py` is deliberately NOT one of this checker's scan targets --
scoping the scan to the report/parse path only, rather than trying to
whitelist by value, is what the RESEARCH's Pitfall 1 write-up recommends.

Fail-closed (T-114-07, the v1.12 hollow-GATE-03 lesson): BOTH scan targets
are mandatory here (unlike SAFE-03's optional third-leg tolerance) -- if
EITHER is missing from disk, the gate fails closed rather than silently
scanning only the file that happens to exist. A checker that quietly
tolerates one missing target is exactly as hollow as scanning nothing.

This is a genuinely-populated AST walk (`ast.parse` + a fresh
`ast.NodeVisitor`), NOT a hollow declared-empty detector. The paired pytest
(`tests/test_check_no_community_support_status_write.py`) proves this
checker actually flips to non-zero on a planted violation, injected via the
`FIRESTARTER_DISP01_REPORT` / `FIRESTARTER_DISP01_PARSER` env-overrides
below (mirrors `tools/check_devtest_orchestrator.py`'s
`FIRESTARTER_DEVTEST_SRC` seam) -- D-05's anti-hollow contract.

Wired via `pytest tests/`, NOT a dedicated `.github/workflows/ci.yml` step
(mirrors the SAFE-03 convention exactly) -- CI's existing
`pytest tests/ --cov-fail-under=70` step picks up the paired test
automatically; adding a YAML step would double-run the gate.

Exit codes:
  0 -- both scan targets exist and contain zero `support_status` writes
       (PASS: line printed, naming both scanned files).
  1 -- a scan target is missing from disk (fail-closed), OR a target
       resolves into the firmware sub-repo (host-only violation), OR at
       least one `support_status` write was found (FAIL: summary printed).
"""

import ast
import os
import sys

# Module-top path constants (mirrors tools/check_devtest_orchestrator.py:80-100's
# env-overridable path-constant idiom).
_HERE = os.path.dirname(__file__)

_DEFAULT_DISP01_REPORT = os.path.join(
    _HERE, "..", "firestarter", "diagnostic_report.py"
)

# Env-override seam (mirrors check_devtest_orchestrator.py's
# FIRESTARTER_DEVTEST_SRC): lets the paired pytest point this checker at a
# deliberately-violating fixture file without editing the real, clean
# diagnostic_report.py source (D-05).
FIRESTARTER_DISP01_REPORT = os.environ.get(
    "FIRESTARTER_DISP01_REPORT", _DEFAULT_DISP01_REPORT
)

_DEFAULT_DISP01_PARSER = os.path.join(_HERE, "parse_devtest_issue.py")

# Env-override seam (mirrors FIRESTARTER_DISP01_REPORT above): lets the
# paired pytest point this checker at a deliberately-violating
# parser-shaped fixture file without editing the real, clean
# parse_devtest_issue.py (anti-hollow proof for the parser leg
# specifically).
FIRESTARTER_DISP01_PARSER = os.environ.get(
    "FIRESTARTER_DISP01_PARSER", _DEFAULT_DISP01_PARSER
)

# ---------------------------------------------------------------------------
# Deny vocabulary (D-02/D-05): the single write-target identifier.
# ---------------------------------------------------------------------------

_SUPPORT_STATUS_KEY = "support_status"


class _SupportStatusWriteVisitor(ast.NodeVisitor):
    """Walk a report/parse-shaped AST, collecting DISP-01 write-target hits.

    Populates a single violation list during one tree walk: any
    `ast.Assign` / `ast.AnnAssign` / `ast.AugAssign` whose target is an
    `ast.Attribute` with `attr == "support_status"`, OR an `ast.Subscript`
    whose slice is an `ast.Constant` exactly equal to the string
    `"support_status"`. A READ (an `Attribute`/`Subscript` in `Load`
    context, a `.get("support_status")` call, a dict-literal key) is never
    visited as an assignment target, so it can never match here.

    Each violation is recorded as a human-readable `"file:line: ..."`
    string so `main()` can print an actionable FAIL: summary.
    """

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.violations: list[str] = []

    def _is_support_status_target(self, target: ast.expr) -> bool:
        if isinstance(target, ast.Attribute) and target.attr == _SUPPORT_STATUS_KEY:
            return True
        if (
            isinstance(target, ast.Subscript)
            and isinstance(target.slice, ast.Constant)
            and target.slice.value == _SUPPORT_STATUS_KEY
        ):
            return True
        return False

    def _record(self, node: ast.AST, kind: str) -> None:
        lineno = getattr(node, "lineno", "?")
        self.violations.append(
            f"{self.filename}:{lineno}: {kind} writes support_status in the "
            "report/parse path (DISP-01: only tools/build_db.py may write it)"
        )

    def visit_Assign(self, node: ast.Assign) -> None:
        for t in node.targets:
            if self._is_support_status_target(t):
                self._record(node, "assignment")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self._is_support_status_target(node.target):
            self._record(node, "annotated assignment")
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if self._is_support_status_target(node.target):
            self._record(node, "augmented assignment")
        self.generic_visit(node)


def _scan_file(path: str) -> _SupportStatusWriteVisitor | None:
    """Parse and walk `path`; return None if the file does not exist.

    Missing-file tolerance exists only so `main()`'s explicit
    missing-target fail-closed check (below) is the single source of
    truth for "target absent" handling, rather than duplicating that
    branch here.
    """
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=path)
    visitor = _SupportStatusWriteVisitor(path)
    visitor.visit(tree)
    return visitor


def _assert_host_only(path: str) -> str | None:
    """Assert `path` does not resolve into the firmware sub-repo (D-02).

    Returns an error string if the resolved path falls inside the sibling
    `firestarter/` firmware submodule (a peer of `firestarter_app/` in the
    meta-repo layout -- see /workspaces/CLAUDE.md), else None. Mirrors
    `tools/check_devtest_orchestrator.py::_assert_host_only` exactly:
    deliberately permissive otherwise (a pytest `tmp_path` fixture used to
    inject a negative test via the env-override seams above is NOT a
    firmware path and must not be rejected here).
    """
    meta_root = os.path.abspath(os.path.join(_HERE, "..", ".."))
    firmware_root = os.path.join(meta_root, "firestarter")
    resolved = os.path.abspath(path)
    if resolved == firmware_root or resolved.startswith(firmware_root + os.sep):
        return f"target path {resolved} resolves INTO the firmware sub-repo ({firmware_root})"
    return None


def _print_bucket(label: str, violations: list[str]) -> None:
    print(f"FAIL: {len(violations)} {label}:")
    for v in violations[:20]:
        print(f"  {v}")
    if len(violations) > 20:
        print(f"  ... and {len(violations) - 20} more")


def main() -> None:
    """Entry point: scan the report/parse source(s), exit non-zero on any hit.

    Scans `FIRESTARTER_DISP01_REPORT` (default: the real
    `firestarter/diagnostic_report.py`) and `FIRESTARTER_DISP01_PARSER`
    (default: the real `tools/parse_devtest_issue.py`) IN FULL. Unlike
    `check_devtest_orchestrator.py`'s optional third leg, BOTH targets here
    are mandatory -- neither is ever legitimately absent in production, so
    a missing target fails closed immediately (T-114-07) rather than
    falling through to a "scanned nothing" check after the fact.
    """
    targets = [FIRESTARTER_DISP01_REPORT, FIRESTARTER_DISP01_PARSER]

    missing_targets = [t for t in targets if not os.path.isfile(t)]
    if missing_targets:
        print(
            "FAIL: scan target(s) not found on disk -- the gate cannot "
            f"vacuously pass with a target silently skipped: {missing_targets}"
        )
        sys.exit(1)

    host_only_errors: list[str] = []
    for t in targets:
        err = _assert_host_only(t)
        if err:
            host_only_errors.append(err)

    write_violations: list[str] = []
    scanned: list[str] = []
    for t in targets:
        visitor = _scan_file(t)
        if visitor is not None:
            scanned.append(t)
            write_violations.extend(visitor.violations)

    if not scanned:
        # Defense in depth: the missing_targets guard above should already
        # have caught this, but a scanned-empty state must never vacuously
        # pass regardless of how it was reached (D-05 anti-hollow contract).
        print(
            "FAIL: no report/parse source files found to scan "
            f"(checked: {targets}) -- the gate cannot vacuously pass with "
            "nothing scanned"
        )
        sys.exit(1)

    if host_only_errors or write_violations:
        if host_only_errors:
            _print_bucket("host-only framing violation(s)", host_only_errors)
        if write_violations:
            _print_bucket(
                "support_status write(s) in the report/parse path", write_violations
            )
        sys.exit(1)

    print(
        f"PASS: scanned {', '.join(os.path.relpath(s, _HERE) for s in scanned)}; "
        "0 support_status writes (sole write locus stays tools/build_db.py)"
    )


if __name__ == "__main__":
    main()
