"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 127 Plan 127-04 -- HOST-02 / D-08 (one shared refusal helper closes the
live `--usb-id`-accepted-on-stable gap) and HOST-08 / D-07 (channel gating
proven both ways, in one test module, deterministically in both CI legs).

**Why a subprocess.** `firestarter.channel.is_prerelease_build()` imports
`firestarter` *inside* the function body, so it re-reads
`firestarter.__version__` on every call and is straightforwardly
monkeypatchable in-process. But `firestarter/cli_handlers.py`'s
`_BOARD_CHOICES` and `_PY32_ENABLED` are computed exactly once, at import
time, from that same version (see the comment above those two module
globals in `cli_handlers.py`). An in-process monkeypatch of
`firestarter.__version__` after `cli_handlers` has already been imported in
this test process would flip the service-layer choke point (`channel.py`)
while leaving the CLI surface -- `_BOARD_CHOICES`, `_PY32_ENABLED`, and
therefore every `hidden=` decision and both `_reject_py32_only_option` call
sites -- stale at whatever version was current the first time `cli_handlers`
was imported. That is a test that would pass, but for the wrong reason.

One subprocess per simulated version makes the import-time computation a
structural fact instead of an assertion about a mock: `firestarter.__version__`
is assigned *before* `firestarter.cli_handlers` is ever imported in that
child process, and the child's own preamble asserts as much before doing so
(see `_CHILD_PROGRAM` below) -- ROADMAP Criterion 5's "computed at import
time, not cached stale across a version change", proved by construction.

**Why not a module-reload approach.** Re-executing `cli_handlers`'s module
body in place -- rather than in a fresh process -- re-evaluates every
`@click.option` decorator and rebuilds the Click command objects from
scratch, but `cli.py`'s `@cli.group()` / `add_command()` wiring still holds
references to the *old* command objects. A test built on a module-reload
approach would silently assert against a stale command object while
believing it exercises the live one. `tests/test_skip_census.py`'s module
docstring documents the identical frozen-at-import property for its own
subject (`tests/fw_presence.py`'s `FW_REPO_PRESENT` / `FW_ABSENT_REASON`
bindings) and reaches the same conclusion: an in-process re-run cannot see a
different environment than the one that was live when the process started.

**Determinism.** Every assertion below holds identically whether or not
`pyusb` is importable in this interpreter -- none reaches the network or an
attached device. This module carries no skip marker of any kind and adds no
`ALLOWED_SKIP_REASONS` entry (`tests/test_skip_census.py` covers that).
"""

from __future__ import annotations

import functools
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_APP_DIR = Path(__file__).parent.parent

# Every version string used by this module's tests is a literal below, not
# read from `firestarter.__version__` -- the point is to simulate a channel,
# not observe whichever one this checkout currently is.
_STABLE_VERSION = "3.0.0"
_PRERELEASE_VERSION = "3.0.0b1"

# The child program, run via `python -c`. Order is load-bearing (see module
# docstring): `firestarter` is imported bare first; the preamble then asserts
# `firestarter.cli_handlers` is not yet in `sys.modules`; ONLY THEN is
# `__version__` overwritten; ONLY THEN is `cli_handlers` imported for the
# first time in this process -- so `_BOARD_CHOICES` / `_PY32_ENABLED` are
# computed against the simulated version by construction, never patched
# after the fact.
_CHILD_PROGRAM = """
import json
import sys

import firestarter

assert "firestarter.cli_handlers" not in sys.modules, (
    "firestarter.cli_handlers was already imported before the simulated "
    "version was assigned -- _BOARD_CHOICES/_PY32_ENABLED would be frozen "
    "against the wrong version"
)
firestarter.__version__ = {version!r}

from click.testing import CliRunner
from firestarter import cli_handlers

runner = CliRunner()
result = runner.invoke(cli_handlers.cli, {argv!r})
print(json.dumps({{
    "version": firestarter.__version__,
    "board_choices": cli_handlers._BOARD_CHOICES,
    "py32_enabled": cli_handlers._PY32_ENABLED,
    "exit_code": result.exit_code,
    "output": result.output,
}}))
"""


@dataclass(frozen=True)
class _ChildResult:
    """One child process's parsed report -- see `_CHILD_PROGRAM`."""

    version: str
    board_choices: list[str]
    py32_enabled: bool
    exit_code: int
    output: str


@functools.lru_cache(maxsize=None)  # noqa: UP033 -- lru_cache named explicitly by plan
def _run_cli(version: str, argv: tuple[str, ...]) -> _ChildResult:
    """Run `firestarter <argv>` in a fresh process simulating `version`.

    Cached (per (version, argv) pair) so the several assertions per simulated
    channel below pay the child-process cost exactly once each.
    """
    program = _CHILD_PROGRAM.format(version=version, argv=list(argv))
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(_APP_DIR),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"child process for version={version!r} argv={argv!r} exited "
            f"{result.returncode} (a non-zero return here means the child's "
            f"own preamble assertion failed, or it crashed) -- stderr:\n"
            f"{result.stderr}"
        )
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["version"] == version, (
        f"child reported version {report['version']!r}, expected "
        f"{version!r} -- the simulated-version patch did not take effect "
        f"before firestarter.cli_handlers was imported"
    )
    return _ChildResult(
        version=report["version"],
        board_choices=list(report["board_choices"]),
        py32_enabled=report["py32_enabled"],
        exit_code=report["exit_code"],
        output=report["output"],
    )


# ---------------------------------------------------------------------------
# Simulated stable (`__version__ = "3.0.0"`)
# ---------------------------------------------------------------------------


def test_simulated_stable_board_choices_and_flag() -> None:
    """Stable: py32f071 is absent from `_BOARD_CHOICES`, `_PY32_ENABLED` is
    False."""
    result = _run_cli(_STABLE_VERSION, ("fw", "--help"))
    assert result.board_choices == ["uno", "uno328pb", "leonardo"]
    assert result.py32_enabled is False


def test_simulated_stable_help_omits_py32f071() -> None:
    result = _run_cli(_STABLE_VERSION, ("fw", "--help"))
    assert "py32f071" not in result.output


def test_simulated_stable_dfu_probe_rejected() -> None:
    """Unchanged behaviour: `--dfu-probe` was already refused before this
    plan; this pins that it still is."""
    result = _run_cli(_STABLE_VERSION, ("fw", "--dfu-probe"))
    assert result.exit_code == 2
    assert "no such option: --dfu-probe" in result.output


def test_simulated_stable_usb_id_rejected() -> None:
    """HOST-02's closure. The refusal fires before the release listing, so
    `--list` never reaches the network on a simulated stable build -- this
    is the live gap Task 1 closed."""
    result = _run_cli(_STABLE_VERSION, ("fw", "--usb-id", "1a86:8012", "--list"))
    assert result.exit_code == 2
    assert "no such option: --usb-id" in result.output


def test_simulated_stable_board_choice_rejects_py32f071() -> None:
    """Click's own `Choice` rejection -- proves the board is genuinely absent
    from the choice set, not merely hidden from `--help`."""
    result = _run_cli(_STABLE_VERSION, ("fw", "--board", "py32f071", "--list"))
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Simulated pre-release (`__version__ = "3.0.0b1"`)
# ---------------------------------------------------------------------------


def test_simulated_prerelease_board_choices_and_flag() -> None:
    result = _run_cli(_PRERELEASE_VERSION, ("fw", "--help"))
    assert result.board_choices == ["uno", "uno328pb", "leonardo", "py32f071"]
    assert result.py32_enabled is True


def test_simulated_prerelease_help_includes_py32_surface() -> None:
    result = _run_cli(_PRERELEASE_VERSION, ("fw", "--help"))
    assert "py32f071" in result.output
    assert "--usb-id" in result.output
    assert "--dfu-probe" in result.output


def test_simulated_prerelease_dfu_probe_and_usb_id_not_refused() -> None:
    """The positive half of HOST-02. Deliberately asserts nothing about the
    exit code: with pyusb absent the path raises through `PyusbMissingError`,
    and with pyusb present (the `ci-py32` leg) it enumerates the bus and
    finds nothing attached. Both are correct outcomes for a bench-less CI
    run; only the absence of the refusal message is this test's claim, and
    that is deterministic in both legs.
    """
    result = _run_cli(
        _PRERELEASE_VERSION, ("fw", "--dfu-probe", "--usb-id", "1a86:8012")
    )
    assert "no such option" not in result.output
