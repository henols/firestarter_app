"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Guard tests for `tools/audit_coverage_matrix.py`'s default `--output` /
`--ledger` path resolution.

`_REPO_ROOT` is derived from three `dirname()` hops off the tool's own
`__file__`. In this project's own devcontainer that lands on the meta repo
one level above `firestarter_app`, which is intentional. In any other
checkout of this package the same arithmetic lands somewhere the tool has no
business writing to.

Every test below drives the REAL tool as a subprocess against a disposable
COPY of it, planted under a scratch tree that mirrors the real
`firestarter_app/tools/` layout one level below the scratch root. Copying
the file (rather than monkeypatching `__file__` or `_REPO_ROOT`) is what
lets `__file__`-derived path arithmetic run for real, unmodified, inside a
scratch root the test fully controls. `FIRESTARTER_DB_FILE` is pointed at
this repo's real `chip_database.json` for every invocation: without it, the
copied tool would fail on a missing database before the path guard is ever
reached, and every assertion below would pass for the wrong reason.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_APP_ROOT = Path(__file__).resolve().parent.parent
_REAL_TOOL = _APP_ROOT / "tools" / "audit_coverage_matrix.py"
_REAL_DB = _APP_ROOT / "firestarter" / "data" / "chip_database.json"
_SUBPROCESS_TIMEOUT = 300


def _make_scratch_repo(tmp_path: Path, *, with_planning_dir: bool) -> Path:
    scratch_root = tmp_path / "scratch-repo"
    tools_dir = scratch_root / "firestarter_app" / "tools"
    tools_dir.mkdir(parents=True)
    shutil.copyfile(_REAL_TOOL, tools_dir / "audit_coverage_matrix.py")
    if with_planning_dir:
        (scratch_root / ".planning").mkdir()
    return scratch_root


def _run_tool(
    scratch_root: Path, extra_args: list[str]
) -> subprocess.CompletedProcess[str]:
    app_root = scratch_root / "firestarter_app"
    tool_path = app_root / "tools" / "audit_coverage_matrix.py"
    env = dict(os.environ)
    env["FIRESTARTER_DB_FILE"] = str(_REAL_DB)
    return subprocess.run(
        [sys.executable, str(tool_path), *extra_args],
        cwd=str(app_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
        check=False,
    )


def _files_under(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize(
    "extra_args",
    [[], ["--all-algorithms"], ["--check"]],
    ids=["plain", "all-algorithms", "check"],
)
def test_guard_fails_closed_when_no_planning_dir(
    tmp_path: Path, extra_args: list[str]
) -> None:
    scratch_root = _make_scratch_repo(tmp_path, with_planning_dir=False)

    result = _run_tool(scratch_root, extra_args)

    assert result.returncode == 2, (
        f"expected exit 2, got {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "--output" in result.stderr, result.stderr
    assert "--ledger" in result.stderr, result.stderr
    assert not (scratch_root / ".planning").exists()
    assert _files_under(scratch_root) == [
        os.path.join("firestarter_app", "tools", "audit_coverage_matrix.py")
    ]


@pytest.mark.parametrize(
    "only_flag", ["--output", "--ledger"], ids=["only-output", "only-ledger"]
)
def test_guard_fails_closed_when_only_one_path_is_explicit(
    tmp_path: Path, only_flag: str
) -> None:
    scratch_root = _make_scratch_repo(tmp_path, with_planning_dir=False)
    explicit_path = tmp_path / "explicit-target"

    result = _run_tool(scratch_root, [only_flag, str(explicit_path)])

    assert result.returncode == 2, (
        f"expected exit 2, got {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert not explicit_path.exists()
    assert not (scratch_root / ".planning").exists()


def test_defaults_resolve_when_planning_dir_exists(tmp_path: Path) -> None:
    scratch_root = _make_scratch_repo(tmp_path, with_planning_dir=True)

    result = _run_tool(scratch_root, [])

    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    matrix_path = scratch_root / ".planning" / "v1.3-COVERAGE-MATRIX.md"
    ledger_path = scratch_root / ".planning" / "v1.3-defect-coverage-ids.json"
    assert matrix_path.exists()
    assert ledger_path.exists()
    assert "## §3: Full Enumeration" in matrix_path.read_text(encoding="utf-8")


def test_explicit_paths_bypass_the_guard(tmp_path: Path) -> None:
    scratch_root = _make_scratch_repo(tmp_path, with_planning_dir=False)
    out = tmp_path / "explicit-output.md"
    ledger = tmp_path / "explicit-ledger.json"

    result = _run_tool(scratch_root, ["--output", str(out), "--ledger", str(ledger)])

    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert out.exists()
    assert ledger.exists()
    assert not (scratch_root / ".planning").exists()
