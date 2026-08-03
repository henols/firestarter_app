"""
DB invariant for the AT28C SDP `0x0D` identity gate (Phase 116 TRACE-05) and
the anti-narrowing partition gate (Phase 131 Plan 03, GATE-08).

Reads firestarter/data/chip_database.json directly (not through EpromDatabase),
so this measures the shipped data rather than the loader's interpretation.

Coverage:
  1. Real-DB count: exactly 84 chip_database.json entries have
     programming.algorithm == 13 (the 0x0D / EEPROM_PARALLEL dispatch bucket).
  2. Per-element invariant: every one of those 84 entries has
     programming.chip_id_check is False (identity comparison, not merely falsy).
  3. Companion fact: every one of those 84 entries has
     programming.chip_id_value == "0x00000000" -- the field that actually
     determines firmware behaviour (eeprom_28c.cpp's identity branch only
     fires when handle->chip_id > 0).
  4. Non-vacuous proof: a synthetic in-memory DB dict with one algorithm==13
     chip carrying chip_id_check: True IS flagged by the same shared helper
     the real test calls -- proves the invariant is capable of failing, not a
     vacuous always-pass check (RESEARCH F9's "hollow in one direction"
     warning).
  5. Anti-narrowing element-wise parity: the measured 0x0D ALLOW set (via
     `sdp_capability_for_entry`) equals the committed
     `_COMMITTED_SDP_ALLOW_ENTRIES` snapshot exactly, element for element --
     naming any chip that moved in either direction. This is GATE-08 / D-06 /
     correction F-01's replacement for the (not implementable in this repo,
     see the constant's comment) independently-derived leg.
  6. Anti-narrowing literal triple: the measured partition is exactly
     43 ALLOW / 41 REFUSE / 84 total, with all three counts derived from
     `_partition_0x0d`, never hardcoded a second time.
  7. Non-vacuous proof for the narrowing gate: a synthetic chip moved out of
     ALLOW (renamed to a token `SDP_CAPABLE_TOKENS` does not recognise) MUST
     make `_assert_partition_matches_committed` raise, and the raised message
     MUST name the moved chip -- proves the narrowing gate is capable of
     catching P-10's hole, not a vacuous always-pass check.

This module intentionally carries NO FW_ABSENT-style skip marker: it reads
only the packaged chip_database.json, which is always present in host-only
CI. Keeping this concern in its own file (separate from
test_sdp_bus_config_drift.py's FW_ABSENT-marked tests) prevents that skip
marker from leaking in here and silently making TRACE-05 / GATE-08 vacuous in
CI (correction F-02: `test_sdp_table_parity.py` imports the sibling-repo
presence marker from `tests.fw_presence` at module scope and is therefore
skipped whole-module under the CI-parity recipe's empty-sibling leg -- any
narrowing gate placed there would be invisible exactly where it matters
most).
"""

import json
from pathlib import Path

from firestarter.sdp_capability import sdp_capability_for_entry

# Absolute path to the firestarter_app directory (independent of cwd)
_FA_DIR = Path(__file__).parent.parent
_DB_FILE = _FA_DIR / "firestarter" / "data" / "chip_database.json"

# Upstream protocol_id / firmware dispatch key for configure_eeprom28c (0x0D).
_ALGORITHM_0X0D = 13

# ---------------------------------------------------------------------------
# Shared helpers -- both the real-DB tests and the non-vacuity tests call
# these, so the non-vacuity legs exercise the same code the real tests do.
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


def _assert_chip_id_check_false(selected: list[tuple[str, dict]]) -> None:
    """Raise AssertionError naming every offending chip if any selected
    entry's programming.chip_id_check is not exactly False."""
    offenders = [
        f"{mfr}/{chip.get('part_number', '?')}"
        for mfr, chip in selected
        if chip["programming"]["chip_id_check"] is not False
    ]
    assert not offenders, (
        "TRACE-05: every algorithm==13 (0x0D) chip must carry "
        "chip_id_check: false -- the identity gate must be provably dead "
        f"across the whole 0x0D bucket. Offending chips: {offenders}"
    )


def _partition_0x0d(db: dict) -> tuple[list[str], list[str]]:
    """Partition every algorithm==13 (0x0D) chip in `db` into ALLOW/REFUSE
    lists of `"MANUFACTURER/PART_NUMBER"` keys, using the production
    predicate `sdp_capability_for_entry` -- never a reimplementation.

    Key is manufacturer-qualified, not part-number-only: three ALLOW part
    numbers are duplicated across manufacturers in the shipped DB --
    `M28010`, `M28C64,M28C64A` and `M28C64-xxW` each appear under both
    `SGS-THOMSON` and `ST` -- so a part-number-only key would collide and
    silently lose entries.

    Bridges the DB-file shape to the predicate's shape: `chip_database.json`
    carries `part_number` and `programming.algorithm`; `sdp_capability_for_entry`
    reads `name` and `protocol-id`. This module deliberately reads the shipped
    JSON directly rather than through `EpromDatabase` (see the module
    docstring), so the synthesis below is the bridge -- and it is faithful:
    `EpromDatabase.get_eprom("AT28C256")` was measured (2026-08-03) to return
    `name` equal to the raw `part_number` string and `protocol-id` equal to
    `programming.algorithm`, so the synthesized entry matches the production
    path for both fields the predicate reads.

    Both returned lists are sorted.
    """
    selected = _select_0x0d_chips(db)
    allow: list[str] = []
    refuse: list[str] = []
    for mfr, chip in selected:
        part_number = chip["part_number"]
        entry = {
            "protocol-id": chip["programming"]["algorithm"],
            "name": part_number,
        }
        allowed, _reason = sdp_capability_for_entry(entry, part_number)
        key = f"{mfr}/{part_number}"
        (allow if allowed else refuse).append(key)
    return sorted(allow), sorted(refuse)


def _assert_partition_matches_committed(
    measured_allow: list[str], committed_allow: tuple[str, ...]
) -> None:
    """Raise AssertionError naming both directions of the symmetric
    difference between `measured_allow` and `committed_allow`.

    Direction matters (P-10): a chip present in `committed_allow` but absent
    from `measured_allow` LEFT the allow-set -- the narrowing signal this
    gate exists to catch. A chip present in `measured_allow` but absent from
    `committed_allow` ENTERED it -- the widening signal
    `tools/check_sdp_capability_invariants.py` already gates elsewhere.
    Named separately so a reader can tell which happened at a glance.
    """
    measured_set = set(measured_allow)
    committed_set = set(committed_allow)
    left_allow = sorted(committed_set - measured_set)
    entered_allow = sorted(measured_set - committed_set)
    offenders = left_allow or entered_allow
    assert not offenders, (
        "GATE-08: the measured 0x0D ALLOW partition no longer matches the "
        "committed snapshot. A chip may move ALLOW->REFUSE only with a "
        "decode reason (its flags bit changed, or the decode was wrong) -- "
        "NEVER with a test-outcome reason (narrowing the allow-set to green "
        "a failing field report retires the only evidence path this "
        f"feature has). Left ALLOW (narrowing signal): {left_allow}. "
        f"Entered ALLOW (widening signal): {entered_allow}."
    )


# ---------------------------------------------------------------------------
# The committed ALLOW snapshot -- GATE-08 / D-06 / correction F-01.
#
# What it is: the ALLOW half of the `0x0D` SDP partition, snapshotted
# Phase 131 plan 131-03, measured 43 of 84. Prior value: none, first
# snapshot.
#
# Why it is a committed snapshot and not a derivation: D-06 leg 1 asked for
# the partition to be recomputed from `chip_database.json` plus the
# committed `flags` bit-15 decode and compared against what
# `sdp_capability()` computes. That is not implementable in this repo,
# measured 2026-08-03: `chip_database.json` contains ZERO occurrences of the
# string "flags" (no per-chip protection metadata is shipped), and
# `tools/infoic*.xml` -- the bit-15 source -- is gitignored
# (`.gitignore:29`, pattern `tools/infoic*.xml`) and absent from the working
# tree. Implementing leg 1 literally would recompute the partition using the
# very function under test -- self-parity, which passes whenever both sides
# drift together, and which is precisely the hole this gate exists to close
# (correction F-01, PITFALLS P-10). So the independent side here is instead
# a committed, sorted, manufacturer-qualified 43-entry ALLOW list; the
# measured side comes from `_partition_0x0d`, which calls the production
# `sdp_capability_for_entry` predicate.
#
# Change protocol: a chip may move ALLOW->REFUSE ONLY with a decode reason
# -- its `flags` bit changed, or the decode was wrong -- and NEVER with a
# test-outcome reason. Narrowing this list to green a failing field report
# (e.g. a community `dev test` FAIL on an AT28C part) converts a real
# finding into an `NA` step at exit 0 and quietly retires the only evidence
# path this feature has. The widening counterpart to this gate already
# exists: `tools/check_sdp_capability_invariants.py` plus
# `tests/fixtures/planted_widenable_allowset.py`.
_COMMITTED_SDP_ALLOW_ENTRIES: tuple[str, ...] = (
    "ATMEL/AT28BV256,AT28LV256",
    "ATMEL/AT28BV64B,AT28LV64B",
    "ATMEL/AT28C010,AT28C010E",
    "ATMEL/AT28C040,AT28C040E",
    "ATMEL/AT28C256,AT28C256E,AT28C256F,AT28HC256,AT28HC256E,AT28HC256F,AT28HC256L",
    "ATMEL/AT28C64B,AT28HC64B,AT28HC64BF",
    "ATMEL/AT28LV010",
    "ATMEL/AT28MC010",
    "ATMEL/AT28MC020",
    "ATMEL/AT28MC040",
    "CATALYST(CSI)/CAT28C010",
    "CATALYST(CSI)/CAT28C020",
    "CATALYST(CSI)/CAT28C040",
    "CATALYST(CSI)/CAT28C256,CAT28C257",
    "CATALYST(CSI)/CAT28C512",
    "CATALYST(CSI)/CAT28C64B",
    "CATALYST(CSI)/CAT28LV256",
    "CATALYST(CSI)/CAT28LV64,CAT28LV65",
    "EXEL/XLE28C256,XLS28C256",
    "EXEL/XLE28C64B,XLS28C64B",
    "HITACHI/HN58C256AP",
    "MAXWELL/28C010,28C010T,28C011,28C011T",
    "MICROCHIP memory/28C256,28C256F",
    "MICROCHIP memory/28C64B",
    "NEC/UPD28C256",
    "SAMSUNG/KM28C64",
    "SAMSUNG/KM28C64A,KM28C65A",
    "SGS-THOMSON/M28010",
    "SGS-THOMSON/M28C64,M28C64A",
    "SGS-THOMSON/M28C64-xxW",
    "ST/M28010",
    "ST/M28256",
    "ST/M28C64,M28C64A",
    "ST/M28C64-xxW",
    "ST/M28LV64",
    "WED/WE128K8",
    "WED/WE256K8",
    "WED/WE512K8",
    "WED/WME128K8",
    "XICOR/X28256,X28C256",
    "XICOR/X28C010",
    "XICOR/X28C64(NonStandard),X28HC64(NonStandard)",
    "XICOR/X28C64,X28HC64",
)


# ---------------------------------------------------------------------------
# Test 1: real-DB count
# ---------------------------------------------------------------------------


def test_exactly_84_algorithm_0x0d_entries() -> None:
    """TRACE-05 / CLOSE-01: exactly 84 chip_database.json entries have
    programming.algorithm == 13.

    A count change means a chip was added to or removed from the 0x0D
    bucket and every trace-coverage assumption in this milestone needs
    re-checking. This is also CLOSE-01's "84-chip count unchanged" fact,
    landed six phases early.
    """
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    selected = _select_0x0d_chips(db)
    assert len(selected) == 84, (
        "TRACE-05/CLOSE-01: expected exactly 84 chip_database.json entries "
        f"with programming.algorithm == 13, found {len(selected)}. A count "
        "change means a chip was added to or removed from the 0x0D bucket "
        "-- re-check every Phase 116+ trace-coverage assumption before "
        "proceeding."
    )


# ---------------------------------------------------------------------------
# Test 2: per-element chip_id_check invariant
# ---------------------------------------------------------------------------


def test_all_0x0d_chips_have_chip_id_check_false() -> None:
    """TRACE-05: every algorithm==13 entry has chip_id_check is False.

    Identity comparison (is False), not merely falsy -- a missing key or a
    None value must not silently satisfy this check.
    """
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    selected = _select_0x0d_chips(db)
    _assert_chip_id_check_false(selected)


# ---------------------------------------------------------------------------
# Test 3: companion fact -- the field that actually gates firmware behaviour
# ---------------------------------------------------------------------------


def test_all_0x0d_chips_have_chip_id_value_zero_sentinel() -> None:
    """Pin the companion fact that makes the identity gate provably dead
    rather than merely disabled.

    Firmware (eeprom_28c.cpp:eeprom28c_write_init) only enters the identity
    branch when handle->chip_id > 0; chip_id_check: false alone doesn't
    prove that condition is unreachable for the 0x0D bucket -- this pins the
    zero sentinel that makes it so.
    """
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    selected = _select_0x0d_chips(db)
    offenders = [
        f"{mfr}/{chip.get('part_number', '?')}"
        for mfr, chip in selected
        if chip["programming"]["chip_id_value"] != "0x00000000"
    ]
    assert not offenders, (
        "TRACE-05: every algorithm==13 (0x0D) chip must carry "
        "chip_id_value: '0x00000000' -- firmware only skips the identity "
        "branch when handle->chip_id > 0 (eeprom_28c.cpp:eeprom28c_write_init). "
        f"Offending chips: {offenders}"
    )


# ---------------------------------------------------------------------------
# Test 4: non-vacuity proof (TRACE-05)
# ---------------------------------------------------------------------------


def test_synthetic_chip_id_check_true_is_flagged_non_vacuous() -> None:
    """Non-vacuity proof: a synthetic algorithm==13 chip with
    chip_id_check: True MUST make the shared helper raise.

    Proves the invariant gate is capable of failing -- not a vacuous
    always-pass check (RESEARCH F9's "hollow in one direction" warning).
    Exercises the exact same _select_0x0d_chips / _assert_chip_id_check_false
    helpers the real-DB test above calls, not a parallel reimplementation.
    """
    synthetic_db = {
        "SYNTHETIC_MFR": [
            {
                "part_number": "SYNTHETIC_0x0D_VIOLATION",
                "programming": {
                    "algorithm": 13,
                    "chip_id_check": True,
                    "chip_id_value": "0x00000000",
                },
            }
        ]
    }
    selected = _select_0x0d_chips(synthetic_db)
    assert len(selected) == 1, "Synthetic fixture setup error: expected 1 selected chip"

    try:
        _assert_chip_id_check_false(selected)
    except AssertionError:
        pass
    else:
        raise AssertionError(
            "Non-vacuity failure: the shared helper did not raise on a "
            "synthetic chip_id_check: True row -- the TRACE-05 invariant "
            "gate is vacuous."
        )


# ---------------------------------------------------------------------------
# Test 5: GATE-08 anti-narrowing element-wise parity
# ---------------------------------------------------------------------------


def test_sdp_partition_matches_committed_allow_list_element_wise() -> None:
    """GATE-08 / D-06 leg 1 (as amended by correction F-01): the measured
    0x0D ALLOW partition must equal `_COMMITTED_SDP_ALLOW_ENTRIES` exactly,
    element for element.

    A chip moving ALLOW->REFUSE (P-10's narrowing-for-convenience hole)
    reddens this leg; the only diff that greens it is a visible edit to the
    named committed constant, governed by the change protocol on it.
    Element-wise is what catches a single chip moving -- a count-only
    assertion (test 6 below) does not.
    """
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    allow, _refuse = _partition_0x0d(db)
    _assert_partition_matches_committed(allow, _COMMITTED_SDP_ALLOW_ENTRIES)


# ---------------------------------------------------------------------------
# Test 6: GATE-08 anti-narrowing literal triple
# ---------------------------------------------------------------------------


def test_sdp_partition_counts_are_43_41_84() -> None:
    """GATE-08 / D-06 leg 2: the measured 0x0D partition is exactly
    43 ALLOW / 41 REFUSE / 84 total, all three measured from
    `_partition_0x0d`, never hardcoded a second time.

    A count change means a chip moved between halves and must be justified
    by a decode reason (see the change-protocol comment on
    `_COMMITTED_SDP_ALLOW_ENTRIES`), never a test-outcome reason.

    The 84 total is deliberately redundant with
    `test_exactly_84_algorithm_0x0d_entries` above: one asserts the bucket
    size from the DB directly, the other asserts the partition sums back to
    it -- a divergence between them means `_partition_0x0d` dropped an
    entry.
    """
    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    allow, refuse = _partition_0x0d(db)
    assert len(allow) == 43, (
        f"GATE-08: expected 43 ALLOW entries, measured {len(allow)}. A count "
        "change means a chip moved between ALLOW and REFUSE and must be "
        "justified by a decode reason, never a test-outcome reason."
    )
    assert len(refuse) == 41, (
        f"GATE-08: expected 41 REFUSE entries, measured {len(refuse)}. A "
        "count change means a chip moved between ALLOW and REFUSE and must "
        "be justified by a decode reason, never a test-outcome reason."
    )
    assert len(allow) + len(refuse) == 84, (
        "GATE-08: ALLOW + REFUSE must sum back to the 84-chip 0x0D bucket "
        f"-- measured {len(allow)} + {len(refuse)} = {len(allow) + len(refuse)}. "
        "A divergence from test_exactly_84_algorithm_0x0d_entries means "
        "_partition_0x0d dropped an entry."
    )


# ---------------------------------------------------------------------------
# Test 7: non-vacuity proof for the narrowing gate (GATE-08's anti-hollow half)
# ---------------------------------------------------------------------------


def test_partition_flags_a_moved_chip_non_vacuous() -> None:
    """Non-vacuity proof: a synthetic chip moved out of ALLOW MUST make
    `_assert_partition_matches_committed` raise, and the message MUST name
    the moved chip.

    Builds a synthetic in-memory DB dict -- never a mutation of the shipped
    file -- with two algorithm==13 chips: one whose part number
    (`AT28C256`, a real ALLOW token) the real partition places in ALLOW, and
    one whose part number (the AT28C16 REFUSE anchor) stays in REFUSE
    throughout, as a control. The ALLOW chip is then "moved" by renaming its
    part number to a token `SDP_CAPABLE_TOKENS` does not recognise, so it
    falls into REFUSE -- exactly P-10's narrowing-for-convenience shape.

    Drives the SAME `_partition_0x0d` / `_assert_partition_matches_committed`
    helpers the real legs (tests 5/6 above) call, with a committed-list
    argument scoped to this synthetic fixture (a snapshot of the partition
    taken BEFORE the move) -- never a parallel reimplementation. Asserting
    only that this raised is not enough (an assertion firing for the wrong
    reason is a defect class this project has already recorded twice); the
    raised message must also name the moved chip specifically, and must NOT
    name the untouched control chip.
    """
    synthetic_db_before_move = {
        "SYNTHETIC_MFR": [
            {"part_number": "AT28C256", "programming": {"algorithm": 13}},
            {
                "part_number": "AT28C16,AT28HC16,AT28HC16L",
                "programming": {"algorithm": 13},
            },
        ]
    }
    allow_before, refuse_before = _partition_0x0d(synthetic_db_before_move)
    assert allow_before == ["SYNTHETIC_MFR/AT28C256"], (
        "Fixture setup error: expected exactly the AT28C256 synthetic chip "
        f"in ALLOW before the move, measured {allow_before!r}"
    )
    assert refuse_before == ["SYNTHETIC_MFR/AT28C16,AT28HC16,AT28HC16L"], (
        "Fixture setup error: expected the control chip in REFUSE before "
        f"the move, measured {refuse_before!r}"
    )
    committed_snapshot = tuple(allow_before)

    synthetic_db_after_move = {
        "SYNTHETIC_MFR": [
            {
                "part_number": "AT28C256_MOVED_TOKEN_NOT_RECOGNISED",
                "programming": {"algorithm": 13},
            },
            {
                "part_number": "AT28C16,AT28HC16,AT28HC16L",
                "programming": {"algorithm": 13},
            },
        ]
    }
    allow_after, _refuse_after = _partition_0x0d(synthetic_db_after_move)
    assert "SYNTHETIC_MFR/AT28C256" not in allow_after, (
        "Fixture setup error: the renamed chip must no longer be recognised "
        "as an ALLOW-token by SDP_CAPABLE_TOKENS"
    )

    try:
        _assert_partition_matches_committed(allow_after, committed_snapshot)
    except AssertionError as exc:
        message = str(exc)
        assert "SYNTHETIC_MFR/AT28C256" in message, (
            "Non-vacuity failure: the raised message does not name the "
            f"moved chip. Message was: {message!r}"
        )
        assert "AT28C16" not in message, (
            "Non-vacuity failure: the raised message names the untouched "
            f"control chip, which never moved. Message was: {message!r}"
        )
    else:
        raise AssertionError(
            "Non-vacuity failure: moving a synthetic chip out of ALLOW did "
            "not make _assert_partition_matches_committed raise -- the "
            "GATE-08 narrowing gate is vacuous."
        )
