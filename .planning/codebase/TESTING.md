# Testing Patterns

**Analysis Date:** 2026-05-08

## Test Framework
**Runner:**
- No Python test framework (pytest, unittest) is configured or present
- No `tests/` directory, no `test_*.py` or `*_test.py` files exist
- No `pytest.ini`, `tox.ini`, or `[tool.pytest]` section in `pyproject.toml`

**Run Commands:**
```bash
./firestarter_test.sh [EPROM_NAME]   # Full hardware integration test suite (default: W27C512)
./write_test.sh [EPROM_NAME]          # Focused write/read/verify test with multiple data patterns
```

## Test File Organization
**Location:** Project root as shell scripts (`firestarter_test.sh`, `write_test.sh`)

**Naming:** `<purpose>_test.sh` pattern. Temporary test data written to `./test_data/` during runs.

## Test Structure
**Patterns:**
- Each test invocation is wrapped in `exec_firestarter()` — a helper function that runs a `firestarter` CLI command and exits the script with code 1 if the command returns non-zero
- Test output prints a separator banner, the test name, and the full command line before running
- `sleep 0.5` added between hardware operations for hardware stabilization
- Data integrity verified using `xxd` hex dumps piped to `diff --suppress-common-lines -y`
- `firestarter_test.sh` uses a `trap ... EXIT` to clean up `./test_data/` on script exit or interrupt when `CLEAN_UP=1`

## Test Types

**Unit Tests:**
- None. There are no isolated unit tests for Python modules.
- Some modules contain a `if __name__ == "__main__":` block for ad-hoc manual testing (e.g., `database.py`, `config.py`, `utils.py`). These are demonstration/debug code, not automated tests.

**Integration Tests (Shell-based):**

`firestarter_test.sh` — full end-to-end test suite, organized in sections:

1. **Firmware Tests** — runs `firestarter fw` to verify firmware communication
2. **Hardware Tests** — runs `firestarter hw`, `config`, `vpp -t 5`, `vpe -t 5`
3. **EPROM Tests** — reads EPROM metadata from `firestarter/data/database_generated.json` via `jq`, generates random binary test data using `dd`/`/dev/urandom`, then:
   - Chip ID check (if `has-chip-id == true`)
   - Write full random data
   - Verify written data
   - Read back data
   - Binary diff of original vs. read-back via `xxd` | `colordiff`
   - Erase (if `can-erase == true`)
   - Blank check
4. **Info Tests** — runs `firestarter list`, `search <name>`, `info <name>`

`write_test.sh` — focused write cycle test with multiple data patterns:

- Generates four test files: null bytes, `0xFF` bytes, random data (full size), partial low/high halves
- Runs `read_write_test()` for each: write → verify → read back → diff
- Tests partial address writes using `-b -a <offset>` flags
- Tests split write (low half, then high half at offset) then reads back the full combined result

**External Tool Dependencies for Tests:**
- `jq` — JSON parsing to extract EPROM metadata from database
- `dd` — binary test data generation
- `xxd` — hex dump for byte-level diff
- `diff` / `colordiff` — file comparison
- `firestarter` CLI — must be installed and on PATH (via `pip install -e .`)
- Physical hardware — Arduino RURP shield with target EPROM inserted

## Coverage
**Requirements:** None enforced. No coverage tooling configured.

## Common Patterns

**EPROM name normalization:** Input EPROM names are uppercased with `tr '[:lower:]' '[:upper:]'` before use.

**Test data sizing:** Memory size is read from the JSON database as hex (e.g., `0x10000`) and converted to decimal with bash arithmetic `$((MEMORY_SIZE_HEX))`. Half-size files are generated for split-write tests.

**Conditional test sections:** Boolean flags at the top of `firestarter_test.sh` (`FIRMWARE_TESTS`, `HARDWARE_TESTS`, `EPROM_TESTS`, `INFO_TESTS`) allow skipping sections. `ONLY_EPROM_TESTS=1` disables everything except EPROM operations.

**Verbose mode:** Setting `VERBOSE=1` in the script passes `--verbose` to all `firestarter` invocations.

**Adding a new hardware operation test:** Add a call to `exec_firestarter "Test Name" <command> [eprom] [file] [extra_args]` in the relevant section of `firestarter_test.sh`.

---
*Testing analysis: 2026-05-08*
