"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 136 Plan 136-01 Task 2 -- the empirical spike that settles which Click
hook carries the informative dev-tools refusal (136-RESEARCH.md §1,
136-CONTEXT.md D-01).

**This module tests a fact about Click itself, not about this project's own
gate.** `firestarter.cli_handlers` is never imported here. The real gate
(`_DevGroup` in `cli_handlers.py`, plan 136-02) is proven end to end via a
subprocess in plan 136-03 (per D-04: an in-process channel test is vacuous
because `is_prerelease_build()` reads this checkout's own pre-release
`__version__`). This spike needs no subprocess because it is not observing a
channel decision -- it is measuring which of Click's own dispatch hooks fires
first, which is a property of the installed `click` package, not of this
app's version.

**Finding, measured live against this run's own installed Click (see the
`_CLICK_VERSION` capture below via `importlib.metadata.version("click")` --
not the deprecated `click.__version__` -- so a future reader can confirm
which version this was measured against): `get_command` is the hook, not
`resolve_command`.**

`click.Group.resolve_command()` (the method Click's own `MultiCommand.invoke`
/ `main()` call to turn a command-line token into a `Command` object) calls
`self.get_command(ctx, cmd_name)` itself, and only falls through to its own
generic ``Error: No such command %r.`` `UsageError` when `get_command`
returns `None`. So overriding `get_command` to raise a *different*
`UsageError` for a known-but-unregistered name intercepts strictly before
`resolve_command`'s own fallback ever executes -- `resolve_command` itself
needs no override at all. This is exactly the mechanism plan 136-02's
`_DevGroup` implements for the seven gated `dev` subcommands.

**Use `click.Group`, never `click.MultiCommand`.** `click.MultiCommand` is
still reachable as a deprecated alias (`click.core._MultiCommand`) but is
absent from `dir(click)` and is slated for removal in Click 9.0 -- confirmed
live against both Click 8.3.3 (this devcontainer's ambient interpreter) and
Click 8.4.2 (the `.venv/ci-replica` venv this test module actually runs
under). Subclassing `click.Group` avoids shipping a construct with a known
removal date.
"""

from __future__ import annotations

import importlib.metadata

import click
import pytest
from click.testing import CliRunner

# Captured once, at import time, purely so a future reader can see which
# Click version this module's finding was measured against -- never asserted
# to equal a specific value, because the mechanism this module pins
# (`get_command` firing before `resolve_command`'s own fallback) is not
# expected to be version-specific within the 8.x series.
_CLICK_VERSION = importlib.metadata.version("click")

_GATED_NAME = "reg"
_REFUSAL_TEXT = "reg is gated by this spike -- 136-01 Task 2 fixed message"


class _SpikeGatedGroup(click.Group):
    """Throwaway group proving `get_command` intercepts before Click's own fallback.

    `_GATED` names a command that is deliberately never registered as a real
    `click.Command` on this group -- the point is to prove the refusal fires
    for a *gated-but-absent* name, exactly the shape `_DevGroup` (plan 136-02)
    needs for the seven gated `dev` subcommands: genuinely absent from
    `self.commands` (satisfies non-registration), yet refused informatively
    rather than with Click's generic typo message (satisfies the informative
    refusal).
    """

    _GATED: frozenset[str] = frozenset({_GATED_NAME})

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        real = super().get_command(ctx, cmd_name)
        if real is not None:
            return real
        if cmd_name in self._GATED:
            raise click.UsageError(_REFUSAL_TEXT, ctx=ctx)
        return None


def _make_spike_group() -> _SpikeGatedGroup:
    group = _SpikeGatedGroup(name="spike")

    @group.command(name="read")
    def _read() -> None:
        click.echo("read ran")

    return group


def test_gated_unregistered_name_gets_informative_refusal_not_generic_click_error() -> (
    None
):
    """`get_command` intercepts a gated name BEFORE Click's generic fallback fires."""
    runner = CliRunner()
    result = runner.invoke(_make_spike_group(), [_GATED_NAME])

    assert result.exit_code != 0
    assert _REFUSAL_TEXT in result.output
    assert "No such command" not in result.output


def test_genuine_typo_still_gets_clicks_own_generic_error_not_swallowed() -> None:
    """A name NOT in the gated set must still produce Click's own generic error."""
    runner = CliRunner()
    result = runner.invoke(_make_spike_group(), ["bogus"])

    assert result.exit_code != 0
    assert "No such command" in result.output
    assert _REFUSAL_TEXT not in result.output


def test_real_registered_command_runs_normally() -> None:
    """The one real command on the spike group is unaffected by the override."""
    runner = CliRunner()
    result = runner.invoke(_make_spike_group(), ["read"])

    assert result.exit_code == 0
    assert "read ran" in result.output


def test_resolve_command_was_never_overridden() -> None:
    """Structural proof: only `get_command` needed overriding for this mechanism."""
    assert "resolve_command" not in vars(_SpikeGatedGroup)
    assert "get_command" in vars(_SpikeGatedGroup)


def test_list_commands_needs_no_override_gated_name_is_simply_absent() -> None:
    """A gated-but-unregistered name is absent from `list_commands` by construction."""
    group = _make_spike_group()
    ctx = click.Context(group)

    assert group.list_commands(ctx) == ["read"]
    assert _GATED_NAME not in group.list_commands(ctx)
    # The gated name IS still a known name to the class, in its own frozenset
    # -- it is simply never registered as a `click.Command`, which is what
    # keeps it out of `list_commands` without any override of that method.
    assert _GATED_NAME in _SpikeGatedGroup._GATED


def test_click_version_captured_for_a_future_reader() -> None:
    """Sanity: the version-capture mechanism itself resolves to a real string."""
    assert _CLICK_VERSION
    assert _CLICK_VERSION[0].isdigit()


@pytest.mark.parametrize("attr_name", ["MultiCommand"])
def test_multicommand_is_deprecated_alias_not_in_dir_but_still_reachable(
    attr_name: str,
) -> None:
    """`click.MultiCommand` is a removal-pending alias -- confirms why this
    spike subclasses `click.Group` instead (136-RESEARCH.md §1)."""
    assert attr_name not in dir(click)
    assert getattr(click, attr_name) is not None
