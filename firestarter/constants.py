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

# Max host->fw DATA chunk (write/verify pull-protocol). LOCKSTEP CONTRACT with the
# firmware COBS decoder rurp_communication_read_data: it commits at most
# DATA_BUFFER_SIZE-1 payload bytes (the guard reserves the NUL-terminator slot).
# The decoded payload is data_chunk + CRC8, so the data chunk must
# satisfy len(data) + 1 (CRC) <= DATA_BUFFER_SIZE - 1, i.e. len(data) <= BUFFER_SIZE - 2.
# Sending a full BUFFER_SIZE (512) chunk overflows the decoder -> "Data error: -2"
# and breaks write/verify on every board (bench-confirmed on both Uno + Leonardo).
# OBSOLETE: _calculate_buffer_size now reads firmware_max_chunk
# directly; this constant is no longer used as the chunk-size default. Retained to
# avoid breaking external references.
MAX_DATA_CHUNK = BUFFER_SIZE - 2  # 510

# Command-channel frame size limit — Firmware sync: firestarter.h CMD_FRAME_MAX
# Largest legitimate JSON command (~422 B) + headroom = 512; equals BUFFER_SIZE.
# Firmware parity: firestarter.h #define CMD_FRAME_MAX DATA_BUFFER_SIZE
# per CLAUDE.md constant-parity rule.
CMD_FRAME_MAX = 512

# <=256K (262144 byte) size boundary for 0x08
# (EPROM_QUICK) 32-pin parts where pin 31 (A18 on DIP32_STD) is structurally
# unused as an address line and is safe to repurpose as DIP32_27C020's
# PGM/RW strobe. Chips above this boundary (512K AM27C040, 1M AM27C080)
# legitimately use pin 31 = A18 and MUST stay on DIP32_STD (alias guard).
# tools/build_db.py imports this constant (single host-side source of truth)
# rather than redefining it. Firmware parity: firestarter.h #define
# MAX_27C020_SIZE 262144 — a divergence is a hardware-damage A18 risk;
# see tests/test_revision_constants_parity.py.
MAX_27C020_SIZE = 262144


# Wire-protocol command codes — Firmware sync: firestarter.h
# cmd field values sent in JSON commands to the Arduino firmware.
# The ladder now reaches 16 (COMMAND_LOCK_STATUS). Per
# CLAUDE.md's constants-are-duplicated rule, this ladder and firmware's
# CMD_* ladder in firestarter.h move together — every addition here must be
# mirrored there in the same change, and vice versa.
COMMAND_READ = 1
COMMAND_WRITE = 2
COMMAND_ERASE = 3
COMMAND_BLANK_CHECK = 4
COMMAND_CHECK_CHIP_ID = 5
COMMAND_VERIFY = 6

COMMAND_DEV_ADDRESS = 7
COMMAND_DEV_REGISTERS = 8

# Both SDP commands are unconditional in firmware (firestarter.h:61-62) — never
# DEV_TOOLS-gated, because they are real user-facing operations in every build.
# Their COMMAND_NAMES entries below are load-bearing, not cosmetic:
# COMMAND_NAMES[cmd] is dereferenced by _setup_operation (eprom_operations.py:329)
# and again by _operation_context (eprom_operations.py:405) — a missing entry
# is a KeyError at operation setup, not a cosmetic display gap. Corrected
# 2026-08-03: a prior milestone's insertion staled the
# original 301/377 citation, which is why the corrected form names the
# function first with the line number alongside, not the number alone. See
# test_command_names_dereferences_both_sdp_commands in
# tests/test_revision_constants_parity.py, which pins both dereferences.
COMMAND_SDP_UNLOCK = 9
COMMAND_SDP_LOCK = 10

COMMAND_READ_VPP = 11
COMMAND_READ_VPE = 12
COMMAND_FW_VERSION = 13
COMMAND_CONFIG = 14
COMMAND_HW_VERSION = 15

# Protection-status read. A memory command on the
# firmware side (is_memory_cmd()'s ninth arm, firestarter.h) because the
# read is issued through firestarter_get_data, set only by
# configure_memory() — no exemption needed in
# test_revision_constants_parity.py's four-entry map; it maps to
# COMMAND_LOCK_STATUS by the default CMD_X -> COMMAND_X rule.
COMMAND_LOCK_STATUS = 16

COMMAND_NAMES = {
    COMMAND_READ: "READ",
    COMMAND_WRITE: "WRITE",
    COMMAND_ERASE: "ERASE",
    COMMAND_BLANK_CHECK: "BLANK_CHECK",
    COMMAND_CHECK_CHIP_ID: "CHECK_CHIP_ID",
    COMMAND_VERIFY: "VERIFY",
    COMMAND_DEV_ADDRESS: "DEV_ADDRESS",
    COMMAND_DEV_REGISTERS: "DEV_REGISTERS",
    COMMAND_SDP_UNLOCK: "SDP_UNLOCK",
    COMMAND_SDP_LOCK: "SDP_LOCK",
    COMMAND_READ_VPP: "READ_VPP",
    COMMAND_READ_VPE: "READ_VPE",
    COMMAND_FW_VERSION: "FW_VERSION",
    COMMAND_CONFIG: "CONFIG",
    COMMAND_HW_VERSION: "HW_VERSION",
    COMMAND_LOCK_STATUS: "LOCK_STATUS",
}

# Control Flags — Firmware sync: firestarter.h
# flags bitmask values sent in JSON commands.
FLAG_FORCE = 0x01
FLAG_CAN_ERASE = 0x02
FLAG_SKIP_ERASE = 0x04
FLAG_SKIP_BLANK_CHECK = 0x08
FLAG_VPE_AS_VPP = 0x10

FLAG_OUTPUT_ENABLE = 0x20
FLAG_CHIP_ENABLE = 0x40

FLAG_VERBOSE = 0x80

# Ninth and highest wire flag. Firmware's ctrl_flags is uint32_t, so 0x100 is
# in range, and firmware's flag block ENDS here (firestarter.h:148) — there is
# no 0x200 flag, despite older documentation elsewhere claiming one.
# NOTE: CTRL_VPP_VPE_DROP_ENABLE further below also has the value 0x100, but
# it lives in the separate control-register namespace (mirror of
# rurp_pinout.h), is documentary only (Python never writes the control
# register), and has its own separate parity leg. The two 0x100s are
# unrelated wire vs. control-register values and must not be conflated.
# SDP auto-unlock tripwire. This bit being OFF by default on every write is
# what makes the host's SDP auto-unlock effective by default -- the argument
# that justified deleting the standalone `dev sdp` subcommand. Changing this
# bit's semantics, or either edit point that sets it, invalidates that
# argument. See test_dev_sdp_removal_is_safe_only_because_auto_unlock_is_default_on.
FLAG_SKIP_SDP_UNLOCK = 0x100

# Dev sweep knobs — Firmware sync: json_parser.c (key_read_settling, key_read_strobe)
# JSON key name strings for host-tunable read-timing parameters.
# MUST stay in sync with the PROGMEM key strings in firmware json_parser.c.
# Used by consistency_check_eprom() to emit knob values in per-read JSON commands.
JSON_KEY_READ_SETTLING_DELAY = "read-settling-delay"
JSON_KEY_READ_STROBE_US = "read-strobe-us"
# Per-chip page size wire field. Emitted by database.py's
# convert_to_programmer only when the DB supplies a page_size (curated or,
# provenance-keyed for upstream-native 0x0D rows) --
# emit-when-present, mirrors the chip-id pattern. When absent, firmware
# falls back to its own named AT28C page-size floor constant (algorithm 13
# / 0x0D only; other algorithms' handlers do not consume this key at all).
# Firmware sync: json_parser.c (key_page_size).
# (firestarter commit 58c6a3c) -- the PROGMEM string exists and is dispatched
# from key_parsers[]. tests/test_json_key_parity.py (plan 05) is the
# enforcing test that keeps this string in lockstep with the firmware key.
JSON_KEY_PAGE_SIZE = "page-size"

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
