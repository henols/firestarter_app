"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 86 Plan 04 — VAR-05 / D-10 / D-11 / SAFE-04 non-upstream supplement gate.

2516 and 2532 are physically-real 24-pin UV-EPROM oddballs that are ABSENT from
minipro's infoic.xml. Per operator directive D-10 they ship first-class in
chip_database.json via the curated, provenance-cited supplement
tools/extra_chips.json, which build_db.py merges AFTER the infoic.xml decode loop.

This test pins:
  (a) 2516 + 2532 are present in the GENERATED chip_database.json with their cited
      wire values (so a regression that drops the merge, or mutates a wire value,
      fails loudly);
  (b) 2516 retains its SAFE-04 posture — algorithm 0x0B, pinout DIP24_2716,
      vpp_mv 25000, size_bytes 2048, UV-EPROM — verbatim from the v1.15 user-override
      (DECODE-AUDIT.md), and is marked UNVERIFIED / not write-graduated (its
      support_status stays "supported" so it is still resolvable for read/info, and
      its host-guard posture is unchanged);
  (c) every supplement record carries a non-upstream source marker AND a datasheet
      citation field (D-11 — honest-provenance, not a guessed chip);
  (d) the 24-pin VPP-pin safety holds: every supplement chip sits on a pinout that
      HAS a vpp-pin (the GATE-03 structural invariant check_dispatch.py enforces),
      so 12-25V VPP lands on a real VPP pin, never an address/WE line.

It reads the GENERATED DB (post-merge) and tools/extra_chips.json + pinouts.json
via FIRESTARTER_* path seams so it runs in both the submodule and the meta-repo
test runner.
"""

import json
import os

# ---------------------------------------------------------------------------
# Path seams (mirror the FIRESTARTER_DB_FILE idiom used across the suite)
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(__file__)

_DB_FILE = os.environ.get(
    "FIRESTARTER_DB_FILE",
    os.path.join(_HERE, "..", "firestarter", "data", "chip_database.json"),
)
_PINOUTS_FILE = os.environ.get(
    "FIRESTARTER_PINOUTS_FILE",
    os.path.join(_HERE, "..", "firestarter", "data", "pinouts.json"),
)
_EXTRA_CHIPS_FILE = os.environ.get(
    "FIRESTARTER_EXTRA_CHIPS_FILE",
    os.path.join(_HERE, "..", "tools", "extra_chips.json"),
)

_SUPPLEMENT_SOURCE = "non-upstream-supplement"


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _all_chips(db):
    for mfg, chips in db.items():
        if isinstance(chips, list):
            for chip in chips:
                yield mfg, chip


def _aliases(chip):
    pn = chip.get("part_number", "")
    return {a.split("@")[0].strip() for a in pn.split(",") if a.strip()}


def _find(db, alias):
    """Return the first chip record whose alias-set contains `alias`, or None."""
    for _mfg, chip in _all_chips(db):
        if alias in _aliases(chip):
            return chip
    return None


# ---------------------------------------------------------------------------
# (a) presence in the generated DB
# ---------------------------------------------------------------------------
class TestSupplementPresent:
    def test_2516_present_in_generated_db(self):
        db = _load(_DB_FILE)
        assert _find(db, "2516") is not None, (
            "2516 absent from generated chip_database.json — "
            "the build_db.py post-decode supplement merge did not run (VAR-05 / D-10)"
        )

    def test_2532_present_in_generated_db(self):
        db = _load(_DB_FILE)
        assert _find(db, "2532") is not None, (
            "2532 absent from generated chip_database.json — "
            "the build_db.py post-decode supplement merge did not run (VAR-05 / D-10)"
        )


# ---------------------------------------------------------------------------
# (b) SAFE-04: 2516 wire values verbatim + UNVERIFIED / not write-graduated
# ---------------------------------------------------------------------------
class TestSafe04WireStability:
    def test_2516_wire_values_match_v1_15_override(self):
        """SAFE-04: 2516 wire values are the v1.15 user-override verbatim — algorithm
        0x0B, pinout DIP24_2716, electrical.type UV-EPROM, vpp_mv 25000, size 2048.
        A regression that silently moves any of these fails here."""
        db = _load(_DB_FILE)
        c = _find(db, "2516")
        assert c is not None, "2516 missing"
        assert c["programming"]["algorithm"] == 0x0B, c["programming"]
        assert c["pinout"] == "DIP24_2716", c["pinout"]
        assert c["electrical"]["type"] == "UV-EPROM", c["electrical"]
        assert c["electrical"]["vpp_mv"] == 25000, c["electrical"]
        assert c["electrical"]["size_bytes"] == 2048, c["electrical"]
        assert c["electrical"]["pin_count"] == 24, c["electrical"]

    def test_2516_unverified_not_write_graduated(self):
        """SAFE-04 / D-11: 2516 is resolvable (support_status 'supported', host guard
        unchanged) but explicitly UNVERIFIED — NOT write-graduated. The UNVERIFIED
        marker must be present so the chip is not mistaken for a write-proven part."""
        db = _load(_DB_FILE)
        c = _find(db, "2516")
        assert c is not None, "2516 missing"
        # Resolvable for read/info: support_status stays "supported" (host guard
        # only refuses non-"supported" chips; 2516 must remain readable).
        assert c.get("support_status") == "supported", c.get("support_status")
        # ... but explicitly UNVERIFIED (not write-graduated).
        assert c.get("verification_status") == "UNVERIFIED", c.get(
            "verification_status"
        )

    def test_2532_basic_decode(self):
        """2532 is a 4KB non-JEDEC 24-pin UV-EPROM on the DIP24_2532 pinout, 0x0B."""
        db = _load(_DB_FILE)
        c = _find(db, "2532")
        assert c is not None, "2532 missing"
        assert c["programming"]["algorithm"] == 0x0B, c["programming"]
        assert c["pinout"] == "DIP24_2532", c["pinout"]
        assert c["electrical"]["type"] == "UV-EPROM", c["electrical"]
        assert c["electrical"]["size_bytes"] == 4096, c["electrical"]
        assert c.get("verification_status") == "UNVERIFIED", c.get(
            "verification_status"
        )


# ---------------------------------------------------------------------------
# (c) D-11: every supplement record cites a datasheet + carries a source marker
# ---------------------------------------------------------------------------
class TestSupplementProvenance:
    def test_every_supplement_record_cites_a_datasheet(self):
        extra = _load(_EXTRA_CHIPS_FILE)
        records = [
            c for chips in extra.values() if isinstance(chips, list) for c in chips
        ]
        assert records, "extra_chips.json has no records"
        for c in records:
            pn = c.get("part_number", "<unknown>")
            assert c.get("source") == _SUPPLEMENT_SOURCE, (
                f"{pn}: missing source='{_SUPPLEMENT_SOURCE}' marker (D-11 fencing)"
            )
            ds = c.get("datasheet", "")
            assert ds, (
                f"{pn}: missing datasheet citation (D-11 field-cites-a-datasheet)"
            )

    def test_supplement_records_carry_source_marker_in_generated_db(self):
        """The non-upstream source marker survives the merge into the generated DB so
        diff_db.py can fence the rows as cited supplement rows."""
        db = _load(_DB_FILE)
        for alias in ("2516", "2532"):
            c = _find(db, alias)
            assert c is not None, f"{alias} missing"
            assert c.get("source") == _SUPPLEMENT_SOURCE, (
                f"{alias}: source marker not preserved through the build_db.py merge"
            )


# ---------------------------------------------------------------------------
# (d) D-11 / GATE-03: 24-pin VPP-pin safety — supplement pinouts have a vpp-pin
# ---------------------------------------------------------------------------
class TestSupplementVppSafety:
    def test_supplement_pinouts_have_a_vpp_pin(self):
        """The supplement chips are 0x0B UV-EPROMs that route to configure_eprom and
        assert VPP. check_dispatch.py's GATE-03 structural guard requires their pinout
        to expose a real vpp-pin (else 12-25V would land on an address/WE line — a
        hardware-damage path). Assert the invariant directly so this test stands on its
        own; the full check_dispatch.py gate is run separately in the plan's verify."""
        db = _load(_DB_FILE)
        pinouts = _load(_PINOUTS_FILE)
        for alias in ("2516", "2532"):
            c = _find(db, alias)
            assert c is not None, f"{alias} missing"
            pin_key = c["pinout"]
            assert pin_key in pinouts, (
                f"{alias}: pinout {pin_key} absent from pinouts.json"
            )
            vpp = pinouts[pin_key].get("pins", {}).get("vpp-pin")
            assert vpp, (
                f"{alias}: pinout {pin_key} has no vpp-pin — GATE-03 structural VPP "
                f"hazard for a 0x0B configure_eprom chip (D-11)"
            )
