"""
Project Name: Firestarter
Copyright (c) 2025 Henrik Olsson

Permission is hereby granted under MIT license.

Configuration Management Module
"""

import time
import logging

from firestarter.constants import *
from firestarter.serial_comm import (
    find_programmer,
    wait_for_ok,
    wait_for_response,
    write_ok,
    clean_up,
)

logger = logging.getLogger("Hardware")


def hardware():
    """
    Reads the hardware revision of the programmer.

    Returns:
        int: 0 if successful, 1 otherwise.
    """
    logger.info("Reading hardware revision...")
    data = {"cmd": STATE_HW_VERSION}

    connection, msg = find_programmer(data)
    if not connection:
        return 1

    try:
        resp, version = wait_for_ok(connection)
        if resp:
            logger.info(f"Hardware revision: {version}")
        else:
            logger.error(f"Failed to read hardware revision. {resp}: {version}")
            return 1
    finally:
        clean_up(connection)
    return 0


def config(rev=None, r1=None, r2=None):
    data = {"cmd": STATE_CONFIG}
    if not rev == None:
        if rev == -1:
            logger.info("Disabling hardware revision override")
            rev = 0xFF
        data["rev"] = rev
    if r1:
        data["r1"] = r1
    if r2:
        data["r2"] = r2
    logger.info("Reading configuration")

    connection, msg = find_programmer(data)
    if not connection:
        return 1

    try:
        resp, version = wait_for_ok(connection)
        if resp:
            logger.info(f"Config: {version}")
        else:
            return 1
    finally:
        clean_up(connection)
    return 0


def read_vpp(timeout=None):
    """
    Reads the VPP voltage from the programmer.
    """
    return read_voltage(STATE_READ_VPP, timeout)


def read_vpe(timeout=None):
    """
    Reads the VPE voltage from the programmer.
    """
    return read_voltage(STATE_READ_VPE, timeout)


def read_voltage(state, timeout=None):
    type = "VPE"
    if state == STATE_READ_VPP:
        type = "VPP"
    logger.info(f"Reading {type} voltage")

    data = {"cmd": state}
    connection, msg = find_programmer(data)
    if not connection:
        return 1

    try:
        write_ok(connection)

        if timeout:
            start = time.time()

        while (t := wait_for_response(connection))[0] == "DATA":
            if logger.isEnabledFor(logging.DEBUG):
                logger.info(t[1])
            else:
                print(f"\r{t[1]}", end="")

            if timeout and time.time() > start + timeout:
                return 0

            write_ok(connection)

    except Exception as e:
        logger.error(f"Error while reading {type} voltage: {e}")
    finally:
        clean_up(connection)
    return 1
