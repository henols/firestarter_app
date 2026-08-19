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
 * spelled CORRECTLY ("page-size", the wire's hyphen form), but its
 * key_parsers[] row is ABSENT. This reproduces the declared-but-unwired
 * hole: the string exists, a naive presence check on the PROGMEM
 * declaration alone would pass, and the key is never dispatched at
 * runtime -- json_parse's dispatch loop only ever consults key_parsers[],
 * so an undeclared-in-the-table key is silently treated as an unknown
 * field and skipped, exactly like a typo would be. This fixture isolates
 * the dispatch-TABLE defect from the key-STRING defect the sibling
 * fixture (planted_json_parser_key_string_drift.c) reproduces.
 *
 * "Fixing" this file (i.e. adding the key_parsers[] row for key_page_size
 * back) would silently hollow this gate's central dispatch-detection leg --
 * the defect class this gate exists to catch. Do NOT "fix" this file. If
 * the real firmware's key-string block or dispatch table ever changes
 * shape, update this fixture to match the new shape (keeping exactly ONE
 * planted omitted row), do not delete the violation.
 */

const char key_mem_size[] PROGMEM = "memory-size";
const char key_address[] PROGMEM = "address";
const char key_flags[] PROGMEM = "flags";
const char key_chip_id[] PROGMEM = "chip-id";
const char key_pin_count[] PROGMEM = "pin-count";
const char key_pulse_delay[] PROGMEM = "pulse-delay";
const char key_vpp_mv[] PROGMEM = "vpp_mv";
const char key_algorithm[] PROGMEM = "algorithm";
/* Phase 44 -- host-tunable read-timing knobs (D-04 sweep params) */
const char key_read_settling[] PROGMEM = "read-settling-delay";
const char key_read_strobe[]   PROGMEM = "read-strobe-us";
/* Phase 149 -- per-chip page-write size delivered by the host (PGSZ-01/PGSZ-02).
 * Wire key is the HYPHEN form "page-size" -- spelled correctly here. The
 * PLANTED VIOLATION is below: this identifier's key_parsers[] row is
 * omitted entirely, so this correctly-spelled string is never dispatched. */
const char key_page_size[]     PROGMEM = "page-size";

typedef struct {
    PGM_P key;
    bool (*parser_func)(const char* json, jsmntok_t* tokens, int pos, firestarter_handle_t* handle);
} key_parser_t;

/* PLANTED VIOLATION -- the real firmware's table below has one more row,
 * for the page-size identifier declared above (Phase 149 -- page-size
 * seam, PGSZ-01/PGSZ-02). This fixture omits that row entirely: the
 * PROGMEM string above is declared but never dispatched. The omitted
 * row's identifier is deliberately NOT named inside the initializer body
 * below (not even in a comment), since this gate's dispatch-identifier
 * extractor scans that body's raw text and naming it there would
 * self-satisfy the very check this fixture exists to fail. */
static const key_parser_t key_parsers[] PROGMEM = {
    {key_mem_size, get_memory_size}, {key_address, get_address},         {key_flags, get_flags},
    {key_chip_id, get_chip_id},      {key_pin_count, get_pin_count},     {key_pulse_delay, get_delay},
    {key_vpp_mv, get_vpp_mv},        {key_algorithm, get_algorithm},
    /* Phase 44 -- read-timing sweep knobs (RCA-01 causal proof, D-04) */
    {key_read_settling, get_read_settling},                              {key_read_strobe, get_read_strobe},
};
