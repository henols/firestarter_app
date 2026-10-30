"""The curated protection-readability tables are well-scoped.

Every key of MECHANISM_BY_TOKEN, PERMANENCE_BY_TOKEN and
AMBIGUOUS_DOC_CITATIONS is a curated token, and every value is a known state.
No test here asserts that a readability verdict is correct.
"""

from __future__ import annotations

from firestarter.protection_readability import (
    AMBIGUOUS_DOC_CITATIONS,
    DOCUMENTED_NOT_READABLE_TOKENS,
    DOCUMENTED_READABLE_TOKENS,
    MECHANISM_BY_TOKEN,
    MECHANISM_STATES,
    PERMANENCE_BY_TOKEN,
    PERMANENCE_STATES,
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
