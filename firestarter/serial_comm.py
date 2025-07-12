"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Serial Communication Module
"""

import serial
import serial.tools.list_ports
import serial.serialutil
import time
import re
import functools
import operator
import json
import logging
from collections import namedtuple
from typing import Optional, Generator, Tuple, List

from firestarter.constants import *
from firestarter.config import ConfigManager  # Assuming ConfigManager is refactored

logger = logging.getLogger("SerialComm")
rurp_logger = logging.getLogger("RURP")

# Define a structured object for responses to improve clarity over tuples.
Response = namedtuple('Response', ['type', 'message'])

# Compile regex for parsing prefixes once for efficiency

DEFAULT_SERIAL_TIMEOUT = 1.0  # seconds for read operations
DEFAULT_RESPONSE_TIMEOUT = 10  # seconds for waiting for a specific response
CONNECTION_STABILIZE_DELAY = 2.0  # seconds after opening port

EXPECTED_PREFIXES = [
    "OK",
    "INFO",
    "DEBUG",
    "ERROR",
    "WARN",
    "DATA",
    "MAIN",
    "INIT",
    "END",
]
PREFIX_REGEX = re.compile(rf"\b({'|'.join(EXPECTED_PREFIXES)}):(.*)")

STATE_MACHINE_PREFIXES = ["INIT", "MAIN", "END"]
NON_RESPONSE_PREFIXES = ["INFO", "DEBUG"]
class SerialError(Exception):
    """Custom exception for serial communication errors."""

    pass


class SerialTimeoutError(SerialError):
    """Custom exception for serial timeouts."""

    pass


class ProgrammerNotFoundError(SerialError):
    """Custom exception when no programmer is found."""

    pass


class FirmwareOutdatedError(SerialError):
    """Custom exception for outdated firmware."""

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
        self.connection: Optional[serial.Serial] = None
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
            logger.debug(f"Successfully connected to {self.port_name}.")
        except (
            OSError,
            serial.SerialException,
            serial.serialutil.SerialException,
        ) as e:
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

    def read_line_bytes(self) -> Optional[bytes]:
        if not self.is_connected():
            raise SerialError("Not connected.")
        try:
            if self.connection.in_waiting > 0:
                return self.connection.readline()
            return None
        except serial.SerialException as e:
            raise SerialError(f"Serial error reading from {self.port_name}: {e}") from e

    def _parse_response_line(self, line_bytes: bytes) -> Optional[Response]:
        """
        Parses a raw byte line from the serial port into a structured Response object.
        It filters non-printable characters and uses a regex to find a known prefix.
        """
        if not line_bytes:
            return None
        # Filters a byte array to extract readable characters.
        res_bytes = bytes(b for b in line_bytes if 32 <= b <= 126)
        line_str = res_bytes.decode("ascii", errors="ignore") if res_bytes else ""
        if not line_str:
            return None

        match = PREFIX_REGEX.search(line_str)
        if match:
            # Found a known prefix, return a structured response
            return Response(type=match.group(1), message=match.group(2).strip())

        # No known prefix found, return the raw line as a message with no type
        return Response(type=None, message=line_str)

    def _log_rurp_feedback(self, response: Response):
        """Logs feedback from the programmer based on the parsed Response object."""
        if not response or not response.type:
            return

        message = response.message
        if response.type in STATE_MACHINE_PREFIXES:
                message = "Done"

        level = logging.DEBUG
        if response.type == "ERROR":
            level = logging.ERROR
        elif response.type == "WARN":
            level = logging.WARNING

        # Shorten prefix for debug, full for others
        log_prefix = (
            response.type[:1]
            if rurp_logger.isEnabledFor(logging.DEBUG) and response.type in NON_RESPONSE_PREFIXES
            else response.type
        )
        rurp_logger.log(level, f"{log_prefix}: {message}")

    def _read_and_parse_lines(self, timeout: float) -> Generator[Response, None, None]:
        """
        A generator that continuously reads lines from the serial port,
        parses them, logs them, and yields them as Response objects.
        Resets the timeout if any data is received.
        """
        if not self.is_connected():
            raise SerialError("Not connected.")

        start_time = time.time()
        while time.time() - start_time < timeout:
            line_bytes = self.read_line_bytes()
            if line_bytes:
                response = self._parse_response_line(line_bytes)
                if response:
                    self._log_rurp_feedback(response)
                    yield response
                    start_time = time.time()  # Reset timeout on any valid line
            time.sleep(0.01)  # Prevent busy-waiting

    def get_response(
        self, timeout: float = DEFAULT_RESPONSE_TIMEOUT
    ) -> Response:
        """
        Waits for and returns the next significant (i.e., not INFO or DEBUG)
        response from the programmer.
        """
        for response in self._read_and_parse_lines(timeout):
            if response.type and response.type not in NON_RESPONSE_PREFIXES:
                return response

        # If the generator finishes without yielding a significant response, it's a timeout.
        logger.warning(f"Timeout waiting for a response from {self.port_name}.")
        raise SerialTimeoutError(
            f"Timeout waiting for a significant response from {self.port_name}."
        )

    def expect_ack(
        self, timeout: float = DEFAULT_RESPONSE_TIMEOUT
    ) -> Tuple[bool, Optional[str]]:
        """
        Waits for an 'OK' or 'ERROR' response from the programmer.
        """
        while True:
            response = self.get_response(timeout)
            if response.type == "OK":
                return True, response.message
            elif response.type == "ERROR":
                return False, response.message
            # Other significant responses are ignored by this loop, which is the intended behavior.

    def send_ack(self):
        self.send_string("OK")

    def send_done(self):
        self.send_string("DONE")

    def consume_remaining_input(self, timeout: float = 0.5):
        """Consumes and logs any pending input from the serial buffer."""
        if not self.is_connected():
            return

        # Temporarily set a short timeout for the underlying serial read
        original_timeout = self.connection.timeout
        self.connection.timeout = 0.05
        try:
            # Simply exhaust the generator with the short timeout
            for _ in self._read_and_parse_lines(timeout):
                pass
        finally:
            self.connection.timeout = original_timeout  # Restore original timeout

    def disconnect(self):
        if self.is_connected():
            try:
                self.consume_remaining_input()
                self.connection.close()
                logger.debug(f"Disconnected from {self.port_name}.")
            except serial.SerialException as e:
                logger.error(f"Error closing port {self.port_name}: {e}")
            finally:
                self.connection = None
                self.programmer_info = None

    def _log_command_details(self, command_dict: dict):
        if logger.isEnabledFor(logging.DEBUG):
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
                    logger.debug(
                        f"  Flags set: {', '.join(flag_details)} (0x{flags:02X})"
                    )

    @staticmethod
    def _list_potential_ports(
        preferred_port: Optional[str] = None
    ) -> List[str]:
        ports = []
        if preferred_port:
            ports.append(preferred_port)

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

    @staticmethod
    def _is_version_sufficient(current_version_str: str, required_version_str: str) -> bool:
        """Compares two version strings. Returns True if current >= required."""
        if not current_version_str or not required_version_str:
            return False
        try:
            # Replace 'x' with a high number for comparison purposes
            current = tuple(map(int, current_version_str.lower().replace('x', '999').split('.')))
            required = tuple(map(int, required_version_str.lower().replace('x', '999').split('.')))
            return current >= required
        except (ValueError, AttributeError):
            logger.warning(f"Could not parse version string for comparison: '{current_version_str}'")
            return False # If parsing fails, assume it's not sufficient.


    @staticmethod
    def _probe_port(
        port_name: str,
        baud_rate: int,
        command_to_send: dict,
        config_manager: ConfigManager,
    ) -> Optional["SerialCommunicator"]:
        """
        Attempts to connect to and validate a programmer on a single port.
        This is a helper for find_and_connect.
        """
        communicator = None
        try:
            logger.debug(f"Probing for programmer on {port_name}...")
            communicator = SerialCommunicator(port=port_name, baud_rate=baud_rate)
            communicator.consume_remaining_input()
            communicator.send_json_command(command_to_send)
            is_ok, msg = communicator.expect_ack()

            if is_ok:
                # Firmware version check for all commands except firmware check itself.
                exempt_cmds = [COMMAND_FW_VERSION]
                command_code = command_to_send.get("state") or command_to_send.get("cmd")
                if command_code not in exempt_cmds:
                    try:
                        # Expected format from new firmware: "FW: 2.0.x, HW: Rev2, ..."
                        if msg and "FW:" in msg:
                            match = re.search(r"FW:\s*([\d.x]+)", msg)
                            if match:
                                current_version = match.group(1).strip()
                                if not SerialCommunicator._is_version_sufficient(current_version, "2.0.0"):
                                    raise FirmwareOutdatedError(
                                        f"Firmware version {current_version} is outdated. "
                                        f"Version 2.0.0 or higher is required. "
                                        f"Please upgrade the firmware using 'firestarter fw --install'."
                                    )
                            else:
                                # "FW:" is present but version not found, treat as error
                                raise FirmwareOutdatedError(
                                    "Could not parse firmware version from programmer response. "
                                    "Please upgrade the firmware using 'firestarter fw --install'."
                                )
                        else:
                            # Response does not contain "FW:", assume it's an old firmware.
                            raise FirmwareOutdatedError(
                                "Firmware is outdated (pre-2.0.0). "
                                "Please upgrade the firmware using 'firestarter fw --install'."
                            )
                    except (IndexError, AttributeError):
                        # This case should be rare with the regex, but as a fallback.
                        raise FirmwareOutdatedError(
                            "Could not determine firmware version. "
                            "Please upgrade the firmware using 'firestarter fw --install'."
                        )

                communicator.programmer_info = msg
                logger.debug(f"Programmer found on {port_name}: {msg}")
                config_manager.set_value("port", port_name)  # Save successful port
                return communicator
            else:
                logger.debug(f"Port {port_name} responded but not with OK: {msg}")
                communicator.disconnect()
                return None

        except (SerialError, FirmwareOutdatedError) as e:
            logger.debug(f"Probe failed for {port_name}: {e}")
            if communicator:
                communicator.disconnect()
            if isinstance(e, FirmwareOutdatedError):
                raise
        except Exception as e:
            logger.error(f"Unexpected error while probing {port_name}: {e}")
            if communicator:
                communicator.disconnect()
        return None

    @classmethod
    def find_and_connect(
        cls,
        command_to_send: dict,
        config_manager: ConfigManager,
        preferred_port: Optional[str] = None,
        baud_rate: int = int(BAUD_RATE),
    ) -> "SerialCommunicator":
        """
        Finds a compatible programmer by probing potential serial ports.
        """
        if not preferred_port:
            preferred_port = config_manager.get_value("port")

        potential_ports = cls._list_potential_ports(preferred_port)
        if not potential_ports:
            raise ProgrammerNotFoundError("No potential serial ports found.")

        # For non-verbose mode, provide a single-line status update via the logger.
        # Our custom handler will interpret the 'status' extra to handle overwriting.
        status_update_active = False
        if logger.isEnabledFor(logging.INFO) and not logger.isEnabledFor(logging.DEBUG):
            logger.info("Connecting...", extra={"status": "start"})
            status_update_active = True

        for port_name in potential_ports:
            try:
                communicator = cls._probe_port(port_name, baud_rate, command_to_send, config_manager)
                if communicator:
                    if status_update_active:
                        logger.info("Connecting... OK      ", extra={"status": "end"})
                    # The "Programmer found on..." message is logged by _probe_port on a new line.
                    return communicator
            except FirmwareOutdatedError as e:
                if status_update_active:
                    logger.info("Connecting... Failed  ", extra={"status": "end"})
                # If firmware is outdated on a port, stop probing and raise the specific error.
                raise e

        # If the loop completes without finding a programmer, it's a failure.
        if status_update_active:
            logger.info("Connecting... Failed  ", extra={"status": "end"})
        raise ProgrammerNotFoundError("No compatible programmer found on any port.")

    def read_data_block(self) -> bytes:
        """Reads a specific number of bytes, typically after a DATA: response."""
        if not self.is_connected():
            raise SerialError("Not connected.")
        try:
            num_bytes = int.from_bytes(self.connection.read(2), "big")
            checksum_rcvd = self.connection.read(1)

            data = b''
            bytes_to_read = num_bytes
            while bytes_to_read > 0:
                # read() will block until timeout or all bytes are received.
                chunk = self.connection.read(bytes_to_read)
                if not chunk:
                    # Timeout occurred before all bytes were received
                    break
                data += chunk
                bytes_to_read -= len(chunk)

            checksum = functools.reduce(operator.xor, data, 0)
            if checksum_rcvd[0] != checksum:
                raise SerialError("Data corruption detected (checksum mismatch).")

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

    config = ConfigManager()

    # Test data for finding programmer
    test_command = {"state": COMMAND_FW_VERSION}

    comm = None
    try:
        # To test, you might need to specify a port if auto-detection is tricky
        # comm = SerialCommunicator.find_and_connect(test_command, config, preferred_port="/dev/ttyACM0")
        comm = SerialCommunicator.find_and_connect(test_command, config)

        logger.info(
            f"Successfully connected to programmer: {comm.programmer_info} on {comm.port_name}"
        )

        # Example: Send another command after connection
        # comm.send_json_command({"state": STATE_HW_VERSION})
        ok, msg = comm.expect_ack()
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
