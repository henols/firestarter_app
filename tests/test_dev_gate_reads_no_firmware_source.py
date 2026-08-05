"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 136 Plan 136-03 Task 2 -- CHAN-07, made comprehensively provable rather
than merely structurally true.

136-CONTEXT.md D-02 says CHAN-07 is satisfied *structurally*: `channel.py`
reuses `is_prerelease_build()`, which reads only the package's own
`__version__` -- no firmware source, no handshake, no serial. That is a true
claim about the design, but a true design claim with no assertion behind it
is exactly how Phase 117's four host gates "failed OPEN" -- they scanned
firmware source under the belief that doing so proved something, and their
own real defect was invisible to a reader trusting the design note alone.
This module inverts that risk for CHAN-07: it does not scan firmware source
(there is none to scan, by design) -- it asserts the ABSENCE of any file-read
capability in the gate's own new code, comprehensively, across both files
this phase touched (`channel.py` in full, plus `cli_handlers.py`'s new
`_DevGroup.get_command`).

**Why `inspect.getsource`, not a whole-file read of `cli_handlers.py`.**
`cli_handlers.py` is a large, pre-existing file with many unrelated command
handlers. Scanning the whole file for `open(` would trivially fail
vacuously-in-the-other-direction (false positive) the moment any unrelated
command handler in the same file legitimately opens a config file or a
report -- which several already do. Scoping the scan to exactly the five
callables this phase's gate introduced (`inspect.getsource` on each function
object) keeps the assertion meaningful and immune to unrelated churn
elsewhere in the same file.

**Non-vacuity.** See this plan's SUMMARY.md for the verbatim RED output
captured when `open("/dev/null")` was planted inside
`channel.is_dev_tools_enabled` and this module was re-run -- it failed,
naming `is_dev_tools_enabled` as the offending callable, before the plant was
reverted byte-identically.
"""

from __future__ import annotations

import inspect
from typing import Callable

import pytest

from firestarter import channel, cli_handlers

# Forbidden tokens naming a firmware path or file extension -- CHAN-07's own
# touch note says "channel.py reads the package's own __version__ and opens
# no file at all"; these are the shapes a firmware-source read would take if
# one were ever added.
_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "firestarter/include",
    '.h"',
    ".ino",
    "serial_comm",
    "frame_parser",
)

# The five callables this phase's gate introduced or extended. Referenced as
# objects (not by name/string) so a rename would break this test loudly at
# collection time rather than silently skip a callable.
_GATE_CALLABLES: tuple[tuple[str, Callable[..., object]], ...] = (
    ("channel.is_prerelease_build", channel.is_prerelease_build),
    ("channel.dev_tools_enabled_by_env", channel.dev_tools_enabled_by_env),
    ("channel.is_dev_tools_enabled", channel.is_dev_tools_enabled),
    ("channel.dev_command_gate_message", channel.dev_command_gate_message),
    ("cli_handlers._DevGroup.get_command", cli_handlers._DevGroup.get_command),
)


@pytest.mark.parametrize(
    ("label", "fn"), _GATE_CALLABLES, ids=[label for label, _ in _GATE_CALLABLES]
)
def test_gate_callable_source_contains_no_open_call(
    label: str, fn: Callable[..., object]
) -> None:
    """Parametrized so a failure names exactly which callable violated the
    property, rather than one monolithic assertion hiding which one."""
    source = inspect.getsource(fn)
    assert "open(" not in source, (
        f"{label}'s source contains an 'open(' call -- CHAN-07 requires the "
        f"gate's own new code to read no file at all"
    )


@pytest.mark.parametrize(
    ("label", "fn"), _GATE_CALLABLES, ids=[label for label, _ in _GATE_CALLABLES]
)
def test_gate_callable_source_contains_no_firmware_path_token(
    label: str, fn: Callable[..., object]
) -> None:
    source = inspect.getsource(fn)
    for token in _FORBIDDEN_TOKENS:
        assert token not in source, (
            f"{label}'s source contains the forbidden token {token!r} -- "
            f"CHAN-07 requires the gate to reference no firmware path or "
            f"firmware file extension"
        )


def test_channel_module_source_contains_no_open_call_anywhere() -> None:
    """The stronger, file-wide claim CHAN-07's own touch note asks for:
    'channel.py reads the package's own __version__ and opens no file at
    all' -- checked across the WHOLE file, not just the four callables
    parametrized above (channel.py also has module-level constants and a
    docstring that should carry no open() either)."""
    source = inspect.getsource(channel)
    assert "open(" not in source
