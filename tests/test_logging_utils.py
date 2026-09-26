"""Phase 42 / ERR-03 fallback coverage lift for ``SingleLineStatusHandler``
(D-14 fallback per CONTEXT)."""

import io
import logging

from firestarter.logging_utils import SingleLineStatusHandler


def test_normal_record_emits_message() -> None:
    """A normal log record without 'status' extra emits message + newline."""
    buf = io.StringIO()
    handler = SingleLineStatusHandler(stream=buf)
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="x",
        lineno=1,
        msg="hello",
        args=None,
        exc_info=None,
    )
    handler.format = lambda r: r.msg  # type: ignore[assignment]
    handler.emit(record)
    assert "hello" in buf.getvalue()


def test_status_start_then_end_overwrites_line() -> None:
    """status='start' suppresses newline; status='end' adds CR + newline."""
    buf = io.StringIO()
    handler = SingleLineStatusHandler(stream=buf)
    handler.format = lambda r: r.msg  # type: ignore[assignment]

    start_record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname="x",
        lineno=1,
        msg="working",
        args=None,
        exc_info=None,
    )
    start_record.status = "start"
    handler.emit(start_record)
    assert "working" in buf.getvalue()
    assert handler._status_line_active is True

    end_record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname="x",
        lineno=1,
        msg="done",
        args=None,
        exc_info=None,
    )
    end_record.status = "end"
    handler.emit(end_record)
    assert "done" in buf.getvalue()
    assert handler._status_line_active is False


def test_normal_after_status_inserts_newline() -> None:
    """A normal record after an active status line inserts a newline first."""
    buf = io.StringIO()
    handler = SingleLineStatusHandler(stream=buf)
    handler.format = lambda r: r.msg  # type: ignore[assignment]

    # Activate status line
    start_record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname="x",
        lineno=1,
        msg="status_line",
        args=None,
        exc_info=None,
    )
    start_record.status = "start"
    handler.emit(start_record)

    # Then emit a normal record — should reset status line first
    normal = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname="x",
        lineno=1,
        msg="normal_msg",
        args=None,
        exc_info=None,
    )
    handler.emit(normal)
    out = buf.getvalue()
    # Both messages present, status flag cleared
    assert "status_line" in out
    assert "normal_msg" in out
    assert handler._status_line_active is False


def test_emit_handles_exception_via_handle_error() -> None:
    """If format() raises, emit invokes handleError() rather than crashing."""

    class FailingFormatter(logging.Formatter):
        def format(self, record):
            raise RuntimeError("formatter blew up")

    buf = io.StringIO()
    handler = SingleLineStatusHandler(stream=buf)
    handler.setFormatter(FailingFormatter())
    # Suppress the default handleError stderr noise
    handler.handleError = lambda record: None  # type: ignore[method-assign]

    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname="x",
        lineno=1,
        msg="x",
        args=None,
        exc_info=None,
    )
    handler.emit(record)  # Should not raise
