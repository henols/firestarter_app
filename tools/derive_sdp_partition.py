#!/usr/bin/env python3
"""Reproducible, fetch-based independent re-derivation of the SDP-capability
partition (PROV-04, Phase 136.1 Plan 02).

Pinned to minipro commit ``a8efaedc236c1d9718bd28299dfbb99536b010ff`` -- the
same revision ``tools/build_db.py``'s ``MINIPRO_XML_URL`` fetches.
``tools/infoic*.xml`` is gitignored (``.gitignore:29``) and absent from a
clean checkout, so this script FETCHES the XML live via ``requests.get`` by
default -- mirroring ``build_db.py``'s own default behavior exactly -- or
reads a local file via the ``INFOIC_XML_PATH`` env var if set (a caller
convenience only; this script's own source carries no reference to any
specific local path, cached or otherwise).

This is a standalone, occasional/manual reproducibility recipe -- analogous
to ``build_db.py`` itself -- never imported by production code or by the
pytest suite, and never wired into CI (CI must never depend on a live
network fetch).

Preserves Phase 120's exact token-matching rules verbatim (origin:
``.planning/phases/120-host-cli-surface-wire-emission-capability-refusal/
120-derive-sdp-allowset.py``): key on the EXACT ``part_number`` token
(comma-split, uppercased), strip ONLY the ``@PACKAGE`` suffix, do NOT strip
parentheticals -- stripping ``(Non-Standard)`` collapses
``AT28C64B(Non-Standard)`` onto the separate ``AT28C64B`` entry and
fabricates a spurious MIXED verdict (see
``firestarter/sdp_capability.py``'s ``split_part_number_tokens``, the same
rule cross-referenced here).

Independently cross-checks the freshly-derived partition against BOTH:
  (a) ``firestarter.sdp_capability.sdp_capability_for_entry`` -- the
      production transcription predicate (imported from the installed
      package, never reimplemented), and
  (b) ``chip_database.json``'s own committed ``protect_on_after`` field
      (Plan 136.1-01) -- read directly, mirroring
      ``tests/test_sdp_db_invariant.py``'s
      ``_partition_from_protect_on_after_field`` selection logic,
      DUPLICATED here deliberately (this script must not import from
      ``tests/``).

Exit code: 0 if the freshly-derived partition measures 43 ALLOW / 41 REFUSE
/ 84 total AND agrees with both comparison targets on every chip; 1
otherwise, naming every disagreeing chip.
"""

from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import requests

from firestarter.sdp_capability import sdp_capability_for_entry

# Duplicated verbatim from tools/build_db.py's MINIPRO_XML_URL (:17-20) --
# keep the two constants in lockstep, the same discipline this project
# already applies to constants.py/firestarter.h.
MINIPRO_XML_URL = (
    "https://gitlab.com/DavidGriffith/minipro/-/raw/"
    "a8efaedc236c1d9718bd28299dfbb99536b010ff/infoic.xml"
)

_TOOLS_DIR = Path(__file__).parent
_FA_DIR = _TOOLS_DIR.parent
_DB_FILE = _FA_DIR / "firestarter" / "data" / "chip_database.json"

_ALGORITHM_0X0D = 13
_MP_PROTECT_AFTER = 0x8000  # infoic.xml flags bit 15 (MP_PROTECT_AFTER)


def _load_infoic_xml() -> str:
    """Read ``INFOIC_XML_PATH`` if set (a caller convenience -- e.g. a
    cached copy -- never hardcoded here); otherwise fetch
    ``MINIPRO_XML_URL`` live via ``requests.get``, mirroring
    ``build_db.py``'s own default behavior exactly."""
    local_path = os.environ.get("INFOIC_XML_PATH")
    if local_path:
        return Path(local_path).read_text(encoding="utf-8")
    resp = requests.get(MINIPRO_XML_URL, timeout=30)
    resp.raise_for_status()
    return resp.text


def _build_token_index(xml_text: str) -> dict[str, set[int]]:
    """EXACT ``part_number`` token (comma-split, uppercased, ``@PACKAGE``
    suffix stripped, parentheticals NOT stripped -- Phase 120 rule 1) ->
    set of ``flags`` ints seen for that token across every
    ``<database type="INFOIC2PLUS">`` ``<ic>`` entry."""
    root = ET.fromstring(xml_text)
    sections = root.findall(".//database[@type='INFOIC2PLUS']")
    if not sections:
        raise RuntimeError("INFOIC2PLUS section not found in infoic.xml")
    section = sections[0]

    token_index: dict[str, set[int]] = defaultdict(set)
    for mfr in section.findall("manufacturer"):
        for ic in mfr.findall("ic"):
            flags = int(ic.get("flags", "0x0"), 16)
            for raw_token in ic.get("name", "").split(","):
                token = raw_token.strip()
                if not token:
                    continue
                # Strip ONLY the @PACKAGE suffix; keep parentheticals verbatim.
                token = token.split("@")[0].strip().upper()
                token_index[token].add(flags)
    return token_index


def _select_0x0d_chips(db: dict) -> list[tuple[str, dict]]:
    """Duplicated from tests/test_sdp_db_invariant.py's
    ``_select_0x0d_chips`` -- deliberately, since this script must not
    import from ``tests/``."""
    selected = []
    for mfr, chips in db.items():
        for chip in chips:
            if chip["programming"]["algorithm"] == _ALGORITHM_0X0D:
                selected.append((mfr, chip))
    return selected


def _split_tokens(part_number: str) -> list[str]:
    """Same rule as ``firestarter.sdp_capability.split_part_number_tokens``
    (comma-split, uppercased, parentheticals kept)."""
    return [t.strip().upper() for t in part_number.split(",") if t.strip()]


def main() -> int:
    xml_text = _load_infoic_xml()
    token_index = _build_token_index(xml_text)

    db = json.loads(_DB_FILE.read_text(encoding="utf-8"))
    selected = _select_0x0d_chips(db)

    allow: list[str] = []
    refuse: list[str] = []
    mixed: list[str] = []
    nomatch: list[str] = []
    xml_allow_set: set[str] = set()

    for mfr, chip in selected:
        part_number = chip["part_number"]
        key = f"{mfr}/{part_number}"
        tokens = _split_tokens(part_number)
        b15_values: set[bool] = set()
        unmatched: list[str] = []
        for token in tokens:
            flags_seen = token_index.get(token)
            if not flags_seen:
                unmatched.append(token)
                continue
            for flags in flags_seen:
                b15_values.add(bool(flags & _MP_PROTECT_AFTER))

        if unmatched and not b15_values:
            nomatch.append(key)
        elif b15_values == {True}:
            allow.append(key)
            xml_allow_set.add(key)
        elif b15_values == {False}:
            refuse.append(key)
        else:
            mixed.append(key)

    total = len(allow) + len(refuse) + len(mixed) + len(nomatch)

    width = 100
    print("=" * width)
    print("SDP-capability partition derived from a freshly-loaded infoic.xml")
    print(
        "minipro @ a8efaedc236c1d9718bd28299dfbb99536b010ff "
        "(INFOIC2PLUS, exact-token keying, parens NOT stripped)"
    )
    print("=" * width)
    print(f"ALLOW   : {len(allow)}")
    print(f"REFUSE  : {len(refuse)}")
    print(f"MIXED   : {len(mixed)}")
    print(f"NOMATCH : {len(nomatch)}")
    print(f"TOTAL   : {total}  (must be 84)")
    print()

    ok = True
    if len(allow) != 43 or len(refuse) != 41 or total != 84:
        ok = False
        print(
            f"FAIL: partition is not 43 ALLOW / 41 REFUSE / 84 total "
            f"(measured {len(allow)}/{len(refuse)}/{total})."
        )
        if mixed:
            print(f"  MIXED ({len(mixed)}): {sorted(mixed)}")
        if nomatch:
            print(f"  NOMATCH ({len(nomatch)}): {sorted(nomatch)}")
    else:
        print("PASS: fresh XML derivation measures 43 ALLOW / 41 REFUSE / 84 total.")

    # Comparison (a): production transcription (sdp_capability_for_entry,
    # SDP_CAPABLE_TOKENS-based -- imported from the installed package).
    production_allow: set[str] = set()
    for mfr, chip in selected:
        part_number = chip["part_number"]
        entry = {
            "protocol-id": chip["programming"]["algorithm"],
            "name": part_number,
        }
        allowed, _reason = sdp_capability_for_entry(entry, part_number)
        if allowed:
            production_allow.add(f"{mfr}/{part_number}")

    only_xml_vs_production = sorted(xml_allow_set - production_allow)
    only_production_vs_xml = sorted(production_allow - xml_allow_set)
    if only_xml_vs_production or only_production_vs_xml:
        ok = False
        print(
            "FAIL: freshly-derived XML partition disagrees with "
            "sdp_capability_for_entry (production transcription)."
        )
        print(f"  Only in fresh XML derivation: {only_xml_vs_production}")
        print(f"  Only in production transcription: {only_production_vs_xml}")
    else:
        print(
            "PASS: fresh XML derivation agrees with sdp_capability_for_entry "
            f"on all {len(xml_allow_set)} ALLOW entries."
        )

    # Comparison (b): chip_database.json's own committed protect_on_after
    # field (Plan 136.1-01).
    field_allow: set[str] = set()
    for mfr, chip in selected:
        part_number = chip["part_number"]
        if chip["programming"]["protect_on_after"]:
            field_allow.add(f"{mfr}/{part_number}")

    only_xml_vs_field = sorted(xml_allow_set - field_allow)
    only_field_vs_xml = sorted(field_allow - xml_allow_set)
    if only_xml_vs_field or only_field_vs_xml:
        ok = False
        print(
            "FAIL: freshly-derived XML partition disagrees with "
            "chip_database.json's committed protect_on_after field."
        )
        print(f"  Only in fresh XML derivation: {only_xml_vs_field}")
        print(f"  Only in committed protect_on_after field: {only_field_vs_xml}")
    else:
        print(
            "PASS: fresh XML derivation agrees with the committed "
            f"protect_on_after field on all {len(field_allow)} ALLOW entries."
        )

    print()
    if ok:
        print(
            "PASS: 43/41/84, zero disagreement against both comparison "
            "targets (sdp_capability_for_entry and protect_on_after)."
        )
        return 0
    print("FAIL: see above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
