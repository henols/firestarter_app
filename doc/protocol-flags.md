<p align="left"><img src="https://raw.githubusercontent.com/henols/firestarter_app/refs/heads/main/images/firestarter_logo.png" alt="Firestarter EPROM Programmer" width="200"></p>

---

## Protocol Flags Reference

This document is derived from the field dictionary (`doc/infoic-field-dictionary.md`). Source: `MP_*` constants from [`database.c#L39`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L39)–[`L50`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L50) @ commit `a8efaedc236c1d9718bd28299dfbb99536b010ff`.

The full 32-bit `flags` value is forwarded to TL866II+ firmware. Only bits with a defined `MP_*` constant in the open minipro source are CONFIRMED below.

---

## Source-Confirmed Bits (CONFIRMED)

Each bit in this table has a corresponding `MP_*` constant in `database.c` lines 39–50.

| Bit   | Mask           | MP_* Constant                           | Meaning (CONFIRMED)                                                                   |
|-------|----------------|-----------------------------------------|---------------------------------------------------------------------------------------|
| 1     | `0x00000002`   | `MP_REVERSED_PACKAGE`                   | Package pin numbering is reversed                                                     |
| 4     | `0x00000010`   | `MP_ERASE_MASK`                         | **Can be electrically erased** — the WARNING-5 discriminator (see note below)         |
| 5     | `0x00000020`   | `MP_ID_MASK`                            | Has readable manufacturer/device chip ID                                              |
| 12    | `0x00001000`   | `MP_DATA_MEMORY_ADDRESS`                | Has data memory offset                                                                |
| 13    | `0x00002000`   | `MP_DATA_BUS_WIDTH` (alias `MP_DATA_ORG`) | Data bus width: 0 = 8-bit, 1 = 16-bit                                              |
| 14    | `0x00004000`   | `MP_OFF_PROTECT_BEFORE`                 | Off-protection before operation                                                       |
| 15    | `0x00008000`   | `MP_PROTECT_AFTER`                      | Protect after operation                                                               |
| 18    | `0x00040000`   | `MP_LOCK_BIT_WRITE_ONLY`                | Lock-bit is write-only                                                                |
| 19    | `0x00080000`   | `MP_CALIBRATION`                        | Has calibration data                                                                  |
| 20-21 | `0x00300000`   | `MP_SUPPORTED_PROGRAMMING`              | Programming support level                                                             |

**WARNING-5 note — bit 4 (`MP_ERASE_MASK = 0x10`):** `build_db.py` derives `_etype` from `flags & 0x10`. When `_etype == "Flash/EEPROM"` (bit 4 set), a chip is electrically erasable. The WARNING-5 override uses this discriminator to flip `DIP28_2764 + protocol_id 0x07 + electrically-erasable` chips to `algorithm 0x0D` (EEPROM_POLL), preventing 12V VPP assertion on pin 1 of 5V-only EEPROMs. Bit 4 means "can be electrically erased" — it does **not** mean "requires write-enable sequence." See `CLAUDE.md` WARNING-5 section and `check_dispatch.py`.

---

## Bits Without a Defined MP_* Constant (UNKNOWN)

The following bits appear in `flags` values in `chip_database.json` but have **no** `MP_*` constant in `database.c` lines 39–50. Their meaning is UNKNOWN from the open minipro source. They may have meaning to the closed-source TL866II+ firmware (which receives the full 32-bit `flags` value), but cannot be confirmed from the open source.

| Bit | Mask           | Current Docs Claim                          | Correct Statement                                                                                     |
|-----|----------------|---------------------------------------------|-------------------------------------------------------------------------------------------------------|
| 3   | `0x00000008`   | "Requires VPP (High Programming Voltage)"   | **UNKNOWN** — not a defined `MP_*` constant in `database.c` lines 39–50                              |
| 6   | `0x00000040`   | "Is UV-erasable EPROM"                      | **UNKNOWN** — not a defined `MP_*` constant in `database.c` lines 39–50                              |
| 7   | `0x00000080`   | "Is Electrically Erasable or Writable"      | **UNKNOWN** — not a defined `MP_*` constant in `database.c` lines 39–50; likely a TL866II+ firmware-internal bit forwarded raw |

The existing inferred meanings for bits 3/6/7 are derived from observed chip patterns, not from minipro source constants. They must not be treated as CONFIRMED.

---

## build_db.py Usage

- `_etype` derived from `flags & 0x10` (`MP_ERASE_MASK`) — correct
- `chip_id_check` derived from `flags & 0x20` (`MP_ID_MASK`) — correct
- Full `flags` value stored in `chip_database.json` and forwarded to firmware in wire JSON commands
