#!/usr/bin/env python3
"""AST-based string-literal claim scanner over `firestarter/diagnostic_report.py`
(CLOSE-03, v1.30 Phase 137 plan 137-02).

Purpose: `diagnostic_report.py`'s string literals are the `dev test`
diagnostic report text that reaches a stranger's terminal on every single
run -- PITFALLS.md P-12 names this as "the surface no existing gate scans
today". The meta-repo claim gate authored in this same phase's plan 137-01
(`.planning/phases/137-close-honesty-ledger-claim-gate-gh12-followup/check_permitted_claims.py`)
only ever scans four named `.planning/` markdown artifacts; it has never
opened this file, and it never will (it is hosted in a directory the meta
repo's own CI never runs pytest against at all). This checker closes that
gap on the host side, where `firestarter_app`'s existing `ci` job already
runs `pytest tests/` on every PR.

Vocabulary: the 14-entry `FORBIDDEN_PATTERNS` table (and `REQUIRED_CAVEAT_PATTERN`,
carried for vocabulary parity -- see the asymmetry note below) is forked
VERBATIM (same 14 labels, same regexes, all `re.IGNORECASE`) from plan
137-01's meta-repo copy
(`.planning/phases/137-close-honesty-ledger-claim-gate-gh12-followup/check_permitted_claims.py`)
so the host-side and meta-repo halves of CLOSE-03/CLOSE-04's honesty-ledger
discipline never drift into two different vocabularies scanning for two
different things.

**Asymmetry (load-bearing, by design -- do not "fix" this):** unlike the
meta-repo gate, this checker never requires the presence of the required
silicon caveat sentence (`REQUIRED_CAVEAT_PATTERN` / `no AT28C silicon was
tested`). `diagnostic_report.py` is a rendering module -- a `DiagnosticReport`
data-assembly-and-render class, not a claims essay -- and its string literals
(field labels, table headers, the `NOT_MEASURED` sentinel, etc.) have no
natural home for that caveat sentence. This checker only ever scans for the
ABSENCE of a forbidden phrase; it never scans for the PRESENCE of the caveat
prose. `REQUIRED_CAVEAT_PATTERN` is carried here purely so the vocabulary
constant set matches the donor byte-for-byte; it is intentionally unused by
`main()`'s control flow.

**Scope note (also load-bearing):** this checker scans EXACTLY
`diagnostic_report.py`, matching REQUIREMENTS.md's own CLOSE-03 wording.
`firestarter/cli_handlers.py` used to carry two named SDP recovery-string
constants (`_SDP_RECOVERY_LOUD` / `_SDP_RECOVERY_NEUTRAL`) with their own
committed, scoped wording gate (`tests/test_sdp_recovery_wording.py`, v1.30
Phase 134 plan 134-09, LEG-14) -- quick task 260821-spg deleted the console
echo those constants fed, the constants themselves, and that gate along with
them, so there is no longer a second scanned surface for this checker to
defer to. The scan target here remains exactly `diagnostic_report.py`; this
checker's scope was never widened to cover `cli_handlers.py` and still isn't.

**Explicit non-claim (load-bearing):** this checker catches literal forbidden
phrases baked into `diagnostic_report.py`'s source AS WRITTEN, today. It
CANNOT catch a future f-string that assembles a forbidden phrase at runtime
from data not present in the source (e.g. concatenating fragments held in
separate variables). It is not a substitute for the meta-repo claim gate
(plan 137-01, which covers the four closing-artifact documents) or for
CLOSE-06's blocking operator wording review (plan 137-05) -- both cover
different artifacts and neither is replaced by a green run of this gate.

Exit codes:
  0 -- the scan target exists, parses as valid Python, and its string
       literals contain zero forbidden-phrase matches (`PASS:` line printed,
       naming the target and the literal count scanned).
  1 -- the scan target is missing from disk (fail-closed -- this gate must
       never vacuously pass with a target silently absent), OR the target
       fails to parse as Python (fail-closed -- never silently skipped), OR
       at least one forbidden-phrase match was found (a bucketed `FAIL:`
       summary, capped at 20 entries, is printed).

Wired via `pytest tests/` (`tests/test_check_diagnostic_report_claims.py`),
NOT a dedicated `.github/workflows/ci.yml` step -- mirrors
`tools/check_no_community_support_status_write.py`'s own convention exactly:
CI's existing `pytest tests/ --cov-fail-under=70` step picks up the paired
test automatically; adding a YAML step would double-run this gate.
"""

import ast
import os
import re
import sys

# ---------------------------------------------------------------------------
# Module-top path constant (mirrors check_no_community_support_status_write.py's
# idiom exactly).
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(__file__)

_DEFAULT_DIAGREPORT_SRC = os.path.join(
    _HERE, "..", "firestarter", "diagnostic_report.py"
)

# Env-override seam (mirrors FIRESTARTER_DISP01_REPORT): lets the paired
# pytest point this checker at a committed planted-violation fixture without
# ever editing the real, clean diagnostic_report.py source.
FIRESTARTER_DIAGREPORT_SRC = os.environ.get(
    "FIRESTARTER_DIAGREPORT_SRC", _DEFAULT_DIAGREPORT_SRC
)

# ---------------------------------------------------------------------------
# Vocabulary -- forked VERBATIM (same 14 labels, same regexes) from plan
# 137-01's meta-repo copy:
# .planning/phases/137-close-honesty-ledger-claim-gate-gh12-followup/check_permitted_claims.py
# Do not re-derive a second, possibly-divergent list here.
# ---------------------------------------------------------------------------

FORBIDDEN_PATTERNS = [
    # -- forked verbatim from Phase 122's check_permitted_claims.py (via plan 137-01) --
    ("verified-fixed", re.compile(r"verified\s+fixed", re.IGNORECASE)),
    ("confirmed-working", re.compile(r"confirmed\s+working", re.IGNORECASE)),
    ("silicon-verified", re.compile(r"silicon[-\s]verified", re.IGNORECASE)),
    (
        "verified-on-silicon",
        re.compile(
            r"verified\s+(?:on|against)\s+(?:real\s+)?(?:at28c\w*|silicon)",
            re.IGNORECASE,
        ),
    ),
    (
        "works-on-silicon",
        re.compile(r"works?\s+on\s+(?:\w+\s+){0,2}(?:at28c\w*|silicon)", re.IGNORECASE),
    ),
    ("now-works", re.compile(r"now\s+works?\b", re.IGNORECASE)),
    ("should-now-work", re.compile(r"should\s+now\s+work", re.IGNORECASE)),
    (
        "proven-on-silicon",
        re.compile(r"proven\s+on\s+(?:\w+\s+){0,2}(?:at28c\w*|silicon)", re.IGNORECASE),
    ),
    # -- v1.30-specific additions, PITFALLS.md P-11 point 2 (via plan 137-01) --
    (
        "lock-inhibited-the-write",
        re.compile(r"lock\s+inhibited\s+the\s+write", re.IGNORECASE),
    ),
    (
        # Do not confuse with the literal rendered enum values HELD/NOT-HELD
        # from chip_test.sdp_hold_state() -- those are permitted data, not a
        # prose causal claim. This pattern requires the words "the", "lock",
        # "held" in that order.
        "lock-held-unqualified",
        re.compile(r"\bthe\s+lock\s+held\b", re.IGNORECASE),
    ),
    ("proven-behaviour", re.compile(r"proven\s+behaviou?r", re.IGNORECASE)),
    (
        "behaviourally-verified",
        re.compile(r"behaviou?rally\s+verified", re.IGNORECASE),
    ),
    ("now-proven", re.compile(r"now\s+proven\b", re.IGNORECASE)),
    (
        "dev-test-proves-unqualified",
        re.compile(r"dev\s+test\s+proves\b", re.IGNORECASE),
    ),
]

# Carried for vocabulary parity with the donor only -- see the module
# docstring's "Asymmetry" note. Deliberately UNUSED by main()'s control flow:
# this checker never requires the caveat sentence's presence.
REQUIRED_CAVEAT_PATTERN = re.compile(
    r"no\s+AT28C\s+silicon\s+was\s+tested", re.IGNORECASE
)


class _StringLiteralVisitor(ast.NodeVisitor):
    """Collect every `ast.Constant` string literal in a parsed module, along
    with its `.lineno`.

    Python 3.8+ folds all literal kinds (str, bytes, int, ...) into
    `ast.Constant` -- the deprecated `ast.Str` node no longer appears in
    output from `ast.parse`. Only `str`-valued constants are collected;
    `isinstance(node.value, str)` deliberately excludes bytes literals
    (`isinstance(b"x", str)` is `False`) and every non-string constant.
    """

    def __init__(self) -> None:
        self.literals: list[tuple[str, int]] = []

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self.literals.append((node.value, node.lineno))
        self.generic_visit(node)


def _extract_string_literals(source: str, filename: str) -> list[tuple[str, int]]:
    """Parse `source` and return every string literal as `(text, lineno)`.

    Raises `SyntaxError` on unparsable source -- `main()` catches this and
    fails closed rather than silently skipping the file.
    """
    tree = ast.parse(source, filename=filename)
    visitor = _StringLiteralVisitor()
    visitor.visit(tree)
    return visitor.literals


def _scan_literals(literals: list[tuple[str, int]]) -> list[tuple[str, str, int]]:
    """Scan collected string literals for `FORBIDDEN_PATTERNS` matches.

    Each literal's text is sanitized (embedded newlines replaced with a
    single space) before being placed on its own line in one concatenated
    scan buffer, so a strict 1:1 correspondence holds between a buffer line
    index and the ORIGINAL source `lineno` of the literal that produced it --
    this is what keeps the FAIL summary's line numbers meaningful even
    though every literal is scanned together in one pass (mirrors
    `check_permitted_claims.py`'s `scan_text` mechanics, minus the proximity
    window: every string literal in this file is already
    SDP/diagnostic-report context by construction, so windowing would add
    complexity with no signal).

    Returns a list of `(label, matched_substring, original_lineno)` tuples,
    one entry per match.
    """
    sanitized = [text.replace("\n", " ").replace("\r", " ") for text, _ in literals]
    linenos = [lineno for _, lineno in literals]
    buffer = "\n".join(sanitized)
    buffer_lines = buffer.splitlines()

    hits: list[tuple[str, str, int]] = []
    for label, pattern in FORBIDDEN_PATTERNS:
        for i, line in enumerate(buffer_lines):
            for m in pattern.finditer(line):
                hits.append((label, m.group(0), linenos[i]))
    return hits


def _print_bucket(label: str, violations: list[str]) -> None:
    print(f"FAIL: {len(violations)} {label}:")
    for v in violations[:20]:
        print(f"  {v}")
    if len(violations) > 20:
        print(f"  ... and {len(violations) - 20} more")


def main(argv: list[str]) -> int:
    """Entry point: resolve the scan target, scan it, exit non-zero on any
    forbidden-phrase match or fail-closed condition.

    Resolution: `argv[0]` if given, else `FIRESTARTER_DIAGREPORT_SRC`
    (default: the real `firestarter/diagnostic_report.py`).
    """
    target = argv[0] if argv else FIRESTARTER_DIAGREPORT_SRC

    if not os.path.isfile(target):
        print(f"FAIL: scan target not found on disk -- {target}")
        return 1

    with open(target, encoding="utf-8") as f:
        source = f.read()

    try:
        literals = _extract_string_literals(source, target)
    except SyntaxError:
        print(f"FAIL: could not parse {target} as Python")
        return 1

    hits = _scan_literals(literals)

    if hits:
        violations = [
            f"{target}:{lineno}: forbidden phrase match [{label}]: {substr!r}"
            for label, substr, lineno in hits
        ]
        _print_bucket("forbidden phrase match(es)", violations)
        return 1

    print(
        f"PASS: scanned {target}, {len(literals)} string literals checked, "
        "zero forbidden matches"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
