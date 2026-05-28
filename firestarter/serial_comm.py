"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Serial Communication Module
"""

import json
import logging
import os
import re
import struct
import time
from typing import Generator, List, Optional, Tuple  # noqa: UP035

import serial
import serial.serialutil
import serial.tools.list_ports

import firestarter.codec as codec
from firestarter.config import ConfigManager  # Assuming ConfigManager is refactored
from firestarter.constants import (
    BAUD_RATE,
    COMMAND_FW_VERSION,
    FLAG_CAN_ERASE,
    FLAG_CHIP_ENABLE,
    FLAG_FORCE,
    FLAG_OUTPUT_ENABLE,
    FLAG_SKIP_BLANK_CHECK,
    FLAG_SKIP_ERASE,
    FLAG_VPE_AS_VPP,
)
from firestarter.exceptions import (
    FirmwareOutdatedError,
    ProgrammerNotFoundError,
    SerialError,
    SerialTimeoutError,
)

# Re-exports for backward compatibility — test_decoder.py imports MAGIC_PREAMBLE,
# LogMessage, Response, _crc8_ccitt directly from firestarter.serial_comm and must
# keep passing UNCHANGED (SC#2 / D-07). The canonical definitions now live in
# frame_parser.py (D-05). _decode_param is also pulled in so _format_message /
# _decode_id_frame in this module resolve it via the new leaf.
from firestarter.frame_parser import (  # noqa: F401  — re-exports for test_decoder.py
    MAGIC_PREAMBLE,
    LogMessage,
    Response,
    _crc8_ccitt,
    _decode_param,
)

logger = logging.getLogger("SerialComm")
rurp_logger = logging.getLogger("RURP")


DEFAULT_SERIAL_TIMEOUT = 1.0  # seconds for read operations
DEFAULT_RESPONSE_TIMEOUT = 10  # seconds for waiting for a specific response
CONNECTION_STABILIZE_DELAY = 2.0  # seconds after opening port

# Phase 8 W-01: INIT/MAIN/END removed (now arrive as ID frames via the catalog
# severity-band lookup). OK + DATA remain until Plan 04 + 05 firmware
# conversions land (firmware still emits those as text prefixes for now).
EXPECTED_PREFIXES = [
    "OK",
    "INFO",
    "DEBUG",
    "ERROR",
    "WARN",
    "DATA",
]
# Prefix regex matches "<PREFIX>: <message>" anywhere in the line. The leading
# word-boundary anchor was REMOVED because the Uno's USB-CDC bridge can prepend
# garbage bytes to legitimate response lines: the firmware's data-bus writes
# during programming toggle PD1 (which doubles as UART TX), and the bridge
# captures those toggles as spurious UART frames. After the host's non-printable
# filter, the garbage can leave digits or letters immediately before the real
# prefix (e.g., "...80OK: Req data"), which the old `\b` anchor refused to match.
# The combined `_parse_response_line` rightmost-match logic ensures we pick the
# real prefix (which always appears at the end of the line, before \r\n) rather
# than any false-positive embedded in the garbage.
PREFIX_REGEX = re.compile(rf"({'|'.join(EXPECTED_PREFIXES)}):(.*)")

NON_RESPONSE_PREFIXES = ["INFO", "DEBUG"]


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
        return self.send_string(json_data)

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

        # Use the RIGHTMOST prefix occurrence — the real response always appears
        # at the end of the line (followed by message + \r\n), and the Uno's
        # USB-CDC bridge can prepend spurious bytes that the printable-ASCII
        # filter doesn't fully strip. Without this, a legitimate "OK: Req data"
        # at the end of a long noisy line can be missed if the garbage happens
        # to contain an earlier "OK:"-like sequence.
        matches = list(PREFIX_REGEX.finditer(line_str))
        if matches:
            match = matches[-1]
            return Response(type=match.group(1), message=match.group(2).strip())

        # No known prefix found, return the raw line as a message with no type
        return Response(type=None, message=line_str)

    def _log_rurp_feedback(self, response: Response):
        """Logs feedback from the programmer based on the parsed Response object."""
        if not response or not response.type:
            return

        message = response.message
        level = logging.DEBUG
        if response.type == "ERROR":
            level = logging.ERROR
        elif response.type == "WARN":
            level = logging.WARNING

        # Shorten prefix for debug, full for others
        log_prefix = (
            response.type[:1]
            if rurp_logger.isEnabledFor(logging.DEBUG)
            and response.type in NON_RESPONSE_PREFIXES
            else response.type
        )
        rurp_logger.log(level, f"{log_prefix}: {message}")

    def _decode_id_frame(self, frame_len: int, body: bytes) -> Optional[LogMessage]:
        """Compatibility wrapper — see codec.decode_id_frame."""
        return codec.decode_id_frame(frame_len, body)

    # =================================================================
    # DO NOT MODIFY — v1.9 RCA territory (GATE-1.8d)
    # The body of this generator is the host-side baseline for v1.9's
    # read-bug RCA. Phase 26 baseline binaries (.planning/v1.6/
    # consistency-check-runs/W27C512-leonardo-20260526-*-v2*/) were
    # captured against this exact body. Structural-only changes here
    # (e.g. type hints on the signature) are OK; any change to the
    # byte-by-byte read loop, the magic-preamble dispatch, the
    # frame-length read, or the timeout reset semantics MUST be
    # flagged and deferred to v1.9 alongside binary re-validation.
    # =================================================================
    def _read_and_parse_lines(self, timeout: float) -> Generator[Response, None, None]:
        """
        [ring-fenced — v1.9 RCA territory; see header comment] Always-on byte-stream reader (Phase 6 D-05). A single generator
        handles BOTH legacy text lines (terminated by 0x0A) AND binary
        ID-encoded frames (4-byte magic preamble + length-authoritative
        body + CRC + 0x0A re-sync anchor) through the same yield surface.

        Each read of one byte is appended to a small accumulator. The
        accumulator is dispatched on either:
          - 4-byte tail matching MAGIC_PREAMBLE → flush any preceding
            text via _parse_response_line, then consume `len + body
            + terminator` as a binary frame and dispatch via
            _decode_id_frame.
          - byte 0x0A → flush the accumulator as a text line via
            _parse_response_line.

        Yields Response(type, message) for both paths so existing callers
        (_log_rurp_feedback, expect_ack, get_response, consume_remaining_input)
        require zero modification. LHOST-03 routing surface preserved.

        Resets the timeout on any successfully parsed yield.
        """
        if not self.is_connected():
            raise SerialError("Not connected.")

        accumulator = bytearray()
        start_time = time.time()
        magic_len = len(MAGIC_PREAMBLE)
        while time.time() - start_time < timeout:
            try:
                chunk = self.connection.read(1)
            except serial.SerialException as e:
                raise SerialError(
                    f"Serial error reading from {self.port_name}: {e}"
                ) from e

            if not chunk:
                # Empty read — pyserial timeout. Do NOT reset start_time;
                # the outer timeout window must still expire.
                time.sleep(0.001)
                continue

            b = chunk[0]
            accumulator.append(b)

            # Magic-preamble match: dispatch preceding text (if any),
            # then consume the binary frame.
            if (
                len(accumulator) >= magic_len
                and bytes(accumulator[-magic_len:]) == MAGIC_PREAMBLE
            ):
                preceding = bytes(accumulator[:-magic_len])
                accumulator.clear()
                if preceding:
                    text_response = self._parse_response_line(preceding)
                    if text_response is not None:
                        self._log_rurp_feedback(text_response)
                        yield text_response
                        start_time = time.time()

                # Read length field (u16 big-endian, W-04: 2 bytes MSB then LSB).
                try:
                    len_bytes = self.connection.read(2)
                except serial.SerialException as e:
                    raise SerialError(
                        f"Serial error reading from {self.port_name}: {e}"
                    ) from e
                if len(len_bytes) < 2:
                    logger.warning(
                        "Magic preamble seen but length bytes not received "
                        "before timeout — re-syncing."
                    )
                    continue
                frame_len = struct.unpack_from(">H", len_bytes)[0]

                # Read body (`frame_len` bytes: id + params + crc).
                try:
                    body = self.connection.read(frame_len)
                except serial.SerialException as e:
                    raise SerialError(
                        f"Serial error reading from {self.port_name}: {e}"
                    ) from e
                if len(body) != frame_len:
                    logger.warning(
                        f"Frame body truncated: expected {frame_len} bytes, "
                        f"got {len(body)} — re-syncing."
                    )
                    continue

                # Consume the trailing terminator (D-04: anchor, not
                # delimiter — present but its identity is not enforced).
                try:
                    _terminator = self.connection.read(1)
                except serial.SerialException as e:
                    raise SerialError(
                        f"Serial error reading from {self.port_name}: {e}"
                    ) from e
                # _terminator is intentionally not checked: per CONTEXT §D-04
                # the byte is a re-sync anchor, not a delimiter.

                decoded = self._decode_id_frame(frame_len, body)
                if decoded is not None:
                    # Propagate raw-bytes payload for MSG_DATA_CHUNK (W-04);
                    # Response.payload is None for all other message types.
                    response = Response(
                        type=decoded.severity,
                        message=decoded.text,
                        payload=decoded.payload,
                    )
                    self._log_rurp_feedback(response)
                    yield response
                    start_time = time.time()
                continue

            # Newline → flush accumulator as a text line.
            if b == 0x0A:
                line_bytes = bytes(accumulator)
                accumulator.clear()
                response = self._parse_response_line(line_bytes)
                if response is not None:
                    self._log_rurp_feedback(response)
                    yield response
                    start_time = time.time()
                continue

            # Otherwise: keep accumulating; the byte is already appended.

    def get_response(self, timeout: float = DEFAULT_RESPONSE_TIMEOUT) -> Response:
        """
        Waits for and returns the next significant (i.e., not INFO or DEBUG)
        response from the programmer.
        """
        for response in self._read_and_parse_lines(timeout):
            if response.type and response.type not in NON_RESPONSE_PREFIXES:
                return response

        # If the generator finishes without yielding a significant response, it's a timeout.  # noqa: E501
        logger.warning(f"Timeout waiting for a response from {self.port_name}.")
        raise SerialTimeoutError(
            f"Timeout waiting for a significant response from {self.port_name}."
        )

    def expect_ack(
        self, timeout: float = DEFAULT_RESPONSE_TIMEOUT
    ) -> Tuple[bool, Optional[str]]:  # noqa: UP006
        """
        Waits for an 'OK' or 'ERROR' response from the programmer.
        """
        while True:
            response = self.get_response(timeout)
            if response.type == "OK":
                return True, response.message
            elif response.type == "ERROR":
                return False, response.message
            # Other significant responses are ignored by this loop, which is the intended behavior.  # noqa: E501

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
    def _list_potential_ports(preferred_port: Optional[str] = None) -> List[str]:  # noqa: UP006
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
    def _is_version_sufficient(
        current_version_str: str, required_version_str: str
    ) -> bool:
        """Compares two version strings. Returns True if current >= required."""
        if not current_version_str or not required_version_str:
            return False
        try:
            # Replace 'x' with a high number for comparison purposes
            current = tuple(
                map(int, current_version_str.lower().replace("x", "999").split("."))
            )
            required = tuple(
                map(int, required_version_str.lower().replace("x", "999").split("."))
            )
            return current >= required
        except (ValueError, AttributeError):
            logger.warning(
                f"Could not parse version string for comparison: '{current_version_str}'"  # noqa: E501
            )
            return False  # If parsing fails, assume it's not sufficient.

    @staticmethod
    def _validate_firmware_version(
        version_str: str, allow_pre_v12: bool = False
    ) -> None:
        """Pure-policy version guard. Raises FirmwareOutdatedError on reject.

        Owns the complete version-guard policy (D-01 / D-03): strips trailing
        alpha suffix (e.g. ``"3.0.0-dev"`` -> ``"3.0.0"``) per RESEARCH §7
        Option A, parses the major version (``ValueError``/``IndexError`` ->
        ``major=0``), refuses pre-v1.2 (``major < 3``) unless ``allow_pre_v12``,
        then enforces the 2.0.0 floor via ``_is_version_sufficient``. Never
        reads ``os.environ`` (D-02 — env-var I/O is ``_probe_port``'s job).
        """
        # RESEARCH §7 Option A: strip trailing alpha suffix before parsing so
        # direct callers (and future test harnesses) match production wire
        # behavior, which is already handled by the _probe_port regex
        # r"FW:\s*([\d.x]+)" stripping "-dev" before this method ever sees it.
        version_str = re.sub(r"-.*$", "", version_str)
        try:
            major = int(version_str.split(".")[0])
        except (ValueError, IndexError):
            major = 0
        if major < 3 and not allow_pre_v12:
            raise FirmwareOutdatedError(
                f"Firmware version {version_str} is pre-v1.2 (text-format logging). "  # noqa: E501
                f"This host expects v1.2+ firmware emitting ID-encoded log frames. "  # noqa: E501
                f"Please upgrade the firmware to v3.0.0 or later using 'firestarter fw --install'. "  # noqa: E501
                f"(No fallback to text-format protocol — the host and firmware must be upgraded together; "  # noqa: E501
                f'see PROJECT.md "Constraints".)'
            )
        if not SerialCommunicator._is_version_sufficient(version_str, "2.0.0"):
            raise FirmwareOutdatedError(
                f"Firmware version {version_str} is outdated. "
                f"Version 2.0.0 or higher is required. "
                f"Please upgrade the firmware using 'firestarter fw --install'."  # noqa: E501
            )

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

            # FW-version handshake (independent of the user's command). Firmware
            # emits the LFW-05 "OK: FW: <version>" text line on CMD_FW_VERSION;
            # we use it to gate version compatibility before sending the actual
            # user command. Prior firmware shipped MSG_OK_FW_HANDSHAKE with the
            # version in every ack body, but that was dropped in Phase 9 — a
            # dedicated probe is now the load-bearing version check.
            exempt_cmds = [COMMAND_FW_VERSION]
            command_code = command_to_send.get("state") or command_to_send.get("cmd")
            if command_code not in exempt_cmds:
                communicator.send_json_command({"state": COMMAND_FW_VERSION})
                # CMD_FW_VERSION emits 2 acks: setup-complete "Ready" from
                # init_programmer, then "OK: FW: <version>" from fw_get_version.
                # Discard the first; parse the second for version validation.
                pre_is_ok, _pre_msg = communicator.expect_ack()
                if not pre_is_ok:
                    logger.debug(
                        f"Port {port_name}: FW-probe setup-ack not OK: {_pre_msg}"
                    )
                    communicator.disconnect()
                    return None
                fw_is_ok, fw_msg = communicator.expect_ack()
                if not fw_is_ok:
                    logger.debug(f"Port {port_name}: FW-probe payload not OK: {fw_msg}")
                    communicator.disconnect()
                    return None

                try:
                    if fw_msg and "FW:" in fw_msg:
                        match = re.search(r"FW:\s*([\d.x]+)", fw_msg)
                        if match:
                            current_version = match.group(1).strip()

                            # Phase 6 (LFW-05 + LHOST-04): refuse pre-v1.2 firmware. The firmware bumped  # noqa: E501
                            # to major=3 in Phase 9. Set FIRESTARTER_DEV_ALLOW_PRE_V12=1 to bypass when  # noqa: E501
                            # bench-testing a current host against a historical (v2.x) firmware build.  # noqa: E501
                            allow_pre_v12 = (
                                os.environ.get("FIRESTARTER_DEV_ALLOW_PRE_V12") == "1"
                            )
                            SerialCommunicator._validate_firmware_version(
                                current_version, allow_pre_v12=allow_pre_v12
                            )
                        else:
                            raise FirmwareOutdatedError(
                                "Could not parse firmware version from programmer response. "  # noqa: E501
                                "Please upgrade the firmware using 'firestarter fw --install'."  # noqa: E501
                            )
                    else:
                        raise FirmwareOutdatedError(
                            "Firmware is outdated (pre-2.0.0). "
                            "Please upgrade the firmware using 'firestarter fw --install'."  # noqa: E501
                        )
                except (IndexError, AttributeError):
                    raise FirmwareOutdatedError(
                        "Could not determine firmware version. "
                        "Please upgrade the firmware using 'firestarter fw --install'."
                    )

                # FW probe succeeded; drain any trailing diagnostic frames the
                # firmware emitted alongside the FW handshake before we send
                # the user's actual command.
                communicator.consume_remaining_input()

            # Send the user's actual command (or CMD_FW_VERSION re-send when exempt).
            communicator.send_json_command(command_to_send)
            is_ok, msg = communicator.expect_ack()

            if is_ok:
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
                communicator = cls._probe_port(
                    port_name, baud_rate, command_to_send, config_manager
                )
                if communicator:
                    if status_update_active:
                        logger.info("Connecting... OK      ", extra={"status": "end"})
                    # The "Programmer found on..." message is logged by _probe_port on a new line.  # noqa: E501
                    return communicator
            except FirmwareOutdatedError as e:
                if status_update_active:
                    logger.info("Connecting... Failed  ", extra={"status": "end"})
                # If firmware is outdated on a port, stop probing and raise the specific error.  # noqa: E501
                raise e

        # If the loop completes without finding a programmer, it's a failure.
        if status_update_active:
            logger.info("Connecting... Failed  ", extra={"status": "end"})
        raise ProgrammerNotFoundError("No compatible programmer found on any port.")


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
        # comm = SerialCommunicator.find_and_connect(test_command, config, preferred_port="/dev/ttyACM0")  # noqa: E501
        comm = SerialCommunicator.find_and_connect(test_command, config)

        logger.info(
            f"Successfully connected to programmer: {comm.programmer_info} on {comm.port_name}"  # noqa: E501
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
