"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Message rendering: sentinel-aware format_message function and hardware-revision
silkscreen table.
"""

import struct
from typing import Optional  # noqa: UP035

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
from firestarter.frame_parser import _decode_param
from firestarter.messages import (
    DBG_CMD,
    DEBUG_CATALOG,
    MSG_DATA_CHUNK,
    MSG_DEBUG,
    MSG_INFO_CMD,
    MSG_INFO_HW,
    MSG_INFO_PHYSICAL_HW,
    MSG_OK_CFG,
    MSG_OK_REV,
)

# Phase 34: REVISION_* byte → silkscreen-string mapping for MSG_OK_REV rendering.
# Mirrors firmware enum at firestarter/include/rurp_shield.h. Lookup-via-dict.get()
# so unknown bytes fall back to "Rev{n}" instead of raising.
_REVISION_SILKSCREEN = {
    REVISION_0: "Rev 0",
    REVISION_1: "Rev 1",
    REVISION_2_0: "Rev 2.0-class",  # broad bucket per Phase 34 D-04
    REVISION_2_1: "Rev 2.1 (override)",
    REVISION_2_2: "Rev 2.2 (override)",
    REVISION_2_3: "Rev 2.3",
    REVISION_UNKNOWN: "rev_unknown",
}


def format_message(msg_id: int, params: list, entry) -> Optional[str]:
    """Sentinel-aware message renderer for P-02/P-03 shaped IDs and
    MSG_DEBUG sub-payloads (currently DBG_CMD gets symbolic-name
    annotation; other DBG_* sub_ids render via DEBUG_CATALOG).

    Returns the rendered string for sentinel-byte IDs where the catalog
    format string cannot express the conditional (0xFF = no override),
    and for the silkscreen-aware INFO surfaces that share the same
    revision byte as MSG_OK_REV (Phase 35 D-03 / D-04 — close WR-01 + WR-02).

    Returns None for all other IDs (caller falls through to generic rendering).

    P-02 MSG_OK_REV  — params[0]=physical u8, params[1]=effective u8
      effective==0xFF → "Rev{physical}" (no override)
      effective!=0xFF → "Rev{effective}, Override HW: Rev{physical}"

    P-03 MSG_OK_CFG  — params[0]=r1 u32, params[1]=r2 u32, params[2]=override u8
      override==0xFF → "R1: {r1}, R2: {r2}"
      override!=0xFF → "R1: {r1}, R2: {r2}, Override HW: {silkscreen_str}"
      Phase 35 D-04 / WR-02 close: override clause now routes through
      _REVISION_SILKSCREEN so the same byte that surfaces as "Rev 2.0-class"
      via MSG_OK_REV no longer surfaces as "Rev2" on the adjacent ack line.

    MSG_INFO_HW (0x5B) — single u8 revision byte; renders
      "HW: {silkscreen_str}" via _REVISION_SILKSCREEN.get(byte, "Rev{byte}").
      Phase 35 D-03 / WR-01 close: was rendering catalog-default
      "HW: Rev%u" (e.g. "HW: Rev254" for REVISION_UNKNOWN=0xFE) — directly
      contradicting Phase 34 D-09 (host displays silkscreen strings; wire
      carries raw byte).

    MSG_INFO_PHYSICAL_HW (0x5C) — same shape as MSG_INFO_HW with the
      "Physical HW: " prefix. Phase 35 D-03 / WR-01 close.
    """
    if msg_id == MSG_OK_REV and len(params) == 2:
        physical, effective = params[0], params[1]
        phys_str = _REVISION_SILKSCREEN.get(physical, f"Rev{physical}")
        if effective == 0xFF:
            return phys_str
        eff_str = _REVISION_SILKSCREEN.get(effective, f"Rev{effective}")
        return f"{eff_str}, Override HW: {phys_str}"

    if msg_id == MSG_OK_CFG and len(params) == 3:
        r1, r2, override = params[0], params[1], params[2]
        if override == 0xFF:
            return f"R1: {r1}, R2: {r2}"
        # Phase 35 D-04 / WR-02 close: route the override byte through
        # _REVISION_SILKSCREEN so the same byte that renders "Rev 2.0-class"
        # on MSG_OK_REV no longer renders "Rev2" on this adjacent ack line.
        # No-space "Rev{n}" fallback mirrors the MSG_OK_REV branch shape.
        override_str = _REVISION_SILKSCREEN.get(override, f"Rev{override}")
        return f"R1: {r1}, R2: {r2}, Override HW: {override_str}"

    # Phase 35 D-03 / WR-01 close — silkscreen-aware rendering for the two
    # boot-time INFO surfaces that carry the same revision byte as
    # MSG_OK_REV. Mirror of the MSG_OK_REV branch shape above: lookup via
    # _REVISION_SILKSCREEN.get() with no-space "Rev{n}" fallback.
    if msg_id == MSG_INFO_HW and len(params) == 1:
        byte = params[0]
        return f"HW: {_REVISION_SILKSCREEN.get(byte, f'Rev{byte}')}"

    if msg_id == MSG_INFO_PHYSICAL_HW and len(params) == 1:
        byte = params[0]
        return f"Physical HW: {_REVISION_SILKSCREEN.get(byte, f'Rev{byte}')}"

    if msg_id == MSG_INFO_CMD and len(params) == 1:
        cmd = params[0]
        name = COMMAND_NAMES.get(cmd)
        return f"Cmd: 0x{cmd:02x} ({name})" if name else f"Cmd: 0x{cmd:02x}"

    if msg_id == MSG_DEBUG and len(params) == 2:
        sub_id = params[0]
        sub_body = params[1] if isinstance(params[1], (bytes, bytearray)) else b""
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
                values: list = []
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
