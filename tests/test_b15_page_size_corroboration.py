"""
Measured, in-tree refutation of the "bit 15 (`protect_on_after`) approximates
a page-write family marker" equivalence (Phase 136.1 Plan 03, PROV-06).

`firestarter/sdp_capability.py`'s docstring (`:32-36`) already carries this
claim in prose: "Bit 15 is not a page-write proxy -- it disagrees with
`page_size > 1` on 12 of the 84 entries." That prose has NO enforcing test
today -- this module is that test, added WITHOUT editing `sdp_capability.py`
(Plan 136.1-02 owns that file this wave; this plan deliberately does not
touch it -- `git diff --stat -- firestarter/sdp_capability.py` stays empty
throughout this plan).

**Two independently different methodologies -- named, not assumed to
agree:** Phase 120's original "12 of 84" figure was computed via
cross-token-set matching across possibly-multiple XML `<ic>` entries per
alias (`120-derive-sdp-allowset.py`'s approach: each comma-joined alias in a
`part_number` string may correspond to more than one upstream `<ic>` element,
and Phase 120 matched sets of tokens against each other). This test instead
does a per-row, single-value comparison: for each of the 84 `algorithm==13`
`chip_database.json` entries (one JSON object per row, already collapsing
any upstream multi-`<ic>` grouping during `build_db.py`'s regeneration), it
reads that one row's own `programming.protect_on_after` (bit 15,
Plan 136.1-01) and `programming.infoic_page_size_raw` (raw upstream
page_size, also Plan 136.1-01) fields directly and compares them. These two
methodologies operate on different underlying groupings and could, in
principle, disagree -- this test does not assume they must produce the same
number; it measures fresh and states the result.

**Measured result (2026-08-05, against the current committed
`chip_database.json`): 12 of 84 entries disagree** -- confirming Phase 120's
original figure via this independently different methodology. The two
methodologies happen to agree here; that is a corroborating finding, not a
guaranteed one.

**Every disagreeing chip, named (12 total, `manufacturer/part_number`,
`protect_on_after` / `infoic_page_size_raw`):**

  1. `AMD/AM28C64A,AM28C64AE,AM28C64B,AM28C64BE`               -- False / 32
  2. `ATMEL/AT28PC64,AT28PC64E`                                -- False / 32
  3. `CATALYST(CSI)/CAT28C64A,CAT28C65`                        -- False / 32
  4. `CYPRESS/FM28V020`                                        -- False / 128
  5. `EXEL/XLE2865A,XLS2865A`                                  -- False / 32
  6. `EXEL/XLE28C16B,XLS28C16B`                                -- False / 16
  7. `EXEL/XLE28C64A,XLS28C64A`                                -- False / 64
  8. `FUJITSU/MB85R256H`                                       -- False / 256
  9. `NEC/UPD28C64`                                            -- False / 32
  10. `XICOR/X2816B,X2816C`                                    -- False / 16
  11. `XICOR/X2864AP`                                          -- False / 16
  12. `XICOR/X28C64(NonStandard),X28HC64(NonStandard)`         -- True / 1

Eleven of the twelve disagree in the direction `protect_on_after=False` with
`infoic_page_size_raw>1` (a multi-byte page-write part that bit 15 says has
no SDP command decoder) -- exactly the "reader must not substitute page_size
for b15" hazard PROV-06 names: these parts would be wrongly inferred
SDP-capable if a reader used `page_size > 1` as a stand-in for bit 15. One
disagrees the other way (`X28C64(NonStandard)`: `protect_on_after=True` with
`infoic_page_size_raw=1`, a byte-write part bit 15 says DOES have SDP).
"""

import json
from pathlib import Path

_FA_DIR = Path(__file__).parent.parent
_DB_FILE = _FA_DIR / "firestarter" / "data" / "chip_database.json"

# Upstream protocol_id / firmware dispatch key for configure_eeprom28c
# (0x0D) -- the same 84-entry bucket test_sdp_db_invariant.py's GATE-08
# scopes to. Deliberately duplicated here (not imported from
# test_sdp_db_invariant.py) -- matching this project's house convention
# (see test_lockable_proms_doc_claims.py / this plan's own action) of not
# cross-importing between narrow, single-purpose test modules.
_ALGORITHM_0X0D = 13

# The measured, fresh count of algorithm==13 entries where
# protect_on_after != (infoic_page_size_raw > 1). NOT copied uncritically
# from Phase 120's "12" -- measured directly against the live DB below and
# asserted as this literal (see module docstring for the full named list and
# the two-methodologies discussion).
_EXPECTED_DISAGREEMENT_COUNT = 12


def _select_0x0d_chips(db: dict) -> list[tuple[str, dict]]:
    """Select every (manufacturer, chip) pair with programming.algorithm == 13.

    Deliberately duplicated from test_sdp_db_invariant.py's helper of the
    same name and shape rather than imported -- this module's own house
    convention of staying self-contained.
    """
    selected = []
    for mfr, chips in db.items():
        for chip in chips:
            if chip["programming"]["algorithm"] == _ALGORITHM_0X0D:
                selected.append((mfr, chip))
    return selected


def _find_disagreements(
    pairs: list[tuple[str, bool, int]],
) -> list[str]:
    """Given a list of `(key, protect_on_after, infoic_page_size_raw)`
    triples, return the sorted list of `key`s where
    `protect_on_after != (infoic_page_size_raw > 1)`.

    This is the named counting helper PROV-06 requires: its `len(...)` is
    the disagreement count, and the returned list names every offender --
    never a bare integer with no way to audit which chips it counts.
    """
    return sorted(
        key
        for key, protect_on_after, page_size_raw in pairs
        if protect_on_after != (page_size_raw > 1)
    )


def _load_0x0d_protect_page_pairs() -> list[tuple[str, bool, int]]:
    """Read the real, committed chip_database.json and return
    `(manufacturer/part_number, protect_on_after, infoic_page_size_raw)`
    triples for all 84 algorithm==13 entries.

    Both fields are read with NO `.get()` default -- a missing key must
    raise `KeyError` loudly (a regression check that Plan 136.1-01's fields
    survived intact), never silently read as some fallback value that would
    make this test vacuously pass or fail.
    """
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    selected = _select_0x0d_chips(db)
    pairs = []
    for mfr, chip in selected:
        programming = chip["programming"]
        key = f"{mfr}/{chip['part_number']}"
        protect_on_after = programming["protect_on_after"]
        page_size_raw = programming["infoic_page_size_raw"]
        pairs.append((key, protect_on_after, page_size_raw))
    return pairs


# ---------------------------------------------------------------------------
# Non-vacuity first: prove the counting helper itself can distinguish
# agreement from disagreement, on a small hand-constructed synthetic set,
# BEFORE trusting it against the real 84-entry data (Non-Vacuity Obligation
# #4, 136.1-VALIDATION.md).
# ---------------------------------------------------------------------------


def test_disagreement_helper_non_vacuous_on_hand_counted_synthetic_pairs() -> None:
    """5 hand-constructed (key, protect_on_after, infoic_page_size_raw)
    triples with a hand-counted 2 disagreements. The helper must return
    exactly those 2 keys -- not 0 (which would mean it never fires) and not
    5 (which would mean it always fires) -- proving it genuinely
    distinguishes agreement from disagreement rather than being an
    accidentally-hardcoded or vacuously-correct check.

    Hand count:
      - "AGREE_BYTE_WRITE": protect_on_after=False, page_size_raw=1  -> False == (1>1)=False  -- AGREE
      - "AGREE_PAGE_WRITE": protect_on_after=True,  page_size_raw=32 -> True  == (32>1)=True   -- AGREE
      - "DISAGREE_A":       protect_on_after=False, page_size_raw=32 -> False != (32>1)=True   -- DISAGREE
      - "DISAGREE_B":       protect_on_after=True,  page_size_raw=1  -> True  != (1>1)=False   -- DISAGREE
      - "AGREE_BOUNDARY":   protect_on_after=False, page_size_raw=0  -> False == (0>1)=False   -- AGREE
    """
    synthetic_pairs: list[tuple[str, bool, int]] = [
        ("AGREE_BYTE_WRITE", False, 1),
        ("AGREE_PAGE_WRITE", True, 32),
        ("DISAGREE_A", False, 32),
        ("DISAGREE_B", True, 1),
        ("AGREE_BOUNDARY", False, 0),
    ]
    disagreements = _find_disagreements(synthetic_pairs)
    assert disagreements == ["DISAGREE_A", "DISAGREE_B"], (
        "Non-vacuity check failed: the counting helper must return exactly "
        "the 2 hand-counted disagreeing keys from the synthetic set, no "
        f"more and no fewer. Got: {disagreements}"
    )
    assert len(disagreements) == 2


# ---------------------------------------------------------------------------
# The real, measured refutation against the committed chip_database.json.
# ---------------------------------------------------------------------------


def test_all_84_entries_carry_both_fields_regression_check() -> None:
    """Regression check that Plan 136.1-01's fields survived intact: all 84
    algorithm==13 entries must carry both `protect_on_after` and
    `infoic_page_size_raw` (enforced structurally by
    `_load_0x0d_protect_page_pairs`'s no-`.get()`-default reads raising
    `KeyError` on any missing key -- this test just confirms the load
    itself succeeds and counts 84)."""
    pairs = _load_0x0d_protect_page_pairs()
    assert len(pairs) == 84, (
        f"Expected exactly 84 algorithm==13 entries in chip_database.json, "
        f"found {len(pairs)}."
    )


def test_b15_disagrees_with_page_size_on_measured_count_of_84_entries() -> None:
    """The measured, fresh disagreement count between `protect_on_after`
    (bit 15) and `infoic_page_size_raw > 1` over the real 84-entry
    algorithm==13 bucket. Asserted as the literal MEASURED value (12) --
    see module docstring for the full named list of all 12 disagreeing
    chips and the two-methodologies discussion (this test's per-row
    single-value comparison vs. Phase 120's cross-token-set matching)."""
    pairs = _load_0x0d_protect_page_pairs()
    disagreeing_keys = _find_disagreements(pairs)

    assert len(disagreeing_keys) == _EXPECTED_DISAGREEMENT_COUNT, (
        f"Measured {len(disagreeing_keys)} disagreements between "
        f"protect_on_after and (infoic_page_size_raw > 1) over the 84 "
        f"algorithm==13 entries; expected {_EXPECTED_DISAGREEMENT_COUNT} "
        f"(Phase 120's original figure, re-measured here via an "
        f"independently different per-row methodology -- see this test "
        f"module's docstring). If this count has genuinely changed, do "
        f"NOT force it back to {_EXPECTED_DISAGREEMENT_COUNT} -- update "
        f"this literal to the new measured value and name every "
        f"disagreeing chip in the docstring, reasoning about whether the "
        f"change is a methodology artifact or a real upstream data change. "
        f"Disagreeing keys measured this run: {disagreeing_keys}"
    )

    # Every disagreeing chip must be one of the 12 named explicitly in this
    # module's docstring (an auditable list, not a bare count) -- this
    # cross-check fails loudly if the SET changes even while the COUNT
    # coincidentally stays 12.
    expected_keys = {
        "AMD/AM28C64A,AM28C64AE,AM28C64B,AM28C64BE",
        "ATMEL/AT28PC64,AT28PC64E",
        "CATALYST(CSI)/CAT28C64A,CAT28C65",
        "CYPRESS/FM28V020",
        "EXEL/XLE2865A,XLS2865A",
        "EXEL/XLE28C16B,XLS28C16B",
        "EXEL/XLE28C64A,XLS28C64A",
        "FUJITSU/MB85R256H",
        "NEC/UPD28C64",
        "XICOR/X2816B,X2816C",
        "XICOR/X2864AP",
        "XICOR/X28C64(NonStandard),X28HC64(NonStandard)",
    }
    assert set(disagreeing_keys) == expected_keys, (
        "The SET of disagreeing chips no longer matches this module's "
        "named docstring list, even though the count may still be 12. "
        f"Measured this run: {sorted(disagreeing_keys)}. "
        f"Expected (docstring): {sorted(expected_keys)}. "
        f"Symmetric difference: {set(disagreeing_keys) ^ expected_keys}."
    )


def test_sdp_capability_module_untouched_this_plan() -> None:
    """Structural guard mirroring the plan's own acceptance criterion: this
    test file must never depend on, or require edits to,
    `firestarter/sdp_capability.py` -- Plan 136.1-02 owns that file this
    wave. This test only checks that the module still imports cleanly and
    still carries its existing "12 of the 84" docstring prose unedited (the
    supplementary color this plan deliberately leaves as-is rather than
    creating a same-wave file conflict)."""
    from firestarter import sdp_capability

    assert "12 of the 84" in (sdp_capability.__doc__ or ""), (
        "sdp_capability.py's docstring no longer carries the '12 of the "
        "84' prose this plan deliberately left unedited -- if it changed "
        "for a legitimate reason, this assertion should be updated by "
        "whichever plan makes that change, not silently ignored."
    )
