"""SDP (Software Data Protection) capability predicate for protocol 0x0D.

This module is a static fail-closed allow-list. A 0x0D part a user adds to
`~/.firestarter/database.json` -- merged live, invisible to CI -- is therefore
REFUSED by default rather than silently permitted, because it can never appear
on the list below.

Source: minipro `infoic.xml`, flags bit 15 (MP_PROTECT_AFTER). Nothing reads
that file at runtime or in CI, and it is not committed here: the table below is
a TRANSCRIPTION, not a computation.

Bit 15 is NOT a page-write proxy: it disagrees with `page_size > 1` on
12 of the 84 entries, so do not substitute one for the other.
"""

from __future__ import annotations

# Import purity: the module's top-level import set is a subset of
# {"__future__", "typing"} — no click, no serial, no firestarter.* imports.
# `Mapping` is imported from `typing` (not `collections.abc`) to keep that
# invariant literal; ruff's UP035 (prefer collections.abc) is suppressed
# below since this file has no runtime dependency on the deprecated alias
# beyond a lazily-evaluated annotation (`from __future__ import annotations`).
from typing import Any, Mapping  # noqa: UP035

# The DB's `protocol-id` value (mirrored from `programming.algorithm` by
# `database.py:_map_data`) that identifies the `0x0D` / EEPROM_PARALLEL
# dispatch bucket this predicate is scoped to. Read `protocol-id` from
# `db.get_eprom()`'s output — never `algorithm` from `resolve_chip()` /
# `convert_to_programmer()`, which do not carry this key (RESEARCH F-02, F-06).
SDP_PROTOCOL_ID = 13

# The distinct uppercased alias tokens for the allowed parts, comma-split from
# their part_number strings.
SDP_CAPABLE_TOKENS: frozenset[str] = frozenset(
    {
        # ATMEL
        "AT28BV256",
        "AT28LV256",
        "AT28BV64B",
        "AT28LV64B",
        "AT28C010",
        "AT28C010E",
        "AT28C040",
        "AT28C040E",
        "AT28C256",
        "AT28C256E",
        "AT28C256F",
        "AT28HC256",
        "AT28HC256E",
        "AT28HC256F",
        "AT28HC256L",
        "AT28C64B",
        "AT28HC64B",
        "AT28HC64BF",
        "AT28LV010",
        "AT28MC010",
        "AT28MC020",
        "AT28MC040",
        # CATALYST(CSI)
        "CAT28C010",
        "CAT28C020",
        "CAT28C040",
        "CAT28C256",
        "CAT28C257",
        "CAT28C512",
        "CAT28C64B",
        "CAT28LV256",
        "CAT28LV64",
        "CAT28LV65",
        # EXEL
        "XLE28C256",
        "XLS28C256",
        "XLE28C64B",
        "XLS28C64B",
        # HITACHI
        "HN58C256AP",
        # MAXWELL
        "28C010",
        "28C010T",
        "28C011",
        "28C011T",
        # MICROCHIP memory
        "28C256",
        "28C256F",
        "28C64B",
        # NEC
        "UPD28C256",
        # SAMSUNG
        "KM28C64",
        "KM28C64A",
        "KM28C65A",
        # SGS-THOMSON / ST (second-source duplicates — see docstring above)
        "M28010",
        "M28C64",
        "M28C64A",
        "M28C64-XXW",
        "M28256",
        "M28LV64",
        # WED
        "WE128K8",
        "WE256K8",
        "WE512K8",
        "WME128K8",
        # XICOR
        "X28256",
        "X28C256",
        "X28C010",
        "X28C64(NONSTANDARD)",
        "X28HC64(NONSTANDARD)",
        "X28C64",
        "X28HC64",
    }
)

# Ferroelectric RAM parts. Both carry `electrical.type == "EEPROM"` in the DB
# (nothing in the DB says FRAM), so the existing `etype in ("SRAM", "FRAM")`
# idiom (`eprom_operations.py`'s `_SRAM_PROTO_IDS` short-circuit) is blind to
# them and no structural rule can find them — this is why the contract says
# "resolved in code".
FRAM_TOKENS: frozenset[str] = frozenset({"FM28V020", "MB85R256H"})

# The named pre-SDP class plus its identical-generation second sources.
# `2817` sits on `DIP28_28C64` while `2804`/`2816` sit on `DIP24_2816`, so the
# trio spans two pinouts and no pinout rule can express it (RESEARCH F-03).
PRE_SDP_NAMED_TOKENS: frozenset[str] = frozenset(
    {
        "2804",
        "2816",
        "2817",
        "X2804A",
        "X2804AI",
        "X2816A",
        "X2816B",
        "X2816C",
        "XL2804A",
        "XL2816A",
        "XLE28C16A",
        "XLS28C16A",
    }
)

# Reason-fragment constants — tests assert on these stable substrings rather
# than whole sentences.
REASON_NOT_FOUND = "not found in the chip database"
REASON_WRONG_PROTOCOL = "SDP lock/unlock applies only to protocol 0x0D parallel EEPROMs"
REASON_FRAM = "ferroelectric RAM (FRAM)"
REASON_NOT_CAPABLE = "not on the SDP-capable list"
REASON_ALLOWED = "SDP-capable per infoic.xml INFOIC2PLUS flags bit 15"


def split_part_number_tokens(part_number: str) -> tuple[str, ...]:
    """Comma-split a DB `part_number` string into uppercased alias tokens.

    Rule: key on the exact token as it appears in `part_number`; do **not**
    strip parentheticals — stripping collapses `AT28C64B(Non-Standard)` onto
    the separate `AT28C64B` entry and produces a spurious MIXED verdict
    (`120-SDP-PARTITION.md` §5), and it makes the key not a function of one
    entry.
    """
    return tuple(
        token.strip().upper() for token in part_number.split(",") if token.strip()
    )


def sdp_capability_for_entry(
    entry: Mapping[str, Any] | None, display_name: str
) -> tuple[bool, str]:
    """Decide SDP capability for a `db.get_eprom()`-shaped full entry dict.

    Unanimity rule: if any alias token of `entry["name"]` is not on the
    allow-list, the whole entry is refused — fail-closed, because a single
    DB entry can only be answered once, never token-by-token. For example
    `EXEL/XL2816A,XLE28C16A,XLS28C16A` refuses as a whole entry even though
    two of its three tokens (`XLE28C16A`, `XLS28C16A`) look like `28C`-
    generation parts that might otherwise seem plausibly SDP-capable — the
    partition's own answer for this entry is REFUSE, and no per-token
    leniency is applied.

    Pure: no serial, no Click, no DB construction, no file I/O.
    """
    if not entry:
        return False, f"{display_name.upper()}: {REASON_NOT_FOUND}"

    if "protocol-id" not in entry:
        raise KeyError(
            f"sdp_capability_for_entry: entry for {display_name.upper()!r} has no "
            "'protocol-id' key. This is very likely the *programmer* dict "
            "returned by resolve_chip()/convert_to_programmer(), which carries "
            "neither 'protocol-id' nor 'name' — pass the full dict returned by "
            "db.get_eprom() instead. A silent default here is exactly how "
            "check_eprom_blank's _SRAM_PROTO_IDS short-circuit became vacuous "
            "in production (RESEARCH F-06); this predicate hard-fails instead."
        )

    protocol_id = entry["protocol-id"]
    if protocol_id != SDP_PROTOCOL_ID:
        return False, (
            f"{display_name.upper()}: {REASON_WRONG_PROTOCOL} "
            f"(observed protocol 0x{protocol_id:02X})"
        )

    name = entry.get("name") or display_name
    tokens = split_part_number_tokens(name)

    if any(token in FRAM_TOKENS for token in tokens):
        return False, (
            f"{display_name.upper()}: {REASON_FRAM} has no EEPROM software-data-"
            "protection command decoder at all; the SDP sequence would be "
            "stored as data rather than recognised as a command."
        )

    unrecognised = [token for token in tokens if token not in SDP_CAPABLE_TOKENS]
    if unrecognised:
        described = [
            f"{token} (pre-SDP generation)"
            if token in PRE_SDP_NAMED_TOKENS
            else f"{token} (unrecognised)"
            for token in unrecognised
        ]
        return False, (
            f"{display_name.upper()}: {REASON_NOT_CAPABLE}: {', '.join(described)}. "
            "Refused fail-closed because the SDP command sequence is not inert "
            "on a part without an SDP command decoder — its bytes are stored "
            "as data at the bus-truncated magic addresses."
        )

    return True, f"{display_name.upper()}: {REASON_ALLOWED}"


def sdp_capability(chip_name: str, db: Any) -> tuple[bool, str]:
    """Name-keyed SDP capability predicate: `sdp_capability_for_entry(db.get_eprom(chip_name), chip_name)`.

    Mechanism correction: an earlier design said "no DB-loader coupling",
    which is **not achievable** — the predicate needs the part number, and
    `resolve_chip`'s programmer dict carries neither `protocol-id` nor `name`
    nor any part number (RESEARCH F-06, measured). The predicate is therefore
    **name-keyed** with an injected `db`; the semantics (pure function, no
    serial, no Click, `-> (allowed, reason)`) are preserved.

    The entry this function evaluates is the entry `db.get_eprom` **actually
    chose** — never the user's typed string — because `get_eprom_config`
    returns the first alias match in DB iteration order and two entries can
    match the same paren-stripped alias.
    """
    return sdp_capability_for_entry(db.get_eprom(chip_name), chip_name)
