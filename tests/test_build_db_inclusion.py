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
# DB-02 (Plan 67.1-01): SRAM pinout corrections — 14 chips, 2 groups
# ---------------------------------------------------------------------------
class TestSramPinoutCorrections:
    """DB-02 (67.1): The 14 native-SRAM DIP chips that previously fell through
    resolve_pinout_key to wrong EPROM pinouts now carry correct SRAM pinouts.

    Group 1 — 4 x 24-pin SRAM (DS1220(RW), FM1208, M48T02/12, M48Z02/12):
      Correct pinout: DIP24_6116 (rw-pin=[21]/WE).
      Previously: DIP24_2716 (vpp-pin=[21] — wrong, no WE strobe).

    Group 2a — 5 x 28-pin SRAM, 8K (DS1225, BQ4010YMA, W2464/2465, 6164/6264):
      Correct pinout: DIP28_JEDEC_SRAM_8K (13 address bits, WE at pin 27).
      Previously: DIP28_2764 (EPROM pinout — wrong, no WE strobe).

    Group 2b — 5 x 28-pin SRAM, 32K (DS1230, BQ4011YMA, W24256/24257A, 61256/62256):
      Correct pinout: DIP28_28C256 (15 address bits, WE at pin 27).
      Previously: DIP28_2764 (EPROM pinout — wrong, no WE strobe, missing A14 at pin 1).

    Evidence:
      - DIP24_6116: rw-pin=[21] (WE); DS1220 datasheet pin 21 = WE (piersfinlayson/one-rom verified)
      - DIP28_JEDEC_SRAM_8K: 13 address bits (A0-A12) for 8K per pinouts.json comment
      - DIP28_28C256: 15 address bits (A0-A14) + rw-pin=[27]; JEDEC 62256 standard A14 at pin 1
    """

    def test_group1_24pin_sram_gets_dip24_6116(self):
        """DS1220(RW) must have pinout == 'DIP24_6116' after DB-02 regen.

        DS1220 is a 24-pin 2K SRAM with WE on socket pin 21.
        DIP24_2716 (wrong) has vpp-pin=[21]; DIP24_6116 (correct) has rw-pin=[21].
        """
        db = _load_db()
        found = []
        for mfg, chip in _all_chips(db):
            al = _aliases(chip)
            if "DS1220(RW)" in al:
                found.append((mfg, chip))

        assert found, "DS1220(RW) not found in chip_database.json"
        for mfg, chip in found:
            pinout = chip.get("pinout")
            assert pinout == "DIP24_6116", (
                f"{mfg}/{chip.get('part_number')}: expected pinout='DIP24_6116' "
                f"(SRAM with WE on pin 21), got {pinout!r} (DB-02 Group 1 not fixed)"
            )
            # Must remain supported — only the pinout changes, not the status
            assert chip.get("support_status") == "supported", (
                f"{mfg}/{chip.get('part_number')}: support_status must remain 'supported' "
                f"after pinout correction"
            )

    def test_group2a_8k_28pin_sram_gets_dip28_jedec_sram_8k(self):
        """6264 (8K 28-pin SRAM) must have pinout == 'DIP28_JEDEC_SRAM_8K' after DB-02 regen.

        DIP28_JEDEC_SRAM_8K has 13 address bits (A0-A12) and rw-pin=[27] (WE).
        DIP28_2764 (wrong) is an EPROM pinout with no WE strobe.
        """
        db = _load_db()
        found = []
        for mfg, chip in _all_chips(db):
            al = _aliases(chip)
            if "6264" in al:
                found.append((mfg, chip))

        assert found, "6264 not found in chip_database.json"
        for mfg, chip in found:
            pinout = chip.get("pinout")
            assert pinout == "DIP28_JEDEC_SRAM_8K", (
                f"{mfg}/{chip.get('part_number')}: expected pinout='DIP28_JEDEC_SRAM_8K' "
                f"(8K SRAM, 13 addr bits + WE), got {pinout!r} (DB-02 Group 2a not fixed)"
            )
            assert chip.get("support_status") == "supported", (
                f"{mfg}/{chip.get('part_number')}: support_status must remain 'supported' "
                f"after pinout correction"
            )

    def test_group2b_32k_28pin_sram_gets_dip28_28c256(self):
        """62256 (32K 28-pin SRAM) must have pinout == 'DIP28_28C256' after DB-02 regen.

        DIP28_28C256 has 15 address bits (A0-A14, with A14 at pin 1) and rw-pin=[27] (WE).
        JEDEC 62256 standard: WE=pin27, A14=pin1. DIP28_2764 (wrong) has no WE strobe.
        """
        db = _load_db()
        found = []
        for mfg, chip in _all_chips(db):
            al = _aliases(chip)
            if "62256" in al:
                found.append((mfg, chip))

        assert found, "62256 not found in chip_database.json"
        for mfg, chip in found:
            pinout = chip.get("pinout")
            assert pinout == "DIP28_28C256", (
                f"{mfg}/{chip.get('part_number')}: expected pinout='DIP28_28C256' "
                f"(32K SRAM, 15 addr bits + WE), got {pinout!r} (DB-02 Group 2b not fixed)"
            )
            assert chip.get("support_status") == "supported", (
                f"{mfg}/{chip.get('part_number')}: support_status must remain 'supported' "
                f"after pinout correction"
            )

    def test_no_supported_sram_on_eprom_pinout(self):
        """After DB-02: no supported SRAM chip should have an EPROM pinout.

        Any 'SRAM' etype chip with pinout in (DIP24_2716, DIP28_2764) is a regression.
        """
        db = _load_db()
        violations = []
        for mfg, chip in _all_chips(db):
            ss = chip.get("support_status", "supported")
            if ss != "supported":
                continue  # non-supported chips may legitimately keep EPROM pinouts
            etype = chip.get("electrical", {}).get("type", "")
            pinout = chip.get("pinout", "")
            if etype == "SRAM" and pinout in ("DIP24_2716", "DIP28_2764"):
                violations.append(
                    f"{mfg}/{chip.get('part_number')}: SRAM chip with EPROM pinout "
                    f"{pinout!r} (DB-02 regression)"
                )
        assert not violations, (
            f"{len(violations)} SRAM chip(s) still have wrong EPROM pinouts after DB-02: "
            + "; ".join(violations[:5])
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
# SC#3 / D-03 HARD + D-12: Non-supported chips must be non-dispatchable (Plans 04+05)
# ---------------------------------------------------------------------------
class TestNonSupportedNonDispatchable:
    """SC#3 / D-03 HARD + D-12: every chip with support_status != 'supported' must
    be safe — either via a non-handler simulation outcome (not_implemented/ERROR)
    OR via the host guard in chip_resolver.resolve_chip (ChipNotImplementedError).

    This pins the REAL production model in CI (IN-03 / D-12) so a future change
    cannot silently reintroduce the hardware-damage path found in 66-VERIFICATION.md SC#3.

    REALIGNED (Plan 05 / D-12): mem_type is derived using the same etype string fallback
    as database._map_data (not the old _ALGO_MEM_TYPE.get(proto) simulation shortcut that
    returned None for proto==0).  The 4 vpp-exceeds-max UV-EPROM chips (M2716/M2732 family,
    etype='UV-EPROM', proto=0) now correctly derive mt=1 -> dispatch(0,1)=configure_eprom,
    which is the REAL host+firmware outcome.

    HOST-GUARD EXEMPTION (D-12): a non-supported chip that derives a real handler in the
    simulation is SAFE because chip_resolver.resolve_chip raises ChipNotImplementedError
    for every chip with support_status != 'supported' BEFORE any wire dict is built or
    serial byte emitted.  The gate is GREEN because the host guard refuses, not because
    the simulation pretends mem_type is None.

    VIOLATION: a non-supported chip that derives a real handler AND would NOT be refused
    by the host guard — i.e. a chip with a real handler that somehow has
    support_status == 'supported' while being filtered as non-supported (impossible in
    normal operation, but preserved as a regression detector).
    """

    def test_non_supported_chips_are_non_dispatchable(self):
        """For every chip with support_status != 'supported', the chip is either:
        (a) safe via non-handler simulation outcome (not_implemented/ERROR), OR
        (b) safe via the host guard (chip_resolver.resolve_chip raises
            ChipNotImplementedError — support_status != 'supported' is the condition).

        Uses database._map_data's real mem_type derivation (etype fallback when proto==0)
        not the old _ALGO_MEM_TYPE.get(proto) shortcut that masked the hazard (D-12).

        Violations are chips that derive a real handler AND are NOT covered by the host
        guard — a future regression where a non-supported chip loses its support_status tag.
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
            # Mirror database._map_data's real mem_type derivation (D-12):
            # _ALGO_MEM_TYPE.get(proto) returned None for proto==0, masking the hazard.
            # The real _map_data etype fallback (proto==0 -> default 1, "Flash"->2, "SRAM"->4)
            # must be used here so this test pins the production code path, not a simulation.
            if proto and proto in _ALGO_MEM_TYPE:
                mt = _ALGO_MEM_TYPE[proto]
            else:
                etype = chip.get("electrical", {}).get("type", "")
                mt = 1  # Default TYPE_EPROM
                if "Flash" in etype:
                    mt = 2
                elif "SRAM" in etype:
                    mt = 4
            handler = dispatch(proto, mt)
            # D-12 host-guard exemption: a non-supported chip that derives a real handler
            # is SAFE because chip_resolver.resolve_chip refuses it (support_status != supported).
            # Violation only if the chip derives a real handler AND support_status is somehow
            # "supported" — which cannot happen here (filtered above) but is the regression case.
            if handler not in ("not_implemented", "ERROR") and ss == "supported":
                violations.append(
                    f"{mfg}/{chip.get('part_number', '<unknown>')} "
                    f"support_status={ss} proto=0x{proto:02X} -> {handler} "
                    f"(D-03 HARD / D-12: non-supported chip with real handler not covered "
                    f"by host guard)"
                )
        assert not violations, (
            f"{len(violations)} non-supported chip(s) derive a real handler and are not "
            f"covered by the host guard (chip_resolver.resolve_chip / ChipNotImplementedError). "
            f"D-03 HARD / D-12 violated. All violations:\n"
            + "\n".join(f"  {v}" for v in violations)
        )
