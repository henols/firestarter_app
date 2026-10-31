"""Run the CLI in a fresh process that reports a given version.

`cli_handlers` fixes the `--board` choices, the py32-only options and the
`dev` command registry at import, from `firestarter.__version__`. A test of a
release channel therefore needs a process in which the version is set before
that import; patching the version in this process would leave them stale.

The child strips an inherited FIRESTARTER_DEV_TOOLS, so only an explicit
`env` can enable the dev tools.
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_APP_DIR = Path(__file__).parent.parent

_CHILD_PROGRAM = """
import json
import sys

import firestarter

assert "firestarter.cli_handlers" not in sys.modules, (
    "firestarter.cli_handlers was imported before the version was set"
)
firestarter.__version__ = {version!r}

from click.testing import CliRunner
from firestarter import cli_handlers

result = CliRunner().invoke(cli_handlers.cli, {argv!r})
print(json.dumps({{
    "version": firestarter.__version__,
    "exit_code": result.exit_code,
    "output": result.output,
}}))
"""


@dataclass(frozen=True)
class CliResult:
    exit_code: int
    output: str

    @property
    def board_choices(self) -> list[str]:
        """The `--board` choices that `fw --help` shows."""
        match = re.search(r"--board \[([^\]]+)\]", self.output)
        assert match, f"no --board choice list in:\n{self.output}"
        return match.group(1).split("|")

    @property
    def commands(self) -> set[str]:
        """The subcommand names in the `Commands:` section of a group's help."""
        section = self.output.split("Commands:\n", 1)[1]
        return {
            line.split()[0]
            for line in section.splitlines()
            if line.startswith("  ") and not line.startswith("   ")
        }


def run_cli_fresh(
    version: str, *argv: str, env: dict[str, str] | None = None
) -> CliResult:
    """Run `firestarter <argv>` in a new process that reports `version`."""
    return _run(version, argv, tuple(sorted((env or {}).items())))


@functools.cache
def _run(
    version: str, argv: tuple[str, ...], env: tuple[tuple[str, str], ...]
) -> CliResult:
    child_env = {k: v for k, v in os.environ.items() if k != "FIRESTARTER_DEV_TOOLS"}
    child_env.update(env)
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_PROGRAM.format(version=version, argv=list(argv))],
        cwd=str(_APP_DIR),
        capture_output=True,
        text=True,
        timeout=120,
        env=child_env,
    )
    assert completed.returncode == 0, (
        f"child for version={version!r} argv={argv!r} env={dict(env)!r} exited "
        f"{completed.returncode}:\n{completed.stderr}"
    )
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report["version"] == version
    return CliResult(exit_code=report["exit_code"], output=report["output"])
