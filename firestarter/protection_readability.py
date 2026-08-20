"""Hand-curated family-level protection-readability table (LOCK-01).

(a) This table is hand-curated, and hand-curation is DATA-04-compliant here rather
than a violation of it, because ``infoic.xml`` was measured and found unable to
supply protection readability at all. `.planning/notes/infoic-xml-protection-
flags-research.md` is the negative result: ``status_readable`` is not derivable
from any ``infoic.xml`` flag, and the sharpest evidence is that **W29C020C** (a
readable, permanently-locking boot block) is flag-identical to **W29EE011**
(SDP-only, unreadable) — two devices this table must tell apart carry the same
upstream bits. The whole AMD Autoselect-readable group additionally carries zero
protection bits of its own. With no machine-derivable axis, a hand transcription
of the one document that *does* state readability, cited row by row, is the
honest option DATA-04 leaves open.

(b) The source axis is ``firestarter_app/doc/lockable-proms.md`` — 399 lines, 126
family rows across 18 numbered sections, each family cited to its own vendor
datasheet where the document names one. Its ``## Key`` section defines the
``Yes—sector`` / ``Yes—global`` / ``Yes—special`` / ``Indirect`` / ``No`` /
``Permanent`` vocabulary this module reads off of, row by row.

(c) The census: the chip database holds 746 rows across 59 vendors. The curation
surface this module covers is 217 entries across algorithms ``0x05`` (27
entries) and ``0x06`` (190 entries), carrying 273 distinct alias tokens. The
other 529 of 746 rows are algorithm-derivable and appear in no frozenset here —
they need no curated token at all.

(d) Nothing reads this module at runtime except ``protection_gate_for_entry``
(plan 151-06, not yet written when this module was authored), and nothing in it
reaches the wire — it is a pure, static, hand-transcribed lookup.

(e) The fail-closed direction: a token present in neither
``DOCUMENTED_READABLE_TOKENS`` nor ``DOCUMENTED_NOT_READABLE_TOKENS`` resolves to
``undocumented`` by complement. Forgetting to curate a token can therefore only
ever produce a refusal downstream, never a silently-granted permission to read
silicon.

(f) Measured reason literal matching cannot build this table by itself: only 10
of the 42 ``0x05`` tokens and 20 of the 231 ``0x06`` tokens appear verbatim
anywhere in ``lockable-proms.md``, because the document writes families in
elided shorthand — ``Am29F010 / F010B``, ``MX29F010 / F020 / F040`` — where a
bare suffix like ``F010B`` is a continuation of the row's shared stem, not an
independent part number. Curating the remaining 243 tokens means recognising
which row's stem a given DB alias continues, or recognising that no row's stem
covers it at all.

(g) The C-17 ambiguity is recorded, not silently resolved: ``lockable-proms.md``'s
row key at line 21 is ``W29C020 / W29C020C`` and covers both parts, but every
restatement of that row elsewhere in the document (lines 30, 335, 350) names
``W29C020C`` only. Per `151-DESIGN.md` §5's named tiebreak rule, the token that
the restatements are silent about — bare ``W29C020`` — takes the **more
restrictive** of the two readings (``documented-not-readable``), and the
disagreement itself is recorded in ``AMBIGUOUS_DOC_CITATIONS`` rather than
erased. This changes nothing about the worked ``W29C020,W29C020C,W29C022`` DB
entry's refusal: ``W29C022`` is undocumented either way, so the entry refuses
regardless of how the ``W29C020`` tiebreak resolves — the tiebreak only changes
how many offending aliases the refusal names.

(h) Negative control: of the 5 ``0x05`` DB entries whose *every* alias token
happens to match the document verbatim (``AT29C020``, ``AT29C040``, ``AT29C256``,
``AT29C512``, ``W29EE011``), four are Atmel ``AT29C*`` parts that
`doc/lockable-proms.md` §15 records as having "No explicit SDP state" — i.e.
documented-**not**-readable. The set of tokens that are easy to locate by exact
string match is not the set of tokens that are readable; the two are close to
disjoint here.
"""

from __future__ import annotations

# Import-purity invariant (mirrors sdp_honesty.py's own invariant comment,
# which names its two permitted modules and asserts both are leaves): this
# module's top-level import set is a subset of {"__future__", "typing",
# "firestarter.sdp_capability"}. The third entry is admitted only because
# `sdp_capability` is itself import-pure ({"__future__", "typing"} only, so
# the set stays a shallow, checkable tree, not an unbounded one) and because
# plan 151-06 needs exactly one name from it — `split_part_number_tokens` —
# whose no-parenthetical-stripping rule is a measured correctness requirement
# (`120-SDP-PARTITION.md` §5) that must not be copied a second time; a second
# copy is precisely the kind of drift this codebase keeps removing. No
# `click`, no `json`, no `pathlib`, no `firestarter.database`, no
# `firestarter.sdp_honesty` — the composed refusal *prose* lives one layer up,
# in `lock_status.py` (plan 151-08+), so `sdp_honesty.py` keeps the caveat
# sentence's single home and this module never depends on it. In this task
# (151-02) the actual import list is only `__future__` and `typing`; 151-06
# adds the `sdp_capability` import when it adds the function that needs it.
# `Mapping` is imported from `typing` (not `collections.abc`) to keep the
# invariant literal; ruff's UP035 (prefer collections.abc) is suppressed below
# since this file has no runtime dependency on the deprecated alias beyond a
# lazily-evaluated annotation (`from __future__ import annotations`).
from typing import Mapping  # noqa: UP035

# D-06's three readability states, frozen. `undocumented` deliberately has no
# backing collection anywhere in this module — it is the complement of the
# union of the two frozensets below, so a token can reach it only by absence,
# never by an explicit assignment that could itself be wrong. Adding a fourth
# state is a decision D-06 forbids; DESIGN.md §5(a) restates this for the C-17
# ambiguity specifically: the tiebreak does not add a state, it only decides
# which of the existing three a disputed token lands in.
READABILITY_STATES: tuple[str, ...] = (
    "documented-readable",
    "documented-not-readable",
    "undocumented",
)

# Reason-fragment constants. Tests assert on these stable substrings rather
# than on whole composed sentences, so a later prose rewording cannot silently
# collapse two distinct reasons into one. The full sentences are composed one
# layer up, in plan 151-06's `protection_gate_for_entry` / plan 151-08's
# `lock_status.py`, not here.
REASON_NOT_FOUND = "not found in the chip database"
REASON_NO_MECHANISM = "has no write-protection mechanism at all"
REASON_NOT_IMPLEMENTED = "documented but this codebase does not read it"
REASON_NOT_READABLE = "documented as not having a readable protection state"
REASON_UNDOCUMENTED_ALIAS = "not documented in lockable-proms.md"
REASON_READ_PERMITTED = "every alias documented-readable; silicon read permitted"

# A **gate** token, not one of D-09's eight output classes — the CLI never
# prints this string. `protection_gate_for_entry` (plan 151-06) returns it
# internally to mean "proceed to the silicon read"; `lock_status.py` (plan
# 151-08+) is the only place D-09's eight class tokens are assembled and
# emitted.
GATE_TOKEN_READ_PERMITTED: str = "read_permitted"

# ---------------------------------------------------------------------------
# The 273-token curated surface: algorithms 0x05 (Winbond/Atmel/SST 5V
# boot-block family, 27 DB entries / 42 tokens) and 0x06 (AMD Autoselect and
# its command-compatible clones, 190 DB entries / 231 tokens). Every token
# below is transcribed from a specific `lockable-proms.md` row, cited by line
# number, document section number (`§N`, matching the document's own "# N. ..."
# headings — there is no separate per-row vendor-datasheet reference for most
# of these rows; where the document does cite one by footnote number (AMD
# `[1]`, Macronix `[4]`/`[5]`, SST `[6]`, Atmel AT29C `[7]`), that footnote
# number is carried in the citation comment too) and the row-key text, quoted
# verbatim (markdown `**bold**` markers stripped) so it can be located in the
# document by substring search.
#
# Suffix-collapsing rule (measured necessity — see module docstring (f)): a
# `lockable-proms.md` row names a family by its shared numeric stem plus a
# short list of explicit suffix continuations (`Am29F010 / F010B`). Where the
# DB's own alias tokens for that same numeric stem carry *additional*
# boot-sector-orientation or revision suffixes the row does not spell out
# (`AM29F002BB`, `AM29F002NBT`, ...), this module extends the row's verdict to
# those additional suffix variants of the *same* numeric stem — never to a
# different numeric stem, and never across a voltage-class change (5 V "F" vs
# low-voltage "LV"/"BV" are always separately curated). This is the same kind
# of judgement `sdp_capability.py`'s docstring describes as "a curator's
# adjudication", made explicit here rather than performed silently.
DOCUMENTED_READABLE_TOKENS: frozenset[str] = frozenset(
    {
        # WINBOND -- lockable-proms.md:21 §1 "W29C020 / W29C020C"
        # Yes-special, boot-block status readable in Product ID mode, permanence Yes.
        # W29C020C matches the row verbatim; see AMBIGUOUS_DOC_CITATIONS for bare W29C020.
        "W29C020C",
        # AMD -- lockable-proms.md:38 §2 "Am29F010 / F010B" [1]
        # Yes-sector (Autoselect Sector Protection Verify), normally reversible.
        "AM29F010",
        "AM29F010B",
        # AMD -- lockable-proms.md:39 §2 "Am29F002 / F002B / F002NB" [1]
        # Yes-sector, boot-sector protection, normally reversible. Boot-orientation
        # suffix variants (BB/BT/T/NBB/NBT/NT) collapsed onto the named base/B/NB stem.
        "AM29F002B",
        "AM29F002BB",
        "AM29F002BT",
        "AM29F002NB",
        "AM29F002NBB",
        "AM29F002NBT",
        "AM29F002NT",
        "AM29F002T",
        # AMD -- lockable-proms.md:41 §2 "Am29F040 / F040B" [1]
        # Yes-sector, normally reversible.
        "AM29F040",
        "AM29F040B",
        # FUJITSU -- lockable-proms.md:67 §3 "MBM29F010 / F020 / F040 / F080"
        # Yes-sector, normally not permanent.
        "MBM29F040",
        # FUJITSU -- lockable-proms.md:68 §3 "MBM29F002 / F200 / F400 / F800"
        # Yes-sector, normally not permanent.
        "MBM29F002B",
        "MBM29F002T",
        # HYNIX / HYUNDAI -- lockable-proms.md:181 §10 "HY29Fxxx"
        # Usually yes-sector, Hynix AMD-compatible families. Explicit document
        # wildcard ("xxx"); §10's table carries no permanence column at all.
        "HY29F002T",
        "HY29F040",
        "HY29F040A",
        "HY29F040T",
        # CFEON / EON -- lockable-proms.md:161 §8 "EN29F010 / F020 / F040 / F080"
        # Yes-sector, normally not permanent.
        "EN29F010",
        "EN29F040",
        "EN29F040A",
        # CFEON / EON -- lockable-proms.md:162 §8 "EN29F002 / F200 / F400 / F800"
        # Yes-sector, normally not permanent. Boot-orientation suffix variants
        # (AB/ANB/ANT/AT/B/NB/NT/T) collapsed onto the named base/200/400/800 stem.
        "EN29F002AB",
        "EN29F002ANB",
        "EN29F002ANT",
        "EN29F002AT",
        "EN29F002B",
        "EN29F002NB",
        "EN29F002NT",
        "EN29F002T",
        # CFEON / EON -- lockable-proms.md:163 §8 "EN29LVxxx"
        # Yes-sector, normally not permanent. Explicit document wildcard.
        "EN29LV040A",
        # MACRONIX(MXIC) -- lockable-proms.md:114 §5 "MX29F010 / F020 / F040" [4]
        # Yes-sector, Sector Protect Verify in Auto Select mode, normally not permanent.
        "MX29F040",
        "MX29F040C",
        # MACRONIX(MXIC) -- lockable-proms.md:115 §5 "MX29F001 / F002" [4]
        # Yes-sector, boot-sector devices, normally not permanent. Suffix variants
        # collapsed onto the named 001/002 stem.
        "MX29F001B",
        "MX29F001T",
        "MX29F002B",
        "MX29F002NB",
        "MX29F002NT",
        "MX29F002T",
        # MACRONIX(MXIC) -- lockable-proms.md:125 §5 "MX29LVxxx" [5]
        # Yes-sector, usually reversible. Explicit document wildcard.
        "MX29LV002CB",
        "MX29LV002CT",
        "MX29LV002NCB",
        "MX29LV002NCT",
        # SGS-THOMSON / ST -- lockable-proms.md:136 §6 "M29F010 / F020 / F040 / F080"
        # Yes-sector, usually reversible.
        "M29F010B",
        "M29F040B",
        # SGS-THOMSON / ST -- lockable-proms.md:137 §6 "M29F002 / F200 / F400 / F800"
        # Yes-sector, usually reversible. Boot-orientation suffix variants
        # (B/BB/BNB/BNT/BT/NT) collapsed onto the named base/200/400/800 stem.
        "M29F002B",
        "M29F002BB",
        "M29F002BNB",
        "M29F002BNT",
        "M29F002BT",
        "M29F002NT",
        "M29F002T",
        # WINBOND -- lockable-proms.md:25 §1 "W49F002 / W49F002U"
        # Yes-sector/special, boot sectors, usually reversible with proper voltage.
        "W49F002",
        "W49F002A",
        "W49F002B",
        "W49F002U",
        # WINBOND -- lockable-proms.md:26 §1 "W49F020"
        # Yes-sector, individual sectors, usually not permanent.
        "W49F020",
        # ATMEL -- lockable-proms.md:283 §16 "AT49BV/LVxxx"
        # Yes-sector/special, sector protection. Explicit document wildcard covering
        # any AT49BV*/AT49LV*-prefixed token; permanence is mixed ("Some lock bits
        # permanent") so recorded as unknown rather than permanent or reversible.
        # Does NOT cover the AT49H-prefixed variants (AT49HBV010, AT49HLV010,
        # AT49HF010) -- the inserted H is not part of this wildcard's literal
        # prefix and no other row names it, so those three tokens are undocumented.
        "AT49BV001",
        "AT49BV001A",
        "AT49BV001AN",
        "AT49BV001ANT",
        "AT49BV001AT",
        "AT49BV001N",
        "AT49BV001NT",
        "AT49BV001T",
        "AT49BV002",
        "AT49BV002A",
        "AT49BV002AN",
        "AT49BV002ANT",
        "AT49BV002AT",
        "AT49BV002N",
        "AT49BV002NT",
        "AT49BV002T",
        "AT49BV010",
        "AT49BV020",
        "AT49BV040",
        "AT49BV040A",
        "AT49BV040B",
        "AT49BV040T",
        "AT49BV512",
        "AT49LV001",
        "AT49LV001N",
        "AT49LV001NT",
        "AT49LV001T",
        "AT49LV002",
        "AT49LV002N",
        "AT49LV002NT",
        "AT49LV002T",
        "AT49LV010",
        "AT49LV020",
        "AT49LV040",
        "AT49LV040T",
        "AT49LV512",
        # ATMEL -- lockable-proms.md:280 §16 "AT49F001 / F002"
        # Yes-special on many variants, boot-block lockout, often permanent. The
        # readability cell bolds the exact section-Key term Yes-special, hedged
        # by on many variants; read here as documented-readable per the literal
        # bolded value. Suffix variants collapsed onto the named 001/002 stem.
        "AT49F001",
        "AT49F001A",
        "AT49F001AN",
        "AT49F001ANT",
        "AT49F001AT",
        "AT49F001N",
        "AT49F001NT",
        "AT49F001T",
        "AT49F002",
        "AT49F002A",
        "AT49F002AN",
        "AT49F002ANT",
        "AT49F002AT",
        "AT49F002N",
        "AT49F002NT",
        "AT49F002T",
    }
)

# The complementary destination: a token whose covering row's readability
# column is `No`, `Indirect`, variant-dependent, or otherwise declines a clean
# readable verdict per §Key. Per `lockable-proms.md:3`, indirect (write→verify)
# methods are explicitly excluded from "readable" -- an `Indirect` row is not
# readable.
DOCUMENTED_NOT_READABLE_TOKENS: frozenset[str] = frozenset(
    {
        # WINBOND -- lockable-proms.md:20 §1 "W29C010 / W29C010M"
        # Usually no for SDP -- a command-sequence requirement, not a readable lock bit.
        "W29C010",
        # WINBOND -- lockable-proms.md:21 §1 "W29C020 / W29C020C"
        # C-17 tiebreak (DESIGN.md §5): the row covers both parts but every
        # restatement (lines 30, 335, 350) names W29C020C only. Bare W29C020 takes
        # the more-restrictive state by rule. See AMBIGUOUS_DOC_CITATIONS.
        "W29C020",
        # WINBOND -- lockable-proms.md:22 §1 "W29C040 / W29C040P"
        # Variant-dependent (D-03): the readable part is W29C020C, not this family.
        "W29C040",
        # WINBOND -- lockable-proms.md:23 §1 "W29EE011 / W29EE012"
        # Usually no for SDP, whole device.
        "W29EE011",
        "W29EE012",
        # ATMEL -- lockable-proms.md:266 §15 "AT29C256"
        # No explicit SDP state.
        "AT29C256",
        # ATMEL -- lockable-proms.md:267 §15 "AT29C512"
        # No explicit SDP state.
        "AT29C512",
        # ATMEL -- lockable-proms.md:268 §15 "AT29C010 / 010A" [7]
        # No explicit SDP state.
        "AT29C010A",
        # ATMEL -- lockable-proms.md:269 §15 "AT29C020 / 020A" [7]
        # No explicit SDP state.
        "AT29C020",
        # ATMEL -- lockable-proms.md:270 §15 "AT29C040 / 040A" [7]
        # No explicit SDP state.
        "AT29C040",
        "AT29C040A",
        # ATMEL -- lockable-proms.md:271 §15 "AT29LV010 / LV020 / LV040"
        # Usually no. LV020/LV040 match the row's bare stems verbatim; the DB's
        # A-suffixed siblings (AT29LV010A, AT29LV040A) are NOT covered -- this
        # row names no A continuation, unlike the AT29C010/010A and AT29C040/040A
        # rows above, which do -- so those two tokens are undocumented instead.
        "AT29LV020",
        "AT29LV040",
        # ATMEL -- lockable-proms.md:279 §16 "AT49F010 / F020 / F040"
        # Variant-dependent, boot-block lockout or sector lock.
        "AT49F010",
        "AT49F020",
        "AT49F040",
        # ATMEL -- lockable-proms.md:281 §16 "AT49F040A"
        # Check exact revision -- variant-dependent. AT49F040T's T suffix
        # collapses onto this row's 040A stem the same way AT49F040 (bare, from
        # the row above) does.
        "AT49F040A",
        "AT49F040T",
        # CHINGIS / PMC -- lockable-proms.md:180 §10 "PM29F002 / F004 and similar"
        # Variant-dependent, datasheet required. Does NOT extend to PM39F010/020/040
        # -- 39 is a different digit family from 29, not a suffix continuation.
        "PM29F002B",
        "PM29F002T",
        "PM29F004B",
        "PM29F004T",
        # SST -- lockable-proms.md:243 §14 "SST39SF010A / SF020A / SF040" [6]
        # No explicit lock bit -- hardware/software data protection, not a
        # conventional individually-lockable sector with a status query.
        "SST39SF010",
        "SST39SF010A",
        "SST39SF020",
        "SST39SF020A",
        "SST39SF040",
        # SST -- lockable-proms.md:244 §14 "SST39VF010 / VF020 / VF040"
        # Usually no explicit lock bit.
        "SST39VF010",
        "SST39VF020",
        "SST39VF040",
        "SST39VF040A",
        # SST -- lockable-proms.md:254 §14 "SST39SF512 / 010 / 020 older revisions"
        # Usually no for SDP.
        "SST39SF512",
        "SST39SF512A",
        # WINBOND -- lockable-proms.md:28 §1 "W39L010 / W39L020 / W39L040"
        # Variant-dependent, boot blocks or sectors, 3.3V families.
        "W39L040A",
    }
)

# C-17's disagreement, recorded rather than resolved (DESIGN.md §5). The
# tiebreak rule in words: where the source document's table row and its own
# restatements disagree about whether a token is covered, the token takes the
# **more restrictive** readability state -- it is not adjudicated per entry by
# curator judgement (D-06's rejected alternative; DATA-04 forbids exactly that).
# Consequence, stated so it cannot be missed: the worked
# `W29C020,W29C020C,W29C022` DB entry refuses under D-06 regardless of how this
# tiebreak resolves, because `W29C022` is undocumented either way -- the
# tiebreak changes only how many offending aliases the refusal names (one vs.
# two), never the entry's verdict. C-18, recorded alongside: all three aliases
# -- W29C020, W29C020C, W29C022 -- are one upstream `<ic>` entry with one chip
# id `0x0000da45`, so no firmware read could distinguish which of the three
# parts is actually in the socket, whatever this tiebreak resolves to.
AMBIGUOUS_DOC_CITATIONS: Mapping[str, str] = {
    "W29C020": (
        "lockable-proms.md:21 names the row key 'W29C020 / W29C020C' (Yes-special, "
        "covering both parts), but every restatement elsewhere in the document -- "
        "lockable-proms.md:30, :335, and :350 -- names 'W29C020C' only, never bare "
        "W29C020. Bare W29C020 appears exactly once in the document's 399 lines: "
        "the :21 row key itself. Tiebreak rule (DESIGN.md §5): the more-restrictive "
        "reading wins, so W29C020 curates to documented-not-readable."
    ),
}


def readability_for_token(token: str) -> str:
    """Resolve one alias token to a `READABILITY_STATES` member.

    Fail-closed by construction: a token is `documented-readable` only if it is
    a literal member of `DOCUMENTED_READABLE_TOKENS`; `documented-not-readable`
    only if a literal member of `DOCUMENTED_NOT_READABLE_TOKENS`; every other
    token -- including one this module's author simply forgot to curate -- is
    `undocumented` by complement. The two membership tests are `in` checks
    against the frozenset names themselves (never a subscript or dict lookup),
    because `151-09`'s AST gate detects permit-by-default by looking for
    exactly that compare shape.
    """
    if token in DOCUMENTED_READABLE_TOKENS:
        return READABILITY_STATES[0]
    if token in DOCUMENTED_NOT_READABLE_TOKENS:
        return READABILITY_STATES[1]
    return READABILITY_STATES[2]


# ---------------------------------------------------------------------------
# Mechanism and permanence: two independent reporting-only axes transcribed
# from `lockable-proms.md` §Key alongside readability. `lockable-proms.md`
# treats readability and permanence as independent axes (a part can be
# documented-not-readable and still have a documented, even permanent,
# protection mechanism -- W29C020C is the case where permanence matters most,
# and it is documented-readable *and* permanent).
#
# These two axes are **reporting-only**. They do not gate answering anywhere:
# D-06 keys the read/refuse decision only on the readability axis above, never
# on mechanism or permanence. Consequently `151-09`'s AST gate's rule for these
# two mappings is deliberately **weaker** than the literal-frozenset-membership
# rule it applies to readability -- stated here in words, per PATTERNS.md's
# requirement that the weakening not be left implicit, and restated in
# `151-09`'s gate docstring.
MECHANISM_STATES: tuple[str, ...] = (
    "boot_block_lockout",
    "sector_protection",
    "boot_block_or_sector_variant",
    "whole_device_sdp",
    "sdp_no_readable_lock_bit",
    "unknown",
)

PERMANENCE_STATES: tuple[str, ...] = (
    "permanent",
    "reversible",
    "unknown",
)

# Keys are drawn only from the union of the two curated frozensets above --
# never from the undocumented complement, which by definition has no
# documented mechanism. Grouped by mechanism value; every token in a group
# shares the citation already given for it above in DOCUMENTED_READABLE_TOKENS
# / DOCUMENTED_NOT_READABLE_TOKENS.
MECHANISM_BY_TOKEN: Mapping[str, str] = {
    # -> "whole_device_sdp": W29C010 (:20), AT29C* family (:266-271), W29EE011/012 (:23)
    "AT29C010A": "whole_device_sdp",
    "AT29C020": "whole_device_sdp",
    "AT29C040": "whole_device_sdp",
    "AT29C040A": "whole_device_sdp",
    "AT29C256": "whole_device_sdp",
    "AT29C512": "whole_device_sdp",
    "AT29LV020": "whole_device_sdp",
    "AT29LV040": "whole_device_sdp",
    "W29C010": "whole_device_sdp",
    "W29EE011": "whole_device_sdp",
    "W29EE012": "whole_device_sdp",
    # -> "boot_block_lockout": W29C020/W29C020C (:21), AT49F001/F002 (:280)
    "AT49F001": "boot_block_lockout",
    "AT49F001A": "boot_block_lockout",
    "AT49F001AN": "boot_block_lockout",
    "AT49F001ANT": "boot_block_lockout",
    "AT49F001AT": "boot_block_lockout",
    "AT49F001N": "boot_block_lockout",
    "AT49F001NT": "boot_block_lockout",
    "AT49F001T": "boot_block_lockout",
    "AT49F002": "boot_block_lockout",
    "AT49F002A": "boot_block_lockout",
    "AT49F002AN": "boot_block_lockout",
    "AT49F002ANT": "boot_block_lockout",
    "AT49F002AT": "boot_block_lockout",
    "AT49F002N": "boot_block_lockout",
    "AT49F002NT": "boot_block_lockout",
    "AT49F002T": "boot_block_lockout",
    "W29C020": "boot_block_lockout",
    "W29C020C": "boot_block_lockout",
    # -> "boot_block_or_sector_variant": AT49F010/020/040 (:279), AT49F040A (:281),
    #    W29C040 (:22), W39L040A (:28)
    "AT49F010": "boot_block_or_sector_variant",
    "AT49F020": "boot_block_or_sector_variant",
    "AT49F040": "boot_block_or_sector_variant",
    "AT49F040A": "boot_block_or_sector_variant",
    "AT49F040T": "boot_block_or_sector_variant",
    "W29C040": "boot_block_or_sector_variant",
    "W39L040A": "boot_block_or_sector_variant",
    # -> "sdp_no_readable_lock_bit": SST39SF*/SST39VF* (:243, :244, :254)
    "SST39SF010": "sdp_no_readable_lock_bit",
    "SST39SF010A": "sdp_no_readable_lock_bit",
    "SST39SF020": "sdp_no_readable_lock_bit",
    "SST39SF020A": "sdp_no_readable_lock_bit",
    "SST39SF040": "sdp_no_readable_lock_bit",
    "SST39SF512": "sdp_no_readable_lock_bit",
    "SST39SF512A": "sdp_no_readable_lock_bit",
    "SST39VF010": "sdp_no_readable_lock_bit",
    "SST39VF020": "sdp_no_readable_lock_bit",
    "SST39VF040": "sdp_no_readable_lock_bit",
    "SST39VF040A": "sdp_no_readable_lock_bit",
    # -> "unknown": PM29F002/F004 (:180, no mechanism detail given at all)
    "PM29F002B": "unknown",
    "PM29F002T": "unknown",
    "PM29F004B": "unknown",
    "PM29F004T": "unknown",
    # -> "sector_protection": every Yes-sector / Yes-sector-special AMD-Autoselect-
    #    compatible family cited in DOCUMENTED_READABLE_TOKENS above (AMD, Fujitsu,
    #    Hynix/Hyundai, EON/CFEON, Macronix, ST/SGS-Thomson, Winbond W49F, AT49BV/LVxxx)
    "AM29F002B": "sector_protection",
    "AM29F002BB": "sector_protection",
    "AM29F002BT": "sector_protection",
    "AM29F002NB": "sector_protection",
    "AM29F002NBB": "sector_protection",
    "AM29F002NBT": "sector_protection",
    "AM29F002NT": "sector_protection",
    "AM29F002T": "sector_protection",
    "AM29F010": "sector_protection",
    "AM29F010B": "sector_protection",
    "AM29F040": "sector_protection",
    "AM29F040B": "sector_protection",
    "AT49BV001": "sector_protection",
    "AT49BV001A": "sector_protection",
    "AT49BV001AN": "sector_protection",
    "AT49BV001ANT": "sector_protection",
    "AT49BV001AT": "sector_protection",
    "AT49BV001N": "sector_protection",
    "AT49BV001NT": "sector_protection",
    "AT49BV001T": "sector_protection",
    "AT49BV002": "sector_protection",
    "AT49BV002A": "sector_protection",
    "AT49BV002AN": "sector_protection",
    "AT49BV002ANT": "sector_protection",
    "AT49BV002AT": "sector_protection",
    "AT49BV002N": "sector_protection",
    "AT49BV002NT": "sector_protection",
    "AT49BV002T": "sector_protection",
    "AT49BV010": "sector_protection",
    "AT49BV020": "sector_protection",
    "AT49BV040": "sector_protection",
    "AT49BV040A": "sector_protection",
    "AT49BV040B": "sector_protection",
    "AT49BV040T": "sector_protection",
    "AT49BV512": "sector_protection",
    "AT49LV001": "sector_protection",
    "AT49LV001N": "sector_protection",
    "AT49LV001NT": "sector_protection",
    "AT49LV001T": "sector_protection",
    "AT49LV002": "sector_protection",
    "AT49LV002N": "sector_protection",
    "AT49LV002NT": "sector_protection",
    "AT49LV002T": "sector_protection",
    "AT49LV010": "sector_protection",
    "AT49LV020": "sector_protection",
    "AT49LV040": "sector_protection",
    "AT49LV040T": "sector_protection",
    "AT49LV512": "sector_protection",
    "EN29F002AB": "sector_protection",
    "EN29F002ANB": "sector_protection",
    "EN29F002ANT": "sector_protection",
    "EN29F002AT": "sector_protection",
    "EN29F002B": "sector_protection",
    "EN29F002NB": "sector_protection",
    "EN29F002NT": "sector_protection",
    "EN29F002T": "sector_protection",
    "EN29F010": "sector_protection",
    "EN29F040": "sector_protection",
    "EN29F040A": "sector_protection",
    "EN29LV040A": "sector_protection",
    "HY29F002T": "sector_protection",
    "HY29F040": "sector_protection",
    "HY29F040A": "sector_protection",
    "HY29F040T": "sector_protection",
    "M29F002B": "sector_protection",
    "M29F002BB": "sector_protection",
    "M29F002BNB": "sector_protection",
    "M29F002BNT": "sector_protection",
    "M29F002BT": "sector_protection",
    "M29F002NT": "sector_protection",
    "M29F002T": "sector_protection",
    "M29F010B": "sector_protection",
    "M29F040B": "sector_protection",
    "MBM29F002B": "sector_protection",
    "MBM29F002T": "sector_protection",
    "MBM29F040": "sector_protection",
    "MX29F001B": "sector_protection",
    "MX29F001T": "sector_protection",
    "MX29F002B": "sector_protection",
    "MX29F002NB": "sector_protection",
    "MX29F002NT": "sector_protection",
    "MX29F002T": "sector_protection",
    "MX29F040": "sector_protection",
    "MX29F040C": "sector_protection",
    "MX29LV002CB": "sector_protection",
    "MX29LV002CT": "sector_protection",
    "MX29LV002NCB": "sector_protection",
    "MX29LV002NCT": "sector_protection",
    "W49F002": "sector_protection",
    "W49F002A": "sector_protection",
    "W49F002B": "sector_protection",
    "W49F002U": "sector_protection",
    "W49F020": "sector_protection",
}

# Keys are drawn only from the same union as MECHANISM_BY_TOKEN. Grouped by
# permanence value; "unknown" covers both explicit "Variant-dependent"/"Device-
# dependent" cells and rows with no permanence column at all (§10's AS29/PM29/
# HY29 table).
PERMANENCE_BY_TOKEN: Mapping[str, str] = {
    # -> "reversible"
    "AM29F002B": "reversible",
    "AM29F002BB": "reversible",
    "AM29F002BT": "reversible",
    "AM29F002NB": "reversible",
    "AM29F002NBB": "reversible",
    "AM29F002NBT": "reversible",
    "AM29F002NT": "reversible",
    "AM29F002T": "reversible",
    "AM29F010": "reversible",
    "AM29F010B": "reversible",
    "AM29F040": "reversible",
    "AM29F040B": "reversible",
    "AT29C010A": "reversible",
    "AT29C020": "reversible",
    "AT29C040": "reversible",
    "AT29C040A": "reversible",
    "AT29C256": "reversible",
    "AT29C512": "reversible",
    "AT29LV020": "reversible",
    "AT29LV040": "reversible",
    "EN29F002AB": "reversible",
    "EN29F002ANB": "reversible",
    "EN29F002ANT": "reversible",
    "EN29F002AT": "reversible",
    "EN29F002B": "reversible",
    "EN29F002NB": "reversible",
    "EN29F002NT": "reversible",
    "EN29F002T": "reversible",
    "EN29F010": "reversible",
    "EN29F040": "reversible",
    "EN29F040A": "reversible",
    "EN29LV040A": "reversible",
    "M29F002B": "reversible",
    "M29F002BB": "reversible",
    "M29F002BNB": "reversible",
    "M29F002BNT": "reversible",
    "M29F002BT": "reversible",
    "M29F002NT": "reversible",
    "M29F002T": "reversible",
    "M29F010B": "reversible",
    "M29F040B": "reversible",
    "MBM29F002B": "reversible",
    "MBM29F002T": "reversible",
    "MBM29F040": "reversible",
    "MX29F001B": "reversible",
    "MX29F001T": "reversible",
    "MX29F002B": "reversible",
    "MX29F002NB": "reversible",
    "MX29F002NT": "reversible",
    "MX29F002T": "reversible",
    "MX29F040": "reversible",
    "MX29F040C": "reversible",
    "MX29LV002CB": "reversible",
    "MX29LV002CT": "reversible",
    "MX29LV002NCB": "reversible",
    "MX29LV002NCT": "reversible",
    "SST39SF010": "reversible",
    "SST39SF010A": "reversible",
    "SST39SF020": "reversible",
    "SST39SF020A": "reversible",
    "SST39SF040": "reversible",
    "SST39SF512": "reversible",
    "SST39SF512A": "reversible",
    "SST39VF010": "reversible",
    "SST39VF020": "reversible",
    "SST39VF040": "reversible",
    "SST39VF040A": "reversible",
    "W29C010": "reversible",
    "W29EE011": "reversible",
    "W29EE012": "reversible",
    "W49F002": "reversible",
    "W49F002A": "reversible",
    "W49F002B": "reversible",
    "W49F002U": "reversible",
    "W49F020": "reversible",
    # -> "permanent"
    "AT49F001": "permanent",
    "AT49F001A": "permanent",
    "AT49F001AN": "permanent",
    "AT49F001ANT": "permanent",
    "AT49F001AT": "permanent",
    "AT49F001N": "permanent",
    "AT49F001NT": "permanent",
    "AT49F001T": "permanent",
    "AT49F002": "permanent",
    "AT49F002A": "permanent",
    "AT49F002AN": "permanent",
    "AT49F002ANT": "permanent",
    "AT49F002AT": "permanent",
    "AT49F002N": "permanent",
    "AT49F002NT": "permanent",
    "AT49F002T": "permanent",
    "W29C020": "permanent",
    "W29C020C": "permanent",
    # -> "unknown"
    "AT49BV001": "unknown",
    "AT49BV001A": "unknown",
    "AT49BV001AN": "unknown",
    "AT49BV001ANT": "unknown",
    "AT49BV001AT": "unknown",
    "AT49BV001N": "unknown",
    "AT49BV001NT": "unknown",
    "AT49BV001T": "unknown",
    "AT49BV002": "unknown",
    "AT49BV002A": "unknown",
    "AT49BV002AN": "unknown",
    "AT49BV002ANT": "unknown",
    "AT49BV002AT": "unknown",
    "AT49BV002N": "unknown",
    "AT49BV002NT": "unknown",
    "AT49BV002T": "unknown",
    "AT49BV010": "unknown",
    "AT49BV020": "unknown",
    "AT49BV040": "unknown",
    "AT49BV040A": "unknown",
    "AT49BV040B": "unknown",
    "AT49BV040T": "unknown",
    "AT49BV512": "unknown",
    "AT49F010": "unknown",
    "AT49F020": "unknown",
    "AT49F040": "unknown",
    "AT49F040A": "unknown",
    "AT49F040T": "unknown",
    "AT49LV001": "unknown",
    "AT49LV001N": "unknown",
    "AT49LV001NT": "unknown",
    "AT49LV001T": "unknown",
    "AT49LV002": "unknown",
    "AT49LV002N": "unknown",
    "AT49LV002NT": "unknown",
    "AT49LV002T": "unknown",
    "AT49LV010": "unknown",
    "AT49LV020": "unknown",
    "AT49LV040": "unknown",
    "AT49LV040T": "unknown",
    "AT49LV512": "unknown",
    "HY29F002T": "unknown",
    "HY29F040": "unknown",
    "HY29F040A": "unknown",
    "HY29F040T": "unknown",
    "PM29F002B": "unknown",
    "PM29F002T": "unknown",
    "PM29F004B": "unknown",
    "PM29F004T": "unknown",
    "W29C040": "unknown",
    "W39L040A": "unknown",
}
