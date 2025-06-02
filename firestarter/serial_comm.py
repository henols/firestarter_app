"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Serial Communication Module
"""

import serial
import serial.tools.list_ports
import time
import json
import logging

from firestarter.constants import *
from firestarter.config import ConfigManager  # Assuming ConfigManager is refactored

logger = logging.getLogger("SerialComm")
rurp_logger = logging.getLogger("RURP")

DEFAULT_SERIAL_TIMEOUT = 1.0  # seconds for read operations
DEFAULT_RESPONSE_TIMEOUT = 10  # seconds for waiting for a specific response
CONNECTION_STABILIZE_DELAY = 2.0  # seconds after opening port

EXPECTED_PREFIXES = ["OK:", "INFO:", "DEBUG:", "ERROR:", "WARN:", "DATA:"]


class SerialError(Exception):
    """Custom exception for serial communication errors."""

    pass


class SerialTimeoutError(SerialError):
    """Custom exception for serial timeouts."""

    pass


class ProgrammerNotFoundError(SerialError):
    """Custom exception when no programmer is found."""

    pass


class SerialCommunicator:
    """
    Manages serial communication with the EPROM programmer hardware.
    It handles port connection, sending commands (including JSON-formatted ones),
    receiving and parsing responses, and error management for serial interactions.
    Includes a class method to find and connect to a compatible programmer
    across available serial ports.
    """

    def __init__(
        self,
        port: str,
        baud_rate: int = int(BAUD_RATE),
        timeout: float = DEFAULT_SERIAL_TIMEOUT,
    ):
        self.port_name = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.connection: serial.Serial | None = None
        self.programmer_info: str | None = None

        try:
            logger.debug(
                f"Attempting to connect to {self.port_name} at {self.baud_rate} baud."
            )
            self.connection = serial.Serial(
                port=self.port_name,
                baudrate=self.baud_rate,
                timeout=self.timeout,
            )
            time.sleep(CONNECTION_STABILIZE_DELAY)  # Allow port to stabilize
            logger.info(f"Successfully connected to {self.port_name}.")
        except (OSError, serial.SerialException) as e:
            logger.error(f"Failed to open serial port {self.port_name}: {e}")
            self.connection = None
            raise SerialError(f"Could not connect to {self.port_name}: {e}") from e

    def is_connected(self) -> bool:
        return self.connection is not None and self.connection.is_open

    def send_bytes(self, data_bytes: bytes) -> int:
        if not self.is_connected():
            raise SerialError("Not connected.")
        try:
            written_bytes = self.connection.write(data_bytes)
            self.connection.flush()
            logger.debug(f"Sent {written_bytes} bytes to {self.port_name}.")
            return written_bytes
        except serial.SerialTimeoutException as e:
            raise SerialTimeoutError(f"Timeout writing to {self.port_name}: {e}") from e
        except serial.SerialException as e:
            raise SerialError(f"Serial error writing to {self.port_name}: {e}") from e

    def send_string(self, data_string: str, encoding: str = "ascii") -> int:
        logger.debug(f"Sending string: {data_string}")
        return self.send_bytes(data_string.encode(encoding))

    def send_json_command(self, command_dict: dict) -> int:
        self._log_command_details(command_dict)
        json_data = json.dumps(command_dict, separators=(",", ":"))
        # json_data = json.dumps(command_dict)
        return self.send_string(json_data)

    def read_line_bytes(self) -> bytes | None:
        if not self.is_connected():
            raise SerialError("Not connected.")
        try:
            if self.connection.in_waiting > 0:
                return self.connection.readline()
            return None
        except serial.SerialException as e:
            raise SerialError(f"Serial error reading from {self.port_name}: {e}") from e

    def _parse_response_line(self, line_bytes: bytes) -> tuple[str | None, str | None]:
        if not line_bytes:
            return None, None
        # Filters a byte array to extract readable characters.
        res_bytes = bytes(b for b in line_bytes if 32 <= b <= 126)
        line_str = res_bytes.decode("ascii", errors="ignore") if res_bytes else ""
        # logger.debug(f"Received bytes: {line_bytes}")
        # logger.debug(f"Received line: {line_str}")
        if not line_str:
            return None, None

        for prefix in EXPECTED_PREFIXES:
            idx = line_str.find(prefix)
            if idx != -1:  # Prefix found in the line
                # The "effective" line starts from the found prefix
                effective_line_str = line_str[idx:]
                # The type is the prefix itself (minus the colon)
                response_type = prefix[:-1]
                # The message is everything after the prefix in the effective line
                message_content = effective_line_str[len(prefix) :].strip()
                return response_type, message_content
        return None, line_str

    def _log_rurp_feedback(self, response_type: str | None, message: str | None):
        if response_type and message:
            level = logging.DEBUG
            if response_type == "ERROR":
                level = logging.ERROR
            elif response_type == "WARN":
                level = logging.WARNING

            # Shorten prefix for debug, full for others
            log_prefix = (
                response_type[:1]
                if rurp_logger.isEnabledFor(logging.DEBUG)
                and response_type not in ["OK", "ERROR", "WARN"]
                else response_type
            )
            rurp_logger.log(level, f"{log_prefix}: {message}")

    def get_response(
        self, timeout: float = DEFAULT_RESPONSE_TIMEOUT
    ) -> tuple[str | None, str | None]:
        if not self.is_connected():
            raise SerialError("Not connected.")

        start_time = time.time()
        while time.time() - start_time < timeout:
            line_bytes = self.read_line_bytes()
            if line_bytes:
                response_type, message = self._parse_response_line(line_bytes)
                self._log_rurp_feedback(response_type, message)
                if response_type and response_type not in [
                    "INFO",
                    "DEBUG",
                ]:  # Return significant responses
                    return response_type, message
                start_time = time.time()
            time.sleep(0.01)  # Small delay to prevent busy-waiting
        logger.warning(f"Timeout waiting for a response from {self.port_name}.")
        raise SerialTimeoutError(
            f"Timeout waiting for a significant response from {self.port_name}."
        )

    def expect_ok(
        self, timeout: float = DEFAULT_RESPONSE_TIMEOUT
    ) -> tuple[bool, str | None]:
        while True:
            response_type, message = self.get_response(timeout)
            if response_type == "OK":
                return True, message
            elif response_type == "ERROR":
                return False, message

    def send_ack(self):
        self.send_string("OK")

    def consume_remaining_input(self, timeout: float = 0.5):
        if not self.is_connected():
            return

        start_time = time.time()
        # Temporarily set a short timeout for consuming
        original_timeout = self.connection.timeout
        self.connection.timeout = 0.05
        try:
            while time.time() - start_time < timeout:
                if self.connection.in_waiting > 0:
                    line_bytes = self.read_line_bytes()
                    if line_bytes:
                        response_type, message = self._parse_response_line(line_bytes)
                        self._log_rurp_feedback(
                            response_type, message
                        )  # Log what's being consumed
                        start_time = (
                            time.time()
                        )  # Reset timeout if we are still getting data
                    else:  # No more data in buffer or partial line
                        break
                else:  # No data in waiting
                    break
        finally:
            self.connection.timeout = original_timeout  # Restore original timeout

    def disconnect(self):
        if self.is_connected():
            try:
                self.consume_remaining_input()
                self.connection.close()
                logger.info(f"Disconnected from {self.port_name}.")
            except serial.SerialException as e:
                logger.error(f"Error closing port {self.port_name}: {e}")
            finally:
                self.connection = None
                self.programmer_info = None

    def _log_command_details(self, command_dict: dict):
        logger.debug(f"Sending command to programmer: {command_dict}")
        flags = command_dict.get("flags", 0)
        if flags:
            flag_details = []
            if flags & FLAG_FORCE:
                flag_details.append("Force")
            if flags & FLAG_CAN_ERASE:
                flag_details.append("CanErase")
            if flags & FLAG_SKIP_ERASE:
                flag_details.append("SkipErase")
            if flags & FLAG_SKIP_BLANK_CHECK:
                flag_details.append("SkipBlankCheck")
            if flags & FLAG_VPE_AS_VPP:
                flag_details.append("VPEasVPP")
            if flags & FLAG_CHIP_ENABLE:
                flag_details.append("ChipEnable")
            if flags & FLAG_OUTPUT_ENABLE:
                flag_details.append("OutputEnable")
            if flag_details:
                logger.debug(f"  Flags set: {', '.join(flag_details)} (0x{flags:02X})")

    @staticmethod
    def _list_potential_ports(
        preferred_port: str | None = None, config_manager: ConfigManager | None = None
    ) -> list[str]:
        ports = []
        # 1. Add preferred_port if specified
        if preferred_port:
            ports.append(preferred_port)

        # 2. Add port from config if ConfigManager is available
        if config_manager:
            saved_port = config_manager.get_value("port")
            if saved_port and saved_port not in ports:
                ports.append(saved_port)

        # 3. Scan system ports
        system_ports = serial.tools.list_ports.comports()
        for p in system_ports:
            if p.device not in ports:  # Avoid duplicates
                # Common keywords for Arduino, FTDI, CH340, etc.
                if (
                    p.manufacturer
                    and (
                        "Arduino" in p.manufacturer
                        or "FTDI" in p.manufacturer
                        or "CH340" in p.manufacturer
                    )
                ) or (p.description and "USB Serial" in p.description):
                    ports.append(p.device)

        logger.debug(f"Potential programmer ports found: {ports}")
        return ports

    @classmethod
    def find_and_connect(
        cls,
        command_to_send: dict,
        preferred_port: str | None = None,
        baud_rate: int = int(BAUD_RATE),
    ) -> "SerialCommunicator":
        config_manager = ConfigManager()  # Get singleton instance

        potential_ports = cls._list_potential_ports(preferred_port, config_manager)
        if not potential_ports:
            raise ProgrammerNotFoundError("No potential serial ports found.")

        for port_name in potential_ports:
            communicator = None
            try:
                logger.info(f"Attempting to connect to programmer on {port_name}...")
                communicator = cls(
                    port=port_name, baud_rate=baud_rate
                )  # This tries to open the port
                if not communicator.is_connected():
                    continue  # Try next port
                communicator.consume_remaining_input()
                communicator.send_json_command(command_to_send)
                is_ok, msg = communicator.expect_ok()

                if is_ok:
                    communicator.programmer_info = msg
                    logger.info(f"Programmer found on {port_name}: {msg}")
                    if config_manager:
                        config_manager.set_value(
                            "port", port_name
                        )  # Save successful port
                    return communicator
                else:
                    logger.warning(f"Port {port_name} responded but not with OK: {msg}")
                    communicator.disconnect()  # Clean up before trying next port

            except SerialError as e:  # Includes SerialTimeoutError from expect_ok
                logger.debug(
                    f"Failed to establish valid communication with {port_name}: {e}"
                )
                if communicator:
                    communicator.disconnect()
            except Exception as e:  # Catch any other unexpected errors during probing
                logger.error(f"Unexpected error while probing {port_name}: {e}")
                if communicator:
                    communicator.disconnect()

        raise ProgrammerNotFoundError("No compatible programmer found on any port.")

    def read_data_block(self, num_bytes: int) -> bytes:
        """Reads a specific number of bytes, typically after a DATA: response."""
        if not self.is_connected():
            raise SerialError("Not connected.")
        try:
            data = self.connection.read(num_bytes)
            if len(data) < num_bytes:
                logger.warning(
                    f"Expected {num_bytes} bytes, but received {len(data)} from {self.port_name}"
                )
            return data
        except serial.SerialTimeoutException as e:
            raise SerialTimeoutError(
                f"Timeout reading data block from {self.port_name}: {e}"
            ) from e
        except serial.SerialException as e:
            raise SerialError(
                f"Serial error reading data block from {self.port_name}: {e}"
            ) from e


# Example usage (for testing this module directly)
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG, format="[%(levelname)s:%(name)s:%(lineno)d] %(message)s"
    )

    # Test data for finding programmer
    test_command = {"state": COMMAND_FW_VERSION}

    comm = None
    try:
        # To test, you might need to specify a port if auto-detection is tricky
        # comm = SerialCommunicator.find_and_connect(test_command, preferred_port="/dev/ttyACM0")
        comm = SerialCommunicator.find_and_connect(test_command)

        logger.info(
            f"Successfully connected to programmer: {comm.programmer_info} on {comm.port_name}"
        )

        # Example: Send another command after connection
        # comm.send_json_command({"state": STATE_HW_VERSION})
        ok, msg = comm.expect_ok()
        if ok:
            logger.info(f"Hardware version: {msg}")
        else:
            logger.error(f"Failed to get hardware version: {msg}")

    except ProgrammerNotFoundError:
        logger.error("Test failed: Could not find the programmer.")
    except SerialError as e:
        logger.error(f"Test failed: Serial communication error: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred during test: {e}")
    finally:
        if comm and comm.is_connected():
            comm.disconnect()
