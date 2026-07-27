/*
 * DELIBERATELY-VIOLATING fixture for
 * tests/test_check_no_log_in_sdp_window.py (Phase 116 Plan 04, TRACE-03c).
 *
 * This file is a minimal, standalone, never-compiled C++ source. It is not
 * built by platformio.ini and is not part of any firmware target. It exists
 * ONLY so the paired pytest can point tools/check_no_log_in_sdp_window.py's
 * FIRESTARTER_SDP_SRC env-override seam at it and prove the checker actually
 * exits non-zero on a real logging call planted inside the SDP timing
 * window.
 *
 * "Fixing" this file (i.e. removing the planted LOG_INFO_ID(...) call below)
 * would silently hollow TRACE-03's third negative -- the anti-hollow gate
 * this project has required since the v1.12 hollow-GATE-03 tech debt. Do
 * NOT "fix" this file. If the checker's anchors ever change shape, update
 * this fixture to match the new shape (keeping the planted violation
 * between the anchors), do not delete the violation.
 */

void eeprom28c_write_init(firestarter_handle_t* handle) {
    if (handle->chip_id > 0) {
        eeprom28c_check_chip_id(handle);
        if (handle->response_code == RESPONSE_CODE_ERROR) {
            return;
        }
    }
    // Disable SDP (Software Data Protection) before writing.
    flash_execute_command(EEPROM_SDP_DISABLE);
    LOG_INFO_ID(MSG_DEBUG);  // PLANTED VIOLATION -- inside the SDP timing window
    // Wait for SDP disable internal write cycle to complete
    if (!eeprom28c_wait_for_write(handle, 0x5555, 0x20)) {
        return;
    }
    if (!is_flag_set(FLAG_SKIP_BLANK_CHECK)) {
        mem_util_blank_check(handle);
    }
}
