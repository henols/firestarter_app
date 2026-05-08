# CLAUDE.md — Firestarter App

Python host CLI for the Firestarter EPROM programmer. Communicates with the Arduino firmware over serial at 250000 baud.

## Development Commands

```bash
pip install -e .                    # install in dev mode
firestarter --help                  # verify install
./firestarter_test.sh [EPROM]       # hardware integration test
python tools/parse_db_2.py          # regenerate chip database from infoic.xml
```

## Architecture

### Data Flow

```
infoic.xml → parse_db_2.py → minipro_complete_db.json
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

- `firestarter/data/minipro_complete_db.json` — generated chip database (do NOT edit by hand)
- `firestarter/data/pinouts.json` — physical DIP pin → RURP bus line mappings
- `firestarter/database.py` — `EpromDatabase` singleton: lookup, pin translation, command building
- `firestarter/eprom_operations.py` — high-level operations (read, write, erase, verify, blank check)
- `firestarter/serial_comm.py` — serial protocol implementation (INIT/MAIN/END state machine)
- `firestarter/main.py` — Click CLI entry point
- `tools/parse_db_2.py` — database pipeline: parses minipro `infoic.xml`, outputs JSON

### Wire Protocol

JSON commands sent to firmware at 250000 baud. The `algorithm` field carries the minipro `protocol_id` integer and is the primary firmware dispatch key.

Example write command:
```json
{
  "cmd": 2,
  "type": 1,
  "algorithm": 7,
  "memory-size": 65536,
  "vpp": 12000,
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

`tools/parse_db_2.py` parses `tools/infoic.xml` (minipro chip database XML) and outputs `firestarter/data/minipro_complete_db.json`.

Key fields per chip entry:
- `algorithm` — minipro `protocol_id` integer (primary dispatch key)
- `vpp_mv` — VPP voltage in millivolts (decoded from `voltages` field)
- `pinout` — DIP pinout key (`DIP24_2716`, `DIP28_27256`, `DIP28_27512`, `DIP28_2764`, `DIP32_STD`)

Known protocols (chips with unknown protocol_id are skipped with a warning):
`0x05, 0x06, 0x07, 0x08, 0x0B, 0x0D, 0x0E, 0x10, 0x27, 0x28, 0x29, 0x35, 0x39`

### Constants

`firestarter/constants.py` must stay in sync with `firestarter/include/firestarter.h` in the firmware sub-repo. Both define the same flag bit values and command codes.
