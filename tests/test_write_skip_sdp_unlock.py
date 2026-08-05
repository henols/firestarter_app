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

from .conftest import _FakeSerial, build_frame
from .conftest import make_app_context as _make_app_context

# --- Concrete chip names, drawn from 120-SDP-PARTITION.md section 3, mirroring
# test_sdp_honesty.py's fixture chips so both suites exercise the same
# ground-truth entries. ---

# FRAM -- capability-refused, support_status == "supported" (not adapter-required).
_FRAM_CHIP = "FM28V020"
# Pre-SDP DIP24_2816 generation -- capability-refused, support_status == "supported".
_PRESDP_DIP2816_CHIP = "2816"
# Non-0x0D chip -- wrong-protocol, no SDP surface at all.
_NON_0X0D_CHIP = "w27c512"
# Allowed 0x0D chip -- capability-allowed, reaches a normal write.
_ALLOWED_CHIP = "AT28C256"


def make_app_context(
    *,
    db: EpromDatabase | Mock | None = None,
    config_manager: ConfigManager | Mock | None = None,
    eprom_operator: EpromOperator | Mock | None = None,
    hardware_manager: HardwareManager | Mock | None = None,
    firmware_manager: FirmwareManager | Mock | None = None,
    eprom_presenter: EpromConsolePresenter | Mock | None = None,
) -> AppContext:
    """Typed local delegate onto tests/conftest.py's shared factory (Phase 132
    Plan 05, RETIRE-05, D-10) -- preserves this module's one non-default
    behaviour: `eprom_operator` defaults to a REAL `EpromOperator`, not a
    Mock, because this suite proves the flags bit reaches the composed wire
    command_dict, which only a real EpromOperator composes.

    Ordering is load-bearing: `config_manager` must be resolved to a
    concrete value BEFORE `eprom_operator` is built from it (the real
    operator's constructor takes the config manager) -- reversing this
    order was the source of this module's now-fixed seventh mypy error
    (`Argument 1 to "EpromOperator" has incompatible type "object"`).
    Everything else forwards to the shared factory unchanged; the type work
    itself now happens there, not here.
    """
    if config_manager is None:
        config_manager = ConfigManager()
    if eprom_operator is None:
        eprom_operator = EpromOperator(config_manager)
    return _make_app_context(
        db=db,
        config_manager=config_manager,
        eprom_operator=eprom_operator,
        hardware_manager=hardware_manager,
        firmware_manager=firmware_manager,
        eprom_presenter=eprom_presenter,
    )


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _fresh_serial_and_comm():
    """Build an independent (fake_serial, make_comm) pair.

    Mirrors conftest.py's `fake_serial`/`make_comm` fixtures exactly, but as
    a plain function rather than a fixture pair -- needed only by the
    two-leg RETIRE-07 tripwire test below, which drives two full,
    independent `write` invocations in one test function. The
    fixture-injected pair cannot be reused for a second drive: the first
    drive's `SerialCommunicator` closes its fake serial port
    (`_FakeSerial.close()` sets `is_open = False`) at the end of a
    successful write, so a second `_drive_write` call sharing that same
    fake_serial fails with "Not connected" -- this is what a same-fixture
    two-drive test measures instead of the intended behaviour.
    """
    from firestarter.serial_comm import SerialCommunicator

    serial = _FakeSerial()

    def _factory():
        instance = SerialCommunicator.__new__(SerialCommunicator)
        instance.connection = serial
        instance.port_name = "/dev/null"
        instance.baud_rate = 250000
        instance.timeout = 0.1
        instance.programmer_info = None
        instance._fault_inject_outgoing = None
        instance.firmware_buffer_size = None
        instance.firmware_max_chunk = None
        instance.seen_message_ids = set()
        return instance

    return serial, _factory


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


# ---------------------------------------------------------------------------
# RETIRE-07 / D-14 tripwire -- the named test whose failure IS the record
# ---------------------------------------------------------------------------


def test_dev_sdp_removal_is_safe_only_because_auto_unlock_is_default_on(
    runner: CliRunner, tmp_path, make_comm, fake_serial
) -> None:
    """RETIRE-07 / D-14 tripwire.

    Phase 132 deleted the standalone `firestarter dev sdp` subcommand
    (RETIRE-01). That deletion was safe ONLY BECAUSE the host auto-unlocks
    every capability-refused protocol-0x0D part by default on every `write`
    -- no user needs a manual unlock surface as long as this stays true.

    This test pins that default-on behaviour. If the auto-set default is
    ever flipped -- the `_build_op_flags` `skip_sdp_unlock=False` default,
    the `--skip-sdp-unlock` Click option's `default=False`, or the D-04
    auto-set condition itself in `cli_handlers.py`'s `write()` -- this test
    fails, and the RETIRE-01 removal decision must be revisited alongside
    whatever change broke it.

    The companion comments recording the same dependency live at the
    decision site (the D-04 auto-set condition inside `write()` in
    `firestarter/cli_handlers.py`) and at the flag's definition
    (`FLAG_SKIP_SDP_UNLOCK` in `firestarter/constants.py`).

    Two legs, both required:
      1. A capability-refused 0x0D part with NO flag passed gets the skip
         bit auto-set on the wire, and the mandatory report line fires --
         this is the dependency itself, asserted as behaviour.
      2. A capability-ALLOWED 0x0D part with NO flag does NOT get the bit
         set -- proving the auto-set is conditional, not blanket. Without
         this leg the test would still pass under a blanket unconditional
         set, which is a different and worse behaviour than the one
         RETIRE-01's argument actually depends on.
    """
    refused_result, refused_captured = _drive_write(
        runner, _FRAM_CHIP, tmp_path, make_comm, fake_serial
    )
    assert refused_result.exit_code == 0, refused_result.output
    assert refused_captured["command_dict"]["flags"] & FLAG_SKIP_SDP_UNLOCK, (
        "RETIRE-07/D-14 TRIPWIRE FIRED: a capability-refused protocol-0x0D "
        "part did NOT get FLAG_SKIP_SDP_UNLOCK auto-set on a plain write. "
        "The host's SDP auto-unlock is no longer default-on for this case, "
        "which is the removal-safety argument RETIRE-01 (Phase 132, deleting "
        "`firestarter dev sdp`) rests on -- that removal decision must be "
        "revisited alongside whatever change broke this."
    )
    assert "auto-setting --skip-sdp-unlock on your behalf" in refused_result.output, (
        "RETIRE-07/D-14 TRIPWIRE FIRED: the mandatory auto-set report line "
        "did not appear for a capability-refused protocol-0x0D part on a "
        "plain write -- see the docstring of this test and the D-04 "
        "auto-set comment in cli_handlers.py's write()."
    )

    # A second, independent (fake_serial, make_comm) pair -- the fixture-
    # injected pair used above is already closed by the drive that just
    # completed. See _fresh_serial_and_comm's docstring.
    allowed_serial, allowed_comm = _fresh_serial_and_comm()
    allowed_result, allowed_captured = _drive_write(
        runner, _ALLOWED_CHIP, tmp_path, allowed_comm, allowed_serial
    )
    assert allowed_result.exit_code == 0, allowed_result.output
    assert not (allowed_captured["command_dict"]["flags"] & FLAG_SKIP_SDP_UNLOCK), (
        "RETIRE-07/D-14 TRIPWIRE FIRED (discriminating leg): a capability-"
        "ALLOWED protocol-0x0D part got FLAG_SKIP_SDP_UNLOCK auto-set on a "
        "plain write. The host's SDP auto-unlock is no longer conditional -- "
        "it looks blanket/unconditional instead, which is a DIFFERENT and "
        "WORSE behaviour than the one RETIRE-01's removal argument depends "
        "on. Revisit RETIRE-01 alongside whatever change broke this."
    )
