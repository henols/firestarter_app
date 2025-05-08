"""
Project Name: Firestarter
Copyright (c) 2025 Henrik Olsson

Permission is hereby granted under MIT license.
AVRdude Tool Wrapper Module
"""
import os
import re
import time
import logging
import serial
from subprocess import Popen, PIPE, CalledProcessError, TimeoutExpired
from pathlib import Path
from shutil import which

logger = logging.getLogger("Avrdude")


class AvrdudeNotFoundError(FileNotFoundError): ...


class AvrdudeConfigNotFoundError(FileNotFoundError): ...


class Avrdude:
    """
    A wrapper class for interacting with the Avrdude command-line utility.
    It helps in finding the Avrdude executable, managing its configuration file,
    retrieving its version, and executing commands for flashing firmware
    or testing connections to AVR microcontrollers.
    """
    def __init__(
        self,
        partno,
        programmer_id,
        baud_rate,
        port,
        avrdude_config_path=None,
        avrdude_path=None,
    ):
        self.partno = partno
        self.programmer_id = programmer_id
        self.baud_rate = baud_rate
        self.port = port
        self.command = self._find_avrdude_path(avrdude_path)
        self.version = self._get_avrdude_version()
        if self.version < 7.0:
            self.config = self._configure_avrconf(avrdude_config_path)
        else:
            self.config = None
        logger.debug(f"Initialized Avrdude on port: {self.port}")

    def _find_avrdude_path(self, avrdude_path):
        """Find the avrdude executable path."""
        executable = "avrdude"
        paths_to_check = [
            avrdude_path,
            Path(avrdude_path) / executable if avrdude_path else None,
            which(executable),
        ]
        for path in paths_to_check:
            if path and which(str(path)):
                return which(str(path))
        raise AvrdudeNotFoundError

    def _configure_avrconf(self, confpath):
        """Configure the avrdude configuration file path."""
        if not confpath:
            confpath = os.path.dirname(self.command)

        avrconf = Path(confpath)
        if not avrconf.name.endswith("avrdude.conf"):
            avrconf /= "avrdude.conf"

        if not avrconf.exists():
            raise AvrdudeConfigNotFoundError

        return avrconf

    def _get_avrdude_version(self):
        """Retrieve and validate the avrdude version."""
        stderr, _ = self._execute_command([])
        match = re.search(
            r"avrdude\s+version\s+(\d+\.\d+(\.\d+)?)", stderr, re.IGNORECASE
        )
        if match:
            version = match.group(1)
            logger.debug(f"avrdude version: {version}")
            return float(version)
        logger.warning("Could not determine avrdude version.")
        return None

    def _execute_command(self, options):
        """Execute an avrdude command with the provided options."""
        cmd = [self.command] + options

        if len(options) > 0 and self.partno == "atmega32u4":
            if not self._trigger_reset():
                return f"Failed to open port {self.port}.", -1

        logger.debug(f"Executing command: {' '.join(cmd)}")
        process = Popen(cmd, stdout=PIPE, stderr=PIPE, stdin=PIPE)
        try:
            stdout, stderr = process.communicate(timeout=30)
            returncode = process.returncode
        except TimeoutExpired as e:
            stderr = e.stderr
            returncode = -1
        return stderr.decode("ISO-8859-1"), returncode

    def _trigger_reset(self):
        """Trigger a reset for certain microcontrollers."""
        try:
            serial.Serial(port=self.port, baudrate=1200).close()
            time.sleep(2)
            return True
        except Exception as e:
            logger.warning(f"Failed to trigger reset: {self.port}")
            return False

    def build_options(self, extra_flags=None):
        """Build common avrdude command-line options."""
        options = [
            "-v",
            "-p",
            self.partno,
            "-c",
            self.programmer_id,
            "-b",
            str(self.baud_rate),
            "-P",
            self.port,
        ]
        if self.config:
            options += ["-C", str(self.config.absolute())]
        if extra_flags:
            options += extra_flags
        return options

    def flash_firmware(self, hex_file, extra_flags=None):
        """Flash firmware to the microcontroller."""
        options = self.build_options(extra_flags) + [
            "-D",
            "-U",
            f"flash:w:{hex_file}:i",
        ]
        return self._execute_command(options)

    def test_connection(self, extra_flags=None):
        """Test the connection to the microcontroller."""
        options = self.build_options(extra_flags)
        return self._execute_command(options)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    avrdude = Avrdude(
        partno="atmega328p",
        programmer_id="arduino",
        baud_rate=115200,
        port="/dev/ttyACM0",
        # avrdude_path="/home/henrik/.platformio/packages/tool-avrdude@1.60300.200527",
    )

    # Test connection to the microcontroller
    stderr, returncode = avrdude.test_connection()
    logger.error(stderr)
