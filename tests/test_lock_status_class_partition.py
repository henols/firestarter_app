"""The D-12 invariant: `protection_gate_for_entry` walked over every row of
the committed `chip_database.json` (LOCK-04, plan 151-12).

D-12's whole argument is that LOCK-04 cannot be enforced by careful
authoring -- a prose-level guarantee rots the first time someone edits a
sentence. This file asserts on **class tokens** over the whole database,
never on message text, so it goes red when a new row lands in no class and
is indifferent to wording.

Two legs here are the ones most easily authored vacuously, and both have a
specific non-vacuous form:

  - The exhaustiveness leg (below) was **red by construction** before plan
    `151-06` assigned the `XICOR/X88C64P,X88C64S` row (`algorithm: 52`,
    `0x34`) a class: D-09's seven named no-mechanism algorithms sum to 405,
    not 406, so that row landed in no class under D-09's literal
    enumeration. The red-then-green transcript for this is recorded
    verbatim in `151-12-SUMMARY.md`, produced by temporarily removing `52`
    from `protection_readability.NOT_IMPLEMENTED_PROTOCOL_IDS`, observing
    this leg fail naming that row, then restoring the set and observing it
    pass again. That temporary edit is never committed.
  - The unreachability leg (leg 4) consumes the real planted fixture
    `tests/fixtures/planted_protection_permit_by_default.py`, committed by
    plan `151-09`, which genuinely returns a silicon-only class token from
    the pure path -- proving the leg is capable of failing, not merely an
    absence assertion that would pass trivially.

Coverage (task numbering matches `151-12-PLAN.md`):
  Task 1 (this file's first half):
    1. Exhaustiveness -- all 746 rows resolve into the frozen token set.
    2. Disjointness / determinism -- one token per row, two consecutive
       walks byte-equal.
    3. The census, pinned as literals: `no_mechanism` 405 (with the seven
       per-algorithm counts), `not_implemented` 40 (the `39 at 0x10 + 1 at
       0x34` decomposition, the OD-2 / `151-DESIGN.md` §4 correction that
       supersedes `151-VALIDATION.md`'s earlier figure of 39), the `0x0D`
       84 / curation 24 split of `not_readable`, the `0x05`+`0x06` 217
       surface split 81/24/112 (matching `151-06-SUMMARY.md`'s measured
       distribution exactly), and the total arithmetic
       405 + 40 + 84 + 217 == 746.
  Task 2 (this file's second half):
    4. Structural unreachability of `protected`/`unprotected`, paired with
       the planted fixture routed through the subprocess gate seam.
    6. Robustness: the two key-less TEXAS INSTRUMENTS rows, the ten
       non-`"supported"` rows, and a synthetic novel-algorithm control.
    7. `AMBIGUOUS_DOC_CITATIONS` is live over the real corpus.

**168-09 note (2026-08-31):** this file originally also carried a "Leg 5"
citation-presence gate asserting that a `DOCUMENTED_READABLE_TOKENS`
citation's quoted row-key fragment resolved verbatim in the app repository's
own copy of the Lockable PROMs reference
(`test_every_readable_token_has_a_citation_that_resolves_in_the_doc`). That
leg is deleted here, since the file it read is deleted as part of
MIGRATE-02; the doc-resolution half of the property it proved is not
replaced in this phase -- see 168-09-SUMMARY.md. The citation-presence half
of leg 5 (every documented-readable token has SOME citation comment) is
still covered by `tests/test_protection_table_citations.py`'s own
`test_every_curated_token_has_a_citation_comment`, which asserts the same
property over both curated frozensets.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from firestarter.lock_status import SILICON_ONLY_TOKENS
from firestarter.protection_readability import (
    AMBIGUOUS_DOC_CITATIONS,
    GATE_TOKEN_NO_MECHANISM,
    GATE_TOKEN_NOT_IMPLEMENTED,
    GATE_TOKEN_NOT_READABLE,
    GATE_TOKEN_READ_PERMITTED,
    GATE_TOKEN_UNDOCUMENTED_ALIAS,
    protection_gate_for_entry,
)
from firestarter.sdp_capability import split_part_number_tokens

# Absolute paths, cwd-independent -- mirrors test_sdp_db_invariant.py:74-75
# and test_protection_table_citations.py:27-29.
_FA_DIR = Path(__file__).parent.parent
_DB_FILE = _FA_DIR / "firestarter" / "data" / "chip_database.json"
_MODULE_FILE = _FA_DIR / "firestarter" / "protection_readability.py"

_ALL_GATE_TOKENS = frozenset(
    {
        GATE_TOKEN_NO_MECHANISM,
        GATE_TOKEN_NOT_IMPLEMENTED,
        GATE_TOKEN_NOT_READABLE,
        GATE_TOKEN_UNDOCUMENTED_ALIAS,
        GATE_TOKEN_READ_PERMITTED,
    }
)


# ---------------------------------------------------------------------------
# Shared walk machinery -- every leg below drives this, never a
# reimplementation. `_walk_database_for_class_tokens` never raises itself;
# it collects a failure string per offending row so leg 1's message can name
# every one of them, not just the first. `_resolve_database_or_raise` is the
# thin wrapper that turns "any failures" into one AssertionError -- reused by
# leg 1 (expected clean over the real DB) and leg 6(c) (expected to raise
# over a synthetic DB, naming only the synthetic row).
# ---------------------------------------------------------------------------


class _RowResolution(NamedTuple):
    vendor: str
    part_number: str
    algorithm: int
    token: str
    reason: str


def _load_db() -> dict:
    return json.loads(_DB_FILE.read_text(encoding="utf-8"))


def _all_rows(db: dict) -> list[tuple[str, str, int]]:
    """Every (vendor, part_number, algorithm) triple in `db`, read directly
    off the raw JSON shape -- never through `EpromDatabase`, so this
    measures the shipped data rather than the loader's interpretation
    (mirrors `test_sdp_db_invariant.py`'s own stated rationale)."""
    rows = []
    for vendor, chips in db.items():
        for chip in chips:
            rows.append((vendor, chip["part_number"], chip["programming"]["algorithm"]))
    return rows


def _key(vendor: str, part_number: str) -> str:
    return f"{vendor}/{part_number}"


def _walk_database_for_class_tokens(
    db: dict,
) -> tuple[list[_RowResolution], list[str]]:
    """Drive `protection_gate_for_entry` over every row of `db`, building
    each row's input dict the way `database._map_data` does (`name` ==
    the full comma-separated part number, `protocol-id` == the algorithm
    integer) so this walk exercises the production shape, not a
    hand-rolled one.

    Returns `(resolutions, failures)`. A row that raises is recorded as a
    formatted failure string naming its vendor, part_number and
    protocol-id, and the walk continues -- so a caller can report every
    unresolved row in one message, not just the first.
    """
    resolutions: list[_RowResolution] = []
    failures: list[str] = []
    for vendor, part_number, algorithm in _all_rows(db):
        entry = {"name": part_number, "protocol-id": algorithm}
        display_name = part_number.split(",")[0]
        try:
            token, reason = protection_gate_for_entry(entry, display_name)
        except (KeyError, ValueError) as exc:
            failures.append(
                f"{_key(vendor, part_number)} (protocol-id={algorithm}, "
                f"0x{algorithm:02X}): {exc}"
            )
            continue
        resolutions.append(
            _RowResolution(vendor, part_number, algorithm, token, reason)
        )
    return resolutions, failures


def _resolve_database_or_raise(db: dict) -> list[_RowResolution]:
    """Leg 1's and leg 6(c)'s shared entry point: walk `db` and raise one
    `AssertionError` naming every unresolved row if any exist, otherwise
    return the full resolution list."""
    resolutions, failures = _walk_database_for_class_tokens(db)
    assert not failures, (
        "D-12 leg 1: the following rows did not resolve into the frozen "
        f"class-token set: {failures}"
    )
    return resolutions


# ---------------------------------------------------------------------------
# Leg 1: exhaustiveness
# ---------------------------------------------------------------------------


def test_all_746_rows_resolve_exhaustively() -> None:
    """Every one of the 746 committed database rows resolves into the
    frozen gate-token set, and the walk raises for none of them.

    Non-vacuity: this leg was **observed red** on the `XICOR/X88C64P,X88C64S`
    row before plan `151-06` classed `algorithm: 52`. The red-then-green
    transcript (temporarily removing `52` from `NOT_IMPLEMENTED_PROTOCOL_IDS`,
    observing this leg fail naming that row, then restoring the set and
    observing it pass) is recorded verbatim in `151-12-SUMMARY.md`, per the
    plan's explicit acceptance condition -- "the leg exists" does not
    satisfy it.
    """
    db = _load_db()
    resolutions = _resolve_database_or_raise(db)
    assert len(resolutions) == 746, (
        f"D-12 leg 1: expected 746 resolved rows, measured {len(resolutions)}"
    )
    unknown_tokens = {r.token for r in resolutions} - _ALL_GATE_TOKENS
    assert not unknown_tokens, (
        f"D-12 leg 1: rows resolved to tokens outside the frozen gate-token "
        f"set: {sorted(unknown_tokens)}"
    )


# ---------------------------------------------------------------------------
# Leg 2: disjointness and determinism
# ---------------------------------------------------------------------------


def test_exactly_one_token_per_row() -> None:
    """Every resolved row carries exactly one `(token, reason)` result --
    `protection_gate_for_entry` returns a single 2-tuple, never a
    per-token collection, so this is really asserting the walk never
    double-counts a vendor/part_number key."""
    db = _load_db()
    resolutions = _resolve_database_or_raise(db)
    keys = [_key(r.vendor, r.part_number) for r in resolutions]
    assert len(keys) == len(set(keys)), (
        "D-12 leg 2: the walk produced duplicate vendor/part_number keys, "
        "meaning some row was resolved more than once."
    )


def test_two_consecutive_walks_are_byte_equal() -> None:
    """Two consecutive walks over the same database produce identical
    per-row results -- proves `protection_gate_for_entry` is pure over the
    real 746-row corpus, not merely over the hand-picked cases in
    `test_protection_resolution.py`."""
    db = _load_db()
    first = _resolve_database_or_raise(db)
    second = _resolve_database_or_raise(db)
    assert first == second, (
        "D-12 leg 2: two consecutive walks over the same database produced "
        "different results -- protection_gate_for_entry is not provably "
        "pure over the real corpus."
    )


# ---------------------------------------------------------------------------
# Leg 3: the census, pinned as literals
# ---------------------------------------------------------------------------

# The seven no-mechanism algorithms and their measured per-algorithm row
# counts (D-09). 170 + 127 + 32 + 20 + 2 + 34 + 20 == 405.
_NO_MECHANISM_ALGORITHM_COUNTS: dict[int, int] = {
    0x07: 170,
    0x08: 127,
    0x0B: 32,
    0x0E: 20,
    0x27: 2,
    0x28: 34,
    0x29: 20,
}
_NO_MECHANISM_TOTAL = 405
assert sum(_NO_MECHANISM_ALGORITHM_COUNTS.values()) == _NO_MECHANISM_TOTAL

# not_implemented == 40: 39 at 0x10 (Intel/AMD/Catalyst/ST-family, documented
# readable per lockable-proms.md but this release implements no read for
# 0x10, D-02) plus the single 0x34 row (XICOR/X88C64P,X88C64S -- no protocol
# handler exists at all). This 40 SUPERSEDES `151-VALIDATION.md`'s figure of
# 39 -- that document predates the 0x34 resolution recorded in
# `151-DESIGN.md` §4 / OD-2. Pinned as a SET (not just a count), following
# `test_b15_page_size_corroboration.py:230-243`'s symmetric-difference shape,
# keyed "VENDOR/PART_NUMBER" to match this file's own `_key()`.
_NOT_IMPLEMENTED_KEYS: frozenset[str] = frozenset(
    {
        # -- 0x10 (39 rows) --
        "AMD/AM28F010",
        "AMD/AM28F010A",
        "AMD/AM28F020",
        "AMD/AM28F020A",
        "AMD/AM28F256",
        "AMD/AM28F512",
        "CATALYST(CSI)/CAT28F001P-B",
        "CATALYST(CSI)/CAT28F001P-T",
        "CATALYST(CSI)/CAT28F010",
        "CATALYST(CSI)/CAT28F020",
        "CATALYST(CSI)/CAT28F256",
        "CATALYST(CSI)/CAT28F512",
        "FUJITSU/MBM28F010",
        "HITACHI/HN28F101P,HN28F101FP",
        "INTEL/M28F256",
        "INTEL/P28F001BX-B",
        "INTEL/P28F001BX-T",
        "INTEL/P28F010",
        "INTEL/P28F020",
        "INTEL/P28F256A",
        "INTEL/P28F512",
        "ISSI/IS28F010",
        "ISSI/IS28F020",
        "MACRONIX(MXIC)/MX28F1000P",
        "MACRONIX(MXIC)/MX28F2000P",
        "MACRONIX(MXIC)/MX28F2000T",
        "MITSUBISHI/M5M28F101,M5M28F101A",
        "SGS-THOMSON/M28F101",
        "SGS-THOMSON/M28F201",
        "SGS-THOMSON/M28F256",
        "SGS-THOMSON/M28F512,M28F512B,M28F010",
        "SST/SST28LF040,SST28LF040A,SST28VF040,SST28VF040A",
        "SST/SST28SF040,SST28SF040A",
        "ST/M28F101",
        "ST/M28F201",
        "ST/M28F256",
        "ST/M28F512,M28F512B,M28F010",
        "TI/TMS28F010,TMS28F010A,TMS28F010B",
        "TI/TMS28F020",
        # -- 0x34 (1 row, OD-2) --
        "XICOR/X88C64P,X88C64S",
    }
)
assert len(_NOT_IMPLEMENTED_KEYS) == 40

# The 27 rows at algorithm 0x05 (Winbond/Atmel/SST 5V boot-block family) --
# pinned as a SET, per the plan's instruction that this bucket ("the 0x05
# surface's 27") is small enough to name directly, unlike no_mechanism's 405.
_ALGORITHM_0X05_KEYS: frozenset[str] = frozenset(
    {
        "ASD/AE29F1008",
        "ASD/AE29F2008",
        "ASD/AE29F4008",
        "ATMEL/AT29BV010A,AT29LV010A",
        "ATMEL/AT29BV020,AT29LV020",
        "ATMEL/AT29BV040,AT29LV040",
        "ATMEL/AT29BV040A,AT29LV040A",
        "ATMEL/AT29C010A",
        "ATMEL/AT29C020",
        "ATMEL/AT29C040",
        "ATMEL/AT29C040A",
        "ATMEL/AT29C256",
        "ATMEL/AT29C257",
        "ATMEL/AT29C512",
        "ATMEL/AT29LV256",
        "ATMEL/AT29LV512",
        "SST/SST29EE010",
        "SST/SST29EE020",
        "SST/SST29EE512",
        "SST/SST29LE010,SST29VE010",
        "SST/SST29LE020,SST29VE020",
        "SST/SST29LE512,SST29VE512",
        "WINBOND/W29C010,W29C011,W29C011A,W29EE010,W29EE012",
        "WINBOND/W29C020,W29C020C,W29C022",
        "WINBOND/W29C040,W29C042",
        "WINBOND/W29C512,W29EE512",
        "WINBOND/W29EE011",
    }
)
assert len(_ALGORITHM_0X05_KEYS) == 27

# 151-06-SUMMARY.md's measured gate-token distribution over the 217 0x05+0x06
# entries -- pinned exactly. Per orchestrator constraint 4: if a whole-database
# walk disagrees with these on the 0x05/0x06 subset, that is a stop-and-report
# condition, never a fudge.
_CURATION_READ_PERMITTED = 81
_CURATION_UNDOCUMENTED_ALIAS = 112
_CURATION_NOT_READABLE = 24
_CURATION_SURFACE_TOTAL = 217
assert (
    _CURATION_READ_PERMITTED + _CURATION_UNDOCUMENTED_ALIAS + _CURATION_NOT_READABLE
    == _CURATION_SURFACE_TOTAL
)

_NOT_READABLE_0X0D_TOTAL = 84

_GRAND_TOTAL = 746
assert (
    _NO_MECHANISM_TOTAL + 40 + _NOT_READABLE_0X0D_TOTAL + _CURATION_SURFACE_TOTAL
    == _GRAND_TOTAL
)


def test_no_mechanism_census_405_with_per_algorithm_decomposition() -> None:
    """`no_mechanism` resolves for exactly 405 rows, decomposed by algorithm
    into the seven counts D-09 names: 0x07 (170), 0x08 (127), 0x0B (32),
    0x0E (20), 0x27 (2), 0x28 (34), 0x29 (20). 405 is too large a bucket to
    pin as a set (per the plan's instruction), so this pins the count plus
    the per-algorithm decomposition instead."""
    db = _load_db()
    resolutions = _resolve_database_or_raise(db)
    no_mechanism = [r for r in resolutions if r.token == GATE_TOKEN_NO_MECHANISM]
    assert len(no_mechanism) == _NO_MECHANISM_TOTAL, (
        f"D-12 leg 3: no_mechanism measured {len(no_mechanism)}, expected "
        f"{_NO_MECHANISM_TOTAL}"
    )
    per_algorithm: dict[int, int] = {}
    for r in no_mechanism:
        per_algorithm[r.algorithm] = per_algorithm.get(r.algorithm, 0) + 1
    assert per_algorithm == _NO_MECHANISM_ALGORITHM_COUNTS, (
        f"D-12 leg 3: no_mechanism per-algorithm decomposition measured "
        f"{per_algorithm}, expected {_NO_MECHANISM_ALGORITHM_COUNTS}"
    )


def test_not_implemented_census_40_pinned_as_a_set() -> None:
    """`not_implemented` resolves for exactly 40 rows -- 39 at `0x10` plus
    the single `0x34` row (`151-DESIGN.md` §4, OD-2). This figure
    SUPERSEDES `151-VALIDATION.md`'s earlier figure of 39, because that
    document predates the `0x34` resolution. Pinned as a set with a
    symmetric-difference failure message, following
    `test_b15_page_size_corroboration.py:230-243`'s shape."""
    db = _load_db()
    resolutions = _resolve_database_or_raise(db)
    measured_keys = {
        _key(r.vendor, r.part_number)
        for r in resolutions
        if r.token == GATE_TOKEN_NOT_IMPLEMENTED
    }
    symmetric_difference = measured_keys ^ _NOT_IMPLEMENTED_KEYS
    assert not symmetric_difference, (
        "D-12 leg 3: measured not_implemented rows disagree with the pinned "
        f"40-row set. Symmetric difference: {sorted(symmetric_difference)}"
    )
    assert "XICOR/X88C64P,X88C64S" in measured_keys, (
        "D-12 leg 3: the OD-2 row XICOR/X88C64P,X88C64S must be among the "
        "not_implemented set."
    )


def test_algorithm_0x05_surface_27_pinned_as_a_set() -> None:
    """The 27 rows at algorithm 0x05 are pinned as a set, independent of
    which gate token each one individually resolves to -- this checks DB
    population stability for the bucket the plan names as small enough to
    pin directly ("the 0x05 surface's 27")."""
    db = _load_db()
    measured_keys = {_key(v, p) for v, p, alg in _all_rows(db) if alg == 0x05}
    symmetric_difference = measured_keys ^ _ALGORITHM_0X05_KEYS
    assert not symmetric_difference, (
        "D-12 leg 3: the measured algorithm-0x05 population disagrees with "
        f"the pinned 27-row set. Symmetric difference: "
        f"{sorted(symmetric_difference)}"
    )


def test_not_readable_84_from_0x0d_plus_curation_additions() -> None:
    """`not_readable` resolves for at least the 84 `0x0D` rows -- every one
    of them, since `NOT_READABLE_PROTOCOL_IDS` classifies unconditionally
    by protocol id -- plus whatever the 0x05/0x06 curation adds on top."""
    db = _load_db()
    resolutions = _resolve_database_or_raise(db)
    not_readable = [r for r in resolutions if r.token == GATE_TOKEN_NOT_READABLE]
    from_0x0d = [r for r in not_readable if r.algorithm == 0x0D]
    from_curation = [r for r in not_readable if r.algorithm in (0x05, 0x06)]
    assert len(from_0x0d) == _NOT_READABLE_0X0D_TOTAL, (
        f"D-12 leg 3: not_readable rows at algorithm 0x0D measured "
        f"{len(from_0x0d)}, expected {_NOT_READABLE_0X0D_TOTAL}"
    )
    assert len(from_curation) == _CURATION_NOT_READABLE, (
        f"D-12 leg 3: not_readable rows from 0x05/0x06 curation measured "
        f"{len(from_curation)}, expected {_CURATION_NOT_READABLE}"
    )
    assert len(not_readable) == _NOT_READABLE_0X0D_TOTAL + _CURATION_NOT_READABLE


def test_0x05_0x06_curation_surface_217_matches_151_06_measurement() -> None:
    """The `0x05`+`0x06` surface splits into `read_permitted` /
    `not_readable` / `undocumented_alias` exactly matching the distribution
    `151-06-SUMMARY.md` measured: 81 / 24 / 112, summing to 217. Per
    orchestrator constraint 4, a disagreement here is a stop-and-report
    condition, never adjusted to fit."""
    db = _load_db()
    resolutions = _resolve_database_or_raise(db)
    curation = [r for r in resolutions if r.algorithm in (0x05, 0x06)]
    assert len(curation) == _CURATION_SURFACE_TOTAL, (
        f"D-12 leg 3: 0x05+0x06 row count measured {len(curation)}, expected "
        f"{_CURATION_SURFACE_TOTAL}"
    )
    read_permitted = [r for r in curation if r.token == GATE_TOKEN_READ_PERMITTED]
    not_readable = [r for r in curation if r.token == GATE_TOKEN_NOT_READABLE]
    undocumented = [r for r in curation if r.token == GATE_TOKEN_UNDOCUMENTED_ALIAS]
    assert len(read_permitted) == _CURATION_READ_PERMITTED, (
        f"D-12 leg 3: read_permitted measured {len(read_permitted)}, "
        f"expected {_CURATION_READ_PERMITTED}"
    )
    assert len(not_readable) == _CURATION_NOT_READABLE, (
        f"D-12 leg 3: not_readable-from-curation measured {len(not_readable)}, "
        f"expected {_CURATION_NOT_READABLE}"
    )
    assert len(undocumented) == _CURATION_UNDOCUMENTED_ALIAS, (
        f"D-12 leg 3: undocumented_alias measured {len(undocumented)}, "
        f"expected {_CURATION_UNDOCUMENTED_ALIAS}"
    )
    assert (
        len(read_permitted) + len(not_readable) + len(undocumented)
        == _CURATION_SURFACE_TOTAL
    )
    # No 0x05/0x06 row can resolve no_mechanism or not_implemented -- those
    # tokens are reachable only from the algorithm-derived sets, never the
    # curation set (protection_readability.py's step ordering short-circuits
    # before step 6 for any row whose protocol-id is 5 or 6).
    stray_tokens = {r.token for r in curation} - {
        GATE_TOKEN_READ_PERMITTED,
        GATE_TOKEN_NOT_READABLE,
        GATE_TOKEN_UNDOCUMENTED_ALIAS,
    }
    assert not stray_tokens, (
        f"D-12 leg 3: 0x05/0x06 rows resolved to unexpected tokens: "
        f"{sorted(stray_tokens)}"
    )


def test_total_arithmetic_sums_to_746() -> None:
    """405 + 40 + 84 + 217 == 746, asserted as arithmetic over the ACTUAL
    measured counts (not the literal constants alone), so a future row
    that shifts one bucket without shifting the total is still caught."""
    db = _load_db()
    resolutions = _resolve_database_or_raise(db)
    no_mechanism = sum(1 for r in resolutions if r.token == GATE_TOKEN_NO_MECHANISM)
    not_implemented = sum(
        1 for r in resolutions if r.token == GATE_TOKEN_NOT_IMPLEMENTED
    )
    not_readable = sum(1 for r in resolutions if r.token == GATE_TOKEN_NOT_READABLE)
    read_permitted = sum(1 for r in resolutions if r.token == GATE_TOKEN_READ_PERMITTED)
    undocumented_alias = sum(
        1 for r in resolutions if r.token == GATE_TOKEN_UNDOCUMENTED_ALIAS
    )
    measured_total = (
        no_mechanism
        + not_implemented
        + not_readable
        + read_permitted
        + undocumented_alias
    )
    assert measured_total == _GRAND_TOTAL, (
        f"D-12 leg 3: measured token counts sum to {measured_total}, "
        f"expected {_GRAND_TOTAL}. no_mechanism={no_mechanism} "
        f"not_implemented={not_implemented} not_readable={not_readable} "
        f"read_permitted={read_permitted} undocumented_alias={undocumented_alias}"
    )
    assert no_mechanism == _NO_MECHANISM_TOTAL
    assert not_implemented == 40
    assert not_readable + read_permitted + undocumented_alias == (
        _NOT_READABLE_0X0D_TOTAL + _CURATION_SURFACE_TOTAL
    )


def test_no_row_resolves_to_a_silicon_only_token() -> None:
    """No row, anywhere in the real 746-row database, resolves to
    `protected` or `unprotected` -- the two `SILICON_ONLY_TOKENS`, which
    `protection_gate_for_entry`'s signature is structurally incapable of
    returning (T-151-58)."""
    db = _load_db()
    resolutions = _resolve_database_or_raise(db)
    stray = {r.token for r in resolutions} & SILICON_ONLY_TOKENS
    assert not stray, (
        f"D-12 leg 3: the following silicon-only tokens were reachable from "
        f"the pure database walk: {sorted(stray)}"
    )


# ---------------------------------------------------------------------------
# Leg 4: structural unreachability of the two silicon-only tokens, with a
# planted fixture.
# ---------------------------------------------------------------------------


def test_silicon_only_tokens_never_appear_in_a_return_value_ast() -> None:
    """D-12 leg 4(a): walk `protection_readability.py`'s AST (never grep) and
    assert neither `SILICON_ONLY_TOKENS` literal appears as, or anywhere
    inside, any `Return` node's value.

    This half alone would pass trivially -- the real module was never
    going to contain the literal. `test_planted_fixture_fails_the_gate_seam_
    naming_class1` below is what makes it non-decorative: it proves this
    same rule, applied by `tools/check_protection_readability_invariants.py`,
    is actually capable of failing on a real return of a silicon-only
    token."""
    source = _MODULE_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_MODULE_FILE))
    offending: list[tuple[int, object]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Constant) and sub.value in SILICON_ONLY_TOKENS:
                    offending.append((getattr(sub, "lineno", node.lineno), sub.value))
    assert not offending, (
        "D-12 leg 4(a): silicon-only token literal(s) found inside a Return "
        f"value in protection_readability.py: {offending}"
    )


def _run_protection_readability_checker(
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Mirrors `tests/test_check_protection_readability.py`'s `_run_checker`
    shape: always a real subprocess through the env-override seam, never an
    in-process pytest env-patching fixture -- the seam binds at import
    time."""
    env = {**os.environ, **(env_overrides or {})}
    return subprocess.run(
        [sys.executable, "tools/check_protection_readability_invariants.py"],
        cwd=str(_FA_DIR),
        capture_output=True,
        text=True,
        env=env,
    )


def test_planted_fixture_fails_the_gate_seam_naming_class1() -> None:
    """D-12 leg 4(b), first half: the real planted fixture plan `151-09`
    committed --
    `tests/fixtures/planted_protection_permit_by_default.py` -- genuinely
    returns the silicon-only token `"unprotected"` from a pure-shaped
    `protection_gate_for_entry`-lookalike function, with no membership test
    dominating the return. Routed through
    `FIRESTARTER_PROTECTION_READABILITY_SRC` into
    `tools/check_protection_readability_invariants.py` as a subprocess, it
    must exit non-zero naming Class 1 -- this is the fixture's proof that
    leg 4(a)'s AST rule is a real checkable negative, not a rule that was
    never going to fire."""
    result = _run_protection_readability_checker(
        {
            "FIRESTARTER_PROTECTION_READABILITY_SRC": (
                "tests/fixtures/planted_protection_permit_by_default.py"
            )
        }
    )
    assert result.returncode != 0, (
        "D-12 leg 4(b): the planted permit-by-default fixture must fail the "
        f"gate. stdout: {result.stdout!r} stderr: {result.stderr!r}"
    )
    assert "Class 1" in result.stdout, (
        "D-12 leg 4(b): the gate's failure output must name Class 1. "
        f"stdout: {result.stdout!r}"
    )


def test_real_module_passes_the_same_gate_seam() -> None:
    """D-12 leg 4(b), second half -- the complement that isolates the
    planted fixture's failure above as the actual cause: the REAL
    `protection_readability.py`, routed through the identical seam, must
    exit 0."""
    result = _run_protection_readability_checker()
    assert result.returncode == 0, (
        "D-12 leg 4(b): the real protection_readability.py module must pass "
        f"the gate. stdout: {result.stdout!r} stderr: {result.stderr!r}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Leg 6: robustness controls.
# ---------------------------------------------------------------------------


def test_ti_key_less_rows_resolve_to_no_mechanism_without_raising() -> None:
    """D-12 leg 6(a): the two TEXAS INSTRUMENTS rows (`2516`, `2532`) carry
    NEITHER `protect_on_after` nor `protect_off_before` at all -- the
    744-of-746 exception. `protection_gate_for_entry` never reads either
    field (per its own docstring), so this asserts both rows resolve
    cleanly to `no_mechanism` (both are algorithm `0x0B`) rather than
    raising a `KeyError` on the missing fields."""
    for part_number in ("2516", "2532"):
        token, _reason = protection_gate_for_entry(
            {"name": part_number, "protocol-id": 0x0B}, part_number
        )
        assert token == GATE_TOKEN_NO_MECHANISM, (
            f"D-12 leg 6(a): TEXAS INSTRUMENTS/{part_number} expected "
            f"no_mechanism, measured {token!r}"
        )


def test_ten_non_supported_rows_all_resolve() -> None:
    """D-12 leg 6(b): every row whose `support_status` is not `"supported"`
    -- 10 such rows in the committed database -- still resolves through the
    walk without raising."""
    db = _load_db()
    non_supported = []
    for vendor, chips in db.items():
        for chip in chips:
            if chip.get("support_status") != "supported":
                non_supported.append((vendor, chip["part_number"]))
    assert len(non_supported) == 10, (
        f"D-12 leg 6(b): expected exactly 10 non-'supported' rows, measured "
        f"{len(non_supported)}: {non_supported}"
    )
    resolutions = _resolve_database_or_raise(db)
    resolved_keys = {_key(r.vendor, r.part_number) for r in resolutions}
    for vendor, part_number in non_supported:
        assert _key(vendor, part_number) in resolved_keys, (
            f"D-12 leg 6(b): non-supported row {_key(vendor, part_number)} "
            "did not resolve"
        )


def test_synthetic_novel_algorithm_control_raises_naming_only_itself() -> None:
    """D-12 leg 6(c), the plan's required synthetic-novel-algorithm control:
    a synthetic two-row database, one control row carrying a real,
    already-classed algorithm (`0x10` / `not_implemented`) and one row
    carrying algorithm `999` -- an id genuinely absent from BOTH the
    committed database and every classified protocol-id set (orchestrator
    constraint 5: the control must be genuinely novel, proving the
    partition fails closed on an id nobody has ever classified, not merely
    on a hand-picked bad one).

    Mirrors `test_sdp_db_invariant.py::
    test_partition_flags_a_moved_chip_via_db_field_non_vacuous`'s shape:
    assert the fixture setup first with a `"Fixture setup error: ..."`
    message, then assert the exhaustiveness walk raises, naming the
    synthetic row and not the control.
    """
    control_row = {"part_number": "AM28F010", "programming": {"algorithm": 0x10}}
    control_only_db = {"SYNTHETIC_MFR": [control_row]}
    control_resolutions, control_failures = _walk_database_for_class_tokens(
        control_only_db
    )
    assert not control_failures, (
        "Fixture setup error: the control row SYNTHETIC_MFR/AM28F010 must "
        f"resolve cleanly on its own; measured failures {control_failures!r}"
    )
    assert control_resolutions[0].token == GATE_TOKEN_NOT_IMPLEMENTED, (
        "Fixture setup error: the control row must resolve not_implemented, "
        f"measured {control_resolutions[0].token!r}"
    )

    synthetic_row = {
        "part_number": "SYNTHETIC_NOVEL_ALGORITHM_ROW",
        "programming": {"algorithm": 999},
    }
    mutated_db = {"SYNTHETIC_MFR": [control_row, synthetic_row]}

    try:
        _resolve_database_or_raise(mutated_db)
    except AssertionError as exc:
        message = str(exc)
        assert "SYNTHETIC_NOVEL_ALGORITHM_ROW" in message, (
            "Non-vacuity failure: the raised message does not name the "
            f"synthetic row. Message was: {message!r}"
        )
        assert "AM28F010" not in message, (
            "Non-vacuity failure: the raised message names the untouched "
            f"control row, which never moved. Message was: {message!r}"
        )
    else:
        raise AssertionError(
            "Non-vacuity failure: a synthetic row with algorithm 999 "
            "(absent from every classified protocol-id set and from the "
            "committed database) did not make the exhaustiveness walk raise."
        )


# ---------------------------------------------------------------------------
# Leg 7: the AMBIGUOUS_DOC_CITATIONS record is live over the real corpus.
# ---------------------------------------------------------------------------


def test_ambiguous_doc_citation_reaches_a_real_refusal_reason() -> None:
    """D-12 leg 7: the C-17 `AMBIGUOUS_DOC_CITATIONS` record is live over
    the real corpus, not sitting inert in the module. For every database
    entry containing a token that is a key of `AMBIGUOUS_DOC_CITATIONS`
    (bare `"W29C020"`), the entry must resolve to a refusal token, and the
    refusal's reason must contain a distinctive substring of the recorded
    ambiguity note."""
    assert AMBIGUOUS_DOC_CITATIONS, (
        "D-12 leg 7: AMBIGUOUS_DOC_CITATIONS must be non-empty for this leg "
        "to exercise anything real."
    )
    db = _load_db()
    resolutions = _resolve_database_or_raise(db)

    ambiguous_tokens = set(AMBIGUOUS_DOC_CITATIONS)
    matched_any = False
    distinctive_fragment = "more-restrictive reading wins"
    for r in resolutions:
        row_tokens = set(split_part_number_tokens(r.part_number))
        offending = row_tokens & ambiguous_tokens
        if not offending:
            continue
        matched_any = True
        assert r.token != GATE_TOKEN_READ_PERMITTED, (
            f"D-12 leg 7: {_key(r.vendor, r.part_number)} carries an "
            f"AMBIGUOUS_DOC_CITATIONS token {sorted(offending)} but resolved "
            f"to {GATE_TOKEN_READ_PERMITTED!r}, not a refusal."
        )
        for token in offending:
            note = AMBIGUOUS_DOC_CITATIONS[token]
            assert distinctive_fragment in note, (
                "Fixture setup error: the recorded ambiguity note for "
                f"{token!r} no longer contains the expected distinctive "
                f"fragment {distinctive_fragment!r}: {note!r}"
            )
            assert distinctive_fragment in r.reason, (
                f"D-12 leg 7: the refusal reason for "
                f"{_key(r.vendor, r.part_number)} does not surface the "
                f"recorded ambiguity note's distinctive fragment "
                f"{distinctive_fragment!r}. Reason was: {r.reason!r}"
            )

    assert matched_any, (
        "D-12 leg 7: no real database entry contains an "
        "AMBIGUOUS_DOC_CITATIONS token -- this leg would be vacuous."
    )
