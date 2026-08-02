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

import functools
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from firestarter import py32_dfu

_APP_DIR = Path(__file__).parent.parent

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


# ---------------------------------------------------------------------------
# Subprocess half (Task 2): a genuine `sys.meta_path` import blocker, run in a
# child process where `usb` is truly unreachable -- proving the import
# *graph* is clean, not merely that this devcontainer happens to lack pyusb.
# Copies the harness idiom established by `tests/test_skip_census.py`
# (`functools.lru_cache`, `[sys.executable, ...]`, `cwd=str(_APP_DIR)`,
# `capture_output=True, text=True`, an explicit `timeout=`, and a
# *prove-the-argument-took-effect* pre-check) -- see that module's docstring
# for why an in-process re-run cannot substitute when bindings are frozen at
# import (Q4, `127-RESEARCH.md`).
# ---------------------------------------------------------------------------

_CHILD_PROGRAM_TEMPLATE = '''
import importlib.abc
import json
import sys


class _UsbBlocker(importlib.abc.MetaPathFinder):
    """Raises rather than deferring. Returning None from find_spec would only
    defer to the next finder and let a genuinely installed pyusb through --
    silently making this test vacuous in the ci-py32 leg (Q4, T-127-07-07)."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "usb" or fullname.startswith("usb."):
            raise ModuleNotFoundError(f"blocked: {fullname}", name=fullname)
        return None


sys.meta_path.insert(0, _UsbBlocker())
# Required even in the pyusb-absent devcontainer, and load-bearing in the
# ci-py32 leg where pyusb is genuinely installed and may already be imported.
for _name in [m for m in list(sys.modules) if m == "usb" or m.startswith("usb.")]:
    del sys.modules[_name]

# Prove the blocker actually blocks BEFORE the CLI is imported -- a broken
# blocker must surface as a child failure, never as a silently passing test
# (the "prove the argument took effect" pattern from test_skip_census.py).
try:
    import usb  # noqa: F401

    raise SystemExit("blocker did not raise for import usb -- see find_spec")
except ModuleNotFoundError:
    pass

import requests

from firestarter import firmware as _firmware_module


def _raise_request_exception(*_args, **_kwargs):
    raise requests.RequestException(
        "blocked: no network access in this subprocess test"
    )


# Stub the HTTP seam so `fw --list` needs no network: list_releases() already
# catches requests.RequestException and returns an empty list (the real code
# path, not a bypass of it).
_firmware_module.requests.get = _raise_request_exception

from click.testing import CliRunner
from firestarter.cli_handlers import cli

runner = CliRunner()
result = runner.invoke(cli, __ARGV_JSON__)

print(
    json.dumps(
        {
            "exit_code": result.exit_code,
            "output": result.output,
            "usb_modules": sorted(
                m for m in sys.modules if m == "usb" or m.startswith("usb.")
            ),
        }
    )
)
'''


@dataclass(frozen=True)
class _BlockedCliResult:
    exit_code: int
    output: str
    usb_modules: tuple[str, ...]


@functools.lru_cache(maxsize=None)  # noqa: UP033 -- keeps the explicit-cache idiom
def _run_blocked_cli(argv: tuple[str, ...]) -> _BlockedCliResult:
    """Run `argv` against the real CLI in a subprocess where `usb` is genuinely
    unimportable, and return its exit code, output, and post-run `usb*` module
    list.

    A subprocess is required, not an in-process monkeypatch: `cli_handlers.py`'s
    `_BOARD_CHOICES` / `_PY32_ENABLED` are computed once at **import** time
    (Q4, `127-RESEARCH.md`), so an in-process poke of `sys.modules` after this
    test process has already imported `cli_handlers` would prove nothing about
    a fresh process's import graph.

    Cached per `argv` (not per-module like `test_skip_census.py`'s single
    cached run) because each invocation here is cheap and independent -- no
    two tests need the identical argv's result composed differently.
    """
    program = _CHILD_PROGRAM_TEMPLATE.replace("__ARGV_JSON__", json.dumps(list(argv)))
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(_APP_DIR),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"child process for argv={argv!r} exited {completed.returncode} -- "
        f"this is a blocker/setup failure, not a CLI assertion failure:\n"
        f"--- stderr ---\n{completed.stderr}\n--- stdout ---\n{completed.stdout}"
    )
    last_line = completed.stdout.strip().splitlines()[-1]
    payload = json.loads(last_line)
    return _BlockedCliResult(
        exit_code=payload["exit_code"],
        output=payload["output"],
        usb_modules=tuple(payload["usb_modules"]),
    )


def test_fw_help_exits_zero_with_py32_options_advertised() -> None:
    """`fw --help` exits 0, and its output contains `--board` and `--usb-id` --
    a CLI carrying py32-only options still works with pyusb genuinely
    unreachable. The real installed version is a pre-release, so the py32
    surface is present here, which is the stronger claim."""
    result = _run_blocked_cli(("fw", "--help"))
    assert result.exit_code == 0
    assert "--board" in result.output
    assert "--usb-id" in result.output


def test_fw_list_exits_zero_with_header_row() -> None:
    """`fw --list` exits 0 offline (the HTTP seam is stubbed in the child) and
    prints the header row's column labels. No assertion is made about the
    number of rows."""
    result = _run_blocked_cli(("fw", "--list"))
    assert result.exit_code == 0
    assert "Version" in result.output
    assert "Channel" in result.output
    assert "Published" in result.output
    assert "Asset URL" in result.output


@pytest.mark.parametrize("argv", [("fw", "--help"), ("fw", "--list")])
def test_nothing_imported_usb(argv: tuple[str, str]) -> None:
    """The sharpest assertion in this module: after either invocation, the
    child's `usb*` `sys.modules` list is empty. An eager top-level `import
    usb` anywhere in the import graph would have had to raise; a
    lazily-guarded one leaves no trace."""
    result = _run_blocked_cli(argv)
    assert result.usb_modules == ()


def test_firestarter_help_exits_zero_under_the_blocker() -> None:
    """The top-level group (not the `fw` subcommand) exits 0 under the same
    blocker -- the smoke path `ci.yml` already runs, now proven
    pyusb-independent."""
    result = _run_blocked_cli(("--help",))
    assert result.exit_code == 0


def test_fw_dfu_probe_surfaces_the_install_hint_at_the_cli() -> None:
    """The operator-facing absence path: `fw --dfu-probe` under the blocker
    exits non-zero and its output contains all three of the C-4-measured
    message substrings -- proving `PyusbMissingError` reaches the CLI surface
    through `DfuError` -> `FirmwareOperationError` -> `ClickException`, not
    only the library API. No exact exit code is asserted."""
    result = _run_blocked_cli(("fw", "--dfu-probe"))
    assert result.exit_code != 0
    assert "pip install 'firestarter[py32]'" in result.output
    assert "libusb" in result.output
    assert "WinUSB" in result.output
