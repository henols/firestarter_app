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

try:
    from .config import get_config_value, set_config_value
    from .utils import verbose

except ImportError:
    from config import get_config_value, set_config_value
    from utils import verbose

# Constants
BAUD_RATE = "250000"
FALLBACK_BAUD_RATE = "115200"
BUFFER_SIZE = 512


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
    try:
        if verbose():
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
                return None
            res, msg = wait_for_response(ser)

        if verbose():
            print(f"Programmer: {msg}")
        return ser
    except (OSError, serial.SerialException, Exception):
        if verbose():
            print(f"Failed to open port: {port}")
        pass

    return None


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

    if verbose():
        print(f"Found ports: {ports}")
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

    if verbose():
        print(f"Firestarter data: {data}")

    json_data = json.dumps(data, separators=(",", ":"))
    if port:
        ports = [port]
    else:
        ports = find_comports(port)

    for port in ports:
        serial_port = check_port(port, json_data)
        if serial_port:
            set_config_value("port", port)
            return serial_port

    print("No programmer found.")
    return None


def read_response(ser):
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
            write_feedback(type, msg)
            # if type == "ERROR":
            #     raise Exception(msg)
            return type, msg
    return None, None


def consume_response(ser):
    time.sleep(0.1)
    while read_response(ser)[0] != None:
        time.sleep(0.1)


def wait_for_response(ser, timeout=2):
    """
    Waits for a response from the serial connection.

    Args:
        ser (Serial): The serial connection.

    Returns:
        tuple: Response type (e.g., "OK") and message.
    """
    _timeout = time.time() + timeout  # Set timeout period
    while time.time() < _timeout:
        type, msg = read_response(ser)
        if not type:
            continue
        if type and type != "INFO" and type != "DEBUG":
            return type, msg
        _timeout = time.time() + timeout
        
    raise Exception(f"Timeout, no response on {ser.portstr}")


def write_feedback(type, msg):
    """
    Writes feedback messages to the console if verbose mode is enabled.

    Args:
        msg (str): The feedback message.
    """
    if type and msg and (verbose() or (type.upper() == "ERROR" or type == "WARN")):
        print(f"{type}: {msg}")


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
