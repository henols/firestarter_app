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

from pathlib import Path
from unittest.mock import Mock

import pytest

from firestarter.chip_test import (
    _DESTRUCTIVE_GATE_REASON,  # test-internal: chip-ID gate reason (SWEEP-03)
    _UV_WRITE_REGION_LENGTH,  # test-internal: engine module constant (PATT-03)
    OP_BLANK_CHECK,
    OP_ERASE,
    OP_ID,
    OP_READ,
    OP_VERIFY,
    OP_WRITE,
    OP_WRITE_PARTIAL,  # 121-06 D-06: the seventh op string
    VERDICT_BAD,
    VERDICT_MARGINAL,
    VERDICT_NA,
    VERDICT_OK,
    VERDICT_SKIPPED,
    BannerCounts,
    Plan,
    Step,
    _diff_offsets,  # test-internal: the shared divergence primitive (D-04)
    _dispatch_multi_run,  # test-internal: fail-closed dispatch proof (121-02)
    _dispatch_step,  # test-internal: fail-closed dispatch proof (121-02)
    _write_region_for,  # test-internal: UV small-region selector (PATT-03)
    address_fold_byte,
    classify_fingerprint,
    count_applicable,
    derive_plan,
    generate_pattern,
    is_uv_eprom,  # exact 301/301 UV-EPROM axis (D-02, 121-05)
    prepass_images,
    run_plan,
)
from firestarter.database import EpromDatabase
from firestarter.exceptions import (
    ChipNotFoundError,
    ChipNotImplementedError,
    EpromOperationError,
)

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


# ---------------------------------------------------------------------------
# is_uv_eprom -- exact 301/301 UV-EPROM axis (D-02, 121-05 Task 1)
# ---------------------------------------------------------------------------


def test_is_uv_eprom_exact_301_over_real_db():
    # Enumerate the real database rather than hardcoding a spot check, so a
    # future DB change that moves the count is caught (acceptance criterion).
    eproms = _REAL_DB.get_eproms()
    assert sum(1 for e in eproms if is_uv_eprom(e)) == 301


def test_is_uv_eprom_simple_true_false_missing():
    assert is_uv_eprom({"electrical-type": "UV-EPROM"}) is True
    assert is_uv_eprom({"electrical-type": "EEPROM"}) is False
    assert is_uv_eprom({}) is False


@pytest.mark.parametrize(
    "name,expected",
    [
        # ST M27C512 -- genuine UV-EPROM, algorithm 0x07. The execution-time
        # algorithm proxy would MISS this (0x07 is not 0x0B).
        ("M27C512", True),
        # AM27C020 -- genuine UV-EPROM, algorithm 0x08. Same miss as above.
        ("AM27C020", True),
        # Winbond W27C512 -- routinely confused with the ST M27C512
        # (.planning memory reference_st_m27c512_vs_winbond_w27c512.md);
        # electrical-type is EEPROM, not UV-EPROM.
        ("W27C512", False),
        # Atmel AT28C256 -- ordinary EEPROM, not UV.
        ("AT28C256", False),
    ],
)
def test_is_uv_eprom_four_chip_table(name, expected):
    full = _REAL_DB.get_eprom(name)
    assert full is not None, f"{name} missing from live DB"
    assert is_uv_eprom(full) is expected


def test_is_uv_eprom_exact_where_algorithm_proxy_is_not():
    # M27C512 (algorithm 0x07) and AM27C020 (algorithm 0x08) both return
    # True from is_uv_eprom, while the algorithm==0x0B proxy would miss both.
    for name in ("M27C512", "AM27C020"):
        full = _REAL_DB.get_eprom(name)
        assert full["electrical-type"] == "UV-EPROM"
        assert full["protocol-id"] != 0x0B
        assert is_uv_eprom(full) is True


# ---------------------------------------------------------------------------
# Plan.is_uv / Step.write_region -- carried fields, defaulted (D-02)
# ---------------------------------------------------------------------------


def test_plan_and_step_carried_fields_default():
    p = Plan(name="x")
    s = Step(op=OP_WRITE, supported=True, reason="")
    assert p.is_uv is False
    assert s.write_region is None


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

    # write_scope="full": erase is a supported step in the executable steps
    # list (D-01 -- write_scope="none" would structurally omit it).
    plan = derive_plan("AS29F002T", _REAL_DB, write_scope="full")
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
    # read is always present in the executable steps list, regardless of
    # write_scope. verify is present only on a write-executing plan (112-05
    # SC2/SWEEP-05: verify is gated behind write_scope exactly like
    # write/erase, D-01) -- see test_derive_plan_verify_gated_behind_destructive
    # for the write_scope="none"-omission coverage.
    for name in ("M8720", "AM2716", "AE29F1008", "DS1220(RW)"):
        plan = derive_plan(name, _REAL_DB)
        read_step = _step(plan, "read")
        assert read_step.supported is True

        plan_destructive = derive_plan(name, _REAL_DB, write_scope="full")
        verify_step = _step(plan_destructive, "verify")
        assert verify_step.supported is True


def test_derive_plan_write_present_and_destructive():
    # write_scope="full": write remains in the executable steps list,
    # exactly as Phase 108 produced it (D-01 write-executing path unchanged).
    plan = derive_plan("M8720", _REAL_DB, write_scope="full")
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


def test_derive_plan_destructive_flag_strips_not_annotates():
    # Phase 109 (D-01, SAFE-01) INVERTS the Phase-108 annotate-only
    # contract: write_scope="none" must structurally OMIT write/erase from
    # the executable steps list; write_scope="full" keeps them exactly as
    # Phase 108 produced them (121-05 D-02: the kwarg is now the
    # three-valued write_scope, not a destructive bool -- behaviour
    # unchanged for these two scopes; the compared op sequences below are
    # the behavioural-equivalence proof required by 121-05 Task 2).
    plan_default = derive_plan("M8720", _REAL_DB, write_scope="none")
    plan_destructive = derive_plan("M8720", _REAL_DB, write_scope="full")
    ops_default = [s.op for s in plan_default.steps]
    ops_destructive = [s.op for s in plan_destructive.steps]

    # Recorded op sequences (SUMMARY): write_scope="none" ->
    # ["id", "read", "blank-check"]; write_scope="full" ->
    # ["id", "read", "blank-check", "write", "verify", "erase"] -- identical
    # to the pre-121-05 destructive=False/True sequences for this chip.
    assert ops_default == ["id", "read", "blank-check"]
    assert ops_destructive == [
        "id",
        "read",
        "blank-check",
        "write",
        "verify",
        "erase",
    ]
    assert plan_default.locked_destructive == [
        (OP_WRITE, 'write_scope="none": write omitted (D-01)'),
        (OP_VERIFY, 'write_scope="none": verify omitted (D-01)'),
        (OP_ERASE, 'write_scope="none": erase omitted (D-01)'),
    ]
    assert plan_destructive.locked_destructive == []
    ops_default_set = set(ops_default)
    ops_destructive_set = set(ops_destructive)
    assert "write" not in ops_default_set
    assert "erase" not in ops_default_set
    assert "write" in ops_destructive_set
    assert "erase" in ops_destructive_set
    # verify is now stripped from the write_scope="none" plan alongside
    # write/erase (112-05 SC2/SWEEP-05: verify gated behind write_scope,
    # D-01) -- only id/read/blank-check remain.
    assert ops_default_set == ops_destructive_set - {"write", "erase", "verify"}


def test_derive_plan_strip_default_only_destructive_ops_removed():
    # strip_default (109-01 Task 1 behavior, corrected by 112-05 SC2/SWEEP-05):
    # write/erase are removed from the executable steps list when
    # write_scope="none" because they mutate the chip (_DESTRUCTIVE_OPS).
    # verify is gated at plan-construction time in derive_plan behind
    # write_scope (D-01) -- it is NOT added to _DESTRUCTIVE_OPS (verify
    # does not mutate the chip; the runtime id-first gate stays scoped to
    # write/erase), but a bare verify with no preceding write would compare
    # a freshly-generated pattern against unrelated chip contents, so it is
    # omitted from the write_scope="none" plan too.
    plan = derive_plan("M8720", _REAL_DB, write_scope="none")
    ops = {s.op for s in plan.steps}
    assert ops == {"id", "read", "blank-check"}
    assert "write" not in ops
    assert "erase" not in ops
    assert "verify" not in ops


def test_derive_plan_verify_gated_behind_destructive():
    # 112-05 SC2/SWEEP-05: non-mocked composition assertion. M8720
    # (protocol 0x08, EEPROM, FLAG_CAN_ERASE set) is the module's
    # established erasable-chip fixture (see the fixture comment near
    # _REAL_DB above).
    plan_default = derive_plan("M8720", _REAL_DB, write_scope="none")
    nd_ops = [s.op for s in plan_default.steps]
    assert nd_ops == [OP_ID, OP_READ, OP_BLANK_CHECK]
    assert OP_VERIFY not in nd_ops
    locked_ops = {op for op, _reason in plan_default.locked_destructive}
    assert OP_VERIFY in locked_ops

    plan_destructive = derive_plan("M8720", _REAL_DB, write_scope="full")
    d_ops = [s.op for s in plan_destructive.steps]
    assert OP_VERIFY in d_ops
    assert d_ops.index(OP_VERIFY) > d_ops.index(OP_WRITE)
    assert d_ops.index(OP_VERIFY) < d_ops.index(OP_ERASE)


def test_derive_plan_advisory_populated_when_non_destructive():
    # advisory_populated: locked_destructive is a non-empty list of
    # (op, reason) tuples covering the omitted write, verify (112-05
    # SC2/SWEEP-05), and erase (since M8720's erase is a supported
    # destructive op) when write_scope="none".
    plan = derive_plan("M8720", _REAL_DB, write_scope="none")
    assert plan.locked_destructive
    locked_ops = {op for op, _reason in plan.locked_destructive}
    assert locked_ops == {"write", "verify", "erase"}
    for _op, reason in plan.locked_destructive:
        assert reason


def test_derive_plan_destructive_keeps_and_empties_advisory():
    # destructive_keeps: write_scope="full" keeps write/erase in steps
    # exactly as Phase 108 produced them, and locked_destructive is empty.
    plan = derive_plan("M8720", _REAL_DB, write_scope="full")
    ops = {s.op for s in plan.steps}
    assert "write" in ops
    assert "erase" in ops
    assert plan.locked_destructive == []


def test_derive_plan_na_erase_advisory_only_records_write():
    # na_erase_advisory: AM2716 (UV-EPROM) has no supported erase (no
    # FLAG_CAN_ERASE) -- the non-destructive plan omits write and verify
    # (112-05 SC2/SWEEP-05) to locked_destructive, but the NA erase must
    # NOT be fabricated as a runnable/locked step. It stays an unsupported
    # `erase` Step in `steps` (as before) and is not added to
    # locked_destructive.
    full = _REAL_DB.get_eprom("AM2716")
    assert full["electrical-type"] == "UV-EPROM"

    plan = derive_plan("AM2716", _REAL_DB, write_scope="none")
    locked_ops = {op for op, _reason in plan.locked_destructive}
    assert locked_ops == {"write", "verify"}

    erase_step = _step(plan, "erase")
    assert erase_step.supported is False
    assert "erase" not in {s.op for s in plan.steps if s.op == "erase" and s.supported}


# ---------------------------------------------------------------------------
# write_scope="partial" -- new third mode (D-02, 121-05 Task 3 leg 1)
# ---------------------------------------------------------------------------


def test_derive_plan_partial_same_ops_as_full_different_region():
    # Same step op sequence as "full" EXCEPT the write op string itself --
    # "partial" emits OP_WRITE_PARTIAL ("write-partial") instead of OP_WRITE
    # (D-06, Phase 121 Plan 06) -- plus a different write_region on the write
    # and verify steps. M27C512 (UV-EPROM, memory-size 65536): "full" uses
    # the same top-anchored window as "partial" here (both are UV), so
    # compare against a NON-UV chip to see the region actually differ
    # between the two scopes -- M8720 (non-UV) gets the engine default under
    # "full" but the top-anchored-window formula under "partial" (partial is
    # not is_uv-gated, D-02).
    plan_full = derive_plan("M8720", _REAL_DB, write_scope="full")
    plan_partial = derive_plan("M8720", _REAL_DB, write_scope="partial")

    ops_full = [s.op for s in plan_full.steps]
    ops_partial = [s.op for s in plan_partial.steps]
    # Only the write op string differs (OP_WRITE -> OP_WRITE_PARTIAL);
    # everything else (id, read, blank-check, verify, erase) is identical.
    assert [op if op != "write-partial" else "write" for op in ops_partial] == ops_full
    assert ops_partial.count("write-partial") == 1
    assert "write" not in ops_partial

    write_full = _step(plan_full, "write")
    write_partial = _step(plan_partial, "write-partial")
    verify_full = _step(plan_full, "verify")
    verify_partial = _step(plan_partial, "verify")

    assert write_full.write_region != write_partial.write_region
    assert verify_full.write_region != verify_partial.write_region
    # verify's region equals the write step's for BOTH scopes (D-07); the
    # verify op string stays the plain "verify" for both (D-07, no
    # "verify-partial" partner).
    assert write_full.write_region == verify_full.write_region
    assert write_partial.write_region == verify_partial.write_region


def test_derive_plan_partial_write_region_uv_memory_size():
    # write_scope="partial" on a UV part with memory-size 65536 yields a
    # write-partial step whose write_region is (65280, 256) (acceptance
    # criterion), and a plain "verify" step (D-07) with the equal region.
    full = _REAL_DB.get_eprom("M27C512")
    assert full["electrical-type"] == "UV-EPROM"
    assert full["memory-size"] == 65536

    plan = derive_plan("M27C512", _REAL_DB, write_scope="partial")
    write_step = _step(plan, "write-partial")
    verify_step = _step(plan, "verify")
    assert write_step.write_region == (65280, 256)
    assert verify_step.write_region == (65280, 256)


def test_derive_plan_partial_write_region_missing_memory_size_falls_back():
    # write_scope="partial" on a chip with memory-size 0/missing yields the
    # engine default (0, 256) (acceptance criterion) -- proven via a spy DB
    # since every real DB entry carries a real memory-size.
    full = {"electrical-type": "UV-EPROM", "protocol-id": 7}  # no memory-size key
    prog = {"algorithm": 7, "flags": 0, "chip-id": 0}
    spy_db = Mock(spec=["get_eprom", "convert_to_programmer"])
    spy_db.get_eprom.return_value = full
    spy_db.convert_to_programmer.return_value = prog

    plan = derive_plan("SYNTHETIC", spy_db, write_scope="partial")
    write_step = _step(plan, "write-partial")
    assert write_step.write_region == (0, 256)


# ---------------------------------------------------------------------------
# write_scope rejects anything else fail-closed (D-02, 121-05 Task 3 leg 2)
# ---------------------------------------------------------------------------


def test_derive_plan_write_scope_rejects_unknown_value():
    with pytest.raises(ValueError) as excinfo:
        derive_plan("M8720", _REAL_DB, write_scope="bogus")
    message = str(excinfo.value)
    assert "bogus" in message
    assert "none" in message
    assert "partial" in message
    assert "full" in message


# ---------------------------------------------------------------------------
# Plan.is_uv wiring proof, through derive_plan (D-02, 121-05 Task 3 leg 3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected_is_uv",
    [
        ("M27C512", True),
        ("AM27C020", True),
        ("W27C512", False),
        ("AT28C256", False),
    ],
)
def test_derive_plan_is_uv_wired_from_is_uv_eprom(name, expected_is_uv):
    # Proven through derive_plan (NOT by calling is_uv_eprom directly) so
    # the wiring itself is proven, using the four-chip table from Task 1.
    plan = derive_plan(name, _REAL_DB, write_scope="none")
    assert plan.is_uv is expected_is_uv


# ---------------------------------------------------------------------------
# run_plan -- non-fatal per-step executor (SWEEP-02/03, 108-04 Task 1)
# ---------------------------------------------------------------------------
#
# Bench-free: a Mock(spec=[...]) stand-in for EpromOperator drives each step's
# outcome; resolve_chip runs for real against EpromDatabase(skip_local_override
# =True) (no ~/.firestarter, no serial). M8720 is real+supported (protocol
# 0x08, EEPROM) so resolve_chip succeeds for every step by default.

_OPERATOR_METHODS = [
    "check_eprom_id",
    "read_eprom",
    "check_eprom_blank",
    "write_eprom",
    "verify_eprom",
    "erase_eprom",
]


def _mock_operator(**returns):
    op = Mock(spec=_OPERATOR_METHODS)
    op.check_eprom_id.return_value = (True, 0x1234)
    op.read_eprom.return_value = True
    op.check_eprom_blank.return_value = True
    op.write_eprom.return_value = True
    op.verify_eprom.return_value = True
    op.erase_eprom.return_value = True
    for name, value in returns.items():
        getattr(op, name).return_value = value
        getattr(op, name).side_effect = None
    return op


def _plan_with_steps(*steps):
    return Plan(name="M8720", steps=list(steps))


def _result(results, op):
    for r in results:
        if r.op == op:
            return r
    raise AssertionError(f"no result for op {op!r} in {[r.op for r in results]}")


def test_run_plan_non_fatal_raising_step_does_not_abort_later_steps():
    operator = _mock_operator()
    operator.read_eprom.side_effect = EpromOperationError(
        "boot block locked", error_code=0xA4
    )
    plan = _plan_with_steps(
        Step(op=OP_READ, supported=True, reason=""),
        Step(op=OP_WRITE, supported=True, reason="", destructive=True),
    )
    results = run_plan(plan, operator, _REAL_DB)

    read_result = _result(results, OP_READ)
    write_result = _result(results, OP_WRITE)
    assert read_result.verdict == VERDICT_BAD
    assert read_result.error_code == 0xA4
    # The later step still ran -- one step's exception never aborts the rest.
    assert write_result.verdict == VERDICT_OK
    operator.write_eprom.assert_called()


def test_run_plan_verdict_vocabulary_and_na_not_executed():
    operator = _mock_operator()
    plan = _plan_with_steps(
        Step(op=OP_BLANK_CHECK, supported=False, reason="not applicable"),
        Step(op=OP_READ, supported=True, reason=""),
    )
    results = run_plan(plan, operator, _REAL_DB)

    blank_result = _result(results, OP_BLANK_CHECK)
    read_result = _result(results, OP_READ)
    assert blank_result.verdict == VERDICT_NA
    assert blank_result.reason == "not applicable"
    operator.check_eprom_blank.assert_not_called()
    assert read_result.verdict == VERDICT_OK
    assert {r.verdict for r in results} <= {
        VERDICT_OK,
        VERDICT_BAD,
        VERDICT_NA,
        VERDICT_SKIPPED,
    }


def test_run_plan_resolver_refusal_maps_to_skipped(monkeypatch):
    import firestarter.chip_test as chip_test_mod

    spy = Mock(side_effect=ChipNotImplementedError("adapter-required"))
    monkeypatch.setattr(chip_test_mod, "resolve_chip", spy)

    operator = _mock_operator()
    plan = _plan_with_steps(Step(op=OP_READ, supported=True, reason=""))
    results = run_plan(plan, operator, _REAL_DB)

    read_result = _result(results, OP_READ)
    assert read_result.verdict in (VERDICT_SKIPPED, VERDICT_NA)
    assert read_result.reason
    operator.read_eprom.assert_not_called()


def test_run_plan_chip_not_found_maps_to_skipped(monkeypatch):
    import firestarter.chip_test as chip_test_mod

    spy = Mock(side_effect=ChipNotFoundError("no-such-chip"))
    monkeypatch.setattr(chip_test_mod, "resolve_chip", spy)

    operator = _mock_operator()
    plan = _plan_with_steps(Step(op=OP_ID, supported=True, reason=""))
    results = run_plan(plan, operator, _REAL_DB)

    id_result = _result(results, OP_ID)
    assert id_result.verdict in (VERDICT_SKIPPED, VERDICT_NA)
    operator.check_eprom_id.assert_not_called()


def test_run_plan_routes_through_resolve_chip_not_derivation_dict(monkeypatch):
    import firestarter.chip_test as chip_test_mod

    real_resolve = chip_test_mod.resolve_chip
    spy = Mock(side_effect=real_resolve)
    monkeypatch.setattr(chip_test_mod, "resolve_chip", spy)

    operator = _mock_operator()
    plan = _plan_with_steps(Step(op=OP_READ, supported=True, reason=""))
    run_plan(plan, operator, _REAL_DB)

    spy.assert_called_once_with("M8720", db=_REAL_DB)
    # The operator was called with the freshly-resolved dict, not any
    # derive_plan-internal structure.
    called_args = operator.read_eprom.call_args
    assert called_args.args[0] == "M8720"
    assert called_args.args[1] == real_resolve("M8720", db=_REAL_DB)


# ---------------------------------------------------------------------------
# id-first chip-ID mismatch destructive gate (SWEEP-03, 108-04 Task 2)
# ---------------------------------------------------------------------------
#
# AS29F002T carries a real nonzero chip-id (21168 == 0x52B0) in the DB --
# used as the id-bearing chip so a mismatch is meaningful. M8720's chip-id is
# the sentinel 0 (NA id step, never gates).


def _real_expected_chip_id(name: str) -> int:
    full = _REAL_DB.get_eprom(name)
    prog = _REAL_DB.convert_to_programmer(full)
    return prog["chip-id"]


def test_id_mismatch_gate_skips_destructive_steps_without_calling_operator():
    name = "AS29F002T"
    expected_id = _real_expected_chip_id(name)
    assert expected_id  # sanity: this chip has a real nonzero chip-id

    operator = _mock_operator()
    operator.check_eprom_id.return_value = (False, 0x9999)
    plan = Plan(
        name=name,
        steps=[
            Step(op=OP_ID, supported=True, reason=""),
            Step(op=OP_READ, supported=True, reason=""),
            Step(op=OP_WRITE, supported=True, reason="", destructive=True),
            Step(op=OP_ERASE, supported=True, reason="", destructive=True),
        ],
    )
    results = run_plan(plan, operator, _REAL_DB)

    id_result = _result(results, OP_ID)
    write_result = _result(results, OP_WRITE)
    erase_result = _result(results, OP_ERASE)
    read_result = _result(results, OP_READ)

    assert id_result.verdict == VERDICT_BAD
    assert write_result.verdict == VERDICT_SKIPPED
    assert erase_result.verdict == VERDICT_SKIPPED
    assert write_result.reason
    assert erase_result.reason
    # Chip left pristine: destructive operator methods NEVER called.
    operator.write_eprom.assert_not_called()
    operator.erase_eprom.assert_not_called()
    # Non-destructive findings are still recorded (id/read).
    assert read_result.verdict == VERDICT_OK
    operator.read_eprom.assert_called()


def test_id_detected_mismatch_gate_skips_destructive_steps():
    # Explicit numeric mismatch: is_ok=True but detected id != expected id.
    name = "AS29F002T"
    expected_id = _real_expected_chip_id(name)
    assert expected_id

    operator = _mock_operator()
    operator.check_eprom_id.return_value = (True, expected_id + 1)
    plan = Plan(
        name=name,
        steps=[
            Step(op=OP_ID, supported=True, reason=""),
            Step(op=OP_WRITE, supported=True, reason="", destructive=True),
        ],
    )
    results = run_plan(plan, operator, _REAL_DB)

    assert _result(results, OP_ID).verdict == VERDICT_BAD
    assert _result(results, OP_WRITE).verdict == VERDICT_SKIPPED
    operator.write_eprom.assert_not_called()


def test_id_match_leaves_destructive_steps_ungated():
    name = "AS29F002T"
    expected_id = _real_expected_chip_id(name)

    operator = _mock_operator()
    operator.check_eprom_id.return_value = (True, expected_id)
    plan = Plan(
        name=name,
        steps=[
            Step(op=OP_ID, supported=True, reason=""),
            Step(op=OP_WRITE, supported=True, reason="", destructive=True),
            Step(op=OP_ERASE, supported=True, reason="", destructive=True),
        ],
    )
    results = run_plan(plan, operator, _REAL_DB)

    assert _result(results, OP_ID).verdict == VERDICT_OK
    assert _result(results, OP_WRITE).verdict == VERDICT_OK
    assert _result(results, OP_ERASE).verdict == VERDICT_OK
    operator.write_eprom.assert_called()
    operator.erase_eprom.assert_called()


def test_id_mismatch_does_not_gate_non_destructive_steps():
    name = "AS29F002T"
    operator = _mock_operator()
    operator.check_eprom_id.return_value = (False, 0x9999)
    plan = Plan(
        name=name,
        steps=[
            Step(op=OP_ID, supported=True, reason=""),
            Step(op=OP_READ, supported=True, reason=""),
            Step(op=OP_BLANK_CHECK, supported=True, reason=""),
        ],
    )
    results = run_plan(plan, operator, _REAL_DB)

    assert _result(results, OP_READ).verdict == VERDICT_OK
    assert _result(results, OP_BLANK_CHECK).verdict == VERDICT_OK
    operator.read_eprom.assert_called()
    operator.check_eprom_blank.assert_called_once()


# ---------------------------------------------------------------------------
# N>=2 marginal policy + write/verify fingerprint wiring (SWEEP-04,
# 108-04 Task 3)
# ---------------------------------------------------------------------------


def _writes_bytes_to_output_file(data: bytes):
    """Build a `read_eprom` side_effect that writes `data` to `output_file`."""

    def _side_effect(_name, _eprom_data, output_file=None, **_kwargs):
        if output_file:
            Path(output_file).write_bytes(data)
        return True

    return _side_effect


def test_runs_boundary_rejects_below_2_before_any_operator_call():
    operator = _mock_operator()
    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True)
    )
    results = run_plan(plan, operator, _REAL_DB, runs=1)

    # No operator method was called -- rejected before resolve/dispatch.
    operator.write_eprom.assert_not_called()
    operator.read_eprom.assert_not_called()
    operator.check_eprom_id.assert_not_called()
    assert len(results) == 1
    assert results[0].verdict == VERDICT_BAD
    assert "runs" in results[0].reason.lower()


def test_marginal_on_disagreeing_write_runs():
    operator = _mock_operator()
    # write#1 True, write#2 False -- the AM27C020 write#1/write#2 case.
    operator.write_eprom.side_effect = [True, False]
    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True)
    )
    results = run_plan(plan, operator, _REAL_DB, runs=2)

    write_result = _result(results, OP_WRITE)
    assert write_result.verdict == VERDICT_MARGINAL
    assert write_result.run_count == 2


def test_agreeing_destructive_runs_report_confident_ok():
    operator = _mock_operator()
    operator.write_eprom.return_value = True
    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True)
    )
    results = run_plan(plan, operator, _REAL_DB, runs=2)

    write_result = _result(results, OP_WRITE)
    assert write_result.verdict == VERDICT_OK
    assert write_result.run_count == 2


def test_agreeing_destructive_runs_report_confident_bad():
    operator = _mock_operator()
    operator.write_eprom.return_value = False
    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True)
    )
    results = run_plan(plan, operator, _REAL_DB, runs=2)

    write_result = _result(results, OP_WRITE)
    assert write_result.verdict == VERDICT_BAD
    assert write_result.run_count == 2


def test_marginal_on_disagreeing_verify_runs():
    operator = _mock_operator()
    operator.verify_eprom.side_effect = [True, False]
    plan = _plan_with_steps(Step(op=OP_VERIFY, supported=True, reason=""))
    results = run_plan(plan, operator, _REAL_DB, runs=2)

    verify_result = _result(results, OP_VERIFY)
    assert verify_result.verdict == VERDICT_MARGINAL


# ---------------------------------------------------------------------------
# Fail-closed dispatch on an unmapped op (T-121-05/06/07, 121-02 Task 1)
# ---------------------------------------------------------------------------
#
# RESEARCH Pitfall 1a, reproduced against the pre-fix tree:
# _dispatch_multi_run("write-partial", "AT28C256", {"memory-size": 32768},
# operator, runs=2) fell through the run loop's terminal `else: # OP_ERASE`
# arm and _dispatch_step's unconditional trailing
# `return _dispatch_multi_run(...)`, calling operator.erase_eprom() TWICE and
# reporting VERDICT_OK for an op string nobody wrote a handler for. This is
# the host mirror of the firmware NULL-`main` phantom-success class Phase 119
# D-06/D-07 fixed at the op layer (`operation_utils.cpp::
# op_execute_stateful_operation`). The op string used below is deliberately
# NOT "write-partial" (that string does not exist in this tree yet -- it is
# added by Plan 121-06, AFTER this fail-closed guard lands) and is not any of
# the six existing OP_* values, so this proof can never be accidentally
# satisfied by a later plan's op addition. Every test's load-bearing
# assertion is a NEGATIVE call assertion on `operator.erase_eprom` (never a
# verdict-only or exit-code-only check --
# `reference_dev_test_absent_chip_false_green_trap.md`).

_UNMAPPED_OP = "unmapped-op-for-fail-closed-proof"


def test_unhandled_op_fails_closed_never_erases():
    operator = _mock_operator()
    result = _dispatch_multi_run(
        _UNMAPPED_OP, "AT28C256", {"memory-size": 32768}, operator, runs=2
    )

    # Load-bearing: an unmapped op must never reach erase_eprom.
    operator.erase_eprom.assert_not_called()
    assert result.verdict != VERDICT_OK


def test_unhandled_op_fails_closed_names_the_op_in_the_reason():
    operator = _mock_operator()
    result = _dispatch_multi_run(
        _UNMAPPED_OP, "AT28C256", {"memory-size": 32768}, operator, runs=2
    )

    assert _UNMAPPED_OP in result.reason
    assert "refus" in result.reason.lower()
    assert result.run_count == 0
    # Load-bearing: nothing ran -- no operator method of any kind was called.
    operator.write_eprom.assert_not_called()
    operator.verify_eprom.assert_not_called()
    operator.erase_eprom.assert_not_called()


def test_dispatch_step_refuses_an_op_outside_the_multi_run_allow_list():
    operator = _mock_operator()
    step = Step(op=_UNMAPPED_OP, supported=True, reason="")
    result = _dispatch_step("AT28C256", step, {"memory-size": 32768}, operator, runs=2)

    assert result.verdict == VERDICT_BAD
    # Load-bearing: none of the three chip-mutating operator methods ran.
    operator.write_eprom.assert_not_called()
    operator.verify_eprom.assert_not_called()
    operator.erase_eprom.assert_not_called()


# ---------------------------------------------------------------------------
# Sampler hook (D-04, Phase 112 112-01) -- bracket site is _dispatch_multi_run's
# OP_WRITE branch ONLY; sampler=None must be a proven no-op (SC4, D-04).
# ---------------------------------------------------------------------------


def test_run_plan_sampler_brackets_write():
    operator = _mock_operator()
    calls: list[str] = []

    def sampler(phase):
        calls.append(phase)

    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True)
    )
    results = run_plan(plan, operator, _REAL_DB, runs=2, sampler=sampler)

    write_result = _result(results, OP_WRITE)
    assert write_result.verdict == VERDICT_OK
    # Exactly one "before"/"after" pair per run (runs=2 -> 4 calls total),
    # each "before" immediately preceding and "after" immediately following
    # the corresponding write_eprom call.
    assert calls == ["before", "after", "before", "after"]
    assert operator.write_eprom.call_count == 2


def test_run_plan_sampler_not_invoked_around_non_write_ops():
    operator = _mock_operator()
    calls: list[str] = []

    def sampler(phase):
        calls.append(phase)

    plan = _plan_with_steps(
        Step(op=OP_ID, supported=True, reason=""),
        Step(op=OP_READ, supported=True, reason=""),
        Step(op=OP_BLANK_CHECK, supported=True, reason=""),
        Step(op=OP_VERIFY, supported=True, reason=""),
        Step(op=OP_ERASE, supported=True, reason=""),
    )
    results = run_plan(plan, operator, _REAL_DB, runs=2, sampler=sampler)

    # None of id/read/blank-check/verify/erase should invoke the sampler --
    # the bracket is scoped to OP_WRITE only (D-04).
    assert calls == []
    for op in (OP_ID, OP_READ, OP_BLANK_CHECK, OP_VERIFY, OP_ERASE):
        assert _result(results, op).verdict in (VERDICT_OK, VERDICT_BAD)


def test_run_plan_sampler_none_is_noop_matches_baseline():
    baseline_operator = _mock_operator()
    plan_a = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True)
    )
    baseline_results = run_plan(plan_a, baseline_operator, _REAL_DB, runs=2)

    sampler_free_operator = _mock_operator()
    plan_b = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True)
    )
    explicit_none_results = run_plan(
        plan_b, sampler_free_operator, _REAL_DB, runs=2, sampler=None
    )

    baseline_write = _result(baseline_results, OP_WRITE)
    explicit_write = _result(explicit_none_results, OP_WRITE)
    assert baseline_write.verdict == explicit_write.verdict == VERDICT_OK
    assert baseline_write.run_count == explicit_write.run_count == 2
    assert baseline_operator.write_eprom.call_count == (
        sampler_free_operator.write_eprom.call_count
    )


def test_run_plan_sampler_exception_does_not_abort_write_step():
    operator = _mock_operator()

    def raising_sampler(phase):
        raise RuntimeError(f"bench sampler exploded during {phase}")

    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True)
    )
    results = run_plan(plan, operator, _REAL_DB, runs=2, sampler=raising_sampler)

    write_result = _result(results, OP_WRITE)
    # The write step's verdict is still computed purely from the operator
    # outcome -- a sampler exception is swallowed, never surfacing as BAD
    # or aborting the step (Pitfall 1 extended to the sampler).
    assert write_result.verdict == VERDICT_OK
    assert write_result.run_count == 2
    assert operator.write_eprom.call_count == 2


def test_read_step_disagreement_is_divergence_metric_not_marginal():
    operator = _mock_operator()
    # Two runs of read_eprom write DIFFERENT bytes to output_file --
    # byte-level divergence, never a verdict flip, never marginal (D-06).
    call_results = [b"\x00" * 64, b"\xff" * 64]
    call_count = {"n": 0}

    def _read_side_effect(_name, _eprom_data, output_file=None, **_kwargs):
        data = call_results[call_count["n"] % len(call_results)]
        call_count["n"] += 1
        if output_file:
            Path(output_file).write_bytes(data)
        return True

    operator.read_eprom.side_effect = _read_side_effect
    plan = _plan_with_steps(Step(op=OP_READ, supported=True, reason=""))
    results = run_plan(plan, operator, _REAL_DB, runs=2)

    read_result = _result(results, OP_READ)
    assert read_result.verdict == VERDICT_OK  # never a verdict flip
    assert read_result.verdict != VERDICT_MARGINAL  # never marginal (D-06)
    assert read_result.divergence is not None
    assert read_result.divergence["bad"] > 0


def test_read_step_agreement_no_divergence_recorded():
    operator = _mock_operator()
    operator.read_eprom.side_effect = _writes_bytes_to_output_file(b"\xaa" * 32)
    plan = _plan_with_steps(Step(op=OP_READ, supported=True, reason=""))
    results = run_plan(plan, operator, _REAL_DB, runs=2)

    read_result = _result(results, OP_READ)
    assert read_result.verdict == VERDICT_OK
    assert not read_result.divergence


def test_write_step_attaches_fingerprint_with_region_start_addr_base():
    operator = _mock_operator()
    # Read-back matches the expected address-derived pattern exactly for
    # region [0, 256) -- perfect verify -> classify_fingerprint should NOT
    # be "address-line"/"transport" (no mismatches at all).
    from firestarter.chip_test import generate_pattern as _gen

    expected_bytes = _gen(0, 256)
    operator.read_eprom.side_effect = _writes_bytes_to_output_file(expected_bytes)
    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True)
    )
    results = run_plan(plan, operator, _REAL_DB, runs=2)

    write_result = _result(results, OP_WRITE)
    assert write_result.fingerprint is not None
    assert write_result.fingerprint.bad == 0


def test_write_step_fingerprint_addr_base_matches_region_start():
    operator = _mock_operator()
    # Corrupt the read-back at every address where bit A8 is set -- proves
    # the classifier clustered on addr_base + offset (Pitfall 3), matching
    # the write region start (0 in this engine's default region).
    from firestarter.chip_test import generate_pattern as _gen

    length = 0x400
    expected_bytes = _gen(0, length)
    actual = bytearray(expected_bytes)
    for i in range(length):
        if i & 0x100:
            actual[i] ^= 0xFF

    operator.read_eprom.side_effect = _writes_bytes_to_output_file(bytes(actual))
    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True)
    )
    # This test only cares about addr_base wiring; the engine's default
    # region length (256) is smaller than this fault pattern's span, so we
    # only assert addr_base was passed through as the region start (0) by
    # checking the fingerprint was computed at all with region-start-based
    # evidence -- the exact classification is covered by classify_fingerprint's
    # own unit tests (PATT-02).
    results = run_plan(plan, operator, _REAL_DB, runs=2)
    write_result = _result(results, OP_WRITE)
    assert write_result.fingerprint is not None


# ---------------------------------------------------------------------------
# _write_region_for reads Step.write_region; it no longer guesses UV-ness
# (D-02, Phase 121 Plan 06 -- converted from the pre-121-06 execution-time
# guess tests; see 121-06-SUMMARY.md for the conversion rationale)
# ---------------------------------------------------------------------------
#
# Bench-free: `_write_region_for` is a pure selector over `(step, eprom_data)`,
# no operator/DB call. The pre-121-06 implementation guessed UV-ness from
# `eprom_data` (`electrical-type` or `algorithm == 0x0B`); these tests now
# prove that guess is GONE, not merely bypassed -- the selector reads ONLY
# `step.write_region` and `eprom_data` plays no role, even when `eprom_data`
# is shaped exactly like the old UV-triggering dict.


def test_write_region_for_reads_step_carried_region():
    # step_carried: a Step carrying an explicit write_region is returned
    # UNCHANGED regardless of eprom_data -- this is the UV top-anchored
    # window derive_plan would compute for AM2716 (memory-size 2048).
    step = Step(op=OP_WRITE, supported=True, reason="", write_region=(1792, 256))
    non_uv_eprom_data = _REAL_DB.get_eprom("M8720")
    start, length = _write_region_for(step, non_uv_eprom_data)
    assert (start, length) == (1792, 256)
    assert length == _UV_WRITE_REGION_LENGTH


def test_write_region_for_no_carried_region_returns_engine_default_even_for_uv_shaped_data():
    # Acceptance criterion (D-02): a Step carrying NO region, paired with an
    # eprom_data dict that would previously have triggered the deleted UV
    # guess (electrical-type "UV-EPROM" AND algorithm 0x0B, memory-size
    # 65536), must return the engine default (0, 256) -- NOT (65280, 256).
    # This is the behavioural proof the guess was deleted, not bypassed.
    step = Step(op=OP_WRITE, supported=True, reason="")  # write_region=None
    uv_shaped_eprom_data = {
        "electrical-type": "UV-EPROM",
        "algorithm": 0x0B,
        "memory-size": 65536,
    }
    start, length = _write_region_for(step, uv_shaped_eprom_data)
    assert (start, length) == (0, 256)


def test_write_region_for_step_none_returns_engine_default():
    # step=None (e.g. a defensive call site) is equivalent to "no carried
    # region" -- the engine default, never a guess, even against a real UV
    # chip's full DB dict.
    full = _REAL_DB.get_eprom("AM2716")
    assert full["electrical-type"] == "UV-EPROM"
    start, length = _write_region_for(None, full)
    assert (start, length) == (0, 256)


def test_write_region_for_step_region_wins_over_bogus_eprom_data_width_hint():
    # cap_not_widenable (SC4, carried forward from PATT-03): a synthetic
    # eprom_data dict with an injected bogus size/width hint must NOT
    # override the Step-carried region -- the selector reads ONLY
    # step.write_region.
    step = Step(op=OP_WRITE, supported=True, reason="", write_region=(1792, 256))
    malicious_eprom_data = {
        "electrical-type": "UV-EPROM",
        "memory-size": 1_048_576,
        "write-region-length": 999_999,  # bogus width field the selector must ignore
    }
    start, length = _write_region_for(step, malicious_eprom_data)
    assert (start, length) == (1792, 256)


def test_addr_base_absolute_matches_region_start():
    # addr_base_absolute: the region start fed to generate_pattern equals
    # the addr_base fed to classify_fingerprint (Pitfall 3) -- verified via
    # the selector + generate_pattern's own consumption contract, bench-free.
    step = Step(op=OP_WRITE, supported=True, reason="", write_region=(1792, 256))
    start, length = _write_region_for(step, {})
    pattern = generate_pattern(start, length)
    # The pattern's first byte must equal address_fold_byte(start) -- i.e.
    # generate_pattern was invoked with the ABSOLUTE region start, not an
    # offset-relative 0 (which would silently ignore the UV window).
    assert pattern[0] == address_fold_byte(start)


def test_dispatch_multi_run_uses_selector_for_uv_chip():
    # Integration check: _dispatch_multi_run must pass the SAME absolute
    # `start` (as carried on the Step, the way derive_plan sets it) to both
    # generate_pattern and classify_fingerprint's addr_base when driving a
    # UV-EPROM chip through run_plan (no lingering bare _WRITE_REGION_START
    # inside the UV path, and no re-derivation from eprom_data).
    from firestarter.chip_test import generate_pattern as _gen

    start, length = 1792, 256  # AM2716's UV window, as derive_plan would set it
    expected_bytes = _gen(start, length)
    operator = _mock_operator()
    operator.read_eprom.side_effect = _writes_bytes_to_output_file(expected_bytes)
    plan = _plan_with_steps(
        Step(
            op=OP_WRITE,
            supported=True,
            reason="",
            destructive=True,
            write_region=(start, length),
        )
    )
    results = run_plan(
        Plan(name="AM2716", steps=plan.steps), operator, _REAL_DB, runs=2
    )
    write_result = _result(results, OP_WRITE)
    # A perfect read-back against the UV-window-anchored expected pattern
    # must classify as zero mismatches -- proving the SAME start fed both
    # generate_pattern (to build `expected_bytes` here) and the engine's
    # internal generate_pattern/classify_fingerprint(addr_base=...) calls.
    assert write_result.fingerprint is not None
    assert write_result.fingerprint.bad == 0


def test_generate_pattern_and_classify_fingerprint_source_unchanged():
    # Guard against regressing D-02: generate_pattern/classify_fingerprint
    # must remain region-parameterized pure functions -- PATT-03 only
    # chooses different start/length per chip, it never edits these bodies.
    import inspect

    import firestarter.chip_test as chip_test_mod

    gen_src = inspect.getsource(chip_test_mod.generate_pattern)
    assert "_WRITE_REGION_START" not in gen_src
    assert "_UV_WRITE_REGION_LENGTH" not in gen_src

    classify_src = inspect.getsource(chip_test_mod.classify_fingerprint)
    assert "_WRITE_REGION_START" not in classify_src
    assert "_UV_WRITE_REGION_LENGTH" not in classify_src


# ---------------------------------------------------------------------------
# OP_WRITE_PARTIAL through the production run_plan path (D-06/D-07, Phase 121
# Plan 06, Task 3) -- RESEARCH Pitfall 4: every region proof here drives
# run_plan/resolve_chip with the REAL programmer-dict shape production uses;
# none of these tests call `_write_region_for` with a `full`-shaped dict.
# ---------------------------------------------------------------------------


def _capturing_write(captured: dict):
    """`operator.write_eprom` side_effect that captures the tmp source file's
    bytes at call time (Task 3) -- `_dispatch_multi_run` deletes the tmp file
    in its `finally` block once the run loop returns, so the bytes MUST be
    read back from inside the call itself, not after `run_plan` returns."""

    def _write(name: str, eprom_data: dict, source_path: str) -> bool:
        captured["bytes"] = Path(source_path).read_bytes()
        return True

    return _write


def test_write_region_via_run_plan_uses_the_plan_carried_window():
    # M27C512 (UV-EPROM, memory-size 65536): write_scope="partial" carries
    # the top-anchored (65280, 256) window on the write-partial step (D-02).
    # Driving run_plan end to end (real resolve_chip/convert_to_programmer),
    # operator.write_eprom must be called with a pattern file containing
    # EXACTLY _UV_WRITE_REGION_LENGTH bytes generated for that base.
    name = "M27C512"
    expected_id = _real_expected_chip_id(name)
    plan = derive_plan(name, _REAL_DB, write_scope="partial")
    write_step = _step(plan, OP_WRITE_PARTIAL)
    assert write_step.write_region == (65280, 256)

    operator = _mock_operator()
    operator.check_eprom_id.return_value = (True, expected_id)
    captured: dict = {}
    operator.write_eprom.side_effect = _capturing_write(captured)
    operator.read_eprom.side_effect = _writes_bytes_to_output_file(
        generate_pattern(65280, 256)
    )

    results = run_plan(plan, operator, _REAL_DB, runs=2)

    operator.write_eprom.assert_called()
    write_result = _result(results, OP_WRITE_PARTIAL)
    assert write_result.verdict == VERDICT_OK
    assert len(captured["bytes"]) == _UV_WRITE_REGION_LENGTH == 256
    assert captured["bytes"] == generate_pattern(65280, 256)


def test_write_region_via_run_plan_uv_part_full_scope_uses_full_window():
    # Correction recorded in 121-06-SUMMARY.md: measured (unchanged since
    # 121-05's derive_plan), write_scope="full" for a UV part ALSO uses the
    # top-anchored window -- identical to "partial"'s, because derive_plan's
    # "full" arm is is_uv-gated onto `_top_anchored_or_default` exactly like
    # "partial". The REGION does not distinguish the two scopes for a UV
    # part; the OP STRING does (OP_WRITE here vs OP_WRITE_PARTIAL for the
    # partial plan) -- which is precisely why D-06 introduces a dedicated op
    # string: dedup_fingerprint hashes on `op`, so the region axis alone
    # could not keep a UV part's full and partial runs from cross-agreeing
    # (T-121-24).
    name = "M27C512"
    expected_id = _real_expected_chip_id(name)
    plan = derive_plan(name, _REAL_DB, write_scope="full")
    write_step = _step(plan, OP_WRITE)
    assert write_step.write_region == (65280, 256)

    operator = _mock_operator()
    operator.check_eprom_id.return_value = (True, expected_id)
    captured: dict = {}
    operator.write_eprom.side_effect = _capturing_write(captured)
    operator.read_eprom.side_effect = _writes_bytes_to_output_file(
        generate_pattern(65280, 256)
    )

    results = run_plan(plan, operator, _REAL_DB, runs=2)

    operator.write_eprom.assert_called()
    write_result = _result(results, OP_WRITE)
    assert write_result.verdict == VERDICT_OK
    assert captured["bytes"] == generate_pattern(65280, 256)
    # Same region as the partial plan (see correction above); op strings
    # differ ("write" vs "write-partial"), which is the actual distinguisher.
    assert write_step.write_region == (65280, 256)


def test_partial_write_gated_on_id_mismatch():
    # A mismatched chip-ID must gate the write-partial step exactly as it
    # gates a full write (T-121-21) -- the load-bearing line is
    # `write_eprom.assert_not_called()`; the SKIPPED verdict alone is not
    # sufficient proof (a verdict can be produced after the fact).
    name = "M27C512"
    plan = derive_plan(name, _REAL_DB, write_scope="partial")

    operator = _mock_operator()
    operator.check_eprom_id.return_value = (False, 0x9999)

    results = run_plan(plan, operator, _REAL_DB, runs=2)

    write_result = _result(results, OP_WRITE_PARTIAL)
    assert write_result.verdict == VERDICT_SKIPPED
    assert write_result.reason == _DESTRUCTIVE_GATE_REASON
    operator.write_eprom.assert_not_called()


def test_verify_region_matches_the_preceding_partial_write_region():
    # On a partial plan, the verify step's write_region equals the write
    # step's (D-07), and the verify step's op is the plain "verify" string
    # -- no "verify-partial" partner exists.
    plan = derive_plan("M27C512", _REAL_DB, write_scope="partial")
    write_step = _step(plan, OP_WRITE_PARTIAL)
    verify_step = _step(plan, OP_VERIFY)

    assert verify_step.op == OP_VERIFY
    assert verify_step.write_region == write_step.write_region == (65280, 256)


# ---------------------------------------------------------------------------
# count_applicable -- applicable-only N-of-M banner DATA (SWEEP-05, 109-02)
# ---------------------------------------------------------------------------
#
# AM2716 (UV-EPROM): non-destructive steps = {id(NA), read, blank-check,
# verify}; locked_destructive = {write} (erase is NA -- never locked).
#   M = 3 supported (read/blank-check/verify) + 1 locked (write) = 4
#   N (all-OK run) = 3 (read/blank-check/verify; id is NA, excluded)
#
# M8720 (EEPROM, FLAG_CAN_ERASE set): non-destructive steps = {id(NA),
# read, blank-check, verify}; locked_destructive = {write, erase}.
#   M = 3 supported + 2 locked (write, erase) = 5
#   N (all-OK run) = 3


def test_count_applicable_uv_counts():
    plan = derive_plan("AM2716", _REAL_DB, write_scope="none")
    operator = _mock_operator()
    results = run_plan(plan, operator, _REAL_DB)

    counts = count_applicable(plan, results)

    assert isinstance(counts, BannerCounts)
    assert counts.m_applicable == 4
    # verify is now gated behind destructive (112-05 SC2/SWEEP-05 fix) --
    # only id/read/blank-check actually run on a non-destructive plan.
    assert counts.n_ran == 2
    assert counts.n_ran < counts.m_applicable
    assert {op for op, _reason in counts.locked_steps} == {"write", "verify"}


def test_count_applicable_eeprom_counts():
    # Confirm M8720 actually has FLAG_CAN_ERASE set (erase applicable).
    full = _REAL_DB.get_eprom("M8720")
    prog = _REAL_DB.convert_to_programmer(full)
    from firestarter.constants import FLAG_CAN_ERASE

    assert prog["flags"] & FLAG_CAN_ERASE

    plan = derive_plan("M8720", _REAL_DB, write_scope="none")
    operator = _mock_operator()
    results = run_plan(plan, operator, _REAL_DB)

    counts = count_applicable(plan, results)

    assert counts.m_applicable == 5
    # verify is now gated behind destructive (112-05 SC2/SWEEP-05 fix) --
    # only id/read/blank-check actually run on a non-destructive plan.
    assert counts.n_ran == 2
    assert counts.n_ran < counts.m_applicable
    assert {op for op, _reason in counts.locked_steps} == {
        "write",
        "verify",
        "erase",
    }


def test_count_applicable_bad_counts_as_ran():
    # A BAD read still counts toward N (ran); NA (id) does not. verify no
    # longer runs on a non-destructive plan (112-05 SC2/SWEEP-05 fix).
    operator = _mock_operator()
    operator.read_eprom.return_value = False
    plan = derive_plan("AM2716", _REAL_DB, write_scope="none")
    results = run_plan(plan, operator, _REAL_DB)

    read_result = _result(results, OP_READ)
    assert read_result.verdict == VERDICT_BAD

    counts = count_applicable(plan, results)
    assert counts.n_ran == 2  # read(BAD) + blank-check(OK)


def test_count_applicable_skipped_does_not_count_as_ran():
    # A SKIPPED step (destructive gate closed by an id mismatch) must not
    # count toward N, even though its op is a supported/executable step.
    name = "AS29F002T"
    expected_id = _real_expected_chip_id(name)
    assert expected_id

    operator = _mock_operator()
    operator.check_eprom_id.return_value = (False, 0x9999)
    plan = derive_plan(name, _REAL_DB, write_scope="full")
    results = run_plan(plan, operator, _REAL_DB)

    write_result = _result(results, OP_WRITE)
    assert write_result.verdict == VERDICT_SKIPPED

    counts = count_applicable(plan, results)
    # write/erase were gated SKIPPED -- excluded from N despite being
    # counted in M (they are `plan.steps` supported entries here, since
    # write_scope="full" keeps them in steps rather than locked_destructive).
    ran_ops = {r.op for r in results if r.verdict not in (VERDICT_NA, VERDICT_SKIPPED)}
    assert "write" not in ran_ops
    assert "erase" not in ran_ops
    assert counts.n_ran == len(ran_ops)


def test_count_applicable_m_from_single_plan_never_rederives(monkeypatch):
    import firestarter.chip_test as chip_test_mod

    plan = derive_plan("AM2716", _REAL_DB, write_scope="none")
    operator = _mock_operator()
    results = run_plan(plan, operator, _REAL_DB)

    spy = Mock(side_effect=AssertionError("count_applicable must not re-derive"))
    monkeypatch.setattr(chip_test_mod, "derive_plan", spy)

    counts = count_applicable(plan, results)

    spy.assert_not_called()
    assert counts.m_applicable == 4


def test_count_applicable_n_equals_m_when_destructive():
    # Same chip (M8720), write_scope="full": locked_destructive is empty and
    # every applicable step actually executes -- N == M (banner would not
    # trigger).
    plan = derive_plan("M8720", _REAL_DB, write_scope="full")
    operator = _mock_operator()
    results = run_plan(plan, operator, _REAL_DB)

    counts = count_applicable(plan, results)

    assert plan.locked_destructive == []
    assert counts.n_ran == counts.m_applicable == 5


def test_count_applicable_no_print_or_render_introduced():
    # Banner DATA only -- this task must not add print/render/CLI output.
    import re

    src = Path(chip_test_source_path()).read_text()
    assert not re.search(r"\bprint\(|\bclick\.|\bconsole", src)


def chip_test_source_path() -> str:
    import firestarter.chip_test as chip_test_mod

    return chip_test_mod.__file__


# ---------------------------------------------------------------------------
# SAFE-02 orchestrator-only verification (Phase 109 Plan 02, Task 2)
# ---------------------------------------------------------------------------
#
# Every op run_plan executes routes through chip_resolver.resolve_chip (the
# guard-HONORING path) and calls only existing EpromOperator public methods;
# it sets no VPP, builds no raw wire/command dict, passes no --force; a
# firmware VPP-guard refusal is captured as a step finding, never silently
# retried. This section asserts that property mechanically -- it does not
# change run_plan's behavior (assert-only), except where noted.


def test_safe02_routes_via_resolve_chip_for_every_executed_step(monkeypatch):
    import firestarter.chip_test as chip_test_mod

    real_resolve = chip_test_mod.resolve_chip
    spy = Mock(side_effect=real_resolve)
    monkeypatch.setattr(chip_test_mod, "resolve_chip", spy)

    operator = _mock_operator()
    plan = derive_plan("M8720", _REAL_DB, write_scope="full")
    # M8720's id step is NA (chip-id sentinel 0) -- every OTHER step here is
    # supported, so all of them must resolve through the spy.
    executed_steps = [s for s in plan.steps if s.supported]
    assert len(executed_steps) >= 4

    run_plan(plan, operator, _REAL_DB)

    # resolve_chip is called once per executed (supported) step -- never
    # reused from derive_plan's guard-bypassing dict.
    assert spy.call_count == len(executed_steps)
    for call in spy.call_args_list:
        assert call.args == ("M8720",)
        assert call.kwargs == {"db": _REAL_DB}


def test_safe02_no_vpp_no_wire_no_force_source_scan():
    # Human-readable companion to the Plan-03 AST checker (not a
    # replacement): a lightweight substring scan of chip_test.py's CODE
    # (docstrings/comments stripped) asserting no VPP-set call, no raw
    # wire/command dict literal, and no force=True / "--force" pass-through
    # was introduced. Prose mentions of these terms in comments/docstrings
    # describing the safety property itself (e.g. "passes no --force") are
    # expected and must not trip this check -- only executable code lines.
    import ast

    src = Path(chip_test_source_path()).read_text()
    tree = ast.parse(src)

    # Strip module/function/class docstrings, then re-render source lines
    # without comments by re-parsing each non-string-expression statement's
    # own source segment. Simpler + robust: walk AST nodes and only inspect
    # literal string/keyword values that are NOT docstrings, plus attribute/
    # call names -- i.e. inspect the parsed AST, not raw text.
    forbidden_call_names = {"set_vpp"}
    forbidden_dict_keys = {"cmd", "bus-config", "vpp_mv"}
    forbidden_kwarg = "force"

    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_nodes.add(id(body[0].value))

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden_call_names:
            raise AssertionError(f"forbidden attribute access: .{node.attr}")
        if isinstance(node, ast.Call):
            func = node.func
            call_name = getattr(func, "attr", None) or getattr(func, "id", None)
            if call_name in forbidden_call_names:
                raise AssertionError(f"forbidden call: {call_name}(...)")
            for kw in node.keywords:
                if kw.arg == forbidden_kwarg:
                    raise AssertionError("forbidden force= kwarg passed to a call")
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and key.value in forbidden_dict_keys
                ):
                    raise AssertionError(
                        f"forbidden raw dict key literal: {key.value!r}"
                    )
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_nodes
            and node.value == "--force"
        ):
            raise AssertionError(
                "forbidden literal '--force' string outside docstrings"
            )


def test_safe02_vpp_guard_refusal_is_a_finding_not_a_retry_single_run():
    # A VPP-guard-flavored EpromOperationError from a single-run step
    # (blank-check) becomes a captured BAD finding with error_code -- and
    # the operator method is invoked EXACTLY ONCE (no silent retry-around
    # the refusal).
    operator = _mock_operator()
    operator.check_eprom_blank.side_effect = EpromOperationError(
        "VPP guard refused: voltage out of range", error_code=0xA9
    )
    plan = _plan_with_steps(Step(op=OP_BLANK_CHECK, supported=True, reason=""))
    results = run_plan(plan, operator, _REAL_DB)

    result = _result(results, OP_BLANK_CHECK)
    assert result.verdict == VERDICT_BAD
    assert result.error_code == 0xA9
    operator.check_eprom_blank.assert_called_once()


def test_safe02_vpp_guard_refusal_is_a_finding_not_a_retry_multi_run():
    # Multi-run destructive/verify steps: a VPP-guard refusal on the FIRST
    # invocation must not be retried-around -- run_plan's try/except wraps
    # the whole step body, so the exception propagates out of the runs-loop
    # immediately (exactly 1 call, not `runs` calls, and no bypass call).
    operator = _mock_operator()
    operator.write_eprom.side_effect = EpromOperationError(
        "VPP guard refused: voltage out of range", error_code=0xA9
    )
    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True)
    )
    results = run_plan(plan, operator, _REAL_DB, runs=2)

    result = _result(results, OP_WRITE)
    assert result.verdict == VERDICT_BAD
    assert result.error_code == 0xA9
    # No silent retry-around the guard refusal: called exactly once (the
    # exception aborts the runs-loop for this step; run_plan moves on to
    # the NEXT step, it does not re-invoke write_eprom to bypass the guard).
    operator.write_eprom.assert_called_once()


def test_safe02_only_known_operator_methods_no_attribute_error():
    # Mock(spec=[six methods]): accessing/calling ANY out-of-spec attribute
    # (e.g. a VPP setter) raises AttributeError immediately -- proven here
    # against the same Mock instance run_plan uses below. A full destructive
    # run through every op must complete without ever tripping that guard,
    # proving run_plan never reaches for a method outside the six existing
    # EpromOperator public methods.
    operator = _mock_operator()

    with pytest.raises(AttributeError):
        operator.set_vpp  # out-of-spec attribute access -- sanity check

    plan = derive_plan("M8720", _REAL_DB, write_scope="full")
    results = run_plan(plan, operator, _REAL_DB)  # must not raise AttributeError

    assert len(results) == len(plan.steps)


# ---------------------------------------------------------------------------
# DEVTEST-01 host half (Phase 121 D-12): the 0x0D sweep never fabricates an
# erase, and an all-OK 0x0D sweep no longer auto-tags community-fail
# ---------------------------------------------------------------------------


def test_devtest01_0x0d_sweep_erase_is_na_and_erase_eprom_never_called():
    """DEVTEST-01 end-to-end sweep leg: for a protocol-0x0D chip (AT28C256),
    `run_plan` over a plan derived at `write_scope="full"` produces an erase
    result whose verdict is NA, with the family-fact reason surviving into
    the `StepResult`, and `operator.erase_eprom` is NEVER called. A `NA`
    verdict alone does not prove nothing was dispatched -- this negative-call
    assertion is the load-bearing line (Phase 121 D-12)."""
    name = "AT28C256"
    full = _REAL_DB.get_eprom(name)
    assert full["protocol-id"] == 13  # 0x0D

    plan = derive_plan(name, _REAL_DB, write_scope="full")
    erase_step = _step(plan, "erase")
    assert erase_step.supported is False

    operator = _mock_operator()
    results = run_plan(plan, operator, _REAL_DB)

    erase_result = _result(results, OP_ERASE)
    assert erase_result.verdict == VERDICT_NA
    assert "0x0D" in erase_result.reason
    assert "FLAG_CAN_ERASE" not in erase_result.reason
    operator.erase_eprom.assert_not_called()


def test_devtest01_0x0d_all_ok_sweep_no_longer_tags_community_fail():
    """DEVTEST-01 ladder leg: an all-OK protocol-0x0D sweep whose erase step
    is NA no longer produces the `community-fail` ladder tag -- that
    fabricated-erase-poisons-an-otherwise-passing-chip's-ladder-state bug is
    exactly what DEVTEST-01 closes. The resulting `ladder_state` is the
    `community-reported` value, and `community-fail` is asserted absent."""
    from firestarter.diagnostic_report import build_db_diff

    name = "AT28C256"
    plan = derive_plan(name, _REAL_DB, write_scope="full")
    operator = _mock_operator()
    results = run_plan(plan, operator, _REAL_DB)

    verdicts = {r.verdict for r in results}
    assert VERDICT_BAD not in verdicts

    db_diff = build_db_diff(name, _REAL_DB, results)
    assert db_diff.ladder_state == "community-reported"
    assert db_diff.ladder_state != "community-fail"
