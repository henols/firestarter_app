"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

EPROM Operations Module
"""

import os
import time
# from serial_comm import find_programmer, wait_for_response, print_progress
# from config import get_config_value
# from database import get_eprom, search_eprom, get_eproms
# from utils import extract_hex_to_decimal

from .serial_comm import find_programmer, wait_for_response, print_progress
from .config import get_config_value
from .database import get_eprom, search_eprom, get_eproms
from .utils import extract_hex_to_decimal

# Constants
BUFFER_SIZE = 512
STATE_READ = 1
STATE_WRITE = 2
STATE_ERASE = 3
STATE_CHECK_BLANK = 4
STATE_CHECK_CHIP_ID = 5


STATE_READ_VPP = 11
STATE_READ_VPE = 12
STATE_FW_VERSION = 13
STATE_CONFIG = 14
STATE_HW_VERSION = 15

def list_eproms(verified=False):
    """
    Lists EPROMs in the database.

    Args:
        verified (bool): If True, only lists verified EPROMs.
    """
    print("Listing EPROMs in the database:")
    eproms = get_eproms(verified)
    if not eproms:
        print("No EPROMs found.")
    for eprom in eproms:
        print(eprom)


def search_eproms(query):
    """
    Searches for EPROMs in the database by name or properties.

    Args:
        query (str): Search text.
    """
    print(f"Searching for EPROMs with query: {query}")
    results = search_eprom(query, True)
    if not results:
        print("No matching EPROMs found.")
    for result in results:
        print(result)


def eprom_info(eprom_name):
    """
    Displays information about a specific EPROM.

    Args:
        eprom_name (str): Name of the EPROM.
    """
    eprom = get_eprom(eprom_name)
    if not eprom:
        print(f"EPROM {eprom_name} not found.")
        return
    print(f"Information for EPROM {eprom_name}:")
    for key, value in eprom.items():
        print(f"{key}: {value}")


def read_chip(eprom_name, output_file=None, force=False):
    """
    Reads data from an EPROM.

    Args:
        eprom_name (str): Name of the EPROM.
        output_file (str): File to save the data (optional).
        force (bool): Force reading even if chip ID mismatches.
    """
    eprom = get_eprom(eprom_name)
    if not eprom:
        print(f"EPROM {eprom_name} not found.")
        return 1

    eprom_name = eprom.pop("name")
    eprom["state"] = STATE_READ
    if force:
        eprom["force"] = True

    if not output_file:
        output_file = f"{eprom_name}.bin"
    print(f"Reading EPROM {eprom_name}, saving to {output_file}")

    ser = find_programmer(eprom)
    if not ser:
        return 1

    try:
        ser.write("OK".encode("ascii"))
        ser.flush()
        with open(output_file, "wb") as file:
            bytes_read = 0
            mem_size = eprom["memory-size"]
            start_time = time.time()

            while True:
                resp, info = wait_for_response(ser)
                if resp == "DATA":
                    data = ser.read(BUFFER_SIZE)
                    file.write(data)
                    bytes_read += len(data)
                    percent = int(bytes_read / mem_size * 100)
                    print_progress(percent, bytes_read - BUFFER_SIZE, bytes_read)
                    ser.write("OK".encode("ascii"))
                    ser.flush()
                elif resp == "OK":
                    print("\nRead complete.")
                    break
                elif resp == "ERROR":
                    print(f"\nError: {info}")
                    return 1
                else:
                    print(f"\nUnexpected response: {info}")
                    return 1

            print(f"Data saved to {output_file}")
            print(f"Time taken: {time.time() - start_time:.2f}s")
    except Exception as e:
        print(f"Error while reading: {e}")
    finally:
        ser.close()


def write_chip(eprom_name, input_file, address=None, ignore_blank_check=False, force=False):
    """
    Writes data to an EPROM.

    Args:
        eprom_name (str): Name of the EPROM.
        input_file (str): File containing the data to write.
        address (str): Starting address for writing (optional).
        ignore_blank_check (bool): If True, skip blank check and erase steps.
        force (bool): Force writing even if chip ID mismatches.
    """
    eprom = get_eprom(eprom_name)
    if not eprom:
        print(f"EPROM {eprom_name} not found.")
        return 1
    if not os.path.exists(input_file):
        print(f"Input file {input_file} not found.")
        return 1

    eprom_name = eprom.pop("name")
    eprom["state"] = STATE_WRITE
    if address:
        eprom["address"] = int(address, 16) if "0x" in address else int(address)
    if ignore_blank_check:
        eprom["skip-erase"] = True
        eprom["blank-check"] = False
    if force:
        eprom["force"] = True

    ser = find_programmer(eprom)
    if not ser:
        return 1

    try:
        with open(input_file, "rb") as file:
            bytes_written = 0
            mem_size = eprom["memory-size"]
            file_size = os.path.getsize(input_file)

            if file_size != mem_size:
                print("Warning: File size does not match memory size.")

            print(f"Writing {input_file} to {eprom_name}...")

            while True:
                data = file.read(BUFFER_SIZE)
                if not data:
                    ser.write(int(0).to_bytes(2, byteorder="big"))
                    ser.flush()
                    break

                ser.write(len(data).to_bytes(2, byteorder="big"))
                ser.flush()
                ser.write(data)
                ser.flush()

                resp, info = wait_for_response(ser)
                if resp == "OK":
                    bytes_written += len(data)
                    percent = int(bytes_written / file_size * 100)
                    print_progress(percent, bytes_written - BUFFER_SIZE, bytes_written)
                elif resp == "ERROR":
                    print(f"\nError: {info}")
                    return 1

            print("\nWrite complete.")
    except Exception as e:
        print(f"Error while writing: {e}")
    finally:
        ser.close()


def erase(eprom_name):
    """
    Erases an EPROM.

    Args:
        eprom_name (str): Name of the EPROM.
    """
    eprom = get_eprom(eprom_name)
    if not eprom:
        print(f"EPROM {eprom_name} not found.")
        return 1

    eprom["state"] = STATE_ERASE
    ser = find_programmer(eprom)
    if not ser:
        return 1

    resp, info = wait_for_response(ser)
    if resp == "OK":
        print(f"EPROM {eprom_name} erased successfully.")
    elif resp == "ERROR":
        print(f"Error erasing EPROM {eprom_name}: {info}")


def check_chip_id(eprom_name):
    """
    Checks the chip ID of an EPROM.

    Args:
        eprom_name (str): Name of the EPROM.
    """
    eprom = get_eprom(eprom_name)
    if not eprom:
        print(f"EPROM {eprom_name} not found.")
        return 1

    eprom["state"] = STATE_CHECK_CHIP_ID
    ser = find_programmer(eprom)
    if not ser:
        return 1

    resp, info = wait_for_response(ser)
    if resp == "OK":
        print(f"Chip ID check passed for {eprom_name}: {info}")
    elif resp == "ERROR":
        print(f"Chip ID check failed for {eprom_name}: {info}")
        chip_id = extract_hex_to_decimal(info)
        print(f"Extracted chip ID: {chip_id}" if chip_id else "Failed to extract chip ID.")


def blank_check(eprom_name):
    """
    Performs a blank check on an EPROM.

    Args:
        eprom_name (str): Name of the EPROM.
    """
    eprom = get_eprom(eprom_name)
    if not eprom:
        print(f"EPROM {eprom_name} not found.")
        return 1

    eprom["state"] = STATE_CHECK_BLANK
    ser = find_programmer(eprom)
    if not ser:
        return 1

    resp, info = wait_for_response(ser)
    if resp == "OK":
        print(f"EPROM {eprom_name} is blank.")
    elif resp == "ERROR":
        print(f"Blank check failed for {eprom_name}: {info}")

def read_vpp_voltage():
    """
    Reads the VPP voltage from the programmer.
    """
    read_voltage(STATE_READ_VPP)

def read_vpe_voltage():
    """
    Reads the VPE voltage from the programmer.
    """
    read_voltage(STATE_READ_VPE)

def read_voltage(state):
    data = {}
    data["state"] = state

    type = "VPE"
    if state == STATE_READ_VPP:
        type = "VPP"
    print(f"Reading {type} voltage")
    ser = find_programmer(data)
    if not ser:
        return 1
    resp, info = wait_for_response(ser)
    if not resp == "OK":
        print()
        print(f"Error reading {type} voltage: {info}")
        return 1
    ser.write("OK".encode("ascii"))

    while (t := wait_for_response(ser))[0] == "DATA":
        print(f"\r{t[1]}", end="")
        ser.write("OK".encode("ascii"))
    ser.close()

