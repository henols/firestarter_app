"""
Project Name: Firestarter
Copyright (c) 2025 Henrik Olsson

Permission is hereby granted under MIT license.
"""

FIRESTARTER_RELEASE_URL = (
    "https://api.github.com/repos/henols/firestarter/releases/latest"
)

FIRESTARTER_RELEASES_URL = "https://api.github.com/repos/henols/firestarter/releases"

FIRESTARTER_RELEASE_BY_TAG_URL = (
    "https://api.github.com/repos/henols/firestarter/releases/tags/{tag}"
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

COMMAND_NAMES = {
    COMMAND_READ: "READ",
    COMMAND_WRITE: "WRITE",
    COMMAND_ERASE: "ERASE",
    COMMAND_BLANK_CHECK: "BLANK_CHECK",
    COMMAND_CHECK_CHIP_ID: "CHECK_CHIP_ID",
    COMMAND_VERIFY: "VERIFY",
    COMMAND_DEV_ADDRESS: "DEV_ADDRESS",
    COMMAND_DEV_REGISTERS: "DEV_REGISTERS",
    COMMAND_READ_VPP: "READ_VPP",
    COMMAND_READ_VPE: "READ_VPE",
    COMMAND_FW_VERSION: "FW_VERSION",
    COMMAND_CONFIG: "CONFIG",
    COMMAND_HW_VERSION: "HW_VERSION",
}

# Control Flags
FLAG_FORCE = 0x01
FLAG_CAN_ERASE = 0x02
FLAG_SKIP_ERASE = 0x04
FLAG_SKIP_BLANK_CHECK = 0x08
FLAG_VPE_AS_VPP = 0x10

FLAG_OUTPUT_ENABLE = 0x20
FLAG_CHIP_ENABLE = 0x40

FLAG_VERBOSE = 0x80

# RURP Control Register Bits — mirror of firestarter/include/rurp_pinout.h
# Documentary only — Python does not write the control register directly
# (firmware owns that). Used by `firestarter dev registers --firestarter`
# and similar host-side helpers. Keep in sync per CLAUDE.md sync rule.
CTRL_VPP_VPE_DROP_ENABLE = 0x100  # was VPE_TO_VPP (wide layout)
CTRL_VPP_REGULATOR_ENABLE = 0x080  # was REGULATOR
CTRL_READ_WRITE = 0x040  # was READ_WRITE
CTRL_ADDRESS_LINE_18 = 0x020
CTRL_ADDRESS_LINE_17 = 0x010
CTRL_VPP_P1_ENABLE = 0x008  # was P1_VPP_ENABLE
CTRL_VPE_ENABLE = 0x004  # was VPE_ENABLE
CTRL_VPP_A9_ENABLE = 0x002  # was A9_VPP_ENABLE
CTRL_ADDRESS_LINE_16 = 0x001

# RURP Hardware Revisions — mirror of firestarter/include/rurp_shield.h
# REVISION_* enum. Documentary only — Python does not perform the ADC
# band-detect (firmware owns that). Used by host-side mapping of the
# MSG_OK_REV physical-u8 byte to a silkscreen-version string for log /
# CLI output. Keep in sync per CLAUDE.md sync rule.
# 0xFF is reserved as the EEPROM-override-absent sentinel (see
# rurp_config_utils.cpp:37 + serial_comm.py _format_message).
REVISION_0 = 0x00
REVISION_1 = 0x01
REVISION_2_0 = 0x02  # broad bucket: covers Rev 2.0 / 2.1 / 2.2 (R41=4k7)
REVISION_2_1 = 0x03  # via EEPROM override only — ADC cannot distinguish
REVISION_2_2 = 0x04  # via EEPROM override only — ADC cannot distinguish
REVISION_2_3 = 0x05  # R41=10k physical detect
REVISION_UNKNOWN = 0xFE  # ADC band-gap or pre-detect-resistor + A2 indeterminate
