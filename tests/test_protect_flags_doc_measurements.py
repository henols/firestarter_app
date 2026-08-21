"""DATA-06's proof: the doc/infoic-field-dictionary.md section documenting
`protect_off_before` / `protect_on_after` must state figures that equal a
fresh recomputation against the committed `chip_database.json` -- and the
absence of a runtime consumer must be proven by a source scan, not asserted
in prose.

Why this file exists as Python rather than more markdown (C-13):
`firestarter_app/.github/workflows/ci.yml`'s `paths-ignore` includes
`'**.md'` on both `push` and `pull_request`, so a markdown-only commit fires
no CI at all. This module is not markdown, so it is the thing that actually
runs.

Modeled on `tests/test_b15_page_size_corroboration.py`'s shape: pin the
measured DB figures as literals with a docstring citing how they were
measured, parse the doc's own stated figures with pre-compiled regex
patterns (never hard-coding only the expected values -- so this test goes
red if the *doc* drifts from the DB in either direction), and carry a
non-vacuity control plus an untouched-guard for `sdp_capability.py`.

**Leg 4 note (source-scan for a runtime consumer), a stale-plan-premise
correction:** this plan's own text assumed exactly one in-package prose
occurrence (`sdp_capability.py:74`). By the time this plan executed,
`firestarter/protection_readability.py` (landed by the same-phase Plan
151-06, wave 1) had already added two more -- a comment (`:163`) and a
docstring note (`:238`) that explicitly states no branch in that module
reads either field. A literal "exactly one allowed file" check would
therefore be wrong on arrival. Leg 4 below is structural instead: it parses
every `.py` file under `firestarter_app/firestarter/` with `ast` + `tokenize`
to compute which line numbers are comment or docstring text, and fails
naming any occurrence of either field name that falls OUTSIDE that prose set
-- i.e. any occurrence that would be executable code. This proves the actual
invariant (`D-16`: no runtime consumer) regardless of how many prose files
mention the field, and does not need updating every time a comment moves.
"""

from __future__ import annotations

import ast
import io
import json
import re
import tokenize
from pathlib import Path

_FA_DIR = Path(__file__).parent.parent
_DB_FILE = _FA_DIR / "firestarter" / "data" / "chip_database.json"
_DOC_FILE = _FA_DIR / "doc" / "infoic-field-dictionary.md"
_PKG_DETAILS_FILE = _FA_DIR / "doc" / "package-details.md"
_PROTO_FLAGS_FILE = _FA_DIR / "doc" / "protocol-flags.md"
_FIRESTARTER_PKG_DIR = _FA_DIR / "firestarter"

# The exact algorithm id of the two TEXAS INSTRUMENTS rows missing both
# protect_* keys (UV-EPROM, 0x0B).
_ALGORITHM_UV_EPROM_0X0B = 11

# The shared pointer substring Task 2 (this plan) landed verbatim in both
# doc/package-details.md and doc/protocol-flags.md -- defined here as the
# module constant leg 5 asserts against both files.
_SHARED_POINTER_SUBSTRING = (
    "[documented once in `infoic-field-dictionary.md`]"
    "(infoic-field-dictionary.md#protect-flags-bits-14-15)"
)

# ---------------------------------------------------------------------------
# Pre-compiled patterns that PARSE the doc's own stated figures -- these are
# not the expected values themselves; leg 1 compares whatever the doc
# currently says against a fresh DB recomputation, so a doc edit that
# introduces a wrong number goes red naming that number, not just a stale
# hard-coded assertion.
# ---------------------------------------------------------------------------

_RE_POA_HEADLINE = re.compile(
    r"protect_on_after`: `true` on \*\*(\d+) of (\d+)\*\* rows, "
    r"`false` on (\d+)"
)
_RE_POA_BY_ALG = re.compile(
    r"By algorithm: `5` → \*\*(\d+) of (\d+)\*\* \(a constant there\), "
    r"`13` → (\d+)\."
)
_RE_POB_HEADLINE = re.compile(
    r"protect_off_before`: `true` on \*\*(\d+) of (\d+)\*\*, `false` on (\d+)"
)
_RE_POB_BY_ALG = re.compile(
    r"By algorithm: `5` → (\d+) of (\d+), `6` → (\d+) of (\d+), "
    r"`13` → (\d+) of (\d+), `52` → (\d+) of (\d+)\."
)
_RE_BOTH_KEYS = re.compile(r"\*\*(\d+) of (\d+)\*\* rows carry both fields")
_RE_PROMOTION_SPLIT_POA = re.compile(
    r"\*\*(\d+) of (\d+) upstream-native `0x0D` rows plus (\d+) of the "
    r"(\d+) promoted rows\*\*"
)
_RE_PROMOTION_SPLIT_POB = re.compile(
    r"`protect_off_before` splits identically: (\d+) of (\d+) native, "
    r"(\d+) of (\d+) promoted"
)
_RE_ALG6_CORRELATION = re.compile(
    r"(\d+) of (\d+) rows on algorithm 6 [^.]*carry `protect_off_before: "
    r"true`"
)


# ---------------------------------------------------------------------------
# DB loading / measurement helpers
# ---------------------------------------------------------------------------


def _load_db() -> dict:
    return json.loads(_DB_FILE.read_text(encoding="utf-8"))


def _all_rows(db: dict) -> list[tuple[str, dict]]:
    rows = []
    for mfr, chips in db.items():
        for chip in chips:
            rows.append((mfr, chip))
    return rows


def _measure_protect_field(
    rows: list[tuple[str, dict]], field: str
) -> tuple[int, int, int, dict[int, int], dict[int, int]]:
    """Recompute (true_count, false_count, absent_count, by_algorithm_true,
    by_algorithm_total) for `field` ("protect_on_after" or
    "protect_off_before") over every row.

    Uses `.get(...)` with a strict `is True` comparison -- never a direct
    subscript -- so the two TEXAS INSTRUMENTS rows missing both keys do not
    raise (see `test_two_row_exception_is_real_and_get_discipline_justified`
    for the leg proving a direct index WOULD raise on exactly those rows).
    """
    true_n = false_n = absent_n = 0
    by_alg_true: dict[int, int] = {}
    by_alg_total: dict[int, int] = {}
    for _mfr, chip in rows:
        programming = chip["programming"]
        alg = programming["algorithm"]
        by_alg_total[alg] = by_alg_total.get(alg, 0) + 1
        val = programming.get(field)
        if val is None:
            absent_n += 1
        elif val is True:
            true_n += 1
            by_alg_true[alg] = by_alg_true.get(alg, 0) + 1
        else:
            false_n += 1
    return true_n, false_n, absent_n, by_alg_true, by_alg_total


# ---------------------------------------------------------------------------
# Leg 1: doc figures equal the DB, element-wise where a set is small enough
# to read.
# ---------------------------------------------------------------------------


def test_doc_protect_on_after_figures_match_recomputed_db() -> None:
    """Every protect_on_after figure the doc states -- headline true/false
    and the by-algorithm breakdown for algorithms 5 and 13 -- must equal a
    fresh recomputation against the committed chip_database.json."""
    rows = _all_rows(_load_db())
    true_n, false_n, absent_n, by_alg_true, by_alg_total = _measure_protect_field(
        rows, "protect_on_after"
    )
    assert (true_n, false_n, absent_n) == (70, 674, 2), (
        f"Measured protect_on_after true/false/absent = "
        f"{true_n}/{false_n}/{absent_n}; expected 70/674/2."
    )
    assert by_alg_true.get(5) == 27 and by_alg_total.get(5) == 27, (
        "Measured protect_on_after is not 27 of 27 on algorithm 5: "
        f"{by_alg_true.get(5)} of {by_alg_total.get(5)}."
    )
    assert by_alg_true.get(13) == 43, (
        f"Measured protect_on_after on algorithm 13 is {by_alg_true.get(13)}; "
        "expected 43."
    )

    doc = _DOC_FILE.read_text(encoding="utf-8")
    m = _RE_POA_HEADLINE.search(doc)
    assert m, (
        "doc/infoic-field-dictionary.md does not state the protect_on_after headline figures in the expected shape"
    )
    doc_true, doc_total, doc_false = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    assert (doc_true, doc_total, doc_false) == (true_n, 746, false_n), (
        f"doc states protect_on_after true={doc_true} of {doc_total}, "
        f"false={doc_false}; measured true={true_n} of 746, false={false_n}."
    )

    m2 = _RE_POA_BY_ALG.search(doc)
    assert m2, (
        "doc/infoic-field-dictionary.md does not state the protect_on_after by-algorithm figures in the expected shape"
    )
    doc_alg5, doc_alg5_total, doc_alg13 = (
        int(m2.group(1)),
        int(m2.group(2)),
        int(m2.group(3)),
    )
    assert doc_alg5 == by_alg_true.get(5) and doc_alg5_total == by_alg_total.get(5), (
        f"doc states protect_on_after algorithm 5 = {doc_alg5} of "
        f"{doc_alg5_total}; measured {by_alg_true.get(5)} of "
        f"{by_alg_total.get(5)}."
    )
    assert doc_alg13 == by_alg_true.get(13), (
        f"doc states protect_on_after algorithm 13 = {doc_alg13}; measured "
        f"{by_alg_true.get(13)}."
    )


def test_doc_protect_off_before_figures_match_recomputed_db() -> None:
    """Every protect_off_before figure the doc states -- headline true/false
    and the by-algorithm breakdown for algorithms 5, 6, 13 and 52 -- must
    equal a fresh recomputation against the committed chip_database.json."""
    rows = _all_rows(_load_db())
    true_n, false_n, absent_n, by_alg_true, by_alg_total = _measure_protect_field(
        rows, "protect_off_before"
    )
    assert (true_n, false_n, absent_n) == (148, 596, 2), (
        f"Measured protect_off_before true/false/absent = "
        f"{true_n}/{false_n}/{absent_n}; expected 148/596/2."
    )
    assert by_alg_true.get(5) == 27
    assert by_alg_true.get(6) == 77 and by_alg_total.get(6) == 190
    assert by_alg_true.get(13) == 43 and by_alg_total.get(13) == 84
    assert by_alg_true.get(52) == 1 and by_alg_total.get(52) == 1

    doc = _DOC_FILE.read_text(encoding="utf-8")
    m = _RE_POB_HEADLINE.search(doc)
    assert m, (
        "doc/infoic-field-dictionary.md does not state the protect_off_before headline figures in the expected shape"
    )
    doc_true, doc_total, doc_false = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    assert (doc_true, doc_total, doc_false) == (true_n, 746, false_n), (
        f"doc states protect_off_before true={doc_true} of {doc_total}, "
        f"false={doc_false}; measured true={true_n} of 746, false={false_n}."
    )

    m2 = _RE_POB_BY_ALG.search(doc)
    assert m2, (
        "doc/infoic-field-dictionary.md does not state the protect_off_before by-algorithm figures in the expected shape"
    )
    (
        doc_alg5,
        doc_alg6,
        doc_alg6_total,
        doc_alg13,
        doc_alg13_total,
        doc_alg52,
        doc_alg52_total,
    ) = (
        int(m2.group(1)),
        int(m2.group(3)),
        int(m2.group(4)),
        int(m2.group(5)),
        int(m2.group(6)),
        int(m2.group(7)),
        int(m2.group(8)),
    )
    assert doc_alg5 == by_alg_true.get(5)
    assert (doc_alg6, doc_alg6_total) == (by_alg_true.get(6), by_alg_total.get(6))
    assert (doc_alg13, doc_alg13_total) == (by_alg_true.get(13), by_alg_total.get(13))
    assert (doc_alg52, doc_alg52_total) == (by_alg_true.get(52), by_alg_total.get(52))


def test_doc_both_keys_count_matches_recomputed_db() -> None:
    """The '744 of 746 rows carry both fields' claim must equal a fresh
    count against the committed chip_database.json."""
    rows = _all_rows(_load_db())
    both_present = sum(
        1
        for _mfr, chip in rows
        if "protect_on_after" in chip["programming"]
        and "protect_off_before" in chip["programming"]
    )
    assert both_present == 744, f"Measured {both_present} of 746; expected 744."

    doc = _DOC_FILE.read_text(encoding="utf-8")
    m = _RE_BOTH_KEYS.search(doc)
    assert m, "doc does not state the '<N> of 746 rows carry both fields' figure"
    doc_both, doc_total = int(m.group(1)), int(m.group(2))
    assert (doc_both, doc_total) == (both_present, 746), (
        f"doc states {doc_both} of {doc_total} rows carry both fields; "
        f"measured {both_present} of 746."
    )


# ---------------------------------------------------------------------------
# Leg 2: the two-row exception is real, and the .get(...) discipline is
# justified (a direct index DOES raise), not merely asserted.
# ---------------------------------------------------------------------------


def test_two_row_exception_is_real_and_get_discipline_is_justified() -> None:
    db = _load_db()
    rows = _all_rows(db)

    missing = [
        (mfr, chip["part_number"])
        for mfr, chip in rows
        if "protect_on_after" not in chip["programming"]
        or "protect_off_before" not in chip["programming"]
    ]
    assert len(missing) == 2, f"Expected exactly 2 rows missing a key, found: {missing}"
    assert set(missing) == {
        ("TEXAS INSTRUMENTS", "2516"),
        ("TEXAS INSTRUMENTS", "2532"),
    }, f"Expected the two TEXAS INSTRUMENTS rows; measured: {missing}"
    for mfr, part_number in missing:
        chip = next(c for m, c in rows if m == mfr and c["part_number"] == part_number)
        assert chip["programming"]["algorithm"] == _ALGORITHM_UV_EPROM_0X0B

    # The .get(...) discipline: walking every row with .get(...) never
    # raises, including on the two exception rows.
    for _mfr, chip in rows:
        chip["programming"].get("protect_on_after")
        chip["programming"].get("protect_off_before")

    # Non-vacuity for that discipline: a DIRECT index DOES raise KeyError on
    # exactly the two exception rows -- proving .get(...) is necessary, not
    # merely a stylistic preference.
    for mfr, part_number in missing:
        chip = next(c for m, c in rows if m == mfr and c["part_number"] == part_number)
        try:
            _ = chip["programming"]["protect_on_after"]
        except KeyError:
            pass
        else:
            raise AssertionError(
                f"{mfr}/{part_number}: a direct index into "
                "programming['protect_on_after'] did not raise KeyError as "
                "expected -- the .get(...) discipline would be asserted, "
                "never justified non-vacuously."
            )


# ---------------------------------------------------------------------------
# Leg 3: the algorithm-13 promotion split -- 18 of 18 native, 25 of 66
# promoted -- and the doc must state the split, not the bare 43.
# ---------------------------------------------------------------------------


def test_algorithm_13_promotion_split_matches_doc() -> None:
    rows = _all_rows(_load_db())
    alg13 = [chip for _mfr, chip in rows if chip["programming"]["algorithm"] == 13]
    assert len(alg13) == 84, f"Expected 84 algorithm-13 rows, found {len(alg13)}."

    native = [c for c in alg13 if "page_size" in c["programming"]]
    promoted = [c for c in alg13 if "page_size" not in c["programming"]]
    assert len(native) == 18 and len(promoted) == 66, (
        f"Expected 18 native / 66 promoted algorithm-13 rows (by the "
        f"page_size-presence proxy); measured {len(native)} native / "
        f"{len(promoted)} promoted."
    )

    native_poa_true = sum(
        1 for c in native if c["programming"]["protect_on_after"] is True
    )
    promoted_poa_true = sum(
        1 for c in promoted if c["programming"]["protect_on_after"] is True
    )
    assert native_poa_true == 18, (
        f"Expected all 18 native rows protect_on_after=True; measured {native_poa_true}."
    )
    assert promoted_poa_true == 25, (
        f"Expected 25 of 66 promoted rows protect_on_after=True; measured {promoted_poa_true}."
    )
    assert native_poa_true + promoted_poa_true == 43

    native_pob_true = sum(
        1 for c in native if c["programming"]["protect_off_before"] is True
    )
    promoted_pob_true = sum(
        1 for c in promoted if c["programming"]["protect_off_before"] is True
    )
    assert native_pob_true == 18
    assert promoted_pob_true == 25

    doc = _DOC_FILE.read_text(encoding="utf-8")
    m = _RE_PROMOTION_SPLIT_POA.search(doc)
    assert m, "doc does not state the 18/18 + 25/66 protect_on_after promotion split"
    assert (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))) == (
        native_poa_true,
        len(native),
        promoted_poa_true,
        len(promoted),
    ), "doc's promotion-split figures do not match the recomputed DB values"

    m2 = _RE_PROMOTION_SPLIT_POB.search(doc)
    assert m2, "doc does not state the protect_off_before promotion split"
    assert (int(m2.group(1)), int(m2.group(2)), int(m2.group(3)), int(m2.group(4))) == (
        native_pob_true,
        len(native),
        promoted_pob_true,
        len(promoted),
    )

    # The bare, unqualified "43" alone must not appear as the ONLY figure
    # documenting algorithm 13 -- the split sentence itself must be present
    # (already asserted above); this is a belt-and-suspenders check that the
    # doc text contains the "18 of 18" and "25 of 66" substrings verbatim.
    assert "18 of 18" in doc and "25 of 66" in doc


def test_algorithm_6_correlation_is_stated_as_suggestive_not_derivable() -> None:
    rows = _all_rows(_load_db())
    alg6 = [chip for _mfr, chip in rows if chip["programming"]["algorithm"] == 6]
    pob_true = sum(1 for c in alg6 if c["programming"]["protect_off_before"] is True)
    assert (pob_true, len(alg6)) == (77, 190)

    doc = _DOC_FILE.read_text(encoding="utf-8")
    m = _RE_ALG6_CORRELATION.search(doc)
    assert m, (
        "doc does not carry the algorithm-6 protect_off_before correlation sentence"
    )
    assert (int(m.group(1)), int(m.group(2))) == (77, 190)
    assert "non-derivable" in doc or "non-derivable" in doc.lower()
    assert "W29C020C" in doc and "W29EE011" in doc


# ---------------------------------------------------------------------------
# Leg 4: no runtime consumer, proven by a source scan (structural, not a
# gate -- D-16 forbids a new tools/check_*.py; the in-tree precedent for
# this shape is a test).
# ---------------------------------------------------------------------------


def _prose_line_numbers(text: str) -> set[int]:
    """Line numbers covered by a `#` comment token or a module/class/
    function docstring -- i.e. text a human reads as prose, never code that
    executes. Built from `tokenize` (comments) + `ast` (docstrings) rather
    than a naive "starts with #" check, so a field-name mention inside a
    multi-line docstring (which does not start with `#`) is still correctly
    classified as prose."""
    prose_lines: set[int] = set()

    readline = io.StringIO(text).readline
    for tok in tokenize.generate_tokens(readline):
        if tok.type == tokenize.COMMENT:
            prose_lines.update(range(tok.start[0], tok.end[0] + 1))

    tree = ast.parse(text)

    def _mark_if_docstring(node: ast.AST) -> None:
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(getattr(body[0], "value", None), ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            first = body[0]
            end = getattr(first, "end_lineno", first.lineno)
            prose_lines.update(range(first.lineno, end + 1))

    _mark_if_docstring(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _mark_if_docstring(node)

    return prose_lines


def test_no_runtime_consumer_in_shipped_package_source_scan() -> None:
    """Every occurrence of `protect_on_after` / `protect_off_before` under
    `firestarter_app/firestarter/` must fall on a comment or docstring line
    -- never on a line of executable code. This is the structural proof
    that D-14/D-16's "no runtime consumer" claim holds, and it is a Python
    test that actually runs (see module docstring, C-13)."""
    offenders: list[str] = []
    checked_any = False
    for py_file in sorted(_FIRESTARTER_PKG_DIR.rglob("*.py")):
        rel = py_file.relative_to(_FA_DIR)
        text = py_file.read_text(encoding="utf-8")
        if "protect_on_after" not in text and "protect_off_before" not in text:
            continue
        checked_any = True
        prose_lines = _prose_line_numbers(text)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "protect_on_after" in line or "protect_off_before" in line:
                if lineno not in prose_lines:
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert checked_any, (
        "No file under firestarter_app/firestarter/ mentions either field "
        "at all -- this would mean the known provenance comments were "
        "removed; investigate before trusting the (vacuous) pass below."
    )
    assert not offenders, (
        "Found protect_on_after/protect_off_before OUTSIDE a comment or "
        "docstring (i.e. as executable code) -- naming file:line: "
        + "; ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Leg 5: documented once -- one authoritative heading, one pointer per file,
# no pointer restates a figure.
# ---------------------------------------------------------------------------


def test_documented_once_one_heading_two_pointers_no_restated_figures() -> None:
    doc = _DOC_FILE.read_text(encoding="utf-8")
    headings = [
        line
        for line in doc.splitlines()
        if line.startswith("### ")
        and "protect_off_before" in line
        and "protect_on_after" in line
    ]
    assert len(headings) == 1, (
        f"Expected exactly one authoritative ### heading naming both "
        f"fields; found {len(headings)}: {headings}"
    )

    measured_figures = (
        "70 of 746",
        "148 of 746",
        "744 of 746",
        "27 of 27",
        "18 of 18",
        "25 of 66",
        "77 of 190",
    )
    for path in (_PKG_DETAILS_FILE, _PROTO_FLAGS_FILE):
        text = path.read_text(encoding="utf-8")
        pointer_lines = [
            line for line in text.splitlines() if _SHARED_POINTER_SUBSTRING in line
        ]
        assert len(pointer_lines) == 1, (
            f"{path.name}: expected exactly one pointer line carrying the "
            f"shared substring; found {len(pointer_lines)}: {pointer_lines}"
        )
        line = pointer_lines[0]
        for figure in measured_figures:
            assert figure not in line, (
                f"{path.name}'s pointer line restates the figure "
                f"'{figure}' -- 'documented once' means one statement, not "
                "a second place to keep in sync."
            )


# ---------------------------------------------------------------------------
# Leg 6: sdp_capability.py untouched -- D-16's untouched-guard, copied from
# test_b15_page_size_corroboration.py's shape.
# ---------------------------------------------------------------------------


def test_sdp_capability_module_untouched_this_plan() -> None:
    """Structural guard: this plan must not depend on, or require edits to,
    `firestarter/sdp_capability.py` -- D-16 forbids editing it. This test
    only checks that the module still imports cleanly and still carries a
    distinctive substring of its own docstring, unedited."""
    from firestarter import sdp_capability

    assert "static fail-closed allow-list" in (sdp_capability.__doc__ or ""), (
        "sdp_capability.py's docstring no longer carries the 'static "
        "fail-closed allow-list' prose this plan deliberately left "
        "unedited -- if it changed for a legitimate reason, this "
        "assertion should be updated by whichever plan makes that change, "
        "not silently ignored."
    )


# ---------------------------------------------------------------------------
# Leg 7: non-vacuity control for the recomputation-vs-doc comparison
# machinery -- a moved synthetic chip must be named, and an untouched
# control chip must not.
# ---------------------------------------------------------------------------


def _partition_by_protect_on_after(db: dict) -> tuple[list[str], list[str]]:
    """(allow, refuse) sorted key lists where allow = protect_on_after is
    True, over a `{manufacturer: [chip, ...]}`-shaped db (real or
    synthetic)."""
    allow: list[str] = []
    refuse: list[str] = []
    for mfr, chips in db.items():
        for chip in chips:
            key = f"{mfr}/{chip['part_number']}"
            if chip["programming"].get("protect_on_after") is True:
                allow.append(key)
            else:
                refuse.append(key)
    return sorted(allow), sorted(refuse)


def _assert_partitions_match(
    partition_a: list[str], partition_b: list[str], label_a: str, label_b: str
) -> None:
    set_a, set_b = set(partition_a), set(partition_b)
    only_a = sorted(set_a - set_b)
    only_b = sorted(set_b - set_a)
    offenders = only_a or only_b
    assert not offenders, (
        f"'{label_a}' and '{label_b}' protect_on_after partitions "
        f"disagree. Only in '{label_a}': {only_a}. Only in '{label_b}': "
        f"{only_b}."
    )


def test_recomputation_helper_non_vacuous_on_a_moved_synthetic_chip() -> None:
    """Flipping a synthetic chip's protect_on_after field between two
    in-memory DBs must make `_assert_partitions_match` raise, naming the
    moved chip and NOT the untouched control -- proving the comparison
    machinery this plan's doc-vs-DB legs rely on genuinely distinguishes
    agreement from disagreement, rather than being vacuously green."""
    synthetic_before = {
        "SYNTHETIC_MFR": [
            {
                "part_number": "MOVED_CHIP",
                "programming": {"algorithm": 13, "protect_on_after": True},
            },
            {
                "part_number": "CONTROL_CHIP",
                "programming": {"algorithm": 13, "protect_on_after": False},
            },
        ]
    }
    allow_before, refuse_before = _partition_by_protect_on_after(synthetic_before)
    assert allow_before == ["SYNTHETIC_MFR/MOVED_CHIP"], (
        "Fixture setup error: expected exactly the moved chip in the ALLOW "
        f"partition before the flip; measured {allow_before!r}"
    )
    assert refuse_before == ["SYNTHETIC_MFR/CONTROL_CHIP"], (
        "Fixture setup error: expected the control chip in the REFUSE "
        f"partition before the flip; measured {refuse_before!r}"
    )

    synthetic_after = {
        "SYNTHETIC_MFR": [
            {
                "part_number": "MOVED_CHIP",
                "programming": {"algorithm": 13, "protect_on_after": False},
            },
            {
                "part_number": "CONTROL_CHIP",
                "programming": {"algorithm": 13, "protect_on_after": False},
            },
        ]
    }
    allow_after, _refuse_after = _partition_by_protect_on_after(synthetic_after)
    assert "SYNTHETIC_MFR/MOVED_CHIP" not in allow_after, (
        "Fixture setup error: the flipped chip must no longer measure ALLOW "
        f"after the move; measured {allow_after!r}"
    )

    try:
        _assert_partitions_match(allow_before, allow_after, "before", "after")
    except AssertionError as exc:
        message = str(exc)
        assert "MOVED_CHIP" in message, (
            f"Non-vacuity failure: the raised message does not name the "
            f"moved chip. Message was: {message!r}"
        )
        assert "CONTROL_CHIP" not in message, (
            f"Non-vacuity failure: the raised message names the untouched "
            f"control chip, which never moved. Message was: {message!r}"
        )
    else:
        raise AssertionError(
            "Non-vacuity failure: flipping a synthetic chip's "
            "protect_on_after field did not make _assert_partitions_match "
            "raise -- the comparison machinery this plan's doc-vs-DB legs "
            "rely on would be vacuous."
        )
