/*
 * DELIBERATELY-VIOLATING fixture for
 * tests/test_check_is_memory_cmd_no_ifdef.py (Phase 119 Plan 03, LOCK-03,
 * D-04's textual oracle).
 *
 * This file is a minimal, standalone, never-compiled C++ header. It is not
 * built by platformio.ini and is not part of any firmware target. It exists
 * ONLY so the paired pytest can point tools/check_is_memory_cmd_no_ifdef.py's
 * FIRESTARTER_CMD_ADMISSION_SRC env-override seam at it and prove the
 * checker actually exits non-zero on a real preprocessor conditional planted
 * inside is_memory_cmd()'s body.
 *
 * The conditional below is planted INSIDE the predicate's switch body (never
 * between two case labels' outer scope, never in the surrounding #define
 * block) so it isolates assertion (a) -- "no conditional in the body" -- from
 * assertion (b) -- "the body enumerates exactly the eight expected
 * commands". The eight CMD_* identifiers are ALL still textually present in
 * the body regardless of the #ifdef/#endif wrapping (this checker scans
 * source text, it does not run a C preprocessor), so this fixture trips (a)
 * only, never (b) -- a fixture that failed for two reasons at once could not
 * prove which check fired.
 *
 * "Fixing" this file (i.e. removing the planted #ifdef DEV_TOOLS / #endif
 * pair below) would silently hollow LOCK-03's textual oracle -- the
 * anti-hollow gate this project has required since the v1.12 hollow-GATE-03
 * tech debt, followed again by v1.21 SAFE-03 and Phase 118 D-06. Do NOT "fix"
 * this file. If the checker's target predicate ever changes shape again,
 * update this fixture to match the new shape (keeping the planted
 * conditional inside the switch body), do not delete the violation.
 */

#include <stdint.h>

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

static inline bool is_memory_cmd(uint8_t cmd) {
    switch (cmd) {
        case CMD_READ:
        case CMD_WRITE:
        case CMD_ERASE:
        case CMD_BLANK_CHECK:
        case CMD_CHECK_CHIP_ID:
        case CMD_VERIFY:
#ifdef DEV_TOOLS  // PLANTED VIOLATION -- conditional inside the predicate body
        case CMD_SDP_UNLOCK:
        case CMD_SDP_LOCK:
#endif
            return true;
        default:
            return false;
    }
}
