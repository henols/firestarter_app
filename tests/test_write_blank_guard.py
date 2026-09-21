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

import ast
from unittest.mock import patch

from firestarter.chip_resolver import resolve_chip
from firestarter.compare import CompareAccumulator, CompareResult
from firestarter.config import ConfigManager
from firestarter.constants import COMMAND_READ, COMMAND_WRITE, FLAG_SKIP_ERASE
from firestarter.database import EpromDatabase
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import ProgrammerNotFoundError
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

from .conftest import _FakeSerial, build_frame

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


def _comm_factory_for(serial: _FakeSerial):
    """Build a `SerialCommunicator` factory wired to one specific fake
    serial port, mirroring `tests/conftest.py`'s `make_comm` fixture and
    `tests/test_write_skip_sdp_unlock.py`'s `_fresh_serial_and_comm` shape."""
    from firestarter.serial_comm import SerialCommunicator

    def _factory():
        instance = SerialCommunicator.__new__(SerialCommunicator)
        instance.connection = serial
        instance.port_name = "/dev/null"
        instance.baud_rate = 250000
        instance.timeout = 0.1
        instance.programmer_info = None
        instance._fault_inject_outgoing = None
        instance.firmware_buffer_size = None
        instance.firmware_max_chunk = None
        instance.firmware_identity = None
        instance.hw_revision = None
        instance.write_block_budget_s = None
        instance.seen_message_ids = set()
        return instance

    return _factory


def _drive_write_eprom(
    tmp_path,
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
    `_read_phase_frames`/`_write_phase_frames`) -- one entry per connection
    `write_eprom` opens, in order (the guard's own read, then the write
    itself, when both happen). Each entry gets its OWN fresh `_FakeSerial`
    instance, mirroring D-17's reality that every operation opens (and, on
    completion, closes) its own port. Sharing a single fake serial port
    across two connections desyncs it: the first connection's
    `_disconnect_programmer()` closes the shared fake port
    (`_FakeSerial.close()` sets `is_open = False`), so a second connection
    reusing it fails with "Not connected" -- the exact pitfall
    `tests/test_write_skip_sdp_unlock.py::_fresh_serial_and_comm` documents.

    Returns `(ok, opened)`: `write_eprom`'s return value, and the list of
    `command_dict["cmd"]` values `find_and_connect` observed, in call
    order.
    """
    input_file = tmp_path / f"wbg_{id(frame_scripts)}_{len(payload)}.bin"
    input_file.write_bytes(payload)

    factories = []
    for script in frame_scripts:
        serial = _FakeSerial()
        for frame in script:
            serial.feed(frame)
        factories.append(_comm_factory_for(serial))
    pending = iter(factories)

    opened: list[int] = []

    def _fake_find_and_connect(command_dict, config, **kwargs):
        opened.append(command_dict["cmd"])
        return next(pending)()

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
            frame_scripts=[_read_phase_frames(region_payload)],
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
        frame_scripts=[_read_phase_frames(region_payload), _write_phase_frames()],
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
        frame_scripts=[_read_phase_frames(region_payload), _write_phase_frames()],
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
        frame_scripts=[_write_phase_frames()],
    )

    assert ok is True
    assert opened == [COMMAND_WRITE]


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
        frame_scripts=[_write_phase_frames()],
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

    def _raise_not_found(command_dict, config, **kwargs):
        raise ProgrammerNotFoundError("No compatible programmer found on any port.")

    operator = EpromOperator(ConfigManager())
    with patch(
        "firestarter.serial_comm.SerialCommunicator.find_and_connect",
        side_effect=_raise_not_found,
    ):
        ok = operator.write_eprom(
            "m27c512",
            _m27c512_data(),
            str(input_file),
        )

    assert ok is False
    assert operator.last_write_guard_verdict == 2


def test_write_with_skip_blank_check_flag_pays_no_guard_read(tmp_path) -> None:
    """WRITE-03 / D-09: `FLAG_SKIP_BLANK_CHECK` bypasses the guard on an
    otherwise-guarded, non-exempt M27C512 -- the captured sequence is
    exactly [COMMAND_WRITE], no guard read paid."""
    from firestarter.constants import FLAG_SKIP_BLANK_CHECK

    payload = b"\xaa" * 64

    ok, opened = _drive_write_eprom(
        tmp_path,
        eprom_name="m27c512",
        eprom_data=_m27c512_data(),
        payload=payload,
        frame_scripts=[_write_phase_frames()],
        operation_flags=FLAG_SKIP_BLANK_CHECK,
    )

    assert ok is True
    assert opened == [COMMAND_WRITE]


# ---------------------------------------------------------------------------
# Regression: the slice moved nothing else (Task 3)
# ---------------------------------------------------------------------------


def _call_sites_for(func, target_name: str) -> list[ast.Call]:
    """Every `ast.Call` node inside `func`'s body whose callee attribute
    name is `target_name` -- e.g. every `self._drive_region_compare(...)`
    call inside `verify_eprom`."""
    import inspect
    import textwrap

    source = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(source)
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee = node.func
            name = (
                callee.attr
                if isinstance(callee, ast.Attribute)
                else callee.id
                if isinstance(callee, ast.Name)
                else None
            )
            if name == target_name:
                calls.append(node)
    return calls


def test_verify_and_blank_never_pass_on_result() -> None:
    """`verify_eprom` and `check_eprom_blank` must take the default
    `on_result=None` path -- neither call site passes the `on_result`
    keyword to `_drive_region_compare`. An AST walk proves this at the
    source level rather than relying on behaviour alone."""
    for func in (EpromOperator.verify_eprom, EpromOperator.check_eprom_blank):
        calls = _call_sites_for(func, "_drive_region_compare")
        assert calls, f"{func.__name__} no longer calls _drive_region_compare"
        for call in calls:
            keyword_names = {kw.arg for kw in call.keywords}
            assert "on_result" not in keyword_names, (
                f"{func.__name__} passes on_result to _drive_region_compare"
            )


def test_blank_run_on_non_blank_chip_still_emits_mismatch_line(
    make_comm, fake_serial, caplog
) -> None:
    """Pairs with the structural AST test above: the default `on_result=None`
    path is not just structurally present but behaviourally exercised --
    `check_eprom_blank` on a non-blank fake still renders its `Mismatch`
    range line through `render_compare_lines`, exactly as before this
    plan's `on_result` parameter existed."""
    payload = bytearray(b"\xff" * 8)
    payload[3] = 0x00
    payload = bytes(payload)

    def _fake_find_and_connect(command_dict, config, **kwargs):
        return make_comm()

    fake_serial.feed(build_frame(MSG_INIT_DONE, b""))
    fake_serial.feed(build_frame(MSG_DATA_CHUNK, payload))
    fake_serial.feed(build_frame(MSG_MAIN_DONE, b""))
    fake_serial.feed(build_frame(MSG_END_DONE, b""))

    operator = EpromOperator(ConfigManager())
    with (
        caplog.at_level("INFO", logger="EpromOperator"),
        patch(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            side_effect=_fake_find_and_connect,
        ),
    ):
        verdict = operator.check_eprom_blank(
            "W27C512", {"memory-size": 8, "flags": 0, "cmd": 1}
        )

    assert verdict == 1
    messages = [rec.message for rec in caplog.records]
    assert any("Mismatch 0x000003-0x000003 (1 bytes)" in m for m in messages)


def test_predicate_module_reads_only_algorithm_and_flags_keys_ast() -> None:
    """The stronger, structural form of the earlier behavioural test above:
    parse `write_blank_guard.py`'s own source and collect every string
    constant used as a `dict.get(...)` first argument or as a subscript
    slice inside its function bodies. The collected set must be a subset of
    `{"algorithm", "flags"}` -- an AST walk ignores comments, so the module
    is free to NAME the keys it deliberately does not read (which is the
    whole point of the comment at the bottom of the module) without that
    prose tripping this test. This is the test that would have caught
    `check_eprom_blank`'s inert `electrical-type`/`protocol-id` SRAM
    short-circuit had it been a new predicate instead of pre-existing code.
    """
    import inspect

    import firestarter.write_blank_guard as wbg

    source = inspect.getsource(wbg)
    tree = ast.parse(source)
    read_keys: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "get"
                    and inner.args
                    and isinstance(inner.args[0], ast.Constant)
                    and isinstance(inner.args[0].value, str)
                ):
                    read_keys.add(inner.args[0].value)
                if isinstance(inner, ast.Subscript):
                    slice_node = inner.slice
                    if isinstance(slice_node, ast.Constant) and isinstance(
                        slice_node.value, str
                    ):
                        read_keys.add(slice_node.value)

    assert read_keys, "AST walk found no dict-key reads -- test authoring error"
    assert read_keys <= {"algorithm", "flags"}, read_keys


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
