"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 127 / Plan 127-06 (HOST-04 / D-02 / D-03) — keep the optional-dependency
collection gate honest.

tests/conftest.py's `collect_ignore` keeps tests/test_pyusb_api_surface.py out
of the primary (pyusb-absent) leg via a CONDITIONAL COLLECTION RULE, not a skip
marker. A conditional collection rule is invisible when it misfires -- pytest
reports nothing for a module it silently never collected -- so it needs its
own gate that runs in BOTH legs (this module carries no marker of any kind).

This module also carries a fifth, unrelated-to-the-gate check (C-6's second
strengthening): that no production `ctrl_transfer` call-site in
firestarter/py32_dfu.py passes its 5th positional argument by keyword. It
lives here, not in the gated module, precisely because it needs no pyusb and
must run in every suite execution.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import tests.conftest as conftest

_TESTS_DIR = Path(__file__).parent
_GATED_MODULE_NAME = "test_pyusb_api_surface.py"
_PY32_DFU_PATH = _TESTS_DIR.parent / "firestarter" / "py32_dfu.py"


def test_gated_module_exists() -> None:
    """tests/test_pyusb_api_surface.py must be present on disk.

    A rename requires updating BOTH tests/conftest.py's `collect_ignore` and
    the `ci-py32` job's pytest argument in .github/workflows/ci.yml.
    """
    path = _TESTS_DIR / _GATED_MODULE_NAME
    assert path.is_file(), (
        f"{_GATED_MODULE_NAME} is missing from tests/. Renaming or removing "
        "it requires updating both tests/conftest.py's `collect_ignore` and "
        "the ci-py32 job's pytest argument in .github/workflows/ci.yml."
    )


def test_every_collect_ignore_entry_names_a_real_file() -> None:
    """A stale collect_ignore entry would silently ignore nothing.

    Each entry is a path relative to tests/conftest.py's directory (how
    pytest interprets `collect_ignore`).
    """
    entries = conftest.collect_ignore
    for entry in entries:
        resolved = _TESTS_DIR / entry
        assert resolved.is_file(), (
            f"collect_ignore entry {entry!r} does not resolve to a real "
            f"file under tests/ ({resolved}) -- a stale entry silently "
            "disarms nothing."
        )


def test_gate_armed_correctly_for_this_environment() -> None:
    """The gate must invert correctly with pyusb's actual availability.

    Biconditional: the gated module's name is in `collect_ignore` if and
    only if `importlib.util.find_spec("usb")` is None. This is a real
    behavioural assertion in whichever leg it runs.
    """
    pyusb_absent = importlib.util.find_spec("usb") is None
    module_is_ignored = _GATED_MODULE_NAME in conftest.collect_ignore
    assert module_is_ignored == pyusb_absent, (
        f"collect_ignore membership for {_GATED_MODULE_NAME!r} "
        f"({module_is_ignored}) must equal pyusb absence ({pyusb_absent}) "
        "-- the gate has failed to invert with this environment."
    )


def test_gate_is_keyed_on_find_spec_usb() -> None:
    """The condition must be keyed on what it claims to be keyed on.

    A source scan of tests/conftest.py, behind a non-vacuity guard that the
    file was actually read and the `collect_ignore` assignment was located.
    """
    conftest_path = _TESTS_DIR / "conftest.py"
    source = conftest_path.read_text(encoding="utf-8")
    assert source, "tests/conftest.py read as empty text -- non-vacuity guard tripped"

    assignment = re.search(r"^collect_ignore\b.*$", source, re.MULTILINE)
    assert assignment is not None, (
        "no module-scope `collect_ignore` assignment found in "
        "tests/conftest.py -- cannot verify what it is keyed on"
    )

    assert "find_spec" in source, (
        "the collect_ignore gate must be keyed on "
        'importlib.util.find_spec("usb"), but "find_spec" does not appear '
        "in tests/conftest.py"
    )
    assert '"usb"' in source, (
        'the collect_ignore gate must probe for the "usb" module name, '
        'but the literal "usb" does not appear in tests/conftest.py'
    )


def test_no_production_ctrl_transfer_call_passes_5th_arg_by_keyword() -> None:
    """No production `ctrl_transfer` call-site passes its 5th arg by keyword.

    C-6's second strengthening: the real pyusb signature is
    `ctrl_transfer(self, bmRequestType, bRequest, wValue, wIndex,
    data_or_wLength, timeout)`. tests/test_pyusb_api_surface.py (ci-py32 only)
    pins that parameter order; this test guards the OTHER half of the
    contract in every leg -- that every call site passes it positionally, so
    a rename of `data_or_wLength` cannot hide behind a keyword argument.

    A floor, not an exact count, is asserted: Plan 127-09 adds UPLOAD call
    sites, which would only raise this count.
    """
    source = _PY32_DFU_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_PY32_DFU_PATH))

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "ctrl_transfer"
    ]

    assert len(calls) >= 5, (
        f"expected at least 5 ctrl_transfer call sites in "
        f"firestarter/py32_dfu.py, found {len(calls)} -- non-vacuity guard "
        "for the positional/keyword scan below"
    )

    for call in calls:
        assert len(call.args) >= 5 and not call.keywords, (
            f"ctrl_transfer call at firestarter/py32_dfu.py:{call.lineno} "
            f"must pass all arguments positionally (found {len(call.args)} "
            f"positional, {len(call.keywords)} keyword) -- pyusb's "
            "ctrl_transfer parameter order is pinned by "
            "tests/test_pyusb_api_surface.py, and a keyword argument would "
            "survive a parameter rename undetected"
        )
