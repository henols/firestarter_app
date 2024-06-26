"""
Copyright 2016 Mike Killian

This module allows multiple board to be programmed from the same hex file

Inspired by avr_helpers written by Ryan Fobel
"""

import os
from subprocess import Popen, PIPE, CalledProcessError
import logging

from pathlib import Path

# from serial_device2 import WriteError, ReadError, SerialDevice, \
#     SerialDevices, find_serial_device_ports, find_serial_device_port

logger = logging.getLogger()
try:
    from shutil import which
except:
    logger.warning(
        "shutil.which not found, location of avrdude needs to be ", "set manually "
    )


class Avrdude:

    def __init__(
        self, partno, programmer_id, baud_rate, port, confpath=None, avrdudePath=None
    ):
        self.partno = partno
        self.programmer_id = programmer_id
        self.baud_rate = baud_rate
        avrdude = "avrdude"
        path = None
        if avrdudePath:
            path = which(avrdudePath)
            if path is None:
                a = Path(avrdudePath) / Path(avrdude)
                path = which(str(a))

        if path is None:
            path = which(avrdude)
        if path is None:
            raise FileNotFoundError("avrdude not found in PATH")
        self.avrdudeCommand = path
        self.port = port
        logger.info("Connecting to port: {}".format(self.port))

        if confpath is None:
            self.avrconf = Path(os.path.dirname(__file__))
        else:
            self.avrconf = Path(confpath).abspath()

        self.avrconf = self.avrconf / Path("data") / Path("avrdude.conf")

    def _executeCommand(self, options):
        cmd = [self.avrdudeCommand]

        cmd.extend(options)

        logger.info("Executing: {}".format(cmd))

        proc = Popen(cmd, stdout=PIPE, stderr=PIPE, stdin=PIPE)
        outs, errs = proc.communicate()
        return outs, errs, proc.returncode

    def flashFirmware(self, hexFile, extraFlags=None):
        options = [
            "-c",
            self.programmer_id,
            "-b",
            str(self.baud_rate),
            "-p",
            self.partno,
            "-P",
            self.port,
            "-C",
            self.avrconf,
            "-U",
            "flash:w:{}:i".format(hexFile),
        ]
        if extraFlags:
            options.extend(extraFlags)

        outs, errs, returncode = self._executeCommand(options)
        return outs, errs, returncode

    def testConnection(self, extraFlags=None):
        options = [
            "-c",
            self.programmer_id,
            "-b",
            str(self.baud_rate),
            "-p",
            self.partno,
            "-P",
            self.port,
            "-C",
            self.avrconf,
        ]
        if extraFlags:
            options.extend(extraFlags)

        outs, errs, returncode = self._executeCommand(options)
        return outs, errs, returncode
