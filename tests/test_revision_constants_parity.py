"""
Project Name: Firestarter
Copyright (c) 2025 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 34 Plan 05 — RURP_HARDWARE_REVISIONS Python parity gate.

Hard pytest parity assertion enforcing the byte values of the Python
`REVISION_*` block in `firestarter/constants.py` against the firmware
enum at `firestarter/include/rurp_shield.h:25-31` (Phase 34 D-08
single-atomic-commit substrate; VALIDATION Dim 3 / Dim 6 stronger
coverage — Wave 0 optional toggle activated).

If a future firmware-side enum drift sneaks in without a matching
Python update, this test FAILs at pytest time so the cross-repo
invariant is enforced at commit-test time (not at runtime in the
field).

Phase 36 Plan 02 — Extend to COMMAND_*/FLAG_*/CTRL_* blocks (TEST-04).

Adds three `skipif`-guarded functions that assert all COMMAND_*, FLAG_*,
and CTRL_* Python constants against their hard-coded firmware-header
literals. The `skipif` guard keys on `firestarter/include/firestarter.h`
existence — if that header is absent the firmware checkout is not present
and the new assertions skip cleanly (host-only milestone; CI may not have
the firmware sub-repo). CTRL_* mirrors `firestarter/include/rurp_pinout.h`
(not `firestarter.h`); the same `firestarter.h` proxy covers both headers
since they live alongside each other in the firmware checkout (RESEARCH
Open Question 1, resolved).
"""

import pytest
from pathlib import Path

from firestarter.constants import (
    REVISION_0,
    REVISION_1,
    REVISION_2_0,
    REVISION_2_1,
    REVISION_2_2,
    REVISION_2_3,
    REVISION_UNKNOWN,
)

# ---------------------------------------------------------------------------
# Firmware-checkout presence guard (Phase 36 TEST-04 extension)
#
# The firmware sub-repo may be absent in CI environments. If firestarter.h is
# absent the three new parity functions skip cleanly. When present, rurp_pinout.h
# is always alongside it (same include/ directory), so this single proxy covers
# both headers (RESEARCH Open Question 1, resolved).
# ---------------------------------------------------------------------------
FIRMWARE_HEADER = (
    Path(__file__).parent.parent.parent / "firestarter" / "include" / "firestarter.h"
)
FW_ABSENT = not FIRMWARE_HEADER.exists()


def test_revision_byte_values_match_firmware_enum():
    """Assert each REVISION_* byte value matches the firmware enum at
    `firestarter/include/rurp_shield.h:25-31` (post-Plan-02 HEAD). This is
    the Phase 34 D-08 cross-repo parity invariant — drift on either side
    fails the gate at pytest time."""
    assert REVISION_0 == 0x00
    assert REVISION_1 == 0x01
    assert REVISION_2_0 == 0x02
    assert REVISION_2_1 == 0x03
    assert REVISION_2_2 == 0x04
    assert REVISION_2_3 == 0x05  # NEW Phase 34
    assert REVISION_UNKNOWN == 0xFE  # NEW Phase 34
    # 0xFF is reserved as the EEPROM-override-absent sentinel — NOT a REVISION_ value.


@pytest.mark.skipif(FW_ABSENT, reason="firestarter firmware checkout absent")
def test_command_values_match_firmware():
    """Assert each COMMAND_* Python constant matches the hard-coded literal from
    `firestarter/include/firestarter.h` (CMD_* defines). Phase 36 TEST-04 /
    D-11 extension — widens GATE-1.8c to the full command surface.

    COMMAND_DEV_ADDRESS (0x07) and COMMAND_DEV_REGISTERS (0x08) are inside
    `#ifdef DEV_TOOLS` in the firmware header. The Python side defines them
    unconditionally so the parity assertions below stand as Python-value-only
    checks (not against a header literal that may not be compiled in) — noted
    with a `#ifdef DEV_TOOLS in firmware` comment per RESEARCH Pitfall 7.
    """
    from firestarter.constants import (
        COMMAND_READ,
        COMMAND_WRITE,
        COMMAND_ERASE,
        COMMAND_BLANK_CHECK,
        COMMAND_CHECK_CHIP_ID,
        COMMAND_VERIFY,
        COMMAND_DEV_ADDRESS,
        COMMAND_DEV_REGISTERS,
        COMMAND_READ_VPP,
        COMMAND_READ_VPE,
        COMMAND_FW_VERSION,
        COMMAND_CONFIG,
        COMMAND_HW_VERSION,
    )

    assert COMMAND_READ == 0x01  # CMD_READ
    assert COMMAND_WRITE == 0x02  # CMD_WRITE
    assert COMMAND_ERASE == 0x03  # CMD_ERASE
    assert COMMAND_BLANK_CHECK == 0x04  # CMD_BLANK_CHECK
    assert COMMAND_CHECK_CHIP_ID == 0x05  # CMD_CHECK_CHIP_ID
    assert COMMAND_VERIFY == 0x06  # CMD_VERIFY
    # CMD_DEV_ADDRESS and CMD_DEV_REGISTER are #ifdef DEV_TOOLS in firmware —
    # assert Python values as standalone literals only:
    assert COMMAND_DEV_ADDRESS == 0x07  # #ifdef DEV_TOOLS in firmware
    assert COMMAND_DEV_REGISTERS == 0x08  # #ifdef DEV_TOOLS in firmware
    assert COMMAND_READ_VPP == 0x0B  # CMD_READ_VPP
    assert COMMAND_READ_VPE == 0x0C  # CMD_READ_VPE
    assert COMMAND_FW_VERSION == 0x0D  # CMD_FW_VERSION (D-09: confirmed present)
    assert COMMAND_CONFIG == 0x0E  # CMD_CONFIG
    assert COMMAND_HW_VERSION == 0x0F  # CMD_HW_VERSION


@pytest.mark.skipif(FW_ABSENT, reason="firestarter firmware checkout absent")
def test_flag_values_match_firmware():
    """Assert each FLAG_* Python constant matches the hard-coded literal from
    `firestarter/include/firestarter.h` (FLAG_* defines). Phase 36 TEST-04 /
    D-11 extension — widens GATE-1.8c to the full control-flag surface."""
    from firestarter.constants import (
        FLAG_FORCE,
        FLAG_CAN_ERASE,
        FLAG_SKIP_ERASE,
        FLAG_SKIP_BLANK_CHECK,
        FLAG_VPE_AS_VPP,
        FLAG_OUTPUT_ENABLE,
        FLAG_CHIP_ENABLE,
        FLAG_VERBOSE,
    )

    assert FLAG_FORCE == 0x01  # FLAG_FORCE
    assert FLAG_CAN_ERASE == 0x02  # FLAG_CAN_ERASE
    assert FLAG_SKIP_ERASE == 0x04  # FLAG_SKIP_ERASE
    assert FLAG_SKIP_BLANK_CHECK == 0x08  # FLAG_SKIP_BLANK_CHECK
    assert FLAG_VPE_AS_VPP == 0x10  # FLAG_VPE_AS_VPP
    assert FLAG_OUTPUT_ENABLE == 0x20  # FLAG_OUTPUT_ENABLE
    assert FLAG_CHIP_ENABLE == 0x40  # FLAG_CHIP_ENABLE
    assert FLAG_VERBOSE == 0x80  # FLAG_VERBOSE


@pytest.mark.skipif(FW_ABSENT, reason="firestarter firmware checkout absent")
def test_ctrl_values_match_firmware():
    """Assert each CTRL_* Python constant matches the hard-coded literal from
    `firestarter/include/rurp_pinout.h` (not `firestarter.h`).

    CTRL_* mirrors the HARDWARE_REVISION wide-layout branch of rurp_pinout.h
    Section 2 — the branch active when `#ifdef HARDWARE_REVISION` is defined.
    The `firestarter.h` skipif proxy is sufficient: rurp_pinout.h lives in the
    same `firestarter/include/` directory and is present whenever firestarter.h
    is present (RESEARCH Open Question 1, resolved).

    Phase 36 TEST-04 / D-11 extension — widens GATE-1.8c to the full
    control-register-bit surface (CTRL_* block in constants.py mirrors
    rurp_pinout.h per CLAUDE.md sync rule).
    """
    from firestarter.constants import (
        CTRL_VPP_VPE_DROP_ENABLE,
        CTRL_VPP_REGULATOR_ENABLE,
        CTRL_READ_WRITE,
        CTRL_ADDRESS_LINE_18,
        CTRL_ADDRESS_LINE_17,
        CTRL_VPP_P1_ENABLE,
        CTRL_VPE_ENABLE,
        CTRL_VPP_A9_ENABLE,
        CTRL_ADDRESS_LINE_16,
    )

    # HARDWARE_REVISION wide-layout branch (rurp_pinout.h §2 #else branch):
    assert CTRL_ADDRESS_LINE_16 == 0x001  # CTRL_ADDRESS_LINE_16 (wide layout)
    assert CTRL_VPP_A9_ENABLE == 0x002  # CTRL_VPP_A9_ENABLE
    assert CTRL_VPE_ENABLE == 0x004  # CTRL_VPE_ENABLE
    assert CTRL_VPP_P1_ENABLE == 0x008  # CTRL_VPP_P1_ENABLE
    assert CTRL_ADDRESS_LINE_17 == 0x010  # CTRL_ADDRESS_LINE_17
    assert CTRL_ADDRESS_LINE_18 == 0x020  # CTRL_ADDRESS_LINE_18
    assert CTRL_READ_WRITE == 0x040  # CTRL_READ_WRITE
    assert CTRL_VPP_REGULATOR_ENABLE == 0x080  # CTRL_VPP_REGULATOR_ENABLE
    assert (
        CTRL_VPP_VPE_DROP_ENABLE == 0x100
    )  # CTRL_VPP_VPE_DROP_ENABLE (wide layout, differs from legacy 0x01)
