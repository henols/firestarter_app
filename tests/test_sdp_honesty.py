"""CliRunner tests for `dev sdp` subcommand (Phase 120 Plan 08).

Hardware-free proof of the `firestarter dev sdp <chip> <enable|disable>`
wiring: no real serial port or bench access is opened anywhere in this
module -- every manager on `AppContext` is `Mock(spec=...)` (except the one
dedicated real-operator leg) and `EpromDatabase` is constructed with
`skip_local_override=True`. TTY-gating is controlled by patching the
module-level `firestarter.cli_handlers._is_interactive` function directly
(NOT `sys.stdin.isatty`) because `click.testing.CliRunner.invoke` replaces
`sys.stdin` with its own stream for the duration of the call, so a
`patch("sys.stdin.isatty", ...)` applied before `invoke()` silently does not
survive (documented in cli_handlers.py's `_is_interactive` docstring).

FALSE-GREEN TRAP (v1.22 HOST-01): an exit-code-only refusal test is a known
false-green here. An absent chip, a capability refusal and a support-status
refusal all exit non-zero identically, so only "no confirm shown", "no port
opened" and the **reason text** distinguish gate order from gate presence --
exit code alone never proves *which* gate fired, or that it fired *before*
the confirm/serial call rather than after. This is the same lesson as
`dev test`'s absent-chip work (114.1), where the load-bearing assertion was
`read_hardware_revision_value.assert_not_called()`, not the exit code.
"""

from __future__ import annotations

import re
import struct
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from firestarter.cli_handlers import AppContext, cli
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import EpromOperationError
from firestarter.firmware import FirmwareManager
from firestarter.hardware import HardwareManager
from firestarter.messages import (
    MSG_END_DONE,
    MSG_ERR_UNKNOWN_CMD,
    MSG_INIT_DONE,
    MSG_MAIN_DONE,
    MSG_WARN_SDP_TBLC_EXCEEDED,
)

from .conftest import build_frame

# --- Concrete chip names, drawn from 120-SDP-PARTITION.md section 3 ---

# Absent from the DB entirely -- Gate 1 (SAFE-04).
_ABSENT_CHIP = "NO_SUCH_CHIP_XYZ"
# FRAM -- capability-refused, support_status == "supported" (not adapter-required).
_FRAM_CHIP = "FM28V020"
# Pre-SDP DIP24_2816 generation -- capability-refused, support_status == "supported".
_PRESDP_DIP2816_CHIP = "2816"
# Non-0x0D chip -- wrong-protocol refusal.
_NON_0X0D_CHIP = "w27c512"
# Allowed 0x0D chip -- reaches the confirm/serial gates.
_ALLOWED_CHIP = "AT28C256"

# All nine `adapter-required` 0x0D parts (first alias token of each; see
# 120-SDP-PARTITION.md section 3's REFUSE table). D-08's capability-before-
# support-status ordering is load-bearing on every one of these.
_ADAPTER_REQUIRED_CHIPS = [
    "28C04A",
    "28C04AF",
    "28C16A",
    "28C16AF",
    "AT28C04",
    "AT28C04E",
    "AT28C16",
    "AT28C16E",
    "UPD28C04",
]


def make_app_context(**overrides: object) -> AppContext:
    """Construct a minimal, hardware-free AppContext for `dev sdp` tests.

    Mirrors test_dev_test_cmd.py's make_app_context: EpromDatabase uses
    skip_local_override=True and every manager is Mock(spec=...) unless the
    caller overrides it. No real serial port or bench access is ever opened.
    """
    db = overrides.pop("db", None)
    if db is None:
        db = EpromDatabase(skip_local_override=True)
    config_manager = overrides.pop("config_manager", None)
    if config_manager is None:
        config_manager = ConfigManager()
    return AppContext(
        db=db,
        config_manager=config_manager,
        eprom_operator=overrides.pop("eprom_operator", Mock(spec=EpromOperator)),
        hardware_manager=overrides.pop("hardware_manager", Mock(spec=HardwareManager)),
        firmware_manager=overrides.pop("firmware_manager", Mock(spec=FirmwareManager)),
        eprom_presenter=overrides.pop(
            "eprom_presenter", Mock(spec=EpromConsolePresenter)
        ),
    )


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _off_tty():
    """Context manager forcing the off-TTY branch (D-06)."""
    return patch("firestarter.cli_handlers._is_interactive", return_value=False)


def _on_tty():
    """Context manager forcing the on-TTY branch."""
    return patch("firestarter.cli_handlers._is_interactive", return_value=True)


# ---------------------------------------------------------------------------
# Surface shape
# ---------------------------------------------------------------------------


def test_surface_is_chip_then_mode_with_a_yes_flag(runner: CliRunner) -> None:
    """v1.22 HOST-01: `dev sdp --help` shows the locked surface -- chip
    argument before mode argument, enable/disable both offered, -y present,
    and no --destructive-style mode flag (D-05)."""
    result = runner.invoke(cli, ["dev", "sdp", "--help"])
    assert result.exit_code == 0, result.output
    usage_line = next(
        line for line in result.output.splitlines() if line.startswith("Usage:")
    )
    assert "EPROM" in usage_line
    assert "{enable|disable}" in usage_line or "MODE" in usage_line
    assert usage_line.index("EPROM") < usage_line.index("enable") or (
        "MODE" in usage_line and usage_line.index("EPROM") < usage_line.index("MODE")
    )
    assert "enable" in result.output
    assert "disable" in result.output
    assert "-y" in result.output or "--yes" in result.output
    assert "--destructive" not in result.output


# ---------------------------------------------------------------------------
# Gate order: absent chip, capability, wrong-protocol -- all three legs
# assert no-confirm + no-port-opened + reason text, never exit code alone.
# ---------------------------------------------------------------------------


def test_gate_order_absent_chip_refuses_before_confirm_and_before_serial(
    runner: CliRunner,
) -> None:
    """v1.22 HOST-01: an absent chip hard-fails at Gate 1, before the confirm
    and before any port is opened -- proven by three independent assertions,
    never by exit code alone (the false-green trap named at module level)."""
    operator = Mock(spec=EpromOperator)
    app = make_app_context(eprom_operator=operator)
    with (
        _on_tty(),
        patch("firestarter.cli_handlers.Confirm") as mock_confirm,
        patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect"
        ) as mock_find_and_connect,
    ):
        result = runner.invoke(cli, ["dev", "sdp", _ABSENT_CHIP, "enable"], obj=app)
    assert result.exit_code != 0, result.output
    assert f"{_ABSENT_CHIP}: not found in database" in result.output
    mock_confirm.ask.assert_not_called()
    mock_find_and_connect.assert_not_called()
    operator.sdp_lock.assert_not_called()
    operator.sdp_unlock.assert_not_called()


@pytest.mark.parametrize(
    "chip,reason_fragment",
    [
        (_FRAM_CHIP, "ferroelectric RAM (FRAM)"),
        (_PRESDP_DIP2816_CHIP, "not on the SDP-capable list"),
    ],
)
def test_gate_order_capability_refusal_refuses_before_confirm_and_before_serial(
    runner: CliRunner, chip: str, reason_fragment: str
) -> None:
    """v1.22 HOST-01: a capability-refused chip (one FRAM part, one pre-SDP
    DIP24_2816 part -- both support_status == "supported") is refused at
    Gate 2, before the confirm and before any port is opened. The reason
    text pins WHICH gate fired, not merely that exit code was non-zero."""
    operator = Mock(spec=EpromOperator)
    app = make_app_context(eprom_operator=operator)
    with (
        _on_tty(),
        patch("firestarter.cli_handlers.Confirm") as mock_confirm,
        patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect"
        ) as mock_find_and_connect,
    ):
        result = runner.invoke(cli, ["dev", "sdp", chip, "enable"], obj=app)
    assert result.exit_code != 0, result.output
    assert reason_fragment in result.output, result.output
    mock_confirm.ask.assert_not_called()
    mock_find_and_connect.assert_not_called()
    operator.sdp_lock.assert_not_called()
    operator.sdp_unlock.assert_not_called()


@pytest.mark.parametrize("chip", _ADAPTER_REQUIRED_CHIPS)
def test_adapter_required_part_hears_the_capability_reason_not_the_adapter_reason(
    runner: CliRunner, chip: str
) -> None:
    """v1.22 HOST-01: D-08's stated purpose -- an adapter-required 0x0D part
    with no SDP command decoder must hear "this part has no SDP" rather than
    "get an adapter", because no adapter would have helped. All NINE
    adapter-required 0x0D parts are exercised here, not a hypothetical
    subset, so this ordering is proven load-bearing on the whole population."""
    operator = Mock(spec=EpromOperator)
    app = make_app_context(eprom_operator=operator)
    with (
        _on_tty(),
        patch("firestarter.cli_handlers.Confirm") as mock_confirm,
        patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect"
        ) as mock_find_and_connect,
    ):
        result = runner.invoke(cli, ["dev", "sdp", chip, "enable"], obj=app)
    assert result.exit_code != 0, result.output
    assert "not on the SDP-capable list" in result.output, result.output
    assert "adapter" not in result.output.lower(), result.output
    mock_confirm.ask.assert_not_called()
    mock_find_and_connect.assert_not_called()
    operator.sdp_lock.assert_not_called()
    operator.sdp_unlock.assert_not_called()


def test_non_0x0d_chip_is_refused_with_the_wrong_protocol_reason(
    runner: CliRunner,
) -> None:
    """v1.22 HOST-01: a chip on a protocol other than 0x0D (here W27C512,
    protocol 0x07) is refused at Gate 2 with the wrong-protocol reason, and
    again no confirm is shown and no port is opened."""
    operator = Mock(spec=EpromOperator)
    app = make_app_context(eprom_operator=operator)
    with (
        _on_tty(),
        patch("firestarter.cli_handlers.Confirm") as mock_confirm,
        patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect"
        ) as mock_find_and_connect,
    ):
        result = runner.invoke(cli, ["dev", "sdp", _NON_0X0D_CHIP, "enable"], obj=app)
    assert result.exit_code != 0, result.output
    assert "SDP lock/unlock applies only to protocol 0x0D" in result.output, (
        result.output
    )
    mock_confirm.ask.assert_not_called()
    mock_find_and_connect.assert_not_called()
    operator.sdp_lock.assert_not_called()
    operator.sdp_unlock.assert_not_called()


# ---------------------------------------------------------------------------
# Consent matrix (Gate 4, D-05/D-06/D-07)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case,interactive,assume_yes,confirm_return,expect_exit_zero,expect_operator_called,expect_confirm_called",  # noqa: E501
    [
        ("tty_no_yes_accept", True, False, True, True, True, True),
        ("tty_no_yes_decline", True, False, False, True, False, True),
        ("off_tty_no_yes", False, False, None, False, False, False),
        ("off_tty_with_yes", False, True, None, True, True, False),
    ],
)
def test_consent_matrix(
    runner: CliRunner,
    case: str,
    interactive: bool,
    assume_yes: bool,
    confirm_return: bool | None,
    expect_exit_zero: bool,
    expect_operator_called: bool,
    expect_confirm_called: bool,
) -> None:
    """v1.22 HOST-01: the four consent-gate cells. D-06's inversion is the
    load-bearing fact here: `dev test` proceeds off-TTY because
    `--destructive` itself is its consent signal; `dev sdp` has no such flag,
    so the mere absence of a TTY cannot stand in for consent and MUST refuse
    (case off_tty_no_yes) rather than proceed."""
    operator = Mock(spec=EpromOperator)
    operator.sdp_lock.return_value = True
    operator.sdp_unlock.return_value = True
    app = make_app_context(eprom_operator=operator)

    args = ["dev", "sdp", _ALLOWED_CHIP, "enable"]
    if assume_yes:
        args.append("-y")

    with (
        patch("firestarter.cli_handlers._is_interactive", return_value=interactive),
        patch("firestarter.cli_handlers.Confirm") as mock_confirm,
        patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect"
        ) as mock_find_and_connect,
    ):
        if confirm_return is not None:
            mock_confirm.ask.return_value = confirm_return
        result = runner.invoke(cli, args, obj=app)

    if expect_exit_zero:
        assert result.exit_code == 0, result.output
    else:
        assert result.exit_code != 0, result.output
        assert "-y" in result.output, result.output

    if expect_confirm_called:
        mock_confirm.ask.assert_called_once()
    else:
        mock_confirm.ask.assert_not_called()

    if expect_operator_called:
        operator.sdp_lock.assert_called_once()
    else:
        operator.sdp_lock.assert_not_called()
        mock_find_and_connect.assert_not_called()


def test_enable_and_disable_share_one_gate_with_different_text(
    runner: CliRunner,
) -> None:
    """v1.22 HOST-01: D-07 -- one confirm gate, two strings. `enable`'s
    prompt warns about writes being refused until explicitly unlocked AND
    that the result cannot be read back; `disable`'s prompt warns that write
    protection is being removed."""
    operator = Mock(spec=EpromOperator)
    app = make_app_context(eprom_operator=operator)

    with (
        _on_tty(),
        patch("firestarter.cli_handlers.Confirm") as mock_confirm,
    ):
        mock_confirm.ask.return_value = False
        runner.invoke(cli, ["dev", "sdp", _ALLOWED_CHIP, "enable"], obj=app)
        enable_prompt = mock_confirm.ask.call_args[0][0]

    with (
        _on_tty(),
        patch("firestarter.cli_handlers.Confirm") as mock_confirm2,
    ):
        mock_confirm2.ask.return_value = False
        runner.invoke(cli, ["dev", "sdp", _ALLOWED_CHIP, "disable"], obj=app)
        disable_prompt = mock_confirm2.ask.call_args[0][0]

    assert "refuse writes" in enable_prompt.lower()
    assert "cannot be read back" in enable_prompt.lower()
    assert "removes its write" in disable_prompt.lower() or (
        "removes" in disable_prompt.lower() and "protection" in disable_prompt.lower()
    )
    assert enable_prompt != disable_prompt


# ---------------------------------------------------------------------------
# No port opened -- proven with a REAL operator, not a Mock (a Mock only
# proves the handler did not delegate; the real operator + transport patch
# proves no port was actually opened).
# ---------------------------------------------------------------------------


def test_no_port_opened_on_any_refusal_with_a_real_operator(runner: CliRunner) -> None:
    """v1.22 HOST-01: a Mock(spec=EpromOperator) proves the handler did not
    delegate to the operator -- it does NOT prove no port was opened, since
    a mock never touches the transport layer at all. This leg uses a REAL
    `EpromOperator` with `SerialCommunicator.find_and_connect` patched, so
    the `assert_not_called()` below is a genuine transport-level proof."""
    real_operator = EpromOperator(ConfigManager())
    app = make_app_context(eprom_operator=real_operator)
    with (
        _on_tty(),
        patch("firestarter.cli_handlers.Confirm") as mock_confirm,
        patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect"
        ) as mock_find_and_connect,
    ):
        result = runner.invoke(cli, ["dev", "sdp", _FRAM_CHIP, "enable"], obj=app)
    assert result.exit_code != 0, result.output
    mock_confirm.ask.assert_not_called()
    mock_find_and_connect.assert_not_called()


# ---------------------------------------------------------------------------
# Report honesty (D-10) + exit-code contract (D-11) + firmware-too-old (D-14)
# ---------------------------------------------------------------------------


def test_summary_line_carries_the_unreadable_state_caveat_on_both_directions(
    runner: CliRunner,
) -> None:
    """v1.22 HOST-05: symmetry matters because firmware's `0x5F`
    (`MSG_INFO_SDP_UNLOCK_DONE_US`) frame carries no honesty caveat where
    `0x61` (`MSG_INFO_SDP_LOCK_DONE_US`) does (F-120-03) -- so the host
    summary line is the ONLY carrier of the caveat on the unlock direction.
    The catalog fix itself is deferred to Phase 121/122; this test pins the
    host-side symmetry that stands in for it until then."""
    operator = Mock(spec=EpromOperator)
    operator.sdp_lock.return_value = True
    operator.sdp_unlock.return_value = True
    app = make_app_context(eprom_operator=operator)

    with _off_tty():
        enable_result = runner.invoke(
            cli, ["dev", "sdp", _ALLOWED_CHIP, "enable", "-y"], obj=app
        )
        disable_result = runner.invoke(
            cli, ["dev", "sdp", _ALLOWED_CHIP, "disable", "-y"], obj=app
        )

    assert enable_result.exit_code == 0, enable_result.output
    assert disable_result.exit_code == 0, disable_result.output
    assert "cannot be read back" in enable_result.output
    assert "cannot be read back" in disable_result.output


def test_summary_line_carries_no_duration_figure(runner: CliRunner) -> None:
    """v1.22 HOST-05/D-10: the host summary line itself contains no
    microsecond unit and no digit-plus-unit duration token. Scoped to the
    summary line specifically (not the whole captured output, which may
    legitimately contain a firmware `0x5F`/`0x61` frame carrying the real
    figure -- this test only asserts about the host's OWN line).

    This is mechanically enforced, not merely a discipline:
    `get_response()` filters the entire INFO band (`NON_RESPONSE_PREFIXES =
    ["INFO", "DEBUG"]`) out at `serial_comm.py:424`, so the operation layer
    literally cannot see the firmware's duration frame to plumb a figure
    through even if someone tried."""
    operator = Mock(spec=EpromOperator)
    operator.sdp_lock.return_value = True
    app = make_app_context(eprom_operator=operator)

    with _off_tty():
        result = runner.invoke(
            cli, ["dev", "sdp", _ALLOWED_CHIP, "enable", "-y"], obj=app
        )
    assert result.exit_code == 0, result.output

    summary_line = next(
        line for line in result.output.splitlines() if "was emitted" in line
    )
    assert not re.search(r"\d+\s*(us|µs|ms|s)\b", summary_line, re.IGNORECASE), (
        summary_line
    )


def test_no_fabricated_lock_state_boolean_in_the_report(runner: CliRunner) -> None:
    """v1.22 HOST-05: the outcome sentence is framed as "the sequence was
    emitted" plus the caveat -- a positive framing assertion, not a brittle
    forbidden-substring word-list, so this leg does not rot as wording
    evolves. This is HOST-05's honesty floor: the host-side application of
    Phase 117 D-05 / Phase 118 D-02 / Phase 119 D-12 -- honesty in the
    message text, never in a status a caller could misread as a state
    claim."""
    operator = Mock(spec=EpromOperator)
    operator.sdp_lock.return_value = True
    app = make_app_context(eprom_operator=operator)

    with _off_tty():
        result = runner.invoke(
            cli, ["dev", "sdp", _ALLOWED_CHIP, "enable", "-y"], obj=app
        )
    assert result.exit_code == 0, result.output

    summary_line = next(
        line for line in result.output.splitlines() if "was emitted" in line
    )
    assert "was emitted" in summary_line
    assert "cannot be read back" in summary_line
    assert "not a claim about the chip's actual state" in summary_line


def test_tblc_warn_prints_at_warning_and_exit_code_stays_zero(
    runner: CliRunner, make_comm, fake_serial
) -> None:
    """v1.22 HOST-05/D-11: since the protection state is unreadable either
    way, no exit code can honestly encode more than "the sequence was
    emitted" -- a `MSG_WARN_SDP_TBLC_EXCEEDED` (0x87) frame prints at
    WARNING and does NOT change the exit code away from 0."""

    def _fake_find_and_connect(command_dict, config, **kwargs):
        return make_comm()

    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_WARN_SDP_TBLC_EXCEEDED, struct.pack(">I", 650)))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    real_operator = EpromOperator(ConfigManager())
    app = make_app_context(eprom_operator=real_operator)

    with (
        _off_tty(),
        patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ),
    ):
        result = runner.invoke(
            cli, ["dev", "sdp", _ALLOWED_CHIP, "enable", "-y"], obj=app
        )

    assert "t_BLC budget" in result.output or "TBLC" in result.output.upper()
    assert result.exit_code == 0, result.output


def test_firmware_too_old_is_reported_when_unknown_cmd_comes_back(
    runner: CliRunner,
) -> None:
    """v1.22 HOST-05/D-14: D-14 keys on the message **id**, not the text.
    This is the command half of HOST-06's asymmetry -- an unknown COMMAND
    produces an error and is detectable, whereas an unknown flag BIT
    produces silence, which is why the flag half needs plan 120-09's ack
    requirement instead."""
    operator = Mock(spec=EpromOperator)
    operator.sdp_lock.side_effect = EpromOperationError(
        "Unknown command: 9", error_code=MSG_ERR_UNKNOWN_CMD
    )
    app = make_app_context(eprom_operator=operator)

    with _off_tty():
        result = runner.invoke(
            cli, ["dev", "sdp", _ALLOWED_CHIP, "enable", "-y"], obj=app
        )

    assert result.exit_code != 0, result.output
    assert "firestarter fw --install" in result.output, result.output
    assert "outdated" in result.output.lower() or "does not implement" in (
        result.output.lower()
    )


def test_success_exit_zero_and_failure_exit_one(runner: CliRunner) -> None:
    """v1.22 HOST-05/D-11: plain binary exit-code contract -- 0 on ok, 1 on
    not-ok, no tri-state introduced."""
    operator_ok = Mock(spec=EpromOperator)
    operator_ok.sdp_lock.return_value = True
    app_ok = make_app_context(eprom_operator=operator_ok)
    with _off_tty():
        result_ok = runner.invoke(
            cli, ["dev", "sdp", _ALLOWED_CHIP, "enable", "-y"], obj=app_ok
        )
    assert result_ok.exit_code == 0, result_ok.output

    operator_fail = Mock(spec=EpromOperator)
    operator_fail.sdp_lock.return_value = False
    app_fail = make_app_context(eprom_operator=operator_fail)
    with _off_tty():
        result_fail = runner.invoke(
            cli, ["dev", "sdp", _ALLOWED_CHIP, "enable", "-y"], obj=app_fail
        )
    assert result_fail.exit_code == 1, result_fail.output
