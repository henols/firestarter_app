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
from .fw_presence import fw_path, requires_fw

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


# ---------------------------------------------------------------------------
# Leg 1: `blank` is registered and its help text renders.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Leg 2: `blank` reaches the host blank-check entry point exactly once.
# ---------------------------------------------------------------------------


def test_blank_command_reaches_the_host_blank_check_call(
    runner: CliRunner, real_db: EpromDatabase
) -> None:
    """Drive `blank <chip>` through the CLI runner against a
    `Mock(spec=EpromOperator)` double and assert `check_eprom_blank` was
    called exactly once with the resolved chip -- a positive call
    assertion, not merely "no exception raised".
    """
    operator = Mock(spec=EpromOperator)
    operator.check_eprom_blank.return_value = True
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


# ---------------------------------------------------------------------------
# Leg 3: `blank` surfaces a not-blank verdict, not a false success.
# ---------------------------------------------------------------------------


def test_blank_command_reports_not_blank_correctly(
    runner: CliRunner, real_db: EpromDatabase
) -> None:
    """A `blank` command that cannot report NOT BLANK is worse than none --
    drive it with a double whose `check_eprom_blank` reports not-blank and
    assert the command surfaces that outcome (non-zero exit) rather than
    reporting success.
    """
    operator = Mock(spec=EpromOperator)
    operator.check_eprom_blank.return_value = False
    app = make_app_context(db=real_db, eprom_operator=operator)

    result = runner.invoke(cli, ["blank", _CHIP], obj=app)

    assert result.exit_code == 1, (
        f"blank must exit non-zero when check_eprom_blank reports "
        f"not-blank; got exit_code={result.exit_code}, output={result.output!r}"
    )
    operator.check_eprom_blank.assert_called_once()


# ---------------------------------------------------------------------------
# Leg 4: the firmware still wires CMD_BLANK_CHECK to mem_util_blank_check.
# ---------------------------------------------------------------------------

_EEPROM_28C_SOURCE = fw_path("src", "proms", "eeprom_28c.cpp")


def _read_eeprom_28c_source() -> str:
    """Read the firmware source text, failing closed: an absent path under
    a present repo is a MissingScanTargetError from `fw_path` itself (never
    a silent skip); this helper is only reached when `@requires_fw` has
    already confirmed the sibling checkout exists.
    """
    return _EEPROM_28C_SOURCE.read_text(encoding="utf-8")


@requires_fw
def test_firmware_still_wires_the_blank_check_arm() -> None:
    """Cross-repo source assertion: `configure_eeprom28c`'s `CMD_BLANK_CHECK`
    arm still maps to the shared `mem_util_blank_check` helper, and that
    helper name appears EXACTLY once in this file -- the count plan 02's
    criteria established. Skips cleanly (via `requires_fw`) when the
    sibling firmware checkout is absent, because the host package's own CI
    has no firmware checkout at all -- a hard failure there would be a
    false negative about this repo's own correctness, not a real gate hit.
    """
    text = _read_eeprom_28c_source()
    assert "case CMD_BLANK_CHECK:" in text, (
        f"expected a 'case CMD_BLANK_CHECK:' arm in {_EEPROM_28C_SOURCE}"
    )
    # Scope the mapping assertion to the CMD_BLANK_CHECK case block itself
    # (up to the next case label or the switch's closing brace), so this
    # leg cannot be satisfied by an unrelated mem_util_blank_check mention
    # elsewhere in the file.
    case_start = text.index("case CMD_BLANK_CHECK:")
    next_case = text.find("case CMD_", case_start + len("case CMD_BLANK_CHECK:"))
    case_block = text[case_start : next_case if next_case != -1 else len(text)]
    assert "mem_util_blank_check" in case_block, (
        f"CMD_BLANK_CHECK's case block in {_EEPROM_28C_SOURCE} must assign "
        f"mem_util_blank_check; block was: {case_block!r}"
    )
    total_occurrences = text.count("mem_util_blank_check")
    assert total_occurrences == 1, (
        f"expected exactly 1 occurrence of mem_util_blank_check in "
        f"{_EEPROM_28C_SOURCE} (plan 02's established count), found "
        f"{total_occurrences}"
    )


# ---------------------------------------------------------------------------
# Leg 5: D-153-04 -- no operation_end is wired for CMD_ERASE on 0x0D.
# ---------------------------------------------------------------------------


@requires_fw
def test_no_post_erase_blank_check_was_wired_on_0x0d() -> None:
    """D-153-04's disposition as a source assertion: the `CMD_ERASE` case
    block in `configure_eeprom28c` assigns no
    `firestarter_operation_end` function -- no post-erase blank check is
    wired on protocol 0x0D. Erase and blank stay independent, standalone
    steps, per ERASE-05's own requirement that `blank` not be folded into
    erase's completion. Skips cleanly (via `requires_fw`) for the same
    reason leg 4 does.
    """
    text = _read_eeprom_28c_source()
    assert "case CMD_ERASE:" in text, (
        f"expected a 'case CMD_ERASE:' arm in {_EEPROM_28C_SOURCE}"
    )
    case_start = text.index("case CMD_ERASE:")
    next_case = text.find("case CMD_", case_start + len("case CMD_ERASE:"))
    case_block = text[case_start : next_case if next_case != -1 else len(text)]
    assert "operation_end" not in case_block, (
        f"CMD_ERASE's case block in {_EEPROM_28C_SOURCE} must NOT assign "
        f"firestarter_operation_end (D-153-04: no post-erase blank check is "
        f"wired on 0x0D); block was: {case_block!r}"
    )
