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
    from .serial_comm import (
        find_programmer,
        wait_for_response,
        wait_for_ok,
        write_data,
        write_ok,
        clean_up,
    )
    from .database import get_eprom as db_get_eprom
    from .database import search_chip_id
    from .utils import extract_hex_to_decimal
    from .eprom_info import format_eproms
except ImportError:
    from constants import *
    from serial_comm import (
        find_programmer,
        wait_for_response,
        wait_for_ok,
        write_data,
        write_ok,
        clean_up,
    )
    from database import get_eprom as db_get_eprom
    from database import search_chip_id
    from utils import extract_hex_to_decimal
    from eprom_info import format_eproms

logger = logging.getLogger("EPROM")

bar_format = "{l_bar}{bar}| {n:#06x}/{total:#06x} bytes "


def read(eprom_name, output_file=None, flags=0, address=None, size=None):

    eprom, connection, buffer_size = setup_read(eprom_name, flags, address, size)
    if not eprom or not connection:
        return 1

    if not output_file:
        output_file = f"{eprom_name.upper()}.bin"

    logger.info(f"Reading EPROM {eprom_name.upper()}, saving to {output_file}")
    start_time = time.time()

    try:
        with open(output_file, "wb") as file:
            mem_size = eprom["memory-size"]
            read_address = eprom["address"] if "address" in eprom else 0
            bytes_read = 0

            write_ok(connection)

            with logging_redirect_tqdm(), tqdm.tqdm(
                total=mem_size - read_address,
                bar_format=bar_format,
            ) as pbar:
                while True:
                    data = read_data(connection, buffer_size)
                    if not data:
                        break
                    file.write(data)
                    bytes_read += len(data)
                    pbar.update(len(data))
    except Exception as e:
        logger.error(f"Error while reading: {e}")
        return 1
    finally:
        clean_up(connection)

    logger.info(f"Read complete ({time.time() - start_time:.2f}s)")
    logger.info(f"Data saved to {output_file}")
    return 0


def write(eprom_name, input_file, flags=0, address=None):
    eprom, ser, buffer_size = setup_command(eprom_name, STATE_WRITE, flags, address)
    if not eprom or not ser:
        return 1

    logger.info(f"Writing {input_file} to {eprom_name.upper()}")

    start_time = time.time()
    if not send_file(eprom, input_file, ser, buffer_size):
        return 1

    logger.info(f"Write complete ({time.time() - start_time:.2f}s)")
    return 0


def verify(eprom_name, input_file, flags=0, address=None):

    eprom, ser, buffer_size = setup_command(eprom_name, STATE_VERIFY, flags, address)
    if not eprom or not ser:
        return 1

    start_time = time.time()
    logger.info(f"Verifying {input_file} to {eprom_name.upper()}")
    if not send_file(eprom, input_file, ser, buffer_size):
        return 1

    logger.info(f"Verify complete ({time.time() - start_time:.2f}s)")
    return 0


def erase(eprom_name, flags=0):
    """
    Erases an EPROM.

    Args:
        eprom_name (str): Name of the EPROM.
    """
    eprom, connection, buffer_size = setup_command(eprom_name, STATE_ERASE, flags)
    if not eprom or not connection:
        return 1
    logger.info(f"Erasing EPROM {eprom_name.upper()}")
    start_time = time.time()
    try:
        write_ok(connection)
        resp, msg = wait_for_ok(connection)
        if not resp:
            logger.error(f"Failed to erase EPROM {eprom_name}")
            return 1
    finally:
        clean_up(connection)

    logger.info(f"Erase complete ({time.time() - start_time:.2f}s)")
    return 0


def check_chip_id(eprom_name, flags=0):
    """
    Checks the chip ID of an EPROM.

    Args:
        eprom_name (str): Name of the EPROM.
    """
    eprom, connection, buffer_size = setup_command(eprom_name, STATE_CHECK_CHIP_ID, flags)
    if not eprom or not connection:
        return 1
    logger.info(f"Checking chip ID for {eprom_name.upper()}")
    start_time = time.time()
    try:
        write_ok(connection)
        resp, info = wait_for_ok(connection)
        if not resp:
            chip_id = extract_hex_to_decimal(info)
            if not chip_id:
                logger.error("Failed to extract chip ID.")
                return 1
            else:
                eproms = search_chip_id(chip_id)
                if not eproms:
                    logger.warning(f"Chip ID 0x{chip_id:x} not found in the database.")
                else:
                    logger.info(f"Chip ID 0x{chip_id:x} found in the database:")
                    format_eproms(eproms)
            return 0 if flags & FLAG_FORCE == FLAG_FORCE else 1
    finally:
        clean_up(connection)

    logger.info(
        f"Chip ID check passed for {eprom_name.upper()}: {info} ({time.time() - start_time:.2f}s)"
    )
    return 0


def blank_check(eprom_name, flags=0):
    """
    Performs a blank check on an EPROM.

    Args:
        eprom_name (str): Name of the EPROM.
    """
    eprom, connection, buffer_size = setup_command(eprom_name, STATE_CHECK_BLANK, flags)

    if not eprom or not connection:
        return 1
    logger.info(f"Performing blank check for {eprom_name.upper()}")
    start_time = time.time()
    try:
        write_ok(connection)
        resp, msg = wait_for_ok(connection)
        if not resp:
            logger.error(f"Blank check failed for {eprom_name.upper()}")
            return 1
    finally:
        clean_up(connection)

    logger.info(f"Blank check complete ({time.time() - start_time:.2f}s)")
    return 0


def dev_read(eprom_name, address=None, size="256", flags=0):
    if not size:
        size = "256"
    eprom, connection, buffer_size = setup_read(eprom_name, flags, address, size)
    if not eprom or not connection:
        return 1

    read_address = eprom["address"] if "address" in eprom else 0
    logger.info(
        f"Reading {size} bytes at address 0x{read_address:04x} from EPROM {eprom_name.upper()}"
    )
    start_time = time.time()
    try:
        bytes_read = 0

        write_ok(connection)

        while True:
            data = read_data(connection, buffer_size)
            if not data:
                break
            address = bytes_read + read_address
            bytes_read += len(data)
            hexdump(address, data)

    except Exception as e:
        logger.error(f"Error while reading: {e}")
        return 1
    finally:
        clean_up(connection)
    logger.info(f"Read complete ({time.time() - start_time:.2f}s)")
    return 0


def get_eprom(eprom_name):
    logger.debug(f"Getting EPROM {eprom_name}")
    eprom = db_get_eprom(eprom_name)
    if not eprom:
        logger.error(f"EPROM {eprom_name} not found.")
        return None
    logger.debug(f"EPROM {eprom_name} found.")
    eprom["flags"] = 0
    if "can-erase" in eprom:
        ce = eprom.pop("can-erase")
        if ce:
            eprom["flags"] |= FLAG_CAN_ERASE

    return eprom

def calculate_buffer_size(msg):
    return LEONARDO_BUFFER_SIZE if msg and "leonardo" in msg else BUFFER_SIZE

def setup_command(eprom_name, state, flags=0, address=None):
    eprom = get_eprom(eprom_name)
    if not eprom:
        return None, None, None

    eprom["cmd"] = state
    eprom["flags"] |= flags

    if address:
        eprom["address"] = int(address, 16) if "0x" in address else int(address)
    connection, msg = find_programmer(eprom)
    return eprom, connection, calculate_buffer_size(msg)


def setup_read(eprom_name, flags=0, address=None, size=None):
    start_time = time.time()
    try:
        eprom = get_eprom(eprom_name)
        if not eprom:
            return None, None, None

        eprom["cmd"] = STATE_READ

        read_address = 0
        if address:
            read_address = int(address, 16) if "0x" in address else int(address)
            eprom["address"] = read_address

        if size:
            eprom["memory-size"] = (
                int(size, 16) if "0x" in size else int(size)
            ) + read_address

        eprom["flags"] |= flags
        connection, msg = find_programmer(eprom)
        return eprom, connection, calculate_buffer_size(msg)
    finally:
        if eprom and connection:
            logger.debug(f"Setup complete ({time.time() - start_time:.2f}s)")


def read_data(connection, buffer_size=BUFFER_SIZE):
    while True:
        resp, info = wait_for_response(connection)
        if resp == "DATA":
            data = connection.read(buffer_size)

            write_ok(connection)

            return data
        elif resp == "OK":
            return None
        elif resp == "ERROR":
            raise Exception(info)


def send_file(eprom, input_file, connection, buffer_size=BUFFER_SIZE):
    if not os.path.exists(input_file):
        logger.error(f"Input file {input_file} not found.")
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
                    data = file.read(buffer_size)
                    if not data:
                        logger.info("End of file reached")
                        write_data(connection, int(0).to_bytes(2, byteorder="big"))
                        resp, msg = wait_for_ok(connection)
                        return resp

                    write_data(connection, len(data).to_bytes(2, byteorder="big"))
                    
                    resp, msg = wait_for_ok(connection)
                    if not resp:
                        return False

                    nr_bytes = write_data(connection, data)

                    bytes_written += nr_bytes
            
                    resp, msg = wait_for_ok(connection)
                    if not resp:
                        return False

                    pbar.update(nr_bytes)
    except Exception as e:
        logger.error(f"Error while sending data: {e}")
        raise e
        # return False
    finally:
        clean_up(connection)
    return True


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
