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
from typing import Optional, Tuple  # noqa: UP035

from firestarter.config import ConfigManager
from firestarter.constants import (
    COMMAND_CONFIG,
    COMMAND_HW_VERSION,
    COMMAND_READ_VPE,
    COMMAND_READ_VPP,
)
from firestarter.exceptions import (
    HardwareOperationError,
    ProgrammerNotFoundError,
    SerialError,
    SerialTimeoutError,
)
from firestarter.serial_comm import SerialCommunicator

logger = logging.getLogger("Hardware")

# Tolerant match for the FIRST "%u.%uV" pair in a 0xE4/0xE5 DATA message
# (e.g. "VPP: 20.9V, Internal VCC: 5.0V") -- guards against catalog format
# wording drift (Pitfall 3 / T-111-DRIFT).
_VOLTAGE_RE = re.compile(r"(\d+)\.(\d+)\s*V")


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
    ) -> Tuple[bool, Optional[str]]:  # noqa: UP006
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
            # comm.send_json_command(actual_data_command_dict)
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
            logger.error(f"Failed to {operation_name.lower()}: {e}")
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

    def set_hardware_config(
        self,
        rev: Optional[int] = None,
        r1_val: Optional[int] = None,
        r2_val: Optional[int] = None,
        flags: int = 0,
    ) -> bool:
        """
        Sets hardware configuration parameters on the programmer.
        Returns True if successful, False otherwise.
        """
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

    def _read_voltage_loop(
        self,
        state_to_set: int,
        voltage_type_str: str,
        timeout_seconds: Optional[int] = None,
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

            start_time = time.time()
            while True:
                response = comm.get_response()
                response_type = response.type
                message = response.message

                if response_type == "DATA":
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
        self, timeout_seconds: Optional[int] = None, flags: int = 0
    ) -> bool:
        """Reads the VPP voltage from the programmer."""
        return self._read_voltage_loop(COMMAND_READ_VPP, "VPP", timeout_seconds, flags)

    def read_vpe_voltage(
        self, timeout_seconds: Optional[int] = None, flags: int = 0
    ) -> bool:
        """Reads the VPE voltage from the programmer."""
        return self._read_voltage_loop(COMMAND_READ_VPE, "VPE", timeout_seconds, flags)

    def _parse_voltage_frame(self, message: Optional[str]) -> Optional[int]:
        """
        Parses the FIRST "%u.%uV" pair out of a 0xE4/0xE5 DATA message (e.g.
        "VPP: 20.9V, Internal VCC: 5.0V") and reconstructs the value as
        v_int*1000 + v_dec*100 -- the wire only carries whole volts + one
        tenths digit, so the result sits on a 100 mV grid (never finer).

        Returns None (honest fallback, never a fabricated 0) if the message
        does not match -- e.g. no message, garbage, or an unexpected format.
        """
        match = _VOLTAGE_RE.search(message or "")
        if not match:
            return None
        v_int, v_dec = int(match.group(1)), int(match.group(2))
        return v_int * 1000 + v_dec * 100

    def _sample_one_voltage(
        self, state: int, n: int = 3, flags: int = 0
    ) -> Optional[int]:
        """
        Reads N DATA frames for the given rail `state` (COMMAND_READ_VPP or
        COMMAND_READ_VPE) and returns the median reconstructed mV value.

        Mirrors the _read_voltage_loop handshake (find_and_connect ->
        expect_ack -> send_ack -> get_response) but stops after `n` frames
        instead of looping forever, and returns a value instead of printing.

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

            for _ in range(n):
                response = comm.get_response()
                if response.type != "DATA":
                    break
                mv = self._parse_voltage_frame(response.message)
                if mv is not None:
                    samples.append(mv)
                comm.send_ack()  # acknowledge data and request next reading
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

    def sample_vpp_mv(self, n: int = 3) -> Optional[int]:
        """Value-returning sibling of read_vpp_voltage: median VPP mV over
        `n` samples (100 mV resolution), or None if not measured."""
        return self._sample_one_voltage(COMMAND_READ_VPP, n=n)

    def sample_vpe_mv(self, n: int = 3) -> Optional[int]:
        """Value-returning sibling of read_vpe_voltage: median VPE mV over
        `n` samples (100 mV resolution), or None if not measured."""
        return self._sample_one_voltage(COMMAND_READ_VPE, n=n)
