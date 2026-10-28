"""RAM test (March C-): policy, result decoding and the operator seam.

The refusal cases use real rows of the committed `chip_database.json`. The
decode cases feed a real `MSG_ERR_RAM_TEST` frame through the host's own
frame decoder, so the regex is tested against the catalog's rendered text,
not against a hand-copied string.

Validation ceiling: software only. No SRAM part was on the bench.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from firestarter import ram_test
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import FirmwareOutdatedError
from firestarter.messages import (
    MSG_DATA_PROGRESS,
    MSG_END_DONE,
    MSG_ERR_RAM_TEST,
    MSG_ERR_UNKNOWN_CMD,
    MSG_INIT_DONE,
    MSG_MAIN_DONE,
)

from .conftest import build_frame

_DB = EpromDatabase(skip_local_override=True)


def _failure_frame(element: int, address: int, expected: int, actual: int) -> bytes:
    params = bytes(
        [element, (address >> 16) & 0xFF, (address >> 8) & 0xFF, address & 0xFF]
    ) + bytes([expected, actual])
    return build_frame(MSG_ERR_RAM_TEST, params)


def _progress_frame(done: int, total: int) -> bytes:
    return build_frame(
        MSG_DATA_PROGRESS, done.to_bytes(4, "big") + total.to_bytes(4, "big")
    )


# --- refusal policy, against real database rows ---


@pytest.mark.parametrize("chip", ["6116", "6264", "62256", "628128"])
def test_volatile_sram_runs(chip: str) -> None:
    assert ram_test.refusal_reason(_DB.get_eprom(chip), chip) is None


@pytest.mark.parametrize("chip", ["FM1608", "DS1225(RW)", "DS1225(TEST)"])
def test_non_volatile_part_is_refused(chip: str) -> None:
    reason = ram_test.refusal_reason(_DB.get_eprom(chip), chip)
    assert reason is not None
    assert "without power" in reason


@pytest.mark.parametrize("chip", ["27C256", "AT28C256", "W27C512"])
def test_non_ram_part_is_refused(chip: str) -> None:
    reason = ram_test.refusal_reason(_DB.get_eprom(chip), chip)
    assert reason is not None
    assert "not a RAM" in reason


def test_0x0e_rows_are_refused() -> None:
    """0x0E reaches only NVRAM read/write rows; the firmware refuses the RAM
    test there too, so the host must not offer it."""
    assert 0x0E not in ram_test.RAM_TEST_PROTOCOLS
    entry = {"protocol-id": 0x0E, "non-volatile": False}
    assert ram_test.refusal_reason(entry, "x") is not None


# --- decoding the firmware's first failure ---


def test_failure_decodes_from_the_rendered_frame(make_comm, fake_serial) -> None:
    comm = make_comm()
    fake_serial.feed(_failure_frame(3, 0x5A5A, 0xFF, 0xFD))
    response = comm.get_response(timeout=1.0)
    assert response.type == "ERROR"
    assert response.id == MSG_ERR_RAM_TEST

    failure = ram_test.parse_failure(response.message)
    assert failure == ram_test.RamTestFailure(
        element=3, address=0x5A5A, expected=0xFF, actual=0xFD
    )
    assert failure.bad_bits == 0x02


def test_unrelated_text_is_not_a_failure() -> None:
    assert ram_test.parse_failure("0x00 != 0xff at 0x000000") is None
    assert ram_test.parse_failure(None) is None


def test_render_names_element_address_and_bad_bits() -> None:
    text = ram_test.render_failure(
        "62256",
        ram_test.RamTestFailure(element=3, address=0x2000, expected=0x00, actual=0x04),
    )
    assert "62256" in text
    assert "4 of 6, down (r0, w1)" in text
    assert "0x002000" in text
    assert "Bad bits: 0x04" in text


# --- the operator seam: frames in, verdict out ---


def _operator(make_comm) -> EpromOperator:
    operator = EpromOperator(ConfigManager())
    operator.comm = make_comm()

    @contextmanager
    def _context(*_args, **_kwargs):
        yield {"cmd": 18}, 512, "RAM_TEST"

    operator._operation_context = _context  # type: ignore[method-assign]
    return operator


def test_a_pass_does_not_ack_progress_frames(make_comm, fake_serial) -> None:
    """The firmware reads no input during a simple operation's MAIN, so an
    ack per progress frame would wait there and answer the END ACK wait.
    The host sends exactly the four phase acks: INIT, MAIN, END, final."""
    operator = _operator(make_comm)
    operator.comm.send_ack = Mock()  # type: ignore[method-assign]
    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    for done in (256, 512, 768):
        fake_serial.feed(_progress_frame(done, 6 * 2048))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    passed, failure = operator.ram_test("6116", {})

    assert (passed, failure) == (True, None)
    assert operator.comm.send_ack.call_count == 4


def test_a_bad_cell_returns_the_first_failure(make_comm, fake_serial) -> None:
    operator = _operator(make_comm)
    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(_failure_frame(1, 0x123, 0x00, 0x04))

    passed, failure = operator.ram_test("62256", {})

    assert passed is False
    assert failure == ram_test.RamTestFailure(
        element=1, address=0x123, expected=0x00, actual=0x04
    )


def test_unknown_command_raises_firmware_outdated(make_comm, fake_serial) -> None:
    operator = _operator(make_comm)
    fake_serial.feed(build_frame(MSG_ERR_UNKNOWN_CMD, bytes([18])))

    with pytest.raises(FirmwareOutdatedError, match="does not implement the RAM test"):
        operator.ram_test("62256", {})
