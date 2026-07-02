"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Pytest unit tests for `firestarter/chip_test.py` (v1.21 Phase 108 PATT-01/02).

Pure, bench-free compute-layer tests: the address-derived pattern generator
(PATT-01, D-01/D-02) and the four-bucket byte-mismatch fingerprint
classifier (PATT-02, D-03/D-04). All tests operate on hand-built byte
arrays -- no serial I/O, no EpromOperator, no hardware.

Test taxonomy:

  Pattern generator (PATT-01)
    test_address_fold_byte_zero              -> 0
    test_address_fold_byte_high_bit_folds     -> A8 folds into low byte
    test_generate_pattern_region_parameterized -> len + per-byte derivation
    test_generate_pattern_high_base_differs   -> no full-chip assumption
    test_prepass_images                       -> (0x00*n, 0xFF*n)

  Shared byte-diff-offset helper (D-04 reuse target)
    test_diff_offsets_equal_arrays            -> zero diffs, 0.0 pct
    test_diff_offsets_known_positions         -> offsets [2, 5], pct
    test_diff_offsets_unequal_length          -> cmp_len = min(len_a, len_b)

  Fingerprint classifier (PATT-02)
    test_fp_blank_near_all_ff                 -> "blank/contact"
    test_fp_address_line_bit_a8               -> "address-line", line==8
    test_fp_address_line_absolute_addr_base   -> addr_base clustering
    test_fp_transport_scattered_repeatable    -> "transport"
    test_fp_indeterminate_ambiguous           -> "indeterminate" (never coerced)
    test_fingerprint_evidence_fields          -> evidence dict shape

References:
  - .planning/phases/108-test-plan-engine-address-derived-pattern-fingerprint/108-02-PLAN.md
  - .planning/phases/108-test-plan-engine-address-derived-pattern-fingerprint/108-RESEARCH.md
    §Deep-Dive 1 (Fingerprint Classifier Signature Math, D-04, PATT-02)
  - .planning/phases/108-test-plan-engine-address-derived-pattern-fingerprint/108-PATTERNS.md
    §Pure functions (copy verbatim) + Pattern 5 (byte-diff-offset reuse)
  - .planning/phases/108-test-plan-engine-address-derived-pattern-fingerprint/108-CONTEXT.md
    D-01/D-02/D-03/D-04
"""

from unittest.mock import Mock

from firestarter.chip_test import (
    _diff_offsets,  # test-internal: the shared divergence primitive (D-04)
    address_fold_byte,
    classify_fingerprint,
    derive_plan,
    generate_pattern,
    prepass_images,
)
from firestarter.database import EpromDatabase

# ---------------------------------------------------------------------------
# Pattern generator (PATT-01)
# ---------------------------------------------------------------------------


def test_address_fold_byte_zero():
    assert address_fold_byte(0) == 0


def test_address_fold_byte_high_bit_folds():
    # A8 (0x100) folds into the low byte: 0x100 -> 0x01
    assert address_fold_byte(0x100) == 0x01


def test_generate_pattern_region_parameterized():
    start, length = 0x2000, 32
    pattern = generate_pattern(start, length)
    assert len(pattern) == length
    for i in range(length):
        assert pattern[i] == address_fold_byte(start + i)


def test_generate_pattern_high_base_differs():
    # No full-chip assumption baked in -- a high base address changes the
    # pattern relative to offset 0.
    assert generate_pattern(0x8000, 16) != generate_pattern(0, 16)


def test_prepass_images():
    n = 10
    zeros, ffs = prepass_images(n)
    assert zeros == b"\x00" * n
    assert ffs == b"\xff" * n


# ---------------------------------------------------------------------------
# Shared byte-diff-offset helper (reused by classify_fingerprint, D-04)
# ---------------------------------------------------------------------------


def test_diff_offsets_equal_arrays():
    a = bytes([1, 2, 3, 4])
    b = bytes([1, 2, 3, 4])
    cmp_len, diff_offsets, pct, first = _diff_offsets(a, b)
    assert cmp_len == 4
    assert diff_offsets == []
    assert pct == 0.0
    assert first is None


def test_diff_offsets_known_positions():
    a = bytes([0, 0, 0, 0, 0, 0, 0, 0])
    b = bytearray(a)
    b[2] = 0xFF
    b[5] = 0xFF
    cmp_len, diff_offsets, pct, first = _diff_offsets(a, bytes(b))
    assert cmp_len == 8
    assert diff_offsets == [2, 5]
    assert first == 2
    assert pct == 100.0 * 2 / 8


def test_diff_offsets_unequal_length():
    a = bytes([1, 2, 3, 4, 5])
    b = bytes([1, 2, 9])
    # Only compares min(len_a, len_b) == 3, and does not raise.
    cmp_len, diff_offsets, pct, first = _diff_offsets(a, b)
    assert cmp_len == 3
    assert diff_offsets == [2]
    assert first == 2


# ---------------------------------------------------------------------------
# Fingerprint classifier (PATT-02, D-03/D-04)
# ---------------------------------------------------------------------------


def test_fp_blank_near_all_ff():
    length = 256
    expected = generate_pattern(0, length)
    actual = b"\xff" * length
    fp = classify_fingerprint(expected, actual)
    assert fp.classification == "blank/contact"
    assert fp.total == length
    assert fp.evidence["ff_ratio"] >= 0.98


def test_fp_address_line_bit_a8():
    # Flip the expected pattern at every address where bit A8 (0x100) is
    # set across a region spanning that bit boundary.
    start, length = 0, 0x400  # spans bit 8 and bit 9
    expected = generate_pattern(start, length)
    actual = bytearray(expected)
    for i in range(length):
        addr = start + i
        if addr & 0x100:
            actual[i] ^= 0xFF  # corrupt every byte where A8 is set
    fp = classify_fingerprint(expected, bytes(actual), addr_base=start)
    assert fp.classification == "address-line"
    assert fp.evidence["suspected_line"] == 8


def test_fp_address_line_absolute_addr_base():
    # Same fault pattern, but the region does NOT start at 0 -- proves the
    # classifier clusters on the ABSOLUTE address (addr_base + offset), not
    # the raw offset (Pitfall 3).
    addr_base = 0x8000
    length = 0x400
    expected = generate_pattern(addr_base, length)
    actual = bytearray(expected)
    for i in range(length):
        addr = addr_base + i
        if addr & 0x100:
            actual[i] ^= 0xFF
    fp = classify_fingerprint(expected, bytes(actual), addr_base=addr_base)
    assert fp.classification == "address-line"
    assert fp.evidence["suspected_line"] == 8


_SCATTERED_OFFSETS = [
    3,
    17,
    40,
    77,
    101,
    130,
    190,
    220,
    300,
    350,
    410,
    470,
    500,
    550,
    600,
    650,
]


def test_fp_transport_scattered_repeatable():
    # Scattered + non-repeatable mismatches (no dominant high bit) with
    # repeat_divergent=True (run1 != run2 on re-read) -> transport. Region
    # is large enough (1024 B) that no single high bit clusters >= 0.9 of
    # these hand-picked scattered offsets (verified: max clustering ~0.81).
    length = 1024
    expected = generate_pattern(0, length)
    actual = bytearray(expected)
    for o in _SCATTERED_OFFSETS:
        actual[o] ^= 0x01
    fp = classify_fingerprint(expected, bytes(actual), repeat_divergent=True)
    assert fp.classification == "transport"
    assert fp.evidence["repeat_divergent"] is True


def test_fp_indeterminate_ambiguous():
    # Mixed/ambiguous distribution: repeatable (repeat_divergent=False),
    # scattered (no dominant bit), and not near-all-0xFF -- never coerced
    # into a confident label.
    length = 1024
    expected = generate_pattern(0, length)
    actual = bytearray(expected)
    for o in _SCATTERED_OFFSETS:
        actual[o] ^= 0x01
    fp = classify_fingerprint(expected, bytes(actual), repeat_divergent=False)
    assert fp.classification == "indeterminate"


def test_fingerprint_evidence_fields():
    length = 64
    expected = generate_pattern(0, length)
    actual = bytearray(expected)
    actual[0] ^= 0xFF
    fp = classify_fingerprint(expected, bytes(actual), repeat_divergent=False)
    assert fp.total == length
    assert fp.bad == 1
    assert fp.bad_pct == 100.0 * 1 / length
    assert isinstance(fp.evidence, dict)
    assert "ff_ratio" in fp.evidence
    assert "repeat_divergent" in fp.evidence
    assert "first_offset" in fp.evidence


# ---------------------------------------------------------------------------
# derive_plan (SWEEP-01, 108-03 Task 1) -- guard-bypassing derivation path
# ---------------------------------------------------------------------------
#
# Real chips pulled from the shipped chip_database.json via
# EpromDatabase(skip_local_override=True) (no ~/.firestarter, no serial) --
# same seam as tests/test_validate_family_cmd.py. Names/protocols/chip-ids
# verified against the live DB this session (RESEARCH.md Deep-Dive 2):
#   AE29F1008    -- protocol 0x05 (flash4), Flash/EEPROM, FLAG_CAN_ERASE clear
#   AM2716       -- protocol 0x0B, UV-EPROM, chip-id sentinel 0 (no real id)
#   M8720        -- protocol 0x08, EEPROM, chip-id sentinel 0 (no real id)
#   AS29F002T    -- protocol 0x06, Flash/EEPROM, real nonzero chip-id (21168),
#                   FLAG_CAN_ERASE set (algorithm != 5)
#   DS1220(RW)   -- protocol 0x28, SRAM, blank-check must be NA
#   AT28C04,AT28HC04 -- support_status "adapter-required" (resolve_chip refuses)

_REAL_DB = EpromDatabase(skip_local_override=True)


def test_derive_plan_id_check_first():
    plan = derive_plan("M8720", _REAL_DB)
    assert plan.steps[0].op == "id"


def test_derive_plan_reads_via_get_eprom_and_convert_to_programmer_only():
    # A minimal spy DB exposing ONLY get_eprom/convert_to_programmer (no
    # resolve_chip, no get_eprom_config) -- proves derive_plan never reaches
    # for resolve_chip's guard.
    full = _REAL_DB.get_eprom("M8720")
    prog = _REAL_DB.convert_to_programmer(full)

    spy_db = Mock(spec=["get_eprom", "convert_to_programmer"])
    spy_db.get_eprom.return_value = full
    spy_db.convert_to_programmer.return_value = prog

    plan = derive_plan("M8720", spy_db)

    spy_db.get_eprom.assert_called_once_with("M8720")
    spy_db.convert_to_programmer.assert_called_once_with(full)
    assert plan.steps[0].op == "id"


def test_derive_plan_never_calls_resolve_chip(monkeypatch):
    # Belt-and-suspenders: patch resolve_chip in chip_resolver and assert it
    # is never invoked by derive_plan (Pitfall 2 / T-108-06).
    import firestarter.chip_resolver as chip_resolver_mod

    spy = Mock(side_effect=AssertionError("resolve_chip must not be called"))
    monkeypatch.setattr(chip_resolver_mod, "resolve_chip", spy)

    derive_plan("M8720", _REAL_DB)

    spy.assert_not_called()


def test_derive_bypasses_guard_for_non_supported_chip():
    # AT28C04 has support_status "adapter-required" -- resolve_chip would
    # raise ChipNotImplementedError, but derive_plan must still yield a
    # full plan because it never calls resolve_chip (SWEEP-01).
    name = "AT28C04,AT28HC04"
    raw_config, _manufacturer = _REAL_DB.get_eprom_config(name)
    assert raw_config.get("support_status") == "adapter-required"

    plan = derive_plan(name, _REAL_DB)  # must NOT raise ChipNotImplementedError

    assert len(plan.steps) > 0
    assert plan.steps[0].op == "id"


def test_derive_plan_flag_can_erase_imported_not_redefined():
    import firestarter.chip_test as chip_test_mod
    from firestarter.constants import FLAG_CAN_ERASE

    assert chip_test_mod.FLAG_CAN_ERASE is FLAG_CAN_ERASE
    assert FLAG_CAN_ERASE == 0x02


def test_derive_plan_unknown_chip_returns_empty_plan_with_reason():
    plan = derive_plan("NO-SUCH-CHIP-XYZ", _REAL_DB)
    assert plan.steps == []
    assert plan.reason


def test_derive_plan_no_runtime_classify_call():
    # grep-gate companion: derive_plan's source contains no call to
    # classify() (build-time only, tools/build_db.py).
    import inspect

    import firestarter.chip_test as chip_test_mod

    src = inspect.getsource(chip_test_mod.derive_plan)
    assert "classify(" not in src


# ---------------------------------------------------------------------------
# Protocol-driven op-inclusion rules (SWEEP-01, 108-03 Task 2)
# ---------------------------------------------------------------------------


def _step(plan, op):
    for s in plan.steps:
        if s.op == op:
            return s
    raise AssertionError(f"no step named {op!r} in plan: {[s.op for s in plan.steps]}")


def test_derive_plan_id_step_supported_when_chip_id_present():
    # AS29F002T has a real nonzero chip-id (21168).
    plan = derive_plan("AS29F002T", _REAL_DB)
    id_step = _step(plan, "id")
    assert id_step.supported is True


def test_derive_plan_id_step_na_when_chip_id_absent():
    # AM2716 (UV-EPROM, protocol 0x0B) carries the chip-id sentinel 0 in the
    # programmer dict -- nothing to compare against, so the id step is NA.
    plan = derive_plan("AM2716", _REAL_DB)
    id_step = _step(plan, "id")
    assert id_step.supported is False
    assert id_step.reason


def test_derive_plan_flash4_erase_na():
    # AE29F1008 -- protocol 0x05 (flash4), Flash/EEPROM. FLAG_CAN_ERASE is
    # deliberately clear for 0x05 (auto-erase per page; Pitfall 6).
    full = _REAL_DB.get_eprom("AE29F1008")
    assert full["protocol-id"] == 5
    assert full["electrical-type"] == "Flash/EEPROM"

    plan = derive_plan("AE29F1008", _REAL_DB)
    erase_step = _step(plan, "erase")
    assert erase_step.supported is False
    assert erase_step.reason


def test_derive_plan_uv_eprom_erase_na():
    # AM2716 -- UV-EPROM, no electrical erase; FLAG_CAN_ERASE never set for
    # UV-EPROM electrical-type.
    full = _REAL_DB.get_eprom("AM2716")
    assert full["electrical-type"] == "UV-EPROM"

    plan = derive_plan("AM2716", _REAL_DB)
    erase_step = _step(plan, "erase")
    assert erase_step.supported is False
    assert erase_step.reason


def test_derive_plan_eeprom_erase_supported_when_can_erase_set():
    # AS29F002T -- protocol 0x06, Flash/EEPROM -- FLAG_CAN_ERASE is set
    # (etype in {EEPROM, Flash/EEPROM} and algorithm != 5).
    full = _REAL_DB.get_eprom("AS29F002T")
    prog = _REAL_DB.convert_to_programmer(full)
    assert full["electrical-type"] == "Flash/EEPROM"
    assert prog["algorithm"] != 5
    from firestarter.constants import FLAG_CAN_ERASE

    assert prog["flags"] & FLAG_CAN_ERASE

    plan = derive_plan("AS29F002T", _REAL_DB)
    erase_step = _step(plan, "erase")
    assert erase_step.supported is True


def test_derive_plan_blank_check_na_for_sram_chip():
    # DS1220(RW) -- protocol 0x28, SRAM. derive_plan must own this NA
    # decision up front (RESEARCH nuance recommendation (a)), not rely on
    # the operator's own check_eprom_blank short-circuit.
    full = _REAL_DB.get_eprom("DS1220(RW)")
    assert full["electrical-type"] == "SRAM"

    plan = derive_plan("DS1220(RW)", _REAL_DB)
    blank_step = _step(plan, "blank-check")
    assert blank_step.supported is False
    assert blank_step.reason


def test_derive_plan_blank_check_supported_for_regular_eeprom():
    plan = derive_plan("M8720", _REAL_DB)
    blank_step = _step(plan, "blank-check")
    assert blank_step.supported is True


def test_derive_plan_read_and_verify_always_present():
    for name in ("M8720", "AM2716", "AE29F1008", "DS1220(RW)"):
        plan = derive_plan(name, _REAL_DB)
        read_step = _step(plan, "read")
        verify_step = _step(plan, "verify")
        assert read_step.supported is True
        assert verify_step.supported is True


def test_derive_plan_write_present_and_destructive():
    plan = derive_plan("M8720", _REAL_DB)
    write_step = _step(plan, "write")
    assert write_step.supported is True
    assert write_step.destructive is True


def test_derive_plan_erase_condition_checks_flag_and_protocol():
    # Structural check on the source: the erase-inclusion condition
    # references both FLAG_CAN_ERASE and a check against protocol 0x05 (or
    # an equivalent named constant) -- acceptance criterion for Task 2.
    import inspect

    import firestarter.chip_test as chip_test_mod

    src = inspect.getsource(chip_test_mod.derive_plan)
    assert "FLAG_CAN_ERASE" in src
    assert "0x05" in src or "0x5" in src or "PROTOCOL_FLASH4" in src


def test_derive_plan_destructive_flag_annotates_not_strips():
    # destructive=False must NOT strip write/erase from the plan -- the
    # plan-construction --destructive gate is Phase 109; here it only
    # annotates (Task 2 `done` criterion).
    plan_default = derive_plan("M8720", _REAL_DB, destructive=False)
    plan_destructive = derive_plan("M8720", _REAL_DB, destructive=True)
    ops_default = {s.op for s in plan_default.steps}
    ops_destructive = {s.op for s in plan_destructive.steps}
    assert "write" in ops_default
    assert "erase" in ops_default
    assert ops_default == ops_destructive
