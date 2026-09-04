"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Pytest unit tests for `firestarter/transport_counters.py` (Phase 176 plan 01,
RPT-C1/RPT-C2).

The sink is process-lifetime, module-level state, so every test resets it
first with `transport_counters.reset()` -- matching the module's own
reset-before-measurement-window contract.

Test taxonomy:

  Sink contract
    test_snapshot_after_reset_returns_wired_keys_sorted_zeroed -> keys
        sorted, all zero after reset()
    test_snapshot_returns_a_fresh_copy -> mutating a returned snapshot dict
        never reaches back into the sink

  End-to-end decode-failure path (the plus-one-and-nothing-else-moved leg)
    test_decode_id_frame_corrupt_crc_raises_decode_failures_by_one_and_nothing_else
        -> direct _decode_id_frame call on a CRC-flipped body
    test_corrupt_frame_through_read_and_parse_lines_raises_decode_failures_by_one
        -> the same corrupt frame through the generator's otherwise-silent
           drop path

  Lifetime
    test_counter_survives_communicator_teardown -> readable after the
        incrementing SerialCommunicator is discarded

  Report surface
    test_default_transport_health_decode_failures_not_measured -> a
        default-constructed report renders "not measured", never 0
    test_to_dict_decode_failures_reflects_assigned_integer -> a real integer
        assigned onto transport.decode_failures round-trips through
        to_dict()

References:
  - .planning/phases/176-transport-instrumentation-connect-cost-measurement-partially/176-01-PLAN.md
  - .planning/phases/176-transport-instrumentation-connect-cost-measurement-partially/176-RESEARCH.md
  - .planning/phases/176-transport-instrumentation-connect-cost-measurement-partially/176-PATTERNS.md
"""

from __future__ import annotations

from firestarter import transport_counters
from firestarter.diagnostic_report import NOT_MEASURED
from firestarter.frame_parser import _crc8_ccitt
from firestarter.messages import MSG_OK_READY
from tests.conftest import build_frame
from tests.test_diagnostic_report import _build_report


def _corrupted_ok_ready_body() -> bytes:
    """A valid MSG_OK_READY body with its trailing CRC byte XOR'd by one.

    Same construction as `tests/test_serial_comm.py`'s CAP-01 CRC-flip
    trigger: a 2-byte param region, a real `_crc8_ccitt`, then one flipped
    bit in the CRC byte -- guaranteed to fail `codec.decode_id_frame`'s CRC
    check without touching any other field.
    """
    params = b"\x02\x00"
    msg_id_byte = bytes([MSG_OK_READY])
    crc = _crc8_ccitt(msg_id_byte + params)
    body = bytearray(msg_id_byte + params + bytes([crc]))
    body[-1] ^= 1
    return bytes(body)


def test_snapshot_after_reset_returns_wired_keys_sorted_zeroed() -> None:
    transport_counters.reset()
    result = transport_counters.snapshot()
    assert list(result) == sorted(result)
    assert list(result) == ["decode_failures"]
    assert all(value == 0 for value in result.values())


def test_snapshot_returns_a_fresh_copy() -> None:
    transport_counters.reset()
    transport_counters.record_decode_failure()
    first = transport_counters.snapshot()
    first["decode_failures"] = 999
    second = transport_counters.snapshot()
    assert second["decode_failures"] == 1


def test_decode_id_frame_corrupt_crc_raises_decode_failures_by_one_and_nothing_else(
    make_comm,
) -> None:
    transport_counters.reset()
    comm = make_comm()
    good_params = b"\x02\x00"
    good_msg_id_byte = bytes([MSG_OK_READY])
    good_crc = _crc8_ccitt(good_msg_id_byte + good_params)
    good_body = good_msg_id_byte + good_params + bytes([good_crc])

    good_result = comm._decode_id_frame(len(good_body), good_body)
    unmoved = transport_counters.snapshot()
    assert good_result is not None
    assert unmoved["decode_failures"] == 0

    bad_body = _corrupted_ok_ready_body()
    bad_result = comm._decode_id_frame(len(bad_body), bad_body)
    after = transport_counters.snapshot()

    assert bad_result is None
    assert after["decode_failures"] == unmoved["decode_failures"] + 1
    unmoved_rest = {k: v for k, v in unmoved.items() if k != "decode_failures"}
    after_rest = {k: v for k, v in after.items() if k != "decode_failures"}
    assert after_rest == unmoved_rest


def test_corrupt_frame_through_read_and_parse_lines_raises_decode_failures_by_one(
    make_comm, fake_serial
) -> None:
    transport_counters.reset()
    comm = make_comm()
    wire = bytearray(build_frame(MSG_OK_READY, b"\x02\x00"))
    wire[-2] ^= 1
    fake_serial.feed(bytes(wire))

    before = transport_counters.snapshot()
    list(comm._read_and_parse_lines(0.05))
    after = transport_counters.snapshot()

    assert after["decode_failures"] == before["decode_failures"] + 1
    before_rest = {k: v for k, v in before.items() if k != "decode_failures"}
    after_rest = {k: v for k, v in after.items() if k != "decode_failures"}
    assert after_rest == before_rest


def test_counter_survives_communicator_teardown(make_comm) -> None:
    transport_counters.reset()
    comm = make_comm()
    body = _corrupted_ok_ready_body()
    comm._decode_id_frame(len(body), body)
    del comm
    assert transport_counters.snapshot()["decode_failures"] == 1


def test_default_transport_health_decode_failures_not_measured() -> None:
    report = _build_report()
    d = report.to_dict()
    assert d["transport_health"]["decode_failures"] == NOT_MEASURED
    assert d["transport_health"]["decode_failures"] != 0


def test_to_dict_decode_failures_reflects_assigned_integer() -> None:
    report = _build_report()
    report.transport.decode_failures = 3
    d = report.to_dict()
    assert d["transport_health"]["decode_failures"] == 3
