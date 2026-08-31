"""DATA-06's proof: the absence of a `protect_off_before` / `protect_on_after`
runtime consumer must be proven by a source scan, not asserted in prose.

Why this file exists as Python rather than more markdown (C-13):
`firestarter_app/.github/workflows/ci.yml`'s `paths-ignore` includes
`'**.md'` on both `push` and `pull_request`, so a markdown-only commit fires
no CI at all. This module is not markdown, so it is the thing that actually
runs.

**168-09 note (2026-08-31):** this module originally also gated the
"Infoic Field Dictionary" wiki page's own stated protect-flag figures
against a fresh recomputation of the committed `chip_database.json` (six
legs, modeled on `tests/test_b15_page_size_corroboration.py`'s shape:
pre-compiled regex patterns parsing the doc's own stated figures, never
hard-coding only the expected values). Those legs read the app repository's
own copies of the Infoic Field Dictionary, Package Details and Protocol
Flags references, all deleted here as part of MIGRATE-02.
The doc-vs-DB parity they proved is not replaced in this phase -- see
168-09-SUMMARY.md for exactly what left and what, if anything, checks it
now.

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
import tokenize
from pathlib import Path

_FA_DIR = Path(__file__).parent.parent
_DB_FILE = _FA_DIR / "firestarter" / "data" / "chip_database.json"
_FIRESTARTER_PKG_DIR = _FA_DIR / "firestarter"

# The exact algorithm id of the two TEXAS INSTRUMENTS rows missing both
# protect_* keys (UV-EPROM, 0x0B).
_ALGORITHM_UV_EPROM_0X0B = 11


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
