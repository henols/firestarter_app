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
skip. The `ci-py32` CI job
names this file explicitly (`pytest tests/test_pyusb_api_surface.py -q`),
which is exactly why a missing `[py32]` extra surfaces there as a hard
collection error rather than a quiet pass: `collect_ignore` does not
suppress a path named directly on the command line.

The DFU download sequence also runs here against a device whose every
`ctrl_transfer` call is bound to the installed pyusb's real method through an
autospec, so a call the real method would refuse fails here too. This module
is the one place the real method is available.

**Claim ceiling.** Enumerating USB devices and binding calls to the real
`ctrl_transfer` proves pyusb imports and accepts the calls `py32_dfu` makes.
It proves NOTHING about a PY32F071 -- no PCB exists, and no DFU device is
present in CI.
"""

from __future__ import annotations

import importlib.metadata
from unittest import mock

import pytest
import usb.core

from firestarter import py32_dfu
from firestarter.py32_dfu import Py32DfuFlasher
from tests.test_py32_dfu import _FakeUsbDevice, _interface


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


class _InstalledSignatureDevice:
    """The fake DFU device, with every ctrl_transfer call first bound to the
    INSTALLED pyusb's `Device.ctrl_transfer` through an autospec. A call the
    real method would not accept raises TypeError here, as it would on a
    real device."""

    def __init__(self, fake: _FakeUsbDevice) -> None:
        self.fake = fake
        self.spec = mock.create_autospec(usb.core.Device, instance=True)

    def ctrl_transfer(self, *args, **kwargs):
        self.spec.ctrl_transfer(*args, **kwargs)
        return self.fake.ctrl_transfer(*args, **kwargs)


@pytest.mark.parametrize("dfuse", [True, False])
def test_a_dfu_flash_calls_ctrl_transfer_as_installed_pyusb_accepts(
    monkeypatch, tmp_path, dfuse
) -> None:
    """The DFU download sequence runs against the real `ctrl_transfer`
    signature: every call binds, including the ones that omit `timeout`."""
    device = _InstalledSignatureDevice(_FakeUsbDevice())
    interface = _interface(device, dfuse=dfuse)
    monkeypatch.setattr(
        py32_dfu, "find_dfu_interfaces", lambda vid=None, pid=None: [interface]
    )
    monkeypatch.setattr(py32_dfu, "_sleep", lambda _seconds: None)
    image = tmp_path / "fw.bin"
    image.write_bytes(bytes(range(130)))

    assert Py32DfuFlasher().flash(str(image)) is True
    assert device.fake.calls, "the flash made no ctrl_transfer call"
    assert device.spec.ctrl_transfer.call_count == len(device.fake.calls)


def test_a_call_the_installed_pyusb_would_refuse_is_refused() -> None:
    """The control: the autospec does check the signature."""
    device = _InstalledSignatureDevice(_FakeUsbDevice())
    with pytest.raises(TypeError):
        device.ctrl_transfer(0x21, 1, 0, 0, None, 1000, "extra")
    with pytest.raises(TypeError):
        device.ctrl_transfer(0x21, 1, data_length=None)
