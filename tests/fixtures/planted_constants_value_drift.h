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
 * on a real firmware/host value disagreement.
 *
 * It is a faithful copy of firestarter/include/firestarter.h's CMD_* region
 * (lines 34-68) and FLAG_* region (lines 131-148), preserving every define,
 * every #ifdef DEV_TOOLS conditional, and every value -- with exactly ONE
 * planted change: CMD_VERIFY's value below is 106, not the real firmware's
 * 6. Every other CMD_* and all nine FLAG_* defines are untouched, so this
 * fixture trips ONLY the two-way CMD_* value-drift assertion inside
 * test_every_firmware_cmd_define_maps_two_way_to_constants_py.
 *
 * This fixture deliberately does NOT trip:
 *   - the host-missing-define leg (no CMD_* or FLAG_* name is added)
 *   - the firmware-missing-define leg (no CMD_* or FLAG_* name is removed)
 *   - the COMMAND_NAMES-coverage leg (CMD_VERIFY still resolves to a real
 *     COMMAND_VERIFY entry with a COMMAND_NAMES key -- only its *value*
 *     disagrees, which is a different leg's job)
 *   - the conditional-compilation leg (the #ifdef DEV_TOOLS block below is
 *     unchanged)
 * A fixture that failed for two reasons at once could not prove which
 * check fired -- this isolation is the whole point of using three separate
 * fixtures instead of one three-drift fixture.
 *
 * "Fixing" this file (i.e. changing 106 back to 6) would silently hollow
 * HOST-03's value-drift detection leg -- the anti-hollow gate this project
 * has required since the v1.12 hollow-GATE-03 tech debt. Do NOT "fix" this
 * file. If the real header's CMD_* region ever changes shape, update this
 * fixture to match the new shape (keeping exactly one planted value drift
 * on a command with no special handling), do not delete the violation.
 */

#define CMD_FRAME_MAX DATA_BUFFER_SIZE

#define CMD_IDLE 0
#define CMD_READ 1
#define CMD_WRITE 2
#define CMD_ERASE 3
#define CMD_BLANK_CHECK 4
#define CMD_CHECK_CHIP_ID 5
#define CMD_VERIFY 106  /* PLANTED VIOLATION -- real firmware value is 6 */

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
