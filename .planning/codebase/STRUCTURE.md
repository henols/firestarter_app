# Codebase Structure

**Analysis Date:** 2026-05-08

## Directory Layout
```
firestarter_app/
├── firestarter/              # Main Python package
│   ├── __init__.py           # Package version string (__version__)
│   ├── main.py               # CLI entry point; argument parsing; command dispatch
│   ├── constants.py          # All numeric constants (commands, flags, baud rate, buffer size)
│   ├── config.py             # ConfigManager singleton; user config file at ~/.firestarter/config.json
│   ├── database.py           # EpromDatabase singleton; pin_conversions table; DB merging
│   ├── eprom_operations.py   # EpromOperator; state machine; read/write/verify/erase/id
│   ├── hardware.py           # HardwareManager; VPP/VPE voltage reading; hw rev; config command
│   ├── firmware.py           # FirmwareManager; version check; GitHub download; avrdude flashing
│   ├── serial_comm.py        # SerialCommunicator; port discovery; JSON protocol; data blocks
│   ├── eprom_info.py         # EpromConsolePresenter; detail display; list table
│   ├── ic_layout.py          # EpromSpecBuilder; DIP layout; jumper/protocol/flag interpretation
│   ├── avr_tool.py           # Avrdude subprocess wrapper
│   ├── logging_utils.py      # SingleLineStatusHandler (carriage-return status lines)
│   ├── utils.py              # Pure utility functions (hex parsing, size/time formatting)
│   └── data/
│       ├── minipro_complete_db.json    # Primary EPROM definitions database (active)
│       ├── pinouts.json                # Pin map configurations keyed by pinout variant name
│       ├── database_generated.json     # Legacy generated database (not loaded by default)
│       └── database_overrides.json     # Legacy override file (not loaded by default)
├── tools/                    # Developer/maintenance scripts (not part of the installed package)
│   ├── parse_db.py           # Database parse/conversion tool
│   ├── parse_db_2.py         # Alternate database parse tool
│   ├── infoic2.xml           # Source XML for database generation
│   ├── infoic.xml            # Alternate source XML
│   ├── pin-layouts.odt       # Pin layout documentation
│   └── verified.txt          # List of verified EPROM names
├── .planning/
│   └── codebase/             # Architecture and structure documentation
├── .github/
│   ├── workflows/            # CI/CD workflow definitions
│   └── scripts/              # GitHub Actions helper scripts
├── doc/                      # User-facing documentation
├── images/                   # README images
├── pyproject.toml            # Build config; dependencies; entry point declaration
├── requirements.txt          # Development dependencies
├── firestarter_test.sh       # Full hardware test suite
├── write_test.sh             # Focused write/read/verify test
└── CLAUDE.md                 # Claude Code project instructions
```

---

## Directory Purposes

**`firestarter/` (package root):**
- Purpose: All application source code
- Contains: Python modules, one sub-directory (`data/`)
- Key files: `main.py` (entry point), `constants.py` (shared constants)

**`firestarter/data/`:**
- Purpose: Bundled data files shipped with the package
- Contains: JSON database files, JSON pin-map files
- Key files: `minipro_complete_db.json` (active EPROM DB), `pinouts.json` (active pin maps)
- Note: Files declared in `pyproject.toml` under `[tool.setuptools.package-data]`; the legacy `database_generated.json` and `database_overrides.json` are still shipped but not loaded by current code

**`tools/`:**
- Purpose: Developer utilities for maintaining and regenerating the EPROM database
- Contains: Python scripts and XML source data; not installed as part of the package

**`~/.firestarter/` (runtime, not in repo):**
- Purpose: User-specific runtime data created at first run
- Contains: `config.json` (saved serial port, avrdude paths), `database.json` (user EPROM overrides), `pin-maps.json` (user pin-map overrides), downloaded firmware `.hex` files

---

## Key File Locations

**Entry Points:**
- `firestarter/main.py`: `main()` function; registered as `firestarter` console script in `pyproject.toml`
- `firestarter/__init__.py`: `__version__` string; source of truth for version (read by `setuptools_scm`)

**Configuration:**
- `pyproject.toml`: Build system, dependencies, entry point, package-data inclusions
- `firestarter/constants.py`: All shared numeric constants; import with `from firestarter.constants import *`
- `firestarter/config.py`: `ConfigManager` singleton; user config file path `~/.firestarter/config.json`

**Core Logic:**
- `firestarter/database.py`: `EpromDatabase` singleton; `_map_data()` transformation; `convert_to_programmer()` projection; `pin_conversions` table
- `firestarter/eprom_operations.py`: `EpromOperator`; all EPROM read/write/verify/erase operations; state machine driver
- `firestarter/serial_comm.py`: `SerialCommunicator`; `find_and_connect()` factory; response parser; `read_data_block()` with checksum

**Data Files:**
- `firestarter/data/minipro_complete_db.json`: Primary EPROM definitions, keyed by manufacturer
- `firestarter/data/pinouts.json`: Pin map variants keyed by variant name (e.g., `"DIP28_27512"`)
- `~/.firestarter/database.json`: Optional user additions/overrides (merged at startup)
- `~/.firestarter/pin-maps.json`: Optional user pin-map additions/overrides (merged at startup)

**Tests / Scripts:**
- `firestarter_test.sh`: Bash integration test suite; requires live hardware
- `write_test.sh`: Focused write-path test; requires live hardware

---

## Naming Conventions

**Files:** `snake_case.py`; data files use `kebab-case.json`

**Classes:** `PascalCase`; manager classes named `<Domain>Manager` (e.g., `FirmwareManager`, `HardwareManager`); singleton databases named `<Domain>Database`; presenters named `<Domain>Presenter`; builders named `<Domain>Builder`

**Exceptions:** `PascalCase` with `Error` suffix, inheriting from the most specific built-in (e.g., `AvrdudeNotFoundError(FileNotFoundError)`)

**Constants:** `UPPER_SNAKE_CASE`; command codes prefixed `COMMAND_`; flag bits prefixed `FLAG_`

**Logger names:** Match the class being logged, passed as a string to `logging.getLogger()` (e.g., `"EpromOperator"`, `"SerialComm"`)

**JSON database keys:** `kebab-case` for hardware-facing fields (`"memory-size"`, `"pin-count"`, `"bus-config"`); `snake_case` for internal DB fields (`"part_number"`, `"chip_id_value"`)

---

## Where to Add New Code

**New EPROM command (hardware operation):**
- Add constant to `firestarter/constants.py`: `COMMAND_<NAME> = <next int>`
- Add public method to `EpromOperator` in `firestarter/eprom_operations.py` following the `_operation_context` + `_run_state_machine` pattern
- Add CLI subparser creator `create_<name>_args()` in `firestarter/main.py`
- Add dispatch branch in `main()` in `firestarter/main.py`

**New hardware utility command (voltage, config, revision):**
- Add constant to `firestarter/constants.py` if a new command code is needed
- Add method to `HardwareManager` in `firestarter/hardware.py` using `_execute_simple_command()` or `_read_voltage_loop()`
- Add CLI subparser and dispatch branch in `firestarter/main.py`

**New EPROM definition:**
- Edit `firestarter/data/minipro_complete_db.json` (or user's `~/.firestarter/database.json` for local overrides)
- If a new pin-map variant is needed, add it to `firestarter/data/pinouts.json`

**New configuration key:**
- Add any new constant/default in `firestarter/config.py`
- Use `config_manager.get_value("key")` / `config_manager.set_value("key", value)` in the consuming module

**New display feature for EPROM info:**
- Extend `EpromSpecBuilder.build_specifications()` in `firestarter/ic_layout.py` to add data to the output dict
- Extend `EpromConsolePresenter.present_eprom_details()` in `firestarter/eprom_info.py` to render the new field

**New utility function:**
- Add to `firestarter/utils.py` if pure (no I/O, no imports from other firestarter modules)

**New database maintenance tool:**
- Add script to `tools/` directory; it is not part of the installed package

---

*Structure analysis: 2026-05-08*
