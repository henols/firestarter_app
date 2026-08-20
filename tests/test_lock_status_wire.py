"""Phase 151 (LOCK-02): frame-level build/parse tests for `CMD_LOCK_STATUS`.

Uses `conftest.py`'s existing frame-level harness -- `build_frame`, the
`fake_serial` fixture, and the `make_comm` factory -- exactly as it already
exists for `test_serial_comm.py`/`test_serial_characterization.py`. No new
harness is built here, and the deliberately-incomplete firmware-*source*
fixture directory that stands in for `tests/fw_presence.py` (not a device)
is not used.

Six legs:
  1. The outgoing command frame carries `COMMAND_LOCK_STATUS`.
  2. Both definite decodes (`DECODE_UNPROTECTED`/`DECODE_PROTECTED`) parse and
     the raw byte reaches the caller exactly as fed.
  3. A raw byte with bits set in both nibbles survives an unrecognised
     decode unmasked, unshifted, unnormalised -- and the classifier returns
     a non-state token for it.
  4. A one-bit-corrupted CRC is rejected outright, never partially accepted.
  5. A truncated (one-byte) payload yields no state token downstream.
  6. The DATA band is not filtered by `get_response()` -- the non-vacuity
     control for every leg above.
"""

from __future__ import annotations

import json
import re

import pytest

from firestarter.constants import COMMAND_LOCK_STATUS
from firestarter.frame_parser import cobs_decode
from firestarter.lock_status import (
    DECODE_INDETERMINATE,
    DECODE_PROTECTED,
    DECODE_UNPROTECTED,
    SILICON_ONLY_TOKENS,
    classify_protection_response,
)
from firestarter.messages import MSG_DATA_PROTECTION_STATUS
from firestarter.protection_readability import GATE_TOKEN_READ_PERMITTED
from firestarter.serial_comm import SerialTimeoutError

from .conftest import build_frame

# `MSG_DATA_PROTECTION_STATUS`'s catalog format string is
# "Lock status probe: raw=0x%02X decode=%u" (messages.py). `Response.payload`
# is populated only for MSG_DATA_CHUNK (W-04); every other id-frame's decoded
# param values reach the caller only as already-rendered prose -- the same
# text-extraction idiom `_TIMEOUT_ADDR_RE`/`_PULSE_WIDTH_RE`/`_LOCK_STATUS_RE`
# already use in `eprom_operations.py`.
_LOCK_STATUS_MESSAGE_RE = re.compile(r"raw=0x([0-9A-Fa-f]{2}) decode=(\d+)")


def test_outgoing_command_carries_command_lock_status_constant(
    make_comm, fake_serial
) -> None:
    """Leg 1 (frame build). `EpromOperator._setup_operation` builds a
    command dict with `cmd=COMMAND_LOCK_STATUS` and hands it to
    `SerialCommunicator.find_and_connect`, which drives
    `send_json_command` at connect time -- this is the request the host
    actually emits for this command. Asserted against the constant, never
    a bare literal command-value integer, so this leg survives a
    deliberate renumber and reddens only on an accidental one."""
    comm = make_comm()
    comm.send_json_command({"cmd": COMMAND_LOCK_STATUS, "algorithm": 6})

    fake_serial._buf.seek(0)
    written = fake_serial._buf.read()

    assert written[-1:] == b"\x00"
    body = written[:-1]
    decoded = cobs_decode(body)
    payload = decoded[:-1]  # strip the trailing CRC8 byte
    sent = json.loads(payload)
    assert sent["cmd"] == COMMAND_LOCK_STATUS


@pytest.mark.parametrize("decode_byte", [DECODE_UNPROTECTED, DECODE_PROTECTED])
def test_response_parse_both_definite_decodes_preserve_raw_byte(
    make_comm, fake_serial, decode_byte
) -> None:
    """Leg 2. Both definite decodes parse via `get_response()`, and byte 0
    (the raw silicon byte) reaches the caller exactly as fed -- extracted
    from the DATA response's rendered message text."""
    comm = make_comm()
    raw = 0x3C
    fake_serial.feed(build_frame(MSG_DATA_PROTECTION_STATUS, bytes([raw, decode_byte])))

    response = comm.get_response(timeout=1.0)
    assert response.type == "DATA"
    assert response.id == MSG_DATA_PROTECTION_STATUS

    match = _LOCK_STATUS_MESSAGE_RE.search(response.message or "")
    assert match is not None, response.message
    assert bytes.fromhex(match.group(1))[0] == raw
    assert int(match.group(2)) == decode_byte


def test_raw_byte_survives_an_unrecognised_decode_unmasked(
    make_comm, fake_serial
) -> None:
    """Leg 3. A raw byte with bits set in both nibbles (`0xA5`) survives
    the whole wire path -- fed, CRC-covered, transmitted, decoded,
    rendered -- byte for byte, even though the decode byte is
    `DECODE_INDETERMINATE` and the classifier returns a non-state token.
    This is D-03's probe guarantee: an unresolved decode must never
    destroy the raw observation."""
    comm = make_comm()
    raw = 0xA5  # 1010_0101 -- bits set in both nibbles
    fake_serial.feed(
        build_frame(MSG_DATA_PROTECTION_STATUS, bytes([raw, DECODE_INDETERMINATE]))
    )

    response = comm.get_response(timeout=1.0)
    match = _LOCK_STATUS_MESSAGE_RE.search(response.message or "")
    assert match is not None, response.message
    recovered_raw = bytes.fromhex(match.group(1))[0]
    assert recovered_raw == raw  # unmasked, unshifted, unnormalised

    payload = bytes([recovered_raw, int(match.group(2))])
    token, _reason = classify_protection_response(
        GATE_TOKEN_READ_PERMITTED, payload, forced=False
    )
    assert token not in SILICON_ONLY_TOKENS, token


def test_corrupted_crc_is_rejected_not_partially_accepted(
    make_comm, fake_serial
) -> None:
    """Leg 4. A frame whose CRC byte has one bit corrupted is rejected
    outright by `decode_id_frame`'s CRC check -- never partially
    accepted. `build_frame` computes its CRC via `conftest.py`'s own
    table-free reference implementation, so a regression in the
    production lookup table (`frame_parser._crc8_ccitt`) would also be
    caught here."""
    comm = make_comm()
    good_frame = build_frame(
        MSG_DATA_PROTECTION_STATUS, bytes([0x11, DECODE_UNPROTECTED])
    )
    corrupted = bytearray(good_frame)
    corrupted[-2] ^= 0x01  # flip one bit of the CRC byte (last byte is the terminator)
    fake_serial.feed(bytes(corrupted))

    with pytest.raises(SerialTimeoutError):
        comm.get_response(timeout=0.05)


def test_truncated_payload_yields_no_state_token(make_comm, fake_serial) -> None:
    """Leg 5. A `MSG_DATA_PROTECTION_STATUS` frame with only a one-byte
    payload fails `decode_id_frame`'s fixed-width shape check and is
    dropped outright -- never partially decoded. Downstream, with no
    payload ever captured, `classify_protection_response` returns the
    operational-failure token (Task 1 step 3 of this plan), never a
    coerced decode."""
    comm = make_comm()
    fake_serial.feed(build_frame(MSG_DATA_PROTECTION_STATUS, bytes([0x11])))

    with pytest.raises(SerialTimeoutError):
        comm.get_response(timeout=0.05)

    token, _reason = classify_protection_response(
        GATE_TOKEN_READ_PERMITTED, None, forced=False
    )
    assert token not in SILICON_ONLY_TOKENS, token
    assert token == "firmware_outdated"


def test_data_band_is_not_filtered_by_get_response(make_comm, fake_serial) -> None:
    """Leg 6 -- the non-vacuity control every other leg in this file
    depends on. `get_response()` filters `NON_RESPONSE_PREFIXES =
    ["INFO", "DEBUG"]` (`serial_comm.py:424`); `DATA` is not one of them.
    This is measurably worth asserting because the INFO band IS filtered
    there -- it is exactly why `sdp_honesty.py`'s own docstring records
    that the operation layer cannot see firmware's `0x5F`/`0x61` duration
    frame. If the DATA band turned out to be filtered too, every leg above
    that relies on `get_response()` ever returning this DATA response
    would be vacuous."""
    comm = make_comm()
    fake_serial.feed(
        build_frame(MSG_DATA_PROTECTION_STATUS, bytes([0x00, DECODE_UNPROTECTED]))
    )

    response = comm.get_response(timeout=1.0)
    assert response.type == "DATA"
