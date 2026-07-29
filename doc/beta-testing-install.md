<p align="left"><img src="https://raw.githubusercontent.com/henols/firestarter_app/refs/heads/main/images/firestarter_logo.png" alt="Firestarter EPROM Programmer" width="200"></p>

---

## Install the Beta & Flash Beta Firmware to Help Test PROMs

This doc is for a **community member on a fresh machine** who wants to help
test PROMs — you have never touched this project before, you have an Arduino
Uno, Arduino Leonardo, or a 328PB-Uno, and an RURP shield. In one sentence:
**install the beta app, flash the matching beta firmware to your board, run a
quick smoke check, then run `firestarter dev test <chip>` against real
hardware.**

> **Bench-validated:** every per-board step below was run end-to-end on real
> Arduino Uno, Arduino Leonardo, and 328PB-Uno hardware against the `3.0.0b11`
> beta — fresh-venv `pip install --pre firestarter` → bare `fw -i` beta
> auto-route → `.hex` flash + avrdude verify → `fw`/`hw` smoke all pass. If a
> step here doesn't match what you see, please file an issue (see the bottom of
> this doc).

**What this doc is NOT:** it does not walk you through writing or verifying a
chip. The steps below only prove that the beta app + beta firmware you just
installed are alive and speaking the protocol to each other. Proving that a
*specific chip* actually works is the job of `firestarter dev test <chip>` —
see the hand-off section at the end.

---

### 1. Prerequisite: avrdude

Flashing firmware to your board uses **avrdude**, a separate tool this project
does not bundle. Install it before you start:

- **Linux (Debian/Ubuntu):** `sudo apt install avrdude`
- **macOS (Homebrew):** `brew install avrdude`
- **Windows / any OS:** avrdude also ships inside the [Arduino IDE](https://www.arduino.cc/en/software) toolchain and inside [PlatformIO](https://platformio.org/)'s toolchain if you already have either installed.

Firestarter auto-detects `avrdude` on your `PATH`. If it can't find it, pass
`--avrdude-path /full/path/to/avrdude` on the `fw -i` command below.

**Version matters:** avrdude `>= 7.0` needs no extra configuration. If you're
stuck on the older `6.3` release (or another install that can't find its own
config file), pass `-c/--avrdude-config-path /path/to/avrdude.conf` on the same
command. Check your version with `avrdude -v`.

---

### 2. Know your board, know your `.hex`

Firestarter builds a **separate firmware image per board** — flashing the
wrong one will not work (avrdude's signature check rejects a mismatched MCU).
Use the row that matches your hardware:

| Your board | Board flag | Firmware asset | avrdude partno / programmer / baud |
|---|---|---|---|
| Arduino Uno | `-b uno` | `firestarter_uno.hex` | `atmega328p` / `arduino` / `115200` |
| Arduino Leonardo | `-b leonardo` | `firestarter_leonardo.hex` | `atmega32u4` / `avr109` / `57600` |
| 328PB-Uno (Uno R3 carrier re-MCU'd with ATmega328PB) | `-b uno328pb` | `firestarter_uno328pb.hex` | `atmega328pb` / `urclock` / `115200` |

You don't type the `.hex` filename or the avrdude flags yourself — passing
`-b <board>` to `firestarter fw -i` (below) picks all of this for you
automatically. The table above just tells you what to expect in the output, and
lets you double check you asked for the right board.

**328PB-Uno note:** these boards look identical to a plain Uno from the
outside — nothing on the case tells you which MCU is actually soldered in. If
you're not sure which you have, it's safer to first try `-b uno328pb`; if
avrdude's signature check rejects the flash, you likely have a plain Uno —
retry with `-b uno` instead. Don't guess and force it; let avrdude's signature
check tell you.

> **328PB-Uno known quirks (from bench validation):** (1) a bare `firestarter
> fw` on a 328PB-Uno prints a trailing *"Could not find firmware version or URL
> for board 'uno328pb' in the latest release"* — this is harmless: `fw` also
> checks the **stable** channel for updates, and the 328PB ships **only**
> beta/prerelease `.hex` (no stable build), so that lookup finds nothing. The
> firmware-version read itself still succeeds (you'll see the correct
> `3.0.0b11 ... controller: uno328pb` line above it). (2) The 328PB-Uno has been
> less stable than the Uno/Leonardo on heavier operations in the past (occasional
> read timeouts / voltage-read drift); the install + flash + smoke chain here is
> reliable, but if a later `dev test` run is flaky on this board, retry and
> capture the exact output rather than assuming a chip verdict.

---

### 3. Install the beta app (fresh machine)

Use a fresh virtual environment so you don't mix this into any existing Python
setup:

```bash
python3 -m venv firestarter-beta-venv
source firestarter-beta-venv/bin/activate      # Windows: firestarter-beta-venv\Scripts\activate

pip install --upgrade pip
pip install --pre firestarter

firestarter --version    # MUST print a beta string like 3.0.0bN — not a
                          # plain 3.x.y. If you see a plain stable version,
                          # the --pre flag above didn't take effect; re-run
                          # `pip install --pre firestarter --force-reinstall`.
```

`pip install --pre` opts you into pre-release ("beta") versions on PyPI.
Plain `pip install firestarter` (no `--pre`) only ever installs the latest
stable release and will not give you the beta app.

---

### 4. Flash the matching beta firmware

Plug in your board over USB, then run (substituting your board's flag from the
table in step 2):

```bash
firestarter fw -i -b uno          # or -b leonardo / -b uno328pb
```

Because you installed a **beta** app in step 3, this bare command (no
`--stable`, no `--firmware-version`) automatically routes to the beta
(`--pre`) firmware channel for you — you don't need to pass `--pre` yourself.
This downloads the board-matching `.hex` from the project's GitHub prerelease
and uses avrdude to flash + verify it onto your board.

Watch the command's output for:
- the resolved release/tag it downloaded from (should be a beta identifier,
  e.g. `3.0.0bN`)
- the exact asset name it fetched (should match the `.hex` from the table in
  step 2 for your board)
- avrdude's final flash + verify result

If you ever want to force the stable firmware channel instead (not needed for
this walkthrough), the escape hatch is `firestarter fw -i -b <board> --stable`.

---

### 5. A note on `/dev/ttyACM*` / `/dev/ttyUSB*` port numbers

If you have more than one board plugged in, or you unplug/replug a board
between commands, **the OS may reassign which `/dev/ttyACM*` (or `/dev/ttyUSB*`,
or `COMx` on Windows) number belongs to which physical board.** Don't assume
port 0 is still "the same board" as your last command — if a command reports
the wrong board name back to you, or seems to hang, double-check which port is
actually attached to which board before retrying (unplug the others if you're
unsure, or pass `--port` explicitly). This is a normal OS-level quirk, not a
bug in firestarter.

---

### 6. Smoke-test the flashed board

Two quick checks — neither of these touches a chip in the socket at all
(unlike `dev test`, covered next, which always writes to whatever chip is
seated):

```bash
firestarter fw     # confirms: current firmware version + board identity
firestarter hw     # confirms: the board is alive and answers a live protocol op
```

`firestarter fw` (no `-i`) reads back the firmware identity string from the
board and reports the version + board it's currently running — this should
match the beta version + board you just flashed in step 4. `firestarter hw`
reads the hardware revision from the board over the live serial protocol,
which requires nothing more than a powered board (no chip needs to be seated
in the socket).

**Again: this smoke test does not write to or read from any chip.** It only
proves the beta app + beta firmware you just installed can talk to each other.
If both commands succeed, your beta stack is alive and ready.

---

### 7. Next: help test a chip

Once your board smoke-tests clean, you're ready to run the real community
chip-validation sweep against a chip you have on hand:

```bash
firestarter dev test <chip>
```

**`dev test` writes to the chip — run it only on a blank or scratch part you
are willing to sacrifice.** If the chip is a UV-erasable EPROM, the command
stops and asks first: answering yes writes the full device, and answering no
(or running with no terminal attached at all) still writes a small 256-byte
region — there is no read-only answer. Every other family — including this
project's own AT28C — is written in full, twice, with no prompt at all. Once
the sweep finishes it produces a diagnostic report you can review and, if
you'd like, file as a GitHub issue to help the project learn whether that
chip actually works. For the full picture of what `dev test` does, how its
results are classified, and what it means (and doesn't mean) for a chip's
official support status, see
[`community-validation.md`](community-validation.md).

---

### Reporting a beta install/flash problem

If something in this walkthrough didn't work, please open an issue and include:

- **App beta version:** output of `firestarter --version`
- **Board:** `uno`, `uno328pb`, or `leonardo`
- **The exact command that failed** and its full output
- **OS:** macOS / Linux / Windows + version
- **avrdude version:** output of `avrdude -v`

Report app/install/flash issues at: https://github.com/henols/firestarter_app/issues

Report firmware issues at: https://github.com/henols/firestarter/issues
