<p align="left"><img src="https://raw.githubusercontent.com/henols/firestarter_app/refs/heads/main/images/firestarter_logo.png" alt="Firestarter EPROM Programmer" width="200"></p>

---

## Protocol Reference

| Protocol ID | Typical Devices (examples)                              | Device Class                   | Programming Characteristics                                                                                           |
|-------------|---------------------------------------------------------|--------------------------------|------------------------------------------------------------------------------------------------------------------------|
| `0x05`      | SST29EE010, W29C010/020/040                             | 5 V flash / EEPROM             | Needs JEDEC software command sequences for write/erase; standard VCC; often implements data-protect unlock cycles.    |
| `0x06`      | Am29F010, SST39SF010A/040, W39V040A                     | Parallel flash                 | Intel/AMD-style flash algorithm with command writes for program/erase; no elevated VPP; supports sector/chip erase.   |
| `0x07`      | AT28C17/64/256, AM28C64, CAT28C256, X28C64/256          | 28-pin byte EEPROM             | Byte-programmed 5 V EEPROMs; WE-controlled writes with optional software data protection; no dedicated high VPP pin.  |
| `0x08`      | 27C256, 27C512, W27C512, Linkage/PTC 28C010/20/40       | UV EPROM / EPROM-like EEPROMs  | Classic EPROM algorithm; requires VPP ≥12 V and long pulses; some “28C” labeled parts behave like EPROMs under this.   |
| `0x0B`      | 2716/2732, AT28C04/16, AM28C16A                         | 24-pin legacy EPROM/EEPROM     | Older 24-pin socket footprints; high VPP on shared pin; short byte pulses and minimal command sequencing.             |
| `0x0D`      | AT28C010/020/040, CAT28C010/020/040, X28C010            | 32-pin high-density EEPROM     | Byte-programmed 5 V EEPROMs with many address lines; long internal write delays; always do SDP unlock before writes.  |
| `0x0E`      | M48T128Y, BQ4013YMA                                     | NVRAM / battery-backed SRAM    | Conventional SRAM read/writes combined with RTC/NVRAM control; no special programming voltage, but device-specific ops.|
| `0x10`      | 28F010, M28F010                                         | Intel-style flash              | Requires Intel command set (write/erase/clear status); 5 V only; sector erase granularity.                             |

---

## Flag Bits (single-bit meaning)

| Bit | Mask        | Meaning (most consistent interpretation)                                 |
|-----|-------------|---------------------------------------------------------------------------|
| 3   | `0x00000008`| Requires elevated VPP for programming (typical EPROM behavior).           |
| 4   | `0x00000010`| Requires explicit write-enable or software unlock sequence.              |
| 5   | `0x00000020`| Supports readable manufacturer/device ID command.                        |
| 6   | `0x00000040`| UV-erasable EPROM timing (longer pulses, erase by UV).                   |
| 7   | `0x00000080`| Electrically erasable / writable (EEPROM/Flash/SRAM).                    |
| 9   | `0x00000200`| Boot-block/sector-protection features present.                           |
| 14  | `0x00004000`| Software data protection (SDP) supported or enabled.                     |
| 15  | `0x00008000`| Hardware/firmware write-protection requirements (extra sequence or pin). |
| 22  | `0x00400000`| Supports per-block lock or sector protection toggles.                    |

---

## Combined Flag Interpretations

| Combined Mask | Observed On                                   | Practical Meaning                                                                                                        |
|---------------|-----------------------------------------------|---------------------------------------------------------------------------------------------------------------------------|
| `0x00000058` (Bits 3,4,6) | Linkage/PTC “28C010/20/40” entries | Behaves like EPROM despite “28C” name: needs VPP, UV-style pulse timing, plus write-sequence unlock.                      |
| `0x00000068` (Bits 3,5,6) | Fairchild NMC27C128C               | Standard UV EPROM with readable ID; no EEPROM behavior.                                                                   |
| `0x00000080` (Bit 7 only) | AMD/Catalyst/Exel 28-pin parts     | Generic electrically-erasable parts without explicit SDP flag; simple WE pulse suffices.                                  |
| `0x00000010` (Bit 4 only) | Atmel/Microchip/NEC 24/28-pin parts| Needs SDP unlock sequence even though no other features flagged; pure byte EEPROM algorithm.                              |
| `0x0000C010` (Bits 14,15 + Bit 4) | AT28C64B/256 families        | EEPROM with mandatory SDP unlock; expect write commands to start with 3-byte sequence.                                    |
| `0x0000C080` (Bits 14,15 + Bit 7) | Catalyst/SGS/Exel parts      | Electrically erasable with advanced software protection toggles, but vendors omit write-sequence flag.                    |
| `0x0000C000` (Bits 14,15 only) | Samsung KM28C64, Xicor X28C64/256 | Vendor asserts SDP support but leaves other hints unset; assume unlock cycle even if Bit 4 is 0.                           |
| `0x0000C058` (Bits 3,4,6,14,15) | 32-pin 28C010/020/040 families | Needs high VPP plus SDP unlock and EPROM-like timing.                                                                     |
| `0x0000C000` + Bit 22 (e.g., `0x0040C000`) | Advanced flash parts | Combination indicates block-locking on top of SDP; issue block-unlock commands before erasing.                             |

---

## Oddballs / Special Cases

1. **Linkage & PTC “28C010/20/40”** (`protocol-id 0x08`, `flags 0x00000058`): despite the 28C naming they are treated exactly like EPROMs, requiring 12 V VPP and EPROM pulse lengths. Plan for EPROM algorithm until confirmed otherwise.
2. **AT28C64B (Non-Standard)**: shares the usual `0x07` protocol but keeps the older `0x00000010` flag combination, indicating a different SDP unlock order than the standard B revision.
3. **Samsung KM28C64 / Xicor X28C64/256**: use flags `0x0000C000` (no Bit 4/7) even though they are byte EEPROMs; always perform SDP unlock or writes may silently fail.
4. **Fairchild NMC27C128C**: only 27-series device with a “C” but is strictly a UV EPROM (`flags 0x00000068`, `protocol-id 0x07`), so treat it like a 27C128 even though it appears among EEPROMs in the database.
5. **Large Atmel/CAT/Xicor 28C512/010 parts**: all map to `protocol-id 0x0D` and `flags 0x0000C058`. They never expose a readable chip ID despite being newer devices—chip-id is zero in the database—so auto-detection relies entirely on user selection.
6. **Intel-compatible flash parts with EEPROM-like IDs**: Some SST parts advertise EEPROM IDs but sit on protocol `0x05`. Always use the command set implied by the protocol rather than trusting the flags alone.

---

Use this sheet as a quick lookup when adding new entries or building programmer support; cross-check questionable parts (“oddballs”) against datasheets before trusting the inferred behavior.

