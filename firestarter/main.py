#!/usr/bin/env python
"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Entry-point stub for the `firestarter` console script (Phase 41 / D-08, D-16).
Re-exports Click's ``cli`` as ``main`` so the ``firestarter.main:main`` entry
point declared in pyproject.toml keeps resolving without churn after the
argparse -> Click migration.
"""

import signal
import sys

from firestarter.cli_handlers import cli

# D-08: preserve `firestarter.main:main` entry-point references via re-export.
main = cli


def exit_gracefully(signum, frame):
    sys.exit(1)


if __name__ == "__main__":
    if sys.version_info < (3, 9):  # noqa: UP036
        sys.exit(
            "Error: Firestarter requires Python 3.9 or higher. "
            "Please update your Python version."
        )

    signal.signal(signal.SIGINT, exit_gracefully)
    cli()
