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
FALLBACK_BAUD_RATE = "115200"
BUFFER_SIZE = 512


STATE_READ = 1
STATE_WRITE = 2
STATE_ERASE = 3
STATE_CHECK_BLANK = 4
STATE_CHECK_CHIP_ID = 5
STATE_VERIFY = 6
STATE_READ_VPP = 11
STATE_READ_VPE = 12
STATE_FW_VERSION = 13
STATE_CONFIG = 14
STATE_HW_VERSION = 15

# Control Flags
FLAG_FORCE = 0x01
FLAG_CAN_ERASE = 0x02
FLAG_SKIP_ERASE = 0x04
FLAG_SKIP_BLANK_CHECK = 0x08
FLAG_VPE_AS_VPP = 0x10
