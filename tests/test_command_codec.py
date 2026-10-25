"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Tests for the binary command frame encoder (firestarter/command_codec.py).

The golden frames in tests/golden/command_vectors.json are generated in the
meta repo from tools/protocol/command_frame.toml by a reference encoder. The
firmware's native suite decodes the same frames, so a host encoding that
matches them is a host encoding the firmware reads.
"""

import json
from pathlib import Path

import pytest

from firestarter import command_frame as cf
from firestarter.command_codec import (
    CommandEncodeError,
    command_ordinal,
    encode_command,
    encode_legacy_fw_version_query,
)
from firestarter.constants import (
    COMMAND_CONFIG,
    COMMAND_FW_VERSION,
    COMMAND_NAMES,
    COMMAND_READ,
    COMMAND_WRITE,
    FLAG_SKIP_SDP_UNLOCK,
)
from firestarter.database import EpromDatabase

_VECTORS = json.loads(
    (Path(__file__).resolve().parent / "golden" / "command_vectors.json").read_text()
)
_ACCEPT = _VECTORS["accept"]

_READ_27C512 = {
    "cmd": COMMAND_READ,
    "memory-size": 65536,
    "algorithm": 7,
    "pin-count": 28,
    "vpp_mv": 12500,
    "bus-config": {"bus": list(range(16))},
}


def _with(**changes):
    command = json.loads(json.dumps(_READ_27C512))
    for key, value in changes.items():
        command[key.replace("_", "-") if key != "vpp_mv" else key] = value
    return command


def _with_bus(**changes):
    command = json.loads(json.dumps(_READ_27C512))
    command["bus-config"].update({k.replace("_", "-"): v for k, v in changes.items()})
    return command


def test_golden_vector_set_is_the_generated_one():
    assert len(_ACCEPT) == 12
    assert len(_VECTORS["refuse"]) == 21


@pytest.mark.parametrize("vector", _ACCEPT, ids=[v["name"] for v in _ACCEPT])
def test_encoder_reproduces_every_golden_frame(vector):
    command = {"cmd": vector["cmd"], "flags": vector["flags"], **vector["fields"]}
    assert encode_command(command).hex() == vector["frame"]


def test_state_key_is_the_same_as_cmd():
    assert encode_command({"state": COMMAND_FW_VERSION}) == encode_command(
        {"cmd": COMMAND_FW_VERSION}
    )


def test_absent_flags_encode_as_zero():
    assert encode_command({"state": COMMAND_FW_VERSION}) == bytes.fromhex(
        "f5010d00000000"
    )


def test_every_host_command_has_a_payload_kind():
    for cmd in COMMAND_NAMES:
        assert cmd in cf.PAYLOAD_KIND, f"command {cmd} has no payload kind"
    assert 4 not in cf.PAYLOAD_KIND and 6 not in cf.PAYLOAD_KIND


@pytest.mark.parametrize(
    "command",
    [
        _with(pin_count=True),
        {"state": COMMAND_FW_VERSION, "flags": True},
        _with_bus(bus=[True, 1, 2]),
        _with_bus(rw_pin=False),
    ],
    ids=["pin-count", "flags", "bus line", "rw-pin"],
)
def test_bool_is_not_an_integer(command):
    with pytest.raises(CommandEncodeError):
        encode_command(command)


def test_every_frame_field_has_one_key_constant():
    field_keys = {name: key for name, key, _, _, _ in cf.MEMORY_FIELDS}
    field_keys.update({name: key for name, key, _, _ in cf.CONFIG_FIELDS})
    constants = {
        name[len("KEY_") :].lower(): value
        for name, value in vars(cf).items()
        if name.startswith("KEY_")
    }
    assert constants == field_keys


def test_every_database_chip_encodes_for_every_memory_command():
    """Every chip the database ships must survive the encoder's checks, and
    carry the four fields the firmware requires of a memory command.

    The one exception is the TI 2532: its A11 is socket pin 18, which the
    shield wires to chip enable, so its pin map puts the CE control line in
    the address bus. The JSON firmware truncated that 0x100 to bus line 0, so
    bus line 0 carried A11 in place of A0 at every address. The frame refuses it.
    """
    db = EpromDatabase(skip_local_override=True)
    encoded, refused = 0, []
    for mfg, chips in db.proms.items():
        for chip in chips:
            mapped = db.get_eprom(chip.get("part_number", ""))
            wire = db.convert_to_programmer(mapped)
            for key in ("memory-size", "algorithm", "pin-count", "vpp_mv"):
                assert wire.get(key), f"{chip.get('part_number')}: '{key}' is zero"
            try:
                for cmd in (COMMAND_READ, COMMAND_WRITE):
                    frame = encode_command({**wire, "cmd": cmd})
                    assert len(frame) == cf.HEADER_SIZE + cf.MEMORY_SIZE
            except CommandEncodeError as e:
                assert "'bus' line 256" in str(e)
                refused.append(f"{mfg}/{chip.get('part_number')}")
                continue
            encoded += 1
    assert refused == ["TEXAS INSTRUMENTS/2532"]
    assert encoded == 745


def test_config_payload_marks_only_present_values():
    frame = encode_command({"state": COMMAND_CONFIG, "r1": 270000})
    payload = frame[cf.HEADER_SIZE :]
    assert payload[0] == 0x01
    assert payload[1:5] == (270000).to_bytes(4, "big")
    assert payload[5:] == bytes(cf.CONFIG_SIZE - 5)


# Refusals: each check is shown to refuse, and its boundary to pass.


@pytest.mark.parametrize(
    "command",
    [
        _with(value=42),
        _with_bus(cs_pin=3),
        {"state": COMMAND_FW_VERSION, "address": 0},
        {"state": COMMAND_CONFIG, "r3": 1},
    ],
    ids=[
        "unknown-memory-key",
        "unknown-bus-key",
        "payload-on-simple",
        "unknown-config-key",
    ],
)
def test_unknown_keys_are_refused(command):
    with pytest.raises(CommandEncodeError, match="cannot carry"):
        encode_command(command)


def test_missing_command_ordinal_is_refused():
    with pytest.raises(CommandEncodeError, match="neither 'cmd' nor 'state'"):
        command_ordinal({"flags": 0})


@pytest.mark.parametrize(
    "command",
    [
        _with(memory_size=1 << 32),
        _with(memory_size=-1),
        _with(algorithm=256),
        _with(vpp_mv=0x10000),
        _with(memory_size="65536"),
        _with(address=None),
        {"state": COMMAND_CONFIG, "bg": 0x10000},
    ],
    ids=[
        "u32-overflow",
        "negative",
        "u8-overflow",
        "u16-overflow",
        "string",
        "none",
        "config-u16",
    ],
)
def test_values_outside_their_field_are_refused(command):
    with pytest.raises(CommandEncodeError, match="must be an integer"):
        encode_command(command)


def test_field_maxima_pass():
    encode_command(_with(memory_size=0xFFFFFFFF, algorithm=0xFF, vpp_mv=0xFFFF))


def test_bus_line_at_bus_width_is_refused_and_last_line_passes():
    encode_command(_with_bus(bus=[cf.BUS_LINES - 1]))
    for bus_key in ("bus", "static_high"):
        with pytest.raises(CommandEncodeError, match="outside bus lines"):
            encode_command(_with_bus(**{bus_key: [cf.BUS_LINES]}))
    for pin_key in ("rw_pin", "vpp_pin"):
        encode_command(_with_bus(**{pin_key: cf.BUS_LINES - 1}))
        with pytest.raises(CommandEncodeError, match="outside bus lines"):
            encode_command(_with_bus(**{pin_key: cf.BUS_LINES}))


def test_duplicate_address_line_is_refused():
    with pytest.raises(CommandEncodeError, match="twice"):
        encode_command(_with_bus(bus=[0, 1, 1]))


def test_more_address_lines_than_the_frame_carries_is_refused():
    encode_command(_with_bus(bus=list(range(cf.ADDRESS_LINES))))
    with pytest.raises(CommandEncodeError, match="address lines"):
        encode_command(_with_bus(bus=list(range(cf.ADDRESS_LINES + 1))))


@pytest.mark.parametrize("key", ["read-settling-delay", "read-strobe-us"])
def test_read_timing_above_the_cap_is_refused(key):
    encode_command({**_READ_27C512, key: cf.READ_TIMING_MAX_US})
    with pytest.raises(CommandEncodeError, match="at most"):
        encode_command({**_READ_27C512, key: cf.READ_TIMING_MAX_US + 1})


def test_undefined_flag_bits_are_refused():
    encode_command({**_READ_27C512, "flags": cf.FLAGS_MASK})
    encode_command({**_READ_27C512, "flags": FLAG_SKIP_SDP_UNLOCK})
    for bit in (0x08, 0x200, 0x8000):
        with pytest.raises(CommandEncodeError, match="outside"):
            encode_command({**_READ_27C512, "flags": bit})


# The one JSON frame this host may still send.


def test_legacy_version_query_is_the_json_era_bytes():
    assert (
        encode_legacy_fw_version_query({"state": COMMAND_FW_VERSION}) == b'{"state":13}'
    )
    assert (
        encode_legacy_fw_version_query({"state": COMMAND_FW_VERSION, "flags": 0x80})
        == b'{"state":13,"flags":128}'
    )


def test_binary_magic_cannot_start_a_json_text():
    """JSON-era firmware rejects the frame on byte 0 alone: jsmn refuses any
    byte outside 32..126 when it scans a primitive."""
    assert not 32 <= cf.MAGIC <= 126
