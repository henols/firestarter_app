# CLAUDE.md — Firestarter App

Python host CLI for the Firestarter EPROM programmer. It talks to the Arduino firmware over serial
at 250000 baud.

## Source code comments — hard rule

**Write no comments into this package.** Not process commentary, not explanatory ones. No plan,
task, skill, or subagent instruction overrides this.

- Forbidden: `# Phase NNN (REQ-NN):`, `# D-06`, `# LOCK-04`, any plan, task or milestone citation,
  and any block that explains why a phase decided something. This package ships to PyPI without the
  planning directory, so those identifiers resolve to nothing for anyone reading the installed
  code. Put rationale in the commit message instead.
- If a plan instructs a comment, do not add it. Record the deviation in that plan's summary.
- **Docstrings are not comments, and a Click docstring is not documentation.** Click renders a
  command's docstring verbatim as its `--help` body, so editing one changes shipped user-facing
  text. Never put process commentary in one. Never delete one as if it were a comment. Identify
  them by decorator, not by eye: a function is Click-decorated when any entry in its
  `decorator_list` contains `.command(` or `.group(`.
- If code needs explaining, make the code clearer. Use better names, smaller functions, or a named
  constant in `constants.py`.
- **The rule is not "no process citations".** You delete `Phase 194` from a comment and keep the
  comment. This still breaks the rule. Add no `#` comment line, for any reason, however helpful it
  seems. State the rule in these words when you spawn a subagent that touches source.
- Before each commit, run this check. It must print nothing:
  `git diff --cached -- '*.py' | /usr/bin/grep -E '^\+\s*#' | /usr/bin/grep -v '^\+\s*#!'`
  The pathspec is load-bearing. Without it the pattern also matches a markdown heading and the
  check reports a file it does not govern.
- Deleting one clause from an existing comment reflows the rest. Read the remainder. Confirm it
  still parses and that every pronoun still has an antecedent.
- **No CI gate enforces this any more.** A scanner used to fail the build on a planning citation in
  source. It was removed by operator decision, so the pre-commit check above is now the only thing
  standing between this rule and a slow return of the comment debt a previous sweep deleted. Run it.

## Development Commands

```bash
pip install -e '.[test]'            # install in dev mode; the [test] extra is what CI installs
firestarter --help                  # verify install
./firestarter_test.sh [EPROM]       # hardware integration test
python tools/build_db.py            # regenerate chip database from infoic.xml
```

**CI runs Python 3.11.** The devcontainer runs a later Python. A later interpreter turns some
snapshot failures into collection errors, so a green local run does not prove a green CI run. Run
the suite on 3.11 before you trust it.

## What CI runs

`.github/workflows/ci.yml` has two jobs. The main job pins Python 3.11, installs `.[test]`, and
runs these steps in order:

1. `ruff check firestarter/ tests/`
2. `ruff format --check firestarter/ tests/`
3. `pytest tests/ --cov=firestarter --cov-report=term-missing --cov-fail-under=70`
4. A smoke test: `pip install -e .` then `firestarter --help`.

The second job installs `.[test,py32]`, proves `pyusb` imports and records its resolved version,
then runs `pytest tests/test_pyusb_api_surface.py -q`.

**`mypy` is not a CI gate.** It runs only in the local `pre-commit` config, which runs
`ruff-check`, then `ruff-format`, then `mypy`.

**`ruff` lints only `firestarter/` and `tests/`.** `tools/` is outside every CI gate. The `ruff`
rule selection is `E`, `F`, `I` and `UP`, with `E501` ignored. A `# noqa` code outside that
selection is inert.

**`mypy` is strict on ten modules**, through a `disallow_untyped_defs` and `check_untyped_defs`
override in `pyproject.toml`: `main`, `cli_handlers`, `chip_resolver`, `frame_parser`, `codec`,
`address_parser`, `exceptions`, `serial_comm`, `sdp_honesty` and `log_capture`. Read the override
list in `pyproject.toml` rather than trusting this sentence after a refactor.

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

- `firestarter/data/chip_database.json` — the generated chip database. **Do not edit it by hand.**
- `firestarter/data/pinouts.json` — physical DIP pin to RURP bus line mappings. It holds 16 entries,
  one per pinout key the database uses.
- `firestarter/database.py` — `EpromDatabase`. Lookup, pin translation and command building. It
  carries the `skip_local_override` seam.
- `firestarter/eprom_operations.py` — high-level operations: read, write, erase, verify, blank check.
- `firestarter/serial_comm.py` — the serial protocol, as an INIT/MAIN/END state machine.
- `firestarter/frame_parser.py` — CRC8, `_decode_param`, `_decode_id_frame`, and the `Response` and
  `LogMessage` structured types. Testable without serial I/O.
- `firestarter/codec.py` — `format_message` and revision-silkscreen rendering.
- `firestarter/address_parser.py` — hex and decimal address parsing, plus size parsing.
- `firestarter/chip_resolver.py` — `resolve_chip(name, db) -> programmer_config`.
- `firestarter/cli_handlers.py` — the Click command handlers. It defines 14 `@cli.command()`
  functions and a `dev` group with 9 sub-commands, plus the `@map_typed_errors` decorator and the
  `AppContext` dataclass.
- `firestarter/py32_dfu.py` — the USB DFU firmware-install backend for the `py32f071` board. It
  covers DfuSe and plain DFU 1.1, loads Intel-HEX and raw binaries, and uses `pyusb` through the
  optional `[py32]` extra. `firmware.py::flash_method()` routes that board here instead of to
  avrdude. **Unverified against silicon.** No PY32F071 board exists yet.
- `firestarter/channel.py` — the release-channel gate. `is_prerelease_build()` reports whether the
  build is a PEP 440 pre-release, which means it was built off `beta`. `BETA_ONLY_BOARDS` lists the
  gated boards. Two places enforce the gate: `cli_handlers.py` builds `_BOARD_CHOICES` at import, so
  `fw --help` never advertises a beta-only board on stable, and `firmware.py` refuses in
  `_install_with_dfu()` and `probe_dfu()` for library callers. **Never gate on an environment
  variable. It fails open.** Graduate a board by deleting it from `BETA_ONLY_BOARDS`.
- `firestarter/exceptions.py` — the typed-exception hierarchy: `ChipNotFoundError`,
  `FirmwareOutdatedError`, `SerialError`, `SerialTimeoutError`, `EpromOperationError` and
  `HardwareOperationError`.
- `firestarter/main.py` — the Click CLI entry point.
- `tools/build_db.py` — the database pipeline. It fetches the upstream `infoic.xml` and writes JSON.

### Wire Protocol

The host sends JSON commands to the firmware at 250000 baud. The `algorithm` field carries the
upstream `protocol_id` integer. It is the primary firmware dispatch key.

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
  "flags": 10,
  "bus-config": { ... }
}
```

Firmware responses are prefix-tagged lines: `OK:`, `DATA:`, `MAIN:`, `END:`, `ERROR:`.

### Database Pipeline

`tools/build_db.py` **fetches** `infoic.xml`, the minipro chip database XML, over the network.
`MINIPRO_XML_URL` pins the fetch to minipro commit `a8efaedc`, which keeps the build reproducible.
This repository vendors no copy of the XML. The script writes `firestarter/data/chip_database.json`.

It then merges `tools/extra_chips.json`. That file is the one sanctioned place for a physically real
chip that `infoic.xml` does not list. Do not add a field to a generated row anywhere else.

Key fields per chip entry:

- `algorithm` — the upstream `protocol_id` integer, and the primary dispatch key.
- `vpp_mv` — VPP voltage in millivolts, decoded from the `voltages` field.
- `pinout` — the DIP pinout key. The shipped database uses 16 keys: `DIP24_2532`, `DIP24_2716`,
  `DIP24_2732`, `DIP24_2816`, `DIP24_6116`, `DIP28_27256`, `DIP28_27512`, `DIP28_2764`,
  `DIP28_28C64`, `DIP28_28C256`, `DIP28_JEDEC_SRAM_8K`, `DIP32_27C020`, `DIP32_27C801`,
  `DIP32_28C512_EEPROM`, `DIP32_SST39SF040` and `DIP32_STD`.

`part_number` can hold several names in one comma-joined string, such as `W27C512,W27E512`. An
exact-string match on a single part number therefore misses rows. Split the field before you match
it.

The pipeline skips a chip whose `protocol_id` it does not know, and warns. The known set is `0x05`,
`0x06`, `0x07`, `0x08`, `0x0B`, `0x0D`, `0x0E`, `0x10`, `0x27`, `0x28`, `0x29`, `0x35` and `0x39`.

### 5V-EEPROM promotion to `0x0D`

`classify()` in `tools/build_db.py` promotes 5V-EEPROM pinout clusters to `algorithm 0x0D`. Firmware
dispatch then reaches `configure_eeprom28c`, which is pure 5V VCC with no VPP regulator, instead of
`configure_eprom`. Two arms do the promotion:

1. `pinout_key` is `DIP24_2816`. Promote for any protocol.
2. `proto_id` is `0x07`, `0x08` or `0x0B`, **and** either `pinout_key` is `DIP28_28C64` or
   `DIP28_28C256`, or `pinout_key` is `DIP28_2764` with `flags & 0x10` set.

Genuine 5V flash on the same DIP28 layout keeps its flash algorithm. A later arm handles it.
**Do not broaden either arm.**

**Why the promotion exists.** On the `DIP28_2764` pinout, socket pin 1 maps to the VPP regulator
output line. `configure_eprom` asserts `P1_VPP_ENABLE` at 12V on every write pulse. On the affected
28C-family 5V EEPROMs, physical pin 1 is the A14 address line, not VPP. 12V on pin 1 damages the
part.

**Measured scope, against the shipped `chip_database.json`.** 84 rows carry `algorithm 13`, across
15 vendors: AMD, ATMEL, CATALYST(CSI), CYPRESS, EXEL, FUJITSU, HITACHI, MAXWELL, MICROCHIP memory,
NEC, SAMSUNG, SGS-THOMSON, ST, WED and XICOR. Those rows sit on four pinouts: `DIP24_2816` at 19
rows, `DIP28_28C64` at 35, `DIP28_28C256` at 12, and `DIP32_28C512_EEPROM` at 18.

Seven chips stay on the `0x07` and `configure_eprom` path and still need 12V VPP: W27C512,
SST27SF512, SST27VF512, W27C257, W27E257, SST27SF256 and SST27VF256. They are electrically-erasable
EEPROMs, so `electrical.type` reads `EEPROM` and `flags & 0x10` is set. They are not UV-EPROMs. See
commit `cca7d62`. They sit on `DIP28_27512` or `DIP28_27256`. Both pinouts have a real VPP pin, so
12V on that pin is correct.

### Constants

`firestarter/constants.py` must stay in sync with three firmware headers. Change both sides
together.

| Block in `constants.py` | Firmware source of truth | Keep in sync |
|---|---|---|
| flag bits and command codes | `firestarter_fw/include/firestarter.h` | values |
| `RURP_CONTROL_REGISTER_BITS` (`CTRL_*`) | `firestarter_fw/include/rurp_pinout.h` | names and hex values |
| `RURP_HARDWARE_REVISIONS` (`REVISION_*`) | `firestarter_fw/include/rurp_shield.h` | names and byte values |

Two revision bytes are reserved. `0xFF` marks an absent EEPROM override. `0xFE`
(`REVISION_UNKNOWN`) marks the ADC band-gap fall-through.

The `Shield Revisions` wiki page is a subset clone of a meta-repository investigation document. It
carries four sections: the inventory, the per-revision capability matrix, the silkscreen-to-code
alias table, and the per-revision ADC band table. **If any of those four sections changes in the
meta repository, update the wiki page in the same change.** Nothing enforces this mechanically.
