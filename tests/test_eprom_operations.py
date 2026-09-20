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
import tempfile
from pathlib import Path
from unittest.mock import patch

from firestarter.compare import CompareAccumulator
from firestarter.config import ConfigManager
from firestarter.eprom_operations import EpromOperator
from firestarter.messages import (
    MSG_DATA_CHUNK,
    MSG_END_DONE,
    MSG_ERR_NOT_BLANK,
    MSG_ERR_TIMEOUT,
    MSG_INIT_DONE,
    MSG_MAIN_DONE,
)

from .conftest import build_frame
from .fake_chip import WriteInitPreflightChip


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


# read_timing block
# Tests for host-side read-timing knob params in consistency_check_eprom.
# Selectable with: pytest -k "read_timing"

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


def test_region_end_key_constant() -> None:
    """JSON_KEY_REGION_END must equal the firmware PROGMEM key string.

    The firmware declares: const char key_region_end[] PROGMEM = "region-end";
    (json_parser.c). If the host string drifts, the firmware silently ignores the param
    (jsmn skips unknown keys) and a partial write silently falls back to a whole-device
    blank check instead of a region-scoped one.

    Selected by `pytest -k region_end`.
    """
    from firestarter.constants import (
        JSON_KEY_REGION_END,  # type: ignore[attr-defined]
    )

    assert JSON_KEY_REGION_END == "region-end"


def test_write_into_blank_region_of_non_blank_part_succeeds() -> None:
    """BLANK-01 / BLANK-03: before the fake learned the region (this plan),
    this leg passed VACUOUSLY -- `WriteInitPreflightChip._is_blank` compared
    the WHOLE buffer, so it modelled the whole-device check exactly and
    could never distinguish "blank at the target" from "blank everywhere".
    A non-blank part with data at low addresses now accepts a write into a
    genuinely blank region elsewhere on the device, matching the real
    firmware's region-scoped write-init blank check (Phase 201, D-09/D-10).

    Selected by `pytest -k region_end or blank_region`.
    """
    memory_size = 16384
    chip = WriteInitPreflightChip(memory_size, uv=True)
    chip.data[0x10:0x11] = b"\x00"  # non-blank, well outside the target region

    fh = tempfile.NamedTemporaryFile(prefix="p201_", suffix=".bin", delete=False)
    fh.write(b"\xaa" * 256)
    fh.close()
    try:
        outcome = chip.write_eprom("test-part", {}, fh.name, 0, address_str="0x2000")
    finally:
        Path(fh.name).unlink()

    assert outcome is True
    assert chip.last_firmware_error_code is None


def test_write_into_non_blank_region_is_still_refused() -> None:
    """The paired negative control: a non-blank byte genuinely INSIDE the
    target region still refuses the write -- the fix SCOPES the check, it
    does not delete it. A programmed bit cannot be un-programmed on a UV
    part, so a silently dropped refusal would be irreversible data loss.

    Selected by `pytest -k region_end or blank_region`.
    """
    memory_size = 16384
    chip = WriteInitPreflightChip(memory_size, uv=True)
    chip.data[0x2010:0x2011] = b"\x00"  # non-blank, inside the target region

    fh = tempfile.NamedTemporaryFile(prefix="p201_", suffix=".bin", delete=False)
    fh.write(b"\xaa" * 256)
    fh.close()
    try:
        outcome = chip.write_eprom("test-part", {}, fh.name, 0, address_str="0x2000")
    finally:
        Path(fh.name).unlink()

    assert outcome is False
    assert chip.last_firmware_error_code == MSG_ERR_NOT_BLANK


def test_region_end_emitted_on_write(make_comm, fake_serial) -> None:
    """D-05 / D-07: the command_dict `_setup_operation` returns for
    COMMAND_WRITE carries JSON_KEY_REGION_END == address + region_length --
    the wire key BLANK-01's firmware side reads to scope its write-init
    blank check to this write's own region instead of the whole device.
    Captured at the wire boundary (the dict SerialCommunicator.find_and_connect
    receives), the same boundary TestSdpOperationsWireShape above uses.

    Selected by `pytest -k region_end`.
    """
    from firestarter.constants import COMMAND_WRITE, JSON_KEY_REGION_END

    captured: dict = {}

    def _fake_find_and_connect(command_dict, config, **kwargs):
        captured["command_dict"] = command_dict
        return make_comm()

    operator = EpromOperator(ConfigManager())
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=_fake_find_and_connect,
    ):
        operator._setup_operation(
            "W27C512",
            dict(_MINIMAL_EPROM_DATA),
            COMMAND_WRITE,
            address="0x1000",
            region_length=256,
        )

    assert captured["command_dict"][JSON_KEY_REGION_END] == 0x1000 + 256


# RETIRED 202-01 (D-01/D-02/D-10): `test_region_end_emitted_on_verify` used to
# live here. It drove `_setup_operation` directly with an explicit
# COMMAND_VERIFY to prove `_setup_operation` emits JSON_KEY_REGION_END for
# that ordinal -- true of the function in isolation, but `verify_eprom` no
# longer composes COMMAND_VERIFY at all (it drives COMMAND_READ; see
# `TestVerifyEpromHostSideRead` above), so the test passed while proving
# nothing about shipped `verify` behaviour, and its docstring's claim that
# "write and verify share one dict-construction path" was no longer true.
# Retired rather than "fixed", because there is no real call site left for
# it to pin: COMMAND_VERIFY's `_setup_operation` branch is now genuinely
# dead in production, kept alive only by the constant staying in
# `constants.py` pending phase 204's removal. `test_region_end_absent_for_read`
# immediately below is what actually covers verify's wire shape now (it
# proves COMMAND_READ never carries JSON_KEY_REGION_END); no replacement
# test is needed here for verify specifically.


def test_region_end_absent_for_read(make_comm, fake_serial) -> None:
    """D-05: the emission is guarded on cmd being COMMAND_WRITE or
    COMMAND_VERIFY -- COMMAND_READ never carries JSON_KEY_REGION_END, so
    the read path's existing `memory-size` narrowing (`:495`) stays
    undisturbed. region_length is passed here too (not merely omitted) to
    prove the guard is on `cmd`, not on the caller happening not to supply
    a value.

    Selected by `pytest -k region_end`.
    """
    from firestarter.constants import COMMAND_READ, JSON_KEY_REGION_END

    captured: dict = {}

    def _fake_find_and_connect(command_dict, config, **kwargs):
        captured["command_dict"] = command_dict
        return make_comm()

    operator = EpromOperator(ConfigManager())
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=_fake_find_and_connect,
    ):
        operator._setup_operation(
            "W27C512",
            dict(_MINIMAL_EPROM_DATA),
            COMMAND_READ,
            address="0x1000",
            region_length=256,
        )

    assert JSON_KEY_REGION_END not in captured["command_dict"]


class TestVerifyEpromHostSideRead:
    """CMP-01 / phase 202 success criterion 1: `firestarter verify <chip>
    <file>` completes against firmware that still carries the verify
    ordinal without ever composing a command dict whose `cmd` is the verify
    ordinal (COMMAND_VERIFY, 6) -- it composes only COMMAND_READ (1) and
    compares chunk by chunk on the host (202-01 D-01/D-02/D-10).
    """

    def test_no_composed_command_dict_carries_the_verify_ordinal(
        self, make_comm, fake_serial, tmp_path
    ) -> None:
        """Collect EVERY composed `command_dict` (not just the last) via the
        `find_and_connect` side-effect idiom, alongside `_capture_written_frames`
        for the raw wire bytes -- no entry's `cmd` is COMMAND_VERIFY (6) and at
        least one is COMMAND_READ (1)."""
        from firestarter.constants import COMMAND_READ, COMMAND_VERIFY

        payload = b"\xde\xad\xbe\xef"
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(payload)

        command_dicts: list[dict] = []

        def _fake_find_and_connect(command_dict, config, **kwargs):
            command_dicts.append(dict(command_dict))
            return make_comm()

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_DATA_CHUNK, payload))
        fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
        fake_serial.feed(build_frame(MSG_END_DONE, b""))
        written = _capture_written_frames(fake_serial)

        operator = EpromOperator(ConfigManager())
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ):
            verdict = operator.verify_eprom(
                "W27C512", dict(_MINIMAL_EPROM_DATA), str(input_file)
            )

        assert verdict == 0
        assert command_dicts, "find_and_connect was never called"
        assert all(cd["cmd"] != COMMAND_VERIFY for cd in command_dicts)
        assert any(cd["cmd"] == COMMAND_READ for cd in command_dicts)
        # Sanity: the driven exchange actually wrote bytes on the wire (acks).
        assert written

    def test_byte_identical_readback_returns_zero(
        self, make_comm, fake_serial, tmp_path
    ) -> None:
        payload = b"\x01\x02\x03\x04"
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(payload)

        def _fake_find_and_connect(command_dict, config, **kwargs):
            return make_comm()

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_DATA_CHUNK, payload))
        fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
        fake_serial.feed(build_frame(MSG_END_DONE, b""))

        operator = EpromOperator(ConfigManager())
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ):
            verdict = operator.verify_eprom(
                "W27C512", dict(_MINIMAL_EPROM_DATA), str(input_file)
            )

        assert verdict == 0

    def test_one_flipped_byte_returns_one(
        self, make_comm, fake_serial, tmp_path
    ) -> None:
        payload = b"\x01\x02\x03\x04"
        corrupted = bytearray(payload)
        corrupted[2] ^= 0xFF
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(payload)

        def _fake_find_and_connect(command_dict, config, **kwargs):
            return make_comm()

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_DATA_CHUNK, bytes(corrupted)))
        fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
        fake_serial.feed(build_frame(MSG_END_DONE, b""))

        operator = EpromOperator(ConfigManager())
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ):
            verdict = operator.verify_eprom(
                "W27C512", dict(_MINIMAL_EPROM_DATA), str(input_file)
            )

        assert verdict == 1

    def test_incomplete_compare_never_reports_a_match(
        self, make_comm, fake_serial, tmp_path
    ) -> None:
        """Standing prohibition this plan carries: a compare that covered
        fewer bytes than the declared region must never report a clean
        match, even when every byte it DID compare was equal. Simulates a
        device that ends MAIN early (after 4 of 8 declared bytes) -- every
        compared byte matches, but `compared (4) != total (8)`, so the
        verdict must be 1, not 0."""
        payload = b"\x01\x02\x03\x04\x05\x06\x07\x08"
        short_chunk = payload[:4]
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(payload)

        def _fake_find_and_connect(command_dict, config, **kwargs):
            return make_comm()

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_DATA_CHUNK, short_chunk))
        fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
        fake_serial.feed(build_frame(MSG_END_DONE, b""))

        operator = EpromOperator(ConfigManager())
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ):
            verdict = operator.verify_eprom(
                "W27C512", dict(_MINIMAL_EPROM_DATA), str(input_file)
            )

        assert verdict == 1

    def test_setup_failure_returns_two(self) -> None:
        """The entry guard returns 2 (matching `consistency_check_eprom`'s
        own setup-failure convention), not the old bare `False`."""
        from firestarter.exceptions import SerialError

        operator = EpromOperator(ConfigManager())
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=SerialError("no board attached"),
        ):
            verdict = operator.verify_eprom(
                "W27C512", dict(_MINIMAL_EPROM_DATA), "does-not-matter.bin"
            )

        assert verdict == 2


class TestVerifyEpromReadAbort:
    """202-04: the default (non-`--full`) compare stops the programmer at
    the first mismatching byte instead of draining it (CMP-04, D-06), never
    mistakes the deliberate stop for a fault (D-08), and reports the
    compared prefix honestly (D-09).

    The only in-repo way to observe "stopped acking" is the captured write
    frames (`_capture_written_frames`) -- the returned verdict alone cannot
    distinguish "aborted" from "drained, then reported" (measured project
    fact). Every test here that claims an abort happened proves it either
    by the ack-write count or by the chunk-callback call count, never by
    the verdict alone.
    """

    @staticmethod
    def _install_feed_counter(monkeypatch):
        """Monkeypatch CompareAccumulator.feed to record every call it
        receives while still doing the real work -- the only way to observe
        "the chunk callback fired N times" without a hook the production
        code otherwise exposes."""
        calls: list = []
        original_feed = CompareAccumulator.feed

        def _counting_feed(self, address, expected, actual):
            calls.append((address, actual))
            return original_feed(self, address, expected, actual)

        monkeypatch.setattr(CompareAccumulator, "feed", _counting_feed)
        return calls

    def test_default_abort_stops_acking_after_the_mismatching_chunk(
        self, make_comm, fake_serial, tmp_path, monkeypatch
    ) -> None:
        """Four chunks (2 bytes each) are staged; the second differs. The
        host must ack chunk 1, feed the chunk callback exactly twice, and
        never ack again -- chunks 3 and 4 are never sent because the
        firmware's op_wait_for_ack times out first (simulated here by an
        ERROR frame carrying MSG_ERR_TIMEOUT immediately after the
        mismatching chunk). Also proves the port teardown completed: a
        second operation on the SAME operator succeeds afterward."""
        payload = b"\x01\x02\x03\x04\x05\x06\x07\x08"
        corrupted_chunk2 = bytes(b ^ 0xFF for b in payload[2:4])
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(payload)

        feed_calls = self._install_feed_counter(monkeypatch)

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_DATA_CHUNK, payload[0:2]))
        fake_serial.feed(build_frame(MSG_DATA_CHUNK, corrupted_chunk2))
        fake_serial.feed(build_frame(MSG_ERR_TIMEOUT, b""))
        written = _capture_written_frames(fake_serial)

        def _fake_find_and_connect(command_dict, config, **kwargs):
            return make_comm()

        operator = EpromOperator(ConfigManager())
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ):
            verdict = operator.verify_eprom(
                "W27C512", dict(_MINIMAL_EPROM_DATA), str(input_file)
            )

        assert verdict == 1
        assert len(feed_calls) == 2, feed_calls

        ack_writes = [w for w in written if w == b"OK"]
        # The first two "OK" writes are the state machine's own
        # phase-transition signals (INIT-phase start, MAIN-phase start),
        # both pre-existing and orthogonal to this feature. The third is
        # the per-chunk ack for the FIRST (matching) chunk. No fourth "OK"
        # is ever written: the mismatching second chunk's ack is withheld
        # -- that withholding IS the abort.
        assert len(ack_writes) == 3, ack_writes

        # Port teardown completed cleanly on the error path: a second,
        # unrelated operation on the same operator succeeds without raising.
        # `_FakeSerial.close()` (called by `disconnect()`'s teardown) flips
        # `is_open` False on the shared fixture instance -- a real second
        # `find_and_connect` would open a fresh port, so the fake needs an
        # explicit reopen here to model that, not because production code
        # has an equivalent step.
        fake_serial.is_open = True
        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_DATA_CHUNK, payload))
        fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
        fake_serial.feed(build_frame(MSG_END_DONE, b""))
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ):
            second_verdict = operator.verify_eprom(
                "W27C512", dict(_MINIMAL_EPROM_DATA), str(input_file)
            )
        assert second_verdict == 0

    def test_non_timeout_error_after_intended_abort_returns_two(
        self, make_comm, fake_serial, tmp_path
    ) -> None:
        """D-08 negative 1: a terminating error frame carrying an id OTHER
        than the timeout id is a real fault wearing the abort's clothes --
        it must take the exit-2 path even though the default path DID
        request a stop and DID record one."""
        payload = b"\x01\x02\x03\x04"
        corrupted = bytearray(payload)
        corrupted[0] ^= 0xFF
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(payload)

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_DATA_CHUNK, bytes(corrupted)))
        fake_serial.feed(
            build_frame(MSG_ERR_NOT_BLANK, bytes([0x00, 0x00, 0x00, 0xAB]))
        )

        def _fake_find_and_connect(command_dict, config, **kwargs):
            return make_comm()

        operator = EpromOperator(ConfigManager())
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ):
            verdict = operator.verify_eprom(
                "W27C512", dict(_MINIMAL_EPROM_DATA), str(input_file)
            )

        assert verdict == 2

    def test_timeout_on_full_path_with_no_abort_requested_returns_two(
        self, make_comm, fake_serial, tmp_path
    ) -> None:
        """D-08 negative 2: `--full` never sets an abort_predicate, so
        `_read_abort_intended` is False and `_read_abort_stopped_at` stays
        `None` for the whole run. A genuine firmware timeout on that path
        must never be attributed to a stop nobody requested."""
        payload = b"\x01\x02\x03\x04"
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(payload)

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_DATA_CHUNK, payload))
        fake_serial.feed(build_frame(MSG_ERR_TIMEOUT, b""))

        def _fake_find_and_connect(command_dict, config, **kwargs):
            return make_comm()

        operator = EpromOperator(ConfigManager())
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ):
            verdict = operator.verify_eprom(
                "W27C512", dict(_MINIMAL_EPROM_DATA), str(input_file), full=True
            )

        assert verdict == 2
        assert operator._read_abort_stopped_at is None

    def test_timeout_outside_the_acceptance_window_returns_two(
        self, make_comm, fake_serial, tmp_path, monkeypatch
    ) -> None:
        """D-08 negative 3: even with the default path's intent flag set
        and a stop genuinely recorded, a timeout arriving long after that
        stop is not this stop's consequence -- simulated by moving the
        recorded stop timestamp back beyond
        READ_ABORT_ACCEPTANCE_WINDOW_S. Real wall-clock waiting in a test
        is neither necessary nor safe, so `time.monotonic` is faked for
        just the two calls this path makes (the stop, then the check)."""
        import firestarter.eprom_operations as eprom_ops_mod

        payload = b"\x01\x02\x03\x04"
        corrupted = bytearray(payload)
        corrupted[0] ^= 0xFF
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(payload)

        class _FakeTime:
            """Wraps the real `time` module, overriding only `monotonic()`
            -- `time.time()` (used elsewhere in this call for duration
            logging) still delegates to the real module."""

            def __init__(self, values):
                self._it = iter(values)

            def monotonic(self):
                return next(self._it)

            def __getattr__(self, name):
                import time as real_time

                return getattr(real_time, name)

        stop_time = 1_000.0
        check_time = stop_time + eprom_ops_mod.READ_ABORT_ACCEPTANCE_WINDOW_S + 1.0
        monkeypatch.setattr(eprom_ops_mod, "time", _FakeTime([stop_time, check_time]))

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_DATA_CHUNK, bytes(corrupted)))
        fake_serial.feed(build_frame(MSG_ERR_TIMEOUT, b""))

        def _fake_find_and_connect(command_dict, config, **kwargs):
            return make_comm()

        operator = EpromOperator(ConfigManager())
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ):
            verdict = operator.verify_eprom(
                "W27C512", dict(_MINIMAL_EPROM_DATA), str(input_file)
            )

        assert verdict == 2

    def test_first_byte_mismatch_aborts_after_one_chunk_and_reports_a_range(
        self, make_comm, fake_serial, tmp_path, caplog
    ) -> None:
        """CMP-04 boundary: a mismatch AT the first byte of the region is
        still reported as a range and still exits 1 -- the abort fires
        after the very first chunk, so the second (never-sent) chunk's
        bytes are never part of the compared prefix."""
        payload = b"\x01\x02\x03\x04"
        corrupted_chunk1 = bytes([0x01 ^ 0xFF, 0x02])  # byte 0 mismatched
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(payload)

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_DATA_CHUNK, corrupted_chunk1))
        fake_serial.feed(build_frame(MSG_ERR_TIMEOUT, b""))

        def _fake_find_and_connect(command_dict, config, **kwargs):
            return make_comm()

        operator = EpromOperator(ConfigManager())
        with (
            caplog.at_level(logging.INFO, logger="EpromOperator"),
            patch(
                "firestarter.serial_comm.SerialCommunicator.find_and_connect",
                side_effect=_fake_find_and_connect,
            ),
        ):
            verdict = operator.verify_eprom(
                "W27C512", dict(_MINIMAL_EPROM_DATA), str(input_file)
            )

        assert verdict == 1
        messages = [rec.message for rec in caplog.records]
        assert any("Mismatch 0x000000-0x000000 (1 bytes)" in m for m in messages)

    def test_last_byte_mismatch_completes_normally_not_as_an_abort(
        self, make_comm, fake_serial, tmp_path, caplog
    ) -> None:
        """CMP-04 boundary, other end: a mismatch AT the last byte of the
        region still reports a range and exits 1, but the firmware
        completes the read (MAIN arrives) before its ack wait would have
        expired -- a COMPLETE compare, not an abort: compared == total,
        aborted is False."""
        payload = b"\x01\x02\x03\x04"
        corrupted_chunk2 = bytes([0x03, 0x04 ^ 0xFF])  # byte 3 (last) mismatched
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(payload)

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_DATA_CHUNK, payload[0:2]))
        fake_serial.feed(build_frame(MSG_DATA_CHUNK, corrupted_chunk2))
        fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
        fake_serial.feed(build_frame(MSG_END_DONE, b""))

        def _fake_find_and_connect(command_dict, config, **kwargs):
            return make_comm()

        operator = EpromOperator(ConfigManager())
        with (
            caplog.at_level(logging.INFO, logger="EpromOperator"),
            patch(
                "firestarter.serial_comm.SerialCommunicator.find_and_connect",
                side_effect=_fake_find_and_connect,
            ),
        ):
            verdict = operator.verify_eprom(
                "W27C512", dict(_MINIMAL_EPROM_DATA), str(input_file)
            )

        assert verdict == 1
        messages = [rec.message for rec in caplog.records]
        assert any("Mismatch 0x000003-0x000003 (1 bytes)" in m for m in messages)
        # compared == total (4): the last chunk was fully fed before the
        # predicate ever fired, so this is not a truncated/aborted prefix.
        assert any("1 bad of 4 compared of 4" in m for m in messages)

    def test_sixteen_consecutive_differences_report_one_range(
        self, make_comm, fake_serial, tmp_path, caplog
    ) -> None:
        """CMP-04 adjacency: a run of 16 consecutive differing bytes inside
        ONE chunk is one coalesced range with byte count 16, not 16 ranges."""
        payload = bytes(range(16))
        corrupted = bytes(b ^ 0xFF for b in payload)  # every byte differs
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(payload)

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_DATA_CHUNK, corrupted))
        fake_serial.feed(build_frame(MSG_ERR_TIMEOUT, b""))

        def _fake_find_and_connect(command_dict, config, **kwargs):
            return make_comm()

        operator = EpromOperator(ConfigManager())
        with (
            caplog.at_level(logging.INFO, logger="EpromOperator"),
            patch(
                "firestarter.serial_comm.SerialCommunicator.find_and_connect",
                side_effect=_fake_find_and_connect,
            ),
        ):
            verdict = operator.verify_eprom(
                "W27C512", dict(_MINIMAL_EPROM_DATA), str(input_file)
            )

        assert verdict == 1
        range_lines = [
            rec.message for rec in caplog.records if rec.message.startswith("Mismatch ")
        ]
        assert len(range_lines) == 1, range_lines
        assert "(16 bytes)" in range_lines[0]

    def test_zero_length_region_never_reports_a_clean_pass(
        self, make_comm, fake_serial, tmp_path
    ) -> None:
        """CMP-04 empty: a zero-length read-back compares as PERFECT
        equality byte-for-byte -- only an explicit length check
        distinguishes "nothing was compared" from a genuine match. No
        predicate ever fires (there is nothing to feed) and the verdict
        must never be 0."""
        input_file = tmp_path / "empty.bin"
        input_file.write_bytes(b"")

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
        fake_serial.feed(build_frame(MSG_END_DONE, b""))

        def _fake_find_and_connect(command_dict, config, **kwargs):
            return make_comm()

        operator = EpromOperator(ConfigManager())
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ):
            verdict = operator.verify_eprom(
                "W27C512", dict(_MINIMAL_EPROM_DATA), str(input_file)
            )

        assert verdict != 0, "an empty region must never read as a clean pass"
        assert operator._read_abort_stopped_at is None

    def test_two_mismatches_in_one_chunk_report_the_lower_addressed_range(
        self, make_comm, fake_serial, tmp_path, caplog
    ) -> None:
        """CMP-04 ordering: with mismatches at two separate addresses
        inside ONE chunk, the single default-path range (max_ranges=1) is
        the LOWER-addressed one -- the accumulator retains ranges in feed
        order, and the lower address is coalesced first."""
        payload = b"\x01\x02\x03\x04\x05\x06"
        corrupted = bytearray(payload)
        corrupted[1] ^= 0xFF  # address 1
        corrupted[4] ^= 0xFF  # address 4
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(payload)

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_DATA_CHUNK, bytes(corrupted)))
        fake_serial.feed(build_frame(MSG_ERR_TIMEOUT, b""))

        def _fake_find_and_connect(command_dict, config, **kwargs):
            return make_comm()

        operator = EpromOperator(ConfigManager())
        with (
            caplog.at_level(logging.INFO, logger="EpromOperator"),
            patch(
                "firestarter.serial_comm.SerialCommunicator.find_and_connect",
                side_effect=_fake_find_and_connect,
            ),
        ):
            verdict = operator.verify_eprom(
                "W27C512", dict(_MINIMAL_EPROM_DATA), str(input_file)
            )

        assert verdict == 1
        range_lines = [
            rec.message for rec in caplog.records if rec.message.startswith("Mismatch ")
        ]
        assert len(range_lines) == 1, range_lines
        assert "0x000001-0x000001" in range_lines[0]

    def test_aborted_run_counts_are_exact_integers_matching_pre_stop_bytes(
        self, make_comm, fake_serial, tmp_path, monkeypatch
    ) -> None:
        """CMP-04 precision: on an aborted run, the bad count, the compared
        count, and every retained range's byte count are exact `int`
        instances -- never derived from a list length or a float -- and
        the compared count is strictly less than the region total and
        equals the sum of the chunk lengths fed before the stop."""
        payload = b"\x01\x02\x03\x04\x05\x06\x07\x08"
        corrupted_chunk2 = bytes(b ^ 0xFF for b in payload[2:4])
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(payload)

        captured_results: list = []
        original_finalise = CompareAccumulator.finalise

        def _capturing_finalise(self, **kwargs):
            result = original_finalise(self, **kwargs)
            captured_results.append(result)
            return result

        monkeypatch.setattr(CompareAccumulator, "finalise", _capturing_finalise)

        fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
        fake_serial.feed(build_frame(MSG_DATA_CHUNK, payload[0:2]))
        fake_serial.feed(build_frame(MSG_DATA_CHUNK, corrupted_chunk2))
        fake_serial.feed(build_frame(MSG_ERR_TIMEOUT, b""))

        def _fake_find_and_connect(command_dict, config, **kwargs):
            return make_comm()

        operator = EpromOperator(ConfigManager())
        with patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ):
            verdict = operator.verify_eprom(
                "W27C512", dict(_MINIMAL_EPROM_DATA), str(input_file)
            )

        assert verdict == 1
        assert len(captured_results) == 1
        result = captured_results[0]
        assert result.aborted is True
        assert isinstance(result.bad, int)
        assert isinstance(result.compared, int)
        for r in result.ranges:
            assert isinstance(r.count, int)
        assert result.compared == 4  # 2 chunks x 2 bytes, before the stop
        assert result.compared < result.total


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


# RED tests for write_cycle_eprom 3-way verdict


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


# unit tests for fault_inject_cycle (coverage gate)


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


# harness refinement: measure_command_nak_latency
# Per-frame firmware NAK latency on an established single-port connection.


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


# (RED): SRAM/FRAM blank-check host short-circuit
#
# These tests MUST FAIL until Task 2 adds the short-circuit to check_eprom_blank.
# The negative control (non-SRAM still issues blank-check) MUST PASS both now
# and after the fix.

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


# pin the SDP payload-free wire shape + the emitted
# `flags` residue + the new FLAG_SKIP_SDP_UNLOCK bit, all at the wire
# boundary (the composed command_dict SerialCommunicator.find_and_connect
# receives), not at the Python function-return boundary.


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
        """REVERSAL RECORD (Phase 121 D-12): this test previously asserted the
        composed command_dict["flags"] for an at28c256 input is 2
        (FLAG_CAN_ERASE), NOT 0 -- on the claim that
        `database.py`'s (former) `algo != 5` exclusion set FLAG_CAN_ERASE
        (0x02) for every EEPROM / Flash-EEPROM part with algorithm != 5,
        including all 84 protocol-0x0D chips, and that this was safe because
        configure_eeprom28c never reads the bit (firmware-inert).

        D-12 reversed that POLICY, not the fact: configure_eeprom28c still
        never reads FLAG_CAN_ERASE -- the firmware-inertness claim was never
        wrong. What changed at the time was that an inert-but-false
        capability advertisement is still false: DEVTEST-01's `dev test`
        sweep reads it and plans a real erase step that reports OK having
        done nothing. `database.py` excluded algorithm 13 (0x0D) as well as
        5, so the wire flags for at28c256 were 0, not 2.

        REVERSAL RECORD (Phase 153, ERASE-03): Phase 153 restores the bit at
        the source because `configure_eeprom28c` now implements the
        AN-0544B software chip erase and `CMD_ERASE` is wired to it, so the
        wire flags for a 0x0D part carry FLAG_CAN_ERASE again. This leg now
        exists to catch a future reader clearing the bit a second time.

        Per D-153-05: carrying the bit on an SDP command frame does NOT mean
        an SDP command erases anything. The bit is a capability
        advertisement read only by the firmware's standalone-erase
        precondition (`eprom_erase`'s refusal gate in `eprom_operations.cpp`);
        no erase-on-write block was added to the 0x0D write path, and
        sdp_unlock itself performs no erase.
        """
        from firestarter.constants import FLAG_CAN_ERASE

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

        assert captured["command_dict"]["flags"] & FLAG_CAN_ERASE

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


def _drive_write_eprom_for_ack_check(
    tmp_path,
    make_comm,
    fake_serial,
    *,
    skip_sdp_unlock: bool,
    ack_present: bool,
):
    """Drive a full, otherwise-successful write_eprom() against a real
    protocol-0x0D chip (at28c256) through a fake serial port.

    Feed sequence mirrors test_write_skip_sdp_unlock.py's happy path:
    [0x86 WARN, if ack_present] -> INIT_DONE -> OK_REQ_DATA -> MAIN_DONE ->
    END_DONE. The WARN frame MUST land inside the INIT (or END) phase
    window, never inside MAIN: _main_phase_send_data's tight
    request/response loop only tolerates MAIN/ERROR/OK-request-chunk
    responses and raises EpromOperationError on anything else (e.g. WARN),
    whereas _execute_phase's INIT/END loop routes WARN through
    _handle_progress_response harmlessly. seen_message_ids is populated as
    each frame is decoded, so any position within the single continuous
    INIT->MAIN->END pass that _run_state_machine completes before
    write_eprom's post-state-machine check runs is equally valid, as long
    as it does not desync the MAIN phase's flow control.
    """
    from firestarter.eprom_operations import EpromOperator, build_flags
    from firestarter.messages import MSG_OK_REQ_DATA, MSG_WARN_SDP_UNLOCK_SKIPPED

    input_file = tmp_path / "at28c256.bin"
    input_file.write_bytes(b"\x01\x02\x03\x04")

    if ack_present:
        fake_serial.feed(build_frame(MSG_WARN_SDP_UNLOCK_SKIPPED, b""))
    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_OK_REQ_DATA, b""))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    def _fake_find_and_connect(command_dict, config, **kwargs):
        return make_comm()

    operator = EpromOperator(ConfigManager())
    operation_flags = build_flags(skip_sdp_unlock=skip_sdp_unlock)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=_fake_find_and_connect,
    ):
        ok = operator.write_eprom(
            "at28c256",
            _at28c256_programmer_dict(),
            str(input_file),
            operation_flags=operation_flags,
        )
    return ok


def test_missing_sdp_ack_fails_the_write_loudly(
    tmp_path, make_comm, fake_serial, caplog
) -> None:
    """v1.22 HOST-06 / D-15: this is the flag half of HOST-06's asymmetry --
    an unknown command produces a loud firmware error (D-14, plan 120-08),
    but an unknown flag bit produces SILENCE, so the ack's absence is the
    only signal available. --skip-sdp-unlock was set; the wire stream
    completes an otherwise-successful write (INIT -> MAIN -> END all ack)
    but never emits MSG_WARN_SDP_UNLOCK_SKIPPED (0x86). write_eprom must
    fail the operation and report plainly that the opt-out was not
    honoured -- this DETECTS after the fact, it does not PREVENT: on old
    firmware the unlock has already been emitted by the time the user is
    told.
    """
    with caplog.at_level(logging.ERROR, logger="EpromOperator"):
        ok = _drive_write_eprom_for_ack_check(
            tmp_path,
            make_comm,
            fake_serial,
            skip_sdp_unlock=True,
            ack_present=False,
        )

    assert ok is False
    messages = [rec.message for rec in caplog.records]
    assert any(
        "did not acknowledge" in m and "unlock ran anyway" in m for m in messages
    )
    assert any("firestarter fw --install" in m for m in messages)


def test_sdp_ack_honoured_produces_no_complaint(
    tmp_path, make_comm, fake_serial, caplog
) -> None:
    """v1.22 HOST-06 / D-15: the converse leg -- proves the check does not
    over-fire. Same flag, same otherwise-successful stream, PLUS the 0x86
    ack. write_eprom's return value must be unchanged from a normal
    successful write, and no missing-ack complaint may be emitted.
    """
    with caplog.at_level(logging.ERROR, logger="EpromOperator"):
        ok = _drive_write_eprom_for_ack_check(
            tmp_path,
            make_comm,
            fake_serial,
            skip_sdp_unlock=True,
            ack_present=True,
        )

    assert ok is True
    messages = [rec.message for rec in caplog.records]
    assert not any("did not acknowledge" in m for m in messages)


def test_ack_check_does_not_run_when_the_flag_was_not_set(
    tmp_path, make_comm, fake_serial, caplog
) -> None:
    """v1.22 HOST-06 / D-15: --skip-sdp-unlock was NOT set. Even though the
    0x86 ack never arrives, write_eprom's return value is unchanged and no
    missing-ack complaint is emitted -- proving the check is scoped to the
    flag rather than unconditional.
    """
    with caplog.at_level(logging.ERROR, logger="EpromOperator"):
        ok = _drive_write_eprom_for_ack_check(
            tmp_path,
            make_comm,
            fake_serial,
            skip_sdp_unlock=False,
            ack_present=False,
        )

    assert ok is True
    messages = [rec.message for rec in caplog.records]
    assert not any("did not acknowledge" in m for m in messages)


def test_seen_message_ids_records_decoded_ids_and_stays_bounded(
    make_comm, fake_serial
) -> None:
    """v1.22 HOST-06 / D-15: _decode_id_frame records every successfully
    decoded id into the connection's bounded seen_message_ids set. An
    unknown id that codec.decode_id_frame drops (catalog miss) does not
    raise and is never added to the record -- the record is keyed off
    decode SUCCESS, not raw frame arrival, and stays a plain set of
    integers, never anything sized from frame content (T-120-39).
    """
    from firestarter.codec import CATALOG
    from firestarter.messages import MSG_WARN_SDP_UNLOCK_SKIPPED

    unknown_id = 0x06
    assert unknown_id not in CATALOG, (
        "test fixture assumption: this id must stay uncataloged"
    )

    comm = make_comm()
    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(unknown_id, b""))
    fake_serial.feed(build_frame(MSG_WARN_SDP_UNLOCK_SKIPPED, b""))

    gen = comm._read_and_parse_lines(timeout=0.2)
    next(gen)  # MSG_INIT_DONE decodes and yields
    # The unknown_id frame decodes to None and is dropped without a yield;
    # the generator's internal loop continues straight to the next frame.
    next(gen)  # MSG_WARN_SDP_UNLOCK_SKIPPED decodes and yields

    assert comm.seen_message_ids == {MSG_INIT_DONE, MSG_WARN_SDP_UNLOCK_SKIPPED}
    assert unknown_id not in comm.seen_message_ids
    assert isinstance(comm.seen_message_ids, set)
    assert all(isinstance(i, int) for i in comm.seen_message_ids)
