"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 203 Plan 01 -- the host refuses a non-blank write, end to end
(WRITE-01 criterion 1) -- and the paired positive control, a blank region
of a non-blank part still writes (WRITE-06 criterion 5, Plan 01 Task 2).

Two layers of coverage:

1. Unit-level: `write_blank_guard`'s three predicates and its refusal text,
   and `compare.py`'s additive `CompareResult.first_actual` field -- all
   pure, no board, no serial.
2. Integration-level: the genuine `EpromOperator.write_eprom` driven
   through a fake serial port (`tests/conftest.py`'s `_FakeSerial` /
   `make_comm`), proving the refusal fires (or does not) at the real host
   path, not against a double. `tests/fake_chip.py`'s `WriteInitPreflightChip`
   models the FIRMWARE write-init pre-flight only (its own docstring says
   so) and would pass regardless of what the real guard does -- it cannot
   provide this coverage.
"""

from __future__ import annotations

from unittest.mock import patch

from firestarter.chip_resolver import resolve_chip
from firestarter.compare import CompareAccumulator, CompareResult
from firestarter.config import ConfigManager
from firestarter.constants import COMMAND_READ, COMMAND_WRITE, FLAG_SKIP_ERASE
from firestarter.database import EpromDatabase
from firestarter.eprom_operations import EpromOperator
from firestarter.messages import (
    MSG_DATA_CHUNK,
    MSG_END_DONE,
    MSG_INIT_DONE,
    MSG_MAIN_DONE,
    MSG_OK_REQ_DATA,
)
from firestarter.write_blank_guard import (
    GUARDED_PROTOCOL_IDS,
    NAMED_EXEMPT_PROTOCOL_IDS,
    SRAM_PROTOCOL_IDS,
    effective_flags,
    is_erase_exempt,
    is_guarded_protocol,
    refusal_text,
    requires_blank_check,
)

from .conftest import build_frame

# The Uno-floor default `_calculate_buffer_size()` returns when
# `firmware_max_chunk` is unset (`make_comm`'s default) -- used here only to
# keep the READ script's chunking realistic (multi-chunk address tracking
# exercised for a region larger than one chunk); the firmware, not the
# host, actually decides a READ's chunk boundaries.
_BUFFER_SIZE = 512


def _m27c512_data() -> dict:
    """A real, resolved M27C512 wire dict -- protocol 0x07 / algorithm 7,
    flags 0 (no FLAG_CAN_ERASE): guarded, non-erase-exempt (WRITE-01's
    negative control chip)."""
    db = EpromDatabase(skip_local_override=True)
    return resolve_chip("m27c512", db=db)


def _w27c512_data() -> dict:
    """A real, resolved W27C512 wire dict -- protocol 0x07 / algorithm 7,
    flags 2 (FLAG_CAN_ERASE set): guarded protocol, but erase-exempt
    (WRITE-06's erase-exempt positive control)."""
    db = EpromDatabase(skip_local_override=True)
    return resolve_chip("w27c512", db=db)


def _at28c256_data() -> dict:
    """A real, resolved AT28C256 wire dict -- protocol 0x0D / algorithm 13:
    not in GUARDED_PROTOCOL_IDS, so unguarded per D-01/Fork A."""
    db = EpromDatabase(skip_local_override=True)
    return resolve_chip("at28c256", db=db)


# ---------------------------------------------------------------------------
# Unit level: the three predicates + refusal_text (pure, no board)
# ---------------------------------------------------------------------------


def test_guarded_protocol_ids_pinned() -> None:
    """D-02: this test pins the EXACT guarded set -- it must fail if the
    set narrows or widens."""
    assert GUARDED_PROTOCOL_IDS == frozenset({6, 7, 8, 11, 16})


def test_is_guarded_protocol_true_for_every_guarded_id() -> None:
    for algorithm in (6, 7, 8, 11, 16):
        assert is_guarded_protocol({"algorithm": algorithm}) is True, algorithm


def test_is_guarded_protocol_false_for_every_named_exemption() -> None:
    # 5 = flash4, 13 = SDP/28C, 14/39/40/41 = SRAM/FRAM.
    for algorithm in (5, 13, 14, 39, 40, 41):
        assert is_guarded_protocol({"algorithm": algorithm}) is False, algorithm


def test_is_guarded_protocol_fails_closed_on_absent_evidence() -> None:
    assert is_guarded_protocol(None) is True
    assert is_guarded_protocol({}) is True
    assert is_guarded_protocol({"algorithm": None}) is True


def test_named_exempt_protocol_ids_is_the_documented_union() -> None:
    from firestarter.flash4_erase_gate import FLASH4_PROTOCOL_ID
    from firestarter.sdp_capability import SDP_PROTOCOL_ID

    assert NAMED_EXEMPT_PROTOCOL_IDS == SRAM_PROTOCOL_IDS | {
        FLASH4_PROTOCOL_ID,
        SDP_PROTOCOL_ID,
    }
    assert GUARDED_PROTOCOL_IDS.isdisjoint(NAMED_EXEMPT_PROTOCOL_IDS)


def test_effective_flags_ors_operation_flags_over_the_wire_dict_default() -> None:
    assert effective_flags(None, 5) == 5
    assert effective_flags({}, 5) == 5
    assert effective_flags({"flags": 2}, 4) == 6


def test_is_erase_exempt_true_when_can_erase_set_and_skip_erase_clear() -> None:
    assert is_erase_exempt({"flags": 2}, 0) is True


def test_is_erase_exempt_false_when_skip_erase_re_arms_the_guard() -> None:
    """D-03: --skip-erase re-arms the guard on an erase-capable part."""
    assert is_erase_exempt({"flags": 2}, FLAG_SKIP_ERASE) is False


def test_is_erase_exempt_false_when_can_erase_not_set() -> None:
    assert is_erase_exempt({"flags": 0}, 0) is False


def test_requires_blank_check_true_for_guarded_non_exempt() -> None:
    assert requires_blank_check({"algorithm": 7, "flags": 0}, 0) is True


def test_requires_blank_check_false_with_skip_blank_check_flag() -> None:
    from firestarter.constants import FLAG_SKIP_BLANK_CHECK

    assert (
        requires_blank_check({"algorithm": 7, "flags": 0}, FLAG_SKIP_BLANK_CHECK)
        is False
    )


def test_refusal_text_is_exactly_one_line_with_address_and_value() -> None:
    text = refusal_text("m27c512", 0x008000, 0xAB)
    assert text == "Refusing write to M27C512: not blank at 0x008000, v: 0xAB."
    assert "\n" not in text


def test_refusal_text_carries_no_remedy_clause() -> None:
    """D-10: no mention of the bypass flag, no remedy clause."""
    text = refusal_text("m27c512", 0, 0)
    for forbidden in ("-b", "--no-blank-check", "bypass", "skip"):
        assert forbidden not in text.lower(), (forbidden, text)


def test_predicate_module_reads_only_algorithm_and_flags_keys() -> None:
    """The module must read ONLY the `algorithm` and `flags` keys of the
    wire dict -- never `electrical-type`, never `protocol-id` (the keys
    `check_eprom_blank`'s inert SRAM short-circuit reads instead)."""
    for bad_key in ("electrical-type", "protocol-id"):
        d = {"algorithm": 7, "flags": 0, bad_key: "SRAM"}
        # A dict carrying an extra, unread key must produce the identical
        # verdict as one without it -- the module never even looks.
        d_without = {"algorithm": 7, "flags": 0}
        assert requires_blank_check(d, 0) == requires_blank_check(d_without, 0)


# ---------------------------------------------------------------------------
# Unit level: compare.py's additive `first_actual` field
# ---------------------------------------------------------------------------


def test_compare_result_has_first_actual_field() -> None:
    assert "first_actual" in CompareResult.__dataclass_fields__


def test_first_actual_none_on_a_clean_stream() -> None:
    acc = CompareAccumulator(addr_base=0)
    acc.feed(0, b"\xff\xff", b"\xff\xff")
    result = acc.finalise(aborted=False)
    assert result.first_actual is None


def test_first_actual_carries_the_byte_value_at_first_offset() -> None:
    acc = CompareAccumulator(addr_base=0)
    acc.feed(0, b"\xff\xff\xff", b"\xff\xab\xff")
    result = acc.finalise(aborted=False)
    assert result.first_offset == 1
    assert result.first_actual == 0xAB


# ---------------------------------------------------------------------------
# Integration level: drive the genuine EpromOperator.write_eprom
# ---------------------------------------------------------------------------


def _read_phase_frames(payload: bytes) -> list[bytes]:
    """Wire frames for one complete COMMAND_READ main phase whose delivered
    payload is `payload`, in the order `_main_phase_read_data` expects:
    INIT_DONE, N x DATA_CHUNK (chunked at the Uno-floor buffer size so a
    multi-chunk region exercises address tracking), MAIN_DONE, END_DONE.

    A "test authoring error" self-check (`tests/test_write_progress.py`'s
    style): the chunk count must match ceiling division of `len(payload)`
    by `_BUFFER_SIZE`, so a future edit to this helper cannot silently
    produce zero chunks or the wrong count and pass as a false negative.
    """
    chunks = [
        payload[i : i + _BUFFER_SIZE] for i in range(0, len(payload), _BUFFER_SIZE)
    ] or [b""]
    expected_chunks = max(1, -(-len(payload) // _BUFFER_SIZE))
    assert len(chunks) == expected_chunks, (
        f"test authoring error: payload length {len(payload)} needs "
        f"{expected_chunks} chunk(s) against the {_BUFFER_SIZE}-byte Uno "
        f"floor, but chunking produced {len(chunks)}"
    )
    frames = [build_frame(MSG_INIT_DONE, b"")]
    frames += [build_frame(MSG_DATA_CHUNK, chunk) for chunk in chunks]
    frames.append(build_frame(MSG_MAIN_DONE, b""))
    frames.append(build_frame(MSG_END_DONE, b""))
    return frames


def _write_phase_frames() -> list[bytes]:
    """Wire frames for one complete, otherwise-successful COMMAND_WRITE
    main phase -- mirrors `_drive_write_eprom_for_ack_check`'s script shape
    (`tests/test_eprom_operations.py`)."""
    return [
        build_frame(MSG_INIT_DONE, b""),
        build_frame(MSG_OK_REQ_DATA, b""),
        build_frame(MSG_MAIN_DONE, b""),
        build_frame(MSG_END_DONE, b""),
    ]


def _drive_write_eprom(
    tmp_path,
    make_comm,
    fake_serial,
    *,
    eprom_name: str,
    eprom_data: dict,
    payload: bytes,
    frame_scripts: list[list[bytes]],
    address_str: str | None = None,
    operation_flags: int = 0,
) -> tuple[bool, list[int]]:
    """Drive the genuine `EpromOperator.write_eprom` through `_FakeSerial`.

    `frame_scripts` is a list of already-built frame sequences (from
    `_read_phase_frames`/`_write_phase_frames`), fed onto the fake serial
    port's SHARED buffer, in order, BEFORE the drive -- `feed()` and
    `write()` share one `BytesIO` and one write position
    (`tests/conftest.py`'s `_FakeSerial`), so a script fed mid-drive would
    desync the stream. Each connection `write_eprom` opens (the guard's own
    read, then the write itself, in that order when both happen) reads from
    this one pre-loaded stream.

    Returns `(ok, opened)`: `write_eprom`'s return value, and the list of
    `command_dict["cmd"]` values `find_and_connect` observed, in call
    order.
    """
    input_file = tmp_path / f"wbg_{id(fake_serial)}_{len(payload)}.bin"
    input_file.write_bytes(payload)

    for script in frame_scripts:
        for frame in script:
            fake_serial.feed(frame)

    opened: list[int] = []

    def _fake_find_and_connect(command_dict, config, **kwargs):
        opened.append(command_dict["cmd"])
        return make_comm()

    operator = EpromOperator(ConfigManager())
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=_fake_find_and_connect,
    ):
        ok = operator.write_eprom(
            eprom_name,
            eprom_data,
            str(input_file),
            operation_flags=operation_flags,
            address_str=address_str,
        )
    return ok, opened


def test_write_to_non_blank_region_of_guarded_part_is_refused(
    tmp_path, make_comm, fake_serial, caplog
) -> None:
    """WRITE-01 criterion 1 / D-04 / D-10 / D-11, end to end: a write to a
    non-blank region of a guarded, non-erase-exempt M27C512 is refused by
    the host with exactly one output line naming the first non-blank
    address and the byte value read there, and no COMMAND_WRITE frame ever
    reaches `find_and_connect`."""
    payload = b"\xaa" * 64
    region_payload = bytearray(b"\xff" * 64)
    region_payload[10] = 0xAB  # the one non-blank byte inside the region
    region_payload = bytes(region_payload)

    with caplog.at_level("ERROR", logger="EpromOperator"):
        ok, opened = _drive_write_eprom(
            tmp_path,
            make_comm,
            fake_serial,
            eprom_name="m27c512",
            eprom_data=_m27c512_data(),
            payload=payload,
            frame_scripts=[_read_phase_frames(region_payload)],
        )

    assert ok is False
    # List equality, not `not in`: a guard that raised before any connect
    # at all would pass a bare `not in` vacuously.
    assert opened == [COMMAND_READ]
    assert COMMAND_WRITE not in opened

    error_lines = [rec.message for rec in caplog.records if rec.levelname == "ERROR"]
    assert error_lines == ["Refusing write to M27C512: not blank at 0x00000A, v: 0xAB."]
