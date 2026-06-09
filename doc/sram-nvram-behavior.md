# SRAM / NVRAM Behavior — Phase 59

This document describes the behavioral characteristics of NVRAM and FRAM devices supported
by Firestarter via the SRAM protocol set (`algorithm` values `0x0E`, `0x27`, `0x28`, `0x29`).
These are operator-facing facts you must know before programming battery-backed NVRAM or
timekeeper SRAM parts.

**Full audit trail:** `.planning/phases/59-correctness-gate-per-chip-diff-sram-audit/59-SRAM-AUDIT.md`
(meta-repo, investigation-canonical).

---

## Blank-Check Limitation

NVRAM and FRAM devices **never contain factory-blank data** (all 0xFF). Unlike UV-EPROMs,
they cannot be bulk-erased — data is retained indefinitely by battery backup or ferroelectric
polarization and can only be overwritten byte-by-byte.

Affected families:

- **Battery-backed SRAM / NVRAM:** DS1225, DS1230, DS1245, BQ4010, BQ4011
- **Timekeeper NVRAM:** M48T02, M48T08, M48T35
- **FRAM (Ferroelectric RAM):** FM1608, FM16W08, FM1808

Key facts:

- If `FLAG_SKIP_BLANK_CHECK` (flag `-b`) is **not set**, the write path runs a blank-check
  before writing. For a non-blank NVRAM chip this check will **always fail** and abort the
  write.
- This is **not a defect** — it is correct behavior. The chip is not blank; blank-check is
  telling the truth.
- Always pass `-b` (or the equivalent `FLAG_SKIP_BLANK_CHECK` flag) when writing any NVRAM
  or FRAM part. The write will overwrite existing data without erasing first.

[CITED: .planning/research/PITFALLS.md §E-3]

---

## Write-Protect (WP#) Behavior

Two distinct write-protect mechanisms exist across the SRAM/NVRAM families supported by
Firestarter. Neither represents a hardware-damage path — the worst case is a silent write
failure.

### DS1225 class (hardware WP# pin)

Parts: DS1225, DS1230 (8K/32K battery-backed SRAM, DIP28)

- Write-protect is controlled by a **hardware pin** (WP#, pin 26 on the DS1225).
- WP# is **internally pulled low by default**, so writes are enabled without any external
  connection. [ASSUMED — DS1225 standard datasheet claim; risk LOW]
- If WP# is held high externally (e.g., by board wiring), writes will fail silently.
- The RURP firmware does not drive the WP# pin — write-protect state is passive from
  firmware's perspective.

### M48T08 class (software control-register bit)

Parts: M48T02, M48T08, M48T35 (timekeeper SRAM, DIP28/DIP32)

- Write-protect is **bit 7 of the control register byte** in the chip's data space
  (e.g., address `0x1FF8` for M48T08). [ASSUMED — M48T08 standard datasheet claim; risk LOW]
- This is a **software-accessible bit**, not a hardware pin.
- The firmware's generic SRAM write path does **not** clear this bit automatically.
- If the control byte has WP bit 7 set, writes to the rest of the array will fail silently.
- To program an M48T08-class timekeeper: ensure the memory image you write includes a
  cleared control byte (bit 7 = 0) at the appropriate address, or pre-clear the control
  register before programming.

Key facts:

- Both WP mechanisms result in **silent write failure** (data not written), not hardware damage.
- Neither WP mechanism involves VPP or any high-voltage path.
- No firmware change is needed for either class — these are operator awareness items.

[CITED: .planning/research/PITFALLS.md §E-3]

---

## RTC Oscillator Side Effect

Parts affected: M48T08, M48T35, and timekeeper NVRAM devices containing a 32.768 kHz
oscillator and RTC counter.

Timekeeper NVRAMs contain a built-in **32.768 kHz crystal oscillator** and **RTC counter**
that run whenever VCC is applied — including during RURP read and write operations.

Key facts:

- The RTC clock **advances** during any programming operation.
- This is **not a hardware-damage path** — the oscillator is designed to run continuously.
- It is a **state-change side effect**: if the RTC was previously set, the time will have
  advanced by the duration of the programming operation.
- After programming a timekeeper NVRAM, **re-set the RTC time** if accurate timekeeping
  is required.
- This side effect applies to both read and write operations (a read also advances the clock).

[CITED: .planning/research/PITFALLS.md §E-3]

---

## Safety

The firmware handler dispatched for all SRAM/NVRAM protocols (`0x0E`, `0x27`, `0x28`, `0x29`)
is `configure_sram` in `firestarter/src/proms/sram.cpp`. This function is a near-no-op: it
emits a debug log message and delegates all bus I/O to the generic read/write callbacks. It
asserts **no VPP** and enables **no voltage regulator**.

`check_dispatch.py` (GATE-03 / BLOCKER-2) confirms that 0 of 743 chips with SRAM protocols
route to `configure_eprom` (the VPP-asserting handler) across the full chip database.

The blank-check limitation, WP# behavior, and RTC-oscillator side effect documented above are
**operator considerations**, not hardware-damage paths. No firmware change was made in v1.11
for the SRAM/NVRAM path — `configure_sram` as a near-no-op is correct for the JEDEC SRAM
byte-write use case.
