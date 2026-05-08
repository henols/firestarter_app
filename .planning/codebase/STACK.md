# Technology Stack

**Analysis Date:** 2026-05-08

## Languages
**Primary:**
- Python 3.9+ - All application logic (enforced via `requires-python = ">=3.9"` in `pyproject.toml`)
- Bash - Test scripts (`firestarter_test.sh`, `write_test.sh`)

## Runtime
**Environment:**
- Python 3.13.5 (current dev machine runtime; min supported is 3.9)
- OS Independent (classifiers confirm Linux/macOS/Windows support)

**Package Manager:**
- pip / setuptools
- Lockfile: absent (only `requirements.txt` with bare package names, no pinned versions)

## Frameworks
**Core:**
- None (plain Python with argparse for CLI; no web or async framework)

**Testing:**
- Bash-based integration tests (`firestarter_test.sh`, `write_test.sh`) — require physical hardware
- No Python unit test framework (pytest/unittest) present

**Build/Dev:**
- `setuptools>=45` — package build backend (`pyproject.toml`)
- `setuptools_scm>=6.2` — version derived from git tags; version attribute in `firestarter/__init__.py`
- `python -m build` — used in CI to produce distribution artifacts

## Key Dependencies
**Critical:**
- `pyserial>=3.5` — all hardware communication; used in `serial_comm.py` and `avr_tool.py`
- `requests>=2.20` — GitHub API calls and firmware binary downloads in `firmware.py`
- `rich>=14.0` — terminal UI (tables, prompts, status output) throughout `main.py`, `firmware.py`, `eprom_info.py`
- `tqdm>=4.60` — progress bars during read/write operations in `eprom_operations.py`
- `argcomplete>=3.6.2` — bash tab-completion for the `firestarter` CLI in `main.py`

**Infrastructure:**
- `avrdude` (external system binary) — required at runtime to flash firmware to Arduino; located via `shutil.which` in `avr_tool.py`

## Configuration
**Environment:**
- No environment variables used by the application itself
- User config stored in `~/.firestarter/config.json` (JSON, managed by `ConfigManager` singleton in `config.py`)
- User EPROM database overrides: `~/.firestarter/database.json`
- User pin-map overrides: `~/.firestarter/pin-maps.json`

**Build:**
- `pyproject.toml` — single source of truth for build, metadata, and dependencies
- `MANIFEST.in` — controls extra files included in sdist
- `firestarter/__init__.py` — version string (`__version__`) read by setuptools_scm and displayed via `--version`

## Platform Requirements
**Development:**
- Python 3.9+
- `avrdude` installed and on `PATH` (for firmware install subcommand only)
- Physical RURP Arduino shield connected via USB serial for any hardware tests
- `xxd` and `diff` available for bash test scripts

**Production:**
- Distributed via PyPI as the `firestarter` package
- Entry point binary: `firestarter` (maps to `firestarter.main:main`)
- Requires USB serial access (typically `/dev/ttyUSB*` or `/dev/ttyACM*` on Linux, `COM*` on Windows)

---
*Stack analysis: 2026-05-08*
