"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Criterion-4 regression for Phase 179 (UV Slot Writes -- FLAG_SKIP_BLANK_CHECK,
hardware-gated).

This module proves the HOST path: that the engine composes
`FLAG_SKIP_BLANK_CHECK` from the monotonicity witness, passes it positionally,
and adjudicates the UV pre-write blank-check so the run folds to `PASS`. It
does NOT prove the hardware claim -- that a physical UV EPROM in a physical
socket accepts the write -- which is `D-179-1`'s bench half and lands as
`179-MEASUREMENT.md`. Naming that boundary here is the point: the failure
this project has already recorded is a fixture-passing selftest read as
working tooling.

Test taxonomy:

  Operator-double harness
    _REAL_DB, _OPERATOR_METHODS, _result -- copied in shape from
    tests/test_chip_test_sdp_leg.py:219-262, not imported (these names are
    module-private in a large sibling test module). `_OPERATOR_METHODS`
    backs leg 12's `Mock(spec=_OPERATOR_METHODS)` -- no chip state is
    needed there, and `spec=` is what makes a typo'd method raise
    `AttributeError` rather than silently answering truthy, the
    absent-chip false-green family this project refuses.

  The double is not theatre (legs 1-2)
    test_the_double_refuses_a_non_blank_write_without_the_flag
    test_the_double_accepts_a_non_blank_write_with_the_flag
      -- these exist so legs 3-6 cannot pass against a double that refuses
      nothing.

  Criterion 4's committed core (legs 3-6)
    test_uv_slot_write_on_a_non_blank_part_reaches_pass_with_run_count_two
      -- the criterion-4 claim itself.
    test_the_flag_reaches_the_wire_on_every_cycle
      -- the flag is on the wire for BOTH cycles, not just the first.
    test_the_blank_check_adjudication_is_what_lifts_the_run_to_pass
      -- the PASS is produced by the adjudication, not by the run
      happening to contain no BAD for some other reason.
    test_uv_slot_write_preserves_the_not_blank_finding
      -- the finding survives the verdict change (Q7).

  The witness wins -- ROADMAP criterion 3 (legs 7-8)
    test_uv_slot_policy_without_the_witness_does_not_set_the_flag
    test_fixed_policy_with_the_witness_sets_the_flag
      -- the two signals constructed in disagreement, in both directions,
      via the public `WriteContext.cycle_targets` injection seam -- never a
      monkeypatch of `_resolve_write_target`.

  Anti-vacuity siblings (legs 9-12)
    test_the_witness_is_false_for_absent_empty_and_unmasked_targets
      -- the fail-closed empty cases.
    test_the_prescribed_probe_read_string_equality_would_never_match
      -- `.planning/research/SUMMARY.md:89`'s prescribed witness form is
      measured to never match; this leg turns a future regression to it
      into a RED instead of a silent no-op.
    test_a_non_uv_plan_never_sets_the_skip_blank_check_flag
      -- the flag is not always-on.
    test_a_non_uv_blank_check_failure_is_still_bad
      -- the adjudication is not always-on.
"""

from __future__ import annotations

import copy
import re
import tempfile
from pathlib import Path
from unittest.mock import Mock

from firestarter import chip_test as ct
from firestarter import submit as sub
from firestarter.constants import FLAG_SKIP_BLANK_CHECK
from firestarter.database import EpromDatabase
from firestarter.diagnostic_report import AutoCapture, DiagnosticReport, TransportHealth

from .fake_chip import WriteInitPreflightChip

_REAL_DB = EpromDatabase(skip_local_override=True)

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


def _result(results, op):
    for r in results:
        if r.op == op:
            return r
    raise AssertionError(f"no result for op {op!r} in {[r.op for r in results]}")


def _seeded_m27c512_double() -> tuple[WriteInitPreflightChip, dict]:
    """A `WriteInitPreflightChip` at the real `m27c512` memory size, seeded
    with content OUTSIDE `uv_slot_starts`' top-down first slot (the target
    write slot at the HIGH end of the device stays virgin). This is UV-01's
    precondition, and the same shape as Phase 83's bench-proven 16-byte
    write at `0x0000`."""
    full = _REAL_DB.get_eprom("m27c512") or {}
    mem_size = int(full.get("memory-size", 0) or 0)
    chip = WriteInitPreflightChip(mem_size, uv=True)
    chip.data[0x0000:0x0100] = bytes(range(256))
    return chip, full


def test_the_double_refuses_a_non_blank_write_without_the_flag() -> None:
    """Mirrors `firestarter/src/proms/eprom.cpp:143-145`: `write_eprom`
    RETURNS `False` and stamps `last_firmware_error_code` when the target
    region is not blank and `FLAG_SKIP_BLANK_CHECK` is absent -- it never
    raises, matching the REAL `EpromOperator.write_eprom` contract, where
    `eprom_operations._run_state_machine` catches the `EpromOperationError`
    and returns `(False, str(e))` (`eprom_operations.py:597-609`). Without
    this leg, every downstream leg asserting `verdict == OK` would be
    theatre: a double that refuses nothing makes any PASS meaningless.

    Phase 201 (BLANK-01) scoped the real write-init blank check -- and this
    fake, in lockstep -- to the write's OWN target region rather than the
    whole device. `_seeded_m27c512_double`'s non-blank content sits
    OUTSIDE the target slot deliberately (legs 3-6 below need exactly that
    shape to exercise the witness/FLAG_SKIP_BLANK_CHECK policy), so it no
    longer makes a region-scoped double refuse anything -- reusing it here
    would make this leg theatre again, the opposite of its own purpose.
    This leg therefore seeds its own double with the non-blank byte INSIDE
    the write's target region, which is what a region-scoped refusal
    actually requires."""
    full = _REAL_DB.get_eprom("m27c512") or {}
    mem_size = int(full.get("memory-size", 0) or 0)
    chip = WriteInitPreflightChip(mem_size, uv=True)
    chip.data[0xFF00:0xFF10] = bytes(range(16))
    ed = ct.resolve_chip("m27c512", db=_REAL_DB)
    fh = tempfile.NamedTemporaryFile(prefix="p179_", suffix=".bin", delete=False)
    fh.write(ct.generate_pattern(0xFF00, 256))
    fh.close()
    try:
        outcome = chip.write_eprom("m27c512", ed, fh.name, 0, address_str="0xff00")
    finally:
        Path(fh.name).unlink()
    assert outcome is False
    assert chip.last_firmware_error_code == 0xB0


def test_the_double_accepts_a_non_blank_write_with_the_flag() -> None:
    """The converse of the leg above, on the SAME seeded double: with
    `FLAG_SKIP_BLANK_CHECK` set, `write_eprom` returns `True` and leaves
    `last_firmware_error_code` at `None` -- the firmware pre-flight is
    genuinely bypassed, not merely reported as bypassed."""
    chip, _full = _seeded_m27c512_double()
    ed = ct.resolve_chip("m27c512", db=_REAL_DB)
    fh = tempfile.NamedTemporaryFile(prefix="p179_", suffix=".bin", delete=False)
    fh.write(ct.generate_pattern(0xFF00, 256))
    fh.close()
    try:
        outcome = chip.write_eprom(
            "m27c512", ed, fh.name, FLAG_SKIP_BLANK_CHECK, address_str="0xff00"
        )
    finally:
        Path(fh.name).unlink()
    assert outcome is True
    assert chip.last_firmware_error_code is None


def test_uv_slot_write_on_a_non_blank_part_reaches_pass_with_run_count_two() -> None:
    """The criterion-4 core: a UV part holding data outside the target slot
    accepts the slot write and the two-cycle run folds to
    `submit.overall_verdict(results) == "PASS"` with the write and verify
    steps' `run_count == 2`. `overall_verdict` is asserted directly on
    `report.results`, never on a `to_dict()` key -- `DiagnosticReport.
    to_dict()` carries `run_status`, not `overall_verdict`; the only route
    to the world is `submit.build_title`."""
    chip, full = _seeded_m27c512_double()
    plan = ct.derive_plan("m27c512", _REAL_DB, write_scope="full")
    results = ct.run_plan(plan, chip, _REAL_DB, runs=2)
    write = _result(results, ct.OP_WRITE)
    verify = _result(results, ct.OP_VERIFY)
    assert write.verdict == ct.VERDICT_OK
    assert write.run_count == 2
    assert verify.verdict == ct.VERDICT_OK
    assert verify.run_count == 2
    assert sub.overall_verdict(results) == "PASS"
    prog = _REAL_DB.convert_to_programmer(full)
    report = DiagnosticReport(
        auto_capture=AutoCapture(
            host_version="0.0.0",
            chip="m27c512",
            protocol=str(prog.get("algorithm")),
        ),
        transport=TransportHealth(),
        plan=plan,
        results=results,
    )
    title = sub.build_title(report, "m27c512")
    assert re.match(r"^\[dev test\] m27c512 — PASS \([0-9a-f]{12}\)$", title)


def test_the_flag_reaches_the_wire_on_every_cycle() -> None:
    """`chip.write_flags_seen == [8, 8]` after a two-cycle run -- the flag is
    on the wire for cycle 1 AND cycle 2. The second entry is the interesting
    one: the staged tranche targets are the only ones that reach
    `write_eprom`, so a witness that were not carried through
    `_uv_cycle_targets` would give `[8, 0]` -- or, one iteration earlier in
    this design's history, `[0, 0]` from a `current_source == "probe read"`
    equality that never matches (see the anti-vacuity leg below)."""
    chip, _full = _seeded_m27c512_double()
    plan = ct.derive_plan("m27c512", _REAL_DB, write_scope="full")
    ct.run_plan(plan, chip, _REAL_DB, runs=2)
    assert chip.write_flags_seen == [8, 8]


def test_the_blank_check_adjudication_is_what_lifts_the_run_to_pass() -> None:
    """UV-02's own text: `FLAG_SKIP_BLANK_CHECK` fixes the firmware
    write-init pre-flight ONLY; the standalone `blank-check` step is a
    second, independent defect, and "the write step going OK" is explicitly
    not the criterion. Deep-copying the same results and forcing only the
    `blank-check` verdict back to `BAD` must fold to `FAIL` -- proving the
    `PASS` above was produced by the adjudication, not by the run happening
    to contain no `BAD` for some other reason."""
    chip, _full = _seeded_m27c512_double()
    plan = ct.derive_plan("m27c512", _REAL_DB, write_scope="full")
    results = ct.run_plan(plan, chip, _REAL_DB, runs=2)
    assert sub.overall_verdict(results) == "PASS"
    forced = copy.deepcopy(results)
    _result(forced, ct.OP_BLANK_CHECK).verdict = ct.VERDICT_BAD
    assert sub.overall_verdict(forced) == "FAIL"


def test_uv_slot_write_preserves_the_not_blank_finding() -> None:
    """Q1a: the same run's `blank-check` result carries `verdict ==
    "SKIPPED"` (never `NA`, which was rejected precisely because
    `_reason_text` suppresses an `NA` row's reason to `-` and would destroy
    the finding this step exists to surface), a non-empty `reason`,
    `error_code == 176` (`MSG_ERR_NOT_BLANK`), and
    `submit._reason_text(verdict, reason)` returns the reason VERBATIM."""
    chip, _full = _seeded_m27c512_double()
    plan = ct.derive_plan("m27c512", _REAL_DB, write_scope="full")
    results = ct.run_plan(plan, chip, _REAL_DB, runs=2)
    blank_check = _result(results, ct.OP_BLANK_CHECK)
    assert blank_check.verdict == ct.VERDICT_SKIPPED
    assert blank_check.reason
    assert blank_check.error_code == 176
    assert sub._reason_text(blank_check.verdict, blank_check.reason) == (
        blank_check.reason
    )


def test_uv_slot_policy_without_the_witness_does_not_set_the_flag() -> None:
    """Direction A of ROADMAP criterion 3's disagreement: `region_policy ==
    "uv-slot"` and the mask came from a probe read are CURRENTLY coextensive
    but are NOT the same predicate, and were provably not coextensive one
    design iteration ago. Injects a hand-built, UNMASKED target via the
    public `WriteContext.cycle_targets` seam -- which short-circuits
    `_resolve_write_target` (`_cycle_target`, `chip_test.py:1330-1345`) --
    so the policy says uv-slot while the witness is absent. Asserts on THE
    FLAGS THE DOUBLE RECORDED, never on the verdict, which is `OK` in both
    directions and would prove nothing; and on ZERO `read_eprom` calls, so
    the leg is proven to test the witness rather than the resolver."""
    step = ct.Step(
        op=ct.OP_WRITE,
        supported=True,
        reason="",
        destructive=True,
        write_region=(0xFF00, 256),
        region_policy=ct.REGION_POLICY_UV_SLOT,
        full_device_permitted=False,
        cycle_payload=ct.CYCLE_PAYLOAD_UV_TRANCHE,
    )
    target = ct.WriteTarget(
        region=(0xFF00, 256),
        pattern=ct.generate_pattern(0xFF00, 256),
        masked=False,
        bits_cleared=0,
        bits_retained=0,
        current_source="address-derived pattern (unmasked)",
        region_policy=ct.REGION_POLICY_UV_SLOT,
    )
    write_context = ct.WriteContext()
    write_context.cycle_targets = [target]
    write_context.cycle_index = 0
    ed = ct.resolve_chip("m27c512", db=_REAL_DB)
    chip = WriteInitPreflightChip(65536, uv=True)
    ct._dispatch_multi_run(
        ct.OP_WRITE,
        "m27c512",
        ed,
        chip,
        runs=1,
        step=step,
        write_context=write_context,
    )
    assert chip.write_flags_seen == [0]
    assert sum(1 for call in chip.calls if call[0] == "read_eprom") == 0


def test_fixed_policy_with_the_witness_sets_the_flag() -> None:
    """Direction B, the converse: a `fixed`-policy `Step` with a hand-built,
    MASKED, probe-read-witnessed target injected via the same
    `WriteContext.cycle_targets` seam. The policy says fixed; the witness
    is present; the flag must go ON regardless. Legs 7 and 8 together are
    ROADMAP criterion 3's "constructed so the two signals disagree and the
    witness wins" -- one direction alone would be satisfiable by a flag
    that is simply always off."""
    current = b"\xff" * 256
    desired = ct.generate_pattern(0x1000, 256)
    step = ct.Step(
        op=ct.OP_WRITE,
        supported=True,
        reason="",
        destructive=True,
        write_region=(0x1000, 256),
        region_policy=ct.REGION_POLICY_FIXED,
        full_device_permitted=False,
    )
    target = ct.WriteTarget(
        region=(0x1000, 256),
        pattern=ct.mask_write_pattern(current, desired),
        masked=True,
        bits_cleared=ct.bits_cleared_by(current, desired),
        bits_retained=ct.bits_retained_by(current, desired),
        current_source="probe read",
        current=current,
        current_is_probe_read=True,
        region_policy=ct.REGION_POLICY_FIXED,
    )
    write_context = ct.WriteContext()
    write_context.cycle_targets = [target]
    write_context.cycle_index = 0
    ed = ct.resolve_chip("m27c512", db=_REAL_DB)
    chip = WriteInitPreflightChip(65536, uv=True)
    ct._dispatch_multi_run(
        ct.OP_WRITE,
        "m27c512",
        ed,
        chip,
        runs=1,
        step=step,
        write_context=write_context,
    )
    assert chip.write_flags_seen == [8]
    assert sum(1 for call in chip.calls if call[0] == "read_eprom") == 0


def test_the_witness_is_false_for_absent_empty_and_unmasked_targets() -> None:
    """The probe-surfaced `UV-03 / empty` edge, committed:
    `_is_monotonic_masked_target` is `False` for `None`, for a `masked=False`
    target, and for a `masked=True` target whose `current` is empty -- the
    fail-closed cases -- and `True` only when all three conjuncts hold, so
    this leg is not vacuously satisfied by a predicate that returns `False`
    for everything."""
    current = b"\xff" * 256
    desired = ct.generate_pattern(0x1000, 256)
    unmasked = ct.WriteTarget(
        region=(0x1000, 256),
        pattern=desired,
        masked=False,
        bits_cleared=0,
        bits_retained=0,
        current_source="address-derived pattern (unmasked)",
    )
    empty_current = ct.WriteTarget(
        region=(0x1000, 256),
        pattern=ct.mask_write_pattern(current, desired),
        masked=True,
        bits_cleared=ct.bits_cleared_by(current, desired),
        bits_retained=ct.bits_retained_by(current, desired),
        current_source="probe read",
        current=b"",
        current_is_probe_read=True,
    )
    full_witness = ct.WriteTarget(
        region=(0x1000, 256),
        pattern=ct.mask_write_pattern(current, desired),
        masked=True,
        bits_cleared=ct.bits_cleared_by(current, desired),
        bits_retained=ct.bits_retained_by(current, desired),
        current_source="probe read",
        current=current,
        current_is_probe_read=True,
    )
    assert ct._is_monotonic_masked_target(None) is False
    assert ct._is_monotonic_masked_target(unmasked) is False
    assert ct._is_monotonic_masked_target(empty_current) is False
    assert ct._is_monotonic_masked_target(full_witness) is True


def test_the_prescribed_probe_read_string_equality_would_never_match() -> None:
    """`.planning/research/SUMMARY.md:89` prescribes the witness as
    `target.current_source == "probe read"`. MEASURED, every target
    `_plan_cycle_targets`/`_uv_cycle_targets` stages for a probed UV part
    over a two-cycle run carries `current_source` reading
    `"probe read (tranche N/2)"` (`chip_test.py:1518`) -- the equality
    NEVER matches, while the prefix always holds. A witness written as that
    equality would ship green and do nothing; this leg turns a future
    "simplification" back to it into a RED instead of a silent no-op."""
    current = b"\xff" * 256
    desired = ct.generate_pattern(0xFF00, 256)
    probe = ct.WriteTarget(
        region=(0xFF00, 256),
        pattern=ct.mask_write_pattern(current, desired),
        masked=True,
        bits_cleared=ct.bits_cleared_by(current, desired),
        bits_retained=ct.bits_retained_by(current, desired),
        current_source="probe read",
        current=current,
        current_is_probe_read=True,
        region_policy=ct.REGION_POLICY_UV_SLOT,
    )
    staged = ct._uv_cycle_targets(probe, 2)
    assert len(staged) == 2
    for target in staged:
        assert target.current_source != "probe read"
        assert target.current_source.startswith("probe read ")


def test_a_non_uv_plan_never_sets_the_skip_blank_check_flag() -> None:
    """Without this leg an always-on flag would satisfy legs 3, 4 and 8
    equally well. A full-scope two-cycle run on `at28c256` (non-UV) against
    a `WriteInitPreflightChip` constructed with `uv=False` records only `0`
    in `write_flags_seen`."""
    full = _REAL_DB.get_eprom("at28c256") or {}
    mem_size = int(full.get("memory-size", 0) or 0)
    chip = WriteInitPreflightChip(mem_size, uv=False)
    plan = ct.derive_plan("at28c256", _REAL_DB, write_scope="full")
    ct.run_plan(plan, chip, _REAL_DB, runs=2)
    assert set(chip.write_flags_seen) == {0}


def test_a_non_uv_blank_check_failure_is_still_bad() -> None:
    """The adjudication must fire ONLY on a UV plan's pre-write blank-check
    (`Step.uv_prewrite` at its `False` default here) -- a non-UV part that
    is not blank after an erase is still a real tool-health failure. Uses
    `Mock(spec=_OPERATOR_METHODS)` rather than a chip-modelling double: no
    chip state is needed, and `spec=` keeps a typo'd operator method an
    `AttributeError` rather than a silently-truthy `Mock`."""
    operator = Mock(spec=_OPERATOR_METHODS)
    operator.check_eprom_blank.return_value = False
    step = ct.Step(op=ct.OP_BLANK_CHECK, supported=True, reason="")
    ed = ct.resolve_chip("m27c512", db=_REAL_DB)
    result = ct._dispatch_step("m27c512", step, ed, operator, runs=1)
    assert result.verdict == ct.VERDICT_BAD
