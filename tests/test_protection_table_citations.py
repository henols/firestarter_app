"""Prove every curated protection-readability citation resolves (LOCK-01, Task 3).

Shape mirrors `test_lockable_proms_doc_claims.py`: module-level `_FA_DIR`,
`_DOC_FILE`, a `_read_doc_text()` helper, pre-compiled `re` patterns as module
constants each with a comment explaining why the pattern is a real checkable
negative, and per-leg docstrings that assert on text rather than line number.

No leg in this file asserts that any readability verdict is *correct* -- no
test in this phase can establish that (`151-DESIGN.md` §8's evidence ceiling).
Every leg here asserts only that a verdict carries a citation, and that the
citation resolves to text actually present in `doc/lockable-proms.md`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from firestarter.protection_readability import (
    AMBIGUOUS_DOC_CITATIONS,
    DOCUMENTED_NOT_READABLE_TOKENS,
    DOCUMENTED_READABLE_TOKENS,
    MECHANISM_BY_TOKEN,
    MECHANISM_STATES,
    PERMANENCE_BY_TOKEN,
    PERMANENCE_STATES,
)

_FA_DIR = Path(__file__).parent.parent
_MODULE_FILE = _FA_DIR / "firestarter" / "protection_readability.py"
_DOC_FILE = _FA_DIR / "doc" / "lockable-proms.md"

# A citation comment quotes its lockable-proms.md row-key fragment inside a
# pair of double quotes, e.g. `"W29C020 / W29C020C"`. This is a real
# checkable negative: if a row is edited out from under a citation, or a
# citation is fabricated, the quoted fragment stops resolving as a substring
# of the document and the search below fails loudly instead of silently.
_QUOTED_FRAGMENT_RE = re.compile(r'"([^"]{3,})"')

# A `lockable-proms.md:NNN` style line reference. Real checkable negative:
# a citation missing this entirely cannot be traced back to a specific row.
_LINE_REF_RE = re.compile(r":\d+")

# A `§N` section reference, matching the document's own numbered `# N. ...`
# headings. Real checkable negative: distinguishes a citation naming a
# specific document section from a bare unattributed claim.
_SECTION_REF_RE = re.compile(r"§\d+")

# A bare quoted alias token inside a frozenset display line, e.g. `"AM29F010",`.
# Deliberately excludes `key: value` dict-display lines (used by
# AMBIGUOUS_DOC_CITATIONS / MECHANISM_BY_TOKEN / PERMANENCE_BY_TOKEN), which
# this file does not require citations for -- only the two readability
# frozensets are in scope for the citation-coverage legs.
_TOKEN_LINE_RE = re.compile(r'^\s*"([A-Z0-9]+)",?\s*$')
_TOKEN_ON_LINE_RE = re.compile(r'"([A-Z0-9]+)"')


def _read_module_text() -> str:
    return _MODULE_FILE.read_text()


def _read_doc_text() -> str:
    return _DOC_FILE.read_text()


def _extract_citation_groups(
    source_text: str, block_start_marker: str, block_end_marker: str = ")"
) -> list[tuple[str, list[str]]]:
    """Split one frozenset-display block into (comment_text, [tokens]) groups.

    A "group" is one run of consecutive `#`-comment lines (concatenated into
    one comment_text) followed by the token-literal lines that immediately
    follow it, up to the next comment run or the end of the block.
    """
    lines = source_text.splitlines()
    start = next(i for i, line in enumerate(lines) if block_start_marker in line)
    end = next(
        i for i in range(start + 1, len(lines)) if lines[i].strip() == block_end_marker
    )
    block = lines[start:end]

    groups: list[tuple[str, list[str]]] = []
    current_comment: list[str] = []
    current_tokens: list[str] = []
    for line in block:
        stripped = line.strip()
        if stripped.startswith("#"):
            if current_tokens:
                groups.append((" ".join(current_comment), current_tokens))
                current_comment, current_tokens = [], []
            current_comment.append(stripped.lstrip("#").strip())
        else:
            found = _TOKEN_ON_LINE_RE.findall(line)
            if found:
                current_tokens.extend(found)
    if current_tokens:
        groups.append((" ".join(current_comment), current_tokens))
    return groups


def _assert_all_fragments_resolve(
    groups: list[tuple[str, list[str]]], doc_text: str
) -> None:
    """Leg 2's real checkable negative, reused by leg 5's non-vacuity control.

    For every citation comment, every double-quoted fragment inside it must
    appear verbatim, as a plain substring, in `doc_text`. Raises naming the
    offending fragment and the tokens it cites -- never the control.
    """
    for comment_text, tokens in groups:
        for fragment in _QUOTED_FRAGMENT_RE.findall(comment_text):
            if fragment not in doc_text:
                raise AssertionError(
                    f"citation fragment {fragment!r} (tokens {tokens}) not found "
                    "verbatim in lockable-proms.md"
                )


def _readable_groups() -> list[tuple[str, list[str]]]:
    return _extract_citation_groups(
        _read_module_text(), "DOCUMENTED_READABLE_TOKENS: frozenset[str] = frozenset("
    )


def _not_readable_groups() -> list[tuple[str, list[str]]]:
    return _extract_citation_groups(
        _read_module_text(),
        "DOCUMENTED_NOT_READABLE_TOKENS: frozenset[str] = frozenset(",
    )


def test_every_curated_token_has_a_citation_comment() -> None:
    """Leg 1: every token in either frozenset is cited by a comment block."""
    cited_readable = {t for _, toks in _readable_groups() for t in toks}
    cited_not_readable = {t for _, toks in _not_readable_groups() for t in toks}

    uncited_readable = DOCUMENTED_READABLE_TOKENS - cited_readable
    uncited_not_readable = DOCUMENTED_NOT_READABLE_TOKENS - cited_not_readable
    assert not uncited_readable, (
        f"uncited documented-readable tokens: {sorted(uncited_readable)}"
    )
    assert not uncited_not_readable, (
        f"uncited documented-not-readable tokens: {sorted(uncited_not_readable)}"
    )

    # And the reverse: every cited token is actually a member of the
    # frozenset it was cited under (catches a citation for a token that was
    # since removed from the display it claims to cover).
    stray_readable = cited_readable - DOCUMENTED_READABLE_TOKENS
    stray_not_readable = cited_not_readable - DOCUMENTED_NOT_READABLE_TOKENS
    assert not stray_readable, (
        f"cited but not in DOCUMENTED_READABLE_TOKENS: {sorted(stray_readable)}"
    )
    assert not stray_not_readable, (
        f"cited but not in DOCUMENTED_NOT_READABLE_TOKENS: {sorted(stray_not_readable)}"
    )


def test_every_quoted_citation_fragment_resolves_in_the_doc() -> None:
    """Leg 2: every quoted row-key fragment is present verbatim in the doc."""
    doc_text = _read_doc_text()
    _assert_all_fragments_resolve(_readable_groups(), doc_text)
    _assert_all_fragments_resolve(_not_readable_groups(), doc_text)


def test_every_readable_citation_has_line_and_section_reference() -> None:
    """Leg 3: a documented-readable citation names both a line and a §section."""
    for comment_text, tokens in _readable_groups():
        assert _LINE_REF_RE.search(comment_text), (
            f"citation for {tokens} has no ':NNN' line reference: {comment_text!r}"
        )
        assert _SECTION_REF_RE.search(comment_text), (
            f"citation for {tokens} has no '§N' section reference: {comment_text!r}"
        )


def test_mechanism_and_permanence_keys_and_values_are_well_formed() -> None:
    """Leg 4: MECHANISM_BY_TOKEN / PERMANENCE_BY_TOKEN are well-scoped mappings."""
    union = DOCUMENTED_READABLE_TOKENS | DOCUMENTED_NOT_READABLE_TOKENS

    mech_keys = set(MECHANISM_BY_TOKEN)
    perm_keys = set(PERMANENCE_BY_TOKEN)
    assert mech_keys <= union, (
        f"MECHANISM_BY_TOKEN keys outside the curated union: {sorted(mech_keys - union)}"
    )
    assert perm_keys <= union, (
        f"PERMANENCE_BY_TOKEN keys outside the curated union: {sorted(perm_keys - union)}"
    )

    bad_mech_values = {
        v for v in MECHANISM_BY_TOKEN.values() if v not in MECHANISM_STATES
    }
    bad_perm_values = {
        v for v in PERMANENCE_BY_TOKEN.values() if v not in PERMANENCE_STATES
    }
    assert not bad_mech_values, (
        f"MECHANISM_BY_TOKEN values outside MECHANISM_STATES: {bad_mech_values}"
    )
    assert not bad_perm_values, (
        f"PERMANENCE_BY_TOKEN values outside PERMANENCE_STATES: {bad_perm_values}"
    )


def test_citation_resolution_non_vacuity_control() -> None:
    """Leg 5: the leg-2 checker actually fails on a fabricated fragment.

    Mirrors `test_sdp_db_invariant.py::
    test_partition_flags_a_moved_chip_via_db_field_non_vacuous`'s shape: build
    a synthetic two-row citation text -- one fabricated row that is not in
    the document, one untouched control -- and prove the checker raises
    naming the fabricated fragment and not the control.
    """
    fixture_text = """
FIXTURE_TOKENS: frozenset[str] = frozenset(
    {
        # CONTROL -- lockable-proms.md:21 §1 "W29C020 / W29C020C"
        "CONTROLTOKEN",
        # FABRICATED -- lockable-proms.md:999 §1 "THIS ROW KEY DOES NOT EXIST ANYWHERE"
        "FABRICATEDTOKEN",
    }
)
"""
    groups = _extract_citation_groups(
        fixture_text, "FIXTURE_TOKENS: frozenset[str] = frozenset("
    )

    # Fixture setup assertion, checked first per the plan's instruction.
    assert groups == [
        ('CONTROL -- lockable-proms.md:21 §1 "W29C020 / W29C020C"', ["CONTROLTOKEN"]),
        (
            'FABRICATED -- lockable-proms.md:999 §1 "THIS ROW KEY DOES NOT EXIST ANYWHERE"',
            ["FABRICATEDTOKEN"],
        ),
    ], f"Fixture setup error: unexpected groups {groups!r}"

    doc_text = _read_doc_text()
    # The control fragment must actually resolve, or this control proves
    # nothing about the checker distinguishing fabricated from real.
    assert "W29C020 / W29C020C" in doc_text, (
        "Fixture setup error: control fragment not in doc"
    )
    assert "THIS ROW KEY DOES NOT EXIST ANYWHERE" not in doc_text, (
        "Fixture setup error: fabricated fragment unexpectedly present in doc"
    )

    with pytest.raises(AssertionError) as exc_info:
        _assert_all_fragments_resolve(groups, doc_text)

    message = str(exc_info.value)
    assert "THIS ROW KEY DOES NOT EXIST ANYWHERE" in message, (
        f"non-vacuity control did not name the fabricated fragment: {message!r}"
    )
    assert "W29C020 / W29C020C" not in message, (
        f"non-vacuity control wrongly implicated the control fragment: {message!r}"
    )


def test_ambiguous_doc_citations_non_empty_and_scoped() -> None:
    """Leg 6: the C-17 ambiguity record is populated and scoped to curated tokens."""
    union = DOCUMENTED_READABLE_TOKENS | DOCUMENTED_NOT_READABLE_TOKENS
    assert AMBIGUOUS_DOC_CITATIONS, "AMBIGUOUS_DOC_CITATIONS must not be empty"
    assert "W29C020" in AMBIGUOUS_DOC_CITATIONS
    stray_keys = set(AMBIGUOUS_DOC_CITATIONS) - union
    assert not stray_keys, (
        f"AMBIGUOUS_DOC_CITATIONS keys outside the curated union: {sorted(stray_keys)}"
    )
