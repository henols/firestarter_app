<p align="left"><img src="https://raw.githubusercontent.com/henols/firestarter_app/refs/heads/main/images/firestarter_logo.png" alt="Firestarter EPROM Programmer" width="200"></p>

---

## infoic.xml Field Dictionary

This is the canonical source-cited authority Phase 57 codes against. Every Firestarter-relevant `infoic.xml` attribute is documented here, cross-referenced against minipro source at the pinned commit below. Each attribute carries a CONFIRMED / INFERRED / UNKNOWN confidence marker. Where `build_db.py` decodes a field incorrectly, the correct semantics are stated here with a BUG note — the code fix is deferred to Phase 57; this file documents what the code **should** do.

**Citation commit:** `a8efaedc236c1d9718bd28299dfbb99536b010ff` (2026-03-23, "infoic: Correct ATMEGA328PB fuse defaults")
**Permalink base:** `https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/`

All per-attribute source citations in this file use the SHA above. To verify a citation: `<permalink base><file>#L<line>`.

---

### `package_details` (uint32 hex) — CONFIRMED

**Source:** [`database.c#L618`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L618) – [`database.c#L703`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L703) @ `a8efaedc`

| Bit(s)  | Mask         | Meaning                                                        |
|---------|--------------|----------------------------------------------------------------|
| 31      | `0x80000000` | SMD flag — `is_smd` (surface-mount; exclude from DIP filter)  |
| 29-24   | `0x3F000000` | Raw pin count (6-bit field; `build_db.py` uses `0x7F000000` which is harmless for standard DIP) |
| 15-8    | `0x0000FF00` | ICSP serial-interface index — `is_serial` (non-zero = exclude) |
| 7-0     | `0x000000FF` | Adapter type (0x00 = DIP native)                               |

PLCC adapters remap the pin count: adapter byte `0x38`→20 pins, `0x3E`→28 pins, `0x3F`→32 pins, `0x3D`→44 pins.

**build_db.py usage:** DIP filter applies `24 <= pin_count <= 32`, `is_smd == 0`, `is_serial == 0`, `type_int in [1, 4]` — correct.

---

### `type` (uint32 hex) — CONFIRMED

**Source:** [`database.c#L583`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L583) @ `a8efaedc`; constants in `minipro.h`

| Value  | Constant    | Meaning                                                            |
|--------|-------------|--------------------------------------------------------------------|
| `0x01` | `MP_MEMORY` | ROM / EPROM / Flash / EEPROM (parallel memory)                     |
| `0x02` | `MP_MCU`    | Microcontroller                                                    |
| `0x03` | `MP_PLD`    | Programmable logic device (GAL, CPLD)                              |
| `0x04` | `MP_SRAM`   | SRAM / NVRAM / FRAM                                                |
| `0x05` | `MP_LOGIC`  | Logic IC (logicic.xml, not infoic.xml)                             |

**build_db.py usage:** filters `type_int in [1, 4]` — correct for Firestarter scope. Safety guard: `type_int == 4` with EPROM-family `protocol_id` triggers `fm1608` override; `type_int == 3` (PLDs) are filtered before reaching the `KNOWN_PROTOCOLS` check.

---

### `variant` (uint32 hex) — CONFIRMED

**Source:** [`database.c#L585`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L585) @ `a8efaedc`

Low byte (`variant_lo` = `variant & 0xFF`) = sub-algorithm / variant index sent to programmer. Bits 15-8 = T56/T76 name index (irrelevant on RURP).

**build_db.py usage:** `variant & 0xFF` — correct.

DIP28 UV-EPROM `variant_lo` sub-discriminator (CONFIRMED from chip survey):

| `variant_lo` | Pinout key      | Example chip      |
|-------------|-----------------|-------------------|
| `0x10`      | `DIP28_27512`   | 27C512 (VPP on pin 22) |
| `0x11`      | `DIP28_27256`   | 27C256 (VPP on pin 1)  |
| `0x12`      | `DIP28_2764`    | 27C128             |
| `0x13`      | `DIP28_2764`    | 27C64 / 2764A      |
| else        | `DIP28_2764`    | default            |

DIP24 `variant_lo`: `0x00` = `DIP24_2716`, `0x01` = `DIP24_2732`.

---

### `protocol_id` (uint8 hex) — CONFIRMED

**Source:** [`database.c#L685`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L685) @ `a8efaedc`; `IC2_ALG_*` constants in [`database.h#L24`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.h#L24)–[`L77`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.h#L77) @ `a8efaedc`

**In-scope IDs (reach INFOIC2PLUS DIP-24..32 filter):**

| protocol_id | IC2_ALG Constant    | Firestarter Label   | Firmware Handler        | Notes                                                      |
|-------------|---------------------|---------------------|-------------------------|------------------------------------------------------------|
| `0x05`      | `IC2_ALG_F29EE`     | `FLASH_AMD_STD`     | `configure_flash4`      | AMD/Fujitsu 5V page-write flash                            |
| `0x06`      | `IC2_ALG_W29F32P`   | `FLASH_AMD_ALT`     | `configure_flash3`      | Winbond/SST AMD-unlock 5V flash                            |
| `0x07`      | `IC2_ALG_ROM28P_1`  | `EPROM_STD`         | `configure_eprom`       | 28-pin UV-EPROM primary; some EEPROMs mistagged here       |
| `0x08`      | `IC2_ALG_ROM32P`    | `EPROM_QUICK`       | `configure_eprom`       | 32-pin UV-EPROM (27C010/020/040)                           |
| `0x0B`      | `IC2_ALG_ROM24P_1`  | `EPROM_LEGACY`      | `configure_eprom`       | 24-pin legacy EPROM (2716/2732)                            |
| `0x0D`      | `IC2_ALG_EE28C32P`  | `EEPROM_POLL`       | `configure_eeprom28c`   | 28/32-pin 5V EEPROM (28C-series, DQ7 poll)                 |
| `0x0E`      | `IC2_ALG_RAM32_1`   | `SRAM_32PIN`        | `configure_sram`        | 32-pin SRAM type 1                                         |
| `0x10`      | `IC2_ALG_28F32P`    | `FLASH_INTEL`       | `configure_flash_intel` | Intel 28F parallel flash (12V VPP, command register)       |
| `0x27`      | `IC2_ALG_ROM24P_2`  | `SRAM_24PIN`        | `configure_sram`        | 24-pin SRAM (6116 family); fm1608 override in effect       |
| `0x28`      | `IC2_ALG_ROM28P_2`  | `SRAM_STD`          | `configure_sram`        | 28-pin SRAM; fm1608/WARNING-5 override target              |
| `0x29`      | `IC2_ALG_RAM32_2`   | `SRAM_512K_1M`      | `configure_sram`        | 32-pin SRAM 512K/1M                                        |

**Out-of-scope / excluded IDs (with rationale):**

| protocol_id | IC2_ALG Constant or status | Exclusion Reason                                                                    |
|-------------|----------------------------|-------------------------------------------------------------------------------------|
| `0x11`      | `IC2_ALG_FWH`              | Intel LPC 4-wire serial bus + 3.3V VCC — not parallel, not 5V; infeasible on RURP  |
| `0x2A`      | `IC2_ALG_GAL16`            | GAL16V8 PLD algorithm — `type=3`; zero DIP memory chips                             |
| `0x2C`      | `IC2_ALG_GAL22`            | GAL22V10 PLD algorithm — `type=3`; zero DIP memory chips                            |
| `0x2E`      | `IC2_ALG_PIC32X_2`         | PIC32 MCU algorithm — `type=2`; zero DIP memory chips                               |
| `0x35`      | `IC2_ALG_ITE`              | ITE IT8xxx EC MCU — TQFP128; `type=2`; zero DIP memory chips                        |
| `0x39`      | NO IC2_ALG CONSTANT        | Phantom — no constant in `database.h`; INFOIC2PLUS-unreachable (legacy INFOIC only) |
| `0x3C`      | NO IC2_ALG CONSTANT        | Invented — not in minipro source at all; no chips                                   |

**BUG-4 (DEC-05):** Correct is: canonical `IC2_ALG_*` names from `database.h#L24`–`L77`; `0x2A` = `IC2_ALG_GAL16` (GAL PLD, not NVRAM), `0x2C` = `IC2_ALG_GAL22` (GAL PLD, not NVRAM), `0x2E` = `IC2_ALG_PIC32X_2` (PIC MCU, not NVRAM), `0x35` = `IC2_ALG_ITE` (ITE MCU, not `FLASH_EEPROM_LIKE`), `0x39` = phantom (no `IC2_ALG` constant, should be removed from `KNOWN_PROTOCOLS`), `0x3C` = invented (not in minipro source, should be removed from `PROTOCOL_MAP` entirely); current `build_db.py` `PROTOCOL_MAP` labels for these entries are wrong / phantom / invented; fix deferred to Phase 57.

---

### `flags` (uint32 hex) — CONFIRMED for decoded bits; UNKNOWN for bits 3/6/7

**Source:** [`database.c#L39`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L39)–[`L50`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L50) @ `a8efaedc`

**Source-confirmed bits (CONFIRMED) — each has a corresponding `MP_*` constant in `database.c` lines 39–50:**

| Bit   | Mask         | MP_* Constant              | Meaning                                             |
|-------|--------------|----------------------------|-----------------------------------------------------|
| 1     | `0x00000002` | `MP_REVERSED_PACKAGE`      | Package pin numbering is reversed                   |
| 4     | `0x00000010` | `MP_ERASE_MASK`            | **Can be electrically erased** — the WARNING-5 discriminator (`build_db.py` uses `flags & 0x10` to distinguish 5V EEPROMs from UV-EPROMs when both share `protocol_id=0x07`; bit 4 set = electrically erasable) |
| 5     | `0x00000020` | `MP_ID_MASK`               | Has readable manufacturer/device chip ID            |
| 12    | `0x00001000` | `MP_DATA_MEMORY_ADDRESS`   | Has data memory offset                              |
| 13    | `0x00002000` | `MP_DATA_BUS_WIDTH` (alias `MP_DATA_ORG`) | Data bus width: 0 = 8-bit, 1 = 16-bit |
| 14    | `0x00004000` | `MP_OFF_PROTECT_BEFORE`    | Off-protection before operation                     |
| 15    | `0x00008000` | `MP_PROTECT_AFTER`         | Protect after operation                             |
| 18    | `0x00040000` | `MP_LOCK_BIT_WRITE_ONLY`   | Lock-bit is write-only                              |
| 19    | `0x00080000` | `MP_CALIBRATION`           | Has calibration data                                |
| 20-21 | `0x00300000` | `MP_SUPPORTED_PROGRAMMING` | Programming support level                           |

**Bits with NO `MP_*` constant — meaning UNKNOWN:**

| Bit | Mask         | Current docs claim                          | Correct statement                                                                                     |
|-----|--------------|---------------------------------------------|-------------------------------------------------------------------------------------------------------|
| 3   | `0x00000008` | "Requires VPP (High Programming Voltage)"   | UNKNOWN — not a defined `MP_*` constant in `database.c` lines 39–50                                  |
| 6   | `0x00000040` | "Is UV-erasable EPROM"                      | UNKNOWN — not a defined `MP_*` constant in `database.c` lines 39–50                                  |
| 7   | `0x00000080` | "Is Electrically Erasable or Writable"      | UNKNOWN — not a defined `MP_*` constant in `database.c` lines 39–50; likely a TL866II+ firmware-internal bit forwarded raw |

The full 32-bit `flags` value is forwarded to TL866II+ firmware. Bits 3/6/7 may have meaning to the closed-source TL866II+ firmware but are NOT documented in the open minipro source. The existing docs' meanings for these bits are inferred from observed chip patterns, not source-confirmed. They must never be promoted to CONFIRMED.

**build_db.py usage:** `_etype` derived from `flags & 0x10` (`MP_ERASE_MASK`) — correct. `chip_id_check` derived from `flags & 0x20` (`MP_ID_MASK`) — correct.

---

### `voltages` (uint32 hex) — CONFIRMED

**Source:** [`database.c#L921`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L921)–[`L923`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L923) and [`database.c#L680`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L680)–[`L685`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L685) @ `a8efaedc`

**Field layout (correct — minipro source):**

| Bits  | Field              | Extract                              |
|-------|--------------------|--------------------------------------|
| 7-0   | VPP byte           | `device->voltages.vpp = voltages & 0xff` |
| 11-8  | VCC nibble         | `device->voltages.vcc = (voltages >> 8) & 0x0f` |
| 15-12 | VDD nibble         | `device->voltages.vdd = (voltages >> 12) & 0x0f` |

**VCC/VDD nibble → voltage** (from `tl866ii_vcc_voltages[]`, [`database.c#L130`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L130)–[`L135`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L135) @ `a8efaedc`):

| Nibble | Voltage   | In build_db.py VCC_VOLTAGES? |
|--------|-----------|------------------------------|
| `0x00` | 5V        | Yes                          |
| `0x01` | 3.3V      | Yes                          |
| `0x02` | **4V**    | **NO — MISSING**             |
| `0x03` | **4.5V**  | **NO — MISSING**             |
| `0x04` | 5.5V      | Yes                          |
| `0x05` | 6.5V      | Yes                          |

**VPP byte → millivolts** (VPP_MV in `build_db.py` — CONFIRMED correct):

| Byte   | VPP       | Byte   | VPP    | Byte   | VPP    | Byte   | VPP    |
|--------|-----------|--------|--------|--------|--------|--------|--------|
| `0x00` | 12V       | `0x40` | 11V    | `0x80` | 13.5V  | `0xC0` | 16V    |
| `0x10` | 9V        | `0x50` | 11.5V  | `0x90` | 14V    | `0xD0` | 16.5V  |
| `0x20` | 9.5V      | `0x60` | 12.5V  | `0xA0` | 14.5V  | `0xE0` | 17V    |
| `0x30` | 10V       | `0x70` | 13V    | `0xB0` | 15.5V  | `0xF0` | 18V    |

**BUG-1 (DEC-04):** Correct is: complete VCC/VDD nibble table includes `0x02=4V` and `0x03=4.5V` (from `tl866ii_vcc_voltages[]`, `database.c#L130`); current `build_db.py` `VCC_VOLTAGES` is missing `0x02` and `0x03`, causing any chip with VCC nibble `0x02` or `0x03` to silently fall back to the default `"5V"`; fix deferred to Phase 57.

**BUG-3 (DEC-04):** Correct is: `vdd = (voltages >> 12) & 0x0F` (bits 15-12), `vcc = (voltages >> 8) & 0x0F` (bits 11-8) as in `database.c#L921`–`L923`; current `build_db.py` lines 510–511 have vdd and vcc labels swapped — `vdd` reads the `>>8` (VCC) position and `vcc` reads the `>>12` (VDD) position; fix deferred to Phase 57.

---

### `pin_map` (uint32 hex) — CONFIRMED

**Source:** [`database.c#L608`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L608)–[`L617`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L617) @ `a8efaedc`

Low byte (`pm_idx = pin_map_raw & 0xFF`) = pin-test map index; clusters chips by physical layout family.

Upper flag bits:

| Bit/Mask     | Constant       | Meaning                                                                                |
|--------------|----------------|----------------------------------------------------------------------------------------|
| `0x10000000` | `T56_FLAG`     | Chip supported on T56 programmer                                                       |
| `0x20000000` | `TL866II_FLAG` | Note: `TL866II_FLAG=0` does NOT mean unprogrammable on TL866II+                        |
| `0x40000000` | `T48_FLAG`     | Chip supported on T48 programmer                                                       |

**build_db.py usage:** `pm_idx = pin_map_raw & 0xFF` — correct.

---

### `pulse_delay` (uint32 hex) — CONFIRMED

**Source:** [`database.c#L866`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L866) @ `a8efaedc`

**Raw value is microseconds for ALL protocols, with no transformation.** Minipro loads the field directly:

```c
err += get_attr_value(xml_device, size, "pulse_delay", &device->pulse_delay);
```

No multiplication or protocol-conditional conversion follows. The raw value stored in `device->pulse_delay` IS the pulse delay in microseconds.

Verified values (raw XML hex → µs):

| Chip       | protocol_id | Raw hex | Correct µs | build_db.py output (BUG-2) |
|------------|-------------|---------|------------|----------------------------|
| AM27C64    | `0x07`      | `0x64`  | 100 µs     | 10000 µs (×100 wrong)      |
| W27C512    | `0x07`      | `0x64`  | 100 µs     | 10000 µs (×100 wrong)      |
| AM2716     | `0x0B`      | `0x1F4` | 500 µs     | 50000 µs (×100 wrong)      |
| AT28C256   | `0x07`      | `0x2710`| 10000 µs   | 1000000 µs (×100 wrong)    |

**BUG-2 (DEC-03):** Correct is: raw `pulse_delay` value = microseconds for ALL protocols; current `build_db.py` `interpret_timing()` applies `val * 100` for `protocol_id` `0x07` and `0x0B` (`database.c#L866`) — this ×100 multiplier is wrong; 252 chips across those two protocols currently have inflated `pulse_duration` values in `chip_database.json`; fix deferred to Phase 57.

---

### `chip_id` (uint32 hex) — CONFIRMED

**Source:** [`database.c#L600`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L600), [`database.c#L561`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L561) @ `a8efaedc`

Raw silicon manufacturer/device ID. `0` = no ID. ID check is gated by `flags & MP_ID_MASK` (`0x20`).

**build_db.py usage:** `chip_id_check = True if (flags & 0x20) else False` — correct.

---

### `code_memory_size` (uint32 hex) — CONFIRMED

**Source:** [`database.c#L592`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L592) @ `a8efaedc`

Total addressable bytes. Example: 27C512 = `0x10000` = 65536 bytes. Used as firmware `memory-size`.

**build_db.py usage:** `mem_size = int(ic.get("code_memory_size"), 16)` — correct.

---

### `page_size` (uint32 hex) — CONFIRMED

**Source:** [`database.c#L598`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L598) @ `a8efaedc`

Page-write size for EEPROM/Flash. Typically 64 or 128 bytes for 28C-family; `0` or `1` if not applicable to the device type.

**build_db.py usage:** Not currently stored in `chip_database.json`. No decode bug; simply not used yet.

---

### `chip_info` (uint32 hex) — CONFIRMED

**Source:** [`database.c#L605`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L605) @ `a8efaedc`

Opaque discriminator. Known sentinel values:

| Value    | Constant        | Meaning                        |
|----------|-----------------|--------------------------------|
| `0x0006` | `MP_VOLTAGES1`  | Adjustable VCC                 |
| `0x0007` | `MP_VOLTAGES2`  | Adjustable VPP                 |
| else     | —               | MCU-specific or `0x0000` for standard parallel memory |

**build_db.py usage:** Not currently stored in `chip_database.json`. No decode bug; simply not used yet.

---

### `blank_value` (uint8 hex, optional) — CONFIRMED

**Source:** [`database.c#L627`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L627)–[`L631`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L631) @ `a8efaedc`

The erased-read byte for this device. Default `0xFF` when the attribute is absent from the XML. Used for blank-check operations.

**build_db.py usage:** Not currently stored in `chip_database.json`. No decode bug; simply not used yet.

---

## Summary: build_db.py Known Bugs vs Correct Semantics

| Bug ID | Attribute       | Correct decode                                                     | Current build_db.py behavior                                                    | Phase 57 fix |
|--------|-----------------|---------------------------------------------------------------------|---------------------------------------------------------------------------------|--------------|
| BUG-1  | `voltages` (VCC/VDD nibble) | `0x00=5V, 0x01=3.3V, 0x02=4V, 0x03=4.5V, 0x04=5.5V, 0x05=6.5V` (complete table from `tl866ii_vcc_voltages[]`) | `VCC_VOLTAGES` missing `0x02` and `0x03`; chips with those nibbles silently decode as `"5V"` | Add `0x02: "4V"` and `0x03: "4.5V"` to `VCC_VOLTAGES` |
| BUG-2  | `pulse_delay`   | Raw value is µs for ALL protocols; no multiplier                    | `interpret_timing()` applies ×100 for `0x07` and `0x0B`; 252 chips affected     | Remove ×100 multiplier from `interpret_timing()` |
| BUG-3  | `voltages` (field positions) | `vdd = (voltages >> 12) & 0x0F`, `vcc = (voltages >> 8) & 0x0F` | Lines 510–511 have labels swapped: `vdd` reads `>>8` (VCC position), `vcc` reads `>>12` (VDD position) | Swap `>>8` and `>>12` assignments |
| BUG-4  | `protocol_id`   | Canonical `IC2_ALG_*` names; `0x2A=IC2_ALG_GAL16`, `0x2C=IC2_ALG_GAL22`, `0x2E=IC2_ALG_PIC32X_2`, `0x35=IC2_ALG_ITE`; `0x39` = phantom (no constant); `0x3C` = invented (remove) | `PROTOCOL_MAP` labels `0x2A` as `NVRAM_32PIN`, `0x2C` as `NVRAM_TIMEKEEPER`, `0x2E` as `NVRAM_512K`, `0x35` as `FLASH_EEPROM_LIKE`; `0x39` and `0x35` remain in `KNOWN_PROTOCOLS` | Fix `PROTOCOL_MAP` entries; remove `0x39`/`0x35` from `KNOWN_PROTOCOLS`; remove `0x3C` from `PROTOCOL_MAP` |

---

*This file is the canonical authority for Phase 57 code changes. Do not modify `build_db.py` decode behavior in Phase 56 — that is Phase 57's scope.*
