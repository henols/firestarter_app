# CLAUDE.md — Firestarter App

Python host CLI for the Firestarter EPROM programmer. Communicates with the Arduino firmware over serial at 250000 baud.

## Development Commands

```bash
pip install -e .                    # install in dev mode
firestarter --help                  # verify install
./firestarter_test.sh [EPROM]       # hardware integration test
python tools/build_db.py            # regenerate chip database from infoic.xml
```

## Architecture

### Data Flow

```
infoic.xml → build_db.py → chip_database.json
                                        ↓
firestarter <chip> write/read/erase
     ↓
EpromDatabase.get_eprom(name)       # look up chip
     ↓
database._map_data()                # extract algorithm, vpp_mv, pinout
     ↓
database.convert_to_programmer()    # translate DIP pins to bus config
     ↓
eprom_operations.py                 # build JSON command
     ↓
serial_comm.py                      # send over serial, handle response
```

### Key Files

- `firestarter/data/chip_database.json` — generated chip database (do NOT edit by hand)
- `firestarter/data/pinouts.json` — physical DIP pin → RURP bus line mappings
- `firestarter/database.py` — `EpromDatabase` singleton: lookup, pin translation, command building
- `firestarter/eprom_operations.py` — high-level operations (read, write, erase, verify, blank check)
- `firestarter/serial_comm.py` — serial protocol implementation (INIT/MAIN/END state machine)
- `firestarter/main.py` — Click CLI entry point
- `tools/build_db.py` — database pipeline: parses the upstream `infoic.xml`, outputs JSON

### Wire Protocol

JSON commands sent to firmware at 250000 baud. The `algorithm` field carries the upstream `protocol_id` integer and is the primary firmware dispatch key.

Example write command:
```json
{
  "cmd": 2,
  "type": 1,
  "algorithm": 7,
  "memory-size": 65536,
  "vpp_mv": 12000,
  "pulse-delay": 0,
  "pin-count": 28,
  "chip-id": 42495,
  "flags": 10,
  "bus-config": { ... }
}
```

Firmware responses are prefix-tagged lines: `OK:`, `DATA:`, `MAIN:`, `END:`, `ERROR:`.

### Database Pipeline

`tools/build_db.py` parses `tools/infoic.xml` (minipro chip database XML) and outputs `firestarter/data/chip_database.json`.

Key fields per chip entry:
- `algorithm` — upstream `protocol_id` integer (primary dispatch key)
- `vpp_mv` — VPP voltage in millivolts (decoded from `voltages` field)
- `pinout` — DIP pinout key (`DIP24_2716`, `DIP28_27256`, `DIP28_27512`, `DIP28_2764`, `DIP32_STD`)

Known protocols (chips with unknown protocol_id are skipped with a warning):
`0x05, 0x06, 0x07, 0x08, 0x0B, 0x0D, 0x0E, 0x10, 0x27, 0x28, 0x29, 0x35, 0x39`

Protocol overrides (WARNING-5): `build_db.py` applies an inline 3-predicate
conditional after deriving `_etype` and before constructing `chip_entry`. When
`pinout_key == "DIP28_2764"` AND `proto_id == 0x07` (EPROM_STD) AND
`_etype == "Flash/EEPROM"`, the chip's `algorithm` is flipped to `0x0D`
(EEPROM_POLL) so firmware dispatch reaches `configure_eeprom28c` (pure 5V VCC,
no VPP regulator engagement) instead of `configure_eprom`. Rationale: on the
`DIP28_2764` pinout, socket pin 1 maps to the VPP regulator output line and
`configure_eprom` asserts `P1_VPP_ENABLE` (12V) on every write pulse; on the
~23 affected 28C-family 5V EEPROMs, physical pin 1 is the A14 address line,
not VPP, so 12V on pin 1 is a hardware-damage path. Scope: ~23 chips across 6
manufacturers — ATMEL (AT28C/BV family), MICROCHIP memory (28C/28LV family),
NEC (UPD28C family), XICOR (X28C family), ST (M28256), EXEL (XLE2865A). 7 chips
remain on the `0x07` path because they are genuine UV-EPROMs on `DIP28_27512`
or `DIP28_27256` pinouts (W27C512, SST27SF512, SST27VF512, W27C257, W27E257,
SST27SF256, SST27VF256) and DO need 12V VPP on pin 1. See `WARNING-5` in
`.planning/v1.0-MILESTONE-AUDIT.md` and the phase folder
`.planning/phases/13-close-gap-warning-5-at28c256-64-5v-eeprom-override-12v-on-we/`.
Regression guard: `tools/check_dispatch.py` asserts no chip with
`pinout=DIP28_2764 AND electrical.type=Flash/EEPROM` routes to `configure_eprom`.

### Constants

`firestarter/constants.py` must stay in sync with `firestarter/include/firestarter.h` in the firmware sub-repo. Both define the same flag bit values and command codes. Additionally, the `RURP_CONTROL_REGISTER_BITS` block in `constants.py` (CTRL_* names) mirrors the control-register-bit declarations in `firestarter/include/rurp_pinout.h` (Phase 33 / v1.7 — silkscreen-label code-alias migration). Keep CTRL_* names + hex values in sync with the firmware header. Additionally, the `RURP_HARDWARE_REVISIONS` block in `constants.py` (REVISION_* names) mirrors the hardware-revision enum declarations in `firestarter/include/rurp_shield.h` (Phase 34 / v1.7 — shield-version-detect design + firmware plumbing). Keep REVISION_* names + byte values in sync with the firmware enum; `0xFF` is reserved as the EEPROM-override-absent sentinel and `0xFE` (`REVISION_UNKNOWN`) is reserved for the ADC-band-gap fall-through. Additionally, the sub-repo `firestarter/doc/SHIELD-REVISIONS.md` operator-facing doc is a subset clone of meta-repo `.planning/v1.7-SHIELD-REVS.md` sections §1 (inventory) / §6 (per-rev capability matrix) / §7 (silkscreen → code alias table) / §9 (per-rev ADC band table) (Phase 35 / v1.7 — close); if any of those four sections change in the meta-repo, update the sub-repo doc in lockstep.
