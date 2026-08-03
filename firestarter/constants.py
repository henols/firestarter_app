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
# DATA_BUFFER_SIZE-1 payload bytes (CR-01 guard reserves the NUL-terminator slot;
# Phase 51 P04). The decoded payload is data_chunk + CRC8, so the data chunk must
# satisfy len(data) + 1 (CRC) <= DATA_BUFFER_SIZE - 1, i.e. len(data) <= BUFFER_SIZE - 2.
# Sending a full BUFFER_SIZE (512) chunk overflows the decoder -> "Data error: -2"
# and breaks write/verify on every board (bench-confirmed Phase 53, both Uno + Leonardo).
# OBSOLETE (Phase 54/EVEN-01): _calculate_buffer_size now reads firmware_max_chunk
# directly; this constant is no longer used as the chunk-size default. Retained to
# avoid breaking external references.
MAX_DATA_CHUNK = BUFFER_SIZE - 2  # 510

# Command-channel frame size limit — Firmware sync: firestarter.h CMD_FRAME_MAX
# Largest legitimate JSON command (~422 B) + headroom = 512; equals BUFFER_SIZE.
# Firmware parity: firestarter.h #define CMD_FRAME_MAX DATA_BUFFER_SIZE
# per CLAUDE.md constant-parity rule (FRAME-05 / D-06).
CMD_FRAME_MAX = 512

# IN-02 (Phase 98-03/98-05): <=256K (262144 byte) size boundary for 0x08
# (EPROM_QUICK) 32-pin parts where pin 31 (A18 on DIP32_STD) is structurally
# unused as an address line and is safe to repurpose as DIP32_27C020's
# PGM/RW strobe. Chips above this boundary (512K AM27C040, 1M AM27C080)
# legitimately use pin 31 = A18 and MUST stay on DIP32_STD (D-04 alias guard).
# tools/build_db.py imports this constant (single host-side source of truth)
# rather than redefining it. Firmware parity: firestarter.h #define
# MAX_27C020_SIZE 262144 — a divergence is a hardware-damage A18 risk;
# see tests/test_revision_constants_parity.py.
MAX_27C020_SIZE = 262144


# Wire-protocol command codes — Firmware sync: firestarter.h
# cmd field values sent in JSON commands to the Arduino firmware.
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
# COMMAND_NAMES[cmd] is dereferenced at eprom_operations.py:301 and again at
# :377 (_setup_operation / _operation_context) — a missing entry is a KeyError
# at operation setup, not a cosmetic display gap.
COMMAND_SDP_UNLOCK = 9
COMMAND_SDP_LOCK = 10

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
    COMMAND_SDP_UNLOCK: "SDP_UNLOCK",
    COMMAND_SDP_LOCK: "SDP_LOCK",
    COMMAND_READ_VPP: "READ_VPP",
    COMMAND_READ_VPE: "READ_VPE",
    COMMAND_FW_VERSION: "FW_VERSION",
    COMMAND_CONFIG: "CONFIG",
    COMMAND_HW_VERSION: "HW_VERSION",
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
# no 0x200 flag, contrary to ROADMAP.md:363 and Phase 120's *Depends on* line
# (F-120-05, corrected in 120-02-SUMMARY.md).
# NOTE: CTRL_VPP_VPE_DROP_ENABLE further below also has the value 0x100, but
# it lives in the separate control-register namespace (mirror of
# rurp_pinout.h), is documentary only (Python never writes the control
# register), and has its own separate parity leg. The two 0x100s are
# unrelated wire vs. control-register values and must not be conflated.
# D-14 / RETIRE-07 tripwire (third location): this bit's default-OFF state on
# every write is what makes the host's SDP auto-unlock effective by default
# -- the argument RETIRE-01 (Phase 132) relies on to justify deleting the
# standalone `firestarter dev sdp` subcommand. Changing this bit's semantics,
# or the default either edit point that sets it defaults to
# (`cli_handlers.py`'s `_build_op_flags` `skip_sdp_unlock` parameter, or the
# `--skip-sdp-unlock` Click option on `write`), invalidates that argument.
# See the decision-site comment at the D-04 auto-set condition in
# `cli_handlers.py`'s `write()`, and the named test
# `test_dev_sdp_removal_is_safe_only_because_auto_unlock_is_default_on` in
# `tests/test_write_skip_sdp_unlock.py`.
FLAG_SKIP_SDP_UNLOCK = 0x100

# Dev sweep knobs — Firmware sync: json_parser.c (key_read_settling, key_read_strobe)
# JSON key name strings for host-tunable read-timing parameters.
# MUST stay in sync with the PROGMEM key strings in firmware json_parser.c.
# Used by consistency_check_eprom() to emit knob values in per-read JSON commands.
JSON_KEY_READ_SETTLING_DELAY = "read-settling-delay"
JSON_KEY_READ_STROBE_US = "read-strobe-us"
# Per-chip page size wire field (PGSZ-03 / CR-01) — Firmware sync: json_parser.c (key_page_size)
# Emitted by eprom_operations.py only when the DB supplies a datasheet-sourced page_size
# (emit-when-present, mirrors read-strobe-us pattern). When absent, firmware falls back
# to flash4_page_size(mem_size) heuristic. 0 = use firmware default.
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
