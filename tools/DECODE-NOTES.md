# DECODE-NOTES.md — infoic.xml `variant` Field Decode (VAR-01)

**Phase:** 86 — infoic.xml Variant-Field Decode + Correct DB Regen
**Plan:** 86-01 (documentation + Wave-0 test oracle)
**Authored:** 2026-06-25
**Scope:** documents the minipro `infoic.xml` `variant` field **in full** (low byte +
high byte) so `build_db.py`'s classifier can be re-derived in Plan 86-02 from
*principled, source-grounded* decode instead of the hand-maintained Rule 1 / Rule 2 /
Rule 3 override stack.

> This document records the decode and its source grounding. It does **not** change
> `build_db.py` — Plan 86-02 applies the classifier rewrite and any `MINIPRO_XML_URL`
> pin (see §3). The `[VERIFIED: minipro database.c#Lxxx @ <SHA>]` citation idiom used
> throughout matches the convention already embedded in `build_db.py` and `diff_db.py`.

---

## 0. Pinned upstream reproducibility (the SHA this regen is grounded on)

`build_db.py` fetches the chip catalog **live** from
`https://gitlab.com/DavidGriffith/minipro/-/raw/master/infoic.xml`
(`MINIPRO_XML_URL`, `build_db.py` L11) — there is **no vendored `tools/infoic.xml`**.
For the Phase-86 regen to be reproducible, the upstream `master` commit used must be
recorded.

**Resolved minipro `master` commit (this regen):**

```
a8efaedc236c1d9718bd28299dfbb99536b010ff
```

Resolved via `git ls-remote https://gitlab.com/DavidGriffith/minipro.git master`
on 2026-06-25. Short form: **`a8efaedc`** — which is the *same* commit the existing
`[VERIFIED: ... @ a8efaedc]` citations in `build_db.py`/`diff_db.py` already pin, so
the Phase-86 decode and the prior v1.11 decode share a single upstream reference.

**Provenance decision (per D-05 discretion):** the pinned SHA is recorded **here** as
the regen provenance of record. Plan 86-02 MAY additionally pin `MINIPRO_XML_URL` to
this SHA (i.e. swap `/-/raw/master/` → `/-/raw/a8efaedc236c1d9718bd28299dfbb99536b010ff/`)
so the fetch is deterministic; if it does, it cites this section. Either way the
load-bearing reproducibility artifact is this recorded SHA — the `diff_db.py` gate
(D-07) re-runs against the same upstream snapshot.

---

## 1. LOW byte — `variant & 0xFF` (pinout-family sub-discriminator)

The variant **low** byte is consumed by `resolve_pinout_key` (`build_db.py`
~L193-283) as the pinout-family sub-discriminator *within* a physical layout
cluster (`pm_idx`).

**Updated 2026-09-10 (Phase 182 Plan 02, D-02):** the 32-pin `proto_id == 0x08`
cluster was, until this plan, the **one place** `resolve_pinout_key` abandoned
`variant_lo` and substituted a hand-tuned `mem_size` threshold — the sole
exception to this section's otherwise-uniform rule. That threshold routed every
32-pin `proto_id == 0x08` chip above 256 KB onto `DIP32_STD`, so all eight 1 MB
rows (`AM27C080`, `AM27LV080`, `AT27C080`, `M27C801` ×2, `MX27C8000`,
`MX27C8000A`, `UPD27C8001`) landed on `DIP32_STD` with `vpp-pin: [1]` even
though pin 1 on that class is A19, not VPP. The corrected rule forks on
`variant_lo` inside the `proto_id == 0x08` branch exactly as the 24-pin and
28-pin arms already do; the `mem_size` threshold survives only as the residual
fall-through arm, because `SST37VF040` (`pm_idx` 13, `variant_lo` 0x04, 524288
bytes) must stay on `DIP32_STD` and a pure `variant_lo` ladder would move it.
The corrected rule was run over all 767 filtered infoic rows: the measured
blast radius is exactly 8 rows — the same eight parts named above. The
sentence that used to stand here, claiming `resolve_pinout_key` was left
byte-for-byte unedited this phase, is retracted; that claim is no longer true
for the 32-pin cluster.

**Correction to CONTEXT's D-02 table:** `pin_map` for the 32-pin cluster is
`0x600C`, not `0x000c` as CONTEXT's D-02 table states — `0x000c` is the low
byte only (`pm_idx = 0x0C = 12`). `pm_idx` is what `resolve_pinout_key`
switches on; the correction does not change any resolved key.

The concrete `variant_lo` values it switches on:

| `pm_idx` | `variant_lo` | resolved pinout | note |
|----------|--------------|------------------|------|
| 23 (24-pin) | `0x01` | `DIP24_2732` | 4KB UV-EPROM |
| 23 (24-pin) | `0x10` | `DIP24_2816` | 28C-family 5V EEPROM (reliable 28C discriminator — many 28C parts have `flags=0x0000`) |
| 23 (24-pin) | else (`0x00`) | `DIP24_2716` | 2KB UV-EPROM |
| 22 (28-pin) | `0x10` | `DIP28_27512` | **VPP on pin 22** (OE/VPP shared) |
| 22 (28-pin) | `0x11` | `DIP28_27256` | **VPP on pin 1** |
| 22 (28-pin) | else | `DIP28_2764` | 27C128 / 27C64 layout |
| 12 (32-pin) | `0x03` | `DIP32_27C801` | 1 MB 27C080 / M27C801 class — **A19 on pin 1**, VPP on pin 24 shared with /OE |
| 12 (32-pin) | `0x02` | `DIP32_STD` | 512 KB 27C040 class — pin 1 = VPP, pin 31 = A18 |
| 12 (32-pin) | else | `DIP32_27C020` via the residual `_PGM_ON_PIN31_MAX_SIZE` size arm | the `0x00`, `0x01` and `0x04` classes — the size threshold still separates these because `SST37VF040` (`pm_idx` 13, `variant_lo` 0x04, 524288 bytes) must stay on `DIP32_STD` |

**Critical (RESEARCH Pitfall 3):** `0x10 → DIP28_27512` (VPP pin 22) and
`0x11 → DIP28_27256` (VPP pin 1) must never be swapped — that is a 12V-to-wrong-pin
hardware-damage path. The 28-pin low-byte logic is unchanged by this plan.

**Critical (Phase 182 D-02):** the `variant_lo` fork for the 32-pin cluster must
stay **inside** the `proto_id == 0x08` test. Protocol `0x10` rows at `pm_idx` 10
and 13 carry `variant_lo` values `0x10` through `0x13`; hoisting the fork above
the protocol test would reroute Intel-flash parts onto the wrong layout.

---

## 2. HIGH byte — `variant >> 8` (minipro T56/T76 `algo_number`) — NOT a classifier

### 2.1 The source-grounded truth

The variant **high** byte is minipro's **`algo_number`** — a per-protocol FPGA
algorithm-file selector for the **T56/T76** programmers. It is consumed at exactly one
place in minipro's own code:

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

- `algo_table` (`database.c#L334`) is indexed by **`protocol_id`**, NOT by the variant
  high byte. The high byte (`algo_number`) is the **hex suffix** appended to the
  protocol's algorithm *name* to select one specific algorithm/bitstream file *within*
  that protocol family for the T56/T76 programmer firmware.
- For the TL866II / RURP-relevant path the high byte is **not consulted at all** for
  classification, electrical type, pinout, or VPP — it only affects which T56/T76 FPGA
  algorithm blob is loaded. `variant` appears at exactly two sites in `database.c`:
  L585 (parse) and L1918 (the `>>8` above); grep confirms no other use.

**Conclusion (the honest VAR-01 answer):** the high byte is a *programmer-firmware
algorithm-file selector*, **not** a memory-class taxonomy. The phase documents it as
such and does **NOT** derive `electrical.type` / `algorithm` / `pinout` from it.
Classification keys on **`type`** / **`protocol_id`** / **`pm_idx`** / **`flags`** — the
fields minipro itself uses to classify a device — NOT on the high byte.

### 2.2 Full high-byte census (659 unique DIP-parallel chips, INFOIC2PLUS filter)

`[VERIFIED: exhaustive local parse of infoic.xml @ a8efaedc, build_db.py's exact filter
(24–32 pin, no SMD, no serial, type∈{1,4})]`

| variant hi | count | (type, protocol_id) it co-occurs with | algo_table family (proto) |
|-----------|-------|----------------------------------------|---------------------------|
| 0x11 | 73 | (1,0x08) | ROM32P |
| 0x12 | 4 | (1,0x08) | ROM32P |
| 0x13 | 7 | (1,0x08) | ROM32P |
| 0x14 | 27 | (1,0x08) | ROM32P |
| 0x31 | 41 | (1,0x07) ×40, (1,0x34) ×1 (X88C64P) | ROM28P / GEN_ |
| 0x32 | 56 | (1,0x07) | ROM28P |
| 0x33 | 44 | (1,0x07) | ROM28P |
| 0x34 | 9 | (1,0x04) — DataFlash, skipped by KNOWN_PROTOCOLS gate | AT45D |
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
| 0x93 | 2 | (1,0x11) — FWH, infeasible, skipped | FWH |
| 0xE2 | 6 | (1,0x08) ×5, (4,0x07) ×1 (M48T08) | ROM32P |

**Reading the table:** every high-byte value sits inside one protocol family (the
algo_table column), confirming it is a *sub-variant within a protocol* (the
`algo_number` suffix), NOT a cross-cutting type/algorithm/pinout code. The
"structured" appearance is **correlation with protocol family**, not an independent
semantic axis.

### 2.3 The two collision cells (why the high byte cannot be the classifier)

These two cells are exactly the FM1608 and X88C64 cases and prove the high byte is not
load-bearing for classification:

- **`hi = 0x41`** mixes `(1,0x07)` real 28C EEPROMs **and** `(4,0x07)` FRAM/NVRAM —
  the *same* variant `0x4126` for both. FM1608 (type=4) and AT28C64 (type=1) are
  **indistinguishable by `variant`**; the discriminator is **`type`** (4 vs 1).
- **`hi = 0x31`** is the 27512 EPROM family (variant `0x3110`); X88C64P is the lone
  `(1,0x34)` entry with variant `0x3100`, separated from the EPROMs by **`protocol_id`**
  (0x34), not by the high byte.

**Anti-pattern (RESEARCH Pitfall 1):** branching `classify()` on `variant >> 8` would
be a coincidence-fit that breaks the moment FM1608/AT28C64 (shared `0x4126`) need
different `electrical.type`. Key on `type`/`proto`/`pm_idx`/`flags`.

---

## 3. build_db.py provenance decision (records, does not apply)

- **Decision (D-05 discretion):** the pinned SHA `a8efaedc236c1d9718bd28299dfbb99536b010ff`
  (§0) is the recorded regen provenance. This is the load-bearing reproducibility
  artifact.
- **Plan 86-02 MAY** additionally pin `MINIPRO_XML_URL` (L11) from `/-/raw/master/` to
  `/-/raw/a8efaedc236c1d9718bd28299dfbb99536b010ff/` for a deterministic fetch, citing
  this section. **NOT changed here** — `build_db.py` is untouched by Plan 86-01.

---

## 4. X88C64 fix rationale (`proto_id == 0x34` → `electrical.type = EEPROM`)

**Live ground-truth tuple** `[VERIFIED: infoic.xml INFOIC2PLUS @ a8efaedc]`:

```
X88C64P: type=1  protocol_id=0x34  variant=0x3100  flags=0x00414200
         voltages=0x0200  pin_map=0x9000e600 (pm_idx=0)  size=0x2000
```

- `flags & 0x10 == 0` → the existing flags-based "electrically-erasable" EEPROM rule
  **misses it**, so today it falls through to the `UV-EPROM` default (confirmed in the
  live `chip_database.json`: `electrical.type = "UV-EPROM"`, `algorithm = 52` (0x34),
  `support_status = "protocol-not-implemented"`).
- **Principled fix (Plan 86-02):** treat `protocol_id == 0x34` (XICOR NovRAM/EEPROM) as
  `electrical.type = "EEPROM"`. This is a **display/classification correction only** —
  X88C64P stays `support_status = "protocol-not-implemented"` and is **non-dispatchable**
  (no wire dict / no serial byte is emitted for it; the host guard
  `chip_resolver.resolve_chip` refuses it). Implementing the 0x34 *programming handler*
  is still PCB-blocked (FUT-01) and is NOT in scope.
- X88C64P is the **only** `0x34` DIP-parallel chip in scope, so the change is bounded
  to this one record.

---

## 5. FM1608 identity (Phase 197 D-08 deleted the relabel — the row now reads SRAM)

**Live ground-truth tuple** `[VERIFIED: infoic.xml INFOIC2PLUS @ a8efaedc]`:

```
FM1608: type=4  protocol_id=0x07  variant=0x4126  flags=0x00000000
        voltages=0x0100  pin_map=0 (pm_idx=0)  size=0x2000
```

`type == 4` (`MP_SRAM`, `[VERIFIED: minipro minipro.h#L70 @ a8efaedc]`) is the
authoritative FRAM/NVRAM-class signal — **not** the variant. The principled classifier
emits `algorithm = 0x28` (decimal 40, `SRAM_STD`) for type=4 chips, by type, with pinout
`DIP28_JEDEC_SRAM_8K`. **Updated 2026-09-18 (Phase 197, D-08):** the Phase-84 cosmetic
`SRAM → FRAM` relabel that used to apply here has been deleted — every host site treats
`SRAM` and `FRAM` identically, and the label was arbitrary among FM1608's own Ramtron
SRAM-class siblings (`FM1208`, `FM16W08`, `FM1808`, `FM18L08`), which already read
`SRAM`. The row now emits `electrical.type: "SRAM"` rather than `"FRAM"`, and
`electrical.vcc_mv` takes the SRAM single-rail rewrite (`build_db.py`'s
`if _etype == "SRAM": chip_entry["electrical"]["vcc_mv"] = chip_entry["electrical"]["vdd_mv"]`)
that the `FRAM` label had been bypassing — `vcc_mv` moves from its raw-decoded 3300 to
5000, matching `vdd_mv` and its `SRAM`-labelled Ramtron siblings. The recurring "FM1608
0x40" in old notes is a **decimal-40 ↔ hex-0x28 conflation** — the true identity is
`proto 0x07 + type 4 + variant 0x4126`, classified by `type`, classifier output
`algorithm = 40 (0x28)`.

---

## 6. Honest gaps (documented, never guessed — D-05)

- **No high-byte value is a classification gap.** Every high-byte value in the §2.2
  census is resolved by `database.c#L1918` as the `algo_number` (T56/T76 algorithm-file
  suffix); none is needed to classify `electrical.type` / `algorithm` / `pinout`, which
  are derived from `type`/`proto`/`pm_idx`/`flags`. There is therefore **no undecoded
  high-byte value left guessed**.
- **2516 / 2532 are NOT a decode gap.** They are physically-real 24-pin UV-EPROM
  oddballs that are **absent from `infoic.xml`** entirely (no upstream record at all —
  a categorically different concern from a chip whose fields we cannot decode). Per the
  operator directive D-10/D-11 they are introduced first-class via a **non-upstream
  supplement** in **Plan 86-04** (VAR-05), which runs *after* the Plan-02 regen and
  *before* the Plan-03 baseline re-pin. 2516 keeps its **SAFE-04 UNVERIFIED** status
  (resolvable but not write-proven; host guards stay). This Plan 86-01 makes **no**
  assertion about 2516's presence/absence — see §"Note on 2516" in `86-01-PLAN.md` and
  the `86-04` plan. **Cross-reference: Plan 86-04 owns 2516/2532.**
  **[Plan 86-04 IMPLEMENTED]** The supplement now ships as `tools/extra_chips.json`
  (manufacturer-keyed, one record each for 2516/2532, each carrying a
  `source: "non-upstream-supplement"` marker + a `datasheet` citation). `build_db.py`
  merges it into `complete_db` **after** the infoic.xml decode loop and **before** the
  JSON write (see the `VAR-05 / D-10` block in `main()` + `EXTRA_CHIPS_FILE`). The
  supplement records are NOT routed through `classify()` / `resolve_pinout_key` — they
  arrive fully-specified. 2516 keeps its SAFE-04 UNVERIFIED posture
  (`verification_status: "UNVERIFIED"`, wire values unmoved, `support_status: supported`
  so it stays resolvable for read/info; host guards unchanged). 2532 uses the new
  non-JEDEC `DIP24_2532` pinout (VPP=pin 21, distinct from `DIP24_2732`).

---

## 7. Sources

- minipro `src/database.c` @ a8efaedc — `variant>>8 = algo_number` (L1918), `algo_table`
  (L334), variant parse (L585).
  https://gitlab.com/DavidGriffith/minipro/-/raw/a8efaedc236c1d9718bd28299dfbb99536b010ff/src/database.c
- minipro `src/minipro.h` @ a8efaedc — `MP_MEMORY=0x01 … MP_SRAM=0x04` (L67-74).
- `infoic.xml` @ a8efaedc — INFOIC2PLUS survey of 659 unique DIP-parallel chips; FM1608
  / X88C64P ground-truth tuples.
- `.planning/phases/86-variant-decode-correct-db-regen/86-RESEARCH.md` — §"Variant Field
  Semantics — The Decisive Finding", high-byte census, §"X88C64 EEPROM-type fix",
  §"Honest Gaps".
- `.planning/phases/86-variant-decode-correct-db-regen/86-CONTEXT.md` — D-04, D-05,
  D-10, D-11.

---

## 8. `pulse_delay` unit finding (PULSE-01, Phase 197 — the decode rule survives)

**Verdict: the "microseconds for all protocols" decode rule is CONFIRMED**, and the
finding is that `infoic.xml` carries a bulk family default rather than a per-part
value.

**What `interpret_timing` does today** `[VERIFIED: build_db.py:339-380]`: raw
`pulse_delay` is parsed as hex and returned verbatim as microseconds, with no
multiplier, for `proto_id in (0x07, 0x08, 0x0B)`; every other `proto_id`, including the
adjacent `0x0D`, returns `0`. The membership test is exact protocol-id equality, not a
range — `0x0D` is not adjacent in behaviour, only adjacent in value. `interpret_timing`
is called with the `proto_id` produced **after** `classify()` runs, so a row that
`classify()` promotes into `0x0D` (the 5V-EEPROM promotion documented at CLAUDE.md
"5V-EEPROM promotion to `0x0D`") emits `pulse_duration_us: 0` on the shipped row, not
its raw upstream value — the promotion changes which branch of `interpret_timing` a row
takes, even though the raw `pulse_delay` field itself never changes.

**The positive confirmation.** `FUJITSU/MBM27C4001` carries raw `pulse_delay=0x0064`
(100). Its own datasheet (`datasheets/MBM27C4001.pdf`, page 8, AC CHARACTERISTICS)
states Programming Pulse Width `tPW` min 95, typ **100**, max 105 µs, and the front page
states *"Fast programming: 0.1ms pulse"*. Raw `0x0064` decoded as 100 µs is exactly the
datasheet's typical value, in the datasheet's own unit — a positive match, not merely an
absence of contradiction.

**The falsification of "per-part value".** Across every `proto_id` 0x07/0x08 `<ic>`
entry in the pinned `infoic.xml` (a8efaedc), before the DIP filter — **675 entries** —
raw `pulse_delay=0x0064` (100) accounts for **462** of them (68.4%). Two Fujitsu parts
share that identical raw value with different datasheet answers: `FUJITSU/MBM27C4001`
(100 µs, confirmed above) and `FUJITSU/MBM27C1001` (500 µs, per its own datasheet's AC
CHARACTERISTICS table). The same raw input cannot correctly decode to two different
values for two different parts, so the 462-way share is a bulk family default upstream
carries for an entire class of parts, not a measurement made per part.

**No uniform multiplier is coherent.** The raw ladder spans `0x0001` (1) to `0x2710`
(10000) — four orders of magnitude. A multiplier that maps 100 → 500 (×5) also maps
10000 → 50000 and 1 → 5; a 5 µs program pulse is not a value any EPROM datasheet
specifies. Plain microseconds is the only reading consistent with the full ladder, and
`MBM27C4001` pins it exactly.

**What `pulse_duration_us` means on the wire** `[VERIFIED: firestarter_fw/src/proms/eprom.cpp,
src/proms/eprom_params.cpp, include/eprom_params.h]` — this settles which datasheet
number to record when a datasheet states two. The firmware runs a verify-per-pulse loop,
not a single fixed pulse: `handle->pulse_delay` is the per-byte program pulse width in
µs, applied up to `max_pulses` times (25 for protocols `0x07`/`0x08`) with a verify after
each, and `overprogram_factor` is **0** on all three of `0x07`/`0x08`/`0x0B`'s parameter
rows — no over-program margin pulse is ever emitted on any target. The value to record
is therefore the fast-algorithm **initial** programming pulse width (`tPW`), never a
conventional single-shot width. For `FUJITSU/MBM27128` that is the Quick Pro figure of
1000 µs (`TPW = 1 ms ± 50 µs`, datasheet Figure 3), not the conventional 50 ms
single-shot pulse on the same datasheet's preceding page. A corrected value still yields
an incomplete fast algorithm for the same reason: the datasheet's `tOPW` over-program
pulse is never implemented, whatever value `pulse_duration_us` carries.

**Empty-input behaviour is fail-closed, not fail-open, and is provably dead against the
pinned upstream.** A missing or unparseable `pulse_delay` raises
(`f"chip with protocol {protocol_id:#04x} has unparseable pulse_delay {raw_hex!r} —
refusing to default to 0 us"`) rather than defaulting to `0`. Against the pinned
`infoic.xml` this branch never fires for any of the 746 shipped rows, so
`tests/test_build_db_interpret_timing.py` — unchanged by Phase 197 and still green — is
this branch's only coverage; the branch is real, fail-closed code, exercised only by
that module's own synthetic input, not by any live chip.

Sources: `.planning/phases/197-the-override-mechanism-and-the-program-pulse/197-RESEARCH.md`
§ "PULSE-01 — The Falsification Job"; `197-PULSE-INVENTORY.md` (the rows this finding
does not correct); `tools/datasheet_overrides.json` entries `FUJITSU/MBM27128`,
`FUJITSU/MBM27C1000`, `FUJITSU/MBM27C1001`.

---

## 9. The voltage word's two nibbles and VPP byte (VOLT-01, Phase 198 — the two nibbles and the VPP byte select a programmer rail index, not a chip requirement)

**Verdict: the voltage word's two nibbles and the VPP byte select a programmer rail index,
not a chip requirement.** The value a row's `voltages` field decodes to names which slot in
the connected programmer model's internal DAC table drives that pin — not a transcription of
the part's own datasheet figure. `198-01` and `198-02` each shipped a decode-table
completion (`VPP_MV`, `VCC_VOLTAGES`); this section is the general finding those two
completions are instances of, with its evidence and its limits.

**What the decode does today** `[VERIFIED: build_db.py, the `_d_vpp_mv` / `_d_vcc_mv` /
`_d_vdd_mv` assignment immediately after `classify()` runs]`: three lookups run against the
same 16-bit `voltages` word. `_d_vpp_mv` reads the low byte — exact-matched first against the
two non-zero-low-nibble keys `0xF1`/`0xF2`, then masked `& 0xF0` and looked up in `VPP_MV`
with a `0` default. `_d_vcc_mv` reads bits 11-8 and `_d_vdd_mv` reads bits 15-12, both looked
up in `VCC_VOLTAGES` with a `5000` default. Each table carries a
`[VERIFIED: minipro <file>#<lines> @ a8efaedc — <array>[]]` marker naming the specific
upstream C array it transcribes.

`VPP_MV` is completed from upstream's `xg_vpp_voltages[]` `[VERIFIED: database.c#L161-L170 @
a8efaedc]`, a strict, conflict-free superset of `tl866ii_vpp_voltages[]`: the sixteen indices
already shipped are byte-identical between the two tables, and `xg_vpp_voltages[]` adds
exactly `0xF1` (25000 mV) and `0xF2` (21000 mV) beyond them, matched against the full low
byte before the `& 0xF0` mask is applied so they are not collapsed onto `0xF0`.
`VCC_VOLTAGES` is completed from `xg_vcc_voltages[]` `[VERIFIED: database.c#L182-L190 @
a8efaedc]`, likewise a strict, conflict-free superset of `tl866ii_vcc_voltages[]`: the six
shipped indices (`0x00`-`0x05`) are byte-identical, and `xg_vcc_voltages[]` adds nine more
(`0x06`=1800 … `0x0E`=6250). Both completions are decoder completion, not correction — one
encoding implemented to different depths by different upstream tables, never `tl866a_*`'s
genuinely different encoding, which conflicts on 4 of 6 shared VCC indices and 8 of 8 shared
VPP indices.

**A falsified citation, found and corrected.** `VCC_VOLTAGES` and the
`_VCC_MARGIN_RAIL_MV` block immediately below it each carried the identical marker
`[VERIFIED: minipro database.c#L130-L135 @ a8efaedc — tl866ii_vcc_voltages[]]`. At the pinned
sha, lines 130-135 are `tl866a_vpp_voltages[]` plus the start of `tl866a_vcc_voltages[]` —
not `tl866ii_vcc_voltages[]` (which lives at L154-L159) and not `xg_vcc_voltages[]`
(L182-L190). Both copies named the wrong table and the wrong line range; both were deleted
rather than rewritten in place, and the corrected provenance lives here.

**The twelve-row carve-out at vdd index `0x06` (D-04, D-05).** Twelve rows — seven EXEL,
three ST and two SGS-THOMSON 28C-class parts carrying voltage word `0x64xx` — decode to
1800 mV under the completed table. 1.8 V is not credible as a program rail for a 5 V
28C-class parallel EEPROM, so these twelve keep the `vdd_mv: 5000` they emitted before the
table was completed, held there by twelve explicit `UNSOURCED` entries in
`tools/datasheet_overrides.json` rather than by leaving `0x06` out of the table.
`198-VOLT03-DISPOSITION.md` disposes of these twelve, and the sixteen rows at vdd index
`0x04`, in full — with all 28 rows' evidence and honesty limit.

**The positive confirmation.** Three vendored Fujitsu datasheets — `MBM27128.pdf`,
`MBM27C1001.pdf`, `MBM27C4001.pdf` — all state a program VCC of 6.0 V ± 0.25 V. All three rows
decode to vdd index `0x04`, which `VCC_VOLTAGES` maps to 5500 mV; `tools/datasheet_overrides.json`
now holds each at the datasheet's 6000 mV instead. A model whose rail table is asked to
represent "6.0 V ± 0.25 V" and answers "5500" is reporting which rail the model selects, not
the part's datasheet figure — and the model's own 6500 mV rail (index `0x05`) sits closer to
that band than index `0x04` does, which is itself evidence that the index selects a model
slot rather than tracking the datasheet value. This is a positive match to the datasheet's
own stated figure, not merely an absence of contradiction.

**The falsification of the rival reading — that these are chip requirements.** The same
index means different volts on different programmer models: VPP index `0x00` is 12 V on the
TL866-II (`tl866ii_vpp_voltages[]`) and 12.5 V on the TL866A (`tl866a_vpp_voltages[]`); index
`0x20` is 9.5 V on the TL866-II and 21 V on the TL866A. A chip requirement cannot mean two
different voltages depending on which programmer model happens to be plugged in — only a
model-side table slot can. Upstream's own comment says as much, quoted verbatim
`[VERIFIED: database.c#L125-L126 @ a8efaedc]`:

> "These are not raw DAC output values, but rather indices into internal lookup tables
> defined in the firmware."

**Cite lines 125-126, not 123.** The phase context that opened this work cited
`database.c:123`; at the pinned sha, line 123 reads
`* The Vcc and Vpp settings are linked through the 'voltages'`, a different line of the same
four-line comment block. The quoted sentence is exact and the argument is unaffected — a
phase whose subject is decode provenance does not propagate a citation it has measured as
wrong.

**The arithmetic argument — the sharpest single piece of evidence, and it is arithmetic
rather than appeal.** The three Fujitsu datasheets' band is 5.75-6.25 V. The model's two
nearest rails, `VCC_VOLTAGES[0x04]` = 5500 and `VCC_VOLTAGES[0x05]` = 6500, are 5.5 V and
6.5 V. Neither 5.5 nor 6.5 lies inside 5.75-6.25. A field that cannot represent the datasheet
value is not recording the datasheet value — it is picking the nearest thing the model can
actually drive.

**A second instance of the same pattern: saturation at the model maximum.** 28 rows carry
VPP low byte `0xF0`, decoding to 18000 mV — the largest entry `VPP_MV` shipped before this
phase's completion, i.e. the TL866-II's VPP ceiling. That group includes all eight NMOS
2716/2732 rows and `MBM27128`, which a separate community report (gh#71) measured as needing
21 V. Twenty-eight rows landing on the exact model maximum, several needing more than that
maximum can deliver, is the same finding as the arithmetic argument above: the field records
what the model can select, and clamps there when the part's own requirement exceeds it.

**The edge case and dead branch (D-16).** Upstream's `xg_vpp_voltages[]` carries two further
indices beyond `0xF0`: `0xF1` = 25000 and `0xF2` = 21000 — exactly the two figures Phase
197's six `UNSOURCED` NMOS override entries already hold as `is` values, independently of
this phase's decode work. This was checked and closed rather than left as a loose thread: no
filtered row carries either `0xF1` or `0xF2` today, so completing `VPP_MV` to include them is
provably a zero-diff change — regenerating with the completed table reproduces the
previously-committed database byte-for-byte. No test can exercise either branch until
upstream ships an `infoic.xml` row carrying one of these two low bytes; until then, both
entries are real, reachable code with no live chip to reach them. Record this deliberately:
it is a tempting false lead — a decodable 25 V or 21 V value sitting right there in the
table — that this phase's own research nearly rediscovered, and the saturation-at-the-maximum
pattern above is direct, independent evidence for the section's verdict.

**The surviving silent fallback (D-03).** The `.get(idx, default)` idiom on all three lookups
was offered a fail-closed alternative — raise on an unmapped index instead of defaulting —
and the offer was declined; the generator keeps defaulting rather than stopping the build.
Nothing mechanical reports a future unmapped index, so it is named here instead: after this
phase's table completions, no row in the 767-row filtered population reaches any of the
three defaults today. A later `infoic.xml` update that introduces a genuinely new index would
silently reuse this same fallback, unannounced.

**The `vdd < vcc` predicate (D-06).** Decoded vdd strictly below decoded vcc selects exactly
28 rows and nothing else, against 373 rows where the two are equal and 366 where vdd is
greater — 28 + 373 + 366 = 767, the full filtered population. The predicate is documented
here, not shipped as a build-time assertion in `build_db.py`; no value hangs off it this
phase. `198-VOLT03-DISPOSITION.md` disposes of the 28 rows the predicate selects.

**The limits, named rather than hedged:**

- **The datasheet corroboration is n = 3, and all three are Fujitsu.** The positive-
  confirmation evidence above rests on three datasheets from one manufacturer. No claim is
  made that Fujitsu's 6.0 V ± 0.25 V figure generalises to any other manufacturer's parts at
  the same index.
- **No claim is made about the 167 rows at vdd index `0x4` this phase does not reach.**
  `VCC_VOLTAGES[0x04]` = 5500 is the same index the three corrected Fujitsu rows carried
  before their override; 167 further rows decode to that same index and this phase leaves
  every one of them unexamined. **Cross-reference: Phase 200 owns them**, one datasheet per
  row, under the same discipline this phase used for the three it did examine.
- **The finding does not assert that any particular row's emitted value is correct.** It
  asserts what the field *is* — a programmer rail index — not that any given row's decoded or
  overridden voltage is the right one for that part.
- **The low nibble's option-flag reading is this generator's own working reading, not
  upstream-attested.** No named constant in `minipro.h` or `database.c` documents the VPP
  byte's low nibble as an option-flag field; the reading is corroborated only by the
  `0x00`/`0x01` and `0x70`/`0x71` pairing pattern in the low-byte census — the same rail
  index appearing twice, once with the low nibble clear and once set. The two powerdown bits
  upstream *does* name — `LAST_JEDEC_BIT_IS_POWERDOWN_ENABLE` and `POWERDOWN_MODE_DISABLE`
  `[VERIFIED: minipro.h#L84-L85 @ a8efaedc]` — sit at bit positions 12 and 13, inside the
  **vdd** nibble, not the VPP byte's low nibble, and are scoped in their own comment to the
  ATF20V10C/ATF16V8C PLD variants this generator's `type in {1, 4}` filter excludes entirely.
  That is itself a second, independent instance of this section's own claim: the same bit
  positions mean different things depending on which part family owns the row.

Plan `198-03` completes this section with the general finding above (D-15); plans `198-01`
and `198-02` established the two decode-table completions, the falsified-citation correction
and the twelve-row carve-out this section folds in as evidence for it.

Sources: `.planning/phases/198-the-two-voltage-nibbles/198-RESEARCH.md` F-1, F-2, F-4, F-13;
`.planning/phases/198-the-two-voltage-nibbles/198-VOLT03-DISPOSITION.md`;
`.planning/phases/198-the-two-voltage-nibbles/198-REGEN-DIFF.md`;
`tools/datasheet_overrides.json` entries `FUJITSU/MBM27128`, `FUJITSU/MBM27C1001`,
`FUJITSU/MBM27C4001`, and the twelve `0x06`-carve-out entries; `database.c#L125-L126 @
a8efaedc`; `database.c#L161-L170 @ a8efaedc`; `database.c#L182-L190 @ a8efaedc`;
`minipro.h#L84-L85 @ a8efaedc`.

---

## 10. What the rails actually deliver, and the firmware's routing decision that follows (RAIL-01, RAIL-02, RAIL-04, Phase 199 — the generator's 25 V ceiling stays theoretical; the measured, smaller deliverable maximum is now a firmware routing ceiling)

**Verdict: `RURP_VPP_CEILING_MV = 25000` keeps its job as the generator's refusal gate and remains a
regulator's theoretical figure, while what the shield actually delivers at the socket is a separate,
smaller, measured number that now lives in firmware as a routing ceiling, not in this generator.**
Plan `199-02` measured the two socket-pin-1 figures on the bench; plan `199-03` turned the
drop-resistor figure into a firmware constant and a routing comparison; plan `199-04` turned the
30-row disposition below into a test the generator's build can fail; this plan (`199-05`) is where
all three meet and where gh#71's reporter gets an answer.

**What the ceiling is and why it stays (D-01, D-02).** `RURP_VPP_CEILING_MV = 25000`, defined in
`firestarter_app/tools/build_db.py`, is compared strictly greater-than against each row's decoded
`vpp_mv`; the six rows sitting exactly on 25000 stay `supported` because of that strictness. No
row's `support_status` moved in this phase and none moves as a result of this section. Replacing the
ceiling with a measured figure was considered and declined: a measured maximum near 16–18 V would
flip all thirty rows below to `vpp-exceeds-max`, and `chip_resolver.py` turns any non-`supported` row
into a `ChipNotImplementedError` — the silent refusal this milestone's activation decisions forbid.
The generator gained no second constant; whatever ceiling the routing decision needs lives in
firmware instead, described below.

**What was measured (D-04, D-05, D-07).** One bench session, 2026-09-19, on one shield identified by
its operator by silkscreen as **Rev 2.0** — `hw_revision` cannot distinguish Rev 2.0 from **Rev 2.2**
from the modified **Rev 0**, so the identification is an operator statement, not an instrument
reading. Socket empty, pot at maximum and untouched between readings, meter at **socket pin 1**
against board ground. Two control-register composites, both held and read at that one pot setting:
`0x188` (`REGULATOR | VPE-DROP | P1`, the standard drop-resistor path) measured **17380 mV**, and
`0x088` (`REGULATOR | P1`, drop bit clear — VPE routed directly to pin 1) measured **22140 mV**. The
rail was held by the firmware's own `while (!rurp_user_button_pressed()) delay(200);` loop at the
tail of `dt_set_registers()`, entered after the host had already disconnected — no `firestarter`
command ran while either rail was held. A method that instead holds the serial port open does not
work on this rig: closing the port de-asserts DTR, which resets the board and drops the rail before
a reading can be taken.

**What the programmer's own monitor said, and why it is not the same measurement (D-05).** Two
bounded one-second sampling windows (`firestarter vpp -t 1`, `firestarter vpe -t 1`), with the
recording rule fixed before either was opened: take the last frame in the window, unless the last
three frames disagree by more than one 100 mV step. Both windows emitted two identical frames. The
drop-resistor monitor read **18700 mV** against the 17380 mV meter figure — a signed difference of
+1320 mV, **+7.59 %**. The direct-VPE monitor read **23900 mV** against the 22140 mV meter figure — a
signed difference of +1760 mV, **+7.95 %**. Both figures fall inside this project's separately
measured ratiometric ADC figure of roughly 7.5 % (range 6.8–8.3 %), which they corroborate rather
than replace; that instrument's standing operational rule is unchanged by this session — a pot target
is set from a meter reading, never from the firmware's own figure.

**The routing decision — route it, in firmware, on path capability (D-21).** `eprom_hv_route_mask`
(`firestarter_fw/src/proms/eprom.cpp`) makes the decision. `FLAG_VPE_AS_VPP` is checked first and
still wins over the table with no table read — it is the pre-existing manual override for the
25 V NMOS parts and the manual-pot workflow. Failing that, and failing an unresolved protocol closed
toward the drop path, the function reads the protocol's `vpp_path` column
(`firestarter_fw/src/proms/eprom_params.cpp` assigns `VPP_PATH_DROP_RESISTOR` to protocols `0x07` and
`0x08` and `VPP_PATH_DIRECT_VPE` to `0x0B`); a `VPP_PATH_DIRECT_VPE` row already routes the undropped
rail. The addition this phase records: a `VPP_PATH_DROP_RESISTOR` row whose required `vpp_mv` is
strictly greater than `RURP_VPP_DROP_PATH_MAX_DELIVERABLE_MV` also routes the undropped rail.
`eprom_check_vpp` calls the same `eprom_hv_route_mask` before it reads anything, unchanged by this
phase, so its acceptance window already follows whichever rail was routed. This is a firmware
decision and no host-side rule sets it: the host and the firmware ship on independent channels — a
host pinned at an old version, or carrying a threshold measured on a different shield, would
mis-route a high-voltage rail onto a socket pin if the decision lived on that side instead. No host
module exists for this; none is named because none was built.

**Why the trigger is path capability and not the live reading — the sharpest finding in this section
(D-22).** `eprom_check_vpp`'s existing acceptance check fires `MSG_WARN_VPP_LOW` when the read rail
sits below 95 % of the required voltage. Measured against the bench pair, that reading-based rule
would have caught almost none of the rows this phase exists to rescue. At a required 18000 mV the
95 % threshold is 17100 mV; the drop-path monitor reads 18700 mV, which is above the threshold, so
the check stays silent — while the socket itself, at 17380 mV, is 620 mV short. At a required 21000
mV the threshold is 19950 mV; the same 18700 mV monitor reading is now below it, so the check does
fire, and this is the one row (`FUJITSU/MBM27128`) whose shortfall (3620 mV) exceeds the roughly
+7.6 % monitor error enough to be caught anyway. A trigger built on the ADC reading would therefore
have silently missed nine of the ten drop-resistor rows this section's classification names below.
Routing is decided on what the path itself can deliver at any pot setting; the ADC-based acceptance
check is left exactly as it was and, because it already calls `eprom_hv_route_mask`, continues to
verify whichever rail the path-capability decision routed. **Consequence (D-23):** future calibration
of that ADC improves the verification leg only — it is never a precondition for the routing decision
being correct, because the routing decision was deliberately built not to depend on it.

**A ceiling, not a threshold (D-22, carrying D-15's surviving substance).** The undropped route does
not deliver a fixed voltage; it delivers whatever the pot is set to. `RURP_VPP_DROP_PATH_MAX_DELIVERABLE_MV`
therefore does not mean "17380 mV is what a routed part receives" — it means no pot setting lets the
drop-resistor path exceed 17380 mV, so any part requiring more must take the direct-VPE route
instead. One pot sets both rails, so a part now routed onto the undropped rail still needs the pot
set near its own required voltage; the firmware's existing ±5 %/+500 mV window is what adjudicates
that, unchanged by this phase, and a first attempt on a newly-routed part can hard-error there if the
pot is not set close enough.

**The posture change, and the documentation that now reflects it (D-12, reversed).** Before this
work, `FLAG_VPE_AS_VPP` was the only route to the undropped rail besides the `vpp_path` table column
itself, and `eprom_hv_route_mask`'s own resolution-order documentation in `eprom.cpp`, along with the
declaration comment above it in `include/eprom.h`, both said so. Both sites were corrected in the
same commits that added the ceiling comparison: the `eprom.cpp` block gained a fourth documented
resolution step covering the new comparison, and its two prior steps were reworded to say that the
override is no longer the only route to the undropped rail; the `eprom.h` declaration now says the
route comes from the `vpp_path` column "though not from that column alone." The posture changed and
the firmware's own documentation now says so — this is not a stale sentence left standing.

**The classification (D-17, D-18), re-measured from the live generated database.** Filtering every
row whose `electrical.vpp_mv` is at or above 18000 mV, and mapping each row's `programming.algorithm`
through the same algorithm-to-path table `eprom_params.cpp` carries, yields exactly the state
`tests/test_vpp_rail_classification.py` asserts: **30 rows**, voltage histogram **21 at 18000 mV, 3
at 21000 mV, 6 at 25000 mV**, every one `support_status: supported`. Ten rows sit on the
drop-resistor path (algorithm `0x07`, pinout `DIP28_2764`, `vpp-pin: [1]`); twenty sit on the
direct-VPE path (algorithm `0x0B`, pinout `DIP24_2716`/`DIP24_2732`/`DIP24_2532`, `vpp-pin: [21]`).
The drop-resistor path's measured 17380 mV ceiling reaches **none** of the thirty: the nine rows at
18000 mV are short by 620 mV, and the tenth (`FUJITSU/MBM27128`, at 21000 mV) is short by 3620 mV.
With the routing change, every one of the thirty now takes the direct-VPE route: the measured direct
figure of 22140 mV at socket pin 1 covers all ten former drop-resistor rows, worst margin 1140 mV
(`FUJITSU/MBM27128` at 21000 mV).

| Path | Required VPP | Manufacturer | Part Number | Algorithm | Pinout | Reason |
|---|---|---|---|---|---|---|
| drop-resistor | 21000 mV | FUJITSU | MBM27128 | 0x07 | DIP28_2764 | Corrected 2026-09-19 from its own vendored datasheet (`datasheets/MBM27128.pdf`, Figure 3) — the gh#71 correction, 18000 → 21000. |
| drop-resistor | 18000 mV | FUJITSU | MBM27C128P | 0x07 | DIP28_2764 | Upstream decode: VPP low byte `0xF0` saturates at the decode table's largest entry (18000 mV) — the model's own maximum, not a per-part measurement. |
| drop-resistor | 18000 mV | FUJITSU | MBM27C64 | 0x07 | DIP28_2764 | Upstream decode, `0xF0` saturation, as above. |
| drop-resistor | 18000 mV | HITACHI | HN27C64G | 0x07 | DIP28_2764 | Upstream decode, `0xF0` saturation, as above. |
| drop-resistor | 18000 mV | HITACHI | HN27C64FP | 0x07 | DIP28_2764 | Upstream decode, `0xF0` saturation, as above. |
| drop-resistor | 18000 mV | INTEL | 2764 | 0x07 | DIP28_2764 | Upstream decode, `0xF0` saturation, as above. |
| drop-resistor | 18000 mV | INTEL | 27128,D27128 | 0x07 | DIP28_2764 | Upstream decode, `0xF0` saturation, as above. |
| drop-resistor | 18000 mV | MITSUBISHI | M5M27C128 | 0x07 | DIP28_2764 | Upstream decode, `0xF0` saturation, as above. |
| drop-resistor | 18000 mV | NEC | UPD2764,UPD2764C,UPD2764D | 0x07 | DIP28_2764 | Upstream decode, `0xF0` saturation, as above. |
| drop-resistor | 18000 mV | TI | TMS2764 | 0x07 | DIP28_2764 | Upstream decode, `0xF0` saturation, as above. |
| direct-vpe | 25000 mV | INTEL | 2732,2732A,M2732,M2732A | 0x0B | DIP24_2732 | Held by an `UNSOURCED` Phase 197 override entry — no datasheet citation. |
| direct-vpe | 25000 mV | INTEL | M2716,M2716M | 0x0B | DIP24_2716 | Held by an `UNSOURCED` Phase 197 override entry — no datasheet citation. |
| direct-vpe | 25000 mV | SGS-THOMSON | ETC2716,M2716 | 0x0B | DIP24_2716 | Held by an `UNSOURCED` Phase 197 override entry — no datasheet citation. |
| direct-vpe | 25000 mV | ST | ETC2716,M2716 | 0x0B | DIP24_2716 | Held by an `UNSOURCED` Phase 197 override entry — no datasheet citation. |
| direct-vpe | 25000 mV | TEXAS INSTRUMENTS | 2516 | 0x0B | DIP24_2716 | Hardcoded in the non-upstream supplement (`tools/extra_chips.json`), `UNVERIFIED`, not write-graduated — comes from neither the decode nor the override file. |
| direct-vpe | 25000 mV | TEXAS INSTRUMENTS | 2532 | 0x0B | DIP24_2532 | Hardcoded in the non-upstream supplement (`tools/extra_chips.json`), `UNVERIFIED`, not write-graduated — comes from neither the decode nor the override file. |
| direct-vpe | 21000 mV | SGS-THOMSON | M2732A | 0x0B | DIP24_2732 | Held by an `UNSOURCED` Phase 197 override entry — no datasheet citation. |
| direct-vpe | 21000 mV | ST | M2732A | 0x0B | DIP24_2732 | Held by an `UNSOURCED` Phase 197 override entry — no datasheet citation. |
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

The shipped constant, for the mechanical equality this phase's own gate proves against the bench
record and against `firestarter_fw/include/rurp_pinout.h`:

```
RURP_VPP_DROP_PATH_MAX_DELIVERABLE_MV = 17380
```

In prose, for a human reader: the drop-resistor path's measured deliverable maximum is **17.38 V** at
socket pin 1, one Rev 2.0 shield, pot at maximum, socket empty
`[VERIFIED: .planning/phases/199-what-the-rails-can-actually-deliver/199-BENCH-RECORD.md § "Measured
figures", bench session 2026-09-19]`.

**The limits, named rather than hedged:**

- **One shield, one session, one pot setting.** These are figures for this Rev 2.0 board at maximum
  pot, not a specification, not a fleet figure and not a tolerance band. The **Rev 2.2** board is
  unmeasured and named as such here rather than assumed identical. The modified **Rev 0** could
  contribute no monitor figure in any case: `hw_read_voltage` refuses on Rev 0, and `eprom_check_vpp`
  returns early there before it ever reads a voltage.
- **The monitor-versus-meter difference is a combined instrument-plus-switch-path discrepancy this
  session cannot decompose**, because the monitor commands assert no socket-routing bit while the
  meter reads at the socket through one. This project's separately measured ratiometric figure for
  the instrument alone (roughly 7.5 %, range 6.8–8.3 %) is the only instrument-only number this
  project has; both figures measured here fall inside that range and corroborate it without replacing
  it. Its standing operational rule stands: set any pot target from a meter reading, never from the
  firmware's own figure.
- **The monitor's wire format carries whole volts plus one tenths digit**, so every firmware-reported
  figure here sits on a 100 mV grid and is never finer than that, independently of accuracy.
- **The direct-VPE-to-pin-21 destination was not measured.** The twenty direct-VPE rows above receive
  VPE through `CTRL_VPE_ENABLE` to **pin 21**, a different physical destination from the socket-pin-1
  figure this section classifies them against — `CTRL_VPE_ENABLE` and `CTRL_VPP_P1_ENABLE` route to
  different pins. This is stated here as an approximation, not corrected by any figure in this
  record; in particular nothing here licenses the inference that the six rows at 25000 mV are out of
  reach.
- **The classification test mirrors a firmware table the host cannot read**, so it detects a
  database-side change only. A firmware-side repointing of an algorithm onto a different rail would
  pass this database-side test silently.
- **The two Texas Instruments supplement rows at a hardcoded 25000 mV are `UNVERIFIED` and not
  write-graduated** — see the table above; they come from neither the infoic.xml decode nor a
  datasheet-cited override.
- **The shortfall warning gap.** Where a part's requirement exceeds what the routed rail delivers,
  the only operator-facing signal is the firmware's existing low-VPP warning
  (`MSG_WARN_VPP_LOW`, `eprom_check_vpp`), which names the measured rail and the required voltage and
  proceeds. Its trigger is a 5 % window applied to a reading this session measured as roughly 7.6 %
  high, so a real shortfall smaller than that combined margin can pass without ever firing the
  warning — named here as a limit, not solved by anything in this phase.
- **Nothing was written to any of the thirty parts in this work.** No claim is made that any of them
  now programs; this section records what a rail delivers and what the firmware decides about it, not
  a write outcome.

The bench session established the two socket-pin-1 figures and the paired ADC error; the firmware
plan turned the drop-resistor figure into `RURP_VPP_DROP_PATH_MAX_DELIVERABLE_MV` and a routing
comparison proven by a native test suite to read no voltage; the classification-census test turned
the thirty-row disposition above into an assertion the generator's own build can fail. This section
is the record a reader with the installed package, and neither a planning directory nor a bench log,
can find all three in.

Sources: `.planning/phases/199-what-the-rails-can-actually-deliver/199-BENCH-RECORD.md` §§ "Measured
figures", "Task 2 — The two hold windows", "Task 3 — Paired ADC reads and the error figures", "Limits";
`.planning/phases/199-what-the-rails-can-actually-deliver/199-03-FIRMWARE-NOTES.md` §§ 1–3;
`.planning/phases/199-what-the-rails-can-actually-deliver/199-CONTEXT.md` D-01, D-02, D-04, D-05,
D-06, D-07, D-12, D-17, D-18, D-21, D-22, D-23; `firestarter_app/tools/build_db.py`
(`RURP_VPP_CEILING_MV`); `firestarter_fw/include/rurp_pinout.h`
(`RURP_VPP_DROP_PATH_MAX_DELIVERABLE_MV`); `firestarter_fw/src/proms/eprom.cpp`
(`eprom_hv_route_mask`, `eprom_check_vpp`); `firestarter_fw/include/eprom.h`;
`firestarter_fw/src/proms/eprom_params.cpp` (the protocol-keyed `vpp_path` table);
`firestarter_app/tests/test_vpp_rail_classification.py`; `firestarter_app/firestarter/diagnostic_report.py`
(`_RAIL_READING_DISCLOSURE`).
