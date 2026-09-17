# CLAUDE.md — Firestarter App

Python host CLI for the Firestarter EPROM programmer. Communicates with the Arduino firmware over serial at 250000 baud.

## Source code comments — hard rule

**Write no comments into this package.** Not GSD process commentary, not explanatory ones. This is
not overridable by a plan, task, skill, or subagent instruction.

- Forbidden: `# Phase NNN (REQ-NN):`, `# D-06`, `# LOCK-04`, plan/task/milestone citations, and
  blocks explaining why a phase decided something. This package ships to PyPI without
  `.planning/` — which lives in a different repository — so those identifiers resolve to nothing
  for anyone reading the installed code, and phase numbers get renumbered at milestone close.
- **Where rationale goes instead:** the phase `SUMMARY.md` in the meta repo, `REQUIREMENTS.md`
  traceability, or the commit message. Nothing in GSD asks for it in source.
- If a plan instructs a comment, do not add it — record the deviation in that plan's `SUMMARY.md`.
- **Docstrings are not comments, and Click docstrings are not documentation** — they are the
  user-facing `--help` text. Never put process commentary in one, and never delete one as if it
  were a comment.
- If code needs explaining, make the code clearer: better names, smaller functions, a named
  constant in `constants.py`.
- **The rule is not "no GSD citations".** You delete `Phase 194` from a comment and keep the
  comment. This still breaks the rule. Add no `#` comment line, for any reason, however helpful
  it seems. State the rule in these words when you spawn a subagent that touches source.
- Before each commit, run this check. It must print nothing:
  `git diff --cached | /usr/bin/grep -E '^\+\s*#' | /usr/bin/grep -v '^\+\s*#!'`
- Deleting one clause from an existing comment reflows the rest. Read the remainder. Confirm it
  still parses and that every pronoun still has an antecedent.

## Development Commands

```bash
pip install -e '.[test]'            # install in dev mode; the [test] extra is what CI installs
firestarter --help                  # verify install
./firestarter_test.sh [EPROM]       # hardware integration test
python tools/build_db.py            # regenerate chip database from infoic.xml
```

**CI runs Python 3.11.** The devcontainer runs a later Python. A later interpreter turns some
snapshot failures into collection errors, so a green local run does not prove a green CI run.
Run the suite on 3.11 before you trust it.

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
- `firestarter/database.py` — `EpromDatabase` (`skip_local_override` seam, see Phase 36 D-06): lookup, pin translation, command building
- `firestarter/eprom_operations.py` — high-level operations (read, write, erase, verify, blank check)
- `firestarter/serial_comm.py` — serial protocol implementation (INIT/MAIN/END state machine)
- `firestarter/frame_parser.py` — CRC8 + `_decode_param` + `_decode_id_frame` + `Response`/`LogMessage` structured types (Phase 38 STRUCT-01; testable without serial I/O)
- `firestarter/codec.py` — `format_message` + revision-silkscreen rendering (Phase 38 STRUCT-02)
- `firestarter/address_parser.py` — hex/decimal address + size parsing (Phase 38 STRUCT-03)
- `firestarter/chip_resolver.py` — `resolve_chip(name, db) -> programmer_config` (Phase 39 DATA-01; replaces 9× chip-lookup copy-paste)
- `firestarter/cli_handlers.py` — Click command handlers (14 `@cli.command()` + `dev` group with 4 sub-commands) + `@map_typed_errors` decorator + `AppContext` dataclass (Phase 41 CLI-01..04 + Phase 42 ERR-01)
- `firestarter/py32_dfu.py` — USB DFU firmware-install backend for the `py32f071` board (DfuSe + plain DFU 1.1, Intel-HEX/raw-bin loader, pyusb via the optional `[py32]` extra). `firmware.py::flash_method()` routes boards here instead of avrdude. **Unverified against silicon** — no PY32F071 board exists yet
- `firestarter/channel.py` — release-channel gate. `is_prerelease_build()` (PEP 440 pre-release ⇒ built off `beta`) plus `BETA_ONLY_BOARDS`. Beta-only features are gated twice: `cli_handlers.py` builds `_BOARD_CHOICES` at import so `fw --help` never advertises them on stable, and `firmware.py` refuses in `_install_with_dfu()`/`probe_dfu()` for library callers. **Never gate on an env var** — it fails open. Graduate a board by deleting it from `BETA_ONLY_BOARDS`
- `firestarter/exceptions.py` — consolidated typed-exception hierarchy: `ChipNotFoundError`, `FirmwareOutdatedError`, `SerialError`, `SerialTimeoutError`, `EpromOperationError`, `HardwareOperationError` (Phase 38 STRUCT-04)
- `firestarter/main.py` — Click CLI entry point
- `tools/build_db.py` — database pipeline: parses the upstream `infoic.xml`, outputs JSON

### Wire Protocol

JSON commands sent to firmware at 250000 baud. The `algorithm` field carries the upstream `protocol_id` integer and is the primary firmware dispatch key.

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

`tools/build_db.py` **fetches** `infoic.xml` (the minipro chip database XML) over the network.
`MINIPRO_XML_URL` pins the fetch to minipro commit `a8efaedc`, which keeps the build reproducible.
This repo vendors no copy of the XML. The script writes `firestarter/data/chip_database.json`.

It then merges `tools/extra_chips.json`. That file is the one sanctioned place for a physically
real chip that `infoic.xml` does not list. Do not add a field to a generated row anywhere else.

Key fields per chip entry:
- `algorithm` — upstream `protocol_id` integer (primary dispatch key)
- `vpp_mv` — VPP voltage in millivolts (decoded from `voltages` field)
- `pinout` — DIP pinout key. The shipped database uses 16 keys: `DIP24_2532`, `DIP24_2716`,
  `DIP24_2732`, `DIP24_2816`, `DIP24_6116`, `DIP28_27256`, `DIP28_27512`, `DIP28_2764`,
  `DIP28_28C64`, `DIP28_28C256`, `DIP28_JEDEC_SRAM_8K`, `DIP32_27C020`, `DIP32_27C801`,
  `DIP32_28C512_EEPROM`, `DIP32_SST39SF040`, `DIP32_STD`.

`part_number` can hold several names in one comma-joined string, such as `W27C512,W27E512`. An
exact-string match on a single part number therefore misses rows. Split the field before you
match it.

Known protocols (chips with unknown protocol_id are skipped with a warning):
`0x05, 0x06, 0x07, 0x08, 0x0B, 0x0D, 0x0E, 0x10, 0x27, 0x28, 0x29, 0x35, 0x39`

### 5V-EEPROM promotion to `0x0D` (the rule the audit called WARNING-5)

`classify()` in `tools/build_db.py` promotes 5V-EEPROM pinout clusters to `algorithm 0x0D`.
Firmware dispatch then reaches `configure_eeprom28c` (pure 5V VCC, no VPP regulator) instead of
`configure_eprom`. Two arms do the promotion:

1. `pinout_key` is `DIP24_2816`. Promote for any protocol.
2. `proto_id` is `0x07`, `0x08` or `0x0B`, **and** either `pinout_key` is `DIP28_28C64` or
   `DIP28_28C256`, or `pinout_key` is `DIP28_2764` with `flags & 0x10` set.

Genuine 5V flash on the same DIP28 layout keeps its flash algorithm. A later arm handles it.
**Do not broaden either arm.**

**Why the promotion exists.** On the `DIP28_2764` pinout, socket pin 1 maps to the VPP regulator
output line. `configure_eprom` asserts `P1_VPP_ENABLE` (12V) on every write pulse. On the affected
28C-family 5V EEPROMs, physical pin 1 is the A14 address line, not VPP. 12V on pin 1 damages the
part.

**Measured scope, against the shipped `chip_database.json`:** 84 rows carry `algorithm 13`, across
15 vendors — AMD, ATMEL, CATALYST(CSI), CYPRESS, EXEL, FUJITSU, HITACHI, MAXWELL, MICROCHIP memory,
NEC, SAMSUNG, SGS-THOMSON, ST, WED and XICOR. Those rows sit on four pinouts: `DIP24_2816` (19
rows), `DIP28_28C64` (35), `DIP28_28C256` (12) and `DIP32_28C512_EEPROM` (18).

Seven chips stay on the `0x07` and `configure_eprom` path. They still need 12V VPP: W27C512,
SST27SF512, SST27VF512, W27C257, W27E257, SST27SF256 and SST27VF256. They are
electrically-erasable EEPROMs (`electrical.type` is `EEPROM`, `flags & 0x10` set), not UV-EPROMs.
See `cca7d62`. They sit on `DIP28_27512` or `DIP28_27256`. Both pinouts have a real VPP pin, so 12V
on that pin is correct.

Origin record: `.planning/milestones/v1.0-MILESTONE-AUDIT.md`, section `WARNING-5`. The phase
folder that closed it was archived at milestone close and no longer exists at its original path.

### Constants

`firestarter/constants.py` must stay in sync with `firestarter_fw/include/firestarter.h` in the firmware sub-repo. Both define the same flag bit values and command codes. Additionally, the `RURP_CONTROL_REGISTER_BITS` block in `constants.py` (CTRL_* names) mirrors the control-register-bit declarations in `firestarter_fw/include/rurp_pinout.h` (Phase 33 / v1.7 — silkscreen-label code-alias migration). Keep CTRL_* names + hex values in sync with the firmware header. Additionally, the `RURP_HARDWARE_REVISIONS` block in `constants.py` (REVISION_* names) mirrors the hardware-revision enum declarations in `firestarter_fw/include/rurp_shield.h` (Phase 34 / v1.7 — shield-version-detect design + firmware plumbing). Keep REVISION_* names + byte values in sync with the firmware enum; `0xFF` is reserved as the EEPROM-override-absent sentinel and `0xFE` (`REVISION_UNKNOWN`) is reserved for the ADC-band-gap fall-through. Additionally, the wiki page `Shield Revisions` is a subset clone of meta-repo `.planning/milestones/v1.7-SHIELD-REVS.md` sections §1 (inventory) / §6 (per-rev capability matrix) / §7 (silkscreen → code alias table) / §9 (per-rev ADC band table) (Phase 35 / v1.7 — close); if any of those four sections change in the meta-repo, update the wiki page in lockstep.

**Tooling gate (v1.8):** `ruff check` + `ruff format --check` + `pytest --cov-fail-under=70` — all enforced by `.github/workflows/ci.yml` on every PR. `mypy` (strict on 8 modules per Phase 42 D-06: `main.py`, `cli_handlers.py`, `chip_resolver.py`, `frame_parser.py`, `codec.py`, `address_parser.py`, `exceptions.py`, `serial_comm.py`) is wired in the local `pre-commit` config only and is **not** a CI gate; `pre-commit` runs `ruff-check` → `ruff-format` → `mypy` locally.
