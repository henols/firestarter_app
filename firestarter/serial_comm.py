"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Serial Communication Module
"""

import os
import serial
import serial.tools.list_ports
import serial.serialutil
import time
import re
import struct
import functools
import operator
import json
import logging
from collections import namedtuple
from typing import Any, Optional, Generator, Tuple, List

from firestarter.constants import *
from firestarter.config import ConfigManager  # Assuming ConfigManager is refactored
from firestarter.constants import COMMAND_NAMES
from firestarter.messages import (
    CATALOG,
    DEBUG_CATALOG,
    SEVERITY_LABEL,
    MSG_OK_REV,
    MSG_OK_CFG,
    MSG_INFO_CMD,
    MSG_DATA_CHUNK,
    MSG_DEBUG,
    DBG_CMD,
)

logger = logging.getLogger("SerialComm")
rurp_logger = logging.getLogger("RURP")

# Define a structured object for responses to improve clarity over tuples.
# `payload` carries raw bytes for MSG_DATA_CHUNK frames (W-04); None otherwise.
Response = namedtuple('Response', ['type', 'message', 'payload'], defaults=[None])

# Phase 6: ID-encoded wire frame primitives. MAGIC_PREAMBLE locked by
# CONTEXT §D-02; LogMessage is the decoded-frame value type per D-06.
# `payload` carries raw bytes for MSG_DATA_CHUNK (W-04); None for all others.
LogMessage = namedtuple('LogMessage', ['severity', 'text', 'id', 'payload'], defaults=[None])
MAGIC_PREAMBLE: bytes = b'\xAA\x55\xAA\x55'


def _build_crc8_table() -> bytes:
    """Precompute the 256-byte CRC8-CCITT lookup table.

    Algorithm pinned by CONTEXT §D-03: polynomial 0x07, seed 0x00,
    no reflection, no final XOR. Same algorithm the firmware Unity suite
    asserts (firestarter test_messages/test_rurp_log_id.cpp).
    """
    table = bytearray(256)
    for byte in range(256):
        crc = byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
        table[byte] = crc
    return bytes(table)


_CRC8_CCITT_TABLE: bytes = _build_crc8_table()


def _crc8_ccitt(data: bytes) -> int:
    """Compute CRC8-CCITT (poly 0x07, seed 0x00) over `data` via lookup table."""
    crc = 0
    for byte in data:
        crc = _CRC8_CCITT_TABLE[crc ^ byte]
    return crc


def _decode_param(ptype: str, buf: bytes, cursor: int) -> Tuple[Any, int]:
    """Decode one MSB-first parameter starting at `buf[cursor]`.

    Returns `(value, new_cursor)`. Raises ValueError on unknown ptype or
    out-of-range cursor.

    Param types match the canonical catalog grammar (see catalog/codegen.py
    validator rule 5): u8, i8, u16, i16, u24, u32, i32, ascii_str.

    ascii_str is a 1-byte length prefix followed by N data bytes; decoded
    with `errors='replace'` so a tampered/truncated string surfaces visibly
    rather than crashing the read loop.
    """
    if ptype == "u8":
        return buf[cursor], cursor + 1
    if ptype == "i8":
        return struct.unpack_from(">b", buf, cursor)[0], cursor + 1
    if ptype == "u16":
        return struct.unpack_from(">H", buf, cursor)[0], cursor + 2
    if ptype == "i16":
        return struct.unpack_from(">h", buf, cursor)[0], cursor + 2
    if ptype == "u24":
        b0, b1, b2 = buf[cursor], buf[cursor + 1], buf[cursor + 2]
        return (b0 << 16) | (b1 << 8) | b2, cursor + 3
    if ptype == "u32":
        return struct.unpack_from(">I", buf, cursor)[0], cursor + 4
    if ptype == "i32":
        return struct.unpack_from(">i", buf, cursor)[0], cursor + 4
    if ptype == "ascii_str":
        # WR-04: bounds-check the length prefix against the remaining buffer
        # BEFORE slicing. Python slicing silently truncates when end >
        # len(buf), which would advance the cursor past the end of the buffer
        # and leave a mangled string in the rendered output if ascii_str is
        # the last param in a catalog entry. The CRC check upstream catches
        # truncated wire frames; this guards against a malformed length-prefix
        # byte inside an otherwise-correctly-CRC'd payload.
        length = buf[cursor]
        start = cursor + 1
        end = start + length
        if end > len(buf):
            raise ValueError(
                f"ascii_str length {length} exceeds remaining buffer "
                f"({len(buf) - start} bytes available at cursor={cursor})"
            )
        return buf[start:end].decode("ascii", errors="replace"), end
    if ptype == "bytes":
        # Variable-length raw payload (W-04 MSG_DATA_CHUNK): consume all
        # remaining bytes from cursor to end of params_bytes. No length-prefix
        # wire encoding — the frame's u16 len field already delimits the body.
        raw = buf[cursor:]
        return raw, len(buf)
    raise ValueError(f"Unknown param type: {ptype}")

# Compile regex for parsing prefixes once for efficiency

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

STATE_MACHINE_PREFIXES = []  # W-01: state-machine acks now arrive as ID frames; catalog format strings own the rendering.
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
        # W-01: STATE_MACHINE_PREFIXES is now empty; the old "Done" rewrite for
        # INIT/MAIN/END is removed — catalog format strings own the rendering for
        # ID frames. The conditional is kept but is a no-op ([] never matches).

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

    def _format_message(self, msg_id: int, params: list, entry) -> Optional[str]:
        """Sentinel-aware message renderer for P-02/P-03 shaped IDs and
        MSG_DEBUG sub-payloads (currently DBG_CMD gets symbolic-name
        annotation; other DBG_* sub_ids render via DEBUG_CATALOG).

        Returns the rendered string for sentinel-byte IDs where the catalog
        format string cannot express the conditional (0xFF = no override).
        Returns None for all other IDs (caller falls through to generic rendering).

        P-02 MSG_OK_REV  — params[0]=physical u8, params[1]=effective u8
          effective==0xFF → "Rev{physical}" (no override)
          effective!=0xFF → "Rev{effective}, Override HW: Rev{physical}"

        P-03 MSG_OK_CFG  — params[0]=r1 u32, params[1]=r2 u32, params[2]=override u8
          override==0xFF → "R1: {r1}, R2: {r2}"
          override!=0xFF → "R1: {r1}, R2: {r2}, Override HW: Rev{override}"
        """
        if msg_id == MSG_OK_REV and len(params) == 2:
            physical, effective = params[0], params[1]
            if effective == 0xFF:
                return f"Rev{physical}"
            return f"Rev{effective}, Override HW: Rev{physical}"

        if msg_id == MSG_OK_CFG and len(params) == 3:
            r1, r2, override = params[0], params[1], params[2]
            if override == 0xFF:
                return f"R1: {r1}, R2: {r2}"
            return f"R1: {r1}, R2: {r2}, Override HW: Rev{override}"

        if msg_id == MSG_INFO_CMD and len(params) == 1:
            cmd = params[0]
            name = COMMAND_NAMES.get(cmd)
            return f"Cmd: 0x{cmd:02x} ({name})" if name else f"Cmd: 0x{cmd:02x}"

        if msg_id == MSG_DEBUG and len(params) == 2:
            sub_id = params[0]
            sub_body = params[1] if isinstance(params[1], (bytes, bytearray)) else b""
            sub_entry = DEBUG_CATALOG.get(sub_id)
            # Special-case DBG_CMD: annotate the cmd byte with its symbolic
            # name from COMMAND_NAMES so verbose logs read e.g. "Cmd: 0x02 (WRITE)".
            if sub_id == DBG_CMD and len(sub_body) >= 1:
                cmd = sub_body[0]
                name = COMMAND_NAMES.get(cmd)
                return f"Cmd: 0x{cmd:02x} ({name})" if name else f"Cmd: 0x{cmd:02x}"
            # Generic DBG render: walk sub_entry.params and format. Falls back
            # to the standard "[debug:N]" string for sub_ids the catalog hasn't
            # seen yet so unknown debug emits still appear in the log.
            if sub_entry is not None:
                try:
                    values: list = []
                    cursor = 0
                    for ptype, _prender in sub_entry.params:
                        value, cursor = _decode_param(ptype, sub_body, cursor)
                        values.append(value)
                    fmt_values = [v for v in values if not isinstance(v, (bytes, bytearray))]
                    return sub_entry.format % tuple(fmt_values) if fmt_values else sub_entry.format
                except (IndexError, struct.error, ValueError):
                    return None  # fall through to generic [debug:N] render
            return None

        if msg_id == MSG_DATA_CHUNK and len(params) == 1 and isinstance(params[0], (bytes, bytearray)):
            # W-04: return a short summary so log lines don't dump 512 raw bytes.
            return f"<chunk: {len(params[0])} bytes>"

        return None  # fall through to generic catalog format-string rendering

    def _decode_id_frame(self, frame_len: int, body: bytes) -> Optional[LogMessage]:
        """
        Decode an ID-encoded wire frame body (the bytes between the length
        byte and the trailing 0x0A re-sync anchor).

        `body` carries `id | params | crc` exactly `frame_len` bytes long
        (length is authoritative per CONTEXT §D-03; CRC8 covers `[id, params]`
        but not the length byte nor the terminator).

        Returns a LogMessage on success. Returns None (with a `logger.warning`)
        on shape mismatch / CRC fail / unknown ID / format-render error — the
        outer read loop continues to the next byte (DoS resilience per T-06-12).
        """
        if frame_len < 2 or len(body) != frame_len:
            logger.warning(
                f"Frame too short or truncated: declared len={frame_len}, "
                f"actual body len={len(body)}"
            )
            return None

        msg_id = body[0]
        crc_received = body[-1]
        params_bytes = bytes(body[1:-1])

        crc_expected = _crc8_ccitt(bytes([msg_id]) + params_bytes)
        if crc_expected != crc_received:
            logger.warning(
                f"CRC mismatch for ID 0x{msg_id:02x}: "
                f"expected 0x{crc_expected:02x}, got 0x{crc_received:02x}"
            )
            return None

        entry = CATALOG.get(msg_id)
        if entry is None:
            logger.warning(
                f"Unknown message ID 0x{msg_id:02x} — catalog out of date?"
            )
            return None

        # WR-03: reject id-frame payloads for catalog entries flagged
        # wire_format="text". MSG_OK_FW_VERSION (0x03) is expected to arrive
        # over the legacy text channel only (LFW-05). A buggy or malicious
        # peer emitting id=0x03 as a binary frame would otherwise render via
        # the catalog format string and bypass the host's pre-v1.2 firmware-
        # version guard in _probe_port (which only inspects the text path).
        if entry.wire_format != "id_frame":
            logger.warning(
                f"Rejected id-frame for catalog entry with "
                f"wire_format={entry.wire_format!r}: id=0x{msg_id:02x} "
                f"({entry.name})"
            )
            return None

        # Shape check for fixed-width entries. Variable-length (ascii_str)
        # entries carry param_bytes == -1 in the catalog; for those we
        # cannot pre-validate, but _decode_param will surface any overrun
        # via IndexError below.
        if entry.param_bytes >= 0 and len(params_bytes) != entry.param_bytes:
            logger.warning(
                f"Param shape mismatch for ID 0x{msg_id:02x} ({entry.name}): "
                f"expected {entry.param_bytes} bytes, got {len(params_bytes)}"
            )
            return None

        # Decode each param per the catalog grammar.
        values: list = []
        cursor = 0
        try:
            for ptype, _prender in entry.params:
                value, cursor = _decode_param(ptype, params_bytes, cursor)
                values.append(value)
        except (IndexError, struct.error, ValueError) as exc:
            logger.warning(
                f"Param decode failed for ID 0x{msg_id:02x} ({entry.name}): {exc}"
            )
            return None

        # Sentinel-aware rendering for P-02/P-03 shaped IDs (W-02).
        # _format_message returns a string for MSG_OK_REV/CFG,
        # or None to fall through to the generic catalog format-string path.
        text = self._format_message(msg_id, values, entry)
        if text is None:
            # Generic render via the catalog format string. Format errors fall
            # back to a tagged placeholder so the read loop continues yielding
            # subsequent frames (T-06-12).
            # Filter out raw-bytes values (bytes-type params, e.g. MSG_DATA_CHUNK)
            # before printf-style substitution — they have no corresponding %
            # specifier in the format string.
            fmt_values = [v for v in values if not isinstance(v, (bytes, bytearray))]
            try:
                text = entry.format % tuple(fmt_values) if fmt_values else entry.format
            except (TypeError, ValueError) as exc:
                logger.warning(
                    f"Format-error rendering ID 0x{msg_id:02x} ({entry.name}): {exc}"
                )
                text = f"<format-error: {entry.name}>"

        # Extract raw-bytes payload for MSG_DATA_CHUNK (W-04) so the chip-read
        # loop can obtain the chip data without a second read call.
        chunk_payload = None
        if msg_id == MSG_DATA_CHUNK and values and isinstance(values[0], (bytes, bytearray)):
            chunk_payload = bytes(values[0])

        severity_label = SEVERITY_LABEL.get(entry.severity, f"SEV{entry.severity}")
        return LogMessage(severity=severity_label, text=text, id=msg_id, payload=chunk_payload)

    def _read_and_parse_lines(self, timeout: float) -> Generator[Response, None, None]:
        """
        Always-on byte-stream reader (Phase 6 D-05). A single generator
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
                    logger.debug(f"Port {port_name}: FW-probe setup-ack not OK: {_pre_msg}")
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

                            # Phase 6 (LFW-05 + LHOST-04): refuse pre-v1.2 firmware. The firmware bumped
                            # to major=3 in Phase 9. Set FIRESTARTER_DEV_ALLOW_PRE_V12=1 to bypass when
                            # bench-testing a current host against a historical (v2.x) firmware build.
                            try:
                                major = int(current_version.split(".")[0])
                            except (ValueError, IndexError):
                                major = 0
                            if (
                                major < 3
                                and os.environ.get("FIRESTARTER_DEV_ALLOW_PRE_V12") != "1"
                            ):
                                raise FirmwareOutdatedError(
                                    f"Firmware version {current_version} is pre-v1.2 (text-format logging). "
                                    f"This host expects v1.2+ firmware emitting ID-encoded log frames. "
                                    f"Please upgrade the firmware to v3.0.0 or later using 'firestarter fw --install'. "
                                    f"(No fallback to text-format protocol — the host and firmware must be upgraded together; "
                                    f"see PROJECT.md \"Constraints\".)"
                                )

                            if not SerialCommunicator._is_version_sufficient(current_version, "2.0.0"):
                                raise FirmwareOutdatedError(
                                    f"Firmware version {current_version} is outdated. "
                                    f"Version 2.0.0 or higher is required. "
                                    f"Please upgrade the firmware using 'firestarter fw --install'."
                                )
                        else:
                            raise FirmwareOutdatedError(
                                "Could not parse firmware version from programmer response. "
                                "Please upgrade the firmware using 'firestarter fw --install'."
                            )
                    else:
                        raise FirmwareOutdatedError(
                            "Firmware is outdated (pre-2.0.0). "
                            "Please upgrade the firmware using 'firestarter fw --install'."
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
