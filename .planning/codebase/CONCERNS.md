# Codebase Concerns

**Analysis Date:** 2026-05-08

---

## Tech Debt

**Database format migration in progress:**
- Issue: The codebase is mid-migration from `database_generated.json` (old format, field names like `"name"`, `"chip-id"`, `"flags"`) to `minipro_complete_db.json` (new format, fields like `"part_number"`, `"electrical"`, `"programming"`). The old `database_overrides.json` override system is hard-commented-out with `# //_read_config_file("database_overrides.json")`. The merge logic in `_merge_databases()` contains inline notes that it may not work correctly for the new format.
- Files: `firestarter/database.py` (lines 170, 182, 208, 213), `firestarter/data/database_generated.json`, `firestarter/data/minipro_complete_db.json`
- Impact: The override/customisation workflow (documented as `~/.firestarter/database.json`) is silently broken. Users cannot add custom EPROMs in the documented way.
- Fix approach: Decide on the final format, update `_merge_databases()` to fully support it, re-enable overrides, and remove the old database files once the migration is confirmed stable.

**`pulse-delay` field always zero:**
- Issue: `_map_data()` hardcodes `"pulse-delay": 0` because the new database format encodes the pulse delay as a human-readable string rather than an integer, and parsing has not been implemented.
- Files: `firestarter/database.py` (line 346)
- Impact: EPROMs that require a non-zero pulse delay will be programmed with the wrong timing, potentially producing write failures or silent data corruption on certain chips.
- Fix approach: Parse the pulse delay string in `_map_data()` and populate the field correctly.

**`verified` flag logic in new database:**
- Issue: The new `minipro_complete_db.json` format does not include a `"verified"` field. The `search_eprom()` docstring acknowledges this: `'verified' is not in new DB, needs adding or logic change`. The `get_eproms(verified=True)` filter therefore silently returns an empty list.
- Files: `firestarter/database.py` (line 478)
- Impact: The `firestarter list --verified` command shows nothing; users cannot filter to only tested EPROMs.
- Fix approach: Either add a `"verified"` field to the new database schema or remove the filter until the field is available.

**Simplified type determination in `_map_data()`:**
- Issue: `_map_data()` only distinguishes `Flash` (type 2) and `SRAM` (type 4) by checking if the type string contains those words. The previous schema had finer-grained flash types (types 2 and 3). A comment marks this as "Simplified type determination".
- Files: `firestarter/database.py` (line 316–321)
- Impact: Flash chips that previously used type 3 may be sent the wrong protocol command to the firmware.
- Fix approach: Map all flash sub-types from the new `electrical.type` strings to their exact integer codes.

**`info-flags` derivation is incomplete:**
- Issue: The `info_flags` value assembled in `_map_data()` only sets two bits (`0x20` for chip ID check, `0x10` for Flash/EEPROM erasability). All other flag bits from the old format are lost.
- Files: `firestarter/database.py` (lines 332–337)
- Impact: Programmer behaviour controlled by other flag bits (boot-block, sector sizes, etc.) will be incorrect for chips that needed them.
- Fix approach: Audit all flag bits used by the firmware and map them from fields present in the new database format.

**New data files missing from `pyproject.toml` package-data:**
- Issue: `database.py` loads `minipro_complete_db.json` and `pinouts.json`, but `pyproject.toml` declares only `database_generated.json`, `database_overrides.json`, and `pin-maps.json` as package data. The new files will be absent from an installed wheel.
- Files: `pyproject.toml`, `firestarter/database.py`
- Impact: `pip install firestarter` produces a broken installation; `EpromDatabase()` silently initialises with an empty `proms` dict and all EPROM lookups fail.
- Fix approach: Add `data/minipro_complete_db.json` and `data/pinouts.json` to the `[tool.setuptools.package-data]` list and remove the obsolete entries when the old files are retired.

**`LEONARDO_BUFFER_SIZE` constant is defined but unused:**
- Issue: `constants.py` defines `LEONARDO_BUFFER_SIZE = 1024`, but `EpromOperator._calculate_buffer_size()` always returns `BUFFER_SIZE` (512) regardless of the connected board type.
- Files: `firestarter/constants.py`, `firestarter/eprom_operations.py` (line 148)
- Impact: Leonardo boards run at half their potential throughput during write/verify operations.
- Fix approach: Detect the board type from the firmware handshake message and select the appropriate buffer size.

---

## Known Bugs

**`avrdude` version `float()` crash on three-part version strings:**
- Symptoms: `Avrdude.__init__()` raises `ValueError: could not convert string to float` when the installed avrdude version is e.g. `7.3.0`. The regex in `_get_avrdude_version()` correctly captures the full `"7.3.0"` string, then `float()` fails on it.
- Files: `firestarter/avr_tool.py` (lines 88–90)
- Trigger: Any system with avrdude >= 7.x where the version string has three numeric components (e.g., released packages on many Linux distributions).
- Workaround: None — firmware installation (`firestarter fw --install`) crashes before attempting to flash.

**`_read_and_parse_lines` polling misses data when `in_waiting == 0`:**
- Symptoms: `read_line_bytes()` returns `None` whenever there are no bytes waiting. The generator then sleeps 10 ms and retries. Under high CPU load or USB jitter the firmware response can arrive between the `in_waiting` check and the 10 ms sleep, adding latency without any timeout reset. This can cause spurious `SerialTimeoutError` on slow operations.
- Files: `firestarter/serial_comm.py` (lines 143–215)
- Trigger: Slow EPROM operations on loaded systems, or USB-to-serial adapters with high latency.
- Workaround: Use `--verbose` to reduce chance of timeout by seeing more output; retry the operation.

**`_verbose` global in `utils.py` is defined but never written to or read from:**
- Symptoms: Dead code. The module-level `_verbose = False` flag has no effect.
- Files: `firestarter/utils.py` (line 13)
- Trigger: Not a runtime bug, but increases maintenance confusion.
- Workaround: None needed; it has no effect.

---

## Security Considerations

**`subprocess.Popen` with externally-supplied executable path:**
- Risk: `Avrdude._find_avrdude_path()` accepts `avrdude_path` from user CLI input (`--avrdude-path`) and passes it directly to `Popen` without sanitisation. A malicious or accidentally wrong value could execute an arbitrary binary.
- Files: `firestarter/avr_tool.py` (lines 57–65, 103)
- Current mitigation: `which(str(path))` is called before use, which rejects paths that are not executable. However, this still executes whatever is found at that path.
- Recommendations: Validate that the resolved path ends with the expected executable name (`avrdude`) before executing.

**Firmware downloaded over HTTPS but without integrity verification:**
- Risk: Firmware `.hex` files are downloaded from the GitHub Releases API (`FIRESTARTER_RELEASE_URL`) and written directly to `~/.firestarter/`. There is no checksum or signature verification. A man-in-the-middle or a compromised GitHub release could deliver malicious firmware.
- Files: `firestarter/firmware.py` (lines 148–179), `firestarter/constants.py`
- Current mitigation: HTTPS is used, which provides transport security but not content integrity.
- Recommendations: Publish and verify SHA256 checksums alongside the `.hex` assets. Refuse installation if the checksum does not match.

**Serial port auto-discovery saves last-used port to config without confirmation:**
- Risk: `_probe_port()` calls `config_manager.set_value("port", port_name)` every time a port is successfully probed. On multi-user systems, or after a device change, this silently overwrites the stored port with the newly detected one.
- Files: `firestarter/serial_comm.py` (line 403)
- Current mitigation: None.
- Recommendations: Low severity, but worth noting for shared-hardware lab environments.

---

## Performance Bottlenecks

**2-second unconditional sleep on every serial connection:**
- Problem: `SerialCommunicator.__init__()` always sleeps `CONNECTION_STABILIZE_DELAY = 2.0` seconds after opening the port. Since `_probe_port()` is called once per candidate port, connecting to a programmer on the third candidate port takes at least 6 seconds of dead time.
- Files: `firestarter/serial_comm.py` (lines 35–36, 106)
- Cause: Arduino resets on DTR assertion when the serial port opens; 2 seconds waits for the bootloader to pass. This is necessary for Arduino Uno/Leonardo but may be excessive. The delay also applies even when reconnecting to an already-running programmer that does not reset.
- Improvement path: Detect that the port was previously saved (`config.get("port")`) and try it first with a shorter delay; fall back to 2 s only if the probe fails.

**`globals()` scanned on every EPROM command to recover command name:**
- Problem: `_setup_operation()` and `_operation_context()` both call `[k for k, v in globals().items() if v == cmd][0]` to reverse-lookup the command name from its integer value. This is O(n) over the module global namespace on every operation.
- Files: `firestarter/eprom_operations.py` (lines 163, 217)
- Cause: No reverse-lookup map was defined alongside the constants.
- Improvement path: Add a `COMMAND_NAMES = {COMMAND_READ: "READ", ...}` dict in `constants.py` and use it directly.

**Polling read loop with 10 ms sleep:**
- Problem: `_read_and_parse_lines()` polls `in_waiting` every 10 ms rather than using blocking `readline()` with a deadline. During a large read (e.g. a 512 KB chip) this loop cycles thousands of times unnecessarily.
- Files: `firestarter/serial_comm.py` (lines 197–215)
- Cause: Non-blocking read pattern chosen to support timeout, but `serial.Serial` supports a hardware timeout that enables blocking reads with a deadline natively.
- Improvement path: Set `serial.Serial(timeout=X)` and call `readline()` directly; check elapsed time after each line.

---

## Fragile Areas

**`ic_layout.py` pin-map field access assumes list values:**
- Files: `firestarter/ic_layout.py` (lines 419, 424, 431, 436)
- Why fragile: Pin-map fields like `rw-pin`, `oe-pin`, and `vpp-pin` are accessed as `pin_map_details["rw-pin"][0]`, assuming list values. If a pinout entry in `pinouts.json` stores a scalar integer instead of a list, this raises `TypeError`. `database.py`'s `get_bus_config()` handles both forms, but `ic_layout.py` does not.
- Safe modification: Always normalise pin values to lists when reading from the JSON, or use a helper that handles both scalars and lists.
- Test coverage: No automated tests; only verified through manual hardware use.

**`_merge_databases()` performs a shallow `dict.update()` on matched entries:**
- Files: `firestarter/database.py` (lines 192–216)
- Why fragile: When an override entry's `name` matches a `part_number` in the main database, `existing_names[manual_item["name"]].update(manual_item)` only updates top-level keys. Nested dicts like `electrical` or `programming` are replaced wholesale rather than merged, which can silently drop sub-fields from the base entry.
- Safe modification: Implement a deep-merge strategy for nested dicts before re-enabling the override system.
- Test coverage: None.

**Singleton `EpromDatabase._initialized` flag is a class variable, not per-instance:**
- Files: `firestarter/database.py` (lines 144–159)
- Why fragile: If the database needs to be re-initialised (e.g., after a user modifies their local override file), there is no way to do so without restarting the process. The `_instance` / `_initialized` pattern prevents reloading.
- Safe modification: Add a `reset()` classmethod that clears `_instance` and `_initialized` for testing or hot-reload scenarios.
- Test coverage: None.

**`avr_tool._get_avrdude_version()` returns `None` on parse failure and is compared with `< 7.0`:**
- Files: `firestarter/avr_tool.py` (lines 81–92, 48)
- Why fragile: If version detection fails, `self.version` is `None`. The subsequent `if self.version < 7.0:` raises `TypeError: '<' not supported between instances of 'NoneType' and 'float'`. This would crash firmware installation before the user gets a useful error message.
- Safe modification: Guard `if self.version is None or self.version < 7.0:`.
- Test coverage: None.

---

## Scaling Limits

This is a hardware-coupled single-device tool. The serial protocol is inherently sequential; only one EPROM operation can proceed at a time. The database is fully loaded into memory on startup (~14 K lines of JSON); at current scale this is fast, but the `get_eproms()` method with `_map_data()` applied to every entry performs O(n) object construction on each list call, which will become noticeable if the database grows significantly (current size: ~1,400 entries in `minipro_complete_db.json`).

---

## Dependencies at Risk

**`argcomplete >= 3.6.2` is a very recent minimum:**
- Risk: `argcomplete 3.6.x` is a significant version bump; systems with older environments (e.g., distro-packaged Python) may not be able to satisfy this constraint.
- Impact: Installation fails on those systems.
- Migration plan: Verify whether 3.6.2 features are actually required; lower the floor to `>=2.0` if not.

**`requests` for firmware download (no async, no retry):**
- Risk: The `requests.get()` calls in `firmware.py` are synchronous with a hardcoded `timeout=10` for the API call and `timeout=30` for the download. Large firmware files or slow connections will block the CLI with no user feedback.
- Impact: Poor UX; no progress indication during download.
- Migration plan: Use `tqdm` (already a dependency) with `requests` streaming to show download progress, or add retry logic with `requests.adapters.HTTPAdapter`.

---

## Missing Critical Features

**No Python unit tests:**
- Problem: There is no `tests/` directory and no pytest or unittest setup. The only automated testing is hardware-dependent shell scripts (`firestarter_test.sh`, `write_test.sh`) that require a physically connected EPROM programmer.
- Blocks: CI/CD validation of database queries, flag calculations, protocol parsing, and version comparison logic without hardware.

**No progress indication during firmware download:**
- Problem: `_download_firmware_file()` streams the file in 8 KB chunks but only logs "Downloading firmware from..." without a progress bar.
- Blocks: User has no feedback during potentially multi-second downloads; may appear hung.

**Database overrides disabled:**
- Problem: The user override mechanism (`~/.firestarter/database.json`) is hard-commented-out while the new database format is being adopted. The documented feature does not work.
- Blocks: Users cannot add unsupported EPROMs without modifying the package source.

---

## Test Coverage Gaps

**EPROM database logic:**
- What's not tested: `_map_data()`, `get_eproms()`, `search_eprom()`, `convert_to_programmer()`, `_merge_databases()`, `get_bus_config()`, `search_chip_id()`.
- Files: `firestarter/database.py`
- Risk: The ongoing format migration can silently break EPROM lookups (wrong type, zero pulse delay, missing flags) without any automated detection.
- Priority: High

**Serial communication protocol:**
- What's not tested: `_parse_response_line()`, `_read_and_parse_lines()`, `expect_ack()`, `read_data_block()` (checksum path), timeout handling.
- Files: `firestarter/serial_comm.py`
- Risk: Protocol regressions (e.g., checksum mismatch handling, timeout edge cases) would only surface during hardware testing.
- Priority: High

**Version comparison logic:**
- What's not tested: `SerialCommunicator._is_version_sufficient()`, `FirmwareManager._compare_versions()`, `Avrdude._get_avrdude_version()`.
- Files: `firestarter/serial_comm.py`, `firestarter/firmware.py`, `firestarter/avr_tool.py`
- Risk: The known `float("7.3.0")` crash in `avr_tool.py` would be caught immediately by a single unit test.
- Priority: High

**Flag calculation:**
- What's not tested: `build_flags()`, `build_arg_flags()`, flag bit composition in `convert_to_programmer()`.
- Files: `firestarter/eprom_operations.py`, `firestarter/main.py`, `firestarter/database.py`
- Risk: Wrong flags sent to firmware cause silent data loss or hardware damage (e.g., applying VPP at wrong voltage).
- Priority: High

**IC layout rendering:**
- What's not tested: `EpromSpecBuilder.build_eprom_specs()`, pin map rendering, jumper info generation.
- Files: `firestarter/ic_layout.py`
- Risk: Incorrect pin layout display could mislead users connecting hardware.
- Priority: Medium

---

*Concerns audit: 2026-05-08*
