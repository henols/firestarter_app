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

## 1. LOW byte — `variant & 0xFF` (pinout-family sub-discriminator) — UNCHANGED

The variant **low** byte is **already consumed** by `resolve_pinout_key`
(`build_db.py` ~L193-270) as the pinout-family sub-discriminator *within* a physical
layout cluster (`pm_idx`). It is **NOT changed this phase** — `resolve_pinout_key`
stays verbatim (RESEARCH Pitfall 3: swapping these would put 12V on the wrong pin).

The concrete `variant_lo` values it switches on:

| `pm_idx` | `variant_lo` | resolved pinout | note |
|----------|--------------|------------------|------|
| 23 (24-pin) | `0x01` | `DIP24_2732` | 4KB UV-EPROM |
| 23 (24-pin) | `0x10` | `DIP24_2816` | 28C-family 5V EEPROM (reliable 28C discriminator — many 28C parts have `flags=0x0000`) |
| 23 (24-pin) | else (`0x00`) | `DIP24_2716` | 2KB UV-EPROM |
| 22 (28-pin) | `0x10` | `DIP28_27512` | **VPP on pin 22** (OE/VPP shared) |
| 22 (28-pin) | `0x11` | `DIP28_27256` | **VPP on pin 1** |
| 22 (28-pin) | else | `DIP28_2764` | 27C128 / 27C64 layout |

**Critical (RESEARCH Pitfall 3):** `0x10 → DIP28_27512` (VPP pin 22) and
`0x11 → DIP28_27256` (VPP pin 1) must never be swapped — that is a 12V-to-wrong-pin
hardware-damage path. The low-byte logic is correct and out of scope for the rewrite.

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

## 5. FM1608 identity (no decode change — already correct in the DB)

**Live ground-truth tuple** `[VERIFIED: infoic.xml INFOIC2PLUS @ a8efaedc]`:

```
FM1608: type=4  protocol_id=0x07  variant=0x4126  flags=0x00000000
        voltages=0x0100  pin_map=0 (pm_idx=0)  size=0x2000
```

`type == 4` (`MP_SRAM`, `[VERIFIED: minipro minipro.h#L70 @ a8efaedc]`) is the
authoritative FRAM/NVRAM-class signal — **not** the variant. The principled classifier
emits `algorithm = 0x28` (decimal 40, `SRAM_STD`) for type=4 chips, and the existing
Phase-84 cosmetic `SRAM → FRAM` relabel then applies, with pinout
`DIP28_JEDEC_SRAM_8K`. The recurring "FM1608 0x40" in old notes is a **decimal-40 ↔
hex-0x28 conflation** — the true identity is `proto 0x07 + type 4 + variant 0x4126`,
classified by `type`, classifier output `algorithm = 40 (0x28)`.

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
