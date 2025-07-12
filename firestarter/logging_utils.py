"""
Project Name: Firestarter
Copyright (c) 2025 Henrik Olsson

Permission is hereby granted under MIT license.

Custom Logging Utilities
"""

import logging
import sys


class SingleLineStatusHandler(logging.StreamHandler):
    """
    A logging handler that can overwrite a single line in the console.
    It looks for a 'status' attribute in the log record's 'extra' dict.

    - status='start': prints the message without a newline.
    - status='end': prints the message on the same line (using \\r) and adds a newline.

    Normal log records will clear any active status line before being printed.
    """

    def __init__(self, stream=None):
        # Use stdout by default as it's for interactive console output
        super().__init__(stream or sys.stdout)
        self._status_line_active = False

    def emit(self, record):
        # If a status line is active and this new record is a normal one,
        # we must first add a newline to not overwrite the status line.
        if self._status_line_active and not hasattr(record, "status"):
            self.stream.write(self.terminator)
            self._status_line_active = False

        try:
            msg = self.format(record)
            status = getattr(record, "status", None)

            if status == "start":
                self.stream.write(msg)
                self._status_line_active = True
            elif status == "end":
                self.stream.write("\r" + msg + self.terminator)
                self._status_line_active = False
            else:
                self.stream.write(msg + self.terminator)
                self._status_line_active = False  # Ensure it's reset

            self.flush()
        except Exception:
            self.handleError(record)