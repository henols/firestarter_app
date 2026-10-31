"""Run the CLI in a fresh process that reports a given version.

`cli_handlers` fixes the `--board` choices, the py32-only options and the
`dev` command registry at import, from `firestarter.__version__`. A test of a
release channel therefore needs a process in which the version is set before
that import; patching the version in this process would leave them stale.

The child inherits no FIRESTARTER_* variable and gets its own empty config
directory, so a result depends only on the arguments: only an explicit `env`
can enable the dev tools, and a cached result is safe to share between tests.
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_APP_DIR = Path(__file__).parent.parent

_CHILD_PROGRAM = """
import json
import sys

import click
import firestarter

assert "firestarter.cli_handlers" not in sys.modules, (
    "firestarter.cli_handlers was imported before the version was set"
)
firestarter.__version__ = {version!r}

from click.testing import CliRunner
from firestarter import cli_handlers

argv = {argv!r}
result = CliRunner().invoke(cli_handlers.cli, argv)
group = cli_handlers.cli.commands.get(argv[0]) if argv else None
print(json.dumps({{
    "version": firestarter.__version__,
    "exit_code": result.exit_code,
    "output": result.output,
    "registered": sorted(group.commands) if isinstance(group, click.Group) else None,
}}))
"""


@dataclass(frozen=True)
class CliResult:
    exit_code: int
    output: str
    # The subcommands registered on the group named by argv[0], hidden or not.
    registered: frozenset[str] | None

    @property
    def board_choices(self) -> list[str]:
        """The `--board` choices that `fw --help` shows."""
        match = re.search(r"--board \[([^\]]+)\]", self.output)
        assert match, f"no --board choice list in:\n{self.output}"
        return match.group(1).split("|")

    @property
    def listed(self) -> set[str]:
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
    child_env = {
        k: v for k, v in os.environ.items() if not k.startswith("FIRESTARTER_")
    }
    with tempfile.TemporaryDirectory(prefix="fresh-cli-") as config_dir:
        child_env["FIRESTARTER_CONFIG_DIR"] = config_dir
        child_env.update(env)
        program = _CHILD_PROGRAM.format(version=version, argv=list(argv))
        completed = subprocess.run(
            [sys.executable, "-c", program],
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
    registered = report["registered"]
    return CliResult(
        exit_code=report["exit_code"],
        output=report["output"],
        registered=None if registered is None else frozenset(registered),
    )
