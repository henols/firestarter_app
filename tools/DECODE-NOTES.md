<p align="left"><img src="https://raw.githubusercontent.com/henols/firestarter/main/images/branding/firestarter_logo_horizontal.png" alt="Firestarter EPROM Programmer" width="400"></p>

# DECODE-NOTES.md: how `build_db.py` decodes `infoic.xml`

This document records how `tools/build_db.py` decodes the fields of the minipro `infoic.xml` chip
catalog. For each field, it gives the decode rule and the upstream source that supports the rule.
It covers the `variant` field (low byte and high byte), `pulse_delay`, the `voltages` word, and
the VPP rails that the RURP shield can supply.

A `[VERIFIED: minipro database.c#Lxxx @ <SHA>]` marker cites one upstream source line at one
upstream commit. `build_db.py` and `diff_db.py` use the same marker.

---

## 0. The pinned upstream commit

`build_db.py` downloads the chip catalog from minipro on GitLab. `MINIPRO_XML_URL` pins the
download to one minipro commit:

```
a8efaedc236c1d9718bd28299dfbb99536b010ff
```

The short form is **`a8efaedc`**. All `[VERIFIED: ... @ a8efaedc]` markers in `build_db.py`,
`diff_db.py` and this document refer to this commit.

This repository has no copy of `infoic.xml`. The pinned SHA is what makes a regeneration
reproducible. The `diff_db.py` gate runs against the same upstream snapshot.

---

## 1. LOW byte: `variant & 0xFF` (pinout-family sub-discriminator)

`resolve_pinout_key` in `build_db.py` uses the variant **low** byte (`variant_lo`) to select the
pinout family inside one physical layout cluster (`pm_idx`).

`pm_idx` is the low byte of `pin_map`. For the 32-pin cluster, `pin_map` is `0x600C`, so `pm_idx`
is `0x0C` (12). `resolve_pinout_key` switches on `pm_idx`, not on the full `pin_map`.

These are the `variant_lo` values that `resolve_pinout_key` switches on:

| `pm_idx` | `variant_lo` | resolved pinout | note |
|----------|--------------|------------------|------|
| 23 (24-pin) | `0x01` | `DIP24_2732` | 4KB UV-EPROM |
| 23 (24-pin) | `0x10` | `DIP24_2816` | 28C-family 5V EEPROM. This is the reliable 28C discriminator, because many 28C parts have `flags=0x0000`. |
| 23 (24-pin) | else (`0x00`) | `DIP24_2716` | 2KB UV-EPROM |
| 22 (28-pin) | `0x10` | `DIP28_27512` | **VPP on pin 22** (OE/VPP shared) |
| 22 (28-pin) | `0x11` | `DIP28_27256` | **VPP on pin 1** |
| 22 (28-pin) | else | `DIP28_2764` | 27C128 / 27C64 layout |
| 12 (32-pin) | `0x03` | `DIP32_27C801` | 1 MB 27C080 / M27C801 class. **A19 on pin 1**, VPP on pin 24 shared with /OE. |
| 12 (32-pin) | `0x02` | `DIP32_STD` | 512 KB 27C040 class. Pin 1 = VPP, pin 31 = A18. |
| 12 (32-pin) | else | `DIP32_27C020` through the residual `_PGM_ON_PIN31_MAX_SIZE` size arm | The `0x00`, `0x01` and `0x04` classes. The size threshold still separates them (see below). |

**The 32-pin `proto_id == 0x08` cluster.** An earlier rule ignored `variant_lo` in this cluster and
used a `mem_size` threshold. That rule put every chip above 256 KB on `DIP32_STD` with
`vpp-pin: [1]`. Eight 1 MB rows got the wrong pinout: `AM27C080`, `AM27LV080`, `AT27C080`,
`M27C801` (two rows), `MX27C8000`, `MX27C8000A` and `UPD27C8001`. On that class, pin 1 is A19, not
VPP.

The current rule forks on `variant_lo` inside the `proto_id == 0x08` branch, the same as the 24-pin
and 28-pin arms. The `mem_size` threshold stays only as the residual fall-through arm.
`SST37VF040` (`pm_idx` 13, `variant_lo` 0x04, 524288 bytes) must stay on `DIP32_STD`, and a pure
`variant_lo` ladder would move it. A run of the current rule over all 767 filtered `infoic.xml` rows
changed exactly 8 rows: the eight parts named above.

**Critical:** never swap `0x10 → DIP28_27512` (VPP pin 22) and `0x11 → DIP28_27256` (VPP pin 1).
A swap puts 12 V on the wrong pin and can damage the chip.

**Critical:** keep the 32-pin `variant_lo` fork **inside** the `proto_id == 0x08` test. Protocol
`0x10` rows at `pm_idx` 10 and 13 have `variant_lo` values `0x10` through `0x13`. If the fork moves
above the protocol test, Intel flash parts get the wrong layout.

---

## 2. HIGH byte: `variant >> 8` (minipro T56/T76 `algo_number`), NOT a classifier

### 2.1 The source-grounded truth

The variant **high** byte is minipro's **`algo_number`**. It selects one FPGA algorithm file for
the **T56/T76** programmers, inside one protocol. Minipro uses it at exactly one place:

```c
// minipro src/database.c#L1918 — function get_algorithm()
uint8_t algo_number = (uint8_t)(device->variant >> 8);
...
// database.c#L1953-L1981
snprintf(algo_str, sizeof(algo_str), "%02X", algo_number);
char *name = stpcpy(algorithm->name, entry);   // entry = algo_table[protocol_id-1]
strcat(name, algo_str);                          // e.g. "ROM28P" + "41" => "ROM28P41"
```

`[VERIFIED: minipro database.c#L1918-L1985 @ a8efaedc —
https://gitlab.com/DavidGriffith/minipro/-/raw/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c]`

- `algo_table` (`database.c#L334`) uses **`protocol_id`** as its index, NOT the variant high byte.
  The high byte is a **hex suffix** on the algorithm name of the protocol. The suffix selects one
  algorithm or bitstream file for the T56/T76 programmer firmware.
- On the TL866II and RURP path, minipro does **not** read the high byte for classification,
  electrical type, pinout or VPP. `variant` occurs at exactly two sites in `database.c`: L585
  (parse) and L1918 (the `>>8` above).

**Conclusion:** the high byte selects a programmer algorithm file. It is **not** a memory-class
code. `build_db.py` does **NOT** derive `electrical.type`, `algorithm` or `pinout` from it.
Classification uses **`type`**, **`protocol_id`**, **`pm_idx`** and **`flags`**. These are the
fields that minipro itself uses to classify a device.

### 2.2 Full high-byte census (659 unique DIP-parallel chips, INFOIC2PLUS filter)

`[VERIFIED: full local parse of infoic.xml @ a8efaedc, with the build_db.py filter
(24–32 pin, no SMD, no serial, type∈{1,4})]`

| variant hi | count | (type, protocol_id) it occurs with | algo_table family (proto) |
|-----------|-------|----------------------------------------|---------------------------|
| 0x11 | 73 | (1,0x08) | ROM32P |
| 0x12 | 4 | (1,0x08) | ROM32P |
| 0x13 | 7 | (1,0x08) | ROM32P |
| 0x14 | 27 | (1,0x08) | ROM32P |
| 0x31 | 41 | (1,0x07) ×40, (1,0x34) ×1 (X88C64P) | ROM28P / GEN_ |
| 0x32 | 56 | (1,0x07) | ROM28P |
| 0x33 | 44 | (1,0x07) | ROM28P |
| 0x34 | 9 | (1,0x04), DataFlash, skipped by the KNOWN_PROTOCOLS gate | AT45D |
| 0x37 | 3 | (1,0x07) | ROM28P |
| 0x3A | 12 | (1,0x0B) | ROM24P |
| 0x3B | 10 | (1,0x0B) | ROM24P |
| 0x41 | 69 | (1,0x07) ×45, (4,0x07) ×14, (4,0x28) ×10 | ROM28P / ROM28P_2 |
| 0x43 | 24 | (1,0x0B) ×19, (4,0x0B) ×3, (4,0x27) ×2 | ROM24P |
| 0x44 | 17 | (1,0x0D) | EE28C32P |
| 0x50 | 24 | (4,0x0E) ×12, (4,0x29) ×12 | RAM32 |
| 0x51 | 12 | (4,0x0E) ×4, (4,0x29) ×8 | RAM32 |
| 0x70 | 39 | (1,0x06) | W29F32P |
| 0x71 | 119 | (1,0x06) | W29F32P |
| 0x75 | 25 | (1,0x05) | F29EE |
| 0x77 | 2 | (1,0x05) | F29EE |
| 0x79 | 2 | (1,0x10) | 28F32P |
| 0x7A | 23 | (1,0x10) | 28F32P |
| 0x7B | 4 | (1,0x10) | 28F32P |
| 0x7C | 2 | (1,0x10) | 28F32P |
| 0x80 | 3 | (1,0x10) | 28F32P |
| 0x93 | 2 | (1,0x11), FWH, not possible on RURP, skipped | FWH |
| 0xE2 | 6 | (1,0x08) ×5, (4,0x07) ×1 (M48T08) | ROM32P |

**How to read the table:** each high-byte value stays inside one protocol family (the algo_table
column). The value is a sub-variant inside a protocol (the `algo_number` suffix). It is NOT a type,
algorithm or pinout code that applies across protocols. The pattern in the table comes from the
protocol family, not from a separate meaning.

### 2.3 The two collision cells (why the high byte cannot be the classifier)

These two cells are the FM1608 and X88C64 cases. They show that classification cannot use the high
byte:

- **`hi = 0x41`** holds `(1,0x07)` real 28C EEPROMs **and** `(4,0x07)` FRAM/NVRAM parts. Both have
  the same variant `0x4126`. FM1608 (type=4) and AT28C64 (type=1) have **the same `variant`**. The
  discriminator is **`type`** (4 or 1).
- **`hi = 0x31`** is the 27512 EPROM family (variant `0x3110`). X88C64P is the only `(1,0x34)`
  entry, with variant `0x3100`. **`protocol_id`** (0x34) separates it from the EPROMs, not the high
  byte.

**Do not branch `classify()` on `variant >> 8`.** That rule fits by coincidence. It fails as soon
as FM1608 and AT28C64 (shared `0x4126`) need a different `electrical.type`. Use
`type`/`proto`/`pm_idx`/`flags`.

---

## 3. The URL pin in `build_db.py`

- The pinned SHA `a8efaedc236c1d9718bd28299dfbb99536b010ff` (§0) is the provenance of each
  regeneration.
- `MINIPRO_XML_URL` in `build_db.py` uses `/-/raw/a8efaedc236c1d9718bd28299dfbb99536b010ff/`, not
  `/-/raw/master/`. The download is therefore deterministic.

---

## 4. X88C64 fix rationale (`proto_id == 0x34` → `electrical.type = EEPROM`)

**Ground-truth tuple** `[VERIFIED: infoic.xml INFOIC2PLUS @ a8efaedc]`:

```
X88C64P: type=1  protocol_id=0x34  variant=0x3100  flags=0x00414200
         voltages=0x0200  pin_map=0x9000e600 (pm_idx=0)  size=0x2000
```

- `flags & 0x10 == 0`, so the flags-based "electrically erasable" EEPROM rule does not catch it.
  Without the fix, the row falls through to the `UV-EPROM` default, with `algorithm = 52` (0x34)
  and `support_status = "protocol-not-implemented"`.
- **The fix:** `classify()` gives `protocol_id == 0x34` (XICOR NovRAM/EEPROM) the value
  `electrical.type = "EEPROM"`. This changes the displayed classification only.
- X88C64P stays `support_status = "protocol-not-implemented"`. It stays **non-dispatchable**. The
  generator writes no wire dict and no serial byte for it, and the host guard
  `chip_resolver.resolve_chip` refuses it.
- A 0x34 programming handler needs a PCB change. It is not part of this fix.
- X88C64P is the **only** `0x34` DIP-parallel chip in scope, so the change affects this one record
  only.

---

## 5. FM1608 identity (the row reads SRAM)

**Ground-truth tuple** `[VERIFIED: infoic.xml INFOIC2PLUS @ a8efaedc]`:

```
FM1608: type=4  protocol_id=0x07  variant=0x4126  flags=0x00000000
        voltages=0x0100  pin_map=0 (pm_idx=0)  size=0x2000
```

`type == 4` (`MP_SRAM`, `[VERIFIED: minipro minipro.h#L70 @ a8efaedc]`) is the signal for the
FRAM/NVRAM class. The variant is **not** that signal. For type=4 chips, the classifier writes
`algorithm = 0x28` (decimal 40, `SRAM_STD`) and pinout `DIP28_JEDEC_SRAM_8K`.

The row reads `electrical.type: "SRAM"`, not `"FRAM"`:

- An earlier cosmetic `SRAM → FRAM` relabel for this row is gone.
- Each host site treats `SRAM` and `FRAM` the same.
- The Ramtron SRAM-class siblings of FM1608 (`FM1208`, `FM16W08`, `FM1808`, `FM18L08`) read `SRAM`.

Because the row is `SRAM`, it gets the SRAM single-rail rewrite in `build_db.py`:

```python
if _etype == "SRAM": chip_entry["electrical"]["vcc_mv"] = chip_entry["electrical"]["vdd_mv"]
```

`vcc_mv` is therefore 5000, not the raw decoded 3300. This agrees with `vdd_mv` and with the
Ramtron siblings.

Old notes sometimes say "FM1608 0x40". That mixes decimal 40 and hex 0x28. The true identity is
`proto 0x07 + type 4 + variant 0x4126`. The classifier uses `type` and writes
`algorithm = 40 (0x28)`.

---

## 6. Gaps (documented, never guessed)

- **No high-byte value is a classification gap.** `database.c#L1918` resolves each high-byte value
  in the §2.2 census as the `algo_number` (T56/T76 algorithm-file suffix). Classification does not
  need any of them, because `electrical.type`, `algorithm` and `pinout` come from
  `type`/`proto`/`pm_idx`/`flags`. No high-byte value is a guess.
- **2516 and 2532 are NOT a decode gap.** They are real 24-pin UV-EPROMs that `infoic.xml` does not
  list at all. This is a different problem from a field that the generator cannot decode.
- `tools/extra_chips.json` supplies them. The file uses the manufacturer as its key. It has one
  record each for 2516 and 2532. Each record has a `source: "non-upstream-supplement"` marker and a datasheet
  citation.
- `build_db.py` merges `EXTRA_CHIPS_FILE` into `complete_db` in `main()`, **after** the
  `infoic.xml` decode loop and **before** the JSON write. The supplement records do not go through
  `classify()` or `resolve_pinout_key`. They arrive fully specified.
- 2516 is `verification_status: "UNVERIFIED"`. Its wire values do not change, and it is
  `support_status: supported`, so `read` and `info` can resolve it. The host guards stay the same.
  No write on real hardware proves it.
- 2532 uses the non-JEDEC `DIP24_2532` pinout (VPP = pin 21). This is different from `DIP24_2732`.

---

## 7. Sources

- minipro `src/database.c` @ a8efaedc: `variant>>8 = algo_number` (L1918), `algo_table` (L334),
  variant parse (L585).
  https://gitlab.com/DavidGriffith/minipro/-/raw/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c
- minipro `src/minipro.h` @ a8efaedc: `MP_MEMORY=0x01 … MP_SRAM=0x04` (L67-74).
- `infoic.xml` @ a8efaedc: INFOIC2PLUS survey of 659 unique DIP-parallel chips, and the FM1608 and
  X88C64P ground-truth tuples.

---

## 8. `pulse_delay` unit

**Result: raw `pulse_delay` is microseconds for all protocols.** Also, `infoic.xml` often holds a
family default, not a value for each part.

**What `interpret_timing` does** `[VERIFIED: build_db.py interpret_timing()]`:

- It parses raw `pulse_delay` as hex and returns it as microseconds, with no multiplier, for
  `proto_id in (0x07, 0x08, 0x0B)`.
- It returns `0` for all other `proto_id` values, including `0x0D`. The test is exact equality, not
  a range.
- The caller passes the `proto_id` that `classify()` returns. `classify()` promotes some 5V-EEPROM
  rows to `0x0D`. Such a row ships `pulse_duration_us: 0`, not its raw upstream value. The raw
  `pulse_delay` field does not change. Only the branch of `interpret_timing` changes.

**The positive match.** `FUJITSU/MBM27C4001` has raw `pulse_delay=0x0064` (100). Its datasheet
(page 8, AC CHARACTERISTICS) gives Programming Pulse Width `tPW` min 95, typ **100**, max 105 µs.
The front page says *"Fast programming: 0.1ms pulse"*. Raw `0x0064` read as 100 µs is exactly the
typical value, in the unit of the datasheet.

**The value is not per part.** The pinned `infoic.xml` has **675** `<ic>` entries with `proto_id`
0x07 or 0x08, before the DIP filter. Of these, **462** (68.4%) have raw `pulse_delay=0x0064` (100).
Two Fujitsu parts share that raw value but have different datasheet values:

- `FUJITSU/MBM27C4001`: 100 µs (see above).
- `FUJITSU/MBM27C1001`: 500 µs, from the AC CHARACTERISTICS table of its datasheet.

One raw input cannot decode to two different values for two parts. The 462 rows therefore carry a
family default from upstream, not a measurement for each part.

**No single multiplier fits.** The raw values go from `0x0001` (1) to `0x2710` (10000), four orders
of magnitude. A multiplier that maps 100 → 500 (×5) also maps 10000 → 50000 and 1 → 5. No EPROM
datasheet gives a 5 µs program pulse. Plain microseconds is the only reading that fits all values,
and `MBM27C4001` confirms it exactly.

**What `pulse_duration_us` means on the wire** `[VERIFIED: firestarter_fw src/proms/eprom.cpp,
src/proms/eprom_params.cpp, include/eprom_params.h]`. This tells you which datasheet number to
record when a datasheet gives two:

- The firmware runs a loop that verifies after each pulse. It does not send one fixed pulse.
- `handle->pulse_delay` is the program pulse width for each byte, in µs. The firmware applies it up
  to `max_pulses` times (25 for protocols `0x07`/`0x08`), with a verify after each pulse.
- `overprogram_factor` is **0** in the parameter rows of `0x07`, `0x08` and `0x0B`. No target ever
  gets an over-program margin pulse.
- Record the **initial** programming pulse width (`tPW`) of the fast algorithm. Do not record a
  conventional single-shot width.
- For `FUJITSU/MBM27128`, that is the Quick Pro value of 1000 µs (`TPW = 1 ms ± 50 µs`, datasheet
  Figure 3). It is not the conventional 50 ms single-shot pulse on the page before it.
- A correct value still gives an incomplete fast algorithm. The firmware does not implement the
  `tOPW` over-program pulse of the datasheet, whatever value `pulse_duration_us` has.

**Empty input fails closed.** A missing or unparseable `pulse_delay` raises an error. It does not
default to `0`. The message is:

```
chip with protocol {protocol_id:#04x} has unparseable pulse_delay {raw_hex!r} — refusing to default to 0 us
```

No shipped row reaches this branch against the pinned `infoic.xml`. The only test of the branch is
`tests/test_build_db_interpret_timing.py`, with synthetic input.

Related entries in `tools/datasheet_overrides.json`: `FUJITSU/MBM27128`, `FUJITSU/MBM27C1000`,
`FUJITSU/MBM27C1001`.

---

## 9. The voltage word: the two nibbles and the VPP byte select a programmer rail index, not a chip requirement

**Result:** the two nibbles and the VPP byte of the `voltages` word select a programmer rail
index. They do not give a chip requirement. The decoded value names the slot in the internal DAC
table of the programmer model that drives the pin. It does not copy the datasheet value of the part.

### 9.1 What the decode does

`[VERIFIED: build_db.py, the _d_vpp_mv / _d_vcc_mv / _d_vdd_mv assignment immediately after
classify()]`. Three lookups read the same 16-bit `voltages` word:

- `_d_vpp_mv` reads the low byte. It first tries an exact match on the two keys with a non-zero low
  nibble, `0xF1` and `0xF2`. Then it masks with `& 0xF0` and looks up `VPP_MV`, with default `0`.
- `_d_vcc_mv` reads bits 11-8. It looks up `VCC_VOLTAGES`, with default `5000`.
- `_d_vdd_mv` reads bits 15-12. It looks up `VCC_VOLTAGES`, with default `5000`.

Each table has a `[VERIFIED: minipro <file>#<lines> @ a8efaedc — <array>[]]` marker. The marker
names the upstream C array that the table copies.

### 9.2 The two tables

**`VPP_MV`** comes from the upstream `xg_vpp_voltages[]` `[VERIFIED: database.c#L161-L170 @
a8efaedc]`:

- It is a strict superset of `tl866ii_vpp_voltages[]`, with no conflict.
- The sixteen indices that `tl866ii_vpp_voltages[]` has are the same in both tables, byte for byte.
- `xg_vpp_voltages[]` adds exactly `0xF1` (25000 mV) and `0xF2` (21000 mV).
- The decode matches these two on the full low byte before the `& 0xF0` mask, so they do not
  collapse onto `0xF0`.

**`VCC_VOLTAGES`** comes from `xg_vcc_voltages[]` `[VERIFIED: database.c#L182-L190 @ a8efaedc]`:

- It is a strict superset of `tl866ii_vcc_voltages[]`, with no conflict.
- The six indices `0x00`-`0x05` are the same in both tables, byte for byte.
- `xg_vcc_voltages[]` adds nine more (`0x06`=1800 … `0x0E`=6250).

Both tables complete one encoding. Different upstream tables implement it to different depths. The
decode never uses `tl866a_*`. That is a different encoding. It conflicts on 4 of 6 shared VCC
indices and on 8 of 8 shared VPP indices.

**A wrong citation, now removed.** `VCC_VOLTAGES` and the `_VCC_MARGIN_RAIL_MV` block below it
carried the same marker, `[VERIFIED: minipro database.c#L130-L135 @ a8efaedc —
tl866ii_vcc_voltages[]]`. At the pinned SHA, lines 130-135 are `tl866a_vpp_voltages[]` and the
start of `tl866a_vcc_voltages[]`. `tl866ii_vcc_voltages[]` is at L154-L159, and
`xg_vcc_voltages[]` is at L182-L190. Both copies named the wrong table and the wrong lines. Both are
deleted. This section holds the correct provenance.

### 9.3 The twelve rows at vdd index `0x06`

Twelve rows have voltage word `0x64xx`: seven EXEL, three ST and two SGS-THOMSON 28C-class parts.
With the completed table they decode to 1800 mV. A 1.8 V program rail is not credible for a 5 V
28C-class parallel EEPROM. These twelve rows therefore keep `vdd_mv: 5000`. Twelve explicit
`UNSOURCED` entries in `tools/datasheet_overrides.json` hold that value. Index `0x06` stays in the
table.

### 9.4 The evidence

**The positive match.** Three Fujitsu datasheets give a program VCC of 6.0 V ± 0.25 V: `MBM27128`,
`MBM27C1001` and `MBM27C4001`.

- All three rows decode to vdd index `0x04`. `VCC_VOLTAGES` maps that index to 5500 mV.
- `tools/datasheet_overrides.json` now holds each row at the datasheet value, 6000 mV.
- The model rail at index `0x05` (6500 mV) is nearer to the datasheet band than index `0x04`.
  This shows again that the index selects a model slot and does not follow the datasheet value.

**The rival reading fails: the index is not a chip requirement.** The same index gives different
volts on different programmer models:

- VPP index `0x00` is 12 V on the TL866-II (`tl866ii_vpp_voltages[]`) and 12.5 V on the TL866A
  (`tl866a_vpp_voltages[]`).
- VPP index `0x20` is 9.5 V on the TL866-II and 21 V on the TL866A.

A chip requirement cannot change with the programmer model. Only a slot in a model table can. The
upstream comment says this `[VERIFIED: database.c#L125-L126 @ a8efaedc]`:

> "These are not raw DAC output values, but rather indices into internal lookup tables
> defined in the firmware."

Cite lines 125-126. Line 123 of the same comment block reads
`* The Vcc and Vpp settings are linked through the 'voltages'`. It is not the quoted sentence.

**The arithmetic.** The band of the three Fujitsu datasheets is 5.75-6.25 V. The two nearest model
rails are `VCC_VOLTAGES[0x04]` = 5500 and `VCC_VOLTAGES[0x05]` = 6500. Neither 5.5 V nor 6.5 V is
inside 5.75-6.25 V. The field cannot hold the datasheet value, so it does not record it. It records
the nearest rail that the model can drive.

**Saturation at the model maximum.** 28 rows have VPP low byte `0xF0`, which decodes to 18000 mV.
That was the largest `VPP_MV` entry before the table was completed, and it is the TL866-II VPP
maximum. The group includes all eight NMOS 2716/2732 rows and `MBM27128`. A community report
(issue #71) measured that `MBM27128` needs 21 V. 28 rows sit on the model maximum, and some need
more. The field records what the model can select, and it clamps there.

**The edge case and the dead branch.** `xg_vpp_voltages[]` has two indices above `0xF0`:
`0xF1` = 25000 and `0xF2` = 21000.

- These are the same two values as the `is` values of the six `UNSOURCED` NMOS entries in
  `tools/datasheet_overrides.json`. Those entries came from a different source.
- No filtered row has `0xF1` or `0xF2` today. A regeneration with the completed table gives the
  same database, byte for byte.
- No test can reach either branch until upstream adds an `infoic.xml` row with one of these low
  bytes. The code is real and reachable, but no chip reaches it.
- This is a false lead: a 25 V or 21 V value that the table can decode. The saturation pattern above
  is the real evidence for the result of this section.

**The silent fallback.** All three lookups use `.get(idx, default)`. The generator uses the default
for an unmapped index and does not stop the build. No check reports a new unmapped index. Today, no
row in the 767-row filtered population reaches a default. A later `infoic.xml` update with a new
index would use the same fallback, with no message.

**The `vdd < vcc` predicate.** Decoded vdd below decoded vcc selects exactly 28 rows. 373 rows
have the two equal, and 366 rows have vdd greater. 28 + 373 + 366 = 767, the full filtered
population. `build_db.py` does not assert this predicate at build time.

### 9.5 Limits

- **The datasheet evidence is n = 3, all from Fujitsu.** This document makes no claim that the
  Fujitsu 6.0 V ± 0.25 V value applies to other manufacturers at the same index.
- **This document makes no claim about the 167 other rows at vdd index `0x04`.**
  `VCC_VOLTAGES[0x04]` = 5500 is the index of the three Fujitsu rows before their override. 167 more rows decode to that index.
  Nobody has examined them yet. Each needs its own datasheet.
- **The result does not say that the value of a row is correct.** It says what the field is: a
  programmer rail index. It does not say that a decoded or overridden voltage is right for a part.
- **The option-flag reading of the low nibble is a reading of this generator, not of upstream.**
  No named constant in `minipro.h` or `database.c` defines the low nibble of the VPP byte as flags.
  The only support is the `0x00`/`0x01` and `0x70`/`0x71` pairs in the low-byte census: one rail
  index with the low nibble clear and set.
- Upstream names two powerdown bits: `LAST_JEDEC_BIT_IS_POWERDOWN_ENABLE` and
  `POWERDOWN_MODE_DISABLE` `[VERIFIED: minipro.h#L84-L85 @ a8efaedc]`. They are at bits 12 and 13,
  in the **vdd** nibble, not in the VPP byte. Their comment limits them to the ATF20V10C/ATF16V8C
  PLD variants, which the `type in {1, 4}` filter excludes. The same bits mean different things in
  different part families. This is a second example of the result of this section.

Sources: `tools/datasheet_overrides.json` entries `FUJITSU/MBM27128`, `FUJITSU/MBM27C1001`,
`FUJITSU/MBM27C4001` and the twelve `0x06` entries. `database.c#L125-L126`, `database.c#L161-L170`,
`database.c#L182-L190` and `minipro.h#L84-L85`, all @ a8efaedc.

---

## 10. What the rails deliver, and the firmware routing decision

**Result:** `RURP_VPP_CEILING_MV = 25000` stays the refusal gate of the generator. It is a
theoretical regulator value. The shield delivers a smaller voltage at the socket. That measured
value is a routing ceiling in the firmware, not in the generator.

### 10.1 The generator ceiling

`RURP_VPP_CEILING_MV = 25000` is in `tools/build_db.py`. The generator compares each decoded
`vpp_mv` against it with strict greater-than. The six rows at exactly 25000 therefore stay
`supported`.

A measured maximum near 16–18 V would change all thirty rows in §10.5 to `vpp-exceeds-max`.
`chip_resolver.py` turns any row that is not `supported` into a `ChipNotImplementedError`, which is a
silent refusal. The generator therefore keeps 25000 and has no second constant. The routing ceiling
is in the firmware (§10.3).

### 10.2 What was measured

One bench session, 2026-09-19, on one shield:

- The operator identified the shield by its silkscreen as **Rev 2.0**. `hw_revision` cannot tell
  Rev 2.0, **Rev 2.2** and the modified **Rev 0** apart. The identification comes from the
  operator, not from an instrument.
- The socket was empty. The pot was at maximum and did not move between readings. The meter read
  **socket pin 1** against board ground.
- `0x188` (`REGULATOR | VPE-DROP | P1`, the standard drop-resistor path) gave **17380 mV**.
- `0x088` (`REGULATOR | P1`, drop bit clear, VPE directly to pin 1) gave **22140 mV**.

The firmware held the rail with its own `while (!rurp_user_button_pressed()) delay(200);` loop at the
end of `dt_set_registers()`. The host disconnected before the loop started. No `firestarter`
command ran while a rail was held. A method that keeps the serial port open does not work: when the
port closes, DTR drops and the board resets, and the rail goes off before the reading.

**The monitor of the programmer.** Two one-second sampling windows ran, with the hidden `-t`
option: `firestarter vpp -t 1` and `firestarter vpe -t 1`. The rule, fixed before the windows
opened, was: take the last frame, unless the last three frames differ by more than one 100 mV step. Each window gave two identical frames.

- Drop-resistor monitor: **18700 mV** against the meter value of 17380 mV. Difference +1320 mV,
  **+7.59 %**.
- Direct-VPE monitor: **23900 mV** against the meter value of 22140 mV. Difference +1760 mV,
  **+7.95 %**.

Both values are inside the separately measured ratiometric ADC error of about 7.5 % (range
6.8–8.3 %). They agree with that figure but do not replace it. The operating rule stays: set a pot
target from a meter reading, never from the firmware value.

### 10.3 The routing decision in the firmware

`eprom_hv_route_mask` in `firestarter_fw/src/proms/eprom.cpp` makes the decision:

1. If `FLAG_VPE_AS_VPP` is set, it wins, with no table read. This is the manual override for the
   25 V NMOS parts and the manual-pot workflow.
2. An unresolved protocol fails closed toward the drop path.
3. Otherwise the function reads the `vpp_path` column of the protocol.
   `firestarter_fw/src/proms/eprom_params.cpp` gives `VPP_PATH_DROP_RESISTOR` to protocols `0x07`
   and `0x08`, and `VPP_PATH_DIRECT_VPE` to `0x0B`. A `VPP_PATH_DIRECT_VPE` row routes the
   undropped rail.
4. A `VPP_PATH_DROP_RESISTOR` row whose required `vpp_mv` is strictly greater than
   `RURP_VPP_DROP_PATH_MAX_DELIVERABLE_MV` also routes the undropped rail.

`eprom_check_vpp` calls `eprom_hv_route_mask` before it reads a voltage. Its acceptance window
therefore follows the routed rail.

The decision is in the firmware, and no host rule sets it. The host and the firmware ship on
different channels. A host at an old version, or with a threshold from a different shield, could
send a high-voltage rail to the wrong socket pin. No host module for this decision exists.

**Why the trigger is path capability, not the live reading.** `eprom_check_vpp` sends
`MSG_WARN_VPP_LOW` when the rail reads below 95 % of the required voltage. With the bench values,
a rule on the reading would miss almost all of the rows that need the new route:

- Required 18000 mV: the 95 % threshold is 17100 mV. The drop-path monitor reads 18700 mV, above
  the threshold, so there is no warning. The socket gets 17380 mV, which is 620 mV short.
- Required 21000 mV: the threshold is 19950 mV. The 18700 mV reading is below it, so the warning
  comes. This is the one row (`FUJITSU/MBM27128`) whose shortfall (3620 mV) is larger than the
  monitor error of about +7.6 %.

A trigger on the ADC reading would miss nine of the ten drop-resistor rows in §10.5. The routing
therefore uses what the path can deliver at any pot setting. The ADC acceptance check does not
change. It calls `eprom_hv_route_mask`, so it checks the rail that the routing selected. A better
ADC calibration improves only the check. The routing does not depend on it.

### 10.4 A ceiling, not a threshold

The undropped route does not deliver a fixed voltage. It delivers the voltage that the pot sets.
`RURP_VPP_DROP_PATH_MAX_DELIVERABLE_MV` does not mean "a routed part gets 17380 mV". It means that
no pot setting makes the drop-resistor path go above 17380 mV. A part that needs more must use the
direct-VPE route.

One pot sets both rails. A part on the undropped rail still needs the pot near its own required
voltage. The firmware VPP window (−5 % / +500 mV) decides if the voltage is correct. If the pot is
not near enough, the first attempt on a newly routed part can stop with a hard error.

Before this change, `FLAG_VPE_AS_VPP` was the only route to the undropped rail other than the
`vpp_path` column. The comments in `eprom.cpp` (the resolution order of `eprom_hv_route_mask`) and
in `include/eprom.h` said so. Both comments now describe the ceiling comparison. The `eprom.h`
declaration says that the route comes from the `vpp_path` column "though not from that column
alone."

### 10.5 The classification

The filter is every row with `electrical.vpp_mv` at 18000 mV or more. The map is from
`programming.algorithm` to path, with the table in `eprom_params.cpp`. The result agrees with
`tests/test_vpp_rail_classification.py`:

- **30 rows**: **21 at 18000 mV, 3 at 21000 mV, 6 at 25000 mV**. All are `support_status: supported`.
- Ten rows are on the drop-resistor path: algorithm `0x07`, pinout `DIP28_2764`, `vpp-pin: [1]`.
- Twenty rows are on the direct-VPE path: algorithm `0x0B`, pinout
  `DIP24_2716`/`DIP24_2732`/`DIP24_2532`, `vpp-pin: [21]`.
- The measured drop-resistor ceiling of 17380 mV reaches **none** of the thirty. The nine rows at
  18000 mV are 620 mV short. The tenth (`FUJITSU/MBM27128`, 21000 mV) is 3620 mV short.
- With the routing change, all thirty use the direct-VPE route. The measured direct value of
  22140 mV at socket pin 1 covers all ten former drop-resistor rows. The smallest margin is 1140 mV
  (`FUJITSU/MBM27128` at 21000 mV).

| Path | Required VPP | Manufacturer | Part Number | Algorithm | Pinout | Reason |
|---|---|---|---|---|---|---|
| drop-resistor | 21000 mV | FUJITSU | MBM27128 | 0x07 | DIP28_2764 | Changed 2026-09-19 from its datasheet (Figure 3), 18000 → 21000, after issue #71. |
| drop-resistor | 18000 mV | FUJITSU | MBM27C128P | 0x07 | DIP28_2764 | Upstream decode. VPP low byte `0xF0` saturates at the largest table entry (18000 mV), the model maximum. Not a per-part value. |
| drop-resistor | 18000 mV | FUJITSU | MBM27C64 | 0x07 | DIP28_2764 | Upstream decode, `0xF0` saturation, as above. |
| drop-resistor | 18000 mV | HITACHI | HN27C64G | 0x07 | DIP28_2764 | Upstream decode, `0xF0` saturation, as above. |
| drop-resistor | 18000 mV | HITACHI | HN27C64FP | 0x07 | DIP28_2764 | Upstream decode, `0xF0` saturation, as above. |
| drop-resistor | 18000 mV | INTEL | 2764 | 0x07 | DIP28_2764 | Upstream decode, `0xF0` saturation, as above. |
| drop-resistor | 18000 mV | INTEL | 27128,D27128 | 0x07 | DIP28_2764 | Upstream decode, `0xF0` saturation, as above. |
| drop-resistor | 18000 mV | MITSUBISHI | M5M27C128 | 0x07 | DIP28_2764 | Upstream decode, `0xF0` saturation, as above. |
| drop-resistor | 18000 mV | NEC | UPD2764,UPD2764C,UPD2764D | 0x07 | DIP28_2764 | Upstream decode, `0xF0` saturation, as above. |
| drop-resistor | 18000 mV | TI | TMS2764 | 0x07 | DIP28_2764 | Upstream decode, `0xF0` saturation, as above. |
| direct-vpe | 25000 mV | INTEL | 2732,2732A,M2732,M2732A | 0x0B | DIP24_2732 | An `UNSOURCED` entry in `tools/datasheet_overrides.json` holds it. No datasheet citation. |
| direct-vpe | 25000 mV | INTEL | M2716,M2716M | 0x0B | DIP24_2716 | An `UNSOURCED` override entry holds it. No datasheet citation. |
| direct-vpe | 25000 mV | SGS-THOMSON | ETC2716,M2716 | 0x0B | DIP24_2716 | An `UNSOURCED` override entry holds it. No datasheet citation. |
| direct-vpe | 25000 mV | ST | ETC2716,M2716 | 0x0B | DIP24_2716 | An `UNSOURCED` override entry holds it. No datasheet citation. |
| direct-vpe | 25000 mV | TEXAS INSTRUMENTS | 2516 | 0x0B | DIP24_2716 | Hardcoded in the supplement (`tools/extra_chips.json`), `UNVERIFIED`, no write proof. Not from the decode or the override file. |
| direct-vpe | 25000 mV | TEXAS INSTRUMENTS | 2532 | 0x0B | DIP24_2532 | Hardcoded in the supplement (`tools/extra_chips.json`), `UNVERIFIED`, no write proof. Not from the decode or the override file. |
| direct-vpe | 21000 mV | SGS-THOMSON | M2732A | 0x0B | DIP24_2732 | An `UNSOURCED` override entry holds it. No datasheet citation. |
| direct-vpe | 21000 mV | ST | M2732A | 0x0B | DIP24_2732 | An `UNSOURCED` override entry holds it. No datasheet citation. |
| direct-vpe | 18000 mV | AMD | AM2716 | 0x0B | DIP24_2716 | Upstream decode, `0xF0` saturation, as above. |
| direct-vpe | 18000 mV | AMD | AM2732,AM2732A | 0x0B | DIP24_2732 | Upstream decode, `0xF0` saturation, as above. |
| direct-vpe | 18000 mV | FAIRCHILD | NMC27C16 | 0x0B | DIP24_2716 | Upstream decode, `0xF0` saturation, as above. |
| direct-vpe | 18000 mV | FAIRCHILD | NMC27C16Q | 0x0B | DIP24_2716 | Upstream decode, `0xF0` saturation, as above. |
| direct-vpe | 18000 mV | FAIRCHILD | NMC27C32,NMC27C32E,NMC27C32EH,NMC27C32H,NMC27C32Q | 0x0B | DIP24_2732 | Upstream decode, `0xF0` saturation, as above. |
| direct-vpe | 18000 mV | FUJITSU | MBM2732,MBM2732A,MBM27C32,MBM27C32A | 0x0B | DIP24_2732 | Upstream decode, `0xF0` saturation, as above. |
| direct-vpe | 18000 mV | NSC | NMC27C16 | 0x0B | DIP24_2716 | Upstream decode, `0xF0` saturation, as above. |
| direct-vpe | 18000 mV | NSC | NMC27C16Q | 0x0B | DIP24_2716 | Upstream decode, `0xF0` saturation, as above. |
| direct-vpe | 18000 mV | SGS-THOMSON | ETC2732 | 0x0B | DIP24_2732 | Upstream decode, `0xF0` saturation, as above. |
| direct-vpe | 18000 mV | ST | ETC2732 | 0x0B | DIP24_2732 | Upstream decode, `0xF0` saturation, as above. |
| direct-vpe | 18000 mV | TI | TMS2716 | 0x0B | DIP24_2716 | Upstream decode, `0xF0` saturation, as above. |
| direct-vpe | 18000 mV | TI | TMS2732A | 0x0B | DIP24_2732 | Upstream decode, `0xF0` saturation, as above. |

The shipped constant, in `firestarter_fw/include/rurp_pinout.h`:

```
RURP_VPP_DROP_PATH_MAX_DELIVERABLE_MV = 17380
```

In words: the measured maximum of the drop-resistor path is **17.38 V** at socket pin 1. This is
for one Rev 2.0 shield, with the pot at maximum and the socket empty, in the bench session of
2026-09-19.

### 10.6 Limits

- **One shield, one session, one pot setting.** These values are for this Rev 2.0 board at maximum
  pot. They are not a specification, a fleet value or a tolerance band. Nobody measured the
  **Rev 2.2** board, and this document does not assume that it is the same.
- The modified **Rev 0** cannot give a monitor value: `hw_read_voltage` refuses on Rev 0, and
  `eprom_check_vpp` returns early there before it reads a voltage.
- **The monitor-to-meter difference combines instrument error and switch-path error.** This session
  cannot separate the two. The monitor commands set no socket-routing bit, but the meter reads at
  the socket through one. The separately measured ratiometric figure (about 7.5 %, range
  6.8–8.3 %) is the only value for the instrument alone. Both values here are inside that range.
  The operating rule stays: set a pot target from a meter reading, never from the firmware value.
- **The monitor wire format has whole volts and one tenths digit.** Each firmware value is on a
  100 mV grid, whatever its accuracy.
- **Nobody measured the direct-VPE-to-pin-21 destination.** The twenty direct-VPE rows get VPE
  through `CTRL_VPE_ENABLE` to **pin 21**. This is a different pin from the socket-pin-1 value that
  this section uses. `CTRL_VPE_ENABLE` and `CTRL_VPP_P1_ENABLE` route to different pins. The
  comparison is an approximation. Nothing here shows that the six rows at 25000 mV are out of reach.
- **The classification test copies a firmware table that the host cannot read.** It finds a change
  on the database side only. If the firmware moves an algorithm to a different rail, this test still
  passes.
- **The two Texas Instruments supplement rows at a hardcoded 25000 mV are `UNVERIFIED`**, with no
  write proof. They do not come from the `infoic.xml` decode or from a datasheet-cited override.
- **The shortfall warning gap.** If a part needs more than the routed rail delivers, the only
  operator signal is the low-VPP warning of the firmware (`MSG_WARN_VPP_LOW`, `eprom_check_vpp`).
  The warning names the measured rail and the required voltage, and the operation continues. Its
  trigger is a 5 % window on a reading that is about 7.6 % high. A real shortfall smaller than that
  combined margin can pass with no warning.
- **Nobody wrote to any of the thirty parts.** This section makes no claim that any of them
  now programs. It records what a rail delivers and what the firmware decides. It does not record a
  write result.

Sources: `firestarter_app/tools/build_db.py` (`RURP_VPP_CEILING_MV`),
`firestarter_fw/include/rurp_pinout.h` (`RURP_VPP_DROP_PATH_MAX_DELIVERABLE_MV`),
`firestarter_fw/src/proms/eprom.cpp` (`eprom_hv_route_mask`, `eprom_check_vpp`),
`firestarter_fw/include/eprom.h`, `firestarter_fw/src/proms/eprom_params.cpp` (the `vpp_path`
table for each protocol), `firestarter_app/tests/test_vpp_rail_classification.py` and
`firestarter_app/firestarter/diagnostic_report.py` (`_RAIL_READING_DISCLOSURE`).
