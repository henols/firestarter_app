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
    out = format_message(MSG_OK_CFG, [1000, 2000, 0xFF, 1100], CATALOG[MSG_OK_CFG])
    assert out is not None
    assert "R1: 1000" in out
    assert "R2: 2000" in out
    assert "Bandgap: 1100 mV" in out


def test_format_msg_ok_cfg_with_override() -> None:
    """MSG_OK_CFG with a non-0xFF override appends 'Override HW: ...' via silkscreen."""
    out = format_message(
        MSG_OK_CFG, [1000, 2000, REVISION_2_2, 1019], CATALOG[MSG_OK_CFG]
    )
    assert out is not None
    assert "Override HW" in out
    assert "Bandgap: 1019 mV" in out


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


def _cfg_body(params: bytes) -> bytes:
    from firestarter.frame_parser import _crc8_ccitt

    head = bytes([MSG_OK_CFG]) + params
    return head + bytes([_crc8_ccitt(head)])


def test_decode_msg_ok_cfg_current_11_byte_form() -> None:
    """The current ack carries r1, r2, override and the bandgap."""
    import struct

    from firestarter.codec import decode_id_frame

    body = _cfg_body(struct.pack(">IIBH", 270000, 44000, 0xFF, 1100))
    msg = decode_id_frame(len(body), body)
    assert msg is not None
    assert msg.text == "R1: 270000, R2: 44000, Bandgap: 1100 mV"


def test_decode_msg_ok_cfg_legacy_9_byte_form() -> None:
    """Firmware before 3.1.0b4 (including 3.1.0 stable) sends no bandgap.

    The decoder must accept the 9-byte ack and render it without the
    Bandgap clause, so the config command still gets a readable ack.
    """
    import struct

    from firestarter.codec import decode_id_frame

    body = _cfg_body(struct.pack(">IIB", 270000, 44000, 0xFF))
    msg = decode_id_frame(len(body), body)
    assert msg is not None
    assert msg.severity == "OK"
    assert msg.text == "R1: 270000, R2: 44000"

    body = _cfg_body(struct.pack(">IIB", 270000, 44000, REVISION_2_2))
    msg = decode_id_frame(len(body), body)
    assert msg is not None
    assert "Override HW" in msg.text
    assert "Bandgap" not in msg.text


def test_decode_msg_ok_cfg_other_lengths_still_rejected() -> None:
    """Only the 9- and 11-byte forms decode; any other length is refused."""
    import struct

    from firestarter.codec import decode_id_frame

    for params in (
        struct.pack(">II", 270000, 44000),
        struct.pack(">IIBB", 270000, 44000, 0xFF, 1),
        struct.pack(">IIBHB", 270000, 44000, 0xFF, 1100, 0),
    ):
        body = _cfg_body(params)
        assert decode_id_frame(len(body), body) is None


def test_format_msg_ok_cfg_legacy_three_params_has_no_bandgap() -> None:
    out = format_message(MSG_OK_CFG, [1000, 2000, 0xFF], CATALOG[MSG_OK_CFG])
    assert out == "R1: 1000, R2: 2000"
