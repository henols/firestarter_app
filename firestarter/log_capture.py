"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Process-lifetime log-capture sink -- a `dev test` run's own diagnostic
evidence, buffered so it survives past the terminal it was printed to.

Per-instance state cannot serve this purpose: `EpromOperator.comm` is torn
down after every operator call (see `transport_counters.py`'s own docstring
on the same point), so a buffer living on a `SerialCommunicator` is destroyed
before `DiagnosticReport` is ever built. The sink here instead lives for the
whole host process, reset explicitly by the caller that owns a measurement
window (`cli_handlers.dev_test`), never read across process restarts.

The sink attaches to the ROOT logger, not to `serial_comm.rurp_logger` or any
one host module logger: root is the one place both the firmware feedback
logger and every host module logger converge, so one handler collects both
sides of D-01's `source` split with no per-logger wiring anywhere else.

The measurement window is opened and closed by the caller (`install()` /
`snapshot()` / `uninstall()`), never by this module at import time: opening
at import would attach a handler for the whole process lifetime, including
every unrelated command, and would make `install()`'s own idempotence
unobservable.

Import purity: this module imports nothing from `firestarter` -- that is
what keeps the import graph acyclic, the same property `transport_counters`
states about itself.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

MAX_ENTRIES = 20
MAX_LINE_CHARS = 160
FIRMWARE_LOGGER_NAME = "RURP"

_entries: "deque[dict[str, Any]]" = deque(maxlen=MAX_ENTRIES)
_captured = 0
_dropped = 0
_truncated = 0
_current_step: str | None = None
_handler: "_CaptureHandler | None" = None


class _CaptureHandler(logging.Handler):
    """The sink's only handler class -- `install()`/`uninstall()` identify
    their own attachments on the root logger by `isinstance` against this
    class, never by tracking a single module-level reference alone, so a
    leaked prior instance is always found and removed."""

    def emit(self, record: logging.LogRecord) -> None:
        global _captured, _dropped, _truncated
        try:
            message = record.getMessage()
            was_truncated = False
            if len(message) > MAX_LINE_CHARS:
                message = message[: MAX_LINE_CHARS - 3] + "..."
                was_truncated = True
            source = "firmware" if record.name == FIRMWARE_LOGGER_NAME else "host"
            entry: dict[str, Any] = {
                "step": _current_step,
                "source": source,
                "level": record.levelname,
                "message": message,
                "repeat": 1,
            }
            _captured += 1
            if was_truncated:
                _truncated += 1
            if len(_entries) == _entries.maxlen:
                _dropped += _entries[0]["repeat"]
            _entries.append(entry)
        except Exception:
            pass


def install() -> None:
    """Attach a fresh sink to the root logger, self-healing by construction.

    Removes any previously attached `_CaptureHandler` instance FIRST, then
    clears the buffer and every counter, then attaches a fresh handler at
    `logging.WARNING`. A run that raised on the way out and skipped its own
    `uninstall()` call therefore cannot leave a second handler behind: the
    next `install()` finds and removes it before attaching its own.
    """
    global _handler, _captured, _dropped, _truncated, _current_step
    uninstall()
    _entries.clear()
    _captured = 0
    _dropped = 0
    _truncated = 0
    _current_step = None
    handler = _CaptureHandler(level=logging.WARNING)
    logging.getLogger().addHandler(handler)
    _handler = handler


def uninstall() -> None:
    """Remove every `_CaptureHandler` instance from the root logger.

    A no-op when none is attached. Removing every instance by class, not
    just the one this module currently tracks, is what makes `install()`
    calling this first actually idempotent.
    """
    global _handler
    root = logging.getLogger()
    for existing in list(root.handlers):
        if isinstance(existing, _CaptureHandler):
            root.removeHandler(existing)
    _handler = None


@contextmanager
def step_scope(op: str) -> Iterator[None]:
    """Mark the sink as "currently running step `op`" for the duration of
    the `with` block, restoring the PREVIOUS value on exit -- including when
    the body raises -- rather than a hard `None`.

    Restoring the previous value, exactly as `transport_counters.probe_scope`
    does, is what keeps a nested scope or one that exits via an exception
    from leaving the sink attributing every later line to a step that has
    already finished.
    """
    global _current_step
    previous = _current_step
    _current_step = op
    try:
        yield
    finally:
        _current_step = previous


def snapshot() -> dict[str, Any]:
    """Return a fresh mapping of the sink's current state.

    A new dict (and a new list of entry dicts) is built on every call, so a
    caller mutating the returned object can never reach back into the sink.
    Every key is present unconditionally, whatever the sink's state.
    """
    return {
        "entries": [dict(entry) for entry in _entries],
        "captured": _captured,
        "dropped": _dropped,
        "truncated": _truncated,
        "max_entries": MAX_ENTRIES,
        "max_line_chars": MAX_LINE_CHARS,
    }
