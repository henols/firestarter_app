"""The repeat CYCLE's per-family payload recipes (D-2).

Separate file, mirroring how `test_chip_test_sdp_leg.py` carries the SDP leg:
this is one coherent mechanism -- how successive cycles differ so that each
cycle's write has real work to do -- and it is easier to reason about away
from `test_chip_test.py`'s 2000-line plan-derivation suite.

The claim these tests exist to protect: **a verify only proves the write
worked if the write had to change something.** Each family reaches that
differently, and getting the wrong recipe onto a family is silent -- the run
still reports OK.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from firestarter import chip_test as ct
from firestarter.chip_resolver import resolve_chip
from firestarter.database import EpromDatabase

_REAL_DB = EpromDatabase()

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

# Family representatives, one per recipe. Chosen from the shipped database and
# asserted to still carry the recipe under test (see
# `test_each_family_gets_its_own_recipe`), so a database change that moves a
# chip between families fails loudly here instead of quietly weakening the
# family's coverage below.
_ERASABLE = "M8720"  # 0x08 EEPROM -- erase inside the cycle resets the state
_SRAM = "DS1220(RW)"  # freely rewritable in both bit directions
_UV = "M27C512"  # monotonic; cannot be erased at all
_UV_LARGE = "AM27C020"


def _popcount(data: bytes) -> int:
    return sum(byte.bit_count() for byte in data)


def _cycle_operator(name: str, *, blank: bool = False):
    """A chip double that answers a REGION read correctly.

    `_read_region` slices the read-back file at the ABSOLUTE offset, because a
    real region read produces a hole-padded file (`_write_to_file` seeks to the
    address first). A double that writes at offset 0 makes every UV slot probe
    come back short, which silently turns a masked write into a SKIPPED step --
    so this double seeks, and the tests below would not pass without it.
    """
    eprom_data = resolve_chip(name, db=_REAL_DB)
    writes: list[tuple[str | None, bytes]] = []
    operator = Mock(spec=_OPERATOR_METHODS)
    operator.check_eprom_id.return_value = (True, eprom_data.get("chip-id") or 0)
    operator.check_eprom_blank.return_value = blank
    # 202-01 D-10: verify_eprom now returns an int (0 == match), unlike the
    # other three, which stay bare bools.
    operator.verify_eprom.return_value = 0
    for method in ("erase_eprom", "sdp_lock", "sdp_unlock"):
        getattr(operator, method).return_value = True

    def _read(_name, data, output_file=None, address_str=None, size_str=None, **_kw):
        start = int(address_str, 0) if address_str else 0
        size = int(size_str, 0) if size_str else int(data.get("memory-size", 4096))
        with open(output_file, "wb") as handle:
            handle.seek(start)
            handle.write(b"\xff" * size)
        return True

    def _write(_name, _data, path, operation_flags=0, address_str=None, **_kw):
        with open(path, "rb") as handle:
            writes.append((address_str, handle.read()))
        return True

    operator.read_eprom.side_effect = _read
    operator.write_eprom.side_effect = _write
    return operator, writes


def _run(name: str, *, runs: int = 2):
    operator, writes = _cycle_operator(name)
    plan = ct.derive_plan(name, _REAL_DB, write_scope="full")
    # `allow_single_run` mirrors what `dev test --fast` passes: `runs=1` alone
    # still fails the whole plan fail-closed, deliberately.
    results = ct.run_plan(
        plan, operator, _REAL_DB, runs=runs, allow_single_run=runs < 2
    )
    write_result = next(
        r for r in results if r.op in (ct.OP_WRITE, ct.OP_WRITE_PARTIAL)
    )
    return writes, write_result


@pytest.mark.parametrize(
    "chip,expected",
    [
        (_ERASABLE, ct.CYCLE_PAYLOAD_SAME),
        ("AT28C256", ct.CYCLE_PAYLOAD_SAME),
        ("W29C040", ct.CYCLE_PAYLOAD_SAME),
        (_SRAM, ct.CYCLE_PAYLOAD_ALTERNATE),
        (_UV, ct.CYCLE_PAYLOAD_UV_TRANCHE),
        (_UV_LARGE, ct.CYCLE_PAYLOAD_UV_TRANCHE),
    ],
)
def test_each_family_gets_its_own_recipe(chip: str, expected: str) -> None:
    """`derive_plan` decides the recipe ONCE, from facts it already holds, and
    carries it on both the write and the verify step."""
    plan = ct.derive_plan(chip, _REAL_DB, write_scope="full")
    write_step = next(
        s for s in plan.steps if s.op in (ct.OP_WRITE, ct.OP_WRITE_PARTIAL)
    )
    verify_step = next(s for s in plan.steps if s.op == ct.OP_VERIFY)
    assert write_step.cycle_payload == expected
    assert verify_step.cycle_payload == expected


def test_every_other_step_keeps_the_default_recipe() -> None:
    """Only write/verify carry a recipe. Everything else keeps `same`, which is
    also the pre-cycle behaviour, so no step outside the pair can be given a
    payload it has no use for."""
    plan = ct.derive_plan(_UV, _REAL_DB, write_scope="full")
    for step in plan.steps:
        if step.op in (ct.OP_WRITE, ct.OP_WRITE_PARTIAL, ct.OP_VERIFY):
            continue
        assert step.cycle_payload == ct.CYCLE_PAYLOAD_SAME, step.op


def test_tranches_cost_no_extra_bits() -> None:
    """THE property that makes staging affordable, and the one the design note
    got wrong before it was measured: the last image equals `current & desired`
    exactly, so N cycles consume the SAME total bits as today's single masked
    write. A UV part is a finite regression rig -- staging must not shorten its
    life."""
    current = b"\xff" * 256
    desired = ct.generate_pattern(0, 256)
    images = ct.uv_tranche_images(current, desired, 2)

    assert len(images) == 2
    assert images[-1] == ct.mask_write_pattern(current, desired)
    assert _popcount(images[-1]) == ct.bits_retained_by(current, desired)


def test_tranches_are_monotonic_and_strictly_progressive() -> None:
    """Each image clears bits and never sets one -- a UV cell cannot go 0->1,
    so an image that asked for it would be an unsatisfiable write that could
    only ever fail."""
    current = b"\xff" * 256
    desired = ct.generate_pattern(0, 256)
    images = ct.uv_tranche_images(current, desired, 4)

    previous = current
    for image in images:
        # Monotonic: every set bit in `image` was already set in `previous`.
        assert all(now & ~before & 0xFF == 0 for before, now in zip(previous, image))
        # Progressive: this cycle actually cleared something.
        assert _popcount(image) < _popcount(previous)
        previous = image


def test_tranche_bit_counts_match_the_images() -> None:
    """`uv_tranche_bit_counts` is what becomes each cycle's
    `WriteTarget.bits_cleared`, so it has to agree with the images bit for bit
    -- otherwise the vacuous-pass floor would be checking a number no cycle
    actually cleared."""
    current = b"\xff" * 256
    desired = ct.generate_pattern(0, 256)
    cycles = 3
    images = ct.uv_tranche_images(current, desired, cycles)
    counts = ct.uv_tranche_bit_counts(ct.bits_cleared_by(current, desired), cycles)

    previous = current
    for image, expected in zip(images, counts):
        assert _popcount(previous) - _popcount(image) == expected
        previous = image
    assert sum(counts) == ct.bits_cleared_by(current, desired)


def test_tranches_spread_across_the_whole_region() -> None:
    """Interleaved, not blocked: every tranche touches bytes across the whole
    region rather than one corner of it, so each cycle's programming exercises
    the same address lines the others do."""
    current = b"\xff" * 256
    desired = ct.generate_pattern(0, 256)
    images = ct.uv_tranche_images(current, desired, 2)

    first_tranche_bytes = {
        index
        for index, (before, after) in enumerate(zip(current, images[0]))
        if before != after
    }
    assert min(first_tranche_bytes) < 16, "first tranche never touched the low end"
    assert max(first_tranche_bytes) > 240, "first tranche never touched the high end"


def test_tranche_floor_scales_with_the_cycle_count() -> None:
    """A slot with 64..127 clearable bits passes the single-write floor and
    CANNOT support a two-cycle test. Refusing it here is what makes the slot
    the resolver hands back tranche-feasible by construction."""
    # Exactly 96 clearable bits: 12 bytes of 0xFF against 0x00, rest identical.
    current = b"\xff" * 12 + b"\x00" * 244
    desired = b"\x00" * 256
    assert ct.bits_cleared_by(current, desired) == 96

    assert ct.uv_tranche_images(current, desired, 1) != []
    assert ct.uv_tranche_images(current, desired, 2) == []


def test_tranche_images_reject_a_length_disagreement() -> None:
    assert ct.uv_tranche_images(b"\xff" * 8, b"\x00" * 4, 2) == []
    assert ct.uv_tranche_images(b"\xff" * 8, b"\x00" * 8, 0) == []


# End to end, one test per recipe


def test_uv_cycles_write_different_images_to_the_SAME_slot() -> None:
    """The defect this whole task exists to fix, on the 301 UV rows.

    Same slot -- so the two cycles still isolate the write path from a cell
    defect, which is the entire point of comparing them. Different bytes -- so
    the second cycle's write has real bits to clear instead of being elided by
    the firmware as already-correct.
    """
    writes, write_result = _run(_UV)

    assert write_result.verdict == ct.VERDICT_OK
    assert write_result.run_count == 2
    assert len(writes) == 2

    addresses = {address for address, _payload in writes}
    assert len(addresses) == 1, f"cycles landed on different slots: {addresses}"

    payloads = [payload for _address, payload in writes]
    assert payloads[0] != payloads[1], "both cycles wrote identical bytes"
    # Strictly progressive: cycle 2 clears bits cycle 1 left set.
    assert _popcount(payloads[1]) < _popcount(payloads[0])


def test_uv_final_state_matches_the_single_write_it_replaces() -> None:
    """Staging changed WHEN bits are cleared, not HOW MANY. The device ends
    exactly where one unstaged masked write would have left it."""
    writes, _write_result = _run(_UV)
    final_payload = writes[-1][1]

    start, length = writes[-1][0], len(final_payload)
    assert start is not None
    current = b"\xff" * length
    desired = ct.generate_pattern(int(start, 0), length)
    assert final_payload == ct.mask_write_pattern(current, desired)


def test_sram_cycles_alternate_between_pattern_and_complement() -> None:
    """Freely rewritable in both directions, so a differing payload is free --
    and the complement exercises every data line the other way."""
    writes, write_result = _run(_SRAM)

    assert write_result.verdict == ct.VERDICT_OK
    payloads = [payload for _address, payload in writes]
    assert len(payloads) == 2
    assert payloads[0] == bytes(0xFF ^ byte for byte in payloads[1])


def test_erasable_cycles_write_identical_bytes_and_that_is_correct() -> None:
    """`same` is not a missing feature here. The erase step inside the cycle
    resets the device, so cycle 2's write faces a blank part and has full real
    work to do with byte-identical input -- which is also what keeps the two
    cycles directly comparable."""
    writes, write_result = _run(_ERASABLE)

    assert write_result.verdict == ct.VERDICT_OK
    payloads = [payload for _address, payload in writes]
    assert len(payloads) == 2
    assert payloads[0] == payloads[1]


def test_single_run_uv_still_writes_one_whole_masked_image() -> None:
    """`--fast` (one cycle) must not accidentally write a partial tranche and
    leave the slot half-staged: with `cycles=1` the single image IS
    `current & desired`."""
    writes, write_result = _run(_UV, runs=1)

    assert write_result.run_count == 1
    assert len(writes) == 1
    address, payload = writes[0]
    assert address is not None
    current = b"\xff" * len(payload)
    desired = ct.generate_pattern(int(address, 0), len(payload))
    assert payload == ct.mask_write_pattern(current, desired)


def test_uv_write_target_reports_the_per_cycle_tranche_not_the_slot_total() -> None:
    """`bits_cleared` has to be the PER-CYCLE number: it is what
    `WriteTarget`'s vacuous-pass floor checks, so a slot total would let a
    cycle that cleared almost nothing through."""
    _writes, write_result = _run(_UV)
    target = write_result.write_target
    assert target is not None

    slot_total = ct.bits_cleared_by(
        b"\xff" * target.region[1], ct.generate_pattern(*target.region)
    )
    assert target.bits_cleared == slot_total // 2
    assert "tranche 2/2" in target.current_source


def test_uv_write_is_always_one_slot_never_the_whole_device() -> None:
    """D-4. A UV part receives one 256-byte slot at both scopes, blank or not.
    `uv_slot_starts` is top-down, so that slot is the HIGHEST address on the
    device and every address line is exercised from run 1 -- which is the
    coverage the retired full-device branch was thought to provide."""
    _writes, write_result = _run(_UV)
    target = write_result.write_target
    assert target is not None

    memory_size = int(resolve_chip(_UV, db=_REAL_DB)["memory-size"])
    start, length = target.region
    assert length == 256, "a UV write widened beyond one slot"
    assert start == memory_size - length, "the UV slot was not the top one"


def test_uv_target_carries_rig_life_all_the_way_to_the_report() -> None:
    """D-9, and the bug that shipped it: the counts were originally dropped on
    the staged tranche copies, which are the ONLY targets that reach the
    report -- so the rig-life line was invisible on exactly the family it
    exists for. Caught by running the command, not by the suite, hence this
    test."""
    _writes, write_result = _run(_UV)
    target = write_result.write_target
    assert target is not None

    memory_size = int(resolve_chip(_UV, db=_REAL_DB)["memory-size"])
    assert target.slots_total == memory_size // 256
    assert target.slots_remaining == target.slots_total, "a virgin part is untouched"


def test_rig_life_is_rendered_in_the_write_coverage_line() -> None:
    """One run consumes one slot, so slots-left IS runs-left. An operator
    planning a firmware regression pass needs that number without deriving it
    from a slot address."""
    from firestarter.diagnostic_report import _write_coverage_line

    plan = ct.derive_plan(_UV, _REAL_DB, write_scope="full")
    write_step = next(
        s for s in plan.steps if s.op in (ct.OP_WRITE, ct.OP_WRITE_PARTIAL)
    )
    _writes, write_result = _run(_UV)

    line = _write_coverage_line(write_result, write_step)
    assert line is not None
    assert "slot 0x" in line
    assert "bits cleared this cycle" in line
    assert "slots left on this part" in line


def test_slots_remaining_line_reports_after_this_run_when_the_write_ran() -> None:
    """D-20: the operator-facing number is slots left AFTER this run. The
    resolver's own `slots_remaining` is the resolve-time (before-this-run)
    count -- `_write_coverage_line` subtracts one when the write actually
    ran (an OK/BAD/marginal verdict), because the run this line is about
    just spent the top slot the resolver picked."""
    from firestarter.diagnostic_report import _write_coverage_line

    target = ct.WriteTarget(
        region=(0xFF00, 256),
        pattern=b"\xaa" * 256,
        masked=True,
        bits_cleared=512,
        bits_retained=1536,
        current_source="probe read",
        slots_remaining=256,
        slots_total=256,
        region_policy=ct.REGION_POLICY_UV_SLOT,
    )
    result = ct.StepResult(
        op=ct.OP_WRITE, verdict=ct.VERDICT_OK, run_count=1, write_target=target
    )
    step = ct.Step(op=ct.OP_WRITE, supported=True, reason="")

    line = _write_coverage_line(result, step)
    assert line is not None
    assert "255 of 256 slots left on this part" in line


def test_slots_remaining_line_reports_the_resolved_count_when_the_write_was_refused() -> (
    None
):
    """D-21's flip side of the same predicate: a refused write (SKIPPED,
    the write step was applicable and did not run) has not spent the slot
    the resolver counted, so the reported number stays the resolved count
    unreduced."""
    from firestarter.diagnostic_report import _write_coverage_line

    target = ct.WriteTarget(
        region=(0xFF00, 256),
        pattern=b"\xaa" * 256,
        masked=True,
        bits_cleared=512,
        bits_retained=1536,
        current_source="probe read",
        slots_remaining=256,
        slots_total=256,
        region_policy=ct.REGION_POLICY_UV_SLOT,
    )
    result = ct.StepResult(
        op=ct.OP_WRITE,
        verdict=ct.VERDICT_SKIPPED,
        run_count=0,
        write_target=target,
    )
    step = ct.Step(op=ct.OP_WRITE, supported=True, reason="")

    line = _write_coverage_line(result, step)
    assert line is not None
    assert "256 of 256 slots left on this part" in line


def test_non_uv_target_reports_no_rig_life() -> None:
    """The counts are UV-only: an erasable part has no finite slot budget, so
    a number there would be meaningless rather than merely absent."""
    _writes, write_result = _run(_ERASABLE)
    target = write_result.write_target
    assert target is not None
    assert target.slots_remaining is None
    assert target.slots_total is None


def test_help_describes_the_repeat_as_a_cycle_and_a_rig_check() -> None:
    """D-10: firmware is deterministic and cannot disagree with itself, so the
    user-facing wording must not sell the repeat as extra firmware coverage."""
    import firestarter.cli_handlers as cli_handlers_mod

    doc = (cli_handlers_mod.dev_test.__doc__ or "").lower()
    assert "cycle" in doc
    assert "rig-health" in doc or "rig health" in doc


def test_a_failing_first_cycle_keeps_the_fingerprint_read_back() -> None:
    """Evidence-gated fingerprint read-back across cycles (PRUNE-02).

    Cycle 1 fails, cycle 2 passes -- the gate still consults `per_step[i]`
    across ALL prior cycles, not just the final cycle's own one-element
    `outcomes` list, so the read-back is kept. Built on `_cycle_operator`,
    not `test_chip_test.py`'s plain `_mock_operator`: its `read_eprom` side
    effect seeks to the ABSOLUTE address before writing, which
    `_read_region` depends on -- a double that writes at offset 0 makes
    every region slice come back short and this test would pass for the
    wrong reason. `derive_plan`'s own unconditional `read` step (present on
    every protocol, a separate read-repeatability diagnostic) contributes a
    fixed baseline of `runs` calls regardless of the fingerprint gate; the
    gate's own contribution is measured as the DELTA above that baseline."""
    operator, _writes = _cycle_operator(_ERASABLE)
    operator.write_eprom.side_effect = [False, True]
    plan = ct.derive_plan(_ERASABLE, _REAL_DB, write_scope="full")
    runs = 2

    ct.run_plan(plan, operator, _REAL_DB, runs=runs)

    assert operator.read_eprom.call_count > runs


def test_an_all_passing_two_cycle_run_performs_zero_fingerprint_read_backs() -> None:
    """The adjacency edge's neighbour: no failure injected anywhere in the
    two-cycle run, so the fingerprint is synthesized with zero device
    reads and `read_eprom.call_count` stays at exactly the `read` step's
    own fixed baseline of `runs` calls -- zero ADDED by the gate."""
    operator, _writes = _cycle_operator(_ERASABLE)
    plan = ct.derive_plan(_ERASABLE, _REAL_DB, write_scope="full")
    runs = 2

    ct.run_plan(plan, operator, _REAL_DB, runs=runs)

    assert operator.read_eprom.call_count == runs


def _probe_shaped_target(*, current_is_probe_read: bool) -> ct.WriteTarget:
    start, length = 0xFF00, 256
    current = b"\xff" * length
    desired = ct.generate_pattern(start, length)
    return ct.WriteTarget(
        region=(start, length),
        pattern=ct.mask_write_pattern(current, desired),
        masked=True,
        bits_cleared=ct.bits_cleared_by(current, desired),
        bits_retained=ct.bits_retained_by(current, desired),
        current_source="probe read",
        current=current,
        current_is_probe_read=current_is_probe_read,
        region_policy=ct.REGION_POLICY_UV_SLOT,
    )


def test_uv_cycle_tranches_carry_the_probe_read_witness() -> None:
    """The staged tranches are the ONLY targets that ever reach
    `write_eprom` or the report (Phase 179, UV-03) -- a witness not carried
    through here is invisible on exactly the family it exists for."""
    target = _probe_shaped_target(current_is_probe_read=True)
    staged = ct._uv_cycle_targets(target, 2)
    assert len(staged) == 2
    assert all(t.current_is_probe_read is True for t in staged)


def test_alternating_cycle_complement_carries_no_probe_read_witness() -> None:
    """Anti-vacuity sibling to the tranche-carry test above: sourcing the
    tranche target with `current_is_probe_read=False` must yield staged
    copies that are ALSO `False` -- without this leg, the prior test would
    pass just as well against a hard-coded `current_is_probe_read=True` in
    `_uv_cycle_targets`."""
    target = _probe_shaped_target(current_is_probe_read=False)
    staged = ct._uv_cycle_targets(target, 2)
    assert len(staged) == 2
    assert all(t.current_is_probe_read is False for t in staged)


def test_sram_complement_carries_no_probe_read_witness() -> None:
    """`_alternating_cycle_targets`' complement builds an unmasked,
    address-derived image for SRAM/FRAM -- never a probe-read image -- so
    it must carry the default `False`, not the source target's witness."""
    unmasked = ct.WriteTarget(
        region=(0x1000, 256),
        pattern=ct.generate_pattern(0x1000, 256),
        masked=False,
        bits_cleared=0,
        bits_retained=0,
        current_source="address-derived pattern (unmasked)",
    )
    complement = ct._alternating_cycle_targets(unmasked, 2)
    assert complement[1].current_is_probe_read is False


@pytest.mark.parametrize("chip", ["M27C512", "AM27C020", "TMS27C512"])
def test_uv_plan_blank_check_sits_outside_the_cycle_block(chip: str) -> None:
    """The structural fact that makes a non-`None` `error_code` on a
    `SKIPPED` blank-check step safe (Phase 179, UV-02/Q7): `_dispatch_step`
    now returns a non-`None` `error_code` on a step whose verdict is
    `SKIPPED`, and that is only safe while the step sits OUTSIDE the block
    where `_run_cycle_block` breaks on `result.error_code is not None`
    (`chip_test.py:1648-1652`). A future phase that moves the UV blank-
    check inside the block would silently reintroduce the cycle-2 abort
    this phase exists to remove; this test would go red instead."""
    plan = ct.derive_plan(chip, _REAL_DB, write_scope="full")
    ops = [s.op for s in plan.steps]
    blank_check_index = ops.index(ct.OP_BLANK_CHECK)
    bounds = ct.cycle_block_bounds(plan.steps)
    assert bounds is not None
    block_start, _block_stop = bounds
    assert blank_check_index < block_start
