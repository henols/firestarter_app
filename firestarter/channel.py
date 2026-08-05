"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Release-channel gate for features that ship on beta but not on stable.

The channel is derived from the app's own version: a PEP 440 pre-release
(`3.0.0b13`, `3.0.0rc1`, `2.0.7_dev`) means this build came off `beta`, and a
final release (`3.0.0`) means it came off `main`. That is the same predicate
`_maybe_auto_route_to_pre` already uses to decide a beta app should default to
`--pre` (D-23), so beta-gating adds no new notion of "what channel am I".

The channel gate itself (`is_prerelease_build`, `is_board_available`,
`available_boards`) reads nothing from the environment. A channel gate that
can be flipped by an env var is not a gate — the firmware side already
learned that `-D X=${sysenv.VAR}` fails OPEN and quietly ships the gated
thing, because an unset variable still *defines* the macro. To exercise the
stable behaviour of the channel gate itself, monkeypatch
`firestarter.__version__` (unit tests) or install the stable wheel.

`dev_tools_enabled_by_env` (Phase 136 / CHAN-06) is a deliberate, narrow
exception to that rule, not a contradiction of it: it is a single-purpose
bench override for `dev` subcommands that would otherwise vanish on a stable
build, and it is built to fail **closed** rather than open — presence of
`FIRESTARTER_DEV_TOOLS` is never enough; only the exact literal value `"1"`
enables anything. See that function's own docstring for the full reasoning.
`is_dev_tools_enabled` composes it with `is_prerelease_build`, so it is the
one function in this module whose result *can* be flipped by an env var, on
purpose, and only in the direction of exposing bench tooling — never in the
direction of hiding the channel gate's own decision.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("Channel")

# Boards whose firmware-install path is beta-only. PY32F071 is here because the
# port has never run on real silicon: no PCB exists, its pin map is a documented
# placeholder, and the USB DFU dialect its bootloader speaks is unconfirmed.
# Shipping an install path for it on stable would offer users a flash operation
# nobody has ever completed. It graduates by deletion from this tuple, once the
# target is bench-validated.
BETA_ONLY_BOARDS: tuple[str, ...] = ("py32f071",)

# The six `dev` subcommands gated by channel on a stable build (136-CONTEXT.md
# "Measured baseline": 8 total `dev` subcommands, minus `read`/`test` which
# stable keeps). Consulted for the informative-refusal message ONLY, by
# `_DevGroup.get_command` in `cli_handlers.py` (plan 136-02) — the actual gate
# is non-registration of the six `@dev.command` blocks, not membership in this
# tuple, so this list existing or not existing changes nothing about whether a
# command runs; it only changes whether its refusal is informative or Click's
# own generic "No such command".
BETA_ONLY_DEV_COMMANDS: tuple[str, ...] = (
    "reg",
    "addr",
    "consistency-check",
    "write-cycle",
    "fault-inject",
    "validate-family",
)


def is_prerelease_build() -> bool:
    """True when this build came off `beta` (its own version is a pre-release).

    Fails **closed**: an unparseable version is treated as stable, so a gated
    feature stays hidden rather than leaking. This matches
    `_maybe_auto_route_to_pre`, which also treats `InvalidVersion` as
    not-a-prerelease. In practice `__version__` is a literal in the package and
    is always valid PEP 440; dev versions (`2.0.7_dev`) parse as pre-releases and
    therefore keep gated features enabled while working from a checkout.
    """
    try:
        from packaging.version import InvalidVersion, Version

        import firestarter as _pkg

        try:
            return bool(Version(_pkg.__version__).is_prerelease)
        except InvalidVersion:
            return False
    except ImportError:  # pragma: no cover — packaging is a hard dependency
        return False


def is_board_available(board: str | None) -> bool:
    """False only for a beta-only board on a stable build."""
    if not board:
        return True
    if board.lower() not in BETA_ONLY_BOARDS:
        return True
    return is_prerelease_build()


def available_boards(boards: tuple[str, ...]) -> list[str]:
    """Filter a board list down to what this build exposes, order preserved."""
    return [board for board in boards if is_board_available(board)]


def beta_only_message(board: str) -> str:
    """The single explanation used by every refusal, so they read identically."""
    return (
        f"Firmware install for board {board!r} is available in pre-release builds "
        f"only. This is a stable build. The {board} target has not been validated "
        f"on hardware yet; install a pre-release to use it:\n"
        f"    pip install --pre --upgrade firestarter"
    )


def dev_tools_enabled_by_env() -> bool:
    """True only when `FIRESTARTER_DEV_TOOLS` is the exact literal string "1".

    This is the fail-**closed** bench override CHAN-06 requires (136-CONTEXT.md
    D-03). It exists because of a recorded near-miss: gating `dev reg` (the
    held-erase-rail DMM proxy — load-bearing bench tooling) off `__version__`
    alone means an editable devcontainer install silently loses it at any
    stable version cut, or between betas.

    Read `os.environ.get(...)` at CALL time — mirroring `get_config_dir`'s
    call-time-read style (`config.py`) rather than an import-time-cached
    constant — so a test (or a developer's shell) can flip it without
    reimporting this module.

    **Presence must never be enough; only the one exact value is.** The
    firmware-side analogue, `-D DEV_TOOLS=${sysenv.VAR}`, is on record as
    fail-**OPEN**: an unset variable still *defines* the macro, so every
    `#ifdef DEV_TOOLS` in that source stays true regardless of what the
    variable was set to. The obvious-looking Python translation of that same
    idea, `bool(os.environ.get("FIRESTARTER_DEV_TOOLS"))`, falls into the
    identical trap in a different language: `bool("0")` and `bool("false")`
    are both `True`, because a non-empty string is truthy no matter what it
    says. There is no `.strip()` and no case-folding here either, for the
    same reason — a whitespace-padded or differently-cased value must not be
    treated as equivalent to the one literal that enables anything.
    """
    return os.environ.get("FIRESTARTER_DEV_TOOLS") == "1"


def is_dev_tools_enabled() -> bool:
    """True when this build is pre-release, OR the bench override is set.

    `is_prerelease_build() or dev_tools_enabled_by_env()` — reusing the
    existing channel detector rather than writing a second one (D-02).
    `channel.py` has exactly one notion of "what channel am I"; a second,
    independently-derived detector is how two sources of truth drift apart.

    This function itself is call-time and unmemoized, like
    `dev_tools_enabled_by_env()`. A caller that needs an IMPORT-TIME-FROZEN
    decision — e.g. Click command registration, which must decide once,
    at import, which of the six gated `dev` subcommands to attach — must
    capture this into its own module global at import time, exactly as
    `_BOARD_CHOICES` / `_PY32_ENABLED` already do in `cli_handlers.py` for
    boards. Calling this function directly from inside a Click callback body
    would re-evaluate it on every invocation instead of once at import, which
    is not what CHAN-02's "not registered" requirement means.
    """
    return is_prerelease_build() or dev_tools_enabled_by_env()


def dev_command_gate_message(name: str) -> str:
    """The refusal text for a gated `dev` subcommand, mirroring `beta_only_message`."""
    return (
        f"The 'dev {name}' command is available in pre-release builds only. "
        f"This is a stable build. Install a pre-release to use it:\n"
        f"    pip install --pre --upgrade firestarter\n"
        f"Bench tooling that depends on 'dev {name}' outside a pre-release "
        f"install can instead set FIRESTARTER_DEV_TOOLS=1 in the environment."
    )
