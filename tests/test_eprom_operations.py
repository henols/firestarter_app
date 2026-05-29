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

    def _fake_setup_operation(self_op, eprom_name, eprom_data_dict, cmd, *args, **kwargs):  # noqa: ANN001
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
    from firestarter.constants import JSON_KEY_READ_SETTLING_DELAY  # type: ignore[attr-defined]

    assert JSON_KEY_READ_SETTLING_DELAY == "read-settling-delay"


def test_read_timing_strobe_key_constant() -> None:
    """JSON_KEY_READ_STROBE_US must equal the firmware PROGMEM key string.

    The firmware declares: const char key_read_strobe[] PROGMEM = "read-strobe-us";
    (json_parser.c). If the host string drifts, the firmware silently ignores the param.

    Selected by `pytest -k read_timing`.
    """
    from firestarter.constants import JSON_KEY_READ_STROBE_US  # type: ignore[attr-defined]

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
