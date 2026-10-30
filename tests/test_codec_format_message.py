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
    MSG_ERR_VPP_HIGH_MV,
    MSG_INFO_CMD,
    MSG_INFO_HW,
    MSG_INFO_PHYSICAL_HW,
    MSG_INFO_REGISTERS,
    MSG_OK_CFG,
    MSG_OK_REV,
    MSG_WARN_VPP_HIGH_MV,
    MSG_WARN_VPP_LOW_MV,
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
    # The format is "{eff}, Detected HW: {phys}"
    assert "Detected HW" in out


def test_format_msg_ok_cfg_no_override() -> None:
    """MSG_OK_CFG with override=0xFF returns the R1/R2-only summary."""
    out = format_message(MSG_OK_CFG, [1000, 2000, 0xFF, 1100, 206], CATALOG[MSG_OK_CFG])
    assert out == "R1: 1000, R2: 2000, Bandgap: 1100 mV, Bandgap count: 206"


def test_format_msg_ok_cfg_with_override() -> None:
    """MSG_OK_CFG with a non-0xFF override appends 'Override HW: ...' via silkscreen."""
    out = format_message(
        MSG_OK_CFG, [1000, 2000, REVISION_2_2, 1019, 200], CATALOG[MSG_OK_CFG]
    )
    assert out is not None
    assert "Override HW" in out
    assert out.endswith("Bandgap: 1019 mV, Bandgap count: 200")


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


def test_decode_msg_ok_cfg_current_13_byte_form() -> None:
    """The current ack carries r1, r2, override, the bandgap and its count."""
    import struct

    from firestarter.codec import decode_id_frame

    body = _cfg_body(struct.pack(">IIBHH", 270000, 44000, 0xFF, 1100, 225))
    msg = decode_id_frame(len(body), body)
    assert msg is not None
    assert msg.text == "R1: 270000, R2: 44000, Bandgap: 1100 mV, Bandgap count: 225"


def test_decode_msg_ok_cfg_other_lengths_rejected() -> None:
    """Only the 13-byte form decodes. The 9- and 11-byte forms of firmware
    before 3.3 cannot arrive: that firmware refuses the binary frame first."""
    import struct

    from firestarter.codec import decode_id_frame

    for params in (
        struct.pack(">II", 270000, 44000),
        struct.pack(">IIB", 270000, 44000, 0xFF),
        struct.pack(">IIBH", 270000, 44000, 0xFF, 1100),
        struct.pack(">IIBB", 270000, 44000, 0xFF, 1),
        struct.pack(">IIBHB", 270000, 44000, 0xFF, 1100, 0),
    ):
        body = _cfg_body(params)
        assert decode_id_frame(len(body), body) is None


def test_format_registers_dev_reg_draws_three_tables() -> None:
    """MSG_INFO_REGISTERS view 0 (`dev reg`): MSB, LSB and an 8-bit CTRL, in
    the layout the firmware used to print, value first then D7..D0."""
    out = format_message(
        MSG_INFO_REGISTERS, [0x12, 0x34, 0x0A5, 0x5A, 0], CATALOG[MSG_INFO_REGISTERS]
    )
    assert out == "\n".join(
        [
            "MSB: 0x12",
            "|D7|D6|D5|D4|D3|D2|D1|D0|",
            "| 0| 0| 0| 1| 0| 0| 1| 0|",
            "LSB: 0x34",
            "|D7|D6|D5|D4|D3|D2|D1|D0|",
            "| 0| 0| 1| 1| 0| 1| 0| 0|",
            "CTRL: 0xA5",
            "|D7|D6|D5|D4|D3|D2|D1|D0|",
            "| 1| 0| 1| 0| 0| 1| 0| 1|",
        ]
    )


def test_format_registers_firestarter_view_has_nine_bit_ctrl_and_remap() -> None:
    out = format_message(
        MSG_INFO_REGISTERS, [0, 0, 0x1A5, 0x5A, 1], CATALOG[MSG_INFO_REGISTERS]
    )
    assert out is not None
    lines = out.split("\n")
    assert lines[6:9] == [
        "CTRL: 0x1A5",
        "|D8|D7|D6|D5|D4|D3|D2|D1|D0|",
        "| 1| 1| 0| 1| 0| 0| 1| 0| 1|",
    ]
    assert lines[9] == "CTRL remapped: 0x5A"


def test_format_registers_dev_addr_view_names_the_top_address() -> None:
    out = format_message(
        MSG_INFO_REGISTERS, [0, 0, 0x03, 0x07, 2], CATALOG[MSG_INFO_REGISTERS]
    )
    assert out is not None
    assert "TOP addr: 0x03" in out.split("\n")
    assert "CTRL remapped: 0x07" in out.split("\n")


def test_format_vpp_reports_show_volts_from_millivolts() -> None:
    """The firmware sends mV; the host shows every millivolt, where the old
    frames could only carry tenths."""
    high = format_message(
        MSG_ERR_VPP_HIGH_MV, [14449, 13000], CATALOG[MSG_ERR_VPP_HIGH_MV]
    )
    assert high == "VPP is high: 14.449 V > 13.000 V"
    forced = format_message(
        MSG_WARN_VPP_HIGH_MV, [14449, 13000], CATALOG[MSG_WARN_VPP_HIGH_MV]
    )
    assert forced == "VPP is high: 14.449 V > 13.000 V"
    low = format_message(
        MSG_WARN_VPP_LOW_MV, [12000, 13000], CATALOG[MSG_WARN_VPP_LOW_MV]
    )
    assert low == "VPP is low: 12.000 V < 13.000 V"
