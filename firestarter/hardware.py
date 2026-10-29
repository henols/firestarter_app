"""
Project Name: Firestarter
Copyright (c) 2025 Henrik Olsson

Permission is hereby granted under MIT license.
Hardware Management Module
"""

import logging
import re
import statistics
import time
from typing import NamedTuple, Tuple  # noqa: UP035

from firestarter.config import ConfigManager
from firestarter.constants import (
    COMMAND_CONFIG,
    COMMAND_DEV_ADC,
    COMMAND_HW_VERSION,
    COMMAND_READ_VPE,
    COMMAND_READ_VPP,
    CONFIG_BANDGAP_MAX_MV,
    CONFIG_BANDGAP_MIN_MV,
    CONFIG_R1_MAX,
    CONFIG_R1_MIN,
    CONFIG_R2_MAX,
    CONFIG_R2_MIN,
)
from firestarter.exceptions import (
    HardwareOperationError,
    ProgrammerNotFoundError,
    SerialError,
    SerialTimeoutError,
)
from firestarter.messages import CATALOG, MSG_WARN_VCC_IMPLAUSIBLE
from firestarter.serial_comm import SerialCommunicator

logger = logging.getLogger("Hardware")

# Revision codes the firmware accepts in a config command; -1 is the CLI's
# spelling of 0xFF, "no override".
_CONFIG_REVISIONS = frozenset({-1, 0, 1, 2, 3, 4, 5, 0xFF})


def _config_value_refusal(
    rev: int | None, r1_val: int | None, r2_val: int | None
) -> str | None:
    """The reason a config value is out of the firmware's band, or None."""
    if rev is not None and rev not in _CONFIG_REVISIONS:
        return f"the revision code {rev} is not 0-5 or -1."
    if r1_val is not None and not CONFIG_R1_MIN <= r1_val <= CONFIG_R1_MAX:
        return f"R1 {r1_val} is outside {CONFIG_R1_MIN}-{CONFIG_R1_MAX} ohm."
    if r2_val is not None and not CONFIG_R2_MIN <= r2_val <= CONFIG_R2_MAX:
        return f"R2 {r2_val} is outside {CONFIG_R2_MIN}-{CONFIG_R2_MAX} ohm."
    return None


# The rendered MSG_DATA_VPP_MV / MSG_DATA_VPE_MV text, e.g.
# "VPP: 12449 mV, Internal VCC: 5012 mV". The frame payload does not reach
# the caller (Response.payload is filled only for MSG_DATA_CHUNK), so the
# values come back from the text.
_VOLTAGE_RE = re.compile(r"(\d+) mV, Internal VCC: (\d+) mV")

# Plausible internal-VCC band, in millivolts. USB VBUS is specified
# 4.75-5.25 V (USB 2.0 section 7.2.2) and the board derives VCC from it
# through a series switch, so a reading outside this band is the stored
# bandgap being wrong, not the supply -- and that error scales every VPP/VPE
# reading. The band is for a 5 V board: a py32f071 board runs from 3.3 V and
# gets no such check.
VCC_PLAUSIBLE_MIN_MV = 4400
VCC_PLAUSIBLE_MAX_MV = 5250


def _vcc_is_checked(firmware_identity: str | None) -> bool:
    """False for a board whose VCC is not a 5 V USB supply (py32f071)."""
    return not (firmware_identity or "").endswith(":py32f071")


def _warn_if_vcc_implausible(vcc_mv: int) -> bool:
    """Log MSG_WARN_VCC_IMPLAUSIBLE's text when `vcc_mv` is outside the
    band. Returns True when it warned, so the caller warns once a command."""
    if VCC_PLAUSIBLE_MIN_MV <= vcc_mv <= VCC_PLAUSIBLE_MAX_MV:
        return False
    logger.warning(CATALOG[MSG_WARN_VCC_IMPLAUSIBLE].format % vcc_mv)
    return True


class ProgrammerIdentity(NamedTuple):
    """The two identity values the CAP-02 `MSG_OK_READY` ack carries: the
    human hardware-revision bucket string (ACK #2, `CMD_HW_VERSION`) and the
    raw `"<version>:<board>"` firmware/board tail (ACK #1, set on
    `comm.firmware_identity` during the setup-ack decode). Fields are named
    so callers read them by name -- two `Optional[str]` positionals
    type-check clean when swapped, and a name makes that impossible."""

    hw_revision: str | None
    fw_board_identity: str | None


def _scrub_identity(raw: str | None) -> str | None:
    """Scrub a raw firmware/board identity string for safe recording and
    rendering.

    Keeps only printable ASCII (0x20-0x7E inclusive, so ':', '.', digits and
    letters all survive) and replaces every other character -- U+FFFD from
    serial_comm.py's errors="replace" decode, '\\n', '\\r', any control byte
    -- with a single "?". A partially mangled identity survives with "?" in
    place of the bad bytes and therefore stays visibly faulty: a
    mangled identity is evidence of a transport fault, never silently
    converted to unknown. Truncates to a defensive maximum of 64 characters
    (the firmware caps its tail at 32 in firestarter.cpp, so this is slack,
    not a functional limit). An identity with no printable content at all
    collapses to None because there is no evidence to preserve and PROV-05
    forbids a blank rendering (RESEARCH Open Question 3, resolved in favour
    of collapsing).
    """
    if raw is None or raw == "":
        return None
    scrubbed = "".join(c if "\x20" <= c <= "\x7e" else "?" for c in raw)
    scrubbed = scrubbed[:64]
    return scrubbed if scrubbed else None


class HardwareManager:
    """
    Manages interactions with the EPROM programmer hardware for non-EPROM
    specific operations. This includes retrieving hardware revision, setting
    hardware configurations (like resistor values for voltage dividers),
    and reading VPP/VPE voltages.
    """

    def __init__(self, config_manager: ConfigManager):
        self.config = config_manager

    def _execute_simple_command(
        self, command_dict: dict, operation_name: str
    ) -> Tuple[bool, str | None]:  # noqa: UP006
        """
        Connects, sends a command, expects an OK, and disconnects.
        Returns (success_status, message_from_programmer).
        """
        comm = None
        try:
            comm = SerialCommunicator.find_and_connect(command_dict, self.config)
            # The command_dict itself is the initial command sent by find_and_connect.
            # If find_and_connect succeeds, it means the programmer acknowledged the command.  # noqa: E501
            # The 'msg' from find_and_connect's expect_ack is the programmer_info.
            # For simple state commands, the programmer_info IS the response.

            # If the command sent by find_and_connect was just to establish connection,
            # and the actual data command needs to be sent *after* connection,
            # then we'd do:
            # comm.send_command(actual_data_command_dict)
            # is_ok, msg = comm.expect_ack()
            # For HW_VERSION, FW_VERSION, CONFIG, the initial command IS the data command.  # noqa: E501

            # The `find_and_connect` already sends `command_dict` and expects an OK.
            # The `comm.programmer_info` holds the message part of that OK response.
            if comm.programmer_info is not None:
                logger.info(f"{operation_name}: {comm.programmer_info}")
                return True, comm.programmer_info
            else:
                # This case should ideally be caught by find_and_connect if expect_ack fails.  # noqa: E501
                logger.error(f"Failed to {operation_name.lower()}. No valid response.")
                return False, None
        except (ProgrammerNotFoundError, SerialError, SerialTimeoutError) as e:
            logger.error(f"{operation_name} failed: {e}")
            return False, None
        finally:
            if comm:
                comm.disconnect()

    def get_hardware_revision(self, flags: int = 0) -> bool:
        """
        Reads the hardware revision of the programmer.
        Returns True if successful, False otherwise.
        """
        logger.info("Reading hardware revision...")
        command = {"state": COMMAND_HW_VERSION}
        if flags:
            command["flags"] = flags

        comm = None
        try:
            comm = SerialCommunicator.find_and_connect(command, self.config)
            # The first OK is handled by find_and_connect. Now wait for the second response with the data.  # noqa: E501
            is_ok, msg = comm.expect_ack()
            if is_ok:
                logger.info(f"Hardware revision: {msg}")
                return True
            else:
                logger.error(f"Failed to read hardware revision: {msg}")
                return False
        except (ProgrammerNotFoundError, SerialError, SerialTimeoutError) as e:
            logger.error(f"Failed to read hardware revision: {e}")
            return False
        finally:
            if comm:
                comm.disconnect()

    def read_programmer_identity(self, flags: int = 0) -> ProgrammerIdentity:
        """Value-returning sibling of get_hardware_revision: returns both
        identity values the CAP-02 setup ack carries -- the coarse
        revision-bucket string (or None on any transport error / a
        non-ready ack) and the raw firmware/board identity tail -- instead
        of only logging the former.

        Mirrors get_hardware_revision's exact find_and_connect -> expect_ack
        -> disconnect handshake but returns data rather than printing (same
        relationship sample_vpp_mv/_sample_one_voltage bears to
        read_vpp_voltage). This is the auto-capture source for both
        AutoCapture.hw_revision and AutoCapture.fw_board_identity -- a coarse
        bucket or an honest None is an accepted outcome
        for either field, never a fabricated value. Opens ONE serial read
        (energize/query only) -- no VPP-set, no wire-dict, no --force
        Does NOT change get_hardware_revision's existing
        bool contract -- the `dev hw` CLI command depends on that.

        `comm.firmware_identity` is read before `comm.expect_ack()`:
        it is populated by the setup ack that already happened inside
        find_and_connect, and reading it early means the ack-failure branch
        can still return it -- the two values fail independently. A board
        built without HARDWARE_REVISION answers MSG_ERR_UNKNOWN_CMD here and
        is exactly the board a triager most needs to identify (F-16).
        """
        command = {"state": COMMAND_HW_VERSION}
        if flags:
            command["flags"] = flags

        comm = None
        fw_board_identity = None
        try:
            comm = SerialCommunicator.find_and_connect(command, self.config)
            fw_board_identity = _scrub_identity(comm.firmware_identity)
            is_ok, msg = comm.expect_ack()
            if is_ok:
                return ProgrammerIdentity(
                    hw_revision=msg, fw_board_identity=fw_board_identity
                )
            logger.error(f"Failed to read hardware revision: {msg}")
            return ProgrammerIdentity(
                hw_revision=None, fw_board_identity=fw_board_identity
            )
        except (ProgrammerNotFoundError, SerialError, SerialTimeoutError) as e:
            logger.error(f"Failed to read hardware revision: {e}")
            return ProgrammerIdentity(
                hw_revision=None, fw_board_identity=fw_board_identity
            )
        finally:
            if comm:
                comm.disconnect()

    def set_hardware_config(
        self,
        rev: int | None = None,
        r1_val: int | None = None,
        r2_val: int | None = None,
        flags: int = 0,
    ) -> bool:
        """
        Sets hardware configuration parameters on the programmer.
        Returns True if successful, False otherwise.

        A value the firmware refuses is refused here, before the port opens.
        The firmware reads a leading '-' as 0 and truncates rev to 8 bits
        before its own check, so the raw value is checked here.
        """
        refusal = _config_value_refusal(rev, r1_val, r2_val)
        if refusal:
            logger.error(f"Hardware configuration refused: {refusal}")
            return False
        command = {"state": COMMAND_CONFIG}
        if flags:
            command["flags"] = flags
        log_parts = []
        if rev is not None:
            if rev == -1:  # Special value to disable override
                logger.info("Disabling hardware revision override.")
                command["rev"] = 0xFF
            else:
                command["rev"] = rev
            log_parts.append(f"RevOverride={command['rev']}")
        if r1_val is not None:
            command["r1"] = r1_val
            log_parts.append(f"R1(VPE)={r1_val}")
        if r2_val is not None:
            command["r2"] = r2_val
            log_parts.append(f"R2(GND)={r2_val}")

        if not log_parts:
            logger.info("Reading current hardware configuration...")
        else:
            logger.info(f"Setting hardware configuration: {', '.join(log_parts)}")

        if not log_parts:
            # This is a GET operation, needs special handling for the second response
            comm = None
            try:
                comm = SerialCommunicator.find_and_connect(command, self.config)
                # find_and_connect got the first OK. Now get the second with the data.
                is_ok, msg = comm.expect_ack()
                if is_ok:
                    logger.info(f"Hardware configuration: {msg}")
                    return True
                else:
                    logger.error(f"Failed to read hardware configuration: {msg}")
                    return False
            except (ProgrammerNotFoundError, SerialError, SerialTimeoutError) as e:
                logger.error(f"Failed to read hardware configuration: {e}")
                return False
            finally:
                if comm:
                    comm.disconnect()
        else:
            # This is a SET operation, the simple command execution is sufficient.
            success, _ = self._execute_simple_command(command, "Hardware configuration")
            return success

    # Deliberately field-by-field, not one whole-line pattern. The middle of
    # this message has two renderings: codec.py replaces the raw "Cfg: N" with
    # a silkscreen-aware "Override HW: Rev 2.0-class" when an override is set,
    # and drops the field entirely when it is not. A single pattern spanning
    # them matched only the raw form and broke the moment codec.py did its job.
    # One pattern per field, so wording drift around a field cannot break it.
    _CFG_R1_RE = re.compile(r"R1:\s*(\d+)")
    _CFG_R2_RE = re.compile(r"R2:\s*(\d+)")
    _CFG_BANDGAP_RE = re.compile(r"Bandgap:\s*(\d+)")

    def read_calibration(self, flags: int = 0) -> dict[str, int] | None:
        """Read r1, r2 and the calibrated bandgap.

        The revision override is deliberately not returned: codec.py renders it
        as a silkscreen name rather than the raw byte, so it cannot be read back
        as an integer from this line. `firestarter hw` is where to read it.

        Returns None on any transport error, or when the reply has no bandgap
        field -- which means the firmware predates calibration support.
        """
        command: dict[str, int] = {"state": COMMAND_CONFIG}
        if flags:
            command["flags"] = flags
        comm = None
        try:
            comm = SerialCommunicator.find_and_connect(command, self.config)
            is_ok, msg = comm.expect_ack()
            if not is_ok:
                return None
            text = msg or ""
            r1_m = self._CFG_R1_RE.search(text)
            r2_m = self._CFG_R2_RE.search(text)
            bg_m = self._CFG_BANDGAP_RE.search(text)
            if r1_m is None or r2_m is None or bg_m is None:
                return None
            return {
                "r1": int(r1_m.group(1)),
                "r2": int(r2_m.group(1)),
                "bandgap_mv": int(bg_m.group(1)),
            }
        except (ProgrammerNotFoundError, SerialError, SerialTimeoutError) as e:
            logger.debug(f"Failed to read calibration: {e}")
            return None
        finally:
            if comm:
                comm.disconnect()

    def calibrate_bandgap(self, measured_vcc_mv: int, flags: int = 0) -> bool:
        """Calibrate this board against a measured supply voltage.

        The firmware reads its own bandgap count and back-solves
        ``bandgap_mv = measured_vcc_mv * bandgap_adc / 1024``. It refuses a
        result outside the ATmega datasheet's 1.0-1.2 V window rather than
        clamping it, so a mistyped reading fails loudly instead of storing a
        wrong value the firmware would then trust.
        """
        command: dict[str, int] = {"state": COMMAND_CONFIG, "vcc": measured_vcc_mv}
        if flags:
            command["flags"] = flags
        success, _ = self._execute_simple_command(command, "Calibration")
        return success

    def set_bandgap_mv(self, bandgap_mv: int, flags: int = 0) -> bool:
        """Write the bandgap directly, for --reset back to the nominal.

        A value outside the firmware's accepted band is refused here, before
        the port opens: the firmware refuses it too.
        """
        if not CONFIG_BANDGAP_MIN_MV <= bandgap_mv <= CONFIG_BANDGAP_MAX_MV:
            logger.error(
                f"Calibration reset refused: the bandgap {bandgap_mv} mV is "
                f"outside {CONFIG_BANDGAP_MIN_MV}-{CONFIG_BANDGAP_MAX_MV} mV."
            )
            return False
        command: dict[str, int] = {"state": COMMAND_CONFIG, "bg": bandgap_mv}
        if flags:
            command["flags"] = flags
        success, _ = self._execute_simple_command(command, "Calibration reset")
        return success

    def _read_voltage_loop(
        self,
        state_to_set: int,
        voltage_type_str: str,
        timeout_seconds: int | None = None,
        flags: int = 0,
    ) -> bool:
        """
        Continuously reads and prints voltage from the programmer.
        """
        logger.info(f"Reading {voltage_type_str} voltage...")
        if timeout_seconds:
            logger.info(f"Reading will stop after {timeout_seconds} seconds.")
        else:
            logger.info("Reading continuously. Press Ctrl+C to stop.")

        command_for_connect = {"state": state_to_set}
        if flags:
            command_for_connect["flags"] = flags
        comm = None

        try:
            comm = SerialCommunicator.find_and_connect(command_for_connect, self.config)

            # Wait for the firmware to signal it's ready before we start the reading loop.  # noqa: E501
            # This establishes a handshake and prevents a race condition.
            is_ok, msg = comm.expect_ack()
            if not is_ok:
                logger.error(
                    f"Firmware did not signal ready for voltage reading: {msg}"
                )
                return False
            logger.debug(f"Firmware ready for voltage reading: {msg}")

            # After receiving ready, send an ACK to start the reading loop on the firmware.  # noqa: E501
            comm.send_ack()

            # The VCC warning is printed once per command, not once per
            # reading: the condition cannot change between samples.
            vcc_warned = not _vcc_is_checked(comm.firmware_identity)
            start_time = time.time()
            while True:
                response = comm.get_response()
                response_type = response.type
                message = response.message

                if response_type == "DATA":
                    reading = self._parse_voltage_frame(message)
                    if reading is not None:
                        rail_mv, vcc_mv = reading
                        if not vcc_warned and _warn_if_vcc_implausible(vcc_mv):
                            vcc_warned = True
                        message = (
                            f"{voltage_type_str}: {rail_mv / 1000:.2f} V, "
                            f"Internal VCC: {vcc_mv / 1000:.2f} V"
                        )
                    print(f"\r{message}    ", end="", flush=True)

                    if timeout_seconds and (time.time() - start_time > timeout_seconds):
                        print()  # Newline after continuous printing
                        logger.info(
                            f"{voltage_type_str} reading timed out after {timeout_seconds}s."  # noqa: E501
                        )
                        return True

                    comm.send_ack()  # Acknowledge data and request next reading
                elif response_type == "OK":
                    print()
                    logger.info(
                        f"{voltage_type_str} reading finished by programmer: {message or 'OK'}"  # noqa: E501
                    )
                    return True
                elif response_type == "WARN":
                    # A warning never ends a read, and it is not a sample:
                    # do NOT ack here -- the reading it precedes is still
                    # coming. No log call: _log_response in serial_comm
                    # already surfaces every WARN frame at logging.WARNING.
                    print()
                elif response_type == "ERROR":
                    print()
                    logger.error(f"Error reading {voltage_type_str}: {message}")
                    return False
                else:  # Timeout or unexpected
                    print()
                    logger.error(
                        f"Unexpected response or timeout reading {voltage_type_str}: {response_type} - {message}"  # noqa: E501
                    )
                    return False
        except (
            ProgrammerNotFoundError,
            SerialError,
            SerialTimeoutError,
            HardwareOperationError,
        ) as e:
            print()
            logger.error(f"Failed to read {voltage_type_str} voltage: {e}")
            return False
        except KeyboardInterrupt:
            print()  # Newline after Ctrl+C
            logger.info(f"\n{voltage_type_str} reading stopped by user.")
            return True
        finally:
            if comm:
                comm.disconnect()

    def read_vpp_voltage(
        self, timeout_seconds: int | None = None, flags: int = 0
    ) -> bool:
        """Reads the VPP voltage from the programmer."""
        return self._read_voltage_loop(COMMAND_READ_VPP, "VPP", timeout_seconds, flags)

    def read_vpe_voltage(
        self, timeout_seconds: int | None = None, flags: int = 0
    ) -> bool:
        """Reads the VPE voltage from the programmer."""
        return self._read_voltage_loop(COMMAND_READ_VPE, "VPE", timeout_seconds, flags)

    def _parse_voltage_frame(self, message: str | None) -> tuple[int, int] | None:
        """
        Returns `(rail_mv, vcc_mv)` from a MSG_DATA_VPP_MV / MSG_DATA_VPE_MV
        message, e.g. "VPP: 12449 mV, Internal VCC: 5012 mV" -> (12449, 5012).

        Returns None (honest fallback, never a fabricated 0) if the message
        does not match -- e.g. no message, garbage, or an unexpected format.
        """
        match = _VOLTAGE_RE.search(message or "")
        if not match:
            return None
        return int(match.group(1)), int(match.group(2))

    def _sample_one_voltage(self, state: int, n: int = 3, flags: int = 0) -> int | None:
        """
        Reads N DATA frames for the given rail `state` (COMMAND_READ_VPP or
        COMMAND_READ_VPE) and returns the median reconstructed mV value.

        Mirrors the _read_voltage_loop handshake (find_and_connect ->
        expect_ack -> send_ack -> get_response) but stops after `n` frames
        instead of looping forever, and returns a value instead of printing.

        Unlike the standalone read loop (which runs until the OPERATOR hits
        Ctrl+C), this method deliberately stops acking after `n` frames.
        The firmware's hw_read_voltage handler now recognizes an explicit
        `DONE` message (mirrors eprom_write's OP_MSG_DONE handling) and ends
        the command immediately on receipt, so we send one here -- before
        disconnecting -- instead of leaving the command dangling for the
        firmware's watchdog to reap (dev-test-vpp-vpe-timeout debug session,
        2026-07-03; firmware fix in hardware_operations.cpp).

        Returns None on any transport error, a non-ready ack, a non-DATA
        response, or when no sample could be parsed (honest fallback, never
        a fabricated 0).
        """
        command_for_connect = {"state": state}
        if flags:
            command_for_connect["flags"] = flags
        comm = None
        samples: list = []
        try:
            comm = SerialCommunicator.find_and_connect(command_for_connect, self.config)

            is_ok, _ = comm.expect_ack()
            if not is_ok:
                return None

            comm.send_ack()  # start the reading loop on the firmware
            vcc_warned = not _vcc_is_checked(comm.firmware_identity)

            for _ in range(n):
                response = comm.get_response()
                if response.type == "WARN":
                    # Same contract as _read_voltage_loop: a warning precedes
                    # the DATA frame for its own sample and must not consume
                    # this iteration's ack. serial_comm already logged it.
                    response = comm.get_response()
                if response.type != "DATA":
                    break
                reading = self._parse_voltage_frame(response.message)
                if reading is not None:
                    rail_mv, vcc_mv = reading
                    samples.append(rail_mv)
                    if not vcc_warned and _warn_if_vcc_implausible(vcc_mv):
                        vcc_warned = True
                comm.send_ack()  # acknowledge data and request next reading

            # Tell the firmware to end the command now, rather than letting
            # it stay active until its own watchdog times it out.
            comm.send_done()
        except (
            ProgrammerNotFoundError,
            SerialError,
            SerialTimeoutError,
            HardwareOperationError,
        ) as e:
            logger.debug(f"Failed to sample voltage: {e}")
            return None
        finally:
            if comm:
                comm.disconnect()

        return int(statistics.median(samples)) if samples else None

    _ADC_RAW_RE = re.compile(
        r"ADC mode (\d+): divider (\d+), bandgap (\d+), V (\d+) mV, "
        r"VCC (\d+) mV, R1 (\d+), R2 (\d+)"
    )

    def read_adc_raw(self, flags: int = 0) -> list[dict[str, int]] | None:
        """Read the raw ADC diagnostic frames (dev-gated CMD_DEV_ADC).

        The firmware sweeps three rail states in one command and emits one
        MSG_DATA_ADC_RAW frame per state, so all three are sampled at a single
        pot setting. Every field is a full integer, unlike the vpp/vpe
        monitors whose wire format carries only tenths of a volt.

        Returns one dict per frame with keys mode, divider_adc, bandgap_adc,
        voltage_mv, vcc_mv, r1, r2 -- or None on any transport error or if no
        frame could be parsed. Never a fabricated zero.
        """
        comm = None
        frames: list[dict[str, int]] = []
        command: dict[str, int] = {"state": COMMAND_DEV_ADC}
        if flags:
            command["flags"] = flags
        try:
            # No expect_ack() here. find_and_connect consumes the firmware's
            # MSG_OK_READY as part of the handshake, and dt_read_adc sends no
            # second ack -- it emits its three DATA frames and ends. An
            # expect_ack() at this point eats those frames looking for an OK
            # that never comes, then times out.
            comm = SerialCommunicator.find_and_connect(command, self.config)
            for _ in range(3):
                response = comm.get_response()
                if response.type != "DATA":
                    break
                match = self._ADC_RAW_RE.search(response.message or "")
                if match is None:
                    continue
                mode, divider, bandgap, voltage_mv, vcc_mv, r1, r2 = (
                    int(g) for g in match.groups()
                )
                frames.append(
                    {
                        "mode": mode,
                        "divider_adc": divider,
                        "bandgap_adc": bandgap,
                        "voltage_mv": voltage_mv,
                        "vcc_mv": vcc_mv,
                        "r1": r1,
                        "r2": r2,
                    }
                )
        except (
            ProgrammerNotFoundError,
            SerialError,
            SerialTimeoutError,
            HardwareOperationError,
        ) as e:
            logger.debug(f"Failed to read raw ADC: {e}")
            return None
        finally:
            if comm:
                comm.disconnect()
        return frames or None

    def sample_vpp_mv(self, n: int = 3) -> int | None:
        """Value-returning sibling of read_vpp_voltage: median VPP mV over
        `n` samples (100 mV resolution), or None if not measured."""
        return self._sample_one_voltage(COMMAND_READ_VPP, n=n)

    def sample_vpe_mv(self, n: int = 3) -> int | None:
        """Value-returning sibling of read_vpe_voltage: median VPE mV over
        `n` samples (100 mV resolution), or None if not measured."""
        return self._sample_one_voltage(COMMAND_READ_VPE, n=n)
