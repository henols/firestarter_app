"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Pytest unit tests for the `EpromOperationError.error_code` seam (RPT-03 / D-07).

Closes Phase 108 / Plan 108-01: proves the optional, backward-compatible
`error_code` kwarg on `EpromOperationError` exists, is inherited by its
subclasses, and is threaded through the single `_raise_for_error_response`
chokepoint from the firmware `response.id` byte.

Test taxonomy (5 tests total):

  RPT-03 Test 1  test_eprom_operation_error_stores_code        -> .error_code == kwarg
  RPT-03 Test 2  test_eprom_operation_error_default_none       -> .error_code is None
  RPT-03 Test 3  test_subclass_inherits_error_code             -> ProtocolNotImplementedError
  RPT-03 Test 4  test_raise_for_error_carries_id               -> chokepoint pass-through
  RPT-03 Test 5  test_protocol_not_impl_fork_preserved          -> dispatch fork unchanged

No serial, no hardware, no mock operator needed -- these are pure
exception/chokepoint tests exercising `_raise_for_error_response` directly
with a synthetic `Response` namedtuple.

References:
  - .planning/phases/108-test-plan-engine-address-derived-pattern-fingerprint/108-01-PLAN.md
  - .planning/phases/108-test-plan-engine-address-derived-pattern-fingerprint/108-RESEARCH.md
    § error_code Seam / D-07
"""

from __future__ import annotations

import pytest

from firestarter.eprom_operations import _raise_for_error_response
from firestarter.exceptions import (
    ChipNotImplementedError,
    EpromOperationError,
    ProtocolNotImplementedError,
)
from firestarter.frame_parser import Response
from firestarter.messages import MSG_ERR_PROTOCOL_NOT_IMPLEMENTED


def test_eprom_operation_error_stores_code():
    err = EpromOperationError("boom", error_code=0xA4)
    assert err.error_code == 0xA4
    assert str(err) == "boom"


def test_eprom_operation_error_default_none():
    err = EpromOperationError("boom")
    assert err.error_code is None


def test_subclass_inherits_error_code():
    err = ProtocolNotImplementedError("x", error_code=0xBB)
    assert err.error_code == 0xBB

    default_err = ChipNotImplementedError("x")
    assert default_err.error_code is None


def test_raise_for_error_carries_id():
    response = Response(
        type="ERROR", message="some firmware error", payload=None, id=0xA4
    )

    with pytest.raises(EpromOperationError) as exc_info:
        _raise_for_error_response(response, "some firmware error")

    assert exc_info.value.error_code == 0xA4


def test_protocol_not_impl_fork_preserved():
    response = Response(
        type="ERROR",
        message="protocol not implemented",
        payload=None,
        id=MSG_ERR_PROTOCOL_NOT_IMPLEMENTED,
    )

    with pytest.raises(ProtocolNotImplementedError) as exc_info:
        _raise_for_error_response(response, "protocol not implemented")

    assert exc_info.value.error_code == MSG_ERR_PROTOCOL_NOT_IMPLEMENTED
