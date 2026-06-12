import xml.etree.ElementTree as ET
import json
import os
import requests
import sys

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
PROTOCOL_MAP = {
    0x05: "FLASH_AMD_STD",
    0x06: "FLASH_AMD_ALT",
    0x07: "EPROM_STD",
    0x08: "EPROM_QUICK",
    0x0B: "EPROM_LEGACY",
    0x0E: "SRAM_32PIN",
    0x0D: "EEPROM_POLL",
    0x10: "FLASH_INTEL",
    0x11: "FLASH_FWH",
    0x27: "SRAM_24PIN",
    0x28: "SRAM_STD",
    0x29: "SRAM_512K_1M",
    0x2A: "NVRAM_32PIN",
    0x2C: "NVRAM_TIMEKEEPER",
    0x2E: "NVRAM_512K",
    0x35: "FLASH_EEPROM_LIKE",
    0x39: "FLASH_INTEL_ALT",
    0x3C: "FLASH_4MB",
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
    0x00: 12000, 0x10: 9000, 0x20: 9500, 0x30: 10000,
    0x40: 11000, 0x50: 11500, 0x60: 12500, 0x70: 13000,
    0x80: 13500, 0x90: 14000, 0xA0: 14500, 0xB0: 15500,
    0xC0: 16000, 0xD0: 16500, 0xE0: 17000, 0xF0: 18000,
}

# NMOS VPP correction: promotes the comment at L46-56 to applied code.
# Matched against part_number aliases; "highest VPP wins" for entries with
# multiple NMOS aliases (e.g., INTEL/2732,2732A,M2732,M2732A).
NMOS_TRUE_VPP_MV: dict[str, int] = {
    "M2716": 25000,  # Intel NMOS 2716: 25V VPP (datasheet)
    "M2732": 25000,  # Intel NMOS 2732: 25V VPP (datasheet)
    "M2732A": 21000,  # Intel NMOS 2732A: 21V VPP (later variant)
}
# RURP boost regulator theoretical ceiling (build_db.py L55 comment + hw evidence).
# Chips requiring VPP above this cannot be programmed on any RURP revision.
RURP_VPP_CEILING_MV = 22000

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
    0x34,
    0x35,
    0x39,
}

# CR-01 Option A (Phase 66 gap-closure): algorithm sentinel for non-supported chips.
# dispatch(0x00, None) falls into the mem_type fallback chain (protocol==0 path):
#   _ALGO_MEM_TYPE.get(0x00) → None → {1:..., 4:..., 3:..., 5:...}.get(None, "ERROR")
#   → "ERROR"
# No real handler (configure_eprom / configure_eeprom28c / configure_flash* /
# configure_sram) is ever reached for a non-supported chip. D-03 HARD: do NOT
# route any flagged chip to a working handler.
NON_DISPATCHABLE_ALGO = 0x00

VCC_VOLTAGES = {0x00: "5V", 0x01: "3.3V", 0x04: "5.5V", 0x05: "6.5V"}

DIP28_VARIANT_MAP = {
    0x10: "DIP28_27512",   # 27C512 — VPP on pin 22 (OE pin), 19 address lines
    0x11: "DIP28_27256",   # 27C256 — VPP on pin 1, 15 address lines
    0x12: "DIP28_2764",    # 27C128
    0x13: "DIP28_2764",    # 27C64/2764A
}

# fm1608-db-mismatch: pm_idx is the upstream pin_map low byte (chip-family
# clustering signal per minipro infoic.xml). Maps (pin_count, pm_idx) to a
# firestarter pinout key. Falls back to DIP28_VARIANT_MAP / DIP32_STD when
# the (pin_count, pm_idx) tuple has no specific override.
#
# Derived from a chip-by-chip survey of infoic.xml:
#   - pm_idx clusters chips by chip family — chips in the same group share
#     the same physical pin layout (per minipro/src/database.c gnd/mask
#     analysis), even when protocol_id differs across the group.
#   - For pm_idx=22 (28-pin 27C128/256/512 family), the variant_lo sub-
#     discriminates layouts (DIP28_VARIANT_MAP).
#   - For pm_idx=21 (28-pin 27C64), layout is DIP28_2764 with PGM on pin 27.
#   - For pm_idx=0 type=4 (28-pin SRAM/FRAM), layout is JEDEC SRAM
#     (DIP28_JEDEC_SRAM_8K).
#   - 32-pin pm_idx 7/9/10/11/12/13 are flash/EPROM variants sharing
#     DIP32_STD address bus (different protocols use different control
#     signals but the bus layout is common).
PIN_MAP_TO_PINOUT = {
    # (pin_count, pm_idx): pinout_key  (None = use sub-discriminator)
    # Tuple variants below override based on protocol_id (see resolve_pinout_key).
    (28, 21): "DIP28_2764",        # 27C64 family — 8K UV-EPROM with PGM on pin 27 (one-rom verified)
    (28, 22): None,                 # 27C128/256/512 family — variant_lo discriminates
    (28, 0):  None,                 # SRAM/FRAM (type=4) handled via override below
    (28, 20): "DIP28_28C256",      # 28C256 EEPROM family (A14 at pin 1, WE at pin 27, no VPP — one-rom verified)
    (28, 19): "DIP28_28C64",       # 28C64 EEPROM family (8K, WE at pin 27, no VPP — one-rom verified)
    (28, 18): "DIP28_28C64",       # 28C16/17 EEPROM family (2K-class; same DIP28 5V layout as 28C64, smaller addr range)
    (32, 13): None,                 # 5V flash vs UV-EPROM at same pm_idx — protocol_id discriminates
    (32, 12): None,                 # 27C020/040 vs 5V flash at same pm_idx — protocol_id discriminates
    (32, 11): None,                 # AM29F002 5V flash vs Intel-flash vs 28C-family — protocol_id discriminates
    (32, 10): None,                 # 27C010 UV-EPROM vs Intel-flash — protocol_id discriminates
    (32, 9):  None,                 # 1Mbit mix — proto_id discriminates (5V flash / Intel-flash / 28C-EEPROM)
    (32, 7):  None,                 # Small 32-pin mix
    (32, 5):  "DIP32_STD",         # 32-pin Intel-flash variants (12V VPP at pin 1)
    (32, 0):  None,                 # 32-pin SRAM/NVRAM/EEPROM — protocol_id discriminates
    (24, 23): None,                 # 2716/2732 — variant_lo discriminates
    (24, 0):  None,                 # 24-pin SRAM (6116) — protocol_id discriminates
}

# Per-protocol overrides for chips that share pm_idx but have different layouts.
# Used when the same pin_map index covers chips with different programming families
# (e.g., 5V flash vs UV-EPROM both with pm_idx=13).
#
# Pinout naming families (one-rom verified or one-rom-canonical-derived):
#   DIP32_SST39SF040       : 32-pin 5V flash (CE=22, OE=24, WE=31, no VPP)
#   DIP32_STD              : 32-pin UV-EPROM (CE=22, OE=24, VPP=1, PGM=31)
#   DIP32_28C512_EEPROM    : 32-pin 5V EEPROM 64K (CE=22, OE=24, WE=30, no VPP)
#   DIP28_28C256           : 28-pin 5V EEPROM 32K (CE=20, OE=22, WE=27, no VPP)
#   DIP28_28C64            : 28-pin 5V EEPROM 8K (CE=20, OE=22, WE=27, no VPP)
PIN_MAP_PROTO_TO_PINOUT = {
    # (pin_count, pm_idx, proto_id): pinout_key
    # ---- 32-pin 5V flash (proto 0x05 FLASH_AMD_STD + 0x06 FLASH_AMD_ALT) ----
    # All route to DIP32_SST39SF040 (5V flash family — no VPP, WE on pin 31).
    # Address bus is over-allocated to 19 pins; firmware uses memory-size to
    # restrict driving for smaller variants.
    (32,  7, 0x05): "DIP32_SST39SF040",
    (32,  7, 0x06): "DIP32_SST39SF040",
    (32,  9, 0x05): "DIP32_SST39SF040",
    (32,  9, 0x06): "DIP32_SST39SF040",
    (32, 10, 0x06): "DIP32_SST39SF040",
    (32, 11, 0x05): "DIP32_SST39SF040",
    (32, 11, 0x06): "DIP32_SST39SF040",
    (32, 12, 0x06): "DIP32_SST39SF040",
    (32, 13, 0x05): "DIP32_SST39SF040",
    (32, 13, 0x06): "DIP32_SST39SF040",  # one-rom verified for SST39SF040 + AM29F040
    # ---- 32-pin UV-EPROM (proto 0x08 EPROM_QUICK) ----
    # All route to DIP32_STD (UV-EPROM family — 12V VPP at pin 1, PGM/A18 at pin 31).
    (32,  7, 0x08): "DIP32_STD",
    (32, 10, 0x08): "DIP32_STD",         # one-rom verified for 27C010
    (32, 12, 0x08): "DIP32_STD",         # one-rom verified for 27C020/27C040
    (32, 13, 0x08): "DIP32_STD",
    # ---- 32-pin 5V EEPROM (proto 0x0D EEPROM_POLL) ----
    # All route to DIP32_28C512_EEPROM (one-rom verified for 28C512 family).
    (32,  9, 0x0D): "DIP32_28C512_EEPROM",
    (32, 11, 0x0D): "DIP32_28C512_EEPROM",
    (32, 13, 0x0D): "DIP32_28C512_EEPROM",
    # ---- 32-pin Intel-flash (proto 0x10) ----
    # All route to DIP32_STD (Intel-flash uses 12V VPP at pin 1, similar physical
    # layout to UV-EPROM though programming algorithm is command-register based).
    (32,  7, 0x10): "DIP32_STD",
    (32,  9, 0x10): "DIP32_STD",
    (32, 10, 0x10): "DIP32_STD",
    (32, 11, 0x10): "DIP32_STD",
    (32, 12, 0x10): "DIP32_STD",
    (32, 13, 0x10): "DIP32_STD",
    # ---- 32-pin SRAM/NVRAM (proto 0x0E SRAM_32PIN + 0x29 SRAM_512K_1M) ----
    # JEDEC standard 32-pin SRAM layout (Dallas DS1245/DS1249/DS1250 + ST/SGS-Thomson
    # M48T128/M48T512). All have CE=22, OE=24, **WE=31** (NOT WE=30 like the 28C512
    # EEPROM variant), and no VPP. Pin 1 is A18 for 4M variants / NC for smaller.
    # Same physical layout as DIP32_SST39SF040 — only the programming algorithm
    # differs (SRAM-byte-write via configure_sram vs flash sector-erase).
    #
    # Multi-source evidence for MEDIUM confidence:
    #   - JEDEC JC-42 standard for 32-pin parallel SRAM
    #   - one-rom SST39SF040 verified WE=31 (5V flash, same physical layout class)
    #   - one-rom 28C512 EEPROM (32-pin, 64K) has WE=30 — that's an EEPROM-specific
    #     variation, NOT applicable to SRAM/NVRAM at this pin count
    #   - minipro devices.h: DS1245/49/50 family has package_details=0x20000000
    #     (32-pin) and protocol_id=0xd2 (Dallas-specific NVRAM algorithm) confirming
    #     RAM-class chip classification — pin layout follows JEDEC SRAM
    #
    # Previous routing to DIP32_28C512_EEPROM (WE=30) was WRONG — DIP32_SST39SF040
    # is the correct JEDEC SRAM-family pinout.
    (32,  0, 0x0E): "DIP32_SST39SF040",
    (32,  0, 0x29): "DIP32_SST39SF040",
    # ---- 24-pin 5V SRAM (proto 0x27 SRAM_24PIN) ----
    (24, 0,  0x27): "DIP24_6116",        # one-rom verified for 6116
}

with open(PINOUT_FILE) as _f:
    VALID_PINOUT_KEYS = set(json.load(_f).keys())

# ==========================================
# 4. PROCESSING FUNCTIONS
# ==========================================


def resolve_pinout_key(pin_count, variant, flags_int, pm_idx=None, proto_id=None):
    """Resolve the firestarter pinout key for a chip.

    Lookup order (most-specific-first):
      1. (pin_count, pm_idx, proto_id) from PIN_MAP_PROTO_TO_PINOUT — used when
         chips at the same pm_idx have different layouts based on protocol
         (e.g., (32, 13, 0x06) = 5V flash → DIP32_SST39SF040; 0x08 = UV-EPROM
         → DIP32_STD). Highest specificity wins.
      2. (pin_count, pm_idx) tuple from PIN_MAP_TO_PINOUT.
      3. (pin_count, pm_idx) tuple yielding None → fall through to variant_lo.
      4. DIP28_VARIANT_MAP (variant low byte) for 28-pin chips.
      5. Defaults per pin_count.

    pm_idx is the low byte of infoic.xml's `pin_map` attribute. When combined
    with proto_id, it discriminates chip-layout families precisely.
    """
    key = None

    # Tier 1: (pin_count, pm_idx, proto_id) — most specific
    if pm_idx is not None and proto_id is not None:
        key = PIN_MAP_PROTO_TO_PINOUT.get((pin_count, pm_idx, proto_id))
        if key is not None:
            if key in VALID_PINOUT_KEYS:
                return key
            print(f"WARN: PIN_MAP_PROTO_TO_PINOUT[{pin_count},{pm_idx},0x{proto_id:02X}] = '{key}' not in pinouts.json", file=sys.stderr)

    # Tier 2: (pin_count, pm_idx)
    if pm_idx is not None and (pin_count, pm_idx) in PIN_MAP_TO_PINOUT:
        key = PIN_MAP_TO_PINOUT[(pin_count, pm_idx)]
        if key is not None:
            if key in VALID_PINOUT_KEYS:
                return key
            print(f"WARN: PIN_MAP_TO_PINOUT[{pin_count},{pm_idx}] = '{key}' not in pinouts.json", file=sys.stderr)

    # Tier 3: variant-based fall-through
    if pin_count == 24:
        # 2732 (4K UV-EPROM) has variant_lo=0x01 (full variant=0x3a01).
        # 2716 (2K UV-EPROM) has variant_lo=0x00 (full variant=0x3b00 et al).
        # The previous `variant == 1` check compared the full 16-bit value
        # and missed all real-world 2732 entries — fixed to compare low byte.
        if (variant & 0xFF) == 1:
            key = "DIP24_2732"
        else:
            key = "DIP24_2716"  # Default to 2716
    elif pin_count == 28:
        key = DIP28_VARIANT_MAP.get(variant & 0xFF, "DIP28_2764")
    elif pin_count == 32:
        # Most 32-pin chips follow the standard JEDEC layout
        key = "DIP32_STD"
    else:
        key = None

    if key is not None and key not in VALID_PINOUT_KEYS:
        print(f"WARN: resolved pinout key '{key}' not in pinouts.json", file=sys.stderr)

    return key


def interpret_timing(raw_hex, protocol_id):
    try:
        val = int(raw_hex, 16)
    except:
        val = 0

    # EPROM Legacy (0x0B) is roughly 100us ticks
    if protocol_id == 0x0B:
        return f"{val * 100} us"
    # EPROM Standard (0x07) is roughly 100us ticks
    if protocol_id == 0x07:
        return f"{val * 100} us"
    # Modern (0x08) is often 1us
    if protocol_id == 0x08:
        return f"{val} us"

    return "Algorithm Controlled"


def main():
    print(f"Fetching database from: {MINIPRO_XML_URL}")
    try:
        r = requests.get(MINIPRO_XML_URL)
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
                except:
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
                    _unsupported_reason = (
                        "Protocol 0x34 (XICOR NovRAM serial-parallel hybrid) "
                        "is not implemented on this hardware"
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
                # handler — proto_id unchanged, no DIP24 EEPROM handler wired.
                # Discriminator: pin_count == 24 AND proto_id in EPROM-family
                # AND flags has the "electrically erasable" bit (0x10).
                if (
                    pin_count == 24
                    and proto_id in (0x07, 0x08, 0x0B)
                    and (flags & 0x10)
                ):
                    _support_status = "adapter-required"
                    _unsupported_reason = (
                        f"24-pin 5V EEPROM with EPROM-family algo 0x{proto_id:02X}: "
                        f"socket pin 21 = WE on 28C-family chips; "
                        f"RURP DIP24_2716 pinout maps pin 21 to the 12V VPP rail "
                        f"(hardware-damage path). Requires a dedicated DIP24 EEPROM "
                        f"adapter or firmware handler before this chip can be programmed."
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

                # --- SYNTHESIZE "COMPLETE" DATA ---
                pinout_key = resolve_pinout_key(pin_count, variant, flags, pm_idx=pm_idx, proto_id=proto_id)

                # Derive electrical.type — FLAGS-BASED (used by WARNING-5 trigger).
                # The "electrically erasable" flag distinguishes 5V parallel
                # EEPROMs mistagged with EPROM protocol_id from genuine 12V VPP
                # UV-EPROMs at the same proto_id. WARNING-5 needs this signal
                # BEFORE the algorithm override runs.
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

                # WARNING-5 safety override: DIP28_2764 chips on the 0x07
                # (EPROM_STD) path apply 12V P1_VPP_ENABLE to socket pin 1
                # during the write pulse. On the DIP28_2764 pinout, socket
                # pin 1 = A14 (high address line) on 28C-family 5V CMOS
                # EEPROMs — applying 12V there is a hardware-damage path.
                # Flip proto_id to 0x0D so these chips route to
                # configure_eeprom28c (5V page-write, SDP-disable + DQ7
                # polling, no VPP regulator) which the firmware already
                # implements correctly. Leave _etype = "Flash/EEPROM"
                # unchanged — database.py's info_flags derivation depends
                # on that string for the "electrically erasable" bit, which
                # IS correct for these chips.
                # Discriminator (3 predicates): pinout_key == "DIP28_2764"
                # AND proto_id == 0x07 AND _etype == "Flash/EEPROM".
                # Inline literal — no module-top constant — matches the
                # Phase 12 Plan 04 SRAM-detection precedent above.
                # References: WARNING-5 in .planning/v1.0-MILESTONE-AUDIT.md
                # and .planning/INTEGRATION-CHECK.md.
                if (pinout_key in ("DIP28_2764", "DIP28_28C256")
                        and proto_id == 0x07
                        and _etype == "Flash/EEPROM"):
                    print(
                        f"INFO: {mfg_name}/{name} algorithm override 0x07->0x0D "
                        f"(WARNING-5: 5V EEPROM with non-EPROM pinout — route through configure_eeprom28c)",
                        file=sys.stderr,
                    )
                    proto_id = 0x0D

                # fm1608-db-mismatch override: SRAM-tagged chips with EPROM-family
                # protocol. Upstream infoic.xml tags Ramtron parallel FRAM (FM1208/
                # 1608/16W08/1808/18L08) with `type="4"` (SRAM/RAM-family) but
                # protocol_id 0x07/0x0B (EPROM family). The Phase 12-02 firmware
                # protocol-prefix dispatch routes 0x07/0x0B/0x08 to configure_eprom
                # (engages 12V VPP regulator + asserts P1_VPP_ENABLE on socket pin 1).
                # On a 5V FRAM chip whose pin 1 is an address line (or NC), routing
                # 12V there is a hardware-damage path.
                #
                # Restore the pre-Phase-12-02 working dispatch by flipping proto_id
                # to 0x28 (SRAM_STD). memory.cpp lines 98-99 now route protocol==0x28
                # to configure_sram (no VPP, byte-write at 5V). Also override pinout
                # to DIP28_JEDEC_SRAM_8K (28-pin variants) so pin 27 emits rw-pin
                # (WE strobe for SRAM) instead of pgm-pin (EPROM programming pulse).
                # The 8K pinout has only 13 address bits — 16K/32K Ramtron variants
                # (FM16W08/FM1808/FM18L08) over-emit but tolerate it under configure_sram
                # (firmware ignores address bits beyond memory-size).
                #
                # Reference: fm1608-db-mismatch follow_up in firestarter_prom
                # at .planning/phases/04-hardware-validation-rurp-shield/04-HW-VALIDATION.md
                # Bench-validation pending — Ramtron pinout assumptions need confirmation.
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
                            size_label = f"{mem_size//1024}K"
                        print(
                            f"INFO: {mfg_name}/{name} type=4 SRAM override "
                            f"algorithm 0x{proto_id-0x21:02X}->0x28 + pinout->{pinout_key} "
                            f"(SRAM/FRAM {size_label}; configure_sram dispatch)",
                            file=sys.stderr,
                        )
                    # 24-pin (FM1208) keeps its DIP24_2716 pinout — no SRAM-specific
                    # 24-pin entry yet; configure_sram doesn't engage VPP so the
                    # vpp-pin field is ignored, making the pass-through safe.

                # Re-derive electrical.type protocol-aware after all algorithm
                # overrides have run. The firmware dispatch is the ground truth:
                #   - 0x07/0x08/0x0B → configure_eprom (12V VPP) → UV-EPROM
                #   - 0x0D / 0x05 / 0x06 / 0x10 / 0x35 / 0x39 → Flash/EEPROM family
                #   - 0x0E/0x27/0x28/0x29 → SRAM
                # This keeps the in-DB type consistent with ic_layout.py's
                # protocol-aware Type/Can-be-erased display, eliminating the
                # "display says X but DB says Y" inconsistency that previously
                # masked WARNING-5-class hazards in triage. Note: must run AFTER
                # the WARNING-5 / fm1608 overrides because those rely on the
                # flags-based _etype to detect mistagged chips.
                if proto_id in {0x0E, 0x27, 0x28, 0x29}:
                    _etype = "SRAM"
                elif proto_id in {0x07, 0x08, 0x0B}:
                    _etype = "UV-EPROM"
                elif proto_id in {0x05, 0x06, 0x0D, 0x10, 0x35, 0x39}:
                    _etype = "Flash/EEPROM"
                # else: leave _etype at the flags-based value (uncommon path —
                # any new proto_id added to KNOWN_PROTOCOLS but not classified
                # above falls back to whatever the flags-based block decided).

                # Site C: DB-03 NMOS VPP correction.
                # Must run AFTER all fm1608/WARNING-5 overrides (ordering invariant
                # — see L46-56 comment). "Highest VPP wins": iterate all aliases;
                # the match with the highest VPP determines the final voltage +
                # status (conservative — avoids M2732/M2732A match-order ambiguity
                # on combined entries like INTEL/2732,2732A,M2732,M2732A).
                part_aliases = {a.split("@")[0].strip() for a in name.split(",")}
                for nmos_key, nmos_vpp in NMOS_TRUE_VPP_MV.items():
                    if nmos_key in part_aliases:
                        if _nmos_vpp_mv is None or nmos_vpp > _nmos_vpp_mv:
                            _nmos_vpp_mv = nmos_vpp
                if _nmos_vpp_mv is not None:
                    if _nmos_vpp_mv > RURP_VPP_CEILING_MV:
                        _support_status = "vpp-exceeds-max"
                        _unsupported_reason = (
                            f"VPP {_nmos_vpp_mv // 1000}V exceeds RURP ceiling "
                            f"({RURP_VPP_CEILING_MV // 1000}V); "
                            f"cannot program on this hardware"
                        )
                        # CR-01 Option A: demote to NON_DISPATCHABLE_ALGO so dispatch()
                        # returns ERROR instead of configure_eprom (D-03 HARD invariant).
                        proto_id = NON_DISPATCHABLE_ALGO
                    # else: leave _support_status as "supported" — M2732A (21V)
                    # is within the RURP ceiling.

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
                        "vpp": (
                            f"{_nmos_vpp_mv // 1000}V"
                            if _nmos_vpp_mv is not None
                            else VPP_VOLTAGES.get(voltages & 0xFF, "Unknown")
                        ),
                        "vpp_mv": (
                            _nmos_vpp_mv
                            if _nmos_vpp_mv is not None
                            else VPP_MV.get(voltages & 0xFF, 0)
                        ),
                        "vdd": VCC_VOLTAGES.get((voltages >> 8) & 0x0F, "5V"),
                        "vcc": VCC_VOLTAGES.get((voltages >> 12) & 0x0F, "5V"),
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

                chips.append(chip_entry)
                total_chips += 1

            if chips:
                complete_db[mfg_name] = chips

    with open(OUTPUT_FILE, "w") as f:
        json.dump(complete_db, f, indent=2)

    print(f"Done! {total_chips} chips processed. Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
