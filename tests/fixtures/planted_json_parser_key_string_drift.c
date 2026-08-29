/*
 * DELIBERATELY-VIOLATING fixture for
 * tests/test_json_key_parity.py (Phase 149 Plan 05, PGSZ-03, D-18's
 * cross-repo JSON-key parity gate).
 *
 * This file is a minimal, standalone, never-compiled C snippet. It is not
 * built by platformio.ini and is not referenced from any firmware target or
 * build_src_filter in either repository. It exists ONLY so the paired
 * pytest can point test_json_key_parity.py's module-level
 * FIRMWARE_PARSER_SOURCE path constant at it (via `monkeypatch.setattr` on
 * FIRMWARE_PARSER_SOURCE, never an edit to the real
 * firestarter/src/json_parser.c) and prove the gate actually fails on a
 * real firmware/host wire-key disagreement.
 *
 * It is a faithful copy of firestarter/src/json_parser.c's PROGMEM
 * key-string block and key_parsers[] dispatch table (json_parser.c:51-81),
 * with exactly ONE planted change: the page-size PROGMEM string below is
 * spelled with an UNDERSCORE ("page_size") instead of the wire's HYPHEN
 * ("page-size"). This reproduces Pitfall 10: the internal database key
 * (programming.page_size) and the wire key (page-size) differ by one
 * character, and a firmware string written against the wrong one dispatches
 * on a key the host never sends -- the host writes "page-size" on the wire,
 * this firmware would listen for "page_size", and the two never meet. The
 * key_parsers[] row for the (mis-spelled) identifier is otherwise unchanged
 * -- still dispatched under its own identifier -- so this fixture isolates
 * the key-STRING defect from the dispatch-TABLE defect the sibling fixture
 * (planted_json_parser_undispatched_key.c) reproduces.
 *
 * "Fixing" this file (i.e. changing "page_size" back to "page-size") would
 * silently hollow this gate's central key-string-parity detection leg -- the
 * defect class this gate exists to catch. Do NOT "fix" this file. If the
 * real firmware's key-string block ever changes shape, update this fixture
 * to match the new shape (keeping exactly ONE planted underscore/hyphen
 * drift), do not delete the violation.
 */

const char key_mem_size[] PROGMEM = "memory-size";
const char key_address[] PROGMEM = "address";
const char key_flags[] PROGMEM = "flags";
const char key_chip_id[] PROGMEM = "chip-id";
const char key_pin_count[] PROGMEM = "pin-count";
const char key_pulse_delay[] PROGMEM = "pulse-delay";
const char key_vpp_mv[] PROGMEM = "vpp_mv";
const char key_algorithm[] PROGMEM = "algorithm";
/* host-tunable read-timing knobs (D-04 sweep params) */
const char key_read_settling[] PROGMEM = "read-settling-delay";
const char key_read_strobe[]   PROGMEM = "read-strobe-us";
/* per-chip page-write size delivered by the host (PGSZ-01/PGSZ-02).
 * PLANTED VIOLATION -- real firmware spells this "page-size" (the wire's
 * hyphen form); this fixture spells it "page_size" (the internal database
 * key's underscore form), one character different from the wire. */
const char key_page_size[]     PROGMEM = "page_size";

typedef struct {
    PGM_P key;
    bool (*parser_func)(const char* json, jsmntok_t* tokens, int pos, firestarter_handle_t* handle);
} key_parser_t;

static const key_parser_t key_parsers[] PROGMEM = {
    {key_mem_size, get_memory_size}, {key_address, get_address},         {key_flags, get_flags},
    {key_chip_id, get_chip_id},      {key_pin_count, get_pin_count},     {key_pulse_delay, get_delay},
    {key_vpp_mv, get_vpp_mv},        {key_algorithm, get_algorithm},
    /* read-timing sweep knobs (RCA-01 causal proof, D-04) */
    {key_read_settling, get_read_settling},                              {key_read_strobe, get_read_strobe},
    /* page-size seam (PGSZ-01/PGSZ-02) */
    {key_page_size, get_page_size},
};
