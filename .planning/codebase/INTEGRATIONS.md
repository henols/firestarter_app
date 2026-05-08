# External Integrations

**Analysis Date:** 2026-05-08

## APIs & External Services
**GitHub Releases API:**
- GitHub REST API — fetches latest firmware release metadata and downloads `.hex` firmware binaries
  - Endpoint: `https://api.github.com/repos/henols/firestarter/releases/latest` (defined in `firestarter/constants.py` as `FIRESTARTER_RELEASE_URL`)
  - Used in: `firestarter/firmware.py` (`FirmwareManager.fetch_latest_release_info`, `_download_firmware_file`)
  - SDK/Client: `requests` library (unauthenticated; public repo)
  - Auth: None — public API, no token required
  - Firmware assets downloaded by board variant name (e.g., `firestarter_uno.hex`, `firestarter_leonardo.hex`)

**PyPI:**
- Package published to PyPI as `firestarter`
  - Homepage: `https://github.com/henols/firestarter_app`
  - Auth: `PYPI_API_TOKEN` GitHub Actions secret (used only in CI publish workflow)

## Data Storage
**Databases:**
- No traditional database engine
- EPROM definitions stored as bundled JSON files in `firestarter/data/`:
  - `database_generated.json` — main machine-generated EPROM definitions
  - `database_overrides.json` — bundled override entries
  - `pin-maps.json` — pin mapping configurations for different IC packages
- Additional data files present on `new_database` branch (not yet bundled):
  - `firestarter/data/minipro_complete_db.json`
  - `firestarter/data/pinouts.json`
- `EpromDatabase` class in `firestarter/database.py` merges bundled data with user overrides at runtime (singleton pattern)

**File Storage:**
- Local filesystem only
- User config directory: `~/.firestarter/`
  - `config.json` — application settings (serial port preference, etc.)
  - `database.json` — user EPROM definition overrides
  - `pin-maps.json` — user pin map overrides
  - Downloaded firmware `.hex` files cached here (e.g., `~/.firestarter/firestarter_uno.hex`)

## Hardware Integration
**Serial Communication:**
- Protocol: Custom text-based protocol over serial UART at 250,000 baud (defined as `BAUD_RATE = "250000"` in `firestarter/constants.py`)
- Library: `pyserial` — `SerialCommunicator` class in `firestarter/serial_comm.py`
- Auto-discovery: scans available serial ports via `serial.tools.list_ports` to find the RURP programmer
- Connection stabilization delay: 2 seconds after opening port (`CONNECTION_STABILIZE_DELAY`)
- Commands sent as JSON payloads; responses parsed with prefix regex (`OK:`, `ERROR:`, `DATA:`, `INFO:`, etc.)
- Buffer sizes: 512 bytes (standard) / 1024 bytes (Leonardo variant), defined in `constants.py`
- Supported operations via protocol commands: READ, WRITE, ERASE, BLANK_CHECK, CHECK_CHIP_ID, VERIFY, READ_VPP, READ_VPE, FW_VERSION, CONFIG, HW_VERSION

**AVR Firmware Flashing:**
- Tool: `avrdude` (external system binary, located via `shutil.which`)
- Wrapped by `Avrdude` class in `firestarter/avr_tool.py`
- Supports `atmega328p` (Arduino Uno) and `atmega32u4` (Arduino Leonardo/Pro Micro)
- Leonardo reset trigger: opens port at 1200 baud then closes to force bootloader mode
- Avrdude versions <7.0 require explicit `avrdude.conf` config file path

**Target Hardware:**
- RURP (Relatively Universal ROM Programmer) — an Arduino shield
- Supported Arduino boards: Uno (atmega328p), Leonardo/Pro Micro (atmega32u4)
- Supports EPROM, EEPROM, Flash, and SRAM IC packages (24-pin, 28-pin, 32-pin DIP)

## CI/CD & Deployment
**Hosting:**
- PyPI — production package distribution
- GitHub Releases — firmware `.hex` binary hosting

**CI Pipeline:**
- GitHub Actions (`.github/workflows/`)
  - `release.yml` — triggers on push to `main` (ignoring docs/images/tools); auto-increments patch version in `firestarter/__init__.py` via `.github/scripts/update_version.py`, commits the change, then creates a GitHub Release with the new tag
    - Uses `stefanzweifel/git-auto-commit-action@v5` and `softprops/action-gh-release@v2`
    - Requires `PERSONAL_ACCESS_TOKEN` secret for release creation
  - `publish.yml` — triggers on GitHub Release published event; builds the Python package (`python -m build`) and publishes to PyPI
    - Uses `pypa/gh-action-pypi-publish@release/v1`
    - Requires `PYPI_API_TOKEN` secret

## Environment Configuration
**Required env vars (CI/CD only — no runtime env vars for the application):**
- `PYPI_API_TOKEN` — GitHub Actions secret; used by `publish.yml` to authenticate with PyPI
- `PERSONAL_ACCESS_TOKEN` — GitHub Actions secret; used by `release.yml` to push version commits and create releases

**Runtime configuration (file-based, not env vars):**
- `~/.firestarter/config.json` — persisted user preferences (serial port, etc.)

---
*Integration audit: 2026-05-08*
