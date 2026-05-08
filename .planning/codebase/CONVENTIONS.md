# Coding Conventions

**Analysis Date:** 2026-05-08

## Naming Patterns
**Files:** `snake_case.py` throughout (e.g., `eprom_operations.py`, `serial_comm.py`, `logging_utils.py`). Data files use `kebab-case.json` (e.g., `database_generated.json`, `pin-maps.json`).

**Functions:** `snake_case` for all functions and methods (e.g., `read_eprom`, `get_bus_config`, `build_flags`). Private/internal methods prefixed with `_` (e.g., `_setup_operation`, `_run_state_machine`, `_disconnect_programmer`).

**Variables:** `snake_case` throughout. Local variables are concise but descriptive (e.g., `eprom_data`, `command_dict`, `buffer_size`). Module-level constants use `UPPER_SNAKE_CASE` (e.g., `BUFFER_SIZE`, `COMMAND_READ`, `FLAG_FORCE`).

**Classes:** `PascalCase` (e.g., `EpromDatabase`, `EpromOperator`, `SerialCommunicator`, `ConfigManager`, `HardwareManager`). Custom exceptions follow the `<Domain>Error` pattern (e.g., `EpromOperationError`, `SerialTimeoutError`, `ProgrammerNotFoundError`, `HardwareOperationError`).

## Code Style
**Formatting:**
- Manual — no `black` or `ruff` configuration found in `pyproject.toml`
- 4-space indentation throughout
- f-strings used for all string interpolation
- Lines generally kept reasonable but some long method signatures are multi-line with hanging indent

**Linting:**
- No `.flake8`, `.pylintrc`, `pytest.ini`, or `tox.ini` present
- No linting configuration in `pyproject.toml`

## Import Organization
**Order:**
1. Standard library imports (alphabetically grouped: `os`, `json`, `logging`, `time`, `re`, etc.)
2. Third-party imports (`serial`, `tqdm`, `rich`, `requests`, `argcomplete`)
3. Local `firestarter.*` imports

Within each group, imports are not strictly sorted but related imports are grouped. Star imports (`from firestarter.constants import *`) are used for the constants module throughout the codebase.

## Type Hints
**Usage:** Consistently used on public and private method signatures in newer/refactored modules (`eprom_operations.py`, `serial_comm.py`, `hardware.py`, `firmware.py`, `ic_layout.py`). Uses `typing` module imports (`Optional`, `Tuple`, `Dict`, `List`, `Callable`) rather than the newer built-in generics syntax. The `dict` type is used bare for `eprom_data_dict` parameters. Not all modules use type hints uniformly — `config.py` uses `Optional`, while `database.py` uses them only on some methods.

## Error Handling
**Patterns:**
- Domain-specific exception classes defined per module: `EpromOperationError`, `SerialError`, `SerialTimeoutError`, `ProgrammerNotFoundError`, `FirmwareOutdatedError`, `HardwareOperationError`, `AvrdudeNotFoundError`
- Low-level I/O errors (`json.JSONDecodeError`, `IOError`, `OSError`) are caught at the point of occurrence; errors are logged via `logger.error(...)` and the function returns a safe default (`None`, `{}`, or `False`)
- Hardware/serial errors propagate as custom exceptions, caught at the operation boundary in public API methods
- `try/except/finally` used in operation methods to ensure `_disconnect_programmer()` is called on all paths
- `ValueError` and `TypeError` caught silently (with `pass`) where input parsing may fail non-critically (e.g., progress string parsing)
- Functions return `bool` or `Optional[...]` to signal success/failure rather than raising exceptions to callers

## Logging
**Framework:** Standard library `logging` module with a custom `SingleLineStatusHandler` defined in `firestarter/logging_utils.py`.

**Patterns:**
- Each module creates a module-level logger with a human-readable name: `logger = logging.getLogger("ModuleName")` (e.g., `"Database"`, `"EpromOperator"`, `"SerialComm"`, `"Firmware"`)
- `logger.debug(...)` for detailed diagnostic information (operation setup, data dumps, timing)
- `logger.info(...)` for user-facing operation progress and results (start/completion messages, EPROM info display)
- `logger.warning(...)` for non-fatal issues (missing pin maps, unmatched chip IDs, programmer warnings)
- `logger.error(...)` for failures that cause an operation to abort (file not found, EPROM not in database, serial errors)
- Timing messages use `f"...({time.time() - start_time:.2f}s)"` pattern
- `tqdm.contrib.logging.logging_redirect_tqdm` is used within progress-heavy operations to prevent tqdm/logging interference

## Comments & Docstrings
**When to Comment:**
- Inline comments explain non-obvious logic, hardware register meanings, and protocol decisions
- Section dividers use `# --- Section Name ---` in longer methods
- Commented-out code is present in several places (especially `database.py`), indicating active development

**Docstrings:**
- All modules have a header docstring with project name, copyright year, license, and a brief description of the module's role
- Classes have docstrings explaining their purpose and design patterns (e.g., singleton, factory)
- Public methods have docstrings with `Args:` and `Returns:` sections in some modules (`config.py`, `utils.py`, `database.py`)
- Private helper methods use brief single-line docstrings or none at all
- Format: triple double-quotes (`"""`) throughout

## Function Design
**Size:** Methods are medium-length; complex orchestration methods (`_run_state_machine`, `_setup_operation`) run 30–50 lines. Public API methods (`read_eprom`, `write_eprom`) are compact, delegating to context managers and state machine handlers.

**Parameters:** Public methods consistently accept `eprom_name: str`, `eprom_data_dict: dict`, `operation_flags: int = 0`, and optional `address_str: Optional[str] = None`. This pattern is uniform across all EPROM operation methods.

**Return Values:** Operations return `bool` for success/failure. Methods that also need to return data return `Tuple[bool, Optional[T]]` (e.g., `check_eprom_id` returns `Tuple[bool, Optional[int]]`). Database queries return `Optional[dict]` or `list`.

---
*Convention analysis: 2026-05-08*
