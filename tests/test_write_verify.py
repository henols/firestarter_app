"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 203 Plan 03 -- `write --verify` (WRITE-04, WRITE-05).

Task 2 (this file's operator-tier section): `EpromOperator.write_eprom` and
`EpromOperator.verify_eprom` each gain a keyword-only `suppress_verdict_line`
parameter, and `write_eprom` gains a second transient cause channel,
`last_write_attempt_verdict`, sibling of `last_write_guard_verdict` (Plan
01). Task 3 (appended below) drives the CLI tier's `--verify`/`--full`
options and the seven-arm exit-code branch in `cli_handlers.write` -- see
this plan's `exit_code_contract_resolved` section for the full table.

Two layers of coverage, following `test_write_blank_guard.py`'s split:

1. Integration-level: the genuine `EpromOperator.write_eprom` /
   `verify_eprom` driven through a fake serial port, proving the suppression
   and the cause-channel classification fire at the real host path.
2. Unit-level / source-shape: the two exhaustiveness pins Task 2's action
   text requires (`_setup_operation`'s exactly-three `(None, 0)` return
   count, and the source-order assertion that the post-state-machine
   verdict assignment precedes the `--skip-sdp-unlock` ack block).
"""

from __future__ import annotations

import inspect
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from firestarter.chip_resolver import resolve_chip
from firestarter.cli_handlers import cli
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_operations import EpromOperator, build_flags
from firestarter.exceptions import ProgrammerNotFoundError
from firestarter.messages import (
    MSG_DATA_CHUNK,
    MSG_END_DONE,
    MSG_ERR_SETUP,
    MSG_INIT_DONE,
    MSG_MAIN_DONE,
    MSG_OK_REQ_DATA,
)

from .conftest import _FakeSerial, build_frame
from .conftest import make_app_context as _make_app_context
from .test_write_blank_guard import _m27c512_data

# ---------------------------------------------------------------------------
# Shared chip fixtures
# ---------------------------------------------------------------------------


def _at28c256_data() -> dict:
    """A real, resolved AT28C256 wire dict -- protocol 0x0D / algorithm 13:
    not in GUARDED_PROTOCOL_IDS (D-01/Fork A), so `write_eprom`'s guard is
    always skipped for it regardless of flags -- exactly what most of this
    module's tests need, so the write's own `_operation_context` is reached
    directly without a guard-read connection first."""
    db = EpromDatabase(skip_local_override=True)
    return resolve_chip("at28c256", db=db)


# ---------------------------------------------------------------------------
# Single-connection drive helper (unguarded chip -- one COMMAND_WRITE only)
# ---------------------------------------------------------------------------


def _comm_factory_for(serial: _FakeSerial):
    """Build a `SerialCommunicator` factory wired to one fake serial port,
    mirroring `tests/test_write_blank_guard.py::_comm_factory_for`."""
    from firestarter.serial_comm import SerialCommunicator

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

    return _factory


class _PortDropSerial(_FakeSerial):
    """A fake serial port that behaves normally until its pre-fed bytes are
    exhausted, then raises `serial.SerialException` ONCE on the next
    `read()` instead of returning `b""` -- simulating a genuine port drop
    mid-operation, as opposed to a plain pyserial-timeout empty read. After
    that one raise it reverts to plain empty reads, so `_operation_context`'s
    teardown (`disconnect()` -> `consume_remaining_input()`, which tolerates
    a timeout) does not see a second, unrelated exception."""

    def __init__(self) -> None:
        super().__init__()
        self._raised_once = False

    def read(self, n: int = 1) -> bytes:
        data = super().read(n)
        if not data and not self._raised_once:
            self._raised_once = True
            import serial

            raise serial.SerialException("simulated port drop mid-operation")
        return data


def _write_success_frames() -> list[bytes]:
    """One complete, otherwise-successful COMMAND_WRITE main phase."""
    return [
        build_frame(MSG_INIT_DONE, b""),
        build_frame(MSG_OK_REQ_DATA, b""),
        build_frame(MSG_MAIN_DONE, b""),
        build_frame(MSG_END_DONE, b""),
    ]


def _drive_write(
    tmp_path,
    *,
    eprom_name: str = "at28c256",
    eprom_data: dict | None = None,
    payload: bytes = b"\xaa" * 8,
    frames: list[bytes],
    address_str: str | None = None,
    operation_flags: int = 0,
    suppress_verdict_line: bool = False,
    connect_side_effect=None,
) -> tuple[bool, EpromOperator]:
    """Drive the genuine `write_eprom` over one fake serial connection
    (an unguarded chip by default, so no guard read precedes it) and return
    `(ok, operator)` so callers can inspect the cause-channel attributes."""
    input_file = tmp_path / f"wv_{id(frames)}.bin"
    input_file.write_bytes(payload)

    serial = _FakeSerial()
    for frame in frames:
        serial.feed(frame)

    operator = EpromOperator(ConfigManager())
    side_effect = connect_side_effect or (
        lambda command_dict, config, **kwargs: _comm_factory_for(serial)()
    )
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=side_effect,
    ):
        ok = operator.write_eprom(
            eprom_name,
            eprom_data if eprom_data is not None else _at28c256_data(),
            str(input_file),
            operation_flags=operation_flags,
            address_str=address_str,
            suppress_verdict_line=suppress_verdict_line,
        )
    return ok, operator


# ---------------------------------------------------------------------------
# suppress_verdict_line -- write_eprom
# ---------------------------------------------------------------------------


def test_write_eprom_suppress_verdict_line_true_on_success_no_line_and_returns_true(
    tmp_path, caplog
) -> None:
    with caplog.at_level("INFO", logger="EpromOperator"):
        ok, _ = _drive_write(
            tmp_path,
            frames=_write_success_frames(),
            suppress_verdict_line=True,
        )
    assert ok is True
    messages = [rec.message for rec in caplog.records]
    assert not any(m.startswith("Write to AT28C256 successful") for m in messages)


def test_write_eprom_default_suppress_verdict_line_false_still_logs_success_line(
    tmp_path, caplog
) -> None:
    """Backstop: a suppression that fired unconditionally would pass every
    other leg in this module -- this proves the default path is untouched."""
    with caplog.at_level("INFO", logger="EpromOperator"):
        ok, _ = _drive_write(tmp_path, frames=_write_success_frames())
    assert ok is True
    messages = [rec.message for rec in caplog.records]
    assert any(m.startswith("Write to AT28C256 successful") for m in messages)


def test_write_eprom_suppress_verdict_line_true_on_failure_no_line_and_returns_false(
    tmp_path, caplog
) -> None:
    """A firmware ERROR frame during MAIN fails the write; suppressed, no
    'Write to X failed.' line is logged."""
    frames = [
        build_frame(MSG_INIT_DONE, b""),
        build_frame(MSG_ERR_SETUP, b""),
    ]
    with caplog.at_level("ERROR", logger="EpromOperator"):
        ok, _ = _drive_write(tmp_path, frames=frames, suppress_verdict_line=True)
    assert ok is False
    messages = [rec.message for rec in caplog.records]
    assert "Write to AT28C256 failed." not in messages


def test_write_eprom_default_suppress_verdict_line_false_still_logs_failure_line(
    tmp_path, caplog
) -> None:
    frames = [
        build_frame(MSG_INIT_DONE, b""),
        build_frame(MSG_ERR_SETUP, b""),
    ]
    with caplog.at_level("ERROR", logger="EpromOperator"):
        ok, _ = _drive_write(tmp_path, frames=frames)
    assert ok is False
    messages = [rec.message for rec in caplog.records]
    assert "Write to AT28C256 failed." in messages


# ---------------------------------------------------------------------------
# suppress_verdict_line -- verify_eprom (compare range lines untouched)
# ---------------------------------------------------------------------------


def test_verify_eprom_suppress_verdict_line_true_on_match_no_line_returns_zero(
    make_comm, fake_serial, caplog
) -> None:
    import tempfile

    payload = b"\x01\x02\x03\x04"
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(payload)
        input_file = f.name

    def _fake_find_and_connect(command_dict, config, **kwargs):
        return make_comm()

    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_DATA_CHUNK, payload))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    operator = EpromOperator(ConfigManager())
    with (
        patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ),
        caplog.at_level("INFO", logger="EpromOperator"),
    ):
        verdict = operator.verify_eprom(
            "at28c256",
            _at28c256_data(),
            input_file,
            suppress_verdict_line=True,
        )

    assert verdict == 0
    messages = [rec.message for rec in caplog.records]
    assert not any(m.startswith("Verify for AT28C256 successful") for m in messages)


def test_verify_eprom_suppress_verdict_line_true_on_mismatch_keeps_range_line(
    make_comm, fake_serial, tmp_path, caplog
) -> None:
    """The two verdict lines are suppressed; the compare range line
    (`_drive_region_compare` -> `render_compare_lines`) is NOT -- it is the
    report `--verify` is supposed to produce on a mismatch."""
    payload = b"\x01\x02\x03\x04"
    corrupted = bytearray(payload)
    corrupted[1] ^= 0xFF
    input_file = tmp_path / "in.bin"
    input_file.write_bytes(payload)

    def _fake_find_and_connect(command_dict, config, **kwargs):
        return make_comm()

    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_DATA_CHUNK, bytes(corrupted)))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    operator = EpromOperator(ConfigManager())
    with (
        patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ),
        caplog.at_level("INFO", logger="EpromOperator"),
    ):
        verdict = operator.verify_eprom(
            "at28c256",
            _at28c256_data(),
            str(input_file),
            suppress_verdict_line=True,
        )

    assert verdict == 1
    messages = [rec.message for rec in caplog.records]
    assert any(m.startswith("Mismatch 0x") for m in messages)
    assert "Verify for AT28C256 failed." not in messages
    assert not any(m.startswith("Verify for AT28C256 successful") for m in messages)


def test_verify_eprom_size_str_none_resolves_region_to_input_file_length(
    make_comm, fake_serial, tmp_path
) -> None:
    """`verify_eprom` with `size_str=None` (the default) resolves its
    declared region to the input file's own length -- so `--verify`'s
    region equals the written region for free (D-16), without the CLI tier
    computing it. Proven here by an incomplete read-back: a shorter
    delivered payload than the (longer) file must report verdict 1, not a
    false 0 limited to the bytes actually read."""
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
        verdict = operator.verify_eprom("at28c256", _at28c256_data(), str(input_file))

    assert verdict == 1


# ---------------------------------------------------------------------------
# last_write_guard_verdict -- the four states, pinned here per Task 2
# ---------------------------------------------------------------------------


def test_last_write_guard_verdict_none_when_guard_not_run(tmp_path) -> None:
    """Unguarded protocol (0x0D) -- the guard is skipped entirely."""
    ok, operator = _drive_write(tmp_path, frames=_write_success_frames())
    assert ok is True
    assert operator.last_write_guard_verdict is None


def test_last_write_guard_verdict_two_on_guard_read_transport_failure(
    tmp_path,
) -> None:
    """A guarded chip (M27C512) whose guard-read connection fails: guard
    verdict 2, write never attempted (`last_write_attempt_verdict is None`),
    `write_eprom` returns False, and no COMMAND_WRITE frame is ever sent."""
    input_file = tmp_path / "guard_transport_fail.bin"
    input_file.write_bytes(b"\xaa" * 64)

    def _raise_not_found(command_dict, config, **kwargs):
        raise ProgrammerNotFoundError("No compatible programmer found on any port.")

    operator = EpromOperator(ConfigManager())
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=_raise_not_found,
    ):
        ok = operator.write_eprom("m27c512", _m27c512_data(), str(input_file))

    assert ok is False
    assert operator.last_write_guard_verdict == 2
    assert operator.last_write_attempt_verdict is None


# ---------------------------------------------------------------------------
# last_write_attempt_verdict -- the four states
# ---------------------------------------------------------------------------


def test_last_write_attempt_verdict_none_when_write_never_attempted_guard_refusal(
    tmp_path,
) -> None:
    """A guard refusal (M27C512, non-blank region): the write's own
    operation context is never entered, so `last_write_attempt_verdict`
    stays `None` -- distinct from `last_write_guard_verdict`, which is 1."""
    region_payload = bytearray(b"\xff" * 64)
    region_payload[10] = 0xAB
    guard_read_frames = [
        build_frame(MSG_INIT_DONE, b""),
        build_frame(MSG_DATA_CHUNK, bytes(region_payload)),
        build_frame(MSG_MAIN_DONE, b""),
        build_frame(MSG_END_DONE, b""),
    ]
    input_file = tmp_path / "refused.bin"
    input_file.write_bytes(b"\xaa" * 64)

    serial = _FakeSerial()
    for frame in guard_read_frames:
        serial.feed(frame)

    def _fake_find_and_connect(command_dict, config, **kwargs):
        return _comm_factory_for(serial)()

    operator = EpromOperator(ConfigManager())
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=_fake_find_and_connect,
    ):
        ok = operator.write_eprom("m27c512", _m27c512_data(), str(input_file))

    assert ok is False
    assert operator.last_write_guard_verdict == 1
    assert operator.last_write_attempt_verdict is None


def test_last_write_attempt_verdict_zero_on_successful_write(tmp_path) -> None:
    ok, operator = _drive_write(tmp_path, frames=_write_success_frames())
    assert ok is True
    assert operator.last_write_attempt_verdict == 0


def test_last_write_attempt_verdict_one_on_firmware_error_frame(tmp_path) -> None:
    """A firmware ERROR frame mid-MAIN sets `last_firmware_error_code`, so
    the write phase's own cause classifies as host/firmware-decided (1),
    not transport (2)."""
    frames = [
        build_frame(MSG_INIT_DONE, b""),
        build_frame(MSG_ERR_SETUP, b""),
    ]
    ok, operator = _drive_write(tmp_path, frames=frames)
    assert ok is False
    assert operator.last_firmware_error_code is not None
    assert operator.last_write_attempt_verdict == 1


def test_last_write_attempt_verdict_two_on_connect_failure(tmp_path) -> None:
    """`_setup_operation`'s connect-failure `(None, 0)` arm: the write's own
    `find_and_connect` raises before any frame is exchanged -- transport/
    setup cause, verdict 2, not 1."""

    def _raise_not_found(command_dict, config, **kwargs):
        raise ProgrammerNotFoundError("No compatible programmer found on any port.")

    ok, operator = _drive_write(
        tmp_path,
        frames=[],
        connect_side_effect=_raise_not_found,
    )
    assert ok is False
    assert operator.last_write_attempt_verdict == 2


def test_last_write_attempt_verdict_two_on_mid_write_port_drop(tmp_path) -> None:
    """A genuine transport failure DURING the write (not at connect, not a
    firmware ERROR frame) also classifies as 2 -- arm 3 of the seven-arm
    table, the one a plan review found missing: the write is the longest
    and most hardware-stressed leg, so this is the LIKELIEST transport
    failure of the three, not an exotic one."""
    payload = b"\xaa" * 8
    input_file = tmp_path / "port_drop.bin"
    input_file.write_bytes(payload)

    serial = _PortDropSerial()
    serial.feed(build_frame(MSG_INIT_DONE, b""))
    # No further frames: the next read (MAIN phase's first) raises.

    operator = EpromOperator(ConfigManager())
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=lambda command_dict, config, **kwargs: _comm_factory_for(serial)(),
    ):
        ok = operator.write_eprom("at28c256", _at28c256_data(), str(input_file))

    assert ok is False
    assert operator.last_firmware_error_code is None
    assert operator.last_write_attempt_verdict == 2


def test_last_write_attempt_verdict_one_for_malformed_address_not_two(
    tmp_path,
) -> None:
    """A malformed `-a` value that `require_non_negative_address` silently
    lets through (not numerically negative, just unparsable) still reaches
    `_setup_operation`'s own `parse_address`/`ValueError` arm -- the
    operator's own input was the cause, so verdict 1, not 2."""
    ok, operator = _drive_write(
        tmp_path,
        frames=[],
        address_str="not-an-address",
    )
    assert ok is False
    assert operator.last_write_attempt_verdict == 1


def test_last_write_attempt_verdict_zero_when_skip_sdp_unlock_ack_check_fails(
    make_comm, fake_serial, caplog
) -> None:
    """The `--skip-sdp-unlock` ack check flips `is_ok` to `False` AFTER a
    state-machine run that itself succeeded on the wire -- a host-decided
    failure, so `last_write_attempt_verdict` must stay 0 (recorded BEFORE
    the ack block runs), even though the write ultimately returns False and
    logs 'Write to X failed.'."""
    import logging
    import tempfile

    payload = b"\x01\x02\x03\x04"
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(payload)
        input_file = f.name

    # No MSG_WARN_SDP_UNLOCK_SKIPPED fed -- the ack is absent.
    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_OK_REQ_DATA, b""))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    def _fake_find_and_connect(command_dict, config, **kwargs):
        return make_comm()

    operator = EpromOperator(ConfigManager())
    operation_flags = build_flags(skip_sdp_unlock=True)
    with (
        patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ),
        caplog.at_level(logging.ERROR, logger="EpromOperator"),
    ):
        ok = operator.write_eprom(
            "at28c256",
            _at28c256_data(),
            input_file,
            operation_flags=operation_flags,
        )

    assert ok is False
    assert operator.last_write_attempt_verdict == 0
    messages = [rec.message for rec in caplog.records]
    assert any("did not acknowledge" in m for m in messages)


# ---------------------------------------------------------------------------
# Exhaustiveness pins (Task 2 action text)
# ---------------------------------------------------------------------------


def test_setup_operation_has_exactly_three_none_zero_returns() -> None:
    """The write-side cause classification (`if not cmd_data:` block inside
    `write_eprom`) depends on `_setup_operation` carrying EXACTLY four
    `(None, 0)` return sites: the `parse_address` ValueError arm, the
    `parse_size` arm (gated on `cmd == COMMAND_READ`, unreachable for a
    write), the cold connect-failure arm, and (206-03 SESS-01) the leased
    setup's own rejected-ack arm -- a leased setup whose ack was refused
    is deliberately classified exactly like a cold connect failure, since
    the caller cannot and need not tell the two apart. A fifth arm added
    later would be silently classified as a connection failure without
    this pin. (Test name kept for git-blame continuity; the count moved
    from three to four in 206-03.)"""
    source = inspect.getsource(EpromOperator._setup_operation)
    assert source.count("return None, 0") == 4


def test_write_eprom_verdict_assignment_precedes_skip_sdp_unlock_ack_block() -> None:
    """A verdict assignment that drifted below the `--skip-sdp-unlock` ack
    block would silently reclassify a host-decided ack failure as hardware
    trouble (exit 2 instead of exit 1) -- this pins the source order."""
    source = inspect.getsource(EpromOperator.write_eprom)
    assert source.index("last_write_attempt_verdict") < source.index(
        "MSG_WARN_SDP_UNLOCK_SKIPPED"
    )


def test_suppress_verdict_line_is_keyword_only_and_defaults_false() -> None:
    for name in ("write_eprom", "verify_eprom"):
        param = inspect.signature(getattr(EpromOperator, name)).parameters[
            "suppress_verdict_line"
        ]
        assert param.default is False
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
    return_annotation = inspect.signature(EpromOperator.write_eprom).return_annotation
    assert return_annotation in (bool, "bool")


# =============================================================================
# Task 3 -- `write --verify` and `--full` at the CLI tier
#
# The seven-arm exit-code table (`exit_code_contract_resolved` in
# 203-03-PLAN.md), driven against a `Mock(spec=EpromOperator)` with fixed
# `write_eprom`/`verify_eprom` return values and fixed `last_write_guard_verdict`
# / `last_write_attempt_verdict`, one test per arm -- the
# `tests/test_cli_handlers.py` house style (see its `test_verify_*` legs).
# =============================================================================


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _app_with_operator(operator: Mock):
    return _make_app_context(eprom_operator=operator)


def _run_write(runner: CliRunner, operator: Mock, extra_args: list[str] | None = None):
    app = _app_with_operator(operator)
    args = ["write", "W27C512", "in.bin", *(extra_args or [])]
    return runner.invoke(cli, args, obj=app)


def test_write_verify_help_lists_verify_and_full(runner: CliRunner) -> None:
    import re

    result = runner.invoke(cli, ["write", "--help"])
    assert result.exit_code == 0
    assert "--verify" in result.output
    assert "--full" in result.output
    # D-13: all three exit codes and the "plain write is unchanged" clause
    # must be stated. Click wraps and re-indents help text, so collapse all
    # whitespace runs to a single space before substring-matching.
    collapsed = re.sub(r"\s+", " ", result.output)
    assert "0 the write landed" in collapsed
    assert "1 the invocation ended" in collapsed
    assert "2 the transport" in collapsed
    assert "Without --verify, write exits 0" in collapsed


def test_write_full_without_verify_is_a_usage_error(runner: CliRunner) -> None:
    operator = Mock(spec=EpromOperator)
    result = _run_write(runner, operator, extra_args=["--full"])
    assert result.exit_code != 0
    assert operator.write_eprom.call_count == 0


def test_write_without_verify_never_calls_verify_eprom_regardless_of_verdicts(
    runner: CliRunner,
) -> None:
    """Arm irrelevant to plain write: `write` without `--verify` never calls
    `verify_eprom`, and exits 0/1 purely off `write_eprom`'s bool, regardless
    of what either cause-channel attribute holds."""
    operator = Mock(spec=EpromOperator)
    operator.write_eprom.return_value = False
    operator.last_write_guard_verdict = 2
    operator.last_write_attempt_verdict = 2
    result = _run_write(runner, operator)
    assert result.exit_code == 1
    assert operator.verify_eprom.call_count == 0

    operator2 = Mock(spec=EpromOperator)
    operator2.write_eprom.return_value = True
    operator2.last_write_guard_verdict = 2
    operator2.last_write_attempt_verdict = 2
    result2 = _run_write(runner, operator2)
    assert result2.exit_code == 0
    assert operator2.verify_eprom.call_count == 0


def test_write_verify_arm1_guard_refusal_exits_1_and_prints_nothing_more(
    runner: CliRunner,
) -> None:
    """Arm 1: guard verdict 1. The guard already printed its own one line
    (via logger, not click.echo) -- write's own output must be empty, a
    line-list equality against the empty list."""
    operator = Mock(spec=EpromOperator)
    operator.write_eprom.return_value = False
    operator.last_write_guard_verdict = 1
    operator.last_write_attempt_verdict = None
    result = _run_write(runner, operator, extra_args=["--verify"])
    assert result.exit_code == 1
    assert operator.verify_eprom.call_count == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert lines == []


def test_write_verify_arm2_guard_read_transport_failure_exits_2_no_write_line(
    runner: CliRunner,
) -> None:
    """Arm 2: guard verdict 2 -- the guard read itself failed for a
    transport/hardware reason, the write never ran. Exit 2, the NO-WRITE
    line (not could-not-verify: nothing landed)."""
    operator = Mock(spec=EpromOperator)
    operator.write_eprom.return_value = False
    operator.last_write_guard_verdict = 2
    operator.last_write_attempt_verdict = None
    result = _run_write(runner, operator, extra_args=["--verify"])
    assert result.exit_code == 2
    assert operator.verify_eprom.call_count == 0
    from firestarter.cli_handlers import (
        _WRITE_VERIFY_VERDICT_NO_WRITE,
        _WRITE_VERIFY_VERDICT_UNREADABLE,
    )

    expected = _WRITE_VERIFY_VERDICT_NO_WRITE.format(eprom="W27C512")
    assert expected in result.output
    unreadable = _WRITE_VERIFY_VERDICT_UNREADABLE.format(eprom="W27C512")
    assert unreadable not in result.output


def test_write_verify_arm3_write_transport_failure_exits_2_no_write_line(
    runner: CliRunner,
) -> None:
    """Arm 3: the write itself was attempted and failed for a transport,
    connection, or hardware reason (`last_write_attempt_verdict == 2`,
    `last_write_guard_verdict` is NOT 1 or 2 -- e.g. None, the guard was
    skipped). This is the arm a plan review found missing; it must exit 2,
    not 1, and print the same no-write line as arm 2."""
    operator = Mock(spec=EpromOperator)
    operator.write_eprom.return_value = False
    operator.last_write_guard_verdict = None
    operator.last_write_attempt_verdict = 2
    result = _run_write(runner, operator, extra_args=["--verify"])
    assert result.exit_code == 2
    assert operator.verify_eprom.call_count == 0
    from firestarter.cli_handlers import _WRITE_VERIFY_VERDICT_NO_WRITE

    expected = _WRITE_VERIFY_VERDICT_NO_WRITE.format(eprom="W27C512")
    assert expected in result.output


def test_write_verify_arm4_host_or_firmware_decided_failure_exits_1(
    runner: CliRunner,
) -> None:
    """Arm 4: the write failed for a reason the host or the firmware
    decided (`last_write_attempt_verdict == 1`). Exit 1, the no-write
    line."""
    operator = Mock(spec=EpromOperator)
    operator.write_eprom.return_value = False
    operator.last_write_guard_verdict = None
    operator.last_write_attempt_verdict = 1
    result = _run_write(runner, operator, extra_args=["--verify"])
    assert result.exit_code == 1
    assert operator.verify_eprom.call_count == 0
    from firestarter.cli_handlers import _WRITE_VERIFY_VERDICT_NO_WRITE

    expected = _WRITE_VERIFY_VERDICT_NO_WRITE.format(eprom="W27C512")
    assert expected in result.output


def test_write_verify_arm5_readback_transport_failure_exits_2_unreadable_line(
    runner: CliRunner,
) -> None:
    """Arm 5: the write landed (`write_eprom` returns True) but the
    read-back itself failed for a transport/hardware reason
    (`verify_eprom` returns 2). This is the ONLY arm that prints the
    could-not-verify line."""
    operator = Mock(spec=EpromOperator)
    operator.write_eprom.return_value = True
    operator.verify_eprom.return_value = 2
    result = _run_write(runner, operator, extra_args=["--verify"])
    assert result.exit_code == 2
    assert operator.verify_eprom.call_count == 1
    from firestarter.cli_handlers import _WRITE_VERIFY_VERDICT_UNREADABLE

    expected = _WRITE_VERIFY_VERDICT_UNREADABLE.format(eprom="W27C512")
    assert expected in result.output


def test_write_verify_arm6_readback_mismatch_exits_1_mismatch_line(
    runner: CliRunner,
) -> None:
    """Arm 6: the write landed; the read-back completed and mismatched
    (`verify_eprom` returns 1). Exit 1, the mismatch line."""
    operator = Mock(spec=EpromOperator)
    operator.write_eprom.return_value = True
    operator.verify_eprom.return_value = 1
    result = _run_write(runner, operator, extra_args=["--verify"])
    assert result.exit_code == 1
    from firestarter.cli_handlers import _WRITE_VERIFY_VERDICT_MISMATCH

    expected = _WRITE_VERIFY_VERDICT_MISMATCH.format(eprom="W27C512")
    assert expected in result.output


def test_write_verify_arm7_readback_match_exits_0_ok_line(
    runner: CliRunner,
) -> None:
    """Arm 7: the write landed; the read-back matched (`verify_eprom`
    returns 0). Exit 0, the verified line, no 'successful' anywhere."""
    operator = Mock(spec=EpromOperator)
    operator.write_eprom.return_value = True
    operator.verify_eprom.return_value = 0
    result = _run_write(runner, operator, extra_args=["--verify"])
    assert result.exit_code == 0
    from firestarter.cli_handlers import _WRITE_VERIFY_VERDICT_OK

    expected = _WRITE_VERIFY_VERDICT_OK.format(eprom="W27C512")
    assert expected in result.output
    assert "successful" not in result.output.lower()


def test_write_verify_full_flag_passed_through_as_true(runner: CliRunner) -> None:
    operator = Mock(spec=EpromOperator)
    operator.write_eprom.return_value = True
    operator.verify_eprom.return_value = 0
    _run_write(runner, operator, extra_args=["--verify", "--full"])
    assert operator.verify_eprom.call_args.kwargs["full"] is True


def test_write_verify_alone_passes_full_false(runner: CliRunner) -> None:
    operator = Mock(spec=EpromOperator)
    operator.write_eprom.return_value = True
    operator.verify_eprom.return_value = 0
    _run_write(runner, operator, extra_args=["--verify"])
    assert operator.verify_eprom.call_args.kwargs["full"] is False


def test_write_verify_suppresses_write_eprom_verdict_line_only_when_set(
    runner: CliRunner,
) -> None:
    """`write_eprom` is called with `suppress_verdict_line=verify` -- True
    under `--verify`, False on the plain path."""
    operator = Mock(spec=EpromOperator)
    operator.write_eprom.return_value = True
    operator.verify_eprom.return_value = 0
    _run_write(runner, operator, extra_args=["--verify"])
    assert operator.write_eprom.call_args.kwargs["suppress_verdict_line"] is True

    operator2 = Mock(spec=EpromOperator)
    operator2.write_eprom.return_value = True
    _run_write(runner, operator2)
    assert operator2.write_eprom.call_args.kwargs["suppress_verdict_line"] is False


def test_could_not_verify_line_absent_from_the_other_six_arms(
    runner: CliRunner,
) -> None:
    """The could-not-verify line (arm 5's own) must never appear on any of
    the other six arms."""
    from firestarter.cli_handlers import _WRITE_VERIFY_VERDICT_UNREADABLE

    unreadable = _WRITE_VERIFY_VERDICT_UNREADABLE.format(eprom="W27C512")

    arms: list[tuple[Mock, list[str]]] = []

    op1 = Mock(spec=EpromOperator)
    op1.write_eprom.return_value = False
    op1.last_write_guard_verdict = 1
    op1.last_write_attempt_verdict = None
    arms.append((op1, ["--verify"]))

    op2 = Mock(spec=EpromOperator)
    op2.write_eprom.return_value = False
    op2.last_write_guard_verdict = 2
    op2.last_write_attempt_verdict = None
    arms.append((op2, ["--verify"]))

    op3 = Mock(spec=EpromOperator)
    op3.write_eprom.return_value = False
    op3.last_write_guard_verdict = None
    op3.last_write_attempt_verdict = 2
    arms.append((op3, ["--verify"]))

    op4 = Mock(spec=EpromOperator)
    op4.write_eprom.return_value = False
    op4.last_write_guard_verdict = None
    op4.last_write_attempt_verdict = 1
    arms.append((op4, ["--verify"]))

    op6 = Mock(spec=EpromOperator)
    op6.write_eprom.return_value = True
    op6.verify_eprom.return_value = 1
    arms.append((op6, ["--verify"]))

    op7 = Mock(spec=EpromOperator)
    op7.write_eprom.return_value = True
    op7.verify_eprom.return_value = 0
    arms.append((op7, ["--verify"]))

    for operator, extra_args in arms:
        result = _run_write(runner, operator, extra_args=extra_args)
        assert unreadable not in result.output, (operator, result.output)


def test_verdict_constants_never_contain_the_forbidden_word() -> None:
    """WRITE-05's structural guarantee, asserted over the four constants
    themselves -- not over one rendered run."""
    from firestarter.cli_handlers import (
        _WRITE_VERIFY_VERDICT_MISMATCH,
        _WRITE_VERIFY_VERDICT_NO_WRITE,
        _WRITE_VERIFY_VERDICT_OK,
        _WRITE_VERIFY_VERDICT_UNREADABLE,
    )

    for constant in (
        _WRITE_VERIFY_VERDICT_OK,
        _WRITE_VERIFY_VERDICT_MISMATCH,
        _WRITE_VERIFY_VERDICT_UNREADABLE,
        _WRITE_VERIFY_VERDICT_NO_WRITE,
    ):
        assert "successful" not in constant.lower()


def test_write_verify_calls_verify_eprom_with_size_str_none_and_force_flag(
    runner: CliRunner,
) -> None:
    operator = Mock(spec=EpromOperator)
    operator.write_eprom.return_value = True
    operator.verify_eprom.return_value = 0
    _run_write(runner, operator, extra_args=["--verify", "-f"])
    kwargs = operator.verify_eprom.call_args.kwargs
    assert kwargs["size_str"] is None
    assert kwargs["suppress_verdict_line"] is True
