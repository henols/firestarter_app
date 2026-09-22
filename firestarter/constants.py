"""
Project Name: Firestarter
Copyright (c) 2025 Henrik Olsson

Permission is hereby granted under MIT license.
"""

FIRESTARTER_RELEASE_URL = (
    "https://api.github.com/repos/henols/firestarter_fw/releases/latest"
)

FIRESTARTER_RELEASES_URL = "https://api.github.com/repos/henols/firestarter_fw/releases"

FIRESTARTER_RELEASE_BY_TAG_URL = (
    "https://api.github.com/repos/henols/firestarter_fw/releases/tags/{tag}"
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


# Wire-protocol command codes — Firmware sync: firestarter.h
# cmd field values sent in JSON commands to the Arduino firmware.
# The ladder now reaches 16 (COMMAND_LOCK_STATUS). Per
# CLAUDE.md's constants-are-duplicated rule, this ladder and firmware's
# CMD_* ladder in firestarter.h move together — every addition here must be
# mirrored there in the same change, and vice versa.
COMMAND_READ = 1
COMMAND_WRITE = 2
COMMAND_ERASE = 3

# Ordinal 4 -- the standalone blank-check command -- retired in 3.1.0
# (Phase 204). The firmware side -- firestarter_fw/include/firestarter.h's
# CMD ladder and its is_memory_cmd arm -- was retired in the same commit
# pair. This ordinal must NEVER be reused for any new command, flag or
# reserved meaning: an already-shipped host still composes it, and
# reassigning the number would make a stale host silently drive a
# different operation against firmware that has moved on. The region-scoped
# blank-check machinery on the firmware side survives this retirement --
# it is reached only from write-init and erase-end now, and leaves in
# Phase 205.

COMMAND_CHECK_CHIP_ID = 5

# Ordinal 6 -- the verify command -- retired in 3.1.0 (Phase 204). The
# firmware side -- firestarter_fw/include/firestarter.h's CMD ladder and its
# is_memory_cmd arm -- was retired in the same commit pair. This ordinal
# must NEVER be reused for any new command, flag or reserved meaning: an
# already-shipped host still composes it, and reassigning the number would
# make a stale host silently drive a different operation against firmware
# that has moved on.

COMMAND_DEV_ADDRESS = 7
COMMAND_DEV_REGISTERS = 8

# Both SDP commands are unconditional in firmware (firestarter.h:61-62) — never
# DEV_TOOLS-gated, because they are real user-facing operations in every build.
# Their COMMAND_NAMES entries below are load-bearing, not cosmetic:
# COMMAND_NAMES[cmd] is dereferenced by _setup_operation (eprom_operations.py:584)
# and again by _operation_context (eprom_operations.py:693) — a missing entry
# is a KeyError at operation setup, not a cosmetic display gap. Corrected
# 2026-09-21 (Phase 204): the previous comment cited
# test_command_names_dereferences_both_sdp_commands in
# tests/test_revision_constants_parity.py, a test module that does not exist
# anywhere under firestarter_app/tests/, and gave line numbers (329/405) that
# no longer matched either dereference site. There is no dedicated test
# pinning these two dereferences; the citation now names only the two real
# call sites, by function name with the line number alongside.
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
    COMMAND_CHECK_CHIP_ID: "CHECK_CHIP_ID",
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

# The skip-blank-check control flag, 0x08 -- retired in 3.1.0 (Phase 205).
# The firmware side -- firestarter_fw/include/firestarter.h's control-flag
# ladder -- was retired in the same commit pair. This value must NEVER be
# reused for any new control flag: an already-shipped host still composes
# 0x08 on every `write -b` and on `dev test`'s masked UV slot writes, and
# reassigning the bit would make that stale host silently turn on whatever
# new behaviour took the number. While it existed, the flag selected
# whether write-init's blank check ran; that check itself left the
# firmware in the same phase (FWBLANK-01..03). `-b` now reaches the
# host-side write guard (write_blank_guard.py) as an explicit keyword-only
# signal instead of a wire bit.

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
# convert_to_programmer only when the DB supplies a page_size --
# emit-when-present, mirrors the chip-id pattern. When absent, firmware
# falls back to its own named AT28C page-size floor constant.
# Firmware sync: json_parser.c (key_page_size).
# (firestarter commit 58c6a3c) -- the PROGMEM string exists and is dispatched
# from key_parsers[].
JSON_KEY_PAGE_SIZE = "page-size"
# Absolute, exclusive end address of the operation's region. Emitted by
# _setup_operation in eprom_operations.py, not by database.py's
# convert_to_programmer -- the region is a property of the operation (which
# bytes are being written), not of the chip row.
# Absent-semantics are the OPPOSITE of page-size above: absent means the
# WHOLE DEVICE. page-size guards a destructive write, so refusing on
# absence is the safe direction there; this field guards a relaxation of an
# existing whole-device check, so falling back to the stricter existing
# behaviour on absence is the safe direction here instead.
# Firmware sync: json_parser.c (key_region_end).
JSON_KEY_REGION_END = "region-end"

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
