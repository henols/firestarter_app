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
    # Legacy Integer Keys
    16: "9V",
    32: "9.5V",
    48: "10V",
    64: "11V",
    80: "11.5V",
    0: "12V",
    96: "12.5V",
    112: "13V",
    128: "13.5V",
    144: "14V",
    160: "14.5V",
    176: "15.5V",
    192: "16V",
    208: "16.5V",
    224: "17V",
    240: "18V",
}

VPP_MV = {
    0x00: 12000, 0x10: 9000, 0x20: 9500, 0x30: 10000,
    0x40: 11000, 0x50: 11500, 0x60: 12500, 0x70: 13000,
    0x80: 13500, 0x90: 14000, 0xA0: 14500, 0xB0: 15500,
    0xC0: 16000, 0xD0: 16500, 0xE0: 17000, 0xF0: 18000,
}

KNOWN_PROTOCOLS = {0x05, 0x06, 0x07, 0x08, 0x0B, 0x0D, 0x0E, 0x10, 0x27, 0x28, 0x29, 0x35, 0x39}

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
    (28, 21): "DIP28_2764",   # 27C64 family — 8K UV-EPROM with PGM on pin 27
    (28, 22): None,            # 27C128/256/512 family — variant_lo discriminates
    (28, 0):  None,            # SRAM/FRAM (type=4) handled via override below
    (32, 7):  "DIP32_STD",    # Small 32-pin flash
    (32, 9):  "DIP32_STD",    # 1Mbit 32-pin Intel-flash + AM29F010
    (32, 10): "DIP32_STD",    # 27C010 32-pin UV-EPROM
    (32, 11): "DIP32_STD",    # AM29F002 32-pin flash
    (32, 12): "DIP32_STD",    # 27C040/080 32-pin UV-EPROM
    (32, 13): "DIP32_STD",    # AM29F040 / SST39SF040 32-pin flash
    (24, 23): None,            # 2716/2732 — variant_lo discriminates (DIP24_2716 / DIP24_2732)
}

with open(PINOUT_FILE) as _f:
    VALID_PINOUT_KEYS = set(json.load(_f).keys())

# ==========================================
# 4. PROCESSING FUNCTIONS
# ==========================================


def resolve_pinout_key(pin_count, variant, flags_int, pm_idx=None):
    """Resolve the firestarter pinout key for a chip.

    Lookup order (most-specific-first):
      1. (pin_count, pm_idx) tuple from PIN_MAP_TO_PINOUT — preferred when
         pm_idx is non-None and the tuple has a concrete pinout.
      2. (pin_count, pm_idx) tuple yielding None — fall through to variant_lo.
      3. DIP28_VARIANT_MAP (variant low byte) for 28-pin chips.
      4. Defaults per pin_count.

    pm_idx is the low byte of infoic.xml's `pin_map` attribute (minipro
    chip-family clustering signal). When passed, it strongly suggests the
    physical layout family even when the protocol differs.
    """
    key = None

    if pm_idx is not None and (pin_count, pm_idx) in PIN_MAP_TO_PINOUT:
        key = PIN_MAP_TO_PINOUT[(pin_count, pm_idx)]
        # `None` in the table means "fall through to variant-based logic"
        if key is not None:
            if key in VALID_PINOUT_KEYS:
                return key
            print(f"WARN: PIN_MAP_TO_PINOUT[{pin_count},{pm_idx}] = '{key}' not in pinouts.json", file=sys.stderr)

    # Fall-through: pre-existing variant-based logic
    if pin_count == 24:
        if variant == 1:
            key = "DIP24_2732"
        else:
            key = "DIP24_2716"  # Default to 2716
    elif pin_count == 28:
        key = DIP28_VARIANT_MAP.get(variant & 0xFF, "DIP28_2764")
    elif pin_count == 32:
        # Most 32-pin chips follow the standard JEDEC layout
        # Variant usually just toggles high address lines vs NC pins
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

                # Skip chips with unknown protocol_id
                if proto_id not in KNOWN_PROTOCOLS:
                    print(f"WARN: skipping {name} — unknown protocol_id 0x{proto_id:02X}", file=sys.stderr)
                    continue

                # --- SYNTHESIZE "COMPLETE" DATA ---
                pinout_key = resolve_pinout_key(pin_count, variant, flags, pm_idx=pm_idx)

                # Derive electrical.type — priority order:
                # 1. XML's `type` attribute is authoritative when it says 4 (SRAM/RAM-family).
                #    Per minipro/src/database.c, type=4 means RAM/SRAM regardless of protocol.
                #    FM1608 (FRAM) is tagged type=4 with protocol_id=0x07 — without this guard
                #    we'd mislabel it as UV-EPROM and risk routing 12V VPP to address pins.
                # 2. SRAM-class protocols (configure_sram dispatch).
                # 3. flags bit 0x10 = electrically erasable (Flash/EEPROM family).
                # 4. Default to UV-EPROM.
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
                if (pinout_key == "DIP28_2764"
                        and proto_id == 0x07
                        and _etype == "Flash/EEPROM"):
                    print(
                        f"INFO: {mfg_name}/{name} algorithm override 0x07->0x0D "
                        f"(WARNING-5: 5V EEPROM mistagged as UV-EPROM, DIP28_2764 pin 1 = A14)",
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
                    print(
                        f"INFO: {mfg_name}/{name} type=4 SRAM override "
                        f"algorithm 0x{proto_id:02X}->0x28 + pinout->DIP28_JEDEC_SRAM_8K "
                        f"(fm1608-db-mismatch: route SRAM-tagged chip through configure_sram)",
                        file=sys.stderr,
                    )
                    proto_id = 0x28
                    if pin_count == 28:
                        pinout_key = "DIP28_JEDEC_SRAM_8K"
                    # 24-pin (FM1208) keeps its DIP24_2716 pinout — no SRAM-specific
                    # 24-pin entry yet; configure_sram doesn't engage VPP so the
                    # vpp-pin field is ignored, making the pass-through safe.

                chip_entry = {
                    "part_number": name.split("@")[0],
                    "electrical": {
                        "type": _etype,
                        "size_bytes": mem_size,
                        "pin_count": pin_count,
                        "vpp": VPP_VOLTAGES.get(voltages & 0xFF, "Unknown"),
                        "vpp_mv": VPP_MV.get(voltages & 0xFF, 0),
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

                chips.append(chip_entry)
                total_chips += 1

            if chips:
                complete_db[mfg_name] = chips

    with open(OUTPUT_FILE, "w") as f:
        json.dump(complete_db, f, indent=2)

    print(f"Done! {total_chips} chips processed. Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
