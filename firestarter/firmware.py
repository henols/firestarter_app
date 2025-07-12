"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Firmware Management Module
"""

import os
import time
import requests
import logging
from typing import Optional, Tuple

# Add this line with the other imports
from rich.prompt import Confirm

from firestarter.constants import *
from firestarter.serial_comm import (
    SerialCommunicator,
    ProgrammerNotFoundError,
    SerialError,
)
from firestarter.config import ConfigManager
from firestarter.avr_tool import (
    Avrdude,
    AvrdudeNotFoundError,
    AvrdudeConfigNotFoundError,
)

logger = logging.getLogger("Firmware")

HOME_PATH = os.path.join(os.path.expanduser("~"), ".firestarter")


class FirmwareOperationError(Exception):
    """Custom exception for firmware operation failures."""

    pass


class FirmwareManager:
    """
    Manages firmware-related operations for the EPROM programmer.
    This includes checking the current firmware version on the device,
    fetching information about the latest available release, comparing versions,
    downloading firmware files, and orchestrating the installation process
    using an Avrdude utility wrapper.
    """

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager

    def check_current_firmware(
        self, preferred_port: str | None = None,
        flags: int = 0,
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Checks the currently installed firmware version on the programmer.
        Returns: (port_name, current_version, board_name) or (None, None, None) on failure.
        """
        logger.info("Reading current firmware version...")
        command_dict = {"state": COMMAND_FW_VERSION}
        if flags:
            command_dict["flags"] = flags
        comm = None
        try:
            comm = SerialCommunicator.find_and_connect(
                command_dict, self.config_manager, preferred_port=preferred_port
            )
            # find_and_connect gets the initial OK from the programmer.
            # The firmware then executes the fw_version command and sends a second OK with the payload.
            is_ok, msg = comm.expect_ack()

            if is_ok and msg and ":" in msg:
                # Expected format is "version:board"
                parts = msg.split(":", 1)
                current_version = parts[0].strip()
                board_name = parts[1].strip()

                logger.info(
                    f"Current firmware version: {current_version}, for controller: {board_name} on port {comm.port_name}"
                )
                return comm.port_name, current_version, board_name
            else:
                logger.error(
                    f"Failed to read firmware version: Invalid response from programmer: '{msg}'"
                )
                return None, None, None
        except (ProgrammerNotFoundError, SerialError) as e:
            logger.error(f"Failed to read firmware version: {e}")
            return None, None, None
        finally:
            if comm:
                comm.disconnect()

    def fetch_latest_release_info(
        self, board: str = "uno"
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Fetches the latest firmware version and download URL for the specified board.
        Returns: (latest_version_str, download_url_str) or (None, None) on failure.
        """
        logger.debug(f"Fetching latest firmware release for board: {board}...")
        try:
            response = requests.get(FIRESTARTER_RELEASE_URL, timeout=10)
            response.raise_for_status()  # Raise an exception for HTTP errors
            release_data = response.json()
            latest_version = release_data.get("tag_name")
            firmware_asset_name = f"firestarter_{board}.hex"
            download_url = None
            for asset in release_data.get("assets", []):
                if asset.get("name") == firmware_asset_name:
                    download_url = asset.get("browser_download_url")
                    break

            if not latest_version or not download_url:
                logger.error(
                    f"Could not find firmware version or URL for board '{board}' in the latest release."
                )
                return None, None

            logger.debug(
                f"Latest firmware version for {board}: {latest_version}, URL: {download_url}"
            )
            return latest_version, download_url
        except requests.RequestException as e:
            logger.error(f"Failed to fetch latest firmware release information: {e}")
            return None, None

    def _compare_versions(
        self, current_version_str: str | None, latest_version_str: str | None
    ) -> bool:
        """Compares two version strings (e.g., "1.2.3"). Returns True if current >= latest."""
        if not current_version_str or not latest_version_str:
            return False  # Cannot compare if one is missing
        try:
            current = tuple(map(int, current_version_str.split(".")))
            latest = tuple(map(int, latest_version_str.split(".")))
            return current >= latest
        except ValueError:
            logger.warning(
                f"Could not parse version strings for comparison: '{current_version_str}', '{latest_version_str}'"
            )
            return False  # Treat as not up-to-date if parsing fails

    def _download_firmware_file(self, url: str) -> Optional[str]:
        """Downloads firmware from the URL and saves it to a temporary local path."""
        logger.info(f"Downloading firmware from {url}...")
        start_time = time.time()
        try:
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()

            if not os.path.exists(HOME_PATH):
                os.makedirs(HOME_PATH)

            # Extract filename from URL or use a default
            filename = (
                url.split("/")[-1]
                if url.split("/")[-1]
                else "firestarter_downloaded.hex"
            )
            firmware_path = os.path.join(HOME_PATH, filename)

            with open(firmware_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info(
                f"Firmware downloaded to: {firmware_path} ({time.time() - start_time:.2f}s)"
            )
            return firmware_path
        except requests.RequestException as e:
            logger.error(f"Error downloading firmware: {e}")
            return None
        except IOError as e:
            logger.error(f"Error saving downloaded firmware: {e}")
            return None

    def _install_with_avrdude(
        self,
        hex_file_path: str,
        board: str,
        avrdude_path_override: Optional[str],
        avrdude_config_override: Optional[str],
        target_port: Optional[str],
    ) -> bool:
        """Internal method to perform the Avrdude flashing process."""
        start_time = time.time()

        partno, programmer_id, baud_rate = (
            "atmega328p",
            "arduino",
            115200,
        )  # Defaults for uno
        if board.lower() == "leonardo":
            partno, programmer_id, baud_rate = ("atmega32u4", "avr109", 57600)

        # Determine port: use target_port if provided, else try saved port, else scan.
        # This logic might be better in SerialCommunicator._list_potential_ports if generalized.
        ports_to_try = []
        if target_port:
            ports_to_try.append(target_port)
        else:
            saved_port = self.config_manager.get_value("port")
            if saved_port:
                ports_to_try.append(saved_port)
            # Add scanned ports if no specific/saved port works
            # This part is tricky as avrdude needs a specific port.
            # For now, let's assume if target_port is None, we use the saved port or fail.
            # A more robust solution would involve listing ports and trying.
            # The original code iterated `find_comports()`.
            # For simplicity here, we'll prioritize `target_port` then configured port.
            # If neither, this will likely fail unless Avrdude can auto-detect (unlikely for all setups).
            # Let's assume `target_port` will be the one identified by `check_current_firmware` if not overridden.

        if (
            not ports_to_try and not target_port
        ):  # If target_port was None and no saved port
            logger.error(
                "No specific port provided for Avrdude and no port saved in config. Please specify a port."
            )
            # Could attempt to list ports here, but Avrdude needs one.
            # For now, require a port to be known.
            # The `firmware_check` in original code returned the port.
            # The calling method `manage_firmware_update` should pass this port.
            if (
                not target_port
            ):  # This check is redundant if the logic above is followed.
                logger.error("Target port for Avrdude is unknown.")
                return False
            ports_to_try.append(target_port)  # Should be set by caller

        avrdude_path = avrdude_path_override or self.config_manager.get_value(
            "avrdude-path"
        )
        avrdude_config_path = avrdude_config_override or self.config_manager.get_value(
            "avrdude-config-path"
        )

        for port_to_flash in ports_to_try:  # Usually, this will be a single port.
            logger.info(
                f"Attempting to flash firmware to {board} on port {port_to_flash} using Avrdude..."
            )
            try:
                avrdude = Avrdude(
                    partno=partno,
                    programmer_id=programmer_id,
                    baud_rate=baud_rate,
                    port=port_to_flash,
                    avrdude_path=avrdude_path,
                    avrdude_config_path=avrdude_config_path,
                )

                stderr_output, return_code = avrdude.flash_firmware(hex_file_path)
                if return_code == 0:
                    logger.info(
                        f"Firmware successfully updated on {port_to_flash} ({time.time() - start_time:.2f}s)"
                    )
                    self.config_manager.set_value(
                        "port", port_to_flash
                    )  # Save successful port
                    if (
                        avrdude_path_override is None
                    ):  # Only save if not overridden by user for this run
                        self.config_manager.set_value("avrdude-path", avrdude.command)
                    if avrdude_config_override is None and avrdude.config:
                        self.config_manager.set_value(
                            "avrdude-config-path", str(avrdude.config.absolute())
                        )
                    return True
                else:
                    logger.error(
                        f"Firmware update failed on port {port_to_flash}. Avrdude stderr:"
                    )
                    for line in stderr_output.splitlines():
                        logger.error(f"  {line}")
            except (AvrdudeNotFoundError, AvrdudeConfigNotFoundError) as e:
                logger.error(f"Avrdude setup error for port {port_to_flash}: {e}")
            except (
                Exception
            ) as e:  # Catch other potential errors from Avrdude instantiation/execution
                logger.error(
                    f"An unexpected error occurred with Avrdude on port {port_to_flash}: {e}"
                )

        logger.error("Firmware installation failed on all attempted ports.")
        return False

    def manage_firmware_update(
        self,
        install_flag: bool = False,
        avrdude_path_override: Optional[str] = None,
        avrdude_config_override: Optional[str] = None,
        port_override: Optional[str] = None,
        board_override: Optional[str] = "uno",
        flags: int = 0,
        ) -> bool:
        """
        Manages the firmware update process: checks version, prompts user, and installs if needed.
        Returns True if an operation (check or install) was successful in some sense, False on major failure.
        """
        connected_port, current_version, current_board = self.check_current_firmware(
            preferred_port=port_override,flags=flags
        )

        # Use the port where firmware was checked, or CLI override for flashing
        port_to_use = port_override or connected_port
        if not port_to_use:
            logger.error(
                "Cannot determine port for programmer. Please specify with --port."
            )
            return False

        # Use board detected from firmware if available, else use CLI override or default
        board_to_use = current_board or board_override

        latest_version, download_url = self.fetch_latest_release_info(
            board=board_to_use
        )

        force_install = flags & FLAG_FORCE
        if not current_version and not install_flag and not force_install:
            logger.error(
                "Could not determine current firmware version. Use --install or --force to proceed with installation."
            )
            return False  # Failed to get current version and no intent to install

        is_up_to_date = False
        if current_version and latest_version:
            is_up_to_date = self._compare_versions(current_version, latest_version)

        if is_up_to_date and not force_install:
            logger.info(
                f"Firmware is already up to date (version {current_version} for {board_to_use}). Use --force to reinstall."
            )
            return True  # Successfully checked, no update needed

        # Determine if installation should proceed
        should_install_now = False
        if force_install:
            logger.info(f"Forcing firmware installation for {board_to_use}.")
            should_install_now = True
        elif install_flag:
            if not is_up_to_date:
                logger.info(
                    f"Proceeding with firmware update for {board_to_use} (current: {current_version}, latest: {latest_version})."
                )
                should_install_now = True
            else:  # install_flag is true, but already up-to-date
                logger.info(
                    f"Firmware for {board_to_use} is already version {latest_version}. Use --force to reinstall."
                )
                # No installation needed unless forced
        elif (
            not is_up_to_date and current_version and latest_version
        ):  # No flags, but update available
            if Confirm.ask(
                f"New firmware {latest_version} available for {board_to_use} (current: {current_version}). Update now?",
                default=False,
            ):
                should_install_now = True
            else:
                logger.info("Firmware update cancelled by user.")
        elif not current_version and (
            install_flag or force_install
        ):  # No current version, but user wants to install
            logger.info(
                f"No current firmware version detected. Proceeding with installation of latest version for {board_to_use}."
            )
            should_install_now = True

        if should_install_now:
            if not download_url or not latest_version:
                logger.error(
                    f"Cannot install: latest firmware URL or version for {board_to_use} is not available."
                )
                return False

            hex_file = self._download_firmware_file(download_url)
            if not hex_file:
                logger.error("Firmware download failed. Installation aborted.")
                return False

            install_success = self._install_with_avrdude(
                hex_file_path=hex_file,
                board=board_to_use,
                avrdude_path_override=avrdude_path_override,
                avrdude_config_override=avrdude_config_override,
                target_port=port_to_use,
            )
            # Clean up downloaded file
            if os.path.exists(hex_file):
                try:
                    os.remove(hex_file)
                    logger.debug(f"Cleaned up downloaded firmware: {hex_file}")
                except OSError as e:
                    logger.warning(
                        f"Could not remove temporary firmware file {hex_file}: {e}"
                    )
            return install_success

        return True  # No installation performed, but process completed as expected
