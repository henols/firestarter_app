"""
RPT-F1: measured proof that `cli_handlers._canonical_part_number` names the
alias that was in the socket, over the WHOLE database and over every issue
this project has actually filed -- not merely on the seven hand-picked
inputs Task 1's own inline `<verify>` leg exercises.

Consumes `tests/fixtures/part_number_delta.json` (GATE-04's measured
artifact) as ground truth and never regenerates it: `aggregate` carries the
domain size (953 distinct aliases, 26 filed issues, 16
`chip_not_implemented`), `aliases` carries one row per measured alias, and
`filed_issues` carries the 26 real `(raw_token, resolved_part_number)`
pairs this project has actually posted to GitHub.

The selector mirrors `database.get_eprom_config`'s own exact-then-
alias-exact-then-paren-stripped ladder rung for rung (D-02) rather than
writing a second normalization, so the two agree by construction rather
than by coincidence -- `test_the_selector_agrees_with_the_database_lookup_
for_every_alias` below is what makes that checkable rather than assumed.

Anti-vacuity discipline (this project's house standard): every sweep
asserts its own subject is real
(non-empty) BEFORE asserting anything about it, and
`test_an_empty_expected_alias_set_fails_rather_than_passing_vacuously`
proves the sweep's own guard is load-bearing, not decorative.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any, Optional

import pytest

from firestarter.cli_handlers import _canonical_part_number
from tests.plan_corpus import REAL_DB

_DELTA_PATH = Path(__file__).parent / "fixtures" / "part_number_delta.json"


def _load_delta() -> dict[str, Any]:
    return json.loads(_DELTA_PATH.read_text(encoding="utf-8"))


def _independently_strip_paren(value: str) -> str:
    """A second, independent parenthetical-stripper -- reimplemented here
    rather than imported from `database.py`, so this reference oracle
    cannot silently share a bug with the code it is checking."""
    return re.sub(r"\([^)]*\)", "", value).strip().lower()


def _alias_matching_token(part_number: str, token: str) -> str:
    """Independent reference lookup (never calls the selector): the
    comma-split element of `part_number` whose stripped, lower-cased form
    equals `token`'s, falling back to a paren-stripped comparison (some
    measured tokens, e.g. `at28c64b`, only match a same-row alias after
    ITS parenthetical is stripped -- `AT28C64B(Non-Standard)` -- because a
    different, unrelated row happens to carry the plain `AT28C64B` alias
    and `get_eprom_config`'s per-row rung order reaches this row first).
    Every alias row in the delta artifact is, by construction, one of
    `part_number`'s own comma-split elements under one of these two
    comparisons -- this function proves the selector picked THAT element,
    verbatim, rather than merely returning some string that happens to
    look plausible."""
    query = token.strip().lower()
    elements = [element.strip() for element in part_number.split(",")]
    for element in elements:
        if element.lower() == query:
            return element
    query_stripped = _independently_strip_paren(token)
    for element in elements:
        if _independently_strip_paren(element) == query_stripped:
            return element
    raise AssertionError(f"{token!r} is not an alias of {part_number!r}")


def _alias_matching_token_or_first(part_number: str, token: str) -> str:
    """Same as `_alias_matching_token`, plus D-01's fallback rung: the
    first comma-split element, stripped, verbatim, when no alias matches
    even after paren-stripping."""
    query = token.strip().lower()
    elements = [element.strip() for element in part_number.split(",")]
    for element in elements:
        if element.lower() == query:
            return element
    query_stripped = _independently_strip_paren(token)
    for element in elements:
        if _independently_strip_paren(element) == query_stripped:
            return element
    return elements[0]


def _rung_of(part_number: str, token: str) -> int:
    """Descriptive-only classification (never consulted by the selector or
    the reference oracle) -- which of `get_eprom_config`'s three matching
    rungs this `(part_number, token)` pair exercises, for the rung-count
    non-vacuity assertion below."""
    query = token.strip().lower()
    if query == part_number.lower():
        return 1
    elements = [element.strip() for element in part_number.split(",")]
    if query in (element.lower() for element in elements):
        return 2
    return 3


def _assert_alias_sweep_matches_and_is_non_vacuous(aliases: list[dict]) -> None:
    assert aliases, "no alias rows to sweep -- the sweep would pass vacuously"

    rung_counts = {1: 0, 2: 0, 3: 0}
    for row in aliases:
        part_number = row["resolved_part_number"]
        token = row["token"]
        expected = _alias_matching_token(part_number, token)
        got = _canonical_part_number(part_number, token)
        assert got == expected, (token, part_number, got, expected)
        rung_counts[_rung_of(part_number, token)] += 1

    assert sum(rung_counts.values()) == len(aliases)
    assert rung_counts[2] > 0, (
        "rung two was never exercised -- the pin would be vacuous"
    )


def test_the_selector_returns_the_alias_that_matches_the_raw_token() -> None:
    """D-01's rung-two pin, over the whole measured alias domain (953
    distinct aliases) -- not the seven hand-picked inputs Task 1's own
    inline `<verify>` leg exercises."""
    delta = _load_delta()
    assert delta["aggregate"]["distinct_aliases"] == 953, delta["aggregate"]
    _assert_alias_sweep_matches_and_is_non_vacuous(delta["aliases"])


def test_the_selector_agrees_with_the_database_lookup_for_every_alias() -> None:
    """D-02's agreement pin: for every alias the delta artifact resolved
    (skipping the 16 `not-implemented` rows it already names), the
    selector's output is one of `get_eprom_config`'s OWN matched row's
    comma-split aliases -- the property that makes "mirrors the ladder"
    checkable rather than merely plausible. A second normalization would
    eventually pick an alias from a row the lookup did not match; this
    test is what would catch that."""
    delta = _load_delta()
    aliases = delta["aliases"]
    assert aliases, "no alias rows to sweep -- the sweep would pass vacuously"

    skipped = 0
    disagreements: list[tuple[str, str, str | None]] = []
    for row in aliases:
        if row["resolve_status"] == "not-implemented":
            skipped += 1
            continue
        token = row["token"]
        config, _manufacturer = REAL_DB.get_eprom_config(token)
        assert config is not None, token
        row_part_number = config.get("part_number", "")
        got = _canonical_part_number(row_part_number, token)
        row_aliases = [element.strip() for element in row_part_number.split(",")]
        if got not in row_aliases:
            disagreements.append((token, row_part_number, got))

    assert skipped == 16, skipped
    assert not disagreements, disagreements


def test_all_twenty_six_filed_issues_resolve_to_the_part_that_was_in_the_socket() -> (
    None
):
    """D-01's headline claim: for all 26 issues this project has actually
    filed, the selector's output names the part that was in the socket --
    the token-matching alias where one exists, the first alias otherwise.
    Five of the 26 filed raw tokens are not lower-case (e.g. issue 45's
    `W27E040`), so the case-insensitive comparison this asserts is
    load-bearing rather than decorative."""
    delta = _load_delta()
    filed_issues = delta["filed_issues"]
    assert len(filed_issues) == 26, len(filed_issues)

    mismatches: list[tuple[int, str, str, str | None, str]] = []
    for row in filed_issues:
        part_number = row["resolved_part_number"]
        token = row["raw_token"]
        want = _alias_matching_token_or_first(part_number, token)
        got = _canonical_part_number(part_number, token)
        if got != want:
            mismatches.append((row["issue"], token, part_number, got, want))

    assert not mismatches, mismatches


_PAREN_RUNG_ANCHOR = (
    "            for alias in aliases:\n"
    "                if _strip_paren(alias) == query_stripped:\n"
    "                    return alias\n"
)
_PAREN_RUNG_MUTANT = (
    "            for alias in aliases:\n"
    "                if _strip_paren(alias) == query_stripped:\n"
    "                    return alias.lower()\n"
)


def _assert_paren_alias_kept_verbatim(selector: Any) -> None:
    got = selector("DS1245AB(RW),DS1245Y(RW)", "DS1245AB")
    assert got == "DS1245AB(RW)", got


def test_a_parenthetical_alias_is_carried_verbatim() -> None:
    """D-02, both directions. First: the real selector's paren-stripped
    rung (rung three) returns the alias WITH its parentheses intact --
    stripping would file two distinct database rows (the `(RW)` and
    `(TEST)` variants of the same DALLAS NVRAM) under one title. Second,
    the planted leg: a mutation that lower-cases exactly that rung's
    return value is observed to redden this exact pin -- the anchor's
    uniqueness is asserted BEFORE mutating, and the mutant is built and
    executed entirely in memory; no file is written."""
    _assert_paren_alias_kept_verbatim(_canonical_part_number)

    source = inspect.getsource(_canonical_part_number)
    assert source.count(_PAREN_RUNG_ANCHOR) == 1, "anchor is not unique in the source"
    mutated_source = source.replace(_PAREN_RUNG_ANCHOR, _PAREN_RUNG_MUTANT, 1)

    namespace: dict[str, Any] = {"Optional": Optional}
    exec(
        compile(mutated_source, "<181-05 planted paren-rung mutant>", "exec"),
        namespace,
    )
    mutant = namespace["_canonical_part_number"]
    assert mutant("DS1245AB(RW),DS1245Y(RW)", "DS1245AB") == "ds1245ab(rw)"

    with pytest.raises(AssertionError):
        _assert_paren_alias_kept_verbatim(mutant)


def test_an_empty_expected_alias_set_fails_rather_than_passing_vacuously() -> None:
    """The sweep's own non-vacuity guard, proven rather than assumed: an
    empty alias list must fail the sweep helper, not pass it trivially."""
    with pytest.raises(AssertionError):
        _assert_alias_sweep_matches_and_is_non_vacuous([])
