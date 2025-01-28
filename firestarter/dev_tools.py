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
    from .eprom_operations import get_eprom
    from .database import search_chip_id
    from .utils import extract_hex_to_decimal
except ImportError:
    from constants import *
    from serial_comm import find_programmer, wait_for_response, clean_up
    from eprom_operations import get_eprom
    from database import search_chip_id
    from utils import extract_hex_to_decimal

logger = logging.getLogger("Dev")


def dev_registers(msb, lsb, ctrl_reg, flags=0):
    msb = int(msb, 16) if "0x" in msb else int(msb)
    lsb = int(lsb, 16) if "0x" in lsb else int(lsb)
    ctrl_reg = int(ctrl_reg, 16) if "0x" in ctrl_reg else int(ctrl_reg)
    if msb < 0 or msb > 0xFF :
        logger.error(f"Invalid MSB value: {msb}")
        return 1
    if lsb < 0 or lsb > 0xFF :
        logger.error(f"Invalid LSB value: {lsb}")
        return 1
    if ctrl_reg < 0 or ctrl_reg > 0xFF :
        logger.error(f"Invalid Control Register value: {ctrl_reg}")
        return 1
    eprom={}
    eprom["state"] = STATE_DEV_REGISTERS
    eprom["flags"] = flags
    logger.info(f"Setting registers: MSB: 0x{lsb:02x}, LSB: 0x{msb:02x}, CTRL: 0x{ctrl_reg:02x}")
    ser = find_programmer(eprom)
    if not ser:
        return 1
    try:
        ser.write("OK".encode("ascii"))
        p_b = bytes([msb, lsb, ctrl_reg])
        ser.write(p_b)
        ser.flush()
    except Exception as e:
        logger.error(f"Error while reading: {e}")
        return 1
    finally:
        clean_up(ser)

    return 0


def dev_address(eprom_name, address, flags=0):

    eprom_name = eprom_name.upper()
    eprom = get_eprom(eprom_name)
    if not eprom:
        return 1

    eprom["state"] = STATE_DEV_ADDRESS
    eprom["flags"] |= flags

    eprom["address"] = int(address, 16) if "0x" in address else int(address)

    ser = find_programmer(eprom)
    if not ser:
        return 1

    logger.info(f"Setting address to RURP: 0x{eprom["address"]:06x}")
    logger.info(f"Using {eprom_name}'s pin map")
    # logger.info(f"Setting flags: {flags}")
    try:
        # ser.write("OK".encode("ascii"))
        # ser.flush()
        # except Exception as e:
        # logger.error(f"Error while reading: {e}")
        return 1
    finally:
        clean_up(ser)

    # if read:
    #     logger.warning("Write flag set")
    # else:
    #     logger.warning("Read flag set")
    return 0
