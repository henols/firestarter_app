/*
 * DELIBERATELY-VIOLATING fixture for
 * tests/test_dispatch_mirror.py (SWEEP-07's planted-violation control,
 * the RED half, for the dispatch-mirror C++ leg
 * `test_dispatch_mirror_firmware_leg_enumerates_all_protocols`).
 *
 * This file is a minimal, standalone, never-compiled C++ snippet. It is not
 * built by platformio.ini and is not referenced from any firmware target or
 * build_src_filter in either repository. It exists ONLY so the paired
 * pytest can point test_dispatch_mirror.py's module-level `_FW_DISPATCH_TEST`
 * path constant at it (via `monkeypatch.setattr`, never an edit to the real
 * firestarter/test/native/avr/test_dispatch/test_configure_memory.cpp) and
 * prove the gate actually fails when a §0 protocol is absent from the
 * native dispatch test.
 *
 * It is a faithful copy of `test_configure_memory.cpp`'s full
 * `kAllProtocolFamilies` bucket table (every §0 protocol needing a positive
 * routing test) plus the four real sites that reference the flash_intel
 * protocol's real identifier -- hex `10`, conventionally spelled with a
 * leading `0x` -- (the make_handle call for the read-command dispatch
 * test, that test's own function name, its `kAllProtocolFamilies`
 * bucket-table row, and its RUN_TEST registration). The full table is
 * copied byte-faithful (every OTHER protocol's row is untouched) so this
 * fixture cannot accidentally satisfy the live gate's "missing" assertion
 * for a DIFFERENT protocol -- only the flash_intel row is planted. Exactly
 * ONE planted change: every one of the four flash_intel sites is
 * rewritten to reference `0xFF` instead, and this docstring deliberately
 * never spells the real identifier as one contiguous `0x`-prefixed token
 * anywhere in this file (not even here in the header) -- because
 * `test_dispatch_mirror_firmware_leg_enumerates_all_protocols` extracts
 * every `0x[0-9A-Fa-f]+` token from the WHOLE file text via a bare regex
 * with no comment-awareness, and a stray mention in this very docstring
 * would silently satisfy that regex and invalidate this fixture's entire
 * purpose. A clean rewrite with zero occurrences of the real token makes
 * that protocol genuinely absent from this fixture's token set, and the
 * live gate must report it missing.
 *
 * "Fixing" this file (i.e. restoring the real identifier as a `0x`-prefixed
 * token anywhere, including in a comment) would silently hollow this
 * gate's missing-hex detection leg (test_planted_missing_hex_is_detected).
 * Do NOT "fix" this file. If the real test_configure_memory.cpp's sites
 * for this protocol ever change shape, update this fixture to match the
 * new shape (keeping all four sites rewritten to `0xFF` and the real
 * identifier absent, including from comments), do not delete the
 * violation.
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
