"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

EPROM Operations Module (Refactored)
"""

import os
import time
import functools
import operator
import logging
from typing import Optional, Tuple, Dict, Callable
from contextlib import contextmanager
import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

from firestarter.constants import *
from firestarter.serial_comm import (
    SerialCommunicator,
    ProgrammerNotFoundError,
    SerialError,
    SerialTimeoutError,
)
from firestarter.config import ConfigManager
from firestarter.utils import extract_hex_to_decimal

logger = logging.getLogger("EpromOperator")

bar_format = "{l_bar}{bar}| {n:#06x}/{total:#06x} bytes "


def build_flags(ignore_blank_check=False, force=False, vpe_as_vpp=False, verbose=False):
    flags = 0
    if ignore_blank_check:
        flags |= FLAG_SKIP_ERASE
        flags |= FLAG_SKIP_BLANK_CHECK
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

        logger.info(f"{address+i:08x}: {hex_str:<{width * 3}} {ascii_str}")


class EpromOperationError(Exception):
    """Custom exception for EPROM operation failures."""

    pass


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
            # If no progress bar, we can't do much with incremental updates without a total.
            logger.info(f"Progress: +{completed_steps} steps")

    def set_progress(self, current, total):
        if self.total_steps != total or (
            not self.pbar and not self.progress_callback
        ):
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

    def __init__(self, config: ConfigManager, progress_callback: Optional[Callable] = None):
        self.comm: SerialCommunicator | None = None
        self.config = config
        self.progress_callback = progress_callback
    
    def _calculate_buffer_size(self) -> int:
        # For write/verify, we now use a "pull" protocol where the Arduino
        # requests a data block when it's ready. This means we can send a full
        # page at a time, matching the firmware's internal buffer size,
         # without worrying about overflowing the serial buffer.
        return BUFFER_SIZE  # This is 512 in constants.py

    def _setup_operation( # Remains largely the same, as it's a prerequisite for the context manager
        self,
        eprom_name: str,  # For logging
        eprom_data_dict: dict,  # Pre-fetched EPROM data
        cmd: int,
        operation_flags: int = 0,
        address: Optional[str] = None,
        size: Optional[str] = None,
    ) -> Tuple[Optional[Dict], int]:
        """
        Prepares for an EPROM operation: uses pre-fetched EPROM data, sets up command, and connects.
        Returns (eprom_data_for_command, buffer_size) or (None, 0) on failure.
        """
        operation = [k for k, v in globals().items() if v == cmd][0].replace(
            "COMMAND_", ""
        )  # Get command name
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
                addr = int(address, 16) if "0x" in address.lower() else int(address)
                command_dict["address"] = addr
            except ValueError:
                logger.error(f"Invalid address format: {address}")
                return None, 0

        # Special handling for read operation size
        if cmd == COMMAND_READ and size:
            try:
                read_size = int(size, 16) if "0x" in size.lower() else int(size)
                # 'memory-size' in command_dict will define the end address for read

                command_dict["memory-size"] = addr + read_size
            except ValueError:
                logger.error(f"Invalid size format: {size}")
                return None, 0

        try:
            self.comm = SerialCommunicator.find_and_connect(command_dict, self.config)
            buffer_size = self._calculate_buffer_size()
            logger.debug(
                f"Operation {operation} setup for {eprom_name} (state {cmd}) complete ({time.time() - start_time:.2f}s). Buffer size: {buffer_size}"
            )
            return command_dict, buffer_size
        except (ProgrammerNotFoundError, SerialError) as e:
            logger.error(f"Failed to setup operation {operation} for {eprom_name}: {e}")
            self._disconnect_programmer()  # Ensure comm is None if setup fails
            return None, 0

    @contextmanager
    def _operation_context(self, eprom_name: str, eprom_data_dict: dict, cmd: int, operation_flags: int = 0, address: Optional[str] = None, size: Optional[str] = None):
        """A context manager to handle EPROM operation setup and teardown."""
        command_dict, buffer_size = self._setup_operation(
            eprom_name, eprom_data_dict, cmd, operation_flags, address, size
        )
        if not command_dict or not self.comm:
            yield None, None, None # Yield None to indicate setup failure
            return

        operation_name = [k for k, v in globals().items() if v == cmd][0].replace("COMMAND_", "")
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

    def _run_state_machine(self, operation_name: str, main_phase_handler: Optional[Callable] = None, **handler_kwargs) -> Tuple[bool, Optional[str]]:
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
                end_msg = self._execute_phase("END", progress)

                # --- Final ACK to complete transaction ---
                self.comm.send_ack()
                return True, final_msg
        except (SerialError, SerialTimeoutError, EpromOperationError) as e:
            logger.error(f"Communication error during {operation_name}: {e}")
            return False, str(e)
        finally:
            progress.close()

    def _execute_phase(self, phase_name: str, progress: ClassProgressHandler) -> Optional[str]:
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
                raise EpromOperationError(f"Programmer error during {phase_name.lower()}: {response.message}")
            self._handle_progress_response(response, progress)
        logger.debug(f"{phase_name.lower()} complete.")
        return final_msg

    def _handle_progress_response(self, response, progress: ClassProgressHandler):
        """Helper to process DATA, WARN, OK during a state phase."""
        if response.type == "DATA":
            try:
                if response.message and "/" in response.message:
                    current, total = map(int, response.message.split('/'))
                    if progress: progress.set_progress(current, total)
                elif response.message:
                    progress.update(int(response.message))
            except (ValueError, TypeError):
                pass # Not a parsable progress update
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
                raise EpromOperationError(response.message)
            if response.type == "OK" and final_msg is None:
                final_msg = response.message # Capture final message from MAIN's OK
            self._handle_progress_response(response, progress)
        return final_msg

    def _main_phase_send_data(self, progress: ClassProgressHandler, input_file_path: str, buffer_size: int) -> None:
        """Main phase handler for writing or verifying data."""
        if not os.path.exists(input_file_path):
            raise EpromOperationError(f"Input file {input_file_path} not found.")

        with open(input_file_path, "rb") as file_handle:
            file_size = os.path.getsize(input_file_path)
            progress.start(file_size)
            
            while True:
                response = self.comm.get_response()
                if response.type == "MAIN":
                    break # Main phase is complete
                if response.type != "OK":
                    raise EpromOperationError(f"Programmer did not request data chunk, got {response.type}: {response.message}")

                if file_handle.tell() < file_size:
                    data_chunk = file_handle.read(buffer_size)
                    checksum = functools.reduce(operator.xor, data_chunk, 0)
                    header = b"#" + len(data_chunk).to_bytes(2, "big") + checksum.to_bytes(1)
                    self.comm.send_bytes(header + data_chunk)
                    progress.update(len(data_chunk))
                else:
                    self.comm.send_done()

    def _main_phase_read_data(self, progress: ClassProgressHandler, start_addr: int, end_addr: int, process_data_chunk_callback: Callable):
        """Main phase handler for reading data."""
        data_size = end_addr - start_addr
        if data_size > 0:
            progress.start(data_size)

        while True:
            response = self.comm.get_response()
            if response.type == "MAIN":
                logger.info("EPROM read complete.")
                break
            if response.type == "ERROR":
                raise EpromOperationError(f"Programmer error during read: {response.message}")
            if response.type == "DATA":
                payload = self.comm.read_data_block()
                if not payload:
                    logger.warning("Received DATA signal but no data followed.")
                    continue
                process_data_chunk_callback(start_addr, payload)
                start_addr += len(payload)
                progress.update(len(payload))
                self.comm.send_ack()
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
        with self._operation_context(eprom_name, eprom_data_dict, COMMAND_READ, operation_flags, address_str, size_str) as (cmd_data, _, op_name):
            if not cmd_data: return False

            actual_output_file = output_file or f"{eprom_name.upper()}.bin"
            logger.info(f"Reading EPROM {eprom_name.upper()}, saving to {actual_output_file}")
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
                        process_data_chunk_callback=_write_to_file
                    )
                if is_ok:
                    logger.info(f"Read complete ({time.time() - start_time:.2f}s). Data saved to {actual_output_file}")
                return is_ok
            except IOError as e:
                logger.error(f"File I/O error with {actual_output_file}: {e}")
                return False

    # --- DEV Methods ---

    def dev_read_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        address_str: Optional[str] = None,
        size_str: str = "256",
        operation_flags: int = 0,
    ) -> bool:
        with self._operation_context(eprom_name, eprom_data_dict, COMMAND_READ, operation_flags, address_str, size_str or "256") as (cmd_data, _, op_name):
            if not cmd_data: return False

            start_addr = cmd_data.get("address", 0)
            end_addr = cmd_data.get("memory-size", start_addr)
            logger.info(f"Reading {end_addr - start_addr} bytes from address 0x{start_addr:04X} of {eprom_name.upper()}")
            start_time = time.time()

            is_ok, _ = self._run_state_machine(
                op_name,
                main_phase_handler=self._main_phase_read_data,
                start_addr=start_addr,
                end_addr=end_addr,
                process_data_chunk_callback=hexdump
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
            f"Setting registers: MSB: 0x{msb:02X}, LSB: 0x{lsb:02X}, CTRL: 0x{ctrl_reg:02X}"
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
        self, eprom_name: str, eprom_data_dict: dict, address_str: Optional[str], flags: int = 0
    ) -> bool:
        try:
            # This command sets the RURP into a mode where it holds a specific address
            # based on the EPROM's pin map.
            # eprom_data_dict is pre-fetched and validated by the caller (main.py)
            command_eprom_data, _ = self._setup_operation(
                eprom_name, eprom_data_dict, COMMAND_DEV_ADDRESS, flags, address_str
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
        with self._operation_context(eprom_name, eprom_data_dict, COMMAND_WRITE, operation_flags, address_str) as (cmd_data, buf_size, op_name):
            if not cmd_data: return False

            logger.info(f"Writing {input_file_path} to {eprom_name.upper()}")
            start_time = time.time()

            is_ok, _ = self._run_state_machine(op_name, main_phase_handler=self._main_phase_send_data, input_file_path=input_file_path, buffer_size=buf_size)
            
            if is_ok:
                logger.info(f"Write to {eprom_name.upper()} successful ({time.time() - start_time:.2f}s).")
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
        with self._operation_context(eprom_name, eprom_data_dict, COMMAND_VERIFY, operation_flags, address_str) as (cmd_data, buf_size, op_name):
            if not cmd_data: return False

            logger.info(f"Verifying {input_file_path} against {eprom_name.upper()}")
            start_time = time.time()

            is_ok, _ = self._run_state_machine(op_name, main_phase_handler=self._main_phase_send_data, input_file_path=input_file_path, buffer_size=buf_size)

            if is_ok:
                logger.info(f"Verify for {eprom_name.upper()} successful ({time.time() - start_time:.2f}s).")
            else:
                logger.error(f"Verify for {eprom_name.upper()} failed.")
            return is_ok

    def erase_eprom(
        self, eprom_name: str, eprom_data_dict: dict, operation_flags: int = 0
    ) -> bool:
        with self._operation_context(eprom_name, eprom_data_dict, COMMAND_ERASE, operation_flags) as (cmd_data, _, op_name):
            if not cmd_data: return False
            logger.info(f"Erasing EPROM {eprom_name.upper()}")
            start_time = time.time()
            is_ok, final_msg = self._run_state_machine(op_name)
            if is_ok:
                logger.info(f"Erase for {eprom_name.upper()} successful ({time.time() - start_time:.2f}s). {final_msg or ''}")
            return is_ok

    def check_eprom_blank(
        self, eprom_name: str, eprom_data_dict: dict, operation_flags: int = 0
    ) -> bool:
        with self._operation_context(eprom_name, eprom_data_dict, COMMAND_BLANK_CHECK, operation_flags) as (cmd_data, _, op_name):
            if not cmd_data: return False
            logger.info(f"Blank checking EPROM {eprom_name.upper()}")
            start_time = time.time()
            is_ok, final_msg = self._run_state_machine(op_name)
            if is_ok:
                logger.info(f"Blank check for {eprom_name.upper()} successful ({time.time() - start_time:.2f}s). {final_msg or ''}")
            return is_ok

    def check_eprom_id(
        self, eprom_name: str, eprom_data_dict: dict, operation_flags: int = 0
    ) -> Tuple[bool, Optional[int]]:
        with self._operation_context(eprom_name, eprom_data_dict, COMMAND_CHECK_CHIP_ID, operation_flags) as (cmd_data, _, op_name):
            if not cmd_data: return False, None

            logger.info(f"Checking chip ID for {eprom_name.upper()}")
            start_time = time.time()

            is_ok, final_msg = self._run_state_machine(op_name)
            detected_chip_id_value = None
            if is_ok:
                logger.info(f"Chip ID check passed for {eprom_name.upper()}: {final_msg} ({time.time() - start_time:.2f}s)")
                detected_chip_id_value = cmd_data.get("chip-id")
            else:
                logger.warning(f"Chip ID check for {eprom_name.upper()} did not return OK. Programmer response: {final_msg}")
                detected_chip_id_value = extract_hex_to_decimal(final_msg or "")
                if detected_chip_id_value is not None:
                    logger.info(f"Programmer reported chip ID: 0x{detected_chip_id_value:X}")
                else:
                    logger.error(f"Failed to extract a valid chip ID from programmer response: {final_msg}")
            return is_ok, detected_chip_id_value


# Example usage (for testing this module directly)
