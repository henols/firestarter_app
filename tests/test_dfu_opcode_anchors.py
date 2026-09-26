"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 127 Plan 03 -- HOST-06 / D-18: an independent opcode oracle for
`firestarter/py32_dfu.py`'s DFU and DfuSe wire constants (research finding 7,
`.planning/research/SUMMARY.md:217`).

**Why this module exists.** `tests/test_py32_dfu.py`'s 58 tests import the
DFU/DfuSe opcode constants from the module under test and use them as
*labels* inside sequencing assertions (e.g. `assert (DFUSE_ERASE_PAGE,
FLASH_BASE) in commands`) -- which proves the wire sequence is internally
self-consistent, but proves nothing about whether the constants themselves
are the values the specifications actually define. An implementation
asserted only against itself cannot catch a wrong opcode; this module writes
each opcode down independently, from the specification, with a citing
comment, and compares the module's constant against that independent value --
the same discipline Phase 126 D-05 used for its CRC32 known-answer vector
(`firestarter` repo, `tests/test_config_storage_dualslot.py`).

**C-2 -- this module is purely ADDITIVE (load-bearing).** D-18's original
wording ordered the "self-referential assertions" in `tests/test_py32_dfu.py`
"removed or converted". `127-RESEARCH.md` §C-2 measured that no such
assertion exists there: a targeted scan
(`grep -nE "assert\\s+(py32_dfu\\.)?(DFU|DFUSE|FLASH)_[A-Z_]+\\s*==\\s*(0x)?[0-9]"`)
over that file returns zero matches, and every mention of `DFUSE_ERASE_PAGE`,
`DFUSE_SET_ADDRESS`, `DFUSE_VERSION` and `FLASH_BASE` there is either an
import or a label inside a genuinely independent sequencing assertion --
exactly what D-18's own next sentence orders kept. This plan therefore
creates ONE new module and deletes or converts nothing in
`tests/test_py32_dfu.py`.

**Residual A1 (carried, not hidden).** Research (`.planning/research/SUMMARY.md`)
did not fetch UM1504 or the USB DFU 1.1 specification before this plan ran --
the anchored values were, at that point, merely *consistent with the module*,
which is weaker than *independent of it*. This plan (127-03, Task 1) made one
read-only lookup attempt against both documents:

* **USB DFU 1.1 Revision 1.1** ("USB Device Firmware Upgrade Specification"),
  the official PDF published by usb.org, WAS obtained and read directly
  (fetched 2026-08-01; 47 pages; sha256
  `bbe4a3341c3bfc80cc6ba31b676998c379dcc42602f4b2ca7c5ea8b8dccd5c0d` of the
  fetched file, recorded in `127-03-SUMMARY.md`). Table 3.2 "DFU
  Class-Specific Request Values" (page 10) and Table 4.2 "DFU Functional
  Descriptor" §4.1.3 (page 13) were read verbatim; the seven request codes,
  the functional-descriptor type and the `bitCanUpload` bit position anchored
  below are transcribed from that read, not merely copied from the module
  under test. This is a genuinely independent oracle for those values.
* **UM1504** (the Puya/ST DfuSe application note) was NOT obtained. Two
  read-only fetch attempts against `st.com` (the plausible host for this
  document) both failed at the network layer in this sandboxed environment
  (`curl`: "HTTP/2 stream 1 was not closed cleanly"; `wget`: timed out) --
  the failure is environmental (host unreachable from here), not a statement
  that the document does not exist. The four DfuSe-specific values below
  (`DFUSE_SET_ADDRESS`, `DFUSE_ERASE_PAGE`, `DFUSE_READ_UNPROTECT`,
  `DFUSE_VERSION`/`bcdDFUVersion`) and `FLASH_BASE` therefore remain
  **consistent-with-the-module rather than independently sourced** -- this is
  the genuine, surviving residual of A1. It is recorded here where a reader
  of this test meets it, and carried forward into Plan 127-12's honesty
  ledger; a future plan re-attempting the UM1504 fetch from a different
  network vantage point would fully discharge it.

**The `bitCanUpload` mask.** Plan 127-09 created the module's mask constant,
`_DFU_BIT_CAN_UPLOAD`. The last test below compares it with the anchored bit.

No skip marker of any kind lives in this module: it is a pure module-level
constant comparison, and it runs identically whether or not `pyusb` is
installed.
"""

from __future__ import annotations

from firestarter import py32_dfu

# Independent constant block.
#
# Every value below is written from the specification, with a citing
# comment naming the exact document and section/table -- never imported
# from `firestarter.py32_dfu` to build the expectation. `py32_dfu` is
# imported above ONLY to supply the *observed* value each test compares
# against.

# USB DFU 1.1 (usb.org "USB Device Firmware Upgrade Specification, Revision
# 1.1"), Section 3, Table 3.2 "DFU Class-Specific Request Values" (page 10 of
# 47 in the fetched PDF -- see this module's docstring, residual A1: this
# table WAS read directly, not merely trusted).
_ANCHORED_DFU_DETACH = 0
_ANCHORED_DFU_DNLOAD = 1
_ANCHORED_DFU_UPLOAD = 2
_ANCHORED_DFU_GETSTATUS = 3
_ANCHORED_DFU_CLRSTATUS = 4
_ANCHORED_DFU_GETSTATE = 5
_ANCHORED_DFU_ABORT = 6

# USB DFU 1.1 §4.1.3 "Run-Time DFU Functional Descriptor", Table 4.2, offset
# 1 (bDescriptorType) -- "21h DFU FUNCTIONAL descriptor type" (page 13,
# directly read). This is a DIFFERENT field, in a different wire context,
# from DFUSE_SET_ADDRESS below -- the two share the numeric value 0x21 by
# coincidence, and are anchored here as two visibly separate constants so a
# refactor conflating them fails one of the two assertions that use them
# (T-127-03-05).
_ANCHORED_DFU_FUNCTIONAL_DESCRIPTOR_TYPE = 0x21

_ANCHORED_BIT_CAN_UPLOAD_MASK = 0x02

# UM1504 (Puya/ST DfuSe application note) DfuSe command values, sent as a
# DNLOAD with wBlockNum == 0. NOT independently fetched this plan (residual
# A1, see docstring) -- consistent-with-the-module, not yet independently
# sourced. DFUSE_SET_ADDRESS shares the numeric value 0x21 with the DFU
# functional descriptor type above; see the note on that constant.
_ANCHORED_DFUSE_SET_ADDRESS = 0x21
_ANCHORED_DFUSE_ERASE_PAGE = 0x41
_ANCHORED_DFUSE_READ_UNPROTECT = 0x92

# UM1504 -- the bcdDFUVersion value that marks the ST DfuSe dialect. Same A1
# residual as the DfuSe commands above.
_ANCHORED_DFUSE_VERSION = 0x011A

_ANCHORED_FLASH_BASE = 0x08000000


# Assertions -- one test per group, comparing the module's constant against
# the independent literal above.


def test_dfu_request_codes_match_usb_dfu_11_table_3_2() -> None:
    """USB DFU 1.1 §3 Table 3.2: the seven bRequest values."""
    anchored = {
        "DFU_DETACH": _ANCHORED_DFU_DETACH,
        "DFU_DNLOAD": _ANCHORED_DFU_DNLOAD,
        "DFU_UPLOAD": _ANCHORED_DFU_UPLOAD,
        "DFU_GETSTATUS": _ANCHORED_DFU_GETSTATUS,
        "DFU_CLRSTATUS": _ANCHORED_DFU_CLRSTATUS,
        "DFU_GETSTATE": _ANCHORED_DFU_GETSTATE,
        "DFU_ABORT": _ANCHORED_DFU_ABORT,
    }
    for name, expected in anchored.items():
        actual = getattr(py32_dfu, name)
        assert actual == expected, (
            f"USB DFU 1.1 §3 Table 3.2: expected {name} == {expected:#04x}, "
            f"firestarter.py32_dfu.{name} == {actual:#04x}"
        )


def test_dfu_functional_descriptor_type_matches_usb_dfu_11_section_4_1_3() -> None:
    """USB DFU 1.1 §4.1.3 Table 4.2 offset 1: bDescriptorType == 0x21."""
    expected = _ANCHORED_DFU_FUNCTIONAL_DESCRIPTOR_TYPE
    actual = py32_dfu._DFU_FUNCTIONAL_DESCRIPTOR
    assert actual == expected, (
        "USB DFU 1.1 §4.1.3 Table 4.2 offset 1 (bDescriptorType): expected "
        f"{expected:#04x}, firestarter.py32_dfu._DFU_FUNCTIONAL_DESCRIPTOR == "
        f"{actual:#04x}"
    )


def test_dfuse_commands_match_um1504() -> None:
    """UM1504 DfuSe command values sent as a DNLOAD with wBlockNum == 0."""
    anchored = {
        "DFUSE_SET_ADDRESS": _ANCHORED_DFUSE_SET_ADDRESS,
        "DFUSE_ERASE_PAGE": _ANCHORED_DFUSE_ERASE_PAGE,
        "DFUSE_READ_UNPROTECT": _ANCHORED_DFUSE_READ_UNPROTECT,
    }
    for name, expected in anchored.items():
        actual = getattr(py32_dfu, name)
        assert actual == expected, (
            f"UM1504 DfuSe command: expected {name} == {expected:#04x}, "
            f"firestarter.py32_dfu.{name} == {actual:#04x}"
        )


def test_dfuse_version_matches_um1504() -> None:
    """UM1504: bcdDFUVersion == 0x011A marks the ST DfuSe dialect."""
    expected = _ANCHORED_DFUSE_VERSION
    actual = py32_dfu.DFUSE_VERSION
    assert actual == expected, (
        f"UM1504 bcdDFUVersion: expected {expected:#06x}, "
        f"firestarter.py32_dfu.DFUSE_VERSION == {actual:#06x}"
    )


def test_flash_base_matches_py32f071xb_memory_map() -> None:
    """PY32F071xB flash ORIGIN == 0x08000000 (UM1504 + the live linker script,
    127-RESEARCH.md §C-7/§Q2)."""
    expected = _ANCHORED_FLASH_BASE
    actual = py32_dfu.FLASH_BASE
    assert actual == expected, (
        f"PY32F071xB flash ORIGIN: expected {expected:#010x}, "
        f"firestarter.py32_dfu.FLASH_BASE == {actual:#010x}"
    )


def test_bit_can_upload_mask_matches_usb_dfu_11_section_4_1_3() -> None:
    """USB DFU 1.1 §4.1.3 Table 4.2 offset 2 (bmAttributes) bit 1
    (bitCanUpload): Plan 127-09 created `py32_dfu._DFU_BIT_CAN_UPLOAD`; this
    closes the handoff Plan 127-03 deliberately left open by comparing it
    against the independent literal anchored above."""
    expected = _ANCHORED_BIT_CAN_UPLOAD_MASK
    actual = py32_dfu._DFU_BIT_CAN_UPLOAD
    assert actual == expected, (
        "USB DFU 1.1 §4.1.3 Table 4.2 offset 2 (bmAttributes) bit 1 "
        f"(bitCanUpload): expected {expected:#04x}, "
        f"firestarter.py32_dfu._DFU_BIT_CAN_UPLOAD == {actual:#04x}"
    )
