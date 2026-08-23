/*
 * DELIBERATELY-VIOLATING fixture for
 * tests/test_dispatch_mirror.py (SWEEP-07's planted-violation control,
 * the fail-OPEN half, for the dispatch-mirror C++ leg
 * `test_dispatch_mirror_firmware_leg_enumerates_all_protocols`).
 *
 * This file is a minimal, standalone, never-compiled C++ snippet. It is not
 * built by platformio.ini and is not referenced from any firmware target or
 * build_src_filter in either repository. It exists ONLY so the paired
 * pytest can point test_dispatch_mirror.py's module-level `_FW_DISPATCH_TEST`
 * path constant at it (via `monkeypatch.setattr`, never an edit to the real
 * firestarter/test/native/avr/test_dispatch/test_configure_memory.cpp) and
 * prove the gate's extraction is comment-blind.
 *
 * It is a faithful copy of `planted_dispatch_missing_hex.cpp` (itself a
 * faithful copy of the four real `test_configure_memory.cpp` sites that
 * reference the flash_intel protocol's real hex identifier), with exactly
 * ONE further planted change: a single comment line is added that mentions
 * the real identifier written out as a literal `0x10`, while every actual
 * CODE site remains rewritten to `0xFF`, exactly as in the sibling fixture.
 *
 * THIS FIXTURE'S PAIRED TEST LEG asserts GREEN, not RED. Do NOT "fix" this file by
 * removing the comment or by wrapping the paired leg
 * (test_planted_comment_only_hex_is_NOT_detected) in `pytest.raises`. The
 * GREEN result IS the finding: `test_dispatch_mirror_firmware_leg_enumerates_all_protocols`
 * extracts every `0x[0-9A-Fa-f]+` token from the WHOLE file text via a bare
 * regex with no comment-awareness, so a comment-only mention of `0x10`
 * satisfies the gate exactly as well as a real dispatch case would. A
 * reader who "corrects" this fixture or its leg to expect RED destroys the
 * one committed proof that the gate cannot distinguish "a native dispatch
 * test exists for this protocol" from "a comment mentions this protocol".
 */

#include <Arduino.h>
#include <ArduinoFake.h>
#include <unity.h>

extern "C" {
#include "memory.h"
}
#include "firestarter.h"

static firestarter_handle_t make_handle(uint32_t protocol, uint8_t mem_type, uint8_t cmd) {
    (void)mem_type;
    firestarter_handle_t h = {};
    h.protocol = protocol;
    h.cmd = cmd;
    h.response_code = RESPONSE_CODE_OK;
    return h;
}

// PLANTED VIOLATION (SWEEP-07, fail-open control): historically this
// dispatch case covered protocol 0x10 (flash_intel); the code below has
// since been rewritten to 0xFF, but this comment alone is enough to
// satisfy the live gate's raw-token superset scan.
void test_protocol_0xFF_dispatches_flash_intel(void) {
    firestarter_handle_t h = make_handle(0xFF, 0, CMD_READ);
    configure_memory(&h);
    TEST_ASSERT_NOT_EQUAL(RESPONSE_CODE_ERROR, h.response_code);
}

struct protocol_family_row_t {
    uint32_t protocol;
    const char* family_name;
};

static const protocol_family_row_t kAllProtocolFamilies[] = {
    {0x07, "eprom (0x07/0x08/0x0B)"},
    {0x08, "eprom (0x07/0x08/0x0B)"},
    {0x0B, "eprom (0x07/0x08/0x0B)"},
    {0x0D, "eeprom28c (0x0D)"},
    {0xFF, "flash_intel (0xFF)"},
    {0x06, "flash_nor_unlock (0x06)"},
    {0x05, "flash_5v_page (0x05/0x35/0x39)"},
    {0x35, "flash_5v_page (0x05/0x35/0x39)"},
    {0x39, "flash_5v_page (0x05/0x35/0x39)"},
    {0x0E, "sram (0x0E/0x27/0x28/0x29)"},
    {0x27, "sram (0x0E/0x27/0x28/0x29)"},
    {0x28, "sram (0x0E/0x27/0x28/0x29)"},
    {0x29, "sram (0x0E/0x27/0x28/0x29)"},
};

void run_the_dispatch_suite(void) {
    RUN_TEST(test_protocol_0xFF_dispatches_flash_intel);
}
