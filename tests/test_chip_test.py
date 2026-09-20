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

  Shared byte-diff-offset helper (D-04 reuse target; retired 202-03 D-02 --
  the primitive now lives in `firestarter.compare.diff_summary`, exercised
  directly in `tests/test_compare.py`)
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

import inspect
from pathlib import Path
from unittest.mock import Mock

import pytest

from firestarter.chip_test import (
    _DEFAULT_REGION,
    _DESTRUCTIVE_GATE_REASON,
    _MAX_FULL_DEVICE_LENGTH,  # test-internal: 260821-wna D-E sanity ceiling
    _PROTOCOL_FLASH4,  # test-internal: reused protocol id constant
    _SDP_LEG_STEP_ORDER,
    _UV_WRITE_REGION_LENGTH,
    OP_BLANK_CHECK,
    OP_ERASE,
    OP_ID,
    OP_READ,
    OP_VERIFY,
    OP_WRITE,
    OP_WRITE_PARTIAL,
    REGION_POLICY_FIXED,  # test-internal: 260821-wna region-policy vocab
    REGION_POLICY_FULL_DEVICE,  # test-internal: 260821-wna region-policy vocab
    REGION_POLICY_UV_SLOT,  # test-internal: 260821-wna region-policy vocab
    STATUS_COMPLETE,
    STATUS_ERROR,
    STATUS_SKIP,
    VERDICT_BAD,
    VERDICT_MARGINAL,
    VERDICT_NA,
    VERDICT_OK,
    VERDICT_SKIPPED,
    Plan,
    Step,
    StepResult,
    WriteTarget,
    _aggregate_cycle_results,
    _dispatch_id,
    _dispatch_multi_run,  # test-internal: fail-closed dispatch proof (121-02)
    _dispatch_read,
    _dispatch_step,  # test-internal: fail-closed dispatch proof (121-02)
    _id_step_closes_gate,  # test-internal: destructive-write safety gate (178-02)
    _synthesized_match_fingerprint,
    _write_region_for,
    address_fold_byte,
    classify_fingerprint,
    count_applicable,
    derive_plan,
    generate_pattern,
    is_uv_eprom,
    mask_write_pattern,  # test-internal: 260821-wna D-A masking arithmetic
    prepass_images,
    run_plan,
    run_status,
)
from firestarter.compare import diff_summary
from firestarter.database import EpromDatabase
from firestarter.exceptions import (
    ChipNotFoundError,
    ChipNotImplementedError,
    EpromOperationError,
    SerialError,
)
from firestarter.sdp_capability import sdp_capability_for_entry


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


def test_diff_offsets_unequal_length():
    # `_diff_offsets` retired 202-03 (D-02); the primitive is now
    # `compare.diff_summary`. `test_diff_offsets_equal_arrays` and
    # `test_diff_offsets_known_positions` (the two siblings this test used
    # to sit beside) are retired outright rather than re-pointed -- their
    # coverage is superseded by the direct `diff_summary` unit tests in
    # `tests/test_compare.py`. This one survives because it is also the
    # in-module proof, through `chip_test`'s own import surface, that
    # unequal-length inputs compare over the common prefix and never raise.
    a = bytes([1, 2, 3, 4, 5])
    b = bytes([1, 2, 9])
    # Only compares min(len_a, len_b) == 3, and does not raise.
    summary = diff_summary(a, b)
    assert summary.cmp_len == 3
    assert summary.bad == 1
    assert summary.first_offset == 2


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


def test_plan_and_step_carried_fields_default():
    p = Plan(name="x")
    s = Step(op=OP_WRITE, supported=True, reason="")
    assert p.is_uv is False
    assert s.write_region is None


def test_derive_plan_id_check_first():
    plan = derive_plan("M8720", _REAL_DB, write_scope="full")
    assert plan.steps[0].op == "id"


def test_derive_plan_reads_via_get_eprom_and_convert_to_programmer_only():
    full = _REAL_DB.get_eprom("M8720")
    prog = _REAL_DB.convert_to_programmer(full)

    spy_db = Mock(spec=["get_eprom", "convert_to_programmer"])
    spy_db.get_eprom.return_value = full
    spy_db.convert_to_programmer.return_value = prog

    plan = derive_plan("M8720", spy_db, write_scope="full")

    assert spy_db.get_eprom.call_count == 2, (
        "expected exactly 2 get_eprom calls (derive_plan's own read plus "
        f"sdp_capability's), got {spy_db.get_eprom.call_count}"
    )
    for call in spy_db.get_eprom.call_args_list:
        assert call.args == ("M8720",)
    spy_db.convert_to_programmer.assert_called_once_with(full)
    assert plan.steps[0].op == "id"


def test_derive_plan_never_calls_resolve_chip(monkeypatch):
    # Belt-and-suspenders: patch resolve_chip in chip_resolver and assert it
    # is never invoked by derive_plan (Pitfall 2 / T-108-06).
    import firestarter.chip_resolver as chip_resolver_mod

    spy = Mock(side_effect=AssertionError("resolve_chip must not be called"))
    monkeypatch.setattr(chip_resolver_mod, "resolve_chip", spy)

    derive_plan("M8720", _REAL_DB, write_scope="full")

    spy.assert_not_called()


def test_derive_bypasses_guard_for_non_supported_chip():
    name = "AT28C04,AT28HC04"
    raw_config, _manufacturer = _REAL_DB.get_eprom_config(name)
    assert raw_config.get("support_status") == "adapter-required"

    # must NOT raise ChipNotImplementedError
    plan = derive_plan(name, _REAL_DB, write_scope="full")

    assert len(plan.steps) > 0
    assert plan.steps[0].op == "id"


def test_derive_plan_flag_can_erase_imported_not_redefined():
    import firestarter.chip_test as chip_test_mod
    from firestarter.constants import FLAG_CAN_ERASE

    assert chip_test_mod.FLAG_CAN_ERASE is FLAG_CAN_ERASE
    assert FLAG_CAN_ERASE == 0x02


def test_derive_plan_unknown_chip_returns_empty_plan_with_reason():
    plan = derive_plan("NO-SUCH-CHIP-XYZ", _REAL_DB, write_scope="full")
    assert plan.steps == []
    assert plan.reason


def test_derive_plan_no_runtime_classify_call():
    # grep-gate companion: derive_plan's source contains no call to
    # classify() (build-time only, tools/build_db.py).
    import inspect

    import firestarter.chip_test as chip_test_mod

    src = inspect.getsource(chip_test_mod.derive_plan)
    assert "classify(" not in src


def _step(plan, op):
    for s in plan.steps:
        if s.op == op:
            return s
    raise AssertionError(f"no step named {op!r} in plan: {[s.op for s in plan.steps]}")


def test_derive_plan_id_step_supported_when_chip_id_present():
    # AS29F002T has a real nonzero chip-id (21168).
    plan = derive_plan("AS29F002T", _REAL_DB, write_scope="full")
    id_step = _step(plan, "id")
    assert id_step.supported is True


def test_derive_plan_id_step_na_when_chip_id_absent():
    # AM2716 (UV-EPROM, protocol 0x0B) carries the chip-id sentinel 0 in the
    # programmer dict -- nothing to compare against, so the id step is NA.
    plan = derive_plan("AM2716", _REAL_DB, write_scope="partial")
    id_step = _step(plan, "id")
    assert id_step.supported is False
    assert id_step.reason


def test_derive_plan_flash4_erase_na():
    # AE29F1008 -- protocol 0x05 (flash4), Flash/EEPROM. FLAG_CAN_ERASE is
    # deliberately clear for 0x05 (auto-erase per page; Pitfall 6).
    full = _REAL_DB.get_eprom("AE29F1008")
    assert full["protocol-id"] == 5
    assert full["electrical-type"] == "Flash/EEPROM"

    plan = derive_plan("AE29F1008", _REAL_DB, write_scope="full")
    erase_step = _step(plan, "erase")
    assert erase_step.supported is False
    assert erase_step.reason


def test_derive_plan_uv_eprom_erase_na():
    # AM2716 -- UV-EPROM, no electrical erase; FLAG_CAN_ERASE never set for
    # UV-EPROM electrical-type.
    full = _REAL_DB.get_eprom("AM2716")
    assert full["electrical-type"] == "UV-EPROM"

    plan = derive_plan("AM2716", _REAL_DB, write_scope="partial")
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

    plan = derive_plan("AS29F002T", _REAL_DB, write_scope="full")
    erase_step = _step(plan, "erase")
    assert erase_step.supported is True


def test_derive_plan_blank_check_na_for_sram_chip():
    # DS1220(RW) -- protocol 0x28, SRAM. derive_plan must own this NA
    # decision up front (RESEARCH nuance recommendation (a)), not rely on
    # the operator's own check_eprom_blank short-circuit.
    full = _REAL_DB.get_eprom("DS1220(RW)")
    assert full["electrical-type"] == "SRAM"

    plan = derive_plan("DS1220(RW)", _REAL_DB, write_scope="full")
    blank_step = _step(plan, "blank-check")
    assert blank_step.supported is False
    assert blank_step.reason


def test_derive_plan_blank_check_supported_for_regular_eeprom():
    plan = derive_plan("M8720", _REAL_DB, write_scope="full")
    blank_step = _step(plan, "blank-check")
    assert blank_step.supported is True


def test_derive_plan_read_and_verify_always_present():
    for name in ("M8720", "AM2716", "AE29F1008", "DS1220(RW)"):
        scope = "partial" if name == "AM2716" else "full"
        plan = derive_plan(name, _REAL_DB, write_scope=scope)
        read_step = _step(plan, "read")
        assert read_step.supported is True

        plan_destructive = derive_plan(name, _REAL_DB, write_scope="full")
        verify_step = _step(plan_destructive, "verify")
        assert verify_step.supported is True


def test_derive_plan_write_present_and_destructive():
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
    plan_destructive = derive_plan("M8720", _REAL_DB, write_scope="full")
    ops_destructive = [s.op for s in plan_destructive.steps]

    # Quick task 260807-kaq moved this assertion's write_scope="full" order:
    # M8720 has an executable erase step (protocol 0x08, FLAG_CAN_ERASE set),
    # so blank-check now runs AFTER erase instead of before write -- it
    # doubles as erase's own oracle instead of reporting the chip's
    # pre-existing (pre-erase) state as a false BAD.
    assert ops_destructive == [
        "id",
        "read",
        "write",
        "verify",
        "erase",
        "blank-check",
        *_SDP_LEG_STEP_ORDER,
    ]
    sdp_steps = [s for s in plan_destructive.steps if s.op in _SDP_LEG_STEP_ORDER]
    assert len(sdp_steps) == len(_SDP_LEG_STEP_ORDER)
    assert all(not s.supported for s in sdp_steps), (
        "M8720 is REFUSE -- its six SDP-leg steps must all be unsupported/NA"
    )
    ops_destructive_set = set(ops_destructive)
    assert "write" in ops_destructive_set
    assert "erase" in ops_destructive_set


def test_derive_plan_verify_gated_behind_destructive():
    plan_destructive = derive_plan("M8720", _REAL_DB, write_scope="full")
    d_ops = [s.op for s in plan_destructive.steps]
    assert OP_VERIFY in d_ops
    assert d_ops.index(OP_VERIFY) > d_ops.index(OP_WRITE)
    assert d_ops.index(OP_VERIFY) < d_ops.index(OP_ERASE)


def test_derive_plan_destructive_keeps_and_empties_advisory():
    plan = derive_plan("M8720", _REAL_DB, write_scope="full")
    ops = {s.op for s in plan.steps}
    assert "write" in ops
    assert "erase" in ops


def test_derive_plan_partial_same_ops_as_full_different_region():
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
    assert write_full.write_region == verify_full.write_region
    assert write_partial.write_region == verify_partial.write_region


def test_derive_plan_partial_write_region_uv_memory_size():
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


# region_policy (quick task 260821-wna, D-A..D-F): derive_plan decides the
# region POLICY, purely from the DB. No chip access anywhere in this block.


def test_derive_plan_full_device_region_non_uv_eeprom():
    # AT28C256 (EEPROM, 32768 B, protocol 0x0D -- not flash4): full-device
    # policy, whole-device region.
    plan = derive_plan("AT28C256", _REAL_DB, write_scope="full")
    write_step = _step(plan, "write")
    verify_step = _step(plan, "verify")
    assert write_step.region_policy == REGION_POLICY_FULL_DEVICE
    assert write_step.write_region == (0, 32768)
    assert verify_step.region_policy == REGION_POLICY_FULL_DEVICE
    assert verify_step.write_region == (0, 32768)
    assert write_step.full_device_permitted is True


def test_derive_plan_full_device_region_flash4_carves_boot_blocks():
    # W29C040 (Flash/EEPROM, protocol 5, 524288 B): full-device region minus
    # the two 16 KiB boot blocks, and a reason naming the exclusion even
    # though this is a SUCCESSFUL carve-out (D-D: the exclusion is stated
    # and visible, not merely a refusal-path artifact).
    plan = derive_plan("W29C040", _REAL_DB, write_scope="full")
    write_step = _step(plan, "write")
    assert write_step.region_policy == REGION_POLICY_FULL_DEVICE
    assert write_step.write_region == (16384, 491520)
    assert "boot block" in write_step.reason.lower()


def test_derive_plan_full_device_region_flash4_whole_device_boot_block_falls_back():
    # A synthetic protocol-5, 32768 B row: the two boot blocks cover the
    # entire device, so a full write is structurally impossible -- falls
    # back to the fixed small region with a stated reason, never a FAIL.
    full = {
        "electrical-type": "Flash/EEPROM",
        "memory-size": 32768,
        "protocol-id": _PROTOCOL_FLASH4,
    }
    prog = {"algorithm": _PROTOCOL_FLASH4, "flags": 0, "chip-id": 0}
    spy_db = Mock(spec=["get_eprom", "convert_to_programmer"])
    spy_db.get_eprom.return_value = full
    spy_db.convert_to_programmer.return_value = prog

    plan = derive_plan("SYNTHETIC_FLASH4", spy_db, write_scope="full")
    write_step = _step(plan, "write")
    assert write_step.region_policy == REGION_POLICY_FIXED
    assert write_step.write_region == _DEFAULT_REGION
    assert "boot block" in write_step.reason.lower()


def test_derive_plan_uv_full_scope_uses_uv_slot_policy_and_permits_full_device():
    # M27C512 (UV, 65536 B): region_policy uv-slot, write_region is the
    # FIRST slot candidate (top-anchored, unchanged from pre-task
    # behaviour), and full_device_permitted is True at "full" (D-C).
    plan = derive_plan("M27C512", _REAL_DB, write_scope="full")
    write_step = _step(plan, "write")
    verify_step = _step(plan, "verify")
    assert write_step.region_policy == REGION_POLICY_UV_SLOT
    assert write_step.write_region == (65280, 256)
    assert verify_step.region_policy == REGION_POLICY_UV_SLOT
    assert write_step.full_device_permitted is True


def test_derive_plan_uv_partial_scope_forbids_full_device_outcome():
    # Same first slot candidate as "full", but full_device_permitted is
    # False -- the scope literal forbids the D-C full-device-if-blank
    # outcome regardless of chip state.
    plan = derive_plan("M27C512", _REAL_DB, write_scope="partial")
    write_step = _step(plan, "write-partial")
    assert write_step.region_policy == REGION_POLICY_UV_SLOT
    assert write_step.write_region == (65280, 256)
    assert write_step.full_device_permitted is False


def test_derive_plan_sdp_leg_keeps_fixed_region_at_full_scope():
    # The six SDP-leg steps keep the region they get today at the same
    # scope -- (0, 256) at full for AT28C256 -- and carry region_policy
    # fixed. They are never widened to the full device even though the
    # chip's OWN write step now is.
    plan = derive_plan("AT28C256", _REAL_DB, write_scope="full")
    write_step = _step(plan, "write")
    assert write_step.write_region == (0, 32768)  # the chip's own write IS widened
    for sdp_op in _SDP_LEG_STEP_ORDER:
        leg_step = _step(plan, sdp_op)
        assert leg_step.region_policy == REGION_POLICY_FIXED
        assert leg_step.write_region == _DEFAULT_REGION


@pytest.mark.parametrize("hostile_mem_size", [1 << 40, 300, None, 0])
def test_derive_plan_hostile_memory_size_never_widens_the_window(hostile_mem_size):
    # A hostile DB dict never widens the window: the write step falls back
    # to region_policy fixed with the pre-existing small region and a
    # stated reason (T-wna-01).
    full = {
        "electrical-type": "EEPROM",
        "memory-size": hostile_mem_size,
        "protocol-id": 13,
    }
    prog = {"algorithm": 13, "flags": 0, "chip-id": 0}
    spy_db = Mock(spec=["get_eprom", "convert_to_programmer"])
    spy_db.get_eprom.return_value = full
    spy_db.convert_to_programmer.return_value = prog

    plan = derive_plan("SYNTHETIC_HOSTILE", spy_db, write_scope="full")
    write_step = _step(plan, "write")
    assert write_step.region_policy == REGION_POLICY_FIXED
    assert write_step.write_region == _DEFAULT_REGION
    assert write_step.reason


def test_derive_plan_full_device_region_at_sanity_ceiling_is_honoured():
    full = {
        "electrical-type": "EEPROM",
        "memory-size": _MAX_FULL_DEVICE_LENGTH,
        "protocol-id": 13,
    }
    prog = {"algorithm": 13, "flags": 0, "chip-id": 0}
    spy_db = Mock(spec=["get_eprom", "convert_to_programmer"])
    spy_db.get_eprom.return_value = full
    spy_db.convert_to_programmer.return_value = prog

    plan = derive_plan("SYNTHETIC_CEILING", spy_db, write_scope="full")
    write_step = _step(plan, "write")
    assert write_step.region_policy == REGION_POLICY_FULL_DEVICE
    assert write_step.write_region == (0, _MAX_FULL_DEVICE_LENGTH)


# The no-chip-access invariant with region_policy in play is already pinned
# by `test_derive_plan_reads_via_get_eprom_and_convert_to_programmer_only`
# above (unmodified by this task) -- it exercises the same derive_plan code
# path this task changed and stays green, so no separate leg is added here.


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
    scope = "partial" if expected_is_uv else "full"
    plan = derive_plan(name, _REAL_DB, write_scope=scope)
    assert plan.is_uv is expected_is_uv


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
    "sdp_lock",
    "sdp_unlock",
]


def _mock_operator(**returns):
    op = Mock(spec=_OPERATOR_METHODS)
    op.check_eprom_id.return_value = (True, 0x1234)
    op.read_eprom.return_value = True
    # 202-05 D-10: check_eprom_blank now returns an int (0 == blank), the
    # same convention verify_eprom adopted in 202-01.
    op.check_eprom_blank.return_value = 0
    op.write_eprom.return_value = True
    # 202-01 D-10: verify_eprom now returns an int (0 == match); the
    # multi-run dispatch's `== 0` adapter reads this as success only at 0.
    op.verify_eprom.return_value = 0
    op.erase_eprom.return_value = True
    op.sdp_lock.return_value = True
    op.sdp_unlock.return_value = True
    for name, value in returns.items():
        getattr(op, name).return_value = value
        getattr(op, name).side_effect = None
    return op


def _sdp_leg_readback_operator():
    """A stateful, SDP-lock-AWARE operator double for the AT28C256 0x0D
    sweeps below (v1.30 Phase 134, plan 134-03).

    `_mock_operator`'s `read_eprom` returns `True` while writing NO file --
    the SDP leg's read-back-equality oracle would then see an empty
    read-back on every leg step and report all six BAD via the length gate
    (`_dispatch_sdp_leg`'s D-04 length gate), silently reducing an
    "all-OK" sweep assertion to a false negative that happens to still
    read green for the wrong reason.

    This double instead maintains a small in-memory chip image and honours
    real SDP semantics: while `locked`, a write carrying
    `FLAG_SKIP_SDP_UNLOCK` (write-inhibited's own flag, deliberately set on
    that op alone) is genuinely REJECTED by the simulated chip -- the image
    is left unchanged, exactly what a genuinely-protecting chip does --
    while every other write (no SKIP flag, or the chip unlocked) applies
    normally. This is what makes a real end-to-end run across the full
    twelve-step AT28C256 plan (id/read/blank-check/write/verify/erase-NA
    plus the six SDP-leg ops) genuinely all-OK, rather than a fixed-payload
    double that could only ever satisfy one of the leg's several distinct
    expected read-backs.
    """
    from firestarter.constants import FLAG_SKIP_SDP_UNLOCK

    state = {"image": b"", "locked": False}

    op = Mock(spec=_OPERATOR_METHODS)
    op.check_eprom_id.return_value = (True, 0x1234)
    # 202-05 D-10: check_eprom_blank now returns an int (0 == blank).
    op.check_eprom_blank.return_value = 0
    op.erase_eprom.return_value = True

    def _write_eprom(name, eprom_data, source_path, flags=0, address_str=None, **_kw):
        payload = Path(source_path).read_bytes()
        if state["locked"] and (flags & FLAG_SKIP_SDP_UNLOCK):
            # Genuinely blocked: the chip ignores the write while locked
            # and the firmware's own auto-unlock-wrap was deliberately
            # skipped -- write-inhibited's whole point.
            pass
        else:
            state["image"] = payload
        return True

    def _read_eprom(name, eprom_data, output_file=None, **kwargs):
        if output_file is not None:
            Path(output_file).write_bytes(state["image"])
        return True

    def _verify_eprom(name, eprom_data, source_path, *_args, **_kwargs):
        # 202-01 D-10: int, 0 == match -- see the return_value comment above.
        expected = Path(source_path).read_bytes()
        return 0 if expected == state["image"] else 1

    def _sdp_lock(name, eprom_data):
        state["locked"] = True
        return True

    def _sdp_unlock(name, eprom_data):
        state["locked"] = False
        return True

    op.write_eprom.side_effect = _write_eprom
    op.read_eprom.side_effect = _read_eprom
    op.verify_eprom.side_effect = _verify_eprom
    op.sdp_lock.side_effect = _sdp_lock
    op.sdp_unlock.side_effect = _sdp_unlock
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


def test_blank_check_step_records_pass_verdict_when_operator_returns_zero():
    """202-05 D-10: `check_eprom_blank` now returns an int (0 == blank, the
    same convention `verify_eprom` adopted in 202-01). A mocked operator
    returning 0 must record the passing OK verdict through the dispatch's
    `== 0` adapter."""
    operator = _mock_operator(check_eprom_blank=0)
    plan = _plan_with_steps(Step(op=OP_BLANK_CHECK, supported=True, reason=""))

    results = run_plan(plan, operator, _REAL_DB)

    assert _result(results, OP_BLANK_CHECK).verdict == VERDICT_OK


def test_blank_check_step_records_bad_verdict_when_operator_returns_one():
    """The paired negative: an operator returning 1 (not blank) must not be
    read as success through the same `== 0` adapter."""
    operator = _mock_operator(check_eprom_blank=1)
    plan = _plan_with_steps(Step(op=OP_BLANK_CHECK, supported=True, reason=""))

    results = run_plan(plan, operator, _REAL_DB)

    assert _result(results, OP_BLANK_CHECK).verdict == VERDICT_BAD


"""The two-axis status vocabulary (178-CONTEXT.md D-01/D-02/D-12, 178-02):
the measured non-changes -- the destructive-write gate, the `_skip_result`
bypass, and the run-level fold -- plus the transport-fault arm's own
verdict/status shape."""


def test_transport_fault_carries_skipped_verdict_and_error_status():
    """A `SerialError` raised by the "read" step's operator method produces
    a `StepResult` carrying `verdict == VERDICT_SKIPPED`,
    `status == STATUS_ERROR`, a non-empty `reason`, and `error_code is
    None` -- the D-01 shape: the chip-verdict axis never spends a `BAD` on
    a fault that was never the chip's, while the run-validity axis still
    records that this run did not execute validly."""
    operator = _mock_operator()
    operator.read_eprom.side_effect = SerialError("half-seated cable")
    plan = _plan_with_steps(Step(op=OP_READ, supported=True, reason=""))

    results = run_plan(plan, operator, _REAL_DB)

    result = _result(results, OP_READ)
    assert result.verdict == VERDICT_SKIPPED
    assert result.status == STATUS_ERROR
    assert result.reason
    assert result.error_code is None


def test_id_step_closes_gate_predicate_is_unchanged_by_the_status_axis():
    """D-01: `_id_step_closes_gate` returns True for a `StepResult` carrying
    `verdict=VERDICT_SKIPPED, status=STATUS_ERROR`, and its source text
    still reads the two-element `(VERDICT_BAD, VERDICT_SKIPPED)` tuple and
    mentions neither `status` nor `STATUS_`. `marginal` and `NA` both leave
    this gate OPEN, which would admit a destructive write against a chip
    whose identity was never confirmed -- `SKIPPED` was chosen precisely so
    this predicate needs no edit at all."""
    result = StepResult(
        op=OP_ID,
        verdict=VERDICT_SKIPPED,
        status=STATUS_ERROR,
        reason="half-seated cable",
    )
    assert _id_step_closes_gate(result) is True

    source = inspect.getsource(_id_step_closes_gate)
    assert "VERDICT_BAD, VERDICT_SKIPPED" in source
    assert "status" not in source
    assert "STATUS_" not in source


def test_run_status_folds_error_when_any_step_errored():
    ok = StepResult(op=OP_ID, verdict=VERDICT_OK)
    errored = StepResult(op=OP_READ, verdict=VERDICT_SKIPPED, status=STATUS_ERROR)
    assert run_status([ok, errored]) == STATUS_ERROR


def test_run_status_is_complete_when_no_step_errored():
    """The fold's other leg, including the empty-list case, plus D-02's
    disjointness proof: none of the three `STATUS_*` values is a member of
    `_ALL_OPS` or `_MULTIWORD_OP_VALUES` -- mirroring the `SDP_HOLD_*`
    precedent -- so a later reader cannot "helpfully" register a report
    value as an op string."""
    import firestarter.chip_test as chip_test_mod

    _ALL_OPS = frozenset(
        value
        for name, value in vars(chip_test_mod).items()
        if name.startswith("OP_") and isinstance(value, str)
    )
    _MULTIWORD_OP_VALUES = frozenset(v for v in _ALL_OPS if "-" in v)

    ok = StepResult(op=OP_ID, verdict=VERDICT_OK)
    assert run_status([ok]) == STATUS_COMPLETE
    assert run_status([]) == STATUS_COMPLETE

    status_values = {STATUS_COMPLETE, STATUS_ERROR, STATUS_SKIP}
    assert not (status_values & set(_ALL_OPS))
    assert not (status_values & set(_MULTIWORD_OP_VALUES))


def _writes_bytes_to_output_file(data: bytes):
    """Build a `read_eprom` side_effect that writes `data` at the requested
    ABSOLUTE offset (quick task 260821-wna, finding M-3), defaulting to
    offset 0 when no `address_str` is supplied -- reproducing
    `_write_to_file`'s `file_handle.seek(address)` via the same
    `_parse_addr_or_size` helper `fake_chip.FakeChip` uses.
    """
    from .fake_chip import _parse_addr_or_size

    def _side_effect(_name, _eprom_data, output_file=None, address_str=None, **_kwargs):
        if output_file:
            start = _parse_addr_or_size(address_str) or 0
            with open(output_file, "wb") as fh:
                fh.seek(start)
                fh.write(data)
        return True

    return _side_effect


def _writes_fill_at_requested_region(fill: int = 0xFF):
    """Build a `read_eprom` side_effect that answers ANY region request
    with `fill` repeated for the requested size, written at the requested
    ABSOLUTE offset -- models a virgin/blank chip for the execution-time
    UV-slot probe walk (quick task 260821-wna, Task 4). Falls back to a
    zero-length write when `size_str` is absent (the plain OP_READ step's
    own whole-device call, which this helper is never used for in this
    module -- kept total rather than partial for defensiveness).
    """
    from .fake_chip import _parse_addr_or_size

    def _side_effect(
        _name, _eprom_data, output_file=None, address_str=None, size_str=None, **_kw
    ):
        if not output_file:
            return True
        start = _parse_addr_or_size(address_str) or 0
        length = _parse_addr_or_size(size_str) or 0
        with open(output_file, "wb") as fh:
            fh.seek(start)
            fh.write(bytes([fill]) * length)
        return True

    return _side_effect


def _alternating_read_side_effect(*call_returns: bool):
    """Build a `read_eprom` side_effect returning `call_returns[n % len(call_returns)]`
    on the n-th call, so the caller's argument order IS the run order. Every call that
    writes to `output_file` writes the SAME 64 zero bytes regardless of which element of
    `call_returns` it returns -- the two runs never diverge, `_dispatch_read` records no
    divergence, and the leg isolates the verdict source from the divergence metric
    (`test_read_step_disagreement_is_divergence_metric_not_marginal` owns that job, with
    its own `_read_side_effect` that varies the payload per call and must stay separate
    from this builder). `_writes_bytes_to_output_file` could not be reused here because
    it always returns `True` and cannot express an alternating pass/fail sequence.
    """
    call_count = {"n": 0}

    def _side_effect(_name, _eprom_data, output_file=None, **_kwargs):
        ok = call_returns[call_count["n"] % len(call_returns)]
        call_count["n"] += 1
        if output_file:
            Path(output_file).write_bytes(b"\x00" * 64)
        return ok

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


def _record_operator_calls(operator, calls, *methods):
    """Make each named operator method append its own name to `calls`.

    Preserves the mock's configured `return_value` -- `_mock_operator` sets
    those, and a bare `side_effect` would otherwise shadow them and hand every
    step a `Mock` instead of a bool.
    """

    def _make(method_name, value):
        def _side(*_args, **_kwargs):
            calls.append(method_name)
            return value

        return _side

    for method_name in methods:
        mock_method = getattr(operator, method_name)
        mock_method.side_effect = _make(method_name, mock_method.return_value)


def test_write_and_verify_run_as_a_cycle_not_two_inner_loops():
    """D-1: the ORDER is `write, verify, write, verify` -- not `write, write,
    verify, verify`.

    This is the property the whole cycle loop exists for. A second write onto
    the state the first one produced is a no-op on the 27C path (see
    `_MULTI_RUN_OPS`' note in the engine), so pairing each write with its own
    verify is what makes the repeat mean anything.
    """
    calls: list[str] = []
    operator = _mock_operator()
    _record_operator_calls(operator, calls, "write_eprom", "verify_eprom")
    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True),
        Step(op=OP_VERIFY, supported=True, reason=""),
    )
    run_plan(plan, operator, _REAL_DB, runs=2)

    assert calls == ["write_eprom", "verify_eprom", "write_eprom", "verify_eprom"]


def test_erasable_cycle_puts_the_erase_before_the_next_write():
    """D-3, and the reason the erasable families are fixed by the cycle loop
    ALONE, with no payload change: each cycle's erase blanks the part for the
    NEXT cycle's write, so from cycle 2 on the write has full real work to do
    even though the bytes are identical. The blank-check rides inside the
    cycle and validates that erase every time round.
    """
    calls: list[str] = []
    operator = _mock_operator()
    _record_operator_calls(
        operator,
        calls,
        "write_eprom",
        "verify_eprom",
        "erase_eprom",
        "check_eprom_blank",
    )
    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True),
        Step(op=OP_VERIFY, supported=True, reason=""),
        Step(op=OP_ERASE, supported=True, reason="", destructive=True),
        Step(op=OP_BLANK_CHECK, supported=True, reason=""),
    )
    run_plan(plan, operator, _REAL_DB, runs=2)

    one_cycle = ["write_eprom", "verify_eprom", "erase_eprom", "check_eprom_blank"]
    assert calls == one_cycle * 2
    # The load-bearing consequence, asserted directly rather than inferred
    # from the list above: an erase precedes the second write.
    assert calls.index("erase_eprom") < calls.index("write_eprom", 1)


def test_cycle_block_bounds_matches_each_family_plan_shape():
    """The block is CONSECUTIVE ops starting at the write -- which is what
    keeps a UV plan's pre-write blank-check (a once-only, operator-actionable
    finding) outside the cycle while an erasable plan's post-erase blank-check
    lands inside it, with no per-family special case in the detector."""
    import firestarter.chip_test as chip_test_mod

    for name, expected in (
        ("M8720", [OP_WRITE, OP_VERIFY, OP_ERASE, OP_BLANK_CHECK]),
        ("W27C512", [OP_WRITE, OP_VERIFY, OP_ERASE, OP_BLANK_CHECK]),
        ("M27C512", [OP_WRITE, OP_VERIFY, OP_ERASE]),
        ("W29C040", [OP_WRITE, OP_VERIFY, OP_ERASE]),
    ):
        plan = derive_plan(name, _REAL_DB, write_scope="full")
        bounds = chip_test_mod.cycle_block_bounds(plan.steps)
        assert bounds is not None, name
        assert [s.op for s in plan.steps[bounds[0] : bounds[1]]] == expected, name
        # The SDP leg is NEVER swallowed by the block.
        after = [s.op for s in plan.steps[bounds[1] :]]
        sdp_ops = chip_test_mod._SDP_LEG_OPS | chip_test_mod._SDP_OPS
        assert all(op in sdp_ops for op in after), (
            f"{name}: block ran past the write cycle into {after}"
        )


def test_cycle_loop_reports_one_result_per_step_with_run_count_n():
    """The aggregation contract that keeps the blast radius small: cycling
    changes the EXECUTION order only. The report still sees one row per plan
    step, `run_count` still counts operator calls, so the schema-1.7
    disclosure, the banner counts and `dedup_fingerprint` are all untouched."""
    operator = _mock_operator()
    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True),
        Step(op=OP_VERIFY, supported=True, reason=""),
    )
    results = run_plan(plan, operator, _REAL_DB, runs=2)

    assert [r.op for r in results] == [OP_WRITE, OP_VERIFY]
    assert _result(results, OP_WRITE).run_count == 2
    assert _result(results, OP_VERIFY).run_count == 2
    assert operator.write_eprom.call_count == 2
    assert operator.verify_eprom.call_count == 2


def test_a_passing_run_performs_zero_fingerprint_read_backs():
    """`collect_fingerprint` is True only on the final cycle, and a step
    whose runs all agreed -- with no earlier cycle in the block having
    failed -- needs no read-back at all to know it is clean: the
    fingerprint is synthesized instead. A fingerprint only ever describes
    the device's FINAL state, and on an all-passing run that final state
    needs zero additional device reads to report as `match`."""
    operator = _mock_operator()
    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True),
        Step(op=OP_VERIFY, supported=True, reason=""),
    )
    run_plan(plan, operator, _REAL_DB, runs=3)

    assert operator.read_eprom.call_count == 0


def test_a_passing_write_reports_a_synthesized_match_fingerprint():
    """The zero-read-back path still attaches a `Fingerprint` -- never
    `None` -- and it classifies `match` with `bad == 0`."""
    operator = _mock_operator()
    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True),
        Step(op=OP_VERIFY, supported=True, reason=""),
    )
    results = run_plan(plan, operator, _REAL_DB, runs=3)

    write_result = _result(results, OP_WRITE)
    verify_result = _result(results, OP_VERIFY)
    assert write_result.fingerprint is not None
    assert write_result.fingerprint.classification == "match"
    assert write_result.fingerprint.bad == 0
    assert verify_result.fingerprint is not None
    assert verify_result.fingerprint.classification == "match"
    assert verify_result.fingerprint.bad == 0


def test_synthesized_and_measured_fingerprints_share_one_evidence_key_set():
    """The synthesized cheap-path fingerprint and a real measured one must
    carry the IDENTICAL `evidence` key set, so a later consumer cannot tell
    the two apart by a missing key rather than by a stated value."""
    synthesized = _synthesized_match_fingerprint(4096)
    measured = classify_fingerprint(b"\xa5" * 4096, b"\xa5" * 4096)

    assert sorted(synthesized.evidence) == sorted(measured.evidence)


def test_a_bad_blank_check_does_not_force_a_read_back_on_a_passing_write():
    """The gate is PER STEP, not per run: a `blank-check` step reporting BAD
    must not force a fingerprint read-back on an unrelated passing `write`
    step in the same plan."""
    # 202-05 D-10: 1 (not 0) is now check_eprom_blank's "not blank" verdict.
    operator = _mock_operator(check_eprom_blank=1)
    plan = _plan_with_steps(
        Step(op=OP_BLANK_CHECK, supported=True, reason=""),
        Step(op=OP_WRITE, supported=True, reason="", destructive=True),
        Step(op=OP_VERIFY, supported=True, reason=""),
    )
    run_plan(plan, operator, _REAL_DB, runs=2)

    assert operator.read_eprom.call_count == 0


def test_a_failing_step_still_performs_its_fingerprint_read_back():
    """A single-cycle failure (both runs disagreeing, so the write reports
    `marginal`) keeps the real fingerprint read-back -- the diagnostic this
    gate exists to preserve for anything that did not cleanly pass."""
    operator = _mock_operator()
    operator.write_eprom.side_effect = [True, False]
    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True)
    )
    run_plan(plan, operator, _REAL_DB, runs=2)

    assert operator.read_eprom.call_count > 0


def test_classify_fingerprint_still_returns_blank_contact_for_an_all_ff_perfect_compare():
    """A bit-perfect all-0xFF compare has `bad == 0` too, but it must stay
    `blank/contact`, never `match`: the `ff_ratio >= 0.98` test keeps its
    position as the FIRST bucket in `classify_fingerprint`."""
    fp = classify_fingerprint(b"\xff" * 4096, b"\xff" * 4096)

    assert fp.classification == "blank/contact"


def test_a_zero_length_write_region_synthesizes_a_match_with_no_read():
    """`_synthesized_match_fingerprint(0)` must not raise and must still
    report `match` -- a zero-length write region is a degenerate case the
    cheap path has to handle exactly like any other."""
    fp = _synthesized_match_fingerprint(0)

    assert fp.total == 0
    assert fp.bad == 0
    assert fp.bad_pct == 0.0
    assert fp.classification == "match"


def test_cycle_disagreement_still_reports_marginal():
    """`marginal` moved from `_dispatch_multi_run` (which now sees one cycle at
    a time) to the aggregation, with its MEANING unchanged: cycles that
    disagree never fold to a confident OK/BAD."""
    operator = _mock_operator()
    operator.write_eprom.side_effect = [True, False]
    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True)
    )
    results = run_plan(plan, operator, _REAL_DB, runs=2)

    write_result = _result(results, OP_WRITE)
    assert write_result.verdict == VERDICT_MARGINAL
    assert write_result.run_count == 2
    assert "disagreed" in write_result.reason


def test_allow_single_run_admits_runs_1_and_reports_run_count_1():
    """The ONLY way past the fail-closed guard (quick task 260822-aq6).

    The sibling test above proves `runs=1` alone still fails the whole plan
    -- an accidentally mis-wired caller cannot silently forfeit the marginal
    detector. This one proves the deliberate opt-in works and that the
    forfeit is RECORDED: `run_count == 1` is what every disclosure surface
    and `repeat_policy_tag` read to say so.

    ONE `read_eprom` call, not two (Phase 177, PRUNE-01/PRUNE-02): the
    single policy-governed read from `_dispatch_read` is the only call --
    the write step's OWN cycle passed cleanly (a single run with nothing to
    disagree with, and no prior cycle to have failed), so its fingerprint
    is synthesized with no additional device I/O. The read-back has never
    been part of the repeat policy and `--fast` does not remove it for a
    step that FAILS; a passing `--fast` write now costs exactly what a
    passing full-repeat write costs -- zero extra reads.
    """
    operator = _mock_operator()
    plan = _plan_with_steps(
        Step(op=OP_READ, supported=True, reason=""),
        Step(op=OP_WRITE, supported=True, reason="", destructive=True),
    )
    results = run_plan(plan, operator, _REAL_DB, runs=1, allow_single_run=True)

    assert _result(results, OP_READ).run_count == 1
    assert _result(results, OP_WRITE).run_count == 1
    assert operator.write_eprom.call_count == 1
    assert _result(results, OP_WRITE).fingerprint is not None
    assert _result(results, OP_WRITE).fingerprint.classification == "match"
    assert operator.read_eprom.call_count == 1


def test_allow_single_run_still_rejects_runs_below_1():
    """`allow_single_run=True` unlocks ONE run, not zero. A zero-run step
    would report a verdict for an operator call that never happened -- the
    vacuous pass this codebase refuses everywhere else."""
    operator = _mock_operator()
    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True)
    )
    results = run_plan(plan, operator, _REAL_DB, runs=0, allow_single_run=True)

    operator.write_eprom.assert_not_called()
    assert len(results) == 1
    assert results[0].verdict == VERDICT_BAD


def test_single_run_write_cannot_report_marginal():
    """The cost of `--fast`, proven rather than asserted in prose.

    Identical operator to `test_marginal_on_disagreeing_write_runs` below
    (write#1 True, write#2 False -- the AM27C020 case). At `runs=2` that is
    `marginal`. At `runs=1` the second outcome is never sampled, so the step
    reports a confident OK and the divergence is INVISIBLE. This is exactly
    what the `--fast` help text warns about.
    """
    operator = _mock_operator()
    operator.write_eprom.side_effect = [True, False]
    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True)
    )
    results = run_plan(plan, operator, _REAL_DB, runs=1, allow_single_run=True)

    write_result = _result(results, OP_WRITE)
    assert write_result.verdict == VERDICT_OK
    assert write_result.verdict != VERDICT_MARGINAL
    assert write_result.run_count == 1


def test_repeat_policy_tag_empty_for_the_default_policy():
    from firestarter.chip_test import repeat_policy_tag

    operator = _mock_operator()
    plan = _plan_with_steps(
        Step(op=OP_READ, supported=True, reason=""),
        Step(op=OP_WRITE, supported=True, reason="", destructive=True),
    )
    results = run_plan(plan, operator, _REAL_DB, runs=2)

    assert repeat_policy_tag(results) == ""


def test_repeat_policy_tag_marks_a_single_run_plan():
    from firestarter.chip_test import (
        REPEAT_POLICY_DEGRADED_TAG,
        repeat_policy_tag,
    )

    operator = _mock_operator()
    plan = _plan_with_steps(
        Step(op=OP_WRITE, supported=True, reason="", destructive=True)
    )
    results = run_plan(plan, operator, _REAL_DB, runs=1, allow_single_run=True)

    assert repeat_policy_tag(results) == REPEAT_POLICY_DEGRADED_TAG


def test_repeat_policy_tag_ignores_ops_that_are_single_run_by_design():
    """`run_count == 1` is NORMAL for the id check, the blank check and all
    six SDP-leg ops -- those dispatch arms hard-set it. Reading them as a
    degraded repeat policy would tag every AT28C256 sweep as `--fast` and
    split its dedup group for no reason."""
    import firestarter.chip_test as chip_test_mod
    from firestarter.chip_test import (
        OP_BLANK_CHECK,
        OP_ID,
        OP_SDP_LOCK,
        OP_WRITE_INHIBITED,
        repeat_policy_tag,
    )

    step_result = chip_test_mod.StepResult
    by_design = [
        step_result(op=op, verdict=VERDICT_OK, run_count=1)
        for op in (OP_ID, OP_BLANK_CHECK, OP_SDP_LOCK, OP_WRITE_INHIBITED)
    ]
    # A real N>=2 write/read alongside them, so the list is a plausible sweep.
    by_design += [
        step_result(op=OP_READ, verdict=VERDICT_OK, run_count=2),
        step_result(op=OP_WRITE, verdict=VERDICT_OK, run_count=2),
    ]

    assert repeat_policy_tag(by_design) == ""


def test_repeat_policy_tag_ignores_steps_that_never_ran():
    """A SKIPPED/NA step carries `run_count == 0` and says nothing about the
    policy -- it must not be read as either value."""
    import firestarter.chip_test as chip_test_mod
    from firestarter.chip_test import repeat_policy_tag

    step_result = chip_test_mod.StepResult
    assert (
        repeat_policy_tag(
            [
                step_result(op=OP_WRITE, verdict=VERDICT_SKIPPED, run_count=0),
                step_result(op=OP_VERIFY, verdict=VERDICT_SKIPPED, run_count=0),
            ]
        )
        == ""
    )


# coverage_tag (quick-devtest-coverage-dedup, follow-up to 260821-wna)


def _write_target_result(region_policy, *, region=(0xFF00, 256)):
    """A directly-constructed write-shaped `StepResult` carrying a real
    `WriteTarget` under `region_policy` -- `coverage_tag`'s only input
    besides the op vocabulary it deliberately never reads."""
    import firestarter.chip_test as chip_test_mod

    target = WriteTarget(
        region=region,
        pattern=generate_pattern(*region),
        masked=False,
        bits_cleared=0,
        bits_retained=0,
        current_source="test fixture",
        region_policy=region_policy,
    )
    return chip_test_mod.StepResult(
        op=OP_WRITE, verdict=VERDICT_OK, write_target=target
    )


def test_coverage_tag_marks_a_full_device_write():
    from firestarter.chip_test import COVERAGE_TAG_FULL_DEVICE, coverage_tag

    results = [_write_target_result(REGION_POLICY_FULL_DEVICE)]

    assert coverage_tag(results) == COVERAGE_TAG_FULL_DEVICE


def test_coverage_tag_empty_for_a_slot_or_fixed_write():
    """Load-bearing for `dedup_fingerprint`'s no-re-key property: BOTH
    non-full-device policies -- `uv-slot` and `fixed` -- must return `""`,
    not merely "something other than the full-device tag"."""
    from firestarter.chip_test import coverage_tag

    assert coverage_tag([_write_target_result(REGION_POLICY_UV_SLOT)]) == ""
    assert coverage_tag([_write_target_result(REGION_POLICY_FIXED)]) == ""


def test_coverage_tag_empty_for_a_run_with_no_write_step():
    """Graceful degradation, mirroring `repeat_policy_tag`'s own contract:
    a non-destructive run (id/read/blank-check only, no write step at all)
    reports nothing about coverage rather than raising or guessing."""
    import firestarter.chip_test as chip_test_mod
    from firestarter.chip_test import coverage_tag

    step_result = chip_test_mod.StepResult
    results = [
        step_result(op=OP_ID, verdict=VERDICT_OK),
        step_result(op=OP_READ, verdict=VERDICT_OK),
    ]

    assert coverage_tag(results) == ""
    assert coverage_tag([]) == ""


def test_resolve_error_name_names_the_issue_86_ids():
    from firestarter.chip_test import resolve_error_name

    assert resolve_error_name(183) == "MSG_ERR_OP_TIMEOUT"
    assert resolve_error_name(175) == "MSG_ERR_VERIFY"
    assert resolve_error_name(185) == "MSG_ERR_CHIP_ID_MISMATCH"


def test_resolve_error_name_is_exhaustive_over_the_catalog():
    from firestarter.chip_test import resolve_error_name
    from firestarter.messages import CATALOG

    for msg_id, entry in CATALOG.items():
        assert resolve_error_name(msg_id) == entry.name


def test_resolve_error_name_null_for_a_null_code():
    from firestarter.chip_test import resolve_error_name

    assert resolve_error_name(None) is None


def test_resolve_error_name_null_for_an_id_in_neither_registry():
    from firestarter.chip_test import resolve_error_name

    assert resolve_error_name(54) is None


def test_resolve_error_name_never_falls_back_to_debug_catalog():
    """Id 6 is `DBG_CMD_FINISHED` in `DEBUG_CATALOG` only -- `error_code` is
    always a top-level `response.id`, a namespace `CATALOG` alone describes,
    so a fallback there would put a wrong name on a real failure."""
    from firestarter.chip_test import resolve_error_name

    name = resolve_error_name(6)

    assert name is None
    assert name != "DBG_CMD_FINISHED"


def test_resolve_error_name_never_raises_over_the_unnamed_range():
    from firestarter.chip_test import resolve_error_name
    from firestarter.messages import CATALOG

    for msg_id in range(256):
        if msg_id in CATALOG:
            continue
        assert resolve_error_name(msg_id) is None


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
    # 202-01 D-10: int, 0 == match, 1 == mismatch.
    operator.verify_eprom.side_effect = [0, 1]
    plan = _plan_with_steps(Step(op=OP_VERIFY, supported=True, reason=""))
    results = run_plan(plan, operator, _REAL_DB, runs=2)

    verify_result = _result(results, OP_VERIFY)
    assert verify_result.verdict == VERDICT_MARGINAL


# Fail-closed dispatch on an unmapped op (T-121-05/06/07, 121-02 Task 1)

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


def test_dispatch_id_pass_records_the_echoed_expected_id():
    """RPT-A5/RPT-A1: on a PASS, `check_eprom_id`'s OK reply carries no id
    back from the firmware, so the value it returns is the host's OWN
    expected id echoed out of the command dict -- `chip_id_detected` on a
    pass therefore EQUALS the expected id by construction, and is the echo
    the check was verified against, never an independent read-back. This is
    the measured basis for the console `chip_id` row staying one-sided on
    agreement (D-10)."""
    operator = Mock()
    operator.check_eprom_id.return_value = (True, 0x1F65)
    result = _dispatch_id("m27c512", {"chip-id": 0x1F65}, operator)

    assert result.chip_id_detected == 0x1F65
    assert result.reason == ""
    assert result.verdict == VERDICT_OK


def test_dispatch_id_mismatch_records_the_firmware_reported_id():
    """A mismatching id check yields `chip_id_detected` equal to the id the
    firmware actually reported, and a `reason` byte-identical to the string
    base produces (D-23 -- the human sentence never changes)."""
    operator = Mock()
    operator.check_eprom_id.return_value = (True, 0x1234)
    result = _dispatch_id("m27c512", {"chip-id": 0x1F65}, operator)

    assert result.chip_id_detected == 0x1234
    assert result.reason == "chip-ID mismatch: expected 0x1F65, detected 0x1234"
    assert result.verdict == VERDICT_BAD


def test_dispatch_id_not_ok_with_no_id_leaves_chip_id_detected_none():
    """A `check_eprom_id` returning `(False, None)` yields
    `chip_id_detected is None` and the not-OK reason, byte-identical to
    base (D-23)."""
    operator = Mock()
    operator.check_eprom_id.return_value = (False, None)
    result = _dispatch_id("m27c512", {"chip-id": 0x1F65}, operator)

    assert result.chip_id_detected is None
    assert result.reason == "chip-ID check did not return OK"


def test_read_step_disagreement_is_divergence_metric_not_marginal():
    operator = _mock_operator()
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
    assert read_result.verdict != VERDICT_MARGINAL
    assert read_result.divergence is not None
    assert read_result.divergence["bad"] > 0


def test_read_step_agreement_records_a_zero_bad_divergence_mapping():
    """D-11 (mirroring PRUNE-03): the previous claim here -- `divergence` is
    ABSENT on an agreeing read (`assert not read_result.divergence`) -- was
    DELIBERATELY falsified by this change. `None` used to conflate two
    different facts: "compared and matched" and "never compared". Phase
    180's D-06 cited this test as covering the agreeing case; the agreeing
    case is STILL covered here, by the stronger assertion that a real
    zero-bad mapping is recorded rather than nothing."""
    operator = _mock_operator()
    operator.read_eprom.side_effect = _writes_bytes_to_output_file(b"\xaa" * 32)
    plan = _plan_with_steps(Step(op=OP_READ, supported=True, reason=""))
    results = run_plan(plan, operator, _REAL_DB, runs=2)

    read_result = _result(results, OP_READ)
    assert read_result.verdict == VERDICT_OK
    assert read_result.divergence == {
        "repeat_divergent": False,
        "cmp_len": 32,
        "bad": 0,
        "pct": 0.0,
        "first_offset": None,
    }
    assert read_result.reason == ""


def test_agreeing_and_diverging_divergence_mappings_share_the_same_key_set():
    """Both branches of `_dispatch_read`'s divergence construction emit the
    same five keys, or `parse_devtest_issue.py` and the triage skill see a
    ragged shape."""
    operator = _mock_operator()
    operator.read_eprom.side_effect = _writes_bytes_to_output_file(b"\xaa" * 32)
    plan = _plan_with_steps(Step(op=OP_READ, supported=True, reason=""))
    agreeing = _result(run_plan(plan, operator, _REAL_DB, runs=2), OP_READ)

    diverging_operator = _mock_operator()
    call_results = [b"\x00" * 64, b"\xff" * 64]
    call_count = {"n": 0}

    def _read_side_effect(_name, _eprom_data, output_file=None, **_kwargs):
        data = call_results[call_count["n"] % len(call_results)]
        call_count["n"] += 1
        if output_file:
            Path(output_file).write_bytes(data)
        return True

    diverging_operator.read_eprom.side_effect = _read_side_effect
    diverging = _result(run_plan(plan, diverging_operator, _REAL_DB, runs=2), OP_READ)

    assert sorted(agreeing.divergence) == sorted(diverging.divergence)


def test_a_single_run_and_an_all_empty_read_leave_divergence_none():
    """The outer `len(run_bytes) >= 2 and any(run_bytes)` gate is what
    preserves `None`-means-no-comparison-was-possible for `--fast` (one
    run) and for a failed read (all-empty bytes) -- D-11 changes only the
    inner branch, never this gate. Calls `_dispatch_read` directly (rather
    than through `run_plan`, which refuses `runs < 2` at the plan level) --
    the same seam `test_read_step_disagreement_is_divergence_metric_not_marginal`'s
    siblings above call through `run_plan` for, since a single-run read is
    the one shape `run_plan` itself cannot produce."""
    operator = _mock_operator()
    operator.read_eprom.side_effect = _writes_bytes_to_output_file(b"\xaa" * 32)
    single_run = _dispatch_read("m27c512", {}, operator, runs=1)
    assert single_run.divergence is None

    empty_operator = _mock_operator()

    def _empty_side_effect(_name, _eprom_data, output_file=None, **_kwargs):
        if output_file:
            Path(output_file).write_bytes(b"")
        return False

    empty_operator.read_eprom.side_effect = _empty_side_effect
    empty_read = _dispatch_read("m27c512", {}, empty_operator, runs=2)
    assert empty_read.divergence is None


def test_the_agreeing_branch_never_calls_the_per_byte_diff_primitive(monkeypatch):
    """The cheapest honest proof of the non-call (D-11's own justification):
    the sha equality already proves zero mismatches, so the agreeing branch
    must derive its five values without walking the whole compared region
    through `compare.diff_summary` (the primitive `_diff_offsets` retired
    into, 202-03 D-02)."""
    from firestarter import chip_test as ct

    def _boom(*_args, **_kwargs):
        raise AssertionError(
            "the per-byte diff primitive was called on an agreeing read"
        )

    monkeypatch.setattr(ct, "diff_summary", _boom)

    operator = _mock_operator()
    operator.read_eprom.side_effect = _writes_bytes_to_output_file(b"\x5a" * 128)
    plan = _plan_with_steps(Step(op=OP_READ, supported=True, reason=""))
    results = run_plan(plan, operator, _REAL_DB, runs=2)

    read_result = _result(results, OP_READ)
    assert read_result.divergence["bad"] == 0


def test_aggregate_cycle_results_preserves_a_zero_bad_divergence_mapping():
    """The truthiness-filtered fold at `_aggregate_cycle_results`
    (`next((r.divergence for r in reversed(ran) if r.divergence), None)`)
    keeps an agreeing mapping because a non-empty dict is truthy -- this
    pin reddens if the recorded agreeing shape ever becomes an empty
    mapping, which the fold would then silently discard."""
    divergence = {
        "repeat_divergent": False,
        "cmp_len": 64,
        "bad": 0,
        "pct": 0.0,
        "first_offset": None,
    }
    a = StepResult(
        op=OP_READ,
        verdict=VERDICT_OK,
        run_count=1,
        duration_s=1.0,
        divergence=divergence,
    )
    b = StepResult(
        op=OP_READ,
        verdict=VERDICT_OK,
        run_count=1,
        duration_s=1.0,
        divergence=divergence,
    )
    folded = _aggregate_cycle_results([a, b], OP_READ)
    assert folded.divergence is not None
    assert folded.divergence["bad"] == 0


def test_read_step_last_run_failure_yields_bad():
    """Roadmap criterion 3's positive half, leg 1: a failing LAST full read
    yields VERDICT_BAD, proven through the real `run_plan` -- `_dispatch_read`
    is never called directly, since Phase 178's transport arm keys on
    exceptions rather than a `False` return and nothing else would
    intercept it. `test_read_step_disagreement_is_divergence_metric_not_marginal`
    and `test_read_step_agreement_no_divergence_recorded` already cover
    criterion 3's negative half (two diverging or two agreeing reads never
    flip the verdict to MARGINAL); this leg and its sibling below are the
    positive half neither one covers. Alone, this leg only shows a failing
    last read gives a bad verdict -- it takes the sibling leg to show WHICH
    run's result the verdict actually reads.
    """
    operator = _mock_operator()
    operator.read_eprom.side_effect = _alternating_read_side_effect(True, False)
    plan = _plan_with_steps(Step(op=OP_READ, supported=True, reason=""))
    results = run_plan(plan, operator, _REAL_DB, runs=2)

    read_result = _result(results, OP_READ)
    assert read_result.verdict == VERDICT_BAD
    assert read_result.run_count == 2


def test_read_step_first_run_failure_with_passing_last_run_yields_ok():
    """Roadmap criterion 3's positive half, leg 2: a failing FIRST read
    with a passing LAST read yields VERDICT_OK, proven through the real
    `run_plan` -- `_dispatch_read` is never called directly. This is the
    leg that discriminates "the last full read" from "the first read" or
    from any fold across runs: together with the sibling leg above, it
    proves the read step's verdict is the last full read's return value
    and nothing else.
    """
    operator = _mock_operator()
    operator.read_eprom.side_effect = _alternating_read_side_effect(False, True)
    plan = _plan_with_steps(Step(op=OP_READ, supported=True, reason=""))
    results = run_plan(plan, operator, _REAL_DB, runs=2)

    read_result = _result(results, OP_READ)
    assert read_result.verdict == VERDICT_OK
    assert read_result.run_count == 2


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
    results = run_plan(plan, operator, _REAL_DB, runs=2)
    write_result = _result(results, OP_WRITE)
    assert write_result.fingerprint is not None


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
    """202-03 (D-02): `classify_fingerprint` is now a thin delegating
    wrapper around `compare.classify_streamed` -- the logic this guard
    originally protected (that the two region-scoping constants never leak
    into the pattern generator or the classifier) moved with it, to
    `classify_streamed` and `CompareAccumulator.feed`. A guard that kept
    inspecting only `classify_fingerprint`'s own handful of delegating
    lines would still pass, but vacuously: it would no longer be looking at
    the code that could actually leak those constants. A green test
    guarding nothing is worse than either fixing it or deleting it --
    fixed here by following the logic to where it now lives."""
    import inspect

    import firestarter.chip_test as chip_test_mod
    import firestarter.compare as compare_mod

    gen_src = inspect.getsource(chip_test_mod.generate_pattern)
    assert "_WRITE_REGION_START" not in gen_src
    assert "_UV_WRITE_REGION_LENGTH" not in gen_src

    classify_src = inspect.getsource(chip_test_mod.classify_fingerprint)
    assert "_WRITE_REGION_START" not in classify_src
    assert "_UV_WRITE_REGION_LENGTH" not in classify_src

    streamed_src = inspect.getsource(compare_mod.classify_streamed)
    assert "_WRITE_REGION_START" not in streamed_src
    assert "_UV_WRITE_REGION_LENGTH" not in streamed_src

    feed_src = inspect.getsource(compare_mod.CompareAccumulator.feed)
    assert "_WRITE_REGION_START" not in feed_src
    assert "_UV_WRITE_REGION_LENGTH" not in feed_src


def _capturing_write(captured: dict):
    """`operator.write_eprom` side_effect that captures the tmp source file's
    bytes at call time (Task 3) -- `_dispatch_multi_run` deletes the tmp file
    in its `finally` block once the run loop returns, so the bytes MUST be
    read back from inside the call itself, not after `run_plan` returns."""

    def _write(
        name: str, eprom_data: dict, source_path: str, *_args, **_kwargs
    ) -> bool:
        captured["bytes"] = Path(source_path).read_bytes()
        return True

    return _write


def test_write_region_via_run_plan_uses_the_plan_carried_window():
    name = "M27C512"
    expected_id = _real_expected_chip_id(name)
    plan = derive_plan(name, _REAL_DB, write_scope="partial")
    write_step = _step(plan, OP_WRITE_PARTIAL)
    assert write_step.write_region == (65280, 256)
    assert write_step.full_device_permitted is False

    operator = _mock_operator()
    operator.check_eprom_id.return_value = (True, expected_id)
    captured: dict = {}
    operator.write_eprom.side_effect = _capturing_write(captured)
    operator.read_eprom.side_effect = _writes_fill_at_requested_region(0xFF)

    results = run_plan(plan, operator, _REAL_DB, runs=2)

    operator.write_eprom.assert_called()
    write_result = _result(results, OP_WRITE_PARTIAL)
    assert write_result.verdict == VERDICT_OK
    assert len(captured["bytes"]) == _UV_WRITE_REGION_LENGTH == 256
    assert captured["bytes"] == generate_pattern(65280, 256)
    assert write_result.write_target is not None
    assert write_result.write_target.region == (65280, 256)


def test_write_region_via_run_plan_uv_part_full_scope_uses_the_top_slot():
    # So a UV part now receives the top slot at BOTH scopes, blank or not,
    # which is what this test's ORIGINAL pre-D-C form asserted. The two
    # superseded expectations, kept for the record:
    #   pre-D-C   : captured["bytes"] == generate_pattern(65280, 256)   <- back
    #   D-C era   : captured["bytes"] == generate_pattern(0, 65536)     <- gone
    name = "M27C512"
    expected_id = _real_expected_chip_id(name)
    plan = derive_plan(name, _REAL_DB, write_scope="full")
    write_step = _step(plan, OP_WRITE)
    assert write_step.write_region == (65280, 256)

    operator = _mock_operator()
    operator.check_eprom_id.return_value = (True, expected_id)
    # Blank -- the state that used to trigger the full-device branch. It no
    # longer changes the region at all, which is the point of this test.
    # 202-05 D-10: check_eprom_blank now returns an int (0 == blank).
    operator.check_eprom_blank.return_value = 0
    captured: dict = {}
    operator.write_eprom.side_effect = _capturing_write(captured)
    operator.read_eprom.side_effect = _writes_fill_at_requested_region(0xFF)

    results = run_plan(plan, operator, _REAL_DB, runs=2)

    operator.write_eprom.assert_called()
    write_result = _result(results, OP_WRITE)
    assert write_result.verdict == VERDICT_OK
    assert write_result.write_target is not None
    assert write_result.write_target.region == (65280, 256)
    assert write_result.write_target.masked is True
    assert write_result.write_target.current_source.startswith("probe read")
    # The LAST cycle's bytes are the fully-staged image, which on a virgin
    # slot is exactly the unstaged masked pattern the pre-D-C form expected.
    assert captured["bytes"] == mask_write_pattern(
        b"\xff" * 256, generate_pattern(65280, 256)
    )
    # `Step.write_region` (derive_plan's own decision) is unchanged.
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
    plan = derive_plan("M27C512", _REAL_DB, write_scope="partial")
    write_step = _step(plan, OP_WRITE_PARTIAL)
    verify_step = _step(plan, OP_VERIFY)

    assert verify_step.op == OP_VERIFY
    assert verify_step.write_region == write_step.write_region == (65280, 256)


def test_count_applicable_bad_counts_as_ran():
    # A BAD read still counts toward N (ran); NA (id) does not.
    operator = _mock_operator()
    operator.read_eprom.return_value = False
    plan = derive_plan("AM2716", _REAL_DB, write_scope="partial")
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
    # counted in M (they are `plan.steps` supported entries).
    ran_ops = {r.op for r in results if r.verdict not in (VERDICT_NA, VERDICT_SKIPPED)}
    assert "write" not in ran_ops
    assert "erase" not in ran_ops
    assert counts.n_ran == len(ran_ops)


def test_count_applicable_m_from_single_plan_never_rederives(monkeypatch):
    import firestarter.chip_test as chip_test_mod

    plan = derive_plan("AM2716", _REAL_DB, write_scope="partial")
    operator = _mock_operator()
    results = run_plan(plan, operator, _REAL_DB)

    spy = Mock(side_effect=AssertionError("count_applicable must not re-derive"))
    monkeypatch.setattr(chip_test_mod, "derive_plan", spy)

    counts = count_applicable(plan, results)

    spy.assert_not_called()
    assert counts.m_applicable == 4


def test_count_applicable_n_equals_m_when_destructive():
    # Same chip (M8720), write_scope="full": every applicable step actually
    # executes -- N == M (banner would not trigger).
    plan = derive_plan("M8720", _REAL_DB, write_scope="full")
    operator = _mock_operator()
    results = run_plan(plan, operator, _REAL_DB)

    counts = count_applicable(plan, results)

    assert counts.n_ran == counts.m_applicable == 5


def chip_test_source_path() -> str:
    import firestarter.chip_test as chip_test_mod

    return chip_test_mod.__file__


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

    runs = 2
    run_plan(plan, operator, _REAL_DB, runs=runs)

    # resolve_chip is called once per executed (supported) step PER CYCLE --
    # never reused from derive_plan's guard-bypassing dict. The expected count
    # is DERIVED from the plan and the engine's own cycle bounds rather than
    # restated, so it tracks a change in either without a hand edit: steps
    # outside the repeat cycle resolve once, steps inside it resolve once per
    # cycle, plus ONE resolve for the cycle planner itself (which resolves the
    # chip to compute the per-cycle write targets before cycle 1 begins).
    block = chip_test_mod.cycle_block_bounds(plan.steps)
    assert block is not None, "M8720's plan must contain a write cycle"
    in_cycle = [s for s in plan.steps[block[0] : block[1]] if s.supported]
    outside_cycle = [s for s in executed_steps if s not in in_cycle]
    expected = len(outside_cycle) + len(in_cycle) * runs + 1
    assert spy.call_count == expected, (
        f"{spy.call_count} resolve_chip calls, expected {expected} "
        f"({len(outside_cycle)} outside the cycle + {len(in_cycle)} inside "
        f"x {runs} cycles + 1 for the cycle planner)"
    )
    for call in spy.call_args_list:
        assert call.args == ("M8720",)
        assert call.kwargs == {"db": _REAL_DB}


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


def test_devtest01_0x0d_sweep_erase_is_supported_and_erase_eprom_is_called():
    """DEVTEST-01 end-to-end sweep leg -- REVERSAL RECORD (Phase 153,
    ERASE-03/ERASE-04). For a protocol-0x0D chip (AT28C256), `run_plan`
    over a plan derived at `write_scope="full"` now produces a REAL,
    supported, destructive erase step, and `operator.erase_eprom` IS
    called (exactly once).

    History: Phase 121 D-12's negative-call assertion (`erase_eprom`
    never called) was the load-bearing line precisely because an NA
    verdict alone did not prove nothing was dispatched -- a fabricated
    erase could still have run behind an NA-reported result. Phase 153
    restored FLAG_CAN_ERASE on all 84 algorithm-13 rows (`database.py`'s
    REVERSAL RECORD) after `configure_eeprom28c` gained a real CMD_ERASE
    dispatch arm (AN-0544B software chip erase) -- so `derive_plan`'s
    erase arm (`can_erase and protocol != _PROTOCOL_FLASH4`) now takes the
    supported branch for this chip. The same reasoning that made the
    negative assertion load-bearing now makes the POSITIVE assertion
    load-bearing: a supported verdict alone does not prove anything WAS
    dispatched either.

    MEASURED DISCREPANCY, recorded rather than silently reconciled (the
    project's own carried-forward convention): this plan's own text calls
    for `erase_eprom.assert_called_once()`. Live-measured against
    `run_plan`'s actual dispatch, that is false -- `OP_ERASE` is a member
    of `_MULTI_RUN_OPS` (the write/erase/verify N>=2 disagreement-policy
    set, D-06), and `run_plan`'s default `runs=2` means `_dispatch_multi_
    run` calls `operator.erase_eprom()` twice, not once, exactly like the
    write and verify steps already do. Asserting `assert_called_once()`
    here would be false on this codebase and was rejected rather than
    forced to pass. `erase_eprom.assert_called()` plus the exact
    `call_count == 2` below is the honest positive-call assertion; both
    calls' return values feed the same two-run disagreement check
    write/verify already use, which is why the verdict resolves to OK
    rather than marginal.

    v1.30 Phase 134 (plan 134-03) SDP-leg notes, unchanged by this
    reversal: AT28C256 is a measured ALLOW chip (`sdp_capability` --
    SDP-capable per infoic.xml INFOIC2PLUS flags bit 15), so `derive_plan`'s
    write_scope="full" plan carries the six real SDP-leg steps after
    "erase" (LEG-01). Uses the read-back-capable
    `_sdp_leg_readback_operator` double (not `_mock_operator()`) so the
    leg's read-back-equality oracle sees a real image rather than an
    empty one, mirroring the sibling test below -- both AT28C256 sweeps
    share one correctly-behaving double rather than two different levels
    of realism for the same chip."""
    name = "AT28C256"
    full = _REAL_DB.get_eprom(name)
    assert full["protocol-id"] == 13  # 0x0D

    plan = derive_plan(name, _REAL_DB, write_scope="full")
    erase_step = _step(plan, "erase")
    assert erase_step.supported is True
    assert erase_step.destructive is True

    operator = _sdp_leg_readback_operator()
    results = run_plan(plan, operator, _REAL_DB)

    erase_result = _result(results, OP_ERASE)
    assert erase_result.verdict == VERDICT_OK
    operator.erase_eprom.assert_called()
    assert operator.erase_eprom.call_count == 2  # see MEASURED DISCREPANCY above


def test_devtest01_0x0d_all_ok_sweep_no_longer_tags_community_fail():
    """DEVTEST-01 ladder leg: an all-OK protocol-0x0D sweep whose erase step
    is NA no longer produces the `community-fail` ladder tag -- that
    fabricated-erase-poisons-an-otherwise-passing-chip's-ladder-state bug is
    exactly what DEVTEST-01 closes. The resulting `ladder_state` is the
    `community-reported` value, and `community-fail` is asserted absent.

    v1.30 Phase 134 (plan 134-03) REPAIR, recorded rather than silently
    patched: AT28C256 is a measured ALLOW chip, so this plan's
    write_scope="full" run now ALSO executes the six real SDP-leg steps
    (LEG-01) after "erase". `_mock_operator()`'s `read_eprom` returns
    `True` while writing NO file, so the SDP leg's oracle would see
    `actual = b""`, the length gate (D-04) would fire, and every leg step
    would report BAD -- turning this test's own `VERDICT_BAD not in
    verdicts` assertion RED for a reason that has nothing to do with what
    the test is meant to prove. Repaired, not weakened: swapped in
    `_sdp_leg_readback_operator`, a stateful SDP-lock-aware double (mirrors
    `tests/test_chip_test_sdp_leg.py::_readback_operator`'s shape, extended
    with real lock/unlock state so a write genuinely blocked while locked
    reports OK on write-inhibited) -- so a genuinely-all-OK sweep across
    all twelve steps (six shipped + six SDP-leg) is genuinely all-OK. The
    `VERDICT_BAD not in verdicts` assertion is UNCHANGED and still passes.

    ⚠ SECOND, DEEPER MEASURED FINDING, NOW SUPERSEDED AGAIN (Phase 177,
    D-177-2/RK-174-05-p177-match-bucket-d4d6): v1.30 Phase 134 (plan 134-03)
    measured that a genuinely-equal SDP-leg read-back (bad=0) fell through
    `classify_fingerprint`'s then-four buckets to `indeterminate` (no
    dedicated "perfect match" bucket existed), which tripped
    `build_db_diff`'s `has_indeterminate_fingerprint` check (Phase 114
    GRAD-01) and routed `ladder_state` to `_LADDER_NONE` ("") instead of
    `_LADDER_COMMUNITY_REPORTED`. Phase 177 adds a `bad == 0 -> match`
    bucket to `classify_fingerprint` (D-177-2), placed after the
    `ff_ratio`/address-line tests so `blank/contact` stays unmoved. A
    genuinely-equal SDP-leg read-back now classifies `match`, not
    `indeterminate`, so `has_indeterminate_fingerprint` no longer trips for
    a genuinely-successful ALLOW-chip run and `ladder_state` returns to
    `_LADDER_COMMUNITY_REPORTED` -- the value this test asserted BEFORE
    134-03's measured finding. DEVTEST-01's ORIGINAL claim (Phase 121) --
    that a fabricated erase-NA no longer poisons the ladder state to
    `community-fail` -- still holds and is what the first assertion below
    proves; the intervening MEASURED-SUPERSEDED note from 134-03 is itself
    now superseded, recorded here rather than silently dropped. See
    177-01-SUMMARY.md for the re-key this finding is measured against; the
    `MILESTONES.md` ledger row that recorded it was retired with the
    cross-tree checker on 2026-09-08, and Phase 177's own archived record is
    now the reference."""
    from firestarter.diagnostic_report import build_db_diff

    name = "AT28C256"
    plan = derive_plan(name, _REAL_DB, write_scope="full")
    operator = _sdp_leg_readback_operator()
    results = run_plan(plan, operator, _REAL_DB)

    verdicts = {r.verdict for r in results}
    assert VERDICT_BAD not in verdicts

    db_diff = build_db_diff(name, _REAL_DB, results)
    assert db_diff.ladder_state != "community-fail"
    assert db_diff.ladder_state == "community-reported"


def test_r6_laundering_allow_plans_never_derive_an_empty_steps_list():
    """R6 (LEG-17): `cli_handlers.py`'s `if not results: sys.exit(0)`
    bypasses the exit composition entirely, so the honest discharge is
    proving the PRECONDITION unreachable, not adding a code path: every
    SDP-ALLOW chip's `write_scope="full"` plan derives a non-empty
    `Plan.steps`, so an ALLOW-chip run can never reach that guard.
    Additionally: if `results` genuinely were empty, `sdp_lock` was never
    called -- trivially true, since `run_plan` never dispatched a single
    step -- proven directly against an empty `Plan` below."""
    offenders = []
    for full in _REAL_DB.get_eproms():
        name = full["name"]
        allowed, _reason = sdp_capability_for_entry(full, name)
        if not allowed:
            continue
        plan = derive_plan(name, _REAL_DB, write_scope="full")
        if not plan.steps:
            offenders.append(name)
    assert not offenders, (
        f"{len(offenders)} SDP-ALLOW chip(s) derived an EMPTY Plan.steps at "
        "write_scope='full', which would let an ALLOW-chip run reach "
        f"cli_handlers.py's 'if not results: sys.exit(0)' guard: "
        f"{offenders[:5]}"
    )

    empty_plan = Plan(name="__empty_plan_for_r6__", steps=[])
    operator = _mock_operator()
    empty_results = run_plan(empty_plan, operator, _REAL_DB)
    assert empty_results == []
    operator.sdp_lock.assert_not_called()


class _NonQualifyingEtype28CDatabase(EpromDatabase):
    """Overrides AT28C256's `electrical-type` to a value outside the
    qualifying set, for exactly one chip name -- mirrors
    `tests/fixtures/synthetic_nonzero_chip_id.py`'s
    `SyntheticNonzeroChipIdDatabase` shape (subclass `EpromDatabase`,
    override `get_eprom` for one name, copy every other field verbatim).

    `database.py`'s `convert_to_programmer` sets `FLAG_CAN_ERASE` only when
    `electrical-type in ("EEPROM", "Flash/EEPROM")`; every shipped
    algorithm-13 row satisfies that, which is exactly why the
    `_PROTOCOL_EEPROM_28C` reason arm is unreachable in production today.
    "OTP" is a synthetic, clearly-non-real value chosen only because it is
    outside the qualifying set, is not "UV-EPROM" (which would route to the
    UV arm first), and is not in `_SRAM_FRAM_ETYPES` (which would route to
    the blank-check SRAM/FRAM branch, irrelevant here but kept clean)."""

    def get_eprom(self, chip_name: str) -> dict | None:
        full = super().get_eprom(chip_name)
        if full is not None and chip_name == "AT28C256":
            full = dict(full)
            full["electrical-type"] = "OTP"
        return full


def test_protocol_eeprom_28c_arm_reachable_for_non_qualifying_etype():
    """The `_PROTOCOL_EEPROM_28C` defensive fallthrough arm still fires for
    a `0x0D` row whose `electrical-type` is outside the qualifying set --
    every SHIPPED row qualifies (all 84 algorithm-13 rows carry
    `electrical-type` in {"EEPROM", "Flash/EEPROM"}), so this arm is
    reachable only through a user override like this fixture. Without this
    leg, restoring FLAG_CAN_ERASE on all shipped rows would leave the kept
    arm untested dead code."""
    db = _NonQualifyingEtype28CDatabase(skip_local_override=True)
    full = db.get_eprom("AT28C256")
    assert full["protocol-id"] == 13  # 0x0D
    assert full["electrical-type"] == "OTP"

    prog = db.convert_to_programmer(full)
    assert prog.get("flags", 0) == 0  # FLAG_CAN_ERASE did NOT get set

    plan = derive_plan("AT28C256", db, write_scope="full")
    erase_step = _step(plan, "erase")
    assert erase_step.supported is False
    assert (
        erase_step.reason == "electrical-type for this 0x0D (28C family) chip is not "
        "electrically erasable; no erase step is planned for it"
    )
    assert "FLAG_CAN_ERASE not set for this chip" not in erase_step.reason
    assert "FLAG_CAN_ERASE" not in erase_step.reason


def _gated_allow_operator():
    """Dead-write-path double for an ALLOW chip's `write_scope="full"` plan
    (the same shape as `tests/test_chip_test_sdp_leg.py::_dead_write_path_
    operator`, re-authored locally here rather than imported cross-file):
    `write_eprom` always reports success while `read_eprom` ALWAYS yields
    pattern A over the plan's default write region, regardless of what was
    actually written -- a chip whose write path never transitions. This is
    the fixture `write-baseline-b` (which expects pattern B back) reports
    BAD against, while `write-baseline-a` (which expects pattern A back)
    reports OK -- exactly gh#20's dead-write-path shape, closing the
    baseline gate (D-08) after both baseline directions have genuinely run."""
    region = _DEFAULT_REGION
    a = generate_pattern(*region)
    operator = Mock(spec=_OPERATOR_METHODS)
    operator.check_eprom_id.return_value = (True, None)
    # 202-05 D-10: check_eprom_blank now returns an int (0 == blank).
    operator.check_eprom_blank.return_value = 0
    operator.erase_eprom.return_value = True
    # 202-01 D-10: verify_eprom now returns an int (0 == match).
    operator.verify_eprom.return_value = 0
    operator.write_eprom.return_value = True

    def _read_eprom(name, eprom_data, output_file=None, **kwargs):
        if output_file is not None:
            Path(output_file).write_bytes(a)
        return True

    operator.read_eprom.side_effect = _read_eprom
    return operator


def test_count_applicable_sdp_gated_allow_chip_ratio_drops():
    """LEG-13's pinning test. For AT28C256 (a measured ALLOW chip) at
    `write_scope="full"` with the oracle gated (the dead-write-path shape,
    gh#20's own bench), `count_applicable` measures `m_applicable == 10`
    and `n_ran == 6` (Phase 153 THIRD-GENERATION figures; see below).

    THIRD-GENERATION accounting (Phase 153, ERASE-03/ERASE-04), both
    earlier generations kept visible rather than overwritten:

      Generation 1 (pre-260807-kaq): blank-check was a real supported
      step at its historic position (index 2, before write) -- measured
      `n_ran=6, m_applicable=10`.

      Generation 2 (260807-kaq): blank-check flipped to NA-by-family-fact
      for protocol 0x0D (case 3: every page write auto-erases internally,
      so no step could ever leave the device blank) -- REMOVED from both M
      and N. `m_applicable` dropped 10 -> 9 (3 shipped-supported
      [read/write/verify] + 6 SDP-leg-supported, since id, erase AND
      blank-check were all NA); `n_ran` dropped 6 -> 5.

      Generation 3 (THIS plan, Phase 153 ERASE-03/ERASE-04): restoring
      FLAG_CAN_ERASE on all 84 algorithm-13 rows flips AT28C256's plan to
      a live-measured TWELVE steps -- id (NA), read, write, verify, erase
      (now SUPPORTED and destructive, index 4), blank-check (still NA,
      moved from index 2 to index 5, now sitting behind the erase it
      doubles as an oracle for), then the six SDP-leg ops. TEN of the
      twelve are supported: everything except id and blank-check. Erase
      JOINS the applicable set (it did not exist as a real step in
      Generation 2's accounting), so `m_applicable` rises 9 -> 10. Erase
      also runs and reports (against `_gated_allow_operator`'s always-
      succeeding `erase_eprom`), so `n_ran` rises 5 -> 6 (the read/write/
      verify shipped trio plus erase, plus write-baseline-b/write-
      baseline-a, which report BAD/OK respectively and both count as ran;
      the four `_SDP_LEG_GATED_OPS` members still SKIP once the baseline
      gate closes).

      Recorded explicitly, not glossed: Generation 3's integers (10, 6)
      numerically COINCIDE with Generation 1's pre-260807-kaq figures, but
      this is a coincidence of composition, NOT a restoration -- blank-
      check is NA in both Generation 2 and 3 (unlike Generation 1, where
      it was a real supported step); it is erase joining the applicable/
      ran sets, not blank-check returning to them, that produces the same
      pair of integers. LEG-13's own claim is unaffected either way -- the
      headline ratio still DROPS, now from a misleading "4 of 4" to a
      real "6 of 10" under this leg.

    Figures re-derived live in this session against this commit's
    `chip_test.py` and `_gated_allow_operator` fixture (unchanged by this
    plan), not transcribed from plan text.
    """
    plan = derive_plan("AT28C256", _REAL_DB, write_scope="full")
    operator = _gated_allow_operator()
    results = run_plan(plan, operator, _REAL_DB)

    counts = count_applicable(plan, results)
    assert counts.m_applicable == 10, counts  # erase joined M (9 -> 10)
    assert counts.n_ran == 6, counts  # erase joined N (5 -> 6)
    assert counts.n_ran < counts.m_applicable, counts

    erase_result = _result(results, OP_ERASE)
    assert erase_result.verdict == VERDICT_OK, erase_result
    write_baseline_b = _result(results, "write-baseline-b")
    assert write_baseline_b.verdict == VERDICT_BAD, write_baseline_b
    write_inhibited = _result(results, "write-inhibited")
    assert write_inhibited.verdict == VERDICT_SKIPPED, write_inhibited


def test_count_applicable_refuse_chip_n_equals_m_is_out_of_leg13_scope():
    """LEG-13 says "for ALLOW chips" -- the REFUSE case is explicitly OUT OF
    SCOPE, recorded here as a truthful, mechanically-asserted reading rather
    than a claim in prose. A REFUSE chip's six SDP steps carry
    `supported=False` (NA), so `count_applicable` excludes them from BOTH M
    and N -- `N == M` holds, the banner-trigger condition the ALLOW case
    above breaks. This is NOT silently extended to also claim the REFUSE
    case's ratio drops -- it does not, and this test proves that."""
    name = "M8720"  # measured REFUSE chip (protocol 0x08, not 0x0D)
    plan = derive_plan(name, _REAL_DB, write_scope="full")
    leg_steps = [s for s in plan.steps if s.op in _SDP_LEG_STEP_ORDER]
    assert leg_steps and all(not s.supported for s in leg_steps), leg_steps

    operator = _mock_operator()
    results = run_plan(plan, operator, _REAL_DB)
    counts = count_applicable(plan, results)
    assert counts.n_ran == counts.m_applicable, counts  # REFUSE: N == M, no drop


def test_count_applicable_sdp_banner_row_renders_the_dropped_ratio():
    """LEG-13's visible surface: `diagnostic_report.py`'s banner row formats
    `"{n_ran} of {m_applicable} ran"` (needing no code edit) -- assert the
    rendered console text of a report built from the same gated ALLOW run
    above shows the dropped ratio, not a perfect one.

    THIRD-GENERATION accounting -- see `test_count_applicable_sdp_gated_
    allow_chip_ratio_drops`'s docstring above for the full three-
    generation M/N history (10/6 pre-260807-kaq, 9/5 post-260807-kaq,
    10/6 again after this plan restores FLAG_CAN_ERASE -- a composition
    coincidence, not a restoration, per that docstring). The rendered-
    text assertion below is driven off `banner`'s own fields so it needs
    no second hardcoded literal. The `"4 of 4 ran"` negative assertion is
    NOT made vacuous by the new figures (6 of 10 is still neither "4 of
    4" nor any other perfect ratio), so it is kept unchanged."""
    from rich.console import Console

    import firestarter
    from firestarter.diagnostic_report import (
        AutoCapture,
        DiagnosticReport,
        TransportHealth,
    )

    plan = derive_plan("AT28C256", _REAL_DB, write_scope="full")
    operator = _gated_allow_operator()
    results = run_plan(plan, operator, _REAL_DB)
    banner = count_applicable(plan, results)
    # (third generation): n_ran/m_applicable rose 5/9 -> 6/10 --
    # erase is now a real supported step that runs for this
    # protocol-0x0D chip. See the docstring above on
    # `test_count_applicable_sdp_gated_allow_chip_ratio_drops` for the
    # full M/N delta accounting across all three generations.
    assert banner.n_ran == 6 and banner.m_applicable == 10  # see the docstring above

    auto_capture = AutoCapture(
        host_version=firestarter.__version__, chip="AT28C256", protocol="0x0D"
    )
    report = DiagnosticReport(
        auto_capture=auto_capture,
        transport=TransportHealth(),
        plan=plan,
        results=results,
        banner=banner,
    )
    table = report.render()
    console = Console(record=True, width=200)
    console.print(table)
    rendered = console.export_text()
    assert f"{banner.n_ran} of {banner.m_applicable} ran" in rendered, rendered
    assert "4 of 4 ran" not in rendered, rendered


# Execution-time mask, slot selection and region-scoped I/O (quick task
# 260821-wna, Task 4) -- driven against `fake_chip.FakeChip` through
# `run_plan` (not through the CLI) so each property is pinned at the engine
# seam. `FakeChip` genuinely models UV AND-write physics and absolute-offset
# reads (M-3); a plain `Mock` cannot exercise these properties honestly.

from .fake_chip import FakeChip  # noqa: E402


def test_full_device_write_non_uv_covers_whole_device():
    # AT28C256 (EEPROM, 32768 B, protocol 0x0D -- not flash4): the write
    # step's resolved target spans the WHOLE device, address_str is None
    # (region start 0), and the verify step's target is the SAME object.
    name = "AT28C256"
    plan = derive_plan(name, _REAL_DB, write_scope="full")
    chip = FakeChip.non_uv(32768)
    results = run_plan(plan, chip, _REAL_DB)

    write_result = _result(results, "write")
    verify_result = _result(results, "verify")
    assert write_result.verdict == VERDICT_OK, write_result
    assert write_result.write_target is not None
    assert write_result.write_target.region == (0, 32768)
    assert len(write_result.write_target.pattern) == 32768
    assert verify_result.verdict == VERDICT_OK, verify_result
    write_calls = [c for c in chip.calls if c[0] == "write_eprom"]
    assert write_calls and all(c[1]["address_str"] is None for c in write_calls)


def test_full_device_write_flash4_carves_out_boot_blocks():
    # W29C040 (Flash/EEPROM, protocol 5, 524288 B): the write target excludes
    # the first/last 16 KiB boot blocks, and `write_eprom` receives an
    # `address_str` naming 0x4000 with a file of `memory-size - 32768` bytes.
    name = "W29C040"
    plan = derive_plan(name, _REAL_DB, write_scope="full")
    chip = FakeChip.non_uv(524288)
    results = run_plan(plan, chip, _REAL_DB)

    write_result = _result(results, "write")
    assert write_result.verdict == VERDICT_OK, write_result
    assert write_result.write_target is not None
    assert write_result.write_target.region == (16384, 491520)
    assert len(write_result.write_target.pattern) == 524288 - 32768
    write_calls = [c for c in chip.calls if c[0] == "write_eprom"]
    assert write_calls and all(c[1]["address_str"] == "0x4000" for c in write_calls)


def test_uv_virgin_full_scope_gets_the_top_slot_not_the_whole_device():
    # A virgin part is now treated exactly like a used one: one top slot. The
    # blank-check still RUNS and is still reported (a UV part that is not
    # blank is an operator-actionable finding) -- it simply no longer decides
    # how much of the part gets consumed.
    name = "M27C512"
    plan = derive_plan(name, _REAL_DB, write_scope="full")
    chip = FakeChip.virgin_uv(65536)
    results = run_plan(plan, chip, _REAL_DB)

    write_result = _result(results, "write")
    assert write_result.verdict == VERDICT_OK, write_result
    target = write_result.write_target
    assert target is not None
    assert target.region == (65280, 256)
    assert target.masked is True
    assert target.current_source.startswith("probe read")
    # On a virgin slot the masked image IS the plain address-derived pattern
    # (mask_write_pattern(0xFF, D) == D), fully staged by the final cycle.
    assert target.pattern == generate_pattern(65280, 256)


def test_uv_full_and_partial_scope_now_resolve_to_the_same_slot():
    """The consequence that retired the UV prompt: with D-C gone, both scope
    literals produce an identical write on a UV part, so a yes/no ask could
    not change the outcome -- which is the inert-prompt defect quick task
    260821-wna existed to fix. Asserted here so a future re-introduction of a
    scope-keyed UV branch has to break this test to land."""
    name = "M27C512"
    targets = []
    for scope, op in (("full", "write"), ("partial", "write-partial")):
        plan = derive_plan(name, _REAL_DB, write_scope=scope)
        result = _result(run_plan(plan, FakeChip.virgin_uv(65536), _REAL_DB), op)
        assert result.verdict == VERDICT_OK, result
        assert result.write_target is not None
        targets.append(result.write_target)

    assert targets[0].region == targets[1].region
    assert targets[0].pattern == targets[1].pattern


def test_uv_virgin_partial_scope_writes_single_top_slot_not_whole_device():
    # Same virgin chip, write_scope="partial": full_device_permitted is
    # False, so the scope literal is honoured -- a single top slot, never
    # the whole device.
    name = "M27C512"
    plan = derive_plan(name, _REAL_DB, write_scope="partial")
    chip = FakeChip.virgin_uv(65536)
    results = run_plan(plan, chip, _REAL_DB)

    write_result = _result(results, "write-partial")
    assert write_result.verdict == VERDICT_OK, write_result
    target = write_result.write_target
    assert target is not None
    assert target.region == (65280, 256)
    assert target.pattern == generate_pattern(65280, 256)


def test_uv_used_chip_write_is_genuinely_masked_and_verify_reads_the_region():
    # A UV chip whose top slot already carries SOME content (0xF0 in every
    # byte -- half its bits already cleared relative to a virgin cell): the
    # file `write_eprom` receives equals `mask_write_pattern(slot_content,
    # generate_pattern(slot_start, slot_length))`, and the verify step's
    # own read-back is the REGION slice, not a device-prefix read.
    name = "M27C512"
    plan = derive_plan(name, _REAL_DB, write_scope="partial")
    slot_content = b"\xf0" * 256
    chip = FakeChip.uv_with_content(65536, slot_content, start=65280)
    expected_pattern = generate_pattern(65280, 256)
    expected_masked = mask_write_pattern(slot_content, expected_pattern)

    results = run_plan(plan, chip, _REAL_DB)

    write_result = _result(results, "write-partial")
    assert write_result.verdict == VERDICT_OK, write_result
    target = write_result.write_target
    assert target is not None
    assert target.masked is True
    assert target.pattern == expected_masked
    verify_result = _result(results, "verify")
    assert verify_result.verdict == VERDICT_OK, verify_result


def test_slot_advance_skips_a_saturated_top_slot():
    # The top slot (65280, 256) already carries EXACTLY this pattern (a
    # prior UV write with the same address-derived pattern) -- saturated
    # under D-B (bits_cleared == 0). The selector must advance to the next
    # slot down (65024, 256), and `address_str` must name THAT slot.
    name = "M27C512"
    plan = derive_plan(name, _REAL_DB, write_scope="partial")
    chip = FakeChip.uv_with_content(65536, generate_pattern(65280, 256), start=65280)
    results = run_plan(plan, chip, _REAL_DB)

    write_result = _result(results, "write-partial")
    assert write_result.verdict == VERDICT_OK, write_result
    target = write_result.write_target
    assert target is not None
    assert target.region == (65024, 256), target.region
    write_calls = [c for c in chip.calls if c[0] == "write_eprom"]
    assert write_calls and all(c[1]["address_str"] == "0xFE00" for c in write_calls)


def test_zeroed_slot_is_never_targeted():
    # The top slot reads all-0x00 -- the vacuous-pass refusal's other named
    # case. It must never be targeted; the selector advances past it to the
    # next (virgin) slot.
    name = "M27C512"
    plan = derive_plan(name, _REAL_DB, write_scope="partial")
    chip = FakeChip.uv_with_slot_zeroed(65536, 65280, 256)
    results = run_plan(plan, chip, _REAL_DB)

    write_result = _result(results, "write-partial")
    assert write_result.verdict == VERDICT_OK, write_result
    target = write_result.write_target
    assert target is not None
    assert target.region != (65280, 256)
    assert target.region == (65024, 256)


def test_every_slot_saturated_write_is_skipped_never_ok():
    # A UV chip whose EVERY candidate slot is saturated under this pattern:
    # the write step verdict is SKIPPED naming saturation, the verify step
    # is SKIPPED too, `write_eprom` is NEVER called, and no step reports OK
    # for the write.
    name = "M27C512"
    plan = derive_plan(name, _REAL_DB, write_scope="full")
    chip = FakeChip.uv_all_saturated(65536, 256)
    # 202-05 D-10: FakeChip.check_eprom_blank now returns an int (0 == blank).
    assert chip.check_eprom_blank(name, {}) == 1  # sanity: not the D-C path

    results = run_plan(plan, chip, _REAL_DB)

    write_result = _result(results, "write")
    verify_result = _result(results, "verify")
    assert write_result.verdict == VERDICT_SKIPPED, write_result
    assert "saturat" in write_result.reason.lower(), write_result.reason
    assert write_result.write_target is None
    assert verify_result.verdict == VERDICT_SKIPPED, verify_result
    write_calls = [c for c in chip.calls if c[0] == "write_eprom"]
    assert write_calls == [], "write_eprom must never be called on a saturated chip"
    assert all(r.verdict != VERDICT_OK for r in (write_result, verify_result))


def test_probe_never_persists_a_slot_cursor_to_disk(tmp_path, monkeypatch):
    # Slot selection is stateless: the chip's own content is the state.
    # Patch the config dir to a throwaway directory and assert nothing new
    # appears there after a UV partial-scope run that must probe.
    monkeypatch.setenv("FIRESTARTER_CONFIG_DIR", str(tmp_path))
    name = "M27C512"
    plan = derive_plan(name, _REAL_DB, write_scope="partial")
    chip = FakeChip.virgin_uv(65536)
    run_plan(plan, chip, _REAL_DB)
    assert list(tmp_path.iterdir()) == [], "a slot cursor was persisted to disk"
