import json
import os
import sys
import xml.etree.ElementTree as ET

import requests

from firestarter.constants import MAX_27C020_SIZE

# ==========================================
# 1. CONFIGURATION
# ==========================================
# Pinned to the SHA recorded in tools/DECODE-NOTES.md §0/§3 (the Phase-86 regen
# provenance of record) so the fetch is deterministic and the baseline re-pin is
# reproducible. Was /-/raw/master/ — switched to the pinned commit per D-05
# discretion (DECODE-NOTES.md §3). Short form: a8efaedc.
MINIPRO_XML_URL = (
    "https://gitlab.com/DavidGriffith/minipro/-/raw/"
    "a8efaedc236c1d9718bd28299dfbb99536b010ff/infoic.xml"
)
_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "firestarter", "data")
OUTPUT_FILE = os.path.join(_DATA_DIR, "chip_database.json")
PINOUT_FILE = os.path.join(_DATA_DIR, "pinouts.json")
# VAR-05 / D-10: curated non-upstream chip supplement, merged post-decode (see
# the EXTRA_CHIPS block in main()). Physically-real chips absent from infoic.xml.
EXTRA_CHIPS_FILE = os.path.join(os.path.dirname(__file__), "extra_chips.json")
# IN-02 (98-03 host half, 98-05 mirrored into firestarter/constants.py as the
# single host-side source of truth): named boundary for the DIP32_27C020
# size-keyed resolve_pinout_key arm — 256K (262144 bytes), the largest 0x08
# 32-pin part where A18 (bit 18 = mask 0x40000) is structurally unused. Chips
# above this boundary (512K AM27C040, 1M AM27C080) legitimately use pin 31 =
# A18 and MUST stay on DIP32_STD (D-04 alias guard). Cross-references the
# firmware-side constant of the same name/value in
# firestarter/include/firestarter.h (added by 98-05); see
# tests/test_revision_constants_parity.py for the cross-repo assertion.

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
RURP_VPP_CEILING_MV = 25000

# PGSZ-01 / CR-01: datasheet-sourced per-chip page size map.
# Keyed on the canonical part number (first alias in the comma-separated list).
# Each entry carries a [CITED:] datasheet reference — DO NOT author [ASSUMED] values.
# Chips absent from this map omit the page_size field entirely. For
# algorithm 13 (0x0D, EEPROM_POLL) chips specifically, the firmware falls
# back to its own named AT28C page-size floor constant
# (`eeprom_28c.cpp`'s page-size fallback, Phase 149 D-10) when the field is
# absent; this map is NOT extended to cover that fallback (REQUIREMENTS.md
# §Out of Scope / DATA-04).
# Only in-repo datasheet PDFs are authoritative sources.
_PAGE_SIZE_BY_PART: dict[str, int] = {
    # [CITED: firestarter/datasheets/0x05-FLASH-AMD-STD/W29C040.pdf §6.2
    #         "Every page contains 256 bytes of data."]
    "W29C040": 256,
    # W29C042 shares the same DB entry as W29C040 (same family, same page structure)
    # but is not individually documented in the in-repo datasheet — omitted per
    # PGSZ-01 discipline. The shared entry gets W29C040's citation via part-number lookup.
    # [CITED: firestarter/datasheets/0x05-FLASH-AMD-STD/W29C020.pdf §6.2
    #         "Every page contains 128 bytes of data." + FEATURES "128 bytes per page"]
    "W29C020": 128,
    # W29C020C and W29C022 share the same DB entry as W29C020 (same family).
    # Not individually documented in the in-repo datasheet — omitted per PGSZ-01
    # discipline. The shared entry gets W29C020's citation via part-number lookup.
}

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
    0x00: 5000,
    0x01: 3300,
    0x02: 4000,  # BUG-1 fix: was missing from v1.12
    0x03: 4500,  # BUG-1 fix: was missing from v1.12
    0x04: 5500,
    0x05: 6500,
}

# [VERIFIED: minipro database.c#L130-L135 @ a8efaedc — tl866ii_vcc_voltages[]]
# Phase 148 DATA-01 (D-01/D-02/D-03): 4 V is a real number that is not a real
# operating voltage — no part in this database has a 4.0 V nominal supply.
# VCC_VOLTAGES index 0x02 is the TL866's low-margin VCC *verify* rail, not
# the chip's operating supply. This is a statement about the decode table
# itself, not a patch aimed at one family: any chip whose decoded vcc_mv
# lands on this rail is being misreported, regardless of which family it
# belongs to. Defined as a lookup into VCC_VOLTAGES (never a re-typed
# literal) so the rail value is single-sourced from the decode table by
# construction and cannot drift from it even if the table is ever corrected.
_VCC_MARGIN_RAIL_MV = VCC_VOLTAGES[0x02]

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
                if proto_id == 0x08 and mem_size <= MAX_27C020_SIZE:
                    # D-02/D-04: ≤256K 0x08 chips (27C010/27C020 class) have pin 31 = PGM
                    # (NOT A18 — A18 = bit 18 = mask 0x40000 is unused at ≤256K).
                    # 512K AM27C040 (524288) and 1M AM27C080 (1048576) legitimately use
                    # pin 31 = A18 and MUST stay on DIP32_STD (host-side D-04 alias guard).
                    key = (
                        "DIP32_27C020"  # PGM on pin 31 (off address bus); VPP on pin 1
                    )
                else:
                    key = "DIP32_STD"  # UV-EPROM / Intel-flash; VPP=pin 1
            else:
                key = None
        else:
            key = None  # D-06 fail-safe

    if key is not None and key not in VALID_PINOUT_KEYS:
        print(f"WARN: resolved pinout key '{key}' not in pinouts.json", file=sys.stderr)

    return key


def classify(type_int, proto_id, pm_idx, flags, pinout_key, mem_size):
    """Principled chip classifier — the SOLE classification path (Phase 86 VAR-02).

    Replaces the deleted Rule 1 / Rule 2 (WARNING-5) / Rule 3 override stack and
    the two-pass `_etype` derivation with one function keyed on the fields minipro
    itself uses to classify a device: `type` (MP_MEMORY=0x01 / MP_SRAM=0x04),
    `protocol_id` (the algorithm-family / dispatch axis), `pm_idx` (physical-layout
    cluster, already consumed by resolve_pinout_key), and `flags` (the 0x10
    electrically-erasable bit). The variant HIGH byte is documented as minipro's
    T56/T76 `algo_number` (DECODE-NOTES.md §2) and is deliberately NOT a
    classification input.

    Returns (etype, algorithm, pinout_key) where:
      - etype is the electrical.type string,
      - algorithm is the firmware-dispatch protocol_id integer,
      - pinout_key is the (possibly re-routed) pinout key. Only the type=4 28-pin
        SRAM/FRAM arm re-routes the pinout (subsuming Rule 3's pinout fix);
        every other arm returns pinout_key unchanged.

    Arm order (RESEARCH §"Recommended classifier shape"):
      1. SRAM/FRAM/NVRAM class — type=4 (MP_SRAM) authoritative, or an SRAM-family
         protocol; algorithm 0x28 (SRAM_STD) when the chip arrived with an
         EPROM-family proto (0x07/0x08/0x0B), else proto_id. (subsumes Rule 3)
      2. 5V-EEPROM pinout clusters — DIP24_2816 / DIP28_28C64 / DIP28_28C256, or
         DIP28_2764 with the electrically-erasable bit set → EEPROM / 0x0D
         (configure_eeprom28c, no VPP). (subsumes Rule 1 + Rule 2 / WARNING-5)
      3. EPROM-family proto (0x07/0x08/0x0B) → EEPROM if flags&0x10 else UV-EPROM,
         algorithm proto_id (keeps 12V VPP; W27C512 etc. land here).
      4. Flash families (0x05/0x06/0x0D/0x10) → Flash/EEPROM, algorithm proto_id.
      4b. X88C64 fix — proto 0x34 (XICOR NovRAM/EEPROM) → EEPROM, algorithm
          proto_id (display-only; the chip stays protocol-not-implemented /
          non-dispatchable; DECODE-NOTES.md §4).
      5. default → UV-EPROM, algorithm proto_id.

    [VERIFIED: minipro minipro.h#L70 MP_SRAM=0x04; database.c#L1918 variant>>8 =
     algo_number @ a8efaedc — see tools/DECODE-NOTES.md §2/§4/§5]
    """
    # 1. SRAM / FRAM / NVRAM class (was Rule 3). type=4 (MP_SRAM) is authoritative.
    if type_int == 4 or proto_id in {0x0E, 0x27, 0x28, 0x29}:
        if proto_id in {0x07, 0x08, 0x0B}:
            algorithm = 0x28
            # Rule 3 pinout re-route for 28-pin SRAM/FRAM that arrived with an
            # EPROM-family proto (FM1608 8K -> JEDEC SRAM; FM16W08/1808 16K+ ->
            # DIP28_28C256). 24-pin SRAM (FM1208) already resolves to DIP24_6116
            # via resolve_pinout_key (pm_idx=0) — leave it.
            if pinout_key is not None and pinout_key.startswith("DIP28"):
                if mem_size <= 8192:
                    pinout_key = "DIP28_JEDEC_SRAM_8K"
                else:
                    pinout_key = "DIP28_28C256"
        else:
            algorithm = proto_id
        return "SRAM", algorithm, pinout_key

    # 2. 5V-EEPROM pinout clusters (was Rule 1 + Rule 2 / WARNING-5).
    #    These pinouts have no programming VPP; route to configure_eeprom28c (0x0D).
    #    SCOPE (matches the deleted Rule 1 + Rule 2 exactly — do NOT broaden):
    #      - DIP24_2816 (was Rule 1): force 0x0D for any proto (24-pin 28C family).
    #      - DIP28_28C64 / DIP28_28C256 / (DIP28_2764 with flags&0x10) (was Rule 2):
    #        flip ONLY EPROM-family proto (0x07/0x08/0x0B) chips. Genuine 5V FLASH
    #        on the same DIP28 layout (AT29C256/AT29LV256, proto 0x05) is NOT a 28C
    #        EEPROM — it must keep its Flash algorithm (handled by arm 4 below). The
    #        old Rule 2 keyed on proto==0x07, so flash-proto chips were never flipped.
    if pinout_key == "DIP24_2816":
        return "EEPROM", 0x0D, pinout_key
    if proto_id in {0x07, 0x08, 0x0B} and (
        pinout_key in {"DIP28_28C64", "DIP28_28C256"}
        or (pinout_key == "DIP28_2764" and (flags & 0x10))
    ):
        return "EEPROM", 0x0D, pinout_key

    # 3. EPROM-family proto — flags&0x10 distinguishes CMOS EEPROM from UV-EPROM.
    if proto_id in {0x07, 0x08, 0x0B}:
        return ("EEPROM" if (flags & 0x10) else "UV-EPROM"), proto_id, pinout_key

    # 4. Flash families.
    if proto_id in {0x05, 0x06, 0x0D, 0x10}:
        return "Flash/EEPROM", proto_id, pinout_key

    # 4b. X88C64 (proto 0x34 XICOR NovRAM/EEPROM) — display-only EEPROM type;
    #     algorithm stays proto_id; chip remains protocol-not-implemented.
    if proto_id == 0x34:
        return "EEPROM", proto_id, pinout_key

    # 5. default.
    return "UV-EPROM", proto_id, pinout_key


def interpret_timing(raw_hex, protocol_id):
    # [VERIFIED: minipro database.c#L866 @ a8efaedc]
    # Raw pulse_delay is microseconds for ALL protocols — no multiplier.
    # Contract (D-08, Phase 148 Plan 03): returns an int, always microseconds.
    # `0` means "algorithm-controlled" (protocols that do not consume
    # pulse-delay) -- an unparseable pulse_delay on a protocol that DOES
    # consume it (0x07/0x08/0x0B) is fatal, not a silent 0, so that sentinel
    # keeps exactly one meaning.
    try:
        val = int(raw_hex, 16)
    except (TypeError, ValueError):
        # WR-05 (98-03): narrowed from bare `except Exception` so an unparseable
        # pulse_delay is visible (not silently masked as a valid 0 us timing) —
        # an upstream infoic.xml decode fault would otherwise ship wrong timing
        # to the firmware unnoticed. Phase 148 D-08 finishes what WR-05 started:
        # after the string/int collapse a returned `0` would otherwise mean
        # either "algorithm-controlled" (417 chips) or "decode fault on a
        # 0x07/0x08/0x0B chip", so this branch is now fatal instead of masked —
        # main() aborts before the JSON write and no wrong database is emitted.
        raise ValueError(
            f"chip with protocol {protocol_id:#04x} has unparseable "
            f"pulse_delay {raw_hex!r} — refusing to default to 0 us"
        ) from None

    if protocol_id in (0x07, 0x08, 0x0B):
        return val

    return 0


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
                # PROV-01 (136.1-01): raw, un-curated upstream page_size attribute
                # off this SAME <ic> element. Deliberately NOT the same key as the
                # existing datasheet-curated _PAGE_SIZE_BY_PART / programming.page_size
                # mechanism a few dozen lines below -- same English word, two
                # different sources, never to be confused. This raw field remains
                # the raw provenance axis (PROV-06's corroborating axis, b15 vs
                # infoic_page_size_raw > 1) and is now ALSO, as of Phase 149
                # (PGSZ-01), the value source for the programming.page_size emit
                # arm below when this <ic>'s own protocol_id is 0x0D — the two
                # remain deliberately distinct keys even where their values
                # coincide. Default-safe (0x0) mirroring
                # 120-derive-sdp-allowset.py:26's `pg = int(ic.get("page_size", "0x0"), 16)`
                # pattern.
                raw_page_size = int(ic.get("page_size", "0x0"), 16)
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

                # Step 3: Principled classification (Phase 86 VAR-02).
                # The single classify() call replaces the deleted Rule 1 / Rule 2
                # (WARNING-5) / Rule 3 override stack AND the two-pass _etype
                # derivation. It keys on type/proto/pm_idx/flags (NOT the variant
                # high byte — DECODE-NOTES.md §2), returning the final
                # (electrical.type, algorithm) plus a possibly re-routed pinout for
                # the type=4 28-pin SRAM/FRAM case (subsuming Rule 3's pinout fix).
                # resolve_pinout_key (variant LOW byte) is UNCHANGED. Per D-06 no
                # residual post-classify override remains; check_dispatch.py
                # 0-violations (D-08) is the structural safety backstop.
                #
                # PGSZ-01 (Phase 149): classify() below REASSIGNS `proto_id` to
                # its resolved algorithm and discards provenance (e.g. a
                # promoted 5V-EEPROM arrives as 0x07/0x0B and leaves as 0x0D).
                # The page_size emit arm needs the chip's OWN upstream
                # protocol_id, not the post-classification algorithm, so it is
                # captured here, before the reassignment.
                _upstream_proto_id = proto_id
                _etype, proto_id, pinout_key = classify(
                    type_int, proto_id, pm_idx, flags, pinout_key, mem_size
                )

                # Phase 84 D-40 per-chip cosmetic relabel (fm-fram-full decision).
                # Runs AFTER Pass-2 so the override is applied on top of the
                # protocol-based _etype derivation.  Keyed on part_number; does NOT
                # touch proto_id / pinout / vpp / algorithm — label-only correction.
                #
                # FM1608 (RAMTRON FRAM, algo=0x28/0x29 SRAM_512K_1M):
                #   SRAM → FRAM. CAN_ERASE is unaffected (FRAM ∉ {EEPROM,
                #   Flash/EEPROM}). VPP display stays hidden (companion guard in
                #   ic_layout.py + eprom_info.py: "not in {'SRAM','FRAM'}").
                #
                # SST39SF040: KEEP Flash/EEPROM (sst-keep D-40 STOP — relabeling to
                #   'Flash' would flip FLAG_CAN_ERASE OFF, breaking Phase-77/82-proven
                #   auto-erase). Observation recorded in DECODE-AUDIT.md (plan 84-04).
                _PHASE84_RELABEL = {"FM1608": "FRAM"}
                part_aliases_set = {a.split("@")[0].strip() for a in name.split(",")}
                for _relabel_pn, _relabel_etype in _PHASE84_RELABEL.items():
                    if _relabel_pn in part_aliases_set:
                        _etype = _relabel_etype
                        break

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

                # Canonical part-number key (first alias, @PACKAGE suffix
                # stripped) — hoisted here because the page_size emit arm
                # below (PGSZ-01, Phase 149) needs it in both its lookup and
                # its guard condition; previously recomputed twice inline.
                _canon = name.split(",")[0].split("@")[0].strip()

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
                        "vpp_mv": (
                            _nmos_vpp_mv
                            if _nmos_vpp_mv is not None
                            else VPP_MV.get(voltages & 0xF0, 0)
                        ),
                        # BUG-3 fix: vcc at bits 11-8, vdd at bits 15-12.
                        # v1.12 had them swapped (vdd at bits 11-8, vcc at bits 15-12).
                        # [VERIFIED: minipro database.c#L921-L923 @ a8efaedc]
                        "vcc_mv": VCC_VOLTAGES.get(
                            (voltages >> 8) & 0x0F, 5000
                        ),  # bits 11-8
                        "vdd_mv": VCC_VOLTAGES.get(
                            (voltages >> 12) & 0x0F, 5000
                        ),  # bits 15-12
                    },
                    "programming": {
                        "algorithm": proto_id,
                        "pulse_duration_us": interpret_timing(
                            ic.get("pulse_delay"), proto_id
                        ),
                        "chip_id_check": True if (flags & 0x20) else False,
                        "chip_id_value": ic.get("chip_id"),
                        # PROV-01 (136.1-01): flags bits 14/15 + raw page_size,
                        # decoded directly from the `flags`/`raw_page_size` values
                        # already parsed above for THIS exact <ic> element -- never
                        # a cross-reference or token match. (Phase 120's
                        # 120-derive-sdp-allowset.py needed token-matching because
                        # it worked from raw infoic.xml against an already-built
                        # chip_database.json with no per-row flags field; this
                        # decode has direct access to the source element and needs
                        # none of that machinery.)
                        # [VERIFIED: minipro src/database.c#L39-L50 @ a8efaedc236c1d9718bd28299dfbb99536b010ff
                        #  -- MP_OFF_PROTECT_BEFORE (bit 14, 0x4000) / MP_PROTECT_AFTER
                        #  (bit 15, 0x8000). Cross-reference:
                        #  doc/infoic-field-dictionary.md's CONFIRMED bit 14/15 row.]
                        # This decode is UNIVERSAL: every upstream-decoded entry
                        # gets all three keys below, not just the 84 protocol-0x0D
                        # bucket (mirrors the unconditional chip_id_check /
                        # chip_id_value fields immediately above) -- a flat decode
                        # of XML attributes into JSON fields, gated on nothing
                        # (no protocol/pinout/family condition), never the
                        # "structural regeneration" hard requirement 6 forbids.
                        "protect_off_before": True if (flags & 0x4000) else False,
                        "protect_on_after": True if (flags & 0x8000) else False,
                        "infoic_page_size_raw": raw_page_size,
                        # PGSZ-01 / CR-01 (Phase 149): provenance-keyed page-size
                        # emit rule. The page_size attribute is meaningful for the
                        # algorithm that consumes it, and a record filed upstream
                        # under 0x07/0x0B is not evidence about a 28C page buffer —
                        # so this is a claim about provenance, never about a part.
                        # Two disjoint arms, curated checked first for a minimal
                        # diff (both curated rows are upstream 0x05, so ordering
                        # is a legibility choice, not a correctness one):
                        #   (1) datasheet-curated _PAGE_SIZE_BY_PART lookup by
                        #       canonical part number (unchanged, today's
                        #       behaviour), else
                        #   (2) if this upstream <ic>'s own protocol_id is 0x0D,
                        #       emit its raw_page_size directly — 18 rows qualify,
                        #       15 at 128 and 3 at 64 (149-RESEARCH.md §"D-01
                        #       Verification").
                        # The 66 rows classify() promotes into 0x0D from a
                        # foreign protocol keep the firmware AT28C page-size
                        # floor (D-04) — this arm never fires for them because
                        # it reads _upstream_proto_id (captured before
                        # classify() reassigns proto_id to the resolved
                        # algorithm), not the post-classification value.
                        # Absent chips (neither arm fires) omit the field
                        # entirely. This change is software-proven and
                        # unvalidated on silicon.
                        **(
                            {"page_size": _PAGE_SIZE_BY_PART[_canon]}
                            if _canon in _PAGE_SIZE_BY_PART
                            else (
                                {"page_size": raw_page_size}
                                if _upstream_proto_id == 0x0D
                                else {}
                            )
                        ),
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
                    chip_entry["electrical"]["vcc_mv"] = chip_entry["electrical"][
                        "vdd_mv"
                    ]

                # VCC margin-rail substitution (Phase 148 DATA-01, D-01/D-02/D-03).
                # (1) The semantic: minipro's `vcc` for these parts is the TL866's
                # low-margin VCC *verify* rail (VCC_VOLTAGES index 0x02), not the
                # chip's operating supply -- firestarter surfaces it as though it
                # were the latter.
                # (2) This is the SAME category error the SRAM block immediately
                # above already corrects, generalized from a type key (`_etype ==
                # "SRAM"`) to the decoded value alone (`vcc_mv ==
                # _VCC_MARGIN_RAIL_MV`) -- no part number, no type, no algorithm.
                # (3) Measured blast radius: exactly 56 chips, every one landing on
                # 5000 mV (= their own already-decoded vdd_mv). The rule cannot
                # lower a voltage by construction -- it only ever replaces the 4 V
                # margin rail with the higher vdd_mv value already present on the
                # same chip.
                # (4) Why NOT type/algorithm/part-number keyed: those were measured
                # and rejected. type-keyed (EEPROM/Flash-EEPROM) -> 85 movers;
                # algorithm-keyed (0x0D) -> 84 movers; relation-keyed (vcc < vdd <=
                # 5500) -> 225 movers (sweeping in UV-EPROMs whose vdd is the
                # elevated 6.5V program rail). All three alternatives would also
                # set sixteen genuinely-5V EEPROMs (Microchip 28C256/28C16A/2817,
                # etc.) to 3.3V -- worse than the 4V defect being fixed here. If
                # you are considering widening this condition, re-measure against
                # that four-way split before touching it.
                if chip_entry["electrical"]["vcc_mv"] == _VCC_MARGIN_RAIL_MV:
                    chip_entry["electrical"]["vcc_mv"] = chip_entry["electrical"][
                        "vdd_mv"
                    ]

                chips.append(chip_entry)
                total_chips += 1

            if chips:
                complete_db[mfg_name] = chips

    # ==========================================
    # VAR-05 / D-10: NON-UPSTREAM CHIP SUPPLEMENT (post-decode merge)
    # ==========================================
    # Merge tools/extra_chips.json into complete_db AFTER the infoic.xml decode
    # loop and BEFORE the JSON write. These are physically-real 24-pin UV-EPROM
    # oddballs (2516, 2532) that are ABSENT from minipro's infoic.xml entirely —
    # so they ship first-class in chip_database.json via this curated, provenance-
    # cited supplement instead of per-operator ~/.firestarter/database.json edits.
    #
    # This is NOT a return of the deleted Rule 1/2/3: those patched chips that were
    # already IN infoic.xml (a decode correction); the supplement adds chips with NO
    # upstream record at all (a categorically different concern). The supplement
    # records arrive FULLY-SPECIFIED and are deliberately NOT routed through
    # classify() / resolve_pinout_key — they have no infoic.xml fields to decode.
    # Each record carries its own source/datasheet provenance (D-11) and is byte-
    # faithful here (the merge does not mutate any wire value).
    supplement_count = 0
    if os.path.exists(EXTRA_CHIPS_FILE):
        with open(EXTRA_CHIPS_FILE) as ef:
            extra_db = json.load(ef)
        for mfg_name, extra_chips in extra_db.items():
            if not isinstance(extra_chips, list):
                continue
            complete_db.setdefault(mfg_name, []).extend(extra_chips)
            supplement_count += len(extra_chips)
        print(
            f"VAR-05 supplement: merged {supplement_count} non-upstream chip(s) "
            f"from {EXTRA_CHIPS_FILE} (post-decode)."
        )
    else:
        print(f"VAR-05 supplement: {EXTRA_CHIPS_FILE} not found — skipping merge.")

    with open(OUTPUT_FILE, "w") as f:
        json.dump(complete_db, f, indent=2, sort_keys=True)

    print(
        f"Done! {total_chips} upstream chips processed "
        f"+ {supplement_count} non-upstream supplement chip(s) "
        f"= {total_chips + supplement_count} total. Saved to {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()
