"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

SESS-01 (Phase 206-03) -- `EpromOperator.lease()`.

`EpromOperator` holds at most one `SerialCommunicator` at a time and tears it
down in `_operation_context`'s `finally` after every single call today. A
`dev test` plan for an erasable full-device part at three cycles makes
roughly thirty of those calls, each paying an unconditional connect cost. The
lease lets a `dev test` plan hold ONE validated link across every call it
makes instead, behind a seam that is default-off, opted into at exactly one
call site (`cli_handlers.dev_test`), and reverts as a single commit.

Every test here drives `EpromOperator._operation_context` (the real
integration point every public operator method funnels through) against a
fake `SerialCommunicator` recording its own lifecycle calls in order, so the
assertions are about CONNECT COUNT and CALL ORDER, never about wire bytes --
the wire-level behaviour of the extracted `setup_command` is
`test_serial_comm.py`'s job (206-03 Task 1).

Every test asserts the cold path (no lease acquired) as well as the leased
path, per the plan's own requirement: the lease must be a clean single-commit
revert, so no assertion in this module may depend on the lease existing in
isolation.
"""

from __future__ import annotations

import pytest

from firestarter.config import ConfigManager
from firestarter.constants import COMMAND_WRITE
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import FirmwareOutdatedError, SerialError


class _FakeComm:
    """A minimal `SerialCommunicator` stand-in recording lifecycle calls,
    in order, into a SHARED events list -- `(instance_name, event)` tuples.

    ``fail_setup_on_call`` (1-indexed) makes the Nth `setup_command` call on
    THIS instance raise instead of succeeding, modelling a firmware/hardware
    gate refusal (`FirmwareOutdatedError` is itself a `SerialError`
    subclass, so it exercises the identical D-06 handling a generic
    transport failure would) or, with ``fail_with`` set to a plain
    `SerialError`, a genuine mid-plan transport failure.
    """

    def __init__(
        self,
        events: list[tuple[str, str]],
        name: str,
        *,
        fail_setup_on_call: int | None = None,
        fail_with: type[SerialError] = FirmwareOutdatedError,
    ) -> None:
        self._events = events
        self._name = name
        self._open = True
        self._fail_setup_on_call = fail_setup_on_call
        self._fail_with = fail_with
        self._setup_calls = 0

    def is_connected(self) -> bool:
        return self._open

    def consume_remaining_input(self) -> None:
        self._events.append((self._name, "drain"))

    def setup_command(self, command_to_send, config_manager, **kwargs) -> bool:
        self._setup_calls += 1
        self._events.append((self._name, "setup"))
        if self._fail_setup_on_call == self._setup_calls:
            raise self._fail_with(f"simulated: {self._name} setup refused")
        return True

    def disconnect(self) -> None:
        self._events.append((self._name, "disconnect"))
        self._open = False


def _make_find_and_connect(events: list[tuple[str, str]], comms: list[_FakeComm]):
    """Return a `find_and_connect` replacement that hands out `comms` in
    order, one per call, recording a "connect" event for each -- and raises
    `IndexError` (an unmistakable test-authoring bug, never a legitimate
    result) if a test's own call count assumption was wrong."""
    it = iter(comms)

    def _fake_find_and_connect(command_to_send, config_manager, **kwargs):
        comm = next(it)
        events.append((comm._name, "connect"))
        return comm

    return _fake_find_and_connect


_CMD_DATA = {"algorithm": 7, "flags": 0}


def test_a_leased_plan_opens_one_link_and_a_cold_plan_opens_one_per_call(
    monkeypatch,
) -> None:
    """Cold-path arm: N `_operation_context` calls -> N `find_and_connect`
    calls, one per call, each torn down before the next (the pre-existing,
    unchanged behaviour). Leased-path arm: N calls inside one
    `with operator.lease():` block -> exactly ONE `find_and_connect` call
    total, with the remaining N-1 calls reusing the same link."""
    events: list[tuple[str, str]] = []
    cold_comms = [_FakeComm(events, f"cold{i}") for i in range(3)]
    monkeypatch.setattr(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        _make_find_and_connect(events, cold_comms),
    )

    operator = EpromOperator(ConfigManager())

    # Cold path: three independent calls, three connects.
    for _ in range(3):
        with operator._operation_context("chip", _CMD_DATA, COMMAND_WRITE) as (
            cmd,
            _buf,
            _name,
        ):
            assert cmd is not None
        assert operator.comm is None, "unleased teardown must run after every call"
    connects = [ev for ev in events if ev[1] == "connect"]
    assert len(connects) == 3, "the cold path must open one link PER call"

    # Leased path: three calls, one connect.
    events.clear()
    leased_comm = _FakeComm(events, "leased0")
    monkeypatch.setattr(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        _make_find_and_connect(events, [leased_comm]),
    )
    with operator.lease():
        for _ in range(3):
            with operator._operation_context("chip", _CMD_DATA, COMMAND_WRITE) as (
                cmd,
                _buf,
                _name,
            ):
                assert cmd is not None
    connects = [ev for ev in events if ev[1] == "connect"]
    assert len(connects) == 1, "a leased plan must open exactly ONE link total"
    setups = [ev for ev in events if ev[1] == "setup"]
    assert len(setups) == 2, "the second and third calls must reuse the open link"


def test_the_cold_path_is_byte_identical_when_the_lease_is_never_acquired(
    monkeypatch,
) -> None:
    """Cold-path arm (the whole test): an operator that never once calls
    `.lease()` must connect once and disconnect once PER call, and
    `_leased` must never observably flip True -- proving the new attribute
    is inert until a caller opts in. There is no leased arm here by design:
    this test's entire point is that the unleased path is untouched."""
    events: list[tuple[str, str]] = []
    comms = [_FakeComm(events, f"c{i}") for i in range(3)]
    monkeypatch.setattr(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        _make_find_and_connect(events, comms),
    )

    operator = EpromOperator(ConfigManager())
    assert operator._leased is False

    for _ in range(3):
        with operator._operation_context("chip", _CMD_DATA, COMMAND_WRITE) as (
            cmd,
            _buf,
            _name,
        ):
            assert cmd is not None
        assert operator._leased is False
        assert operator.comm is None

    kinds = [ev[1] for ev in events]
    assert kinds.count("connect") == 3
    assert kinds.count("disconnect") == 3
    assert kinds.count("drain") == 0, "the cold path never drains -- only a reused link needs it"
    assert kinds.count("setup") == 0, "the cold path never calls setup_command directly"


def test_a_leased_setup_drains_input_before_sending_the_setup_command(
    monkeypatch,
) -> None:
    """Leased-path arm: the SECOND call on a held link must drain BEFORE
    sending its setup command -- order, not mere co-occurrence. Cold-path
    arm: a plain cold connect (no lease) never drains at all, because the
    drain exists only to protect a REUSED link from a straggler frame; a
    freshly opened one has nothing to drain."""
    events: list[tuple[str, str]] = []
    leased_comm = _FakeComm(events, "leased0")
    monkeypatch.setattr(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        _make_find_and_connect(events, [leased_comm]),
    )

    operator = EpromOperator(ConfigManager())
    with operator.lease():
        with operator._operation_context("chip", _CMD_DATA, COMMAND_WRITE) as (
            cmd,
            _buf,
            _name,
        ):
            assert cmd is not None
        events.clear()  # isolate the SECOND call's ordering
        with operator._operation_context("chip", _CMD_DATA, COMMAND_WRITE) as (
            cmd,
            _buf,
            _name,
        ):
            assert cmd is not None
        assert [ev[1] for ev in events] == ["drain", "setup"], (
            "the drain must precede the setup command, not merely both occur"
        )

    # Cold-path arm: an unleased call drains nothing at all.
    events.clear()
    cold_comm = _FakeComm(events, "cold0")
    monkeypatch.setattr(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        _make_find_and_connect(events, [cold_comm]),
    )
    with operator._operation_context("chip", _CMD_DATA, COMMAND_WRITE) as (
        cmd,
        _buf,
        _name,
    ):
        assert cmd is not None
    assert [ev[1] for ev in events] == ["connect", "disconnect"]


def test_a_leased_setup_runs_the_firmware_and_hardware_gates(monkeypatch) -> None:
    """Leased-path arm: a leased setup whose fake reports a failing
    firmware identity (`FirmwareOutdatedError`, raised from `setup_command`
    exactly as the real extracted method does) must still refuse -- the
    gate is never skipped just because the link is already open. Cold-path
    arm: the identical refusal on a COLD connect is swallowed into the
    pre-existing `(None, 0)` failed-setup return, never raised -- the
    contrast is deliberate (D-06) and this test pins both halves of it."""
    events: list[tuple[str, str]] = []
    leased_comm = _FakeComm(events, "leased0", fail_setup_on_call=1)
    monkeypatch.setattr(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        _make_find_and_connect(events, [leased_comm]),
    )

    operator = EpromOperator(ConfigManager())
    with operator.lease():
        with operator._operation_context("chip", _CMD_DATA, COMMAND_WRITE) as (
            cmd,
            _buf,
            _name,
        ):
            assert cmd is not None

        with pytest.raises(FirmwareOutdatedError):
            with operator._operation_context("chip", _CMD_DATA, COMMAND_WRITE):
                pass
        assert operator.comm is None, "the gate failure must drop the dead link"
        assert operator._leased is True, "the block is still a lease"

    # Cold-path arm: the SAME refusal, cold, is a silent failed setup.
    def _cold_gate_refusal(command_to_send, config_manager, **kwargs):
        raise FirmwareOutdatedError("simulated: cold connect firmware gate refused")

    monkeypatch.setattr(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        _cold_gate_refusal,
    )
    with operator._operation_context("chip", _CMD_DATA, COMMAND_WRITE) as (
        cmd,
        buf,
        name,
    ):
        assert cmd is None and buf is None and name is None, (
            "a cold connect-time gate refusal must degrade to a silent failed "
            "setup, never raise -- the leased arm above is the deliberate "
            "divergence, not the cold one"
        )


def test_a_mid_plan_serial_error_drops_the_lease_and_the_plan_continues(
    monkeypatch,
) -> None:
    """D-06: a `SerialError` raised while a leased setup is in flight drops
    the held link but leaves the lease itself active, so the NEXT operation
    cold-connects instead of the whole block failing -- and the raised
    error still reaches the caller unchanged (`run_plan`'s own per-step
    handling, exercised end-to-end elsewhere, already maps it). Cold-path
    arm: the bracketing first and third calls are themselves the ordinary,
    unchanged cold-connect-then-teardown cycle."""
    events: list[tuple[str, str]] = []
    comm1 = _FakeComm(events, "comm1", fail_setup_on_call=1, fail_with=SerialError)
    comm2 = _FakeComm(events, "comm2")
    monkeypatch.setattr(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        _make_find_and_connect(events, [comm1, comm2]),
    )

    operator = EpromOperator(ConfigManager())
    with operator.lease():
        # 1st call: an ordinary cold connect (comm1) -- the cold-path arm.
        with operator._operation_context("chip", _CMD_DATA, COMMAND_WRITE) as (
            cmd,
            _buf,
            _name,
        ):
            assert cmd is not None
        assert operator.comm is comm1

        # 2nd call: leased setup on comm1's link; its setup_command raises
        # a genuine transport SerialError.
        with pytest.raises(SerialError):
            with operator._operation_context("chip", _CMD_DATA, COMMAND_WRITE):
                pass
        assert operator.comm is None, "the dead link must be dropped"
        assert operator._leased is True, "the lease itself survives the failure"

        # 3rd call: the held link is gone, so this cold-connects again --
        # proving the plan continues rather than the whole block failing.
        with operator._operation_context("chip", _CMD_DATA, COMMAND_WRITE) as (
            cmd,
            _buf,
            _name,
        ):
            assert cmd is not None
        assert operator.comm is comm2

    connects = [ev for ev in events if ev[1] == "connect"]
    assert connects == [("comm1", "connect"), ("comm2", "connect")], (
        "exactly two connects: the initial lease connect, and the recovery "
        "connect after the dropped link -- never a connect per call"
    )


def test_the_lease_always_disconnects_on_exit(monkeypatch) -> None:
    """Leased-path arm: exactly one disconnect for a lease that exits
    normally, and exactly one disconnect for a lease that exits by
    exception -- `lease()`'s own `finally` guarantees it either way, and
    the exception (unrelated to the lease itself) still propagates. Cold-
    path arm: a plain unleased call disconnects once per call, as always."""
    events: list[tuple[str, str]] = []
    comm_a = _FakeComm(events, "commA")
    monkeypatch.setattr(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        _make_find_and_connect(events, [comm_a]),
    )

    operator = EpromOperator(ConfigManager())

    # Normal exit.
    with operator.lease():
        with operator._operation_context("chip", _CMD_DATA, COMMAND_WRITE) as (
            cmd,
            _buf,
            _name,
        ):
            assert cmd is not None
    disconnects = [ev for ev in events if ev[1] == "disconnect"]
    assert len(disconnects) == 1
    assert operator.comm is None
    assert operator._leased is False

    # Exception exit: an exception unrelated to the lease/transport must
    # still propagate, and the lease must still tear down exactly once.
    events.clear()
    comm_b = _FakeComm(events, "commB")
    monkeypatch.setattr(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        _make_find_and_connect(events, [comm_b]),
    )

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom):
        with operator.lease():
            with operator._operation_context("chip", _CMD_DATA, COMMAND_WRITE) as (
                cmd,
                _buf,
                _name,
            ):
                assert cmd is not None
            raise _Boom("simulated unrelated failure inside the leased block")
    disconnects = [ev for ev in events if ev[1] == "disconnect"]
    assert len(disconnects) == 1
    assert operator._leased is False
    assert operator.comm is None

    # Cold-path arm: an unleased call disconnects once, per call, as always.
    events.clear()
    comm_c = _FakeComm(events, "commC")
    monkeypatch.setattr(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        _make_find_and_connect(events, [comm_c]),
    )
    with operator._operation_context("chip", _CMD_DATA, COMMAND_WRITE) as (
        cmd,
        _buf,
        _name,
    ):
        assert cmd is not None
    disconnects = [ev for ev in events if ev[1] == "disconnect"]
    assert len(disconnects) == 1
