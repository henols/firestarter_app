"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 127 / Plan 127-06 (HOST-04 / D-03) — the real pyusb API surface,
exercised for real.

Research finding 8 (`.planning/research/SUMMARY.md`): the real pyusb API
surface used by `firestarter/py32_dfu.py` is exercised NOWHERE -- not in CI
(`pip install -e .[test]` never installs `.[test,py32]`) and not locally
(pyusb is not importable in the shared devcontainer). `usb.core.find` and
`ctrl_transfer`'s argument order are therefore unverified against a real
pyusb release, and the in-repo fake (`tests/test_py32_dfu.py`'s in-repo fake
USB device) has already drifted from the real signature.

This module runs ONLY where pyusb is installed. It is kept out of the
primary (pyusb-absent) leg by `tests/conftest.py`'s conditional
`collect_ignore`, chosen because it produces a NON-COLLECTION rather than a
skip -- so no new `ALLOWED_SKIP_REASONS` entry is owed. The `ci-py32` CI job
names this file explicitly (`pytest tests/test_pyusb_api_surface.py -q`),
which is exactly why a missing `[py32]` extra surfaces there as a hard
collection error rather than a quiet pass: `collect_ignore` does not
suppress a path named directly on the command line.

Plan 127-08 (HOST-03/HOST-04, C-6) adds the fake-vs-real comparison below:
`tests/test_py32_dfu.py`'s `_FakeUsbDevice` -- the *only* device model the
58 DFU sequencing tests run against -- is compared against the real
`usb.core.Device.ctrl_transfer` pinned above. This module is the one place
the real signature is available to compare against, which is why the
comparison could not be written in Plan 127-06 (it would have been red
until 127-08 aligned the fake).

**Claim ceiling.** Enumerating USB devices and pinning `ctrl_transfer`'s
signature proves pyusb imports and that its API is shaped as expected. It
proves NOTHING about a PY32F071 -- no PCB exists, and no DFU device is
present in CI.
"""

from __future__ import annotations

import importlib.metadata
import inspect

import usb.core

from tests.test_py32_dfu import _FakeUsbDevice

# Independent expectation, written here rather than derived from
# `inspect` at import time -- this is what makes a real pyusb parameter
# rename or reorder detectable rather than silent. Measured against pyusb
# 1.3.1 (`127-RESEARCH.md` §Q3); all five production call-sites in
# `firestarter/py32_dfu.py` pass these positionally.
_CTRL_TRANSFER_PARAMS = [
    "self",
    "bmRequestType",
    "bRequest",
    "wValue",
    "wIndex",
    "data_or_wLength",
]


def test_pyusb_genuinely_imports_and_meets_the_declared_floor() -> None:
    """pyusb must genuinely import, and its version must satisfy the floor.

    `pyproject.toml`'s `[py32]` extra declares `pyusb>=1.3.1,<2` (D-19).
    Compare the resolved version's first two components as INTEGERS, not
    lexically -- a lexical string compare would wrongly rank "1.10.0" below
    "1.3.1".
    """
    version = importlib.metadata.version("pyusb")
    parts = version.split(".")
    major, minor = int(parts[0]), int(parts[1])
    assert (major, minor) >= (1, 3), (
        f"resolved pyusb version {version!r} does not meet the declared "
        "floor pyusb>=1.3.1 (compared as integer (major, minor) tuples, "
        "not lexically)"
    )


def test_usb_core_find_for_real() -> None:
    """`usb.core.find(find_all=True)` is called for real, either/or.

    Never a bare `pass`: the outcome is one of exactly two named branches --
    enumeration succeeded (any length, including zero -- a runner may
    enumerate zero devices without raising, so a non-zero count must never
    be asserted), or `usb.core.NoBackendError` was raised. The `except`
    clause below names ONLY `usb.core.NoBackendError` -- never `ValueError`
    or `Exception` -- because `NoBackendError` subclasses `ValueError` (see
    the next test) and a broad catch would silently make this vacuous.
    """
    outcome = None
    try:
        devices = list(usb.core.find(find_all=True))
        outcome = "enumerated"
        assert isinstance(devices, list), (
            f"the enumerated branch must materialise a list, got {type(devices)!r}"
        )
    except usb.core.NoBackendError:
        outcome = "no_backend"

    assert outcome is not None, (
        "usb.core.find(find_all=True) neither enumerated nor raised "
        "NoBackendError -- the either/or outcome sentinel was never set"
    )
    assert outcome in ("enumerated", "no_backend"), (
        f"unexpected outcome branch {outcome!r}"
    )


def test_no_backend_error_is_a_value_error() -> None:
    """`usb.core.NoBackendError` subclasses `ValueError`.

    This is why the test above catches it explicitly and narrowly rather
    than with a broad handler: a handler naming ValueError or Exception
    anywhere in this file would also catch NoBackendError, making the
    either/or assertion above pass regardless of which branch actually
    occurred.
    """
    assert issubclass(usb.core.NoBackendError, ValueError), (
        "usb.core.NoBackendError no longer subclasses ValueError -- "
        "re-examine the except clause in test_usb_core_find_for_real"
    )


def test_ctrl_transfer_parameter_order_is_pinned() -> None:
    """`ctrl_transfer`'s first six parameter names, in order, are pinned.

    Read from the INSTALLED pyusb via `inspect.signature`, compared against
    the independent literal `_CTRL_TRANSFER_PARAMS` above.
    """
    signature = inspect.signature(usb.core.Device.ctrl_transfer)
    observed = list(signature.parameters.keys())[: len(_CTRL_TRANSFER_PARAMS)]
    assert observed == _CTRL_TRANSFER_PARAMS, (
        f"usb.core.Device.ctrl_transfer's parameter order changed.\n"
        f"  expected: {_CTRL_TRANSFER_PARAMS}\n"
        f"  observed: {observed}\n"
        "A pyusb upgrade is the likely cause -- every production call-site "
        "in firestarter/py32_dfu.py passes these arguments positionally."
    )


def test_ctrl_transfer_timeout_is_optional() -> None:
    """`timeout` exists in the real signature and carries a default.

    The in-repo fake lacks a `timeout` parameter entirely (C-6); Plan 127-08
    reconciles that gap. This test records the real shape only.
    """
    signature = inspect.signature(usb.core.Device.ctrl_transfer)
    assert "timeout" in signature.parameters, (
        "usb.core.Device.ctrl_transfer no longer declares a `timeout` parameter"
    )
    timeout_param = signature.parameters["timeout"]
    assert timeout_param.default is not inspect.Parameter.empty, (
        "usb.core.Device.ctrl_transfer's `timeout` parameter no longer has "
        "a default -- it would become a required argument"
    )


def test_fake_ctrl_transfer_signature_matches_the_real_one() -> None:
    """`_FakeUsbDevice.ctrl_transfer`'s signature matches the real one.

    `tests/test_py32_dfu.py`'s `_FakeUsbDevice` is the *only* device model
    the 58 DFU sequencing tests run against. A silent divergence from the
    real `usb.core.Device.ctrl_transfer` would make all of them agree with
    each other and with nothing real -- Plan 127-08 / C-6.
    """
    real_signature = inspect.signature(usb.core.Device.ctrl_transfer)
    fake_signature = inspect.signature(_FakeUsbDevice.ctrl_transfer)
    real_names = list(real_signature.parameters.keys())
    fake_names = list(fake_signature.parameters.keys())

    # 1. Order-sensitive comparison over the full overlapping prefix,
    #    including data_or_wLength and timeout.
    overlap = min(len(real_names), len(fake_names))
    assert fake_names[:overlap] == real_names[:overlap], (
        "_FakeUsbDevice.ctrl_transfer's parameter order no longer matches "
        "the real usb.core.Device.ctrl_transfer.\n"
        f"  real: {real_names}\n"
        f"  fake: {fake_names}\n"
        "Reconcile per Plan 127-08 / C-6."
    )

    # 2. Every parameter the real signature defaults, the fake also
    #    defaults -- a production call omitting an argument against the
    #    real device would also be legal against the fake.
    for name in real_names:
        real_param = real_signature.parameters[name]
        if real_param.default is inspect.Parameter.empty:
            continue
        assert name in fake_signature.parameters, (
            f"real ctrl_transfer's defaulted parameter {name!r} is absent "
            "from the fake's signature entirely"
        )
        fake_param = fake_signature.parameters[name]
        assert fake_param.default is not inspect.Parameter.empty, (
            f"real ctrl_transfer defaults {name!r}, but the fake's "
            f"{name!r} is required -- a production call that omits it "
            "would be legal against the real device but not the fake"
        )

    # 3. Non-vacuity guard: both signatures were actually obtained, and
    #    each carries more than two parameters -- otherwise the comparison
    #    above would be vacuously true.
    assert len(real_names) > 2, (
        f"real ctrl_transfer signature has only {len(real_names)} "
        "parameters -- the comparison above would be vacuously true"
    )
    assert len(fake_names) > 2, (
        f"fake ctrl_transfer signature has only {len(fake_names)} "
        "parameters -- the comparison above would be vacuously true"
    )
