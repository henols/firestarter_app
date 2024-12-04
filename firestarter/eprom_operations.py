"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

EPROM Operations Module
"""

import os
import time

try:
    from .serial_comm import find_programmer, wait_for_response
    from .database import get_eprom
    from .utils import extract_hex_to_decimal, print_progress, print_progress_bar
except ImportError:
    from serial_comm import find_programmer, wait_for_response
    from database import get_eprom
    from utils import extract_hex_to_decimal, print_progress, print_progress_bar

# Constants
BUFFER_SIZE = 512
STATE_READ = 1
STATE_WRITE = 2
STATE_ERASE = 3
STATE_CHECK_BLANK = 4
STATE_CHECK_CHIP_ID = 5


def read(eprom_name, output_file=None, force=False):
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
            total_iterations = mem_size / BUFFER_SIZE
            while True:
                resp, info = wait_for_response(ser)
                if resp == "DATA":
                    data = ser.read(BUFFER_SIZE)
                    file.write(data)
                    bytes_read += len(data)
                    # percent = int(bytes_read / mem_size * 100)
                    # print_progress(percent, bytes_read - BUFFER_SIZE, bytes_read)
                    from_address = bytes_read - len(data)
                    to_address = bytes_read
                    print_progress_bar(
                        bytes_read / BUFFER_SIZE,
                        total_iterations,
                        prefix=f"Address: 0x{from_address:X} - 0x{to_address:X}",
                        suffix=f"- {time.time() - start_time:.2f}s",
                    )
                    ser.write("OK".encode("ascii"))
                    ser.flush()
                elif resp == "OK":
                    break
                elif resp == "ERROR":
                    print(f"\nError: {info}")
                    return 1
                else:
                    print(f"\nUnexpected response: {info}")
                    return 1

            print(f"\nRead complete in: {time.time() - start_time:.2f} seconds")
            print(f"Data saved to {output_file}")
    except Exception as e:
        print(f"Error while reading: {e}")
    finally:
        ser.close()


def write(eprom_name, input_file, address=None, ignore_blank_check=False, force=False):
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
        eprom["skip-erase"] = True
        eprom["blank-check"] = False
    if force:
        eprom["force"] = True

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

            while True:
                data = file.read(BUFFER_SIZE)
                if not data:
                    ser.write(int(0).to_bytes(2, byteorder="big"))
                    ser.flush()
                    resp, info = wait_for_response(ser)
                    print("\nEnd of file reached")
                    print(info)
                    break

                ser.write(len(data).to_bytes(2, byteorder="big"))
                ser.flush()
                resp, info = wait_for_response(ser)
                if resp == "ERROR":
                    print(f"\nError writing: {info}")
                    return 1
                nr_bytes = ser.write(data)
                bytes_written += nr_bytes
                ser.flush()

                while True:
                    resp, info = wait_for_response(ser)
                    if resp == "OK":
                        # percent = int(bytes_written / file_size * 100)
                        # print_progress(
                        #     percent,
                        #     bytes_written - nr_bytes + write_address,
                        #     bytes_written + write_address,
                        # )
                        from_address = bytes_written - nr_bytes + write_address
                        to_address = bytes_written + write_address
                        print_progress_bar(
                            bytes_written / BUFFER_SIZE,
                            total_iterations,
                            prefix=f"Address: 0x{from_address:X} - 0x{to_address:X}",
                            suffix=f"- {time.time() - start_time:.2f}s",
                        )
                        break
                    elif resp == "ERROR":
                        print(f"\nError: {info}")
                        return 1
                    else:
                        print(f"Warning: {info}")
                if bytes_written == mem_size:
                    break

            print(f"\nWrite complete in: {time.time() - start_time:.2f} seconds")
    except Exception as e:
        print(f"Error while writing: {e}")
        return 1
    finally:
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
        print(
            f"Extracted chip ID: {chip_id}" if chip_id else "Failed to extract chip ID."
        )


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
