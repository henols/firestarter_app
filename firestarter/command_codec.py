"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Binary command frame encoder.

Turns the host's command dict (the dict that `database.convert_to_programmer`
and `EpromOperator._setup_operation` build) into the binary frame the
firmware decodes. The layout comes from `firestarter.command_frame`, which is
generated in the meta repo from `tools/protocol/command_frame.toml`.

The encoder refuses every value the frame cannot carry, and every key it does
not know. The JSON firmware ignored unknown keys; a binary frame has no slot
for one, so dropping it silently would hide a host bug.
"""

import json
import struct
from typing import Any

from firestarter import command_frame as cf
from firestarter.exceptions import EpromOperationError

COMMAND_KEYS = ("cmd", "state")
BUS_CONFIG_KEY = "bus-config"

_FORMAT_MAX = {"B": 0xFF, "H": 0xFFFF, "I": 0xFFFFFFFF}
_MEMORY_KEYS = frozenset(key for _, key, _, _, bus in cf.MEMORY_FIELDS if not bus)
_BUS_KEYS = frozenset(key for _, key, _, _, bus in cf.MEMORY_FIELDS if bus)
_CONFIG_KEYS = frozenset(key for _, key, _, _ in cf.CONFIG_FIELDS)
_HEADER_KEYS = frozenset(COMMAND_KEYS + ("flags",))


class CommandEncodeError(EpromOperationError, ValueError):
    """The command dict cannot be expressed as a binary command frame.

    Raised before anything reaches the port. The JSON firmware accepted such
    values and then shifted by them or truncated them, so a value the frame
    cannot carry was never a value the firmware handled correctly.
    """


def _is_int(value: Any) -> bool:
    # bool is an int subclass; True must not encode as 1.
    return isinstance(value, int) and not isinstance(value, bool)


def _int(key: str, value: Any, fmt: str) -> int:
    if not _is_int(value) or not 0 <= value <= _FORMAT_MAX[fmt]:
        raise CommandEncodeError(
            f"'{key}' must be an integer in 0..{_FORMAT_MAX[fmt]}, got {value!r}"
        )
    return value


def _line(key: str, value: Any) -> int:
    if not _is_int(value) or not 0 <= value < cf.BUS_LINES:
        raise CommandEncodeError(
            f"'{key}' line {value!r} is outside bus lines 0..{cf.BUS_LINES - 1}; "
            f"the shield cannot drive this pin of the chip's pin map as a bus line"
        )
    return value


def _refuse_unknown(keys: Any, known: frozenset, where: str) -> None:
    unknown = sorted(set(keys) - known)
    if unknown:
        raise CommandEncodeError(
            f"{where} has keys the command frame cannot carry: {unknown}"
        )


def _memory_field(
    name: str, key: str, fmt: str, command: dict, bus_config: dict
) -> bytes:
    if name == "address_lines":
        lines = bus_config.get(key, [])
        if len(lines) > cf.ADDRESS_LINES:
            raise CommandEncodeError(
                f"'{key}' has {len(lines)} address lines; the frame carries {cf.ADDRESS_LINES}"
            )
        checked = [_line(key, line) for line in lines]
        if len(set(checked)) != len(checked):
            raise CommandEncodeError(f"'{key}' names a bus line twice: {checked}")
        return bytes(checked + [cf.LINE_ABSENT] * (cf.ADDRESS_LINES - len(checked)))
    if name in ("rw_line", "vpp_line"):
        value = _line(key, bus_config[key]) if key in bus_config else cf.LINE_ABSENT
        return struct.pack(">B", value)
    if name == "static_high_mask":
        mask = 0
        for line in bus_config.get(key, []):
            mask |= 1 << _line(key, line)
        return struct.pack(">I", mask)
    value = _int(key, command.get(key, 0), fmt)
    if name in ("read_settling_us", "read_strobe_us") and value > cf.READ_TIMING_MAX_US:
        raise CommandEncodeError(
            f"'{key}' must be at most {cf.READ_TIMING_MAX_US} us, got {value}"
        )
    if name == "vpp_mv" and value > cf.VPP_MAX_MV:
        raise CommandEncodeError(
            f"'{key}' must be at most {cf.VPP_MAX_MV} mV, got {value}"
        )
    return struct.pack(">" + fmt, value)


def _config_value(name: str, key: str, fmt: str, raw: Any) -> int:
    value = _int(key, raw, fmt)
    if name == "vcc" and value > cf.CALIBRATION_VCC_MAX_MV:
        raise CommandEncodeError(
            f"'{key}' must be at most {cf.CALIBRATION_VCC_MAX_MV} mV, got {value}"
        )
    if name == "rev" and value != 0xFF and value > cf.REVISION_MAX:
        raise CommandEncodeError(
            f"'{key}' must be 0..{cf.REVISION_MAX}, or 255 to clear it, got {value}"
        )
    return value


def _encode_memory(command: dict) -> bytes:
    _refuse_unknown(
        command, _HEADER_KEYS | _MEMORY_KEYS | {BUS_CONFIG_KEY}, "memory command"
    )
    bus_config = command.get(BUS_CONFIG_KEY) or {}
    _refuse_unknown(bus_config, _BUS_KEYS, f"'{BUS_CONFIG_KEY}'")
    payload = b"".join(
        _memory_field(name, key, fmt, command, bus_config)
        for name, key, fmt, _, _ in cf.MEMORY_FIELDS
    )
    assert len(payload) == cf.MEMORY_SIZE
    return payload


def _encode_config(command: dict) -> bytes:
    _refuse_unknown(command, _HEADER_KEYS | _CONFIG_KEYS, "config command")
    present = 0
    fields = b""
    for name, key, fmt, bit in cf.CONFIG_FIELDS:
        value = 0
        if key in command:
            present |= bit
            value = _config_value(name, key, fmt, command[key])
        fields += struct.pack(">" + fmt, value)
    payload = struct.pack(">B", present) + fields
    assert len(payload) == cf.CONFIG_SIZE
    return payload


def command_ordinal(command: dict) -> int:
    """Return the command ordinal from a dict's `cmd` or `state` key."""
    for key in COMMAND_KEYS:
        if key in command:
            return _int(key, command[key], "B")
    raise CommandEncodeError("command dict has neither 'cmd' nor 'state'")


def encode_command(command: dict) -> bytes:
    """Encode a host command dict as one binary command frame (no COBS, no CRC)."""
    cmd = command_ordinal(command)
    flags = _int("flags", command.get("flags", 0), "H")
    if flags & ~cf.FLAGS_MASK:
        raise CommandEncodeError(
            f"'flags' 0x{flags:04X} sets bits outside 0x{cf.FLAGS_MASK:04X}"
        )
    kind = cf.PAYLOAD_KIND.get(cmd, "none")
    if kind == "memory":
        payload = _encode_memory(command)
    elif kind == "config":
        payload = _encode_config(command)
    else:
        _refuse_unknown(command, _HEADER_KEYS, f"command {cmd}")
        payload = b""
    return (
        struct.pack(cf.HEADER_FORMAT, cf.MAGIC, cf.VERSION, cmd, flags, len(payload))
        + payload
    )


def encode_legacy_fw_version_query(command: dict) -> bytes:
    """The JSON firmware-version query a JSON-era firmware understands.

    The firmware-update path sends this only after the firmware has refused
    the binary query as an unsupported frame, so that `fw` can still read the
    version and board of a board it is about to replace. No other path may
    send JSON.
    """
    legacy = {"state": command_ordinal(command)}
    if command.get("flags"):
        legacy["flags"] = command["flags"]
    return json.dumps(legacy, separators=(",", ":")).encode("ascii")
