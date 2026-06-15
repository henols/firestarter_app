"""In-process CliRunner suite for `firestarter.cli_handlers.cli` (Waves 2 + 3).

Wave 2 covered the read-only command surface (list/info/search + --help +
--version + Click's exact-match trap).

Wave 3 / Plan 41-03 extends with happy-path + error-path tests for each of
the 11 remaining commands plus TRAP-specific coverage:
  - TRAP #1 (exit codes) — every test asserts exit_code; chip-op error paths
    exercise the _resolve_or_exit -> sys.exit(1) shape.
  - TRAP #3 (write --no-blank-check polarity vs. erase --blank-check polarity)
    — both polarities have dedicated tests.
  - TRAP #4 (fw 3-way mutex) — 3 pairing tests cover all combinations.
  - TRAP #5 (fw firmware-version validator) — covered by an explicit
    "invalid version" test.
  - D-14 (--json without --list raises UsageError) — covered.
  - D-12 step 5 (dev consistency-check 3-way verdict 0/1/2) — covered by 3
    separate verdict tests proving the handler does NOT bool-to-int wrap.
"""

from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from firestarter.cli_handlers import AppContext, cli
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.eprom_operations import EpromOperator
from firestarter.firmware import FirmwareManager
from firestarter.hardware import HardwareManager


@pytest.fixture
def runner() -> CliRunner:
    """Fresh CliRunner per test — mix_stderr=True so stderr+stdout flow into result.output."""
    return CliRunner()


def make_app_context(**manager_overrides) -> AppContext:
    """Construct an AppContext for in-process CliRunner tests.

    Defaults to a real `EpromDatabase(skip_local_override=True)` (Phase 36
    D-06 seam — hermetic isolation from any local override file) plus
    Mock-spec'd manager fields (no test attempts real serial I/O).

    Overrides let a test substitute a specific manager with a configured mock
    (e.g. `eprom_operator=mock_returning_read_true`).
    """
    db = manager_overrides.pop("db", None)
    if db is None:
        db = EpromDatabase(skip_local_override=True)
    config_manager = manager_overrides.pop("config_manager", None)
    if config_manager is None:
        # ConfigManager is a singleton — use the real one for tests; handlers
        # only read .get_value for port plumbing on the fw install path.
        config_manager = ConfigManager()
    return AppContext(
        db=db,
        config_manager=config_manager,
        eprom_operator=manager_overrides.pop(
            "eprom_operator", Mock(spec=EpromOperator)
        ),
        hardware_manager=manager_overrides.pop(
            "hardware_manager", Mock(spec=HardwareManager)
        ),
        firmware_manager=manager_overrides.pop(
            "firmware_manager", Mock(spec=FirmwareManager)
        ),
        eprom_presenter=manager_overrides.pop(
            "eprom_presenter", Mock(spec=EpromConsolePresenter)
        ),
    )


# ---------------------------------------------------------------------------
# Wave 2 tests (preserved)
# ---------------------------------------------------------------------------


def test_cli_help_runs(runner: CliRunner) -> None:
    """`firestarter --help` exits 0 and the Click usage string mentions the
    three read-only commands landed this wave."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.output
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
    assert "W27C512" in result.output


def test_info_chip_resolution_happy_path(runner: CliRunner) -> None:
    """`firestarter info W27C512` resolves the chip and displays the layout.

    Phase 69 Plan 01 fixed the ic_layout list-vs-int crash; exit_code is now 0.
    """
    result = runner.invoke(cli, ["info", "W27C512"])
    assert "not found in database" not in result.output
    assert result.exit_code == 0


def test_info_unknown_chip_error_path(runner: CliRunner) -> None:
    """`firestarter info NOPE_NOT_A_CHIP` exits 1 with chip-not-found error."""
    result = runner.invoke(cli, ["info", "NOPE_NOT_A_CHIP"])
    assert result.exit_code == 1


def test_search_happy_path(runner: CliRunner) -> None:
    """`firestarter search W27` exits 0 and a matching chip name is in output."""
    result = runner.invoke(cli, ["search", "W27"])
    assert result.exit_code == 0
    assert "W27" in result.output


def test_no_prefix_matching(runner: CliRunner) -> None:
    """TRAP #2 (D-13.2): Click matches command names EXACTLY by default.

    `firestarter lis` MUST NOT dispatch to `list`.
    """
    result = runner.invoke(cli, ["lis"])
    assert result.exit_code != 0
    assert "No such command" in result.output


# ---------------------------------------------------------------------------
# Wave 3 chip-op happy-path + error-path tests
# ---------------------------------------------------------------------------


def test_read_happy_path(runner: CliRunner) -> None:
    """`firestarter read W27C512 out.bin` exits 0 when eprom_operator.read_eprom
    returns True. Real DB used so chip resolution succeeds; mocked operator
    swallows the actual serial call."""
    operator = Mock(spec=EpromOperator)
    operator.read_eprom.return_value = True
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["read", "W27C512", "out.bin"], obj=app)
    assert result.exit_code == 0
    operator.read_eprom.assert_called_once()


def test_read_chip_not_found(runner: CliRunner) -> None:
    """`firestarter read NOPE out.bin` exits 1 via _resolve_or_exit -> None."""
    app = make_app_context()
    result = runner.invoke(cli, ["read", "NOPE_NOT_A_CHIP", "out.bin"], obj=app)
    assert result.exit_code == 1


def test_read_operator_returns_false(runner: CliRunner) -> None:
    """`firestarter read W27C512 out.bin` exits 1 when operator returns False."""
    operator = Mock(spec=EpromOperator)
    operator.read_eprom.return_value = False
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["read", "W27C512", "out.bin"], obj=app)
    assert result.exit_code == 1


def test_write_happy_path(runner: CliRunner) -> None:
    """`firestarter write W27C512 in.bin` exits 0 when write_eprom returns True."""
    operator = Mock(spec=EpromOperator)
    operator.write_eprom.return_value = True
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["write", "W27C512", "in.bin"], obj=app)
    assert result.exit_code == 0
    operator.write_eprom.assert_called_once()


def test_write_operator_returns_false(runner: CliRunner) -> None:
    """`firestarter write W27C512 in.bin` exits 1 when write_eprom returns False."""
    operator = Mock(spec=EpromOperator)
    operator.write_eprom.return_value = False
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["write", "W27C512", "in.bin"], obj=app)
    assert result.exit_code == 1


def test_write_no_blank_check_polarity(runner: CliRunner) -> None:
    """TRAP #3 / D-13.3: ``-b/--no-blank-check`` flips ``blank_check`` to False.

    Default (no flag): blank_check=True. With -b present: blank_check=False.
    Verified by inspecting the FLAGS bit Click computes from --no-blank-check
    and forwards to write_eprom via operation_flags. The FLAG_SKIP_BLANK_CHECK
    bit (0x01) is set iff blank_check=False (matches build_flags in
    eprom_operations.py:62).
    """
    from firestarter.constants import FLAG_SKIP_BLANK_CHECK

    operator = Mock(spec=EpromOperator)
    operator.write_eprom.return_value = True

    # Default (no -b): blank_check should be True -> FLAG_SKIP_BLANK_CHECK NOT set.
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["write", "W27C512", "in.bin"], obj=app)
    assert result.exit_code == 0
    _, kwargs = operator.write_eprom.call_args
    assert not (kwargs["operation_flags"] & FLAG_SKIP_BLANK_CHECK)

    # With -b: blank_check should be False -> FLAG_SKIP_BLANK_CHECK set.
    operator.write_eprom.reset_mock()
    app2 = make_app_context(eprom_operator=operator)
    result2 = runner.invoke(
        cli, ["write", "W27C512", "in.bin", "--no-blank-check"], obj=app2
    )
    assert result2.exit_code == 0
    _, kwargs2 = operator.write_eprom.call_args
    assert kwargs2["operation_flags"] & FLAG_SKIP_BLANK_CHECK


def test_verify_happy_path(runner: CliRunner) -> None:
    """`firestarter verify W27C512 in.bin` exits 0 when verify_eprom returns True."""
    operator = Mock(spec=EpromOperator)
    operator.verify_eprom.return_value = True
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["verify", "W27C512", "in.bin"], obj=app)
    assert result.exit_code == 0


def test_verify_operator_returns_false(runner: CliRunner) -> None:
    """`firestarter verify W27C512 in.bin` exits 1 when verify returns False."""
    operator = Mock(spec=EpromOperator)
    operator.verify_eprom.return_value = False
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["verify", "W27C512", "in.bin"], obj=app)
    assert result.exit_code == 1


def test_blank_happy_path(runner: CliRunner) -> None:
    """`firestarter blank W27C512` exits 0 when check_eprom_blank returns True."""
    operator = Mock(spec=EpromOperator)
    operator.check_eprom_blank.return_value = True
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["blank", "W27C512"], obj=app)
    assert result.exit_code == 0


def test_blank_operator_returns_false(runner: CliRunner) -> None:
    """`firestarter blank W27C512` exits 1 when check_eprom_blank returns False."""
    operator = Mock(spec=EpromOperator)
    operator.check_eprom_blank.return_value = False
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["blank", "W27C512"], obj=app)
    assert result.exit_code == 1


def test_erase_happy_path(runner: CliRunner) -> None:
    """`firestarter erase W27C512` exits 0 when erase_eprom returns True."""
    operator = Mock(spec=EpromOperator)
    operator.erase_eprom.return_value = True
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["erase", "W27C512"], obj=app)
    assert result.exit_code == 0


def test_erase_blank_check_polarity(runner: CliRunner) -> None:
    """TRAP #3 / D-13.3: erase ``-b/--blank-check`` polarity is the inverse of
    write's ``--no-blank-check`` — both coexist verbatim.

    Default (no -b): blank_check=False -> FLAG_SKIP_BLANK_CHECK SET.
    With -b: blank_check=True -> FLAG_SKIP_BLANK_CHECK NOT set.
    """
    from firestarter.constants import FLAG_SKIP_BLANK_CHECK

    operator = Mock(spec=EpromOperator)
    operator.erase_eprom.return_value = True

    # Default (no -b): blank_check=False -> SKIP set.
    app = make_app_context(eprom_operator=operator)
    runner.invoke(cli, ["erase", "W27C512"], obj=app)
    _, kwargs = operator.erase_eprom.call_args
    assert kwargs["operation_flags"] & FLAG_SKIP_BLANK_CHECK

    # With -b: blank_check=True -> SKIP not set.
    operator.erase_eprom.reset_mock()
    app2 = make_app_context(eprom_operator=operator)
    runner.invoke(cli, ["erase", "W27C512", "-b"], obj=app2)
    _, kwargs2 = operator.erase_eprom.call_args
    assert not (kwargs2["operation_flags"] & FLAG_SKIP_BLANK_CHECK)


def test_erase_operator_returns_false(runner: CliRunner) -> None:
    """`firestarter erase W27C512` exits 1 when erase_eprom returns False."""
    operator = Mock(spec=EpromOperator)
    operator.erase_eprom.return_value = False
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["erase", "W27C512"], obj=app)
    assert result.exit_code == 1


def test_id_happy_path(runner: CliRunner) -> None:
    """`firestarter id W27C512` exits 0 when check_eprom_id returns (True, _)."""
    operator = Mock(spec=EpromOperator)
    operator.check_eprom_id.return_value = (True, 0x1234)
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["id", "W27C512"], obj=app)
    assert result.exit_code == 0


def test_id_chip_not_found(runner: CliRunner) -> None:
    """`firestarter id NOPE` exits 1 via _resolve_or_exit -> None."""
    app = make_app_context()
    result = runner.invoke(cli, ["id", "NOPE_NOT_A_CHIP"], obj=app)
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Voltage commands (vpp, vpe)
# ---------------------------------------------------------------------------


def test_vpp_happy_path(runner: CliRunner) -> None:
    """`firestarter vpp` exits 0 when read_vpp_voltage returns True."""
    hw = Mock(spec=HardwareManager)
    hw.read_vpp_voltage.return_value = True
    app = make_app_context(hardware_manager=hw)
    result = runner.invoke(cli, ["vpp"], obj=app)
    assert result.exit_code == 0
    hw.read_vpp_voltage.assert_called_once()


def test_vpp_returns_false(runner: CliRunner) -> None:
    """`firestarter vpp` exits 1 when read_vpp_voltage returns False."""
    hw = Mock(spec=HardwareManager)
    hw.read_vpp_voltage.return_value = False
    app = make_app_context(hardware_manager=hw)
    result = runner.invoke(cli, ["vpp"], obj=app)
    assert result.exit_code == 1


def test_vpe_happy_path(runner: CliRunner) -> None:
    """`firestarter vpe` exits 0 when read_vpe_voltage returns True."""
    hw = Mock(spec=HardwareManager)
    hw.read_vpe_voltage.return_value = True
    app = make_app_context(hardware_manager=hw)
    result = runner.invoke(cli, ["vpe"], obj=app)
    assert result.exit_code == 0
    hw.read_vpe_voltage.assert_called_once()


def test_vpe_returns_false(runner: CliRunner) -> None:
    """`firestarter vpe` exits 1 when read_vpe_voltage returns False."""
    hw = Mock(spec=HardwareManager)
    hw.read_vpe_voltage.return_value = False
    app = make_app_context(hardware_manager=hw)
    result = runner.invoke(cli, ["vpe"], obj=app)
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Hardware commands (hw, config)
# ---------------------------------------------------------------------------


def test_hw_happy_path(runner: CliRunner) -> None:
    """`firestarter hw` exits 0 when get_hardware_revision returns True."""
    hw = Mock(spec=HardwareManager)
    hw.get_hardware_revision.return_value = True
    app = make_app_context(hardware_manager=hw)
    result = runner.invoke(cli, ["hw"], obj=app)
    assert result.exit_code == 0


def test_hw_returns_false(runner: CliRunner) -> None:
    """`firestarter hw` exits 1 when get_hardware_revision returns False."""
    hw = Mock(spec=HardwareManager)
    hw.get_hardware_revision.return_value = False
    app = make_app_context(hardware_manager=hw)
    result = runner.invoke(cli, ["hw"], obj=app)
    assert result.exit_code == 1


def test_config_happy_path(runner: CliRunner) -> None:
    """`firestarter config -r1 1000` exits 0 when set_hardware_config returns True."""
    hw = Mock(spec=HardwareManager)
    hw.set_hardware_config.return_value = True
    app = make_app_context(hardware_manager=hw)
    result = runner.invoke(cli, ["config", "-r1", "1000"], obj=app)
    assert result.exit_code == 0


def test_config_returns_false(runner: CliRunner) -> None:
    """`firestarter config` exits 1 when set_hardware_config returns False."""
    hw = Mock(spec=HardwareManager)
    hw.set_hardware_config.return_value = False
    app = make_app_context(hardware_manager=hw)
    result = runner.invoke(cli, ["config"], obj=app)
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Firmware command (fw) — TRAPs #4 + #5 + D-14
# ---------------------------------------------------------------------------


def test_fw_install_happy_path(runner: CliRunner) -> None:
    """`firestarter fw -i` exits 0 when manage_firmware_update returns True."""
    fw_mgr = Mock(spec=FirmwareManager)
    fw_mgr.manage_firmware_update.return_value = True
    app = make_app_context(firmware_manager=fw_mgr)
    result = runner.invoke(cli, ["fw", "-i"], obj=app)
    assert result.exit_code == 0


def test_fw_install_returns_false(runner: CliRunner) -> None:
    """`firestarter fw -i` exits 1 when manage_firmware_update returns False."""
    fw_mgr = Mock(spec=FirmwareManager)
    fw_mgr.manage_firmware_update.return_value = False
    app = make_app_context(firmware_manager=fw_mgr)
    result = runner.invoke(cli, ["fw", "-i"], obj=app)
    assert result.exit_code == 1


def test_fw_mutex_pre_and_firmware_version(runner: CliRunner) -> None:
    """TRAP #4 / D-13.4: --pre + --firmware-version exits 2 (mutually exclusive).

    Enforced by a single post-parse check at the top of fw()'s body
    (cli_handlers.py:792-805 — WR-03) raising click.UsageError when more
    than one of --pre / --firmware-version / --stable is set.
    """
    fw_mgr = Mock(spec=FirmwareManager)
    app = make_app_context(firmware_manager=fw_mgr)
    result = runner.invoke(
        cli, ["fw", "-i", "--pre", "--firmware-version", "3.0.0b6"], obj=app
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output.lower()


def test_fw_mutex_stable_and_pre(runner: CliRunner) -> None:
    """TRAP #4 / D-13.4: --stable + --pre exits 2 (mutually exclusive)."""
    fw_mgr = Mock(spec=FirmwareManager)
    app = make_app_context(firmware_manager=fw_mgr)
    result = runner.invoke(cli, ["fw", "-i", "--stable", "--pre"], obj=app)
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output.lower()


def test_fw_mutex_firmware_version_and_stable(runner: CliRunner) -> None:
    """TRAP #4 / D-13.4: --firmware-version + --stable exits 2."""
    fw_mgr = Mock(spec=FirmwareManager)
    app = make_app_context(firmware_manager=fw_mgr)
    result = runner.invoke(
        cli, ["fw", "-i", "--firmware-version", "3.0.0", "--stable"], obj=app
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output.lower()


def test_fw_invalid_firmware_version(runner: CliRunner) -> None:
    """TRAP #5 / D-13.5: --firmware-version with non-matching value exits 2.

    The custom Click ParamType _FirmwareVersionType raises BadParameter via
    self.fail(...) when the value does not match FIRMWARE_VERSION_RE.
    """
    fw_mgr = Mock(spec=FirmwareManager)
    app = make_app_context(firmware_manager=fw_mgr)
    result = runner.invoke(
        cli, ["fw", "-i", "--firmware-version", "not-a-version"], obj=app
    )
    assert result.exit_code == 2
    assert "Invalid firmware version" in result.output


def test_fw_json_requires_list(runner: CliRunner) -> None:
    """D-14: --json without --list raises click.UsageError (exit 2 + "Usage:" header)."""
    fw_mgr = Mock(spec=FirmwareManager)
    app = make_app_context(firmware_manager=fw_mgr)
    result = runner.invoke(cli, ["fw", "--json"], obj=app)
    assert result.exit_code == 2
    assert "--json requires --list" in result.output


def test_fw_list_with_json(runner: CliRunner) -> None:
    """`firestarter fw --list --json` exits 0 (legitimate combination)."""
    fw_mgr = Mock(spec=FirmwareManager)
    fw_mgr.list_releases.return_value = []
    app = make_app_context(firmware_manager=fw_mgr)
    result = runner.invoke(cli, ["fw", "--list", "--json"], obj=app)
    assert result.exit_code == 0


def test_fw_list_plain(runner: CliRunner) -> None:
    """`firestarter fw --list` exits 0 with mocked list_releases returning []."""
    fw_mgr = Mock(spec=FirmwareManager)
    fw_mgr.list_releases.return_value = []
    app = make_app_context(firmware_manager=fw_mgr)
    result = runner.invoke(cli, ["fw", "--list"], obj=app)
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# dev group + 4 sub-commands
# ---------------------------------------------------------------------------


def test_dev_read_happy_path(runner: CliRunner) -> None:
    """`firestarter dev read W27C512` exits 0 when dev_read_eprom returns True."""
    operator = Mock(spec=EpromOperator)
    operator.dev_read_eprom.return_value = True
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["dev", "read", "W27C512"], obj=app)
    assert result.exit_code == 0


def test_dev_read_returns_false(runner: CliRunner) -> None:
    """`firestarter dev read W27C512` exits 1 when dev_read_eprom returns False."""
    operator = Mock(spec=EpromOperator)
    operator.dev_read_eprom.return_value = False
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["dev", "read", "W27C512"], obj=app)
    assert result.exit_code == 1


def test_dev_reg_happy_path(runner: CliRunner) -> None:
    """`firestarter dev reg 0x10 0x20 0x30` exits 0 when dev_set_registers True."""
    operator = Mock(spec=EpromOperator)
    operator.dev_set_registers.return_value = True
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["dev", "reg", "0x10", "0x20", "0x30"], obj=app)
    assert result.exit_code == 0


def test_dev_reg_returns_false(runner: CliRunner) -> None:
    """`firestarter dev reg 0x10 0x20 0x30` exits 1 when dev_set_registers False."""
    operator = Mock(spec=EpromOperator)
    operator.dev_set_registers.return_value = False
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["dev", "reg", "0x10", "0x20", "0x30"], obj=app)
    assert result.exit_code == 1


def test_dev_addr_happy_path(runner: CliRunner) -> None:
    """`firestarter dev addr W27C512 0x100` exits 0 when dev_set_address_mode True."""
    operator = Mock(spec=EpromOperator)
    operator.dev_set_address_mode.return_value = True
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["dev", "addr", "W27C512", "0x100"], obj=app)
    assert result.exit_code == 0


def test_dev_addr_returns_false(runner: CliRunner) -> None:
    """`firestarter dev addr W27C512 0x100` exits 1 when dev_set_address_mode False."""
    operator = Mock(spec=EpromOperator)
    operator.dev_set_address_mode.return_value = False
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["dev", "addr", "W27C512", "0x100"], obj=app)
    assert result.exit_code == 1


def test_dev_consistency_check_pass_verdict(runner: CliRunner) -> None:
    """D-12 step 5 / 3-way verdict: PASS (verdict_int=0) -> exit 0."""
    operator = Mock(spec=EpromOperator)
    operator.consistency_check_eprom.return_value = 0
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["dev", "consistency-check", "W27C512"], obj=app)
    assert result.exit_code == 0


def test_dev_consistency_check_fail_verdict(runner: CliRunner) -> None:
    """D-12 step 5 / 3-way verdict: FAIL (verdict_int=1) -> exit 1."""
    operator = Mock(spec=EpromOperator)
    operator.consistency_check_eprom.return_value = 1
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["dev", "consistency-check", "W27C512"], obj=app)
    assert result.exit_code == 1


def test_dev_consistency_check_hardware_error_verdict(runner: CliRunner) -> None:
    """D-12 step 5 / 3-way verdict: HARDWARE ERROR (verdict_int=2) -> exit 2.

    CRITICAL: this test proves the handler does NOT bool-to-int wrap. If the
    handler used `sys.exit(0 if verdict else 1)`, this test would see exit 1
    (FAIL) instead of exit 2 (hardware-error), breaking the v1.6 RCA diagnostic.
    """
    operator = Mock(spec=EpromOperator)
    operator.consistency_check_eprom.return_value = 2
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["dev", "consistency-check", "W27C512"], obj=app)
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Phase-53 Plan 01 Task 3: RED smoke tests for dev write-cycle + dev fault-inject
#
# All four tests MUST FAIL until 53-02 registers the subcommands. Click will
# report "No such command 'write-cycle'" / "No such command 'fault-inject'",
# producing exit code 2 (usage error) instead of the expected 0 or 2 (hw-error).
# ---------------------------------------------------------------------------


def test_dev_write_cycle_pass(runner: CliRunner, tmp_path) -> None:
    """dev write-cycle W27C512 <source>: write_cycle_eprom returns 0 -> exit 0.

    FAILS RED until 53-02 registers the dev write-cycle subcommand
    (Click: 'No such command').
    """
    operator = Mock(spec=EpromOperator)
    operator.write_cycle_eprom.return_value = 0  # type: ignore[attr-defined]
    app = make_app_context(eprom_operator=operator)
    source = tmp_path / "source.bin"
    source.write_bytes(b"\xaa" * 65536)
    result = runner.invoke(cli, ["dev", "write-cycle", "W27C512", str(source)], obj=app)
    assert result.exit_code == 0, (
        f"Expected exit 0 (PASS), got {result.exit_code}. Output: {result.output!r}"
    )


def test_dev_write_cycle_hardware_error(runner: CliRunner, tmp_path) -> None:
    """dev write-cycle W27C512 <source>: write_cycle_eprom returns 2 -> exit 2.

    CRITICAL: exit 2 (hw-error) must NOT be collapsed to 1 (mismatch) —
    the 3-way verdict is load-bearing for the v1.6 RCA diagnostic.

    FAILS RED until 53-02 registers the dev write-cycle subcommand.
    """
    operator = Mock(spec=EpromOperator)
    operator.write_cycle_eprom.return_value = 2  # type: ignore[attr-defined]
    app = make_app_context(eprom_operator=operator)
    source = tmp_path / "source.bin"
    source.write_bytes(b"\xaa" * 65536)
    result = runner.invoke(cli, ["dev", "write-cycle", "W27C512", str(source)], obj=app)
    assert result.exit_code == 2, (
        f"Expected exit 2 (hw-error), got {result.exit_code}. Output: {result.output!r}"
    )


def test_dev_fault_inject_pass(runner: CliRunner) -> None:
    """dev fault-inject W27C512: fault_inject_cycle returns True -> exit 0.

    FAILS RED until 53-02 registers the dev fault-inject subcommand
    (Click: 'No such command').
    """
    operator = Mock(spec=EpromOperator)
    operator.fault_inject_cycle.return_value = True  # type: ignore[attr-defined]
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["dev", "fault-inject", "W27C512"], obj=app)
    assert result.exit_code == 0, (
        f"Expected exit 0 (fault-inject passed), got {result.exit_code}. "
        f"Output: {result.output!r}"
    )


def test_dev_fault_inject_fail(runner: CliRunner) -> None:
    """dev fault-inject W27C512: fault_inject_cycle returns False -> exit 1.

    FAILS RED until 53-02 registers the dev fault-inject subcommand.
    """
    operator = Mock(spec=EpromOperator)
    operator.fault_inject_cycle.return_value = False  # type: ignore[attr-defined]
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["dev", "fault-inject", "W27C512"], obj=app)
    assert result.exit_code == 1, (
        f"Expected exit 1 (fault-inject failed), got {result.exit_code}. "
        f"Output: {result.output!r}"
    )
