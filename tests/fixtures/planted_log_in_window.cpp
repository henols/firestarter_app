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
 * Phase 118 Plan 01 (D-06) redefined the checker's scanned window from the
 * span BETWEEN the emit and wait call sites to the union of the emitter
 * body (`eeprom28c_emit_command_sequence`) and the completion-poll body
 * (`eeprom28c_wait_for_sdp_completion`). Under that new window the old
 * placement of this fixture's planted call -- between the two call sites,
 * inside `eeprom28c_write_init` -- became LEGAL: the checker would return 0
 * on it, the paired pytest's `assert result.returncode == 1` would go RED,
 * and this gate would go hollow. So the planted violation was moved INSIDE
 * `eeprom28c_emit_command_sequence`'s body in the SAME commit as the D-06
 * rewrite. The planted call is now on line 35 (recorded here so a future
 * reader can cross-check it against the paired pytest's line literal).
 *
 * "Fixing" this file (i.e. removing the planted LOG_INFO_ID(...) call below)
 * would silently hollow TRACE-03's third negative -- the anti-hollow gate
 * this project has required since the v1.12 hollow-GATE-03 tech debt. Do
 * NOT "fix" this file. If the checker's target functions ever change shape
 * again, update this fixture to match the new shape (keeping the planted
 * violation inside the emitter body, never between the call sites), do not
 * delete the violation.
 */

static void eeprom28c_emit_command_sequence(firestarter_handle_t* handle, const byte_flip_t* sequence, size_t length) {
    rurp_set_data_output();
    LOG_INFO_ID(MSG_DEBUG);  // PLANTED VIOLATION -- inside the SDP timing window
    for (size_t i = 0; i < length; i++) {
        handle->firestarter_set_data(handle, sequence[i].address, sequence[i].byte);
    }
}

static void eeprom28c_wait_for_sdp_completion(firestarter_handle_t* handle) {
    delay(AT28C_TWC_MAX_MS);
    uint8_t previous = handle->firestarter_get_data(handle, EEPROM28C_TOGGLE_POLL_ADDRESS);
    for (uint8_t j = 0; j < AT28C_TOGGLE_POLL_MAX_READS; j++) {
        delayMicroseconds(10);
        uint8_t observed = handle->firestarter_get_data(handle, EEPROM28C_TOGGLE_POLL_ADDRESS);
        if ((observed & AT28C_DQ6_TOGGLE_MASK) == (previous & AT28C_DQ6_TOGGLE_MASK)) {
            return;
        }
        previous = observed;
    }
}

void eeprom28c_write_init(firestarter_handle_t* handle) {
    if (handle->chip_id > 0) {
        eeprom28c_check_chip_id(handle);
        if (handle->response_code == RESPONSE_CODE_ERROR) {
            return;
        }
    }
    // Disable SDP (Software Data Protection) before writing.
    eeprom28c_emit_command_sequence(handle, EEPROM_SDP_DISABLE, 6);
    // Wait for SDP disable internal write cycle to complete
    eeprom28c_wait_for_sdp_completion(handle);
    if (!is_flag_set(FLAG_SKIP_BLANK_CHECK)) {
        mem_util_blank_check(handle);
    }
}
