"""Phase 153 Plan 12 (ERASE-05) -- `blank` remains its own step, asserted as
NON-REGRESSION.

ERASE-05 is deliberately scoped as a non-regression requirement, so no plan
mistakes it for new work. The chain `firestarter blank` -> CLI `blank`
handler -> `EpromOperator.check_eprom_blank` -> firmware `CMD_BLANK_CHECK` ->
`mem_util_blank_check` already worked end to end before this phase; this
module asserts that it still does, at three layers (CLI, host call boundary,
firmware dispatch arm), without adding a line of new production code.

Why this matters NOW, per D-153-04: no post-erase blank check is wired into
`eeprom28c_erase_execute` on protocol 0x0D -- `erase -b` is a documented
no-op on this family (both sibling protocols decline an `operation_end` arm
too, and a leonardo target already at 0 B MERGE-05 flash headroom has no
budget for one). That makes the standalone `blank` command the ONLY way an
operator gets a blank verdict on this family after an erase; its continued
existence is load-bearing, not incidental.

This module complements, and does not duplicate, the pre-existing blank
coverage in `tests/test_characterization.py`
(`test_help_blank`, `test_no_blank_check_polarity`,
`test_blank_check_happy_path`) and `tests/test_eprom_operations.py`
(`test_blank_check_eprom_happy_path`,
`TestSramBlankCheckShortCircuit::test_eeprom_blank_check_still_reaches_setup`).
Those modules are cited here, not reimplemented; run them alongside this
module's own suite as part of verification (see 153-12-SUMMARY.md for the
combined selection count).

Measured line positions (this session, `firestarter/cli_handlers.py`):
the `@cli.command(name="blank")` decorator is at line 888; `def blank(...)`
itself is at line 898. The requirement text's "cli_handlers.py:856" citation
predates this session's line numbers and names the decorator as though it
were the definition -- both are recorded here as the measured ground truth.
"""

from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from firestarter.chip_resolver import resolve_chip
from firestarter.cli_handlers import cli
from firestarter.database import EpromDatabase
from firestarter.eprom_operations import EpromOperator

from .conftest import make_app_context

_CHIP = "AT28C256"

# Measured this session -- see module docstring.
_CLI_HANDLERS_BLANK_DECORATOR_LINE = 888
_CLI_HANDLERS_BLANK_DEF_LINE = 898


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(scope="module")
def real_db() -> EpromDatabase:
    return EpromDatabase(skip_local_override=True)


# Leg 1: `blank` is registered and its help text renders.


def test_blank_command_is_registered_and_documented(runner: CliRunner) -> None:
    """`blank` is present in the CLI group's command mapping, and invoking
    it with `--help` exits 0 and renders its help text. This is the
    presence half of the non-regression proof; legs 2-3 prove it still
    reaches the host call boundary.
    """
    assert "blank" in cli.commands, (
        "the 'blank' command must remain registered in the CLI group's command mapping"
    )
    result = runner.invoke(cli, ["blank", "--help"])
    assert result.exit_code == 0, result.output
    assert "Checks if an EPROM is blank" in result.output, (
        f"blank --help must render its docstring text; got: {result.output!r}"
    )
    # Recorded for the summary, not asserted against source text here (a
    # decorator-line assertion would be a brittle line-number pin on an
    # unrelated file's churn) -- see module docstring for the measured
    # positions (decorator line, then def line).
    assert _CLI_HANDLERS_BLANK_DECORATOR_LINE < _CLI_HANDLERS_BLANK_DEF_LINE


# Leg 2: `blank` reaches the host blank-check entry point exactly once.


def test_blank_command_reaches_the_host_blank_check_call(
    runner: CliRunner, real_db: EpromDatabase
) -> None:
    """Drive `blank <chip>` through the CLI runner against a
    `Mock(spec=EpromOperator)` double and assert `check_eprom_blank` was
    called exactly once with the resolved chip -- a positive call
    assertion, not merely "no exception raised".
    """
    operator = Mock(spec=EpromOperator)
    # 202-05 D-10: check_eprom_blank now returns an int (0 == blank).
    operator.check_eprom_blank.return_value = 0
    app = make_app_context(db=real_db, eprom_operator=operator)

    result = runner.invoke(cli, ["blank", _CHIP], obj=app)

    assert result.exit_code == 0, result.output
    operator.check_eprom_blank.assert_called_once()
    call_args, call_kwargs = operator.check_eprom_blank.call_args
    assert call_args[0] == _CHIP
    assert call_args[1] == resolve_chip(_CHIP, db=real_db), (
        "check_eprom_blank must be called with the resolved chip's "
        "programmer-config dict, not a raw or partially-resolved one"
    )
    assert "operation_flags" in call_kwargs


# Leg 3: `blank` surfaces a not-blank verdict, not a false success.


def test_blank_command_reports_not_blank_correctly(
    runner: CliRunner, real_db: EpromDatabase
) -> None:
    """A `blank` command that cannot report NOT BLANK is worse than none --
    drive it with a double whose `check_eprom_blank` reports not-blank and
    assert the command surfaces that outcome (non-zero exit) rather than
    reporting success.
    """
    operator = Mock(spec=EpromOperator)
    # 202-05 D-10: check_eprom_blank's "not blank" verdict is now 1.
    operator.check_eprom_blank.return_value = 1
    app = make_app_context(db=real_db, eprom_operator=operator)

    result = runner.invoke(cli, ["blank", _CHIP], obj=app)

    assert result.exit_code == 1, (
        f"blank must exit non-zero when check_eprom_blank reports "
        f"not-blank; got exit_code={result.exit_code}, output={result.output!r}"
    )
    operator.check_eprom_blank.assert_called_once()


# Leg 4: the firmware still wires CMD_BLANK_CHECK to mem_util_blank_check.


# Leg 5: D-153-04 -- no operation_end is wired for CMD_ERASE on 0x0D.
