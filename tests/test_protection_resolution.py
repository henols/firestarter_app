"""Unit tests for `protection_readability.protection_gate_for_entry` (LOCK-03/04).

Proves, in order:
  1. Algorithm-derived classes, table-driven over the four protocol-id sets.
  2. The `0x34` row (`XICOR/X88C64P,X88C64S`) resolves `not_implemented`, named
     separately, per OD-2 / `151-DESIGN.md` §4.
  3. The `W29C022` named leg -- D-06's own stated acceptance condition.
  4. The C-6 alias-set leg (`W29C040,W29C042`) -- both offending aliases named,
     each with a different readability state.
  5. The mixed third Winbond entry (`W29C010,W29C011,W29C011A,W29EE010,W29EE012`).
  6. Unanimity, both directions, with a non-vacuous fixture-setup control.
  7. No per-token verdict: the function returns one 2-tuple of `str`, never a
     per-token collection.
  8. The three raise controls (`None` entry, missing `protocol-id`, unclassed
     `protocol-id`), each observed non-vacuous.
  9. Purity: repeatable output, no input mutation.
  10. `AMBIGUOUS_DOC_CITATIONS` (C-17) surfaces in the refusal reason.

Assert class/gate tokens, never prose, except where a leg's whole point is
that a specific alias name appears in the reason (legs 3, 4, 5, 10).
"""

from __future__ import annotations

import copy

import pytest

from firestarter.protection_readability import (
    AMBIGUOUS_DOC_CITATIONS,
    CURATION_PROTOCOL_IDS,
    NO_MECHANISM_PROTOCOL_IDS,
    NOT_IMPLEMENTED_PROTOCOL_IDS,
    NOT_READABLE_PROTOCOL_IDS,
    REASON_NOT_IMPLEMENTED,
    protection_gate_for_entry,
)

# ---------------------------------------------------------------------------
# Leg 1: algorithm-derived classes, table-driven.
# ---------------------------------------------------------------------------

_ALGORITHM_DERIVED_CASES: tuple[tuple[int, str], ...] = (
    tuple((pid, "no_mechanism") for pid in sorted(NO_MECHANISM_PROTOCOL_IDS))
    + tuple((pid, "not_implemented") for pid in sorted(NOT_IMPLEMENTED_PROTOCOL_IDS))
    + tuple((pid, "not_readable") for pid in sorted(NOT_READABLE_PROTOCOL_IDS))
)


@pytest.mark.parametrize("protocol_id,expected_token", _ALGORITHM_DERIVED_CASES)
def test_algorithm_derived_classes(protocol_id: int, expected_token: str) -> None:
    """D-09: every protocol id in the three algorithm-derived sets resolves to
    its named class token. Assert on the token only, never the reason text.
    """
    token, _reason = protection_gate_for_entry(
        {"protocol-id": protocol_id, "name": "SYNTHETIC"}, "SYNTHETIC"
    )
    assert token == expected_token


def test_curation_protocol_ids_are_disjoint_from_algorithm_derived_sets() -> None:
    """Sanity fixture-setup check: the four sets partition disjointly, so
    leg 1's table-driven parametrisation and the token-resolution legs below
    can never collide on the same protocol id.
    """
    all_sets = (
        NO_MECHANISM_PROTOCOL_IDS,
        NOT_IMPLEMENTED_PROTOCOL_IDS,
        NOT_READABLE_PROTOCOL_IDS,
        CURATION_PROTOCOL_IDS,
    )
    seen: set[int] = set()
    for s in all_sets:
        assert not (seen & s), (
            f"Fixture setup error: overlapping protocol ids {seen & s}"
        )
        seen |= s


# ---------------------------------------------------------------------------
# Leg 2: 0x34 is not_implemented, named separately (OD-2 / 151-DESIGN.md §4).
# ---------------------------------------------------------------------------


def test_0x34_xicor_x88c64p_resolves_not_implemented() -> None:
    """OD-2: D-09's seven no-mechanism algorithms sum to 405, not 406. The
    406th row is algorithm 0x34 (52) -- `XICOR/X88C64P,X88C64S` -- which
    carries `protect_off_before: true`, so classing it `no_mechanism` would
    assert an absence of mechanism upstream directly contradicts. It resolves
    `not_implemented` instead, making that census 40 (superseding
    VALIDATION.md's 39). The reason must never read as unprotected -- assert
    it contains the not-implemented reason fragment constant.
    """
    token, reason = protection_gate_for_entry(
        {"protocol-id": 0x34, "name": "X88C64P,X88C64S"}, "X88C64P"
    )
    assert token == "not_implemented"
    assert REASON_NOT_IMPLEMENTED in reason
    assert "protected" not in reason
    assert "unprotected" not in reason


def test_0x10_and_0x34_are_distinguished_in_the_not_implemented_reason() -> None:
    """Both members of NOT_IMPLEMENTED_PROTOCOL_IDS share the class token but
    must carry different reasons: 0x10 documents readability this release
    does not implement; 0x34 has no protocol handler at all.
    """
    _token_10, reason_10 = protection_gate_for_entry(
        {"protocol-id": 0x10, "name": "2716"}, "2716"
    )
    _token_34, reason_34 = protection_gate_for_entry(
        {"protocol-id": 0x34, "name": "X88C64P,X88C64S"}, "X88C64P"
    )
    assert reason_10 != reason_34


# ---------------------------------------------------------------------------
# Leg 3: the W29C022 named leg -- D-06's own stated acceptance condition.
# ---------------------------------------------------------------------------


def test_w29c022_named_in_the_refusal() -> None:
    """D-06's own acceptance condition: if the refusal for the
    `W29C020,W29C020C,W29C022` DB entry does not name `W29C022`
    specifically, D-06 is not implemented. `W29C022` appears nowhere in
    `lockable-proms.md`, so it is `undocumented` regardless of how the
    C-17 tiebreak resolves bare `W29C020`.
    """
    token, reason = protection_gate_for_entry(
        {"protocol-id": 5, "name": "W29C020,W29C020C,W29C022"}, "W29C020"
    )
    assert token == "undocumented_alias"
    assert "W29C022" in reason


# ---------------------------------------------------------------------------
# Leg 4: the C-6 alias-set leg.
# ---------------------------------------------------------------------------


def test_c6_w29c040_w29c042_both_named_with_differing_states() -> None:
    """C-6: CONTEXT.md assumed the `W29C040,W29C042` entry refuses on
    W29C040's variant-dependence alone; measured, `W29C042` is a second,
    independent undocumented alias. Both must be named, each with its own
    (different) state annotation.
    """
    token, reason = protection_gate_for_entry(
        {"protocol-id": 5, "name": "W29C040,W29C042"}, "W29C040"
    )
    assert token == "undocumented_alias"
    assert "W29C040" in reason
    assert "W29C042" in reason
    assert "documented-not-readable" in reason
    assert "undocumented" in reason
    # The two annotations must differ -- not just be present.
    w29c040_fragment = reason[reason.index("W29C040") : reason.index("W29C040") + 40]
    w29c042_fragment = reason[reason.index("W29C042") : reason.index("W29C042") + 40]
    assert w29c040_fragment != w29c042_fragment


# ---------------------------------------------------------------------------
# Leg 5: the mixed third Winbond entry.
# ---------------------------------------------------------------------------


def test_mixed_third_winbond_entry_names_undocumented_aliases() -> None:
    """The third measured `0x05` Winbond entry
    (`W29C010,W29C011,W29C011A,W29EE010,W29EE012`) mixes
    documented-not-readable (`W29C010`, `W29EE012`) with undocumented
    (`W29C011`, `W29C011A`, `W29EE010`) tokens -- must refuse as
    `undocumented_alias` and name `W29C011` and `W29C011A`.
    """
    token, reason = protection_gate_for_entry(
        {
            "protocol-id": 5,
            "name": "W29C010,W29C011,W29C011A,W29EE010,W29EE012",
        },
        "W29C010",
    )
    assert token == "undocumented_alias"
    assert "W29C011" in reason
    assert "W29C011A" in reason


# ---------------------------------------------------------------------------
# Leg 6: unanimity, both directions, with a non-vacuous fixture-setup check.
# ---------------------------------------------------------------------------


def test_unanimity_both_directions() -> None:
    """D-06 unanimity: an entry whose every alias is `documented-readable`
    reads permitted; the same entry with one token swapped for an
    `undocumented` one refuses. The fixture setup is asserted first with a
    `Fixture setup error: ...` message so a curation change that removes the
    readable token is distinguishable from a rule failure.
    """
    all_readable_entry = {"protocol-id": 6, "name": "AM29F010,AM29F010B"}
    token, _reason = protection_gate_for_entry(all_readable_entry, "AM29F010")
    assert token == "read_permitted", (
        f"Fixture setup error: expected AM29F010,AM29F010B to be "
        f"read_permitted before the mutation, measured {token!r}"
    )

    mixed_entry = {"protocol-id": 6, "name": "AM29F010,AM29F010B,W29C022"}
    token, reason = protection_gate_for_entry(mixed_entry, "AM29F010")
    assert token == "undocumented_alias", (
        f"Fixture setup error: expected the token swap to introduce an "
        f"undocumented alias, measured {token!r}"
    )
    assert "W29C022" in reason


# ---------------------------------------------------------------------------
# Leg 7: no per-token verdict.
# ---------------------------------------------------------------------------


def test_returns_a_single_2_tuple_never_a_per_token_collection() -> None:
    """One entry, one answer: the return value is a 2-tuple of `str`, never
    a per-token collection, even for a mixed entry where one token would
    individually read permitted.
    """
    result = protection_gate_for_entry(
        {"protocol-id": 5, "name": "W29C020,W29C020C,W29C022"}, "W29C020"
    )
    assert isinstance(result, tuple)
    assert len(result) == 2
    token, reason = result
    assert isinstance(token, str)
    assert isinstance(reason, str)
    # The single returned token is a refusal, not the readable alias's verdict.
    assert token != "read_permitted"


# ---------------------------------------------------------------------------
# Leg 8: the three raise controls.
# ---------------------------------------------------------------------------


def test_none_entry_raises_key_error() -> None:
    with pytest.raises(KeyError):
        protection_gate_for_entry(None, "ANYTHING")


def test_missing_protocol_id_raises_key_error_naming_the_two_functions() -> None:
    with pytest.raises(KeyError) as exc_info:
        protection_gate_for_entry({"name": "X"}, "X")
    message = str(exc_info.value)
    assert "resolve_chip" in message
    assert "convert_to_programmer" in message


def test_unclassed_protocol_id_raises_value_error_naming_the_synthetic_row() -> None:
    """Follows `test_sdp_db_invariant.py`'s control shape: assert the
    synthetic fixture's own shape first, then that the raise names the
    synthetic row and not a real one.
    """
    synthetic_entry = {"protocol-id": 999, "name": "SYNTHETIC_UNCLASSED_ROW"}
    assert synthetic_entry["protocol-id"] not in (
        NO_MECHANISM_PROTOCOL_IDS
        | NOT_IMPLEMENTED_PROTOCOL_IDS
        | NOT_READABLE_PROTOCOL_IDS
        | CURATION_PROTOCOL_IDS
    ), "Fixture setup error: 999 must not already be classed by this module"

    with pytest.raises(ValueError) as exc_info:
        protection_gate_for_entry(synthetic_entry, "SYNTHETIC_UNCLASSED_ROW")
    message = str(exc_info.value)
    assert "999" in message
    assert "SYNTHETIC_UNCLASSED_ROW" in message
    assert "AT28C256" not in message
    assert "W29C020" not in message


# ---------------------------------------------------------------------------
# Leg 9: purity.
# ---------------------------------------------------------------------------


def test_purity_repeatable_and_non_mutating() -> None:
    entry = {"protocol-id": 5, "name": "W29C020,W29C020C,W29C022"}
    entry_before = copy.deepcopy(entry)

    result_1 = protection_gate_for_entry(entry, "W29C020")
    result_2 = protection_gate_for_entry(entry, "W29C020")

    assert result_1 == result_2
    assert entry == entry_before


# ---------------------------------------------------------------------------
# Leg 10: AMBIGUOUS_DOC_CITATIONS (C-17) surfaces in the refusal reason.
# ---------------------------------------------------------------------------


def test_ambiguous_doc_citation_surfaces_in_refusal_reason() -> None:
    """Proves the C-17 record is live rather than inert: for an entry
    containing a token that is a key of `AMBIGUOUS_DOC_CITATIONS`, the
    refusal reason contains a distinctive substring of that token's
    recorded note.
    """
    assert AMBIGUOUS_DOC_CITATIONS, (
        "Fixture setup error: AMBIGUOUS_DOC_CITATIONS must be non-empty "
        "for this leg to be non-vacuous"
    )
    ambiguous_token = next(iter(AMBIGUOUS_DOC_CITATIONS))
    note = AMBIGUOUS_DOC_CITATIONS[ambiguous_token]

    # The worked W29C020,W29C020C,W29C022 entry contains the bare W29C020
    # token that AMBIGUOUS_DOC_CITATIONS records the C-17 disagreement for.
    token, reason = protection_gate_for_entry(
        {"protocol-id": 5, "name": f"{ambiguous_token},W29C020C,W29C022"},
        ambiguous_token,
    )
    assert token == "undocumented_alias"
    # A distinctive substring of the recorded note (its line-reference
    # fragment) must appear in the composed reason.
    distinctive_fragment = "restatement elsewhere in the document"
    assert distinctive_fragment in note, (
        "Fixture setup error: expected note to carry the distinctive "
        f"fragment, measured {note!r}"
    )
    assert distinctive_fragment in reason
