"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

203-CR-01 fix pass: the pre-write blank guard's own read connect and
`write_eprom`'s own COMMAND_WRITE connect are two independent
`_operation_context` calls. Before this fix, neither one told
`find_and_connect` to stay on the same physical port as the other, so on a
bench with more than one RURP-compatible board enumerable, the guard could
prove board A's target region blank and the write could then land on board
B -- writing over a chip that was never actually checked.

This module proves the fix end to end, through the genuine `write_eprom`
path (no mocking of `write_eprom` itself). The fake `find_and_connect` here
is hand-written, not `connect_via`: it decides which board answers from the
port keywords the operator passes, which `connect_via` does not see.
"""

from __future__ import annotations

from unittest.mock import patch

from firestarter.config import ConfigManager
from firestarter.constants import COMMAND_READ, COMMAND_WRITE
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import ProgrammerNotFoundError

from .conftest import fresh_serial_and_comm, read_phase_frames, write_success_frames
from .test_write_blank_guard import _m27c512_data


def _board(port_name: str, frames: list[bytes]):
    """A communicator factory for one board on `port_name` that replies with
    `frames`."""
    serial, factory = fresh_serial_and_comm(port_name=port_name)
    for frame in frames:
        serial.feed(frame)
    return factory


def test_write_connect_pins_to_guard_resolved_port_and_fails_closed_when_it_drops(
    tmp_path,
) -> None:
    """203-CR-01, end to end, in one drive:

    (1) `write_eprom`'s own COMMAND_WRITE connect is requested with
        `preferred_port` equal to the exact port the guard's COMMAND_READ
        connect resolved, and `restrict_to_port=True` -- proven directly off
        the captured `find_and_connect` call kwargs, not inferred from a
        side effect.
    (2) When that exact port cannot be reached a second time (board A goes
        quiet -- unplugged, reset by the guard's own disconnect, whatever
        the cause), the write FAILS CLOSED: it returns `False` with a
        transport-failure verdict, and never opens a third connect that
        could land on a different board ("board B").

    Without the fix, `write_eprom`'s second connect carries no port
    override at all, so this test's fake `find_and_connect` falls through
    to its "unpinned discovery" branch and hands the write board B's own
    working comm -- the write then proceeds and this test's assertions on
    `calls[1]` and on `ok` fail. That fall-through branch is not a
    contrived double: it is the exact "probing continued and the caller was
    handed a DIFFERENT board's identity" behaviour
    `_list_potential_ports`'s own docstring describes, reproduced here
    through the same `find_and_connect` seam `_setup_operation` actually
    calls.
    """
    payload = b"\xaa" * 64
    region_payload = b"\xff" * 64  # blank across the whole target region

    board_a_comm_factory = _board("/dev/ttyACM0", read_phase_frames(region_payload))
    board_b_comm_factory = _board("/dev/ttyACM1", write_success_frames())

    calls: list[tuple[int, dict]] = []

    def _fake_find_and_connect(command_dict, config, **kwargs):
        calls.append((command_dict["cmd"], kwargs))
        if command_dict["cmd"] == COMMAND_READ:
            # The guard's own connect: ordinary discovery lands on board A.
            return board_a_comm_factory()
        # COMMAND_WRITE: board A has gone quiet by the time this second
        # connect is attempted. Only a connect that is explicitly PINNED
        # and RESTRICTED to board A's own port is entitled to see that --
        # everything else is the unpinned, pre-fix discovery path, which
        # falls through to board B.
        if (
            kwargs.get("preferred_port") == "/dev/ttyACM0"
            and kwargs.get("restrict_to_port") is True
        ):
            raise ProgrammerNotFoundError(
                "No compatible programmer answered on /dev/ttyACM0."
            )
        return board_b_comm_factory()

    input_file = tmp_path / "wbg_pin_test.bin"
    input_file.write_bytes(payload)

    operator = EpromOperator(ConfigManager())
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=_fake_find_and_connect,
    ):
        ok = operator.write_eprom(
            "m27c512",
            _m27c512_data(),
            str(input_file),
        )

    # --- Property 1: the write's own connect is pinned to the guard's port.
    assert [c[0] for c in calls] == [COMMAND_READ, COMMAND_WRITE], (
        "the write must never reach COMMAND_WRITE without having opened the "
        "guard's COMMAND_READ connect first"
    )
    write_kwargs = calls[1][1]
    assert write_kwargs.get("preferred_port") == "/dev/ttyACM0", (
        "write_eprom's own COMMAND_WRITE connect was not pinned to the "
        "exact port the guard's read just proved blank -- CR-01's defect"
    )
    assert write_kwargs.get("restrict_to_port") is True, (
        "the pin must be a hard restriction (the ONLY candidate), not a "
        "mere preference that discovery can still fall through past"
    )

    # --- Property 2: fail closed. Never a third connect, never board B.
    assert len(calls) == 2, (
        "a dropped pinned port must fail the write outright, not fall "
        "through to a further discovery attempt against a different board"
    )
    assert ok is False
    assert operator.last_write_attempt_verdict == 2

    # last_write_port must reflect that the write's own connect never
    # actually succeeded -- write_eprom only ever sets it after a
    # successful COMMAND_WRITE connect.
    assert operator.last_write_port is None


def test_write_connect_uses_guard_port_when_it_is_still_reachable(tmp_path) -> None:
    """Positive control for the same mechanism: when the guard-resolved
    port answers again on the second connect, the write proceeds on it
    normally, `last_write_port` records it, and no unpinned discovery is
    ever attempted."""
    payload = b"\xaa" * 64
    region_payload = b"\xff" * 64  # blank across the whole target region

    board_a_read_factory = _board("/dev/ttyACM0", read_phase_frames(region_payload))
    board_a_write_factory = _board("/dev/ttyACM0", write_success_frames())

    calls: list[tuple[int, dict]] = []

    def _fake_find_and_connect(command_dict, config, **kwargs):
        calls.append((command_dict["cmd"], kwargs))
        if command_dict["cmd"] == COMMAND_READ:
            return board_a_read_factory()
        assert kwargs.get("preferred_port") == "/dev/ttyACM0"
        assert kwargs.get("restrict_to_port") is True
        return board_a_write_factory()

    input_file = tmp_path / "wbg_pin_positive_test.bin"
    input_file.write_bytes(payload)

    operator = EpromOperator(ConfigManager())
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=_fake_find_and_connect,
    ):
        ok = operator.write_eprom(
            "m27c512",
            _m27c512_data(),
            str(input_file),
        )

    assert ok is True
    assert operator.last_write_port == "/dev/ttyACM0"
    assert len(calls) == 2
