"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Firmware Management Module
"""

import os
import requests
import logging

try:

    from .constants import *
    from .serial_comm import (
        find_programmer,
        wait_for_response,
        find_comports,
        clean_up,
    )
    from .config import get_config_value, set_config_value
    from .avr_tool import Avrdude, AvrdudeNotFoundError, AvrdudeConfigNotFoundError

except ImportError:
    from constants import *
    from serial_comm import (
        find_programmer,
        wait_for_response,
        find_comports,
        clean_up,
    )
    from config import get_config_value, set_config_value
    from avr_tool import Avrdude, AvrdudeNotFoundError, AvrdudeConfigNotFoundError


logger = logging.getLogger("Firmware")

HOME_PATH = os.path.join(os.path.expanduser("~"), ".firestarter")


def firmware(
    install, avrdude_path=None, avrdude_config_path=None, port=None, board="uno"
):
    """
    Handles firmware-related operations, including version check and installation.

    Args:
        install (bool): If True, installs the latest firmware.
        avrdude_path (str): Path to the avrdude tool (optional).
        port (str): Specific port to use (optional).

    Returns:
        int: 0 if successful, 1 otherwise.
    """
    selected_port, url, board_name = firmware_check(port)
    if not install and not url:
        return 1

    if install:
        if not url:
            version, url = latest_firmware(board)
            logger.info(f"Trying to install firmware version: {version}")
        else:
            board = board_name
        if selected_port:
            port = selected_port
        return install_firmware(
            url,
            avrdude_path=avrdude_path,
            avrdude_config_path=avrdude_config_path,
            port=port,
            board=board,
        )

    return 0


def firmware_check(port=None):
    """
    Checks the firmware version of the connected programmer.

    Args:
        port (str): Specific port to check (optional).

    Returns:
        tuple: (bool: up-to-date, str: port, str: firmware URL)
    """
    logger.info("Reading firmware version...")
    data = {"state": STATE_FW_VERSION}

    ser = find_programmer(data, port)
    if not ser:
        return None, None, None

    try:
        resp, version = wait_for_response(ser)

        if not resp == "OK":
            logging.error(f"Failed to read firmware version. {resp}: {version}")
            return None, None, None

        board = "uno"
        if ":" in version:
            version, board = version.split(":")

        logger.info(f"Current firmware version: {version}, for controller: {board}")
        latest_version, url = latest_firmware(board)

        if compare_versions(version, latest_version):
            logger.info(
                f"You have the latest firmware version: {latest_version}, for controller: {board}"
            )
            return ser.portstr, url, board

        logger.info(
            f"New firmware version available: {latest_version}, for controller: {board}"
        )
        return ser.portstr, url, board
    finally:
        clean_up(ser)


def install_firmware(
    url, avrdude_path=None, avrdude_config_path=None, port=None, board="uno"
):
    """
    Installs the latest firmware on the programmer.

    Args:
        url (str): URL to the firmware binary.
        avrdude_path (str): Path to avrdude tool.
        port (str): Specific port to use (optional).

    Returns:
        int: 0 if successful, 1 otherwise.
    """
    logger.info("Installing firmware...")
    if port:
        ports = [port]
    else:
        ports = find_comports()
        if not ports:
            logger.error("No programmer found.")
            return 1

    partno = "atmega328p"
    programmer_id = "arduino"
    baud_rate = 115200
    if board == "leonardo":
        partno = "atmega32u4"
        programmer_id = "avr109"
        baud_rate = 57600

    logger.info("Downloading firmware...")
    firmware_path = download_firmware(url)
    if not firmware_path:
        logger.error("Error downloading firmware.")
        return 1

    for port in ports:
        try:
            avrdude_path = avrdude_path or get_config_value("avrdude-path")
            avrdude_config_path = avrdude_config_path or get_config_value(
                "avrdude-config-path"
            )
            avrdude = Avrdude(
                partno=partno,
                programmer_id=programmer_id,
                baud_rate=baud_rate,
                port=port,
                avrdude_path=avrdude_path,
                avrdude_config_path=avrdude_config_path,
            )

        except AvrdudeNotFoundError as e:
            logger.error(
                "Error: avrdude not found. Provide the full path with --avrdude-path."
            )
            return 1
        except AvrdudeConfigNotFoundError as e:
            logger.error(
                "Error: avrdude.conf not found. Provide the full path with --avrdude-config-path."
            )
            return 1

        # if not test_avrdude_connection(avrdude):
        #     continue

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Flashing firmware to port: {port}")
        else:    
            logger.info("Flashing firmware...")

        error, return_code = avrdude.flash_firmware(firmware_path)
        if return_code == 0:
            logger.info("Firmware successfully updated.")
            set_config_value("port", port)
            set_config_value("avrdude-path", avrdude.command)
            set_config_value("avrdude-config-path", avrdude.config)
            return 0
        else:
            logger.error(f"Firmware update failed on port: {port}")
            if logger.isEnabledFor(logging.DEBUG):
                logger.error(error)

    logger.error(
        "No compatible programmer found. Please reset the device and try again."
    )
    return 1


def latest_firmware(board="uno"):
    """
    Fetches the latest firmware version and download URL.

    Returns:
        tuple: (str: latest version, str: firmware URL)
    """
    logger.debug("Fetching latest firmware release...")

    response = requests.get(FIRESTARTER_RELEASE_URL)
    if response.status_code != 200:
        logger.error("Failed to fetch latest firmware release.")
        return None, None

    release = response.json()
    version = release["tag_name"]
    url = next(
        (
            asset["browser_download_url"]
            for asset in release["assets"]
            if f"firestarter_{board}.hex" in asset["name"]
        ),
        None,
    )

    if not url:
        logger.error(
            f"Firmware binary not found in the latest release, version: {version}."
        )
        return None, None

    logger.debug(f"Latest firmware version: {version}, URL: {url}")

    return version, url


def compare_versions(current_version, latest_version):
    """
    Compares the current firmware version with the latest version.

    Args:
        current_version (str): Current firmware version.
        latest_version (str): Latest firmware version.

    Returns:
        bool: True if up-to-date, False otherwise.
    """
    current = tuple(map(int, current_version.split(".")))
    latest = tuple(map(int, latest_version.split(".")))

    return current >= latest


def download_firmware(url):
    """
    Downloads firmware from the given URL and saves it locally.

    Args:
        url (str): URL to download the firmware from.

    Returns:
        str: Path to the downloaded firmware file.
    """
    response = requests.get(url)
    if response.status_code != 200:
        return None

    if not os.path.exists(HOME_PATH):
        os.makedirs(HOME_PATH)

    firmware_path = os.path.join(HOME_PATH, "firestarter.hex")
    with open(firmware_path, "wb") as file:
        file.write(response.content)

    return firmware_path


def test_avrdude_connection(avrdude):
    """
    Tests the connection to the programmer using avrdude.

    Args:
        avrdude (Avrdude): Avrdude instance.

    Returns:
        bool: True if successful, False otherwise.
    """
    output, error, returncode = avrdude.testConnection()
    if returncode == 0:
        logger.info("Programmer connected successfully.")
        return True
    else:
        logger.error(f"Failed to connect to programmer. {error.decode('ascii')}")
        return False


if __name__ == "__main__":
    version, url = latest_firmware("leonardo")
    print(f"URL: {url}")
    print(f"Version: {version}")
    resp = download_firmware(url)
    print(f"Response: {resp}")
