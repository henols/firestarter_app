"""Phase 42 / ERR-03 fallback coverage lift for ``codec.format_message``
sentinel-aware rendering branches (D-14 fallback).

The branches covered here are the load-bearing P-02/P-03 silkscreen renderers
plus the MSG_DATA_CHUNK summary and MSG_INFO_CMD fallback. Pure-data: no
serial I/O.
"""

from firestarter.codec import format_message
from firestarter.constants import REVISION_2_2, REVISION_UNKNOWN
from firestarter.messages import (
    CATALOG,
    MSG_DATA_CHUNK,
    MSG_INFO_CMD,
    MSG_INFO_HW,
    MSG_INFO_PHYSICAL_HW,
    MSG_OK_CFG,
    MSG_OK_REV,
)


def test_format_msg_ok_rev_no_override_returns_silkscreen() -> None:
    """MSG_OK_REV with effective=0xFF (no override) returns the physical silkscreen string."""
    out = format_message(MSG_OK_REV, [REVISION_2_2, 0xFF], CATALOG[MSG_OK_REV])
    assert out is not None
    assert "Rev 2.2" in out


def test_format_msg_ok_rev_with_override_includes_both() -> None:
    """MSG_OK_REV with effective != 0xFF includes both physical and effective."""
    out = format_message(
        MSG_OK_REV, [REVISION_2_2, REVISION_UNKNOWN], CATALOG[MSG_OK_REV]
    )
    assert out is not None
    # The format is "{eff}, Override HW: {phys}"
    assert "Override HW" in out


def test_format_msg_ok_cfg_no_override() -> None:
    """MSG_OK_CFG with override=0xFF returns the R1/R2-only summary."""
    out = format_message(MSG_OK_CFG, [1000, 2000, 0xFF], CATALOG[MSG_OK_CFG])
    assert out is not None
    assert "R1: 1000" in out
    assert "R2: 2000" in out


def test_format_msg_ok_cfg_with_override() -> None:
    """MSG_OK_CFG with a non-0xFF override appends 'Override HW: ...' via silkscreen."""
    out = format_message(MSG_OK_CFG, [1000, 2000, REVISION_2_2], CATALOG[MSG_OK_CFG])
    assert out is not None
    assert "Override HW" in out


def test_format_msg_info_hw_renders_silkscreen() -> None:
    """MSG_INFO_HW renders as 'HW: {silkscreen_str}'."""
    out = format_message(MSG_INFO_HW, [REVISION_2_2], CATALOG[MSG_INFO_HW])
    assert out is not None
    assert "HW:" in out


def test_format_msg_info_physical_hw_renders_silkscreen() -> None:
    """MSG_INFO_PHYSICAL_HW renders as 'Physical HW: {silkscreen_str}'."""
    out = format_message(
        MSG_INFO_PHYSICAL_HW, [REVISION_2_2], CATALOG[MSG_INFO_PHYSICAL_HW]
    )
    assert out is not None
    assert "Physical HW:" in out


def test_format_msg_info_cmd_with_known_cmd() -> None:
    """MSG_INFO_CMD includes the symbolic command name for known cmd codes."""
    # cmd code 0x02 = WRITE per COMMAND_NAMES
    out = format_message(MSG_INFO_CMD, [0x02], CATALOG[MSG_INFO_CMD])
    assert out is not None
    assert "0x02" in out


def test_format_msg_data_chunk_summary() -> None:
    """MSG_DATA_CHUNK returns a '<chunk: N bytes>' summary instead of dumping bytes."""
    out = format_message(MSG_DATA_CHUNK, [b"hello"], CATALOG[MSG_DATA_CHUNK])
    assert out is not None
    assert "5" in out


def test_format_message_unmatched_id_returns_none() -> None:
    """An ID not in the sentinel-aware list returns None (caller falls back to generic)."""
    out = format_message(0x99, [], CATALOG[MSG_OK_REV])
    assert out is None
