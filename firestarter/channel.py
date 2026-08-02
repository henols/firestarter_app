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

Nothing here reads the environment. A channel gate that can be flipped by an
env var is not a gate — the firmware side already learned that
`-D X=${sysenv.VAR}` fails OPEN and quietly ships the gated thing. To exercise
the stable behaviour, monkeypatch `firestarter.__version__` (unit tests) or
install the stable wheel.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("Channel")

# Boards whose firmware-install path is beta-only. PY32F071 is here because the
# port has never run on real silicon: no PCB exists, its pin map is a documented
# placeholder, and the USB DFU dialect its bootloader speaks is unconfirmed.
# Shipping an install path for it on stable would offer users a flash operation
# nobody has ever completed. It graduates by deletion from this tuple, once the
# target is bench-validated.
BETA_ONLY_BOARDS: tuple[str, ...] = ("py32f071",)


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
