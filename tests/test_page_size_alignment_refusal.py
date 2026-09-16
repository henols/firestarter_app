"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 195 Plan 01 -- host-side, pre-connect refusal for a protocol 0x05 write
whose start address or payload length is not a whole multiple of the chip's
page size (D-01, D-08). Extended in Phase 195 Plan 03 (D-01, D-12) with the
guard's full property matrix -- every rejected class, a pass-through leg at
every page size the shipped database actually carries, both fail-closed
legs, the two no-op legs, the WRITE-02 empty edge, and the `dev test` region
shape -- so "not a blanket refusal" is a measurement rather than a claim.

Nine properties, matching page_size_gate.py's require_page_alignment contract:

  1. Refusal: EpromOperator.write_eprom raises PageAlignmentError, naming the
     chip, BEFORE `_operation_context` is ever entered -- no serial port is
     opened. Covers an unaligned start with an aligned length, an aligned
     start with a partial length, and both together.
  2. Pass-through: the same call against a page-exact write reaches
     `_operation_context` -- proving the guard is not a blanket refusal --
     at every page size the shipped database carries for this protocol
     (64, 128, 256, 512), with the parts chosen by reading the database.
  3. Fail-closed: an unparseable address string and a payload path that does
     not exist both raise PageAlignmentError rather than silently proceeding.
  4. Empty edge (WRITE-02): a zero-byte payload is never refused -- it is a
     whole multiple of every page size and drives no page cycle -- and the
     leg asserts the operation context was entered, not merely that no
     exception was raised, so the guard cannot pass by being skipped.
  5. No-op: a write against another algorithm, and a non-write operation
     against a protocol 0x05 chip, both pass without raising and without
     the guard touching the filesystem -- proven with a payload path that
     does not exist, so the early returns are shown to run before the size
     probe.
  6. `dev test` shape (D-12): a 256-byte payload at address 0 passes for a
     page size of 128 and is refused for a page size of 512, the measured
     form of "25 of 27 protocol 0x05 parts are unaffected, 2 are refused."
  7. Rendering: PageAlignmentError surfaces through map_typed_errors as a
     Click exception carrying the guard's own text verbatim, with no generic
     "Programmer error:" prefix in front of it -- both directly and through a
     real CLI invocation.
"""

from unittest.mock import Mock, patch

import click
import pytest
from click.testing import CliRunner

from firestarter.cli_handlers import cli, map_typed_errors
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import PageAlignmentError
from firestarter.flash4_erase_gate import FLASH4_PROTOCOL_ID
from firestarter.page_size_gate import require_page_alignment

from .conftest import make_app_context


def _make_operator() -> EpromOperator:
    return EpromOperator(ConfigManager())


def _w29c020_data() -> dict:
    """A real, resolved wire dict for W29C020 -- protocol 0x05, page size 128."""
    db = EpromDatabase(skip_local_override=True)
    return dict(db.convert_to_programmer(db.get_eprom("W29C020")))


def _other_algorithm_data() -> dict:
    """A real, resolved wire dict for a non-flash4 part, so the no-op legs
    exercise a genuine other-algorithm chip rather than a fabricated one."""
    db = EpromDatabase(skip_local_override=True)
    return dict(db.convert_to_programmer(db.get_eprom("W27C512")))


def _flash4_rows_by_page_size() -> dict[int, tuple[str, dict]]:
    """One (chip_name, wire_dict) pair per distinct page size the shipped
    database carries for protocol 0x05, picked by reading the database
    rather than by hard-coded part names -- so a database regeneration that
    moves which part carries which page size cannot silently invalidate the
    pass-through matrix below."""
    db = EpromDatabase(skip_local_override=True)
    picked: dict[int, tuple[str, dict]] = {}
    for full in db.get_eproms():
        if full.get("protocol-id") != FLASH4_PROTOCOL_ID:
            continue
        page_size = full.get("page_size")
        if not page_size or page_size in picked:
            continue
        wire = db.convert_to_programmer(full)
        picked[page_size] = (full["name"], wire)
    return picked


def test_write_eprom_unaligned_start_refuses_before_operation_context(tmp_path):
    eprom_data = _w29c020_data()
    assert eprom_data.get("page-size") == 128

    payload = tmp_path / "probe64.bin"
    payload.write_bytes(b"\x55" * 64)

    operator = _make_operator()
    with patch.object(EpromOperator, "_operation_context") as ctx_mock:
        with pytest.raises(PageAlignmentError) as exc_info:
            operator.write_eprom(
                "W29C020", eprom_data, str(payload), address_str="0x40"
            )
        ctx_mock.assert_not_called()

    assert "W29C020" in str(exc_info.value)


def test_write_eprom_unaligned_start_with_aligned_length_refuses_before_operation_context(
    tmp_path,
):
    """Start 64 is not a multiple of the 128 page size; length 128 is."""
    eprom_data = _w29c020_data()
    assert eprom_data.get("page-size") == 128

    payload = tmp_path / "aligned_length.bin"
    payload.write_bytes(b"\x55" * 128)

    operator = _make_operator()
    with patch.object(EpromOperator, "_operation_context") as ctx_mock:
        with pytest.raises(PageAlignmentError) as exc_info:
            operator.write_eprom(
                "W29C020", eprom_data, str(payload), address_str="0x40"
            )
        ctx_mock.assert_not_called()

    assert "W29C020" in str(exc_info.value)


def test_write_eprom_aligned_start_with_partial_length_refuses_before_operation_context(
    tmp_path,
):
    """Start 0 is a multiple of every page size; length 64 is not a
    multiple of the 128 page size."""
    eprom_data = _w29c020_data()
    assert eprom_data.get("page-size") == 128

    payload = tmp_path / "partial_length.bin"
    payload.write_bytes(b"\x55" * 64)

    operator = _make_operator()
    with patch.object(EpromOperator, "_operation_context") as ctx_mock:
        with pytest.raises(PageAlignmentError) as exc_info:
            operator.write_eprom("W29C020", eprom_data, str(payload))
        ctx_mock.assert_not_called()

    assert "W29C020" in str(exc_info.value)


def test_write_eprom_unaligned_start_and_partial_length_refuses_before_operation_context(
    tmp_path,
):
    """Both start 64 and length 64 fail the 128-page-size multiple test."""
    eprom_data = _w29c020_data()
    assert eprom_data.get("page-size") == 128

    payload = tmp_path / "both_unaligned.bin"
    payload.write_bytes(b"\x55" * 64)

    operator = _make_operator()
    with patch.object(EpromOperator, "_operation_context") as ctx_mock:
        with pytest.raises(PageAlignmentError) as exc_info:
            operator.write_eprom(
                "W29C020", eprom_data, str(payload), address_str="0x40"
            )
        ctx_mock.assert_not_called()

    assert "W29C020" in str(exc_info.value)


def test_write_eprom_unparseable_address_refuses_before_operation_context(tmp_path):
    eprom_data = _w29c020_data()
    payload = tmp_path / "aligned.bin"
    payload.write_bytes(b"\x55" * 128)

    operator = _make_operator()
    with patch.object(EpromOperator, "_operation_context") as ctx_mock:
        with pytest.raises(PageAlignmentError) as exc_info:
            operator.write_eprom(
                "W29C020", eprom_data, str(payload), address_str="not-an-address"
            )
        ctx_mock.assert_not_called()

    assert "W29C020" in str(exc_info.value)


def test_write_eprom_missing_payload_file_refuses_before_operation_context(tmp_path):
    eprom_data = _w29c020_data()
    missing_payload = tmp_path / "does_not_exist.bin"

    operator = _make_operator()
    with patch.object(EpromOperator, "_operation_context") as ctx_mock:
        with pytest.raises(PageAlignmentError) as exc_info:
            operator.write_eprom("W29C020", eprom_data, str(missing_payload))
        ctx_mock.assert_not_called()

    assert "W29C020" in str(exc_info.value)


def test_write_eprom_page_exact_write_reaches_operation_context(tmp_path):
    eprom_data = _w29c020_data()
    assert eprom_data.get("page-size") == 128

    payload = tmp_path / "aligned.bin"
    payload.write_bytes(b"\x55" * 128)

    operator = _make_operator()
    with patch.object(EpromOperator, "_operation_context") as ctx_mock:
        ctx_mock.return_value.__enter__ = Mock(return_value=(None, 0, "write"))
        ctx_mock.return_value.__exit__ = Mock(return_value=False)
        result = operator.write_eprom("W29C020", eprom_data, str(payload))

    ctx_mock.assert_called_once()
    assert result is False


@pytest.mark.parametrize("page_size", sorted(_flash4_rows_by_page_size()))
@pytest.mark.parametrize("page_count", [1, 2])
@pytest.mark.parametrize("address_at_one_page", [False, True])
def test_write_eprom_page_exact_write_reaches_operation_context_at_every_shipped_page_size(
    tmp_path, page_size, page_count, address_at_one_page
):
    """D-01: the alignment guard is not a blanket refusal at any page size
    the shipped database actually carries for this protocol -- 64, 128, 256
    and 512 -- with the part for each page size chosen by reading the
    database rather than by a hard-coded name."""
    rows = _flash4_rows_by_page_size()
    chip_name, eprom_data = rows[page_size]
    assert eprom_data.get("page-size") == page_size

    payload = tmp_path / "matrix.bin"
    payload.write_bytes(b"\x55" * (page_size * page_count))
    address_str = str(page_size) if address_at_one_page else None

    operator = _make_operator()
    with patch.object(EpromOperator, "_operation_context") as ctx_mock:
        ctx_mock.return_value.__enter__ = Mock(return_value=(None, 0, "write"))
        ctx_mock.return_value.__exit__ = Mock(return_value=False)
        result = operator.write_eprom(
            chip_name, eprom_data, str(payload), address_str=address_str
        )

    ctx_mock.assert_called_once()
    assert result is False


def test_write_eprom_zero_byte_payload_reaches_operation_context(tmp_path):
    """WRITE-02 empty edge: a zero-length write is a whole multiple of
    every page size and drives no page cycle, so it must never be refused.
    Asserted by the context having been ENTERED, not merely by the absence
    of an exception -- so this leg cannot pass by the guard being skipped."""
    eprom_data = _w29c020_data()
    payload = tmp_path / "empty.bin"
    payload.write_bytes(b"")

    operator = _make_operator()
    with patch.object(EpromOperator, "_operation_context") as ctx_mock:
        ctx_mock.return_value.__enter__ = Mock(return_value=(None, 0, "write"))
        ctx_mock.return_value.__exit__ = Mock(return_value=False)
        operator.write_eprom("W29C020", eprom_data, str(payload))

    ctx_mock.assert_called_once()


def test_require_page_alignment_another_algorithm_never_touches_filesystem():
    """No-op leg: another algorithm's write is never probed, proven with a
    payload path that does not exist -- if the guard touched the
    filesystem, this would raise PageAlignmentError instead of returning."""
    eprom_data = _other_algorithm_data()
    assert eprom_data["algorithm"] != FLASH4_PROTOCOL_ID
    require_page_alignment(
        "W27C512", eprom_data, "write", None, "/nonexistent/should-not-be-read.bin"
    )


def test_require_page_alignment_non_write_operation_never_touches_filesystem():
    """No-op leg: a non-write operation against a protocol 0x05 chip is
    never probed either, proven the same way."""
    eprom_data = _w29c020_data()
    require_page_alignment(
        "W29C020", eprom_data, "read", None, "/nonexistent/should-not-be-read.bin"
    )
    require_page_alignment(
        "W29C020", eprom_data, "verify", None, "/nonexistent/should-not-be-read.bin"
    )


def test_dev_test_default_region_passes_at_page_128_and_refuses_at_page_512(tmp_path):
    """D-12, measured rather than written down: dev test's 256-byte write
    region at address 0 is a whole number of pages for a 128-byte page size
    and a refused partial page for a 512-byte page size."""
    rows = _flash4_rows_by_page_size()

    page128_name, page128_data = rows[128]
    payload_128 = tmp_path / "devtest_region_p128.bin"
    payload_128.write_bytes(b"\x00" * 256)
    require_page_alignment(page128_name, page128_data, "write", None, str(payload_128))

    page512_name, page512_data = rows[512]
    payload_512 = tmp_path / "devtest_region_p512.bin"
    payload_512.write_bytes(b"\x00" * 256)
    with pytest.raises(PageAlignmentError):
        require_page_alignment(
            page512_name, page512_data, "write", None, str(payload_512)
        )


def test_page_alignment_error_renders_verbatim_with_no_generic_prefix():
    message = (
        "W29C020: write refused -- page size is 128 bytes, start address is "
        "0x40, and payload length is 64 bytes."
    )

    @map_typed_errors
    def _raises() -> None:
        raise PageAlignmentError(message)

    with pytest.raises(click.ClickException) as exc_info:
        _raises()

    assert exc_info.value.message == message
    assert "Programmer error" not in exc_info.value.message


def test_page_alignment_error_renders_through_cli_write_command(tmp_path):
    message = (
        "W29C020: write refused -- page size is 128 bytes, start address is "
        "0x40, and payload length is 64 bytes."
    )
    payload = tmp_path / "probe64.bin"
    payload.write_bytes(b"\x55" * 64)
    operator = Mock(spec=EpromOperator)
    operator.write_eprom.side_effect = PageAlignmentError(message)
    app = make_app_context(eprom_operator=operator)

    runner = CliRunner()
    result = runner.invoke(
        cli, ["write", "W29C020", str(payload), "-a", "0x40"], obj=app
    )

    assert result.exit_code != 0
    assert message in result.output
    assert "Programmer error" not in result.output
