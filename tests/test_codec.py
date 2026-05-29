"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 38 — codec.format_message unit tests (STRUCT-02).

Covers all catalog message shapes handled by the sentinel-aware renderer:
MSG_OK_REV, MSG_OK_CFG, MSG_INFO_HW, MSG_INFO_PHYSICAL_HW, MSG_INFO_CMD,
MSG_DEBUG (DBG_CMD), MSG_DATA_CHUNK, and the None fall-through path.

Tests import directly from firestarter.codec — no SerialCommunicator instance
or serial fixtures needed.
"""

import pytest  # noqa: F401
from firestarter.codec import format_message

from firestarter.messages import (
    CATALOG,
    DBG_CMD,
    MSG_DATA_CHUNK,
    MSG_DEBUG,
    MSG_INFO_CMD,
    MSG_INFO_HW,
    MSG_INFO_PHYSICAL_HW,
    MSG_OK_CFG,
    MSG_OK_REV,
)


class TestFormatMessageRevision:
    """MSG_OK_REV / MSG_OK_CFG / MSG_INFO_HW / MSG_INFO_PHYSICAL_HW / MSG_INFO_CMD."""

    def test_msg_ok_rev_no_override(self):
        """MSG_OK_REV: effective==0xFF → physical silkscreen string only."""
        entry = CATALOG[MSG_OK_REV]
        result = format_message(MSG_OK_REV, [0x00, 0xFF], entry)
        assert result == "Rev 0"

    def test_msg_ok_rev_with_override(self):
        """MSG_OK_REV: effective!=0xFF → '{eff_str}, Override HW: {phys_str}'."""
        entry = CATALOG[MSG_OK_REV]
        result = format_message(MSG_OK_REV, [0x02, 0x04], entry)
        assert result == "Rev 2.2 (override), Override HW: Rev 2.0-class"

    def test_msg_ok_cfg_no_override(self):
        """MSG_OK_CFG: override==0xFF → 'R1: {r1}, R2: {r2}'."""
        entry = CATALOG[MSG_OK_CFG]
        result = format_message(MSG_OK_CFG, [10000, 20000, 0xFF], entry)
        assert result == "R1: 10000, R2: 20000"

    def test_msg_ok_cfg_with_override(self):
        """MSG_OK_CFG: override!=0xFF → adds ', Override HW: {silkscreen_str}'."""
        entry = CATALOG[MSG_OK_CFG]
        result = format_message(MSG_OK_CFG, [10000, 20000, 0x02], entry)
        assert result == "R1: 10000, R2: 20000, Override HW: Rev 2.0-class"

    def test_msg_info_hw(self):
        """MSG_INFO_HW: single u8 → 'HW: {silkscreen_str}'."""
        entry = CATALOG[MSG_INFO_HW]
        result = format_message(MSG_INFO_HW, [0x02], entry)
        assert result == "HW: Rev 2.0-class"

    def test_msg_info_physical_hw(self):
        """MSG_INFO_PHYSICAL_HW: single u8 → 'Physical HW: {silkscreen_str}'."""
        entry = CATALOG[MSG_INFO_PHYSICAL_HW]
        result = format_message(MSG_INFO_PHYSICAL_HW, [0x00], entry)
        assert result == "Physical HW: Rev 0"

    def test_msg_info_cmd(self):
        """MSG_INFO_CMD: single u8 cmd → 'Cmd: 0x{n} (NAME)'."""
        entry = CATALOG[MSG_INFO_CMD]
        result = format_message(MSG_INFO_CMD, [0x01], entry)
        assert result == "Cmd: 0x01 (READ)"


class TestFormatMessageDebugChunk:
    """MSG_DEBUG (DBG_CMD) and MSG_DATA_CHUNK rendering."""

    def test_msg_debug_dbg_cmd(self):
        """MSG_DEBUG + DBG_CMD sub-id → 'Cmd: 0x{n} (NAME)'."""
        entry = CATALOG[MSG_DEBUG]
        result = format_message(MSG_DEBUG, [DBG_CMD, bytes([0x02])], entry)
        assert result == "Cmd: 0x02 (WRITE)"

    def test_msg_data_chunk(self):
        """MSG_DATA_CHUNK → '<chunk: N bytes>' summary (not raw dump)."""
        entry = CATALOG[MSG_DATA_CHUNK]
        result = format_message(MSG_DATA_CHUNK, [b"\x00" * 512], entry)
        assert result == "<chunk: 512 bytes>"


class TestFormatMessageNoneSentinel:
    """Unknown/unhandled IDs return None (fall-through to generic rendering)."""

    def test_none_for_unknown_id(self):
        """Unknown msg_id returns None."""
        entry = CATALOG[MSG_OK_REV]  # arbitrary valid entry
        result = format_message(0x99, [], entry)
        assert result is None
