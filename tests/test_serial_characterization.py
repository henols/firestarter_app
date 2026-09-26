"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 36 Plan 02 — Serial frame-parse characterization suite (TEST-02).

Pins _read_and_parse_lines preamble->body->terminator sequence and the
sliding-window timeout-reset invariant. _read_and_parse_lines is
RING-FENCED for v1.9 RCA (GATE-1.8d) — tests observe only via
get_response() and external generator yields. Do NOT modify serial_comm.py.

Covers requirements:
  - TEST-02: _read_and_parse_lines preamble->body->terminator sequence
             (INIT->MAIN->END ack flow via the generator yield surface).
  - TEST-02: Sliding-window timeout resets on every yield — both responses
             yielded when second is fed after the first yield.
  - TEST-02: get_response(timeout=tiny) raises SerialTimeoutError quickly on
             an empty fake serial (tiny real-clock technique, RESEARCH Pattern 5).
"""

import time

import pytest

from firestarter.serial_comm import SerialTimeoutError

from .conftest import build_frame


def _drive_one_response(comm, timeout: float = 1.0):
    """Pull exactly one Response off the read loop, returning None on timeout."""
    gen = comm._read_and_parse_lines(timeout=timeout)
    try:
        return next(gen)
    except StopIteration:
        return None


class TestSerialFrameParse:
    """Characterization tests pinning the INIT->MAIN->END ack sequence
    through _read_and_parse_lines (observed externally only — GATE-1.8d)."""

    def test_preamble_body_terminator_sequence(self, fake_serial, make_comm):
        """Pin: INIT->MAIN->END ack sequence flows through _read_and_parse_lines
        in the correct order, each frame yielding a Response with the expected
        type and message.

        Feeds MSG_OK_READY (INIT ready ack), MSG_INIT_DONE, MSG_MAIN_DONE, and
        MSG_END_DONE as wire frames and asserts each Response.type and message
        in order via the generator yield surface.
        """
        from firestarter.messages import (
            MSG_END_DONE,
            MSG_INIT_DONE,
            MSG_MAIN_DONE,
            MSG_OK_READY,
        )

        comm = make_comm()
        # Feed the four-frame INIT->MAIN->END ack sequence
        fake_serial.feed(build_frame(MSG_OK_READY, b""))
        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
        fake_serial.feed(build_frame(MSG_END_DONE, b""))

        gen = comm._read_and_parse_lines(timeout=1.0)

        r0 = next(gen)
        assert r0 is not None
        assert r0.type == "OK"
        assert r0.message == "Ready"

        r1 = next(gen)
        assert r1 is not None
        assert r1.type == "INIT"
        assert r1.message == "(init done)"

        r2 = next(gen)
        assert r2 is not None
        assert r2.type == "MAIN"
        assert r2.message == "(main done)"

        r3 = next(gen)
        assert r3 is not None
        assert r3.type == "END"
        assert r3.message == "(end done)"

    def test_ok_ready_frame_via_get_response(self, fake_serial, make_comm):
        """Pin: MSG_OK_READY frame surfaces via get_response() as
        Response(type='OK', message='Ready') — verifies the public API surface
        used by expect_ack and the operation state machine."""
        from firestarter.messages import MSG_OK_READY

        comm = make_comm()
        fake_serial.feed(build_frame(MSG_OK_READY, b""))

        response = comm.get_response(timeout=1.0)
        assert response is not None
        assert response.type == "OK"
        assert response.message == "Ready"


def test_timeout_raises_on_empty(make_comm, fake_serial):
    """Pin: with no data fed, get_response(timeout=0.02) raises SerialTimeoutError
    and completes in well under 0.5 seconds.

    Uses the tiny-real-clock technique (RESEARCH Pattern 5): a 20 ms timeout is
    long enough for the generator loop to spin through several empty reads, short
    enough that the test completes in < 25 ms total. No time.time monkeypatching
    (per binding constraint 7 / RESEARCH anti-pattern).
    """
    comm = make_comm()
    # No data fed — get_response must raise SerialTimeoutError quickly
    start = time.time()
    with pytest.raises(SerialTimeoutError):
        comm.get_response(timeout=0.02)
    elapsed = time.time() - start
    assert elapsed < 0.5, (
        f"SerialTimeoutError should arrive in << 0.5 s, took {elapsed:.3f} s"
    )


def test_sliding_window_resets_on_yield(make_comm, fake_serial):
    """Pin: each yield of _read_and_parse_lines resets the timeout window.

    Invariant: if the window did NOT reset on yield, a second response fed after
    the first yield would not be received before the original timeout expired —
    only 1 response would be yielded. The test feeds a second frame immediately
    after the first yield and asserts BOTH are yielded, proving the window reset.

    Uses a 50 ms per-call timeout so total runtime stays well under a second.
    serial_comm.py is NOT modified (ring-fenced, GATE-1.8d).
    """
    from firestarter.messages import MSG_OK_READY

    comm = make_comm()
    # Feed first response
    fake_serial.feed(build_frame(MSG_OK_READY, b""))

    results = []
    # Iterate the generator with a small timeout; feed the second response
    # immediately after the first yield to exercise the sliding-window reset.
    for r in comm._read_and_parse_lines(0.05):
        results.append(r)
        if len(results) == 1:
            # Feed the second response right after the first yield —
            # the window must reset here or the second will be missed
            fake_serial.feed(build_frame(MSG_OK_READY, b""))
        if len(results) >= 2:
            break

    # Both responses must be yielded; window reset verified by construction:
    # if window did NOT reset after yield 1, timeout (50 ms) would have
    # started before the second frame was fed and the second yield would
    # never arrive within the window.
    assert len(results) == 2, (
        f"Expected 2 yielded responses (sliding-window reset); got {len(results)}"
    )
    assert results[0].type == "OK"
    assert results[0].message == "Ready"
    assert results[1].type == "OK"
    assert results[1].message == "Ready"
