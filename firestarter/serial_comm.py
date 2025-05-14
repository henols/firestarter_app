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
from firestarter.config import get_config_value, set_config_value



logger = logging.getLogger("SerialComm")


rurp_logger = logging.getLogger("RURP")


def check_port(port, data, baud_rate=BAUD_RATE):
    """
    Checks the specified serial port for a valid connection.

    Args:
        port (str): The serial port to check.
        data (str): Data to send for validation.
        baud_rate (str): Baud rate for communication.

    Returns:
        Serial: Open serial connection or None if unsuccessful.
    """
    start_time = time.time()
    try:
        logger.debug(f"Checking port: {port}")

        connection = serial.Serial(
            port=port,
            baudrate=baud_rate,
            timeout=1.0,
            write_timeout=1.0,
        )
        time.sleep(2)  # Allow port to stabilize
        write_data(connection, data.encode("ascii"))

        res, msg = wait_for_ok(connection)
        if not res:
            return None, None

        logger.debug(f"Port found ({time.time() - start_time:.2f}s)")
        logger.debug(f"Programmer: {msg} ")
        return connection, msg
    except (OSError, serial.SerialException, Exception):
        logger.debug(f"Failed to open port: {port}")
        pass

    return None, None


def find_comports(port=None):
    """
    Finds available COM ports based on certain criteria.

    Args:
        port (str): Specific port to check, if any.

    Returns:
        list: List of available COM port identifiers.
    """
    ports = []
    if port:
        ports.append(port)
        return ports
    saved_port = get_config_value("port")
    if saved_port:
        ports.append(saved_port)

    serial_ports = serial.tools.list_ports.comports()
    for port in serial_ports:
        if (
            port.manufacturer
            and (
                "Arduino" in port.manufacturer
                or "FTDI" in port.manufacturer
                or "CH340" in port.manufacturer
            )
            or "USB Serial" in port.description
        ) and port.device not in ports:
            ports.append(port.device)

    logger.debug(f"Found ports:")
    for port in ports:
        logger.debug(f" - {port}")
    return ports


def find_programmer(data, port=None):
    """
    Searches for a compatible programmer on available COM ports.

    Args:
        data (dict): Data to validate the programmer.
        port (str): Specific port to search.

    Returns:
        Serial: Serial connection to the programmer or None if not found.
    """
    start_time = time.time()
    logger.debug(f"Firestarter data: {data}")
    flags = data.get("flags", 0)
    if flags:
        logger.debug("Flags set:")
        if flags & FLAG_FORCE:
            logger.debug(" - Force")
        if flags & FLAG_CAN_ERASE:
            logger.debug(" - Can be erased")
        if flags & FLAG_SKIP_ERASE:
            logger.debug(" - Skip erase")
        if flags & FLAG_SKIP_BLANK_CHECK:
            logger.debug(" - Skip blank check")
        if flags & FLAG_VPE_AS_VPP:
            logger.debug(" - Set VPE as VPP")
        if flags & FLAG_CHIP_ENABLE:
            logger.debug(" - Set chip enable")
        if flags & FLAG_OUTPUT_ENABLE:
            logger.debug(" - Set output enable")

    json_data = json.dumps(data, separators=(",", ":"))
    if port:
        ports = [port]
    else:
        ports = find_comports(port)

    for port in ports:
        serial_port, msg = check_port(port, json_data)
        if serial_port:
            set_config_value("port", port)
            logger.debug(
                f"Found programmer on port: {port} ({time.time() - start_time:.2f}s)"
            )
            return serial_port, msg

    logger.error("No programmer found.")
    return None, None


def read_response(ser, feedback=True):
    while ser.in_waiting > 0:
        byte_array = ser.readline()
        res = read_filtered_bytes(byte_array)
        type = None
        msg = None
        try:
            if res:
                if "OK:" in res:
                    msg = res.split("OK:")[-1].strip()
                    type = "OK"
                    return
                elif "INFO:" in res:
                    msg = res.split("INFO:")[-1].strip()
                    type = "INFO"
                    return
                elif "DEBUG:" in res:
                    msg = res.split("DEBUG:")[-1].strip()
                    type = "DEBUG"
                    return
                elif "ERROR:" in res:
                    msg = res.split("ERROR:")[-1].strip()
                    type = "ERROR"
                    return
                elif "WARN:" in res:
                    msg = res.split("WARN:")[-1].strip()
                    type = "WARN"
                    return
                elif "DATA:" in res:
                    msg = res.split("DATA:")[-1].strip()
                    type = "DATA"
                    return
                else:
                    return
        finally:
            if feedback:
                write_feedback(type, msg)
            # if type == "ERROR":
            #     raise Exception(msg)
            return type, msg
    return None, None


def write_feedback(type, msg):
    """
    Writes feedback messages to the console if verbose mode is enabled.

    Args:
        msg (str): The feedback message.
    """
    if type and msg:
        if type.upper() == "ERROR":
            level = logging.ERROR
        elif type.upper() == "WARN":
            level = logging.WARNING
        else:
            level = logging.DEBUG

        rurp_logger.log(
            level,
            f"{type[:1]}: {msg}" if rurp_logger.isEnabledFor(logging.DEBUG) else msg,
        )


def write_ok(connection):
    # time.sleep(0.1)
    connection.write("OK".encode("ascii"))
    connection.flush()


def write_data(ser, data):
    len = ser.write(data)
    ser.flush()
    return len


def consume_response(connection):
    time.sleep(0.5)
    debug = logger.isEnabledFor(logging.DEBUG)
    while read_response(connection, feedback=debug)[0] != None:
        time.sleep(0.1)


def clean_up(connection):
    """
    Closes the serial connection and cleans up resources.

    Args:
        connection (Serial): The serial connection.
    """
    if connection:
        consume_response(connection)
        connection.close()


def wait_for_ok(connection):
    while True:
        resp, info = wait_for_response(connection)
        if resp == "OK":
            return True, info
        elif resp == "ERROR":
            return False, info


def wait_for_response(connection, timeout=10):
    """
    Waits for a response from the serial connection.

    Args:
        connection (Serial): The serial connection.

    Returns:
        tuple: Response type (e.g., "OK") and message.
    """
    _timeout = time.time() + timeout  # Set timeout period
    while time.time() < _timeout:
        type, msg = read_response(connection)
        if not type:
            continue
        if type and type != "INFO" and type != "DEBUG":
            return type, msg
        _timeout = time.time() + timeout

    raise Exception(f"Timeout, no response on {connection.portstr}")


def read_filtered_bytes(byte_array):
    """
    Filters a byte array to extract readable characters.

    Args:
        byte_array (bytes): Byte array to filter.

    Returns:
        str: Filtered and decoded string or None if no valid characters.
    """
    res = [b for b in byte_array if 32 <= b <= 126]
    return "".join(map(chr, res)) if res else None
