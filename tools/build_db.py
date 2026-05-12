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

with open(PINOUT_FILE) as _f:
    VALID_PINOUT_KEYS = set(json.load(_f).keys())

# ==========================================
# 4. PROCESSING FUNCTIONS
# ==========================================


def resolve_pinout_key(pin_count, variant, flags_int):
    """Infers the physical pinout based on Variant + Pin Count."""

    # 24-Pin Logic
    if pin_count == 24:
        if variant == 1:
            key = "DIP24_2732"
        else:
            key = "DIP24_2716"  # Default to 2716

    # 28-Pin Logic
    elif pin_count == 28:
        key = DIP28_VARIANT_MAP.get(variant & 0xFF, "DIP28_2764")

    # 32-Pin Logic
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

                # RAMTRON parallel FRAM safety skip (fm1608-db-mismatch).
                # Upstream infoic.xml mistags Ramtron parallel FRAM chips
                # (FM1208, FM1608, FM16W08, FM1808, FM18L08) as UV-EPROM
                # (type=1) with bogus 12V vpp and 3.3V vdd. Real chips are
                # 5V single-supply parallel FRAM — no programming voltage,
                # SRAM-like writes. Routing them through configure_eprom
                # (algo=0x07/0x0B) engages the 12V VPP regulator and
                # asserts P1_VPP_ENABLE to socket pin 1, which on the
                # Ramtron parallel pinout is A12 (address line), not VPP.
                # Bench-confirmed 2026-05-12: reads return address-bus
                # crosstalk; writes would route 12V to A12 → chip damage.
                # firestarter does not yet ship a FRAM handler or a
                # Ramtron-specific pinout, so we skip these chips entirely:
                # `firestarter info FM1608` returns "chip not found" — a
                # clean error instead of silent damage. Re-enable when a
                # FRAM handler + Ramtron pinout land.
                # Reference: fm1608-db-mismatch follow_up in
                # firestarter_prom meta-repo:
                # .planning/phases/04-hardware-validation-rurp-shield/04-HW-VALIDATION.md
                if mfg_name == "RAMTRON":
                    print(
                        f"WARN: skipping {mfg_name}/{name} — RAMTRON parallel FRAM not supported (fm1608-db-mismatch)",
                        file=sys.stderr,
                    )
                    continue

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

                # Skip chips with unknown protocol_id
                if proto_id not in KNOWN_PROTOCOLS:
                    print(f"WARN: skipping {name} — unknown protocol_id 0x{proto_id:02X}", file=sys.stderr)
                    continue

                # --- SYNTHESIZE "COMPLETE" DATA ---
                pinout_key = resolve_pinout_key(pin_count, variant, flags)

                # SRAM protocols emit electrical.type = "SRAM" (D4) so downstream
                # layers no longer mislabel SRAM as UV-EPROM and the info_flags
                # "electrically erasable" bit is not set spuriously.
                if proto_id in {0x0E, 0x27, 0x28, 0x29}:
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
