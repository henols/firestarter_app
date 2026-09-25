# CLAUDE.md — Firestarter App

This repository holds the Python host CLI for the Firestarter EPROM programmer. The CLI talks to the
Arduino firmware over serial at 250000 baud.

## Development Commands

```bash
pip install -e '.[test]'            # install in dev mode. CI installs the [test] extra.
firestarter --help                  # check the install
./firestarter_test.sh [EPROM]       # run the hardware integration test
python tools/build_db.py            # generate the chip database from infoic.xml
```

**CI runs Python 3.11.** The devcontainer runs a later Python. A later interpreter can change some
snapshot failures into collection errors. A green local run therefore does not prove a green CI
run. Run the suite on Python 3.11 before you trust the result.

## What CI runs

`.github/workflows/ci.yml` has two jobs. The main job uses Python 3.11, installs `.[test]`, and runs
these steps in this order:

1. `ruff check firestarter/ tests/`
2. `ruff format --check firestarter/ tests/`
3. `pytest tests/ --cov=firestarter --cov-report=term-missing --cov-fail-under=70`
4. A smoke test: `pip install -e .`, then `firestarter --help`.

The second job installs `.[test,py32]`. It imports `pyusb` and records the installed version. Then
it runs `pytest tests/test_pyusb_api_surface.py -q`.

**A push to `beta` publishes this package.** `beta-release.yml` runs on each push to `beta`:

1. Its `github` job creates a GitHub pre-release.
2. Its `pypi` job calls `publish.yml` directly, with `secrets: inherit`, and **uploads to PyPI**.

The `pypi` job calls `publish.yml` directly for this reason: the `github` job creates the release
with `PERSONAL_ACCESS_TOKEN`. That token does not have the `workflow` scope, so GitHub does not send
the `release: published` event. **The workflow has no path filter.** A documentation-only push
therefore publishes a new version too. PyPI never accepts the same version two times.

**`mypy` is not a CI gate.** Only the local `pre-commit` config runs it. That config runs
`ruff-check`, then `ruff-format`, then `mypy`.

**`ruff` checks only `firestarter/` and `tests/`.** No CI step lints, formats, type-checks or
measures coverage of `tools/`. The tests do run `tools/build_db.py` and its JSON inputs. The `ruff`
rule selection is `E`, `F`, `I` and `UP`, and the config ignores `E501`. A `# noqa` code outside that
selection has no effect.

**`mypy` is strict on eleven modules.** An override in `pyproject.toml` sets
`disallow_untyped_defs` and `check_untyped_defs` for `main`, `cli_handlers`, `chip_resolver`,
`frame_parser`, `codec`, `address_parser`, `exceptions`, `serial_comm`, `sdp_honesty`, `log_capture`
and `compare`. After a refactor, read the override list in `pyproject.toml`. Do not trust this list.

## Architecture

### Data Flow

```
infoic.xml → build_db.py → chip_database.json
                                        ↓
firestarter <chip> write/read/erase
     ↓
EpromDatabase.get_eprom(name)       # find the chip
     ↓
database._map_data()                # get algorithm, vpp_mv, pinout
     ↓
database.convert_to_programmer()    # change DIP pins to bus config
     ↓
eprom_operations.py                 # make the JSON command
     ↓
serial_comm.py                      # send it over serial, read the response
```

### Key Files

- `firestarter/data/chip_database.json` — the generated chip database. **Do not edit it.**
- `firestarter/data/pinouts.json` — maps physical DIP pins to RURP bus lines. It has one entry for
  each pinout key that the database uses.
- `firestarter/database.py` — `EpromDatabase`. It finds chips, changes pins and makes commands. It
  has the `skip_local_override` seam.
- `firestarter/eprom_operations.py` — the high-level operations: read, write, erase, verify and
  blank check.
- `firestarter/compare.py` — the one host-side comparison engine. `verify_eprom`, `chip_test` and
  the write guard all use it.
- `firestarter/serial_comm.py` — the serial protocol, as an INIT/MAIN/END state machine.
- `firestarter/frame_parser.py` — CRC8, `cobs_encode`, `cobs_decode`, `_decode_param`,
  `MAGIC_PREAMBLE`, and the `Response` and `LogMessage` types. You can test it without serial I/O.
- `firestarter/codec.py` — `format_message`, `decode_id_frame` and the revision-silkscreen text.
- `firestarter/address_parser.py` — parses hex and decimal addresses and sizes.
- `firestarter/chip_resolver.py` — `resolve_chip(name, db) -> programmer_config`.
- `firestarter/cli_handlers.py` — the Click command handlers, the `dev` command group, the
  `@map_typed_errors` decorator and the `AppContext` dataclass.
- `firestarter/py32_dfu.py` — the USB DFU firmware-install backend for the `py32f071` board. It
  supports DfuSe and plain DFU 1.1. It loads Intel-HEX and raw binary files. It uses `pyusb` through
  the optional `[py32]` extra. `firmware.py::flash_method()` sends that board here, not to avrdude.
  **No test on real silicon covers it.**
- `firestarter/channel.py` — the release-channel gate. Refer to the next section.
- `firestarter/exceptions.py` — the typed exceptions. The root classes are `SerialError`,
  `EpromOperationError`, `HardwareOperationError`, `FirmwareOperationError` and
  `ChipNotFoundError`. Read the file for the subclasses.
- `firestarter/main.py` — the CLI entry point. It exports `cli` from `cli_handlers`.
- `tools/build_db.py` — the database pipeline. It downloads the upstream `infoic.xml` and writes
  JSON.

### Release-Channel Gate

`channel.py` decides what a build shows. `is_prerelease_build()` returns true for a PEP 440
pre-release, which is a build from `beta`. Two lists control the gate:

- `BETA_ONLY_BOARDS` — boards that only a pre-release build shows. Two places enforce it.
  `cli_handlers.py` makes `_BOARD_CHOICES` at import, so `fw --help` on a stable build never shows a
  beta-only board. `firmware.py` refuses in `_install_with_dfu()` and `probe_dfu()` for library
  callers. To release a board to stable, delete it from `BETA_ONLY_BOARDS`.
- `BETA_ONLY_DEV_COMMANDS` — `dev` subcommands that a stable build does not register.

**Do not add an environment-variable gate. An environment variable fails open.** The one exception
is `FIRESTARTER_DEV_TOOLS`. It enables the gated `dev` subcommands only when its value is exactly
`1`, so it fails closed.

### Wire Protocol

The host sends each command as COBS-framed JSON with a CRC8, at 250000 baud
(`serial_comm.py::send_json_command`). The `algorithm` field holds the upstream `protocol_id`
integer. The firmware dispatches on it.

Example write command:

```json
{
  "cmd": 2,
  "algorithm": 7,
  "memory-size": 65536,
  "vpp_mv": 12000,
  "pulse-delay": 0,
  "pin-count": 28,
  "chip-id": 42495,
  "flags": 2,
  "bus-config": { ... }
}
```

The firmware sends INIT, MAIN, END and status messages as catalog message ID frames. It still sends
`OK` and `DATA` as text lines. The host parser accepts the text prefixes `OK`, `DATA`, `ERROR`,
`WARN`, `INFO` and `DEBUG` (`serial_comm.py::EXPECTED_PREFIXES`).

The host reads the firmware buffer size from the `MSG_OK_READY` ack, and sizes its data chunks from
that value. If the ack has no size, the host uses 512.

### Database Pipeline

`tools/build_db.py` **downloads** `infoic.xml`, the minipro chip database XML. `MINIPRO_XML_URL`
pins the download to minipro commit `a8efaedc`, so the build is reproducible. This repository has
no copy of the XML. The script writes `firestarter/data/chip_database.json`.

Two files add to the generated data. Do not change a generated row in any other place:

- `tools/extra_chips.json` — adds a real chip that `infoic.xml` does not list.
- `tools/datasheet_overrides.json` — changes a generated field value. Each entry records the old
  value, the new value and a datasheet citation.

Key fields per chip entry:

- `algorithm` — the upstream `protocol_id` integer. The firmware dispatches on it.
- `vpp_mv` — the VPP voltage in millivolts, decoded from the `voltages` field.
- `pinout` — the DIP pinout key. The shipped database uses 16 keys: `DIP24_2532`, `DIP24_2716`,
  `DIP24_2732`, `DIP24_2816`, `DIP24_6116`, `DIP28_27256`, `DIP28_27512`, `DIP28_2764`,
  `DIP28_28C64`, `DIP28_28C256`, `DIP28_JEDEC_SRAM_8K`, `DIP32_27C020`, `DIP32_27C801`,
  `DIP32_28C512_EEPROM`, `DIP32_SST39SF040` and `DIP32_STD`.

`part_number` can hold more than one name in one comma-separated string, for example
`W27C512,W27E512`. An exact-string match on one part number therefore misses rows. Split the field
before you compare it.

The pipeline skips a chip that has an unknown `protocol_id`, and shows a warning. `KNOWN_PROTOCOLS`
in `build_db.py` holds `0x05`, `0x06`, `0x07`, `0x08`, `0x0B`, `0x0D`, `0x0E`, `0x10`, `0x27`,
`0x28`, `0x29` and `0x34`. The pipeline writes the `0x34` chip as protocol-not-implemented.

### 5V-EEPROM promotion to `0x0D`

`classify()` in `tools/build_db.py` promotes 5V-EEPROM pinout clusters to `algorithm 0x0D`. The
firmware then sends them to `configure_eeprom28c`, which uses 5V VCC and no VPP regulator. Without
the promotion they go to `configure_eprom`. Two arms promote:

1. `pinout_key` is `DIP24_2816`. Promote for all protocols.
2. `proto_id` is `0x07`, `0x08` or `0x0B`, **and** one of these is true:
   - `pinout_key` is `DIP28_28C64` or `DIP28_28C256`.
   - `pinout_key` is `DIP28_2764` and `flags & 0x10` is set.

A real 5V flash chip on the same DIP28 layout keeps its flash algorithm. A later arm handles it.
**Do not make either arm wider.**

**Why the promotion exists.** On the `DIP28_2764` pinout, socket pin 1 connects to the VPP
regulator output. `configure_eprom` sets `CTRL_VPP_P1_ENABLE` at 12V on each write pulse. On the
affected 28C-family 5V EEPROMs, physical pin 1 is the A14 address line, not VPP. 12V on pin 1
damages the part.

Rows on `DIP32_28C512_EEPROM` have the upstream `protocol_id` `0x0D`. `classify()` keeps that
value, so these rows need no promotion.

Seven chips stay on `0x07` and `configure_eprom`, and need 12V VPP: W27C512, SST27SF512,
SST27VF512, W27C257, W27E257, SST27SF256 and SST27VF256. They are electrically-erasable EEPROMs, not
UV-EPROMs. Their `electrical.type` is `EEPROM` and `flags & 0x10` is set. Refer to commit
`cca7d62`. They use `DIP28_27512` or `DIP28_27256`. Both pinouts have a real VPP pin, so 12V on
that pin is correct.

### Constants

`firestarter/constants.py` duplicates values from the firmware. Change both sides together.

| Block in `constants.py` | Firmware source of truth | Keep the same |
|---|---|---|
| Command codes, control flags, `CMD_FRAME_MAX` | `firestarter_fw/include/firestarter.h` | values |
| `CTRL_*` control-register bits | `firestarter_fw/include/rurp_pinout.h` | names and hex values |
| `REVISION_*` hardware revisions | `firestarter_fw/include/rurp_shield.h` | names and byte values |
| `JSON_KEY_*` command keys | `firestarter_fw/src/json_parser.c` | key strings |

The project reserves two revision bytes. `0xFF` shows that no EEPROM override is present. `0xFE`
(`REVISION_UNKNOWN`) shows that the ADC band-gap check found no band.

The project reserves control flag `0x08`. Never reuse it. Shipped hosts still send it.
