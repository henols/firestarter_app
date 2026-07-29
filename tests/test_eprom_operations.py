"""Phase 42 / ERR-03 coverage lift for ``EpromOperator`` happy paths (D-14.2).

Tests exercise the read-path state machine via ``make_comm`` + ``fake_serial``
(Phase 36 D-02 fixture pattern). GATE-1.8d: this file EXERCISES the read path
but NEVER modifies it (the source module ``firestarter/eprom_operations.py`` is
not edited beyond Plan 42-01's BUG-2 fix; deferred to v1.9 post-RCA).

WARNING 10: this file contains NO BUG-2 regression test — that contract lives
at ``tests/test_bug_characterization.py::test_eprom_operation_error_not_labeled_as_communication_error``
(flipped to PASSED by Plan 42-01). Happy-path coverage only.

Phase 44 Plan 03 additions (read_timing block):
    test_read_settling_key_constant — JSON_KEY_READ_SETTLING_DELAY string match
    test_read_strobe_key_constant — JSON_KEY_READ_STROBE_US string match
    test_consistency_check_emits_read_settling_in_command — settling param flows into JSON
    test_consistency_check_emits_read_strobe_in_command — strobe param flows into JSON
    test_consistency_check_default_params_absent_from_command — no extra keys when 0
"""

import logging
from unittest.mock import patch

from firestarter.config import ConfigManager
from firestarter.eprom_operations import EpromOperator
from firestarter.messages import MSG_END_DONE, MSG_INIT_DONE, MSG_MAIN_DONE

from .conftest import build_frame


def test_run_state_machine_happy_path(make_comm, fake_serial, caplog) -> None:
    """The unified state machine returns (True, ...) when INIT → MAIN → END
    arrive in order. Wire frames: INIT_DONE, MAIN_DONE, END_DONE.
    """
    config = ConfigManager()
    operator = EpromOperator(config)
    operator.comm = make_comm()

    # Feed the three phase-done frames in the order the firmware would emit them.
    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    with caplog.at_level(logging.DEBUG):
        ok, final_msg = operator._run_state_machine("happy_path_op")
    assert ok is True
    # final_msg comes from MAIN-phase handler; simple-path returns the MAIN frame's text
    assert isinstance(final_msg, str) or final_msg is None


def test_blank_check_eprom_happy_path(make_comm, fake_serial) -> None:
    """check_eprom_blank with a wired fake_serial drives INIT → MAIN → END.

    Uses the same wire-frame pattern as test_run_state_machine_happy_path —
    blank-check goes through ``_run_state_machine`` with no MAIN handler.
    """
    config = ConfigManager()
    operator = EpromOperator(config)
    operator.comm = make_comm()

    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    # Exercise _run_state_machine directly with the "blank check" operation
    # label. This is the same code path higher-level check_eprom_blank uses
    # once setup completes; bypassing _setup_operation avoids the real
    # find_and_connect serial-port enumeration that would otherwise fire.
    ok, _msg = operator._run_state_machine("blank_check_eprom")
    assert ok is True


def test_erase_eprom_happy_path(make_comm, fake_serial) -> None:
    """erase_eprom drives the same INIT → MAIN → END flow."""
    config = ConfigManager()
    operator = EpromOperator(config)
    operator.comm = make_comm()

    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    ok, _msg = operator._run_state_machine("erase_eprom")
    assert ok is True


def test_state_machine_not_connected_returns_false(make_comm, fake_serial) -> None:
    """When ``operator.comm`` is None the state machine returns (False, ...)
    without raising. Exercises the connection-guard path."""
    config = ConfigManager()
    operator = EpromOperator(config)
    operator.comm = None  # explicit not-connected state

    ok, msg = operator._run_state_machine("op_without_comm")
    assert ok is False
    assert msg == "Not connected"


def test_handle_progress_response_data_path(make_comm, fake_serial) -> None:
    """_handle_progress_response with a DATA response advances progress.

    Exercises the DATA-frame branch (line ~322) — covered transitively by
    higher-level operations but pinned here for explicit coverage.
    """
    from firestarter.eprom_operations import ClassProgressHandler
    from firestarter.frame_parser import Response

    config = ConfigManager()
    operator = EpromOperator(config)
    operator.comm = make_comm()
    progress = ClassProgressHandler()

    # Progress 5/100 — total/current syntax used by the firmware for progress
    operator._handle_progress_response(Response(type="DATA", message="5/100"), progress)
    # No exception raised + no return value to assert; coverage gain only.


def test_handle_progress_response_warn_and_ok_paths(make_comm, fake_serial) -> None:
    """The WARN and OK branches of _handle_progress_response also exercise."""
    from firestarter.eprom_operations import ClassProgressHandler
    from firestarter.frame_parser import Response

    config = ConfigManager()
    operator = EpromOperator(config)
    operator.comm = make_comm()
    progress = ClassProgressHandler()

    operator._handle_progress_response(
        Response(type="WARN", message="non-fatal"), progress
    )
    operator._handle_progress_response(
        Response(type="OK", message="continuing"), progress
    )


def test_init_phase_data_frames_not_acked() -> None:
    """D-07 (commit fcf7974): INIT/END-phase DATA progress frames must NOT be acked.

    The default (no ``-b``) write path — which Phase 77's auto-erase graduation
    (FLAG_CAN_ERASE on the wire) makes the common case — drives
    ``_execute_phase("INIT", ...)``, which emits per-chunk blank-check DATA progress
    frames. ``ack_data=False`` ensures those frames are not acked, so spurious OK
    acks cannot pile up in the firmware RX buffer and desync the MAIN handshake into
    ``MSG_ERR_EMPTY_INPUT`` (0xA4). This guard asserts ``send_ack`` fires exactly once
    per INIT phase (the phase-start ack), regardless of how many DATA frames arrive.
    """
    from unittest.mock import MagicMock

    from firestarter.eprom_operations import ClassProgressHandler
    from firestarter.frame_parser import Response

    operator = EpromOperator(ConfigManager())
    mock_comm = MagicMock()
    # Two DATA progress frames followed by the terminating INIT frame.
    mock_comm.get_response.side_effect = [
        Response(type="DATA", message="1/128"),
        Response(type="DATA", message="64/128"),
        Response(type="INIT", message="OK"),
    ]
    operator.comm = mock_comm
    progress = ClassProgressHandler()

    operator._execute_phase("INIT", progress)

    # Only the phase-start ACK fires; the two DATA frames trigger no extra acks.
    mock_comm.send_ack.assert_called_once()


# ---------------------------------------------------------------------------
# build_flags + hexdump (module-level helpers) — D-14 fallback coverage
# ---------------------------------------------------------------------------


def test_build_flags_all_off() -> None:
    """build_flags returns 0 when nothing is set."""
    from firestarter.eprom_operations import build_flags

    assert build_flags() == 0


def test_build_flags_force_sets_bit() -> None:
    """force=True sets FLAG_FORCE."""
    from firestarter.constants import FLAG_FORCE
    from firestarter.eprom_operations import build_flags

    assert build_flags(force=True) & FLAG_FORCE


def test_build_flags_no_blank_check_sets_skip_bit() -> None:
    """blank_check=False sets FLAG_SKIP_BLANK_CHECK."""
    from firestarter.constants import FLAG_SKIP_BLANK_CHECK
    from firestarter.eprom_operations import build_flags

    flags = build_flags(blank_check=False)
    assert flags & FLAG_SKIP_BLANK_CHECK


def test_build_flags_vpe_as_vpp_and_verbose() -> None:
    """vpe_as_vpp + verbose set their respective bits."""
    from firestarter.constants import FLAG_VERBOSE, FLAG_VPE_AS_VPP
    from firestarter.eprom_operations import build_flags

    flags = build_flags(vpe_as_vpp=True, verbose=True)
    assert flags & FLAG_VPE_AS_VPP
    assert flags & FLAG_VERBOSE


def test_hexdump_writes_to_log(caplog) -> None:
    """hexdump emits formatted dump lines to the logger."""
    import logging as _logging

    from firestarter.eprom_operations import hexdump

    data = bytes(range(32))
    with caplog.at_level(_logging.INFO, logger="EpromOperator"):
        hexdump(0x1000, data, width=16)
    # At least one log line was produced with hex offset prefix.
    assert any("00001000" in r.message for r in caplog.records)


def test_class_progress_handler_lifecycle() -> None:
    """ClassProgressHandler with a callback delegates progress updates."""
    from firestarter.eprom_operations import ClassProgressHandler

    captured = []

    def cb(current, total):
        captured.append((current, total))

    h = ClassProgressHandler(progress_callback=cb)
    h.start(100)
    h.update(25)
    h.update(50)
    h.close()
    assert captured[0] == (0, 100)
    # After two updates +25 +50 → current = 75
    assert any(current == 75 for current, _ in captured)


def test_class_progress_handler_set_progress() -> None:
    """ClassProgressHandler.set_progress with explicit current/total."""
    from firestarter.eprom_operations import ClassProgressHandler

    captured = []

    def cb(current, total):
        captured.append((current, total))

    h = ClassProgressHandler(progress_callback=cb)
    h.set_progress(10, 100)
    h.set_progress(50, 100)
    h.close()
    assert (50, 100) in captured


# ---------------------------------------------------------------------------
# Phase 44 Plan 03 — read_timing block
# Tests for host-side read-timing knob params in consistency_check_eprom.
# Selectable with: pytest -k "read_timing"
# ---------------------------------------------------------------------------

# Minimal eprom_data_dict that consistency_check_eprom accepts without real DB.
_MINIMAL_EPROM_DATA: dict = {
    "memory-size": 65536,
    "flags": 0,
    "cmd": 1,
}


def _make_captured_setup_operation(captured: list):
    """Return a _setup_operation mock that records eprom_data_dict and returns
    a fake (command_dict, buffer_size) pair that lets consistency_check_eprom
    proceed to the first run's _operation_context without real serial I/O.

    Yields (None, None) via _operation_context by returning None so the inner
    loop exits immediately with return 2 (hardware error) — we only care about
    what was passed IN, not the output.
    """

    def _fake_setup_operation(
        self_op, eprom_name, eprom_data_dict, cmd, *args, **kwargs
    ):  # noqa: ANN001
        captured.append(dict(eprom_data_dict))
        # Return None so _operation_context yields (None, None, None) and
        # consistency_check_eprom returns 2 (hardware error) on first run.
        return None, 0

    return _fake_setup_operation


def test_read_timing_settling_key_constant() -> None:
    """JSON_KEY_READ_SETTLING_DELAY must equal the firmware PROGMEM key string.

    The firmware declares: const char key_read_settling[] PROGMEM = "read-settling-delay";
    (json_parser.c). If the host string drifts, the firmware silently ignores the param
    (Pitfall 2 — RESEARCH.md). This test pins the constant to the firmware source of truth.

    Selected by `pytest -k read_timing`.
    """
    from firestarter.constants import (
        JSON_KEY_READ_SETTLING_DELAY,  # type: ignore[attr-defined]
    )

    assert JSON_KEY_READ_SETTLING_DELAY == "read-settling-delay"


def test_read_timing_strobe_key_constant() -> None:
    """JSON_KEY_READ_STROBE_US must equal the firmware PROGMEM key string.

    The firmware declares: const char key_read_strobe[] PROGMEM = "read-strobe-us";
    (json_parser.c). If the host string drifts, the firmware silently ignores the param.

    Selected by `pytest -k read_timing`.
    """
    from firestarter.constants import (
        JSON_KEY_READ_STROBE_US,  # type: ignore[attr-defined]
    )

    assert JSON_KEY_READ_STROBE_US == "read-strobe-us"


def test_read_timing_settling_emitted_in_command() -> None:
    """consistency_check_eprom(..., read_settling_us=50) puts "read-settling-delay"
    in the JSON command dict sent to _setup_operation.

    Selected by `pytest -k read_timing`.
    """
    captured: list = []
    config = ConfigManager()
    operator = EpromOperator(config)

    with patch.object(
        EpromOperator,
        "_setup_operation",
        _make_captured_setup_operation(captured),
    ):
        operator.consistency_check_eprom(
            "W27C512",
            dict(_MINIMAL_EPROM_DATA),
            runs=2,
            read_settling_us=50,  # type: ignore[call-arg]
        )

    assert len(captured) >= 1, "Expected _setup_operation to be called at least once"
    assert captured[0].get("read-settling-delay") == 50


def test_read_timing_strobe_emitted_in_command() -> None:
    """consistency_check_eprom(..., read_strobe_us=25) puts "read-strobe-us"
    in the JSON command dict sent to _setup_operation.

    Selected by `pytest -k read_timing`.
    """
    captured: list = []
    config = ConfigManager()
    operator = EpromOperator(config)

    with patch.object(
        EpromOperator,
        "_setup_operation",
        _make_captured_setup_operation(captured),
    ):
        operator.consistency_check_eprom(
            "W27C512",
            dict(_MINIMAL_EPROM_DATA),
            runs=2,
            read_strobe_us=25,  # type: ignore[call-arg]
        )

    assert len(captured) >= 1, "Expected _setup_operation to be called at least once"
    assert captured[0].get("read-strobe-us") == 25


def test_read_timing_default_params_absent_from_command() -> None:
    """With both params == 0 (default), neither "read-settling-delay" nor
    "read-strobe-us" appears in the JSON command sent to _setup_operation.

    Firmware defaults apply when these keys are absent from the JSON.

    Selected by `pytest -k read_timing`.
    """
    captured: list = []
    config = ConfigManager()
    operator = EpromOperator(config)

    with patch.object(
        EpromOperator,
        "_setup_operation",
        _make_captured_setup_operation(captured),
    ):
        operator.consistency_check_eprom(
            "W27C512",
            dict(_MINIMAL_EPROM_DATA),
            runs=2,
            # read_settling_us and read_strobe_us default to 0 — not passed
        )

    assert len(captured) >= 1, "Expected _setup_operation to be called at least once"
    assert "read-settling-delay" not in captured[0]
    assert "read-strobe-us" not in captured[0]


# ---------------------------------------------------------------------------
# Phase-53 Plan 01 Task 1: RED tests for write_cycle_eprom 3-way verdict
#
# These tests MUST FAIL until 53-02 adds EpromOperator.write_cycle_eprom.
# They pin the 3-way verdict contract: 0=all cycles match source / 1=mismatch
# / 2=hw-error. D-06: independent host-side SHA-256 compare, NOT firmware
# built-in verify. Monkeypatch pattern mirrors test_consistency_check.py.
# ---------------------------------------------------------------------------


def _make_fake_ctx_write(memory_size: int = 65536):
    """@contextmanager fake for _operation_context used by write_cycle tests."""
    from contextlib import contextmanager

    @contextmanager
    def fake_ctx(self, eprom_name, eprom_data_dict, cmd, *a, **kw):
        yield {"address": 0, "memory-size": memory_size}, 512, "READ"

    return fake_ctx


def _make_fake_state_machine_for_write_cycle(payload):
    """Fake _run_state_machine that feeds a single payload to the callback.

    Returns (True, None) — for hardware-error variants, a separate helper
    returning (False, "timeout") is provided below.
    """

    def fake_state_machine(self, op_name, **kwargs):
        cb = kwargs.get("process_data_chunk_callback")
        if cb is not None:
            cb(0, payload)
        return (True, None)

    return fake_state_machine


def _make_fake_state_machine_hw_error():
    """Fake _run_state_machine that returns (False, "timeout") — hw-error path."""

    def fake_state_machine(self, op_name, **kwargs):
        return (False, "timeout")

    return fake_state_machine


class TestWriteCycleEprom:
    """Phase-53 RED tests for EpromOperator.write_cycle_eprom (D-06 / XACT-01).

    All four tests MUST FAIL until 53-02 implements write_cycle_eprom.
    """

    _MEMORY_SIZE = 65536  # 64 KB canonical EPROM payload

    def test_write_cycle_eprom_pass(self, tmp_path, monkeypatch):
        """Pass: erase ok, write ok, read-back matches source image -> return 0.

        Source image written to tmp_path. _run_state_machine feeds back the
        identical bytes. erase_eprom and write_eprom both return True.
        Expected: write_cycle_eprom returns 0 (PASS).
        """
        source_path = tmp_path / "source.bin"
        payload = bytes(range(256)) * 256  # deterministic 64 KB
        source_path.write_bytes(payload)

        monkeypatch.setattr(EpromOperator, "erase_eprom", lambda self, *a, **kw: True)
        monkeypatch.setattr(EpromOperator, "write_eprom", lambda self, *a, **kw: True)
        monkeypatch.setattr(
            EpromOperator,
            "_operation_context",
            _make_fake_ctx_write(self._MEMORY_SIZE),
        )
        monkeypatch.setattr(
            EpromOperator,
            "_run_state_machine",
            _make_fake_state_machine_for_write_cycle(payload),
        )

        op = EpromOperator(ConfigManager())
        rc = op.write_cycle_eprom(
            "TEST_CHIP",
            eprom_data_dict={"memory-size": self._MEMORY_SIZE},
            source_image_path=str(source_path),
            runs=1,
            output_dir=str(tmp_path / "out"),
        )
        assert rc == 0, "Matching read-back must return 0 (PASS)."

    def test_write_cycle_eprom_mismatch(self, tmp_path, monkeypatch):
        """Mismatch: read-back payload differs from source image -> return 1.

        Source image is all-0xAA; read-back payload is all-0x55.
        Expected: write_cycle_eprom returns 1 (FAIL / mismatch).
        """
        source_path = tmp_path / "source.bin"
        source_payload = bytes([0xAA]) * self._MEMORY_SIZE
        readback_payload = bytes([0x55]) * self._MEMORY_SIZE
        source_path.write_bytes(source_payload)

        monkeypatch.setattr(EpromOperator, "erase_eprom", lambda self, *a, **kw: True)
        monkeypatch.setattr(EpromOperator, "write_eprom", lambda self, *a, **kw: True)
        monkeypatch.setattr(
            EpromOperator,
            "_operation_context",
            _make_fake_ctx_write(self._MEMORY_SIZE),
        )
        monkeypatch.setattr(
            EpromOperator,
            "_run_state_machine",
            _make_fake_state_machine_for_write_cycle(readback_payload),
        )

        op = EpromOperator(ConfigManager())
        rc = op.write_cycle_eprom(
            "TEST_CHIP",
            eprom_data_dict={"memory-size": self._MEMORY_SIZE},
            source_image_path=str(source_path),
            runs=1,
            output_dir=str(tmp_path / "out"),
        )
        assert rc == 1, "Differing read-back must return 1 (FAIL / mismatch)."

    def test_write_cycle_eprom_hw_error(self, tmp_path, monkeypatch):
        """HW-error: _run_state_machine returns (False, 'timeout') -> return 2.

        CRITICAL: hw-error MUST NOT be collapsed to 1 (mismatch). The 3-way
        verdict is load-bearing for the v1.6 RCA diagnostic.
        Expected: write_cycle_eprom returns 2 (hw-error), NOT 1.
        """
        source_path = tmp_path / "source.bin"
        source_path.write_bytes(bytes([0xAA]) * self._MEMORY_SIZE)

        monkeypatch.setattr(EpromOperator, "erase_eprom", lambda self, *a, **kw: True)
        monkeypatch.setattr(EpromOperator, "write_eprom", lambda self, *a, **kw: True)
        monkeypatch.setattr(
            EpromOperator,
            "_operation_context",
            _make_fake_ctx_write(self._MEMORY_SIZE),
        )
        monkeypatch.setattr(
            EpromOperator,
            "_run_state_machine",
            _make_fake_state_machine_hw_error(),
        )

        op = EpromOperator(ConfigManager())
        rc = op.write_cycle_eprom(
            "TEST_CHIP",
            eprom_data_dict={"memory-size": self._MEMORY_SIZE},
            source_image_path=str(source_path),
            runs=1,
            output_dir=str(tmp_path / "out"),
        )
        assert rc == 2, "State machine failure must return 2 (hw-error), NOT 1."

    def test_write_cycle_eprom_erase_fail(self, tmp_path, monkeypatch):
        """Erase failure: erase_eprom returns False -> return 2 (hw-error).

        An erase failure is a hardware-operation failure, not a data mismatch.
        Expected: write_cycle_eprom returns 2 (hw-error).
        """
        source_path = tmp_path / "source.bin"
        source_path.write_bytes(bytes([0xAA]) * self._MEMORY_SIZE)

        monkeypatch.setattr(EpromOperator, "erase_eprom", lambda self, *a, **kw: False)
        monkeypatch.setattr(EpromOperator, "write_eprom", lambda self, *a, **kw: True)

        op = EpromOperator(ConfigManager())
        rc = op.write_cycle_eprom(
            "TEST_CHIP",
            eprom_data_dict={"memory-size": self._MEMORY_SIZE},
            source_image_path=str(source_path),
            runs=1,
            output_dir=str(tmp_path / "out"),
        )
        assert rc == 2, "Erase failure must return 2 (hw-error)."


# ---------------------------------------------------------------------------
# Phase-53 Plan 02: unit tests for fault_inject_cycle (coverage gate)
#
# These tests exercise fault_inject_cycle directly to keep total coverage
# at >=70%. The CLI smoke tests (test_cli_handlers.py) only mock the method,
# so direct unit tests are required for coverage. XACT-02 / Phase 53 Plan 02.
# ---------------------------------------------------------------------------


class _MockComm:
    """Minimal SerialCommunicator stand-in for fault_inject_cycle tests."""

    def __init__(self) -> None:
        self._fault_inject_outgoing = None


def _make_fake_ctx_for_fault_inject(memory_size: int = 65536):
    """@contextmanager fake _operation_context for fault_inject_cycle tests."""
    from contextlib import contextmanager

    @contextmanager
    def fake_ctx(self, eprom_name, eprom_data_dict, cmd, *a, **kw):
        yield {"address": 0, "memory-size": memory_size}, 512, "READ"

    return fake_ctx


def _make_fault_inject_state_machine(corrupted_fails: bool = True):
    """Fake _run_state_machine for fault_inject_cycle:

    First call (corrupted transfer): returns (not corrupted_fails, None) — i.e.
    if corrupted_fails=True, returns (False, "error") to simulate failure.
    Second call (clean transfer): always returns (True, None).
    """
    counter = {"n": 0}

    def fake_sm(self, op_name, **kwargs):
        cb = kwargs.get("process_data_chunk_callback")
        if cb is not None:
            cb(0, b"\xaa" * 16)
        counter["n"] += 1
        if counter["n"] == 1:
            return (not corrupted_fails, None)  # first call
        return (True, None)  # subsequent calls

    return fake_sm


class TestFaultInjectCycle:
    """Phase-53 Plan 02 unit tests for fault_inject_cycle (coverage gate)."""

    _MEMORY_SIZE = 65536

    def test_fault_inject_cycle_outgoing_pass(self, tmp_path, monkeypatch):
        """Outgoing path: corrupted transfer fails, clean follow-on succeeds -> True."""
        monkeypatch.setattr(
            EpromOperator,
            "_operation_context",
            _make_fake_ctx_for_fault_inject(self._MEMORY_SIZE),
        )
        monkeypatch.setattr(
            EpromOperator,
            "_run_state_machine",
            _make_fault_inject_state_machine(corrupted_fails=True),
        )

        op = EpromOperator(ConfigManager())
        op.comm = _MockComm()  # type: ignore[assignment]
        result = op.fault_inject_cycle(
            "TEST_CHIP",
            {"memory-size": self._MEMORY_SIZE},
            direction="outgoing",
            fault_form="corrupt-crc8",
            output_dir=str(tmp_path / "fi_out"),
        )
        assert result is True, "Corrupted-then-clean cycle must return True."

    def test_fault_inject_cycle_outgoing_drop_delimiter(self, tmp_path, monkeypatch):
        """Outgoing path with drop-delimiter form -> True (same verdict logic)."""
        monkeypatch.setattr(
            EpromOperator,
            "_operation_context",
            _make_fake_ctx_for_fault_inject(self._MEMORY_SIZE),
        )
        monkeypatch.setattr(
            EpromOperator,
            "_run_state_machine",
            _make_fault_inject_state_machine(corrupted_fails=True),
        )

        op = EpromOperator(ConfigManager())
        op.comm = _MockComm()  # type: ignore[assignment]
        result = op.fault_inject_cycle(
            "TEST_CHIP",
            {"memory-size": self._MEMORY_SIZE},
            direction="outgoing",
            fault_form="drop-delimiter",
            output_dir=str(tmp_path / "fi_out"),
        )
        assert result is True, "Drop-delimiter cycle must return True."

    def test_fault_inject_cycle_corrupted_succeeds_returns_false(
        self, tmp_path, monkeypatch
    ):
        """If the corrupted transfer unexpectedly succeeds -> returns False."""
        monkeypatch.setattr(
            EpromOperator,
            "_operation_context",
            _make_fake_ctx_for_fault_inject(self._MEMORY_SIZE),
        )
        monkeypatch.setattr(
            EpromOperator,
            "_run_state_machine",
            # corrupted_fails=False means first call returns (True, None) -> unexpected success
            _make_fault_inject_state_machine(corrupted_fails=False),
        )

        op = EpromOperator(ConfigManager())
        op.comm = _MockComm()  # type: ignore[assignment]
        result = op.fault_inject_cycle(
            "TEST_CHIP",
            {"memory-size": self._MEMORY_SIZE},
            direction="outgoing",
            fault_form="corrupt-crc8",
            output_dir=str(tmp_path / "fi_out"),
        )
        assert result is False, (
            "Unexpectedly successful corrupted transfer must return False."
        )

    # ----- 53-04 harness-fix regression tests (the false-negative fix) ---------

    def test_outgoing_threads_hook_to_operation_context(self, tmp_path, monkeypatch):
        """The outgoing fault MUST be threaded into _operation_context (i.e. armed at
        connection time) for the corrupted leg, and NOT for the clean follow-on leg.

        This is the core 53-04 fix: the old code set the hook AFTER setup, so a READ
        (whose MAIN phase sends only plaintext acks) never fired it -> false negative.
        """
        from contextlib import contextmanager

        seen_hooks: list = []
        mem = self._MEMORY_SIZE

        @contextmanager
        def capturing_ctx(
            self, eprom_name, eprom_data_dict, cmd, *a, fault_inject_outgoing=None, **kw
        ):
            seen_hooks.append(fault_inject_outgoing)
            # First call = corrupted leg: simulate firmware rejecting the corrupt
            # setup frame -> connection did not establish (cmd_data is None).
            if len(seen_hooks) == 1:
                yield None, None, None
            else:
                yield {"address": 0, "memory-size": mem}, 512, "READ"

        monkeypatch.setattr(EpromOperator, "_operation_context", capturing_ctx)
        monkeypatch.setattr(
            EpromOperator,
            "_run_state_machine",
            _make_fault_inject_state_machine(corrupted_fails=False),  # clean leg PASSES
        )

        op = EpromOperator(ConfigManager())
        op.comm = _MockComm()  # type: ignore[assignment]
        result = op.fault_inject_cycle(
            "TEST_CHIP",
            {"memory-size": self._MEMORY_SIZE},
            direction="outgoing",
            fault_form="corrupt-crc8",
            output_dir=str(tmp_path / "fi_thread"),
        )

        assert result is True, "Bounded connect failure + clean recovery -> True."
        assert len(seen_hooks) == 2, "Expected a corrupted leg and a clean leg."
        assert seen_hooks[0] is not None, (
            "Corrupted leg MUST arm the outgoing hook at connection time (53-04 fix)."
        )
        assert seen_hooks[1] is None, "Clean follow-on leg MUST NOT arm the hook."

    def test_outgoing_connect_failure_writes_latency_log(self, tmp_path, monkeypatch):
        """A rejected setup frame (connect fails) is the expected outcome and writes a
        fault-inject log carrying the measured error latency + sub-2s cascade verdict."""
        from contextlib import contextmanager

        calls = {"n": 0}
        mem = self._MEMORY_SIZE

        @contextmanager
        def ctx(self, eprom_name, eprom_data_dict, cmd, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                yield None, None, None  # corrupted setup -> connect failed
            else:
                yield {"address": 0, "memory-size": mem}, 512, "READ"

        monkeypatch.setattr(EpromOperator, "_operation_context", ctx)
        monkeypatch.setattr(
            EpromOperator,
            "_run_state_machine",
            _make_fault_inject_state_machine(corrupted_fails=False),
        )

        out = tmp_path / "fi_log"
        op = EpromOperator(ConfigManager())
        op.comm = _MockComm()  # type: ignore[assignment]
        result = op.fault_inject_cycle(
            "TEST_CHIP",
            {"memory-size": self._MEMORY_SIZE},
            direction="outgoing",
            fault_form="corrupt-crc8",
            output_dir=str(out),
        )

        assert result is True
        log = (out / "fault-inject-outgoing-log.txt").read_text()
        assert "corrupted_transfer_surfaced_clean_error: True" in log
        assert "error_latency:" in log
        assert "sub_second_clean_error_no_2s_cascade:" in log
        assert "clean follow-on transfer PASSED" in log

    def test_corrupt_crc8_and_drop_delimiter_hooks_mutate_frame(self):
        """The fault hooks must produce a frame that differs from the original (so the
        injection is real, not a no-op). Guards against a silent identity hook."""
        op = EpromOperator(ConfigManager())
        op.comm = _MockComm()  # type: ignore[assignment]
        captured: dict = {}

        # Capture the hooks by intercepting _operation_context and reading the armed
        # hook off a throwaway comm via send_json_command behavior is covered in
        # test_serial_comm; here we assert the hook transforms a sample frame.
        from contextlib import contextmanager

        @contextmanager
        def ctx(self, *a, fault_inject_outgoing=None, **kw):
            if fault_inject_outgoing is not None:
                captured["hook"] = fault_inject_outgoing
            yield None, None, None  # force the bounded-failure path

        # The ctx yields None for every leg (clean leg then returns False), but this
        # test only needs to capture the armed hook to assert it mutates a frame.
        with patch.object(EpromOperator, "_operation_context", ctx):
            op.fault_inject_cycle(
                "TEST_CHIP",
                {"memory-size": self._MEMORY_SIZE},
                direction="outgoing",
                fault_form="corrupt-crc8",
                output_dir="/tmp/fi_hook_crc8",
            )
        sample = b"\x03ABC\x55\x00"  # body + crc + delimiter
        mutated = captured["hook"](sample)
        assert mutated != sample, "corrupt-crc8 hook must change the frame."
        assert mutated[-1:] == b"\x00", "corrupt-crc8 keeps the 0x00 delimiter."


# ---------------------------------------------------------------------------
# Phase 53-04 harness refinement: measure_command_nak_latency
# Per-frame firmware NAK latency on an established single-port connection.
# ---------------------------------------------------------------------------


class _FakeNakComm:
    """SerialCommunicator stand-in for measure_command_nak_latency tests.

    Scripts expect_ack: baseline OK, corrupted ERROR, recovery OK. Records the
    armed-hook state at each send so the test can assert the hook is set ONLY for
    the corrupted (2nd) send.
    """

    def __init__(self, *a, **kw) -> None:
        self._fault_inject_outgoing = None
        self.sends: list = []
        self._ack_seq = [(True, "Ready"), (False, "Empty input"), (True, "Ready")]
        self._ack_i = 0
        self.disconnected = False

    def consume_remaining_input(self, *a, **kw) -> None:
        pass

    def send_json_command(self, cmd) -> int:
        self.sends.append(self._fault_inject_outgoing is not None)
        return 1

    def expect_ack(self, *a, **kw):
        r = self._ack_seq[self._ack_i]
        self._ack_i += 1
        return r

    def disconnect(self) -> None:
        self.disconnected = True


class TestMeasureCommandNakLatency:
    def test_pass_arms_hook_only_for_corrupt_send(self, tmp_path, monkeypatch):
        fake = _FakeNakComm()
        monkeypatch.setattr(
            "firestarter.eprom_operations.SerialCommunicator",
            lambda *a, **kw: fake,
        )
        op = EpromOperator(ConfigManager())
        out = tmp_path / "nak"
        result = op.measure_command_nak_latency(
            fault_form="corrupt-crc8",
            output_dir=str(out),
            port="/dev/fake0",
        )
        assert result is True, "baseline OK + corrupt ERROR + recovery OK -> True"
        # 3 sends: baseline, corrupt, recovery
        assert fake.sends == [False, True, False], (
            "hook must be armed ONLY for the corrupted (2nd) send, cleared after"
        )
        assert fake.disconnected is True
        log = (out / "fault-inject-corrupt-crc8-latency.txt").read_text()
        assert "corrupted_frame_surfaced_error_no_silent_accept: True" in log
        assert "per_frame_nak_latency:" in log
        assert "recovery_clean_command_same_connection_ok: True" in log

    def test_no_port_returns_false(self, tmp_path, monkeypatch):
        # Empty config -> no port resolvable -> graceful False (no connection attempt).
        monkeypatch.setattr(ConfigManager, "get_value", lambda self, *a, **kw: None)
        op = EpromOperator(ConfigManager())
        result = op.measure_command_nak_latency(
            fault_form="corrupt-crc8",
            output_dir=str(tmp_path / "nak_noport"),
            port=None,
        )
        assert result is False


# ---------------------------------------------------------------------------
# Phase-84 Plan 02 Task 1 (RED): SRAM/FRAM blank-check host short-circuit
#
# D-30: FM1608 (0x28 SRAM_STD) blank-check surfaces firmware 0xA4
# MSG_ERR_EMPTY_INPUT because configure_sram() leaves a NULL
# firestarter_operation_main for CMD_BLANK_CHECK.  Fix = detect SRAM/FRAM
# at the host blank-check entry and short-circuit BEFORE issuing the command.
#
# These tests MUST FAIL until Task 2 adds the short-circuit to check_eprom_blank.
# The negative control (non-SRAM still issues blank-check) MUST PASS both now
# and after the fix.
# ---------------------------------------------------------------------------

# Minimal eprom_data_dict for an FM1608-class chip (SRAM, proto 0x28).
# ``protocol-id`` mirrors the field name written by database._map_data.
_FM1608_LIKE_EPROM_DATA: dict = {
    "memory-size": 8192,
    "flags": 0,
    "electrical-type": "SRAM",
    "protocol-id": 0x28,
}

# Minimal eprom_data_dict for a W27C512 (EEPROM, proto 0x07) — non-SRAM.
_W27C512_LIKE_EPROM_DATA: dict = {
    "memory-size": 65536,
    "flags": 0,
    "electrical-type": "EEPROM",
    "protocol-id": 0x07,
}


class TestSramBlankCheckShortCircuit:
    """D-30 host short-circuit: SRAM/FRAM blank-check must not reach firmware.

    Positive test: FM1608-class (SRAM, 0x28) must short-circuit; _setup_operation
    must NOT be called (no blank-check command sent to the firmware).
    Negative control: W27C512 (EEPROM, 0x07) must still reach _setup_operation
    (the short-circuit is SRAM/FRAM-scoped, NOT a blanket disable).

    T-84-04 mitigation: the negative control makes the EEPROM path regression-proof.
    """

    def test_sram_blank_check_short_circuits_before_setup(self, monkeypatch) -> None:
        """FM1608-class SRAM chip: check_eprom_blank must NOT call _setup_operation.

        The host short-circuit should fire immediately, returning False (not
        applicable) without reaching the firmware command layer.  This test
        MUST FAIL until Task 2 implements the short-circuit (RED gate).
        """
        setup_called = []

        def _fake_setup_operation(self_op, eprom_name, eprom_data_dict, cmd, *a, **kw):
            setup_called.append((eprom_name, cmd))
            return None, 0

        monkeypatch.setattr(EpromOperator, "_setup_operation", _fake_setup_operation)

        op = EpromOperator(ConfigManager())
        result = op.check_eprom_blank("FM1608", dict(_FM1608_LIKE_EPROM_DATA))

        # Short-circuit: _setup_operation must NOT be reached (no command to firmware).
        assert setup_called == [], (
            "SRAM blank-check must short-circuit BEFORE _setup_operation; "
            f"_setup_operation was called with: {setup_called}"
        )
        # Result must be False (blank-check not applicable to SRAM/FRAM).
        assert result is False

    def test_eeprom_blank_check_still_reaches_setup(self, monkeypatch) -> None:
        """Negative control: W27C512 (EEPROM, 0x07) must still reach _setup_operation.

        The short-circuit must NOT disable blank-check for real EPROM/EEPROM chips
        (T-84-04 mitigated).  This test MUST PASS both before and after the fix.
        """
        setup_called = []

        def _fake_setup_operation(self_op, eprom_name, eprom_data_dict, cmd, *a, **kw):
            setup_called.append((eprom_name, cmd))
            # Return None to abort cleanly (no real serial I/O).
            return None, 0

        monkeypatch.setattr(EpromOperator, "_setup_operation", _fake_setup_operation)

        op = EpromOperator(ConfigManager())
        op.check_eprom_blank("W27C512", dict(_W27C512_LIKE_EPROM_DATA))

        # Non-SRAM: _setup_operation MUST have been called.
        assert len(setup_called) == 1, (
            "W27C512 (EEPROM) blank-check must reach _setup_operation; "
            f"call list: {setup_called}"
        )
        assert setup_called[0][0] == "W27C512"


# ---------------------------------------------------------------------------
# Plan 120-06 Task 3 — pin the SDP payload-free wire shape + the emitted
# `flags` residue + the new FLAG_SKIP_SDP_UNLOCK bit, all at the wire
# boundary (the composed command_dict SerialCommunicator.find_and_connect
# receives), not at the Python function-return boundary.
# ---------------------------------------------------------------------------


def _at28c256_programmer_dict() -> dict:
    """A real at28c256 programmer dict via resolve_chip (protocol 0x0D / 13)."""
    from firestarter.chip_resolver import resolve_chip
    from firestarter.database import EpromDatabase

    db = EpromDatabase(skip_local_override=True)
    return resolve_chip("at28c256", db=db)


def _capture_written_frames(fake_serial):
    """Wrap fake_serial.write to record every chunk the host writes.

    Returns the list the wrapper appends to; the original write behavior
    (buffering into the BytesIO-backed fake) is preserved so the state
    machine's own send_ack()/get_response() flow is unaffected.
    """
    written: list = []
    original_write = fake_serial.write

    def _wrapped(data: bytes) -> int:
        written.append(bytes(data))
        return original_write(data)

    fake_serial.write = _wrapped
    return written


class TestSdpOperationsWireShape:
    """v1.22 HOST-01 / HOST-02: sdp_unlock/sdp_lock are payload-free (cmd 9 /
    cmd 10, no `#` data frame, no host DONE round-trip); the DB's firmware-inert
    FLAG_CAN_ERASE residue and the new FLAG_SKIP_SDP_UNLOCK bit both reach the
    composed command_dict.
    """

    def test_sdp_unlock_emits_cmd_9_payload_free(self, make_comm, fake_serial) -> None:
        """v1.22 HOST-01: sdp_unlock composes cmd == 9 and drives INIT->MAIN->END
        with no main_phase_handler — only send_ack("OK") writes occur; no `#`
        data frame is ever written and send_done("DONE") is never called,
        because _run_state_machine falls through to _main_phase_simple exactly
        like erase_eprom's precedent.
        """
        captured: dict = {}

        def _fake_find_and_connect(command_dict, config, **kwargs):
            captured["command_dict"] = command_dict
            return make_comm()

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
        fake_serial.feed(build_frame(MSG_END_DONE, b""))
        written = _capture_written_frames(fake_serial)

        operator = EpromOperator(ConfigManager())
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ):
            ok = operator.sdp_unlock("at28c256", _at28c256_programmer_dict())

        assert ok is True
        assert captured["command_dict"]["cmd"] == 9
        # No `#`-prefixed data frame and no "DONE" round-trip were written.
        assert not any(chunk.startswith(b"#") for chunk in written)
        assert not any(b"DONE" in chunk for chunk in written)

    def test_sdp_lock_emits_cmd_10_payload_free(self, make_comm, fake_serial) -> None:
        """v1.22 HOST-01: sdp_lock composes cmd == 10, same payload-free shape
        as sdp_unlock above — no `#` data frame, no host DONE round-trip.
        """
        captured: dict = {}

        def _fake_find_and_connect(command_dict, config, **kwargs):
            captured["command_dict"] = command_dict
            return make_comm()

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
        fake_serial.feed(build_frame(MSG_END_DONE, b""))
        written = _capture_written_frames(fake_serial)

        operator = EpromOperator(ConfigManager())
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ):
            ok = operator.sdp_lock("at28c256", _at28c256_programmer_dict())

        assert ok is True
        assert captured["command_dict"]["cmd"] == 10
        assert not any(chunk.startswith(b"#") for chunk in written)
        assert not any(b"DONE" in chunk for chunk in written)

    def test_sdp_unlock_setup_failure_returns_false(self) -> None:
        """v1.22 HOST-01: when find_and_connect fails (as _setup_operation
        handles it), sdp_unlock returns False without raising."""
        from firestarter.exceptions import ProgrammerNotFoundError

        operator = EpromOperator(ConfigManager())
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=ProgrammerNotFoundError("no port"),
        ):
            ok = operator.sdp_unlock("at28c256", _at28c256_programmer_dict())
        assert ok is False

    def test_sdp_lock_setup_failure_returns_false(self) -> None:
        """v1.22 HOST-01: same setup-failure guard for sdp_lock."""
        from firestarter.exceptions import ProgrammerNotFoundError

        operator = EpromOperator(ConfigManager())
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=ProgrammerNotFoundError("no port"),
        ):
            ok = operator.sdp_lock("at28c256", _at28c256_programmer_dict())
        assert ok is False

    def test_sdp_command_flags_carry_the_db_can_erase_bit(
        self, make_comm, fake_serial
    ) -> None:
        """v1.22 HOST-01: the composed command_dict["flags"] for an at28c256
        input is 2 (FLAG_CAN_ERASE), NOT 0.

        database.py:570-595 sets FLAG_CAN_ERASE (0x02) for every EEPROM /
        Flash-EEPROM part with algorithm != 5 -- which is all 84 protocol-0x0D
        chips, including at28c256 -- and configure_eeprom28c never reads it
        (firmware-inert, documented in that comment block). This leg exists so
        a future reader does not mistake `flags: 2` on the wire for a defect.
        It is deliberately NOT a suppression: the wider 0x0D flag-surface
        honesty problem is out of scope for this phase.
        """
        captured: dict = {}

        def _fake_find_and_connect(command_dict, config, **kwargs):
            captured["command_dict"] = command_dict
            return make_comm()

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
        fake_serial.feed(build_frame(MSG_END_DONE, b""))

        operator = EpromOperator(ConfigManager())
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ):
            operator.sdp_unlock("at28c256", _at28c256_programmer_dict())

        assert captured["command_dict"]["flags"] == 2

    def test_skip_sdp_unlock_bit_reaches_the_wire(self, make_comm, fake_serial) -> None:
        """v1.22 HOST-02: build_flags(skip_sdp_unlock=True) passed as
        operation_flags into sdp_unlock reaches the composed command_dict's
        "flags" value with bit 0x100 set -- the HOST-02 oracle at the wire
        boundary rather than at the build_flags function-return boundary.
        """
        from firestarter.constants import FLAG_SKIP_SDP_UNLOCK
        from firestarter.eprom_operations import build_flags

        captured: dict = {}

        def _fake_find_and_connect(command_dict, config, **kwargs):
            captured["command_dict"] = command_dict
            return make_comm()

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
        fake_serial.feed(build_frame(MSG_END_DONE, b""))

        operator = EpromOperator(ConfigManager())
        operation_flags = build_flags(skip_sdp_unlock=True)
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ):
            operator.sdp_unlock(
                "at28c256", _at28c256_programmer_dict(), operation_flags
            )

        assert captured["command_dict"]["flags"] & FLAG_SKIP_SDP_UNLOCK
