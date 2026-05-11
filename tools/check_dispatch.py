"""
Regression scan: assert every chip in minipro_complete_db.json reaches a real
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

# Module-top path constants (mirrors firestarter_app/tools/build_db.py:11-13)
_DATA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "firestarter", "data"
)
DB_FILE = os.environ.get(
    "FIRESTARTER_DB_FILE",
    os.path.join(_DATA_DIR, "minipro_complete_db.json"),
)

# Algorithm (minipro protocol_id) → firmware mem_type integer.
# Must mirror firestarter_app/firestarter/database.py::_ALGO_MEM_TYPE
# (lands in Plan 03 per CONTEXT.md D3).
_ALGO_MEM_TYPE = {
    0x05: 5,   # FLASH_AMD_STD     → TYPE_FLASH_TYPE_4
    0x06: 3,   # FLASH_AMD_ALT     → TYPE_FLASH_TYPE_3
    0x07: 1,   # EPROM_STD         → TYPE_EPROM
    0x08: 1,   # EPROM_QUICK       → TYPE_EPROM
    0x0B: 1,   # EPROM_LEGACY      → TYPE_EPROM
    0x0D: 1,   # EEPROM_POLL       → TYPE_EPROM (firmware dispatches on protocol prefix)
    0x0E: 4,   # SRAM_32PIN        → TYPE_SRAM
    0x10: 1,   # FLASH_INTEL       → TYPE_EPROM (firmware dispatches on protocol prefix)
    0x27: 4,   # SRAM_24PIN        → TYPE_SRAM
    0x28: 4,   # SRAM_STD          → TYPE_SRAM
    0x29: 4,   # SRAM_512K_1M      → TYPE_SRAM
    0x35: 5,   # FLASH_EEPROM_LIKE → TYPE_FLASH_TYPE_4
    0x39: 5,   # FLASH_INTEL_ALT   → TYPE_FLASH_TYPE_4
}

# SRAM protocol set — these MUST route to configure_sram, never configure_eprom
# (BLOCKER-2 electrical-safety; configure_eprom calls eprom_check_vpp which
# enables the VPP boost regulator on a 5V SRAM part).
_SRAM_PROTOCOLS = {0x0E, 0x27, 0x28, 0x29}

# DIP28_2764 + Flash/EEPROM hazard guard (WARNING-5).
# Chips whose pinout == _28C_EEPROM_HAZARD_PINOUT AND electrical.type == "Flash/EEPROM"
# must NOT route to `configure_eprom`. configure_eprom would assert P1_VPP_ENABLE,
# applying 12V to socket pin 1 — which on the DIP28_2764 pinout is A14 on these
# 5V parallel EEPROMs (AT28C-family, MICROCHIP 28C*, NEC UPD28C*, XICOR X28C*, etc.).
# The safe handler is `configure_eeprom28c` (algorithm=0x0D); chips reach it after
# Plan 02 regenerates the DB with `_PROTOCOL_OVERRIDES` in build_db.py.
# See WARNING-5 in .planning/v1.0-MILESTONE-AUDIT.md.
_28C_EEPROM_HAZARD_PINOUT = "DIP28_2764"


def dispatch(protocol, mem_type):
    """Mirror firmware D2 dispatch order in memory.cpp::configure_memory."""
    if protocol == 0x10:                                   return "configure_flash_intel"
    if protocol == 0x0D:                                   return "configure_eeprom28c"
    if protocol == 0x06:                                   return "configure_flash3"
    if protocol in (0x05, 0x35, 0x39):                     return "configure_flash4"
    if protocol in (0x07, 0x08, 0x0B):                     return "configure_eprom"
    if protocol in (0x0E, 0x27, 0x28, 0x29):               return "configure_sram"
    # mem_type fallback chain (matches memory.cpp:83-95)
    return {
        1: "configure_eprom",
        4: "configure_sram",
        3: "configure_flash3",
        5: "configure_flash4",
    }.get(mem_type, "ERROR")


def main():
    """Entry point: scan DB and exit non-zero if any chip lacks a dispatch path."""
    with open(DB_FILE, encoding="utf-8") as f:
        db = json.load(f)

    errors = []
    sram_in_eprom = []
    eeprom28c_in_eprom = []
    total = 0
    for mfg, chips in db.items():
        if not isinstance(chips, list):
            continue
        for chip in chips:
            total += 1
            proto = chip.get("programming", {}).get("algorithm", 0) or 0
            mt = _ALGO_MEM_TYPE.get(proto)
            handler = dispatch(proto, mt)
            part = chip.get("part_number", "<unknown>")
            if handler == "ERROR":
                errors.append(
                    f"{mfg}/{part} proto=0x{proto:02X} mem_type={mt}"
                )
                continue
            # BLOCKER-2 safety: SRAM protocol must never resolve to configure_eprom
            if proto in _SRAM_PROTOCOLS and handler == "configure_eprom":
                sram_in_eprom.append(
                    f"{mfg}/{part} proto=0x{proto:02X} mem_type={mt}"
                )
            # WARNING-5 safety: DIP28_2764 + Flash/EEPROM chips must NOT route to
            # configure_eprom (12V P1_VPP_ENABLE would hit A14 on the 5V part).
            pinout = chip.get("pinout", "")
            etype = chip.get("electrical", {}).get("type", "")
            if (
                pinout == _28C_EEPROM_HAZARD_PINOUT
                and etype == "Flash/EEPROM"
                and handler == "configure_eprom"
            ):
                eeprom28c_in_eprom.append(
                    f"{mfg}/{part} proto=0x{proto:02X} pinout={pinout}"
                )

    if errors or sram_in_eprom or eeprom28c_in_eprom:
        if errors:
            print(
                f"FAIL: {len(errors)} of {total} chips have no valid dispatch path:"
            )
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
                f"FAIL: {len(eeprom28c_in_eprom)} DIP28_2764 Flash/EEPROM chips "
                f"route to configure_eprom (WARNING-5: 12V on A14 hazard):"
            )
            for e in eeprom28c_in_eprom[:20]:
                print(f"  {e}")
            if len(eeprom28c_in_eprom) > 20:
                print(f"  ... and {len(eeprom28c_in_eprom) - 20} more")
        sys.exit(1)

    print(
        f"PASS: all {total} chips have a valid dispatch path; "
        f"0 SRAM chips route to configure_eprom; "
        f"0 DIP28_2764 Flash/EEPROM chips route to configure_eprom"
    )


if __name__ == "__main__":
    main()
