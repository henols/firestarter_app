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

from .config import get_config_value, set_config_value

# Constants
BAUD_RATE = "250000"
FALLBACK_BAUD_RATE = "115200"
BUFFER_SIZE = 512
 

def check_port(port, data, baud_rate=BAUD_RATE, verbose=False):
    """
    Checks the specified serial port for a valid connection.

    Args:
        port (str): The serial port to check.
        data (str): Data to send for validation.
        baud_rate (str): Baud rate for communication.
        verbose (bool): Enables verbose output.

    Returns:
        Serial: Open serial connection or None if unsuccessful.
    """
    try:
        if verbose:
            print(f"Checking port: {port}")

        ser = serial.Serial(
            port=port,
            baudrate=baud_rate,
            timeout=1.0,
        )
        time.sleep(2)  # Allow port to stabilize
        ser.write(data.encode("ascii"))
        ser.flush()

        res, msg = wait_for_response(ser)
        while res != "OK":
            if res == "ERROR":
                raise Exception(msg)
            print(f"{res} - {msg}")
            if res == "TIMEOUT_ERROR":
                return None

        if verbose:
            print(f"Programmer: {msg}")
        return ser
    except (OSError, serial.SerialException):
        pass

    return None


def find_comports(port=None, verbose=False):
    """
    Finds available COM ports based on certain criteria.

    Args:
        port (str): Specific port to check, if any.
        verbose (bool): Enables verbose output.

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
            and ("Arduino" in port.manufacturer or "FTDI" in port.manufacturer)
            and port.device not in ports
        ):
            ports.append(port.device)

    if verbose:
        print(f"Found ports: {ports}")
    return ports


def find_programmer(data, port=None, verbose=False):
    """
    Searches for a compatible programmer on available COM ports.

    Args:
        data (dict): Data to validate the programmer.
        port (str): Specific port to search.
        verbose (bool): Enables verbose output.

    Returns:
        Serial: Serial connection to the programmer or None if not found.
    """
    if verbose:
        print(f"Firestarter data: {data}")

    json_data = json.dumps(data, separators=(",", ":"))
    ports = find_comports(port, verbose)

    for port in ports:
        serial_port = check_port(port, json_data, verbose=verbose)
        if serial_port:
            set_config_value("port", port)
            return serial_port

    for port in ports:
        serial_port = check_port(
            port, json_data, baud_rate=FALLBACK_BAUD_RATE, verbose=verbose
        )
        if serial_port:
            set_config_value("port", port)
            print(
                f"Using fallback baud rate: {FALLBACK_BAUD_RATE}. Consider updating firmware."
            )
            return serial_port

    print("No programmer found.")
    return None


def wait_for_response(ser):
    """
    Waits for a response from the serial connection.

    Args:
        ser (Serial): The serial connection.

    Returns:
        tuple: Response type (e.g., "OK") and message.
    """
    timeout = time.time() + 2  # Set timeout period
    while time.time() < timeout:
        if ser.in_waiting > 0:
            byte_array = ser.readline()
            res = read_filtered_bytes(byte_array)
            if res:
                if "OK:" in res:
                    msg = res.split("OK:")[-1].strip()
                    return "OK", msg
                elif "ERROR:" in res:
                    msg = res.split("ERROR:")[-1].strip()
                    return "ERROR", msg
                elif "WARN:" in res:
                    msg = res.split("WARN:")[-1].strip()
                    return "WARN", msg
                elif "DATA:" in res:
                    msg = res.split("DATA:")[-1].strip()
                    return "DATA", msg
        time.sleep(0.1)

    return "TIMEOUT_ERROR", "Timeout waiting for response"


def write_feedback(msg, verbose=False):
    """
    Writes feedback messages to the console if verbose mode is enabled.

    Args:
        msg (str): The feedback message.
        verbose (bool): Enables verbose output.
    """
    if verbose:
        print(msg)


def print_progress(percent, from_address, to_address, verbose=False):
    """
    Displays progress of operations.

    Args:
        percent (int): Progress percentage.
        from_address (int): Starting address.
        to_address (int): Ending address.
        verbose (bool): Enables verbose output.
    """
    if verbose:
        print(f"{percent}%, address: 0x{from_address:X} - 0x{to_address:X}")
    else:
        print(f"\r{percent}%, address: 0x{from_address:X} - 0x{to_address:X} ", end="")


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
