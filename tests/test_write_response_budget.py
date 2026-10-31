"""HOST-01 write-path response-timeout oracles, plus D-12's negative proof
(Phase 143 Plan 04, `143-04-PLAN.md` Task 1).

**Real chip used throughout this module:** ``w27c512`` (``W27C512,W27E512`` in
``firestarter/data/chip_database.json``, algorithm 7 / protocol ``0x07``
EPROM_STD, ``support_status: supported``). Already the shared "non-0x0D"
fixture chip elsewhere in this suite (``tests/test_write_skip_sdp_unlock.py``'s
``_NON_0X0D_CHIP``), so nothing here introduces a second identity for the
same real part.

**What this module proves:**

- HOST-01: the host's own per-block write-time budget
  (``write_budget.block_budget_s``, from the command's algorithm and
  pulse-delay and the firmware's block size) is used **verbatim** as the
  MAIN-phase ``get_response`` timeout on the write path. The firmware used
  to send this figure in its ack; it no longer does.
- A protocol with no 27C EPROM row falls back to a generous fixed timeout --
  never the old 10 s default and never a refusal.
- D-12 (the most important claim here): every non-write path -- ``verify``,
  ``blank-check``, ``erase`` -- keeps waiting exactly
  ``DEFAULT_RESPONSE_TIMEOUT``, so a genuinely dead board still reports in
  ten seconds on those commands. Since 202-01 D-01/D-02, ``verify_eprom``
  shares ``_main_phase_read_data`` with ``read_eprom`` instead of
  ``_main_phase_send_data`` with ``write_eprom``; this module's Test 4 is
  the proof that verify's calls stay argument-free and can never inherit
  the write path's budget. Since 202-05 D-02, ``check_eprom_blank`` ALSO
  shares ``_main_phase_read_data`` (with ``verify_eprom``/``read_eprom``)
  instead of ``_main_phase_simple`` -- only ``erase_eprom`` still occupies
  the payload-free ``_main_phase_simple``/``_execute_phase`` group the
  driver docstring below describes.
- D-13: the write's INIT/END phases (``_execute_phase``) are untouched --
  they keep their bare, argument-free ``get_response()`` call regardless of
  what MAIN-phase budget applies.
- Pitfall 6 (`143-RESEARCH.md`): none of these tests wait out a real
  timeout. ``_FakeSerial.read()`` returns ``b""`` immediately when its
  buffer is empty, and ``_read_and_parse_lines`` responds with
  ``time.sleep(0.001); continue`` **without** resetting ``start_time``
  (``firestarter/serial_comm.py``) -- so a naive test that "feeds nothing
  and asserts no timeout" would run for the full write budget (120 s,
  or thousands of seconds against a real one). Tests 1-5 use the
  **call-argument oracle**: wrap ``SerialCommunicator.get_response`` and
  assert the ``timeout`` value it was actually called with, which is exact
  and runs in milliseconds. Test 6 uses the **fake-clock oracle** to prove
  the underlying read loop itself survives a long gap, with the number of
  simulated empty reads bounded so the test cannot spin.

**Driver shape:** every driver here feeds ``MSG_INIT_DONE`` ->
[``MSG_OK_REQ_DATA`` for ``write_eprom`` / ``MSG_DATA_CHUNK`` for
``verify_eprom``, 202-01 D-01/D-02] -> ``MSG_MAIN_DONE`` -> ``MSG_END_DONE``
and connects through ``connect_via`` to a real communicator on a fake serial
port. A
4-byte input file always fits in a single chunk at any buffer size this
module exercises (the Uno floor is 512 B, decoded via
``_calculate_buffer_size`` when ``firmware_max_chunk`` is ``None``), so the
write/verify drivers' recorded call list has a fixed, known shape: exactly
``[INIT call, MAIN call #1 (OK_REQ_DATA or DATA_CHUNK), MAIN call #2
(MAIN_DONE), END call]`` -- four calls, in that order, for either method.
The simple-operation driver (``erase_eprom`` alone, as of 202-05 -- see the
D-12 note above; ``check_eprom_blank`` moved onto the read-pull shape, no
``main_phase_handler``, no data file) has three: ``[INIT, MAIN, END]``.

The recorder itself wraps ``SerialCommunicator.get_response`` with an
``autospec=True`` ``side_effect`` that **delegates to the real bound
method** rather than replacing it with a stub -- a stub would return no
real ``Response`` object and the state machine would never advance past its
first call. Empirically confirmed (`.venv/ci-replica`, this session):
``autospec=True`` on a class-level method patch DOES pass the instance as
the mock's own first recorded argument (visible on ``mock.call_args``), but
a wrapper function declared as ``def _recorder(self, *args, **kwargs)``
binds that instance to its own named ``self`` parameter through ordinary
Python argument binding -- so ``args`` INSIDE the wrapper already excludes
it. This module's recorder stores exactly that already-self-free
``(args, kwargs)`` pair, so ``args[0]`` (when present) is whatever the call
site actually passed as its first argument after ``self`` -- nothing at all
for a bare ``self.comm.get_response()`` (INIT/END, D-13), or the resolved
``timeout`` for a write-path MAIN-phase call.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from firestarter.config import ConfigManager
from firestarter.messages import MSG_END_DONE, MSG_INIT_DONE, MSG_MAIN_DONE
from firestarter.serial_comm import DEFAULT_RESPONSE_TIMEOUT, SerialCommunicator

from .conftest import (
    build_frame,
    connect_via,
    fresh_serial_and_comm,
    read_phase_frames,
    write_success_frames,
)

# Real 27C part used throughout this module -- see the module docstring.
_REAL_27C_CHIP = "w27c512"


@contextmanager
def _recording_get_response_patch(calls: list):
    """Patch ``SerialCommunicator.get_response`` with a call-recording wrapper
    that DELEGATES to the real bound method (never a stub -- see the module
    docstring for why a stub would desync the state machine). Appends
    ``(args, kwargs)`` per call, in call order, to ``calls``.
    """
    real_get_response = SerialCommunicator.get_response

    def _recording_get_response(self, *args, **kwargs):
        calls.append((args, kwargs))
        return real_get_response(self, *args, **kwargs)

    with patch.object(
        SerialCommunicator,
        "get_response",
        autospec=True,
        side_effect=_recording_get_response,
    ):
        yield


def _timeout_of(call) -> float | None:
    """Extract the ``timeout`` value from one recorded ``get_response`` call.

    ``self`` is already excluded from the recorded ``args`` (see the module
    docstring), so ``args[0]`` -- when present -- IS the ``timeout``
    positional. A bare ``self.comm.get_response()`` call (INIT/END, D-13)
    carries no further positional or keyword argument at all; this returns
    ``None`` for that case rather than ``DEFAULT_RESPONSE_TIMEOUT``, so
    callers can tell "argument-free" apart from "explicitly resolved to the
    default" if they need to.
    """
    args, kwargs = call
    if "timeout" in kwargs:
        return kwargs["timeout"]
    if len(args) >= 1:
        return args[0]
    return None


def _main_phase_calls(calls: list) -> list:
    """Slice the MAIN-phase ``get_response`` calls out of a full
    INIT->MAIN->END recording for the write/verify driver below (exactly 4
    calls for this module's fixed one-chunk feed sequence: ``[INIT, MAIN,
    MAIN, END]`` -- see the module docstring).
    """
    assert len(calls) == 4, (
        "expected exactly 4 get_response calls (INIT, 2x MAIN, END) for "
        f"this module's fixed feed sequence; got {len(calls)}: {calls}"
    )
    return calls[1:-1]


def _drive_and_record_timeouts(make_comm, fake_serial, frames, call, max_chunk=None):
    """Feed ``frames``, run ``call(operator, programmer_dict)`` against
    ``_REAL_27C_CHIP`` through the fake port, and record every
    ``get_response`` timeout.

    ``max_chunk`` sets ``firmware_max_chunk`` on the connected communicator
    (what the ack's buffer-size field would have set).

    Returns ``(ok, calls)``.
    """
    from firestarter.chip_resolver import resolve_chip
    from firestarter.database import EpromDatabase
    from firestarter.eprom_operations import EpromOperator

    for frame in frames:
        fake_serial.feed(frame)

    def _on_connect(comm):
        if max_chunk is not None:
            comm.firmware_max_chunk = max_chunk

    programmer_dict = resolve_chip(
        _REAL_27C_CHIP, db=EpromDatabase(skip_local_override=True)
    )
    calls: list = []
    operator = EpromOperator(ConfigManager())
    with (
        connect_via(make_comm, on_connect=_on_connect),
        _recording_get_response_patch(calls),
    ):
        ok = call(operator, programmer_dict)
    return ok, calls


def _drive_data_operation_and_record_timeouts(
    tmp_path,
    make_comm,
    fake_serial,
    *,
    method_name: str,
    pulse_delay: int | None = None,
    max_chunk: int | None = None,
):
    """Drive ``write_eprom`` or ``verify_eprom`` against ``_REAL_27C_CHIP``
    with a 4-byte input file, recording every ``get_response`` timeout.

    Shared by the write-path positive proofs (``method_name="write_eprom"``)
    and D-12's negative proof for verify (``method_name="verify_eprom"``).

    ``pulse_delay`` overrides the command's ``pulse-delay`` and
    ``max_chunk`` sets ``firmware_max_chunk``. Together they fix the inputs
    of the host's own budget calculation (``write_budget.block_budget_s``).

    One MAIN-phase data round trip either way, so the recorded call list is
    always exactly ``[INIT, MAIN, MAIN, END]`` (202-01 D-01/D-02:
    ``verify_eprom`` drives ``_main_phase_read_data``, a pull -- it never
    sends or receives ``MSG_OK_REQ_DATA``. Feeding the file's own bytes back
    as the one ``MSG_DATA_CHUNK`` payload keeps the driven verify a clean
    match, so ``ok`` is genuinely 0).

    Returns ``(ok, calls)``.
    """
    input_file = tmp_path / f"{_REAL_27C_CHIP}.bin"
    file_bytes = b"\x01\x02\x03\x04"
    input_file.write_bytes(file_bytes)

    if method_name == "verify_eprom":
        frames = read_phase_frames(file_bytes)
    else:
        frames = write_success_frames()

    def _call(operator, programmer_dict):
        if pulse_delay is not None:
            programmer_dict["pulse-delay"] = pulse_delay
        method = getattr(operator, method_name)
        return method(_REAL_27C_CHIP, programmer_dict, str(input_file))

    return _drive_and_record_timeouts(
        make_comm, fake_serial, frames, _call, max_chunk=max_chunk
    )


def _drive_write_and_record_timeouts(
    tmp_path,
    make_comm,
    fake_serial,
    *,
    pulse_delay: int | None = None,
    max_chunk: int | None = None,
):
    """``write_eprom``-only convenience wrapper over
    ``_drive_data_operation_and_record_timeouts`` -- see its docstring.
    """
    return _drive_data_operation_and_record_timeouts(
        tmp_path,
        make_comm,
        fake_serial,
        method_name="write_eprom",
        pulse_delay=pulse_delay,
        max_chunk=max_chunk,
    )


def _drive_erase_and_record_timeouts(make_comm, fake_serial):
    """Drive ``erase_eprom`` (falls through to ``_main_phase_simple`` -- no
    ``main_phase_handler``, no data file, no ``MSG_OK_REQ_DATA``), recording
    every ``get_response`` timeout. D-12's negative proof for the OTHER
    shared machinery ``write_eprom`` never touches at all.

    The recorded call list is always exactly ``[INIT, MAIN, END]``.
    """
    frames = [
        build_frame(MSG_INIT_DONE, b""),
        build_frame(MSG_MAIN_DONE, b""),
        build_frame(MSG_END_DONE, b""),
    ]
    return _drive_and_record_timeouts(
        make_comm,
        fake_serial,
        frames,
        lambda operator, pd: operator.erase_eprom(_REAL_27C_CHIP, pd),
    )


def _drive_blank_check_and_record_timeouts(make_comm, fake_serial):
    """Drive ``check_eprom_blank``, recording every ``get_response`` timeout.

    202-05 D-02: ``check_eprom_blank`` shares ``_main_phase_read_data`` with
    ``verify_eprom``/``read_eprom``. An explicit ``size_str`` bounds the read
    to one small chunk, and the fed ``MSG_DATA_CHUNK`` is all-0xFF so the
    driven blank-check is genuinely blank (``ok == 0``). The recorded call
    list is ``[INIT, MAIN, MAIN, END]``, the shape ``_main_phase_calls``
    expects.
    """
    blank_bytes = b"\xff\xff\xff\xff"
    return _drive_and_record_timeouts(
        make_comm,
        fake_serial,
        read_phase_frames(blank_bytes),
        lambda operator, pd: operator.check_eprom_blank(
            _REAL_27C_CHIP, pd, size_str=str(len(blank_bytes))
        ),
    )


# 0x07 at 1000 us on a 1024-byte block: 25 pulses x 1000 us x 1024 B =
# 25.6 s, ceil 26, padded 26 x 2 + 2 = 54 s.
_BUDGET_0X07_1000US_1024B = 54.0


def test_write_uses_the_host_calculated_budget(
    tmp_path, make_comm, fake_serial
) -> None:
    """The write path's MAIN-phase timeout is the host's own
    ``write_budget.block_budget_s`` for this command, used verbatim -- never
    multiplied and never the 10 s default."""
    ok, calls = _drive_write_and_record_timeouts(
        tmp_path, make_comm, fake_serial, pulse_delay=1000, max_chunk=1024
    )
    assert ok is True, "the driven write must complete successfully"

    main_phase_timeouts = [_timeout_of(c) for c in _main_phase_calls(calls)]
    assert all(t == _BUDGET_0X07_1000US_1024B for t in main_phase_timeouts), (
        f"expected every MAIN-phase timeout to be {_BUDGET_0X07_1000US_1024B}; "
        f"got {main_phase_timeouts}"
    )


def test_the_budget_follows_the_firmware_block_size(
    tmp_path, make_comm, fake_serial
) -> None:
    """With no buffer size in the ack the host uses the 512-byte floor, and
    the budget is calculated for that block: 25 x 1000 us x 512 B = 12.8 s,
    ceil 13, padded 28 s."""
    ok, calls = _drive_write_and_record_timeouts(
        tmp_path, make_comm, fake_serial, pulse_delay=1000
    )
    assert ok is True, "the driven write must complete successfully"

    main_phase_timeouts = [_timeout_of(c) for c in _main_phase_calls(calls)]
    assert all(t == 28.0 for t in main_phase_timeouts), main_phase_timeouts


def test_a_protocol_without_an_eprom_row_falls_back_to_120s() -> None:
    """Flash, 0x0D EEPROM and SRAM have no 27C row: the write path uses
    ``WRITE_BLOCK_TIMEOUT_FALLBACK_S``."""
    from firestarter.eprom_operations import (
        WRITE_BLOCK_TIMEOUT_FALLBACK_S,
        EpromOperator,
    )

    operator = EpromOperator(ConfigManager())
    for algorithm in (0x05, 0x0D, 0x28):
        timeout = operator._write_block_timeout(
            {"algorithm": algorithm, "pulse-delay": 0}, 1024
        )
        assert timeout == WRITE_BLOCK_TIMEOUT_FALLBACK_S == 120.0


def test_non_write_paths_keep_default_timeout(tmp_path, make_comm, fake_serial) -> None:
    """D-12's negative proof, and the most important test in this module.

    Pre-202-01, ``verify_eprom`` shared ``_main_phase_send_data`` with
    ``write_eprom``, and this test's verify half proved that sharing the
    function did not leak the write path's budget onto verify -- via an
    EXPLICIT resolution (``response_timeout=None -> DEFAULT_RESPONSE_TIMEOUT``
    inside that shared function).

    202-01 D-01/D-02 moves ``verify_eprom`` onto ``_main_phase_read_data``
    instead (the same pull-based handler ``read_eprom`` drives) -- a
    completely different function that has never had a ``response_timeout``
    parameter, or any concept of a write-style budget, to begin with. Its
    ``get_response()`` calls are unconditionally bare, which is a STRONGER
    guarantee than an explicit resolution: verify now has no code path
    through which a write budget could reach it even by accident. The
    bare call still costs exactly ``DEFAULT_RESPONSE_TIMEOUT`` on a dead
    board, because that is ``get_response``'s own default parameter value --
    this test's job below is to prove the call stays bare (never gains an
    explicit override), not that a resolution mechanism ran.

    ``erase_eprom`` exercises the OTHER shared machinery
    (``_main_phase_simple`` / ``_execute_phase``) that this plan never
    touches at all -- its calls must stay truly bare, both before and after
    202-01. ``check_eprom_blank`` moved onto the SAME read-pull shape as
    verify in 202-05 -- its MAIN-phase calls must stay bare for the same
    reason verify's do (no write-style budget parameter exists on that code
    path at all).

    A genuinely dead board must still report in ten seconds on every one of
    these -- inheriting the write path's multi-minute budget would turn a
    real hardware fault into a multi-minute hang on the most commonly run
    commands.
    """
    verify_ok, verify_calls = _drive_data_operation_and_record_timeouts(
        tmp_path, make_comm, fake_serial, method_name="verify_eprom"
    )
    assert verify_ok == 0, "D-12/202-01 D-10: the driven verify must report a match (0)"

    verify_main_timeouts = [_timeout_of(c) for c in _main_phase_calls(verify_calls)]
    assert all(t is None for t in verify_main_timeouts), (
        "202-01 D-01/D-02: verify_eprom's MAIN-phase get_response calls "
        "(now _main_phase_read_data, shared with read_eprom, never "
        "_main_phase_send_data) must stay argument-free -- get_response's "
        f"own {DEFAULT_RESPONSE_TIMEOUT}s default is what bounds a dead "
        f"board, not a resolved override; got {verify_main_timeouts}"
    )

    blank_serial, blank_comm = fresh_serial_and_comm()
    blank_ok, blank_calls = _drive_blank_check_and_record_timeouts(
        blank_comm, blank_serial
    )
    assert blank_ok == 0, "202-05 D-10: the driven blank-check must report blank (0)"

    erase_serial, erase_comm = fresh_serial_and_comm()
    erase_ok, erase_calls = _drive_erase_and_record_timeouts(erase_comm, erase_serial)
    assert erase_ok is True, "D-12: the driven erase must complete successfully"

    for label, calls in (
        ("check_eprom_blank", blank_calls),
        ("erase_eprom", erase_calls),
    ):
        timeouts = [_timeout_of(c) for c in calls]
        assert all(t is None for t in timeouts), (
            f"D-12: {label}'s get_response calls must stay argument-free "
            f"(bare get_response()); got {timeouts}"
        )
        assert 120.0 not in timeouts, (
            f"D-12: {label} must never see the write-path's 120.0 fallback "
            f"timeout; got {timeouts}"
        )


def test_init_and_end_phases_keep_default_timeout(
    tmp_path, make_comm, fake_serial
) -> None:
    """D-13: a write's INIT and END phases stay argument-free even though its
    MAIN phase uses the write budget. The INIT phase of a write
    (erase plus chunked blank check) emits one ``MSG_DATA_PROGRESS`` per
    chunk and each yielded frame resets the read loop's ``start_time``
    (``firestarter/serial_comm.py``), so its 10 s window is fed by
    construction; the END phase for ``CMD_WRITE`` is a bare ack round trip
    because ``firestarter_operation_end`` is ``NULL`` for it. Threading the
    budget into ``_execute_phase`` would buy nothing and widen the blast
    radius -- this is why ``_execute_phase`` keeps its bare
    ``get_response()`` call untouched.

    The contrast half (MAIN *does* carry the budget) is asserted
    in the SAME test and against the SAME drive -- this is also what makes
    the test genuinely fail before Task 2 lands: today nothing is threaded
    at all, so the MAIN calls would be argument-free too, and an INIT/END
    -only assertion would pass on the current code for the wrong reason.
    """
    ok, calls = _drive_write_and_record_timeouts(
        tmp_path, make_comm, fake_serial, pulse_delay=1000, max_chunk=1024
    )
    assert ok is True, "D-13: the driven write must complete successfully"
    assert len(calls) == 4, (
        f"expected [INIT, MAIN, MAIN, END]; got {len(calls)}: {calls}"
    )

    init_call, main_call_1, main_call_2, end_call = calls

    main_timeouts = [_timeout_of(main_call_1), _timeout_of(main_call_2)]
    assert _BUDGET_0X07_1000US_1024B in main_timeouts, (
        "HOST-01: expected this write's own MAIN phase to carry the "
        f"{_BUDGET_0X07_1000US_1024B} s budget so this test can prove the "
        f"CONTRAST with INIT/END; got {main_timeouts}"
    )

    for label, call in (("INIT", init_call), ("END", end_call)):
        timeout = _timeout_of(call)
        assert timeout is None, (
            f"D-13: the write's {label}-phase get_response call must stay "
            "argument-free (bare get_response()) even though the MAIN phase "
            f"carries a budget; got timeout={timeout}"
        )


# Test 6 -- Pitfall 6's fake-clock oracle.


def test_long_gap_within_budget_does_not_time_out(make_comm, fake_serial) -> None:
    """Pitfall 6 (`143-RESEARCH.md`): ``_FakeSerial.read()`` returns ``b""``
    immediately when its buffer is empty, and the read loop sleeps 1 ms per
    empty read without resetting ``start_time`` (``firestarter/serial_comm.py``)
    -- so a naive version of this test, one that actually waited out a real
    gap, would run for the full write budget (120 s, or thousands of
    seconds against a real one).

    This test proves ``get_response`` survives a SIMULATED inter-frame gap
    longer than ``DEFAULT_RESPONSE_TIMEOUT`` (10 s) and shorter than the
    budget, in well under a second of REAL wall clock: it fakes
    ``firestarter.serial_comm``'s ``time.time()`` to advance by a full
    simulated second on every call (20 scripted empty reads x 1.0 s/call
    already yields a ~20 s simulated gap before any preamble byte is even
    read -- comfortably past 10 s and under the 60 s budget used here) while
    leaving ``time.sleep`` completely unpatched. The real-wall-clock guard
    below uses ``time.perf_counter()``, which this test does NOT patch, so it
    independently proves the REAL elapsed time stayed small regardless of
    what the faked ``time.time()`` claims. The loop cannot spin regardless of
    the fake clock's arithmetic: the number of scripted empty reads is
    bounded to a small, fixed count (``empty_reads``), so the real serial
    reader makes at most ``empty_reads`` empty-read iterations before the
    already-fed frame becomes visible.
    """
    import time

    comm = make_comm()
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))

    empty_reads = 20  # bounded, deterministic -- see docstring above
    real_read = fake_serial.read
    call_count = {"n": 0}

    def _gapped_read(n: int = 1) -> bytes:
        if call_count["n"] < empty_reads:
            call_count["n"] += 1
            return b""
        return real_read(n)

    fake_serial.read = _gapped_read

    fake_now = {"t": 1_000_000.0}

    def _fake_time() -> float:
        fake_now["t"] += 1.0  # 1 simulated second per time.time() call
        return fake_now["t"]

    wall_start = time.perf_counter()
    with patch("firestarter.serial_comm.time.time", side_effect=_fake_time):
        response = comm.get_response(60.0)
    wall_elapsed = time.perf_counter() - wall_start

    assert response.type == "MAIN", (
        "expected the fed MSG_MAIN_DONE frame to decode as type MAIN; got "
        f"{response.type!r}"
    )
    assert wall_elapsed < 1.0, (
        "Pitfall 6 guard: this test must complete in well under a second of "
        f"REAL wall clock; took {wall_elapsed:.3f}s -- the empty-read bound "
        "(or the production sleep-per-empty-read) may have regressed"
    )
