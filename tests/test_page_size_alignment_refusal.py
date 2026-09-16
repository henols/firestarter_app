"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 195 Plan 01 -- host-side, pre-connect refusal for a protocol 0x05 write
whose start address or payload length is not a whole multiple of the chip's
page size (D-01, D-08).

Three properties, matching page_size_gate.py's require_page_alignment contract:

  1. Refusal: EpromOperator.write_eprom raises PageAlignmentError, naming the
     chip, BEFORE `_operation_context` is ever entered -- no serial port is
     opened.
  2. Pass-through: the same call against a page-exact write reaches
     `_operation_context` -- proving the guard is not a blanket refusal.
  3. Rendering: PageAlignmentError surfaces through map_typed_errors as a
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

from .conftest import make_app_context


def _make_operator() -> EpromOperator:
    return EpromOperator(ConfigManager())


def _w29c020_data() -> dict:
    """A real, resolved wire dict for W29C020 -- protocol 0x05, page size 128."""
    db = EpromDatabase(skip_local_override=True)
    return dict(db.convert_to_programmer(db.get_eprom("W29C020")))


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
