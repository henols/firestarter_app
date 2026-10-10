"""Phase 42 / ERR-03 fallback coverage lift for ``SingleLineStatusHandler``
(D-14 fallback per CONTEXT)."""

import io
import logging
import os

import pytest

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


def test_closed_pipe_stops_quietly_with_exit_1(capsys) -> None:
    """A reader that closes the pipe (`firestarter list | head`) ends the run.

    Before, logging's handleError printed a "--- Logging error ---" traceback
    for every remaining line of the table.
    """
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    stream = os.fdopen(write_fd, "w")
    handler = SingleLineStatusHandler(stream)
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "row", None, None)
    try:
        with pytest.raises(SystemExit) as exc:
            handler.emit(record)
        assert exc.value.code == 1
        assert "Logging error" not in capsys.readouterr().err
        # The stream now goes to /dev/null, so a later flush cannot fail.
        stream.write("more")
        stream.flush()
    finally:
        stream.close()


def test_other_write_errors_still_go_to_handle_error(monkeypatch) -> None:
    """Only a closed pipe is silenced. Other write errors still reach handleError."""

    class _Failing(io.StringIO):
        def write(self, s):
            raise OSError("disk full")

    handler = SingleLineStatusHandler(_Failing())
    seen = []
    monkeypatch.setattr(handler, "handleError", lambda record: seen.append(record))
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "row", None, None)
    handler.emit(record)
    assert seen == [record]
