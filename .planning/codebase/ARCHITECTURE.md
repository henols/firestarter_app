# Architecture

**Analysis Date:** 2026-05-08

## Pattern Overview
**Overall:** Layered CLI application with command-dispatch, singleton services, and a state-machine hardware protocol

**Key Characteristics:**
- Strict separation between CLI parsing (`main.py`), business logic (operation managers), and hardware I/O (`serial_comm.py`)
- Two singleton services (`EpromDatabase`, `ConfigManager`) initialized once at startup and shared across all components
- All hardware communication goes through a single `SerialCommunicator` abstraction; no module talks to serial directly except through it
- A pull-based, three-phase state machine (INIT / MAIN / END) governs every EPROM operation on the wire
- EPROM data flows through a deliberate two-step conversion: raw DB record -> fully mapped dict -> concise programmer dict

---

## Layers

**CLI / Entry Layer:**
- Purpose: Parse arguments, configure logging, instantiate services, dispatch to the right manager method
- Location: `firestarter/main.py`
- Contains: `argparse` subcommand definitions, flag assembly helper `build_arg_flags()`, command dispatch if-elif chain
- Depends on: all manager classes, `EpromDatabase`, `ConfigManager`, `eprom_info`
- Used by: setuptools entry point `firestarter = "firestarter.main:main"`

**Configuration Layer:**
- Purpose: Persist and retrieve runtime config (serial port, avrdude path, resistor values, hardware rev override)
- Location: `firestarter/config.py`
- Contains: `ConfigManager` singleton (per config file), module-level helpers `get_local_database()` / `get_local_pin_maps()`
- Depends on: stdlib `os`, `json`
- Used by: every manager class; `EpromDatabase` (for user override loading)

**Database Layer:**
- Purpose: Load, merge, and query EPROM definitions and pin-map configurations; translate generic pin numbers to RURP hardware lines
- Location: `firestarter/database.py`
- Contains: `EpromDatabase` singleton; pin-conversion lookup table `pin_conversions` (keyed by DIP pin-count 24/28/32); `_map_data()` transformation; `convert_to_programmer()` projection
- Depends on: `firestarter/data/minipro_complete_db.json`, `firestarter/data/pinouts.json`, user overrides from `~/.firestarter/`
- Used by: `main.py`, `eprom_info.py`, `ic_layout.py`

**Operations Layer:**
- Purpose: Orchestrate multi-step EPROM operations (read, write, verify, erase, blank-check, chip-ID) and developer diagnostics
- Location: `firestarter/eprom_operations.py`
- Contains: `EpromOperator`; context manager `_operation_context()`; unified state machine `_run_state_machine()`; phase handlers `_main_phase_read_data()`, `_main_phase_send_data()`, `_main_phase_simple()`; progress tracking via `ClassProgressHandler` / `tqdm`
- Depends on: `serial_comm.py`, `config.py`, `constants.py`, `utils.py`
- Used by: `main.py`

**Hardware Management Layer:**
- Purpose: Non-EPROM hardware commands: read VPP/VPE voltages, get/set hardware revision, configure resistor values
- Location: `firestarter/hardware.py`
- Contains: `HardwareManager`; simple command executor `_execute_simple_command()`; continuous voltage-reading loop `_read_voltage_loop()`
- Depends on: `serial_comm.py`, `config.py`, `constants.py`
- Used by: `main.py`

**Firmware Management Layer:**
- Purpose: Check firmware version on device, fetch latest release from GitHub, download and flash via avrdude
- Location: `firestarter/firmware.py`
- Contains: `FirmwareManager`; `check_current_firmware()`, `fetch_latest_release_info()`, `_download_firmware_file()`, `_install_with_avrdude()`, `manage_firmware_update()` orchestrator
- Depends on: `serial_comm.py`, `avr_tool.py`, `config.py`, `constants.py`, `requests`, `rich`
- Used by: `main.py`

**Serial Communication Layer:**
- Purpose: All raw serial I/O; port discovery; JSON command serialisation; response parsing and checksum validation
- Location: `firestarter/serial_comm.py`
- Contains: `SerialCommunicator`; `find_and_connect()` class method; `_probe_port()`; `_read_and_parse_lines()` generator; `read_data_block()` with XOR checksum; `Response` namedtuple
- Depends on: `pyserial`, `constants.py`, `config.py`
- Used by: `eprom_operations.py`, `hardware.py`, `firmware.py`

**Display / Info Layer:**
- Purpose: Build human-readable EPROM detail structures and render them to the console; generate DIP package pin diagrams
- Location: `firestarter/eprom_info.py`, `firestarter/ic_layout.py`
- Contains: `EpromConsolePresenter` (data assembly + console rendering); `EpromSpecBuilder` (specifications dict builder, DIP layout, jumper settings, protocol/flag interpretation); `print_eprom_list_table()` helper
- Depends on: `database.py`
- Used by: `main.py`

**Support Modules:**
- `firestarter/avr_tool.py` — `Avrdude` wrapper; subprocess execution; Leonardo reset trigger
- `firestarter/utils.py` — pure utility functions (`extract_hex_to_decimal`, `format_size`, `time_formatter`)
- `firestarter/logging_utils.py` — `SingleLineStatusHandler` for in-place status line updates
- `firestarter/constants.py` — all numeric constants (command codes, flag bits, baud rate, buffer sizes)

---

## Data Flow

**EPROM Read Operation:**
1. `main.py` parses args; calls `db_instance.get_eprom(name)` -> full mapped dict
2. `db_instance.convert_to_programmer(full_dict)` -> concise programmer dict (millivolt VPP, bus config, flags)
3. `eprom_operator.read_eprom(name, programmer_dict, ...)` invoked
4. `_operation_context` calls `_setup_operation`: copies dict, sets `cmd=COMMAND_READ`, opens `SerialCommunicator.find_and_connect()`
5. `find_and_connect` probes ports, sends JSON command, validates firmware version via `expect_ack()`
6. `_run_state_machine` drives INIT -> MAIN -> END phases; for MAIN, `_main_phase_read_data` callback streams incoming `DATA` blocks, calls `read_data_block()` (length-prefix + XOR checksum), writes to output file
7. Context manager teardown calls `disconnect()` on `SerialCommunicator`

**EPROM Write Operation:**
1. Same DB lookup + conversion as read
2. `_main_phase_send_data` implements pull protocol: Arduino sends `OK` requesting next chunk; host reads `BUFFER_SIZE` bytes, builds `# + len(2B) + checksum(1B)` header, sends header, waits for header ACK, sends data bytes
3. Sends `DONE` string when file exhausted

**State Management:**
- `EpromDatabase._instance` / `._initialized` — classic singleton; database loaded once per process
- `ConfigManager._instances` — dict-keyed singleton (per config file path); persists serial port after first successful connection
- `SerialCommunicator` instance is created per-operation and destroyed in the context manager's `finally` block; no persistent connection between commands

---

## Key Abstractions

**EpromDatabase (Singleton):**
- Purpose: Central store for all EPROM and pin-map definitions; translates raw JSON into hardware-usable structs
- Examples: `firestarter/database.py`
- Pattern: Singleton via `__new__` + class-level `_initialized` flag; `_map_data()` acts as a data-mapping factory

**EpromOperator (Context Manager + State Machine):**
- Purpose: Encapsulates the lifecycle and protocol of a single hardware operation
- Examples: `firestarter/eprom_operations.py`
- Pattern: `@contextmanager` for setup/teardown; strategy pattern for swappable `main_phase_handler` callbacks

**SerialCommunicator (Port Discovery + Protocol):**
- Purpose: Abstracts away serial port differences; enforces structured response parsing
- Examples: `firestarter/serial_comm.py`
- Pattern: Static factory method `find_and_connect()`; `Response` namedtuple for typed messages; generator `_read_and_parse_lines()` for timeout-safe reading

**pin_conversions Table:**
- Purpose: Bridges generic DIP pin numbers (24/28/32-pin) to RURP internal address/control line numbers
- Examples: `firestarter/database.py` (module-level dict), used in `get_bus_config()`
- Pattern: Lookup table; special sentinel values `ROM_CE = 0x100`, `ROM_OE = 0x101`

---

## Entry Points

**CLI Entry Point:**
- Location: `firestarter/main.py` -> `main()`
- Triggers: `firestarter` console script (via pyproject.toml), or `python -m firestarter.main`
- Responsibilities: Signal handler setup, argument parsing, singleton initialization, logging setup, command dispatch

**Module Test Mains:**
- Location: `if __name__ == "__main__":` blocks in `database.py`, `eprom_info.py`, `ic_layout.py`, `serial_comm.py`, `avr_tool.py`
- Triggers: Direct `python firestarter/<module>.py` execution
- Responsibilities: Standalone smoke-testing of individual modules

---

## Error Handling

**Strategy:** Log-and-return-False. Public API methods return `bool` (or `Tuple[bool, ...]`) rather than raising. Exceptions are caught at the boundary of each layer and converted to `logger.error()` calls.

**Patterns:**
- `SerialError`, `SerialTimeoutError`, `ProgrammerNotFoundError`, `FirmwareOutdatedError` — raised within `serial_comm.py`, caught in operation/hardware/firmware managers
- `EpromOperationError` — raised within `_run_state_machine` phases on `ERROR:` responses from programmer; caught in `_run_state_machine` and returned as `(False, message)`
- `HardwareOperationError`, `FirmwareOperationError` — defined but seldom raised directly; serial exceptions propagate up instead
- `AvrdudeNotFoundError`, `AvrdudeConfigNotFoundError` — raised in `avr_tool.py`, caught in `firmware.py`
- File I/O errors in operations are caught as `IOError` and logged; function returns `False`
- `KeyboardInterrupt` in voltage-reading loop results in a graceful `True` return (user stop is not an error)

---

## Cross-Cutting Concerns

**Logging:**
- Each module creates its own named logger: `logging.getLogger("EpromOperator")`, `logging.getLogger("SerialComm")`, etc.
- Root logger configured in `main()` with a single `SingleLineStatusHandler` (`firestarter/logging_utils.py`) that supports carriage-return status-line overwriting via `extra={"status": "start"|"end"}`
- Verbose mode (`-v`) switches format to `levelname:name:lineno: message` and sets level to DEBUG; normal mode uses bare `%(message)s` at INFO
- RURP firmware feedback logged via a separate `rurp_logger = logging.getLogger("RURP")` in `serial_comm.py`

**Validation:**
- EPROM name lookup in `main.py` validates existence before calling any operator; unknown names produce `logger.error` + return 1
- Numeric argument parsing (address, size) validated in `_setup_operation()`; hex strings accepted with `0x` prefix
- Firmware version validated by `SerialCommunicator._is_version_sufficient()` during every `find_and_connect()` call (except `COMMAND_FW_VERSION`)
- JSON files loaded with `try/except json.JSONDecodeError`; missing files produce empty dicts rather than crashes

---

*Architecture analysis: 2026-05-08*
