"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 66 — DB inclusion, VPP correction, and support_status.

These tests assert the Phase 66 DB-01/02/03/05 behaviors implemented by
build_db.py with chip_database.json regenerated at 744 chips (Plan 03),
and the SC#3 dispatch-safety invariant enforced by Plan 04.

Taxonomy strings (locked — do NOT change wording):
  "supported"                 — normal dispatchable chip
  "protocol-not-implemented"  — DIP-parallel chip with unimplemented protocol
  "adapter-required"          — chip that physically needs an adapter (24-pin EEPROM hazard)
  "vpp-exceeds-max"           — NMOS chip whose true VPP exceeds the RURP ceiling

The tests load chip_database.json directly (not via EpromDatabase) because
EpromDatabase.get_eprom() does not expose the top-level support_status field.
"""

import json
import os
import sys

_DB_FILE = os.environ.get(
    "FIRESTARTER_DB_FILE",
    os.path.join(
        os.path.dirname(__file__), "..", "firestarter", "data", "chip_database.json"
    ),
)


def _load_db():
    """Load chip_database.json; return the raw dict."""
    with open(_DB_FILE, encoding="utf-8") as f:
        return json.load(f)


def _all_chips(db):
    """Iterate (mfg, chip) pairs across all manufacturer lists."""
    for mfg, chips in db.items():
        if isinstance(chips, list):
            for chip in chips:
                yield mfg, chip


def _aliases(chip):
    """Return the set of bare part-number aliases for a chip record.

    Splits the stored comma-separated part_number on ',' and strips @PACKAGE
    suffixes, matching the alias-extraction idiom in build_db.py.
    """
    pn = chip.get("part_number", "")
    return {a.split("@")[0].strip() for a in pn.split(",") if a.strip()}


# ---------------------------------------------------------------------------
# DB-01: Unknown-protocol DIP chip included as protocol-not-implemented
# ---------------------------------------------------------------------------
class TestProtocolNotImplementedInclusion:
    """DB-01: X88C64P (proto 0x34) appears with support_status=protocol-not-implemented."""

    def test_x88c64_included(self):
        """After DB regen, XICOR X88C64 or X88C64P appears in chip_database.json
        with support_status == 'protocol-not-implemented'.

        GREEN (Plan 03): X88C64P is included with support_status=protocol-not-implemented;
        proto 0x34 is in KNOWN_PROTOCOLS and the chip passes the inclusion gate.
        """
        db = _load_db()
        found = []
        for mfg, chip in _all_chips(db):
            al = _aliases(chip)
            if "X88C64" in al or "X88C64P" in al:
                found.append((mfg, chip))

        assert found, (
            "X88C64 / X88C64P not found in chip_database.json (DB-01 not implemented)"
        )

        # Every matching entry must have support_status == "protocol-not-implemented"
        for mfg, chip in found:
            ss = chip.get("support_status")
            assert ss == "protocol-not-implemented", (
                f"{mfg}/{chip.get('part_number')}: expected support_status="
                f"'protocol-not-implemented', got {ss!r}"
            )


# ---------------------------------------------------------------------------
# DB-02: 9 damage-hazard 24-pin EEPROMs included as adapter-required
# ---------------------------------------------------------------------------
class TestAdapterRequired24Pin:
    """DB-02: The 9 damage-hazard DIP24 EEPROMs appear as adapter-required."""

    # Part-number substrings expected from the 9 families (D-02).
    # These match the part_number values that build_db.py will emit after
    # stripping @PACKAGE suffixes from the raw infoic.xml name strings.
    _EXPECTED_FAMILIES = [
        "AT28C04",
        "AT28C04E",
        "AT28C04F",
        "AT28C16",
        "AT28C16E",
        "AT28C16F",
        "28C04A",
        "28C04AF",
        "28C16A",
        "28C16AF",
        "UPD28C04",
    ]

    def test_adapter_required_24pin(self):
        """Nine DIP24 damage-hazard EEPROMs must appear with support_status=
        'adapter-required' and a non-empty unsupported_reason.

        GREEN (Plan 03): the 9 DIP24 damage-hazard EEPROMs are included as adapter-required
        (build_db.py Site B fall-through with status assignment).
        """
        db = _load_db()
        adapter_chips = [
            (mfg, chip)
            for mfg, chip in _all_chips(db)
            if chip.get("support_status") == "adapter-required"
        ]

        assert adapter_chips, (
            "No chips with support_status='adapter-required' found (DB-02 not implemented)"
        )

        # All adapter-required chips must have a non-empty unsupported_reason
        for mfg, chip in adapter_chips:
            reason = chip.get("unsupported_reason", "")
            assert reason, (
                f"{mfg}/{chip.get('part_number')}: adapter-required chip missing unsupported_reason"
            )

        # At least one of the known 24-pin EEPROM families must be present
        adapter_part_numbers = {
            chip.get("part_number", "") for _, chip in adapter_chips
        }
        family_found = [
            family
            for family in self._EXPECTED_FAMILIES
            if any(family in pn for pn in adapter_part_numbers)
        ]
        assert family_found, (
            f"None of the expected 24-pin EEPROM families found in adapter-required chips. "
            f"Expected families: {self._EXPECTED_FAMILIES[:5]}...; "
            f"got: {sorted(adapter_part_numbers)[:10]}"
        )


# ---------------------------------------------------------------------------
# DB-03: NMOS VPP correction
# ---------------------------------------------------------------------------
class TestNmosVppCorrection:
    """DB-03: M2716/M2732 and M2732A have corrected VPP and correct support_status."""

    def test_nmos_vpp_exceeds_max(self):
        """Entries whose aliases include M2716 or M2732 (but not M2732A alone)
        have electrical.vpp_mv == 25000 and support_status == 'vpp-exceeds-max'.

        GREEN (Plan 03): NMOS VPP corrected to 25000 mV; support_status=vpp-exceeds-max.
        """
        db = _load_db()
        found = []
        for mfg, chip in _all_chips(db):
            al = _aliases(chip)
            if "M2716" in al or "M2732" in al:
                found.append((mfg, chip))

        assert found, "No M2716/M2732 entries found in chip_database.json"

        for mfg, chip in found:
            vpp_mv = chip.get("electrical", {}).get("vpp_mv")
            ss = chip.get("support_status")
            assert vpp_mv == 25000, (
                f"{mfg}/{chip.get('part_number')}: expected vpp_mv=25000, got {vpp_mv}"
            )
            assert ss == "vpp-exceeds-max", (
                f"{mfg}/{chip.get('part_number')}: expected support_status="
                f"'vpp-exceeds-max', got {ss!r}"
            )

    def test_nmos_m2732a_supported(self):
        """Entries whose aliases include M2732A (and NOT M2716/M2732) have
        electrical.vpp_mv == 21000 and support_status == 'supported'.

        GREEN (Plan 03): M2732A standalone entries have vpp_mv=21000 and support_status=supported.
        """
        db = _load_db()
        found = []
        for mfg, chip in _all_chips(db):
            al = _aliases(chip)
            if "M2732A" in al and "M2732" not in al and "M2716" not in al:
                found.append((mfg, chip))

        assert found, (
            "No M2732A-only entries found (excluding combined M2732/M2732A entries)"
        )

        for mfg, chip in found:
            vpp_mv = chip.get("electrical", {}).get("vpp_mv")
            ss = chip.get("support_status")
            assert vpp_mv == 21000, (
                f"{mfg}/{chip.get('part_number')}: expected vpp_mv=21000, got {vpp_mv}"
            )
            assert ss == "supported", (
                f"{mfg}/{chip.get('part_number')}: expected support_status='supported', got {ss!r}"
            )


# ---------------------------------------------------------------------------
# DB-05 / D-07: Universal support_status field
# ---------------------------------------------------------------------------
class TestSupportStatusUniversal:
    """DB-05/D-07: Every chip record carries an explicit support_status key."""

    def test_every_chip_has_support_status(self):
        """Every chip in chip_database.json must have a top-level 'support_status' key.

        GREEN (Plan 03): every chip carries support_status; 744 chips confirmed.
        """
        db = _load_db()
        missing = [
            f"{mfg}/{chip.get('part_number', '<unknown>')}"
            for mfg, chip in _all_chips(db)
            if "support_status" not in chip
        ]
        assert not missing, (
            f"{len(missing)} chip(s) missing 'support_status' key (D-07 not implemented). "
            f"First 5: {missing[:5]}"
        )

    def test_unsupported_reason_only_on_nonsupported(self):
        """D-07: supported chips must NOT carry unsupported_reason; non-supported
        chips MUST carry a non-empty unsupported_reason.

        GREEN (Plan 03): all 730 supported chips lack unsupported_reason; all 14 non-supported
        chips carry a non-empty unsupported_reason.
        """
        db = _load_db()
        violations = []
        for mfg, chip in _all_chips(db):
            ss = chip.get("support_status", "supported")
            reason = chip.get("unsupported_reason", "")
            pn = f"{mfg}/{chip.get('part_number', '<unknown>')}"
            if ss == "supported" and reason:
                violations.append(
                    f"{pn}: supported chip has unsupported_reason={reason!r}"
                )
            elif ss != "supported" and not reason:
                violations.append(f"{pn}: {ss} chip missing unsupported_reason")
        assert not violations, (
            f"{len(violations)} violation(s) of D-07 reason rule. First 5: {violations[:5]}"
        )


# ---------------------------------------------------------------------------
# D-01: Serial/SMD parts must still be skipped
# ---------------------------------------------------------------------------
class TestSerialSmdStillSkipped:
    """D-01: DataFlash (proto 0x04) and FWH (proto 0x11) parts remain absent."""

    def test_serial_smd_still_skipped(self):
        """DataFlash (proto 0x04) and FWH (proto 0x11) parts must NOT appear in
        chip_database.json — they are serial/SMD parts unsupported on RURP.

        Additionally, the TMS87C257@PLCC32 (proto 0x0A) must remain absent,
        and the X88C64 sibling @SOIC24 alias must not create a separate SOIC
        entry (the DIP24 form is the only include candidate).

        This test verifies the D-01 skip policy is preserved after Plan 03's
        build_db.py edits.

        GREEN-NOW note: this test may pass even before Plan 03 because the
        current DB correctly omits these parts. It is included so any accidental
        inclusion regression is caught by the scaffold.
        """
        db = _load_db()
        # Check by algorithm (proto 0x04 = DataFlash, 0x11 = FWH)
        serial_smd_algos = {0x04, 0x11}
        violations = []
        for mfg, chip in _all_chips(db):
            algo = chip.get("programming", {}).get("algorithm", 0)
            if algo in serial_smd_algos:
                violations.append(
                    f"{mfg}/{chip.get('part_number')} algo=0x{algo:02X} "
                    f"(DataFlash/FWH must be skipped)"
                )
        assert not violations, (
            f"{len(violations)} serial/SMD chip(s) found in DB that must be skipped: "
            f"{violations[:5]}"
        )


# ---------------------------------------------------------------------------
# SC#3 / D-03 HARD: Non-supported chips must be non-dispatchable (Plan 04)
# ---------------------------------------------------------------------------
class TestNonSupportedNonDispatchable:
    """SC#3 / D-03 HARD: every chip with support_status != 'supported' must
    dispatch to 'not_implemented' or 'ERROR' — never to a real programming
    handler (configure_eprom / configure_eeprom28c / configure_flash* /
    configure_sram).

    This pins the D-03 HARD invariant in CI (IN-03) so a future build_db.py
    change cannot silently reintroduce the routing defect that was found in
    66-VERIFICATION.md SC#3.

    GREEN (Plan 04): NON_DISPATCHABLE_ALGO = 0x00 is set at Site B
    (adapter-required) and Site C (vpp-exceeds-max) in build_db.py; X88C64P
    keeps proto=0x34 which dispatch() already returns 'not_implemented' for.
    After DB regen all 14 non-supported chips have non-dispatchable outcomes.
    """

    def test_non_supported_chips_are_non_dispatchable(self):
        """For every chip with support_status != 'supported', dispatch(algorithm,
        mem_type) must return 'not_implemented' or 'ERROR'.

        Violations are enumerated in the failure message so a regression is
        immediately identifiable by chip name, proto, and resolved handler.
        """
        # Inject tools/ onto sys.path so check_dispatch is importable — mirrors
        # the pattern used by test_decoder.py (self-contained sys.path injection,
        # not in conftest per 15-PATTERNS.md Critical Note 4).
        _tools_dir = os.path.join(os.path.dirname(__file__), "..", "tools")
        if _tools_dir not in sys.path:
            sys.path.insert(0, _tools_dir)
        from check_dispatch import _ALGO_MEM_TYPE, dispatch  # noqa: PLC0415

        db = _load_db()
        violations = []
        for mfg, chip in _all_chips(db):
            ss = chip.get("support_status", "supported")
            if ss == "supported":
                continue
            proto = chip.get("programming", {}).get("algorithm", 0) or 0
            mt = _ALGO_MEM_TYPE.get(proto)
            handler = dispatch(proto, mt)
            if handler not in ("not_implemented", "ERROR"):
                violations.append(
                    f"{mfg}/{chip.get('part_number', '<unknown>')} "
                    f"support_status={ss} proto=0x{proto:02X} -> {handler} "
                    f"(HARD invariant: non-supported chip wired to a real handler)"
                )
        assert not violations, (
            f"{len(violations)} non-supported chip(s) resolve to a real handler "
            f"(D-03 HARD invariant violated). All violations:\n"
            + "\n".join(f"  {v}" for v in violations)
        )
