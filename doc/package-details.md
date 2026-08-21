<p align="left"><img src="https://raw.githubusercontent.com/henols/firestarter_app/refs/heads/main/images/firestarter_logo.png" alt="Firestarter EPROM Programmer" width="200"></p>

---

## package_details Field Reference

This document is derived from the field dictionary (`doc/infoic-field-dictionary.md`). Sources: [`database.c#L618`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L618)–[`L703`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L703) and [`database.c#L39`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L39)–[`L50`](https://gitlab.com/DavidGriffith/minipro/-/blob/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c#L50) @ commit `a8efaedc236c1d9718bd28299dfbb99536b010ff`.

---

## `package_details` uint32 Layout (CONFIRMED)

The `package_details` field governs physical-package filtering. `build_db.py` uses it to decide whether a chip passes the DIP filter.

| Bit(s) | Mask           | Field            | Meaning                                                                       |
|--------|----------------|------------------|-------------------------------------------------------------------------------|
| 31     | `0x80000000`   | `is_smd`         | SMD flag — surface-mount; exclude from DIP filter                             |
| 29-24  | `0x3F000000`   | pin count        | Raw 6-bit pin count (`build_db.py` uses `0x7F000000` — harmless for DIP)     |
| 15-8   | `0x0000FF00`   | `is_serial`      | ICSP serial-interface index — non-zero = exclude from DIP filter              |
| 7-0    | `0x000000FF`   | adapter type     | Adapter type: `0x00` = DIP native; other values = PLCC or other adapters      |

**PLCC adapter pin-count remapping:** adapter byte `0x38` → 20 pins, `0x3E` → 28 pins, `0x3F` → 32 pins, `0x3D` → 44 pins.

**build_db.py DIP filter:** `24 <= pin_count <= 32`, `is_smd == 0`, `is_serial == 0`, `type_int in [1, 4]` — correct per minipro source.

---

## `flags` Bit Reference

The `flags` field is a separate uint32 that controls programming behavior. It is stored in `chip_database.json` and forwarded in the wire JSON to firmware. The table below covers bits relevant to Firestarter decode.

### Source-Confirmed Bits (CONFIRMED)

Each entry has a corresponding `MP_*` constant in `database.c` lines 39–50.

| Bit   | Hex Mask       | MP_* Constant                           | Meaning                                                                                   | Status    |
|-------|----------------|-----------------------------------------|-------------------------------------------------------------------------------------------|-----------|
| 1     | `0x00000002`   | `MP_REVERSED_PACKAGE`                   | Package pin numbering is reversed                                                         | CONFIRMED |
| 4     | `0x00000010`   | `MP_ERASE_MASK`                         | **Can be electrically erased** — WARNING-5 discriminator (`build_db.py` uses `flags & 0x10` to derive `_etype`) | CONFIRMED |
| 5     | `0x00000020`   | `MP_ID_MASK`                            | Has readable manufacturer/device chip ID                                                  | CONFIRMED |
| 12    | `0x00001000`   | `MP_DATA_MEMORY_ADDRESS`                | Has data memory offset                                                                    | CONFIRMED |
| 13    | `0x00002000`   | `MP_DATA_BUS_WIDTH` (alias `MP_DATA_ORG`) | Data bus width: 0 = 8-bit, 1 = 16-bit                                                  | CONFIRMED |
| 14    | `0x00004000`   | `MP_OFF_PROTECT_BEFORE`                 | Off-protection before operation                                                           | CONFIRMED |
| 15    | `0x00008000`   | `MP_PROTECT_AFTER`                      | Protect after operation                                                                   | CONFIRMED |
| 18    | `0x00040000`   | `MP_LOCK_BIT_WRITE_ONLY`                | Lock-bit is write-only                                                                    | CONFIRMED |
| 19    | `0x00080000`   | `MP_CALIBRATION`                        | Has calibration data                                                                      | CONFIRMED |
| 20-21 | `0x00300000`   | `MP_SUPPORTED_PROGRAMMING`              | Programming support level                                                                 | CONFIRMED |

**Note on bits 14/15:** the row above documents minipro's *bit* semantics only; what the emitted `protect_off_before` / `protect_on_after` database fields mean at runtime, and their measured distributions, is [documented once in `infoic-field-dictionary.md`](infoic-field-dictionary.md#protect-flags-bits-14-15).

### Bits Without a Defined MP_* Constant (UNKNOWN)

The following bits appear in observed `flags` values but have **no** `MP_*` constant in `database.c` lines 39–50. Their meaning cannot be confirmed from the open minipro source.

| Bit | Hex Mask       | Current Docs Claim                          | Correct Statement                                                                                      | Status  |
|-----|----------------|---------------------------------------------|--------------------------------------------------------------------------------------------------------|---------|
| 3   | `0x00000008`   | "Requires VPP (High Programming Voltage)"   | **UNKNOWN** — not a defined `MP_*` constant in `database.c` lines 39–50                               | UNKNOWN |
| 6   | `0x00000040`   | "Is UV-erasable EPROM"                      | **UNKNOWN** — not a defined `MP_*` constant in `database.c` lines 39–50                               | UNKNOWN |
| 7   | `0x00000080`   | "Is Electrically Erasable or Writable"      | **UNKNOWN** — not a defined `MP_*` constant in `database.c` lines 39–50; likely a TL866II+ firmware-internal bit forwarded raw | UNKNOWN |

The inferred meanings for bits 3/6/7 in older documentation are derived from observed chip patterns, not source-confirmed. They must not be promoted to CONFIRMED.

---

## build_db.py Usage

- `_etype` derived from `flags & 0x10` (`MP_ERASE_MASK`) — correct
- `chip_id_check` derived from `flags & 0x20` (`MP_ID_MASK`) — correct
- Full `flags` value stored in `chip_database.json` and forwarded to firmware in wire JSON commands
- DIP filter uses `package_details` fields: `24 <= pin_count <= 32`, `is_smd == 0`, `is_serial == 0`, `type_int in [1, 4]`
