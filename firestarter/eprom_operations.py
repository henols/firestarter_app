"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

EPROM Operations Module
"""

import os
import time
import functools
import operator
import logging
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
from firestarter.eprom_info import print_eprom_list_table  # Changed import

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

    def start(self, total_steps: int):
        self.total_steps = total_steps
        self.current_step = 0
        if self.progress_callback:
            self.progress_callback(self.current_step, total_steps)
        else:
            logging_redirect_tqdm()
            self.pbar = tqdm.tqdm(total=total_steps, bar_format=bar_format)

    def update(self, completed_steps: int):
        self.total_steps += completed_steps
        if self.progress_callback:
            self.progress_callback(self.current_step, self.total_steps)
        if self.pbar:
            self.pbar.update(completed_steps)

    def close(self):
        if self.pbar:
            self.pbar.close()


class EpromOperator:
    """
    Handles various operations on EPROMs, such as reading, writing, verifying,
    erasing, and checking chip IDs. It utilizes an EpromDatabase instance for
    EPROM-specific data and a SerialCommunicator instance (managed per operation)
    for interacting with the EPROM programmer hardware.
    """

    def __init__(self, config: ConfigManager, progress_callback=None):
        self.comm: SerialCommunicator | None = None
        self.config = config
        self.progress_callback = progress_callback

    def _calculate_buffer_size(self) -> int:
        if (
            self.comm
            and self.comm.programmer_info
            and "leonardo" in self.comm.programmer_info.lower()
        ):
            return LEONARDO_BUFFER_SIZE
        return BUFFER_SIZE

    def _setup_operation(
        self,
        eprom_name: str,  # For logging
        eprom_data_dict: dict,  # Pre-fetched EPROM data
        cmd: int,
        operation_flags: int = 0,
        address: str | None = None,
        size: str | None = None,
    ) -> tuple[dict | None, int]:
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

    def _disconnect_programmer(self):
        if self.comm:
            self.comm.disconnect()
            self.comm = None

    def _perform_simple_command(
        self,
        eprom_name: str,  # For logging
        eprom_data_dict: dict,  # Pre-fetched
        cmd: int,
        operation_flags: int = 0,
        success_log_msg: str = "",
    ) -> bool:
        command_eprom_data, _ = self._setup_operation(
            eprom_name, eprom_data_dict, cmd, operation_flags
        )
        if not command_eprom_data or not self.comm:
            return False

        start_time = time.time()
        operation_name = [k for k, v in globals().items() if v == cmd][0].replace(
            "COMMAND_", ""
        )  # Get command name
        logger.info(
            f"{success_log_msg or f'Performing {operation_name} for {eprom_name.upper()}'}"
        )

        try:
            self.comm.send_ack()  # Tell programmer to proceed
            is_ok, msg = self.comm.expect_ok()
            if not is_ok:
                logger.error(f"{operation_name} failed for {eprom_name.upper()}: {msg}")
                return False
            logger.info(
                f"{operation_name} for {eprom_name.upper()} successful ({time.time() - start_time:.2f}s). {msg or ''}"
            )
            return True
        except (SerialError, SerialTimeoutError) as e:
            logger.error(f"Error during {operation_name} for {eprom_name.upper()}: {e}")
            return False
        finally:
            self._disconnect_programmer()

    def _process_pre_post(self, type: str, progress: ClassProgressHandler) -> bool:
        if not self.comm:
            return False

        self.comm.send_ack()  # Tell programmer to start sending data
        while True:
            res, message = self.comm.get_response()
            if res == "ERROR":
                logger.error(f"Programmer error during {type.lower()}: {message}")
                return False
            elif res == type:
                break
            elif res == "WARN":
                logger.warning(f"Programmer warning during {type.lower()}: {message}")

        logger.debug(f"{type.lower()} complete.")
        return True

    def _send_data_core(
        self, eprom_data_for_command: dict, input_file_path: str, buffer_size: int
    ) -> bool:
        if not self.comm:
            return False

        if not os.path.exists(input_file_path):
            logger.error(f"Input file {input_file_path} not found.")
            return False

        progress = ClassProgressHandler(self.progress_callback)
        try:
            if not self._process_pre_post("INIT", progress):
                return False

            with open(input_file_path, "rb") as file_handle:
                file_size = os.path.getsize(input_file_path)
                progress.start(file_size)

                while True:
                    data_chunk: bytes = file_handle.read(buffer_size)
                    if not data_chunk or len(data_chunk) == 0:
                        self.comm.send_done()
                        break

                    # Calculate an 8-bit XOR checksum to match the firmware.
                    checksum = functools.reduce(operator.xor, data_chunk, 0)
                    data = (
                        b"#"
                        + (len(data_chunk)).to_bytes(2, byteorder="big")
                        + checksum.to_bytes(1)
                    )
                    self.comm.send_bytes(data + data_chunk)
                    is_ok, msg = self.comm.expect_ok()
                    if not is_ok:
                        logger.error(f"Programmer did not ACK data chunk: {msg}")
                        return False

                    if progress:
                        progress.update(len(data_chunk))

            if not self._process_pre_post("END", progress):
                return False

        except (SerialError, SerialTimeoutError, EpromOperationError) as e:
            logger.error(f"Error sending file {input_file_path}: {e}")
            return False
        except IOError as e:
            logger.error(f"File I/O error with {input_file_path}: {e}")
            return False

        return True

    def _read_data_core(
        self,
        start_addr: int,
        end_addr: int,
        process_data_chunk_callback,  # New callback
    ):
        if not self.comm:
            return False
        data_size = end_addr - start_addr

        progress = ClassProgressHandler(self.progress_callback)
        try:
            if not self._process_pre_post("INIT", progress):
                return False

            progress.start(data_size)
            while True:
                response_type, message = self.comm.get_response()
                if response_type == "DATA":
                    payload = self.comm.read_data_block()
                    if not payload:
                        logger.warning("Received DATA signal but no data followed.")
                        break

                    process_data_chunk_callback(
                        start_addr, payload
                    )  # Call callback for processing
                    start_addr += len(payload)
                    self.comm.send_ack()
                    if progress:
                        progress.update(len(payload))

                elif response_type == "DONE":
                    progress.close()
                    logger.info("EPROM read complete.")
                    break
                elif response_type == "WARN":
                    logger.warning(f"Programmer warning during read: {message}")
                elif response_type == "ERROR":
                    logger.error(f"Programmer error during read: {message}")
                    raise EpromOperationError(f"Programmer error: {message}")
                else:
                    message = (
                        f"Unexpected response during read: {response_type} - {message}"
                    )
                    logger.error(message)
                    raise EpromOperationError(message)

            if not self._process_pre_post("END", progress):
                return False

        except (SerialError, SerialTimeoutError, EpromOperationError) as e:
            logger.error(f"Error during read: {e}")
            return False
        finally:
            self._disconnect_programmer()
            progress.close()
        return True

    def read_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        output_file: str | None = None,
        operation_flags: int = 0,
        address_str: str | None = None,
        size_str: str | None = None,
    ) -> bool:
        # Existing setup, validation, logging...
        command_eprom_data, _ = self._setup_operation(
            eprom_name,
            eprom_data_dict,
            COMMAND_READ,
            operation_flags,
            address_str,
            size_str,
        )
        if not command_eprom_data or not self.comm:
            return False

        actual_output_file = output_file or f"{eprom_name.upper()}.bin"
        logger.info(
            f"Reading EPROM {eprom_name.upper()}, saving to {actual_output_file}"
        )
        start_addr = command_eprom_data.get("address", 0)
        end_addr = command_eprom_data.get("memory-size", 0)
        start_time = time.time()

        def _write_to_file(address, data_chunk):
            file_handle.seek(address)
            file_handle.write(data_chunk)

        try:
            with open(actual_output_file, "wb") as file_handle:
                if not self._read_data_core(start_addr, end_addr, _write_to_file):
                    return False
            logger.info(
                f"Read complete ({time.time() - start_time:.2f}s). Data saved to {actual_output_file}"
            )
            return True
        except IOError as e:
            logger.error(f"File I/O error with {actual_output_file}: {e}")
            return False

    def dev_read_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        address_str: str | None = None,
        size_str: str = "256",
        operation_flags: int = 0,
    ) -> bool:
        if not size_str:
            size_str = "256"

        command_eprom_data, _ = self._setup_operation(
            eprom_name,
            eprom_data_dict,
            COMMAND_READ,
            operation_flags,
            address_str,
            size_str,
        )
        if not command_eprom_data or not self.comm:
            return False

        start_addr = command_eprom_data.get("address", 0)
        end_addr = command_eprom_data.get("memory-size", start_addr)
        data_size = end_addr - start_addr
        logger.info(
            f"Reading {data_size} bytes from address 0x{start_addr:04X} of {eprom_name.upper()}"
        )
        start_time = time.time()

        def _log_hexdump(address, data_chunk):
            hexdump(address, data_chunk)  # Log hexdump

        if not self._read_data_core(start_addr, end_addr, _log_hexdump):
            return False

        logger.info(f"Read complete ({time.time() - start_time:.2f}s)")
        return True

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
            is_ok, _ = self.comm.expect_ok()
            return is_ok  # True if RURP acknowledged end, False otherwise
        except (SerialError, SerialTimeoutError) as e:
            logger.error(f"Error during dev_set_registers: {e}")
            return False
        finally:
            self._disconnect_programmer()

    def dev_set_address_mode(
        self, eprom_name: str, eprom_data_dict: dict, address_str: str, flags: int = 0
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
            is_ok, _ = self.comm.expect_ok()
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
        address_str: str | None = None,
    ) -> bool:
        # eprom_data_dict is pre-fetched and validated by the caller (main.py)
        command_eprom_data, buffer_size = self._setup_operation(
            eprom_name, eprom_data_dict, COMMAND_WRITE, operation_flags, address_str
        )
        if not command_eprom_data or not self.comm:
            return False

        logger.info(f"Writing {input_file_path} to {eprom_name.upper()}")
        start_time = time.time()
        success = False
        try:
            # Programmer is now configured. It waits for data transfer protocol.
            success = self._send_data_core(
                command_eprom_data, input_file_path, buffer_size
            )
            if success:
                logger.info(
                    f"Write to {eprom_name.upper()} successful ({time.time() - start_time:.2f}s)."
                )
            else:
                logger.error(f"Write to {eprom_name.upper()} failed.")
            return success
        finally:
            self._disconnect_programmer()

    def verify_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        input_file_path: str,
        operation_flags: int = 0,
        address_str: str | None = None,
    ) -> bool:
        # eprom_data_dict is pre-fetched and validated by the caller (main.py)
        command_eprom_data, buffer_size = self._setup_operation(
            eprom_name, eprom_data_dict, COMMAND_VERIFY, operation_flags, address_str
        )
        if not command_eprom_data or not self.comm:
            return False

        logger.info(f"Verifying {input_file_path} against {eprom_name.upper()}")
        start_time = time.time()
        success = False
        try:
            success = self._send_data_core(
                command_eprom_data, input_file_path, buffer_size
            )
            if success:
                logger.info(
                    f"Verify for {eprom_name.upper()} successful ({time.time() - start_time:.2f}s)."
                )
            else:
                logger.error(f"Verify for {eprom_name.upper()} failed.")
            return success
        finally:
            self._disconnect_programmer()

    def erase_eprom(
        self, eprom_name: str, eprom_data_dict: dict, operation_flags: int = 0
    ) -> bool:
        # eprom_data_dict is pre-fetched and validated by the caller (main.py)
        return self._perform_simple_command(
            eprom_name,
            eprom_data_dict,
            COMMAND_ERASE,
            operation_flags,
            f"Erasing EPROM {eprom_name.upper()}",
        )

    def check_eprom_blank(
        self, eprom_name: str, eprom_data_dict: dict, operation_flags: int = 0
    ) -> bool:
        return self._perform_simple_command(
            eprom_name,
            eprom_data_dict,
            COMMAND_BLANK_CHECK,
            operation_flags,
            f"Blank checking EPROM {eprom_name.upper()}",
        )

    def check_eprom_id(
        self, eprom_name: str, eprom_data_dict: dict, operation_flags: int = 0
    ) -> tuple[bool, int | None]:
        # eprom_data_dict is pre-fetched and validated by the caller (main.py)
        command_eprom_data, _ = self._setup_operation(
            eprom_name, eprom_data_dict, COMMAND_CHECK_CHIP_ID, operation_flags
        )
        if not command_eprom_data or not self.comm:
            return False, None

        detected_chip_id_value: int | None = None

        logger.info(f"Checking chip ID for {eprom_name.upper()}")
        start_time = time.time()
        try:
            self.comm.send_ack()  # Tell programmer to proceed
            is_ok, message = (
                self.comm.expect_ok()
            )  # Message contains chip ID if OK, or error

            if is_ok:
                logger.info(
                    f"Chip ID check passed for {eprom_name.upper()}: {message} ({time.time() - start_time:.2f}s)"
                )
                # If response is OK, the ID matched the one sent in command_eprom_data
                detected_chip_id_value = command_eprom_data.get("chip-id")
                return True, detected_chip_id_value
            else:
                # If not OK, the message might be an error or a different detected ID
                logger.warning(
                    f"Chip ID check for {eprom_name.upper()} did not return OK. Programmer response: {message}"
                )
                detected_chip_id_value = extract_hex_to_decimal(message or "")
                if detected_chip_id_value is not None:
                    logger.info(
                        f"Programmer reported chip ID: 0x{detected_chip_id_value:X}"
                    )
                else:
                    logger.error(
                        f"Failed to extract a valid chip ID from programmer response: {message}"
                    )

                return False, detected_chip_id_value

        except (SerialError, SerialTimeoutError) as e:
            logger.error(f"Error during chip ID check for {eprom_name.upper()}: {e}")
            return False, None
        finally:
            self._disconnect_programmer()


# Example usage (for testing this module directly)
if __name__ == "__main__":
    # Setup basic logging to see output from the EpromOperator and other modules
    logging.basicConfig(
        level=logging.DEBUG,  # Use logging.INFO for less verbose output
        format="[%(levelname)s:%(name)s:%(lineno)d] %(message)s",
    )

    logger.info("Starting EPROM operation test...")

    # Initialize the EPROM database (it's a singleton)
    try:

        from firestarter.database import EpromDatabase

        db = EpromDatabase()
        logger.debug("EpromDatabase initialized.")
    except Exception as e:
        logger.error(f"Failed to initialize EpromDatabase: {e}")
        exit(1)

    # Create an instance of the EpromOperator
    eprom_op = EpromOperator(db)
    logger.debug("EpromOperator initialized.")

    # Define the EPROM to read and the output file
    eprom_name_to_read = "W27C512"
    eprom_name_to_read = "SST27SF512"
    # You can specify an output file, or let it default (e.g., "W27C512.bin")
    custom_output_file = f"{eprom_name_to_read}_test_read.bin"

    logger.info(
        f"Attempting to read EPROM '{eprom_name_to_read}' to '{custom_output_file}'..."
    )

    # Perform the read operation
    # We'll use default flags, read the entire EPROM from the beginning.
    # This example usage needs to be updated to reflect the new call signature
    # (fetching eprom_data_dict first). For brevity, I'll skip updating the example here,
    # as the main focus is the library code.
    try:
        full_eprom_data_for_test = db.get_eprom(eprom_name_to_read)
        eprom_data_for_read = None
        if full_eprom_data_for_test:
            eprom_data_for_read = db.convert_full_eprom_to_programmer_data(
                full_eprom_data_for_test
            )
        if eprom_data_for_read:
            success = eprom_op.read_eprom(
                eprom_name=eprom_name_to_read,
                eprom_data_dict=eprom_data_for_read,
                output_file=custom_output_file,
                operation_flags=FLAG_FORCE,
            )
        else:
            logger.error(f"EPROM {eprom_name_to_read} not found in database for test.")
            success = False

        if success:
            logger.info(
                f"Successfully read '{eprom_name_to_read}' and saved to '{custom_output_file}'."
            )
        else:
            logger.error(f"Failed to read EPROM '{eprom_name_to_read}'.")

    except ProgrammerNotFoundError:
        logger.error(
            "Programmer not found. Please ensure it's connected and drivers are installed."
        )
    except SerialError as se:
        logger.error(f"A serial communication error occurred: {se}")
    except EpromOperationError as eoe:
        logger.error(f"An EPROM operation error occurred: {eoe}")
    except Exception as e:
        logger.error(f"An unexpected error occurred during the read operation: {e}")
    finally:
        logger.info("EPROM operation test finished.")
