"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

EPROM Operations Module
"""

import os
import time

try:
    from .serial_comm import find_programmer, wait_for_response, consume_response
    from .database import get_eprom as db_get_eprom
    from .database import search_chip_id
    from .utils import extract_hex_to_decimal, print_progress_bar
except ImportError:
    from serial_comm import find_programmer, wait_for_response, consume_response
    from database import get_eprom as db_get_eprom
    from database import search_chip_id
    from utils import extract_hex_to_decimal, print_progress_bar

# Constants
BUFFER_SIZE = 512

STATE_READ = 1
STATE_WRITE = 2
STATE_ERASE = 3
STATE_CHECK_BLANK = 4
STATE_CHECK_CHIP_ID = 5
STATE_VERIFY = 6

# Control Flags
FLAG_FORCE = 0x01
FLAG_CAN_ERASE = 0x02
FLAG_SKIP_ERASE = 0x04
FLAG_SKIP_BLANK_CHECK = 0x08
FLAG_VPE_AS_VPP = 0x10


def read(eprom_name, output_file=None, force=False, address=None, size=None):
    """
    Reads data from an EPROM.

    Args:
        eprom_name (str): Name of the EPROM.
        output_file (str): File to save the data (optional).
        force (bool): Force reading even if chip ID mismatches.
    """
    start_time = time.time()
    eprom = get_eprom(eprom_name)
    if not eprom:
        print(f"EPROM {eprom_name} not found.")
        return 1

    eprom["state"] = STATE_READ

    read_address = 0
    if address:
        read_address = int(address, 16) if "0x" in address else int(address)
        eprom["address"] = read_address

    if size:
        eprom["memory-size"] = (
            int(size, 16) if "0x" in size else int(size)
        ) + read_address

    if force:
        set_eprom_flag(eprom, FLAG_FORCE)

    ser = find_programmer(eprom)
    if not ser:
        return 1

    if not output_file:
        output_file = f"{eprom_name.upper()}.bin"
    print(f"Reading EPROM {eprom_name}, saving to {output_file}")

    try:
        ser.write("OK".encode("ascii"))
        ser.flush()
        with open(output_file, "wb") as file:
            bytes_read = 0
            mem_size = eprom["memory-size"]
            total_iterations = (mem_size-read_address) / BUFFER_SIZE
            print_progress_bar(0, total_iterations, prefix=f"Address:        -       ")

            while True:
                resp, info = wait_for_response(ser)
                if resp == "DATA":
                    data = ser.read(BUFFER_SIZE)
                    file.write(data)
                    bytes_read += len(data)
                    from_address = bytes_read - len(data) + read_address
                    to_address = bytes_read + read_address - 1
                    print_progress_bar(
                        bytes_read / BUFFER_SIZE,
                        total_iterations,
                        prefix=f"Address: 0x{from_address:04X} - 0x{to_address:04X}",
                        suffix=f"- {time.time() - start_time:.2f}s",
                    )

                    ser.write("OK".encode("ascii"))
                    ser.flush()
                elif resp == "OK":
                    break
                elif resp == "ERROR":
                    # print(f"\nError: {info}")
                    return 1
                # else:
                #     print(f"\nUnexpected response: {info}")
                #     return 1

            print(f"\nRead complete in: {time.time() - start_time:.2f} seconds")
            print(f"Data saved to {output_file}")
    except Exception as e:
        print(f"Error while reading: {e}")
    finally:
        consume_response(ser)
        ser.close()
    return 0


def write(
    eprom_name,
    input_file,
    address=None,
    ignore_blank_check=False,
    force=False,
    vpe_as_vpp=False,
):
    """
    Writes data to an EPROM.

    Args:
        eprom_name (str): Name of the EPROM.
        input_file (str): File containing the data to write.
        address (str): Starting address for writing (optional).
        ignore_blank_check (bool): If True, skip blank check and erase steps.
        force (bool): Force writing even if chip ID mismatches.
    """
    start_time = time.time()
    eprom = get_eprom(eprom_name)
    if not eprom:
        print(f"EPROM {eprom_name} not found.")
        return 1
    if not os.path.exists(input_file):
        print(f"Input file {input_file} not found.")
        return 1
    write_address = 0
    eprom["state"] = STATE_WRITE
    if address:
        write_address = int(address, 16) if "0x" in address else int(address)
        eprom["address"] = write_address

    if ignore_blank_check:
        set_eprom_flag(eprom, FLAG_SKIP_ERASE)
        set_eprom_flag(eprom, FLAG_SKIP_BLANK_CHECK)
    if force:
        set_eprom_flag(eprom, FLAG_FORCE)
    if vpe_as_vpp:
        set_eprom_flag(eprom, FLAG_VPE_AS_VPP)
    ser = find_programmer(eprom)
    if not ser:
        return 1

    try:
        with open(input_file, "rb") as file:
            mem_size = eprom["memory-size"]
            file_size = os.path.getsize(input_file)
            total_iterations = file_size / BUFFER_SIZE

            if file_size != mem_size:
                print("Warning: File size does not match memory size.")
            bytes_written = 0

            print(f"Writing {input_file} to {eprom_name}")
            print_progress_bar(0, total_iterations, prefix=f"Address:        -       ")

            while True:
                data = file.read(BUFFER_SIZE)
                if not data:
                    print("\nEnd of file reached")
                    ser.write(int(0).to_bytes(2, byteorder="big"))
                    ser.flush()
                    resp, info = wait_for_response(ser, timeout=10)
                    while resp != "OK":
                        if resp == "ERROR":
                            return 1
                        resp, info = wait_for_response(ser, timeout=10)
                    break

                ser.write(len(data).to_bytes(2, byteorder="big"))
                ser.flush()
                resp, info = wait_for_response(ser)
                while resp != "OK":
                    if resp == "ERROR":
                        return 1
                    resp, info = wait_for_response(ser)

                nr_bytes = ser.write(data)
                bytes_written += nr_bytes
                ser.flush()
                # print(f"Bytes written: {bytes_written}")
                resp, info = wait_for_response(ser)
                while resp != "OK":
                    if resp == "ERROR":
                        return 1
                    elif resp == "WARN":
                        print(f"Warning: {info}")
                    resp, info = wait_for_response(ser)

                from_address = bytes_written - nr_bytes + write_address
                to_address = bytes_written + write_address -1
                print_progress_bar(
                    bytes_written / BUFFER_SIZE,
                    total_iterations,
                    prefix=f"Address: 0x{from_address:04X} - 0x{to_address:04X}",
                    suffix=f"- {time.time() - start_time:.2f}s",
                )
                if write_address + bytes_written == mem_size:
                    break

            print(f"\nWrite complete in: {time.time() - start_time:.2f} seconds")
    except Exception as e:
        print(f"Error while writing: {e}")
        return 1
    finally:
        consume_response(ser)
        ser.close()
    return 0

def verify(eprom_name, input_file, address=None):
    start_time = time.time()
    eprom = get_eprom(eprom_name)
    if not eprom:
        print(f"EPROM {eprom_name} not found.")
        return 1
    if not os.path.exists(input_file):
        print(f"Input file {input_file} not found.")
        return 1
    verify_address = 0
    eprom["state"] = STATE_VERIFY
    if address:
        verify_address = int(address, 16) if "0x" in address else int(address)
        eprom["address"] = verify_address

    ser = find_programmer(eprom)
    if not ser:
        return 1

    try:
        with open(input_file, "rb") as file:
            mem_size = eprom["memory-size"]
            file_size = os.path.getsize(input_file)
            total_iterations = file_size / BUFFER_SIZE

            if file_size != mem_size:
                print("Warning: File size does not match memory size.")
            bytes_written = 0

            print(f"Verifying {input_file} to {eprom_name}")
            print_progress_bar(0, total_iterations, prefix=f"Address:        -       ")

            while True:
                data = file.read(BUFFER_SIZE)
                if not data:
                    print("\nEnd of file reached")
                    ser.write(int(0).to_bytes(2, byteorder="big"))
                    ser.flush()
                    resp, info = wait_for_response(ser, timeout=10)
                    while resp != "OK":
                        if resp == "ERROR":
                            return 1
                        resp, info = wait_for_response(ser, timeout=10)
                    break

                ser.write(len(data).to_bytes(2, byteorder="big"))
                ser.flush()
                resp, info = wait_for_response(ser)
                while resp != "OK":
                    if resp == "ERROR":
                        return 1
                    resp, info = wait_for_response(ser)

                nr_bytes = ser.write(data)
                bytes_written += nr_bytes
                ser.flush()
                resp, info = wait_for_response(ser)
                while resp != "OK":
                    if resp == "ERROR":
                        return 1
                    elif resp == "WARN":
                        print(f"Warning: {info}")
                    resp, info = wait_for_response(ser)

                from_address = bytes_written - nr_bytes + verify_address
                to_address = bytes_written + verify_address - 1
                print_progress_bar(
                    bytes_written / BUFFER_SIZE,
                    total_iterations,
                    prefix=f"Address: 0x{from_address:04X} - 0x{to_address:04X}",
                    suffix=f"- {time.time() - start_time:.2f}s",
                )
                if verify_address + bytes_written == mem_size:
                    break

            print(f"\nVerify complete in: {time.time() - start_time:.2f} seconds")
    except Exception as e:
        print(f"Error while verifying: {e}")
        return 1
    finally:
        consume_response(ser)
        ser.close()
    return 0

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
    try:
        ser = find_programmer(eprom)
        if not ser:
            return 1

        resp, info = wait_for_response(ser)
        if resp == "OK":
            print(f"EPROM {eprom_name} erased successfully.")
            return 0
        elif resp == "ERROR":
            print(f"Error erasing EPROM {eprom_name}")
            return 1
    finally:
        consume_response(ser)
        ser.close()


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
    try:
        resp, info = wait_for_response(ser)
        if resp == "OK":
            print(f"Chip ID check passed for {eprom_name}: {info}")
            return 0
        chip_id = extract_hex_to_decimal(info)
        if not chip_id:
            print(f"Failed to extract chip ID.")
        else:
            eproms = search_chip_id(chip_id)
            if not eproms:
                print(f"Chip ID 0x{chip_id:x} not found in the database.")
            else:
                print(f"Chip ID 0x{chip_id:x} found in the database:")
            for eprom in eproms:
                print(eprom)
        return 1
    finally:
        consume_response(ser)
        ser.close()


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
    try:
        ser = find_programmer(eprom)
        if not ser:
            return 1

        resp, info = wait_for_response(ser, timeout=10)
        if resp == "OK":
            print(f"EPROM {eprom_name} is blank.")
        elif resp == "ERROR":
            print(f"Blank check failed for {eprom_name}")
    finally:
        consume_response(ser)
        ser.close()


def set_eprom_flag(eprom, flag):
    eprom["flags"] |= flag


def get_eprom(eprom_name):
    eprom = db_get_eprom(eprom_name)
    if not eprom:
        return None

    eprom["flags"] = 0
    if "can-erase" in eprom:
        ce = eprom.pop("can-erase")
        if ce:
            set_eprom_flag(eprom, FLAG_CAN_ERASE)
    return eprom
