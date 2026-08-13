"""HOST-01 write-path response-timeout oracles, plus D-12's negative proof
(Phase 143 Plan 04, `143-04-PLAN.md` Task 1).

**Real chip used throughout this module:** ``w27c512`` (``W27C512,W27E512`` in
``firestarter/data/chip_database.json``, algorithm 7 / protocol ``0x07``
EPROM_STD, ``support_status: supported``). Already the shared "non-0x0D"
fixture chip elsewhere in this suite (``tests/test_write_skip_sdp_unlock.py``'s
``_NON_0X0D_CHIP``), so nothing here introduces a second identity for the
same real part.

**What this module proves:**

- HOST-01 / D-09: a firmware-advertised per-block write-time budget
  (``write_block_budget_s``, CAP-03) is used **verbatim** as the MAIN-phase
  ``get_response`` timeout on the write path -- no host-side multiplier.
- D-10: an absent, zero, or implausibly-large advertisement falls back to a
  derived, generous, fixed timeout -- never the old 10 s default and never a
  refusal.
- D-12 (the most important claim here): every non-write path -- ``verify``,
  ``blank-check``, ``erase`` -- keeps waiting exactly
  ``DEFAULT_RESPONSE_TIMEOUT``, so a genuinely dead board still reports in
  ten seconds on those commands. ``verify_eprom`` shares
  ``_main_phase_send_data`` with ``write_eprom``; this module's Test 4 is the
  proof that sharing the function did not leak the budget onto ``verify``.
- D-13: the write's INIT/END phases (``_execute_phase``) are untouched --
  they keep their bare, argument-free ``get_response()`` call regardless of
  what MAIN-phase budget was advertised.
- Pitfall 6 (`143-RESEARCH.md`): none of these tests wait out a real
  timeout. ``_FakeSerial.read()`` returns ``b""`` immediately when its
  buffer is empty, and ``_read_and_parse_lines`` responds with
  ``time.sleep(0.001); continue`` **without** resetting ``start_time``
  (``firestarter/serial_comm.py``) -- so a naive test that "feeds nothing
  and asserts no timeout" would run for the full advertised budget (120 s,
  or thousands of seconds against a real one). Tests 1-5 use the
  **call-argument oracle**: wrap ``SerialCommunicator.get_response`` and
  assert the ``timeout`` value it was actually called with, which is exact
  and runs in milliseconds. Test 6 uses the **fake-clock oracle** to prove
  the underlying read loop itself survives a long gap, with the number of
  simulated empty reads bounded so the test cannot spin.

**Driver shape, modelled on ``tests/test_eprom_operations.py``'s
``_drive_write_eprom_for_ack_check``:** every driver here feeds
``MSG_INIT_DONE`` -> [``MSG_OK_REQ_DATA``] -> ``MSG_MAIN_DONE`` ->
``MSG_END_DONE`` and patches ``SerialCommunicator.find_and_connect`` to
return a ``make_comm()`` instance without touching a real serial port. A
4-byte input file always fits in a single chunk at any buffer size this
module exercises (the Uno floor is 512 B, decoded via
``_calculate_buffer_size`` when ``firmware_max_chunk`` is ``None``), so the
write/verify drivers' recorded call list has a fixed, known shape: exactly
``[INIT call, MAIN call #1 (OK_REQ_DATA), MAIN call #2 (MAIN_DONE), END
call]`` -- four calls, in that order. The simple-operation driver
(``check_eprom_blank`` / ``erase_eprom``, no ``main_phase_handler``, no data
file) has three: ``[INIT, MAIN, END]``.

The recorder itself wraps ``SerialCommunicator.get_response`` with an
``autospec=True`` ``side_effect`` that **delegates to the real bound
method** rather than replacing it with a stub -- a stub would return no
real ``Response`` object and the state machine would never advance past its
first call. Empirically confirmed (`.venv/ci-replica`, this session):
``autospec=True`` on a class-level method patch passes ``self`` as the
wrapper's own first positional argument, so every recorded ``(args,
kwargs)`` tuple has ``args[0] is <the SerialCommunicator instance>`` and
``args[1:]`` / ``kwargs`` carry whatever the call site actually passed --
nothing at all for a bare ``self.comm.get_response()`` (INIT/END, D-13), or
the resolved ``timeout`` for a write-path MAIN-phase call.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from firestarter.config import ConfigManager
from firestarter.messages import (
    MSG_END_DONE,
    MSG_INIT_DONE,
    MSG_MAIN_DONE,
    MSG_OK_REQ_DATA,
)
from firestarter.serial_comm import DEFAULT_RESPONSE_TIMEOUT, SerialCommunicator

from .conftest import _FakeSerial, build_frame

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

    ``args[0]`` is always ``self`` (autospec includes it -- see the module
    docstring). A bare ``self.comm.get_response()`` call (INIT/END, D-13)
    carries no further positional or keyword argument at all; this returns
    ``None`` for that case rather than ``DEFAULT_RESPONSE_TIMEOUT``, so
    callers can tell "argument-free" apart from "explicitly resolved to the
    default" if they need to.
    """
    args, kwargs = call
    if "timeout" in kwargs:
        return kwargs["timeout"]
    if len(args) >= 2:
        return args[1]
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


def _fresh_serial_and_comm():
    """Build an independent ``(fake_serial, make_comm)`` pair.

    Mirrors ``tests/conftest.py``'s ``fake_serial``/``make_comm`` fixtures
    exactly, including the CAP-02/CAP-03 attributes this module's drivers
    read. Needed only when a test drives two full writes/operations: a
    successful drive's ``SerialCommunicator`` closes its fake serial port at
    the end (``_FakeSerial.close()`` sets ``is_open = False``), so a second
    drive cannot reuse the same fixture-injected ``fake_serial`` instance.
    Precedent: ``tests/test_write_skip_sdp_unlock.py``'s own
    ``_fresh_serial_and_comm``, same reasoning, same shape.
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


def _drive_data_operation_and_record_timeouts(
    tmp_path,
    make_comm,
    fake_serial,
    *,
    method_name: str,
    advertised_budget: int | None = None,
):
    """Drive ``write_eprom`` or ``verify_eprom`` (both share
    ``_main_phase_send_data``) against ``_REAL_27C_CHIP`` through a fake
    serial port, recording every ``get_response`` timeout.

    Shared by the write-path positive proofs (``method_name="write_eprom"``)
    and D-12's negative proof for verify (``method_name="verify_eprom"``) --
    driving the SAME shared handler through the SAME driver is exactly what
    proves the two methods diverge only in whether ``response_timeout`` is
    threaded in.

    ``advertised_budget`` sets ``write_block_budget_s`` directly on the
    ``make_comm()`` instance BEFORE it is returned from
    ``_fake_find_and_connect`` -- simulating what CAP-03's decode arm would
    have populated from a real ack, without needing a real ack frame.
    ``None`` simulates CAP-03 absent (a released beta firmware with CAP-02
    but not CAP-03, or a v1.31 build after 143-03's CAP-02 port but before
    its CAP-03 append -- corrected D-10 non-claim: NOT a mid-milestone
    v1.31 build, which BF-1 shows cannot connect at all).

    Feeds exactly ``MSG_INIT_DONE``, ``MSG_OK_REQ_DATA``, ``MSG_MAIN_DONE``,
    ``MSG_END_DONE`` against a 4-byte input file -- one MAIN-phase data
    round trip, so the recorded call list is always exactly
    ``[INIT, MAIN, MAIN, END]``.

    Returns ``(ok, calls)``.
    """
    from firestarter.chip_resolver import resolve_chip
    from firestarter.database import EpromDatabase
    from firestarter.eprom_operations import EpromOperator

    input_file = tmp_path / f"{_REAL_27C_CHIP}.bin"
    input_file.write_bytes(b"\x01\x02\x03\x04")

    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_OK_REQ_DATA, b""))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    def _fake_find_and_connect(command_dict, config, **kwargs):
        instance = make_comm()
        instance.write_block_budget_s = advertised_budget
        return instance

    db = EpromDatabase(skip_local_override=True)
    programmer_dict = resolve_chip(_REAL_27C_CHIP, db=db)

    calls: list = []
    operator = EpromOperator(ConfigManager())
    with (
        patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ),
        _recording_get_response_patch(calls),
    ):
        method = getattr(operator, method_name)
        ok = method(_REAL_27C_CHIP, programmer_dict, str(input_file))
    return ok, calls


def _drive_write_and_record_timeouts(
    tmp_path, make_comm, fake_serial, *, advertised_budget: int | None
):
    """``write_eprom``-only convenience wrapper over
    ``_drive_data_operation_and_record_timeouts`` -- see its docstring.
    """
    return _drive_data_operation_and_record_timeouts(
        tmp_path,
        make_comm,
        fake_serial,
        method_name="write_eprom",
        advertised_budget=advertised_budget,
    )


def _drive_simple_operation_and_record_timeouts(
    make_comm, fake_serial, *, operation: str
):
    """Drive ``check_eprom_blank`` / ``erase_eprom`` (both fall through to
    ``_main_phase_simple`` -- no ``main_phase_handler``, no data file, no
    ``MSG_OK_REQ_DATA``) against ``_REAL_27C_CHIP``, recording every
    ``get_response`` timeout. D-12's negative proof for the OTHER shared
    machinery ``write_eprom`` never touches at all.

    Feeds exactly ``MSG_INIT_DONE``, ``MSG_MAIN_DONE``, ``MSG_END_DONE`` --
    the recorded call list is always exactly ``[INIT, MAIN, END]``.

    Returns ``(ok, calls)``.
    """
    from firestarter.chip_resolver import resolve_chip
    from firestarter.database import EpromDatabase
    from firestarter.eprom_operations import EpromOperator

    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    def _fake_find_and_connect(command_dict, config, **kwargs):
        return make_comm()

    db = EpromDatabase(skip_local_override=True)
    programmer_dict = resolve_chip(_REAL_27C_CHIP, db=db)

    calls: list = []
    operator = EpromOperator(ConfigManager())
    with (
        patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ),
        _recording_get_response_patch(calls),
    ):
        method = getattr(operator, operation)
        ok = method(_REAL_27C_CHIP, programmer_dict)
    return ok, calls


# ---------------------------------------------------------------------------
# Test 1 -- HOST-01 / D-09: the advertised budget is used verbatim.
# ---------------------------------------------------------------------------


def test_write_uses_advertised_budget(tmp_path, make_comm, fake_serial) -> None:
    """HOST-01 / D-09: an advertised budget is used VERBATIM on the write
    path. A 250 s advertisement must produce a MAIN-phase ``get_response``
    timeout of exactly 250.0 -- never ``250 * anything`` and never the old
    10 s default -- because the firmware already padded the figure (its own
    ``delay(500)`` VPE settle, the final full-block verify pass and the
    per-pulse settle are folded in); the host must not multiply on top.
    """
    ok, calls = _drive_write_and_record_timeouts(
        tmp_path, make_comm, fake_serial, advertised_budget=250
    )
    assert ok is True, "HOST-01: the driven write must complete successfully"

    main_phase_timeouts = [_timeout_of(c) for c in _main_phase_calls(calls)]
    assert 250.0 in main_phase_timeouts, (
        "HOST-01/D-09: expected a MAIN-phase get_response call with "
        f"timeout=250.0 (the advertised budget, used verbatim); got "
        f"{main_phase_timeouts}"
    )
    assert DEFAULT_RESPONSE_TIMEOUT not in main_phase_timeouts, (
        "D-09: the write path must never fall back to DEFAULT_RESPONSE_TIMEOUT "
        f"when a budget was advertised; got {main_phase_timeouts}"
    )


# ---------------------------------------------------------------------------
# Test 2 -- HOST-01 / D-10: absent advertisement falls back to 120 s.
# ---------------------------------------------------------------------------


def test_absent_budget_falls_back_to_120s(tmp_path, make_comm, fake_serial) -> None:
    """HOST-01 / D-10: no advertisement (``write_block_budget_s`` stays
    ``None`` -- the realistic case is a released ``beta`` firmware with
    CAP-02 but not CAP-03, or a v1.31 build after 143-03's CAP-02 port but
    before its CAP-03 append; corrected from D-10's own wording, NOT a
    mid-milestone v1.31 build, which BF-1 shows cannot connect at all) must
    fall back to the derived ``WRITE_BLOCK_TIMEOUT_FALLBACK_S`` (120.0).
    Mirrors ``_calculate_buffer_size``'s precedent: Phase 54's
    ``FirmwareOutdatedError`` was reversed into exactly this shape -- absent
    means safe default, never an error and never a refusal.
    """
    ok, calls = _drive_write_and_record_timeouts(
        tmp_path, make_comm, fake_serial, advertised_budget=None
    )
    assert ok is True, "HOST-01: the driven write must complete successfully"

    main_phase_timeouts = [_timeout_of(c) for c in _main_phase_calls(calls)]
    assert all(t == 120.0 for t in main_phase_timeouts), (
        "D-10: an absent advertisement must fall back to exactly 120.0 on "
        f"every MAIN-phase call; got {main_phase_timeouts}"
    )


# ---------------------------------------------------------------------------
# Test 3 -- D-10: an implausible advertisement is clamped away too.
# ---------------------------------------------------------------------------


def test_implausible_budget_is_clamped_away(tmp_path, make_comm, fake_serial) -> None:
    """D-10: a value that never passed ``serial_comm``'s own
    ``[1, WRITE_BUDGET_MAX_S]`` decode-time plausibility clamp -- reachable
    only if something bypassed the decoder and wrote ``write_block_budget_s``
    directly -- must still fall back to 120.0. This is the consumer's OWN
    ``[1, WRITE_BUDGET_MAX_S]`` range test, a second line of defence behind
    the decoder's clamp: a corrupt or hostile ack must not be able to
    install either a too-tight (``0``) or an unbounded (``999999``) host
    wait. Two sub-cases, driven on fresh instances (a successful write closes
    its fake serial port, so the second drive needs its own
    ``(serial, comm)`` pair -- see ``_fresh_serial_and_comm``).
    """
    pairs = [(fake_serial, make_comm), _fresh_serial_and_comm()]
    for (serial, comm_factory), bad_budget in zip(pairs, (0, 999999)):
        ok, calls = _drive_write_and_record_timeouts(
            tmp_path, comm_factory, serial, advertised_budget=bad_budget
        )
        assert ok is True, "HOST-01: the driven write must complete successfully"

        main_phase_timeouts = [_timeout_of(c) for c in _main_phase_calls(calls)]
        assert all(t == 120.0 for t in main_phase_timeouts), (
            f"D-10: an implausible advertised budget ({bad_budget}) must "
            f"fall back to exactly 120.0 on every MAIN-phase call; got "
            f"{main_phase_timeouts}"
        )


# ---------------------------------------------------------------------------
# Test 4 -- D-12's negative proof (the most important test in this module).
# ---------------------------------------------------------------------------


def test_non_write_paths_keep_default_timeout(tmp_path, make_comm, fake_serial) -> None:
    """D-12's negative proof, and the most important test in this module.

    ``verify_eprom`` shares ``_main_phase_send_data`` with ``write_eprom`` --
    the RED-guaranteeing half of this test proves the SHARED function was
    actually exercised for verify (its MAIN-phase calls become EXPLICIT
    once ``response_timeout`` resolves inside that shared function, ``None``
    ``-> DEFAULT_RESPONSE_TIMEOUT``) rather than merely "unaffected because
    it happened to stay on some other, untouched code path" -- a weaker
    "argument-free-or-10" assertion cannot tell those two apart, and would
    already pass on today's pre-Task-2 code (bare calls everywhere) for the
    wrong reason.

    ``check_eprom_blank`` and ``erase_eprom`` exercise the OTHER shared
    machinery (``_main_phase_simple`` / ``_execute_phase``) that this plan
    never touches at all -- their calls must stay truly bare, both today and
    after Task 2 lands.

    A genuinely dead board must still report in ten seconds on every one of
    these -- inheriting the write path's multi-minute budget would turn a
    real hardware fault into a multi-minute hang on the most commonly run
    commands.
    """
    verify_ok, verify_calls = _drive_data_operation_and_record_timeouts(
        tmp_path, make_comm, fake_serial, method_name="verify_eprom"
    )
    assert verify_ok is True, "D-12: the driven verify must complete successfully"

    verify_main_timeouts = [_timeout_of(c) for c in _main_phase_calls(verify_calls)]
    assert all(t == DEFAULT_RESPONSE_TIMEOUT for t in verify_main_timeouts), (
        "D-12: verify_eprom's MAIN-phase get_response calls (shared "
        "_main_phase_send_data) must resolve to exactly "
        f"DEFAULT_RESPONSE_TIMEOUT ({DEFAULT_RESPONSE_TIMEOUT}) -- never bare "
        f"and never the write path's 120.0; got {verify_main_timeouts}"
    )

    blank_serial, blank_comm = _fresh_serial_and_comm()
    blank_ok, blank_calls = _drive_simple_operation_and_record_timeouts(
        blank_comm, blank_serial, operation="check_eprom_blank"
    )
    assert blank_ok is True, "D-12: the driven blank-check must complete successfully"

    erase_serial, erase_comm = _fresh_serial_and_comm()
    erase_ok, erase_calls = _drive_simple_operation_and_record_timeouts(
        erase_comm, erase_serial, operation="erase_eprom"
    )
    assert erase_ok is True, "D-12: the driven erase must complete successfully"

    for label, calls in (
        ("check_eprom_blank", blank_calls),
        ("erase_eprom", erase_calls),
    ):
        timeouts = [_timeout_of(c) for c in calls]
        assert all(t is None for t in timeouts), (
            f"D-12: {label}'s get_response calls must stay argument-free "
            "(bare get_response()) -- _main_phase_simple/_execute_phase are "
            f"untouched by this plan; got {timeouts}"
        )
        assert 120.0 not in timeouts, (
            f"D-12: {label} must never see the write-path's 120.0 fallback "
            f"timeout; got {timeouts}"
        )


# ---------------------------------------------------------------------------
# Test 5 -- D-13: a write's own INIT/END phases stay on the default too.
# ---------------------------------------------------------------------------


def test_init_and_end_phases_keep_default_timeout(
    tmp_path, make_comm, fake_serial
) -> None:
    """D-13: a write's INIT and END phases stay argument-free even though its
    MAIN phase now uses the advertised budget. The INIT phase of a write
    (erase plus chunked blank check) emits one ``MSG_DATA_PROGRESS`` per
    chunk and each yielded frame resets the read loop's ``start_time``
    (``firestarter/serial_comm.py``), so its 10 s window is fed by
    construction; the END phase for ``CMD_WRITE`` is a bare ack round trip
    because ``firestarter_operation_end`` is ``NULL`` for it. Threading the
    budget into ``_execute_phase`` would buy nothing and widen the blast
    radius -- this is why ``_execute_phase`` keeps its bare
    ``get_response()`` call untouched.

    The contrast half (MAIN *does* carry the advertised budget) is asserted
    in the SAME test and against the SAME drive -- this is also what makes
    the test genuinely fail before Task 2 lands: today nothing is threaded
    at all, so the MAIN calls would be argument-free too, and an INIT/END
    -only assertion would pass on the current code for the wrong reason.
    """
    ok, calls = _drive_write_and_record_timeouts(
        tmp_path, make_comm, fake_serial, advertised_budget=250
    )
    assert ok is True, "D-13: the driven write must complete successfully"
    assert len(calls) == 4, (
        f"expected [INIT, MAIN, MAIN, END]; got {len(calls)}: {calls}"
    )

    init_call, main_call_1, main_call_2, end_call = calls

    main_timeouts = [_timeout_of(main_call_1), _timeout_of(main_call_2)]
    assert 250.0 in main_timeouts, (
        "HOST-01: expected this write's own MAIN phase to carry the "
        f"advertised 250.0 s budget so this test can prove the CONTRAST "
        f"with INIT/END; got {main_timeouts}"
    )

    for label, call in (("INIT", init_call), ("END", end_call)):
        timeout = _timeout_of(call)
        assert timeout is None, (
            f"D-13: the write's {label}-phase get_response call must stay "
            "argument-free (bare get_response()) even though a 250 s budget "
            f"was advertised for the MAIN phase; got timeout={timeout}"
        )


# ---------------------------------------------------------------------------
# Test 6 -- Pitfall 6's fake-clock oracle.
# ---------------------------------------------------------------------------


def test_long_gap_within_budget_does_not_time_out(make_comm, fake_serial) -> None:
    """Pitfall 6 (`143-RESEARCH.md`): ``_FakeSerial.read()`` returns ``b""``
    immediately when its buffer is empty, and the read loop sleeps 1 ms per
    empty read without resetting ``start_time`` (``firestarter/serial_comm.py``)
    -- so a naive version of this test, one that actually waited out a real
    gap, would run for the full advertised budget (120 s, or thousands of
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
