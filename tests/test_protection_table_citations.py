"""Prove every curated protection-readability citation is well-formed (LOCK-01, Task 3).

No leg in this file asserts that any readability verdict is *correct* -- no
test in this phase can establish that (`151-DESIGN.md` §8's evidence ceiling).
Every leg here asserts only that a verdict carries a citation naming a line
and a section reference.

**168-09 note (2026-08-31):** this module originally also asserted that a
citation's quoted row-key fragment resolved verbatim in the app repository's
own copy of the Lockable PROMs reference (`test_every_quoted_citation_fragment_resolves_in_the_doc`,
plus its non-vacuity control `test_citation_resolution_non_vacuity_control`).
Both legs are deleted together here, since the file they read is deleted as
part of MIGRATE-02 and the second leg is only a control for the first. The
citation-resolves-in-the-doc property they proved is not replaced in this
phase -- see 168-09-SUMMARY.md.
"""

from __future__ import annotations

import re
from pathlib import Path

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


def test_ambiguous_doc_citations_non_empty_and_scoped() -> None:
    """Leg 6: the C-17 ambiguity record is populated and scoped to curated tokens."""
    union = DOCUMENTED_READABLE_TOKENS | DOCUMENTED_NOT_READABLE_TOKENS
    assert AMBIGUOUS_DOC_CITATIONS, "AMBIGUOUS_DOC_CITATIONS must not be empty"
    assert "W29C020" in AMBIGUOUS_DOC_CITATIONS
    stray_keys = set(AMBIGUOUS_DOC_CITATIONS) - union
    assert not stray_keys, (
        f"AMBIGUOUS_DOC_CITATIONS keys outside the curated union: {sorted(stray_keys)}"
    )
