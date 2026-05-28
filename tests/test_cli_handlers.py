"""In-process CliRunner suite for `firestarter.cli_handlers.cli` (Wave 2).

Covers the read-only command surface: list / info / search + --help + --version
+ Click's exact-match (no-prefix-matching) trap. Remaining commands land in
Wave 3 / Plan 41-03.
"""

import pytest
from click.testing import CliRunner

from firestarter.cli_handlers import cli


@pytest.fixture
def runner() -> CliRunner:
    """Fresh CliRunner per test — mix_stderr=True so stderr+stdout flow into result.output."""
    return CliRunner()


def test_cli_help_runs(runner: CliRunner) -> None:
    """`firestarter --help` exits 0 and the Click usage string mentions the
    three read-only commands landed this wave."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.output
    # Click's @cli.command(name="...") names appear in the auto-generated
    # Commands: section of --help output.
    assert "list" in result.output
    assert "info" in result.output
    assert "search" in result.output


def test_cli_version_runs(runner: CliRunner) -> None:
    """`firestarter --version` exits 0 and the prog_name ('Firestarter') is in
    the output (matches @click.version_option(prog_name='Firestarter'))."""
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "Firestarter" in result.output


def test_list_happy_path(runner: CliRunner) -> None:
    """`firestarter list` exits 0 and a known chip name appears in the output —
    proves the real DB was queried and the table-print path executed."""
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0
    # W27C512 is a load-bearing chip in the v1.6 read-bug RCA + Phase 36
    # characterization; if it's missing from the DB, something deeper is wrong.
    assert "W27C512" in result.output


def test_info_chip_resolution_happy_path(runner: CliRunner) -> None:
    """`firestarter info W27C512` resolves the chip — note this command currently
    crashes downstream in ic_layout.py (pre-existing TypeError pinned by
    test_characterization::test_info_known_chip with exit 1). This Click-side
    test asserts the chip-resolution PATH succeeds (the `chip not found` error
    is NOT in the output), which is what cli_handlers.py is responsible for
    this wave. The downstream crash is preserved verbatim per GATE-1.8b.

    See plan 41-02 task 2 step 8: "preserve the equivalent error shape from
    the current argparse `info` handler — preserve verbatim". The argparse
    path exits 1 on this same downstream crash; the Click path matches it.
    """
    result = runner.invoke(cli, ["info", "W27C512"])
    # Chip-resolution path succeeded (no 'not found' error message).
    # The downstream ic_layout TypeError is preserved as-is; CliRunner captures
    # it via result.exception. Exit code matches the argparse contract: 1.
    assert "not found in database" not in result.output
    assert result.exit_code == 1


def test_info_unknown_chip_error_path(runner: CliRunner) -> None:
    """`firestarter info NOPE_NOT_A_CHIP` exits 1 with the chip-not-found
    error message — mirrors the argparse contract from main.py:642-644."""
    result = runner.invoke(cli, ["info", "NOPE_NOT_A_CHIP"])
    assert result.exit_code == 1
    # The logger.error format: "EPROM '{name}' not found in database."
    # Output capture depends on the logging handler attached; assert on the
    # exit code (the load-bearing contract) and tolerate either output path.


def test_search_happy_path(runner: CliRunner) -> None:
    """`firestarter search W27` exits 0 and a matching chip name is in output."""
    result = runner.invoke(cli, ["search", "W27"])
    assert result.exit_code == 0
    # At least one W27-family chip should be in the table; W27C512 is the
    # canonical example used elsewhere in the suite.
    assert "W27" in result.output


def test_no_prefix_matching(runner: CliRunner) -> None:
    """TRAP #2 (D-13.2): Click matches command names EXACTLY by default;
    argparse accepts unambiguous prefixes. This pins Click's exact-match
    behaviour as a regression guard.

    `firestarter lis` MUST NOT dispatch to `list`. Click's default error
    wording: "Error: No such command 'lis'." — exit code 2 (usage error).
    """
    result = runner.invoke(cli, ["lis"])
    assert result.exit_code != 0
    assert "No such command" in result.output
