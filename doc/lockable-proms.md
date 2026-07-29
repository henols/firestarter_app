Below is a **family-level master list for common JEDEC-compatible parallel NOR flash and EEPROM-style flash**, mainly from the 1990s through mid-2000s.

“Readable” means a programmer can issue a documented command and directly determine whether a sector, block, boot block, OTP area, or protection mechanism is active. It does **not** include merely attempting a write and seeing whether it fails.

## Key

| Marking         | Meaning                                                                      |
| --------------- | ---------------------------------------------------------------------------- |
| **Yes—sector**  | Individual sector/block state can be queried                                 |
| **Yes—global**  | A device-wide protection state can be queried                                |
| **Yes—special** | Boot block, OTP, security sector, PPB or lock register can be queried        |
| **Indirect**    | No explicit readable state; determined by programming tests or configuration |
| **No**          | No documented readable protection-status mechanism                           |
| **Permanent**   | At least one protection mode cannot normally be reversed                     |

# 1. Winbond parallel flash

| Family / representative parts   | Protection state readable? | Protected region                |                                      Permanent? | Notes                                                                                        |
| ------------------------------- | -------------------------: | ------------------------------- | ----------------------------------------------: | -------------------------------------------------------------------------------------------- |
| **W29C010 / W29C010M**          |         Usually no for SDP | Whole device SDP                |                                              No | Software Data Protection is a command-sequence requirement, not normally a readable lock bit |
| **W29C020 / W29C020C**          |            **Yes—special** | Bottom and top 8 KB boot blocks |                                         **Yes** | Read boot-block status in Product ID mode                                                    |
| **W29C040 / W29C040P**          |          Variant-dependent | Boot blocks or SDP              |                               Variant-dependent | Must check the exact suffix and revision                                                     |
| **W29EE011 / W29EE012**         |         Usually no for SDP | Whole device                    |                                              No | EEPROM-like page-write devices                                                               |
| **W29EE020**                    |          Variant-dependent | Boot block / SDP                |                                        Possibly | Exact datasheet required                                                                     |
| **W49F002 / W49F002U**          |     **Yes—sector/special** | Boot sectors                    | Usually reversible with proper voltage/commands | Replacement family for W29C020C, but not command-identical                                   |
| **W49F020**                     |             **Yes—sector** | Individual sectors              |                                      Usually no | AMD/JEDEC-style sector protection                                                            |
| **W49F040**                     |             **Yes—sector** | Individual sectors              |                                      Usually no | Sector protection status through ID/protection mode                                          |
| **W39L010 / W39L020 / W39L040** |          Variant-dependent | Boot blocks or sectors          |                               Variant-dependent | 3.3 V families; examine exact device revision                                                |

For the **W29C020C**, the bottom and top boot-block states are explicitly readable. This device is unusual because the boot-block lockout is effectively irreversible through ordinary commands.

# 2. AMD Am29F and Am29LV families

The classic AMD command set usually provides an **Autoselect Sector Protection Verify** result.

| Family                                |              Readable? | Protection type                        |                                    Permanent? |
| ------------------------------------- | ---------------------: | -------------------------------------- | --------------------------------------------: |
| **Am29F010 / F010B**                  |         **Yes—sector** | Sector protection                      | Normally reversible using programming voltage |
| **Am29F002 / F002B / F002NB**         |         **Yes—sector** | Boot-sector protection                 |                           Normally reversible |
| **Am29F020 / F020B**                  |         **Yes—sector** | Sector protection                      |                           Normally reversible |
| **Am29F040 / F040B**                  |         **Yes—sector** | Sector protection                      |                           Normally reversible |
| **Am29F080 / F080B**                  |         **Yes—sector** | Sector protection                      |                           Normally reversible |
| **Am29F100 / F200 / F400 / F800**     |         **Yes—sector** | Top/bottom boot and regular sectors    |                           Normally reversible |
| **Am29LV010 / LV020 / LV040**         |         **Yes—sector** | Sector protection                      |                           Normally reversible |
| **Am29LV100 / LV200 / LV400 / LV800** |         **Yes—sector** | Boot and main sectors                  |                           Normally reversible |
| **Am29LV160 / LV320 / LV640**         |         **Yes—sector** | Sector protection                      |                           Normally reversible |
| **Am29DLxxx**                         |         **Yes—sector** | Sector protection in dual-bank array   |                           Normally reversible |
| **Am29PLxxx**                         |         **Yes—sector** | Sector protection                      |                              Device-dependent |
| **Am29BDSxxx**                        | **Yes—sector/special** | Sector protection and security regions |                              Device-dependent |

AMD datasheets explicitly describe Autoselect as providing manufacturer ID, device ID and sector-protection status. ([Infineon][1])

Typical AMD-style result:

* Read a sector address with the specified low address bits.
* `00h` generally means unprotected.
* `01h` generally means protected.

Exact address wiring and byte/word interpretation depend on x8/x16 mode.

# 3. Fujitsu MBM29 families

Most Fujitsu MBM29 parts are command-compatible with AMD counterparts.

| Family                             |      Readable? |       Permanent? | Notes                               |
| ---------------------------------- | -------------: | ---------------: | ----------------------------------- |
| **MBM29F010 / F020 / F040 / F080** | **Yes—sector** |      Normally no | Autoselect Sector Protection Verify |
| **MBM29F002 / F200 / F400 / F800** | **Yes—sector** |      Normally no | Includes top/bottom boot variants   |
| **MBM29LVxxx**                     | **Yes—sector** |      Normally no | Low-voltage equivalents             |
| **MBM29DLxxx**                     | **Yes—sector** |      Normally no | Dual-bank devices                   |
| **MBM29PLxxx**                     | **Yes—sector** | Device-dependent | Later page-mode families            |

Do not assume every Fujitsu suffix has the same unprotect method. Some require elevated voltage on `RESET#`, `A9`, or another control pin.

# 4. Spansion and Cypress S29 families

## Older S29AL/S29JL/S29PL-style parts

| Family       |              Readable? | Protection type                              |         Permanent? |
| ------------ | ---------------------: | -------------------------------------------- | -----------------: |
| **S29ALxxx** |         **Yes—sector** | Traditional sector protection                | Usually reversible |
| **S29JLxxx** |         **Yes—sector** | Traditional sector protection                | Usually reversible |
| **S29PLxxx** | **Yes—sector/special** | Sector protection, sometimes security region |   Device-dependent |
| **S29DLxxx** |         **Yes—sector** | Dual-bank sector protection                  | Usually reversible |
| **S29WSxxx** | **Yes—sector/special** | Sector and advanced protection               |   Device-dependent |

## MirrorBit GL families

| Family                            |              Readable? | What can be queried                                                        |                     Permanent? |
| --------------------------------- | ---------------------: | -------------------------------------------------------------------------- | -----------------------------: |
| **S29GL-N**                       | **Yes—sector/special** | Conventional sector status; some versions include permanent sector locking | **Yes on applicable versions** |
| **S29GL-P**                       | **Yes—sector/special** | PPB, DYB, PPB lock, password mode                                          |           **Yes/configurable** |
| **S29GL-S**                       | **Yes—sector/special** | PPB, DYB, lock register, OTP protection                                    |           **Yes/configurable** |
| **S29GL-T**                       | **Yes—sector/special** | Advanced sector protection                                                 |           **Yes/configurable** |
| **S29GL01G/512/256/128 families** | **Yes—sector/special** | Advanced protection registers                                              |           **Yes/configurable** |

Important distinctions:

* **DYB — Dynamic Protection Bit:** volatile; cleared on reset or power cycle.
* **PPB — Persistent Protection Bit:** nonvolatile per-sector protection.
* **PPB Lock Bit:** controls whether PPBs can be altered.
* **Password mode:** permits protected control through a password.
* **Persistent protection mode:** may make a programmed configuration effectively permanent.
* **OTP/Secure Silicon Region:** may have a permanently programmable lock bit.

The S29GL-P Autoselect mode exposes sector-protection information. ([Infineon][2]) The S29GL-S family can report the combined PPB/DYB sector state, and also provides permanently lockable OTP regions. ([Infineon][3])

# 5. Macronix MX29 families

## Classic 5 V MX29F

| Family                            |      Readable? |       Permanent? | Notes                                     |
| --------------------------------- | -------------: | ---------------: | ----------------------------------------- |
| **MX29F010 / F020 / F040**        | **Yes—sector** |      Normally no | Sector Protect Verify in Auto Select mode |
| **MX29F001 / F002**               | **Yes—sector** |      Normally no | Boot-sector devices                       |
| **MX29F100 / F200 / F400 / F800** | **Yes—sector** |      Normally no | Top/bottom boot variants                  |
| **MX29F1610 / F1615**             | **Yes—sector** | Device-dependent | Check word/byte-mode command addressing   |

Macronix’s current MX29F200C and MX29F400C documentation explicitly includes **sector protect verify**. ([macronix.com][4])

## Low-voltage and later families

| Family           |              Readable? | Protection type                                        |           Permanent? |
| ---------------- | ---------------------: | ------------------------------------------------------ | -------------------: |
| **MX29LVxxx**    |         **Yes—sector** | Sector protection                                      |   Usually reversible |
| **MX29GLxxxE/F** | **Yes—sector/special** | Sector Protection Bits, lock register, security sector | **Yes/configurable** |
| **MX29GAxxx**    |        **Yes—special** | Advanced sector/security protection                    |     Device-dependent |
| **MX29LAxxx**    |         **Yes—sector** | Sector protection                                      |   Usually reversible |

Later MX29GL parts include **sector-lock-status verification**, security-sector indicator bits and advanced protection mechanisms. ([macronix.com][5])

# 6. STMicroelectronics M29 families

| Family                           |              Readable? | Protection type                          |               Permanent? |
| -------------------------------- | ---------------------: | ---------------------------------------- | -----------------------: |
| **M29F010 / F020 / F040 / F080** |         **Yes—sector** | Sector protection                        |       Usually reversible |
| **M29F002 / F200 / F400 / F800** |         **Yes—sector** | Boot and normal sectors                  |       Usually reversible |
| **M29Wxxx**                      |         **Yes—sector** | Sector/block protection                  |       Usually reversible |
| **M29DWxxx**                     |         **Yes—sector** | Dual-bank block protection               |       Usually reversible |
| **M29EWxxx**                     | **Yes—sector/special** | Nonvolatile and volatile protection bits | **Some permanent modes** |
| **M29GLxxx**                     | **Yes—sector/special** | Advanced sector protection               |         Device-dependent |

Classic M29F devices generally use an AMD-compatible Electronic Signature or Autoselect mode. Protection status is usually returned by reading within the selected sector.

# 7. AMIC A29 families

| Family                           |              Readable? |       Permanent? | Notes                              |
| -------------------------------- | ---------------------: | ---------------: | ---------------------------------- |
| **A29F010 / F020 / F040 / F080** |         **Yes—sector** |      Normally no | AMD-compatible protection verify   |
| **A29F002 / F200 / F400 / F800** |         **Yes—sector** |      Normally no | Boot-sector variants               |
| **A29Lxxx**                      |         **Yes—sector** |      Normally no | Low-voltage devices                |
| **A29DLxxx**                     |         **Yes—sector** | Device-dependent | Dual-bank                          |
| **A29GLxxx**                     | **Yes—sector/special** | Device-dependent | Later advanced-protection families |

Be careful with inexpensive programmers: AMIC devices are frequently selected using an “equivalent” AMD or Macronix algorithm, but protection/unprotection details may differ.

# 8. EON EN29 families

| Family                            |              Readable? |       Permanent? | Notes                                |
| --------------------------------- | ---------------------: | ---------------: | ------------------------------------ |
| **EN29F010 / F020 / F040 / F080** |         **Yes—sector** |      Normally no | AMD-style Autoselect                 |
| **EN29F002 / F200 / F400 / F800** |         **Yes—sector** |      Normally no | Boot-sector devices                  |
| **EN29LVxxx**                     |         **Yes—sector** |      Normally no | Low-voltage                          |
| **EN29GLxxx**                     | **Yes—sector/special** | Device-dependent | Advanced protection on later devices |
| **EN29PLxxx**                     | **Yes—sector/special** | Device-dependent | Check exact generation               |

# 9. ISSI IS29 families

| Family                     |              Readable? |       Permanent? | Notes                    |
| -------------------------- | ---------------------: | ---------------: | ------------------------ |
| **IS29F010 / F020 / F040** |         **Yes—sector** |      Normally no | JEDEC/AMD-style          |
| **IS29LVxxx**              |         **Yes—sector** |      Normally no | Sector protection verify |
| **IS29GLxxx**              | **Yes—sector/special** | Device-dependent | Advanced protection      |

# 10. Alliance/ASD/PMC-compatible 29F parts

| Family                          |              Readable? | Notes                         |
| ------------------------------- | ---------------------: | ----------------------------- |
| **AS29F010 / F020 / F040**      | Usually **yes—sector** | Often AMD-compatible          |
| **PM29F002 / F004 and similar** |      Variant-dependent | Datasheet required            |
| **HY29Fxxx**                    | Usually **yes—sector** | Hynix AMD-compatible families |
| **GM29Fxxx**                    |      Variant-dependent | Clone/manufacturer-specific   |
| **KH29LVxxx**                   | Usually **yes—sector** | Samsung parallel NOR variants |

These secondary-source families need verification against the precise datasheet. Some parts implement read-compatible Autoselect but omit or modify the sector-protection programming algorithm.

# 11. Intel command-set parallel NOR

Intel parts generally do not use AMD’s `AA-55-90` Autoselect sequence. Later devices expose a **block lock-status register** or block-status read operation.

| Family                                   |                Readable? | Protection type                     |                                                                Permanent? |
| ---------------------------------------- | -----------------------: | ----------------------------------- | ------------------------------------------------------------------------: |
| **28F256 / 28F512 / 28F010 early parts** | Often **no or indirect** | Global hardware/software protection |                                                                Usually no |
| **28F001BX**                             |    **Yes—block/special** | Boot-block locking                  |                                                          Device-dependent |
| **28F002BC / 28F004BC**                  |            **Yes—block** | Block lock                          |                                                        Usually reversible |
| **28F008SA / 28F016SA**                  |            **Yes—block** | Block lock status                   |                                                        Usually reversible |
| **28F008SC / 28F016SC**                  |            **Yes—block** | Block locking                       |                                                        Usually reversible |
| **E28Fxxx / PA28Fxxx**                   |        Variant-dependent | Block locking                       |                                                          Device-dependent |
| **28F320J3 / 640J3 / 128J3**             |            **Yes—block** | Lock, lock-down                     | **Lock-down can be persistent until reset or permanent by configuration** |
| **28F160B3 / 320B3 / 640B3**             |            **Yes—block** | Block lock status                   |                                                          Device-dependent |
| **28F160C3 / 320C3 / 640C3**             |            **Yes—block** | Block locking                       |                                                          Device-dependent |
| **28FxxxP30/P33**                        |    **Yes—block/special** | Block lock bits, OTP                |                                                           **Yes for OTP** |
| **StrataFlash 28FxxxJ3/K3/L18/P30**      |    **Yes—block/special** | Block status and OTP                |                                                          Device-dependent |

Typical Intel-family query:

1. Issue `Read Identifier Codes`, often command `90h`.
2. Read the appropriate block address plus the lock-status offset.
3. Interpret lock and lock-down bits.

Do not use the AMD protection-query sequence on Intel-command-set parts.

# 12. Sharp LH28 families

Many Sharp devices are Intel-command-set compatible.

| Family                      |                       Readable? | Protection type          |         Permanent? |
| --------------------------- | ------------------------------: | ------------------------ | -----------------: |
| **LH28F008 / LH28F016**     | **Yes—block** on later versions | Block lock               | Usually reversible |
| **LH28F016S / 032S / 064S** |                   **Yes—block** | Lock status register     |   Device-dependent |
| **LH28FxxxSU**              |                   **Yes—block** | Block lock/lock-down     |   Device-dependent |
| **LH28FxxxBF/BJ**           |                   **Yes—block** | Boot-block lock          |   Device-dependent |
| **LH28FxxxPBT/PCT**         |           **Yes—block/special** | Block and OTP protection |   Device-dependent |

# 13. Micron MT28 families

| Family                       |             Readable? | Protection type                    |         Permanent? |
| ---------------------------- | --------------------: | ---------------------------------- | -----------------: |
| **MT28F008 / F016 / F032**   |         **Yes—block** | Lock configuration                 | Usually reversible |
| **MT28F128J3 / 256J3**       |         **Yes—block** | Intel-style lock status            |   Device-dependent |
| **MT28FxxxB3**               |         **Yes—block** | Boot-block and regular block locks |   Device-dependent |
| **MT28FxxxP20/P30**          | **Yes—block/special** | Lock bits and OTP                  |  **OTP permanent** |
| **MT28EW / MT28GU families** |       **Yes—special** | Advanced protection registers      |   Device-dependent |

# 14. SST parallel flash

This requires an important correction to the earlier broad answer: many common SST39 parts have **Software Data Protection**, but do **not** provide a readable per-sector protection bit because SDP is not sector locking.

## SST39SF/VF classic devices

| Family                           |             Readable lock state? | Protection mechanism                            |        Permanent? |
| -------------------------------- | -------------------------------: | ----------------------------------------------- | ----------------: |
| **SST39SF010A / SF020A / SF040** |         **No explicit lock bit** | SDP command sequence and hardware write inhibit |                No |
| **SST39VF010 / VF020 / VF040**   | Usually **no explicit lock bit** | SDP                                             |                No |
| **SST39VF080 / VF160 / VF320**   |      Usually no for ordinary SDP | SDP plus device-specific security features      | Variant-dependent |
| **SST39LF/VF families**          |               Usually no for SDP | Software protection                             |                No |

The common SST39SF010A/020A/040 datasheet describes hardware and software data protection, but not conventional individually lockable sectors with a sector-status query. ([Microchip][6])

## SST boot-block and special families

| Family                                     |             Readable? |                        Permanent? | Notes                              |
| ------------------------------------------ | --------------------: | --------------------------------: | ---------------------------------- |
| **SST39SF512 / 010 / 020 older revisions** |    Usually no for SDP |                                No | Check exact revision               |
| **SST49LFxxx Firmware Hub**                |       **Yes—special** |           Block-locking registers | Some locks may persist until reset |
| **SST49LF00x / 02x / 03x / 04x**           |         **Yes—block** |              Block lock registers | Device-dependent                   |
| **SST49LF008A**                            |         **Yes—block** |     Readable block-lock registers | Device-dependent                   |
| **SST49LF160C**                            | **Yes—block/special** |        Block locking and security | Device-dependent                   |
| **SST49PLxxx**                             |       **Yes—special** | Firmware-hub protection registers | Device-dependent                   |
| **SST55LDxxx flash-disk controllers**      |        Not comparable |                Controller-managed | N/A                                |

# 15. Atmel/Microchip AT29C families

| Family                        |             Readable? | Protection mechanism | Permanent? |
| ----------------------------- | --------------------: | -------------------- | ---------: |
| **AT29C256**                  | No explicit SDP state | Whole-device SDP     |         No |
| **AT29C512**                  | No explicit SDP state | Whole-device SDP     |         No |
| **AT29C010 / 010A**           | No explicit SDP state | Whole-device SDP     |         No |
| **AT29C020 / 020A**           | No explicit SDP state | Whole-device SDP     |         No |
| **AT29C040 / 040A**           | No explicit SDP state | Whole-device SDP     |         No |
| **AT29LV010 / LV020 / LV040** |            Usually no | SDP                  |         No |

These parts generally allow SDP to be enabled or disabled by command sequence, but do not provide a dedicated readable “SDP currently enabled” flag. Microchip describes AT29C020 as an EEPROM-like page-program flash family. ([Microchip][7])

# 16. Atmel/Microchip AT49 families

| Family                     |                        Readable? | Protection type                   |               Permanent? |
| -------------------------- | -------------------------------: | --------------------------------- | -----------------------: |
| **AT49F010 / F020 / F040** |                Variant-dependent | Boot-block lockout or sector lock |  Some versions permanent |
| **AT49F001 / F002**        | **Yes—special** on many variants | Boot-block lockout                |      **Often permanent** |
| **AT49F040A**              |             Check exact revision | Sector/boot protection            |        Variant-dependent |
| **AT49F080 / F8192**       |           **Yes—sector/special** | Sector lockout                    |        Variant-dependent |
| **AT49BV/LVxxx**           |           **Yes—sector/special** | Sector protection                 | Some lock bits permanent |
| **AT49SN/BV16x4 families** |                  **Yes—special** | Sector protection registers       |         Device-dependent |
| **AT49LHxxx Firmware Hub** |                    **Yes—block** | Block lock registers              |         Device-dependent |

AT49 protection varies substantially. Some devices have a boot-block lockout that, once activated, cannot be disabled. Others have sector-lock status reads and reversible locks.

# 17. Parallel EEPROM families: 28Cxxx

Parallel EEPROM SDP is usually **not the same as a readable lock bit**.

| Family                            |   Readable protection state? | Notes                                   |
| --------------------------------- | ---------------------------: | --------------------------------------- |
| **Atmel AT28C64B / AT28C256** (page-write EEPROMs; incl. BV/LV/HC/MC variants) | Usually no explicit SDP flag | SDP can be enabled/disabled |
| **Atmel AT28C16** (incl. AT28C16E/F) and **plain AT28C64** | No — no SDP command decoder at all | Earlier-generation byte-write parts; not SDP-capable, unlike AT28C64B/AT28C256 above |
| **Atmel AT28HC64 / HC256**        |                   Usually no | High-speed EEPROM with SDP              |
| **Microchip 28C64 / 28C256**      |                   Usually no | Device-specific SDP                     |
| **Xicor X28C64 / X28C256**        |                   Usually no | Some have software protection sequences |
| **Catalyst CAT28C64 / CAT28C256** |                   Usually no | SDP/write protection                    |
| **Winbond W28Cxxx**               |                   Usually no | Software write protection               |
| **SST28EE/29EE-type parts**       |                   Usually no | SDP rather than readable sector locks   |

For example, the AT28C256 has software-controlled data protection, but the datasheet does not define a readable status bit telling you whether SDP is active. ([Microchip][8])

# 18. Firmware Hub and LPC flash

These are electrically and logically different from ordinary address-bus parallel NOR, although many programmers support them.

| Family                                    |         Readable? | Protection type                      |       Permanent? |
| ----------------------------------------- | ----------------: | ------------------------------------ | ---------------: |
| **Intel 82802AB / 82802AC**               |     **Yes—block** | Readable block-lock registers        | Device-dependent |
| **SST49LF002 / 003 / 004 / 008**          |     **Yes—block** | Block-lock registers                 | Device-dependent |
| **Winbond W39V040 / V080**                |     **Yes—block** | Boot-block and sector lock registers | Device-dependent |
| **Winbond W39L040**                       | Variant-dependent | Sector/boot locks                    | Device-dependent |
| **Atmel AT49LH002 / LH004**               |     **Yes—block** | Block-lock registers                 | Device-dependent |
| **PMC Pm49FL002 / FL004**                 |     **Yes—block** | Block lock registers                 | Device-dependent |
| **EON EN29F002/EN29LV firmware variants** | Variant-dependent | Block/sector locking                 | Device-dependent |

Firmware Hub parts often memory-map protection registers into special address ranges. A normal “read chip” operation may accidentally dump the array while never showing the lock registers.

# Practical summary

## Families where readable lock status is normally expected

* AMD **Am29F / Am29LV / Am29DL**
* Fujitsu **MBM29F / MBM29LV**
* Macronix **MX29F / MX29LV / MX29GL**
* ST **M29F / M29W / M29EW**
* AMIC **A29F / A29L**
* EON **EN29F / EN29LV**
* Spansion/Cypress **S29AL / S29JL / S29GL**
* Intel/Sharp/Micron **block-erase command-set devices**
* Firmware Hub/LPC devices with block-lock registers
* Winbond **W29C020C** boot-block lock detection
* Winbond **W49F** sector-protection families
* Many Atmel **AT49F/BV** devices

## Families where ordinary protection state usually is not directly readable

* Atmel **AT29Cxxx**
* Atmel **AT28Cxxx**
* Common SST **SST39SFxxx**
* Common SST **SST39VFxxx**, where protection is only SDP
* Winbond **W29EE/ordinary SDP-only W29C variants**
* Most conventional parallel EEPROMs using SDP

## Families with potentially irreversible protection

* **W29C020C** boot-block lockout
* Some **AT49F/BV** boot-block lockout implementations
* Spansion/Cypress **PPB persistent-protection configurations**
* Spansion/Cypress and Macronix **OTP/security-sector locks**
* Intel/Sharp/Micron **OTP parameter regions**
* Certain block **lock-down** configurations
* Some Firmware Hub devices when hardware strap or lock-down policy prevents clearing

## Important programmer implementation rule

A programmer database should not use one generic field called `locked`. It should distinguish at least:

```text
protection_kind:
  none
  software_data_protection
  sector_protection
  block_lock
  boot_block_lock
  volatile_sector_bit
  persistent_sector_bit
  password_protection
  lock_down
  otp_region_lock

status_readable:
  yes
  no
  partial

unlockability:
  command_reversible
  high_voltage_reversible
  reset_reversible
  power_cycle_reversible
  password_reversible
  irreversible
  unknown
```

A part may have several simultaneously. For example, an S29GL device can have a readable volatile DYB, a readable persistent PPB, a PPB lock state and a permanently locked OTP region. A single “locked/unlocked” result would be misleading.

[1]: https://www.infineon.com/assets/row/public/documents/10/57/infineon-am29f002b-am29f002nb-2-megabit-256-k-x-8-bit-datasheet-additionaltechnicalinformation-en.pdf?fileId=8ac78c8c7d0d8da4017d0ed59d8b5539&utm_source=chatgpt.com "AM29F002B/AM29F002NB 2 Megabit (256 K x 8-Bit) Datasheet"
[2]: https://www.infineon.com/assets/row/public/documents/10/49/infineon-s29gl01gp-s29gl512p-s29gl256p-s29gl128p-1-gbit512256128-mbit-3-v-page-flash-with-90-nm-mirrorbit-process-technology-datasheet-en.pdf?fileId=8ac78c8c7d0d8da4017d0ed7850458a5&utm_source=chatgpt.com "S29GL01GP, S29GL512P, S29GL256P, S29GL128P 1 Gbit, 512, 256, 128 Mbit ..."
[3]: https://www.infineon.com/assets/row/public/documents/10/49/infineon-s29gl064s-64-mbit8-mbyte3-datasheet-en.pdf?utm_source=chatgpt.com "S29GL064S, 64 Mb (8 MB) GL-S MIRRORBIT flash, parallel, 3"
[4]: https://www.macronix.com/Lists/Datasheet/Attachments/8545/MX29F200C%20T-B%2C%205V%2C%202Mb%2C%20v2.1.pdf?utm_source=chatgpt.com "MX29F200C T/B - Macronix"
[5]: https://www.macronix.com/Lists/Datasheet/Attachments/8527/MX29GL256F%2C%203V%2C%20256Mb%2C%20v1.5.pdf?utm_source=chatgpt.com "MX29GL256F DATASHEET - Macronix"
[6]: https://ww1.microchip.com/downloads/aemDocuments/documents/MPD/ProductDocuments/DataSheets/SST39SF010A-SST39SF020A-SST39SF040-Data-Sheet-DS20005022.pdf?utm_source=chatgpt.com "SST39SF010A/SST39SF020A/SST39SF040 - 1-Mbit/2-Mbit/4-Mbit (x8) Multi ..."
[7]: https://www.microchip.com/en-us/product/AT29C020?utm_source=chatgpt.com "AT29C020 - Microchip Technology"
[8]: https://ww1.microchip.com/downloads/en/DeviceDoc/doc0006.pdf?utm_source=chatgpt.com "AT28C256 - Microchip Technology"
