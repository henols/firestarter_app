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
    0x35: 5,  # FLASH_EEPROM_LIKE → TYPE_FLASH_TYPE_4
    0x39: 5,  # FLASH_INTEL_ALT   → TYPE_FLASH_TYPE_4
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

# D-10 consistency assertion 2: a chip tagged support_status=protocol-not-implemented
# must genuinely have an unimplemented protocol (i.e. proto NOT in KNOWN_PROTOCOLS).
# IMPORTANT: this set is the INCLUSION-GATE mirror of build_db.py's KNOWN_PROTOCOLS.
# It is intentionally a SUBSET of that set: 0x34 (XICOR X88C64P) is in build_db.py's
# KNOWN_PROTOCOLS so it passes the inclusion gate and gets tagged
# protocol-not-implemented, but 0x34 is NOT in this set because assertion 2 relies on
# X88C64P having proto=0x34 NOT in this set — that is what makes the assertion pass for
# that chip. Do NOT add 0x34 here ("keep in sync" means structure, not membership).
KNOWN_PROTOCOLS = {
    0x05,
    0x06,
    0x07,
    0x08,
    0x0B,
    0x0D,
    0x0E,
    0x10,
    0x27,
    0x28,
    0x29,
    0x35,
    0x39,
}


def dispatch(protocol, mem_type):
    """Mirror firmware D2 dispatch order in memory.cpp::configure_memory."""
    if protocol == 0x10:
        return "configure_flash_intel"  # noqa: E701
    if protocol == 0x0D:
        return "configure_eeprom28c"  # noqa: E701
    if protocol == 0x06:
        return "configure_flash3"  # noqa: E701
    if protocol in (0x05, 0x35, 0x39):
        return "configure_flash4"  # noqa: E701
    if protocol in (0x07, 0x08, 0x0B):
        return "configure_eprom"  # noqa: E701
    if protocol in (0x0E, 0x27, 0x28, 0x29):
        return "configure_sram"  # noqa: E701
    # Phase-64 mirror: non-zero unrecognized protocol → not_implemented
    # (In firmware: protocol != 0 guard before the mem_type chain)
    if protocol != 0:
        return "not_implemented"
    # mem_type fallback chain — protocol == 0 only (backward-compat)
    return {
        1: "configure_eprom",
        4: "configure_sram",
        3: "configure_flash3",
        5: "configure_flash4",
    }.get(mem_type, "ERROR")


def main():
    """Entry point: scan DB and exit non-zero if any chip lacks a dispatch path."""
    with open(DB_FILE, encoding="utf-8") as f:
        db_raw = json.load(f)

    # WIRE-02 (D-15 Shape A): host-side wire-emit round-trip surface.
    # Per-chip we call db.convert_to_programmer(db.get_eprom(part)) and assert
    # the produced wire dict contains canonical "vpp_mv" (Plan 02-01 contract)
    # and never the legacy "vpp" key.
    db = EpromDatabase()

    errors = []
    not_implemented = []
    sram_in_eprom = []
    eeprom28c_in_eprom = []
    wire_regressions = []
    # D-10 Assertion 1: every non-supported chip must have a non-empty unsupported_reason.
    missing_reason = []
    # D-10 Assertion 2: a protocol-not-implemented chip must genuinely have an unimplemented
    # protocol (proto not in KNOWN_PROTOCOLS — would indicate a DB build bug).
    pni_with_known_proto = []
    # D-10 Assertion 3: no supported chip resolves to not_implemented (enforced above
    # in the per-chip loop via the reworked not_implemented bucket — no separate list needed).
    # SC#3 / D-03 HARD inverse guard: non-supported chip wired to a real handler is a
    # gate failure. check_dispatch previously only checked the regression direction
    # (supported → not_implemented); this bucket catches the dangerous inverse.
    non_supported_dispatchable = []
    total = 0
    non_supported_count = 0
    non_dispatchable_count = 0
    for mfg, chips in db_raw.items():
        if not isinstance(chips, list):
            continue
        for chip in chips:
            total += 1
            proto = chip.get("programming", {}).get("algorithm", 0) or 0
            mt = _ALGO_MEM_TYPE.get(proto)
            handler = dispatch(proto, mt)
            part = chip.get("part_number", "<unknown>")
            # D-10 consistency assertions: populate for every chip regardless of handler.
            chip_ss = chip.get("support_status", "supported")
            if chip_ss != "supported":
                non_supported_count += 1
                # Assertion 1: non-supported chip must have a non-empty unsupported_reason.
                if not chip.get("unsupported_reason", ""):
                    missing_reason.append(
                        f"{mfg}/{part} support_status={chip_ss} — missing unsupported_reason"
                    )
                # Assertion 2: protocol-not-implemented chip must have an actually-unimplemented
                # protocol.  If it has a known protocol, the DB build is inconsistent.
                if chip_ss == "protocol-not-implemented" and proto in KNOWN_PROTOCOLS:
                    pni_with_known_proto.append(
                        f"{mfg}/{part} proto=0x{proto:02X} — protocol IS in KNOWN_PROTOCOLS"
                    )
                # SC#3 / D-03 HARD inverse guard: a non-supported chip routing to a real
                # handler is the dangerous inverse regression — non-supported chips must
                # dispatch to not_implemented or ERROR only (CR-02 / Phase 66 gap-closure).
                if handler not in ("not_implemented", "ERROR"):
                    non_supported_dispatchable.append(
                        f"{mfg}/{part} support_status={chip_ss} proto=0x{proto:02X} "
                        f"-> {handler} "
                        f"(HARD invariant: non-supported chip wired to a working handler)"
                    )
                else:
                    # Non-supported chip correctly dispatching to a non-dispatchable
                    # outcome — count these for the truthful PASS summary line.
                    non_dispatchable_count += 1
            if handler == "ERROR":
                if chip_ss == "supported":
                    # A supported chip with no dispatch path is a real gate failure.
                    errors.append(f"{mfg}/{part} proto=0x{proto:02X} mem_type={mt}")
                # else: non-supported chip dispatching to ERROR is the expected outcome
                # (NON_DISPATCHABLE_ALGO=0x00 → dispatch returns ERROR; D-03 HARD enforced).
                continue
            if handler == "not_implemented":
                if chip_ss == "supported":
                    # Regression: a supported chip routed to not_implemented is a gate failure.
                    not_implemented.append(
                        f"{mfg}/{part} proto=0x{proto:02X} support_status={chip_ss}"
                    )
                # else: expected — protocol-not-implemented/adapter-required/vpp-exceeds-max
                # chips correctly route to not_implemented (no handler exists; that is the point).
                continue  # skip VPP/wire checks — no real handler to evaluate
            # BLOCKER-2 safety: SRAM protocol must never resolve to configure_eprom
            if proto in _SRAM_PROTOCOLS and handler == "configure_eprom":
                sram_in_eprom.append(f"{mfg}/{part} proto=0x{proto:02X} mem_type={mt}")
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
        or not_implemented
        or sram_in_eprom
        or eeprom28c_in_eprom
        or wire_regressions
        or missing_reason
        or pni_with_known_proto
        or non_supported_dispatchable
    ):
        if errors:
            print(f"FAIL: {len(errors)} of {total} chips have no valid dispatch path:")
            for e in errors[:20]:
                print(f"  {e}")
            if len(errors) > 20:
                print(f"  ... and {len(errors) - 20} more")
        if not_implemented:
            print(
                f"FAIL: {len(not_implemented)} chips route to not_implemented "
                f"(supported chip with no dispatch handler — protocol regression):"
            )
            for e in not_implemented[:20]:
                print(f"  {e}")
            if len(not_implemented) > 20:
                print(f"  ... and {len(not_implemented) - 20} more")
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
        if wire_regressions:
            print(f"FAIL: {len(wire_regressions)} wire-key regressions:")
            for e in wire_regressions[:20]:
                print(f"  {e}")
            if len(wire_regressions) > 20:
                print(f"  ... and {len(wire_regressions) - 20} more")
        if missing_reason:
            print(
                f"FAIL: {len(missing_reason)} non-supported chips are missing "
                f"unsupported_reason (D-10 assertion 1):"
            )
            for e in missing_reason[:20]:
                print(f"  {e}")
            if len(missing_reason) > 20:
                print(f"  ... and {len(missing_reason) - 20} more")
        if pni_with_known_proto:
            print(
                f"FAIL: {len(pni_with_known_proto)} protocol-not-implemented chips "
                f"have a protocol that IS in KNOWN_PROTOCOLS (D-10 assertion 2 — DB build bug):"
            )
            for e in pni_with_known_proto[:20]:
                print(f"  {e}")
            if len(pni_with_known_proto) > 20:
                print(f"  ... and {len(pni_with_known_proto) - 20} more")
        if non_supported_dispatchable:
            print(
                f"FAIL: {len(non_supported_dispatchable)} non-supported chips dispatch "
                f"to a REAL handler (SC#3 / D-03 HARD invariant — must be "
                f"not_implemented/ERROR):"
            )
            for e in non_supported_dispatchable[:20]:
                print(f"  {e}")
            if len(non_supported_dispatchable) > 20:
                print(f"  ... and {len(non_supported_dispatchable) - 20} more")
        sys.exit(1)

    supported_count = total - non_supported_count
    print(
        f"PASS: all {total} chips scanned; "
        f"{supported_count} supported; "
        f"{non_dispatchable_count} chips confirmed non-dispatchable "
        f"(handler in not_implemented/ERROR); "
        f"0 non_supported_dispatchable; "
        f"0 dispatch regressions; 0 consistency violations"
    )


if __name__ == "__main__":
    main()
