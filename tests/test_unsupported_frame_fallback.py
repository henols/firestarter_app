"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

What the host does when the firmware answers a setup frame with
MSG_ERR_BAD_FRAME (0xA0).

JSON-era firmware parses every frame as JSON. jsmn rejects the binary magic
byte, so that firmware answers every binary command with 0xA0. A chip
operation must refuse with FirmwareOutdatedError. Only the firmware-update
read path may resend, and only the version query, as JSON.
"""

import struct
from unittest.mock import MagicMock

import pytest

from firestarter.command_codec import encode_command
from firestarter.constants import COMMAND_FW_VERSION, COMMAND_READ
from firestarter.exceptions import FirmwareOutdatedError
from firestarter.frame_parser import _crc8_ccitt, cobs_decode
from firestarter.messages import MSG_ERR_BAD_COMMAND, MSG_ERR_BAD_FRAME, MSG_OK_READY
from tests.conftest import build_frame

_READ = {
    "cmd": COMMAND_READ,
    "memory-size": 65536,
    "algorithm": 7,
    "pin-count": 28,
    "vpp_mv": 12500,
    "bus-config": {"bus": list(range(16))},
}


def _ready_ack(identity: str) -> bytes:
    raw = identity.encode("ascii")
    return build_frame(
        MSG_OK_READY, struct.pack(">H", 512) + bytes([0x02, len(raw)]) + raw
    )


def _comm(make_comm, fake_serial, *replies: bytes):
    comm = make_comm()
    sent: list[bytes] = []
    comm.send_bytes = lambda data: sent.append(bytes(data)) or len(data)
    for reply in replies:
        fake_serial.feed(reply)
    return comm, sent


def _payload(frame: bytes) -> bytes:
    """Strip the 0x00 delimiter, undo COBS, check and strip the CRC8."""
    decoded = cobs_decode(frame[:-1])
    assert decoded[-1] == _crc8_ccitt(decoded[:-1])
    return decoded[:-1]


def test_chip_operation_on_json_era_firmware_raises_outdated(make_comm, fake_serial):
    comm, sent = _comm(make_comm, fake_serial, build_frame(MSG_ERR_BAD_FRAME, b""))
    with pytest.raises(
        FirmwareOutdatedError, match="predates the binary command protocol"
    ):
        comm.setup_command(_READ, MagicMock())
    assert [_payload(f) for f in sent] == [encode_command(_READ)]


def test_chip_operation_refuses_even_with_the_update_waiver(make_comm, fake_serial):
    """The waiver covers the version query only; no other command goes as JSON."""
    comm, sent = _comm(make_comm, fake_serial, build_frame(MSG_ERR_BAD_FRAME, b""))
    with pytest.raises(FirmwareOutdatedError):
        comm.setup_command(_READ, MagicMock(), allow_outdated_firmware=True)
    assert len(sent) == 1


def test_version_query_without_the_waiver_refuses(make_comm, fake_serial):
    comm, sent = _comm(make_comm, fake_serial, build_frame(MSG_ERR_BAD_FRAME, b""))
    with pytest.raises(FirmwareOutdatedError):
        comm.setup_command({"state": COMMAND_FW_VERSION}, MagicMock())
    assert len(sent) == 1


def test_update_path_resends_the_version_query_as_json(make_comm, fake_serial):
    comm, sent = _comm(
        make_comm,
        fake_serial,
        build_frame(MSG_ERR_BAD_FRAME, b""),
        _ready_ack("3.1.0:uno"),
    )
    assert comm.setup_command(
        {"state": COMMAND_FW_VERSION}, MagicMock(), allow_outdated_firmware=True
    )
    assert [_payload(f) for f in sent] == [
        bytes.fromhex("f5010d00000000"),
        b'{"state":13}',
    ]
    assert comm.firmware_identity == "3.1.0:uno"


def test_binary_firmware_gets_no_json(make_comm, fake_serial):
    comm, sent = _comm(make_comm, fake_serial, _ready_ack("3.2.0:uno"))
    assert comm.setup_command(
        {"state": COMMAND_FW_VERSION}, MagicMock(), allow_outdated_firmware=True
    )
    assert len(sent) == 1


def test_a_refused_command_is_not_taken_for_old_firmware(make_comm, fake_serial):
    """0xC2 is binary firmware refusing a field: no fallback, no outdated error."""
    comm, sent = _comm(
        make_comm, fake_serial, build_frame(MSG_ERR_BAD_COMMAND, bytes([3]))
    )
    assert not comm.setup_command(
        {"state": COMMAND_FW_VERSION}, MagicMock(), allow_outdated_firmware=True
    )
    assert comm.last_ack_error_id == MSG_ERR_BAD_COMMAND
    assert len(sent) == 1


def test_bad_command_renders_its_reason(make_comm, fake_serial):
    comm, _ = _comm(
        make_comm, fake_serial, build_frame(MSG_ERR_BAD_COMMAND, bytes([3]))
    )
    ok, message = comm.expect_ack()
    assert not ok
    assert message == "Command refused: an address line is outside the bus"
