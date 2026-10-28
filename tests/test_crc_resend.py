"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

A frame that fails its COBS or CRC8 check is sent again, in all three
transfers:

- read chunk (fw -> host): the host answers "NK", the firmware sends the
  same chunk again, and a copy that arrives after the chunk was received is
  dropped without a reply;
- write chunk (host -> fw): the firmware asks for the same address again,
  and the host sends the same bytes again;
- command frame (host -> fw): the firmware answers 0xA4 and stays idle, so
  setup_command sends the command once more.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from firestarter import transport_counters
from firestarter.config import ConfigManager
from firestarter.eprom_operations import EpromOperator
from firestarter.messages import (
    MSG_DATA_SENDING,
    MSG_END_DONE,
    MSG_ERR_EMPTY_INPUT,
    MSG_INIT_DONE,
    MSG_MAIN_DONE,
    MSG_OK_READY,
)

from .conftest import build_chunk_frame, build_frame, build_request_frame


def _corrupt(frame: bytes) -> bytes:
    """Flip the CRC8 byte: frame[-2], before the 0x0A anchor."""
    return frame[:-2] + bytes([frame[-2] ^ 0x01]) + frame[-1:]


def _operator(comm) -> EpromOperator:
    operator = EpromOperator(ConfigManager())
    operator.comm = comm
    return operator


def _capture_sends(comm) -> list:
    """Record each string and frame the host sends, in order."""
    sent: list = []
    comm.send_string = lambda text, encoding="ascii": sent.append(text)
    comm.send_bytes = lambda data: sent.append(bytes(data))
    return sent


@pytest.fixture(autouse=True)
def _reset_counters():
    transport_counters.reset()
    yield
    transport_counters.reset()


def _read(make_comm, fake_serial, frames, data_size):
    for frame in frames:
        fake_serial.feed(frame)
    comm = make_comm()
    sent = _capture_sends(comm)
    received = bytearray()
    operator = _operator(comm)
    ok, _ = operator._run_state_machine(
        "READ",
        main_phase_handler=operator._main_phase_read_data,
        start_addr=0,
        end_addr=data_size,
        process_data_chunk_callback=lambda address, chunk: received.extend(chunk),
    )
    return ok, bytes(received), sent


def test_corrupt_chunk_gets_a_nak_and_the_copy_is_acked(make_comm, fake_serial):
    data = b"\xde\xad\xbe\xef"
    ok, received, sent = _read(
        make_comm,
        fake_serial,
        [
            build_frame(MSG_INIT_DONE, b""),
            _corrupt(build_chunk_frame(0, data)),
            build_chunk_frame(0, data),
            build_frame(MSG_MAIN_DONE, b""),
            build_frame(MSG_END_DONE, b""),
        ],
        len(data),
    )
    assert ok is True
    assert received == data
    assert sent.count("NK") == 1
    assert sent.index("NK") < len(sent) - 1 and sent[sent.index("NK") + 1] == "OK"
    assert transport_counters.snapshot()["resends_read"] == 1


def test_copy_after_a_nak_for_another_frame_is_dropped_without_a_reply(
    make_comm, fake_serial
):
    """The corrupt frame was MSG_DATA_SENDING, not the chunk. The chunk is
    acked, and the copy the firmware sends for the "NK" gets no reply: the
    "NK" already was the reply the firmware waits for."""
    data = b"\x01\x02\x03\x04"
    ok, received, sent = _read(
        make_comm,
        fake_serial,
        [
            build_frame(MSG_INIT_DONE, b""),
            _corrupt(build_frame(MSG_DATA_SENDING, b"")),
            build_chunk_frame(0, data[:2]),
            build_chunk_frame(0, data[:2]),
            build_chunk_frame(2, data[2:]),
            build_frame(MSG_MAIN_DONE, b""),
            build_frame(MSG_END_DONE, b""),
        ],
        len(data),
    )
    assert ok is True
    assert received == data
    # One reply per firmware emission: NK (for the frame that failed), OK for
    # the first copy at 0, nothing for the second copy, OK for the chunk at 2.
    nak = sent.index("NK")
    assert sent[nak : nak + 3] == ["NK", "OK", "OK"]


def test_chunk_ahead_of_the_expected_address_is_refused(make_comm, fake_serial):
    data = b"\x01\x02\x03\x04"
    ok, received, _ = _read(
        make_comm,
        fake_serial,
        [
            build_frame(MSG_INIT_DONE, b""),
            build_chunk_frame(2, data[2:]),
            build_frame(MSG_MAIN_DONE, b""),
            build_frame(MSG_END_DONE, b""),
        ],
        len(data),
    )
    assert ok is False
    assert received == b""


def _write(make_comm, fake_serial, tmp_path, frames, data):
    input_file = tmp_path / "in.bin"
    input_file.write_bytes(data)
    for frame in frames:
        fake_serial.feed(frame)
    comm = make_comm()
    sent = _capture_sends(comm)
    operator = _operator(comm)
    ok, msg = operator._run_state_machine(
        "WRITE",
        main_phase_handler=operator._main_phase_send_data,
        input_file_path=str(input_file),
        buffer_size=4,
    )
    return ok, msg, [s for s in sent if isinstance(s, bytes)]


def test_write_request_for_the_same_address_resends_the_same_chunk(
    make_comm, fake_serial, tmp_path
):
    data = bytes(range(8))
    ok, _, frames = _write(
        make_comm,
        fake_serial,
        tmp_path,
        [
            build_frame(MSG_INIT_DONE, b""),
            build_request_frame(0),
            build_request_frame(4),
            build_request_frame(4),
            build_frame(MSG_MAIN_DONE, b""),
            build_frame(MSG_END_DONE, b""),
        ],
        data,
    )
    assert ok is True
    assert len(frames) == 3
    assert frames[1] == frames[2], "the resend carries the same bytes"
    assert frames[0] != frames[1]
    assert transport_counters.snapshot()["resends_write"] == 1


def test_write_request_past_the_data_sent_is_refused(make_comm, fake_serial, tmp_path):
    ok, msg, frames = _write(
        make_comm,
        fake_serial,
        tmp_path,
        [
            build_frame(MSG_INIT_DONE, b""),
            build_request_frame(0),
            build_request_frame(8),
            build_frame(MSG_MAIN_DONE, b""),
            build_frame(MSG_END_DONE, b""),
        ],
        bytes(range(8)),
    )
    assert ok is False
    assert "outside the data sent so far" in msg
    assert len(frames) == 1


def test_request_and_chunk_frames_carry_their_address(make_comm, fake_serial):
    fake_serial.feed(build_request_frame(0x012345))
    fake_serial.feed(build_chunk_frame(0x000200, b"\xaa\xbb"))
    comm = make_comm()
    request = comm.get_response(timeout=0.5)
    chunk = comm.get_response(timeout=0.5)
    assert (request.type, request.address) == ("OK", 0x012345)
    assert (chunk.type, chunk.address, chunk.payload) == ("DATA", 0x200, b"\xaa\xbb")


def test_corrupt_frame_is_reported_only_when_asked(make_comm, fake_serial):
    bad = _corrupt(build_chunk_frame(0, b"\x01"))
    good = build_chunk_frame(0, b"\x02")
    fake_serial.feed(bad + good + bad + good)
    comm = make_comm()
    assert comm.get_response(timeout=0.5).payload == b"\x02"
    assert comm.get_response(timeout=0.5, report_corrupt=True).type == "CORRUPT"
    assert comm.get_response(timeout=0.5, report_corrupt=True).payload == b"\x02"


def test_setup_resends_a_command_the_firmware_did_not_receive(
    make_comm, fake_serial, monkeypatch
):
    """0xA4 and no ack inside the recovery window: the firmware refused the
    command frame and is still idle, so the command goes out once more."""
    import firestarter.serial_comm as serial_comm

    monkeypatch.setattr(serial_comm, "SETUP_ACK_RECOVERY_TIMEOUT_S", 0.3)
    fake_serial.feed(build_frame(MSG_ERR_EMPTY_INPUT, b""))
    comm = make_comm()
    sends: list = []

    def _send_command(command):
        sends.append(command)
        if len(sends) == 2:
            fake_serial.feed(build_frame(MSG_OK_READY, b""))

    comm.send_command = _send_command
    ok = comm.setup_command({"state": 13}, MagicMock(), allow_outdated_firmware=True)

    assert ok is True
    assert len(sends) == 2
    assert transport_counters.snapshot()["resends_command"] == 1


def test_setup_does_not_resend_when_the_real_ack_follows(make_comm, fake_serial):
    """The Uno DTR-reset case: a spurious 0xA4 and then the real ack. The
    command was received, so it must not go out twice."""
    fake_serial.feed(build_frame(MSG_ERR_EMPTY_INPUT, b""))
    fake_serial.feed(build_frame(MSG_OK_READY, b""))
    comm = make_comm()
    sends: list = []
    comm.send_command = sends.append
    ok = comm.setup_command({"state": 13}, MagicMock(), allow_outdated_firmware=True)

    assert ok is True
    assert len(sends) == 1
    assert transport_counters.snapshot()["resends_command"] == 0
