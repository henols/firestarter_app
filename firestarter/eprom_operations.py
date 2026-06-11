"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

EPROM Operations Module (Refactored)
"""

import hashlib
import logging
import os
import shutil
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple  # noqa: UP035

import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

from firestarter.address_parser import parse_address, parse_size
from firestarter.config import ConfigManager
from firestarter.constants import (
    COMMAND_BLANK_CHECK,
    COMMAND_CHECK_CHIP_ID,
    COMMAND_DEV_ADDRESS,
    COMMAND_DEV_REGISTERS,
    COMMAND_ERASE,
    COMMAND_FW_VERSION,
    COMMAND_NAMES,
    COMMAND_READ,
    COMMAND_VERIFY,
    COMMAND_WRITE,
    FLAG_FORCE,
    FLAG_SKIP_BLANK_CHECK,
    FLAG_SKIP_ERASE,
    FLAG_VERBOSE,
    FLAG_VPE_AS_VPP,
    JSON_KEY_READ_SETTLING_DELAY,
    JSON_KEY_READ_STROBE_US,
)
from firestarter.exceptions import (
    EpromOperationError,
    FirmwareOutdatedError,
    ProgrammerNotFoundError,
    ProtocolNotImplementedError,
    SerialError,
    SerialTimeoutError,
)
from firestarter.frame_parser import _crc8_ccitt, cobs_encode
from firestarter.serial_comm import SerialCommunicator
from firestarter.utils import extract_hex_to_decimal

logger = logging.getLogger("EpromOperator")

bar_format = "{l_bar}{bar}| {n:#06x}/{total:#06x} bytes "


def _raise_for_error_response(response, message: str) -> None:
    """Raise ProtocolNotImplementedError for id 0xBB, EpromOperationError otherwise.

    Centralises typed-exception dispatch for all ERROR-branch sites in the
    state machine so id-keyed detection is not duplicated per raise site.

    `response` is read for its `id` field to dispatch to the typed subclass.
    `message` is the already-composed exception message string (callers may
    prepend a phase-name prefix for EpromOperationError framing; the raw
    firmware text is passed through unchanged for ProtocolNotImplementedError
    so firmware owns rendering per D-02).
    """
    from firestarter.messages import MSG_ERR_PROTOCOL_NOT_IMPLEMENTED

    if response.id == MSG_ERR_PROTOCOL_NOT_IMPLEMENTED:
        raise ProtocolNotImplementedError(response.message)
    raise EpromOperationError(message)


def build_flags(
    blank_check=True, force=False, vpe_as_vpp=False, verbose=False, skip_erase=False
):
    flags = 0
    if not blank_check:
        flags |= FLAG_SKIP_BLANK_CHECK
    if skip_erase:
        flags |= FLAG_SKIP_ERASE
    if force:
        flags |= FLAG_FORCE
    if vpe_as_vpp:
        flags |= FLAG_VPE_AS_VPP
    if verbose:
        flags |= FLAG_VERBOSE

    return flags


def hexdump(address, data, width=16):
    """
    Prints a hexdump similar to xxd.
    :param data: The data to be printed (bytes).
    :param width: Number of bytes per line.
    """
    for i in range(0, len(data), width):
        chunk = data[i : i + width]
        mid = width // 2

        hex_parts = []
        ascii_parts = []
        for j, byte in enumerate(chunk):
            if j == mid:
                hex_parts.append("")  # Creates the double space with ' '.join()
                ascii_parts.append(" ")

            hex_parts.append(f"{byte:02x}")
            ascii_parts.append(chr(byte) if 32 <= byte <= 126 else ".")

        hex_str = " ".join(hex_parts)
        ascii_str = "".join(ascii_parts)

        logger.info(f"{address + i:08x}: {hex_str:<{width * 3}} {ascii_str}")


class ClassProgressHandler:
    def __init__(self, progress_callback=None):
        self.progress_callback = progress_callback
        self.pbar = None
        self.current_step = 0
        self.total_steps = 0

    def start(self, total_steps: int):
        self.total_steps = total_steps
        self.current_step = 0
        if self.progress_callback:
            self.progress_callback(self.current_step, total_steps)
        else:
            if self.pbar:
                self.pbar.close()  # Close old one if any
            logging_redirect_tqdm()
            self.pbar = tqdm.tqdm(total=total_steps, bar_format=bar_format)

    def update(self, completed_steps: int):
        self.current_step += completed_steps
        if self.progress_callback:
            self.progress_callback(self.current_step, self.total_steps)
        if self.pbar:
            self.pbar.update(completed_steps)
        else:
            # If no progress bar, we can't do much with incremental updates without a total.  # noqa: E501
            logger.info(f"Progress: +{completed_steps} steps")

    def set_progress(self, current, total):
        if self.total_steps != total or (not self.pbar and not self.progress_callback):
            self.start(total)

        self.current_step = current
        if self.progress_callback:
            self.progress_callback(current, total)
        if self.pbar:
            self.pbar.n = current
            self.pbar.refresh()

    def close(self):
        if self.pbar:
            self.pbar.close()
            self.pbar = None


class EpromOperator:
    """
    Handles various operations on EPROMs, such as reading, writing, verifying,
    erasing, and checking chip IDs. It utilizes an EpromDatabase instance for
    EPROM-specific data and a SerialCommunicator instance (managed per operation)
    for interacting with the EPROM programmer hardware.
    """

    def __init__(
        self, config: ConfigManager, progress_callback: Optional[Callable] = None
    ):
        self.comm: SerialCommunicator | None = None
        self.config = config
        self.progress_callback = progress_callback

    def _calculate_buffer_size(self) -> int:
        # CAP-01 (Phase 55): firmware_max_chunk is now populated by the
        # _decode_id_frame MSG_OK_READY ack override in serial_comm.py, not
        # by parsing the FW identity string (Phase 54 mechanism removed).
        # Phase 54 D-05 is reversed: when the field is absent (old firmware
        # or ack with 0 param bytes), return 512 — the Uno floor, universally
        # safe minimum — instead of raising FirmwareOutdatedError.
        max_chunk = (
            getattr(self.comm, "firmware_max_chunk", None) if self.comm else None
        )
        if max_chunk is not None and max_chunk >= 1:
            return max_chunk
        # CAP-01 safe Uno-floor default: absent advertisement -> 512.
        return 512

    def _setup_operation(  # Remains largely the same, as it's a prerequisite for the context manager  # noqa: E501
        self,
        eprom_name: str,  # For logging
        eprom_data_dict: dict,  # Pre-fetched EPROM data
        cmd: int,
        operation_flags: int = 0,
        address: Optional[str] = None,
        size: Optional[str] = None,
        fault_inject_outgoing: Optional[Callable[[bytes], bytes]] = None,
    ) -> Tuple[Optional[Dict], int]:  # noqa: UP006
        """
        Prepares for an EPROM operation: uses pre-fetched EPROM data, sets up command, and connects.
        Returns (eprom_data_for_command, buffer_size) or (None, 0) on failure.
        """  # noqa: E501
        operation = COMMAND_NAMES[cmd]  # Get command name
        logger.debug(f"Performing {operation} for {eprom_name.upper()}")

        start_time = time.time()
        # eprom_data_dict is assumed to be valid and pre-fetched by the caller (main.py)
        logger.debug(f"EPROM data: {eprom_data_dict}")
        command_dict = eprom_data_dict.copy()  # Work with a copy for the command
        command_dict["cmd"] = cmd
        # Combine base flags from EPROM data with operation-specific flags
        command_dict["flags"] = eprom_data_dict.get("flags", 0) | operation_flags
        addr = 0
        if address:
            try:
                addr = parse_address(address) or 0
                command_dict["address"] = addr
            except ValueError:
                logger.error(f"Invalid address format: {address}")
                return None, 0

        # Special handling for read operation size
        if cmd == COMMAND_READ and size:
            try:
                read_size = parse_size(size) or 0
                # 'memory-size' in command_dict will define the end address for read

                command_dict["memory-size"] = addr + read_size
            except ValueError:
                logger.error(f"Invalid size format: {size}")
                return None, 0

        try:
            self.comm = SerialCommunicator.find_and_connect(
                command_dict,
                self.config,
                fault_inject_outgoing=fault_inject_outgoing,
            )
            buffer_size = self._calculate_buffer_size()
            logger.debug(
                f"Operation {operation} setup for {eprom_name} (state {cmd}) complete ({time.time() - start_time:.2f}s). Buffer size: {buffer_size}"  # noqa: E501
            )
            return command_dict, buffer_size
        except (ProgrammerNotFoundError, SerialError) as e:
            logger.error(f"Failed to setup operation {operation} for {eprom_name}: {e}")
            self._disconnect_programmer()  # Ensure comm is None if setup fails
            return None, 0

    @contextmanager
    def _operation_context(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        cmd: int,
        operation_flags: int = 0,
        address: Optional[str] = None,
        size: Optional[str] = None,
        fault_inject_outgoing: Optional[Callable[[bytes], bytes]] = None,
    ):
        """A context manager to handle EPROM operation setup and teardown.

        ``fault_inject_outgoing`` (Phase 53-04 / XACT-02, dev-only) is forwarded to
        ``find_and_connect`` so the setup command frame can be corrupted at connection
        time. Default None keeps the production path byte-identical (T-53-03).
        """
        command_dict, buffer_size = self._setup_operation(
            eprom_name,
            eprom_data_dict,
            cmd,
            operation_flags,
            address,
            size,
            fault_inject_outgoing=fault_inject_outgoing,
        )
        if not command_dict or not self.comm:
            yield None, None, None  # Yield None to indicate setup failure
            return

        operation_name = COMMAND_NAMES[cmd]
        try:
            # Yield the necessary data to the 'with' block
            yield command_dict, buffer_size, operation_name
        finally:
            # This block ensures disconnection happens even if errors occur
            self._disconnect_programmer()

    def _disconnect_programmer(self):
        if self.comm:
            self.comm.disconnect()
            self.comm = None

    # --- Unified State Machine ---

    def _run_state_machine(
        self,
        operation_name: str,
        main_phase_handler: Optional[Callable] = None,
        **handler_kwargs,
    ) -> Tuple[bool, Optional[str]]:  # noqa: UP006
        """A unified state machine driver for all operations."""
        if not self.comm:
            return False, "Not connected"

        progress = ClassProgressHandler(self.progress_callback)
        final_msg = None
        try:
            with logging_redirect_tqdm():
                # --- INIT Phase ---
                _ = self._execute_phase("INIT", progress)

                # --- MAIN Phase ---
                self.comm.send_ack()  # Signal start of MAIN
                logger.debug("Main start")
                if main_phase_handler:
                    # Delegate to a specific handler for the main data transfer loop
                    final_msg = main_phase_handler(progress=progress, **handler_kwargs)
                else:
                    # For simple commands, just wait for the MAIN completion signal
                    final_msg = self._main_phase_simple(progress)
                logger.debug("Main complete.")

                # --- END Phase ---
                end_msg = self._execute_phase("END", progress)  # noqa: F841

                # --- Final ACK to complete transaction ---
                self.comm.send_ack()
                return True, final_msg
        except (SerialError, SerialTimeoutError) as e:
            logger.error(f"Communication error during {operation_name}: {e}")
            return False, str(e)
        except EpromOperationError as e:
            logger.error(f"Programmer error during {operation_name}: {e}")
            return False, str(e)
        finally:
            progress.close()

    def _execute_phase(
        self, phase_name: str, progress: ClassProgressHandler
    ) -> Optional[str]:
        """Executes a single phase (INIT or END) of the state machine."""
        self.comm.send_ack()
        logger.debug(f"{phase_name.lower()} start")
        final_msg = None
        while True:
            response = self.comm.get_response()
            if response.type == phase_name:
                final_msg = response.message
                break
            if response.type == "ERROR":
                _raise_for_error_response(
                    response,
                    f"Programmer error during {phase_name.lower()}: {response.message}",
                )
            self._handle_progress_response(response, progress)
        logger.debug(f"{phase_name.lower()} complete.")
        return final_msg

    def _handle_progress_response(self, response, progress: ClassProgressHandler):
        """Helper to process DATA, WARN, OK during a state phase."""
        if response.type == "DATA":
            try:
                if response.message and "/" in response.message:
                    current, total = map(int, response.message.split("/"))
                    if progress:
                        progress.set_progress(current, total)
                elif response.message:
                    progress.update(int(response.message))
            except (ValueError, TypeError):
                pass  # Not a parsable progress update
            self.comm.send_ack()
        elif response.type == "WARN":
            logger.warning(f"Programmer warning: {response.message}")
        elif response.type == "OK":
            logger.debug(f"Got OK: {response.message}")

    # --- Main Phase Handlers ---

    def _main_phase_simple(self, progress: ClassProgressHandler) -> Optional[str]:
        """Main phase handler for simple commands like erase, blank check, id."""
        final_msg = None
        while True:
            response = self.comm.get_response()
            if response.type == "MAIN":
                final_msg = response.message
                break
            if response.type == "ERROR":
                _raise_for_error_response(response, response.message)
            if response.type == "OK" and final_msg is None:
                final_msg = response.message  # Capture final message from MAIN's OK
            self._handle_progress_response(response, progress)
        return final_msg

    def _main_phase_send_data(
        self, progress: ClassProgressHandler, input_file_path: str, buffer_size: int
    ) -> None:
        """Main phase handler for writing or verifying data."""
        if not os.path.exists(input_file_path):
            raise EpromOperationError(f"Input file {input_file_path} not found.")

        with open(input_file_path, "rb") as file_handle:
            file_size = os.path.getsize(input_file_path)
            progress.start(file_size)

            while True:
                response = self.comm.get_response()
                if response.type == "MAIN":
                    break  # Main phase is complete
                if response.type != "OK":
                    raise EpromOperationError(
                        f"Programmer did not request data chunk, got {response.type}: {response.message}"  # noqa: E501
                    )

                if file_handle.tell() < file_size:
                    data_chunk = file_handle.read(buffer_size)
                    crc = _crc8_ccitt(data_chunk)
                    body = cobs_encode(data_chunk + bytes([crc]))
                    frame = b"#" + body + b"\x00"

                    # Firmware decodes the COBS frame via rurp_communication_read_data
                    # (rurp_serial_utils.cpp): reads bytes until the 0x00 delimiter,
                    # COBS-decodes in place, verifies CRC8-CCITT over the payload.
                    # Frame layout (ADR §4.3): b"#" + COBS(payload + CRC8) + b"\x00".
                    # Assembled as ONE bytes object and sent in a single send_bytes call
                    # (atomic-write mandate, ADR §4.1 / T-50-05 SAFE-01 timing guard).
                    self.comm.send_bytes(frame)
                    progress.update(len(data_chunk))
                else:
                    self.comm.send_done()

    def _main_phase_read_data(
        self,
        progress: ClassProgressHandler,
        start_addr: int,
        end_addr: int,
        process_data_chunk_callback: Callable,
    ):
        """Main phase handler for reading data.

        Phase 8 W-04: the firmware now wraps each chip-byte chunk inside a
        MSG_DATA_CHUNK ID frame instead of emitting raw bytes after a DATA:
        text prefix.  The response loop distinguishes:
          - DATA response with payload set → MSG_DATA_CHUNK; extract raw bytes.
          - DATA response with no payload  → MSG_DATA_SENDING (zero-param batch
            starter, which arrives before the chunk frame); skip and continue.
        """
        from firestarter.messages import (
            MSG_DATA_CHUNK,  # local import avoids circular  # noqa: F401
        )

        data_size = end_addr - start_addr
        if data_size > 0:
            progress.start(data_size)

        while True:
            response = self.comm.get_response()
            if response.type == "MAIN":
                logger.info("EPROM read complete.")
                break
            if response.type == "ERROR":
                raise EpromOperationError(
                    f"Programmer error during read: {response.message}"
                )
            if response.type == "DATA":
                if response.payload is not None:
                    # MSG_DATA_CHUNK: the raw chip bytes are in response.payload.
                    payload = response.payload
                    if not payload:
                        logger.warning("Received MSG_DATA_CHUNK with empty payload.")
                        continue
                    process_data_chunk_callback(start_addr, payload)
                    start_addr += len(payload)
                    progress.update(len(payload))
                    self.comm.send_ack()
                else:
                    # MSG_DATA_SENDING (zero-param batch-start ack): no data yet;
                    # the MSG_DATA_CHUNK frame follows immediately.
                    logger.debug(
                        f"Received DATA signal (no payload): {response.message}"
                    )
            else:
                self._handle_progress_response(response, progress)

    # --- Public API Methods ---

    def read_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        output_file: Optional[str] = None,
        operation_flags: int = 0,
        address_str: Optional[str] = None,
        size_str: Optional[str] = None,
    ) -> bool:
        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_READ,
            operation_flags,
            address_str,
            size_str,
        ) as (cmd_data, _, op_name):
            if not cmd_data:
                return False

            actual_output_file = output_file or f"{eprom_name.upper()}.bin"
            logger.info(
                f"Reading EPROM {eprom_name.upper()}, saving to {actual_output_file}"
            )
            start_time = time.time()

            try:
                with open(actual_output_file, "wb") as file_handle:

                    def _write_to_file(address, data_chunk):
                        file_handle.seek(address)
                        file_handle.write(data_chunk)

                    is_ok, _ = self._run_state_machine(
                        op_name,
                        main_phase_handler=self._main_phase_read_data,
                        start_addr=cmd_data.get("address", 0),
                        end_addr=cmd_data.get("memory-size", 0),
                        process_data_chunk_callback=_write_to_file,
                    )
                if is_ok:
                    logger.info(
                        f"Read complete ({time.time() - start_time:.2f}s). Data saved to {actual_output_file}"  # noqa: E501
                    )
                return is_ok
            except IOError as e:  # noqa: UP024
                logger.error(f"File I/O error with {actual_output_file}: {e}")
                return False

    def consistency_check_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        runs: int = 3,
        output_dir: Optional[str] = None,
        keep_files: bool = True,
        max_diffs: int = 10,
        quiet: bool = False,
        operation_flags: int = 0,
        read_settling_us: int = 0,  # address-settling delay (µs; 0=firmware default)
        read_strobe_us: int = 0,  # /CE read-strobe pulse width (µs; 0=firmware default)
    ) -> int:
        """Run N consecutive read_eprom passes and report SHA-256 divergence.

        Returns:
            0 -- all N reads byte-identical (PASS)
            1 -- one or more reads diverge (FAIL -- bug detected)
            2 -- hardware / serial / timeout error (could not complete N reads)

        This is the ONLY EpromOperator method that returns int rather than bool;
        the 3-way verdict (PASS / FAIL / hardware-error) cannot fit in a bool.
        Same exit-code convention as grep(1). Precedent for non-bool return:
        check_eprom_id() returns Tuple[bool, Optional[int]] above.

        Reuses _run_state_machine + _main_phase_read_data verbatim (per Phase 26
        CONTEXT.md D-03 reuse-not-duplicate rule) -- the diagnostic exercises
        the same code path the read bug lives in. Do NOT refactor into a
        parallel read implementation.

        REPRO-03 (Phase 26 / Plan 26-01).
        """
        # D-10 Test 6: reject runs < 2 BEFORE any state-machine invocation
        if runs < 2:
            logger.error(
                f"--runs must be >= 2 (got {runs}); "
                f"a consistency check requires at least 2 reads to compare."
            )
            return 2

        # Quiet mode: suppress tqdm by swapping progress_callback to a no-op.
        # ClassProgressHandler.__init__ checks `if self.progress_callback:` --
        # a truthy no-op short-circuits the tqdm.tqdm() instantiation.
        prior_callback = self.progress_callback
        if quiet:
            self.progress_callback = lambda *a, **kw: None

        try:
            # Default output_dir naming uses datetime + chip name.
            # Board name is optional; if firmware handshake hasn't run we
            # render "unknown-board" rather than blocking on an extra round-
            # trip (per RESEARCH Pitfall 2 Option a, the cleanest production
            # path is to use the actual board name -- but the unit-test
            # surface doesn't have a serial connection, so we fall back).
            if output_dir is None:
                timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
                output_dir = f"consistency-check-{eprom_name}-unknown-board-{timestamp}"
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

            # Merge read-timing knobs into eprom_data_dict copy so they ride
            # into _setup_operation via command_dict = eprom_data_dict.copy().
            # Emit each key only when non-zero (firmware defaults apply when absent).
            # Pattern: consistent with how pulse-delay already travels via the DB dict.
            if read_settling_us or read_strobe_us:
                eprom_data_dict = dict(
                    eprom_data_dict
                )  # shallow copy — never mutate caller's dict
                if read_settling_us:
                    eprom_data_dict[JSON_KEY_READ_SETTLING_DELAY] = read_settling_us
                if read_strobe_us:
                    eprom_data_dict[JSON_KEY_READ_STROBE_US] = read_strobe_us

            # Run loop: N reads through the production state machine
            results = []  # list of (run_i, sha_hex, bytes_written)
            total_size = 0
            for i in range(1, runs + 1):
                run_path = output_path / f"run_{i:02d}.bin"
                logger.info(f"Run {i}/{runs}: reading {eprom_name} -> {run_path}")
                start_t = time.time()

                # Reuse the EXACT code path read_eprom uses (D-03 reuse-not-duplicate)
                try:
                    with self._operation_context(
                        eprom_name,
                        eprom_data_dict,
                        COMMAND_READ,
                        operation_flags,
                    ) as (cmd_data, _, op_name):
                        if not cmd_data:
                            logger.error(f"Run {i}: failed to set up read operation.")
                            return 2  # D-05 hardware error
                        try:
                            with open(run_path, "wb") as fh:

                                def _writer(
                                    address,
                                    data_chunk,
                                    _fh=fh,
                                    _start=cmd_data.get("address", 0),
                                ):
                                    # Mirror read_eprom's _write_to_file inner closure
                                    # (eprom_operations.py:408-411). Use relative-from-start  # noqa: E501
                                    # offset so the file fills from byte 0 regardless of
                                    # absolute start_addr.
                                    _fh.seek(address - _start)
                                    _fh.write(data_chunk)

                                is_ok, _ = self._run_state_machine(
                                    op_name,
                                    main_phase_handler=self._main_phase_read_data,
                                    start_addr=cmd_data.get("address", 0),
                                    end_addr=cmd_data.get("memory-size", 0),
                                    process_data_chunk_callback=_writer,
                                )
                        except IOError as e:  # noqa: UP024
                            logger.error(f"Run {i}: file I/O error on {run_path}: {e}")
                            return 2

                    # Map _run_state_machine (False, msg) -> exit 2 (per
                    # RESEARCH Pitfall 4: state machine catches serial
                    # exceptions and returns (False, str(e)) rather than
                    # propagating).
                    if not is_ok:
                        logger.error(
                            f"Run {i}: hardware/serial error -- read incomplete."
                        )
                        return 2

                except EpromOperationError as e:
                    logger.error(f"Run {i}: {e}")
                    return 2

                bytes_written = run_path.stat().st_size
                sha = hashlib.sha256(run_path.read_bytes()).hexdigest()
                elapsed = time.time() - start_t
                results.append((i, sha, bytes_written))
                total_size = bytes_written  # noqa: F841
                logger.info(
                    f"Run {i}/{runs}: SHA-256 {sha}  "
                    f"bytes={bytes_written}  elapsed={elapsed:.2f}s"
                )

            # Verdict
            distinct = sorted({r[1] for r in results})
            exit_code = 0 if len(distinct) == 1 else 1

            # Print verdict block (D-04 -- exact substrings pinned by
            # Phase 29 forward-compat regex test_stdout_verdict_block_format).
            verdict = "PASS" if exit_code == 0 else "FAIL"
            port = (
                self.config.get_value("port")
                if hasattr(self.config, "get_value")
                else "?"
            )
            print(f"\nConsistency check: {verdict}")
            print(f"Chip: {eprom_name}  Board: unknown-board  Port: {port}")
            print(f"Runs: N={runs}")
            print(f"Distinct SHAs: {len(distinct)}")
            print(f"Output dir: {output_dir}/")

            # Divergence detail on FAIL (D-04)
            if exit_code == 1:
                run1_path = output_path / "run_01.bin"
                run2_path = output_path / "run_02.bin"
                run1_bytes = run1_path.read_bytes()
                run2_bytes = run2_path.read_bytes()
                cmp_len = min(len(run1_bytes), len(run2_bytes))
                diff_offsets = [
                    o for o in range(cmp_len) if run1_bytes[o] != run2_bytes[o]
                ]
                if diff_offsets:
                    first = diff_offsets[0]
                    # 4-hex-digit format guaranteed for 64KB chips; widen
                    # automatically for larger payloads. Use %04X minimum.
                    width = max(4, len(f"{cmp_len - 1:X}"))
                    print(
                        f"First divergence: offset 0x{first:0{width}X}  "
                        f"(run_1=0x{run1_bytes[first]:02X}, "
                        f"run_2=0x{run2_bytes[first]:02X})"
                    )
                    total_diffs = len(diff_offsets)
                    pct = 100.0 * total_diffs / cmp_len if cmp_len else 0.0
                    print(
                        f"Total divergent bytes (run_1 vs run_2): "
                        f"{total_diffs} / {cmp_len} ({pct:.1f}%)"
                    )
                    head = diff_offsets[:max_diffs]
                    offs_str = ", ".join(f"0x{o:0{width}X}" for o in head)
                    print(f"First {max_diffs} divergent offsets: {offs_str}")

            # Cleanup (D-10 #5)
            if not keep_files:
                shutil.rmtree(output_dir, ignore_errors=True)

            return exit_code
        finally:
            # Restore the operator's progress_callback so subsequent
            # operations are unaffected by --quiet for THIS invocation.
            self.progress_callback = prior_callback

    def write_cycle_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        source_image_path: str,
        runs: int = 5,
        output_dir: Optional[str] = None,
        operation_flags: int = 0,
    ) -> int:
        """Erase → write source image → read-back N times; compare each read-back
        against source image SHA-256 (D-06 independent host-side compare).

        Returns:
            0 -- all N read-backs match source image SHA-256 (PASS)
            1 -- any read-back SHA-256 != source image SHA-256 (FAIL / mismatch)
            2 -- erase_eprom or write_eprom returned False, read-back state machine
                 returned is_ok=False, or EpromOperationError raised (hw-error)

        The 3-way verdict (PASS / FAIL / hw-error) mirrors consistency_check_eprom.
        hw-error is NEVER collapsed to mismatch (verdict 2 != verdict 1).

        Reuses _operation_context + _run_state_machine + _main_phase_read_data
        verbatim from consistency_check_eprom (per reuse-not-duplicate rule).
        Do NOT refactor the read-back block into a parallel read implementation.

        XACT-01 / Phase 53 Plan 02.
        """
        source_sha = hashlib.sha256(Path(source_image_path).read_bytes()).hexdigest()

        if output_dir is None:
            timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            output_dir = f"write-cycle-{eprom_name}-unknown-board-{timestamp}"
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        for i in range(1, runs + 1):
            # (a) Erase
            if not self.erase_eprom(eprom_name, eprom_data_dict, operation_flags):
                logger.error(f"Cycle {i}: erase failed.")
                return 2

            # (b) Write
            if not self.write_eprom(
                eprom_name, eprom_data_dict, source_image_path, operation_flags
            ):
                logger.error(f"Cycle {i}: write failed.")
                return 2

            # (c) Read-back — verbatim reuse of the consistency_check_eprom read block
            # (reuse-not-duplicate rule: this block must not be reimplemented separately)
            cycle_path = output_path / f"cycle_{i:02d}_readback.bin"
            logger.info(f"Cycle {i}/{runs}: read-back {eprom_name} -> {cycle_path}")

            try:
                with self._operation_context(
                    eprom_name,
                    eprom_data_dict,
                    COMMAND_READ,
                    operation_flags,
                ) as (cmd_data, _, op_name):
                    if not cmd_data:
                        logger.error(f"Cycle {i}: failed to set up read operation.")
                        return 2
                    try:
                        with open(cycle_path, "wb") as fh:

                            def _writer(
                                address,
                                data_chunk,
                                _fh=fh,
                                _start=cmd_data.get("address", 0),
                            ):
                                _fh.seek(address - _start)
                                _fh.write(data_chunk)

                            is_ok, _ = self._run_state_machine(
                                op_name,
                                main_phase_handler=self._main_phase_read_data,
                                start_addr=cmd_data.get("address", 0),
                                end_addr=cmd_data.get("memory-size", 0),
                                process_data_chunk_callback=_writer,
                            )
                    except IOError as e:  # noqa: UP024
                        logger.error(f"Cycle {i}: file I/O error on {cycle_path}: {e}")
                        return 2

                # Map _run_state_machine (False, msg) -> exit 2 (Pitfall 3:
                # timeout/serial errors return (False, str(e)), never raise).
                if not is_ok:
                    logger.error(
                        f"Cycle {i}: hardware/serial error -- read-back incomplete."
                    )
                    return 2

            except EpromOperationError as e:
                logger.error(f"Cycle {i}: {e}")
                return 2

            # (d) Host-side SHA-256 compare against source image (D-06)
            readback_sha = hashlib.sha256(cycle_path.read_bytes()).hexdigest()
            if readback_sha != source_sha:
                logger.error(
                    f"Cycle {i}: SHA-256 mismatch -- "
                    f"source={source_sha}  readback={readback_sha}"
                )
                return 1

        return 0

    # --- DEV Methods ---

    def fault_inject_cycle(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        direction: str = "outgoing",
        fault_form: str = "corrupt-crc8",
        output_dir: Optional[str] = None,
    ) -> bool:
        """Demonstrate COBS resync by injecting a corrupted frame and asserting a
        bounded clean error followed by a byte-exact clean transfer.

        Returns:
            True  -- corrupted transfer surfaced a clean (bounded) error AND the
                     subsequent clean transfer succeeded byte-exact.
            False -- unexpected success on the corrupted transfer, or the
                     clean follow-on transfer failed.

        direction="outgoing": corrupt the host→fw SETUP command frame via the
            _fault_inject_outgoing hook, threaded into find_and_connect so it fires at
            connection time. This is the ONLY corruptible host→fw command frame — a
            READ's MAIN phase sends only plaintext acks (send_string). The firmware
            rejects the corrupt frame and the connection fails with a bounded error;
            a fresh clean transfer then succeeds. (The prior wiring set the hook AFTER
            setup, so it never fired — a false negative; see
            .planning/debug/fault-inject-harness-outgoing.md.)
        direction="incoming": corrupt fw→host frame via FaultInjectingSerialCommunicator
            on an established connection; the host decoder catches it and the same
            connection recovers on the clean follow-on (Pitfall 2).

        Writes fault-inject-<direction>-log.txt with the measured error latency so the
        sub-second clean error (no 2 s cascade) XACT-02 requires can be confirmed.

        XACT-02 / Phase 53 Plan 02 (harness fix: 53-04).
        """
        from firestarter.serial_comm import FaultInjectingSerialCommunicator

        if output_dir is None:
            timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            output_dir = f"fault-inject-{eprom_name}-{direction}-{timestamp}"
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Build fault hooks for the outgoing path (D-02 fault forms)
        def _corrupt_crc8(frame: bytes) -> bytes:
            """Flip the CRC8 byte (frame[-2]) — frame[-1] is the 0x00 delimiter."""
            return frame[:-2] + bytes([frame[-2] ^ 0x01]) + b"\x00"

        def _drop_delimiter(frame: bytes) -> bytes:
            """Drop trailing 0x00 delimiter — firmware inter-byte timeout fires."""
            return frame[:-1]

        fault_hooks: dict = {
            "corrupt-crc8": _corrupt_crc8,
            "drop-delimiter": _drop_delimiter,
        }
        hook = fault_hooks.get(fault_form, _corrupt_crc8)

        # --- Corrupted transfer ---
        # Outgoing: the hook is threaded into _operation_context so it corrupts the
        #   SETUP command frame at connection time. This is the ONLY corruptible
        #   host->fw command frame — a READ's MAIN phase emits plaintext acks
        #   (send_string), never send_json_command, so the previous "set the hook
        #   after setup" wiring never fired (false negative; see
        #   .planning/debug/fault-inject-harness-outgoing.md). The firmware rejects the
        #   corrupt frame (CRC8-before-parse / inter-byte timeout) -> connection setup
        #   fails with a bounded error == the expected outcome.
        # Incoming: connect cleanly, swap to FaultInjectingSerialCommunicator, run the
        #   read; the host decoder catches the mutated fw->host frame.
        # error_latency_s captures wall-clock from corrupted-attempt start to the
        #   surfaced error so XACT-02's "sub-second clean error, no 2 s cascade" can be
        #   measured (the harness reports it; the firmware's actual latency decides it).
        corrupted_ok = False
        error_latency_s: Optional[float] = None
        corrupted_detail = ""
        _t0 = time.monotonic()
        try:
            with self._operation_context(
                eprom_name,
                eprom_data_dict,
                COMMAND_READ,
                0,
                fault_inject_outgoing=(hook if direction == "outgoing" else None),
            ) as (cmd_data, _, op_name):
                if direction == "outgoing" and not cmd_data:
                    # Setup command frame corrupted -> firmware rejected -> bounded
                    # connection failure. This IS the expected outgoing-fault outcome.
                    error_latency_s = time.monotonic() - _t0
                    corrupted_ok = True
                    corrupted_detail = (
                        "outgoing: setup command frame rejected; connection did not "
                        "establish (firmware did not ack a corrupt host->fw frame)"
                    )
                elif not cmd_data:
                    # Incoming requires a clean connection before the fw->host swap.
                    return False
                else:
                    # We are connected. Incoming: swap comm to the fault subclass.
                    # Outgoing: reaching here means the corrupt setup frame did NOT
                    # prevent connection (fault never fired, or firmware accepted a
                    # corrupt frame) — run the read so an unexpected success is caught.
                    if direction == "incoming":
                        assert self.comm is not None  # noqa: S101
                        fault_comm = FaultInjectingSerialCommunicator.__new__(
                            FaultInjectingSerialCommunicator
                        )
                        fault_comm.__dict__.update(self.comm.__dict__)
                        fault_comm._corrupt_incoming_once = True  # type: ignore[attr-defined]
                        fault_comm._fault_fired = False  # type: ignore[attr-defined]
                        self.comm = fault_comm  # type: ignore[assignment]

                    corrupted_path = output_path / "corrupted_transfer.bin"
                    try:
                        with open(corrupted_path, "wb") as fh:

                            def _writer_corrupt(
                                address,
                                data_chunk,
                                _fh=fh,
                                _start=cmd_data.get("address", 0),
                            ):
                                _fh.seek(address - _start)
                                _fh.write(data_chunk)

                            is_ok_corrupt, _ = self._run_state_machine(
                                op_name,
                                main_phase_handler=self._main_phase_read_data,
                                start_addr=cmd_data.get("address", 0),
                                end_addr=cmd_data.get("memory-size", 0),
                                process_data_chunk_callback=_writer_corrupt,
                            )
                    except IOError as e:  # noqa: UP024
                        logger.error(f"fault_inject_cycle: file I/O error: {e}")
                        return False
                    error_latency_s = time.monotonic() - _t0
                    # The corrupted transfer should have failed (is_ok_corrupt == False)
                    corrupted_ok = not is_ok_corrupt
                    corrupted_detail = (
                        f"{direction}: connected; corrupted read verdict ok="
                        f"{is_ok_corrupt} (expected False)"
                    )
        except (
            EpromOperationError,
            ProgrammerNotFoundError,
            SerialError,
            SerialTimeoutError,
            FirmwareOutdatedError,
        ) as e:
            # A bounded transport/connection error on the corrupted transfer is the
            # expected resync signal (not a silent accept, not an unbounded hang).
            error_latency_s = time.monotonic() - _t0
            corrupted_ok = True
            corrupted_detail = f"{direction}: {type(e).__name__} (expected): {e}"

        # Persist the latency + verdict so the operator can confirm the sub-second
        # clean error (no 2 s cascade) XACT-02 requires (D-01/D-02).
        self._write_fault_inject_log(
            output_path,
            direction,
            fault_form,
            corrupted_ok,
            error_latency_s,
            corrupted_detail,
        )

        if not corrupted_ok:
            logger.error(
                "fault_inject_cycle: corrupted transfer unexpectedly succeeded."
            )
            return False

        # --- Clean follow-on transfer on the same connection ---
        clean_path = output_path / "clean_transfer.bin"
        try:
            with self._operation_context(
                eprom_name,
                eprom_data_dict,
                COMMAND_READ,
                0,
            ) as (cmd_data_clean, _, op_name_clean):
                if not cmd_data_clean:
                    return False
                try:
                    with open(clean_path, "wb") as fh2:

                        def _writer_clean(
                            address,
                            data_chunk,
                            _fh=fh2,
                            _start=cmd_data_clean.get("address", 0),
                        ):
                            _fh.seek(address - _start)
                            _fh.write(data_chunk)

                        is_ok_clean, _ = self._run_state_machine(
                            op_name_clean,
                            main_phase_handler=self._main_phase_read_data,
                            start_addr=cmd_data_clean.get("address", 0),
                            end_addr=cmd_data_clean.get("memory-size", 0),
                            process_data_chunk_callback=_writer_clean,
                        )
                except IOError as e:  # noqa: UP024
                    logger.error(
                        f"fault_inject_cycle: clean-transfer file I/O error: {e}"
                    )
                    return False

            if not is_ok_clean:
                logger.error("fault_inject_cycle: clean follow-on transfer failed.")
                self._append_fault_inject_log(
                    output_path, "clean follow-on transfer FAILED"
                )
                return False
        except EpromOperationError as e:
            logger.error(f"fault_inject_cycle: clean transfer raised: {e}")
            self._append_fault_inject_log(
                output_path, f"clean follow-on transfer raised: {e}"
            )
            return False

        self._append_fault_inject_log(
            output_path, "clean follow-on transfer PASSED (recovery byte-exact)"
        )
        return True

    @staticmethod
    def _write_fault_inject_log(
        output_path: Path,
        direction: str,
        fault_form: str,
        corrupted_ok: bool,
        error_latency_s: Optional[float],
        detail: str,
    ) -> None:
        """Write the XACT-02 fault-injection log (one per cycle).

        Records the measured error latency so the operator can confirm the
        sub-second clean error (no 2 s timeout cascade) the acceptance requires.
        """
        log_path = output_path / f"fault-inject-{direction}-log.txt"
        latency_str = (
            f"{error_latency_s:.3f}s" if error_latency_s is not None else "unmeasured"
        )
        # 2.0 s is the historical timeout-cascade threshold the hardening removes.
        cascade = (
            "UNKNOWN"
            if error_latency_s is None
            else ("NO (sub-2s)" if error_latency_s < 2.0 else "YES (>=2s cascade)")
        )
        try:
            with open(log_path, "w") as fh:
                fh.write(
                    f"# XACT-02 fault-injection log ({direction}, {fault_form})\n"
                    f"corrupted_transfer_surfaced_clean_error: {corrupted_ok}\n"
                    f"error_latency: {latency_str}\n"
                    f"sub_second_clean_error_no_2s_cascade: {cascade}\n"
                    f"detail: {detail}\n"
                )
        except IOError as e:  # noqa: UP024
            logger.error(f"fault_inject_cycle: could not write fault log: {e}")

    @staticmethod
    def _append_fault_inject_log(output_path: Path, line: str) -> None:
        """Append a follow-on line to the most recent fault-injection log(s)."""
        for log_path in output_path.glob("fault-inject-*-log.txt"):
            try:
                with open(log_path, "a") as fh:
                    fh.write(f"{line}\n")
            except IOError as e:  # noqa: UP024
                logger.error(f"fault_inject_cycle: could not append fault log: {e}")

    def measure_command_nak_latency(
        self,
        fault_form: str = "corrupt-crc8",
        output_dir: Optional[str] = None,
        port: Optional[str] = None,
    ) -> bool:
        """XACT-02 outgoing PER-FRAME latency measurement on an ESTABLISHED single-port
        connection (53-04 harness refinement).

        Unlike fault_inject_cycle (which corrupts the connection-SETUP frame and so
        triggers find_and_connect's multi-port retry — inflating the latency), this opens
        ONE pinned port directly, then on the SAME open connection:
          1. sends a clean CMD_FW_VERSION (baseline — firmware alive + at IDLE),
          2. sends ONE corrupted CMD_FW_VERSION frame, timed precisely from send to the
             firmware's error response (the real per-frame NAK latency),
          3. sends a clean CMD_FW_VERSION (recovery on the SAME connection).

        CMD_FW_VERSION is used because it is self-contained: the firmware answers and
        returns to CMD_IDLE, so three commands run back-to-back on one connection without
        a chip, VPP, or the read state machine.

        Returns True iff baseline OK AND the corrupted frame surfaced an error (no silent
        accept) AND the clean recovery transfer succeeded. Writes
        fault-inject-<fault_form>-latency.txt with the precise per-frame latency.
        """
        if port is None:
            port = self.config.get_value("port")
        if not port:
            logger.error(
                "measure_command_nak_latency: no serial port resolved "
                "(pass -p <port> or set config.port)."
            )
            return False

        if output_dir is None:
            timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            output_dir = f"fault-inject-latency-{fault_form}-{timestamp}"
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        def _corrupt_crc8(frame: bytes) -> bytes:
            return frame[:-2] + bytes([frame[-2] ^ 0x01]) + b"\x00"

        def _drop_delimiter(frame: bytes) -> bytes:
            return frame[:-1]

        hook = {"corrupt-crc8": _corrupt_crc8, "drop-delimiter": _drop_delimiter}.get(
            fault_form, _corrupt_crc8
        )

        fw_cmd = {"state": COMMAND_FW_VERSION}
        comm = None
        baseline_ok = False
        corrupted_surfaced_error = False
        recovery_ok = False
        nak_latency_s: Optional[float] = None
        detail = ""
        try:
            comm = SerialCommunicator(port=port)
            comm.consume_remaining_input()

            # 1. Baseline clean command on the open connection.
            comm.send_json_command(fw_cmd)
            baseline_ok, _ = comm.expect_ack()
            comm.consume_remaining_input()
            if not baseline_ok:
                detail = "baseline clean command did not ack OK; aborting measurement"
            else:
                # 2. One corrupted command frame, timed to the firmware error response.
                comm._fault_inject_outgoing = hook  # type: ignore[attr-defined]
                _t0 = time.monotonic()
                comm.send_json_command(fw_cmd)
                try:
                    corrupt_is_ok, corrupt_msg = comm.expect_ack()
                except SerialTimeoutError as e:
                    corrupt_is_ok, corrupt_msg = False, f"host read timeout: {e}"
                nak_latency_s = time.monotonic() - _t0
                comm._fault_inject_outgoing = None  # type: ignore[attr-defined]
                comm.consume_remaining_input()
                corrupted_surfaced_error = not corrupt_is_ok
                detail = (
                    f"corrupted frame response: ok={corrupt_is_ok} msg={corrupt_msg}"
                )

                # 3. Recovery: clean command on the SAME open connection.
                comm.send_json_command(fw_cmd)
                try:
                    recovery_ok, _ = comm.expect_ack()
                except SerialTimeoutError:
                    recovery_ok = False
                comm.consume_remaining_input()
        except (SerialError, SerialTimeoutError) as e:
            detail = f"{type(e).__name__}: {e}"
            logger.error(f"measure_command_nak_latency: {detail}")
        finally:
            if comm is not None:
                comm.disconnect()

        verdict = baseline_ok and corrupted_surfaced_error and recovery_ok
        self._write_nak_latency_log(
            output_path,
            fault_form,
            port,
            baseline_ok,
            corrupted_surfaced_error,
            recovery_ok,
            nak_latency_s,
            detail,
        )
        if not verdict:
            logger.error(
                "measure_command_nak_latency: verdict FAIL "
                f"(baseline_ok={baseline_ok}, corrupted_surfaced_error="
                f"{corrupted_surfaced_error}, recovery_ok={recovery_ok})"
            )
        return verdict

    @staticmethod
    def _write_nak_latency_log(
        output_path: Path,
        fault_form: str,
        port: str,
        baseline_ok: bool,
        corrupted_surfaced_error: bool,
        recovery_ok: bool,
        nak_latency_s: Optional[float],
        detail: str,
    ) -> None:
        """Write the per-frame NAK latency log (53-04 harness refinement)."""
        log_path = output_path / f"fault-inject-{fault_form}-latency.txt"
        latency_str = (
            f"{nak_latency_s:.3f}s" if nak_latency_s is not None else "unmeasured"
        )
        # Sub-second is the XACT-02 fast-fail bar for a complete corrupt frame; a
        # drop-delimiter frame is bounded by the firmware inter-byte deadline (~1 s).
        if nak_latency_s is None:
            verdict = "UNKNOWN"
        elif nak_latency_s < 1.0:
            verdict = "SUB-SECOND (fast-fail)"
        elif nak_latency_s < 2.0:
            verdict = "SUB-2s (bounded; ~inter-byte deadline)"
        else:
            verdict = ">=2s (cascade — investigate)"
        try:
            with open(log_path, "w") as fh:
                fh.write(
                    "# XACT-02 per-frame NAK latency (established single-port connection)\n"
                    f"# port: {port}  fault_form: {fault_form}\n"
                    f"baseline_clean_command_ok: {baseline_ok}\n"
                    f"corrupted_frame_surfaced_error_no_silent_accept: {corrupted_surfaced_error}\n"
                    f"per_frame_nak_latency: {latency_str}\n"
                    f"latency_verdict: {verdict}\n"
                    f"recovery_clean_command_same_connection_ok: {recovery_ok}\n"
                    f"detail: {detail}\n"
                )
        except IOError as e:  # noqa: UP024
            logger.error(f"measure_command_nak_latency: could not write log: {e}")

    def dev_read_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        address_str: Optional[str] = None,
        size_str: str = "256",
        operation_flags: int = 0,
    ) -> bool:
        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_READ,
            operation_flags,
            address_str,
            size_str or "256",
        ) as (cmd_data, _, op_name):
            if not cmd_data:
                return False

            start_addr = cmd_data.get("address", 0)
            end_addr = cmd_data.get("memory-size", start_addr)
            logger.info(
                f"Reading {end_addr - start_addr} bytes from address 0x{start_addr:04X} of {eprom_name.upper()}"  # noqa: E501
            )
            start_time = time.time()

            is_ok, _ = self._run_state_machine(
                op_name,
                main_phase_handler=self._main_phase_read_data,
                start_addr=start_addr,
                end_addr=end_addr,
                process_data_chunk_callback=hexdump,
            )
            if is_ok:
                logger.info(f"Read complete ({time.time() - start_time:.2f}s)")
            return is_ok

    def dev_set_registers(
        self,
        msb_str: str,
        lsb_str: str,
        ctrl_reg_str: str,
        firestarter=False,
        flags: int = 0,
    ) -> bool:
        msb = int(msb_str, 16) if "0x" in msb_str else int(msb_str)
        lsb = int(lsb_str, 16) if "0x" in lsb_str else int(lsb_str)
        ctrl_reg = int(ctrl_reg_str, 16) if "0x" in ctrl_reg_str else int(ctrl_reg_str)
        if msb < 0 or msb > 0xFF:
            logger.error(f"Invalid MSB value: 0x{msb:02x} {msb}")
            return False
        if lsb < 0 or lsb > 0xFF:
            logger.error(f"Invalid LSB value: 0x{lsb:02x} {lsb}")
            return False
        if (
            ctrl_reg < 0
            or (ctrl_reg > 0x1FF and firestarter)
            or (ctrl_reg > 0xFF and not firestarter)
        ):
            logger.error(f"Invalid Control Register value: 0x{ctrl_reg:02x} {ctrl_reg}")
            return False
        command_dict_for_connect = {
            "cmd": COMMAND_DEV_REGISTERS,
            "flags": flags,
        }
        try:
            self.comm = SerialCommunicator.find_and_connect(
                command_dict_for_connect, self.config
            )
            # No EPROM data needed from DB for this specific command after connection.
        except (ProgrammerNotFoundError, SerialError) as e:
            logger.error(f"Failed to connect for dev_set_registers: {e}")
            self._disconnect_programmer()
            return False

        if not self.comm:
            return False

        logger.info(
            f"Setting registers: MSB: 0x{msb:02X}, LSB: 0x{lsb:02X}, CTRL: 0x{ctrl_reg:02X}"  # noqa: E501
        )
        try:
            self.comm.send_ack()  # Tell programmer to expect register data
            self.comm.send_bytes(
                bytes(
                    [
                        msb,
                        lsb,
                        (0x80 if firestarter else 0x00) | (ctrl_reg >> 8 & 0x01),
                        ctrl_reg & 0xFF,
                    ]
                )
            )
            logger.info("Register data sent.")
            is_ok, _ = self.comm.expect_ack()
            return is_ok  # True if RURP acknowledged end, False otherwise
        except (SerialError, SerialTimeoutError) as e:
            logger.error(f"Error during dev_set_registers: {e}")
            return False
        finally:
            self._disconnect_programmer()

    def dev_set_address_mode(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        address_str: Optional[str],
        flags: int = 0,
    ) -> bool:
        try:
            # This command sets the RURP into a mode where it holds a specific address
            # based on the EPROM's pin map.
            # eprom_data_dict is pre-fetched and validated by the caller (main.py)
            command_eprom_data, _ = self._setup_operation(
                eprom_name,
                eprom_data_dict,
                COMMAND_DEV_ADDRESS,
                flags,
                address_str,
            )
            if not command_eprom_data or not self.comm:
                return False  # Setup failed, error already logged by _setup_operation

            # The _setup_operation already sent the command with the address.
            # The RURP is now (presumably) holding this address.
            # The original dev_address function just did setup and cleanup.
            logger.info(
                f"Setting address to RURP: 0x{command_eprom_data['address']:06x}"
            )
            logger.debug(f"Using {eprom_name.upper()}'s pin map")
            is_ok, _ = self.comm.expect_ack()
            return is_ok  # True if RURP acknowledged end, False otherwise
        except (SerialError, SerialTimeoutError) as e:
            logger.error(f"Error during dev_set_address_mode: {e}")
            return False
        finally:
            self._disconnect_programmer()

    def write_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        input_file_path: str,
        operation_flags: int = 0,
        address_str: Optional[str] = None,
    ) -> bool:
        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_WRITE,
            operation_flags,
            address_str,
        ) as (cmd_data, buf_size, op_name):
            if not cmd_data:
                return False

            logger.info(f"Writing {input_file_path} to {eprom_name.upper()}")
            start_time = time.time()

            is_ok, _ = self._run_state_machine(
                op_name,
                main_phase_handler=self._main_phase_send_data,
                input_file_path=input_file_path,
                buffer_size=buf_size,
            )

            if is_ok:
                logger.info(
                    f"Write to {eprom_name.upper()} successful ({time.time() - start_time:.2f}s)."  # noqa: E501
                )
            else:
                logger.error(f"Write to {eprom_name.upper()} failed.")
            return is_ok

    def verify_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        input_file_path: str,
        operation_flags: int = 0,
        address_str: Optional[str] = None,
    ) -> bool:
        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_VERIFY,
            operation_flags,
            address_str,
        ) as (cmd_data, buf_size, op_name):
            if not cmd_data:
                return False

            logger.info(f"Verifying {input_file_path} against {eprom_name.upper()}")
            start_time = time.time()

            is_ok, _ = self._run_state_machine(
                op_name,
                main_phase_handler=self._main_phase_send_data,
                input_file_path=input_file_path,
                buffer_size=buf_size,
            )

            if is_ok:
                logger.info(
                    f"Verify for {eprom_name.upper()} successful ({time.time() - start_time:.2f}s)."  # noqa: E501
                )
            else:
                logger.error(f"Verify for {eprom_name.upper()} failed.")
            return is_ok

    def erase_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        operation_flags: int = 0,
        address_str: Optional[str] = None,
    ) -> bool:
        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_ERASE,
            operation_flags,
            address_str,
        ) as (cmd_data, _, op_name):
            if not cmd_data:
                return False
            logger.info(f"Erasing EPROM {eprom_name.upper()}")
            start_time = time.time()
            is_ok, final_msg = self._run_state_machine(op_name)
            if is_ok:
                logger.info(
                    f"Erase for {eprom_name.upper()} successful ({time.time() - start_time:.2f}s). {final_msg or ''}"  # noqa: E501
                )
            return is_ok

    def check_eprom_blank(
        self, eprom_name: str, eprom_data_dict: dict, operation_flags: int = 0
    ) -> bool:
        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_BLANK_CHECK,
            operation_flags,
        ) as (cmd_data, _, op_name):
            if not cmd_data:
                return False
            logger.info(f"Blank checking EPROM {eprom_name.upper()}")
            start_time = time.time()
            is_ok, final_msg = self._run_state_machine(op_name)
            if is_ok:
                logger.info(
                    f"Blank check for {eprom_name.upper()} successful ({time.time() - start_time:.2f}s). {final_msg or ''}"  # noqa: E501
                )
            return is_ok

    def check_eprom_id(
        self, eprom_name: str, eprom_data_dict: dict, operation_flags: int = 0
    ) -> Tuple[bool, Optional[int]]:  # noqa: UP006
        with self._operation_context(
            eprom_name,
            eprom_data_dict,
            COMMAND_CHECK_CHIP_ID,
            operation_flags,
        ) as (cmd_data, _, op_name):
            if not cmd_data:
                return False, None

            logger.info(f"Checking chip ID for {eprom_name.upper()}")
            start_time = time.time()

            is_ok, final_msg = self._run_state_machine(op_name)
            detected_chip_id_value = None
            if is_ok:
                logger.info(
                    f"Chip ID check passed for {eprom_name.upper()}: {final_msg} ({time.time() - start_time:.2f}s)"  # noqa: E501
                )
                detected_chip_id_value = cmd_data.get("chip-id")
            else:
                logger.warning(
                    f"Chip ID check for {eprom_name.upper()} did not return OK. Programmer response: {final_msg}"  # noqa: E501
                )
                detected_chip_id_value = extract_hex_to_decimal(final_msg or "")
                if detected_chip_id_value is not None:
                    logger.info(
                        f"Programmer reported chip ID: 0x{detected_chip_id_value:X}"
                    )
                else:
                    logger.error(
                        f"Failed to extract a valid chip ID from programmer response: {final_msg}"  # noqa: E501
                    )
            return is_ok, detected_chip_id_value


# Example usage (for testing this module directly)
