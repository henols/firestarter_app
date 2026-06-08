<p align="left"><img src="https://raw.githubusercontent.com/henols/firestarter_app/refs/heads/main/images/firestarter_logo.png" alt="Firestarter EPROM Programmer" width="200"></p>

---

## Protocol-ID Reference

This document is derived from the field dictionary (`doc/infoic-field-dictionary.md`). The canonical source of truth is the minipro `IC2_ALG_*` constants in [`database.h#L24`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.h#L24)–[`L77`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.h#L77) @ commit `a8efaedc236c1d9718bd28299dfbb99536b010ff`.

---

## In-Scope Protocol IDs

These IDs reach the Firestarter INFOIC2PLUS DIP-24..32 filter and have active firmware handlers.

| Protocol-ID | IC2_ALG Constant    | Firestarter Label   | Firmware Handler        | Notes                                                                  |
|-------------|---------------------|---------------------|-------------------------|------------------------------------------------------------------------|
| `0x05`      | `IC2_ALG_F29EE`     | `FLASH_AMD_STD`     | `configure_flash4`      | AMD/Fujitsu 5V page-write flash                                        |
| `0x06`      | `IC2_ALG_W29F32P`   | `FLASH_AMD_ALT`     | `configure_flash3`      | Winbond/SST AMD-unlock 5V flash                                        |
| `0x07`      | `IC2_ALG_ROM28P_1`  | `EPROM_STD`         | `configure_eprom`       | 28-pin UV-EPROM primary; some EEPROMs mistagged here (see WARNING-5)   |
| `0x08`      | `IC2_ALG_ROM32P`    | `EPROM_QUICK`       | `configure_eprom`       | 32-pin UV-EPROM (27C010/020/040)                                       |
| `0x0B`      | `IC2_ALG_ROM24P_1`  | `EPROM_LEGACY`      | `configure_eprom`       | 24-pin legacy EPROM (2716/2732)                                        |
| `0x0D`      | `IC2_ALG_EE28C32P`  | `EEPROM_POLL`       | `configure_eeprom28c`   | 28/32-pin 5V EEPROM (28C-series, DQ7 polling)                         |
| `0x0E`      | `IC2_ALG_RAM32_1`   | `SRAM_32PIN`        | `configure_sram`        | 32-pin SRAM type 1                                                     |
| `0x10`      | `IC2_ALG_28F32P`    | `FLASH_INTEL`       | `configure_flash_intel` | Intel 28F parallel flash (12V VPP, command register)                  |
| `0x27`      | `IC2_ALG_ROM24P_2`  | `SRAM_24PIN`        | `configure_sram`        | 24-pin SRAM (6116 family); `fm1608` override in effect                 |
| `0x28`      | `IC2_ALG_ROM28P_2`  | `SRAM_STD`          | `configure_sram`        | 28-pin SRAM; `fm1608`/WARNING-5 override target                        |
| `0x29`      | `IC2_ALG_RAM32_2`   | `SRAM_512K_1M`      | `configure_sram`        | 32-pin SRAM 512K/1M                                                    |

**WARNING-5 override:** `build_db.py` applies an inline override when `pinout_key == "DIP28_2764"` AND `protocol_id == 0x07` AND `_etype == "Flash/EEPROM"` — the chip's `algorithm` is flipped to `0x0D` (EEPROM_POLL) so firmware dispatch reaches `configure_eeprom28c`. This prevents 12V VPP from being asserted on pin 1 of 5V-only EEPROMs. The `_etype` discriminator uses `flags & 0x10` (`MP_ERASE_MASK`). See `CLAUDE.md` WARNING-5 section and `check_dispatch.py`.

---

## Excluded / Infeasible IDs

These IDs have entries in the minipro source but are not reachable through the Firestarter INFOIC2PLUS DIP-24..32 filter, or have no valid IC2_ALG constant in `database.h`.

| Protocol-ID | IC2_ALG Constant or Status | Exclusion Reason                                                                                                   |
|-------------|----------------------------|--------------------------------------------------------------------------------------------------------------------|
| `0x11`      | `IC2_ALG_FWH`              | Intel LPC Firmware Hub — 4-wire serial bus, 3.3V VCC; not a parallel interface; infeasible on RURP hardware       |
| `0x2A`      | `IC2_ALG_GAL16`            | GAL16V8 PLD algorithm (`type=3`); no DIP parallel memory chips; filtered before reaching `KNOWN_PROTOCOLS` check  |
| `0x2C`      | `IC2_ALG_GAL22`            | GAL22V10 PLD algorithm (`type=3`); no DIP parallel memory chips; filtered before reaching `KNOWN_PROTOCOLS` check |
| `0x2E`      | `IC2_ALG_PIC32X_2`         | PIC32 MCU algorithm (`type=2`); no DIP parallel memory chips; filtered before reaching `KNOWN_PROTOCOLS` check    |
| `0x35`      | `IC2_ALG_ITE`              | ITE IT8xxx EC MCU (`type=2`), TQFP128 package; no DIP parallel memory chips                                       |
| `0x39`      | **PHANTOM — no IC2_ALG constant** | No constant defined in `database.h`; this ID appears only in the legacy INFOIC format (not INFOIC2PLUS) for DIP40 chips; INFOIC2PLUS-unreachable; **must not be treated as a valid protocol** |
| `0x3C`      | **INVENTED — not in minipro source** | No `IC2_ALG` constant exists at `0x3C` in any minipro source file at commit `a8efaedc`; no chips; not a real protocol |

> **Note on `build_db.py` BUG-4 (DEC-05):** The current `PROTOCOL_MAP` in `build_db.py` labels `0x2A` as `NVRAM_32PIN`, `0x2C` as `NVRAM_TIMEKEEPER`, `0x2E` as `NVRAM_512K`, and `0x35` as `FLASH_EEPROM_LIKE` — all wrong. The canonical `IC2_ALG_*` names from `database.h` are the correct identifiers. The entries `0x39` and `0x3C` should be removed from `KNOWN_PROTOCOLS` / `PROTOCOL_MAP` respectively. These fixes are deferred to Phase 57.

---

## Usage Note

The `protocol_id` field in `chip_database.json` is stored as the `algorithm` key and is the primary firmware dispatch key — see `CLAUDE.md` §Wire Protocol. When adding or validating chip entries, use the `IC2_ALG_*` constant name as the canonical identifier and confirm the firmware handler matches the table above.
