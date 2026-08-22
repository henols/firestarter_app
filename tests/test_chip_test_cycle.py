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
    for method in ("verify_eprom", "erase_eprom", "sdp_lock", "sdp_unlock"):
        getattr(operator, method).return_value = True

    def _read(_name, data, output_file=None, address_str=None, size_str=None, **_kw):
        start = int(address_str, 0) if address_str else 0
        size = int(size_str, 0) if size_str else int(data.get("memory-size", 4096))
        with open(output_file, "wb") as handle:
            handle.seek(start)
            handle.write(b"\xff" * size)
        return True

    def _write(_name, _data, path, address_str=None, **_kw):
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


# ---------------------------------------------------------------------------
# The recipe assignment (D-2)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# The UV tranche arithmetic (D-2/D-6) -- pure, no chip
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# End to end, one test per recipe
# ---------------------------------------------------------------------------


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
