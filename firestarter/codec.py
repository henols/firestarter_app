"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Message rendering: sentinel-aware format_message function and hardware-revision
silkscreen table.
"""

import logging
import struct
from typing import Any, List  # noqa: UP035

from firestarter.command_frame import REFUSALS
from firestarter.constants import (
    COMMAND_NAMES,
    REVISION_0,
    REVISION_1,
    REVISION_2_0,
    REVISION_2_1,
    REVISION_2_2,
    REVISION_2_3,
    REVISION_UNKNOWN,
)
from firestarter.frame_parser import LogMessage, _crc8_ccitt, _decode_param
from firestarter.messages import (
    CATALOG,
    DBG_CMD,
    DEBUG_CATALOG,
    MSG_DATA_CHUNK,
    MSG_DEBUG,
    MSG_ERR_BAD_COMMAND,
    MSG_ERR_VPP_HIGH_MV,
    MSG_INFO_CMD,
    MSG_INFO_HW,
    MSG_INFO_PHYSICAL_HW,
    MSG_INFO_REGISTERS,
    MSG_OK_CFG,
    MSG_OK_REV,
    MSG_WARN_VPP_HIGH_MV,
    MSG_WARN_VPP_LOW_MV,
    SEVERITY_LABEL,
    MessageDef,
)

logger = logging.getLogger("Codec")

# MSG_INFO_REGISTERS views (firmware sync: dev_tools.cpp DT_VIEW_*).
_REGISTER_VIEW_REG_FIRESTARTER = 1
_REGISTER_VIEW_ADDR = 2


def volts(mv: int) -> str:
    """`'12.449 V'` for a millivolt value: every millivolt the firmware sent."""
    return f"{mv / 1000:.3f} V"


def _register_table(name: str, value: int, bits: int) -> list[str]:
    """One register as the three lines `dev reg` / `dev addr` print: the
    value, the bit header and the bit row, D(bits-1) first."""
    header = "|D8" if bits == 9 else ""
    row = "".join(f"| {(value >> i) & 1}" for i in range(bits - 1, -1, -1))
    return [
        f"{name}: 0x{value:02X}",
        f"{header}|D7|D6|D5|D4|D3|D2|D1|D0|",
        f"{row}|",
    ]


def _render_registers(msb: int, lsb: int, ctrl: int, remapped: int, view: int) -> str:
    """The bit tables for MSG_INFO_REGISTERS. The firmware sends raw values;
    the table layout is the one the firmware used to print."""
    lines = _register_table("MSB", msb, 8) + _register_table("LSB", lsb, 8)
    if view == _REGISTER_VIEW_ADDR:
        lines += _register_table("TOP addr", ctrl, 8)
        lines += _register_table("CTRL remapped", remapped, 8)
    elif view == _REGISTER_VIEW_REG_FIRESTARTER:
        lines += _register_table("CTRL", ctrl, 9)
        lines += _register_table("CTRL remapped", remapped, 8)
    else:
        lines += _register_table("CTRL", ctrl & 0xFF, 8)
    return "\n".join(lines)


# REVISION_* byte → silkscreen-string mapping for MSG_OK_REV rendering.
# Mirrors firmware enum at firestarter/include/rurp_shield.h. Lookup-via-dict.get()
# so unknown bytes fall back to "Rev{n}" instead of raising.
_REVISION_SILKSCREEN = {
    REVISION_0: "Rev 0",
    REVISION_1: "Rev 1",
    REVISION_2_0: "Rev 2.0-class",  # broad bucket
    REVISION_2_1: "Rev 2.1 (override)",
    REVISION_2_2: "Rev 2.2 (override)",
    REVISION_2_3: "Rev 2.3",
    REVISION_UNKNOWN: "rev_unknown",
}


def format_message(msg_id: int, params: List[Any], entry: MessageDef) -> str | None:  # noqa: UP006
    """Sentinel-aware message renderer for P-02/P-03 shaped IDs and
    MSG_DEBUG sub-payloads (currently DBG_CMD gets symbolic-name
    annotation; other DBG_* sub_ids render via DEBUG_CATALOG).

    Returns the rendered string for sentinel-byte IDs where the catalog
    format string cannot express the conditional (0xFF = no override),
    and for the silkscreen-aware INFO surfaces that share the same
    revision byte as MSG_OK_REV.

    Returns None for all other IDs (caller falls through to generic rendering).

    P-02 MSG_OK_REV  — params[0]=physical u8, params[1]=effective u8
      effective==0xFF → "Rev{physical}" (no override)
      effective!=0xFF → "Rev{effective}, Detected HW: Rev{physical}"
      (the override is in effect; the second field is what the board detected)

    P-03 MSG_OK_CFG  — params[0]=r1 u32, params[1]=r2 u32, params[2]=override u8,
                       params[3]=bandgap_mv u16, params[4]=bandgap_adc u16
      override==0xFF → "R1: {r1}, R2: {r2}, Bandgap: {n} mV, Bandgap count: {c}"
      override!=0xFF → "R1: {r1}, R2: {r2}, Override HW: {silkscreen_str},
                        Bandgap: {n} mV, Bandgap count: {c}"
      Override clause now routes through
      _REVISION_SILKSCREEN so the same byte that surfaces as "Rev 2.0-class"
      via MSG_OK_REV no longer surfaces as "Rev2" on the adjacent ack line.

    MSG_INFO_HW (0x5B) — single u8 revision byte; renders
      "HW: {silkscreen_str}" via _REVISION_SILKSCREEN.get(byte, "Rev{byte}").
      Was rendering catalog-default
      "HW: Rev%u" (e.g. "HW: Rev254" for REVISION_UNKNOWN=0xFE) — directly
      contradicting the rule that the host displays silkscreen strings while the wire
      carries raw byte).

    MSG_INFO_PHYSICAL_HW (0x5C) — same shape as MSG_INFO_HW with the
      "Physical HW: " prefix.
    """
    if msg_id == MSG_OK_REV and len(params) == 2:
        physical, effective = params[0], params[1]
        phys_str = _REVISION_SILKSCREEN.get(physical, f"Rev{physical}")
        if effective == 0xFF:
            return phys_str
        eff_str = _REVISION_SILKSCREEN.get(effective, f"Rev{effective}")
        return f"{eff_str}, Detected HW: {phys_str}"

    if msg_id == MSG_OK_CFG and len(params) == 5:
        r1, r2, override = params[0], params[1], params[2]
        head = f"R1: {r1}, R2: {r2}"
        if override != 0xFF:
            # Route the override byte through
            # _REVISION_SILKSCREEN so the same byte that renders "Rev 2.0-class"
            # on MSG_OK_REV no longer renders "Rev2" on this adjacent ack line.
            # No-space "Rev{n}" fallback mirrors the MSG_OK_REV branch shape.
            override_str = _REVISION_SILKSCREEN.get(override, f"Rev{override}")
            head += f", Override HW: {override_str}"
        return f"{head}, Bandgap: {params[3]} mV, Bandgap count: {params[4]}"

    # Silkscreen-aware rendering for the two
    # boot-time INFO surfaces that carry the same revision byte as
    # MSG_OK_REV. Mirror of the MSG_OK_REV branch shape above: lookup via
    # _REVISION_SILKSCREEN.get() with no-space "Rev{n}" fallback.
    if msg_id == MSG_INFO_HW and len(params) == 1:
        byte = params[0]
        return f"HW: {_REVISION_SILKSCREEN.get(byte, f'Rev{byte}')}"

    if msg_id == MSG_INFO_PHYSICAL_HW and len(params) == 1:
        byte = params[0]
        return f"Physical HW: {_REVISION_SILKSCREEN.get(byte, f'Rev{byte}')}"

    if msg_id == MSG_ERR_BAD_COMMAND and len(params) == 1:
        code = params[0]
        return f"Command refused: {REFUSALS.get(code, f'reason {code}')}"

    if msg_id == MSG_INFO_REGISTERS and len(params) == 5:
        return _render_registers(*params)

    # The firmware sends millivolts; show volts. The catalog text (mV) is the
    # fallback for any other shape.
    if msg_id in (MSG_WARN_VPP_LOW_MV, MSG_WARN_VPP_HIGH_MV, MSG_ERR_VPP_HIGH_MV):
        if len(params) == 2:
            measured, expected = params
            relation = "<" if msg_id == MSG_WARN_VPP_LOW_MV else ">"
            state = "low" if msg_id == MSG_WARN_VPP_LOW_MV else "high"
            return f"VPP is {state}: {volts(measured)} {relation} {volts(expected)}"

    if msg_id == MSG_INFO_CMD and len(params) == 1:
        cmd = params[0]
        name = COMMAND_NAMES.get(cmd)
        return f"Cmd: 0x{cmd:02x} ({name})" if name else f"Cmd: 0x{cmd:02x}"

    if msg_id == MSG_DEBUG and len(params) == 2:
        sub_id = params[0]
        sub_body: bytes = (
            bytes(params[1]) if isinstance(params[1], (bytes, bytearray)) else b""
        )
        sub_entry = DEBUG_CATALOG.get(sub_id)
        # Special-case DBG_CMD: annotate the cmd byte with its symbolic
        # name from COMMAND_NAMES so verbose logs read e.g. "Cmd: 0x02 (WRITE)".
        if sub_id == DBG_CMD and len(sub_body) >= 1:
            cmd = sub_body[0]
            name = COMMAND_NAMES.get(cmd)
            return f"Cmd: 0x{cmd:02x} ({name})" if name else f"Cmd: 0x{cmd:02x}"
        # Generic DBG render: walk sub_entry.params and format. Falls back
        # to the standard "[debug:N]" string for sub_ids the catalog hasn't
        # seen yet so unknown debug emits still appear in the log.
        if sub_entry is not None:
            try:
                values: List[Any] = []  # noqa: UP006
                cursor = 0
                for ptype, _prender in sub_entry.params:
                    value, cursor = _decode_param(ptype, sub_body, cursor)
                    values.append(value)
                fmt_values = [
                    v for v in values if not isinstance(v, (bytes, bytearray))
                ]
                return (
                    sub_entry.format % tuple(fmt_values)
                    if fmt_values
                    else sub_entry.format
                )
            except (IndexError, struct.error, ValueError):
                return None  # fall through to generic [debug:N] render
        return None

    if (
        msg_id == MSG_DATA_CHUNK
        and len(params) == 1
        and isinstance(params[0], (bytes, bytearray))
    ):
        # W-04: return a short summary so log lines don't dump 512 raw bytes.
        return f"<chunk: {len(params[0])} bytes>"

    return None  # fall through to generic catalog format-string rendering


def decode_id_frame(frame_len: int, body: bytes) -> LogMessage | None:
    """
    Read-path-adjacent — behavior preserved verbatim from serial_comm.py per
    Ring-fenced. Do not refactor without re-validating the v1.6 baseline binaries.

    Decode an ID-encoded wire frame body (the bytes between the length
    byte and the trailing 0x0A re-sync anchor).

    `body` carries `id | params | crc` exactly `frame_len` bytes long
    (length is authoritative; CRC8 covers `[id, params]`
    but not the length byte nor the terminator).

    Returns a LogMessage on success. Returns None (with a `logger.warning`)
    on shape mismatch / CRC fail / unknown ID / format-render error — the
    outer read loop continues to the next byte (DoS resilience per T-06-12).
    """
    if frame_len < 2 or len(body) != frame_len:
        logger.warning(
            f"Frame too short or truncated: declared len={frame_len}, "
            f"actual body len={len(body)}"
        )
        return None

    msg_id = body[0]
    crc_received = body[-1]
    params_bytes = bytes(body[1:-1])

    crc_expected = _crc8_ccitt(bytes([msg_id]) + params_bytes)
    if crc_expected != crc_received:
        logger.warning(
            f"CRC mismatch for ID 0x{msg_id:02x}: "
            f"expected 0x{crc_expected:02x}, got 0x{crc_received:02x}"
        )
        return None

    entry = CATALOG.get(msg_id)
    if entry is None:
        logger.warning(f"Unknown message ID 0x{msg_id:02x} — catalog out of date?")
        return None

    # Reject id-frame payloads for catalog entries flagged
    # wire_format="text". MSG_OK_FW_VERSION (0x03) is expected to arrive
    # over the legacy text channel only. A buggy or malicious
    # peer emitting id=0x03 as a binary frame would otherwise render via
    # the catalog format string and bypass the host's pre-v1.2 firmware-
    # version guard in _probe_port (which only inspects the text path).
    if entry.wire_format != "id_frame":
        logger.warning(
            f"Rejected id-frame for catalog entry with "
            f"wire_format={entry.wire_format!r}: id=0x{msg_id:02x} "
            f"({entry.name})"
        )
        return None

    # Shape check for fixed-width entries. Variable-length (ascii_str)
    # entries carry param_bytes == -1 in the catalog; for those we
    # cannot pre-validate, but _decode_param will surface any overrun
    # via IndexError below.
    if entry.param_bytes >= 0 and len(params_bytes) != entry.param_bytes:
        logger.warning(
            f"Param shape mismatch for ID 0x{msg_id:02x} ({entry.name}): "
            f"expected {entry.param_bytes} bytes, got {len(params_bytes)}"
        )
        return None

    # Decode each param per the catalog grammar.
    values: List[Any] = []  # noqa: UP006
    cursor = 0
    try:
        for ptype, _prender in entry.params:
            value, cursor = _decode_param(ptype, params_bytes, cursor)
            values.append(value)
    except (IndexError, struct.error, ValueError) as exc:
        logger.warning(
            f"Param decode failed for ID 0x{msg_id:02x} ({entry.name}): {exc}"
        )
        return None

    # Sentinel-aware rendering for P-02/P-03 shaped IDs (W-02).
    # format_message returns a string for MSG_OK_REV/CFG,
    # or None to fall through to the generic catalog format-string path.
    text = format_message(msg_id, values, entry)
    if text is None:
        # Generic render via the catalog format string. Format errors fall
        # back to a tagged placeholder so the read loop continues yielding
        # subsequent frames.
        # Filter out raw-bytes values (bytes-type params, e.g. MSG_DATA_CHUNK)
        # before printf-style substitution — they have no corresponding %
        # specifier in the format string.
        fmt_values = [v for v in values if not isinstance(v, (bytes, bytearray))]
        try:
            text = entry.format % tuple(fmt_values) if fmt_values else entry.format
        except (TypeError, ValueError) as exc:
            logger.warning(
                f"Format-error rendering ID 0x{msg_id:02x} ({entry.name}): {exc}"
            )
            text = f"<format-error: {entry.name}>"

    # Extract raw-bytes payload for MSG_DATA_CHUNK (W-04) so the chip-read
    # loop can obtain the chip data without a second read call.
    payload = None
    if (
        msg_id == MSG_DATA_CHUNK
        and values
        and isinstance(values[0], (bytes, bytearray))
    ):
        payload = bytes(values[0])

    severity_label = SEVERITY_LABEL.get(entry.severity, f"SEV{entry.severity}")
    return LogMessage(
        severity=severity_label,
        text=text,
        id=msg_id,
        payload=payload,
        params=tuple(values),
    )
