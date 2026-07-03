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

import re
import struct
from typing import Iterator  # noqa: UP035
from unittest.mock import patch

import pytest

from firestarter.config import ConfigManager
from firestarter.constants import COMMAND_READ_VPE
from firestarter.hardware import HardwareManager
from firestarter.messages import MSG_END_DONE

from .conftest import build_frame


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


# ---------------------------------------------------------------------------
# Wave-0 RED scaffold (v1.21 Phase 111, VOLT-01) -- measured-voltage sampler.
#
# `_parse_voltage_frame` / `sample_vpp_mv` / `sample_vpe_mv` do NOT exist yet
# on `HardwareManager` -- Plan 02 creates them. These tests are EXPECTED to
# fail with AttributeError until then; that RED state is the Wave-0
# deliverable (111-VALIDATION.md). Do NOT stub the production symbols here.
# ---------------------------------------------------------------------------


def test_parse_voltage_frame_reconstructs_mv(hw_config) -> None:
    """`_parse_voltage_frame` reconstructs mV as v_int*1000 + v_dec*100 --
    the Pitfall-2 KAT pinning the units contract (never read v_int alone as
    mV, never treat the wire as raw mV)."""
    hw = HardwareManager(hw_config)

    assert hw._parse_voltage_frame("VPP: 20.9V, Internal VCC: 5.0V") == 20900
    assert hw._parse_voltage_frame("VPE: 21.0V, Internal VCC: 5.0V") == 21000


def test_sample_vpp_mv_returns_median(hw_config, make_comm, fake_serial) -> None:
    """`sample_vpp_mv()` feeds three synthetic 0xE4 frames and returns the
    median reconstructed mV value (20900 for a constant 20.9V rail)."""
    fake_serial.feed(_ok_frame_bytes())  # ready handshake
    for _ in range(3):
        fake_serial.feed(build_frame(0xE4, struct.pack(">HHHH", 20, 9, 5, 0)))
    comm = make_comm()

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ):
        assert hw.sample_vpp_mv() == 20900


def test_sample_vpe_mv_uses_state_12(hw_config, make_comm, fake_serial) -> None:
    """`sample_vpe_mv()` mirrors `sample_vpp_mv()` but parses 0xE5 frames and
    connects with `state == COMMAND_READ_VPE` (12)."""
    fake_serial.feed(_ok_frame_bytes())  # ready handshake
    for _ in range(3):
        fake_serial.feed(build_frame(0xE5, struct.pack(">HHHH", 21, 0, 5, 0)))
    comm = make_comm()

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ) as mock_connect:
        assert hw.sample_vpe_mv() == 21000

    command_for_connect = mock_connect.call_args[0][0]
    assert command_for_connect["state"] == COMMAND_READ_VPE


def test_sample_none_returns_none_on_error(hw_config, make_comm, fake_serial) -> None:
    """Transport failure and an in-band ERROR frame both return exactly
    `None` -- never a fabricated `0` (T-111-INPUT honest-fallback)."""
    from firestarter.exceptions import ProgrammerNotFoundError

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=ProgrammerNotFoundError("no port"),
    ):
        result = hw.sample_vpp_mv()
    assert result is None

    fake_serial.feed(_ok_frame_bytes())  # ready handshake
    fake_serial.feed(_error_frame_bytes())  # error instead of a DATA frame
    comm = make_comm()

    hw2 = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ):
        result2 = hw2.sample_vpp_mv()
    assert result2 is None


def test_sample_median_of_even_n_off_grid(hw_config, make_comm, fake_serial) -> None:
    """Median of an even-N sample set (20900, 21000) is 20950, cast to
    `int` -- pins the even-N off-grid rounding behavior (Pitfall 5)."""
    fake_serial.feed(_ok_frame_bytes())  # ready handshake
    fake_serial.feed(build_frame(0xE4, struct.pack(">HHHH", 20, 9, 5, 0)))
    fake_serial.feed(build_frame(0xE4, struct.pack(">HHHH", 21, 0, 5, 0)))
    comm = make_comm()

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ):
        result = hw.sample_vpp_mv(n=2)

    assert isinstance(result, int)
    assert result == 20950


# ---------------------------------------------------------------------------
# dev-test-vpp-vpe-timeout regression (2026-07-03) -- `_sample_one_voltage`
# stops acking after `n` frames while the firmware's CMD_READ_VPP/VPE handler
# is still ACTIVE (no host-sendable stop signal exists for this command).
# The firmware's own FIRMWARE_CMD_TIMEOUT_MS watchdog self-terminates it and
# emits a stray "Command N timed out" ERROR frame ~1s later. Without
# draining that frame on the SAME connection, it leaks into the NEXT
# find_and_connect's handshake and can desync it (the live-hardware
# "Connecting... / ERROR: Command 11 timed out" loop the operator reported).
# ---------------------------------------------------------------------------


def test_sample_vpp_mv_drains_stray_watchdog_timeout_frame(
    hw_config, make_comm, fake_serial
) -> None:
    """A stray MSG_ERR_CMD_TIMEOUT frame arriving after the 3rd sample (the
    firmware's watchdog self-terminating the still-active command) is
    consumed by `_drain_pending_command` on THIS connection -- it must not
    raise, and must not affect the returned median."""
    fake_serial.feed(_ok_frame_bytes())  # ready handshake
    for _ in range(3):
        fake_serial.feed(build_frame(0xE4, struct.pack(">HHHH", 20, 9, 5, 0)))
    # Stray watchdog-timeout frame the firmware emits ~1s after the last ack
    # (COMMAND_READ_VPP == 11) -- simulates the exact race from the debug
    # session's live-hardware capture.
    fake_serial.feed(build_frame(0xAA, struct.pack(">B", 11)))
    comm = make_comm()

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ):
        result = hw.sample_vpp_mv()

    assert result == 20900
    # The stray frame must be drained (read) rather than left pending --
    # confirms _drain_pending_command actually consumed it instead of a
    # no-op that happened to not raise.
    assert fake_serial.in_waiting == 0


def test_sample_vpp_mv_drain_timeout_is_swallowed(
    hw_config, make_comm, fake_serial
) -> None:
    """When NOTHING arrives after the 3rd sample (e.g. the watchdog frame is
    lost, or this connection is closed before it fires), the drain's own
    SerialTimeoutError is swallowed -- `_sample_one_voltage` still returns
    the median, it does not propagate the drain timeout as a failure."""
    fake_serial.feed(_ok_frame_bytes())  # ready handshake
    for _ in range(3):
        fake_serial.feed(build_frame(0xE4, struct.pack(">HHHH", 20, 9, 5, 0)))
    comm = make_comm()

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ):
        result = hw.sample_vpp_mv()

    assert result == 20900


def test_sample_vpp_mv_retries_once_after_a_failed_attempt(
    hw_config, make_comm, fake_serial
) -> None:
    """`sample_vpp_mv()` retries exactly once when the first attempt fails
    (e.g. a stale still-active previous command swallowed the first
    attempt's data-request ack, per the deeper MCU-reset-not-guaranteed
    race). The second `find_and_connect` call lands on a clean handshake
    and the overall call succeeds instead of returning None."""
    from firestarter.exceptions import ProgrammerNotFoundError

    fake_serial.feed(_ok_frame_bytes())  # ready handshake for the 2nd attempt
    for _ in range(3):
        fake_serial.feed(build_frame(0xE4, struct.pack(">HHHH", 20, 9, 5, 0)))
    comm = make_comm()

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=[ProgrammerNotFoundError("stale command swallowed ack"), comm],
    ) as mock_connect:
        result = hw.sample_vpp_mv()

    assert result == 20900
    assert mock_connect.call_count == 2


def test_voltage_format_pin() -> None:
    """Pin the 0xE4/0xE5 `CATALOG` format strings against the sampler's
    tolerant regex -- a codegen regen that changes the wording silently
    breaks the parser otherwise (Pitfall 3 / T-111-DRIFT)."""
    from firestarter.messages import CATALOG

    pattern = r"(\d+)\.(\d+)\s*V"

    rendered_vpp = CATALOG[0xE4].format % (20, 9, 5, 0)
    match_vpp = re.search(pattern, rendered_vpp)
    assert match_vpp is not None
    assert match_vpp.groups() == ("20", "9")

    rendered_vpe = CATALOG[0xE5].format % (21, 0, 5, 0)
    match_vpe = re.search(pattern, rendered_vpe)
    assert match_vpe is not None
    assert match_vpe.groups() == ("21", "0")
