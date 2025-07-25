"""
Project Name: Firestarter
Copyright (c) 2025 Henrik Olsson

Permission is hereby granted under MIT license.
"""

FIRESTARTER_RELEASE_URL = (
    "https://api.github.com/repos/henols/firestarter/releases/latest"
)

# Constants
BAUD_RATE = "250000"

BUFFER_SIZE = 512
LEONARDO_BUFFER_SIZE = 1024


COMMAND_READ = 1
COMMAND_WRITE = 2
COMMAND_ERASE = 3
COMMAND_BLANK_CHECK = 4
COMMAND_CHECK_CHIP_ID = 5
COMMAND_VERIFY = 6

COMMAND_DEV_ADDRESS = 7
COMMAND_DEV_REGISTERS = 8

COMMAND_READ_VPP = 11
COMMAND_READ_VPE = 12
COMMAND_FW_VERSION = 13
COMMAND_CONFIG = 14
COMMAND_HW_VERSION = 15

# Control Flags
FLAG_FORCE = 0x01
FLAG_CAN_ERASE = 0x02
FLAG_SKIP_ERASE = 0x04
FLAG_SKIP_BLANK_CHECK = 0x08
FLAG_VPE_AS_VPP = 0x10

FLAG_OUTPUT_ENABLE = 0x20
FLAG_CHIP_ENABLE = 0x40

FLAG_VERBOSE = 0x80
