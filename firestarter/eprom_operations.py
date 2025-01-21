"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

EPROM Operations Module
"""

import os
import time
try:
    from .constants import *
    from .serial_comm import find_programmer, wait_for_response, clean_up
    from .database import get_eprom as db_get_eprom
    from .database import search_chip_id
    from .utils import extract_hex_to_decimal, print_progress_bar
except ImportError:
    from constants import *
    from serial_comm import find_programmer, wait_for_response, clean_up
    from database import get_eprom as db_get_eprom
    from database import search_chip_id
    from utils import extract_hex_to_decimal, print_progress_bar



def read(eprom_name, output_file=None, flags=0, address=None, size=None):
    eprom_name = eprom_name.upper()
    start_time = time.time()
    eprom = setup_read(eprom_name, flags, address, size)
    if not eprom:
        return 1

    ser = find_programmer(eprom)
    if not ser:
        return 1

    if not output_file:
        output_file = f"{eprom_name.upper()}.bin"
    print(f"Reading EPROM {eprom_name}, saving to {output_file}")

    try:
        with open(output_file, "wb") as file:
            read_address = eprom["address"] if "address" in eprom else 0
            bytes_read = 0
            mem_size = eprom["memory-size"]
            total_iterations = (mem_size - read_address) / BUFFER_SIZE
            print_progress_bar(0, total_iterations, prefix=f"Address:        -       ")

            ser.write("OK".encode("ascii"))
            ser.flush()

            while True:
                data = read_data(ser)
                if not data:
                    break
                file.write(data)
                bytes_read += len(data)
                from_address = bytes_read - len(data) + read_address
                to_address = bytes_read + read_address
                print_progress_bar(
                    bytes_read / BUFFER_SIZE,
                    total_iterations,
                    prefix=f"Address: 0x{from_address:04X} - 0x{to_address:04X}",
                    suffix=f"- {time.time() - start_time:.2f}s",
                )

            print(f"\nRead complete in: {time.time() - start_time:.2f} seconds")
            print(f"Data saved to {output_file}")
    except Exception as e:
        print(f"Error while reading: {e}")
        return 1
    finally:
        clean_up(ser)
    return 0


def setup_read(eprom_name, flags=0, address=None, size=None):
    eprom = get_eprom(eprom_name)
    if not eprom:
        print(f"EPROM {eprom_name} not found.")
        return None

    eprom["state"] = STATE_READ

    read_address = 0
    if address:
        read_address = int(address, 16) if "0x" in address else int(address)
        eprom["address"] = read_address

    if size:
        eprom["memory-size"] = (
            int(size, 16) if "0x" in size else int(size)
        ) + read_address

    eprom["flags"] |= flags
    return eprom


def read_data(ser):
    resp, info = wait_for_response(ser)
    if resp == "DATA":
        data = ser.read(BUFFER_SIZE)

        ser.write("OK".encode("ascii"))
        ser.flush()
        return data
    elif resp == "OK":
        return None
    elif resp == "ERROR":
        raise Exception(info)


def write(
    eprom_name,
    input_file,
    address=None,
    flags=0,
):
    eprom_name = eprom_name.upper()
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

    eprom["flags"] |= flags

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
                to_address = bytes_written + write_address - 1
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
        clean_up(ser)
    return 0


def verify(eprom_name, input_file, address=None, flags=0):
    eprom_name = eprom_name.upper()
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
    eprom["flags"] |= flags

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
        clean_up(ser)
    return 0


def erase(eprom_name, flags=0):
    """
    Erases an EPROM.

    Args:
        eprom_name (str): Name of the EPROM.
    """
    eprom_name = eprom_name.upper()
    eprom = get_eprom(eprom_name)
    if not eprom:
        print(f"EPROM {eprom_name} not found.")
        return 1

    eprom["state"] = STATE_ERASE
    eprom["flags"] |= flags

    try:
        ser = find_programmer(eprom)
        if not ser:
            return 1

        ser.write("OK".encode("ascii"))
        ser.flush()

        resp, info = wait_for_response(ser)
        if resp == "OK":
            print(f"EPROM {eprom_name} erased successfully.")
            return 0
        elif resp == "ERROR":
            print(f"Error erasing EPROM {eprom_name}")
            return 1
    finally:
        clean_up(ser)
    return 1


def check_chip_id(eprom_name, flags=0):
    """
    Checks the chip ID of an EPROM.

    Args:
        eprom_name (str): Name of the EPROM.
    """
    eprom_name = eprom_name.upper()
    eprom = get_eprom(eprom_name)
    if not eprom:
        print(f"EPROM {eprom_name} not found.")
        return 1

    eprom["state"] = STATE_CHECK_CHIP_ID
    eprom["flags"] |= flags

    ser = find_programmer(eprom)
    if not ser:
        return 1

    try:
        ser.write("OK".encode("ascii"))
        ser.flush()

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
        clean_up(ser)


def blank_check(eprom_name, flags=0):
    """
    Performs a blank check on an EPROM.

    Args:
        eprom_name (str): Name of the EPROM.
    """
    eprom_name = eprom_name.upper()
    eprom = get_eprom(eprom_name)

    if not eprom:
        print(f"EPROM {eprom_name} not found.")
        return 1

    eprom["state"] = STATE_CHECK_BLANK
    eprom["flags"] |= flags

    try:
        ser = find_programmer(eprom)
        if not ser:
            return 1

        ser.write("OK".encode("ascii"))
        ser.flush()

        resp, info = wait_for_response(ser, timeout=10)
        if resp == "OK":
            print(f"EPROM {eprom_name} is blank.")
            return 0
        elif resp == "ERROR":
            print(f"Blank check failed for {eprom_name}")
    finally:
        clean_up(ser)
    return 1


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


def dev_read(eprom_name, address=None, size="256", force=False):
    eprom_name = eprom_name.upper()
    if not size:
        size = "256"
    eprom = setup_read(eprom_name, force, address, size)
    if not eprom:
        return 1

    ser = find_programmer(eprom)
    if not ser:
        return 1

    try:
        read_address = eprom["address"] if "address" in eprom else 0
        bytes_read = 0

        ser.write("OK".encode("ascii"))
        ser.flush()

        while True:
            data = read_data(ser)
            if not data:
                return 0
            address = bytes_read + read_address
            bytes_read += len(data)
            hexdump(address, data)

    except Exception as e:
        print(f"Error while reading: {e}")
        return 1
    finally:
        clean_up(ser)


def hexdump(address, data, width=16):
    """
    Prints a hexdump similar to xxd.
    :param data: The data to be printed (bytes).
    :param width: Number of bytes per line.
    """
    for i in range(0, len(data), width):
        chunk = data[i : i + width]
        hex_part = " ".join(f"{byte:02x}" for byte in chunk)
        ascii_part = "".join(
            (chr(byte) if 32 <= byte <= 126 else ".") for byte in chunk
        )
        print(f"{address+i:08x}: {hex_part:<{width * 3}} {ascii_part}")


def build_flags(ignore_blank_check=False, force=False, vpe_as_vpp=False):
    flags = 0
    if ignore_blank_check:
        flags |= FLAG_SKIP_ERASE
        flags |= FLAG_SKIP_BLANK_CHECK
    if force:
        flags |= FLAG_FORCE
    if vpe_as_vpp:
        flags |= FLAG_VPE_AS_VPP
    return flags
