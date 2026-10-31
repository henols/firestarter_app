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

This module shares the fresh-process harness `tests/fresh_cli.py` with
`tests/test_py32_channel_gating.py`.

**Why not a module-reload approach.** Identical reasoning to
`test_py32_channel_gating.py`'s own docstring: re-executing `cli_handlers`'s
module body in place rebuilds Click command objects from scratch, but
`cli.py`'s `@cli.group()` / `add_command()` wiring still holds references to
the *old* objects. Only a fresh process makes the import-time computation a
structural fact.

**Determinism.** Every assertion below holds identically whether or not
`pyusb` is importable in this interpreter -- none of this module's assertions
reach the network or an attached device. No skip marker of any kind.
"""

from __future__ import annotations

from .fresh_cli import run_cli_fresh

# Every version string used by this module's tests is a literal below, not
# read from `firestarter.__version__` -- the point is to simulate a channel,
# not observe whichever one this checkout currently is. Mirrors
# `test_py32_channel_gating.py`'s own two constants exactly.
_STABLE_VERSION = "3.0.0"
_PRERELEASE_VERSION = "3.0.0b1"

_GATED_NAMES = frozenset(
    {
        "reg",
        "addr",
        "consistency-check",
        "write-cycle",
        "fault-inject",
        "validate-family",
        "lock-status",
        "adc",
    }
)
_STABLE_NAMES = frozenset({"read", "test"})
_ALL_GATED_PLUS_STABLE_NAMES = _GATED_NAMES | _STABLE_NAMES

# Simulated stable (`__version__ = "3.0.0"`, no env override)


def test_simulated_stable_help_lists_only_read_and_test() -> None:
    """CHAN-01: `dev --help` on a simulated-stable build lists only `read`
    and `test`, never the gated names."""
    result = run_cli_fresh(_STABLE_VERSION, "dev", "--help")
    assert "read" in result.output
    assert "test" in result.output
    for gated in _GATED_NAMES:
        assert gated not in result.output, (
            f"{gated!r} appeared in simulated-stable dev --help output, "
            f"which CHAN-01 says must list only read/test"
        )


def test_simulated_stable_dev_commands_is_exactly_read_and_test() -> None:
    """CHAN-02: the exact command set that the `dev` group registers (hidden
    commands included) and that `dev --help` lists, not just 'excludes the
    gated set'."""
    result = run_cli_fresh(_STABLE_VERSION, "dev", "--help")
    assert result.registered == result.listed == _STABLE_NAMES


def test_simulated_stable_gated_command_refuses_with_channel_message() -> None:
    """CHAN-03: invoking a gated name on simulated-stable refuses at a
    non-zero exit, with the channel-specific message identifying the cause
    (not merely a non-zero exit code)."""
    result = run_cli_fresh(_STABLE_VERSION, "dev", "reg", "0", "0", "0x86")
    assert result.exit_code != 0
    assert "dev reg" in result.output
    assert "pre-release" in result.output
    assert "FIRESTARTER_DEV_TOOLS" in result.output


def test_simulated_stable_genuine_typo_gets_clicks_generic_message() -> None:
    """The typo control: a name that never existed gets Click's own generic
    'No such command' message, and this module's channel-specific refusal
    text is ABSENT -- proving the override does not swallow real typos."""
    result = run_cli_fresh(_STABLE_VERSION, "dev", "totally-bogus-name")
    assert result.exit_code != 0
    assert "No such command" in result.output
    assert "pre-release" not in result.output
    assert "FIRESTARTER_DEV_TOOLS" not in result.output


# Simulated prerelease (`__version__ = "3.0.0b1"`, no env override) --
# the positive control without which the stable-side assertions above would
# be unfalsifiable.


def test_simulated_prerelease_help_lists_every_name() -> None:
    result = run_cli_fresh(_PRERELEASE_VERSION, "dev", "--help")
    for name in _ALL_GATED_PLUS_STABLE_NAMES:
        assert name in result.output, (
            f"{name!r} missing from simulated-prerelease dev --help output"
        )


def test_simulated_prerelease_dev_commands_is_every_name() -> None:
    result = run_cli_fresh(_PRERELEASE_VERSION, "dev", "--help")
    assert result.registered == result.listed == _ALL_GATED_PLUS_STABLE_NAMES


def test_dev_help_differs_between_channels_and_is_pinned_each_way() -> None:
    """CHAN-04's pin, both directions in one assertion body -- mirroring
    `test_py32_channel_gating.py`'s
    `test_board_choices_are_computed_at_import_not_cached_across_a_version_change`.

    Proved *by construction*, not by inspecting `cli_handlers.py`'s source:
    one process per simulated version, `firestarter.cli_handlers` imported
    exactly once in each, after `firestarter.__version__` is set, guarded by
    that child's own `sys.modules` pre-assertion (see `tests/fresh_cli.py`). The
    two children's listed commands are therefore not the same list
    re-read twice -- they are two independent import-time computations from
    two independent processes, one per simulated channel.
    """
    stable = run_cli_fresh(_STABLE_VERSION, "dev", "--help")
    prerelease = run_cli_fresh(_PRERELEASE_VERSION, "dev", "--help")

    assert stable.output != prerelease.output
    assert stable.registered == stable.listed == _STABLE_NAMES
    assert prerelease.registered == prerelease.listed == _ALL_GATED_PLUS_STABLE_NAMES

    # Pin each channel's own output independently -- both directions.
    for gated in _GATED_NAMES:
        assert gated not in stable.output
        assert gated in prerelease.output
    assert "read" in stable.output and "read" in prerelease.output
    assert "test" in stable.output and "test" in prerelease.output


def test_simulated_stable_with_env_override_registers_every_gated_name() -> None:
    """On simulated-stable WITH FIRESTARTER_DEV_TOOLS=1 set in the child's
    environment before import, every gated name IS registered -- the
    bench override overrides the channel signal."""
    result = run_cli_fresh(
        _STABLE_VERSION, "dev", "--help", env={"FIRESTARTER_DEV_TOOLS": "1"}
    )
    assert result.registered == result.listed == _ALL_GATED_PLUS_STABLE_NAMES
    for gated in _GATED_NAMES:
        assert gated in result.output


def test_simulated_stable_env_override_lets_gated_command_actually_run() -> None:
    """Not just registered -- genuinely invokable. `dev reg` with the
    override set must NOT hit the channel refusal path (it may still fail
    for an unrelated hardware reason -- no board is attached in CI -- but it
    must not fail with the channel-gate message)."""
    result = run_cli_fresh(
        _STABLE_VERSION, "dev", "reg", "--help", env={"FIRESTARTER_DEV_TOOLS": "1"}
    )
    assert result.exit_code == 0
    assert "FIRESTARTER_DEV_TOOLS" not in result.output


def test_simulated_stable_dev_tools_env_override_absent_by_default() -> None:
    """Confirms the "no override" tests above are not accidentally inheriting
    an ambient FIRESTARTER_DEV_TOOLS from this test session's own shell --
    `run_cli_fresh` strips it before every call, override or not."""
    result = run_cli_fresh(_STABLE_VERSION, "dev", "--help")
    assert result.registered == result.listed == _STABLE_NAMES
