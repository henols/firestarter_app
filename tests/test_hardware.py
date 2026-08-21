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
from unittest.mock import Mock, patch

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


def test_read_programmer_identity_happy_path_harvests_the_identity_verbatim(
    hw_config, make_comm, fake_serial
) -> None:
    """PROV-01: read_programmer_identity() returns a ProgrammerIdentity whose
    fw_board_identity is exactly the string comm.firmware_identity carried
    off the connect ack, and whose hw_revision is the CMD_HW_VERSION ack
    message -- read by field name (D-03), never positional unpacking."""
    fake_serial.feed(_ok_frame_bytes())  # the expect_ack() inside picks this up
    fake_serial.feed(MSG_END_DONE.to_bytes(1, "big"))  # any trailing byte is fine
    comm = make_comm()
    # make_comm() sets firmware_identity = None by default (fail-closed) --
    # a populated leg must set it explicitly.
    comm.firmware_identity = "3.0.0b19:leonardo"

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ):
        identity = hw.read_programmer_identity()

    assert identity.fw_board_identity == "3.0.0b19:leonardo"
    assert identity.hw_revision == "ready"


def test_read_programmer_identity_opens_one_connection_and_disconnects_once(
    hw_config, make_comm, fake_serial
) -> None:
    """PROV-02: exactly one find_and_connect and one disconnect() per call --
    asserted mechanically, not argued from reading the source. There is no
    EpromOperator anywhere in this test's graph at all -- that absence IS
    the SAFE-02 property this test pins: read_programmer_identity() writes
    no attribute onto any operator, because none exists here to write onto."""
    fake_serial.feed(_ok_frame_bytes())
    fake_serial.feed(MSG_END_DONE.to_bytes(1, "big"))
    comm = make_comm()
    comm.firmware_identity = "3.0.0b19:leonardo"
    # comm is a REAL SerialCommunicator built via __new__, so its disconnect
    # is not itself a mock -- wrap it so the call can be asserted on.
    comm.disconnect = Mock(wraps=comm.disconnect)

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ) as mock_find_and_connect:
        hw.read_programmer_identity()

    assert mock_find_and_connect.call_count == 1
    comm.disconnect.assert_called_once()


def test_read_programmer_identity_default_comm_yields_the_absent_case(
    hw_config, make_comm, fake_serial
) -> None:
    """With the default make_comm() (firmware_identity left at its
    fail-closed None default) and an OK ack, the result carries the ack
    message as hw_revision and None as fw_board_identity -- the shape a
    board predating the CAP-02 identity tail would produce. The free leg
    the fixture default gives (147-PATTERNS.md)."""
    fake_serial.feed(_ok_frame_bytes())
    fake_serial.feed(MSG_END_DONE.to_bytes(1, "big"))
    comm = make_comm()

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ):
        identity = hw.read_programmer_identity()

    assert identity.hw_revision == "ready"
    assert identity.fw_board_identity is None


def test_read_programmer_identity_revision_fails_but_identity_survives(
    hw_config, make_comm, fake_serial
) -> None:
    """D-04 leg 1: an ERROR revision ack still returns the identity that was
    harvested off the connect ack before the revision command was even
    dispatched. Not a hypothetical: a board built without HARDWARE_REVISION
    answers MSG_ERR_UNKNOWN_CMD to CMD_HW_VERSION while its setup ack
    already carried a good identity (confirmed in firmware source, RESEARCH
    F-16) -- and those non-standard boards are exactly the ones a triager
    most needs to identify."""
    fake_serial.feed(_error_frame_bytes())  # revision ack fails
    comm = make_comm()
    comm.firmware_identity = "3.0.0b19:leonardo"

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ):
        identity = hw.read_programmer_identity()

    assert identity.hw_revision is None
    assert identity.fw_board_identity == "3.0.0b19:leonardo"


@pytest.mark.parametrize("exc_name", ["ProgrammerNotFoundError", "SerialTimeoutError"])
def test_read_programmer_identity_transport_error_returns_both_absent(
    hw_config, exc_name: str
) -> None:
    """D-04 leg 2 / F-17: when find_and_connect itself raises -- either a
    ProgrammerNotFoundError or a SerialTimeoutError, both caught by the same
    three-exception clause read_programmer_identity shares with
    get_hardware_revision -- the call returns a ProgrammerIdentity with BOTH
    fields None. Asserted as `is not None` on the returned object itself:
    the pre-existing contract returned a bare None here, and every
    Mock(spec=HardwareManager) double would silently accept that swap."""
    import firestarter.exceptions as exceptions_mod

    exc_cls = getattr(exceptions_mod, exc_name)

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=exc_cls("transport failure"),
    ):
        identity = hw.read_programmer_identity()

    assert identity is not None
    assert identity.hw_revision is None
    assert identity.fw_board_identity is None


def test_read_programmer_identity_transport_error_after_harvest_keeps_the_identity(
    hw_config, make_comm, fake_serial
) -> None:
    """Distinguishes 'failed before the harvest' from 'failed after it': the
    identity is read off comm.firmware_identity BEFORE comm.expect_ack() is
    called, so a SerialTimeoutError raised by the ack call itself still
    returns the already-harvested identity string -- proving the
    harvest-before-teardown ordering is load-bearing, not incidental. The
    finally-block disconnect() still runs; the exception path does not skip
    teardown."""
    from firestarter.exceptions import SerialTimeoutError

    comm = make_comm()
    comm.firmware_identity = "3.0.0b19:leonardo"
    comm.expect_ack = Mock(side_effect=SerialTimeoutError("ack timed out"))
    comm.disconnect = Mock(wraps=comm.disconnect)

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ):
        identity = hw.read_programmer_identity()

    assert identity.hw_revision is None
    assert identity.fw_board_identity == "3.0.0b19:leonardo"
    comm.disconnect.assert_called_once()


def test_read_programmer_identity_scrub_keeps_a_mangled_identity_visibly_faulty(
    hw_config, make_comm, fake_serial
) -> None:
    """D-07: serial_comm.py's errors="replace" decode can yield U+FFFD on a
    corrupt ack, and that value reaches a public GitHub issue body via
    submit.py. A mangled identity must stay visible as evidence of a
    transport fault -- never silently converted to the unknown marker."""
    from firestarter.diagnostic_report import NOT_REPORTED

    fake_serial.feed(_ok_frame_bytes())
    fake_serial.feed(MSG_END_DONE.to_bytes(1, "big"))
    comm = make_comm()
    comm.firmware_identity = "3.0.0b19:leonar�o"  # one byte mangled

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ):
        identity = hw.read_programmer_identity()

    assert identity.fw_board_identity is not None
    assert identity.fw_board_identity != NOT_REPORTED
    assert all("\x20" <= c <= "\x7e" for c in identity.fw_board_identity)
    assert identity.fw_board_identity.startswith("3.0.0b19")


def test_read_programmer_identity_scrub_collapses_an_empty_identity_to_absent(
    hw_config, make_comm, fake_serial
) -> None:
    """D-07 / P-8: an empty identity is the value a zero-length CAP-02 tail
    decodes to. This is a locked planner decision (RESEARCH Open Question 3,
    resolved in favour of collapsing): an identity with no printable content
    carries no evidence to preserve, and an empty value must not be allowed
    to reach a render, where a blank cell is precisely what PROV-05
    forbids -- so it collapses to None. A companion case in the same test
    pins the OTHER direction: an all-non-printable identity does NOT
    collapse -- it becomes a non-empty substituted string -- so the two
    cases stay distinguishable and the collapse cannot creep wider."""
    fake_serial.feed(_ok_frame_bytes())
    fake_serial.feed(MSG_END_DONE.to_bytes(1, "big"))
    comm = make_comm()
    comm.firmware_identity = ""  # zero-length CAP-02 tail

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ):
        identity = hw.read_programmer_identity()
    assert identity.fw_board_identity is None

    # Companion case: all-non-printable does NOT collapse to None.
    fake_serial.feed(_ok_frame_bytes())
    fake_serial.feed(MSG_END_DONE.to_bytes(1, "big"))
    comm2 = make_comm()
    comm2.firmware_identity = "\x01\x02\x03"  # non-printable, but not empty

    hw2 = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm2,
    ):
        identity2 = hw2.read_programmer_identity()
    assert identity2.fw_board_identity is not None
    assert identity2.fw_board_identity != ""


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
# dev-test-vpp-vpe-timeout fix (2026-07-03) -- `_sample_one_voltage` now
# sends an explicit DONE after its n-sample loop and BEFORE disconnecting,
# so the firmware's hw_read_voltage handler (which now recognizes OP_MSG_DONE,
# mirroring eprom_write) ends the command immediately instead of relying on
# its 1s watchdog. This replaces the superseded drain/retry host-side
# mitigation (reverted commits f7ab92a, 2352b5f) with the real firmware fix.
# ---------------------------------------------------------------------------


def test_sample_vpp_mv_sends_done_before_disconnect(
    hw_config, make_comm, fake_serial
) -> None:
    """After the n-sample loop, `_sample_one_voltage` calls `send_done()`
    on the still-open connection before `disconnect()` -- proving the
    firmware is told to end the command instead of being left dangling."""
    fake_serial.feed(_ok_frame_bytes())  # ready handshake
    for _ in range(3):
        fake_serial.feed(build_frame(0xE4, struct.pack(">HHHH", 20, 9, 5, 0)))
    comm = make_comm()

    manager = Mock()
    manager.attach_mock(Mock(wraps=comm.send_done), "send_done")
    manager.attach_mock(Mock(wraps=comm.disconnect), "disconnect")
    comm.send_done = manager.send_done
    comm.disconnect = manager.disconnect

    hw = HardwareManager(hw_config)
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        return_value=comm,
    ):
        result = hw.sample_vpp_mv()

    # A clean n-sample read still returns the median.
    assert result == 20900
    manager.send_done.assert_called_once()
    manager.disconnect.assert_called_once()
    assert [c[0] for c in manager.mock_calls] == ["send_done", "disconnect"]


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
