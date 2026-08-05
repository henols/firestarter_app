"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 136 Plan 136-01 Task 3 -- `channel.py`'s dev-tools bench-override
vocabulary (CHAN-06 mechanism) and a file-scoped no-firmware-read proof
(CHAN-07 partial; the comprehensive cross-file proof is plan 136-03's).

**D-03's asymmetry, restated as the thing this module actually measures.**
The firmware-side analogue `-D DEV_TOOLS=${sysenv.VAR}` is on record as
fail-**OPEN**: an unset variable still *defines* the macro, so every
`#ifdef DEV_TOOLS` stays true regardless of what the variable was set to, or
whether it was set at all. `dev_tools_enabled_by_env()` must be the opposite
shape: presence is never enough, and the only value that enables anything is
the exact string `"1"`. Every other string -- including ones a naive
`bool(...)` coercion would treat as truthy, like `"0"` and `"false"` -- must
read `False`. This module's parametrized fail-closed matrix exists
specifically to catch a regression back to that `bool(...)` shape; see this
plan's SUMMARY.md for the verbatim RED output captured when that exact
mutation was planted and observed (the non-vacuity obligation).

`monkeypatch.delenv(..., raising=False)` is used before every "unset" case in
case the ambient shell already has `FIRESTARTER_DEV_TOOLS` set.
"""

from __future__ import annotations

import inspect

import pytest

from firestarter import channel

# ---------------------------------------------------------------------------
# dev_tools_enabled_by_env() -- fail-closed matrix
# ---------------------------------------------------------------------------

# `None` is the sentinel for "leave the variable unset" (monkeypatch.delenv),
# distinct from the empty string `""` (monkeypatch.setenv to an empty value)
# -- both must read False, but they exercise different code paths
# (`os.environ.get` returning `None` vs. returning `""`).
_FAIL_CLOSED_VALUES: list[str | None] = [
    None,  # unset
    "",  # empty string
    "0",  # bool("0") is True in Python -- the exact trap D-03 warns against
    "false",  # bool("false") is also True -- same trap, different spelling
    "False",
    " 1 ",  # whitespace-padded -- must NOT be treated as equivalent to "1"
    "1 ",
    " 1",
    "garbage",
    "yes",
    "TRUE",
    "true",
    "10",
    "1.0",
]


@pytest.mark.parametrize("raw_value", _FAIL_CLOSED_VALUES)
def test_dev_tools_enabled_by_env_fails_closed(
    monkeypatch: pytest.MonkeyPatch, raw_value: str | None
) -> None:
    """Every value except the exact literal "1" must read False."""
    if raw_value is None:
        monkeypatch.delenv("FIRESTARTER_DEV_TOOLS", raising=False)
    else:
        monkeypatch.setenv("FIRESTARTER_DEV_TOOLS", raw_value)

    assert channel.dev_tools_enabled_by_env() is False


def test_dev_tools_enabled_by_env_true_only_for_exact_literal_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one and only enabling value."""
    monkeypatch.setenv("FIRESTARTER_DEV_TOOLS", "1")

    assert channel.dev_tools_enabled_by_env() is True


def test_dev_tools_enabled_by_env_reads_at_call_time_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two calls in the same process see a value change -- not import-time-frozen."""
    monkeypatch.delenv("FIRESTARTER_DEV_TOOLS", raising=False)
    assert channel.dev_tools_enabled_by_env() is False

    monkeypatch.setenv("FIRESTARTER_DEV_TOOLS", "1")
    assert channel.dev_tools_enabled_by_env() is True


# ---------------------------------------------------------------------------
# is_dev_tools_enabled() -- truth table over (is_prerelease_build, env var)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prerelease", "env_value", "expected"),
    [
        (True, None, True),  # prerelease alone enables it
        (True, "0", True),  # prerelease still wins even if env is a fail-closed value
        (True, "1", True),  # both true
        (False, "1", True),  # env override alone enables it on a simulated-stable build
        (False, None, False),  # neither -- the only False case
        (False, "0", False),  # env fail-closed value does not flip it
        (False, "garbage", False),
    ],
)
def test_is_dev_tools_enabled_truth_table(
    monkeypatch: pytest.MonkeyPatch,
    prerelease: bool,
    env_value: str | None,
    expected: bool,
) -> None:
    monkeypatch.setattr(channel, "is_prerelease_build", lambda: prerelease)
    if env_value is None:
        monkeypatch.delenv("FIRESTARTER_DEV_TOOLS", raising=False)
    else:
        monkeypatch.setenv("FIRESTARTER_DEV_TOOLS", env_value)

    assert channel.is_dev_tools_enabled() is expected


# ---------------------------------------------------------------------------
# dev_command_gate_message()
# ---------------------------------------------------------------------------


def test_dev_command_gate_message_names_the_command_and_the_pip_instruction() -> None:
    message = channel.dev_command_gate_message("reg")

    assert "reg" in message
    assert "pip install --pre --upgrade firestarter" in message


def test_dev_command_gate_message_varies_by_name() -> None:
    assert "addr" in channel.dev_command_gate_message("addr")
    assert "reg" not in channel.dev_command_gate_message("addr")


# ---------------------------------------------------------------------------
# BETA_ONLY_DEV_COMMANDS -- the measured baseline order (136-CONTEXT.md)
# ---------------------------------------------------------------------------


def test_beta_only_dev_commands_matches_measured_baseline() -> None:
    assert channel.BETA_ONLY_DEV_COMMANDS == (
        "reg",
        "addr",
        "consistency-check",
        "write-cycle",
        "fault-inject",
        "validate-family",
    )


# ---------------------------------------------------------------------------
# CHAN-07 (file-scoped): channel.py itself calls no open() anywhere.
# ---------------------------------------------------------------------------


def test_channel_module_source_contains_no_open_call() -> None:
    """A partial, file-scoped CHAN-07 proof; plan 136-03 covers cli_handlers.py too."""
    source = inspect.getsource(channel)

    assert "open(" not in source
