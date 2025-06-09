"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

EPROM Operations Module
"""

import os
import time
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
from firestarter.database import EpromDatabase
from firestarter.ic_layout import EpromSpecBuilder
from firestarter.utils import extract_hex_to_decimal
from firestarter.eprom_info import print_eprom_list_table  # Changed import

logger = logging.getLogger("EPROM")

bar_format = "{l_bar}{bar}| {n:#06x}/{total:#06x} bytes "


def build_flags(ignore_blank_check=False, force=False, vpe_as_vpp=False):
    flags = 0
    if ignore_blank_check:
        flags |= FLAG_SKIP_ERASE
        flags |= FLAG_SKIP_BLANK_CHECK
    if force:
        flags |= FLAG_FORCE
    if vpe_as_vpp:
        flags |= FLAG_VPE_AS_VPP
    return flags


def hexdump(address, data, width=16):
    """
    Prints a hexdump similar to xxd.
    :param data: The data to be printed (bytes).
    :param width: Number of bytes per line.
    """
    for i in range(0, len(data), width):
        chunk = data[i : i + width]
        hex_part = " ".join(f"{byte:02x}" for byte in chunk)
        ascii_part = "".join(
            (chr(byte) if 32 <= byte <= 126 else ".") for byte in chunk
        )
        logger.info(f"{address+i:08x}: {hex_part:<{width * 3}} {ascii_part}")


class EpromOperationError(Exception):
    """Custom exception for EPROM operation failures."""

    pass


class EpromOperator:
    """
    Handles various operations on EPROMs, such as reading, writing, verifying,
    erasing, and checking chip IDs. It utilizes an EpromDatabase instance for
    EPROM-specific data and a SerialCommunicator instance (managed per operation)
    for interacting with the EPROM programmer hardware.
    """

    def __init__(self, db: EpromDatabase):
        self.db = db
        self.comm: SerialCommunicator | None = None

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
        eprom_name: str, # For logging
        eprom_data_dict: dict, # Pre-fetched EPROM data
        cmd: int,
        operation_flags: int = 0,
        address_str: str | None = None,
        size_str: str | None = None,
    ) -> tuple[dict | None, int]:
        """
        Prepares for an EPROM operation: uses pre-fetched EPROM data, sets up command, and connects.
        Returns (eprom_data_for_command, buffer_size) or (None, 0) on failure.
        """
        start_time = time.time()
        # eprom_data_dict is assumed to be valid and pre-fetched by the caller (main.py)
        logger.debug(f"Using EPROM data for {eprom_name}: {eprom_data_dict}")
        command_dict = eprom_data_dict.copy()  # Work with a copy for the command
        command_dict["cmd"] = cmd
        # Combine base flags from EPROM data with operation-specific flags
        command_dict["flags"] = eprom_data_dict.get("flags", 0) | operation_flags

        if address_str:
            try:
                command_dict["address"] = (
                    int(address_str, 16)
                    if "0x" in address_str.lower()
                    else int(address_str)
                )
            except ValueError:
                logger.error(f"Invalid address format: {address_str}")
                return None, 0

        # Special handling for read operation size
        if cmd == COMMAND_READ and size_str:
            try:
                read_size = (
                    int(size_str, 16) if "0x" in size_str.lower() else int(size_str)
                )
                # 'memory-size' in command_dict will define the end address for read
                current_start_address = command_dict.get("address", 0)
                command_dict["memory-size"] = current_start_address + read_size
            except ValueError:
                logger.error(f"Invalid size format: {size_str}")
                return None, 0

        try:
            self.comm = SerialCommunicator.find_and_connect(command_dict)
            buffer_size = self._calculate_buffer_size()
            logger.debug(
                f"Operation setup for {eprom_name} (state {cmd}) complete ({time.time() - start_time:.2f}s). Buffer size: {buffer_size}"
            )
            return command_dict, buffer_size
        except (ProgrammerNotFoundError, SerialError) as e:
            logger.error(f"Failed to setup operation for {eprom_name}: {e}")
            self._disconnect_programmer()  # Ensure comm is None if setup fails
            return None, 0

    def _disconnect_programmer(self):
        if self.comm:
            self.comm.disconnect()
            self.comm = None

    def _perform_simple_command(
        self,
        eprom_name: str, # For logging
        eprom_data_dict: dict, # Pre-fetched
        cmd: int,
        operation_flags: int = 0,
        success_log_msg: str = "",
    ) -> bool:
        command_eprom_data, _ = self._setup_operation(eprom_name, eprom_data_dict, cmd, operation_flags)
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

    def _read_data_from_programmer(self, buffer_size: int) -> bytes | None:
        if not self.comm:
            return None
        try:
            response_type, message = (
                self.comm.get_response()
            )  # Wait for DATA: or OK: or ERROR:
            if response_type == "DATA":
                # The number of bytes to read should ideally be sent by the programmer
                # or known. Assuming buffer_size is the max chunk.
                # The original code read `buffer_size` bytes directly after DATA.
                # Let's assume the programmer sends data in chunks up to buffer_size.
                # For now, we'll rely on the programmer sending data and then an OK when done.
                # This part might need refinement based on exact RURP protocol for DATA.
                # A simple approach: read what's available up to buffer_size.
                data = self.comm.read_data_block(buffer_size)  # This needs num_bytes
                # This is problematic if programmer doesn't say how much.
                # Let's assume DATA: means a fixed block of buffer_size is coming.
                # Or, DATA: <num_bytes_to_follow>
                # The old code just did connection.read(buffer_size)
                # This implies the programmer sends buffer_size chunks.

                # The original code read `buffer_size` bytes. If the RURP sends exactly `buffer_size`
                # or less followed by OK, this is fine. If it sends more, this needs adjustment.
                # Let's assume for now `read_data_block` is appropriate if the programmer sends fixed chunks.
                # The RURP protocol seems to be: Host sends OK, RURP sends DATA:, Host reads, Host sends OK.

                # The RURP protocol is:
                # Host: OK (to start read)
                # RURP: DATA: (signals data coming)
                # RURP: <sends data bytes>
                # Host: OK (acknowledge receipt of data chunk)
                # ... repeat until RURP sends OK: (signals end of data)

                # This means after RURP sends "DATA:", it will send bytes.
                # The host needs to know how many bytes to expect in this chunk.
                # The original `read_data` function read `buffer_size` bytes.
                # This implies the programmer sends data in chunks of `buffer_size`.

                # Let's refine: `get_response` gets "DATA:". Then we read.
                # The `read_data_block` in SerialCommunicator is designed for this.
                # The number of bytes for `read_data_block` must be known.
                # The original code just did `connection.read(buffer_size)`.

                # For now, let's assume the programmer sends `buffer_size` chunks.
                # This part is tricky without exact protocol spec for DATA transfer size.
                # The original `read_data` function implies the host reads `buffer_size` bytes
                # after receiving "DATA:", then sends "OK".

                # Let's assume the programmer sends data and then waits for an ACK.
                # The amount of data sent per "DATA:" message needs to be defined by the protocol.
                # The original code implies it's `buffer_size`.

                # Re-evaluating: The loop in original `read` implies `read_data` gets one chunk.
                # `read_data` waits for "DATA:", reads `buffer_size`, sends "OK".
                # This seems to be the flow.

                # The `SerialCommunicator.read_data_block(num_bytes)` is the tool.
                # The question is, what is `num_bytes`?
                # If RURP sends "DATA:", then `buffer_size` bytes, then waits for "OK", then this is it.

                # Let's simplify: if `get_response` returns "DATA", we then read `buffer_size` bytes.
                # This matches the old logic.

                # The `read_data_block` in SerialCommunicator is fine if we pass `buffer_size` to it.
                # However, the `get_response` in SerialCommunicator already consumes the "DATA:" line.
                # So, if `get_response` returns "DATA", the next call should be to read the actual binary data.

                # Let's adjust `SerialCommunicator` or how we use it.
                # For now, assume `read_data_block` is called after "DATA:" is confirmed.
                # The `get_response` should probably not consume the DATA: line if we want to handle it here.
                # Or, `get_response` returns "DATA" and the message part is the data itself (if small).

                # Sticking to the original flow: `wait_for_response` (now `self.comm.get_response`)
                # gets "DATA". Then `connection.read(buffer_size)` (now `self.comm.read_data_block(buffer_size)`).
                # Then `write_ok` (now `self.comm.send_ack()`).

                # This means `_read_data_from_programmer` should be called *after* "DATA:" is received.
                # The loop in `read_eprom` will handle this.
                # This method is then just:
                data_bytes = self.comm.read_data_block(buffer_size)
                self.comm.send_ack()
                return data_bytes

            elif response_type == "OK":
                return None  # End of data
            elif response_type == "ERROR":
                logger.error(f"Programmer error during data read: {message}")
                raise EpromOperationError(f"Programmer error: {message}")
            else:  # Timeout or unexpected
                logger.error(
                    f"Unexpected response or timeout during data read: {response_type} - {message}"
                )
                raise EpromOperationError(
                    "Timeout or unexpected response during data read."
                )
        except (SerialError, SerialTimeoutError) as e:
            logger.error(f"Serial communication error during data read: {e}")
            raise EpromOperationError(f"Serial error: {e}") from e

    def _send_file_to_programmer(
        self, eprom_data_for_command: dict, input_file_path: str, buffer_size: int
    ) -> bool:
        if not self.comm:
            return False

        if not os.path.exists(input_file_path):
            logger.error(f"Input file {input_file_path} not found.")
            return False

        target_mem_size = eprom_data_for_command.get("memory-size", 0)
        # Address for write is part of the initial command setup via `eprom_data_for_command['address']`
        # The programmer handles writing from that offset.

        try:
            with open(input_file_path, "rb") as file_handle:
                file_size = os.path.getsize(input_file_path)

                # The original code had a warning if file_size != target_mem_size.
                # This might be too strict if only a partial write from an offset is intended.
                # The programmer should handle writing up to the EPROM's actual capacity
                # or the amount of data sent.
                # For now, we'll send the whole file.
                # The RURP protocol: Host sends length (2 bytes), RURP sends OK. Host sends data, RURP sends OK.

                bytes_sent_total = 0
                with (
                    logging_redirect_tqdm(),
                    tqdm.tqdm(total=file_size, bar_format=bar_format) as pbar,
                ):
                    while True:
                        data_chunk = file_handle.read(buffer_size)
                        if not data_chunk:
                            # End of file. Signal RURP with a zero-length chunk.
                            self.comm.send_bytes(int(0).to_bytes(2, byteorder="big"))
                            is_ok, _ = self.comm.expect_ok()
                            return (
                                is_ok  # True if RURP acknowledged end, False otherwise
                            )

                        # Send length of the upcoming data chunk
                        self.comm.send_bytes(
                            len(data_chunk).to_bytes(2, byteorder="big")
                        )
                        is_ok, msg = self.comm.expect_ok()
                        if not is_ok:
                            logger.error(
                                f"Programmer did not ACK data chunk length: {msg}"
                            )
                            return False

                        # Send the actual data chunk
                        self.comm.send_bytes(data_chunk)
                        is_ok, msg = self.comm.expect_ok()
                        if not is_ok:
                            logger.error(f"Programmer did not ACK data chunk: {msg}")
                            return False

                        bytes_sent_total += len(data_chunk)
                        pbar.update(len(data_chunk))

        except (SerialError, SerialTimeoutError, EpromOperationError) as e:
            logger.error(f"Error sending file {input_file_path}: {e}")
            return False
        except IOError as e:
            logger.error(f"File I/O error with {input_file_path}: {e}")
            return False
        # `finally` block for disconnect is handled by the calling public method.
        # This method should return True if all data is sent and ACKed.
        # The loop termination with zero-length chunk handles the final success.

    # --- Public Operation Methods ---

    def read_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        output_file: str | None = None,
        operation_flags: int = 0,
        address_str: str | None = None,
        size_str: str | None = None,
    ) -> bool:
        # eprom_data_dict is pre-fetched and validated by the caller (main.py)
        command_eprom_data, buffer_size = self._setup_operation(
            eprom_name, eprom_data_dict, COMMAND_READ, operation_flags, address_str, size_str
        )
        if not command_eprom_data or not self.comm:
            return False  # Setup failed

        actual_output_file = output_file or f"{eprom_name.upper()}.bin"
        logger.info(
            f"Reading EPROM {eprom_name.upper()}, saving to {actual_output_file}"
        )
        start_time = time.time()

        try:
            with open(actual_output_file, "wb") as file_handle:
                # Determine total bytes to read based on EPROM data used for the command
                # (which might have been adjusted by address_str and size_str)
                start_addr = command_eprom_data.get("address", 0)
                end_addr = command_eprom_data.get(
                    "memory-size", 0
                )  # This is end-address for read
                total_bytes_to_read = end_addr - start_addr
                if total_bytes_to_read <= 0:
                    logger.error(
                        f"Invalid read range: start {start_addr}, end {end_addr}"
                    )
                    return False

                self.comm.send_ack()  # Tell programmer to start sending data

                bytes_read_total = 0
                with (
                    logging_redirect_tqdm(),
                    tqdm.tqdm(total=total_bytes_to_read, bar_format=bar_format) as pbar,
                ):
                    while bytes_read_total < total_bytes_to_read:
                        # Check for initial "DATA:" or "OK:" (if done) or "ERROR:"
                        response_type, message = self.comm.get_response()

                        if response_type == "DATA":
                            # Programmer is ready to send a chunk.
                            # The size of this chunk is implicitly buffer_size by RURP protocol.
                            data_chunk = self.comm.read_data_block(buffer_size)
                            if not data_chunk:  # Should not happen if DATA was signaled
                                logger.warning(
                                    "Received DATA signal but no data followed."
                                )
                                break
                            file_handle.write(data_chunk)
                            self.comm.send_ack()  # Acknowledge receipt of the chunk
                            bytes_read_total += len(data_chunk)
                            pbar.update(len(data_chunk))
                        elif response_type == "OK":
                            logger.info("Programmer indicated end of data.")
                            break  # Successfully finished
                        elif response_type == "ERROR":
                            logger.error(f"Programmer error during read: {message}")
                            raise EpromOperationError(f"Programmer error: {message}")
                        else:  # Timeout or unexpected
                            logger.error(
                                f"Unexpected response or timeout during read: {response_type} - {message}"
                            )
                            raise EpromOperationError(
                                "Timeout or unexpected response during read."
                            )

            logger.info(
                f"Read complete ({time.time() - start_time:.2f}s). Data saved to {actual_output_file}"
            )
            return True

        except (EpromOperationError, SerialError, SerialTimeoutError) as e:
            logger.error(f"Error during EPROM read: {e}")
            return False
        except IOError as e:
            logger.error(f"File I/O error with {actual_output_file}: {e}")
            return False
        finally:
            self._disconnect_programmer()

    def dev_set_registers(
        self, msb_str: str, lsb_str: str, ctrl_reg_str: str, firestarter =False,flags: int = 0
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
            self.comm = SerialCommunicator.find_and_connect(command_dict_for_connect)
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
            self.comm.send_bytes(bytes([msb, lsb, (0x80 if firestarter else 0x00) | (ctrl_reg >> 8 & 0x01), ctrl_reg & 0xFF]))
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
            logger.info(f"Setting address to RURP: 0x{command_eprom_data['address']:06x}")
            logger.info(f"Using {eprom_name.upper()}'s pin map")
            is_ok, _ = self.comm.expect_ok()
            return  is_ok  # True if RURP acknowledged end, False otherwise
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
            success = self._send_file_to_programmer(
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
            success = self._send_file_to_programmer(
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

    def erase_eprom(self, eprom_name: str, eprom_data_dict: dict, operation_flags: int = 0) -> bool:
        # eprom_data_dict is pre-fetched and validated by the caller (main.py)
        return self._perform_simple_command(
            eprom_name,
            eprom_data_dict,
            COMMAND_ERASE,
            operation_flags,
            f"Erasing EPROM {eprom_name.upper()}",
        )

    def check_eprom_blank(self, eprom_name: str, eprom_data_dict: dict, operation_flags: int = 0) -> bool:
        # eprom_data_dict is pre-fetched and validated by the caller (main.py)
        return self._perform_simple_command(
            eprom_name,
            eprom_data_dict,
            COMMAND_BLANK_CHECK,
            operation_flags,
            f"Performing blank check for {eprom_name.upper()}",
        )

    def check_eprom_id(self, eprom_name: str, eprom_data_dict: dict, operation_flags: int = 0) -> bool:
        # eprom_data_dict is pre-fetched and validated by the caller (main.py)
        command_eprom_data, _ = self._setup_operation(
            eprom_name, eprom_data_dict, COMMAND_CHECK_CHIP_ID, operation_flags
        )
        if not command_eprom_data or not self.comm:
            return False

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
                return True
            else:
                # If not OK, the message might be an error or a different detected ID
                logger.warning(
                    f"Chip ID check for {eprom_name.upper()} did not return OK. Programmer response: {message}"
                )
                chip_id_decimal = extract_hex_to_decimal(message or "")
                if chip_id_decimal is not None:
                    logger.info(f"Programmer reported chip ID: 0x{chip_id_decimal:X}")
                    # Search database for this reported ID
                    found_eproms = self.db.search_chip_id(chip_id_decimal)
                    if found_eproms:
                        logger.info(
                            f"The reported Chip ID 0x{chip_id_decimal:X} matches the following EPROMs in the database:"
                        )
                        # Map raw data from search_chip_id to the format expected by print_eprom_list_table
                        mapped_found_eproms = [
                            self.db._map_data(ic, ic.get("manufacturer", "Unknown"))
                            for ic in found_eproms
                        ]
                        # Create an EpromSpecBuilder instance for the print function
                        spec_builder = EpromSpecBuilder(self.db)
                        print_eprom_list_table(mapped_found_eproms, spec_builder)
                    else:
                        logger.warning(
                            f"Reported Chip ID 0x{chip_id_decimal:X} not found in the database."
                        )
                else:
                    logger.error(
                        f"Failed to extract a valid chip ID from programmer response: {message}"
                    )

                # Return True if FLAG_FORCE is set, otherwise False because ID didn't match expected
                return bool(command_eprom_data.get("flags", 0) & FLAG_FORCE)

        except (SerialError, SerialTimeoutError) as e:
            logger.error(f"Error during chip ID check for {eprom_name.upper()}: {e}")
            return False
        finally:
            self._disconnect_programmer()

    def dev_read_eprom(
        self,
        eprom_name: str,
        eprom_data_dict: dict,
        address_str: str | None = None,
        size_str: str = "256",
        operation_flags: int = 0,
    ) -> bool:
        # Ensure size_str has a default if None, though current signature gives "256"
        size_to_read_str = size_str if size_str is not None else "256"
        # eprom_data_dict is pre-fetched and validated by the caller (main.py)
        command_eprom_data, buffer_size = self._setup_operation(
            eprom_name,
            eprom_data_dict,
            COMMAND_READ, # Dev read uses normal read command
            operation_flags,
            address_str,
            size_to_read_str,
        )
        if not command_eprom_data or not self.comm:
            return False

        start_addr_for_read = command_eprom_data.get("address", 0)
        # memory-size in command_eprom_data is now the end_address for the read
        end_addr_for_read = command_eprom_data.get("memory-size", start_addr_for_read)
        num_bytes_to_dev_read = end_addr_for_read - start_addr_for_read

        logger.info(
            f"Developer Read: Reading {num_bytes_to_dev_read} bytes from address 0x{start_addr_for_read:04X} of {eprom_name.upper()}"
        )
        start_time = time.time()

        try:
            self.comm.send_ack()  # Tell programmer to start sending data
            bytes_read_total = 0

            while bytes_read_total < num_bytes_to_dev_read:
                response_type, message = self.comm.get_response()
                if response_type == "DATA":
                    data_chunk = self.comm.read_data_block(
                        buffer_size
                    )  # RURP sends buffer_size chunks
                    if not data_chunk:
                        break

                    current_dump_address = start_addr_for_read + bytes_read_total
                    hexdump(current_dump_address, data_chunk)  # Log hexdump
                    self.comm.send_ack()  # Acknowledge chunk
                    bytes_read_total += len(data_chunk)
                elif response_type == "OK":
                    break  # End of data
                elif response_type == "ERROR":
                    raise EpromOperationError(f"Programmer error: {message}")
                else:
                    raise EpromOperationError("Timeout or unexpected response.")

            logger.info(f"Developer Read complete ({time.time() - start_time:.2f}s)")
            return True
        except (EpromOperationError, SerialError, SerialTimeoutError) as e:
            logger.error(f"Error during developer read: {e}")
            return False
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
    # You can specify an output file, or let it default (e.g., "W27C512.bin")
    custom_output_file = f"{eprom_name_to_read}_test_read.bin"

    logger.info(f"Attempting to read EPROM '{eprom_name_to_read}' to '{custom_output_file}'...")

    # Perform the read operation
    # We'll use default flags, read the entire EPROM from the beginning.
    # This example usage needs to be updated to reflect the new call signature
    # (fetching eprom_data_dict first). For brevity, I'll skip updating the example here,
    # as the main focus is the library code.
    try:
        eprom_data_for_read = db.get_eprom(eprom_name_to_read)
        if eprom_data_for_read:
            success = eprom_op.read_eprom(
                eprom_name=eprom_name_to_read,
                eprom_data_dict=eprom_data_for_read,
                output_file=custom_output_file,
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
