# CLAUDE.md — Firestarter App

This repository holds the Python host CLI for the Firestarter EPROM programmer. The CLI talks to the
Arduino firmware over serial at 250000 baud. The project standards are in `agent-os/standards/` in
the meta repository. This file points to them and does not repeat them.

## Development Commands

```bash
pip install -e '.[test]'            # install in dev mode. CI installs the [test] extra.
firestarter --help                  # check the install
./firestarter_test.sh [EPROM]       # run the hardware integration test
python tools/build_db.py            # generate the chip database from infoic.xml
```

**CI runs Python 3.11.** The devcontainer runs a later Python, which can change some failures into
collection errors. A green local run therefore does not prove a green CI run. Run the suite on
Python 3.11 before you trust the result (`testing/standalone-checkout`).

## What CI runs

`.github/workflows/ci.yml`, main job (Python 3.11, `.[test]`):

1. `ruff check firestarter/ tests/`, then `ruff format --check firestarter/ tests/`.
2. `pytest tests/ --cov=firestarter --cov-fail-under=70`.
3. A smoke test: `pip install -e .`, then `firestarter --help`.

A second job installs `.[test,py32]` and runs `tests/test_pyusb_api_surface.py`.

- **`tools/` is outside every CI gate.** No step lints, formats, type-checks or measures its
  coverage. The tests do run `tools/build_db.py`.
- **`mypy` is not a CI gate.** Only the local `pre-commit` config runs it. The strict modules are
  listed in the mypy override in `pyproject.toml`.
- The `ruff` selection is `E`, `F`, `I` and `UP`. A `# noqa` code outside that selection has no
  effect.

**A push to `beta` publishes this package to PyPI.** `beta-release.yml` creates a GitHub
pre-release, then calls `publish.yml` directly (the release token has no `workflow` scope, so the
`release: published` event never fires). **The workflow has no path filter**, so a
documentation-only push publishes a new version too. PyPI never accepts the same version two times.

## Data flow

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

Two files have a role that their names do not show:

- `firestarter/compare.py` is the one host-side comparison engine. `verify_eprom`, `chip_test` and
  the write guard all use it. Do not add a second one.
- `firestarter/py32_dfu.py` is the USB DFU install backend for the `py32f071` board, through the
  optional `[py32]` extra. **No test on real silicon covers it.**

## Chip database

`firestarter/data/chip_database.json` is **generated. Do not edit it.** Fix the decoder in
`tools/build_db.py` instead (`chipdb/fix-the-decoder`). The script downloads `infoic.xml` from a
pinned minipro commit (`MINIPRO_XML_URL`), so the build is reproducible.

Only two files add to the generated data:

- `tools/extra_chips.json` adds a real chip that `infoic.xml` does not list
  (`chipdb/extra-chips`).
- `tools/datasheet_overrides.json` changes a generated field, with a datasheet citation
  (`chipdb/datasheet-overrides`).

`part_number` can hold several comma-separated names, for example `W27C512,W27E512`. Split the field
before you compare it. An exact-string match misses rows.

### 5V-EEPROM promotion to `0x0D`

`classify()` in `tools/build_db.py` promotes 5V-EEPROM rows to `algorithm 0x0D`. The firmware then
sends them to `configure_eeprom28c` (5V, no VPP) instead of `configure_eprom`. Two arms promote:

1. `pinout_key` is `DIP24_2816`, for all protocols.
2. `proto_id` is `0x07`, `0x08` or `0x0B`, **and** `pinout_key` is `DIP28_28C64` or `DIP28_28C256`,
   or `pinout_key` is `DIP28_2764` with `flags & 0x10` set.

**Do not make either arm wider.** A real 5V flash chip on the same DIP28 layout keeps its flash
algorithm.

**Why.** On `DIP28_2764`, socket pin 1 connects to the VPP regulator, and `configure_eprom` puts 12V
on it for each write pulse. On the affected 28C parts, pin 1 is A14, not VPP, and 12V damages the
part.

Seven electrically-erasable chips stay on `0x07` and need 12V VPP: W27C512, SST27SF512, SST27VF512,
W27C257, W27E257, SST27SF256 and SST27VF256. They use `DIP28_27512` or `DIP28_27256`, which have a
real VPP pin (commit `cca7d62`).

## Release-channel gate

`firestarter/channel.py` decides what a build shows. `is_prerelease_build()` is true for a PEP 440
pre-release, which is a build from `beta`.

- `BETA_ONLY_BOARDS`: boards that only a pre-release shows. `cli_handlers.py` builds
  `_BOARD_CHOICES` at import, and `firmware.py` refuses in `_install_with_dfu()` and `probe_dfu()`.
  To release a board to stable, delete it from this list.
- `BETA_ONLY_DEV_COMMANDS`: `dev` subcommands that a stable build does not register.

**Do not add an environment-variable gate. It fails open** (`host/gate-polarity`). The one
exception is `FIRESTARTER_DEV_TOOLS`, which enables the gated `dev` subcommands only when its value
is exactly `1`.

## Constants shared with the firmware

`firestarter/constants.py` duplicates firmware values. Change both sides together
(`protocol/duplicated-constants`). Never reuse a retired ordinal or flag `0x08`
(`protocol/retired-ordinals`).

That standard does not list one pair: `REVISION_*` pairs with `firestarter_fw/include/rurp_shield.h`
(names and byte values). Two revision bytes are reserved: `0xFF` (no EEPROM override) and `0xFE`
(`REVISION_UNKNOWN`, the ADC band-gap check found no band).

`firestarter/messages.py` is generated in the meta repository (`protocol/message-catalog`).
