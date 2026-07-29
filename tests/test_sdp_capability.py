"""Core exhaustiveness gate for the SDP capability partition (Phase 120 HOST-04).

Reads firestarter/data/chip_database.json directly (not through EpromDatabase),
so this measures the shipped data rather than the loader's interpretation --
mirroring test_sdp_db_invariant.py's approach.

Coverage:
  1. Totality: the 84 shipped chip_database.json entries with
     programming.algorithm == 13 (protocol 0x0D) partition exactly into
     EXPECTED_ALLOW_PART_NUMBERS (43 pairs / 40 distinct strings) and
     EXPECTED_REFUSE_PART_NUMBERS (41 pairs / 41 distinct strings), with no
     part_number left over and the two sets disjoint.
  2. Token-set equality: comma-splitting all 84 part_number values yields 134
     token instances / 130 distinct uppercased tokens, split 65 ALLOW / 65
     REFUSE with an empty intersection, and the ALLOW-side set equals
     sdp_capability.SDP_CAPABLE_TOKENS exactly -- catching a transcription
     typo between 120-sdp-partition.json and the production module.
  3. Predicate agreement: sdp_capability_for_entry() agrees with the derived
     partition on all 84 real DB entries, using a minimal literal
     {"name": ..., "protocol-id": ...} dict built from the shipped ground
     truth (not db.get_eprom(), which plan 120-05's shape leg separately
     proves produces this same shape for real parts).
  4. Non-vacuity: a synthetic unknown 0x0D entry is refused by both the
     predicate and the totality helper, proving this gate is capable of
     failing rather than a vacuous always-pass check.

This module intentionally carries NO skip marker of any kind (FW_ABSENT or
otherwise): it reads only the packaged chip_database.json, which is always
present in host-only CI. A skip marker here would silently make HOST-04's
partition gate vacuous, exactly the failure mode test_sdp_db_invariant.py's
own module docstring warns against.

The expected partition below is TRANSCRIBED from
120-sdp-partition.json (itself derived from infoic.xml flags bit 15 --
see 120-SDP-PARTITION.md), and is the independent expectation the production
allow-list (sdp_capability.SDP_CAPABLE_TOKENS) is checked against. Production
holds only the ALLOW half; the REFUSE half living here -- not re-derived from
production -- is what makes the gate non-vacuous rather than a tautology that
would pass no matter what SDP_CAPABLE_TOKENS contained.

  5. Named refusal (DIP24_2816): all 19 algorithm==13 entries on pinout
     DIP24_2816 -- the highest-harm group under F-120-01 -- are refused with
     REASON_NOT_CAPABLE, a fact now derived from infoic.xml bit 15 rather
     than asserted on a judgement call.
  6. Named refusal (FRAM): both FRAM parts (FM28V020, MB85R256H) refuse with
     REASON_FRAM present and REASON_NOT_CAPABLE absent, proving the FRAM
     branch fires ahead of the generic allow-list branch.
  7. Named refusal (HOST-04 pre-SDP class): every entry whose token set
     intersects PRE_SDP_NAMED_TOKENS -- exactly 8 entries, spanning two
     pinouts (RESEARCH F-03) -- is refused.
  8. Named refusal (adapter-required): all 9 support_status ==
     "adapter-required" algorithm==13 entries are refused by capability,
     exercising D-08's ordering on the whole population, not a hypothetical
     subset.
  9. Structural invariants: the allow-set contains no adapter-required part
     and no DIP24_2816 part -- consequences of the derivation, never its
     rule (RESEARCH F-03 still holds: DIP28_28C64 splits 15 ALLOW / 20
     REFUSE).
"""

import json
from pathlib import Path

from firestarter import sdp_capability as sdp

# Absolute path to the firestarter_app directory (independent of cwd).
_FA_DIR = Path(__file__).parent.parent
_DB_FILE = _FA_DIR / "firestarter" / "data" / "chip_database.json"

# Upstream protocol_id / firmware dispatch key for configure_eeprom28c (0x0D).
_ALGORITHM_0X0D = 13

# Transcribed from 120-sdp-partition.json's "allow" array. 40 distinct
# strings (not 43) because M28010 / M28C64,M28C64A / M28C64-xxW are each
# listed under both SGS-THOMSON and ST (second-source duplicates); both
# copies land on this same (ALLOW) side.
EXPECTED_ALLOW_PART_NUMBERS: frozenset[str] = frozenset(
    {
        "28C010,28C010T,28C011,28C011T",
        "28C256,28C256F",
        "28C64B",
        "AT28BV256,AT28LV256",
        "AT28BV64B,AT28LV64B",
        "AT28C010,AT28C010E",
        "AT28C040,AT28C040E",
        "AT28C256,AT28C256E,AT28C256F,AT28HC256,AT28HC256E,AT28HC256F,AT28HC256L",
        "AT28C64B,AT28HC64B,AT28HC64BF",
        "AT28LV010",
        "AT28MC010",
        "AT28MC020",
        "AT28MC040",
        "CAT28C010",
        "CAT28C020",
        "CAT28C040",
        "CAT28C256,CAT28C257",
        "CAT28C512",
        "CAT28C64B",
        "CAT28LV256",
        "CAT28LV64,CAT28LV65",
        "HN58C256AP",
        "KM28C64",
        "KM28C64A,KM28C65A",
        "M28010",
        "M28256",
        "M28C64,M28C64A",
        "M28C64-xxW",
        "M28LV64",
        "UPD28C256",
        "WE128K8",
        "WE256K8",
        "WE512K8",
        "WME128K8",
        "X28256,X28C256",
        "X28C010",
        "X28C64(NonStandard),X28HC64(NonStandard)",
        "X28C64,X28HC64",
        "XLE28C256,XLS28C256",
        "XLE28C64B,XLS28C64B",
    }
)

# Transcribed from 120-sdp-partition.json's "refuse" array. 41 distinct
# strings, no duplicated part_number pairs on this side.
EXPECTED_REFUSE_PART_NUMBERS: frozenset[str] = frozenset(
    {
        "2804",
        "2816",
        "2817",
        "28C04A",
        "28C04AF",
        "28C16A",
        "28C16AF",
        "28C17A",
        "28C17AF",
        "28C64A",
        "28C64AF",
        "28LV64A",
        "AM28C16A",
        "AM28C17A",
        "AM28C64A,AM28C64AE,AM28C64B,AM28C64BE",
        "AT28BV64,AT28LV64",
        "AT28C04,AT28HC04",
        "AT28C04E,AT28C04F",
        "AT28C16,AT28HC16,AT28HC16L",
        "AT28C16E,AT28C16F",
        "AT28C17",
        "AT28C17E,AT28C17F",
        "AT28C64,AT28C64B(Non-Standard),AT28HC64,AT28HC64L",
        "AT28C64E,AT28C64F",
        "AT28PC64,AT28PC64E",
        "CAT28C16A,CAT28C16AI",
        "CAT28C17A",
        "CAT28C64A,CAT28C65",
        "FM28V020",
        "MB85R256H",
        "UPD28C04",
        "UPD28C64",
        "X2804A,X2804AI",
        "X2816A",
        "X2816B,X2816C",
        "X2864AP",
        "XL2804A",
        "XL2816A,XLE28C16A,XLS28C16A",
        "XLE2865A,XLS2865A",
        "XLE28C16B,XLS28C16B",
        "XLE28C64A,XLS28C64A",
    }
)


# ---------------------------------------------------------------------------
# Shared helper -- copied in shape from test_sdp_db_invariant.py's
# _select_0x0d_chips, self-contained per that module's own convention.
# ---------------------------------------------------------------------------


def _select_0x0d_chips(db: dict) -> list[tuple[str, dict]]:
    """Select every (manufacturer, chip) pair with programming.algorithm == 13.

    The DB shape is {manufacturer: [chip, ...]}, and the fields live in a
    nested "programming" object. A top-level scan on db (rather than this
    nested per-chip access) finds nothing and would make every downstream
    assertion pass vacuously.
    """
    selected = []
    for _mfr, chips in db.items():
        for chip in chips:
            if chip["programming"]["algorithm"] == _ALGORITHM_0X0D:
                selected.append((_mfr, chip))
    return selected


def _assert_partition_totality(selected: list[tuple[str, dict]]) -> None:
    """Raise AssertionError if `selected` isn't exactly partitioned by the
    expected ALLOW/REFUSE part_number sets.

    Shared by the real-DB leg and the non-vacuity leg so both exercise the
    identical totality check.
    """
    allow_count = 0
    refuse_count = 0
    unrecognised = []
    for mfr, chip in selected:
        part_number = chip.get("part_number", "?")
        if part_number in EXPECTED_ALLOW_PART_NUMBERS:
            allow_count += 1
        elif part_number in EXPECTED_REFUSE_PART_NUMBERS:
            refuse_count += 1
        else:
            unrecognised.append(f"{mfr}/{part_number}")

    assert not unrecognised, (
        "HOST-04: found algorithm==13 (0x0D) part_number value(s) not present "
        f"in either expected partition set: {unrecognised}. A build_db.py "
        "regeneration that adds or renames a 0x0D part must update "
        "120-sdp-partition.json and this table together -- never widen the "
        "allow-list by default."
    )
    assert allow_count == 43, (
        f"HOST-04: expected 43 ALLOW (manufacturer, part_number) pairs, found "
        f"{allow_count}."
    )
    assert refuse_count == 41, (
        f"HOST-04: expected 41 REFUSE (manufacturer, part_number) pairs, found "
        f"{refuse_count}."
    )


# ---------------------------------------------------------------------------
# Leg 1: partition covers exactly the 84 algorithm==13 entries
# ---------------------------------------------------------------------------


def test_partition_covers_exactly_the_84_0x0d_entries() -> None:
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    selected = _select_0x0d_chips(db)
    assert len(selected) == 84, (
        f"HOST-04: expected exactly 84 chip_database.json entries with "
        f"programming.algorithm == 13, found {len(selected)}. A count change "
        "means a chip was added to or removed from the 0x0D bucket -- "
        "re-check 120-sdp-partition.json before proceeding."
    )

    _assert_partition_totality(selected)

    distinct_part_numbers = {chip.get("part_number", "?") for _mfr, chip in selected}
    assert distinct_part_numbers == (
        EXPECTED_ALLOW_PART_NUMBERS | EXPECTED_REFUSE_PART_NUMBERS
    ), (
        "HOST-04: distinct part_number set across the 84 0x0D entries does "
        "not equal the union of the two expected partition sets (expected "
        "81 distinct strings)."
    )
    assert EXPECTED_ALLOW_PART_NUMBERS.isdisjoint(EXPECTED_REFUSE_PART_NUMBERS), (
        "HOST-04: EXPECTED_ALLOW_PART_NUMBERS and EXPECTED_REFUSE_PART_NUMBERS "
        "must be disjoint."
    )


# ---------------------------------------------------------------------------
# Leg 2: allow/refuse token sets are disjoint, total, and match production
# ---------------------------------------------------------------------------


def test_allow_and_refuse_token_sets_are_disjoint_and_total() -> None:
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    selected = _select_0x0d_chips(db)

    all_token_instances: list[str] = []
    allow_tokens: set[str] = set()
    refuse_tokens: set[str] = set()
    for _mfr, chip in selected:
        part_number = chip.get("part_number", "?")
        tokens = sdp.split_part_number_tokens(part_number)
        all_token_instances.extend(tokens)
        if part_number in EXPECTED_ALLOW_PART_NUMBERS:
            allow_tokens.update(tokens)
        elif part_number in EXPECTED_REFUSE_PART_NUMBERS:
            refuse_tokens.update(tokens)

    assert len(all_token_instances) == 134, (
        f"HOST-04: expected 134 total comma-split token instances across the "
        f"84 0x0D entries, found {len(all_token_instances)}."
    )
    distinct_tokens = set(all_token_instances)
    assert len(distinct_tokens) == 130, (
        f"HOST-04: expected 130 distinct uppercased tokens, found "
        f"{len(distinct_tokens)}."
    )
    assert len(allow_tokens) == 65, (
        f"HOST-04: expected 65 distinct ALLOW-side tokens, found {len(allow_tokens)}."
    )
    assert len(refuse_tokens) == 65, (
        f"HOST-04: expected 65 distinct REFUSE-side tokens, found {len(refuse_tokens)}."
    )
    assert allow_tokens.isdisjoint(refuse_tokens), (
        "HOST-04: ALLOW-side and REFUSE-side token sets must be disjoint -- "
        f"overlap: {allow_tokens & refuse_tokens}."
    )
    # The leg that catches a typo in the production transcription: production
    # SDP_CAPABLE_TOKENS must equal the derived allow-side token set exactly.
    assert allow_tokens == sdp.SDP_CAPABLE_TOKENS, (
        "HOST-04: sdp_capability.SDP_CAPABLE_TOKENS does not equal the "
        "allow-side tokens derived from 120-sdp-partition.json. Symmetric "
        f"difference: {allow_tokens ^ sdp.SDP_CAPABLE_TOKENS}."
    )


# ---------------------------------------------------------------------------
# Leg 3: predicate agrees with the derived partition on all 84 entries
# ---------------------------------------------------------------------------


def test_predicate_agrees_with_the_derived_partition_on_all_84_entries() -> None:
    # This leg's subject is the shipped part_number ground truth: a literal
    # {"name": ..., "protocol-id": ...} dict built directly from
    # chip_database.json is correct HERE. It is wrong elsewhere -- plan
    # 120-05's shape leg is the one that proves db.get_eprom() really
    # produces "name" + "protocol-id" for real parts; this leg does not
    # re-prove that.
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    selected = _select_0x0d_chips(db)
    assert len(selected) == 84

    mismatches = []
    for mfr, chip in selected:
        part_number = chip.get("part_number", "?")
        entry = {
            "name": part_number,
            "protocol-id": chip["programming"]["algorithm"],
        }
        allowed, reason = sdp.sdp_capability_for_entry(entry, part_number)
        expected_allowed = part_number in EXPECTED_ALLOW_PART_NUMBERS
        if allowed is not expected_allowed:
            mismatches.append(
                f"{mfr}/{part_number}: predicate={allowed} expected={expected_allowed} reason={reason!r}"  # noqa: E501
            )
        if not reason:
            mismatches.append(f"{mfr}/{part_number}: empty reason string")

    assert not mismatches, (
        "HOST-04: sdp_capability_for_entry disagrees with the derived "
        f"partition on {len(mismatches)} entries:\n" + "\n".join(mismatches)
    )


# ---------------------------------------------------------------------------
# Leg 4: synthetic unknown 0x0D entry is refused, non-vacuously
# ---------------------------------------------------------------------------


def test_synthetic_unknown_0x0d_entry_is_refused_non_vacuous() -> None:
    synthetic_db = {
        "SYNTHETIC_MFR": [
            {
                "part_number": "SYNTHETIC_0X0D_VIOLATION",
                "programming": {"algorithm": _ALGORITHM_0X0D},
            }
        ]
    }
    selected = _select_0x0d_chips(synthetic_db)
    assert len(selected) == 1, "Synthetic fixture setup error: expected 1 selected chip"

    _mfr, chip = selected[0]
    entry = {
        "name": chip["part_number"],
        "protocol-id": chip["programming"]["algorithm"],
    }
    allowed, reason = sdp.sdp_capability_for_entry(entry, chip["part_number"])
    assert not allowed and sdp.REASON_NOT_CAPABLE in reason, reason

    try:
        _assert_partition_totality(selected)
    except AssertionError:
        pass
    else:
        raise AssertionError(
            "Non-vacuity failure: _assert_partition_totality did not raise on "
            "a synthetic part_number not present in either expected "
            "partition set -- the HOST-04 exhaustiveness gate is vacuous."
        )


# ---------------------------------------------------------------------------
# Leg 5: all 19 DIP24_2816 parts are refused
# ---------------------------------------------------------------------------


def test_all_dip24_2816_parts_are_refused() -> None:
    """The highest-harm group under F-120-01 (120-RESEARCH.md), now refused
    wholesale as a *derived* fact: infoic.xml flags bit 15 is 0 on every one
    of these 19 entries, not a curated judgement call."""
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    selected = _select_0x0d_chips(db)

    dip24_2816 = [
        (mfr, chip) for mfr, chip in selected if chip.get("pinout") == "DIP24_2816"
    ]
    assert len(dip24_2816) == 19, (
        "HOST-04: expected exactly 19 algorithm==13 entries on pinout "
        f"DIP24_2816, found {len(dip24_2816)}."
    )

    offenders = []
    for mfr, chip in dip24_2816:
        part_number = chip.get("part_number", "?")
        entry = {"name": part_number, "protocol-id": chip["programming"]["algorithm"]}
        allowed, reason = sdp.sdp_capability_for_entry(entry, part_number)
        if allowed or sdp.REASON_NOT_CAPABLE not in reason:
            offenders.append(
                f"{mfr}/{part_number}: allowed={allowed} reason={reason!r}"
            )

    assert not offenders, (
        "HOST-04: every DIP24_2816 algorithm==13 part must be refused with "
        "REASON_NOT_CAPABLE. Offenders:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# Leg 6: both FRAM parts are refused with the FRAM reason (branch order)
# ---------------------------------------------------------------------------


def test_both_fram_parts_are_refused_with_the_fram_reason() -> None:
    """FM28V020 and MB85R256H both carry electrical.type == "EEPROM" in the
    DB -- nothing in the DB says FRAM -- so no structural rule can find them;
    this is why HOST-04 says the FRAM refusal is "resolved in code" via the
    FRAM_TOKENS branch, which must fire ahead of the generic
    SDP_CAPABLE_TOKENS check (proven here by asserting REASON_NOT_CAPABLE is
    absent from the reason)."""
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    selected = _select_0x0d_chips(db)

    fram = [
        (mfr, chip)
        for mfr, chip in selected
        if chip.get("part_number", "?") in {"FM28V020", "MB85R256H"}
    ]
    assert len(fram) == 2, (
        "HOST-04: expected exactly 2 algorithm==13 FRAM entries (FM28V020, "
        f"MB85R256H), found {len(fram)}."
    )

    offenders = []
    for mfr, chip in fram:
        part_number = chip.get("part_number", "?")
        entry = {"name": part_number, "protocol-id": chip["programming"]["algorithm"]}
        allowed, reason = sdp.sdp_capability_for_entry(entry, part_number)
        if allowed or sdp.REASON_FRAM not in reason or sdp.REASON_NOT_CAPABLE in reason:
            offenders.append(
                f"{mfr}/{part_number}: allowed={allowed} reason={reason!r}"
            )

    assert not offenders, (
        "HOST-04: both FRAM parts must be refused with REASON_FRAM present "
        "and REASON_NOT_CAPABLE absent (proves branch order). Offenders:\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# Leg 7: HOST-04's named pre-SDP class (plus second sources) is refused
# ---------------------------------------------------------------------------


def test_host04_named_pre_sdp_class_is_refused() -> None:
    """2804/2816 sit on DIP24_2816 while 2817 sits on DIP28_28C64 -- the trio
    spans two pinouts and no pinout rule can express it (RESEARCH F-03),
    which is why the token table -- not a structural predicate -- is the
    thing a reviewer must not "simplify" away."""
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    selected = _select_0x0d_chips(db)

    named = [
        (mfr, chip)
        for mfr, chip in selected
        if set(sdp.split_part_number_tokens(chip.get("part_number", "")))
        & sdp.PRE_SDP_NAMED_TOKENS
    ]
    assert len(named) == 8, (
        "HOST-04: expected exactly 8 algorithm==13 entries whose token set "
        f"intersects PRE_SDP_NAMED_TOKENS, found {len(named)}: "
        f"{[chip.get('part_number', '?') for _mfr, chip in named]}."
    )

    offenders = []
    for mfr, chip in named:
        part_number = chip.get("part_number", "?")
        entry = {"name": part_number, "protocol-id": chip["programming"]["algorithm"]}
        allowed, reason = sdp.sdp_capability_for_entry(entry, part_number)
        if allowed:
            offenders.append(
                f"{mfr}/{part_number}: allowed={allowed} reason={reason!r}"
            )

    assert not offenders, (
        "HOST-04: every named pre-SDP-class entry must be refused. "
        "Offenders:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# Leg 8: all 9 adapter-required parts are refused by capability
# ---------------------------------------------------------------------------


def test_all_nine_adapter_required_parts_are_refused_by_capability() -> None:
    """Makes D-08's capability-before-support-status ordering load-bearing on
    all nine adapter-required parts, not a hypothetical subset: an
    adapter-required 0x0D part with no SDP must hear "this part has no SDP"
    rather than "get an adapter", because no adapter would have helped."""
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    selected = _select_0x0d_chips(db)

    adapter_required = [
        (mfr, chip)
        for mfr, chip in selected
        if chip.get("support_status") == "adapter-required"
    ]
    assert len(adapter_required) == 9, (
        "HOST-04: expected exactly 9 algorithm==13 entries with "
        f"support_status == 'adapter-required', found {len(adapter_required)}."
    )

    offenders = []
    for mfr, chip in adapter_required:
        part_number = chip.get("part_number", "?")
        entry = {"name": part_number, "protocol-id": chip["programming"]["algorithm"]}
        allowed, reason = sdp.sdp_capability_for_entry(entry, part_number)
        if allowed:
            offenders.append(
                f"{mfr}/{part_number}: allowed={allowed} reason={reason!r}"
            )

    assert not offenders, (
        "HOST-04: every adapter-required algorithm==13 part must be refused "
        "by capability. Offenders:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# Leg 9: structural invariants -- consequences of the derivation, not its rule
# ---------------------------------------------------------------------------


def test_allow_set_contains_no_adapter_required_and_no_dip24_2816_part() -> None:
    """Two structural invariants the derived partition happens to satisfy --
    consequences of the derivation, never its rule (RESEARCH F-03 still
    holds: DIP28_28C64 splits 15 ALLOW / 20 REFUSE, so no structural rule
    expresses this partition). These exist to catch a careless
    hand-widening of the allow-list table."""
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    selected = _select_0x0d_chips(db)

    offenders = []
    for mfr, chip in selected:
        part_number = chip.get("part_number", "?")
        if part_number not in EXPECTED_ALLOW_PART_NUMBERS:
            continue
        if chip.get("support_status") == "adapter-required":
            offenders.append(
                f"{mfr}/{part_number}: adapter-required part on the allow side"
            )
        if chip.get("pinout") == "DIP24_2816":
            offenders.append(f"{mfr}/{part_number}: DIP24_2816 part on the allow side")

    assert not offenders, (
        "HOST-04: the allow-set must contain no adapter-required part and no "
        "DIP24_2816 part. Offenders:\n" + "\n".join(offenders)
    )
