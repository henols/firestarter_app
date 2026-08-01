"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 127 Plan 127-07 (HOST-05 / D-05 / D-06): covers `PyusbMissingError`'s
de-pragma'd branch in-process, and proves in a subprocess that the CLI
survives pyusb's genuine absence.

Two independent mechanisms, for two independent reasons:

* A subprocess contributes nothing to the parent pytest process's
  `--cov-fail-under=70` coverage run (coverage.py only instruments the
  process it is attached to), so the two statements the removed
  `# pragma: no cover` used to hide need an **in-process** monkeypatch of
  `sys.modules` to be measured at all.
* Conversely, only a genuine subprocess absence -- with `usb` and `usb.*`
  blocked at `sys.meta_path` before the CLI is even imported -- proves the
  import *graph* is clean. An in-process `sys.modules` poke only
  *simulates* absence: an eager top-level `import usb` anywhere in the
  import graph would already have succeeded before the fixture ran, which
  is precisely the regression this half exists to catch. `_BOARD_CHOICES` /
  `_PY32_ENABLED` in `cli_handlers.py` are computed once at import time
  (127-RESEARCH.md Q4), so an in-process poke after this test process has
  already imported `cli_handlers` proves nothing about a *fresh* process's
  import graph -- see `tests/test_skip_census.py`'s module docstring for
  the same argument applied to a sibling frozen-at-import binding.

C-4 (`127-RESEARCH.md`, MEASURED): the message this module asserts on says
`pip install 'firestarter[py32]'`, `libusb` and `WinUSB` -- the
driver-installer utility CONTEXT's original D-06 wording named (`Zadig`)
appears nowhere in `py32_dfu.py`; asserting it would be red on day one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from firestarter import py32_dfu

# ---------------------------------------------------------------------------
# In-process half (Task 1): covers the two statements the removed
# `# pragma: no cover` used to hide, and pins `PyusbMissingError`'s shape.
# ---------------------------------------------------------------------------


def test_require_usb_raises_pyusb_missing_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_require_usb()` raises the concrete `PyusbMissingError` subclass -- not a
    bare `ImportError`, not `DfuError` generically -- when `usb.core` cannot be
    imported."""
    monkeypatch.setitem(sys.modules, "usb", None)
    monkeypatch.setitem(sys.modules, "usb.core", None)
    with pytest.raises(py32_dfu.PyusbMissingError) as excinfo:
        py32_dfu._require_usb()
    assert type(excinfo.value) is py32_dfu.PyusbMissingError


def test_pyusb_missing_error_message_substrings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The message names the three facts C-4 measured actually present in the
    source -- not `Zadig`, which appears nowhere in the file."""
    monkeypatch.setitem(sys.modules, "usb", None)
    monkeypatch.setitem(sys.modules, "usb.core", None)
    with pytest.raises(py32_dfu.PyusbMissingError) as excinfo:
        py32_dfu._require_usb()
    message = str(excinfo.value)
    # C-4, MEASURED (127-RESEARCH.md): these three substrings are the ones
    # actually present in py32_dfu.py's PyusbMissingError message body.
    assert "pip install 'firestarter[py32]'" in message
    assert "libusb" in message
    assert "WinUSB" in message


def test_pyusb_missing_error_chains_the_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`raise ... from exc` survived -- `__cause__` is the original `ImportError`,
    which is the diagnostic a user's traceback depends on."""
    monkeypatch.setitem(sys.modules, "usb", None)
    monkeypatch.setitem(sys.modules, "usb.core", None)
    with pytest.raises(py32_dfu.PyusbMissingError) as excinfo:
        py32_dfu._require_usb()
    assert isinstance(excinfo.value.__cause__, ImportError)


def test_pyusb_missing_error_is_a_dfu_error() -> None:
    """`_install_with_dfu`'s `except DfuError` still catches `PyusbMissingError`
    and converts it to `FirmwareOperationError` -- a one-line structural
    assertion that keeps the error-to-exit-code chain honest."""
    assert issubclass(py32_dfu.PyusbMissingError, py32_dfu.DfuError)


def test_require_usb_pragma_is_gone_and_the_other_two_remain() -> None:
    """The `except ImportError` line inside `_require_usb()` carries no
    coverage-exclusion comment, while the file still carries exactly **two**
    such comments in total (the out-of-scope `_dev` / `_index` guards) -- so
    this test also fails if someone deletes either of those two instead."""
    source_path = Path(py32_dfu.__file__)
    lines = source_path.read_text().splitlines()

    require_usb_index = next(
        (
            i
            for i, line in enumerate(lines)
            if line.strip().startswith("def _require_usb(")
        ),
        None,
    )
    # Non-vacuity guard: fail loudly if `_require_usb` moved or was renamed,
    # rather than silently passing because the search below found nothing.
    assert require_usb_index is not None, "_require_usb() not found in source"

    except_line = next(
        line
        for line in lines[require_usb_index : require_usb_index + 20]
        if "except ImportError" in line
    )
    assert "pragma: no cover" not in except_line

    total_pragmas = sum(1 for line in lines if "pragma: no cover" in line)
    assert total_pragmas == 2
