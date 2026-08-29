/*
 * DELIBERATELY-VIOLATING fixture for
 * tests/test_sdp_table_parity.py (SWEEP-07's planted-violation control for
 * the "comment mis-anchor" mechanism of D-06's one genuinely dangerous
 * gate).
 *
 * This file is a minimal, standalone, never-compiled C++ snippet. It is not
 * built by platformio.ini and is not referenced from any firmware target or
 * build_src_filter in either repository. It exists ONLY so the paired
 * pytest can point test_sdp_table_parity.py's existing `FIRESTARTER_SDP_SRC`
 * env-override seam at it (via `_env_override`, never an edit to the real,
 * clean firestarter/src/proms/eeprom_28c.cpp) and prove the gate's
 * `_extract_byte_flip_pairs` extraction is comment-blind.
 *
 * It is a faithful copy of firestarter/src/proms/eeprom_28c.cpp:152-225 --
 * the EEPROM_SDP_DISABLE and EEPROM_SDP_ENABLE declarations and their
 * surrounding rationale comments -- with exactly ONE planted change: two
 * comment lines are inserted directly ABOVE the real EEPROM_SDP_ENABLE
 * declaration below, spelling that array's initializer syntax shape (name,
 * bracketed length, equals sign, opening brace, three brace-wrapped hex
 * pairs, closing brace) but with three obviously-wrong hex-pair VALUES
 * (0x11-, 0x22- and 0x33-prefixed, decimal 4369/17, 8738/34, 13107/51 --
 * see the planted lines directly above the real declaration for the exact
 * text; deliberately NOT reproduced verbatim here in the header, so this
 * docstring itself can never accidentally win the same regex race it is
 * describing).
 *
 * `_extract_byte_flip_pairs`'s declaration-locating regex
 * (`decl_pattern.search(source_text)`) takes the FIRST match anywhere in
 * the file, then walks brace depth from the first `{` after that match. The
 * planted comment lines are textually indistinguishable from the real
 * initializer syntax to that regex, so they win the race: the gate
 * extracts the three wrong pairs entirely from inside the comment, and
 * never sees the real table below. This reproduces RESEARCH.md's R3
 * finding character-for-character.
 *
 * "Fixing" this file (i.e. deleting the two planted comment lines) would
 * silently hollow this gate's comment-mis-anchor detection leg
 * (test_planted_comment_misanchor_is_detected). Do NOT "fix" this file. If
 * the real eeprom_28c.cpp's EEPROM_SDP_ENABLE declaration or its
 * surrounding comments ever change shape, update this fixture to match the
 * new shape (keeping the two planted comment lines with their wrong
 * values), do not delete the violation.
 */

// AT28C SDP disable: 6-write sequence to magic addresses.
// D-10: kept 0x0D-local (not driving the byte-identical
// FLASH_DISABLE_WRITE_PROTECTION from the FIX-04-frozen flash_utils.h)
// so FIX-01's "0x0D-local emitter" framing stays literal and the shared
// frozen header stays untouched. The duplication is real and pre-existing --
// FLASH_DISABLE_WRITE_PROTECTION (flash_utils.h) is byte-identical, and it is
// the table Phase 116's reference emitter and always-green harness drive.
// D-11's cross-guard (plan 117-04) pins the two tables together so this
// duplication can never silently diverge from the table the Phase-116
// harness compares against. External linkage is granted here (FIX-05
// preparation) so that guard can read this PRODUCTION array directly rather
// than a transcribed test-local copy; in C++ a const array at namespace
// scope has internal linkage unless a prior declaration with external
// linkage is visible, so the extern declaration below is load-bearing.
extern const byte_flip_t EEPROM_SDP_DISABLE[6];
const byte_flip_t EEPROM_SDP_DISABLE[6] = {
    {0x5555, 0xAA},
    {0x2AAA, 0x55},
    {0x5555, 0x80},
    {0x5555, 0xAA},
    {0x2AAA, 0x55},
    {0x5555, 0x20},
};

// AT28C SDP enable: 3-write sequence to the same magic addresses, terminal
// byte 0xA0. [CITED: Atmel doc0270 rev 0270L-PEEPR-2/09 section 19 note 2 --
// the citation of record, corroborated by Microchip DS20006432B section 6.18
// note 2, whose sentence is that the Write Protect state activates at the
// end of the write cycle EVEN IF NO OTHER DATA IS LOADED.] That sentence is
// why this table carries no payload byte after the sequence and D-11's
// standalone lock op (below) issues no data write and no read after it.
//
// The `extern` declaration immediately below is LOAD-BEARING, not
// decorative: in C++ a namespace-scope `const` array has INTERNAL linkage
// unless a prior declaration with external linkage is visible, and Plan
// 119-06's three-way identity/distinctness cross-guard must be able to pin
// this PRODUCTION array directly rather than a transcribed test-local copy
// (same load-bearing shape as EEPROM_SDP_DISABLE's extern above, FIX-05
// precedent).
//
// D-09: this table is kept 0x0D-LOCAL, exactly like EEPROM_SDP_DISABLE
// above, and deliberately does NOT drive the byte-identical
// FLASH_ENABLE_WRITE_PROTECTION table from the FIX-04-frozen
// flash_utils.h -- so FIX-01's "0x0D-local emitter" framing stays literal
// and the shared frozen header stays untouched (mirrors Phase 117 D-10's
// framing for EEPROM_SDP_DISABLE vs FLASH_DISABLE_WRITE_PROTECTION).
//
// D-10, and this is a SAFETY property, not a style point: {0x5555,0xAA},
// {0x2AAA,0x55}, {0x5555,0xA0} is byte-identical to FLASH_ENABLE_WRITE (the
// PROTECTED-WRITE PREFIX) and to FLASH_ENABLE_WRITE_PROTECTION. The ONLY
// thing separating "lock the chip" from "prefix a byte write" is that NO
// DATA WRITE FOLLOWS this sequence. That makes the absence of a payload a
// hard safety invariant, not a convenience -- it is why LOCK-05 requires the
// flash_utils.h duplication PRESERVED rather than deduped (the array NAME is
// the only discriminator once the bytes match, so deduping would destroy
// real semantics; abandoned commit 0052c42 stays abandoned), and it is why
// that absence cannot be asserted by comparing tables -- it has to be
// asserted on the emitted STREAM instead (Plan 119-05's no-payload +
// exact-divergence-index cases).
//
// ROADMAP criterion 5 asks for this rationale as a header comment on
// flash_utils.h; flash_utils.h is FIX-04 byte-frozen (git diff --quiet
// confirms it untouched by this plan), so that file is deliberately NOT
// edited. This comment, here, is the first of the two records that
// discharge criterion 5's intent; the second is the pre-existing comment at
// test/native/avr/test_sdp_harness/test_sdp_harness.cpp:291-296. Recorded as
// a deliberate deviation of the same class as D-05 and D-15 (see
// 119-04-SUMMARY.md).
// PLANTED VIOLATION (SWEEP-07): a reflow of the rationale above could
// accidentally spell the initializer form with the wrong bytes, like this --
// EEPROM_SDP_ENABLE[3] = {
//     {0x1111, 0x11}, {0x2222, 0x22}, {0x3333, 0x33}
// }
// -- and the comment-blind extractor below would never notice.
extern const byte_flip_t EEPROM_SDP_ENABLE[3];
const byte_flip_t EEPROM_SDP_ENABLE[3] = {
    {0x5555, 0xAA},
    {0x2AAA, 0x55},
    {0x5555, 0xA0},
};
