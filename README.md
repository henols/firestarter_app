<p align="left"><img src="https://raw.githubusercontent.com/henols/firestarter/main/images/branding/firestarter_logo_horizontal.png" alt="Firestarter EPROM Programmer" width="400"></p>

# Firestarter

[![PyPI version](https://badge.fury.io/py/firestarter.svg)](https://badge.fury.io/py/firestarter)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Buy me a coffee](https://img.shields.io/badge/Ko--fi-Buy%20me%20a%20coffee-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/E1E21I2WWW)

This is the command-line tool for the **Firestarter EPROM programmer**. The programmer is an
Arduino with a [Relatively-Universal-ROM-Programmer](https://github.com/AndersBNielsen/Relatively-Universal-ROM-Programmer)
(RURP) shield. The tool reads, writes, erases and verifies EPROM, EEPROM, Flash and SRAM chips.
Its database holds 746 parts from 59 manufacturers.

**New to Firestarter?** Start at the [Firestarter project page](https://github.com/henols/firestarter#readme).
It tells you what Firestarter is, what hardware you need and how to read your first chip.

- Firestarter in action: [watch the video](https://youtu.be/JDHOKbyNnrE?si=0_iXKPZZwyyNGUTZ).
- Anders S Nielsen, who made the RURP, talks about Firestarter: [watch the video](https://youtu.be/SZQ50XZlk5o?si=IKOqQUeG4Rms1cUs).
- Support and discussion forum: [Discord](https://discord.com/invite/kmhbxAjQc3).

## Table of contents

- [Install](#install)
- [Usage](#usage)
- [Commands](#commands)
- [Configuration](#configuration)
- [Documentation](#documentation)
- [Developer documents](#developer-documents)
- [Changelog](#changelog)
- [Contributing](#contributing)
- [License](#license)
- [Support](#support)

## Install

1. Install the CLI with pip or pipx:

   ```bash
   pip install firestarter
   # or
   pipx install firestarter
   ```

2. Install the matching firmware on your board. Replace `<board>` with `uno`, `uno328pb` or
   `leonardo`:

   ```bash
   firestarter fw -i -b <board>
   ```

3. Make sure that the board answers:

   ```bash
   firestarter hw
   ```

The firmware install needs [avrdude](https://github.com/avrdudes/avrdude). The tool looks for it on
your `PATH`. Use `--avrdude-path` if it is in a different location.

For more install steps, and for an upgrade from 2.0.x, refer to the wiki
[Install](https://github.com/henols/firestarter/wiki/Install) page.

For pre-release versions, refer to the wiki
[Beta Channel](https://github.com/henols/firestarter/wiki/Beta-Channel) page.

## Usage

```bash
firestarter [OPTIONS] COMMAND [ARGS]
```

Global options:

- `-v`, `--verbose`: show more output.
- `-p`, `--port`: use this serial port, not the port in the configuration.
- `--version`: show the version.

Find your chip, then read it to a file:

```bash
firestarter search 27C256
firestarter info AM27C256
firestarter read AM27C256 dump.bin
```

## Commands

| Command | What it does |
|---|---|
| `read` | Reads a chip to a file. |
| `write` | Writes a binary file to a chip. |
| `verify` | Compares a chip with a file. |
| `blank` | Tells you if a chip is blank. |
| `erase` | Erases a chip, if the chip family supports it. |
| `id` | Reads the manufacturer and device ID of the chip. |
| `info` | Shows the database data for a chip. |
| `list` | Lists all chips in the database. |
| `search` | Finds chips in the database. |
| `fw` | Shows the firmware version. `-i` installs the firmware. |
| `hw` | Shows the hardware revision of the shield. |
| `vpp` | Reads the VPP rail voltage. |
| `vpe` | Reads the VPE rail voltage. |
| `config` | Reads and sets configuration values. |
| `dev` | Diagnostic commands. On a stable install, it has `dev read` and `dev test`. |

Each command accepts `--help`.

## Configuration

The tool keeps its settings in `~/.firestarter/config.json`. Use `firestarter config` to set them.

You can add a chip, or replace the data for a chip, in your own `~/.firestarter/database.json`.
An entry in that file has priority over the shipped database. The wiki
[Chip Database Fields](https://github.com/henols/firestarter/wiki/Chip-Database-Fields) page
describes the fields.

## Documentation

The user documentation is on the [Firestarter wiki](https://github.com/henols/firestarter/wiki).
It includes these pages:

- [Reading Chips](https://github.com/henols/firestarter/wiki/Reading-Chips)
- [Writing and Verifying](https://github.com/henols/firestarter/wiki/Writing-and-Verifying)
- [Testing Chips](https://github.com/henols/firestarter/wiki/Testing-Chips)
- [Known Issues](https://github.com/henols/firestarter/wiki/Known-Issues)

The list of chips that passed a test on real hardware is in
[`VALIDATED-EPROMS.md`](https://github.com/henols/firestarter/blob/main/VALIDATED-EPROMS.md).

## Developer documents

To set up a development install, clone this repository. Then run these commands in its root
directory:

```bash
pip install -e '.[test]'
pytest
```

The tool needs Python 3.11 or later.

[DECODE-NOTES.md](https://github.com/henols/firestarter_app/blob/main/tools/DECODE-NOTES.md)
records how the database generator decodes the minipro `infoic.xml` fields.

## Changelog

All versions of all Firestarter parts are in one
[CHANGELOG.md](https://github.com/henols/firestarter/blob/main/CHANGELOG.md).

## Contributing

The wiki [Contributing](https://github.com/henols/firestarter/wiki/Contributing) page tells you
where to report a problem and where to open a pull request.

## License

[MIT](https://github.com/henols/firestarter_app/blob/main/LICENSE)

## Support

If you want to support the work on Firestarter, you can
[buy me a coffee on Ko-fi](https://ko-fi.com/E1E21I2WWW).
