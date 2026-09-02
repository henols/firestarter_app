<p align="left"><img src="https://raw.githubusercontent.com/henols/firestarter_app/refs/heads/main/images/firestarter_logo.png" alt="Firestarter EPROM Programmer" width="200"></p>

# Firestarter

Command-line tool for the **Firestarter EPROM programmer** — an Arduino with a
Relatively-Universal-ROM-Programmer (RURP) shield. It reads, writes, erases and verifies EPROM,
EEPROM, Flash and SRAM chips, from a database of 746 parts across 59 manufacturers.

**New here?** Start at [firestarter_prom](https://github.com/henols/firestarter_prom) — what
Firestarter is, what hardware you need, and how to read your first chip.

## Table of contents

- [Installation](#installation)
  - [Pre-release channel](#pre-release-channel)
- [Usage](#usage)
- [Commands](#commands)
- [Configuration](#configuration)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)
- [Support me](#support-me)

## Installation

```bash
pip install firestarter
```

Then flash the matching firmware to your board and check it answers:

```bash
firestarter fw -i
firestarter hw
```

Flashing needs [avrdude](https://github.com/avrdudes/avrdude) on your `PATH`.

### Pre-release channel

```bash
pip install --pre firestarter
firestarter fw -i --pre
```

`--pre` opts into pre-release versions on PyPI; plain `pip install firestarter` only ever
installs the latest stable release.

**The CLI and the firmware are upgraded together.** A mismatched pair fails with a timeout or a
decode error — see
[Breaking Changes](https://github.com/henols/firestarter_prom/wiki/Breaking-Changes).

## Usage

```bash
firestarter [OPTIONS] COMMAND [ARGS]
```

Global options: `-v/--verbose`, `-p/--port` to override the saved serial port, `--version`.

Find your chip, then read it:

```bash
firestarter search 27C256
firestarter info AM27C256
firestarter read AM27C256 dump.bin
```

## Commands

| Command | What it does |
|---|---|
| `read` | Read a chip to a file |
| `write` | Write a binary file to a chip |
| `verify` | Verify a chip against a file |
| `blank` | Check whether a chip is blank |
| `erase` | Erase a chip, where the family supports it |
| `id` | Read the chip's manufacturer and device ID |
| `info` | Show what the database knows about a chip |
| `list` | List every chip in the database |
| `search` | Search the database |
| `fw` | Firmware version; `-i` installs |
| `hw` | Shield hardware revision |
| `vpp` / `vpe` | Read the programming rail voltages |
| `config` | Read and set configuration values |
| `dev` | Development and diagnostic commands, including `dev test` |

Every command takes `--help`.

## Configuration

Settings live in `~/.firestarter/config.json`, managed with `firestarter config`.

You can add or override chips with your own `~/.firestarter/database.json`; entries there take
precedence over the shipped database. The field reference is
[Chip Database Fields](https://github.com/henols/firestarter_prom/wiki/Chip-Database-Fields).

## Documentation

Everything else is on the **[Firestarter wiki](https://github.com/henols/firestarter_prom/wiki)** —
supported chips and protocols, pin maps and adapters, shield revisions, and how to test a chip
against real hardware.

## Contributing

See the [Contributing](https://github.com/henols/firestarter_prom/wiki/Contributing) wiki page for where to report a problem and where to open a pull request.

## License

[MIT](https://raw.githubusercontent.com/henols/firestarter_app/main/LICENSE)

## Support me

Support me on ko-fi to keep me motivated to continue to develop Firestarter.

<a href='https://ko-fi.com/M4M2Z7VNE' target='_blank'><img height='36' style='border:0px;height:36px;' src='https://storage.ko-fi.com/cdn/kofi3.png?v=6' border='0' alt='Buy Me a Coffee at ko-fi.com' /></a>
