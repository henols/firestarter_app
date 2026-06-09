"""
Regression scan: assert every chip in chip_database.json reaches a real
firmware dispatch path after Phase 12.

Mirrors the post-Phase-12 dispatch order documented in
firestarter/src/proms/memory.cpp::configure_memory and the algorithm→mem_type
table documented in firestarter_app/firestarter/database.py::_ALGO_MEM_TYPE
(both land later in Wave 1 / Wave 2 of phase-12).

Exit codes:
  0 — every chip in the DB resolves to a real handler AND no SRAM-protocol
      chip (0x0E/0x27/0x28/0x29) would dispatch to `configure_eprom`
      (BLOCKER-2 electrical-safety guard).
  1 — at least one chip would hit "Memory type 0x%02x not supported", OR a
      SRAM-protocol chip's simulated dispatch resolves to configure_eprom.
"""

import json
import os
import sys

from firestarter.database import EpromDatabase

# Module-top path constants (mirrors firestarter_app/tools/build_db.py:11-13)
_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "firestarter", "data")
DB_FILE = os.environ.get(
    "FIRESTARTER_DB_FILE",
    os.path.join(_DATA_DIR, "chip_database.json"),
)
PINOUTS_FILE = os.environ.get(
    "FIRESTARTER_PINOUTS_FILE",
    os.path.join(_DATA_DIR, "pinouts.json"),
)

# Algorithm integer (upstream protocol_id from infoic.xml) → firmware mem_type integer.
# Must mirror firestarter_app/firestarter/database.py::_ALGO_MEM_TYPE
# (lands in Plan 03 per CONTEXT.md D3).
_ALGO_MEM_TYPE = {
    0x05: 5,  # FLASH_AMD_STD     → TYPE_FLASH_TYPE_4
    0x06: 3,  # FLASH_AMD_ALT     → TYPE_FLASH_TYPE_3
    0x07: 1,  # EPROM_STD         → TYPE_EPROM
    0x08: 1,  # EPROM_QUICK       → TYPE_EPROM
    0x0B: 1,  # EPROM_LEGACY      → TYPE_EPROM
    0x0D: 1,  # EEPROM_POLL       → TYPE_EPROM (firmware dispatches on protocol prefix)
    0x0E: 4,  # SRAM_32PIN        → TYPE_SRAM
    0x10: 1,  # FLASH_INTEL       → TYPE_EPROM (firmware dispatches on protocol prefix)
    0x27: 4,  # SRAM_24PIN        → TYPE_SRAM
    0x28: 4,  # SRAM_STD          → TYPE_SRAM
    0x29: 4,  # SRAM_512K_1M      → TYPE_SRAM
}

# SRAM protocol set — these MUST route to configure_sram, never configure_eprom
# (BLOCKER-2 electrical-safety; configure_eprom calls eprom_check_vpp which
# enables the VPP boost regulator on a 5V SRAM part).
_SRAM_PROTOCOLS = {0x0E, 0x27, 0x28, 0x29}

# DIP28_2764 + Flash/EEPROM hazard guard (WARNING-5).
# Chips whose pinout == _28C_EEPROM_HAZARD_PINOUT AND electrical.type is in the
# 5V-EEPROM type set must NOT route to `configure_eprom`. configure_eprom would
# assert P1_VPP_ENABLE, applying 12V to socket pin 1 — which on the DIP28_2764
# pinout is A14 on these 5V parallel EEPROMs (AT28C-family, MICROCHIP 28C*,
# NEC UPD28C*, XICOR X28C*, etc.).
# The safe handler is `configure_eeprom28c` (algorithm=0x0D); chips reach it after
# Plan 02 regenerates the DB with `_PROTOCOL_OVERRIDES` in build_db.py.
# Note: DIP28_2764 DOES have a vpp-pin (pin 1), so the structural no-vpp-pin guard
# below cannot catch this hazard — this type-keyed guard is the correct net here.
# "EEPROM" is added alongside "Flash/EEPROM" so that any future chip reclassified
# to electrical.type="EEPROM" (e.g. cca7d62-style type migration) on this pinout
# is also caught.
# See WARNING-5 in .planning/v1.0-MILESTONE-AUDIT.md.
_28C_EEPROM_HAZARD_PINOUT = "DIP28_2764"
_28C_EEPROM_HAZARD_ETYPES = {"Flash/EEPROM", "EEPROM"}


def dispatch(protocol, mem_type):
    """Mirror firmware D2 dispatch order in memory.cpp::configure_memory."""
    if protocol == 0x10:
        return "configure_flash_intel"
    if protocol == 0x0D:
        return "configure_eeprom28c"
    if protocol == 0x06:
        return "configure_flash3"
    if protocol == 0x05:
        return "configure_flash4"
    if protocol in (0x07, 0x08, 0x0B):
        return "configure_eprom"
    if protocol in (0x0E, 0x27, 0x28, 0x29):
        return "configure_sram"
    # mem_type fallback chain (matches memory.cpp:83-95)
    return {
        1: "configure_eprom",
        4: "configure_sram",
        3: "configure_flash3",
        5: "configure_flash4",
    }.get(mem_type, "ERROR")


def _build_no_vpp_pin_set(pinouts_file):
    """Return the set of pinout keys that have no 'vpp-pin' entry in their pins dict.

    These pinouts have no physical VPP line routed to the socket.  If
    configure_eprom ever asserts P1_VPP_ENABLE on a chip sitting on one of
    these pinouts, the 12 V boost regulator drives a socket pin that is
    actually an address, WE, or NC line on the resident chip — a structural
    VPP hazard that is independent of electrical.type string labelling.
    """
    with open(pinouts_file, encoding="utf-8") as f:
        pinouts = json.load(f)
    return {k for k, v in pinouts.items() if not v.get("pins", {}).get("vpp-pin")}


def main():
    """Entry point: scan DB and exit non-zero if any chip lacks a dispatch path."""
    with open(DB_FILE, encoding="utf-8") as f:
        db_raw = json.load(f)

    # GATE-03 structural guard: build the set of pinouts with no vpp-pin.
    # This is loaded once here and used per-chip in the scan loop below.
    no_vpp_pin_pinouts = _build_no_vpp_pin_set(PINOUTS_FILE)

    # WIRE-02 (D-15 Shape A): host-side wire-emit round-trip surface.
    # Per-chip we call db.convert_to_programmer(db.get_eprom(part)) and assert
    # the produced wire dict contains canonical "vpp_mv" (Plan 02-01 contract)
    # and never the legacy "vpp" key.
    db = EpromDatabase()

    errors = []
    sram_in_eprom = []
    eeprom28c_in_eprom = []
    novpp_in_eprom = []
    vpp_eeprom_in_eprom = []
    wire_regressions = []
    total = 0
    for mfg, chips in db_raw.items():
        if not isinstance(chips, list):
            continue
        for chip in chips:
            total += 1
            proto = chip.get("programming", {}).get("algorithm", 0) or 0
            mt = _ALGO_MEM_TYPE.get(proto)
            handler = dispatch(proto, mt)
            part = chip.get("part_number", "<unknown>")
            if handler == "ERROR":
                errors.append(f"{mfg}/{part} proto=0x{proto:02X} mem_type={mt}")
                continue
            # BLOCKER-2 safety: SRAM protocol must never resolve to configure_eprom
            if proto in _SRAM_PROTOCOLS and handler == "configure_eprom":
                sram_in_eprom.append(f"{mfg}/{part} proto=0x{proto:02X} mem_type={mt}")
            pinout = chip.get("pinout", "")
            etype = chip.get("electrical", {}).get("type", "")
            # WARNING-5 safety: DIP28_2764 + 5V-EEPROM chips must NOT route to
            # configure_eprom (12V P1_VPP_ENABLE would hit A14 on the 5V part).
            # DIP28_2764 DOES have a vpp-pin (pin 1), so the structural guard below
            # cannot catch this case — the type-keyed guard is the correct net here.
            # The type set covers both "Flash/EEPROM" and "EEPROM" so that chips
            # reclassified by cca7d62-style type migrations are also caught.
            if (
                pinout == _28C_EEPROM_HAZARD_PINOUT
                and etype in _28C_EEPROM_HAZARD_ETYPES
                and handler == "configure_eprom"
            ):
                eeprom28c_in_eprom.append(
                    f"{mfg}/{part} proto=0x{proto:02X} pinout={pinout}"
                )
            # GATE-03 PRIMARY structural guard (type-string-independent):
            # configure_eprom asserts the 12 V VPP boost regulator on the pinout's
            # vpp-pin. If the pinout has NO vpp-pin, the regulator would drive a
            # socket pin that is actually an address, WE, or NC line on the resident
            # chip → hardware damage. This guard is intentionally type-string-
            # independent so it auto-covers any future electrical.type label
            # (EEPROM, Flash/EEPROM, or anything else) without needing to track
            # type-string churn. This is the lesson from the Phase 57 dead-predicate
            # and the cca7d62 EEPROM reclassification.
            if handler == "configure_eprom" and pinout in no_vpp_pin_pinouts:
                novpp_in_eprom.append(
                    f"{mfg}/{part} proto=0x{proto:02X} pinout={pinout}"
                )
            # GATE-03: type-string backstop — any chip whose electrical type is
            # Flash/EEPROM (a 5V part) must NOT route to configure_eprom, which
            # asserts 12V P1_VPP_ENABLE. This is pinout-agnostic and is a true
            # superset of the WARNING-5 DIP28_2764 check above. The earlier keying
            # on a 5V-EEPROM-family *algorithm* set {0x05,0x06,0x0D} was a dead
            # predicate: dispatch() never returns configure_eprom for those protocols,
            # so the guard could never fire. The hazardous chips are the ones that DO
            # reach configure_eprom (0x07/0x08/0x0B) while still being 5V Flash/EEPROM
            # parts. NOTE: "EEPROM"-typed chips on real vpp-pin pinouts (e.g. W27C512
            # on DIP28_27512) legitimately need 12V and must NOT be added here.
            if etype == "Flash/EEPROM" and handler == "configure_eprom":
                vpp_eeprom_in_eprom.append(
                    f"{mfg}/{part} proto=0x{proto:02X} pinout={pinout}"
                )

            # WIRE-02 (D-15 Shape A): assert wire emits "vpp_mv" and no legacy
            # "vpp" for every chip. Chips not registered in EpromDatabase's
            # index (rare) skip the wire assert; the dispatch scan above still
            # covers them.
            mapped = db.get_eprom(part)
            if mapped:
                wire = db.convert_to_programmer(mapped)
                if "vpp_mv" not in wire:
                    wire_regressions.append(f"{mfg}/{part} — missing vpp_mv on wire")
                if "vpp" in wire:
                    wire_regressions.append(
                        f"{mfg}/{part} — legacy vpp key still emitted on wire"
                    )

    if (
        errors
        or sram_in_eprom
        or eeprom28c_in_eprom
        or novpp_in_eprom
        or vpp_eeprom_in_eprom
        or wire_regressions
    ):
        if errors:
            print(f"FAIL: {len(errors)} of {total} chips have no valid dispatch path:")
            for e in errors[:20]:
                print(f"  {e}")
            if len(errors) > 20:
                print(f"  ... and {len(errors) - 20} more")
        if sram_in_eprom:
            print(
                f"FAIL: {len(sram_in_eprom)} SRAM chips route to "
                f"configure_eprom (BLOCKER-2 electrical-safety hazard):"
            )
            for e in sram_in_eprom[:20]:
                print(f"  {e}")
            if len(sram_in_eprom) > 20:
                print(f"  ... and {len(sram_in_eprom) - 20} more")
        if eeprom28c_in_eprom:
            print(
                f"FAIL: {len(eeprom28c_in_eprom)} DIP28_2764 5V-EEPROM chips "
                f"route to configure_eprom (WARNING-5: 12V on A14 hazard):"
            )
            for e in eeprom28c_in_eprom[:20]:
                print(f"  {e}")
            if len(eeprom28c_in_eprom) > 20:
                print(f"  ... and {len(eeprom28c_in_eprom) - 20} more")
        if novpp_in_eprom:
            print(
                f"FAIL: {len(novpp_in_eprom)} chips route to configure_eprom "
                f"on a pinout with no vpp-pin "
                f"(GATE-03 structural VPP hazard — type-string-independent):"
            )
            for e in novpp_in_eprom[:20]:
                print(f"  {e}")
            if len(novpp_in_eprom) > 20:
                print(f"  ... and {len(novpp_in_eprom) - 20} more")
        if vpp_eeprom_in_eprom:
            print(
                f"FAIL: {len(vpp_eeprom_in_eprom)} Flash/EEPROM chips "
                f"route to configure_eprom (GATE-03: 12V-on-5V-part hazard):"
            )
            for e in vpp_eeprom_in_eprom[:20]:
                print(f"  {e}")
            if len(vpp_eeprom_in_eprom) > 20:
                print(f"  ... and {len(vpp_eeprom_in_eprom) - 20} more")
        if wire_regressions:
            print(f"FAIL: {len(wire_regressions)} wire-key regressions:")
            for e in wire_regressions[:20]:
                print(f"  {e}")
            if len(wire_regressions) > 20:
                print(f"  ... and {len(wire_regressions) - 20} more")
        sys.exit(1)

    print(
        f"PASS: all {total} chips have a valid dispatch path; "
        f"0 SRAM chips route to configure_eprom; "
        f"0 DIP28_2764 5V-EEPROM chips route to configure_eprom; "
        f"0 chips on no-vpp-pin pinouts route to configure_eprom; "
        f"0 Flash/EEPROM chips route to configure_eprom; "
        f"0 wire-key regressions"
    )


if __name__ == "__main__":
    main()
