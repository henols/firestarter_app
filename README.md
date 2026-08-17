
<p align="left"><img src="https://raw.githubusercontent.com/henols/firestarter_app/refs/heads/main/images/firestarter_logo.png" alt="Firestarter EPROM Programmer" width="200"></p>

----

# Firestarter

[![PyPI version](https://badge.fury.io/py/firestarter.svg)](https://badge.fury.io/py/firestarter)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[![ko-fi](https://raw.githubusercontent.com/henols/firestarter_app/refs/heads/main/images/ko-fi.png)](https://ko-fi.com/E1E21I2WWW)

---

Firestarter is an application for the [Relatively-Universal-ROM-Programmer](https://github.com/AndersBNielsen/Relatively-Universal-ROM-Programmer) RURP Arduino shield ([Get one here](https://www.imania.dk/samlesaet-hobbyelektronik-og-ic-er-relatively-universal-rom-programmer.htm)).

The Firestarter Firmware can be found here [Firestarter](https://github.com/henols/firestarter).

Firestarter in action [watch the video](https://youtu.be/JDHOKbyNnrE?si=0_iXKPZZwyyNGUTZ).

Anders S Nielsen creator of the Relatively-Universal-ROM-Programmer talks about Firestarter [watch the video](https://youtu.be/SZQ50XZlk5o?si=IKOqQUeG4Rms1cUs).

Support and discussions forum at [Discord](https://discord.com/invite/kmhbxAjQc3).

## Table of Content
- [Installation](#Installation)
  - [Installing the Firestarter Python Program](#installing-the-firestarter-python-program)
  - [Beta / Pre-release Channel](#beta--pre-release-channel)
  - [Installing the Firmware on the Arduino](#installing-the-firmware-on-the-arduino)
- [Usage](#usage)
  - [General Usage](#general-usage)
  - [Commands](#commands)
    - [Read](#read)
    - [Write](#write)
    - [Blank](#blank)
    - [Erase](#erase)
    - [Id](#id)
    - [Info](#info)
    - [Vpp](#vpp)
    - [Vpe](#vpe)
    - [Hw](#hardware)
    - [Fw](#firmware)
    - [Config](#configuration)

- [Examples](#examples)
  - [Read](#read-1)
  - [Write](#write-1)
  - [Info](#info-1)
- [Override and adding to the database](#override-and-adding-to-the-database)
  - [Eprom Configuration](#eprom-configuration)
  - [Pin Layout](#pin-layout)
  - [Contributing to the database](#contributing-to-the-database)
- [Handling EPROMs with Non-Standard Pin Layout (Adapters)](#handling-eproms-with-non-standard-pin-layout-adapters)
  - [Configuration Steps](#configuration-steps)
  - [Using the Adapter Configuration](#using-the-adapter-configuration)
- [Contributing](#contributing)
- [License](#license)
- [Support me](#support-me)


## Breaking Changes (v1.20)

### Legacy `type` wire field removed — `algorithm` is the sole dispatch key (breaking change)

The host→firmware JSON command no longer carries a `type` field (the old `mem_type`
integer). `algorithm` (the upstream minipro `protocol_id`) is now the **only** value
the firmware uses to decide how to program a chip — the backward-compatibility
`mem_type` fallback dispatch chain has been removed entirely, on both the firmware
and the host.

Every chip database entry — built-in or user override — now **must** carry a
usable, non-zero `algorithm`. A chip lacking one is refused by the CLI before any
serial byte is sent; there is no silent fallback to a `mem_type`-derived handler
anymore.

**Pre-v1.20 firmware/hosts stay safe.** A stale host CLI that still emits a `type`
field is not harmful to new firmware: the firmware silently skips unknown JSON
fields, so `type` simply no longer does anything. The only functional loss is the
fallback path itself, which was already dead code for every real chip in the
database.

**Upgrade:** `pip install --pre firestarter && firestarter fw -i --pre` — reflash
firmware **and** upgrade the CLI together, the same lockstep discipline as every
prior breaking wire-protocol change.

This change is beta-only (v1.20). Nothing is promoted to stable without operator authorization.

## Breaking Changes (v1.10)

### Command-channel wire protocol — COBS framing + CRC8 (breaking change)

The host→firmware JSON command channel now uses COBS framing with a CRC8-CCITT integrity byte.
Every command — including the firmware version probe — is wrapped as `[COBS(JSON + CRC8)][0x00]`
and written to the serial port in a single atomic call. The firmware verifies CRC8 **before** the
JSON parser sees any byte; the previous command channel had no checksum. The legacy `{`-peek
plaintext command path has been removed entirely from the firmware — there is no plaintext fallback.

**This is a breaking wire-protocol change with no mixed-version interop.** A new host CLI cannot
drive old (unframed) firmware, and an old host CLI cannot drive new firmware. A mismatched pair
simply fails (timeout or decode error). **Upgrade both the firmware and the host CLI together
(lockstep)** — exactly as required by the v1.2 Message-ID rework.

**Upgrade:** `pip install --pre firestarter && firestarter fw -i --pre` — reflash firmware **and**
upgrade the CLI at the same time.

This change is beta-only (v1.10). Nothing is promoted to stable without operator authorization.

## Installation
To install the Firestarter Python program and the firmware on the Arduino, follow the steps below:

### Installing the Firestarter Python Program
Use the package manager [pip](https://pip.pypa.io/en/stable/) to install Firestarter.

``` bash
pip install firestarter 
```

This command installs the Firestarter application, which allows you to interact with EPROMs using the Relatively-Universal-ROM-Programmer.

#### Auto complete
How to enable auto complete for firestarter, see: [auto complete](autocomplete.md)

### Beta / Pre-release Channel

v1.4 introduces an opt-in beta channel for both the app (via PyPI pre-releases) and the firmware
(via GitHub Pre-releases). App and firmware beta versions use matching `X.Y.ZbN` lockstep identifiers
so you always know both components are from the same beta cut.

> **New to the beta channel?** See [`doc/beta-testing-install.md`](doc/beta-testing-install.md)
> for a full stranger-friendly walkthrough: install the beta app, flash the matching beta firmware
> to your specific board, run a smoke check, then hand off into `firestarter dev test <chip>`.
>
> **`dev test` writes to the chip** — run it only on a blank or scratch part.
> A UV-erasable EPROM is asked first (yes = full device write, no/no-TTY =
> a small 256-byte region); every other family is written in full, unprompted.

#### Installing the beta app

```bash
pip install --pre firestarter
firestarter --version   # should print X.Y.ZbN (a beta identifier)
```

`pip install --pre` opts into pre-release versions on PyPI. `pip install firestarter` (without `--pre`)
still installs only the latest stable release.

#### Installing beta firmware

**Fetch and install the latest beta firmware for the configured board:**

```bash
firestarter fw -i --pre
```

Mirrors `pip install --pre` semantics; if no pre-release firmware exists for the board, falls back
to stable.

**Pin an exact firmware tag:**

```bash
firestarter fw -i --firmware-version 3.1.0b2
firestarter fw -i --firmware-version 3.1.0
```

The version string is validated against the PEP 440 regex (`^[0-9]+\.[0-9]+\.[0-9]+((b|rc)[0-9]+)?$`);
invalid input fails fast with no network call.

**Explicit stable channel override (escape hatch):**

```bash
firestarter fw -i --stable
```

Use `--stable` on a beta-installed app to force installation of the stable firmware channel instead
of the magic-default `--pre` routing (see **Magic default on beta-installed apps** below).

#### Listing available firmwares

```bash
firestarter fw --list             # all releases for the configured board
firestarter fw --list --pre       # pre-releases only
firestarter fw --list --stable    # stable releases only
firestarter fw --list --json      # JSON output (greppable / scriptable)
```

Output is plain-text with columns for version, channel, published date, and asset URL by default;
`--json` emits the same data as a JSON array.

#### Magic default on beta-installed apps

On a beta-installed app (i.e. `__version__` parses as a PEP 440 pre-release), bare
`firestarter fw -i` (no channel flag) auto-routes to `--pre` so beta users get matching beta
firmware by default. The following INFO line is logged when this routing occurs:

> Beta app detected — defaulting to --pre. Use --firmware-version X.Y.Z to pin a stable version.

Use `firestarter fw -i --stable` as the escape hatch to keep installing stable firmware on a
beta-installed app.

Note: `--pre`, `--firmware-version`, and `--stable` are mutually exclusive (3-way mutex). Only
one may be specified at a time. `--json` requires `--list`. `-i/--install` and `--list` are
mutually exclusive.

#### Channel selection matrix

| Goal | App install | Firmware install |
|------|-------------|-----------------|
| Stable everything (default) | `pip install firestarter` | `firestarter fw -i` |
| Beta everything (matched lockstep) | `pip install --pre firestarter` | `firestarter fw -i --pre` |
| Beta app, stable firmware (escape hatch) | `pip install --pre firestarter` | `firestarter fw -i --stable` |
| Pin exact firmware version | — | `firestarter fw -i --firmware-version 3.1.0b2` |

#### Stability guarantee

> **⚠ No stability guarantees.** Beta builds are intended for testing pre-release features. They may contain bugs, may change without notice, or may be withdrawn. For production / hardware-bench use, install the stable release.

#### Reporting issues against a beta build

When reporting a bug against a beta build, please include:

- **App beta version:** output of `pip show firestarter` (the `Version:` line, which will read `X.Y.ZbN`)
- **Firmware beta version:** output of `firestarter fw --list` (locate the installed `X.Y.ZbN` row) OR the firmware handshake string printed at `firestarter hw` startup
- **Board:** `uno`, `uno328pb`, or `leonardo` (or other configured board)
- **OS:** macOS / Linux / Windows + version
- **For hardware-related issues:** chip part number + manufacturer
- **Full stderr / traceback** for crashes
- **Repro steps**

Report app issues at: https://github.com/henols/firestarter_app/issues

Report firmware issues at: https://github.com/henols/firestarter/issues

Do not create new issue templates — use the existing Issues page linked above.

### Installing the Firmware on the Arduino

First, a few things to remember. 
1. Don't attach the Programmer shield while power is on.
2. Don't turn on power if the controller (Arduino) isn't programmed with appropriate firmware.
3. Don't insert a ROM in the socket until you're ready to write it. Don't leave a ROM in the socket during reset or programming.

To install the firmware on the Arduino, use the **fw** command with the `--install` option. This command installs the latest firmware version on the Arduino.

Firestarter is using **avrdude** to install the firmware.
**avrdude** is avalible as a separate [installer](https://github.com/avrdudes/avrdude) or via Arduino IDE.

#### Options
* `-p, --avrdude-path <path>`: Full path to avrdude (optional), set if avrdude is not found.
* `-c, --avrdude-config-path <path>`: Full path to avrdude config file (optional), may be needed if avrdude cannot find its default configuration (e.g., with older versions like 6.3 or non-standard installations).
* `--port <port>`: Serial port name (optional), set if the Arduino is not found.
* `-b, --board <board>`: Microcontroller board (optional), defaults to *uno*, other supported boards *uno328pb*, *leonardo*. (Since v1.5, the `uno328pb` target adds support for Arduino Uno R3 boards retrofitted with the ATmega328PB MCU.)
* `-f, --force`: Will install firmware even if the version is the same.
### Description
The `fw --install` command installs the latest firmware on the Arduino. The process typically involves the following steps:

1. **Locate avrdude**: The command locates the avrdude tool, which is used to upload the firmware to the Arduino. You can specify the path to `avrdude` using the `--avrdude-path` option if it is not found automatically.
2. **Identify Serial Port**: The command identifies the serial port to which the Arduino is connected. You can specify the port using the `--port` option.
3. **Upload Firmware**: The command uploads the latest firmware to the Arduino using `avrdude`.

### Example
To install the firmware on the **Arduino UNO**, you can run:
``` bash
firestarter fw --install
```

To install the firmware on the **Arduino Leonardo**, you can run:
``` bash
firestarter fw --install --board leonardo
```

To install the firmware on a **328PB-Uno** (Arduino Uno R3 carrier board re-MCU'd with ATmega328PB):
``` bash
firestarter fw --install --board uno328pb
```

Note: the `uno328pb` board uses MiniCore's Urclock bootloader by default. The host CLI auto-selects `programmer_id=urclock` for this board.


## Usage
Firestarter provides several commands to interact with EPROMs using the Relatively-Universal-ROM-Programmer.
Available commands: read, write, blank, erase, list, search, info, vpp, vcc, fw, config

### General Usage

``` bash
firestarter [options] {command}
```
  * `-h, --help`: Show help message
  * `-v, --verbose`: Enable verbose mode
  * `--version`: Show the Firestarter version and exit.

### Commands

#### Read
Reads the content from an EPROM.
``` bash
firestarter read <eprom> [output_file]
```
* `<eprom>`: The name of the EPROM.
* `[output_file]`: (Optional) Output file name, defaults to `<EPROM_NAME>.bin`.
##### Description
The read command reads the data from the specified EPROM and optionally saves it to a file. The process typically involves the following steps:

1. **Identify EPROM:** The command identifies the specified EPROM.
2. **Read Data:** The data from the EPROM is read.
3. **Save Data:** If an output file is specified, the read data is saved to that file. If no output file is specified, the data is saved to a file named `<EPROM_NAME>.bin`.

#### Write
Writes a binary file to an EPROM.
``` bash
firestarter write <eprom> <input_file> [options]
```
* `<eprom>`: The name of the EPROM.
* `<input_file>`: Input file name.
##### Options
* `-b, --no-blank-check`: Skip the blank check before write (erase still runs if the chip supports it).
* `--skip-erase`: Also skip the pre-write erase (for already-blank or non-erasable/pre-erased parts). **Warning:** skipping erase on a non-blank electrically-erasable chip leaves un-erased bits that cannot be reprogrammed.
* `-f, --force`: Force write, even if the VPP or chip ID don't match.
* `-a, --address <address>`: Write start address in decimal or hexadecimal.
* `--vpe-as-vpp`: Use VPE as the VPP voltage.
* `--pulse-us <1-65535>`: Override the database program-pulse width for this run, in microseconds. This bound is parity with another programmer's own integer width (a uint16), **not** a wire-type or hardware limit. The override replaces the value the host already sends for this run only — it introduces no new wire field and does not edit the database (see [Override and adding to the database](#override-and-adding-to-the-database) for the persistent, per-chip way to change this value).
* `--skip-sdp-unlock`: Decline the automatic software-data-protection unlock the firmware performs at the start of every protocol-0x0D write. Has no effect on any other protocol. **Warning:** on a chip whose software data protection is actually enabled, the write will then fail.
##### Description
The write command writes the contents of a specified binary file to the EPROM. The process typically involves the following steps:

1. **Blank Check:** By default, the command checks if the EPROM is blank before writing. This can be skipped using the `--no-blank-check` option.
2. **Erase:** If the EPROM is not blank, it may need to be erased before writing. This is a separate step from the blank check and is skipped only with the `--skip-erase` option.
3. **Write:** The binary data from the input file is written to the EPROM starting at the specified address (if provided).
4. **Verification:** The written data is verified to ensure it matches the input file.

##### Program-VCC ceiling
The raised program-VCC (around **6.25 V**) some vendor write algorithms assume for threshold margin is
unreachable on this shield, which has no VCC-raise path. What this host and firmware can deliver is
timing, pulse-count and verify fidelity — not silicon-margin fidelity. This is a hardware-bound
limitation, recorded rather than attempted.

#### Blank 
Checks if an EPROM is blank.
``` bash
firestarter blank <eprom>
```

* `<eprom>`: The name of the EPROM.

#### Erase
Erases an EPROM, if supported.
``` bash
firestarter erase <eprom>
```
* `<eprom>`: The name of the EPROM.
#### List
Lists all EPROMs in the database.
``` bash
firestarter list [options]
```

* `-v, --verified`: Only shows verified EPROMs.

#### Search
Searches for EPROMs in the database.
``` bash
firestarter search <text>
```

* `<text>`: Text to search for.

#### Info
Displays information about an EPROM.
``` bash
firestarter info <eprom>
```

* `<eprom>`: EPROM name.
##### Options
* `-c, --config`: Show EPROM configuration data in JSON format.

##### Description
The info command retrieves and displays detailed information about the specified EPROM. This information typically includes:

* EPROM name
* Manufacturer
* Device ID
* Memory size
* Supported voltages
* Pin configuration
* Any other relevant technical specifications

#### VPP
Displays the VPP voltage.
``` bash
firestarter vpp
```

#### VCC
Displays the VCC voltage.
``` bash
firestarter vcc
```

#### Firmware
Checks or installs the firmware version.
``` bash
firestarter fw [options]
```

* `-i, --install`: Try to install the latest firmware. (Do this *without* a chip in the socket or without the shield attached)
* `-p, --avrdude-path <path>`: Full path to avrdude (optional), set if avrdude is not found.
* `--port <port>`: Serial port name (optional).
* `-c, --avrdude-config-path <path>`: Full path to avrdude config file (optional).
* `-b, --board <board>`: Microcontroller board (optional, defaults to *uno*).
* `-f, --force`: Install firmware even if the version matches.

#### Configuration
Shows current configuration values. Use options to set specific values.
``` bash
firestarter config [options]
```

* `-rev, <revision>`: WARNING Overrides hardware revision (0-2), only use with HW mods. -1 disables override.
* `-r1, --r16 <resistance>`: Set R16 resistance, resistor connected to VPE.
* `-r2, --r14r15 <resistance>`: Set R14/R15 resistance, resistors connected to GND.

## Architecture

The `firestarter_app` host CLI uses a flat module layout under `firestarter/`. The 21 modules are organised by layer; the layer-boundary rules (below) describe how data flows between them.

### Module map

**Click handlers (user-facing CLI surface):**
- `main.py` — Click entry point (`main = cli` re-export); `exit_gracefully` SIGINT handler; ABI-preserving `firestarter.main:main` symbol per Phase 41 D-08.
- `cli_handlers.py` — 14 `@cli.command()` callbacks + `dev` `@cli.group()` with 4 sub-commands; `AppContext` dataclass; `@map_typed_errors` decorator at the Click boundary mapping typed exceptions to stable exit codes.

**Service layer (business logic, runs in-process between Click handlers and transport):**
- `chip_resolver.py` — `resolve_chip(name, db) -> programmer_config`; single source of truth replacing the 9× chip-lookup copy-paste shipped in v1.7.
- `eprom_operations.py` — read / write / verify / erase / blank-check / id (`_run_state_machine`, `_main_phase_read_data`); the read-loop body is ring-fenced for v1.9 RCA per GATE-1.8d.
- `hardware.py` — VPP / VPE voltage control (read-side coverage in tests; write-side untested by safety boundary).
- `firmware.py` — `firestarter fw -i` / `--pre` / `firmware list` (Phase 18 INST-01..04 + Phase 22 REL-01/02 beta channel integration).
- `eprom_info.py` — `firestarter info <chip>` data preparation (pure helpers; full path exercised once the upstream `ic_layout` TypeError is fixed in a future milestone).
- `ic_layout.py` — DIP socket pin-to-signal rendering for `firestarter info`.

**Transport (serial framing + codec):**
- `serial_comm.py` — `SerialCommunicator` port discovery + command dispatch; `_read_and_parse_lines` generator (DO NOT MODIFY — v1.9 RCA territory per Phase 40 SERIAL-03); `_validate_firmware_version` `@staticmethod`.
- `frame_parser.py` — CRC8, `_decode_param`, `_decode_id_frame`, `Response` / `LogMessage` structured types; testable without serial I/O.
- `codec.py` — `format_message`, revision-silkscreen rendering; isolated from frame parsing and logging side effects.

**Data layer (chip database + configuration):**
- `database.py` — `EpromDatabase` (`skip_local_override` seam per Phase 36 D-06): lookup, pin translation, command building. No singleton; constructor-injectable for testability.
- `config.py` — `ConfigManager` get/set/persist; `get_local_database` + `pin_maps`.
- `constants.py` — wire-protocol constants (`COMMAND_*`, `FLAG_*`, `CTRL_*`, `REVISION_*`); kept in sync with `firestarter/include/firestarter.h` + `rurp_shield.h` + `rurp_pinout.h` per firmware-contract parity tests.
- `messages.py` — codegen output for the v1.2 1-byte-message-ID catalog (`tools/catalog/messages.toml`).
- `exceptions.py` — consolidated typed hierarchy: `ChipNotFoundError`, `FirmwareOutdatedError`, `SerialError`, `SerialTimeoutError`, `EpromOperationError`, `HardwareOperationError`; flows upward to the Click boundary.

**Address / value parsing:**
- `address_parser.py` — hex / decimal address + size parsing with explicit validation; consumed by `_setup_operation`.

**Utility / system:**
- `avr_tool.py` — `avrdude` wrapper for firmware sideload (`firestarter fw -i` / `firestarter fw -i --pre`).
- `logging_utils.py` — logger configuration; verbosity levels; serial-debug channel handling.
- `utils.py` — small shared helpers (hex dump, byte formatting, etc.).
- `__init__.py` — package marker.

### Layer-boundary rules

Click handlers in `cli_handlers.py` are the user-facing entry. Each command callback is wrapped by `@map_typed_errors` (defined in `cli_handlers.py` per Phase 42 D-03) which catches the typed exceptions raised by lower layers and re-raises each as `click.ClickException` with a stable prefix → exit code 1. Handlers call the service layer (`chip_resolver.resolve_chip`, `eprom_operations.*`, `hardware.*`, `firmware.*`) directly — no central dispatcher. Service-layer code calls the transport layer (`serial_comm.SerialCommunicator`, helpers from `frame_parser` + `codec`) to exchange JSON-over-serial commands at 250000 baud. The `_read_and_parse_lines` generator body inside `serial_comm.py` is **ring-fenced for the v1.9 read-bug RCA** (GATE-1.8d) — structural-only changes; any non-structural edit reopens the v1.6 baseline binaries.

Typed exceptions (`exceptions.py`) flow upward from any layer; the `@map_typed_errors` decorator at the Click boundary is the single mapping point. Service-layer code does NOT translate exceptions to exit codes — that responsibility is concentrated at the Click boundary per Phase 42 ERR-01.

The 3-way verdict contract for `firestarter dev consistency-check` (0=PASS, 1=FAIL, 2=hardware-error) is preserved end-to-end via `sys.exit(verdict_int)` inside `cli_handlers.py:dev_consistency_check`; this `SystemExit` falls outside the `@map_typed_errors` decorator's except list, so the verdict integer is honoured verbatim (see Phase 42 Plan 42-02 D-12 step 5).

### Tooling workflow

The v1.8 quality gate runs locally and in CI:

- **Lint:** `ruff check firestarter/ tests/` — categories E, F, I (+ UP); no `select = ["ALL"]`. Baseline accepted via `--add-noqa` on legacy lines per Phase 37 TOOL-01.
- **Format:** `ruff format firestarter/ tests/` — `ruff format --check` is the CI assertion; the formatter is the canonical source per Phase 37.
- **Type check:** `mypy` runs gradually across the tree (watermark gate); strict mode (`disallow_untyped_defs = true` + `check_untyped_defs = true`) is enabled on 8 modules per Phase 42 D-06: `main.py`, `cli_handlers.py`, `chip_resolver.py`, `frame_parser.py`, `codec.py`, `address_parser.py`, `exceptions.py`, `serial_comm.py`. `eprom_operations.py` is deliberately excluded from the strict list per Phase 42 D-07 (GATE-1.8d read-path ring-fence; deferred to v1.9 post-RCA).
- **Tests + coverage:** `pytest --cov=firestarter --cov-fail-under=70` — 70% floor enforced since Phase 42 Plan 42-03 raised the gate from the earlier 50% floor.

Enforcement points:

- **CI:** `.github/workflows/ci.yml` runs ruff check + ruff format --check + mypy + pytest --cov-fail-under=70 + the `pip install -e . && firestarter --help` entry-point smoke step on every PR (Phase 41 CLI-04 SC#4).
- **Pre-commit:** the `pre-commit` config (Phase 37 TOOL-03) wires the same hook order locally: ruff-check → ruff-format → mypy. Operator can install via `pre-commit install` from the `firestarter_app/` root.
- **Snapshot tests:** the 29 syrupy snapshots under `tests/__snapshots__/` pin the CLI surface (Phase 36 TEST-01); updating them requires explicit `pytest --snapshot-update` plus a documented rationale per Phase 41 Plan 41-04 deviation Rule 4.

---

## Examples
### Read
To read an EPROM named W27C512 and save the output to output.bin:
``` bash
firestarter read W27C512 output.bin
```
### Write
To write a binary file input.bin to an EPROM named W27C512:
``` bash
firestarter write W27C512 input.bin
```
### Info
To get information about an EPROM named W27C512:
``` bash
firestarter info W27C512
```
This command will output detailed information about the W27C512 EPROM, showing package layout and the jumper configuration for the RURP shield.
``` text
Eprom Info 
Name:           W27C512
Manufacturer:   WINBOND
Number of pins: 28
Memory size:    0x10000
Type:           EPROM
Can be erased:  True
Chip ID:        0xda08
VPP:            12v
Pulse delay:    100µS

       28-DIP package
        -----v-----
  A15 -|  1     28 |- VCC   
  A12 -|  2     27 |- A14   
  A7  -|  3     26 |- A13   
  A6  -|  4     25 |- A8    
  A5  -|  5     24 |- A9    
  A4  -|  6     23 |- A11   
  A3  -|  7     22 |- OE/Vpp
  A2  -|  8     21 |- A10   
  A1  -|  9     20 |- CE    
  A0  -| 10     19 |- D7    
  D0  -| 11     18 |- D6    
  D1  -| 12     17 |- D5    
  D2  -| 13     16 |- D4    
  GND -| 14     15 |- D3    
        -----------

        Jumper config
JP1    5V [ ●(● ●)] A13   : A13
JP2    5V [(● ●)● ] A17   : VCC
JP3 28pin [ ● ● ● ] 32pin : NA
```

## Override and adding to the database
### Eprom Configuration
By adding a file named `database.json` in the **.firestarter** folder under the home directory, it is possible to overide **Eprom** configurations or add new **Eproms** in the Firestarter database.

To overide an **Eprom** configuration extract the configuration for the intended **Eprom** with the info config command, ex: `firestarter info W27C512 -c` and the configuration will be printed.

W27C512 config:
``` json
{
    "WINBOND": [
        {
            "name": "W27C512",
            "pin-count": 28,
            "can-erase": true,
            "has-chip-id": true,
            "chip-id": "0x0000da08",
            "pin-map": 16,
            "protocol-id": "0x07",
            "memory-size": "0x10000",
            "type": "memory",
            "voltages": {
                "vpp": "12"
            },
            "pulse-delay": "0x0064",
            "flags": "0x00000078",
            "verified": true
        }
    ]
}
```
To overide the `pulse-delay` copy the configuration into the `database.json` in the **.firestarter** folder and remove all fields that aren't relevant to the change except for the manufacturer name and the **Eprom** name, ex:
``` json
{
    "WINBOND": [
        {
            "name": "W27C512",
            "pulse-delay": "0x0064"
        }
    ]
}
```

The firmware applies this `pulse-delay` width per byte while writing. A value of `0x0000` means the
protocol's own built-in fallback width is used instead. The `write` command's `--pulse-us` option
(see [Write](#write) above) changes this width for one run only, without editing `database.json` — use
the override for a one-off experiment and this file-based override for a persistent, per-chip change.

### Pin layout
By adding a file named `pin-maps.json` in the **.firestarter** folder under the home directory, it is possible to overide a **pin map** or add new **pin map** that aren't existing.

To overide or add a new **pin map** use the info config command, ex: `firestarter info W27C512 -c` to get a starting point.
W27C512 pin map:
``` json
{
    "28": {
        "16": {
            "address-bus-pins": [
                10, 9, 8, 7, 6, 5, 4, 3, 25, 24, 21, 23, 2, 26, 27, 1
            ]
        }
    }
}
```
The top level key (`"28"`) refers to the number of pins for the **Eprom** and the sub-key (`"16"`) is the `pin-map` in the configuration data for an **Eprom**.
Valid fields:
- `address-bus-pins` is an array with the pin numbers from the **Eprom** pin layout, starts with least significant address pin (A0) and added in order to the most significant address pin (A0,A2,...Ax).
- `rw-pin` Optional, if a read/write or a program enable pin is pressent `rw-pin` must be set to the pin layout number.
- `vpp-pin` Optional, if the **Eprom** needs programming voltage (Vpp) on pin 1 `vpp-pin` must be set to 1.

### Contributing to the database
Please report back new or changed configurations to Firestarter.
Create an [Issue](https://github.com/henols/firestarter_app/issues) where you describe the added **Eprom** or the changes that are done and add the json configurations from the database.json and pin-maps.json that are made.

## Handling EPROMs with Non-Standard Pin Layout (Adapters)

Some EPROMs, especially older 24-pin types or those adapted from other packages, require the programming voltage (VPP) or have or not using the OE (Output Enable) pin on a pin different from the standard locations the Firestarter hardware shield expects.

*   **Standard 28/32-pin Sockets:** Firestarter typically applies VPP via **Pin 1** or on the **OE (Output Enable) Pin (20)**.
*   **Standard 24-pin Sockets (using specific protocols):** Might not have the **OE (Output Enable)** pin (e.g., Pin 20 on a 24-pin EPROM, which maps to Pin 22 on the 28-pin ZIF socket), wich Firestarter expects it to be connected to.

When an EPROM's VPP pin doesn't match these expectations (like the M2716 needing VPP on its Pin 21) or the **OE** is omitted, you need two things:

1.  **A Physical Hardware Adapter:** This adapter sits between the Firestarter shield's socket and the EPROM. It reroutes the VPP signal or the OE pin from the pin where Firestarter *outputs* it (e.g., Pin 1 of the 28-pin socket) to the pin where the EPROM *needs* it (e.g., Pin 21 of the M2716). It also maps all other necessary pins correctly.
2.  **A Custom Software Configuration:** You need to tell Firestarter about this new "virtual" EPROM definition that uses the adapter. This involves defining the EPROM's characteristics and, crucially, the *new pin mapping* as seen by the programmer through the adapter.

### Configuration Steps:

The configuration requires adding entries to two files in your `.firestarter` user directory: `database.json` (for the EPROM definition) and `pin-maps.json` (for the physical pin connections of the adapter).

#### Step 1: Define the Adapter Setup in `database.json`

1.  **Find Base Configuration:** Get the original JSON configuration for your EPROM (e.g., `firestarter info M2716 -c`).
2.  **Create New Entry:** In your user `database.json` file, add a new entry under the appropriate manufacturer (e.g., "INTEL").
3.  **Assign Unique Name:** Give it a descriptive name indicating it uses an adapter (e.g., `M2716-adapter`).
4.  **Set `pin-count`:** Use the pin count of the *adapter's socket* that plugs into the programmer (usually 28 or 32). For the M2716 adapter example, this is `28`.
5.  **Assign Unique `pin-map` ID:** Create a *new, unique identifier* for this adapter's pin mapping. This ID links this definition to the corresponding map in `pin-maps.json`. In the example, `2716` is used.
6.  **Copy/Verify Parameters:** Copy essential parameters like `protocol-id`, `memory-size`, `type`, `voltages` (ensure VPP voltage is correct!), `pulse-delay`, and `flags` from the original EPROM definition. Adjust `can-erase` and `has-chip-id` as needed for the specific chip.

**Example (`database.json` entry for M2716-adapter):**

```json
{
    "INTEL": [
        {
            "name": "M2716-adapter",  // Unique name for the adapter setup
            "pin-count": 28,          // Pin count of the adapter socket plugging into the programmer
            "can-erase": false,
            "has-chip-id": false,
            "pin-map": 2716,          // Unique ID linking to pin-maps.json
            "protocol-id": "0x0b",    // Copied from original M2716
            "memory-size": "0x800",   // Copied from original M2716
            "type": "memory",
            "voltages": {
                "vpp": "25"           // VPP required by the M2716
            },
            "pulse-delay": "0x01f4",  // Copied from original M2716
            "flags": "0x00000048",    // Copied from original M2716
            "verified": false
        }
    ]
}
```

#### Step 2: Define the Adapter Pin Mapping in `pin-maps.json`
1. **Find/Create Section:** In your user pin-maps.json, find or create the section corresponding to the `pin-count` of your adapter socket (e.g., `"28"`).
2. **Add New Map Entry:** Add a new key using the exact unique *`pin-map`* ID you created in `database.json` (e.g., `"2716"`).
3. **Define `address-bus-pins`:** List the programmer socket pins (the pins on the 28-pin ZIF socket in this example) that are connected to the EPROM's address lines (A0, A1, A2...) through the adapter. The order must be A0 first, then A1, A2, etc.
4. **Define vpp-pin (Crucial for VPP Rerouting):**
    * If your adapter routes VPP from the programmer's Pin 1 (standard 28/32-pin VPP output) to the EPROM's actual VPP pin, add `"vpp-pin": 1`. This tells Firestarter to control VPP using its standard VPP circuitry connected to Pin 1 of its socket. **This is the case in the M2716-adapter example.**
    * If your adapter routes VPP from the programmer's **OE Pin** (Pin 22 on the 28-pin socket) to the EPROM's actual VPP pin, you would typically omit the `vpp-pin` entry here. Firestarter would likely use the OE pin for VPP control based on the `protocol-id` (like `0x0b`).
5. **Define `rw-pin` (If Applicable):** If the EPROM has a Write Enable (WE) or Program (PGM) pin, add `rw-pin` and set its value to the programmer socket pin connected to the EPROM's WE/PGM pin via the adapter.

**Example (`pin-maps.json` entry for M2716-adapter):**

*(This adapter routes VPP from the programmer's Pin 1 to the EPROM's Pin 21)*
```json
{
    "28": {
        "2716": { // Matches the pin-map ID from database.json
            "address-bus-pins": [
                10, 9, 8, 7, 6, 5, 4, 3, 25, 24, 21 // Programmer socket pins for A0-A10 via adapter
            ],
            "vpp-pin": 1 // Tells Firestarter to use Pin 1 on its socket for VPP control
        }
        // ... other pin maps for 28-pin sockets ...
    }
    // ... other pin counts like "24", "32" ...
}
```
### Using the Adapter Configuration
After saving these changes to your user `database.json` and `pin-maps.json` files, you can now use the adapter definition directly with Firestarter:
```bash
firestarter write M2716-adapter my_rom_file.bin
firestarter read M2716-adapter backup.bin
firestarter blank M2716-adapter
```
Firestarter will use the `M2716-adapter` definition, look up the `pin-map` ID `2716` under the `28`-pin section in `pin-maps.json`, and correctly control the address lines and apply VPP using Pin 1 of its own socket, which the physical adapter then routes to the correct pin on the M2716 EPROM.

## Shield Revision Detection

The Firestarter firmware reports the detected RURP shield silkscreen revision on every handshake (`MSG_OK_REV`). For the per-revision capability matrix, silkscreen → code alias table, and per-rev expected ADC band table, see the firmware sub-repo doc:

https://github.com/henols/firestarter/blob/main/doc/SHIELD-REVISIONS.md

If detection reports `rev_unknown` (pre-detect-resistor boards or guard-gap landing), set the EEPROM override byte: `firestarter rev <N>` where `<N>` is the silkscreen-rev byte:

| Byte | Silkscreen rev |
|------|----------------|
| `0x00` | Rev 0 |
| `0x01` | Rev 1 |
| `0x02` | Rev 2.0-class (broad bucket — covers Rev 2.0/2.1/2.2) |
| `0x05` | Rev 2.3 |
| `0xFE` | `rev_unknown` sentinel (no override) |
| `0xFF` | EEPROM-override-absent sentinel (reset to detect-rev) |

## Contributing
Pull requests are welcome. For major changes, please open an issue first
to discuss what you would like to change.

Please make sure to update tests as appropriate.

## License
[MIT](https://raw.githubusercontent.com/henols/firestarter_app/main/LICENSE)

## Support me
Support me on ko-fi to keep me motivated to continue to develop Firestarter.

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/E1E21I2WWW)