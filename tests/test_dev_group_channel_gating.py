"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 136 Plan 136-03 Task 1 -- the subprocess dual-channel proof for
CHAN-01, CHAN-02, CHAN-03, CHAN-04, CHAN-06.

**Why a subprocess (D-04, restated).** `channel.is_prerelease_build()` reads
the imported package's own `firestarter.__version__` at call time. In this
checkout that string is `3.0.0b15`, which parses as a pre-release, so *any*
in-process assertion that "stable hides `reg`" can only ever observe the
beta branch -- it is vacuous by construction (136-RESEARCH.md §2, measured,
not theorised). Worse, `cli_handlers.py`'s `_DEV_TOOLS_ENABLED` is computed
ONCE, at import time, from `channel.is_dev_tools_enabled()` -- mirroring
`_PY32_ENABLED`/`_BOARD_CHOICES` in the same file (see
`tests/test_py32_channel_gating.py`'s own module docstring for the identical
reasoning about those two globals). A monkeypatch of `firestarter.__version__`
performed after `firestarter.cli_handlers` has already been imported in THIS
test process would leave `_DEV_TOOLS_ENABLED` frozen at whatever channel was
live the first time this module (or any other test module) imported
`cli_handlers` -- a test that would pass, but for the wrong reason.

This module is a direct structural adaptation of
`tests/test_py32_channel_gating.py`'s `_CHILD_PROGRAM` / `_run_cli` shape,
for the `dev` group instead of `_BOARD_CHOICES`/`_PY32_ENABLED`.

**Why not a module-reload approach.** Identical reasoning to
`test_py32_channel_gating.py`'s own docstring: re-executing `cli_handlers`'s
module body in place rebuilds Click command objects from scratch, but
`cli.py`'s `@cli.group()` / `add_command()` wiring still holds references to
the *old* objects. Only a fresh process makes the import-time computation a
structural fact.

**Determinism.** Every assertion below holds identically whether or not
`pyusb` is importable in this interpreter -- none of this module's assertions
reach the network or an attached device. No skip marker of any kind; no
`ALLOWED_SKIP_REASONS` entry needed (`tests/test_skip_census.py` covers that).
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
# not observe whichever one this checkout currently is. Mirrors
# `test_py32_channel_gating.py`'s own two constants exactly.
_STABLE_VERSION = "3.0.0"
_PRERELEASE_VERSION = "3.0.0b1"

# The six gated dev subcommands + the two that stay on every channel --
# literals here, not imported from channel.py, so this test does not become
# trivially self-confirming against the very module it is proving.
_GATED_NAMES = frozenset(
    {
        "reg",
        "addr",
        "consistency-check",
        "write-cycle",
        "fault-inject",
        "validate-family",
    }
)
_STABLE_NAMES = frozenset({"read", "test"})
_ALL_EIGHT_NAMES = _GATED_NAMES | _STABLE_NAMES

# The child program, run via `python -c`. Order is load-bearing (see module
# docstring): `firestarter` is imported bare first; the preamble then asserts
# `firestarter.cli_handlers` is not yet in `sys.modules`; ONLY THEN is
# `__version__` overwritten; ONLY THEN is `cli_handlers` imported for the
# first time in this process -- so `_DEV_TOOLS_ENABLED` is computed against
# the simulated version (and the simulated env override, if any) by
# construction, never patched after the fact.
_CHILD_PROGRAM = """
import json
import sys

import firestarter

assert "firestarter.cli_handlers" not in sys.modules, (
    "firestarter.cli_handlers was already imported before the simulated "
    "version was assigned -- _DEV_TOOLS_ENABLED would be frozen against "
    "the wrong channel"
)
firestarter.__version__ = {version!r}

from click.testing import CliRunner
from firestarter import cli_handlers

runner = CliRunner()
result = runner.invoke(cli_handlers.cli, {argv!r})
print(json.dumps({{
    "version": firestarter.__version__,
    "dev_tools_enabled": cli_handlers._DEV_TOOLS_ENABLED,
    "dev_commands": sorted(cli_handlers.dev.commands.keys()),
    "exit_code": result.exit_code,
    "output": result.output,
}}))
"""


@dataclass(frozen=True)
class _ChildResult:
    """One child process's parsed report -- see `_CHILD_PROGRAM`."""

    version: str
    dev_tools_enabled: bool
    dev_commands: list[str]
    exit_code: int
    output: str


def _env_key(env_overrides: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    """A hashable representation of `env_overrides` for the lru_cache key --
    `dict` is unhashable, so this sorts its items into a tuple."""
    if not env_overrides:
        return ()
    return tuple(sorted(env_overrides.items()))


@functools.lru_cache(maxsize=None)  # noqa: UP033 -- lru_cache named explicitly by plan
def _run_cli_cached(
    version: str, argv: tuple[str, ...], env_key: tuple[tuple[str, str], ...]
) -> _ChildResult:
    """Cache key is `(version, argv, env_key)` -- the actual subprocess call,
    see `_run_cli` below, which reconstructs the env mapping from `env_key`
    before calling this."""
    env_overrides = dict(env_key)
    program = _CHILD_PROGRAM.format(version=version, argv=list(argv))

    # Build a clean copy of the ambient environment for every call -- even
    # the "no override" case. The ambient shell that runs this test suite
    # could ALREADY carry FIRESTARTER_DEV_TOOLS (a developer's own shell, a
    # CI runner's env, etc.) -- a stable-no-override test must not silently
    # inherit that. `env_overrides` (when given) is merged on top of a
    # `FIRESTARTER_DEV_TOOLS`-stripped copy of `os.environ`, so a test can
    # explicitly opt IN to setting it, but never accidentally opts in via an
    # ambient leak.
    import os

    child_env = {k: v for k, v in os.environ.items() if k != "FIRESTARTER_DEV_TOOLS"}
    child_env.update(env_overrides)

    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=str(_APP_DIR),
        capture_output=True,
        text=True,
        timeout=120,
        env=child_env,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"child process for version={version!r} argv={argv!r} "
            f"env_overrides={env_overrides!r} exited {result.returncode} (a "
            f"non-zero return here means the child's own preamble assertion "
            f"failed, or it crashed) -- stderr:\n{result.stderr}"
        )
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["version"] == version, (
        f"child reported version {report['version']!r}, expected "
        f"{version!r} -- the simulated-version patch did not take effect "
        f"before firestarter.cli_handlers was imported"
    )
    return _ChildResult(
        version=report["version"],
        dev_tools_enabled=report["dev_tools_enabled"],
        dev_commands=list(report["dev_commands"]),
        exit_code=report["exit_code"],
        output=report["output"],
    )


def _run_cli(
    version: str,
    argv: tuple[str, ...],
    env_overrides: dict[str, str] | None = None,
) -> _ChildResult:
    """Run `firestarter <argv>` in a fresh process simulating `version`,
    optionally with `env_overrides` merged into the child's environment
    (see `_run_cli_cached`'s docstring for why even the no-override case
    builds a clean env copy). Cached per `(version, argv, env_overrides)`
    combination so the several assertions per simulated channel below pay
    the child-process cost exactly once each."""
    return _run_cli_cached(version, argv, _env_key(env_overrides))


# ---------------------------------------------------------------------------
# Simulated stable (`__version__ = "3.0.0"`, no env override)
# ---------------------------------------------------------------------------


def test_simulated_stable_help_lists_only_read_and_test() -> None:
    """CHAN-01: `dev --help` on a simulated-stable build lists only `read`
    and `test`, never the six gated names."""
    result = _run_cli(_STABLE_VERSION, ("dev", "--help"))
    assert "read" in result.output
    assert "test" in result.output
    for gated in _GATED_NAMES:
        assert gated not in result.output, (
            f"{gated!r} appeared in simulated-stable dev --help output, "
            f"which CHAN-01 says must list only read/test"
        )


def test_simulated_stable_dev_tools_enabled_is_false() -> None:
    result = _run_cli(_STABLE_VERSION, ("dev", "--help"))
    assert result.dev_tools_enabled is False


def test_simulated_stable_dev_commands_is_exactly_read_and_test() -> None:
    """CHAN-02, proven by direct registry introspection -- the stronger,
    exact-set assertion, not just 'excludes the six'."""
    result = _run_cli(_STABLE_VERSION, ("dev", "--help"))
    assert set(result.dev_commands) == _STABLE_NAMES


def test_simulated_stable_gated_command_refuses_with_channel_message() -> None:
    """CHAN-03: invoking a gated name on simulated-stable refuses at a
    non-zero exit, with the channel-specific message identifying the cause
    (not merely a non-zero exit code)."""
    result = _run_cli(_STABLE_VERSION, ("dev", "reg", "0", "0", "0x86"))
    assert result.exit_code != 0
    assert "dev reg" in result.output
    assert "pre-release" in result.output
    assert "FIRESTARTER_DEV_TOOLS" in result.output


def test_simulated_stable_genuine_typo_gets_clicks_generic_message() -> None:
    """The typo control: a name that never existed gets Click's own generic
    'No such command' message, and this module's channel-specific refusal
    text is ABSENT -- proving the override does not swallow real typos."""
    result = _run_cli(_STABLE_VERSION, ("dev", "totally-bogus-name"))
    assert result.exit_code != 0
    assert "No such command" in result.output
    assert "pre-release" not in result.output
    assert "FIRESTARTER_DEV_TOOLS" not in result.output


# ---------------------------------------------------------------------------
# Simulated prerelease (`__version__ = "3.0.0b1"`, no env override) --
# the positive control without which the stable-side assertions above would
# be unfalsifiable.
# ---------------------------------------------------------------------------


def test_simulated_prerelease_help_lists_all_eight() -> None:
    result = _run_cli(_PRERELEASE_VERSION, ("dev", "--help"))
    for name in _ALL_EIGHT_NAMES:
        assert name in result.output, (
            f"{name!r} missing from simulated-prerelease dev --help output"
        )


def test_simulated_prerelease_dev_tools_enabled_is_true() -> None:
    result = _run_cli(_PRERELEASE_VERSION, ("dev", "--help"))
    assert result.dev_tools_enabled is True


def test_simulated_prerelease_dev_commands_is_all_eight() -> None:
    result = _run_cli(_PRERELEASE_VERSION, ("dev", "--help"))
    assert set(result.dev_commands) == _ALL_EIGHT_NAMES


# ---------------------------------------------------------------------------
# CHAN-04: dev --help is PINNED on both channels, in the same test module.
# ---------------------------------------------------------------------------


def test_dev_help_differs_between_channels_and_is_pinned_each_way() -> None:
    """CHAN-04's pin, both directions in one assertion body -- mirroring
    `test_py32_channel_gating.py`'s
    `test_board_choices_are_computed_at_import_not_cached_across_a_version_change`.

    Proved *by construction*, not by inspecting `cli_handlers.py`'s source:
    one process per simulated version, `firestarter.cli_handlers` imported
    exactly once in each, after `firestarter.__version__` is set, guarded by
    that child's own `sys.modules` pre-assertion (see `_CHILD_PROGRAM`). The
    two children's reported `dev_commands` are therefore not the same list
    re-read twice -- they are two independent import-time computations from
    two independent processes, one per simulated channel.
    """
    stable = _run_cli(_STABLE_VERSION, ("dev", "--help"))
    prerelease = _run_cli(_PRERELEASE_VERSION, ("dev", "--help"))

    assert stable.output != prerelease.output
    assert set(stable.dev_commands) == _STABLE_NAMES
    assert set(prerelease.dev_commands) == _ALL_EIGHT_NAMES

    # Pin each channel's own output independently -- both directions.
    for gated in _GATED_NAMES:
        assert gated not in stable.output
        assert gated in prerelease.output
    assert "read" in stable.output and "read" in prerelease.output
    assert "test" in stable.output and "test" in prerelease.output


# ---------------------------------------------------------------------------
# CHAN-06: the FIRESTARTER_DEV_TOOLS override wins over the channel signal
# on a simulated-stable build.
# ---------------------------------------------------------------------------


def test_simulated_stable_with_env_override_registers_all_six_gated_names() -> None:
    """On simulated-stable WITH FIRESTARTER_DEV_TOOLS=1 set in the child's
    environment before import, all six gated names ARE registered -- the
    bench override overrides the channel signal."""
    result = _run_cli(
        _STABLE_VERSION,
        ("dev", "--help"),
        env_overrides={"FIRESTARTER_DEV_TOOLS": "1"},
    )
    assert result.dev_tools_enabled is True
    assert set(result.dev_commands) == _ALL_EIGHT_NAMES
    for gated in _GATED_NAMES:
        assert gated in result.output


def test_simulated_stable_env_override_lets_gated_command_actually_run() -> None:
    """Not just registered -- genuinely invokable. `dev reg` with the
    override set must NOT hit the channel refusal path (it may still fail
    for an unrelated hardware reason -- no board is attached in CI -- but it
    must not fail with the channel-gate message)."""
    result = _run_cli(
        _STABLE_VERSION,
        ("dev", "reg", "--help"),
        env_overrides={"FIRESTARTER_DEV_TOOLS": "1"},
    )
    assert result.exit_code == 0
    assert "FIRESTARTER_DEV_TOOLS" not in result.output


def test_simulated_stable_dev_tools_env_override_absent_by_default() -> None:
    """Confirms the "no override" tests above are not accidentally inheriting
    an ambient FIRESTARTER_DEV_TOOLS from this test session's own shell --
    `_run_cli_cached` strips it before every call, override or not."""
    result = _run_cli(_STABLE_VERSION, ("dev", "--help"))
    assert result.dev_tools_enabled is False
    assert set(result.dev_commands) == _STABLE_NAMES
