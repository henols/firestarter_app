"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

EPROM Operations Module
"""

import os
import time
import logging
import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

try:
    from .constants import *
    from .serial_comm import find_programmer, wait_for_response, clean_up
    from .database import get_eprom as db_get_eprom
    from .database import search_chip_id
    from .utils import extract_hex_to_decimal
    from .eprom_info import format_eproms
except ImportError:
    from constants import *
    from serial_comm import find_programmer, wait_for_response, clean_up
    from database import get_eprom as db_get_eprom
    from database import search_chip_id
    from utils import extract_hex_to_decimal
    from eprom_info import format_eproms

logger = logging.getLogger("EPROM")

bar_format = "{l_bar}{bar}| {n:#06x}/{total:#06x} bytes "


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
    logger.info(f"Reading EPROM {eprom_name}, saving to {output_file}")

    try:
        with open(output_file, "wb") as file:
            mem_size = eprom["memory-size"]
            read_address = eprom["address"] if "address" in eprom else 0
            bytes_read = 0

            write_ok(ser)

            with logging_redirect_tqdm(), tqdm.tqdm(
                total=mem_size - read_address,
                bar_format=bar_format,
            ) as pbar:
                while True:
                    data = read_data(ser)
                    if not data:
                        break
                    file.write(data)
                    bytes_read += len(data)
                    pbar.update(len(data))
            logger.info(f"Read complete in: {time.time() - start_time:.2f} seconds")
            logger.info(f"Data saved to {output_file}")
    except Exception as e:
        logger.error(f"Error while reading: {e}")
        return 1
    finally:
        clean_up(ser)
    return 0




def write(eprom_name, input_file, address=None, flags=0):
    eprom_name = eprom_name.upper()
    start_time = time.time()
    eprom = get_eprom(eprom_name)
    if not eprom:
        return 1

    eprom["state"] = STATE_WRITE
    eprom["flags"] |= flags

    if address:
        eprom["address"] = int(address, 16) if "0x" in address else int(address)

    logger.info(f"Writing {input_file} to {eprom_name}")
    if send_file(eprom, input_file):
        return 1
    logger.info(f"Write complete in: {time.time() - start_time:.2f} seconds")
    return 0


def verify(eprom_name, input_file, address=None, flags=0):
    eprom_name = eprom_name.upper()
    start_time = time.time()
    eprom = get_eprom(eprom_name)
    if not eprom:
        return 1

    eprom["state"] = STATE_VERIFY
    eprom["flags"] |= flags

    if address:
        eprom["address"] = int(address, 16) if "0x" in address else int(address)

    logger.info(f"Verifying {input_file} to {eprom_name}")
    if send_file(eprom, input_file):
        return 1

    logger.info(f"Verify complete in: {time.time() - start_time:.2f} seconds")
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
        return 1

    eprom["state"] = STATE_ERASE
    eprom["flags"] |= flags

    try:
        ser = find_programmer(eprom)
        if not ser:
            return 1

        write_ok(ser)

        resp, info = wait_for_response(ser)
        if resp == "OK":
            logger.info(f"EPROM {eprom_name} erased successfully.")
            return 0
        elif resp == "ERROR":
            logger.error(f"Failed to erase EPROM {eprom_name}")
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
        return 1

    eprom["state"] = STATE_CHECK_CHIP_ID
    eprom["flags"] |= flags

    ser = find_programmer(eprom)
    if not ser:
        return 1

    try:
        write_ok(ser)
        while True:
            resp, info = wait_for_response(ser)
            if resp == "OK":
                logger.info(f"Chip ID check passed for {eprom_name}: {info}")
                return 0
            elif resp == "WARN":
                continue
            chip_id = extract_hex_to_decimal(info)
            if not chip_id:
                logger.error("Failed to extract chip ID.")
            else:
                eproms = search_chip_id(chip_id)
                if not eproms:
                    logger.warning(f"Chip ID 0x{chip_id:x} not found in the database.")
                else:
                    logger.info(f"Chip ID 0x{chip_id:x} found in the database:")
                    format_eproms(eproms)
            return 0 if flags & FLAG_FORCE == FLAG_FORCE else 1
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
        return 1

    eprom["state"] = STATE_CHECK_BLANK
    eprom["flags"] |= flags

    try:
        ser = find_programmer(eprom)
        if not ser:
            return 1

        write_ok(ser)
        while True:
            resp, info = wait_for_response(ser, timeout=10)
            if resp == "OK":
                logger.info(f"EPROM {eprom_name} is blank.")
                break
            elif resp == "ERROR":
                logger.error(f"Blank check failed for {eprom_name}")
                return 1
    finally:
        clean_up(ser)
    return 0


def dev_read(eprom_name, address=None, size="256", flags=0):
    eprom_name = eprom_name.upper()
    if not size:
        size = "256"

    eprom = setup_read(eprom_name, flags, address, size)
    if not eprom:
        return 1

    ser = find_programmer(eprom)
    if not ser:
        return 1

    try:
        read_address = eprom["address"] if "address" in eprom else 0
        bytes_read = 0

        write_ok(ser)

        while True:
            data = read_data(ser)
            if not data:
                return 0
            address = bytes_read + read_address
            bytes_read += len(data)
            hexdump(address, data)

    except Exception as e:
        logger.error(f"Error while reading: {e}")
        return 1
    finally:
        clean_up(ser)


def set_eprom_flag(eprom, flag):
    eprom["flags"] |= flag


def get_eprom(eprom_name):
    eprom = db_get_eprom(eprom_name)
    if not eprom:
        logger.error(f"EPROM {eprom_name} not found.")
        return None

    eprom["flags"] = 0
    if "can-erase" in eprom:
        ce = eprom.pop("can-erase")
        if ce:
            set_eprom_flag(eprom, FLAG_CAN_ERASE)
    return eprom

def setup_read(eprom_name, flags=0, address=None, size=None):
    eprom = get_eprom(eprom_name)
    if not eprom:
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
    while True:
        resp, info = wait_for_response(ser)
        if resp == "DATA":
            data = ser.read(BUFFER_SIZE)

            write_ok(ser)

            return data
        elif resp == "OK":
            return None
        elif resp == "ERROR":
            raise Exception(info)


def send_file(eprom, input_file):
    if not os.path.exists(input_file):
        logger.error(f"Input file {input_file} not found.")
        return 1

    ser = find_programmer(eprom)
    if not ser:
        return 1

    mem_size = eprom["memory-size"]
    address = eprom["address"] if "address" in eprom else 0
    try:
        with open(input_file, "rb") as file:

            file_size = os.path.getsize(input_file)

            if file_size != mem_size:
                logger.warning("File size does not match memory size.")
            bytes_written = 0

            with logging_redirect_tqdm(), tqdm.tqdm(
                total=file_size, bar_format=bar_format
            ) as pbar:
                while True:
                    if address + bytes_written == mem_size:
                        break
                    data = file.read(BUFFER_SIZE)
                    if not data:
                        logger.info("End of file reached")
                        write_data(ser, int(0).to_bytes(2, byteorder="big"))
                        
                        resp, info = wait_for_response(ser, timeout=10)
                        while resp != "OK":
                            if resp == "ERROR":
                                return 1
                            resp, info = wait_for_response(ser, timeout=10)
                        break

                    write_data(ser, len(data).to_bytes(2, byteorder="big"))
                    
                    resp, info = wait_for_response(ser)
                    while resp != "OK":
                        if resp == "ERROR":
                            return 1
                        resp, info = wait_for_response(ser)

                    nr_bytes = write_data(ser, data)
                    
                    bytes_written += nr_bytes
                    resp, info = wait_for_response(ser)
                    while resp != "OK":
                        if resp == "ERROR":
                            return 1
                        elif resp == "WARN":
                            logger.warning(info)
                        resp, info = wait_for_response(ser)

                    pbar.update(nr_bytes)
    except Exception as e:
        logger.error(f"Error while sending data: {e}")
        return 1
    finally:
        clean_up(ser)
    return 0


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
        logger.info(f"{address+i:08x}: {hex_part:<{width * 3}} {ascii_part}")


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

def write_ok(ser):
    # time.sleep(0.1)
    ser.write("OK".encode("ascii"))
    ser.flush()

def write_data(ser, data):
    len = ser.write(data)
    ser.flush()
    return len
