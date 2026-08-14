/*
 * DELIBERATELY-VIOLATING fixture for
 * tests/test_cap03_ack_layout_parity.py (Phase 144 Plan 02, TEST-07,
 * D-17/D-18's cross-repo CAP-03 byte-layout parity gate).
 *
 * This file is a minimal, standalone, never-compiled C++ snippet. It is not
 * built by platformio.ini and is not referenced from any firmware target or
 * build_src_filter in either repository. It exists ONLY so the paired
 * pytest can point test_cap03_ack_layout_parity.py's module-level
 * FIRMWARE_ACK_SOURCE path constant at it (via `monkeypatch.setattr` on
 * FIRMWARE_ACK_SOURCE, never an edit to the real
 * firestarter/src/firestarter.cpp) and prove the gate actually fails on a
 * real firmware/host wire-layout disagreement.
 *
 * It is a faithful copy of firestarter/src/firestarter.cpp's `_ready` pack
 * region (the wire-layout comment plus the block from the `_ready`
 * declaration through the LOG_OK_ID_BYTES emit, firestarter.cpp:166-208),
 * with exactly ONE planted change: the CAP-03 budget below is written at
 * the LITERAL indices 13 and 14, not the real firmware's COMPUTED
 * `4 + _vlen` / `4 + _vlen + 1`. This is BF-1's exact shape: a wire-layout
 * change on one side of a two-repo protocol with nothing comparing the two
 * sides -- a literal index happens to work only for the one firmware
 * version-string length that puts `4 + _vlen` at 13, and silently misreads
 * the budget on every other board name / version-string length.
 *
 * This fixture deliberately does NOT trip:
 *   - the emitted-length leg (test_emitted_length_includes_the_two_budget_bytes)
 *     -- the emit call below still reads `(uint8_t)(4 + _vlen + 2)`, unchanged
 *   - the index-identity leg for bytes 0-3
 *     (test_firmware_and_host_agree_on_indices_zero_through_three) -- bytes
 *     0-3 are untouched
 *   - the big-endian leg (test_both_sides_use_big_endian_for_both_u16_fields)
 *     -- both `>> 8` / `& 0xFF` shapes are still present, just at the wrong
 *     indices
 *   - the pack-order-comment leg
 *     (test_firmware_pack_order_comment_matches_the_wire_layout) -- the
 *     wire-layout comment string below is untouched
 * A fixture that failed for two reasons at once could not prove which check
 * fired -- this isolation is the whole point of using two separate fixtures
 * instead of one two-drift fixture.
 *
 * "Fixing" this file (i.e. changing 13/14 back to `4 + _vlen` /
 * `4 + _vlen + 1`) would silently hollow this gate's central computed-offset
 * detection leg -- the CAP-03 defect class this gate exists to catch. Do
 * NOT "fix" this file. If the real firmware's `_ready` pack region ever
 * changes shape, update this fixture to match the new shape (keeping
 * exactly one planted literal-index drift), do not delete the violation.
 */

    // Wire layout, three length-discriminated extensions of one variable
    // blob:
    //   [buffer_size u16 BE][hw_revision u8][ver_len u8][ver bytes][write_budget_s u16 BE]
    //      CAP-01              CAP-02                                CAP-03

    {
        const char* _ver = FW_VERSION;
        uint8_t _vlen = (uint8_t)strlen(_ver);
        if (_vlen > 32) _vlen = 32;
        uint8_t _ready[4 + 32 + 2];
        _ready[0] = (uint8_t)(((uint16_t)DATA_BUFFER_SIZE >> 8) & 0xFF);
        _ready[1] = (uint8_t)((uint16_t)DATA_BUFFER_SIZE & 0xFF);
#ifdef HARDWARE_REVISION
        _ready[2] = (uint8_t)rurp_get_hardware_revision();
#else
        _ready[2] = 0xFE;  // REVISION_UNKNOWN -- the symbol lives inside that same #ifdef
#endif
        _ready[3] = _vlen;
        memcpy(_ready + 4, _ver, _vlen);
        uint16_t _budget = eprom_block_budget_s(handle->protocol, handle->pulse_delay,
                                                 (uint32_t)DATA_BUFFER_SIZE);
        _ready[13]     = (uint8_t)((_budget >> 8) & 0xFF);  /* PLANTED VIOLATION -- real firmware writes _ready[4 + _vlen] */
        _ready[14] = (uint8_t)(_budget & 0xFF);  /* PLANTED VIOLATION -- real firmware writes _ready[4 + _vlen + 1] */
        LOG_OK_ID_BYTES(MSG_OK_READY, _ready, (uint8_t)(4 + _vlen + 2));
    }
