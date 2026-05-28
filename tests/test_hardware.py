"""Phase 42 / ERR-03 coverage lift for ``HardwareManager`` READ-SIDE voltage
methods (D-14.5).

SAFETY BOUNDARY: this file does NOT exercise ``set_vpp_voltage`` or
``set_vpe_voltage`` — those engage the VPP regulator (12V) on real hardware
and have no safe coverage path. Their low coverage is a deliberate safety
posture, not a coverage hole. v1.9 RCA may surface a way to cover them
safely; that work is explicitly out of v1.8 scope.

Test pattern: patch ``SerialCommunicator.find_and_connect`` to return a
``make_comm()``-built communicator wired to ``fake_serial``; feed the wire
frames the firmware would emit.
"""

from typing import Iterator  # noqa: UP035
from unittest.mock import patch

import pytest

from firestarter.config import ConfigManager
from firestarter.hardware import HardwareManager
from firestarter.messages import MSG_END_DONE


def _ok_frame_bytes() -> bytes:
    """A text-line 'OK: ...' frame the SerialCommunicator parser will see."""
    return b"OK: ready\n"


def _error_frame_bytes() -> bytes:
    """A text-line 'ERROR: ...' frame the parser will surface as a failure path."""
    return b"ERROR: simulated\n"


@pytest.fixture
def hw_config(tmp_path, monkeypatch) -> Iterator[ConfigManager]:
    """Fresh ConfigManager rooted in tmp_path (no ~/.firestarter pollution)."""
    monkeypatch.setattr("firestarter.config.HOME_PATH", str(tmp_path))
    ConfigManager._instances.clear()
    ConfigManager._initialized_configs.clear()
    yield ConfigManager(config_filename="t_hw.json")


def test_get_hardware_revision_happy_path(hw_config, make_comm, fake_serial) -> None:
    """get_hardware_revision succeeds when find_and_connect returns a comm
    that yields an OK ack frame."""
    fake_serial.feed(_ok_frame_bytes())  # The expect_ack() inside picks this up
    fake_serial.feed(MSG_END_DONE.to_bytes(1, "big"))  # any trailing byte is fine
    comm = make_comm()

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ):
        ok = hw.get_hardware_revision()
    assert ok is True


def test_get_hardware_revision_failure_path(hw_config, make_comm, fake_serial) -> None:
    """get_hardware_revision returns False when find_and_connect raises
    a transport-level error."""
    from firestarter.exceptions import ProgrammerNotFoundError

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=ProgrammerNotFoundError("no port"),
    ):
        ok = hw.get_hardware_revision()
    assert ok is False


def test_read_vpp_voltage_finish_on_ok(hw_config, make_comm, fake_serial) -> None:
    """read_vpp_voltage returns True when the firmware emits the trailing OK
    (finish-of-stream signal) right after the ready handshake."""
    # Two OK frames: first is the "ready" handshake; second is the loop-end signal.
    fake_serial.feed(_ok_frame_bytes())
    fake_serial.feed(b"OK: finished\n")
    comm = make_comm()

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ):
        ok = hw.read_vpp_voltage()
    assert ok is True


def test_read_vpe_voltage_finish_on_ok(hw_config, make_comm, fake_serial) -> None:
    """read_vpe_voltage mirrors read_vpp_voltage — same code path, different state."""
    fake_serial.feed(_ok_frame_bytes())
    fake_serial.feed(b"OK: finished\n")
    comm = make_comm()

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ):
        ok = hw.read_vpe_voltage()
    assert ok is True


def test_read_vpp_voltage_error_response_returns_false(
    hw_config, make_comm, fake_serial
) -> None:
    """An ERROR response after the ready handshake returns False (not raises)."""
    fake_serial.feed(_ok_frame_bytes())  # ready handshake
    fake_serial.feed(_error_frame_bytes())  # immediate ERROR in the read loop
    comm = make_comm()

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ):
        ok = hw.read_vpp_voltage()
    assert ok is False


def test_read_vpp_voltage_ready_not_ok_returns_false(
    hw_config, make_comm, fake_serial
) -> None:
    """When the firmware's ready handshake returns ERROR, the read loop never
    starts and the call returns False."""
    fake_serial.feed(_error_frame_bytes())  # ready handshake fails
    comm = make_comm()

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ):
        ok = hw.read_vpp_voltage()
    assert ok is False
