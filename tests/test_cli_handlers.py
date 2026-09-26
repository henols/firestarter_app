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

import logging
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

import firestarter.cli_handlers as cli_handlers_mod
from firestarter.cli_handlers import AppContext, cli
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.eprom_operations import EpromOperator
from firestarter.firmware import FirmwareManager
from firestarter.hardware import HardwareManager
from firestarter.messages import (
    MSG_DATA_CHUNK,
    MSG_END_DONE,
    MSG_INIT_DONE,
    MSG_MAIN_DONE,
)

from .conftest import build_frame


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


# Wave 2 tests (preserved)


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
    Injects a REAL EpromConsolePresenter(db) — Pitfall 1: the default Mock
    presenter returns None from prepare_detailed_eprom_data and masks the fix.
    """
    db = EpromDatabase(skip_local_override=True)
    app = make_app_context(db=db, eprom_presenter=EpromConsolePresenter(db))
    result = runner.invoke(cli, ["info", "W27C512"], obj=app)
    assert "not found in database" not in result.output
    assert result.exit_code == 0


def test_info_unknown_chip_error_path(runner: CliRunner) -> None:
    """`firestarter info NOPE_NOT_A_CHIP` exits 1 with chip-not-found error."""
    result = runner.invoke(cli, ["info", "NOPE_NOT_A_CHIP"])
    assert result.exit_code == 1


def test_info_elevated_programming_vcc_warns(
    runner: CliRunner, caplog: pytest.LogCaptureFixture
) -> None:
    """`firestarter info MBM27C1000` warns that its programming supply
    decodes above the shield's fixed rail (Phase 200, VCC-01/D-04/D-06).

    Measured fact this test depends on: the `cli` group short-circuits
    `_setup_logging` when `ctx.obj` is already an `AppContext`
    (`cli_handlers.py`'s test-mode short-circuit), so under `CliRunner` the
    root logger keeps its ambient level rather than the `INFO` level
    `_setup_logging` would otherwise set. Under pytest that ambient level is
    `WARNING`, so every `logger.info` field row, including
    `Programming VCC:`, never passes the logger's own level check.

    Assertions run against `caplog.text`, not `result.output`: pytest
    installs its own log-capturing handler on the root logger for every
    test, which satisfies `Logger.callHandlers`'s "a handler processed this
    record" condition and so suppresses Python's `logging.lastResort`
    stderr fallback that `CliRunner` would otherwise pick up. Measured this
    session — under a bare interpreter (no pytest) the same invocation's
    `result.output` does contain the warning text via that fallback; under
    pytest it is always `''` regardless of what was logged, so asserting
    against `result.output` here would pass vacuously. `caplog.text` is the
    correct capture surface inside pytest, and it still proves the
    log-level separation: `Programming VCC:` is absent from `caplog.text`
    for the same level-filtering reason it is absent from a real terminal.

    Wrapped in `caplog.at_level(logging.WARNING, logger="EpromConsolePresenter")`
    rather than relying on the ambient root level: measured this session
    that a sibling test invoking `cli` WITHOUT a pre-built `ctx.obj` (e.g.
    `test_info_unknown_chip_error_path`) runs the real `_setup_logging`,
    which sets the *root* logger's level to `INFO` for the rest of the
    process — there is no teardown that restores it. Running this test
    after such a sibling, without pinning the level explicitly here, would
    let `Programming VCC:` leak into `caplog.text` and fail the log-level
    assertion depending on test order. `at_level` sets the named logger's
    level for the duration of the `with` block regardless of what a prior
    test left behind, so this test's outcome does not depend on execution
    order. Injects a REAL `EpromConsolePresenter(db)` — the default Mock
    presenter returns `None` from `prepare_detailed_eprom_data` and would
    mask the feature entirely.
    """
    db = EpromDatabase(skip_local_override=True)
    app = make_app_context(db=db, eprom_presenter=EpromConsolePresenter(db))
    with caplog.at_level(logging.WARNING, logger="EpromConsolePresenter"):
        result = runner.invoke(cli, ["info", "MBM27C1000"], obj=app)
    assert result.exit_code == 0
    assert "WARNING: Programming VCC decodes to 6.0 V; using 5.0 V." in caplog.text
    assert "Programming VCC:" not in caplog.text
    assert "not found in database" not in caplog.text


def test_info_five_volt_part_emits_no_programming_vcc_warning(
    runner: CliRunner, caplog: pytest.LogCaptureFixture
) -> None:
    """`firestarter info W27C512` emits neither the row nor the warning
    (Phase 200, D-06 fail-open).

    W27C512 is one of the 462 rows at or below 5000 mV `vdd_mv` — its
    silence here is D-06's fail-open path observed end to end through the
    real CLI and a real presenter, not an incidental absence of output.
    Asserts against `caplog.text` for the same reason
    `test_info_elevated_programming_vcc_warns` does: under pytest,
    `result.output` is always empty when `ctx.obj` is pre-built, so it
    would pass this assertion vacuously regardless of behavior. Wrapped in
    the same `caplog.at_level(...)` for the same order-independence reason
    documented there.
    """
    db = EpromDatabase(skip_local_override=True)
    app = make_app_context(db=db, eprom_presenter=EpromConsolePresenter(db))
    with caplog.at_level(logging.WARNING, logger="EpromConsolePresenter"):
        result = runner.invoke(cli, ["info", "W27C512"], obj=app)
    assert result.exit_code == 0
    assert "decodes to" not in caplog.text
    assert "Programming VCC" not in caplog.text
    assert "not found in database" not in caplog.text


def test_info_happy_path_no_crash(runner: CliRunner) -> None:
    """`firestarter info W27C512` exits 0 with chip name in output.

    SC#1 regression: proves the ic_layout list-vs-int fix holds end-to-end.
    Uses a REAL EpromConsolePresenter(db) to exercise the full display path.
    """
    db = EpromDatabase(skip_local_override=True)
    app = make_app_context(db=db, eprom_presenter=EpromConsolePresenter(db))
    result = runner.invoke(cli, ["info", "W27C512"], obj=app)
    assert result.exit_code == 0
    assert "Traceback (most recent call last)" not in result.output


def test_info_2732_list_valued_pin_no_crash(runner: CliRunner) -> None:
    """`firestarter info 2732` exits 0 — SC#1 list-valued-pin regression.

    2732 has vpp-pin=[20] (list-valued shared pin); this was the original
    live-crash chip. Proves the scalar-extraction fix in ic_layout.py works
    for the vpp-exceeds-max + shared-pin path. REAL presenter required.
    """
    db = EpromDatabase(skip_local_override=True)
    app = make_app_context(db=db, eprom_presenter=EpromConsolePresenter(db))
    result = runner.invoke(cli, ["info", "2732"], obj=app)
    assert result.exit_code == 0
    assert "Traceback (most recent call last)" not in result.output


def test_info_nmos_25v_no_crash(runner: CliRunner) -> None:
    """`firestarter info M2716` exits 0 — supported 25V NMOS, distinct vpp/oe.

    info renders a chip with distinct vpp and oe pins without crashing. REAL
    presenter required (Pitfall 1).

    Phase 79 (NMOS-02): M2716 graduated from 'vpp-exceeds-max' to 'supported'
    at the raised 25V ceiling; info still exits 0 (it displayed before and
    resolves now).
    """
    db = EpromDatabase(skip_local_override=True)
    app = make_app_context(db=db, eprom_presenter=EpromConsolePresenter(db))
    result = runner.invoke(cli, ["info", "M2716"], obj=app)
    assert result.exit_code == 0
    assert "Traceback (most recent call last)" not in result.output


def test_info_adapter_required_no_crash(runner: CliRunner) -> None:
    """`firestarter info AT28C16` exits 0 — adapter-required 24-pin EEPROM.

    info does NOT refuse adapter-required chips — it displays them. This is the
    correct Phase 68 behavior: the capability guard fires only in resolve_chip
    (the chip-op path), not in the info display path. REAL presenter required.
    """
    db = EpromDatabase(skip_local_override=True)
    app = make_app_context(db=db, eprom_presenter=EpromConsolePresenter(db))
    result = runner.invoke(cli, ["info", "AT28C16"], obj=app)
    assert result.exit_code == 0
    assert "Traceback (most recent call last)" not in result.output
    assert "ChipNotImplementedError" not in result.output


def test_info_protocol_not_implemented_no_crash(runner: CliRunner) -> None:
    """`firestarter info X88C64P` exits 0 — protocol-not-implemented DISPLAYS, not refuses.

    SC#3 protocol-not-implemented coverage (Phase 66 third non-supported status).
    X88C64P is the sole protocol-not-implemented chip in the packaged DB (part_number
    alias "X88C64P,X88C64S", protocol 0x34 XICOR NovRAM). info bypasses resolve_chip
    so it DISPLAYS the chip without refusing — same contract as M2716/AT28C16.
    REAL presenter required (Pitfall 1). Depends on Plan 01 ic_layout fix.
    """
    db = EpromDatabase(skip_local_override=True)
    app = make_app_context(db=db, eprom_presenter=EpromConsolePresenter(db))
    result = runner.invoke(cli, ["info", "X88C64P"], obj=app)
    assert result.exit_code == 0
    assert "Traceback (most recent call last)" not in result.output


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


# Wave 3 chip-op happy-path + error-path tests


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


def test_read_non_supported_typed_refusal(runner: CliRunner) -> None:
    """`firestarter read AT28C04 out.bin` exits 1 with typed support_status refusal.

    SC#3 non-supported chip. The Phase 66 ChipNotImplementedError guard in
    resolve_chip refuses before any wire dict is built. The @map_typed_errors
    decorator converts ChipNotImplementedError -> exit 1 + "Chip not usable:"
    message. No Traceback in output.

    Re-anchored from M2716 in Phase 79: M2716 graduated to 'supported' (NMOS-02),
    so the 'vpp-exceeds-max' category is empty — AT28C04 (adapter-required) is the
    still-non-supported exemplar.
    """
    app = make_app_context()
    result = runner.invoke(cli, ["read", "AT28C04", "out.bin"], obj=app)
    assert result.exit_code == 1
    assert "Traceback (most recent call last)" not in result.output
    assert "AT28C04" in result.output


def test_read_protocol_not_implemented_typed_refusal(runner: CliRunner) -> None:
    """`firestarter read X88C64P out.bin` exits 1 with typed support_status refusal.

    SC#3 protocol-not-implemented (X88C64P — sole protocol-not-implemented chip
    in the packaged DB, part_number alias "X88C64P,X88C64S", protocol 0x34).
    The Phase 66 ChipNotImplementedError guard in resolve_chip refuses before
    any wire dict is built; @map_typed_errors converts to exit 1 with "Chip not
    usable:" and the not-implemented reason substring. No Traceback in output.
    """
    app = make_app_context()
    result = runner.invoke(cli, ["read", "X88C64P", "out.bin"], obj=app)
    assert result.exit_code == 1
    assert "X88C64P" in result.output
    assert "not implemented" in result.output.lower()
    assert "Traceback (most recent call last)" not in result.output


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
    FWBLANK-04 (Phase 205) retired the wire bit this used to travel as;
    verified now by inspecting write_eprom's own `blank_check_requested`
    keyword argument directly.
    """
    operator = Mock(spec=EpromOperator)
    operator.write_eprom.return_value = True

    # Default (no -b): blank_check should be True.
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["write", "W27C512", "in.bin"], obj=app)
    assert result.exit_code == 0
    _, kwargs = operator.write_eprom.call_args
    assert kwargs["blank_check_requested"] is True

    # With -b: blank_check should be False.
    operator.write_eprom.reset_mock()
    app2 = make_app_context(eprom_operator=operator)
    result2 = runner.invoke(
        cli, ["write", "W27C512", "in.bin", "--no-blank-check"], obj=app2
    )
    assert result2.exit_code == 0
    _, kwargs2 = operator.write_eprom.call_args
    assert kwargs2["blank_check_requested"] is False


def test_write_b_decouples_skip_erase_phase92(runner: CliRunner) -> None:
    """Phase 92 decouple: ``write -b`` skips ONLY the blank check, NOT the erase.

    Regression guard for the Phase-90/91 footgun: ``-b`` used to imply
    ``skip_erase=not blank_check``, so ``write -b`` on a non-blank
    electrically-erasable chip silently skipped the required erase (leaving
    un-erasable 0->1 bits while the firmware reported "successful"). After the
    decouple, ``-b`` no longer sets FLAG_SKIP_ERASE; ``--skip-erase`` is the
    explicit opt-in. FWBLANK-04 (Phase 205) retired the wire bit ``-b`` used
    to set; it now travels as write_eprom's `blank_check_requested` keyword
    instead of an operation_flags bit.
    """
    from firestarter.constants import FLAG_SKIP_ERASE

    operator = Mock(spec=EpromOperator)
    operator.write_eprom.return_value = True

    # `write -b`: blank-check skipped, erase NOT skipped (the decouple).
    app = make_app_context(eprom_operator=operator)
    r = runner.invoke(cli, ["write", "W27C512", "in.bin", "-b"], obj=app)
    assert r.exit_code == 0
    kwargs = operator.write_eprom.call_args.kwargs
    assert kwargs["blank_check_requested"] is False
    assert not (kwargs["operation_flags"] & FLAG_SKIP_ERASE)

    # `write -b --skip-erase`: both skipped (explicit opt-in).
    operator.write_eprom.reset_mock()
    app2 = make_app_context(eprom_operator=operator)
    r2 = runner.invoke(
        cli, ["write", "W27C512", "in.bin", "-b", "--skip-erase"], obj=app2
    )
    assert r2.exit_code == 0
    kwargs2 = operator.write_eprom.call_args.kwargs
    assert kwargs2["blank_check_requested"] is False
    assert kwargs2["operation_flags"] & FLAG_SKIP_ERASE

    # plain `write`: neither skipped (erase runs, blank check runs).
    operator.write_eprom.reset_mock()
    app3 = make_app_context(eprom_operator=operator)
    r3 = runner.invoke(cli, ["write", "W27C512", "in.bin"], obj=app3)
    assert r3.exit_code == 0
    kwargs3 = operator.write_eprom.call_args.kwargs
    assert kwargs3["blank_check_requested"] is True
    assert not (kwargs3["operation_flags"] & FLAG_SKIP_ERASE)


def test_verify_happy_path(runner: CliRunner) -> None:
    """`firestarter verify W27C512 in.bin` exits 0 when verify_eprom returns 0.

    202-01 D-10: verify_eprom returns an int (0 match / 1 mismatch /
    2 transport-hardware-or-refusal); cli_handlers.verify now `sys.exit`s
    directly on that int.
    """
    operator = Mock(spec=EpromOperator)
    operator.verify_eprom.return_value = 0
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["verify", "W27C512", "in.bin"], obj=app)
    assert result.exit_code == 0


def test_verify_operator_returns_mismatch(runner: CliRunner) -> None:
    """`firestarter verify W27C512 in.bin` exits 1 when verify_eprom returns 1."""
    operator = Mock(spec=EpromOperator)
    operator.verify_eprom.return_value = 1
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["verify", "W27C512", "in.bin"], obj=app)
    assert result.exit_code == 1


def test_verify_operator_returns_setup_failure(runner: CliRunner) -> None:
    """`firestarter verify W27C512 in.bin` exits 2 when verify_eprom returns 2
    (D-10: transport, hardware, or a pre-wire region refusal)."""
    operator = Mock(spec=EpromOperator)
    operator.verify_eprom.return_value = 2
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["verify", "W27C512", "in.bin"], obj=app)
    assert result.exit_code == 2


def test_verify_cli_prints_range_line_and_bucket_summary_line(
    runner: CliRunner,
    make_comm,
    fake_serial,
    tmp_path,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Task 3 acceptance criterion: a `CliRunner` invocation of `verify`
    against a mismatching fake chip produces output containing both a
    range line (D-13) and the bucket summary line
    `CompareAccumulator.finalise()` now populates via `classify_streamed`
    (D-14, 202-03).

    Drives the REAL `EpromOperator.verify_eprom` (not a `Mock`) through the
    fake-serial harness `tests/test_eprom_operations.py`'s
    `TestVerifyEpromHostSideRead` uses, so the range/bucket lines
    `eprom_operations.verify_eprom` logs are genuinely produced by
    production code -- not read off a mock's return value. `resolve_chip`
    is patched to a minimal chip dict (mirroring
    `tests/test_eprom_operations.py::_MINIMAL_EPROM_DATA`) so this test
    does not also need a real chip's full bus-config from the shipped DB.

    Asserts against `caplog.text`, not `result.output`, for the measured
    reason `test_info_elevated_programming_vcc_warns` documents above: this
    test passes a pre-built `obj=app`, so `cli()`'s test-mode short-circuit
    skips `_setup_logging` entirely, and under pytest `result.output` is
    always empty regardless of what was logged -- asserting against it
    would pass vacuously. `caplog.at_level(logging.INFO, ...)` is the
    correct capture surface inside pytest for `logger.info` output, and it
    is still a genuine `CliRunner` invocation of production code end to end.
    """
    monkeypatch.setattr(
        cli_handlers_mod,
        "resolve_chip",
        lambda name, db=None: {"memory-size": 300, "flags": 0, "cmd": 1},
    )

    length = 300  # > 256 so the bucket line's clustering path is exercised
    expected_bytes = bytes(length)
    input_file = tmp_path / "in.bin"
    input_file.write_bytes(expected_bytes)

    actual_bytes = bytearray(expected_bytes)
    actual_bytes[5] = 0x01  # one mismatch: a range line + a bucket line

    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_DATA_CHUNK, bytes(actual_bytes)))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    def _fake_find_and_connect(command_dict, config, **kwargs):
        return make_comm()

    monkeypatch.setattr(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        _fake_find_and_connect,
    )

    operator = EpromOperator(ConfigManager())
    app = make_app_context(eprom_operator=operator)

    with caplog.at_level(logging.INFO, logger="EpromOperator"):
        result = runner.invoke(cli, ["verify", "W27C512", str(input_file)], obj=app)

    assert result.exit_code == 1
    assert "Mismatch 0x" in caplog.text
    assert " bad of " in caplog.text
    assert " compared of " in caplog.text


def test_blank_happy_path(runner: CliRunner) -> None:
    """`firestarter blank W27C512` exits 0 when check_eprom_blank returns 0.

    202-05 D-10: check_eprom_blank returns an int (0 blank / 1 not blank /
    2 transport-hardware-or-refusal); cli_handlers.blank now `sys.exit`s
    directly on that int, the same shape 202-01 gave `verify`.
    """
    operator = Mock(spec=EpromOperator)
    operator.check_eprom_blank.return_value = 0
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["blank", "W27C512"], obj=app)
    assert result.exit_code == 0


def test_blank_operator_returns_mismatch(runner: CliRunner) -> None:
    """`firestarter blank W27C512` exits 1 when check_eprom_blank returns 1."""
    operator = Mock(spec=EpromOperator)
    operator.check_eprom_blank.return_value = 1
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["blank", "W27C512"], obj=app)
    assert result.exit_code == 1


def test_blank_operator_returns_setup_failure(runner: CliRunner) -> None:
    """`firestarter blank W27C512` exits 2 when check_eprom_blank returns 2
    (D-10: transport, hardware, or a pre-wire region refusal)."""
    operator = Mock(spec=EpromOperator)
    operator.check_eprom_blank.return_value = 2
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["blank", "W27C512"], obj=app)
    assert result.exit_code == 2


# CMP-08 / D-17: region options and the two pre-wire refusals, shared by
# `verify` and `blank`.


def test_verify_refuses_size_larger_than_input_file_before_opening_the_port(
    runner: CliRunner, tmp_path
) -> None:
    """An explicit `--size` longer than the input file must be refused with
    exit 2, naming both the file's actual length and the requested size --
    and it must never open the serial connection."""
    input_file = tmp_path / "in.bin"
    input_file.write_bytes(b"\x01\x02\x03\x04")  # 4 bytes

    operator = Mock(spec=EpromOperator)
    app = make_app_context(eprom_operator=operator)

    with pytest.MonkeyPatch.context() as mp:
        connect_spy = Mock()
        mp.setattr(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect", connect_spy
        )
        result = runner.invoke(
            cli, ["verify", "W27C512", str(input_file), "-s", "64"], obj=app
        )

    assert result.exit_code == 2, result.output
    assert "4" in result.output
    assert "64" in result.output
    connect_spy.assert_not_called()
    operator.verify_eprom.assert_not_called()


@pytest.mark.parametrize("command", ["verify", "blank"])
def test_region_past_chip_end_is_refused_before_opening_the_port(
    runner: CliRunner, tmp_path, command: str
) -> None:
    """A start address + size (or, for `blank`, size alone) whose sum
    exceeds the chip's declared size must be refused with exit 2, naming
    the chip's declared size -- and must never open the serial connection.
    `-a 0x10000 -s 1` against W27C512 (a 65536-byte / 0x10000 chip) starts
    exactly at the chip's end, so even one byte overruns it.
    """
    operator = Mock(spec=EpromOperator)
    app = make_app_context(eprom_operator=operator)

    args = [command, "W27C512"]
    if command == "verify":
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(b"\x01")
        args.append(str(input_file))
    args += ["-a", "0x10000", "-s", "1"]

    with pytest.MonkeyPatch.context() as mp:
        connect_spy = Mock()
        mp.setattr(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect", connect_spy
        )
        result = runner.invoke(cli, args, obj=app)

    assert result.exit_code == 2, result.output
    assert "10000" in result.output.upper() or "65536" in result.output
    connect_spy.assert_not_called()
    operator.verify_eprom.assert_not_called()
    operator.check_eprom_blank.assert_not_called()


# CR-01 (202-05 code review): `-a` alone (no `--size`) against `blank` used to
# skip the past-chip-end check entirely -- `_region_refusal_exit_code` left
# `length` as `None` whenever both `--size` and `input_file` were absent, on
# the reasoning that blank's whole-chip default has "nothing to bound". That
# reasoning only holds when `start == 0`; with `-a` given and no `-s`, the
# declared region is "the rest of the chip from `addr` onward", a real,
# boundable length. These legs are the mirror image of
# `test_region_past_chip_end_is_refused_before_opening_the_port` above,
# which only ever supplied `-a` AND `-s` together and so never exercised
# this path.


@pytest.mark.parametrize("command", ["verify", "blank"])
def test_address_alone_past_chip_end_is_refused_before_opening_the_port(
    runner: CliRunner, tmp_path, command: str
) -> None:
    """`-a <past-end-address>` with NO `--size` must still be refused with
    exit 2 and must never open the serial connection -- CR-01's exact
    repro, generalised to both commands. W27C512 is 65536 (0x10000) bytes;
    `-a 0x10001` is one byte past its end."""
    operator = Mock(spec=EpromOperator)
    app = make_app_context(eprom_operator=operator)

    args = [command, "W27C512"]
    if command == "verify":
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(b"\x01")
        args.append(str(input_file))
    args += ["-a", "0x10001"]

    with pytest.MonkeyPatch.context() as mp:
        connect_spy = Mock()
        mp.setattr(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect", connect_spy
        )
        result = runner.invoke(cli, args, obj=app)

    assert result.exit_code == 2, result.output
    assert "10001" in result.output.upper() or "65536" in result.output
    connect_spy.assert_not_called()
    operator.verify_eprom.assert_not_called()
    operator.check_eprom_blank.assert_not_called()


@pytest.mark.parametrize("command", ["verify", "blank"])
def test_address_alone_exactly_at_chip_end_is_refused(
    runner: CliRunner, tmp_path, command: str
) -> None:
    """`-a <memory-size>` exactly, with no `--size`, is past the last
    addressable byte (valid addresses are `[0, memory-size)`) and must be
    refused -- the explicit boundary decision CR-01 records. W27C512 is
    65536 (0x10000) bytes, so `-a 0x10000` starts exactly at its end."""
    operator = Mock(spec=EpromOperator)
    app = make_app_context(eprom_operator=operator)

    args = [command, "W27C512"]
    if command == "verify":
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(b"\x01")
        args.append(str(input_file))
    args += ["-a", "0x10000"]

    with pytest.MonkeyPatch.context() as mp:
        connect_spy = Mock()
        mp.setattr(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect", connect_spy
        )
        result = runner.invoke(cli, args, obj=app)

    assert result.exit_code == 2, result.output
    connect_spy.assert_not_called()
    operator.verify_eprom.assert_not_called()
    operator.check_eprom_blank.assert_not_called()


def test_blank_address_alone_at_last_valid_byte_is_not_refused(
    runner: CliRunner,
) -> None:
    """The negative control CR-01 requires: `-a <memory-size - 1>` with no
    `--size` is the LAST valid byte (one-byte region ending exactly at the
    chip's declared size) and must NOT be refused -- proving the new guard
    doesn't satisfy itself by refusing everything. W27C512 is 65536
    (0x10000) bytes, so `-a 0xFFFF` is its last valid address."""
    operator = Mock(spec=EpromOperator)
    operator.check_eprom_blank.return_value = 0
    app = make_app_context(eprom_operator=operator)

    result = runner.invoke(cli, ["blank", "W27C512", "-a", "0xFFFF"], obj=app)

    assert result.exit_code == 0, result.output
    operator.check_eprom_blank.assert_called_once()


def test_region_scoped_verify_composes_a_command_dict_bounding_exact_region(
    runner: CliRunner, make_comm, fake_serial, tmp_path, monkeypatch
) -> None:
    """A region-scoped `verify` run composes a command dict whose start
    address and end address (`address` + `memory-size`) bound exactly the
    requested region -- proof that `-a`/`-s` genuinely reach the wire."""
    monkeypatch.setattr(
        cli_handlers_mod,
        "resolve_chip",
        lambda name, db=None: {"memory-size": 4096, "flags": 0, "cmd": 1},
    )

    payload = b"\xaa\xbb\xcc\xdd"
    input_file = tmp_path / "in.bin"
    input_file.write_bytes(payload)

    command_dicts: list[dict] = []

    def _fake_find_and_connect(command_dict, config, **kwargs):
        command_dicts.append(dict(command_dict))
        return make_comm()

    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_DATA_CHUNK, payload))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    monkeypatch.setattr(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        _fake_find_and_connect,
    )

    operator = EpromOperator(ConfigManager())
    app = make_app_context(eprom_operator=operator)

    result = runner.invoke(
        cli,
        ["verify", "W27C512", str(input_file), "-a", "0x100", "-s", "4"],
        obj=app,
    )

    assert result.exit_code == 0, result.output
    assert command_dicts, "find_and_connect was never called"
    cd = command_dicts[0]
    assert cd["address"] == 0x100
    assert cd["memory-size"] == 0x100 + 4


def test_map_typed_errors_never_exits_the_process_directly() -> None:
    """D-11's scope bound, proven structurally: `map_typed_errors`'s own
    source contains no direct process exit and no assignment overriding a
    Click exception's exit code, so every path through it still exits 1."""
    import inspect

    source = inspect.getsource(cli_handlers_mod.map_typed_errors)
    assert "sys.exit" not in source
    assert "os._exit" not in source
    assert "exit_code" not in source


def test_verify_and_blank_docstrings_name_all_three_exit_codes() -> None:
    """Both command docstrings must state the three exit codes -- this is
    what `--help` prints, so an operator reading it sees the contract.
    `.help` (not `.__doc__`, which reads Click's `Command` class docstring
    on the wrapped `click.Command` object) is Click's own parsed rendering
    of the function's docstring."""
    for cmd in (cli_handlers_mod.verify, cli_handlers_mod.blank):
        doc = cmd.help or ""
        assert "0" in doc
        assert "1" in doc
        assert "2" in doc


# Task 3: the exit-code matrix, with two distinct routes to 2 per command,
# each distinguished from a Click usage error (which also exits 2, D-10's
# accepted cost) by the message it prints.


def test_map_typed_errors_still_exits_one_for_a_third_command(
    runner: CliRunner,
) -> None:
    """D-11's scope bound, proven behaviourally (not just by source
    inspection): `map_typed_errors` must still map a typed error to exit 1
    for a command other than `verify`/`blank`, proving the decorator was
    not widened to exit 2 for chip-op commands generally."""
    from firestarter.exceptions import EpromOperationError

    operator = Mock(spec=EpromOperator)
    operator.erase_eprom.side_effect = EpromOperationError("simulated hardware fault")
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["erase", "W27C512"], obj=app)
    assert result.exit_code == 1, result.output


@pytest.mark.parametrize("command", ["verify", "blank"])
def test_service_setup_failure_route_to_exit_2_names_its_own_message(
    runner: CliRunner, tmp_path, caplog: pytest.LogCaptureFixture, command: str
) -> None:
    """The FIRST route to exit 2: a real `EpromOperator` setup failure
    (a transport error before any command reaches the wire). Its own
    logged message is what distinguishes it from a Click usage error, which
    never reaches `EpromOperator`'s logger at all. Drives a REAL operator
    (not a `Mock`) so the message asserted is genuinely service-emitted,
    not read off a mock's configured return value.
    """
    from firestarter.exceptions import SerialError

    args = [command, "W27C512"]
    if command == "verify":
        input_file = tmp_path / "in.bin"
        input_file.write_bytes(b"\x01\x02\x03\x04")
        args.append(str(input_file))

    operator = EpromOperator(ConfigManager())
    app = make_app_context(eprom_operator=operator)

    with (
        caplog.at_level(logging.ERROR, logger="EpromOperator"),
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setattr(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            Mock(side_effect=SerialError("no board attached")),
        )
        result = runner.invoke(cli, args, obj=app)

    assert result.exit_code == 2, result.output
    assert "Usage:" not in result.output
    assert "Failed to setup operation" in caplog.text


@pytest.mark.parametrize("command", ["verify", "blank"])
def test_usage_error_also_exits_2_but_never_reaches_the_operator(
    runner: CliRunner, command: str
) -> None:
    """Click's own `UsageError` also exits 2 (D-10's accepted cost) -- but
    it prints a "Usage:" banner and never reaches the operator at all, so
    it is never confused with a genuine transport/hardware/region verdict."""
    operator = Mock(spec=EpromOperator)
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, [command, "--not-a-real-flag"], obj=app)
    assert result.exit_code == 2, result.output
    assert "Usage:" in result.output
    operator.verify_eprom.assert_not_called()
    operator.check_eprom_blank.assert_not_called()


def test_erase_happy_path(runner: CliRunner) -> None:
    """`firestarter erase W27C512` exits 0 when erase_eprom returns True."""
    operator = Mock(spec=EpromOperator)
    operator.erase_eprom.return_value = True
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["erase", "W27C512"], obj=app)
    assert result.exit_code == 0


def test_erase_blank_check_polarity(runner: CliRunner) -> None:
    """TRAP #3 / D-13.3 (historical): erase ``-b/--blank-check`` polarity is
    the inverse of write's ``--no-blank-check`` — both coexist verbatim.

    FWBLANK-04 (Phase 205 Plan 04) retired the wire bit this test used to
    observe on `operation_flags` -- `_build_op_flags` no longer composes
    any such bit for either command, with `-b` present or absent.
    `erase()`'s own post-erase check (D-01/D-02, Phase 205 Plan 01) is
    driven entirely by the CLI's own `blank_check` local variable calling
    `check_eprom_blank` directly, never through a flag on the erase
    command's wire frame; that dispatch has its own coverage elsewhere.
    This leg is re-anchored to assert the wire-composition invariant
    directly: no flag distinguishes the two invocations any more.
    """
    operator = Mock(spec=EpromOperator)
    operator.erase_eprom.return_value = True

    app = make_app_context(eprom_operator=operator)
    runner.invoke(cli, ["erase", "W27C512"], obj=app)
    _, kwargs = operator.erase_eprom.call_args
    flags_without_b = kwargs["operation_flags"]

    operator.erase_eprom.reset_mock()
    app2 = make_app_context(eprom_operator=operator)
    runner.invoke(cli, ["erase", "W27C512", "-b"], obj=app2)
    _, kwargs2 = operator.erase_eprom.call_args
    flags_with_b = kwargs2["operation_flags"]

    assert flags_without_b == flags_with_b, (
        "erase's operation_flags must be identical with and without -b -- "
        "the retired skip-blank-check bit no longer distinguishes them"
    )


def test_erase_operator_returns_false(runner: CliRunner) -> None:
    """`firestarter erase W27C512` exits 1 when erase_eprom returns False."""
    operator = Mock(spec=EpromOperator)
    operator.erase_eprom.return_value = False
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["erase", "W27C512"], obj=app)
    assert result.exit_code == 1


# 205-01 / D-01/D-02/D-03: `erase -b` gains a host-side post-erase blank
# check through Phase 202's `check_eprom_blank`, with a 0/1/2 exit contract.
# `check_eprom_blank` is the "fake operator" surface here, exactly like the
# `blank` command's own tests above -- the CLI layer's job is only to call
# it and `sys.exit` on its verdict with no mapping layer, so these legs pin
# that wiring without exercising the real serial-I/O engine.


def test_erase_blank_check_exits_zero_when_the_part_reads_blank(
    runner: CliRunner,
) -> None:
    """`erase -b`, erase succeeds, `check_eprom_blank` returns 0 (all
    blank): exits 0, and the check actually ran."""
    operator = Mock(spec=EpromOperator)
    operator.erase_eprom.return_value = True
    operator.check_eprom_blank.return_value = 0
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["erase", "W27C512", "-b"], obj=app)
    assert result.exit_code == 0
    operator.check_eprom_blank.assert_called_once()


def test_erase_blank_check_exits_one_when_the_part_is_not_blank(
    runner: CliRunner,
) -> None:
    """`erase -b`, erase succeeds, `check_eprom_blank` returns 1 (at least
    one non-blank byte): exits 1 -- distinct from a transport/setup
    failure (exit 2, next leg)."""
    operator = Mock(spec=EpromOperator)
    operator.erase_eprom.return_value = True
    operator.check_eprom_blank.return_value = 1
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["erase", "W27C512", "-b"], obj=app)
    assert result.exit_code == 1


def test_erase_blank_check_exits_two_when_the_check_itself_fails(
    runner: CliRunner,
) -> None:
    """`erase -b`, erase succeeds, `check_eprom_blank` returns 2 (transport,
    hardware or setup failure): exits 2. D-02: never folded into the 0/1
    chip verdict -- the defect already filed against `dev test`'s blank
    step this plan refuses to reproduce."""
    operator = Mock(spec=EpromOperator)
    operator.erase_eprom.return_value = True
    operator.check_eprom_blank.return_value = 2
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["erase", "W27C512", "-b"], obj=app)
    assert result.exit_code == 2


def test_erase_blank_check_prints_exactly_one_line_on_a_failed_check(
    runner: CliRunner, caplog: pytest.LogCaptureFixture
) -> None:
    """D-03: a failed post-erase check prints exactly one line -- asserted
    by the caplog record count, not a substring, so a future regression
    that stacks a second diagnostic line on top of `check_eprom_blank`'s
    own is caught.

    Per the measured fact documented on
    `test_info_elevated_programming_vcc_warns` above, `result.output` is
    always `''` for logging-based output under pytest (pytest's own root
    handler suppresses the `logging.lastResort` stderr fallback `CliRunner`
    would otherwise pick up), so this asserts against `caplog.records`,
    the correct capture surface here. The mock's `side_effect` reproduces
    exactly the one `logger.error(...)` call the real `check_eprom_blank`
    makes on a non-blank verdict, so this leg proves the CLI layer adds no
    second line of its own -- it does not re-prove `check_eprom_blank`'s
    own message shape, which is already covered in
    `tests/test_eprom_operations.py`.
    """
    op_logger = logging.getLogger("EpromOperator")

    def _one_error_line_then_not_blank(*args: object, **kwargs: object) -> int:
        op_logger.error("Blank check for W27C512 failed.")
        return 1

    operator = Mock(spec=EpromOperator)
    operator.erase_eprom.return_value = True
    operator.check_eprom_blank.side_effect = _one_error_line_then_not_blank
    app = make_app_context(eprom_operator=operator)

    with caplog.at_level(logging.ERROR, logger="EpromOperator"):
        result = runner.invoke(cli, ["erase", "W27C512", "-b"], obj=app)

    assert result.exit_code == 1
    assert len(caplog.records) == 1, caplog.records


def test_erase_without_blank_check_opens_no_second_port_and_keeps_zero_one(
    runner: CliRunner,
) -> None:
    """Plain `erase` (no `-b`) is unchanged: exactly one port open (the
    erase itself, via `erase_eprom`), no call into `check_eprom_blank`, and
    the existing 0/1 exit codes for both the success and failure case."""
    operator = Mock(spec=EpromOperator)
    operator.erase_eprom.return_value = True
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["erase", "W27C512"], obj=app)
    assert result.exit_code == 0
    operator.erase_eprom.assert_called_once()
    operator.check_eprom_blank.assert_not_called()

    operator.erase_eprom.reset_mock()
    operator.erase_eprom.return_value = False
    result = runner.invoke(cli, ["erase", "W27C512"], obj=app)
    assert result.exit_code == 1
    operator.erase_eprom.assert_called_once()
    operator.check_eprom_blank.assert_not_called()


def test_erase_blank_check_does_not_run_when_the_erase_failed(
    runner: CliRunner,
) -> None:
    """`erase -b` where `erase_eprom` returns False: exits 1 and the blank
    check never runs, so no second port is opened (Fork D) -- a not-blank
    verdict for a part that was never erased would be a fabricated claim
    about silicon, the same shape D-02 exists to refuse."""
    operator = Mock(spec=EpromOperator)
    operator.erase_eprom.return_value = False
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["erase", "W27C512", "-b"], obj=app)
    assert result.exit_code == 1
    operator.check_eprom_blank.assert_not_called()


def test_erase_has_no_full_option(runner: CliRunner) -> None:
    """D-03: `erase` gains no `--full` option; the documented escape hatch
    is `firestarter blank <chip> --full`. Click's own `UsageError` refuses
    it before the operator is ever reached."""
    operator = Mock(spec=EpromOperator)
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["erase", "W27C512", "--full"], obj=app)
    assert result.exit_code == 2, result.output
    assert "Usage:" in result.output
    operator.erase_eprom.assert_not_called()


# 205-01 / OQ-1: `erase -s <addr> -b` is refused before the erase runs.
# Measured hazard (205-RESEARCH.md): on protocol 0x06, a non-zero
# `handle->address` selects a SECTOR erase in `flash_nor_unlock_erase_execute`,
# not the whole-device erase D-01's host-side check assumes. An unconditional
# whole-device check after a sector erase would report the untouched
# remainder as non-blank -- a reliable false negative -- so the combination
# is refused in the CLI tier, before any port opens.


def test_erase_sector_address_with_blank_check_is_refused_before_the_erase(
    runner: CliRunner,
) -> None:
    """`erase -s 0x10000 -b` exits 2, prints exactly one line -- the full
    refusal sentence, not a substring -- and never erases or opens a port."""
    operator = Mock(spec=EpromOperator)
    app = make_app_context(eprom_operator=operator)

    with pytest.MonkeyPatch.context() as mp:
        connect_spy = Mock()
        mp.setattr(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect", connect_spy
        )
        result = runner.invoke(
            cli, ["erase", "W27C512", "-s", "0x10000", "-b"], obj=app
        )

    assert result.exit_code == 2, result.output
    expected = cli_handlers_mod._ERASE_SECTOR_BLANK_REFUSAL.format(eprom="W27C512")
    assert result.output.strip() == expected
    connect_spy.assert_not_called()
    operator.erase_eprom.assert_not_called()
    operator.check_eprom_blank.assert_not_called()


def test_erase_sector_address_without_blank_check_still_runs(
    runner: CliRunner,
) -> None:
    """`-s` alone (no `-b`) is unaffected by the OQ-1 refusal: the sector
    erase still runs exactly as it does today, with the sector address
    reaching `erase_eprom` unchanged."""
    operator = Mock(spec=EpromOperator)
    operator.erase_eprom.return_value = True
    app = make_app_context(eprom_operator=operator)
    result = runner.invoke(cli, ["erase", "W27C512", "-s", "0x10000"], obj=app)
    assert result.exit_code == 0
    operator.erase_eprom.assert_called_once()
    _, kwargs = operator.erase_eprom.call_args
    assert kwargs["address_str"] == "0x10000"


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


# Voltage commands (vpp, vpe)


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


# Hardware commands (hw, config)


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


# dev group + 4 sub-commands


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


# RED smoke tests for dev write-cycle + dev fault-inject
#
# All four tests MUST FAIL until 53-02 registers the subcommands. Click will
# report "No such command 'write-cycle'" / "No such command 'fault-inject'",
# producing exit code 2 (usage error) instead of the expected 0 or 2 (hw-error).


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


# Log output from EpromConsolePresenter goes through the logging subsystem.
# In-process CliRunner tests capture it via pytest caplog (at WARNING level)
# since the AppContext short-circuit skips _setup_logging in the CLI group.


def test_info_non_supported_shows_status(runner: CliRunner, caplog) -> None:
    """`firestarter info AT28C04` shows "Support status" and "adapter" in log output.

    DB-04 SC#1: non-supported chips must render a status-specific line in info.
    AT28C04 is adapter-required (24-pin 5V EEPROM). Exit must be 0 — info displays,
    never refuses. Log captured via caplog (WARNING level).

    Re-anchored from M2716 in Phase 79: M2716 graduated to 'supported' (NMOS-02),
    so the 'vpp-exceeds-max' category is empty — AT28C04 is the still-non-supported
    exemplar for the info status-line contract.
    """
    import logging

    db = EpromDatabase(skip_local_override=True)
    app = make_app_context(db=db, eprom_presenter=EpromConsolePresenter(db))
    with caplog.at_level(logging.WARNING, logger="EpromConsolePresenter"):
        result = runner.invoke(cli, ["info", "AT28C04"], obj=app)
    assert result.exit_code == 0
    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "Support status" in log_text
    assert "adapter" in log_text.lower()


def test_info_adapter_required_shows_status(runner: CliRunner, caplog) -> None:
    """`firestarter info AT28C16` shows "Support status" and "adapter" in log output.

    DB-04 SC#1: adapter-required status must be surfaced in the info display.
    AT28C16 is a 24-pin 5V EEPROM that needs a dedicated DIP24 adapter.
    Exit must be 0 — info does not refuse adapter-required chips.
    """
    import logging

    db = EpromDatabase(skip_local_override=True)
    app = make_app_context(db=db, eprom_presenter=EpromConsolePresenter(db))
    with caplog.at_level(logging.WARNING, logger="EpromConsolePresenter"):
        result = runner.invoke(cli, ["info", "AT28C16"], obj=app)
    assert result.exit_code == 0
    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "Support status" in log_text
    assert "adapter" in log_text.lower()


def test_info_protocol_not_impl_shows_status(runner: CliRunner, caplog) -> None:
    """`firestarter info X88C64P` shows "Support status" and "not implemented" in log output.

    DB-04 SC#1: protocol-not-implemented status must be surfaced in info display.
    X88C64P uses protocol 0x34 (XICOR NovRAM) which is not implemented.
    Exit must be 0 — info does not refuse protocol-not-implemented chips.
    """
    import logging

    db = EpromDatabase(skip_local_override=True)
    app = make_app_context(db=db, eprom_presenter=EpromConsolePresenter(db))
    with caplog.at_level(logging.WARNING, logger="EpromConsolePresenter"):
        result = runner.invoke(cli, ["info", "X88C64P"], obj=app)
    assert result.exit_code == 0
    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "Support status" in log_text
    assert "not implemented" in log_text.lower()


# Each test asserts: exit 1, status-specific text in output, no traceback,
# no generic "Chip not usable:" prefix (Approach A — reason string verbatim).
# Guard fires before resolve_chip/convert_to_programmer → no serial I/O.


def test_read_non_supported_status_refusal(runner: CliRunner) -> None:
    """`firestarter read AT28C04 out.bin` exits 1 with the status reason verbatim.

    DB-04 SC#2/#4: the reason string is rendered directly (no "Chip not usable:"
    prefix). The guard fires in resolve_chip before any serial byte. AT28C04 is
    adapter-required.

    Re-anchored from M2716 in Phase 79: M2716 graduated to 'supported' (NMOS-02),
    so the 'vpp-exceeds-max' category is empty — AT28C04 is the still-non-supported
    exemplar for the verbatim-reason refusal contract.
    """
    app = make_app_context()
    result = runner.invoke(cli, ["read", "AT28C04", "out.bin"], obj=app)
    assert result.exit_code == 1
    assert "adapter" in result.output.lower()
    assert "Chip not usable:" not in result.output
    assert "Traceback (most recent call last)" not in result.output


def test_read_adapter_required_status_refusal(runner: CliRunner) -> None:
    """`firestarter read AT28C16 out.bin` exits 1 with adapter-required reason verbatim.

    DB-04 SC#2/#4 adapter-required: AT28C16 is a 24-pin 5V EEPROM that requires
    a DIP24 adapter. Reason string rendered directly (no "Chip not usable:" prefix).
    Guard fires in resolve_chip before any serial byte.
    """
    app = make_app_context()
    result = runner.invoke(cli, ["read", "AT28C16", "out.bin"], obj=app)
    assert result.exit_code == 1
    assert "adapter" in result.output.lower()
    assert "Chip not usable:" not in result.output
    assert "Traceback (most recent call last)" not in result.output


def test_read_protocol_not_implemented_status_refusal(runner: CliRunner) -> None:
    """`firestarter read X88C64P out.bin` exits 1 with protocol-not-implemented reason verbatim.

    DB-04 SC#2/#4 protocol-not-implemented: X88C64P uses protocol 0x34 (XICOR
    NovRAM). Reason string rendered directly (no "Chip not usable:" prefix).
    Guard fires in resolve_chip before any serial byte.
    """
    app = make_app_context()
    result = runner.invoke(cli, ["read", "X88C64P", "out.bin"], obj=app)
    assert result.exit_code == 1
    assert "not implemented" in result.output.lower()
    assert "Chip not usable:" not in result.output
    assert "Traceback (most recent call last)" not in result.output
