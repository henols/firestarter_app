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
"""

from firestarter.constants import (
    REVISION_0,
    REVISION_1,
    REVISION_2_0,
    REVISION_2_1,
    REVISION_2_2,
    REVISION_2_3,
    REVISION_UNKNOWN,
)


def test_revision_byte_values_match_firmware_enum():
    """Assert each REVISION_* byte value matches the firmware enum at
    `firestarter/include/rurp_shield.h:25-31` (post-Plan-02 HEAD). This is
    the Phase 34 D-08 cross-repo parity invariant — drift on either side
    fails the gate at pytest time."""
    assert REVISION_0       == 0x00
    assert REVISION_1       == 0x01
    assert REVISION_2_0     == 0x02
    assert REVISION_2_1     == 0x03
    assert REVISION_2_2     == 0x04
    assert REVISION_2_3     == 0x05   # NEW Phase 34
    assert REVISION_UNKNOWN == 0xFE   # NEW Phase 34
    # 0xFF is reserved as the EEPROM-override-absent sentinel — NOT a REVISION_ value.
