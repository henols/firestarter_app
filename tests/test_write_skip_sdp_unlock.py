"""Hardware-free contract tests for `write --skip-sdp-unlock` (Phase 120 Plan 09).

v1.22 HOST-02 / HOST-04. `HOST-01`..`HOST-04` already appear elsewhere in this
tree from v1.20 (a different, unrelated requirement axis), so every test
docstring in this module spells its marker `v1.22 HOST-NN` to disambiguate.

Every leg drives `write` end to end through `click.testing.CliRunner`, using a
REAL `EpromOperator` wired to a fake serial port (`make_comm`/`fake_serial`
from `conftest.py`) rather than a `Mock(spec=EpromOperator)`. Each leg asserts
the **emitted `flags` value** captured at the transport seam --
`SerialCommunicator.find_and_connect`'s `command_dict` argument -- because a
function-level assertion on `build_flags`'s return value alone would not
prove the bit actually reaches the composed wire command.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from firestarter.cli_handlers import AppContext, cli
from firestarter.config import ConfigManager
from firestarter.constants import FLAG_SKIP_SDP_UNLOCK
from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.eprom_operations import EpromOperator
from firestarter.firmware import FirmwareManager
from firestarter.hardware import HardwareManager
from firestarter.messages import (
    MSG_END_DONE,
    MSG_INIT_DONE,
    MSG_MAIN_DONE,
    MSG_OK_REQ_DATA,
    MSG_WARN_SDP_UNLOCK_SKIPPED,
)

from .conftest import build_frame

# --- Concrete chip names, drawn from 120-SDP-PARTITION.md section 3, mirroring
# test_dev_sdp_cmd.py's fixture chips so both suites exercise the same
# ground-truth entries. ---

# FRAM -- capability-refused, support_status == "supported" (not adapter-required).
_FRAM_CHIP = "FM28V020"
# Pre-SDP DIP24_2816 generation -- capability-refused, support_status == "supported".
_PRESDP_DIP2816_CHIP = "2816"
# Non-0x0D chip -- wrong-protocol, no SDP surface at all.
_NON_0X0D_CHIP = "w27c512"
# Allowed 0x0D chip -- capability-allowed, reaches a normal write.
_ALLOWED_CHIP = "AT28C256"


def make_app_context(**overrides: object) -> AppContext:
    """Construct an AppContext with a REAL EpromOperator + EpromDatabase.

    Mirrors test_dev_sdp_cmd.py's make_app_context shape, but defaults
    eprom_operator to a real `EpromOperator` (not a Mock) because this suite
    proves the flags bit reaches the composed wire command_dict, which only
    a real EpromOperator composes.
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
        eprom_operator=overrides.pop("eprom_operator", EpromOperator(config_manager)),
        hardware_manager=overrides.pop("hardware_manager", Mock(spec=HardwareManager)),
        firmware_manager=overrides.pop("firmware_manager", Mock(spec=FirmwareManager)),
        eprom_presenter=overrides.pop(
            "eprom_presenter", Mock(spec=EpromConsolePresenter)
        ),
    )


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _drive_write(
    runner: CliRunner,
    chip: str,
    tmp_path,
    make_comm,
    fake_serial,
    extra_args: list[str] | None = None,
):
    """Invoke `firestarter write <chip> <file> [extra_args]` end to end.

    Drives a full, successful write through a REAL EpromOperator wired to a
    fake serial port -- INIT_DONE -> OK_REQ_DATA (one data block requested) ->
    MAIN_DONE -> END_DONE, the same happy-path frame sequence
    `test_characterization.py::test_write_happy_path` pins for `write` --
    while patching `SerialCommunicator.find_and_connect` to capture the
    composed `command_dict` at the exact point it would cross onto the wire.

    Returns (result, captured) where `captured["command_dict"]` is the dict
    passed to `find_and_connect`.

    Plan 120-10 / D-15 note: on a protocol-0x0D chip, real (post-Phase-119)
    firmware emits MSG_WARN_SDP_UNLOCK_SKIPPED (0x86) as part of this exact
    frame sequence whenever FLAG_SKIP_SDP_UNLOCK reaches it -- write_eprom's
    new D-15 check (120-10) requires that ack when the bit was set on a
    0x0D chip, so this fixture feeds it for every 0x0D chip driven here
    (whether or not the bit ends up set) to keep the simulated stream
    faithful to real firmware; it is a no-op for the bit-not-set legs since
    the D-15 check only runs when the bit is set. The WARN frame is fed
    BEFORE INIT_DONE (inside the INIT phase window) rather than between
    INIT_DONE and OK_REQ_DATA: _main_phase_send_data's tight MAIN-phase
    request/response loop only tolerates MAIN/ERROR/OK-request-chunk
    responses and raises on an interleaved WARN, whereas the INIT phase's
    loop routes WARN through _handle_progress_response harmlessly.
    """
    input_file = tmp_path / f"{chip}.bin"
    input_file.write_bytes(b"\x01\x02\x03\x04")

    from firestarter.chip_resolver import resolve_chip
    from firestarter.sdp_capability import SDP_PROTOCOL_ID

    db = EpromDatabase(skip_local_override=True)
    is_protocol_0x0d = resolve_chip(chip, db=db).get("algorithm") == SDP_PROTOCOL_ID

    if is_protocol_0x0d:
        fake_serial.feed(build_frame(MSG_WARN_SDP_UNLOCK_SKIPPED, b""))
    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_OK_REQ_DATA, b""))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    captured: dict = {}

    def _fake_find_and_connect(command_dict, config, **kwargs):
        captured["command_dict"] = command_dict
        return make_comm()

    app = make_app_context()
    args = ["write", chip, str(input_file), *(extra_args or [])]
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=_fake_find_and_connect,
    ):
        result = runner.invoke(cli, args, obj=app)
    return result, captured


# ---------------------------------------------------------------------------
# Explicit flag
# ---------------------------------------------------------------------------


def test_explicit_flag_sets_bit_0x100_on_the_wire(
    runner: CliRunner, tmp_path, make_comm, fake_serial
) -> None:
    """v1.22 HOST-02: `write <allowed-0x0D-chip> <file> --skip-sdp-unlock`
    reaches the wire with bit 0x100 set on the composed command_dict."""
    result, captured = _drive_write(
        runner,
        _ALLOWED_CHIP,
        tmp_path,
        make_comm,
        fake_serial,
        extra_args=["--skip-sdp-unlock"],
    )
    assert result.exit_code == 0, result.output
    assert captured["command_dict"]["flags"] & FLAG_SKIP_SDP_UNLOCK


# ---------------------------------------------------------------------------
# Allowed 0x0D part, no flag -- auto-set must be scoped, not blanket
# ---------------------------------------------------------------------------


def test_no_flag_on_an_allowed_0x0d_part_emits_no_skip_bit_and_no_auto_set_line(
    runner: CliRunner, tmp_path, make_comm, fake_serial
) -> None:
    """v1.22 HOST-04: an allowed 0x0D part with no flag gets bit 0x100 clear
    and no auto-set report line -- proving the D-04 auto-set is scoped to
    capability-refused parts rather than applied unconditionally."""
    result, captured = _drive_write(
        runner, _ALLOWED_CHIP, tmp_path, make_comm, fake_serial
    )
    assert result.exit_code == 0, result.output
    assert not (captured["command_dict"]["flags"] & FLAG_SKIP_SDP_UNLOCK)
    assert "auto-setting --skip-sdp-unlock" not in result.output


# ---------------------------------------------------------------------------
# Refused 0x0D part, no flag -- D-04 auto-set with a mandatory report line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chip", [_FRAM_CHIP, _PRESDP_DIP2816_CHIP])
def test_refused_0x0d_part_gets_the_bit_auto_set_with_an_unconditional_report_line(
    runner: CliRunner, tmp_path, make_comm, fake_serial, chip: str
) -> None:
    """v1.22 HOST-04: a capability-refused 0x0D part (one FRAM part, one
    pre-SDP DIP24_2816 part) gets FLAG_SKIP_SDP_UNLOCK auto-set with an
    unconditional, default-visible report line -- run WITHOUT -v so this leg
    proves default visibility, not verbosity-gated visibility."""
    result, captured = _drive_write(runner, chip, tmp_path, make_comm, fake_serial)
    assert result.exit_code == 0, result.output
    assert captured["command_dict"]["flags"] & FLAG_SKIP_SDP_UNLOCK
    assert chip.upper() in result.output
    assert "auto-setting --skip-sdp-unlock on your behalf" in result.output


# ---------------------------------------------------------------------------
# Refused 0x0D part, user already passed the flag -- no duplicate line
# ---------------------------------------------------------------------------


def test_auto_set_line_is_not_duplicated_when_the_user_passed_the_flag(
    runner: CliRunner, tmp_path, make_comm, fake_serial
) -> None:
    """v1.22 HOST-04: when the user already passed --skip-sdp-unlock on a
    refused part, the bit is set (as requested) but the auto-set report line
    does NOT appear -- the host did not decide anything in this case, so a
    duplicate "on your behalf" line would misreport what happened."""
    result, captured = _drive_write(
        runner,
        _FRAM_CHIP,
        tmp_path,
        make_comm,
        fake_serial,
        extra_args=["--skip-sdp-unlock"],
    )
    assert result.exit_code == 0, result.output
    assert captured["command_dict"]["flags"] & FLAG_SKIP_SDP_UNLOCK
    assert "auto-setting --skip-sdp-unlock on your behalf" not in result.output


# ---------------------------------------------------------------------------
# Non-0x0D chip, flag passed -- D-18 warn-and-proceed
# ---------------------------------------------------------------------------


def test_non_0x0d_chip_with_the_flag_warns_and_proceeds(
    runner: CliRunner, tmp_path, make_comm, fake_serial
) -> None:
    """v1.22 HOST-02 / D-18: --skip-sdp-unlock on a non-0x0D chip warns that
    the flag has no effect on this protocol, still emits the bit (so a
    blanket-flag script across a mixed batch produces identical wire
    frames), and the write still runs to a normal successful exit code."""
    result, captured = _drive_write(
        runner,
        _NON_0X0D_CHIP,
        tmp_path,
        make_comm,
        fake_serial,
        extra_args=["--skip-sdp-unlock"],
    )
    assert result.exit_code == 0, result.output
    assert "has no effect on this chip's protocol" in result.output
    assert captured["command_dict"]["flags"] & FLAG_SKIP_SDP_UNLOCK


def test_non_0x0d_chip_without_the_flag_is_unchanged(
    runner: CliRunner, tmp_path, make_comm, fake_serial
) -> None:
    """v1.22 HOST-02 / D-18: a non-0x0D chip with no flag gets no warning, no
    auto-set line, and bit 0x100 clear -- byte-identical to before this plan."""
    result, captured = _drive_write(
        runner, _NON_0X0D_CHIP, tmp_path, make_comm, fake_serial
    )
    assert result.exit_code == 0, result.output
    assert "has no effect on this chip's protocol" not in result.output
    assert "auto-setting --skip-sdp-unlock" not in result.output
    assert not (captured["command_dict"]["flags"] & FLAG_SKIP_SDP_UNLOCK)
