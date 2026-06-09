# Pinout Safety Review — Phase 58

This document summarises the safety guarantees established by the Phase 58 principled
pinout re-derivation (`resolve_pinout_key` rewrite) and the new `DIP24_2816` pinout entry.

**Full audit trail:** `.planning/phases/58-pinout-re-derivation-24-pin-eeprom-unblock/58-SR-1-CHECKLIST.md`
(meta-repo, investigation-canonical).

---

## Safety Guarantee: DIP24_2816 is 5V-only, no VPP path

`DIP24_2816` is the pinout entry for 24-pin 5V single-supply parallel EEPROMs
(AT28C04/AT28C16/AM28C16A/CAT28C16A family). Its `pinouts.json` entry **has no `vpp-pin`
field** — firmware never asserts the VPP regulator for chips on this pinout.

Key pin assignments:
- Pin 21 = WE# (`rw-pin`) — Write Enable. **Not VPP.**
- Pin 20 = OE# (`oe-pin`)
- Pin 18 = CE# (`ce-pin`)
- Pin 24 = VCC, Pin 12 = GND

Compare: `DIP24_2716` (UV-EPROM layout) has `vpp-pin: [21]`, meaning pin 21 is VPP (12V
during programming). `DIP24_2816` uses the same physical DIP-24 package but pin 21 is WE#
at 5V. These two pinouts **must remain separate named entries** to prevent misrouting.

Chips routed to `DIP24_2816` use algorithm `0x0D` (`configure_eeprom28c`): 5V page-write
with SDP-disable and DQ7 polling. No VPP regulator engagement at any point.

---

## Safety Guarantee: GATE-03 enforces 0 violations across the full chip set

`tools/check_dispatch.py` (GATE-03) verifies that no chip with `electrical.type=Flash/EEPROM`
routes to `configure_eprom` (the VPP-asserting handler).

**Result as of 2026-06-09 (743 chips):**
```
PASS: all 743 chips have a valid dispatch path; 0 SRAM chips route to configure_eprom;
0 DIP28_2764 Flash/EEPROM chips route to configure_eprom;
0 Flash/EEPROM chips route to configure_eprom; 0 wire-key regressions
```

GATE-03 is pinout-agnostic — it loads `pinouts.json` dynamically and covers any new
pinout added in the future without code changes.

---

## Safety Guarantee: configure_eeprom28c is 5V-safe and pin-count-agnostic

The firmware handler dispatched by `algorithm=0x0D` (`configure_eeprom28c`,
`firestarter/src/proms/eeprom_28c.cpp`) operates entirely at 5V VCC. It does not
call `P1_VPP_ENABLE` or any other VPP-enable macro at any point in its code path.
It works for both 24-pin and 28-pin chips via `handle->mem_size` — no pin-count
hardcoding.

---

## What Changed in Phase 58

| Category | Before Phase 58 | After Phase 58 |
|----------|-----------------|----------------|
| 9 AT28C04/16-family chips | Blocked (safety-skip in build_db.py) | Unblocked via DIP24_2816 + algo=0x0D |
| 10 AM28C16A/CAT28C16A-class chips | In DB with algo=0x0B (configure_eprom), DIP24_2716 — 12V on WE# | Fixed: DIP24_2816 + algo=0x0D, 5V-only |
| 12 AT28C256/CAT28C256-class chips | In DB with algo=0x07 (configure_eprom) | Fixed: algo=0x0D (configure_eeprom28c) |
| Total chips in DB | 734 | 743 (+9 unblocked) |
| GATE-03 violations | 0 (DIP24_2716 chips missed by old flags guard) | 0 (all 743 covered) |

---

## Deferred: BENCH-01 Real-Hardware Validation

Source-correctness is established by the principled re-derivation and GATE-03. Real-hardware
write/program validation of the newly unblocked AT28C04/16-family chips is deferred to a future
milestone (BENCH-01 per REQUIREMENTS.md). The chips are safe to use once hardware validation
is complete.

---

## Source Citation

Pinout discriminators are derived from minipro's `infoic.xml` and `database.c`/`.h`.
Minipro source pinned to SHA `a8efaedc`:
`https://gitlab.com/DavidGriffith/minipro/-/commit/a8efaedc`

The `variant_lo=0x10` discriminator for 24-pin EEPROM family is verified against infoic.xml:
all `(pm_idx=23, variant_lo=0x10)` chips in that file are confirmed members of the 28C EEPROM
family. [VERIFIED: infoic.xml @ a8efaedc]
