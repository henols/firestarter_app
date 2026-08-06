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
from typing import Any, Callable, Generator, List, Optional, Tuple  # noqa: UP035

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
    REVISION_2_2,
    REVISION_2_3,
)
from firestarter.exceptions import (
    FirmwareOutdatedError,
    HardwareRevisionUnsupportedError,
    ProgrammerNotFoundError,
    ProtocolNotImplementedError,
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
    cobs_encode,
)
from firestarter.messages import MSG_ERR_PROTOCOL_NOT_IMPLEMENTED, MSG_OK_READY

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

    # CAP-02 identity fields, declared at CLASS level on purpose. __init__ also
    # assigns them, but plenty of call sites never run __init__ — conftest's
    # make_comm builds instances via __new__, and several suites patch __init__
    # to a no-op lambda to avoid opening a real port. _probe_port reads
    # firmware_identity unconditionally, so an instance-only attribute turns
    # every one of those into an AttributeError swallowed by the broad
    # `except Exception` in _probe_port, which degrades to "no programmer
    # found". Class defaults of None keep the gates fail-closed instead.
    firmware_identity: Optional[str] = None
    hw_revision: Optional[int] = None

    def __init__(
        self,
        port: str,
        baud_rate: int = int(BAUD_RATE),
        timeout: float = DEFAULT_SERIAL_TIMEOUT,
    ) -> None:
        self.port_name = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.connection: Optional[serial.Serial] = None
        self.programmer_info: str | None = None
        # Phase-53 fault-injection hook — None by default; production path is byte-identical.
        # Set only within dev fault-inject scope; cleared after the single corrupted transfer.
        # T-53-03: getattr-guarded in send_json_command; this attribute is the formal default.
        self._fault_inject_outgoing: Optional[Callable[[bytes], bytes]] = None
        # DEPRECATED (Phase 55 CAP-01): firmware_buffer_size was set by the Phase 53
        # identity-string parse (3rd colon-field). That parse block is removed; capacity
        # now comes from the MSG_OK_READY ack via firmware_max_chunk. Declaration kept
        # so conftest.py make_comm factory mirrors __init__ without breakage.
        self.firmware_buffer_size: Optional[int] = None
        # CAP-01 (Phase 55): firmware advertises effective MAIN-path decode capacity
        # via the MSG_OK_READY operation-setup ack (2-byte big-endian u16 param).
        # Populated by _decode_id_frame override; None until the first MSG_OK_READY
        # with a 2-byte param is decoded. _calculate_buffer_size returns 512 (safe
        # Uno floor) when None (Phase 54 D-05 reversed; no FirmwareOutdatedError).
        self.firmware_max_chunk: Optional[int] = None
        # CAP-02: the MSG_OK_READY ack was extended past CAP-01's 2-byte
        # buffer-size region to also carry the effective hardware revision and
        # the firmware identity string, so a single command exchange now yields
        # everything the connect-time gates need. Both stay None against
        # firmware that predates CAP-02 (2-byte ack) — and None is a REJECT for
        # the revision gate, never a pass. Populated by _decode_id_frame below.
        #
        # firmware_identity is the raw "<version>:<board>" string, matching what
        # the retired CMD_FW_VERSION probe used to read off the wire; callers
        # wanting the numeric part must strip the board suffix exactly as
        # _probe_port does.
        self.firmware_identity: Optional[str] = None
        self.hw_revision: Optional[int] = None
        # D-15 (Phase 120 / v1.22 HOST-06): bounded record of every id frame
        # successfully decoded on this connection. Populated by the
        # _decode_id_frame override below. A set of integers only — nothing
        # sized from frame content is ever allocated here (T-120-39), mirroring
        # the defensive posture of the firmware_max_chunk plausibility clamp
        # above. Per-connection instance state, not shared across connections.
        self.seen_message_ids: set[int] = set()

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
        """Return True if the underlying serial port is open."""
        return self.connection is not None and self.connection.is_open

    def send_bytes(self, data_bytes: bytes) -> int:
        """Write raw bytes to the serial port and return the byte count written."""
        if not self.is_connected():
            raise SerialError("Not connected.")
        assert self.connection is not None  # narrow for mypy strict (D-06)
        try:
            written_bytes = self.connection.write(data_bytes)
            self.connection.flush()
            logger.debug(f"Sent {written_bytes} bytes to {self.port_name}.")
            # pyserial's write returns Optional[int]; treat None as 0 for our int contract.
            return written_bytes if written_bytes is not None else 0
        except serial.SerialTimeoutException as e:
            raise SerialTimeoutError(f"Timeout writing to {self.port_name}: {e}") from e
        except serial.SerialException as e:
            raise SerialError(f"Serial error writing to {self.port_name}: {e}") from e

    def send_string(self, data_string: str, encoding: str = "ascii") -> int:
        """Encode `data_string` and send it over the serial port."""
        logger.debug(f"Sending string: {data_string}")
        return self.send_bytes(data_string.encode(encoding))

    def send_json_command(self, command_dict: dict) -> int:
        """Serialise ``command_dict`` as a COBS+CRC8 framed command and send it.

        Frame layout (ADR §4.3, FRAME-05, CRC-01):
            COBS(json_bytes + CRC8(json_bytes)) + 0x00

        Encode order is LOAD-BEARING: CRC8 is computed over the RAW json_bytes
        FIRST, then the (json_bytes + crc_byte) stream is COBS-encoded as a unit.
        Never COBS-encode first then CRC the body — that would silently break the
        firmware's CRC8 verify (RESEARCH Pitfall 2).

        The full frame is assembled as a single ``bytes`` object and passed to
        ``send_bytes()`` in ONE call (SAFE-01 sub-claim B — split-write forbidden).
        """
        self._log_command_details(command_dict)
        json_bytes = json.dumps(command_dict, separators=(",", ":")).encode("ascii")
        crc = _crc8_ccitt(json_bytes)
        body = cobs_encode(json_bytes + bytes([crc]))
        frame = body + b"\x00"
        # PHASE-53 FAULT INJECTION — only active when _fault_inject_outgoing is set.
        # Production path: attribute is None by default → no-op (T-53-03).
        # Hook is set only within fault_inject_cycle / dev fault-inject scope and
        # cleared after the single corrupted transfer (D-01 / D-02).
        _hook = getattr(self, "_fault_inject_outgoing", None)
        if _hook is not None:
            frame = _hook(frame)
        return self.send_bytes(frame)

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

    def _log_rurp_feedback(self, response: Response) -> None:
        """Logs feedback from the programmer based on the parsed Response object."""
        if not response or not response.type:
            return

        message = response.message
        level = logging.DEBUG
        if response.type == "ERROR":
            level = logging.ERROR
        elif response.type == "WARN":
            level = logging.WARNING
        elif response.type == "INFO":
            # D-09 / HOST-05 / F-120-02: before this arm, the whole INFO band
            # fell through to the `logging.DEBUG` initialiser above, while
            # `_setup_logging` (cli_handlers.py:83) sets the root logger to
            # `logging.INFO` unless `-v` is passed. That meant every Phase
            # 118/119 SDP report line — emitted unconditionally by firmware —
            # was silently discarded by the host for a whole phase: a
            # two-repo requirement that passed its own phase's verification
            # and was still false end to end.
            #
            # This promotion is deliberately scoped to the `INFO` label only.
            # `OK`, `INIT`, `MAIN`, `END` and `DATA` are protocol-phase frames
            # and stay on the `logging.DEBUG` default — promoting them would
            # flood default-verbosity output.
            #
            # The blast radius is SIX unconditionally-emitted INFO-band ids,
            # not five: `0x5E`, `0x5F`, `0x60`, `0x61`, `0x62` via
            # `LOG_ID`/`LOG_ID_U32`, plus `0x5B` `MSG_INFO_HW` — emitted via
            # the unconditional `LOG_WARN_ID_U8` alias at
            # `rurp_hw_rev_utils.h:96` (`logging_id.h:115` makes that macro an
            # unconditional plain `LOG_ID_U8` despite its name), while its
            # *catalog* severity is INFO. Every other INFO id in the tree is
            # `FLAG_VERBOSE`-gated in firmware and therefore only sent when
            # the host passed `-v`.
            #
            # That `0x5B` case is Phase 35's CR-02 hard-fail-loud revision
            # warning — this arm makes it visible at default verbosity for
            # the first time, a partial fix for a second, older observability
            # defect independent of the SDP work.
            #
            # Side effect: under `-v`, an INFO frame's rendered prefix changes
            # from `I:` to `INFO:`, because the one-character abbreviation
            # below applies only while `rurp_logger.isEnabledFor(logging.DEBUG)`
            # and the type is in `NON_RESPONSE_PREFIXES` — at `-v` the DEBUG
            # gate is open regardless of this arm's level assignment.
            level = logging.INFO

        # Shorten prefix for debug, full for others
        log_prefix = (
            response.type[:1]
            if rurp_logger.isEnabledFor(logging.DEBUG)
            and response.type in NON_RESPONSE_PREFIXES
            else response.type
        )
        rurp_logger.log(level, f"{log_prefix}: {message}")

    def _decode_id_frame(self, frame_len: int, body: bytes) -> Optional[LogMessage]:
        """Compatibility wrapper — see codec.decode_id_frame.

        CAP-01 (Phase 55): after decoding, when the message is MSG_OK_READY and
        the param region is exactly 2 bytes, extract the big-endian u16 and store
        it as firmware_max_chunk (buffer-size advertisement relocated from the FW
        identity string to the operation-setup ack). A plausibility clamp rejects
        values outside [1, 4096] so a hostile/corrupt ack cannot over-size chunks
        (T-55-05 / T-55-06). 0-byte param region (old firmware) leaves
        firmware_max_chunk unchanged (graceful degradation, T-55-07).

        D-15 (Phase 120 / v1.22 HOST-06): every successfully decoded id frame
        has its id recorded into seen_message_ids, regardless of which id it
        is. Trigger: any id for which codec.decode_id_frame returns non-None.
        Degradation against old firmware: a firmware build that never emits a
        given id (e.g. MSG_WARN_SDP_UNLOCK_SKIPPED / 0x86) simply leaves that
        id absent from the set — that absence is exactly the signal callers
        such as write_eprom's D-15 check key on. The record is bounded by
        construction: it stores only the decoded id integer (0-255), never
        anything sized from frame content (T-120-39).

        The GATE-1.8d ring-fenced _read_and_parse_lines body is not touched —
        only this override seam is used (Pitfall 4 / Open Question 3).
        """
        result = codec.decode_id_frame(frame_len, body)
        # body layout: [id_byte][params_bytes...][crc_byte]
        if result is not None and len(body) >= 2:
            msg_id = body[0]
            # D-15: record every successfully decoded id, bounded (set of ints).
            self.seen_message_ids.add(msg_id)
            if msg_id == MSG_OK_READY:
                params_bytes = body[1:-1]  # strip id byte and trailing CRC
                # CAP-01 buffer size occupies the first 2 bytes in BOTH the
                # legacy 2-byte ack and the CAP-02 extended ack, so the length
                # test is >= 2 rather than == 2. Against CAP-02 firmware the
                # old == 2 form silently skipped this and fell back to the 512
                # floor; widening it is what restores full-size chunking.
                if len(params_bytes) >= 2:
                    value = struct.unpack(">H", params_bytes[:2])[0]
                    # Plausibility clamp: reject values outside [1, 4096].
                    # No real board exceeds the 1024-byte Leonardo buffer; 4096
                    # is a generous ceiling. Values outside this range leave
                    # firmware_max_chunk unset so the 512 floor applies (T-55-06).
                    if 1 <= value <= 4096:
                        self.firmware_max_chunk = value
                # CAP-02 tail: [hw_revision u8][ver_len u8][ver bytes]. Absent
                # on pre-CAP-02 firmware, which leaves both attributes None —
                # and None is a reject for the revision gate, never a pass.
                # A truncated or malformed length prefix also leaves
                # firmware_identity None rather than yielding a partial string,
                # so a mangled ack degrades to "refuse", not to "probably fine".
                if len(params_bytes) >= 4:
                    self.hw_revision = params_bytes[2]
                    ver_end = 4 + params_bytes[3]
                    if ver_end <= len(params_bytes):
                        self.firmware_identity = params_bytes[4:ver_end].decode(
                            "ascii", errors="replace"
                        )
        return result

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
                chunk = self.connection.read(1)  # type: ignore[union-attr]  # Phase 42 D-06: GATE-1.8d ring-fence — narrow body untouched
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
                    len_bytes = self.connection.read(2)  # type: ignore[union-attr]  # Phase 42 D-06
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
                    body = self.connection.read(frame_len)  # type: ignore[union-attr]  # Phase 42 D-06
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
                    _terminator = self.connection.read(1)  # type: ignore[union-attr]  # Phase 42 D-06
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
                        id=decoded.id,
                    )
                    self._log_rurp_feedback(response)
                    yield response
                    start_time = time.time()
                continue

            # Newline → flush accumulator as a text line.
            if b == 0x0A:
                line_bytes = bytes(accumulator)
                accumulator.clear()
                text_resp = self._parse_response_line(line_bytes)
                if text_resp is not None:
                    self._log_rurp_feedback(text_resp)
                    yield text_resp
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
                if response.id == MSG_ERR_PROTOCOL_NOT_IMPLEMENTED:
                    raise ProtocolNotImplementedError(response.message)
                return False, response.message
            # Other significant responses are ignored by this loop, which is the intended behavior.  # noqa: E501

    def send_ack(self) -> None:
        """Send the 'OK' acknowledgement string to the programmer."""
        self.send_string("OK")

    def send_done(self) -> None:
        """Send the 'DONE' completion string to the programmer."""
        self.send_string("DONE")

    def consume_remaining_input(self, timeout: float = 0.5) -> None:
        """Consumes and logs any pending input from the serial buffer."""
        if not self.is_connected():
            return
        assert self.connection is not None  # narrow for mypy strict (D-06)

        # Temporarily set a short timeout for the underlying serial read
        original_timeout = self.connection.timeout
        self.connection.timeout = 0.05
        try:
            # Simply exhaust the generator with the short timeout
            for _ in self._read_and_parse_lines(timeout):
                pass
        finally:
            self.connection.timeout = original_timeout  # Restore original timeout

    def disconnect(self) -> None:
        """Close the serial port and clear cached programmer info."""
        if self.is_connected():
            try:
                self.consume_remaining_input()
                self.connection.close()  # type: ignore[union-attr]
                logger.debug(f"Disconnected from {self.port_name}.")
            except serial.SerialException as e:
                logger.error(f"Error closing port {self.port_name}: {e}")
            finally:
                self.connection = None
                self.programmer_info = None

    def _log_command_details(self, command_dict: dict) -> None:
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

    # Bus line 11 is where socket pin 21 lands on the 24-pin RURP wiring, and
    # it is the VPP line for exactly two pinouts — DIP24_2716 and DIP24_2532.
    # Those parts need the 3-position JP4 header introduced on shield Rev 2.2;
    # driving them on an earlier board is a chip-damage path.
    _VPP_LINE_REQUIRING_REV_2_2 = 11
    # ALLOWLIST, deliberately not a `>=` comparison. The REVISION_* bytes are
    # not a version-ordered scale: REVISION_UNKNOWN is 0xFE, numerically ABOVE
    # REVISION_2_2 (0x04), so `detected >= REVISION_2_2` would admit precisely
    # the boards whose revision could not be determined. Membership fails
    # closed for 0xFE, for the 0xFF override-absent sentinel, for the
    # REVISION_2_0 broad bucket, and for None (pre-CAP-02 firmware).
    _REVISIONS_WITH_3_POSITION_JP4 = (REVISION_2_2, REVISION_2_3)

    @staticmethod
    def _validate_hardware_revision(
        command_to_send: dict, detected: Optional[int]
    ) -> None:
        """Pure-policy shield-revision guard. Raises on reject, returns on pass.

        Mirrors _validate_firmware_version's shape: no I/O, no environment
        reads, no serial access — just the wire dict the host is about to act
        on and the revision byte the firmware reported. That makes the policy
        testable without a board and keeps _probe_port free of the reasoning.

        Only chips whose bus-config routes VPP to bus line 11 are gated; every
        other chip passes through untouched regardless of shield revision.

        Note for operators hitting this: ADC detection collapses Rev 2.0, 2.1
        and 2.2 into the single REVISION_2_0 bucket, so a genuine Rev 2.2 board
        reports as 2.0-class until the EEPROM override is written. That is the
        intended design — the operator has to look at the physical header and
        assert it, and asserting it is the safety mechanism, not a workaround.
        """
        bus_config = command_to_send.get("bus-config") or {}
        if bus_config.get("vpp-pin") != SerialCommunicator._VPP_LINE_REQUIRING_REV_2_2:
            return
        if detected in SerialCommunicator._REVISIONS_WITH_3_POSITION_JP4:
            return

        if detected is None:
            reported = "nothing (firmware predates the revision-carrying ack)"
        else:
            reported = f"0x{detected:02X}"
        raise HardwareRevisionUnsupportedError(
            f"This chip routes VPP to socket pin 21, which needs the 3-position "
            f"JP4 header introduced on RURP shield Rev 2.2. The programmer "
            f"reported {reported}. Refusing to program — an earlier shield "
            f"cannot route VPP there and attempting it can damage the EPROM.\n"
            f"If this board really is a Rev 2.2 or 2.3, ADC detection cannot "
            f"tell it apart from a Rev 2.0, so you must assert it once with "
            f"'firestarter config --rev 4' (4 = Rev 2.2, 5 = Rev 2.3). Note "
            f"that --rev takes the revision BYTE, not the silkscreen number: "
            f"'--rev 2.2' truncates to 2 and selects the Rev 2.0 bucket.",
            detected=detected,
        )

    @staticmethod
    def _probe_port(
        port_name: str,
        baud_rate: int,
        command_to_send: dict,
        config_manager: ConfigManager,
        fault_inject_outgoing: Optional[Callable[[bytes], bytes]] = None,
    ) -> Optional["SerialCommunicator"]:
        """
        Attempts to connect to and validate a programmer on a single port.
        This is a helper for find_and_connect.
        """
        communicator = None
        try:
            logger.debug(f"Probing for programmer on {port_name}...")
            communicator = SerialCommunicator(port=port_name, baud_rate=baud_rate)
            # Phase 53-04 / XACT-02 (dev-only): arm the outgoing-frame fault BEFORE
            # the first send_json_command below. Default None => production no-op.
            if fault_inject_outgoing is not None:
                communicator._fault_inject_outgoing = fault_inject_outgoing
            communicator.consume_remaining_input()

            # CAP-02: send the user's actual command straight away. The
            # dedicated CMD_FW_VERSION pre-probe this replaces cost a full
            # command exchange (2 acks) on every single connect; MSG_OK_READY
            # now carries the firmware identity AND the effective hardware
            # revision, so both gates run off the ack this command was going to
            # produce anyway.
            #
            # Validating after the command is on the wire is safe by
            # construction, not by luck. init_programmer_framed does run
            # configure_memory before emitting MSG_OK_READY, but every
            # configure_* handler is pure — it assigns function pointers and
            # pulse defaults, nothing else. The VPP regulator is not engaged
            # until firestarter_operation_init, which blocks on
            # op_wait_for_ack(). Raising below means that ack is never sent, so
            # the operation never starts and the rail stays down.
            communicator.send_json_command(command_to_send)
            is_ok, msg = communicator.expect_ack()

            if not is_ok:
                logger.debug(f"Port {port_name} responded but not with OK: {msg}")
                communicator.disconnect()
                return None

            # Version gate. The POLICY is untouched (D-01/D-03) — only its
            # source moved, from the retired probe's "OK: FW: <ver>" text line
            # to the identity field of the ack. Same [\d.x]+ extraction as the
            # old regex performed, so _validate_firmware_version still receives
            # "3.0.0" rather than the full "3.0.0:uno" identity (feeding it the
            # board suffix would make int() choke and reject every board).
            identity = communicator.firmware_identity
            version_match = re.match(r"[\d.x]+", identity) if identity else None
            if version_match is None:
                raise FirmwareOutdatedError(
                    "Programmer did not report a firmware version in its "
                    "operation-setup ack. This host requires firmware that "
                    "carries the version and hardware revision in that ack. "
                    "Please upgrade the firmware using 'firestarter fw --install'."  # noqa: E501
                )
            # Phase 6 (LFW-05 + LHOST-04): refuse pre-v1.2 firmware. The firmware bumped  # noqa: E501
            # to major=3 in Phase 9. Set FIRESTARTER_DEV_ALLOW_PRE_V12=1 to bypass when  # noqa: E501
            # bench-testing a current host against a historical (v2.x) firmware build.  # noqa: E501
            SerialCommunicator._validate_firmware_version(
                version_match.group(0),
                allow_pre_v12=os.environ.get("FIRESTARTER_DEV_ALLOW_PRE_V12") == "1",
            )

            # Shield-revision gate — ordered after the version check because
            # firmware old enough to fail that check cannot be trusted to have
            # reported a revision at all, and before the caller is handed a
            # connection it would immediately start driving.
            SerialCommunicator._validate_hardware_revision(
                command_to_send, communicator.hw_revision
            )

            communicator.programmer_info = msg
            logger.debug(f"Programmer found on {port_name}: {msg}")
            config_manager.set_value("port", port_name)  # Save successful port
            return communicator

        except HardwareRevisionUnsupportedError:
            # MUST precede the SerialError clause below (it is a subclass) and
            # MUST re-raise. Falling through to `return None` would surface a
            # deliberate safety refusal as "no programmer found" — the worst
            # possible message for an operator looking at a board that is
            # plainly attached, and one that invites them to go hunting for a
            # cable fault instead of reading the actual reason.
            if communicator:
                communicator.disconnect()
            raise
        except (SerialError, FirmwareOutdatedError) as e:
            logger.debug(f"Probe failed for {port_name}: {e}")
            if communicator:
                communicator.disconnect()
            if isinstance(e, FirmwareOutdatedError):
                raise
        except ProtocolNotImplementedError:
            if communicator:
                communicator.disconnect()
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
        fault_inject_outgoing: Optional[Callable[[bytes], bytes]] = None,
    ) -> "SerialCommunicator":
        """
        Finds a compatible programmer by probing potential serial ports.

        ``fault_inject_outgoing`` (Phase 53-04 / XACT-02, dev-only) installs an
        outgoing-frame mutation hook on each probed communicator BEFORE the first
        ``send_json_command`` (the setup/handshake command). It defaults to None, so
        the production path is byte-identical (T-53-03). It exists because a READ's
        MAIN phase emits only plaintext acks (``send_string``) — the setup command
        sent here is the ONLY corruptible host→fw command frame, so the outgoing
        fault MUST be injected at connection time, not after setup.
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
                    port_name,
                    baud_rate,
                    command_to_send,
                    config_manager,
                    fault_inject_outgoing=fault_inject_outgoing,
                )
                if communicator:
                    if status_update_active:
                        logger.info("Connecting... OK      ", extra={"status": "end"})
                    # The "Programmer found on..." message is logged by _probe_port on a new line.  # noqa: E501
                    return communicator
            except (
                FirmwareOutdatedError,
                HardwareRevisionUnsupportedError,
                ProtocolNotImplementedError,
            ) as e:
                if status_update_active:
                    logger.info("Connecting... Failed  ", extra={"status": "end"})
                # If firmware is outdated, the shield revision cannot safely
                # drive this chip, or the protocol is not implemented, stop
                # probing and raise the specific error (all three are
                # stop-probing, surface-the-specific-error cases). Listing the
                # revision error here is about closing the "Connecting..."
                # status line — it already escapes the loop by not matching any
                # clause, but it would leave that line dangling on the way out.
                raise e

        # If the loop completes without finding a programmer, it's a failure.
        if status_update_active:
            logger.info("Connecting... Failed  ", extra={"status": "end"})
        raise ProgrammerNotFoundError("No compatible programmer found on any port.")


class FaultInjectingSerialCommunicator(SerialCommunicator):
    """Dev-only subclass for fw→host fault injection.

    NOT imported in production code. Used only within the dev fault-inject
    subcommand scope. Overrides _decode_id_frame to corrupt the body bytes
    before codec decode — exercising the host decoder's resync path (bounded-
    desync + fail-fast per Phase 50 D-01).

    The body of _read_and_parse_lines() is UNCHANGED (ring-fence preserved,
    GATE-1.8d). Only _decode_id_frame is overridden — this is the correct
    injection point that does NOT touch the generator body (Pitfall 4 / T-53-04).

    XACT-02 / Phase 53 Plan 02.
    """

    def __init__(
        self,
        *args: Any,
        corrupt_incoming_once: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._corrupt_incoming_once = corrupt_incoming_once
        self._fault_fired = False

    def _decode_id_frame(self, frame_len: int, body: bytes) -> Optional[LogMessage]:
        """One-shot incoming-frame fault injection: flip last body byte exactly once.

        After the first call, _fault_fired is set and subsequent calls pass
        through unmodified (one-shot guard). This causes codec.decode_id_frame's
        CRC8 check to fail on the first call, which surfaces as None →
        _read_and_parse_lines re-syncs without touching its body (GATE-1.8d).
        """
        if self._corrupt_incoming_once and not self._fault_fired:
            self._fault_fired = True
            # Flip last byte (CRC8 position) before decode — causes CRC8 mismatch.
            body = body[:-1] + bytes([body[-1] ^ 0x01])
        return super()._decode_id_frame(frame_len, body)


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
