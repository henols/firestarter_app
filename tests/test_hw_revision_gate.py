"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

CAP-02 — shield-revision safety gate + extended MSG_OK_READY decode.

Chips whose bus-config routes VPP to bus line 11 (socket pin 21 on the
DIP24_2716 / DIP24_2532 pinouts) need the 3-position JP4 header introduced on
RURP shield Rev 2.2. Driving them on an earlier shield is a chip-damage path,
so the host refuses at connect time.

Three things are proved here:

  1. `_validate_hardware_revision` -- the pure policy. Most importantly that it
     is an ALLOWLIST and not a `>=` comparison: REVISION_UNKNOWN is 0xFE, which
     is numerically ABOVE REVISION_2_2 (0x04), so a comparison would admit
     precisely the boards whose revision could not be determined.
  2. `_decode_id_frame` -- that the extended ack is parsed, that the legacy
     2-byte ack still yields its buffer size, and that a malformed length
     prefix degrades to "no identity" (reject) rather than a partial string.
  3. The gate's coupling to the REAL database -- that DIP24_2716 / DIP24_2532
     genuinely emit `vpp-pin: 11` and that no other pinout does. Without this,
     a pinout edit could silently move chips in or out of the gate's scope.
"""

import struct
from unittest.mock import MagicMock, patch

import pytest

from firestarter.constants import (
    REVISION_0,
    REVISION_1,
    REVISION_2_0,
    REVISION_2_1,
    REVISION_2_2,
    REVISION_2_3,
    REVISION_UNKNOWN,
)
from firestarter.database import EpromDatabase
from firestarter.exceptions import HardwareRevisionUnsupportedError
from firestarter.messages import MSG_OK_READY
from firestarter.serial_comm import SerialCommunicator

GATED_VPP_LINE = 11

# A wire dict for a chip that needs Rev 2.2+ (VPP on bus line 11), and one for
# an ordinary chip that does not.
GATED_CMD = {"cmd": 1, "bus-config": {"bus": [0, 1, 2], "vpp-pin": GATED_VPP_LINE}}
UNGATED_CMD = {"cmd": 1, "bus-config": {"bus": [0, 1, 2], "vpp-pin": 15}}


def _validate(command, detected):
    return SerialCommunicator._validate_hardware_revision(command, detected)


# ---------------------------------------------------------------------------
# 1. Pure policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("allowed", [REVISION_2_2, REVISION_2_3])
def test_gated_chip_passes_on_rev_2_2_and_later(allowed):
    """Rev 2.2 and Rev 2.3 both carry the 3-position header, so both pass."""
    _validate(GATED_CMD, allowed)  # must not raise


@pytest.mark.parametrize(
    "refused",
    [REVISION_0, REVISION_1, REVISION_2_0, REVISION_2_1],
)
def test_gated_chip_refused_on_earlier_shields(refused):
    """Every pre-2.2 revision is refused, including the REVISION_2_0 bucket.

    REVISION_2_0 is the broad ADC bucket covering Rev 2.0/2.1/2.2, so a genuine
    Rev 2.2 lands here until the operator writes the EEPROM override. Refusing
    is the intended outcome: the operator must look at the physical header and
    assert it, and that assertion IS the safety mechanism.
    """
    with pytest.raises(HardwareRevisionUnsupportedError):
        _validate(GATED_CMD, refused)


def test_revision_unknown_is_refused_despite_being_numerically_higher():
    """THE trap this gate exists to avoid.

    REVISION_UNKNOWN (0xFE) is numerically greater than REVISION_2_2 (0x04), so
    the obvious `detected >= REVISION_2_2` spelling would ADMIT a board whose
    revision could not be determined -- the single case most deserving a
    refusal. The first assertion pins that arithmetic so this test keeps
    explaining itself if the enum values ever move.
    """
    assert REVISION_UNKNOWN > REVISION_2_2, (
        "the REVISION_* bytes are not a version-ordered scale; if this ever "
        "becomes false the allowlist is still correct but this test's "
        "rationale needs rewriting"
    )
    with pytest.raises(HardwareRevisionUnsupportedError):
        _validate(GATED_CMD, REVISION_UNKNOWN)


def test_override_absent_sentinel_is_refused():
    """0xFF ("no EEPROM override active") is not a revision and must refuse."""
    with pytest.raises(HardwareRevisionUnsupportedError):
        _validate(GATED_CMD, 0xFF)


def test_absent_revision_is_refused():
    """Firmware predating CAP-02 sends no revision byte -> detected is None.

    None must be a REJECT, never a pass: "the firmware didn't tell me" is not
    evidence that the shield is safe.
    """
    with pytest.raises(HardwareRevisionUnsupportedError) as exc_info:
        _validate(GATED_CMD, None)
    assert exc_info.value.detected is None
    assert "firmware predates" in str(exc_info.value)


@pytest.mark.parametrize(
    "command",
    [
        UNGATED_CMD,
        {"cmd": 1, "bus-config": {"bus": [0, 1]}},  # no vpp-pin at all
        {"cmd": 1},  # no bus-config at all
        {"state": 13},  # a bare state command
    ],
)
def test_ungated_chips_pass_on_any_revision(command):
    """Only VPP-on-line-11 chips are gated; everything else is untouched.

    Checked against the WORST revision value so a gate that accidentally
    widened to all chips would fail here rather than silently bricking every
    operation on a Rev 2.0 board.
    """
    _validate(command, REVISION_2_0)  # must not raise
    _validate(command, None)  # must not raise


def test_refusal_message_gives_the_exact_remedy():
    """The error must name the byte to write, not the silkscreen number.

    `firestarter config --rev` casts through int(), so '--rev 2.2' truncates to
    2 and silently selects the Rev 2.0 bucket -- the exact opposite of what an
    operator typing it intends. The message has to pre-empt that.
    """
    with pytest.raises(HardwareRevisionUnsupportedError) as exc_info:
        _validate(GATED_CMD, REVISION_2_0)
    text = str(exc_info.value)
    assert "firestarter config --rev 4" in text
    assert "--rev 2.2" in text and "truncates" in text
    assert exc_info.value.detected == REVISION_2_0


# ---------------------------------------------------------------------------
# 2. Extended MSG_OK_READY decode
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 2b. CAP-03 -- the per-block write-time budget (HOST-01, host half only)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 3. Coupling to the real database
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 4. Integration through _probe_port
# ---------------------------------------------------------------------------


def _probe(command, revision):
    with (
        patch.object(SerialCommunicator, "expect_ack", return_value=(True, "Ready")),
        patch.object(SerialCommunicator, "send_json_command", return_value=42),
        patch.object(SerialCommunicator, "consume_remaining_input", return_value=None),
        patch.object(SerialCommunicator, "disconnect", return_value=None),
        patch.object(SerialCommunicator, "firmware_identity", "3.0.0:uno"),
        patch.object(SerialCommunicator, "hw_revision", revision),
        patch.object(SerialCommunicator, "__init__", lambda self, port, **k: None),
    ):
        return SerialCommunicator._probe_port(
            port_name="/dev/null",
            baud_rate=250000,
            command_to_send=command,
            config_manager=MagicMock(),
        )


def test_probe_port_refuses_gated_chip_on_rev_2_0_class_board():
    """The refusal must ESCAPE _probe_port as its own typed error.

    _probe_port's default failure mode is `return None`, which find_and_connect
    turns into "No compatible programmer found on any port" -- a message that
    sends an operator hunting for a cable fault when the board is plainly
    attached and the real answer is "wrong shield for this chip".
    """
    with pytest.raises(HardwareRevisionUnsupportedError):
        _probe(GATED_CMD, REVISION_2_0)


def test_probe_port_allows_gated_chip_on_asserted_rev_2_2():
    assert _probe(GATED_CMD, REVISION_2_2) is not None


def test_probe_port_allows_ungated_chip_on_rev_2_0_class_board():
    assert _probe(UNGATED_CMD, REVISION_2_0) is not None
