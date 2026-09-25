"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Shield-revision gate + extended MSG_OK_READY decode.

Chips whose bus-config routes VPP to bus line 11 (socket pin 21 on the
DIP24_2716 / DIP24_2532 pinouts) need the 3-position JP4 of RURP shield
Rev 2.2 or Rev 2.3. A write or an erase on an earlier shield is a chip-damage
path, so the host refuses it after the setup ack, unless --force is set.

Proved here:

  1. `hw_revision_gate` -- the pure policy. It is an ALLOWLIST and not a `>=`
     comparison: REVISION_UNKNOWN is 0xFE, numerically ABOVE REVISION_2_2
     (0x04), so a comparison would admit precisely the boards whose revision
     could not be determined. Only write and erase are gated, and --force
     turns the refusal into a warning.
  2. `_decode_id_frame` -- that the extended ack is parsed, that the legacy
     2-byte ack still yields its buffer size, and that a malformed length
     prefix degrades to "no identity" (reject) rather than a partial string.
  3. The gate's coupling to the REAL database -- that DIP24_2716 / DIP24_2532
     genuinely emit `vpp-pin: 11` and that no other pinout does.
  4. `EpromOperator._setup_operation` -- the refusal escapes both the cold and
     the leased connect as its own typed error and drops the link.
  5. The CLI -- `write` renders "Hardware error:" and exits 1, and
     `config --rev` takes the silkscreen number.
"""

import json
import struct
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner

from firestarter import hw_revision_gate
from firestarter.chip_resolver import resolve_chip
from firestarter.cli_handlers import AppContext, cli
from firestarter.config import ConfigManager
from firestarter.constants import (
    COMMAND_CHECK_CHIP_ID,
    COMMAND_ERASE,
    COMMAND_READ,
    COMMAND_WRITE,
    FLAG_FORCE,
    REVISION_0,
    REVISION_1,
    REVISION_2_0,
    REVISION_2_1,
    REVISION_2_2,
    REVISION_2_3,
    REVISION_UNKNOWN,
)
from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import (
    HardwareOperationError,
    HardwareRevisionUnsupportedError,
)
from firestarter.firmware import FirmwareManager
from firestarter.hardware import HardwareManager
from firestarter.messages import MSG_OK_READY
from firestarter.serial_comm import SerialCommunicator

GATED_VPP_LINE = 11
_DB_FILE = (
    Path(__file__).resolve().parent.parent
    / "firestarter"
    / "data"
    / "chip_database.json"
)


def _cmd(cmd, vpp_pin=GATED_VPP_LINE, flags=0):
    return {
        "cmd": cmd,
        "flags": flags,
        "bus-config": {"bus": [0, 1, 2], "vpp-pin": vpp_pin},
    }


GATED_WRITE = _cmd(COMMAND_WRITE)


def _require(command, detected, chip_name="2516"):
    return hw_revision_gate.require_supported_revision(chip_name, command, detected)


# 1. Pure policy

REFUSED_REVISIONS = [
    REVISION_0,
    REVISION_1,
    REVISION_2_0,
    REVISION_2_1,
    REVISION_UNKNOWN,
    0xFF,
    None,
]


@pytest.mark.parametrize("cmd", [COMMAND_WRITE, COMMAND_ERASE])
@pytest.mark.parametrize("allowed", [REVISION_2_2, REVISION_2_3])
def test_gated_write_and_erase_pass_on_rev_2_2_and_2_3(cmd, allowed):
    """Rev 2.2 and Rev 2.3 both carry the 3-position JP4, so both pass."""
    _require(_cmd(cmd), allowed)  # must not raise
    assert (
        hw_revision_gate.forced_warning("2516", _cmd(cmd, flags=FLAG_FORCE), allowed)
        is None
    )


@pytest.mark.parametrize("cmd", [COMMAND_WRITE, COMMAND_ERASE])
@pytest.mark.parametrize("refused", REFUSED_REVISIONS)
def test_gated_write_and_erase_refused_on_every_other_revision(cmd, refused):
    """Every value outside the allowlist is refused: the pre-2.2 revisions, the
    REVISION_2_0 ADC bucket (a real Rev 2.2 lands here until the operator
    writes the override), 0xFE, the 0xFF sentinel, and None (no revision)."""
    with pytest.raises(HardwareRevisionUnsupportedError) as exc_info:
        _require(_cmd(cmd), refused)
    assert exc_info.value.detected == refused


def test_revision_unknown_is_refused_despite_being_numerically_higher():
    """THE trap this gate exists to avoid: `detected >= REVISION_2_2` would
    ADMIT 0xFE, the board whose revision could not be determined."""
    assert REVISION_UNKNOWN > REVISION_2_2
    with pytest.raises(HardwareRevisionUnsupportedError):
        _require(GATED_WRITE, REVISION_UNKNOWN)


@pytest.mark.parametrize("cmd", [COMMAND_READ, COMMAND_CHECK_CHIP_ID, None])
@pytest.mark.parametrize("revision", REFUSED_REVISIONS)
def test_non_write_commands_pass_on_every_revision(cmd, revision):
    """read (which blank and verify also use), the chip-ID check and a bare
    state command are not gated, even for a VPP-on-pin-21 chip."""
    command = _cmd(cmd)
    assert hw_revision_gate.is_refused(command, revision) is False
    _require(command, revision)  # must not raise
    assert hw_revision_gate.forced_warning("2516", command, revision) is None


@pytest.mark.parametrize(
    "command",
    [
        _cmd(COMMAND_WRITE, vpp_pin=15),
        {"cmd": COMMAND_WRITE, "bus-config": {"bus": [0, 1]}},  # no vpp-pin
        {"cmd": COMMAND_WRITE},  # no bus-config
    ],
)
def test_ungated_chips_pass_on_any_revision(command):
    """Only VPP-on-line-11 chips are gated. Checked against the worst revision
    values, so a gate that widened to all chips fails here."""
    _require(command, REVISION_2_0)
    _require(command, None)


@pytest.mark.parametrize("refused", [REVISION_2_0, REVISION_UNKNOWN, None])
def test_force_turns_the_refusal_into_a_warning(refused):
    command = _cmd(COMMAND_WRITE, flags=FLAG_FORCE)
    _require(command, refused)  # must not raise
    warning = hw_revision_gate.forced_warning("2516", command, refused)
    assert warning is not None
    assert warning.startswith("WARNING: 2516: ")
    assert "--force is set, so the write continues" in warning


def test_no_warning_without_force():
    assert hw_revision_gate.forced_warning("2516", GATED_WRITE, REVISION_2_0) is None


def test_refusal_text_names_the_chip_the_revision_and_both_escapes():
    with pytest.raises(HardwareRevisionUnsupportedError) as exc_info:
        _require(_cmd(COMMAND_ERASE), REVISION_2_0, chip_name="tms2516")
    text = str(exc_info.value)
    assert text == hw_revision_gate._REFUSAL_FORMAT.format(
        chip_name="TMS2516", reported="Rev 2.0-class", operation="erase"
    )
    assert "firestarter config --rev 2.2" in text
    assert "--force" in text


def test_refusal_text_for_an_absent_revision():
    with pytest.raises(HardwareRevisionUnsupportedError) as exc_info:
        _require(GATED_WRITE, None)
    assert hw_revision_gate._NO_REVISION_TEXT in str(exc_info.value)


def test_refusal_text_for_an_unmapped_byte():
    with pytest.raises(HardwareRevisionUnsupportedError) as exc_info:
        _require(GATED_WRITE, 0xFF)
    assert "revision byte 0xFF" in str(exc_info.value)


def test_refusal_is_a_hardware_error_not_a_serial_error():
    """`_setup_operation` catches SerialError and degrades it to a failed
    connect. The refusal must not be one, or it is hidden again."""
    from firestarter.exceptions import SerialError

    assert issubclass(HardwareRevisionUnsupportedError, HardwareOperationError)
    assert not issubclass(HardwareRevisionUnsupportedError, SerialError)


# 2. Extended MSG_OK_READY decode


def _ready_body(params: bytes) -> bytes:
    """Build the `body` _decode_id_frame receives: [id][params][crc]."""
    from tests.conftest import _ref_crc8_ccitt

    payload = bytes([MSG_OK_READY]) + params
    return payload + bytes([_ref_crc8_ccitt(payload)])


def _cap02_params(buffer_size: int, revision: int, identity: str) -> bytes:
    raw = identity.encode("ascii")
    return struct.pack(">H", buffer_size) + bytes([revision, len(raw)]) + raw


def _cap03_params(
    buffer_size: int, revision: int, identity: str, budget_s: int
) -> bytes:
    """[buffer_size u16 BE][hw_revision u8][ver_len u8][ver bytes][write_budget_s u16 BE].

    Built by COMPOSING `_cap02_params` rather than duplicating its body, so
    the two fixtures cannot drift apart. Reproduces the firmware's documented
    wire layout verbatim -- 143-RESEARCH.md's "Example 2" -- and is the
    closest thing this repo has to a cross-repo wire-layout parity assertion;
    nothing else in either repo compares the two sides (RESEARCH Open
    Question 4 hands the standing gate to Phase 144 / TEST-07).
    """
    return _cap02_params(buffer_size, revision, identity) + struct.pack(">H", budget_s)


def test_decode_extended_ack_populates_all_three_fields(make_comm):
    comm = make_comm()
    body = _ready_body(_cap02_params(1024, REVISION_2_2, "3.0.0:leonardo"))
    comm._decode_id_frame(len(body), body)

    assert comm.firmware_max_chunk == 1024
    assert comm.hw_revision == REVISION_2_2
    assert comm.firmware_identity == "3.0.0:leonardo"


def test_decode_legacy_two_byte_ack_still_yields_buffer_size(make_comm):
    """Pre-CAP-02 firmware: buffer size decodes, the CAP-02 fields stay None."""
    comm = make_comm()
    body = _ready_body(struct.pack(">H", 512))
    comm._decode_id_frame(len(body), body)

    assert comm.firmware_max_chunk == 512
    assert comm.hw_revision is None
    assert comm.firmware_identity is None


def test_decode_truncated_version_prefix_leaves_identity_none(make_comm):
    """A length prefix claiming more bytes than are present must not yield a
    partial string -- the host would then gate on a mangled version. Identity
    stays None, which is a refuse."""
    comm = make_comm()
    # Claims 40 version bytes but supplies 3.
    body = _ready_body(struct.pack(">H", 512) + bytes([REVISION_2_2, 40]) + b"3.0")
    comm._decode_id_frame(len(body), body)

    assert comm.firmware_identity is None
    # The revision byte precedes the malformed prefix and is still trustworthy.
    assert comm.hw_revision == REVISION_2_2


def test_decode_implausible_buffer_size_is_clamped_away(make_comm):
    """The CAP-01 [1, 4096] plausibility clamp survives the widened length
    test -- an absurd advertised size must leave firmware_max_chunk unset so
    the 512 floor applies (T-55-06)."""
    comm = make_comm()
    body = _ready_body(_cap02_params(60000, REVISION_2_2, "3.0.0:uno"))
    comm._decode_id_frame(len(body), body)

    assert comm.firmware_max_chunk is None
    assert comm.hw_revision == REVISION_2_2


def test_decode_cap03_budget_at_short_identity_length(make_comm):
    """106 s is the real advertised figure for 0x0B at its modal 500 us pulse
    width on a 1024-byte block (143-RESEARCH.md's Budget Arithmetic table)."""
    comm = make_comm()
    body = _ready_body(_cap03_params(1024, REVISION_2_2, "3.0.0:uno", 106))
    comm._decode_id_frame(len(body), body)

    assert comm.firmware_max_chunk == 1024
    assert comm.hw_revision == REVISION_2_2
    assert comm.firmware_identity == "3.0.0:uno"
    assert comm.write_block_budget_s == 106


def test_decode_cap03_budget_at_long_identity_length_proves_ver_end_is_computed(
    make_comm,
):
    """This identity is five bytes longer than the short-identity case's
    ("3.0.0:leonardo" vs "3.0.0:uno"), so any FIXED-index decode of the
    budget field passes exactly one of these two cases and fails the other
    -- together they are D-08's named hazard, proved. 3358 s is the real
    advertised figure for 0x07 at its worst reachable --pulse-us width
    (65535 us) on a 1024-byte block."""
    comm = make_comm()
    body = _ready_body(_cap03_params(1024, REVISION_2_2, "3.0.0:leonardo", 3358))
    comm._decode_id_frame(len(body), body)

    assert comm.firmware_max_chunk == 1024
    assert comm.hw_revision == REVISION_2_2
    assert comm.firmware_identity == "3.0.0:leonardo"
    assert comm.write_block_budget_s == 3358


def test_decode_cap02_ack_without_a_budget_tail_leaves_the_budget_none(make_comm):
    """The REALISTIC absent-advertisement case: a released `beta` firmware
    has CAP-02 but not CAP-03 yet (a v1.31 build after CAP-02 is ported but
    before CAP-03 lands is the other reachable case). None must mean "use
    the safe default", never an error and never a refusal (D-10) -- exactly
    the posture Phase 54's reversed FirmwareOutdatedError established."""
    comm = make_comm()
    body = _ready_body(_cap02_params(1024, REVISION_2_2, "3.0.0:leonardo"))
    comm._decode_id_frame(len(body), body)

    assert comm.firmware_max_chunk == 1024
    assert comm.firmware_identity == "3.0.0:leonardo"
    assert comm.write_block_budget_s is None


def test_decode_legacy_two_byte_ack_leaves_both_identity_and_budget_none(make_comm):
    """This is exactly the shape the CURRENT v1.31 firmware branch emits
    (BF-1): a bare 2-byte MSG_OK_READY with no CAP-02 tail at all. That is
    why `_probe_port` refuses such a board today
    (`test_fwguard.py::test_absent_identity_refuses`), and CAP-03 is
    structurally unreachable on it -- offsets 2 and 3 belong to CAP-02,
    which never runs when there is no identity tail."""
    comm = make_comm()
    body = _ready_body(struct.pack(">H", 512))
    comm._decode_id_frame(len(body), body)

    assert comm.firmware_max_chunk == 512
    assert comm.firmware_identity is None
    assert comm.hw_revision is None
    assert comm.write_block_budget_s is None


def test_decode_implausible_cap03_budget_is_clamped_away(make_comm):
    """Mirrors CAP-01's [1, 4096] clamp and its T-55-06 precedent: a corrupt
    or hostile ack must not be able to install an unbounded host timeout.
    The boundary must be INCLUSIVE at WRITE_BUDGET_MAX_S (14400) or the
    clamp is off by one. A rejected OR truncated budget must not take the
    identity down with it -- only the budget field itself may degrade."""
    # Rejected: below the [1, N] floor.
    comm = make_comm()
    body = _ready_body(_cap03_params(1024, REVISION_2_2, "3.0.0:leonardo", 0))
    comm._decode_id_frame(len(body), body)
    assert comm.write_block_budget_s is None
    assert comm.firmware_identity == "3.0.0:leonardo"

    # Rejected: above WRITE_BUDGET_MAX_S (14400).
    comm = make_comm()
    body = _ready_body(_cap03_params(1024, REVISION_2_2, "3.0.0:leonardo", 65535))
    comm._decode_id_frame(len(body), body)
    assert comm.write_block_budget_s is None
    assert comm.firmware_identity == "3.0.0:leonardo"

    # Accepted: the ceiling itself is inclusive.
    comm = make_comm()
    body = _ready_body(_cap03_params(1024, REVISION_2_2, "3.0.0:leonardo", 14400))
    comm._decode_id_frame(len(body), body)
    assert comm.write_block_budget_s == 14400
    assert comm.firmware_identity == "3.0.0:leonardo"

    # Truncated: the params region ends ONE BYTE into the budget field. A
    # partial value is never trusted -- the field stays None, while the
    # identity (decoded earlier, from an unrelated span of params_bytes) is
    # unaffected.
    comm = make_comm()
    params = _cap02_params(1024, REVISION_2_2, "3.0.0:leonardo") + b"\x00"
    body = _ready_body(params)
    comm._decode_id_frame(len(body), body)
    assert comm.write_block_budget_s is None
    assert comm.firmware_identity == "3.0.0:leonardo"


# 3. Coupling to the real database


def test_exactly_two_pinouts_emit_the_gated_vpp_line():
    """Pins the gate to real data.

    If a pinout edit moves another layout onto bus line 11, or moves the 24-pin
    UV-EPROM layouts off it, this fails -- which is the point. The gate keys on
    a wire value, so its scope is defined by pinouts.json, not by this module.
    """
    db = EpromDatabase(skip_local_override=True)
    gated = set()
    for key in db.pin_maps:
        pin_count = int(key.split("_")[0].removeprefix("DIP"))
        bus_config = db.get_bus_config(pin_count, key)
        if bus_config and bus_config.get("vpp-pin") == GATED_VPP_LINE:
            gated.add(key)

    assert gated == {"DIP24_2716", "DIP24_2532"}


def test_exactly_16_database_rows_are_gated():
    """Exact count of the chips the gate covers (15 DIP24_2716 + TI 2532)."""
    data = json.loads(_DB_FILE.read_text())
    gated = [
        row["part_number"]
        for rows in data.values()
        for row in rows
        if row.get("pinout") in {"DIP24_2716", "DIP24_2532"}
    ]
    assert len(gated) == 16


# 4. EpromOperator._setup_operation


def _chip_2516():
    return resolve_chip("2516", db=EpromDatabase(skip_local_override=True))


def _mock_comm(revision):
    comm = MagicMock()
    comm.hw_revision = revision
    comm.firmware_max_chunk = 64
    comm.is_connected.return_value = True
    comm.setup_command.return_value = True
    return comm


def _cold_setup(revision, cmd=COMMAND_WRITE, flags=0):
    operator = EpromOperator(ConfigManager())
    comm = _mock_comm(revision)
    with patch.object(SerialCommunicator, "find_and_connect", return_value=comm):
        result = operator._setup_operation("2516", _chip_2516(), cmd, flags)
    return operator, comm, result


def test_real_2516_wire_dict_is_gated():
    command = _chip_2516()
    command["cmd"] = COMMAND_WRITE
    assert hw_revision_gate.is_refused(command, REVISION_2_0) is True


def test_cold_setup_refusal_propagates_and_drops_the_link():
    operator = EpromOperator(ConfigManager())
    comm = _mock_comm(REVISION_2_0)
    with patch.object(SerialCommunicator, "find_and_connect", return_value=comm):
        with pytest.raises(HardwareRevisionUnsupportedError) as exc_info:
            operator._setup_operation("2516", _chip_2516(), COMMAND_WRITE, 0)
    assert str(exc_info.value).startswith("2516: This chip needs VPP on chip pin 21.")
    comm.disconnect.assert_called_once()
    assert operator.comm is None


def test_cold_setup_passes_on_rev_2_2():
    operator, comm, (command_dict, buffer_size) = _cold_setup(REVISION_2_2)
    assert command_dict is not None and buffer_size == 64
    comm.disconnect.assert_not_called()


def test_cold_setup_read_is_not_gated():
    _, comm, (command_dict, _) = _cold_setup(REVISION_2_0, cmd=COMMAND_READ)
    assert command_dict is not None
    comm.disconnect.assert_not_called()


def test_cold_setup_forced_continues_with_warning_on_stderr(capsys):
    _, comm, (command_dict, _) = _cold_setup(REVISION_2_0, flags=FLAG_FORCE)
    assert command_dict is not None
    comm.disconnect.assert_not_called()
    err = capsys.readouterr().err
    assert err.startswith("WARNING: 2516: The programmer reports Rev 2.0-class")


def test_leased_setup_refusal_propagates_and_drops_the_link():
    operator = EpromOperator(ConfigManager())
    comm = _mock_comm(REVISION_2_0)
    operator.comm = comm
    operator._leased = True
    with pytest.raises(HardwareRevisionUnsupportedError):
        operator._setup_operation("2516", _chip_2516(), COMMAND_WRITE, 0)
    comm.setup_command.assert_called_once()
    comm.disconnect.assert_called_once()
    assert operator.comm is None


# 5. CLI


def _app(**overrides):
    return AppContext(
        db=EpromDatabase(skip_local_override=True),
        config_manager=ConfigManager(),
        eprom_operator=overrides.pop("eprom_operator", Mock(spec=EpromOperator)),
        hardware_manager=overrides.pop("hardware_manager", Mock(spec=HardwareManager)),
        firmware_manager=Mock(spec=FirmwareManager),
        eprom_presenter=Mock(spec=EpromConsolePresenter),
    )


def test_cli_write_renders_hardware_error_and_exits_1(tmp_path):
    """End to end through the real write path: the connect succeeds on a
    Rev 2.0-class board and the write stops before any data frame."""
    image = tmp_path / "image.bin"
    image.write_bytes(b"\x00" * 2048)
    comm = _mock_comm(REVISION_2_0)
    app = _app(eprom_operator=EpromOperator(ConfigManager()))
    with patch.object(SerialCommunicator, "find_and_connect", return_value=comm):
        result = CliRunner().invoke(
            cli, ["write", "2516", str(image), "--no-blank-check"], obj=app
        )
    assert result.exit_code == 1, result.output
    assert "Hardware error: 2516: This chip needs VPP on chip pin 21." in result.output
    comm.send_bytes.assert_not_called()
    comm.disconnect.assert_called_once()


@pytest.mark.parametrize(
    ("value", "byte"),
    [
        ("2.2", REVISION_2_2),
        ("2.3", REVISION_2_3),
        ("2", REVISION_2_0),
        ("2.0", REVISION_2_0),
        ("2.1", REVISION_2_1),
        ("0", REVISION_0),
        ("1", REVISION_1),
        ("-1", -1),
    ],
)
def test_config_rev_maps_the_silkscreen_number_to_the_byte(value, byte):
    hw = Mock(spec=HardwareManager)
    hw.set_hardware_config.return_value = True
    result = CliRunner().invoke(
        cli, ["config", "--rev", value], obj=_app(hardware_manager=hw)
    )
    assert result.exit_code == 0, result.output
    assert hw.set_hardware_config.call_args.args[0] == byte


@pytest.mark.parametrize("value", ["4", "5", "2.4", "abc", "2.20"])
def test_config_rev_refuses_other_values_before_any_serial_byte(value):
    hw = Mock(spec=HardwareManager)
    result = CliRunner().invoke(
        cli, ["config", "--rev", value], obj=_app(hardware_manager=hw)
    )
    assert result.exit_code == 2
    hw.set_hardware_config.assert_not_called()
