"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Firmware Management Module
"""

import os
import requests

try:
    from .serial_comm import find_programmer, wait_for_response
    from .config import get_config_value, set_config_value
    from .avr_tool import Avrdude
except ImportError:
    from serial_comm import find_programmer, wait_for_response
    from config import get_config_value, set_config_value
    from avr_tool import Avrdude

# Constants
FIRESTARTER_RELEASE_URL = (
    "https://api.github.com/repos/henols/firestarter/releases/latest"
)
STATE_FW_VERSION = 13
STATE_HW_VERSION = 15

STATE_CONFIG = 14

HOME_PATH = os.path.join(os.path.expanduser("~"), ".firestarter")


def hardware(verbose=False):
    """
    Reads the hardware revision of the programmer.

    Args:
        verbose (bool): Enables verbose output.

    Returns:
        int: 0 if successful, 1 otherwise.
    """
    print("Reading hardware revision...")
    data = {"state": STATE_HW_VERSION}

    ser = find_programmer(data, verbose=verbose)
    if not ser:
        return 1

    try:
        resp, version = wait_for_response(ser)
        if resp == "OK":
            print(f"Hardware revision: {version}")
        else:
            print(f"Failed to read hardware revision. {resp}: {version}")
            return 1
    finally:
        ser.close()
    return 0


def rurp_config(rev=None, r1=None, r2=None):
    data = {}
    data["state"] = STATE_CONFIG
    if not rev == None:
        if rev == -1:
            print("Disabling hardware revision override")
            rev = 0xFF
        data["rev"] = rev
    if r1:
        data["r1"] = r1
    if r2:
        data["r2"] = r2
    print("Reading configuration")

    ser = find_programmer(data)
    if not ser:
        return 1
    ser.write("OK".encode("ascii"))
    r, version = wait_for_response(ser)
    if r == "OK":
        print(f"Config: {version}")
    else:
        print(r)
        return 1
    return 0


def firmware(install, avrdude_path, port, verbose=False):
    """
    Handles firmware-related operations, including version check and installation.

    Args:
        install (bool): If True, installs the latest firmware.
        avrdude_path (str): Path to the avrdude tool (optional).
        port (str): Specific port to use (optional).
        verbose (bool): Enables verbose output.

    Returns:
        int: 0 if successful, 1 otherwise.
    """
    latest, selected_port, url = firmware_check(port, verbose=verbose)
    if not latest and not install and not url:
        return 1

    if install:
        if not url:
            version, url = latest_firmware(verbose=verbose)
            print(f"Trying to install firmware version: {version}")
        if not selected_port:
            selected_port = port
        return install_firmware(url, avrdude_path, selected_port, verbose=verbose)

    return 0


def firmware_check(port=None, verbose=False):
    """
    Checks the firmware version of the connected programmer.

    Args:
        port (str): Specific port to check (optional).
        verbose (bool): Enables verbose output.

    Returns:
        tuple: (bool: up-to-date, str: port, str: firmware URL)
    """
    print("Reading firmware version...")
    data = {"state": STATE_FW_VERSION}

    ser = find_programmer(data, port, verbose=verbose)
    if not ser:
        return False, None, None

    try:
        ser.write("OK".encode("ascii"))
        resp, version = wait_for_response(ser)
        if resp != "OK":
            print(f"Failed to read firmware version. {resp}: {version}")
            return False, None, None

        print(f"Current firmware version: {version}")
        latest_version, url = latest_firmware(verbose=verbose)

        if compare_versions(version, latest_version):
            print(f"You have the latest firmware version: {latest_version}")
            return True, None, None

        print(f"New firmware version available: {latest_version}")
        return False, ser.portstr, url
    finally:
        ser.close()


def install_firmware(url, avrdude_path, port=None, verbose=False):
    """
    Installs the latest firmware on the programmer.

    Args:
        url (str): URL to the firmware binary.
        avrdude_path (str): Path to avrdude tool.
        port (str): Specific port to use (optional).
        verbose (bool): Enables verbose output.

    Returns:
        int: 0 if successful, 1 otherwise.
    """
    print("Installing firmware...")
    if port:
        ports = [port]
    else:
        ports = find_programmer(data={"state": STATE_FW_VERSION})
        if not ports:
            print("No Arduino found.")
            return 1

    for port in ports:
        try:
            avrdude_path = avrdude_path or get_config_value("avrdude-path")
            avrdude = Avrdude(
                partno="ATmega328P",
                programmer_id="arduino",
                baud_rate="115200",
                port=port,
                avrdudePath=avrdude_path,
            )
        except FileNotFoundError:
            print(
                "Error: avrdude not found. Provide the full path with --avrdude-path."
            )
            return 1

        if not test_avrdude_connection(avrdude):
            continue

        print("Downloading firmware...")
        firmware_path = download_firmware(url)
        if not firmware_path:
            print("Error downloading firmware.")
            return 1

        print("Flashing firmware...")
        if avrdude.flashFirmware(firmware_path) == 0:
            print("Firmware successfully updated.")
            set_config_value("port", port)
            set_config_value("avrdude-path", avrdude_path)
            return 0
        else:
            print("Firmware update failed.")

    print("No compatible programmer found. Please reset the device and try again.")
    return 1


def latest_firmware(verbose=False):
    """
    Fetches the latest firmware version and download URL.

    Args:
        verbose (bool): Enables verbose output.

    Returns:
        tuple: (str: latest version, str: firmware URL)
    """
    if verbose:
        print("Fetching latest firmware release...")

    response = requests.get(FIRESTARTER_RELEASE_URL)
    if response.status_code != 200:
        print("Failed to fetch latest firmware release.")
        return None, None

    release = response.json()
    version = release["tag_name"]
    url = next(
        (
            asset["browser_download_url"]
            for asset in release["assets"]
            if "firmware.hex" in asset["name"]
        ),
        None,
    )

    if not url:
        print("Firmware binary not found in the latest release.")
        return None, None

    if verbose:
        print(f"Latest firmware version: {version}, URL: {url}")

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

    firmware_path = os.path.join(HOME_PATH, "firmware.hex")
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
        print("Programmer connected successfully.")
        return True
    else:
        print("Failed to connect to programmer.")
        print(error.decode("ascii"))
        return False
