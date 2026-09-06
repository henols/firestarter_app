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
    module-private in a large sibling test module).

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

from firestarter import chip_test as ct
from firestarter import submit as sub
from firestarter.constants import FLAG_SKIP_BLANK_CHECK
from firestarter.database import EpromDatabase
from firestarter.diagnostic_report import AutoCapture, DiagnosticReport, TransportHealth

from .fake_chip import WriteInitPreflightChip

_REAL_DB = EpromDatabase(skip_local_override=True)


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
    theatre: a double that refuses nothing makes any PASS meaningless."""
    chip, _full = _seeded_m27c512_double()
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
