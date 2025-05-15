"""
Project Name: Firestarter
Copyright (c) 2025 Henrik Olsson

Permission is hereby granted under MIT license.
Hardware Management Module
"""

import time
import logging

from firestarter.constants import *
from firestarter.serial_comm import (
    SerialCommunicator,
    ProgrammerNotFoundError,
    SerialError,
    SerialTimeoutError,
)

logger = logging.getLogger("Hardware")


class HardwareOperationError(Exception):
    """Custom exception for hardware operation failures."""

    pass


class HardwareManager:
    """
    Manages interactions with the EPROM programmer hardware for non-EPROM
    specific operations. This includes retrieving hardware revision, setting
    hardware configurations (like resistor values for voltage dividers),
    and reading VPP/VPE voltages.
    """

    def __init__(self):
        # No persistent state needed in constructor for now,
        # SerialCommunicator is established per operation.
        pass

    def _execute_simple_command(
        self, command_dict: dict, operation_name: str
    ) -> tuple[bool, str | None]:
        """
        Connects, sends a command, expects an OK, and disconnects.
        Returns (success_status, message_from_programmer).
        """
        comm = None
        try:
            comm = SerialCommunicator.find_and_connect(command_dict)
            # The command_dict itself is the initial command sent by find_and_connect.
            # If find_and_connect succeeds, it means the programmer acknowledged the command.
            # The 'msg' from find_and_connect's expect_ok is the programmer_info.
            # For simple state commands, the programmer_info IS the response.

            # If the command sent by find_and_connect was just to establish connection,
            # and the actual data command needs to be sent *after* connection,
            # then we'd do:
            # comm.send_json_command(actual_data_command_dict)
            # is_ok, msg = comm.expect_ok()
            # For HW_VERSION, FW_VERSION, CONFIG, the initial command IS the data command.

            # The `find_and_connect` already sends `command_dict` and expects an OK.
            # The `comm.programmer_info` holds the message part of that OK response.
            if comm.programmer_info is not None:
                logger.info(f"{operation_name}: {comm.programmer_info}")
                return True, comm.programmer_info
            else:
                # This case should ideally be caught by find_and_connect if expect_ok fails.
                logger.error(f"Failed to {operation_name.lower()}. No valid response.")
                return False, None
        except (ProgrammerNotFoundError, SerialError, SerialTimeoutError) as e:
            logger.error(f"Failed to {operation_name.lower()}: {e}")
            return False, None
        finally:
            if comm:
                comm.disconnect()

    def get_hardware_revision(self) -> bool:
        """
        Reads the hardware revision of the programmer.
        Returns True if successful, False otherwise.
        """
        logger.info("Reading hardware revision...")
        command = {"state": COMMAND_HW_VERSION}
        success, _ = self._execute_simple_command(command, "Hardware revision")
        return success

    def set_hardware_config(
        self,
        rev: int | None = None,
        r1_val: int | None = None,
        r2_val: int | None = None,
    ) -> bool:
        """
        Sets hardware configuration parameters on the programmer.
        Returns True if successful, False otherwise.
        """
        command = {"state": COMMAND_CONFIG}
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

        success, _ = self._execute_simple_command(command, "Hardware configuration")
        return success

    def _read_voltage_loop(
        self,
        state_to_set: int,
        voltage_type_str: str,
        timeout_seconds: int | None = None,
    ) -> bool:
        """
        Continuously reads and prints voltage from the programmer.
        """
        logger.info(f"Reading {voltage_type_str} voltage...")
        command_for_connect = {"state": state_to_set}
        comm = None

        try:
            comm = SerialCommunicator.find_and_connect(command_for_connect)
            # After find_and_connect, the programmer is in the specified state.
            # Now, tell it to start sending data by sending an ACK.
            comm.send_ack()

            start_time = time.time()
            while True:
                response_type, message = comm.get_response()  # Wait for DATA: or OK:
                if response_type == "DATA":
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.info(message)  # Full log for debug
                    else:
                        print(f"\r{message}    ", end="")  # Overwrite line for INFO

                    if timeout_seconds and (time.time() - start_time > timeout_seconds):
                        print()  # Newline after continuous printing
                        logger.info(
                            f"{voltage_type_str} reading timed out after {timeout_seconds}s."
                        )
                        return True  # Or False if timeout should be an error

                    comm.send_ack()  # Acknowledge data and request next
                elif response_type == "OK":
                    print()  # Newline after continuous printing
                    logger.info(
                        f"{voltage_type_str} reading finished by programmer: {message or 'OK'}"
                    )
                    return True
                elif response_type == "ERROR":
                    print()
                    logger.error(f"Error reading {voltage_type_str}: {message}")
                    return False
                else:  # Timeout or unexpected
                    print()
                    logger.error(
                        f"Unexpected response or timeout reading {voltage_type_str}: {response_type} - {message}"
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
        finally:
            if comm:
                comm.disconnect()

    def read_vpp_voltage(self, timeout_seconds: int | None = None) -> bool:
        """Reads the VPP voltage from the programmer."""
        return self._read_voltage_loop(COMMAND_READ_VPP, "VPP", timeout_seconds)

    def read_vpe_voltage(self, timeout_seconds: int | None = None) -> bool:
        """Reads the VPE voltage from the programmer."""
        return self._read_voltage_loop(COMMAND_READ_VPE, "VPE", timeout_seconds)
