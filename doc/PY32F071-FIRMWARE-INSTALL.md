# PY32F071 firmware install over USB

How `firestarter fw --install --board py32f071` reaches a PY32F071 board, how the
board is put into its bootloader, and what still has to be confirmed on real
hardware.

> **Status: unverified against silicon.** No PY32F071 board existed when this was
> written (2026-07-28). The host side is implemented and unit-tested; the two
> facts that can only come from hardware are called out in
> [§5 First bench session](#5-first-bench-session).

---

## 0. Availability — pre-release builds only

This install path ships on **beta** and is **disabled on stable**. The PY32F071
target has never run on real silicon, so a stable build must not offer users a
flash operation nobody has ever completed.

The gate is the app's own version (`firestarter/channel.py`): a PEP 440
pre-release — `3.0.0b13`, `3.0.0rc1`, or a `_dev` checkout — means a beta build,
and a final release like `3.0.0` means stable. That is the same predicate the app
already uses to decide a beta install should default to `--pre` (D-23), so this
introduces no new notion of "which channel am I".

| | beta build | stable build |
|---|---|---|
| `fw --help` board list | `[uno\|uno328pb\|leonardo\|py32f071]` | `[uno\|uno328pb\|leonardo]` |
| `--usb-id`, `--dfu-probe` | shown | hidden |
| `fw --board py32f071` | accepted | exit 2, invalid value |
| `fw --dfu-probe` | runs | exit 2, no such option |
| `_install_with_dfu()` / `probe_dfu()` | run | raise `FirmwareOperationError` |

The refusal is enforced twice on purpose. The CLI gate keeps the feature out of
`--help` and rejects the flags; the service-layer gate in `firmware.py` catches
library callers that never touch Click, so hiding a flag is never the only thing
standing between a stable build and the flash path.

Nothing here reads an environment variable. A channel gate that an env var can
flip is not a gate — the firmware side already learned that
`-D X=${sysenv.VAR}` fails *open* and quietly ships the gated thing. Installing
the optional `[py32]` extra does not enable the feature either; it only adds
pyusb.

AVR install paths are completely untouched by the gate, on both channels.

**To graduate the board to stable**, delete it from `BETA_ONLY_BOARDS` in
`firestarter/channel.py` — one tuple, one line — once the target is
bench-validated.

---

## 1. Why not avrdude

`avrdude` flashes AVR parts through an AVR bootloader. PY32F071 is a Cortex-M0+
with neither. The install path therefore forks on the board name:

| Board | Method | Tool |
|---|---|---|
| `uno`, `uno328pb`, `leonardo` | serial bootloader | `avrdude` (external binary) |
| `py32f071` | **USB DFU** | `firestarter/py32_dfu.py` (in-process, no external binary) |

The fork lives in `firestarter/firmware.py`:

```
manage_firmware_update()
  └── _install_firmware()          # dispatch on flash_method(board)
        ├── _install_with_avrdude()   # unchanged AVR path
        └── _install_with_dfu()       # py32_dfu.Py32DfuFlasher
```

`flash_method()` defaults unknown boards to `avrdude`, so adding another AVR
variant needs no change here.

### Dependencies

The DFU transfer itself is implemented in this repository — there is no
`dfu-util` to install, and Puya's own `PY32DfuTool` is Windows-only and unused.
What it does need is raw USB access:

```bash
pip install 'firestarter[py32]'      # adds pyusb
```

`pyusb` needs a libusb backend. On Linux that is usually already present, plus a
udev rule (or root) to reach the device. On Windows the DFU device needs a WinUSB
driver. This is the one place the "no external tools" goal is imperfect, and the
reason a self-flashing bootloader over the existing CDC + COBS transport remains
the longer-term direction — see the seed
`py32f071-no-external-tool-fw-install.md` in the planning repo.

---

## 2. Selecting the bootloader

PY32F071 holds a factory bootloader in **system memory**
(`0x1FFF0000`–`0x1FFF2F00`, USB on PA11/PA12). Which image the core runs out of
reset is decided by two boot inputs — `nBOOT1` (an option bit) and the `BOOT0`
pin (Puya UM1504, table 3-1):

| `nBOOT1` | `BOOT0` pin | Boot area |
|---|---|---|
| X | 0 | **Main flash** — the Firestarter application |
| 1 | 1 | **System memory** — the DFU bootloader ← *this is the one you want* |
| 0 | 1 | SRAM |

So bootloader entry is a **hardware condition**, not a host command. There are
three ways to satisfy it, in increasing order of convenience and of work
required.

### 2a. Strap BOOT0 and power-cycle (available first)

Pull `BOOT0` high — jumper, button or test pad — with `nBOOT1 = 1`, then reset or
replug. The board comes up in DFU mode, no serial port appears, and
`firestarter fw --install --board py32f071` can flash it.

This is what the first PCB revision must make possible. Strapping BOOT0 and
exposing SWD pads are the two hardware requirements the flash path imposes; they
are cheap on a schematic that does not exist yet and awkward to retrofit.

### 2b. Ask the running firmware to reboot into the bootloader (preferred)

A protocol command that makes the application jump to the system-memory
bootloader removes the button dance entirely: the host sends the command, the
board re-enumerates as a DFU device, and the install proceeds.

The app already has this shape. The Leonardo path does a 1200-baud touch
(`avr_tool.py::_trigger_reset`) to drop the board into its bootloader and then
flashes what re-appears as a *different USB device*. The DFU-class equivalent is
`DFU_DETACH`, which `Py32DfuFlasher.select_interface()` already sends when it
finds a device in DFU **runtime** mode (interface protocol `0x01`) instead of DFU
mode (`0x02`).

Two open items before this route works:

- The firmware must implement the jump (remap system memory and branch to the
  bootloader's reset vector). Whether the PY32F071 permits this in software —
  Cortex-M0+ `VTOR` or a `SYSCFG` `MEM_MODE`-style remap, as on STM32F0 — is
  **unconfirmed**.
- The firmware must expose it as a Firestarter command so the host can trigger it
  before switching to the DFU flasher.

### 2c. Own bootloader over the existing transport (long-term)

A Firestarter-specific bootloader in the first few KB of flash, speaking the same
USB CDC + COBS framing as the firmware, would remove the pyusb/libusb dependency
and the strap requirement together — the host would flash over the serial port it
already owns, exactly as the Uno does today. It is the most work and needs the
USB stack proven first, so it follows rather than replaces 2a/2b.

---

## 3. What the host does during an install

`Py32DfuFlasher.flash()` adapts to the device rather than assuming a dialect:

1. **Discover.** Scan for a USB interface with class `0xFE` / subclass `0x01`
   (DFU). Discovery is by interface class, *not* VID/PID, because the ID the Puya
   bootloader presents is not confirmed. `--usb-id VID:PID` narrows it.
2. **Detach if needed.** A device in DFU runtime mode is sent `DFU_DETACH` and the
   bus is rescanned.
3. **Read the geometry.** `wTransferSize` and `bcdDFUVersion` come from the DFU
   functional descriptor; erase-sector layout comes from the alt-setting mapping
   string (e.g. `@Internal Flash /0x08000000/64*002Kg`).
4. **Load the image.** `.bin` is taken raw at `0x08000000`; `.hex` is parsed as
   Intel HEX and carries its own address. Both are accepted because which asset
   the firmware release publishes is not settled.
5. **Refuse impossible writes.** An image that would fall outside
   `0x08000000`–`0x08020000` (128 KiB) is rejected before a single byte is sent.
6. **Transfer.**
   - *DfuSe* (`bcdDFUVersion 0x011A`, or an `@…` mapping string): erase each
     sector the image touches → set the address pointer → download blocks
     numbered from 2 → zero-length download to finish → leave.
   - *Plain DFU 1.1*: sequential blocks from 0, no erase, no address pointer.
     The load address is then the bootloader's business, not ours, and the host
     says so in a warning.
7. **Tolerate the reset.** After a successful leave, the device drops off the bus.
   USB errors from that point are expected and are not reported as failures.

Every step above is asserted in `tests/test_py32_dfu.py` against a fake USB
device that records control transfers — currently the only check that the
sequence is well-formed.

---

## 4. Command reference

```bash
# What is on the bus? Run this FIRST on new hardware.
firestarter fw --dfu-probe

# Install the latest firmware onto a board sitting in its DFU bootloader.
firestarter fw --install --board py32f071

# Pin a version, or narrow to one USB device.
firestarter fw --install --board py32f071 --firmware-version 3.0.0b12
firestarter fw --install --board py32f071 --usb-id 1a86:8012
```

Two behaviours differ from the AVR boards, both consequences of DFU mode having
no serial port:

- **`--board py32f071` is required, and must not conflict.** A board in DFU mode
  cannot answer the firmware-identity query, so it cannot be auto-detected and
  `--board` defaults to `uno`. If the port lookup fails while a device is in DFU
  mode, the app hints at the right invocation.

  A detected board normally wins over `--board`. That is fine when `--board` was
  left at its default and dangerous when it was typed: with an AVR programmer
  plugged in, `--install --board py32f071` used to retarget the AVR board and
  flash it. A typed `--board` that disagrees with the attached programmer is now
  refused — unplug the other board, or drop `--board`. (Found the hard way: that
  command flashed a live Leonardo during development.)
- **No version comparison is possible.** With no identity to read, there is
  nothing to compare against the release, so `--install` (or `--force`) is
  required — the app will not silently install over unknown firmware.

The release asset is `firestarter_py32f071.hex` — the same
`firestarter_<board>.hex` convention as the AVR boards, and the safer of the two
image formats for DFU because Intel HEX carries its own load address instead of
being assumed to start at `0x08000000`. A raw `firestarter_py32f071.bin` is also
accepted, which is what a local CMake build produces, so an unreleased image can
be flashed without converting it.

Publication is the remaining blocker on the firmware side. The PY32 workflow
builds correct binaries but uploads them as a GitHub **Actions artifact** — a ZIP
bundle, on a different API from releases, expiring after 90 days and requiring
auth. The host reads release *assets*. The fix is not to add a release step to
that workflow: `beta-build.yml` rewrites `include/version.h` and auto-commits it
before building, so an image built in any other job carries a stale version
string. The PY32F071 build has to move into that job, next to `pio run`. See
`platform/py32f071/README.md` § "Release integration" in the firmware repo.

---

## 5. First bench session

Two facts cannot be settled without hardware. Both fall out of one command.

```bash
firestarter fw --dfu-probe
```

Expected output shape:

```
Attached USB DFU devices:
  1a86:8012 alt 0 (DfuSe, transfer 1024 B, bcdDFUVersion 0x011A): @Internal Flash /0x08000000/64*002Kg [DFU mode]
      sectors: 64 x 2048 B @ 0x08000000
```

1. **The USB ID** the Puya bootloader presents. UM1504 lists `PID 0x0448` for
   PY32F071/F072, but that is a device ID in a bootloader-parameter table, not
   necessarily the USB product ID. Whatever `--dfu-probe` reports is the truth;
   record it and it can become a default.
2. **The dialect.** If `bcdDFUVersion` is `0x011A` and/or an `@…` mapping string
   appears, the DfuSe path is correct and the sector geometry is authoritative.
   If not, the plain DFU 1.1 path runs and the load address is whatever the
   bootloader decides — verify by reading the image back before trusting it.

Then, in order:

1. `--dfu-probe` with the board strapped into the bootloader (§2a).
2. `--install --force` with a known image, watching for the erase and block
   counts in the log at `-v`.
3. Power-cycle with `BOOT0` low and confirm the application runs — i.e. that a
   CDC port appears and `firestarter fw` reads back an identity of
   `py32f071`.
4. Only then consider the software reboot-to-bootloader command (§2b), which
   removes the strap step for every later update.

If step 2 or 3 fails, the board is still recoverable: strap `BOOT0` again, or fall
back to SWD. That recovery route is the reason both need to exist on the PCB.
