import json
import os
import sys
import xml.etree.ElementTree as ET

import requests

# ==========================================
# 1. CONFIGURATION
# ==========================================
MINIPRO_XML_URL = "https://gitlab.com/DavidGriffith/minipro/-/raw/master/infoic.xml"
_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "firestarter", "data")
OUTPUT_FILE = os.path.join(_DATA_DIR, "chip_database.json")
PINOUT_FILE = os.path.join(_DATA_DIR, "pinouts.json")

# ==========================================
# 2. PINOUT LIBRARY (The Missing Physical Layer)
# ==========================================

# ==========================================
# 3. LOGIC MAPPERS
# ==========================================

# This map translates the numeric protocol ID from upstream's XML
# into a human-readable string that Firestarter's database uses.
# [VERIFIED: minipro database.h#L24-L77 @ a8efaedc — IC2_ALG_* constants]
PROTOCOL_MAP = {
    0x05: "FLASH_AMD_STD",  # IC2_ALG_F29EE
    0x06: "FLASH_AMD_ALT",  # IC2_ALG_W29F32P
    0x07: "EPROM_STD",  # IC2_ALG_ROM28P_1
    0x08: "EPROM_QUICK",  # IC2_ALG_ROM32P
    0x0B: "EPROM_LEGACY",  # IC2_ALG_ROM24P_1
    0x0D: "EEPROM_POLL",  # IC2_ALG_EE28C32P
    0x0E: "SRAM_32PIN",  # IC2_ALG_RAM32_1
    0x10: "FLASH_INTEL",  # IC2_ALG_28F32P
    0x27: "SRAM_24PIN",  # IC2_ALG_ROM24P_2
    0x28: "SRAM_STD",  # IC2_ALG_ROM28P_2
    0x29: "SRAM_512K_1M",  # IC2_ALG_RAM32_2
    # Excluded IDs documented here for traceability:
    # 0x11: IC2_ALG_FWH  — LPC 4-wire serial bus + 3.3V; infeasible on RURP
    # 0x2A: IC2_ALG_GAL16  — GAL16V8 PLD (type=3); no DIP memory chips
    # 0x2C: IC2_ALG_GAL22  — GAL22V10 PLD (type=3); no DIP memory chips
    # 0x2E: IC2_ALG_PIC32X_2 — PIC32 MCU (type=2); no DIP memory chips
    # 0x35: IC2_ALG_ITE  — ITE EC MCU TQFP128 (type=2); no DIP memory chips
    # 0x39: NO IC2_ALG CONSTANT — phantom; INFOIC2PLUS-unreachable
    # 0x3C: NOT IN MINIPRO SOURCE — invented; remove entirely
}

# Upstream infoic.xml caps VPP at 18V (0xF0), but a handful of antique
# Intel NMOS parts physically require higher programming voltages that the
# upstream schema cannot express. Known cases (preserved from the now-removed
# database_overrides.json):
#   - Intel M2716  → 25V VPP (original 1977 NMOS 2716)
#   - Intel M2732  → 25V VPP (NMOS 2732)
#   - Intel M2732A → 21V VPP (later 21V-VPP variant)
# These chips currently report 18V here because upstream aliases them under
# generic 2716/2732 entries — operator must override via ~/.firestarter/database.json
# before programming an original-NMOS Intel part. RURP shield max is ~22V so
# the 25V variants cannot be programmed on this hardware regardless.
#
# KEY DECODE NOTE: The VPP lookup key is (voltages & 0xF0), NOT (voltages & 0xFF).
# The low byte of the voltages field encodes:
#   bits 7-4 (high nibble): VPP voltage index — matches these table keys
#   bits 3-0 (low nibble):  option flags (powerdown-enable, T48 sub-options, etc.)
# All valid TL866II/RURP VPP codes are multiples of 0x10 (0x00=12V, 0x10=9V, etc.).
# Using the full byte (0xFF mask) causes a 0mV/Unknown result whenever bits 3-0 are
# nonzero (e.g. SST27VF512 voltages=0x0001: 0x01 not in table → was 0mV, now 12V).
# [VERIFIED: minipro/src/database.c + tl866a.c + tl866ii_vpp_voltages[] table]
VPP_VOLTAGES = {
    0x00: "12V",
    0x10: "9V",
    0x20: "9.5V",
    0x30: "10V",
    0x40: "11V",
    0x50: "11.5V",
    0x60: "12.5V",
    0x70: "13V",
    0x80: "13.5V",
    0x90: "14V",
    0xA0: "14.5V",
    0xB0: "15.5V",
    0xC0: "16V",
    0xD0: "16.5V",
    0xE0: "17V",
    0xF0: "18V",
}

VPP_MV = {
    0x00: 12000,
    0x10: 9000,
    0x20: 9500,
    0x30: 10000,
    0x40: 11000,
    0x50: 11500,
    0x60: 12500,
    0x70: 13000,
    0x80: 13500,
    0x90: 14000,
    0xA0: 14500,
    0xB0: 15500,
    0xC0: 16000,
    0xD0: 16500,
    0xE0: 17000,
    0xF0: 18000,
}

# NMOS VPP correction: promotes the comment above to applied code.
# Matched against part_number aliases; "highest VPP wins" for entries with
# multiple NMOS aliases (e.g., INTEL/2732,2732A,M2732,M2732A).
NMOS_TRUE_VPP_MV: dict[str, int] = {
    "M2716": 25000,  # Intel NMOS 2716: 25V VPP (datasheet)
    "M2732": 25000,  # Intel NMOS 2732: 25V VPP (datasheet)
    "M2732A": 21000,  # Intel NMOS 2732A: 21V VPP (later variant)
}
# RURP boost regulator theoretical ceiling (build_db.py comment + hw evidence).
# Chips requiring VPP above this cannot be programmed on any RURP revision.
RURP_VPP_CEILING_MV = 22000

# CR-01 Option A (Phase 66 gap-closure): algorithm sentinel for non-supported chips.
# dispatch(0x00, None) falls into the mem_type fallback chain (protocol==0 path):
#   _ALGO_MEM_TYPE.get(0x00) → None → {1:..., 4:..., 3:..., 5:...}.get(None, "ERROR")
#   → "ERROR"
# No real handler (configure_eprom / configure_eeprom28c / configure_flash* /
# configure_sram) is ever reached for a non-supported chip. D-03 HARD: do NOT
# route any flagged chip to a working handler.
NON_DISPATCHABLE_ALGO = 0x00

# [VERIFIED: canonical IC2_ALG_* constants from database.h#L24-L77 @ a8efaedc]
# 0x35 (IC2_ALG_ITE) and 0x39 (phantom — no IC2_ALG constant) removed:
# neither produces chips in the INFOIC2PLUS DIP-24..32 filter.
# 0x34 = XICOR X88C64P — DIP-parallel NovRAM; unimplemented protocol but
# confirmed DIP-parallel memory. Added here so the chip passes the
# KNOWN_PROTOCOLS gate and gets classified as protocol-not-implemented.
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
    0x34,  # XICOR X88C64P — DIP-parallel NovRAM; included as protocol-not-implemented
    # NOT 0x35 or 0x39 — removed by v1.11 DEC-05
}

# [VERIFIED: minipro database.c#L130-L135 @ a8efaedc — tl866ii_vcc_voltages[]]
VCC_VOLTAGES = {
    0x00: "5V",
    0x01: "3.3V",
    0x02: "4V",  # BUG-1 fix: was missing from v1.12
    0x03: "4.5V",  # BUG-1 fix: was missing from v1.12
    0x04: "5.5V",
    0x05: "6.5V",
}

# D-02: DIP28_VARIANT_MAP, PIN_MAP_TO_PINOUT, and PIN_MAP_PROTO_TO_PINOUT
# have been DELETED (Phase 58 Plan 02). The principled resolve_pinout_key
# function below is the sole pinout-selection path. See RESEARCH.md
# §"Full Principled Rule Structure" for derivation evidence.

with open(PINOUT_FILE) as _f:
    VALID_PINOUT_KEYS = set(json.load(_f).keys())

# ==========================================
# 4. PROCESSING FUNCTIONS
# ==========================================


def resolve_pinout_key(
    pin_count, variant, flags_int, pm_idx=None, proto_id=None, type_int=1, mem_size=0
):
    """Resolve the firestarter pinout key for a chip.

    Principled function (Phase 58 Plan 02): pinout key is a pure function of
    decoded minipro fields (pin_count, pm_idx, variant_lo, type_int, mem_size,
    proto_id). No per-IC names, no per-family lookup tables (D-02, D-03).
    pm_idx is the low byte of infoic.xml's pin_map attribute — it identifies
    the chip-family layout cluster. variant_lo (variant & 0xFF) sub-discriminates
    within a cluster.

    Returns a pinout key string (e.g., "DIP24_2816") or None if the chip cannot
    be classified; None triggers the D-06 fail-safe skip in main().

    [VERIFIED: exhaustive infoic.xml survey, all 24/28/32-pin DIP chips,
     MINIPRO_XML_URL @ commit a8efaedc — see
     .planning/phases/58-pinout-re-derivation-24-pin-eeprom-unblock/58-RESEARCH.md
     §"Full Principled Rule Structure"]
    """
    variant_lo = variant & 0xFF
    key = None

    if pin_count == 24:
        if pm_idx == 23:
            if variant_lo == 0x01:
                key = "DIP24_2732"  # 4KB UV-EPROM
            elif variant_lo == 0x10:
                # 28C-family EEPROM (AT28C04/16, XL2804/2816, AM28C16A, etc.)
                # variant_lo=0x10 is the reliable 28C-EEPROM discriminator —
                # do NOT rely on flags&0x10 here; many 28C parts have flags=0x0000
                # (e.g. AM28C16A, CAT28C16A, XL2804A — confirmed RESEARCH Pitfall 1).
                # [VERIFIED: infoic.xml — all (pm_idx=23, variant_lo=0x10) chips
                #  are the 28C family sharing the DIP24_2816 layout]
                key = "DIP24_2816"  # 5V EEPROM, rw-pin=21 (WE), no vpp-pin
            else:
                key = "DIP24_2716"  # default: 2KB UV-EPROM (variant_lo=0x00)
        elif pm_idx == 0:
            key = "DIP24_6116"  # SRAM-class (type=4 or proto=0x27)
        else:
            key = None  # D-06 fail-safe

    elif pin_count == 28:
        if pm_idx == 22:
            # 27C512/256/128/64 UV-EPROM family — variant_lo sub-discriminates.
            # CRITICAL (RESEARCH Pitfall 3): 0x10→27512 (VPP on pin 22) and
            # 0x11→27256 (VPP on pin 1) must not be swapped — 12V to wrong pin.
            # [VERIFIED: infoic.xml — pm_idx=22 is the 27Cxxx family group]
            if variant_lo == 0x10:
                key = "DIP28_27512"  # VPP on pin 22 (OE/VPP shared)
            elif variant_lo == 0x11:
                key = "DIP28_27256"  # VPP on pin 1
            else:
                key = "DIP28_2764"  # 27C128/27C64 layout
        elif pm_idx == 21:
            key = "DIP28_2764"  # 27C64 family; pm_idx unique to this family
        elif pm_idx == 20:
            key = "DIP28_28C256"  # 28C256 EEPROM; no VPP
        elif pm_idx == 19:
            key = "DIP28_28C64"  # 28C64 EEPROM; no VPP
        elif pm_idx == 18:
            key = "DIP28_28C64"  # 28C16/17 small EEPROM; same layout
        elif pm_idx == 0:
            # SRAM/NVRAM (type=4 or SRAM proto) or 5V flash (proto=0x05)
            if type_int == 4 or proto_id in {0x27, 0x28, 0x29}:
                # JEDEC SRAM; mem_size discriminates 8K vs 16K+
                if mem_size <= 8192:
                    key = "DIP28_JEDEC_SRAM_8K"
                else:
                    key = "DIP28_28C256"  # over-allocates; firmware uses mem_size
            elif proto_id == 0x05:
                key = "DIP28_28C256"  # AT29C256 5V flash; same layout class
            else:
                key = None
        else:
            key = None  # D-06 fail-safe

    elif pin_count == 32:
        if pm_idx == 0:
            # SRAM/NVRAM (type=4; proto 0x0E/0x29) — JEDEC 32-pin SRAM layout
            key = "DIP32_SST39SF040"  # WE=31, no VPP
        elif pm_idx in {5, 7, 9, 10, 11, 12, 13}:
            # Mixed flash/EPROM families — proto_id discriminates
            if proto_id in {0x05, 0x06}:
                key = "DIP32_SST39SF040"  # 5V flash; no VPP, WE=31
            elif proto_id == 0x0D:
                key = "DIP32_28C512_EEPROM"  # 5V EEPROM; WE=30, no VPP
            elif proto_id in {0x07, 0x08, 0x10}:
                key = "DIP32_STD"  # UV-EPROM / Intel-flash; VPP=pin 1
            else:
                key = None
        else:
            key = None  # D-06 fail-safe

    if key is not None and key not in VALID_PINOUT_KEYS:
        print(f"WARN: resolved pinout key '{key}' not in pinouts.json", file=sys.stderr)

    return key


def interpret_timing(raw_hex, protocol_id):
    # [VERIFIED: minipro database.c#L866 @ a8efaedc]
    # Raw pulse_delay is microseconds for ALL protocols — no multiplier.
    try:
        val = int(raw_hex, 16)
    except Exception:
        val = 0

    if protocol_id in (0x07, 0x08, 0x0B):
        return f"{val} us"

    return "Algorithm Controlled"


def main():
    print(f"Fetching database from: {MINIPRO_XML_URL}")
    try:
        r = requests.get(MINIPRO_XML_URL, timeout=30)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    complete_db = {}
    total_chips = 0

    print("Processing and enriching data...")

    for db in root.findall(".//database[@type='INFOIC2PLUS']"):
        for mfg in db.findall(".//manufacturer"):
            mfg_name = mfg.get("name")
            chips = []

            for ic in mfg.findall(".//ic"):
                name = ic.get("name")

                # --- FILTER: DIP PARALLEL ONLY ---
                try:
                    pkg_val = int(ic.get("package_details"), 16)
                    pin_count = (pkg_val & 0x7F000000) >> 24
                    is_smd = pkg_val & 0x80000000
                    is_serial = (pkg_val & 0x0000FF00) >> 8
                    type_int = int(ic.get("type"), 16)
                except Exception:
                    continue

                # Strict Filter: 24-32 pins, No SMD, Memory/SRAM types only
                if not (24 <= pin_count <= 32):
                    continue
                if is_smd or is_serial:
                    continue
                if type_int not in [1, 4]:
                    continue  # 1=Memory, 4=SRAM

                # --- DECODE RAW DATA ---
                variant = int(ic.get("variant"), 16)
                proto_id = int(ic.get("protocol_id"), 16)
                flags = int(ic.get("flags"), 16)
                voltages = int(ic.get("voltages"), 16)
                mem_size = int(ic.get("code_memory_size"), 16)
                # pm_idx: low byte of upstream pin_map field — clusters chips by
                # physical layout family (per minipro infoic.xml schema). Used by
                # resolve_pinout_key as the primary chip-family selector.
                pin_map_raw = int(ic.get("pin_map", "0"), 16)
                pm_idx = pin_map_raw & 0xFF

                # DB-07: Initialize support classification fields.
                # These defaults are overridden at the two inclusion gates below
                # and at the NMOS VPP override block before chip_entry construction.
                _support_status = "supported"
                _unsupported_reason = None
                _nmos_vpp_mv = None

                # Site A: Unknown-protocol gate.
                # X88C64P (proto 0x34) is a confirmed DIP-parallel NovRAM —
                # include it as protocol-not-implemented (not a WARN-skip).
                # All other unknown-protocol chips (DataFlash 0x04, FWH 0x11,
                # PLCC 0x0A) keep their WARN skip because they are serial/SMD
                # or adapter-class parts that cannot physically run on RURP.
                # 0x34 is in KNOWN_PROTOCOLS so the gate now passes it through;
                # the _support_status assignment handles classification.
                if proto_id not in KNOWN_PROTOCOLS:
                    print(
                        f"WARN: skipping {name} — unknown protocol_id 0x{proto_id:02X}",
                        file=sys.stderr,
                    )
                    continue
                if proto_id == 0x34:
                    _support_status = "protocol-not-implemented"
                    # DB-04 Approach A (67.1-01): reason string begins with SC-required
                    # wording so the host can render it verbatim (Plan 02 prints f"{e}").
                    # Must contain "not implemented" substring — existing test
                    # test_read_protocol_not_implemented_typed_refusal asserts it.
                    _unsupported_reason = (
                        "protocol not implemented: 0x34 (XICOR X88C64P — parallel DIP24 5V EEPROM, "
                        "8051 multiplexed-bus interface (ALE/WR/RD); feasible-candidate, handler not implemented)"
                    )

                # SAFETY SKIP / Site B: 24-pin 5V parallel EEPROMs routed via EPROM
                # algorithms (0x07/0x08/0x0B). Affected family per upstream:
                # AT28C04/16, AT28HC16, UPD28C04, 28C04A/16A — 5V single-supply
                # parallel EEPROMs with WE on socket pin 21 (per datasheet).
                # If we let them through with a working handler:
                #   - configure_eprom (0x07/0x08/0x0B dispatch) engages the
                #     12V VPP regulator and asserts VPP_ENABLE during writes.
                #   - DIP24_2716 pinout has vpp-pin=21 — so 12V hits the chip's
                #     WE pin → hardware-damage path.
                # DB-02: include as adapter-required (not a bare skip) so the DB
                # is a complete catalog. D-03 HARD: do NOT route to a working
                # handler — proto_id demoted to NON_DISPATCHABLE_ALGO, no DIP24
                # EEPROM handler wired.
                # Discriminator: pin_count == 24 AND proto_id in EPROM-family
                # AND flags has the "electrically erasable" bit (0x10).
                # ORDERING INVARIANT (Pitfall 6): Site B must fire BEFORE the
                # resolve_pinout_key call so proto_id=0x00 is in effect at pinout
                # resolution. The D-06 fail-safe skip (pinout_key is None) runs
                # AFTER Site B; these chips resolve to DIP24_2716, not None.
                if (
                    pin_count == 24
                    and proto_id in (0x07, 0x08, 0x0B)
                    and (flags & 0x10)
                ):
                    _support_status = "adapter-required"
                    # DB-04 Approach A (67.1-01): reason string begins with
                    # "adapter required:" so the host can render it verbatim.
                    # Non-empty adapter note required (DB-02 SC#1).
                    _unsupported_reason = (
                        "adapter required: requires a dedicated DIP24 EEPROM adapter "
                        "or firmware handler — socket pin 21 = WE, which the RURP "
                        "DIP24_2716 pinout maps to the 12V VPP rail (hardware-damage path)"
                    )
                    print(
                        f"INFO: including {mfg_name}/{name} as adapter-required — "
                        f"24-pin 5V EEPROM with EPROM-family algo 0x{proto_id:02X} "
                        f"(damage hazard: 12V VPP to socket pin 21 = WE of 28C-family "
                        f"chips; tracked in follow_up 24pin-eeprom-no-handler).",
                        file=sys.stderr,
                    )
                    # CR-01 Option A: demote to NON_DISPATCHABLE_ALGO so dispatch()
                    # returns ERROR instead of configure_eprom (D-03 HARD invariant).
                    proto_id = NON_DISPATCHABLE_ALGO

                # Named rule arm: AT28C04/AT28C16 family (D-03, Phase 76)
                # Fires AFTER Site B so it overwrites any generic Site B reason string
                # with explicit named-arm wording.
                # SAFETY NOTE (corrected Phase 76 review WR-01): these DIP24_2816 chips
                # arrive from infoic.xml with proto_id 0x0D (EEPROM_POLL →
                # configure_eeprom28c, pure 5V, NO VPP regulator engagement). That is NOT
                # one of Site B's 0x07/0x08/0x0B EPROM-family algos, so Site B does NOT
                # fire for them and proto_id stays 0x0D — a real, dispatchable handler,
                # NOT NON_DISPATCHABLE_ALGO. They are refused in-host NOT by proto_id
                # demotion but by support_status="adapter-required", which
                # chip_resolver.resolve_chip rejects before any wire dict is built; the
                # 0x0D handler is itself VPP-free, so there is no 12V hazard even if
                # dispatch were somehow reached. This arm therefore sets only
                # support_status + reason and intentionally does NOT touch proto_id.
                # Keys on chip name (not proto_id) for audit-friendly explicit classification.
                # Does NOT encode the DIP24→DIP32 pin remap — that lives in
                # firestarter/doc/AT28C04-ADAPTER.md.
                # Reason string must start with "adapter required:" per
                # test_adapter_required_reason_starts_with_adapter_required.
                _AT28C_DIP24_NAMES = {
                    "AT28C04",
                    "AT28HC04",
                    "AT28C04E",
                    "AT28C04F",
                    "AT28C16",
                    "AT28HC16",
                    "AT28HC16L",
                    "AT28C16E",
                    "AT28C16F",
                    "28C04A",
                    "28C04AF",
                    "28C16A",
                    "28C16AF",
                    "UPD28C04",
                }
                _chip_aliases = {
                    a.split("@")[0].strip() for a in name.split(",") if a.strip()
                }
                if _chip_aliases & _AT28C_DIP24_NAMES:
                    _support_status = "adapter-required"
                    _unsupported_reason = (
                        "adapter required: AT28C04/AT28C16 DIP24 chip — requires a physical "
                        "DIP24-to-DIP32 adapter; see firestarter/doc/AT28C04-ADAPTER.md"
                    )

                # --- SYNTHESIZE "COMPLETE" DATA ---

                # Step 1: Resolve pinout key (principled — D-02/D-03)
                pinout_key = resolve_pinout_key(
                    pin_count,
                    variant,
                    flags,
                    pm_idx=pm_idx,
                    proto_id=proto_id,
                    type_int=type_int,
                    mem_size=mem_size,
                )

                # Step 2: D-06 fail-safe — skip unclassifiable chips entirely.
                # No VPP-asserting dispatch is ever emitted for an uncertain chip.
                # Replaces the old hardcoded 24-pin safety-skip (D-05).
                if pinout_key is None:
                    print(
                        f"WARN: skipping {mfg_name}/{name} — unclassifiable pinout "
                        f"(pin_count={pin_count}, pm_idx={pm_idx}, "
                        f"variant_lo=0x{variant & 0xFF:02X}); "
                        f"add override via ~/.firestarter/database.json",
                        file=sys.stderr,
                    )
                    continue

                # Step 3: Pass 1 — FLAGS-BASED _etype (must run BEFORE algorithm
                # overrides). WARNING-5 and fm1608 need the pre-override _etype
                # to detect mistagged chips. Two-pass pattern preserved (RESEARCH
                # Pitfall 2).
                # Priority:
                #   1. XML type=4 → SRAM/RAM family (per minipro/src/database.c).
                #      FM1608 (FRAM) is tagged type=4 with proto=0x07 — without
                #      this guard we'd misclassify as UV-EPROM and risk 12V VPP
                #      on address pins.
                #   2. SRAM-class proto_id (configure_sram dispatch family).
                #   3. flags bit 0x10 = electrically erasable → Flash/EEPROM.
                #   4. Default → UV-EPROM.
                if type_int == 4:
                    _etype = "SRAM"
                elif proto_id in {0x0E, 0x27, 0x28, 0x29}:
                    _etype = "SRAM"
                elif flags & 0x10:
                    _etype = "Flash/EEPROM"
                else:
                    _etype = "UV-EPROM"

                # Step 4: Rule 1 — 28C EEPROM algorithm correction.
                # Any chip resolving to DIP24_2816 is a 24-pin 5V EEPROM (variant_lo=0x10
                # confirmed 28C family). Force algorithm=0x0D (configure_eeprom28c, 5V
                # page-write + DQ7 polling, SDP-disable, NO VPP regulator assertion).
                # This replaces the old safety-skip (D-05) AND fixes the 10 chips
                # (AM28C16A, CAT28C16A, XL2804A, etc.) that slipped through the old
                # flags&0x10 predicate (RESEARCH Pitfall 1 + §"Dangerous 24-pin EEPROMs").
                # [VERIFIED: RESEARCH.md §"Algorithm Override Rules" + §"Dangerous 24-pin EEPROMs"]
                if pinout_key == "DIP24_2816":
                    orig_proto = proto_id
                    proto_id = 0x0D
                    print(
                        f"INFO: {mfg_name}/{name} algorithm 0x{orig_proto:02X}->0x0D "
                        f"(Rule 1: 28C-EEPROM family, variant_lo=0x10; "
                        f"configure_eeprom28c, no VPP)",
                        file=sys.stderr,
                    )

                # Step 5: Rule 2 — WARNING-5 generalised safety net.
                # A chip that resolves to a 5V EEPROM pinout but carries proto_id=0x07
                # (EPROM_STD) would route to configure_eprom (12V VPP on pin 1/27) —
                # hardware damage. Flip to 0x0D. Named Rule 2 per D-05.
                #
                # Three sub-cases require different discriminators:
                #   DIP28_28C256 (pm_idx=20): always an EEPROM pinout — no UV-EPROM
                #     can land here via the principled rules. The flags & 0x10 guard is
                #     omitted because some 28C256-class chips have flags=0xC000 with no
                #     erasable bit (e.g. CAT28C256). Pinout is the discriminator.
                #     Exception: type=4 SRAM/NVRAM chips (e.g. DS1230, M48T35) that
                #     resolve to DIP28_28C256 via pm_idx=0 mem_size>8K must NOT be
                #     caught by Rule 2 — Rule 3 handles them (proto → 0x28 SRAM_STD).
                #   DIP28_2764 (pm_idx=21 or pm_idx=22 else): genuine UV-EPROMs DO land
                #     here (27C64/27C128). Use _etype == "Flash/EEPROM" from Pass 1 to
                #     identify mistagged 5V EEPROMs that slipped through.
                #   DIP28_28C64 (pm_idx=18 or pm_idx=19): the entire 28C64/28C17 family
                #     is 5V EEPROMs with no VPP pin (pin 1 = NC on the 28C64 layout).
                #     No genuine UV-EPROM uses this pinout cluster, so the guard is
                #     unconditional (no flags check needed).
                #     [VERIFIED: exhaustive infoic.xml survey — all pm_idx=18/19 DIP28
                #      chips are AT28C/BV/LV, AM28C, CAT28C/LV, M28C/LV, X28C families;
                #      datasheet cross-check confirms no VPP pin on 28C64 layout]
                #
                # References: WARNING-5 in .planning/v1.0-MILESTONE-AUDIT.md
                # and .planning/INTEGRATION-CHECK.md.
                if (
                    (
                        pinout_key == "DIP28_28C256"
                        and proto_id == 0x07
                        and type_int != 4  # SRAM-class chips handled by Rule 3
                    )
                    or (
                        pinout_key == "DIP28_2764"
                        and proto_id == 0x07
                        and _etype == "Flash/EEPROM"
                    )
                    or (pinout_key == "DIP28_28C64" and proto_id == 0x07)
                ):
                    print(
                        f"INFO: {mfg_name}/{name} algorithm override 0x07->0x0D "
                        f"(Rule 2 WARNING-5: 5V EEPROM on EPROM pinout ({pinout_key}) — "
                        f"route through configure_eeprom28c)",
                        file=sys.stderr,
                    )
                    proto_id = 0x0D

                # Step 6: Rule 3 — fm1608/SRAM override.
                # SRAM-tagged chips (type=4) with EPROM-family protocol_id. Upstream
                # infoic.xml tags Ramtron parallel FRAM (FM1208/1608/16W08/1808/18L08)
                # with type="4" (SRAM/RAM-family) but protocol_id 0x07/0x0B (EPROM
                # family). configure_eprom (0x07/0x0B dispatch) engages 12V VPP —
                # hardware-damage path for 5V FRAM.
                # Restore correct dispatch by flipping proto_id to 0x28 (SRAM_STD) and
                # overriding pinout for 28-pin variants. Named Rule 3 per D-05.
                # Reference: fm1608-db-mismatch in
                #   .planning/phases/04-hardware-validation-rurp-shield/04-HW-VALIDATION.md
                if type_int == 4 and proto_id in (0x07, 0x08, 0x0B):
                    proto_id = 0x28
                    if pin_count == 28:
                        # Memory-size discriminator: 8K chips use the 13-address
                        # DIP28_JEDEC_SRAM_8K layout; 16K+ chips use the 15-address
                        # DIP28_28C256 layout (same physical layout family, more
                        # address pins to reach A13/A14). Covers FM1608 (8K) +
                        # FM16W08 (16K) + FM1808/FM18L08 (32K) all routed correctly.
                        if mem_size <= 8192:
                            pinout_key = "DIP28_JEDEC_SRAM_8K"
                            size_label = "8K"
                        else:
                            pinout_key = "DIP28_28C256"
                            size_label = f"{mem_size // 1024}K"
                        print(
                            f"INFO: {mfg_name}/{name} type=4 SRAM override "
                            f"algorithm 0x{proto_id - 0x21:02X}->0x28 + pinout->{pinout_key} "
                            f"(Rule 3: SRAM/FRAM {size_label}; configure_sram dispatch)",
                            file=sys.stderr,
                        )
                    # 24-pin SRAM chips (FM1208) route to DIP24_6116 via resolve_pinout_key
                    # (pm_idx=0). configure_sram doesn't engage VPP so the vpp-pin field
                    # is not a hazard here.

                # Step 7: Pass 2 — PROTOCOL-AWARE _etype re-derivation.
                # Re-derive electrical.type after ALL algorithm overrides have run.
                # The firmware dispatch is the ground truth for ERASE capability:
                #   - 0x07/0x08/0x0B → configure_eprom (12V VPP)
                #       flags & 0x10 = True  → "EEPROM"   (electrically erasable)
                #       flags & 0x10 = False → "UV-EPROM" (UV erase only)
                #   - 0x0D / 0x05 / 0x06 / 0x10 → Flash/EEPROM family
                #   - 0x0E/0x27/0x28/0x29 → SRAM
                # For proto=0x07/0x08/0x0B, the flags bit 0x10 discriminates CMOS
                # electrically-erasable EEPROMs (W27C512, SST27SF/VF512, W27C257, etc.)
                # from genuine UV-EPROMs. Both share the configure_eprom dispatch and
                # 12V VPP, but EEPROMs support electrical erase while UV-EPROMs require
                # UV light. Without this check, Pass 2 would overwrite the correct
                # flags-based _etype from Pass 1 with "UV-EPROM" for all 0x07 chips.
                # [VERIFIED: infoic.xml survey — all DIP28_27512/27256 chips with
                #  flags & 0x10 set (W27C*, SST27*F*) are CMOS EEPROMs per datasheet;
                #  all genuine UV-EPROMs on these pinouts have flags & 0x10 = False]
                # This keeps the in-DB type consistent with ic_layout.py's
                # protocol-aware Type/Can-be-erased display. Must run AFTER all
                # overrides (Rules 1/2/3) because those rely on the flags-based
                # _etype from Pass 1 to detect mistagged chips. Two-pass pattern
                # preserved per RESEARCH.md Pitfall 2 (PATTERNS §execution order).
                if proto_id in {0x0E, 0x27, 0x28, 0x29}:
                    _etype = "SRAM"
                elif proto_id in {0x07, 0x08, 0x0B}:
                    # Preserve flags-based EEPROM classification for electrically-
                    # erasable chips that share the configure_eprom (0x07/0x08/0x0B)
                    # dispatch and 12V VPP but are NOT UV-erasable. (BUG-A fix)
                    if flags & 0x10:
                        _etype = "EEPROM"
                    else:
                        _etype = "UV-EPROM"
                elif proto_id in {0x05, 0x06, 0x0D, 0x10}:
                    _etype = "Flash/EEPROM"
                # else: leave _etype at the flags-based value (uncommon path —
                # any new proto_id added to KNOWN_PROTOCOLS but not classified
                # above falls back to whatever the flags-based block decided).

                # Site C: DB-03 NMOS VPP correction.
                # Must run AFTER all fm1608/WARNING-5 overrides (ordering invariant).
                # "Highest VPP wins": iterate all aliases; the match with the highest
                # VPP determines the final voltage + status (conservative — avoids
                # M2732/M2732A match-order ambiguity on combined entries like
                # INTEL/2732,2732A,M2732,M2732A).
                part_aliases = {a.split("@")[0].strip() for a in name.split(",")}
                for nmos_key, nmos_vpp in NMOS_TRUE_VPP_MV.items():
                    if nmos_key in part_aliases:
                        if _nmos_vpp_mv is None or nmos_vpp > _nmos_vpp_mv:
                            _nmos_vpp_mv = nmos_vpp
                if _nmos_vpp_mv is not None:
                    if _nmos_vpp_mv > RURP_VPP_CEILING_MV:
                        _support_status = "vpp-exceeds-max"
                        # DB-04 Approach A (67.1-01): reason string begins with
                        # "VPP <x>V exceeds programmer max (<ceil>V)" so the host
                        # can render it verbatim (Plan 02 prints f"{e}").
                        # Uses "programmer max" (not "RURP ceiling") per SC#2 wording.
                        _unsupported_reason = (
                            f"VPP {_nmos_vpp_mv // 1000}V exceeds programmer max "
                            f"({RURP_VPP_CEILING_MV // 1000}V)"
                        )
                        # CR-01 Option A: demote to NON_DISPATCHABLE_ALGO so dispatch()
                        # returns ERROR instead of configure_eprom (D-03 HARD invariant).
                        proto_id = NON_DISPATCHABLE_ALGO
                    # else: leave _support_status as "supported" — M2732A (21V)
                    # is within the RURP ceiling.

                # Rule 4 — ASD AE29F-series algorithm correction.
                # Upstream infoic.xml assigns protocol_id=0x05 (FLASH_AMD_STD /
                # IC2_ALG_F29EE -> configure_flash4) to these chips, but they
                # require algorithm 0x06 (FLASH_AMD_ALT / IC2_ALG_W29F32P ->
                # configure_flash3) for correct AMD command-set erase.
                # AE49F2008 from the same manufacturer already uses 0x06.
                _ALGO4_TARGETS = {"AE29F1008", "AE29F2008", "AE29F4008"}
                if part_aliases & _ALGO4_TARGETS and proto_id == 0x05:
                    print(
                        f"INFO: {mfg_name}/{name} algorithm 0x{proto_id:02X}->0x06 "
                        f"(Rule 4: ASD AE29F-series requires configure_flash3)",
                        file=sys.stderr,
                    )
                    proto_id = 0x06

                chip_entry = {
                    # Upstream `name` is a comma-separated alias list where each
                    # alias may carry an @PACKAGE suffix (e.g.,
                    # "AT28C256,AT28C256@SOIC28,AT28C256E,AT28HC256,...").
                    # The previous `name.split("@")[0]` truncated at the FIRST
                    # @, silently losing all aliases that appeared after a
                    # @PACKAGE-suffixed entry. Fix: split on comma first, strip
                    # @-suffix from each piece, dedupe, rejoin. Combined with
                    # database.py's alias-aware get_eprom_config lookup, this
                    # makes `firestarter info <alias>` work for every alias.
                    "part_number": ",".join(
                        dict.fromkeys(
                            a.split("@")[0].strip()
                            for a in name.split(",")
                            if a.split("@")[0].strip()
                        )
                    ),
                    "support_status": _support_status,
                    "electrical": {
                        "type": _etype,
                        "size_bytes": mem_size,
                        "pin_count": pin_count,
                        # VPP code occupies bits 7-4 of the 16-bit voltages field
                        # (the HIGH nibble of the low byte). Bits 3-0 carry option
                        # flags. Masking with 0xF0 extracts only the VPP nibble.
                        # BUG-B fix: was voltages & 0xFF (caused 0mV for chips with
                        # flags bits set, e.g. SST27VF512 voltages=0x0001).
                        # NMOS correction (Site C): override vpp/vpp_mv when
                        # _nmos_vpp_mv is set (M2716/M2732/M2732A corrected voltage).
                        "vpp": (
                            f"{_nmos_vpp_mv // 1000}V"
                            if _nmos_vpp_mv is not None
                            else VPP_VOLTAGES.get(voltages & 0xF0, "Unknown")
                        ),
                        "vpp_mv": (
                            _nmos_vpp_mv
                            if _nmos_vpp_mv is not None
                            else VPP_MV.get(voltages & 0xF0, 0)
                        ),
                        # BUG-3 fix: vcc at bits 11-8, vdd at bits 15-12.
                        # v1.12 had them swapped (vdd at bits 11-8, vcc at bits 15-12).
                        # [VERIFIED: minipro database.c#L921-L923 @ a8efaedc]
                        "vcc": VCC_VOLTAGES.get(
                            (voltages >> 8) & 0x0F, "5V"
                        ),  # bits 11-8
                        "vdd": VCC_VOLTAGES.get(
                            (voltages >> 12) & 0x0F, "5V"
                        ),  # bits 15-12
                    },
                    "programming": {
                        "algorithm": proto_id,
                        "pulse_duration": interpret_timing(
                            ic.get("pulse_delay"), proto_id
                        ),
                        "chip_id_check": True if (flags & 0x20) else False,
                        "chip_id_value": ic.get("chip_id"),
                    },
                    "pinout": pinout_key,
                }
                if _unsupported_reason:
                    chip_entry["unsupported_reason"] = _unsupported_reason

                # SRAM/FRAM/NVRAM vcc normalization.
                # Static-memory parts have a single supply rail — there is no
                # separate elevated programming voltage, so the minipro "vcc"
                # (read-rail) vs "vdd" (program-rail) split is meaningless here.
                # Upstream infoic.xml records a lower vcc test-rail (3.3V/4V) for
                # these 5V NVRAM/FRAM families (FM16xx, DS1230, M48Txx, BQ40xx),
                # which misrepresents the chip's nominal supply. The RURP shield
                # supplies a fixed 5V VCC for SRAM-class parts regardless, so the
                # operating voltage firestarter actually applies is vdd. Align
                # vcc to vdd so `firestarter info` reports the true supply.
                # Type-keyed (SRAM only): UV-EPROM and Flash/EEPROM keep their
                # vcc as the correct read voltage (vdd there is the elevated
                # program rail, e.g. 6.5V — must NOT be surfaced as operating Vcc).
                if _etype == "SRAM":
                    chip_entry["electrical"]["vcc"] = chip_entry["electrical"]["vdd"]

                chips.append(chip_entry)
                total_chips += 1

            if chips:
                complete_db[mfg_name] = chips

    with open(OUTPUT_FILE, "w") as f:
        json.dump(complete_db, f, indent=2, sort_keys=True)

    print(f"Done! {total_chips} chips processed. Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
