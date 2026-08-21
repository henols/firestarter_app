"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Quick task 260807-kaq: `dev test` blank-check must run AFTER erase.

Unit-level ordering proof for `derive_plan`'s conditional blank-check
placement rule (see `<placement_rule>` in the quick task's PLAN.md). All
four placement cases are covered against the REAL on-disk chip database
(the same `EpromDatabase(skip_local_override=True)` idiom
`tests/test_chip_test.py`'s `_REAL_DB` fixture uses -- no serial I/O, no
mocking of the database itself):

  1. M8720 (protocol 0x08, EEPROM, FLAG_CAN_ERASE set) -- an executable
     erase step exists, so blank-check moves to AFTER erase and BEFORE the
     SDP leg's six-op contiguous terminal block.
  2. M8720, write_scope="none" -- untouched: no erase step is ever
     executable at write_scope="none" (case 2 requires write_execute), so
     blank-check stays at its historic position.
  3. AM27512 (UV-EPROM) -- untouched: a pre-write blank-check is genuinely
     actionable on an irrecoverable UV write, so case 4 applies regardless
     of write_scope.
  4. AT28C256 (protocol 0x0D, 28C family) -- Phase 153 (ERASE-03/ERASE-04)
     restored FLAG_CAN_ERASE for this family, so it now shares case 1's
     placement (blank-check moves to AFTER the now-supported erase step
     and BEFORE the SDP leg's six-op contiguous terminal block) while
     KEEPING an NA verdict with a family-fact reason (each page write
     auto-erases internally, so no step in the plan can ever leave the
     device blank), never the flag name.

This module was written and observed RED against the unmodified
`chip_test.py` BEFORE the fix landed (Task 2, TDD RED gate) -- see
260807-kaq-SUMMARY.md for the captured failure output.
"""

from firestarter.chip_test import (
    _SDP_LEG_STEP_ORDER,  # test-internal: the D-06 six-op order (v1.30 Phase 134)
    OP_BLANK_CHECK,
    OP_ERASE,
    OP_ID,
    OP_READ,
    derive_plan,
)
from firestarter.database import EpromDatabase

# A real, on-disk-database instance (skip_local_override=True: no
# ~/.firestarter override, no serial) -- the same module-level idiom
# tests/test_chip_test.py's `_REAL_DB` fixture uses.
_REAL_DB = EpromDatabase(skip_local_override=True)

# M8720: protocol 0x08, electrical-type EEPROM, FLAG_CAN_ERASE set -- the
# established erasable fixture across this suite (see tests/test_chip_test.py
# and tests/test_dev_test_cmd.py's own fixture comments). Gets a REAL,
# executable erase step at write_scope="full".
_CHIP_ERASABLE = "M8720"
# AM27512: electrical-type UV-EPROM (is_uv_eprom exact axis). A pre-write
# blank-check here is genuinely actionable -- the write is irrecoverable and
# only UV light erases.
_CHIP_UV = "AM27512"
# AT28C256: protocol 0x0D (EEPROM_POLL / 28C family) -- auto-erases per page
# during write. Phase 153 (ERASE-03/ERASE-04) restored FLAG_CAN_ERASE for
# this family (a real AN-0544B software chip erase now exists in
# `configure_eeprom28c`), so erase IS a real, executable, supported step
# for this chip -- but blank-check stays NA regardless: no step in the
# plan can ever leave the device blank (each page write auto-erases
# internally), which is a family fact about page-writes, not about
# whether erase itself is executable. Also one of the v1.30 SDP-ALLOW
# chips (43/84), so its write_scope="full" plan carries the six-step SDP
# leg too, after erase and the (still-NA) blank-check.
_CHIP_AUTO_ERASE_28C = "AT28C256"


def test_m8720_full_blank_check_moves_after_erase_before_sdp_leg():
    """Case 2: an executable erase step exists, so blank-check moves to
    immediately after it -- and the SDP leg (M8720 is a measured REFUSE
    chip, so its six ops are NA here, D-06/LEG-02) stays a contiguous
    terminal block strictly after blank-check."""
    plan = derive_plan(_CHIP_ERASABLE, _REAL_DB, write_scope="full")
    ops = [s.op for s in plan.steps]

    assert OP_ERASE in ops, "fixture setup error: M8720 must have an erase step"
    assert OP_BLANK_CHECK in ops

    blank_check_index = ops.index(OP_BLANK_CHECK)
    erase_index = ops.index(OP_ERASE)
    assert blank_check_index > erase_index, (
        f"blank-check (index {blank_check_index}) must run AFTER erase "
        f"(index {erase_index}) on an erasable part"
    )

    blank_check_step = next(s for s in plan.steps if s.op == OP_BLANK_CHECK)
    assert blank_check_step.supported is True

    for sdp_op in _SDP_LEG_STEP_ORDER:
        assert sdp_op in ops, f"fixture setup error: SDP leg op {sdp_op!r} missing"
        sdp_index = ops.index(sdp_op)
        assert sdp_index > blank_check_index, (
            f"SDP leg op {sdp_op!r} (index {sdp_index}) must stay strictly "
            f"after blank-check (index {blank_check_index}) -- the leg must "
            "remain a contiguous terminal block"
        )


def test_m8720_write_scope_none_is_unchanged():
    """Case 2 requires write_execute (an erase step is only ever
    'executable' when it was actually appended as a real step) -- at
    write_scope="none" no erase step runs, so blank-check stays at its
    historic position and the untouched-scope proof holds byte-for-byte."""
    plan = derive_plan(_CHIP_ERASABLE, _REAL_DB, write_scope="none")
    assert [s.op for s in plan.steps] == [OP_ID, OP_READ, OP_BLANK_CHECK]

    locked_ops = {op for op, _reason in plan.locked_destructive}
    assert locked_ops == {"write", "verify", "erase"}


def test_am27512_uv_blank_check_position_is_unchanged():
    """Case 4/UV: a pre-write blank-check is genuinely actionable on an
    irrecoverable UV write, so its index and supported status stay exactly
    as they were before this fix."""
    plan = derive_plan(_CHIP_UV, _REAL_DB, write_scope="full")
    ops = [s.op for s in plan.steps]
    assert ops.index(OP_BLANK_CHECK) == 2

    blank_check_step = next(s for s in plan.steps if s.op == OP_BLANK_CHECK)
    assert blank_check_step.supported is True


def test_at28c256_blank_check_moves_after_erase_but_stays_na():
    """Case 3, Phase 153 revision: protocol 0x0D auto-erases per page
    during write, so no step in this plan can ever leave the device
    blank -- blank-check stays NA, and its reason is still the family
    fact, never the internal flag name FLAG_CAN_ERASE. What changed
    (Phase 153, ERASE-03/ERASE-04): restoring FLAG_CAN_ERASE gave this
    family a real, supported, destructive erase step that now precedes
    blank-check, so the step blank-check sits behind actually exists --
    which is why blank-check MOVED from its old position (index 2) to
    its new one (index 5), immediately after erase (index 4) and before
    the SDP leg's six-op contiguous terminal block. AT28C256 now SHARES
    case 1's placement (blank-check after erase, before the SDP leg)
    while KEEPING case 3's verdict (unsupported/NA) -- this combination
    is exactly why this case still earns its own function rather than
    being folded into case 1."""
    plan = derive_plan(_CHIP_AUTO_ERASE_28C, _REAL_DB, write_scope="full")
    ops = [s.op for s in plan.steps]

    assert OP_ERASE in ops, "fixture setup error: AT28C256 must have an erase step"
    blank_check_index = ops.index(OP_BLANK_CHECK)
    erase_index = ops.index(OP_ERASE)
    assert blank_check_index == 5, (
        f"blank-check must sit at the exact measured index 5 (moved from "
        f"the old index 2), got {blank_check_index}"
    )
    assert erase_index == 4, (
        f"erase must sit at the exact measured index 4, got {erase_index}"
    )
    assert blank_check_index > erase_index, (
        f"blank-check (index {blank_check_index}) must run AFTER erase "
        f"(index {erase_index})"
    )

    blank_check_step = next(s for s in plan.steps if s.op == OP_BLANK_CHECK)
    assert blank_check_step.supported is False
    assert "FLAG_CAN_ERASE" not in blank_check_step.reason
    assert "0x0d" in blank_check_step.reason.lower() or "28c" in (
        blank_check_step.reason.lower()
    )

    for sdp_op in _SDP_LEG_STEP_ORDER:
        assert sdp_op in ops, f"fixture setup error: SDP leg op {sdp_op!r} missing"
        sdp_index = ops.index(sdp_op)
        assert sdp_index > blank_check_index, (
            f"SDP leg op {sdp_op!r} (index {sdp_index}) must stay strictly "
            f"after blank-check (index {blank_check_index}) -- the leg "
            "must remain a contiguous terminal block"
        )


def test_m8720_full_plan_has_exactly_one_blank_check_and_one_erase_step():
    """Non-vacuity leg: the index comparisons above cannot pass by accident
    on a duplicated op -- M8720's write_scope="full" plan has exactly one
    OP_BLANK_CHECK step and exactly one OP_ERASE step."""
    plan = derive_plan(_CHIP_ERASABLE, _REAL_DB, write_scope="full")
    ops = [s.op for s in plan.steps]
    assert ops.count(OP_BLANK_CHECK) == 1
    assert ops.count(OP_ERASE) == 1
