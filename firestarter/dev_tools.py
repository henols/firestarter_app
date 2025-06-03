"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

EPROM Operations Module
"""

import logging

from firestarter.constants import *
from firestarter.serial_comm import find_programmer, write_ok, write_data, clean_up
from firestarter.eprom_operations import setup_command, get_eprom

logger = logging.getLogger("Dev")

def dev_registers(msb, lsb, ctrl_reg, firestarter=False, flags=0):
    msb = int(msb, 16) if "0x" in msb else int(msb)
    lsb = int(lsb, 16) if "0x" in lsb else int(lsb)
    ctrl_reg = int(ctrl_reg, 16) if "0x" in ctrl_reg else int(ctrl_reg)
    if msb < 0 or msb > 0xFF:
        logger.error(f"Invalid MSB value: 0x{msb:02x} {msb}")
        return 1
    if lsb < 0 or lsb > 0xFF:
        logger.error(f"Invalid LSB value: 0x{lsb:02x} {lsb}")
        return 1
    if (
        ctrl_reg < 0
        or (ctrl_reg > 0x1FF and firestarter)
        or (ctrl_reg > 0xFF and not firestarter)
    ):
        logger.error(f"Invalid Control Register value: 0x{ctrl_reg:02x} {ctrl_reg}")
        return 1

    data = {"cmd": STATE_DEV_REGISTERS, "flags": flags}
    connection, msg = find_programmer(data)
    if not connection:
        return 1

    logger.info(
        f"Setting registers: MSB: 0x{msb:02x}, LSB: 0x{lsb:02x}, CTRL: 0x{ctrl_reg:02x}"
    )
    try:
        write_ok(connection)
        
        write_data(
            connection,
            bytes([msb, lsb, (0x80 if firestarter else 0x00) | (ctrl_reg >> 8 & 0x01), ctrl_reg & 0xFF]),
        )

    except Exception as e:
        logger.error(f"Error while reading: {e}")
        return 1
    finally:
        clean_up(connection)

    return 0


def dev_address(eprom_name, address, flags=0):
    eprom, connection, buffer_size = setup_command(
        eprom_name, STATE_DEV_ADDRESS, flags, address
    )

    if not eprom or not connection:
        return 1

    logger.info(f"Setting address to RURP: 0x{eprom['address']:06x}")
    logger.info(f"Using {eprom_name.upper()}'s pin map")
    clean_up(connection)
    return 0
