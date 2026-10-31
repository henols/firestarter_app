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
   through a fake serial port (`tests/conftest.py`'s `connect_via` /
   `fresh_serial_and_comm`), proving the refusal fires (or does not) at the real host
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
from firestarter.exceptions import ProgrammerNotFoundError
from firestarter.messages import MSG_END_DONE, MSG_INIT_DONE, MSG_MAIN_DONE
from firestarter.write_blank_guard import (
    GUARDED_PROTOCOL_IDS,
    NAMED_EXEMPT_PROTOCOL_IDS,
    NOR_UNLOCK_PROTOCOL_ID,
    SRAM_PROTOCOL_IDS,
    effective_flags,
    is_erase_exempt,
    is_guarded_protocol,
    refusal_text,
    requires_blank_check,
)

from .conftest import (
    build_frame,
    connect_via,
    fresh_serial_and_comm,
    read_phase_frames,
    write_success_frames,
)


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


def _sst39sf020_data() -> dict:
    """A real, resolved SST39SF020 wire dict -- protocol 0x06 / algorithm 6
    (`NOR_UNLOCK_PROTOCOL_ID`), flags 2 (`FLAG_CAN_ERASE` set), a
    262144-byte device: 205-CR-01's headline chip, the one validated
    protocol-0x06 part (D-06)."""
    db = EpromDatabase(skip_local_override=True)
    return resolve_chip("sst39sf020", db=db)


def _am28f256_data() -> dict:
    """A real, resolved AM28F256 wire dict -- protocol 0x10 / algorithm 16
    (Intel flash), flags 2 (`FLAG_CAN_ERASE` set), a 32768-byte device:
    205-CR-01's control chip -- its erase scope does not read the address
    (`flash_intel_erase_execute` writes to hard-coded address 0), so the
    exemption must survive at every address."""
    db = EpromDatabase(skip_local_override=True)
    return resolve_chip("am28f256", db=db)


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


# ---------------------------------------------------------------------------
# Unit level: 205-CR-01, the address dimension (WR-02)
# ---------------------------------------------------------------------------


def test_is_erase_exempt_is_false_for_nor_unlock_at_a_non_zero_address() -> None:
    """The gap's headline leg. `flash_nor_unlock_erase_execute`
    (`firestarter_fw/src/proms/flash_nor_unlock.cpp:111-119`) takes its
    `handle->address != 0` SECTOR-erase branch here, not the whole-chip
    branch the address-blind exemption assumed -- so the exemption must not
    hold."""
    d = {"algorithm": NOR_UNLOCK_PROTOCOL_ID, "flags": 2}
    assert is_erase_exempt(d, 0, address=0x10000) is False


def test_is_erase_exempt_is_true_for_nor_unlock_at_address_zero() -> None:
    """The companion the gap explicitly requires: at address 0,
    `flash_nor_unlock_erase_execute` takes its whole-chip `FLASH_ERASE`
    branch, so the exemption is still sound there."""
    d = {"algorithm": NOR_UNLOCK_PROTOCOL_ID, "flags": 2}
    assert is_erase_exempt(d, 0, address=0) is True


def test_is_erase_exempt_is_true_for_intel_flash_at_every_address() -> None:
    """`flash_intel_erase_execute` (`flash_intel.cpp:105-114`) writes its
    erase setup and confirm bytes to hard-coded address `0` regardless of
    `handle->address` -- the exemption is address-independent for algorithm
    16 and must survive at both address 0 and a non-zero address."""
    d = {"algorithm": 16, "flags": 2}
    assert is_erase_exempt(d, 0, address=0) is True
    assert is_erase_exempt(d, 0, address=0x10000) is True


def test_is_erase_exempt_default_address_is_zero() -> None:
    """Calling without the `address` keyword must give the identical
    verdict to `address=0` for a protocol-0x06 dict -- this is what keeps
    every pre-205-CR-01 caller and test unchanged."""
    d = {"algorithm": NOR_UNLOCK_PROTOCOL_ID, "flags": 2}
    assert is_erase_exempt(d, 0) == is_erase_exempt(d, 0, address=0) is True


def test_is_erase_exempt_skip_erase_rearms_before_the_protocol_test_is_reached() -> (
    None
):
    """`FLAG_SKIP_ERASE` set, algorithm 6, non-zero address: `False`,
    reached through the erase-claim short-circuit (D-03's pre-existing
    re-arming) rather than through the new protocol/address branch."""
    d = {"algorithm": NOR_UNLOCK_PROTOCOL_ID, "flags": 2}
    assert is_erase_exempt(d, FLAG_SKIP_ERASE, address=0x10000) is False


def test_requires_blank_check_is_true_for_nor_unlock_at_a_non_zero_address() -> None:
    """The caller-level pair, proving the withdrawn exemption propagates
    through to the top-level verdict."""
    d = {"algorithm": NOR_UNLOCK_PROTOCOL_ID, "flags": 2}
    assert requires_blank_check(d, 0, address=0x10000) is True


def test_requires_blank_check_blank_check_requested_false_still_bypasses_at_a_non_zero_nor_unlock_address() -> (
    None
):
    """D-G2/D-09: `-b` still wins over the new address-aware narrowing --
    `blank_check_requested` short-circuits before the exemption test is
    ever reached."""
    d = {"algorithm": NOR_UNLOCK_PROTOCOL_ID, "flags": 2}
    assert (
        requires_blank_check(d, 0, address=0x10000, blank_check_requested=False)
        is False
    )


def test_nor_unlock_protocol_id_is_a_member_of_the_guarded_set() -> None:
    """The cheap anti-drift link between the new constant and the
    deliberately-literal `GUARDED_PROTOCOL_IDS` frozenset (D-01, Phase
    203) -- this module does not modify that pin, only adds a sibling
    constant naming one of its existing members."""
    assert NOR_UNLOCK_PROTOCOL_ID in GUARDED_PROTOCOL_IDS


def test_requires_blank_check_true_for_guarded_non_exempt() -> None:
    assert requires_blank_check({"algorithm": 7, "flags": 0}, 0) is True


def test_requires_blank_check_false_with_blank_check_requested_false() -> None:
    """FWBLANK-04 (Phase 205): the bypass is now the explicit keyword-only
    `blank_check_requested=False` signal, not a wire bit -- the retired
    flag (0x08) no longer exists on either ladder."""
    assert (
        requires_blank_check(
            {"algorithm": 7, "flags": 0}, 0, blank_check_requested=False
        )
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


def test_incomplete_refusal_text_is_exactly_one_line_with_the_coverage() -> None:
    """203-REVIEW WR-02 / 207.1 D-07: an incomplete guard read (`bad == 0`,
    `compared < total`) names no fabricated address or value -- it states
    the coverage instead."""
    from firestarter.write_blank_guard import incomplete_refusal_text

    text = incomplete_refusal_text("m27c512", 32, 64)
    assert text == (
        "Refusing write to M27C512: the blank check read only 32 of 64 "
        "bytes; every byte it read was blank, but the region is not proven "
        "blank."
    )
    assert "\n" not in text


def test_incomplete_refusal_text_carries_no_remedy_clause() -> None:
    """D-10's no-remedy property, carried over to the incomplete-read
    refusal (207.1 D-07)."""
    from firestarter.write_blank_guard import incomplete_refusal_text

    text = incomplete_refusal_text("m27c512", 32, 64)
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


def _scripted_connections(*scripts: list[bytes]):
    """A communicator factory whose n-th call connects to a fresh fake port
    fed `scripts[n]`.

    Every operation opens (and, on completion, closes) its own port (D-17),
    so each connection needs its own fake port: a port the first operation
    closed cannot serve a second one.
    """
    factories = []
    for script in scripts:
        serial, factory = fresh_serial_and_comm()
        for frame in script:
            serial.feed(frame)
        factories.append(factory)
    pending = iter(factories)
    return lambda: next(pending)()


def _drive_write_eprom(
    tmp_path,
    *,
    eprom_name: str,
    eprom_data: dict,
    payload: bytes,
    frame_scripts: list[list[bytes]],
    address_str: str | None = None,
    operation_flags: int = 0,
    blank_check_requested: bool = True,
) -> tuple[bool, list[int]]:
    """Drive the genuine `EpromOperator.write_eprom` through fake serial ports.

    `frame_scripts` is a list of already-built frame sequences (from
    `read_phase_frames`/`write_success_frames`) -- one entry per connection
    `write_eprom` opens, in order (the guard's own read, then the write
    itself, when both happen). Each entry gets its own fake port (see
    `_scripted_connections`).

    Returns `(ok, opened)`: `write_eprom`'s return value, and the list of
    `command_dict["cmd"]` values `find_and_connect` observed, in call
    order.
    """
    input_file = tmp_path / f"wbg_{id(frame_scripts)}_{len(payload)}.bin"
    input_file.write_bytes(payload)

    operator = EpromOperator(ConfigManager())
    with connect_via(_scripted_connections(*frame_scripts)) as captured:
        ok = operator.write_eprom(
            eprom_name,
            eprom_data,
            str(input_file),
            operation_flags=operation_flags,
            address_str=address_str,
            blank_check_requested=blank_check_requested,
        )
    return ok, [command["cmd"] for command in captured]


def test_write_to_non_blank_region_of_guarded_part_is_refused(tmp_path, caplog) -> None:
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
            eprom_name="m27c512",
            eprom_data=_m27c512_data(),
            payload=payload,
            frame_scripts=[read_phase_frames(region_payload)],
        )

    assert ok is False
    # List equality, not `not in`: a guard that raised before any connect
    # at all would pass a bare `not in` vacuously.
    assert opened == [COMMAND_READ]
    assert COMMAND_WRITE not in opened

    error_lines = [rec.message for rec in caplog.records if rec.levelname == "ERROR"]
    assert error_lines == ["Refusing write to M27C512: not blank at 0x00000A, v: 0xAB."]


# ---------------------------------------------------------------------------
# Integration level: the positive controls (Task 2, WRITE-06 criterion 5)
# ---------------------------------------------------------------------------


def test_write_into_blank_region_of_guarded_part_succeeds(tmp_path) -> None:
    """WRITE-06 criterion 5, end to end: a write into a genuinely blank
    region of a guarded, non-erase-exempt M27C512 proceeds through the
    genuine host path -- the command sequence observed at
    `find_and_connect` is exactly [COMMAND_READ, COMMAND_WRITE], in that
    order."""
    payload = b"\xaa" * 64
    region_payload = b"\xff" * 64  # blank across the whole target region

    ok, opened = _drive_write_eprom(
        tmp_path,
        eprom_name="m27c512",
        eprom_data=_m27c512_data(),
        payload=payload,
        frame_scripts=[read_phase_frames(region_payload), write_success_frames()],
    )

    assert ok is True
    assert opened == [COMMAND_READ, COMMAND_WRITE]


def test_write_into_blank_region_of_non_blank_part_succeeds_via_host_path(
    tmp_path,
) -> None:
    """D-04, end to end: the guard reads the write's REGION only, not the
    whole device. A non-blank byte that sits outside the target region
    (here, an explicit non-zero `address_str` so the guard's own read never
    requests byte 0 at all) does not stop the write -- only what the guard
    actually reads (the region itself, all 0xFF here) governs the
    verdict."""
    payload = b"\xaa" * 64
    region_payload = b"\xff" * 64  # blank across the whole target region

    ok, opened = _drive_write_eprom(
        tmp_path,
        eprom_name="m27c512",
        eprom_data=_m27c512_data(),
        payload=payload,
        frame_scripts=[read_phase_frames(region_payload), write_success_frames()],
        address_str="0x008000",
    )

    assert ok is True
    assert opened == [COMMAND_READ, COMMAND_WRITE]


def test_write_on_erase_exempt_part_pays_no_guard_read(tmp_path) -> None:
    """An erase-capable part (W27C512, algorithm 7, flags 2 == FLAG_CAN_ERASE)
    is erase-exempt (`is_erase_exempt`) -- the guard is skipped entirely and
    the captured sequence is exactly [COMMAND_WRITE], with no guard read
    paid at all."""
    payload = b"\xaa" * 64

    ok, opened = _drive_write_eprom(
        tmp_path,
        eprom_name="w27c512",
        eprom_data=_w27c512_data(),
        payload=payload,
        frame_scripts=[write_success_frames()],
    )

    assert ok is True
    assert opened == [COMMAND_WRITE]


def test_write_at_a_non_zero_address_on_an_erase_capable_nor_unlock_part_pays_a_guard_read(
    tmp_path,
) -> None:
    """205-CR-01, the tracer's own proof. `sst39sf020` (protocol 0x06,
    `FLAG_CAN_ERASE` set, `FLAG_SKIP_ERASE` clear) is erase-exempt at
    address 0 -- `flash_nor_unlock_erase_execute`
    (`firestarter_fw/src/proms/flash_nor_unlock.cpp:111-119`) erases the
    WHOLE chip there. At a non-zero address that same firmware function
    erases only a SECTOR, so the host's address-blind exemption was
    covering a region the erase never touched. Before this task's fix, the
    observed connect sequence here is [COMMAND_WRITE] -- no guard read paid
    at all. After it, the guard pays a region-scoped read and the observed
    sequence is [COMMAND_READ, COMMAND_WRITE]."""
    payload = b"\xaa" * 64
    region_payload = b"\xff" * 64  # blank across the whole target region
    chip_data = _sst39sf020_data()
    assert chip_data["algorithm"] == NOR_UNLOCK_PROTOCOL_ID

    ok, opened = _drive_write_eprom(
        tmp_path,
        eprom_name="sst39sf020",
        eprom_data=chip_data,
        payload=payload,
        frame_scripts=[read_phase_frames(region_payload), write_success_frames()],
        address_str="0x010000",
    )

    assert ok is True
    assert opened == [COMMAND_READ, COMMAND_WRITE]


def test_write_of_zero_byte_input_file_pays_no_guard_read(tmp_path) -> None:
    """Backstop truth: a guarded write whose input file is zero bytes
    performs no guard read and behaves exactly as it does today, because
    `_drive_region_compare` returns 1 for a zero-length region and would
    otherwise refuse every empty write. `region_length` is falsy (0), so
    `last_write_guard_verdict` stays `None` and the captured sequence is
    exactly [COMMAND_WRITE]."""
    ok, opened = _drive_write_eprom(
        tmp_path,
        eprom_name="m27c512",
        eprom_data=_m27c512_data(),
        payload=b"",
        frame_scripts=[write_success_frames()],
    )

    assert ok is True
    assert opened == [COMMAND_WRITE]


def test_write_returns_false_when_guard_read_cannot_connect(tmp_path) -> None:
    """`_run_write_blank_guard` returns 2 (transport/setup failure) when its
    own `_operation_context` cannot connect -- `write_eprom` still returns
    `False` and never reaches `COMMAND_WRITE`. Covers the `if not cmd_data:
    return 2` branch inside `_run_write_blank_guard`."""
    payload = b"\xaa" * 64
    input_file = tmp_path / "wbg_transport_fail.bin"
    input_file.write_bytes(payload)

    operator = EpromOperator(ConfigManager())
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=ProgrammerNotFoundError(
            "No compatible programmer found on any port."
        ),
    ):
        ok = operator.write_eprom(
            "m27c512",
            _m27c512_data(),
            str(input_file),
        )

    assert ok is False
    assert operator.last_write_guard_verdict == 2


def test_write_with_incomplete_guard_read_is_refused_and_no_write_reaches_the_wire(
    tmp_path, caplog
) -> None:
    """Gap G1 (advisory WR-02): `_drive_region_compare` fails CLOSED when
    the guard's own read stream ends before the whole target region
    arrived -- `result.bad == 0` (no divergent byte was ever seen) but
    `result.compared != result.total`, so the branch at the end of
    `_drive_region_compare` returns verdict 1, not 0. The safety property
    under test: an INCOMPLETE guard read REFUSES the write exactly like a
    genuinely non-blank one does -- it must never be treated as a pass.

    Driven by feeding the guard's read phase fewer DATA_CHUNK bytes (32)
    than the write's own region_length (64) -- every delivered byte is
    0xFF (matches the blank expectation), so no mismatch is ever recorded,
    but the read still completes its own INIT/MAIN/END sequence normally
    (`is_ok` is True at the state-machine level; nothing timed out). This
    is what makes it a SHORT read, not a hardware/transport failure --
    `last_write_guard_verdict` must read 1 (host-decided refusal), never 2
    (transport failure)."""
    payload = b"\xaa" * 64
    short_region_payload = b"\xff" * 32  # 32 bytes delivered, region wants 64

    with caplog.at_level("ERROR", logger="EpromOperator"):
        ok, opened = _drive_write_eprom(
            tmp_path,
            eprom_name="m27c512",
            eprom_data=_m27c512_data(),
            payload=payload,
            frame_scripts=[read_phase_frames(short_region_payload)],
        )

    # Safety property 1: refused, not allowed.
    assert ok is False
    # Safety property 2: no COMMAND_WRITE ever reached find_and_connect.
    assert opened == [COMMAND_READ]
    assert COMMAND_WRITE not in opened

    # 203-REVIEW WR-02, fixed by 207.1 D-07: with no observed divergent
    # byte, `first_offset` and `first_actual` are both `None`, so the
    # refusal states the compare's COVERAGE instead of inventing an
    # address and a value.
    error_lines = [rec.message for rec in caplog.records if rec.levelname == "ERROR"]
    assert error_lines == [
        "Refusing write to M27C512: the blank check read only 32 of 64 "
        "bytes; every byte it read was blank, but the region is not proven "
        "blank."
    ]

    # Safety property 3: this is a host-decided refusal (verdict 1), not a
    # transport failure (verdict 2) -- the read itself completed cleanly at
    # the state-machine level, it just never covered the whole region.
    operator = EpromOperator(ConfigManager())
    # Re-derive the verdict the same way write_eprom does, using a second,
    # freshly instrumented operator so this assertion is not coupled to
    # `_drive_write_eprom`'s helper internals.
    input_file = tmp_path / "wbg_short_read_verdict.bin"
    input_file.write_bytes(payload)
    with connect_via(_scripted_connections(read_phase_frames(short_region_payload))):
        ok2 = operator.write_eprom("m27c512", _m27c512_data(), str(input_file))
    assert ok2 is False
    assert operator.last_write_guard_verdict == 1


def test_write_with_blank_check_requested_false_pays_no_guard_read(tmp_path) -> None:
    """WRITE-03 / D-09: `blank_check_requested=False` (FWBLANK-04's
    explicit-keyword replacement for the retired skip-blank-check wire
    bit) bypasses the guard on an otherwise-guarded, non-exempt M27C512 --
    the captured sequence is exactly [COMMAND_WRITE], no guard read paid."""
    payload = b"\xaa" * 64

    ok, opened = _drive_write_eprom(
        tmp_path,
        eprom_name="m27c512",
        eprom_data=_m27c512_data(),
        payload=payload,
        frame_scripts=[write_success_frames()],
        blank_check_requested=False,
    )

    assert ok is True
    assert opened == [COMMAND_WRITE]


# ---------------------------------------------------------------------------
# Plan 02 Task 2 -- the bypass, the re-arming, and the two dev callers
# ---------------------------------------------------------------------------


def test_write_with_skip_erase_on_erase_capable_part_refuses_a_non_blank_region(
    tmp_path,
) -> None:
    """D-03, end to end: `--skip-erase` re-arms the guard on an
    otherwise-erase-exempt W27C512 -- the exemption's premise ("the erase
    immediately above the check already guarantees blank") is false when
    the erase itself is skipped, so the guard fires exactly as it would on
    a non-exempt part. Captured sequence is `[COMMAND_READ]`, no
    `COMMAND_WRITE` ever reached."""
    from firestarter.constants import FLAG_SKIP_ERASE

    payload = b"\xaa" * 64
    region_payload = bytearray(b"\xff" * 64)
    region_payload[5] = 0x11
    region_payload = bytes(region_payload)

    ok, opened = _drive_write_eprom(
        tmp_path,
        eprom_name="w27c512",
        eprom_data=_w27c512_data(),
        payload=payload,
        frame_scripts=[read_phase_frames(region_payload)],
        operation_flags=FLAG_SKIP_ERASE,
    )

    assert ok is False
    assert opened == [COMMAND_READ]


def test_write_no_blank_check_via_build_op_flags_on_erase_capable_part_pays_no_guard_read(
    tmp_path,
) -> None:
    """The `-b` integration leg: drives `write_eprom` with the REAL
    `_build_op_flags(blank_check=False)` output for `operation_flags`
    (which FWBLANK-04 makes a no-op wire-wise -- it composes no bit at all
    now) PLUS `blank_check_requested=False`, the exact combination
    `cli_handlers.write`'s `-b` option now produces, proving the
    CLI-flag-to-guard plumbing end to end on an erase-capable part.
    Captured sequence is exactly `[COMMAND_WRITE]`, no read paid."""
    from firestarter.cli_handlers import _build_op_flags

    payload = b"\xaa" * 64

    ok, opened = _drive_write_eprom(
        tmp_path,
        eprom_name="w27c512",
        eprom_data=_w27c512_data(),
        payload=payload,
        frame_scripts=[write_success_frames()],
        operation_flags=_build_op_flags(blank_check=False),
        blank_check_requested=False,
    )

    assert ok is True
    assert opened == [COMMAND_WRITE]


def _erase_phase_frames() -> list[bytes]:
    """Wire frames for one complete, otherwise-successful COMMAND_ERASE
    main phase -- `_main_phase_simple`'s shape: no data-request frame, no
    data chunks, just INIT/MAIN/END completion."""
    return [
        build_frame(MSG_INIT_DONE, b""),
        build_frame(MSG_MAIN_DONE, b""),
        build_frame(MSG_END_DONE, b""),
    ]


def test_write_cycle_eprom_for_an_erase_capable_part_performs_no_guard_read(
    tmp_path,
) -> None:
    """RESEARCH section 11.2 / D-07's flag-proxy argument: `write_cycle_eprom`
    erases immediately before every write, so the exemption's premise holds
    on every part this command can actually run on -- proven here through
    the real `write_eprom` call inside `write_cycle_eprom`, not asserted in
    isolation. The captured sequence is `[COMMAND_ERASE, COMMAND_WRITE,
    COMMAND_READ]` -- erase, then write with NO guard read in between, then
    write_cycle_eprom's own read-back (a real read this command always
    performs, unrelated to the blank guard)."""
    from firestarter.constants import COMMAND_ERASE

    # A small, custom memory-size keeps the read-back frame script short --
    # real W27C512 is 65536 bytes; only the erase-exempt flag combination
    # (algorithm 7, FLAG_CAN_ERASE) matters for this test, not the real size.
    eprom_data = dict(_w27c512_data())
    eprom_data["memory-size"] = 16

    payload = b"\xaa" * 16
    source_file = tmp_path / "wbg_cycle_source.bin"
    source_file.write_bytes(payload)

    connections = _scripted_connections(
        _erase_phase_frames(),
        write_success_frames(),
        read_phase_frames(payload),
    )
    operator = EpromOperator(ConfigManager())
    output_dir = tmp_path / "write-cycle-output"
    with connect_via(connections) as captured:
        verdict = operator.write_cycle_eprom(
            "w27c512",
            eprom_data,
            str(source_file),
            runs=1,
            output_dir=str(output_dir),
        )

    assert verdict == 0
    opened = [command["cmd"] for command in captured]
    assert opened == [COMMAND_ERASE, COMMAND_WRITE, COMMAND_READ]


# ---------------------------------------------------------------------------
# Regression: the slice moved nothing else (Task 3)
# ---------------------------------------------------------------------------


def _drive_compare(make_comm, fake_serial, chunk: bytes, call):
    """Run one compare operation against a fake board that reads `chunk`."""
    for frame in read_phase_frames(chunk):
        fake_serial.feed(frame)
    operator = EpromOperator(ConfigManager())
    with connect_via(make_comm):
        return call(operator)


def test_verify_eprom_hands_its_compare_result_to_on_result(
    make_comm, fake_serial, tmp_path
) -> None:
    """Phase 206 Task 3 (DEVTEST-02): `verify_eprom` passes the host
    compare engine's result to `on_result`. `chip_test.py` relies on this to
    see that a verify step's comparison reached the engine (`compare_path`)."""
    expected = b"\x01\x02\x03\x04"
    input_file = tmp_path / "expected.bin"
    input_file.write_bytes(expected)
    results: list[CompareResult] = []

    verdict = _drive_compare(
        make_comm,
        fake_serial,
        b"\x01\x02\x00\x04",
        lambda op: op.verify_eprom(
            "at28c256", _at28c256_data(), str(input_file), on_result=results.append
        ),
    )

    assert verdict == 1
    assert len(results) == 1
    assert (results[0].compared, results[0].bad) == (4, 1)


def test_check_eprom_blank_hands_its_compare_result_to_on_result(
    make_comm, fake_serial
) -> None:
    """Phase 206 Task 1 (DEVTEST-01): `check_eprom_blank` passes the host
    compare engine's result to `on_result`."""
    results: list[CompareResult] = []

    verdict = _drive_compare(
        make_comm,
        fake_serial,
        b"\xff" * 8,
        lambda op: op.check_eprom_blank(
            "W27C512",
            {"memory-size": 8, "flags": 0, "cmd": 1},
            on_result=results.append,
        ),
    )

    assert verdict == 0
    assert len(results) == 1
    assert (results[0].compared, results[0].bad) == (8, 0)


def test_blank_run_on_non_blank_chip_still_emits_mismatch_line(
    make_comm, fake_serial, caplog
) -> None:
    """The default `on_result=None` path is exercised too --
    `check_eprom_blank` on a non-blank fake still renders its `Mismatch`
    range line through `render_compare_lines`, exactly as before this
    plan's `on_result` parameter existed."""
    payload = bytearray(b"\xff" * 8)
    payload[3] = 0x00
    payload = bytes(payload)
    for frame in read_phase_frames(payload):
        fake_serial.feed(frame)

    operator = EpromOperator(ConfigManager())
    with (
        caplog.at_level("INFO", logger="EpromOperator"),
        connect_via(make_comm),
    ):
        verdict = operator.check_eprom_blank(
            "W27C512", {"memory-size": 8, "flags": 0, "cmd": 1}
        )

    assert verdict == 1
    messages = [rec.message for rec in caplog.records]
    assert any("Mismatch 0x000003-0x000003 (1 bytes)" in m for m in messages)


def test_predicate_fires_against_real_resolve_chip_dicts() -> None:
    """A predicate whose unit tests pass against hand-built literals while a
    real wire dict never reaches it is the exact failure this guards
    against. Drive `is_guarded_protocol` and `requires_blank_check` with a
    dict from the REAL `resolve_chip` for one guarded part (M27C512) and one
    unguarded part (AT28C256, algorithm 13 / D-01 Fork A)."""
    guarded = _m27c512_data()
    unguarded = _at28c256_data()

    assert is_guarded_protocol(guarded) is True
    assert requires_blank_check(guarded, 0) is True

    assert is_guarded_protocol(unguarded) is False
    assert requires_blank_check(unguarded, 0) is False


def test_address_aware_exemption_fires_against_real_resolve_chip_dicts() -> None:
    """205-CR-01's own form of the doctrine directly above: a predicate
    whose unit tests pass against hand-built `{"algorithm": 6, ...}`
    literals while a real resolved wire dict never reaches it is the exact
    failure to guard against. `sst39sf020` (algorithm 6) requires a check
    at a non-zero address and not at address 0; `am28f256` (algorithm 16)
    requires no check at either."""
    nor_unlock = _sst39sf020_data()
    intel_flash = _am28f256_data()

    assert requires_blank_check(nor_unlock, 0, address=0x10000) is True
    assert requires_blank_check(nor_unlock, 0, address=0) is False
    assert requires_blank_check(intel_flash, 0, address=0x10000) is False


# ---------------------------------------------------------------------------
# Integration level: 205-CR-01, the address dimension end to end (WR-02)
# ---------------------------------------------------------------------------


def test_write_at_address_zero_on_an_erase_capable_nor_unlock_part_still_pays_no_guard_read(
    tmp_path,
) -> None:
    """The leg that proves the fix is a narrowing and not a blanket
    withdrawal: at address 0, `flash_nor_unlock_erase_execute` erases the
    WHOLE chip, so the exemption still holds and no guard read is paid --
    the observed sequence is exactly [COMMAND_WRITE]."""
    payload = b"\xaa" * 64

    ok, opened = _drive_write_eprom(
        tmp_path,
        eprom_name="sst39sf020",
        eprom_data=_sst39sf020_data(),
        payload=payload,
        frame_scripts=[write_success_frames()],
    )

    assert ok is True
    assert opened == [COMMAND_WRITE]


def test_write_at_a_non_zero_address_on_a_non_blank_nor_unlock_region_is_refused(
    tmp_path, caplog
) -> None:
    """The restored safety property itself, end to end: at a non-zero
    address, the withdrawn exemption routes to the ordinary region-scoped
    guard read (D-G2), and a genuinely non-blank region is refused exactly
    as `test_write_to_non_blank_region_of_guarded_part_is_refused` proves
    for the pre-existing guarded families -- `write_eprom` returns `False`
    and `COMMAND_WRITE` never reaches `find_and_connect`."""
    payload = b"\xaa" * 64
    region_payload = bytearray(b"\xff" * 64)
    region_payload[10] = 0xAB  # the one non-blank byte inside the region
    region_payload = bytes(region_payload)

    with caplog.at_level("ERROR", logger="EpromOperator"):
        ok, opened = _drive_write_eprom(
            tmp_path,
            eprom_name="sst39sf020",
            eprom_data=_sst39sf020_data(),
            payload=payload,
            frame_scripts=[read_phase_frames(region_payload)],
            address_str="0x010000",
        )

    assert ok is False
    assert opened == [COMMAND_READ]
    assert COMMAND_WRITE not in opened


def test_write_with_an_unparseable_address_keeps_its_existing_error_contract(
    tmp_path,
) -> None:
    """D-G4: an unparseable `-a` must not be "hardened" into a guard
    refusal. `write_eprom`'s own address resolution (this task's
    `guard_address`) treats a `ValueError` as address 0, so the guard
    itself pays no read (erase-exempt at address 0, same as
    `test_write_at_address_zero...` above). The write's own connect then
    fails the same way it does today: `_setup_operation` re-parses
    `address_str`, raises `ValueError`, and returns `(None, 0)` BEFORE
    `find_and_connect` is ever called -- no connection of any kind is
    opened, and `write_eprom` returns `False` with
    `last_write_attempt_verdict == 1` (the malformed-address cause,
    distinct from a transport failure)."""
    payload = b"\xaa" * 64

    input_file = tmp_path / "wbg_unparseable_address.bin"
    input_file.write_bytes(payload)

    operator = EpromOperator(ConfigManager())
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=AssertionError(
            "find_and_connect must not be called for an unparseable address"
        ),
    ):
        ok = operator.write_eprom(
            "sst39sf020",
            _sst39sf020_data(),
            str(input_file),
            address_str="not-an-address",
        )

    assert ok is False
    assert operator.last_write_attempt_verdict == 1
