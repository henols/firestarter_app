"""HOST-02 host-side write-progress tests (Phase 143 Plan 06, `143-06-PLAN.md` Task 1).

**Real chip used throughout this module:** ``w27c512`` (``W27C512,W27E512`` in
``firestarter/data/chip_database.json``, algorithm 7 / protocol ``0x07``
EPROM_STD, ``memory-size`` 65536 -- verified this session via
``resolve_chip("w27c512", db=...)``). The same real part
``tests/test_write_response_budget.py`` (Phase 143 Plan 04) already uses for
the write path; reusing it introduces no second identity for the same chip.

**The four traps this module pins (`143-06-PLAN.md` objective):**

1. **The MAIN-phase loop raises on a DATA frame today.**
   ``_main_phase_send_data``'s response loop handles only ``MAIN``, ``ERROR``
   and ``OK``, then raises
   ``EpromOperationError("Programmer did not request data chunk, got {type}: "
   "{message}")`` on anything else -- a mid-block ``MSG_DATA_PROGRESS``
   (``DATA``-type) frame hits that raise. This is exactly what makes plan
   143-05's firmware emission BREAK the write today, and exactly what Task
   2's new DATA branch must fix.
2. **Acking the frame desyncs the stream (D-05).** The firmware is mid-block,
   waiting for nothing; on a Leonardo a stray buffered ``OK`` makes
   ``op_get_message`` return ``OP_MSG_ACK``, and ``_process_incoming_data``'s
   ``default: return false`` aborts the write with **no error frame at all**
   -- the ``#write-empty-input-regression`` trap in a new place.
3. **``set_progress`` tears the bar down on a differing total (Pitfall 2 /
   D-04).** The write bar starts at ``file_size``; ``0xE0`` carries the
   chip's ``mem_size``. ``set_progress(current, total)`` calls
   ``start(total)`` whenever they differ, and ``start()`` closes and
   re-creates the ``tqdm`` bar and zeroes ``current_step`` -- so every frame
   would rebuild the bar unless the write branch bypasses ``set_progress``
   entirely.
4. **Two progress sources fight over one bar (Pitfall 1).** The existing
   chunk-handoff ``progress.update(len(data_chunk))`` measures bytes *sent*;
   ``0xE0`` reports bytes *programmed*. Without a latch that stops the
   handoff once a frame has been seen, the bar jumps ahead at each chunk
   boundary and then visibly rewinds as the firmware's own frames catch up.

**D-25 / genuine-RED discipline:** every test below must fail **for the
right reason** on the pre-Task-2 code -- not on a collection error, and
(per this plan's own acceptance criteria) not *vacuously* on a value that
would be identical whether or not the DATA branch exists. Because the
current loop raises unconditionally on the very *first* DATA frame it
sees, an assertion built only from values recorded *before* that frame
(e.g. "the first recorded position is 0" -- true only because
``ClassProgressHandler.start()`` always records ``(0, total)`` first, with
or without any frame ever arriving; or "start() was called exactly once"
-- true only because the abort happens before any second call could ever
occur) would pass on today's broken code, for the wrong reason. Tests 3, 4
and 5 therefore lead with (or fold in) an explicit ``ok is True`` check for
exactly this reason -- mirroring Phase 143 Plan 04's own documented fix for
the identical trap (see that plan's SUMMARY, "D-25 Evidence" section, on
its Test 4 and Test 5).

**Driver shape:** ``_drive_write_with_progress_frames`` drives a full,
otherwise-successful ``write_eprom()`` against ``w27c512`` through a fake
serial port, feeding a caller-supplied list of ``MSG_DATA_PROGRESS`` frames
**strictly between** ``MSG_OK_REQ_DATA`` and ``MSG_MAIN_DONE`` -- i.e.
inside the MAIN window, one batch of frames per chunk boundary.

WHY the frames must land there, not in INIT/END:
``tests/test_eprom_operations.py``'s own ``_drive_write_eprom_for_ack_check``
docstring states plainly that ``_main_phase_send_data``'s loop raises
``EpromOperationError`` on any non-``MAIN``/``ERROR``/``OK`` frame inside the
MAIN window today -- which is exactly the RED this module needs before Task
2 lands, and exactly why today's existing suites are careful to feed their
own WARN frames inside INIT instead.

WHY a ``progress_callback`` rather than a real ``tqdm`` bar:
``ClassProgressHandler.start()`` only builds a real ``tqdm.tqdm`` object in
its ``else`` branch, taken when no callback is set -- passing one keeps
``pbar`` at ``None`` for the whole drive, so bar state is observable through
a plain Python list, with no terminal and no dependency on ``tqdm``'s own
internals.
"""

from __future__ import annotations

import struct
from unittest.mock import patch

from firestarter.chip_resolver import resolve_chip
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_operations import ClassProgressHandler, EpromOperator
from firestarter.messages import (
    MSG_DATA_PROGRESS,
    MSG_END_DONE,
    MSG_INIT_DONE,
    MSG_MAIN_DONE,
    MSG_OK_REQ_DATA,
)
from firestarter.serial_comm import SerialCommunicator

from .conftest import _FakeSerial, build_frame

# Real 27C part used throughout this module -- see the module docstring.
_REAL_27C_CHIP = "w27c512"
# w27c512's memory-size (firestarter/data/chip_database.json), verified this
# session via resolve_chip("w27c512", db=...). Used as a realistic `total`
# in fed progress frames -- the host discards it (D-04), but a real firmware
# would report exactly this value for this chip.
_CHIP_MEM_SIZE = 65536
# _calculate_buffer_size() returns this Uno-floor default whenever
# firmware_max_chunk is None -- which make_comm() (and this module's own
# _fresh_serial_and_comm()) always set. Every driver call in this module
# relies on this to compute how many chunks a given file_size needs.
_BUFFER_SIZE = 512


def _progress_frame(current: int, total: int) -> bytes:
    """Build a MSG_DATA_PROGRESS (0xE0) wire frame for (current, total).

    messages.py's catalog entry declares
    ``params=(("u32", "dec"), ("u32", "dec")), param_bytes=8`` -- two
    big-endian u32s -- and renders via the generic catalog-format path
    (codec.py) as ``"%lu/%lu" % (current, total)``, i.e.
    ``Response.message == f"{current}/{total}"``.
    """
    return build_frame(MSG_DATA_PROGRESS, struct.pack(">II", current, total))


def _fresh_serial_and_comm():
    """Build an independent (fake_serial, make_comm) pair.

    Needed whenever a single test drives write_eprom() more than once (Test
    2's zero-frames contrast run; Test 5's negative sibling below drives
    once but Test 2 needs two independent drives in the SAME test): a
    successful drive closes its fake serial port (``_FakeSerial.close()``
    sets ``is_open = False``), so a second drive cannot reuse the
    fixture-injected ``fake_serial``. Mirrors
    ``tests/test_write_response_budget.py``'s own ``_fresh_serial_and_comm``
    (Phase 143 Plan 04) and ``tests/test_write_skip_sdp_unlock.py``'s --
    same reasoning, same shape; this repo's established precedent is local
    duplication over a shared fixture for this specific need.
    """
    serial = _FakeSerial()

    def _factory():
        instance = SerialCommunicator.__new__(SerialCommunicator)
        instance.connection = serial
        instance.port_name = "/dev/null"
        instance.baud_rate = 250000
        instance.timeout = 0.1
        instance.programmer_info = None
        instance._fault_inject_outgoing = None
        instance.firmware_buffer_size = None
        instance.firmware_max_chunk = None
        instance.firmware_identity = None
        instance.hw_revision = None
        instance.write_block_budget_s = None
        instance.seen_message_ids = set()
        return instance

    return serial, _factory


def _drive_write_with_progress_frames(
    tmp_path,
    make_comm,
    fake_serial,
    *,
    file_size: int,
    chunk_progress_frames: list[list[tuple[int, int]]],
    address_str: str | None = None,
):
    """Drive a full, hardware-free write_eprom() against w27c512, feeding
    ``chunk_progress_frames[i]`` (a list of ``(current, total)`` pairs) as
    MSG_DATA_PROGRESS frames right after the i-th MSG_OK_REQ_DATA -- i.e.
    mid-block, inside the MAIN window (see module docstring for why there).

    ``len(chunk_progress_frames)`` MUST equal the number of chunks
    ``file_size`` needs against the Uno-floor 512-byte buffer -- asserted
    below as a check on this test module's OWN authoring, not a behaviour
    under test. After the last chunk's frames, MSG_MAIN_DONE is fed
    directly: the fake serial buffer is pre-loaded, so (mirroring
    ``_drive_write_eprom_for_ack_check`` and
    ``test_write_response_budget.py``'s own drivers) the firmware's real
    request-a-chunk-or-signal-done choice does not need to be modelled --
    only the response TYPES ``_main_phase_send_data`` branches on matter
    here.

    Returns ``(ok, positions, start_calls, ack_count)``:
      ok          -- write_eprom()'s return value.
      positions   -- every ``(current, total)`` the progress_callback
                     received, in call order (from
                     ``ClassProgressHandler.start()``, ``.update()``, and --
                     once Task 2 lands -- the new ``_apply_write_progress()``).
      start_calls -- every value ``ClassProgressHandler.start()`` was
                     invoked with, in call order -- recorded independently
                     of ``positions`` (via a delegating ``patch.object``
                     wrapper, never a stub) so a test can assert an
                     INVOCATION COUNT without conflating it with position
                     values.
      ack_count   -- how many times ``SerialCommunicator.send_ack()`` was
                     called during the drive (also a delegating wrapper --
                     a stub would break MAIN-phase flow control, since
                     ``_run_state_machine`` and ``_execute_phase`` both call
                     ``send_ack()`` for their own phase-transition
                     bookkeeping).
    """
    expected_chunks = -(-file_size // _BUFFER_SIZE)  # ceiling division
    assert len(chunk_progress_frames) == expected_chunks, (
        f"test authoring error: file_size={file_size} needs "
        f"{expected_chunks} chunk(s) against the {_BUFFER_SIZE}-byte Uno "
        f"floor, but chunk_progress_frames has {len(chunk_progress_frames)} entries"
    )

    input_file = tmp_path / f"progress_{file_size}_{id(fake_serial)}.bin"
    input_file.write_bytes(bytes(i % 256 for i in range(file_size)))

    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    for frames in chunk_progress_frames:
        fake_serial.feed(build_frame(MSG_OK_REQ_DATA, b""))
        for current, total in frames:
            fake_serial.feed(_progress_frame(current, total))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    def _fake_find_and_connect(command_dict, config, **kwargs):
        return make_comm()

    positions: list[tuple[int, int]] = []
    start_calls: list[int] = []
    ack_calls = {"count": 0}

    def _record_progress(current, total):
        positions.append((current, total))

    real_start = ClassProgressHandler.start

    def _recording_start(self, *args, **kwargs):
        start_calls.append(args[0] if args else kwargs.get("total_steps"))
        return real_start(self, *args, **kwargs)

    real_send_ack = SerialCommunicator.send_ack

    def _recording_send_ack(self, *args, **kwargs):
        ack_calls["count"] += 1
        return real_send_ack(self, *args, **kwargs)

    db = EpromDatabase(skip_local_override=True)
    programmer_dict = resolve_chip(_REAL_27C_CHIP, db=db)

    operator = EpromOperator(ConfigManager(), progress_callback=_record_progress)
    with (
        patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ),
        patch.object(
            ClassProgressHandler, "start", autospec=True, side_effect=_recording_start
        ),
        patch.object(
            SerialCommunicator,
            "send_ack",
            autospec=True,
            side_effect=_recording_send_ack,
        ),
    ):
        ok = operator.write_eprom(
            _REAL_27C_CHIP,
            programmer_dict,
            str(input_file),
            address_str=address_str,
        )

    return ok, positions, start_calls, ack_calls["count"]


def _positions_only(positions: list[tuple[int, int]]) -> list[int]:
    """Extract just the recorded ``current`` values, in call order."""
    return [p[0] for p in positions]


def _is_monotonic_non_decreasing(values: list[int]) -> bool:
    return all(a <= b for a, b in zip(values, values[1:]))


# ---------------------------------------------------------------------------
# Test 1 -- HOST-02: render, don't raise.
# ---------------------------------------------------------------------------


def test_data_frame_in_main_phase_is_rendered(tmp_path, make_comm, fake_serial) -> None:
    """HOST-02: a mid-block MSG_DATA_PROGRESS frame is RENDERED, not raised
    on.

    Today, ``_main_phase_send_data``'s loop handles only MAIN/ERROR/OK and
    raises ``EpromOperationError("Programmer did not request data chunk,
    got DATA: <message>")`` on anything else -- a mid-block progress frame
    hits that raise, which is caught by ``_run_state_machine`` and
    surfaces HERE as ``ok is False`` (never a propagated exception, since
    the state machine's own try/except already converts it). The DATA
    branch this plan adds must sit *before* that raise so a mid-block frame
    is applied (D-04) and the write completes successfully instead.
    """
    ok, positions, _start_calls, _ack_count = _drive_write_with_progress_frames(
        tmp_path,
        make_comm,
        fake_serial,
        file_size=4,
        chunk_progress_frames=[[(2048, _CHIP_MEM_SIZE)]],
    )

    assert ok is True, (
        "HOST-02: a mid-block MSG_DATA_PROGRESS frame must be rendered, not "
        "raised on -- pre-Task-2 this fails because the frame hits "
        '"Programmer did not request data chunk, got DATA", caught by '
        "_run_state_machine and surfaced here as ok=False"
    )
    assert any(p[0] == 2048 for p in positions), (
        "HOST-02: the recorded progress_callback positions must include "
        f"the frame's rendered current (2048); got {positions}"
    )


# ---------------------------------------------------------------------------
# Test 2 -- HOST-02 / D-05: the frame must never be acked.
# ---------------------------------------------------------------------------


def test_progress_frame_is_not_acked(tmp_path, make_comm, fake_serial) -> None:
    """D-05: a mid-block progress frame must NEVER be acked.

    The firmware is mid-block, waiting for nothing -- an ack desyncs the
    stream. The failure mode is worse than a generic desync: on a Leonardo
    a stray buffered "OK" makes ``op_get_message`` return ``OP_MSG_ACK``,
    and ``_process_incoming_data``'s ``default: return false`` aborts the
    write with NO error frame at all. This is the
    ``#write-empty-input-regression`` trap in a new place -- that
    regression's fix was exactly ``ack_data=False`` on the write's INIT/END
    progress frames; the MAIN-phase write branch must carry the same
    discipline (and must not be routed through
    ``_handle_progress_response``, whose ``ack_data`` defaults to True).

    Proof shape: drive the SAME write twice -- once with zero mid-block
    frames, once with three -- and assert ``send_ack()``'s call count is
    IDENTICAL. Pre-Task-2 this is genuinely RED: three frames abort the
    write on the first one (only the INIT-phase-start and MAIN-phase-start
    acks fire: 2 total), while zero frames lets the write run to
    completion (all four phase-transition acks fire: INIT-start,
    MAIN-start, END-start, final: 4 total) -- 2 != 4. Post-Task-2 both
    drives complete, and neither a DATA frame's presence nor its absence
    adds or removes an ack, so the counts match.
    """
    ok_zero, _positions_zero, _start_zero, ack_count_zero = (
        _drive_write_with_progress_frames(
            tmp_path,
            make_comm,
            fake_serial,
            file_size=4,
            chunk_progress_frames=[[]],
        )
    )
    assert ok_zero is True, (
        "HOST-02: the zero-progress-frame control drive must itself "
        f"succeed (sanity check on the driver, not on D-05); got ok={ok_zero}"
    )

    serial2, comm2 = _fresh_serial_and_comm()
    ok_frames, _positions_frames, _start_frames, ack_count_frames = (
        _drive_write_with_progress_frames(
            tmp_path,
            comm2,
            serial2,
            file_size=4,
            chunk_progress_frames=[
                [(10, _CHIP_MEM_SIZE), (20, _CHIP_MEM_SIZE), (30, _CHIP_MEM_SIZE)]
            ],
        )
    )

    assert ack_count_frames == ack_count_zero, (
        "HOST-02/D-05: send_ack()'s call count with three mid-block "
        "progress frames present must equal the count with zero frames -- "
        f"a progress frame must never be acked; got {ack_count_frames} "
        f"(with frames, ok={ok_frames}) vs {ack_count_zero} (zero frames)"
    )
    assert ok_frames is True, (
        "HOST-02: once the DATA branch exists, a write with mid-block "
        f"progress frames present must also succeed; got ok={ok_frames}"
    )


# ---------------------------------------------------------------------------
# Test 3 -- HOST-02 / D-04: absolute-to-relative offset arithmetic.
# ---------------------------------------------------------------------------


def test_offset_write_bar_starts_at_zero(tmp_path, make_comm, fake_serial) -> None:
    """D-04's arithmetic: MSG_DATA_PROGRESS carries an ABSOLUTE chip
    address, but the write bar's origin is the write's OWN start address
    (0 for a full-chip write, non-zero for an --address write) -- not 0 on
    the chip. Getting this wrong shows up as a bar that starts mid-way (or
    beyond 100%) on an --address write.

    Drives with ``address_str="0x1000"`` (4096) and feeds a frame whose
    ``current`` EQUALS that start address -- the rendered position must be
    0, not 4096 -- then a second frame 16 bytes further, which must render
    as 16.

    ``ok is True`` is asserted FIRST and is the genuine pre-Task-2 RED
    signal: the write aborts on the very first frame today, so any
    assertion based only on values recorded before that abort (e.g. "the
    very first recorded position is 0") would pass vacuously, because
    ``ClassProgressHandler.start()`` always records ``(0, total)`` first
    regardless of any frame ever arriving. Isolating the LAST two recorded
    positions (this drive's single chunk means exactly one handoff
    precedes the frames) is what actually exercises the offset arithmetic.
    """
    ok, positions, _start_calls, _ack_count = _drive_write_with_progress_frames(
        tmp_path,
        make_comm,
        fake_serial,
        file_size=4,
        chunk_progress_frames=[[(4096, _CHIP_MEM_SIZE), (4112, _CHIP_MEM_SIZE)]],
        address_str="0x1000",
    )

    assert ok is True, (
        "HOST-02/D-04: a write with mid-block progress frames at a "
        f"non-zero --address must still succeed; got ok={ok}"
    )
    assert positions[-2:] == [(0, 4), (16, 4)], (
        "HOST-02/D-04: an --address 0x1000 (4096) write fed frames at "
        "absolute addresses 4096 and 4112 must render positions 0 and 16 "
        "(absolute - start_addr), not the raw absolute addresses; got the "
        f"last two recorded positions: {positions[-2:]}"
    )


# ---------------------------------------------------------------------------
# Test 4 -- HOST-02 / D-04 / Pitfall 2: no bar rebuild on a differing total.
# ---------------------------------------------------------------------------


def test_differing_total_does_not_rebuild_the_bar(
    tmp_path, make_comm, fake_serial
) -> None:
    """Pitfall 2, precisely: ``ClassProgressHandler.set_progress(current,
    total)`` calls ``self.start(total)`` whenever ``self.total_steps !=
    total``, and ``start()`` CLOSES AND RE-CREATES the tqdm bar and zeroes
    ``current_step``. The write bar is started with ``file_size``; a
    MSG_DATA_PROGRESS frame carries the chip's ``mem_size``. For a short
    input file (as here) they differ, so a write branch that naively
    called ``set_progress`` for every frame would rebuild the bar on EVERY
    ONE of them. This is a REAL defect, not a theoretical one -- and it is
    being ROUTED AROUND here, not fixed at its source, because
    ``set_progress`` is shared code on the read and blank-check paths (a
    deferred idea, out of this plan's scope).

    Drives an input file far shorter than the chip's 65536-byte
    memory-size, feeding three frames whose ``total`` values all differ
    from each other AND from ``file_size``, and asserts ``start()`` is
    invoked exactly ONCE for the whole write -- the initial
    ``progress.start(file_size)`` -- never once per frame and never once
    per distinct total.

    ``ok is True`` is asserted IN THE SAME compound check as the start()
    count, and is the genuine pre-Task-2 RED signal: today, the write
    aborts on the very first frame (before any progress-handling code ever
    runs for it), so ``start()`` is trivially called exactly once EITHER
    WAY on the pre-Task-2 code -- a bare ``len(start_calls) == 1``
    assertion would pass vacuously, for the wrong reason (early abort, not
    correct routing). Tying it to ``ok is True`` (which IS false
    pre-Task-2) makes the failure genuine.
    """
    ok, _positions, start_calls, _ack_count = _drive_write_with_progress_frames(
        tmp_path,
        make_comm,
        fake_serial,
        file_size=8,
        chunk_progress_frames=[
            [
                (100, 65536),
                (200, 70000),
                (300, 60000),
            ]
        ],
    )

    assert ok is True and start_calls == [8], (
        "HOST-02/D-04 (Pitfall 2): expected the write to succeed with "
        "ClassProgressHandler.start() invoked exactly once (the initial "
        "progress.start(8) call) despite three progress frames whose "
        "totals (65536, 70000, 60000) all differ from file_size (8) and "
        f"from each other; got ok={ok}, start_calls={start_calls}"
    )


# ---------------------------------------------------------------------------
# Test 5 -- HOST-02 / Pitfall 1: the latch, and its Uno-class negative.
# ---------------------------------------------------------------------------


def test_bar_does_not_rewind_when_firmware_drives_it(
    tmp_path, make_comm, fake_serial
) -> None:
    """Pitfall 1's latch: the chunk-handoff ``progress.update(len(data_
    chunk))`` measures bytes SENT; a MSG_DATA_PROGRESS frame reports bytes
    PROGRAMMED -- two different things, and the handoff runs first.
    Without a latch, the SECOND chunk's handoff would jump the bar ahead of
    where the first chunk's own frames left it, and the second chunk's
    frames would then visibly rewind it back down (tqdm permits ``pbar.n``
    to move backward). The fix is a latch: once the first mid-block frame
    is applied, stop calling ``update()`` on handoff and let the firmware
    drive the bar absolutely. Deleting the ``update()`` call outright is
    the WRONG fix -- see this test's sibling below, which proves a board
    that never delivers a frame must keep advancing via handoff alone.

    Drives a 600-byte file (two chunks against the 512-byte Uno-floor
    buffer: 512 + 88) with frames for BOTH chunks, chosen so an un-latched
    second handoff would be directly visible: chunk 1's frames (600, 650)
    leave ``current_step`` at 650; an un-latched chunk-2 handoff would add
    88 more (738) BEFORE chunk 2's own first frame (700) pulled it back
    down -- 738 -> 700 is a rewind, and 6 vs 7 recorded positions is a
    direct count of whether that handoff fired at all. ``ok is True`` is
    asserted first: today the write aborts on chunk 1's own first frame,
    so a "monotonic" or "exact count" check alone would still need to
    survive that abort -- see Test 4's docstring for the identical
    reasoning.
    """
    ok, positions, _start_calls, _ack_count = _drive_write_with_progress_frames(
        tmp_path,
        make_comm,
        fake_serial,
        file_size=600,
        chunk_progress_frames=[
            [(600, _CHIP_MEM_SIZE), (650, _CHIP_MEM_SIZE)],
            [(700, _CHIP_MEM_SIZE), (750, _CHIP_MEM_SIZE)],
        ],
    )

    assert ok is True, (
        "HOST-02/Pitfall 1: a two-chunk write with mid-block progress "
        f"frames for both chunks must succeed; got ok={ok}"
    )
    assert len(positions) == 6, (
        "HOST-02/Pitfall 1: expected exactly 6 recorded positions (start + "
        "chunk-1 handoff + 2 chunk-1 frames + 2 chunk-2 frames) -- chunk "
        "2's OWN handoff must be SKIPPED once the latch engages from "
        f"chunk 1's first frame; got {len(positions)}: {positions}"
    )
    values = _positions_only(positions)
    assert _is_monotonic_non_decreasing(values), (
        "HOST-02/Pitfall 1: the recorded position sequence must never "
        "rewind once the firmware starts driving the bar -- an un-latched "
        "chunk-2 handoff would jump to 738 before chunk 2's own first "
        f"frame (700) pulled it back down; got {values}"
    )


def test_bar_still_advances_with_zero_progress_frames(
    tmp_path, make_comm, fake_serial
) -> None:
    """Pitfall 1's non-vacuity guard, and the reason the latch must never
    simply DELETE the chunk-handoff ``update()`` call: a board that emits
    no mid-block MSG_DATA_PROGRESS frame at all (every uno/uno328pb write,
    BF-2) must NOT be regressed to a bar that never moves.

    This test is expected to pass on BOTH the pre- and post-Task-2 code --
    it exercises the handoff path alone, which this plan does not change --
    and is recorded here as an honest characterization (mirroring Phase 143
    Plan 04's own Test 6, which "legitimately PASSES both before and after
    Task 2" per that plan's SUMMARY) rather than forced into an artificial
    RED.

    Drives the SAME 600-byte / two-chunk shape as this test's sibling
    above, but with zero progress frames for either chunk: the bar must
    still advance via handoff alone, in two visible steps.
    """
    ok, positions, _start_calls, _ack_count = _drive_write_with_progress_frames(
        tmp_path,
        make_comm,
        fake_serial,
        file_size=600,
        chunk_progress_frames=[[], []],
    )

    assert ok is True, (
        f"HOST-02/Pitfall 1: the zero-progress-frame write must succeed; got ok={ok}"
    )
    values = _positions_only(positions)
    assert values == [0, 512, 600], (
        "HOST-02/Pitfall 1: with no mid-block frames at all, the bar must "
        "still advance from chunk handoff alone (start=0, chunk 1's 512 "
        f"bytes, chunk 2's 88 more bytes = 600); got {values}"
    )
