"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 194 Plan 05 -- host-side, pre-connect refusal for a protocol 0x05 write
against a chip with no recorded page size (D-05/D-07).

Four properties, matching page_size_gate.py's contract:

  1. Refusal: EpromOperator.write_eprom raises PageSizeUnavailableError,
     naming the chip, BEFORE `_operation_context` is ever entered -- no
     serial port is opened.
  2. Pass-through: the same call against a chip whose wire dict carries a
     non-zero page size reaches `_operation_context` -- proving the guard
     is not a blanket refusal.
  3. No-op: a write against another algorithm, and a non-write operation
     against a protocol 0x05 chip with no page size, both pass without
     raising.
  4. Rendering: PageSizeUnavailableError surfaces through map_typed_errors
     as a Click exception carrying the guard's own text verbatim, with no
     generic "Programmer error:" prefix in front of it.
"""

from unittest.mock import Mock, patch

import click
import pytest
from click.testing import CliRunner

from firestarter.cli_handlers import cli, map_typed_errors
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_operations import EpromOperator
from firestarter.exceptions import EpromOperationError, PageSizeUnavailableError
from firestarter.flash4_erase_gate import FLASH4_PROTOCOL_ID
from firestarter.page_size_gate import require_page_size, requires_page_size

from .conftest import make_app_context


def _make_operator() -> EpromOperator:
    return EpromOperator(ConfigManager())


def _protocol_0x05_data_missing_page_size() -> dict:
    """A real, resolved wire dict for a protocol 0x05 part, with `page-size`
    stripped -- reproducing the pre-Phase-194-01 absent-value shape on
    otherwise-live data, so `bus-config` stays realistic for `jp5_gate`."""
    db = EpromDatabase(skip_local_override=True)
    eprom_data = dict(db.convert_to_programmer(db.get_eprom("W29C020")))
    eprom_data.pop("page-size", None)
    return eprom_data


def _protocol_0x05_data_with_page_size() -> dict:
    db = EpromDatabase(skip_local_override=True)
    return db.convert_to_programmer(db.get_eprom("W29C020"))


def _protocol_other_algorithm_data() -> dict:
    db = EpromDatabase(skip_local_override=True)
    return db.convert_to_programmer(db.get_eprom("W27C512"))


def test_write_eprom_no_page_size_refuses_before_operation_context():
    eprom_data = _protocol_0x05_data_missing_page_size()
    assert eprom_data["algorithm"] == FLASH4_PROTOCOL_ID
    assert not eprom_data.get("page-size")

    operator = _make_operator()
    with patch.object(EpromOperator, "_operation_context") as ctx_mock:
        with pytest.raises(PageSizeUnavailableError) as exc_info:
            operator.write_eprom("W29C020", eprom_data, "in.bin")
        ctx_mock.assert_not_called()

    assert "W29C020" in str(exc_info.value)


def test_write_eprom_no_page_size_refuses_with_no_programmer_available():
    """The refusal fires regardless of hardware presence -- it is a pure
    pre-connect predicate, not a consequence of a failed connect attempt."""
    eprom_data = _protocol_0x05_data_missing_page_size()
    operator = _make_operator()
    with patch.object(EpromOperator, "_operation_context") as ctx_mock:
        with pytest.raises(PageSizeUnavailableError):
            operator.write_eprom("W29C020", eprom_data, "in.bin")
        ctx_mock.assert_not_called()


def test_write_eprom_with_page_size_reaches_operation_context():
    eprom_data = _protocol_0x05_data_with_page_size()
    assert eprom_data.get("page-size")

    operator = _make_operator()
    with patch.object(EpromOperator, "_operation_context") as ctx_mock:
        ctx_mock.return_value.__enter__ = Mock(return_value=(None, 0, "write"))
        ctx_mock.return_value.__exit__ = Mock(return_value=False)
        result = operator.write_eprom("W29C020", eprom_data, "in.bin")

    ctx_mock.assert_called_once()
    assert result is False


def test_require_page_size_another_algorithm_never_raises():
    eprom_data = _protocol_other_algorithm_data()
    assert eprom_data["algorithm"] != FLASH4_PROTOCOL_ID
    assert not requires_page_size(eprom_data)
    require_page_size("W27C512", eprom_data, "write")


def test_require_page_size_non_write_operation_never_raises_even_with_no_page_size():
    eprom_data = _protocol_0x05_data_missing_page_size()
    require_page_size("W29C020", eprom_data, "read")
    require_page_size("W29C020", eprom_data, "verify")


def test_write_eprom_another_algorithm_reaches_operation_context_regardless_of_page_size():
    eprom_data = _protocol_other_algorithm_data()
    operator = _make_operator()
    with patch.object(EpromOperator, "_operation_context") as ctx_mock:
        ctx_mock.return_value.__enter__ = Mock(return_value=(None, 0, "write"))
        ctx_mock.return_value.__exit__ = Mock(return_value=False)
        operator.write_eprom("W27C512", eprom_data, "in.bin")

    ctx_mock.assert_called_once()


def test_page_size_unavailable_renders_verbatim_with_no_generic_prefix():
    message = (
        "W29C512: no page size is recorded for this chip, and a protocol "
        "0x05 write is refused."
    )

    @map_typed_errors
    def _raises() -> None:
        raise PageSizeUnavailableError(message)

    with pytest.raises(click.ClickException) as exc_info:
        _raises()

    assert exc_info.value.message == message
    assert "Programmer error" not in exc_info.value.message


def test_generic_eprom_operation_error_still_maps_to_programmer_error():
    """Negative control: the pre-existing generic arm stays reachable and
    keeps its prefix -- proving the new arm's placement above it did not
    swallow the base-class case."""

    @map_typed_errors
    def _raises() -> None:
        raise EpromOperationError("some programmer failure")

    with pytest.raises(click.ClickException) as exc_info:
        _raises()

    assert exc_info.value.message == "Programmer error: some programmer failure"


def test_page_size_unavailable_renders_through_cli_write_command():
    message = (
        "W29C512: no page size is recorded for this chip, and a protocol "
        "0x05 write is refused."
    )
    operator = Mock(spec=EpromOperator)
    operator.write_eprom.side_effect = PageSizeUnavailableError(message)
    app = make_app_context(eprom_operator=operator)

    runner = CliRunner()
    result = runner.invoke(cli, ["write", "W29C512", "in.bin"], obj=app)

    assert result.exit_code == 1
    assert message in result.output
    assert "Programmer error" not in result.output
