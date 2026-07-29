/*
 * DELIBERATELY-VIOLATING fixture for
 * tests/test_revision_constants_parity.py (Phase 120 Plan 07, HOST-03,
 * D-12/D-13's anti-hollow two-way CMD_*/FLAG_* gate).
 *
 * This file is a minimal, standalone, never-compiled C header. It is not
 * built by platformio.ini and is not referenced from any firmware target or
 * build_src_filter. It exists ONLY so the paired pytest can point
 * test_revision_constants_parity.py's module-level FIRMWARE_HEADER path
 * constant at it (via `monkeypatch.setattr` on FIRMWARE_HEADER, never an
 * edit to the real firestarter.h) and prove the rebuilt gate actually fails
 * when firmware defines a command the host has never heard of.
 *
 * It is a faithful copy of firestarter/include/firestarter.h's CMD_* region
 * (lines 34-68) and FLAG_* region (lines 131-148), preserving every define,
 * every #ifdef DEV_TOOLS conditional, and every value -- with exactly ONE
 * planted addition: CMD_DEBUG_DUMP (value 16, the next unused command
 * slot), a plausible-looking command with NO constants.py counterpart and
 * NOT present in _EXEMPT_FW_TO_HOST. No existing CMD_* or FLAG_* value is
 * changed and nothing existing is removed.
 *
 * This fixture deliberately does NOT trip:
 *   - the value-drift leg (every pre-existing CMD_*/FLAG_* value below is
 *     unchanged from the real header)
 *   - the firmware-missing-define leg (nothing is removed)
 *   - the COMMAND_NAMES-coverage leg on any PRE-EXISTING command (the new
 *     CMD_DEBUG_DUMP has no host constant at all, so it is caught earlier,
 *     by the two-way leg's forward direction -- it never reaches the
 *     COMMAND_NAMES check)
 *   - the conditional-compilation leg (the #ifdef DEV_TOOLS block below is
 *     unchanged)
 * A fixture that failed for two reasons at once could not prove which
 * check fired -- this isolation is why three separate fixtures exist
 * instead of one three-drift fixture.
 *
 * "Fixing" this file (i.e. deleting the CMD_DEBUG_DUMP line) would silently
 * hollow HOST-03's host-missing-counterpart detection leg -- the anti-hollow
 * gate this project has required since the v1.12 hollow-GATE-03 tech debt.
 * Do NOT "fix" this file. If the real header's CMD_* region ever changes
 * shape, update this fixture to match the new shape (keeping exactly one
 * planted addition with no host counterpart), do not delete the violation.
 */

#define CMD_FRAME_MAX DATA_BUFFER_SIZE

#define CMD_IDLE 0
#define CMD_READ 1
#define CMD_WRITE 2
#define CMD_ERASE 3
#define CMD_BLANK_CHECK 4
#define CMD_CHECK_CHIP_ID 5
#define CMD_VERIFY 6

#ifdef DEV_TOOLS
#define CMD_DEV_ADDRESS 7
#define CMD_DEV_REGISTER 8
#endif

#define CMD_SDP_UNLOCK 9
#define CMD_SDP_LOCK 10

#define CMD_READ_VPP 11
#define CMD_READ_VPE 12
#define CMD_FW_VERSION 13
#define CMD_CONFIG 14
#define CMD_HW_VERSION 15

// PLANTED VIOLATION -- a plausible new command with no host counterpart at
// all, and not present in _EXEMPT_FW_TO_HOST.
#define CMD_DEBUG_DUMP 16

// Control flags
#define FLAG_FORCE 0x01
#define FLAG_CAN_ERASE 0x02
#define FLAG_SKIP_ERASE 0x04
#define FLAG_SKIP_BLANK_CHECK 0x08
#define FLAG_VPE_AS_VPP 0x10

#define FLAG_OUTPUT_ENABLE 0x20
#define FLAG_CHIP_ENABLE 0x40

#define FLAG_VERBOSE 0x80

#define FLAG_SKIP_SDP_UNLOCK 0x100
