# Changelog

All notable changes to the Firestarter CLI.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**The CLI and the firmware ship as a pair** and share a version number. An entry
here that changes the wire protocol changes both. The subset of changes that
need you to do something when upgrading — with the table of which mixed CLI and
firmware pairings work — is on the wiki at
[Breaking Changes](https://github.com/henols/firestarter/wiki/Breaking-Changes).

Releases before 3.0 are not listed here. This file starts at the 3.x line.

## [Unreleased]

### Added

- `firestarter fw` refuses to install a firmware release whose version is newer
  than the CLI, instead of installing it and leaving a board the CLI cannot
  talk to. The comparison is on the first two version numbers, so patch
  releases install normally and `3.1.0b2` and `3.1.0b5` count as the same
  version. `--allow-newer-firmware` installs anyway; that pairing is not
  tested. A version that cannot be read on either side is refused.
- `firestarter fw --list` is unaffected and still enumerates every release,
  including ones too new to install, so there is always a way to find out which
  version to pin with `--firmware-version`.

## [3.1.0b2] - 2026-09-25

### Added

- `firestarter info` draws the jumper headers for the chip's pin map and marks
  the jumper where one is needed. The VPP and erase lines come from measured
  behaviour rather than from the silkscreen alone.

## [3.1.0b1] - 2026-09-24

### Changed

- **`verify` and `blank` now run on the host.** Both read the chip with an
  ordinary read and compare on your computer. The firmware's verify command
  (ordinal 6) and blank-check command (ordinal 4) are removed, and neither
  number will be reused.
- **The blank check before a write moved to the CLI**, from the firmware.
  `write -b` still means "skip that check", and the erase still runs.

### Removed

- The skip-blank-check control flag (`0x08`) is retired from the wire.

> Mixed CLI and firmware pairings across this change behave differently
> depending on the direction. See
> [Breaking Changes](https://github.com/henols/firestarter/wiki/Breaking-Changes)
> before upgrading only one side.
