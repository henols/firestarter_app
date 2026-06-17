# Validation Matrix — SRAM Family (Tier 3)

Generated: 2026-06-17T14:03:00Z  
Harness version: 71

## Tier-3 HIL Results

| Family | Board | Tier | Verdict | Pass Type | Evidence SHA | Retry Count |
|--------|-------|------|---------|-----------|--------------|-------------|
| sram | leonardo | 3 | PASS | authoritative | 1ae62b3110141bf43af6a7a14875442afaea8460122b814e36466febf39ca654 | 2 |

## Notes

- **VAL-06 = table-stakes-PASS** (D-09 hard gate SATISFIED — definitive verdict)
- Chip: FM1608 (FRAM, 8KB, algorithm 0x28, DIP28_JEDEC_SRAM_8K)
- Method: Two-pattern write+read-back (D-06/D-07/D-08)
  - Pattern A: 0x5A repeating (8192 bytes) — Run 1: 0 mismatches; Run 2: 0 mismatches
  - Pattern B: 0xA5 repeating (8192 bytes) — Run 1: 0 mismatches; Run 2: 0 mismatches
  - All 4 round trips: ZERO mismatches across all 8192 bytes
- Evidence SHA: sha256(pattern_a.bin) = 1ae62b3110141bf43af6a7a14875442afaea8460122b814e36466febf39ca654
- retry_count = 2 (N≥2 per D-07: two full write+read-back cycles per pattern)
- Pass type: authoritative (Leonardo board per D-14)
- Negative control: verify FM1608 against baseline after pattern A write → exit 1 (FAIL) — oracle non-vacuous
- Erase probe: firestarter erase FM1608 → exit 1 (Not supported) — erase path confirmed not viable; write -b path used (Pitfall 3)
- Parked byte-0 FRAM bug: NOT triggered (byte 0 matched in all 4 runs)
- **FIX-01 (SRAM real read/write) → CLOSED NOT-NEEDED with evidence**
  configure_sram does write via generic_memory_write_execute; Phase 74 FIX-01 is not needed

## Pre-Write Gate (D-11/D-03)

- controller: leonardo (CONFIRMED)
- Shield: Rev 2.0-class (CONFIRMED)
- R1: 270000 (within [202500, 337500] band)
- Firmware: 3.0.0b8
