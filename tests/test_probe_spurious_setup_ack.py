"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

beta-probe-empty-input — regression coverage for the self-sustaining probe
failure reported against the published beta (app 3.0.0b36 + firmware
3.0.0b25) on Uno-class hardware.

Root cause, proven directly from `firestarter.cpp`'s CMD_IDLE decoder and
`rurp_serial_utils.cpp`'s `_drain_to_delimiter`: `MSG_ERR_EMPTY_INPUT` is a
generic catch-all the firmware reuses for any COBS/CRC/framing decode
failure, not only a genuinely empty read. Uno-class boards have two other
already-documented instances of the same hazard class in this file
(`PREFIX_REGEX`'s rightmost-match comment for PD1/TX garbage, and firmware's
`rurp_set_communication_mode()` for PD0/RX garbage around a DTR reset) — a
stray frame of this kind can arrive interleaved with the genuine setup ack.

`SerialCommunicator._probe_port` treated the FIRST post-send response as
final: a spurious `MSG_ERR_EMPTY_INPUT` frame ahead of the real "OK: Ready"
made the probe give up and disconnect, even though the genuine ack was
already queued moments behind it (only `disconnect()`'s own opportunistic
drain ever read it, too late to help). Because Uno-class boards reset on
every port open, this reproduced on every retry — the self-sustaining trap
the report describes.

These tests replay the reporter's exact frame ordering through a
`_FakeSerial` — no hardware, Uno or otherwise, is needed to prove the host
logic. Test A pins the fixed, correct behaviour (probe recovers). Test B is
a negative control: a genuine, non-recoverable decode error must still fail
the probe.
"""

from unittest.mock import MagicMock, patch

from firestarter.messages import MSG_ERR_EMPTY_INPUT, MSG_OK_READY
from firestarter.serial_comm import SerialCommunicator

from .conftest import _FakeSerial, build_frame


def _make_init(fake_ser: _FakeSerial):
    def _init(self, port=None, baud_rate=None, **kwargs):
        self.connection = fake_ser
        self.port_name = port or "/dev/fake"
        self.baud_rate = baud_rate or 250000
        self.timeout = 0.1
        self.programmer_info = None
        self._fault_inject_outgoing = None
        self.firmware_buffer_size = None
        self.firmware_max_chunk = None
        self.firmware_identity = None
        self.hw_revision = None
        self.write_block_budget_s = None
        self.seen_message_ids = set()

    return _init


def test_probe_port_recovers_from_spurious_empty_input_ahead_of_real_ack() -> None:
    """A spurious `Empty input` frame ahead of the genuine "OK: Ready" must
    not sink the probe — the real ack is one read away.
    """
    fake_ser = _FakeSerial()
    fake_ser.feed(build_frame(MSG_ERR_EMPTY_INPUT, b""))
    fake_ser.feed(build_frame(MSG_OK_READY, b""))

    with (
        patch.object(SerialCommunicator, "__init__", _make_init(fake_ser)),
        patch.object(SerialCommunicator, "send_json_command", return_value=15),
        patch.object(SerialCommunicator, "consume_remaining_input", return_value=None),
    ):
        comm = SerialCommunicator._probe_port(
            port_name="/dev/fake",
            baud_rate=250000,
            command_to_send={"state": 13},
            config_manager=MagicMock(),
            allow_outdated_firmware=True,
        )

    assert comm is not None
    assert comm.programmer_info == "Ready"


def test_probe_port_still_fails_on_a_genuine_unrecoverable_error() -> None:
    """Negative control: a real rejection with nothing recoverable behind it
    must still fail the probe — the fix must not paper over genuine errors.
    """
    fake_ser = _FakeSerial()
    fake_ser.feed(build_frame(MSG_ERR_EMPTY_INPUT, b""))

    with (
        patch.object(SerialCommunicator, "__init__", _make_init(fake_ser)),
        patch.object(SerialCommunicator, "send_json_command", return_value=15),
        patch.object(SerialCommunicator, "consume_remaining_input", return_value=None),
        patch.object(SerialCommunicator, "disconnect", return_value=None),
    ):
        comm = SerialCommunicator._probe_port(
            port_name="/dev/fake",
            baud_rate=250000,
            command_to_send={"state": 13},
            config_manager=MagicMock(),
            allow_outdated_firmware=True,
        )

    assert comm is None
