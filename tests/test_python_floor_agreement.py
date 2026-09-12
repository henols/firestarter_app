"""FLOOR-01 and FLOOR-02, asserted by test rather than by a sentence: the
four statements of this project's Python floor -- `project.requires-python`,
`[tool.ruff] target-version`, `[tool.mypy] python_version` in
`pyproject.toml`, and every `python-version:` pin in `.github/workflows/*.yml`
-- all name the same version, after normalisation.

This module uses stdlib `tomllib` deliberately: reaching for a third-party
TOML-parsing package to read `pyproject.toml`, or a third-party
YAML-parsing package to read the workflow files, would add a dependency in
the very act of asserting that four configuration values agree, which is
the same defect this milestone removes elsewhere. After this phase's own
floor raise, `tomllib` is stdlib at the project's own declared minimum, so
the choice needs no further justification than that.

This tree already has a precedent for exactly this shape:
`tests/test_runtime_dependencies.py` parses `pyproject.toml` with `tomllib`
and pins a value from it by exact equality, and `186-PATTERNS.md` names it
as this file's primary analog. No existing test in this tree compares a
value between `pyproject.toml` and a GitHub workflow file, nor TOML against
YAML -- the structure below is copied from that precedent, but the pairing
it asserts is new.
"""

import re
import sys
import tomllib
from pathlib import Path

import pytest

_APP_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = _APP_ROOT / "pyproject.toml"
_WORKFLOWS_DIR = _APP_ROOT / ".github" / "workflows"

_WORKFLOW_PIN_FLOOR = 3
"""A floor, not a target: three `python-version:` pins exist today
(`ci.yml` x2, `beta-release.yml` x1). Raised when a fourth workflow gains a
pin, never lowered -- lowering it is how a count-floor stops detecting an
emptied inventory."""

_PYTHON_VERSION_RE = re.compile(
    r"^\s*python-version:\s*['\"]?([0-9]+\.[0-9]+)['\"]?\s*$", re.MULTILINE
)
_REQUIRES_PYTHON_RE = re.compile(r"^>=([0-9]+\.[0-9]+)$")


def _normalise_ruff_target(target: str) -> str:
    """Map ruff's `--target-version` token to a dotted version string.

    The token's minor component is variable width -- `py39` is 3.9 and
    `py311` is 3.11 -- so this splits after the single-digit major and
    treats everything after it as the minor, rather than assuming a fixed
    two-character minor. A fixed-width parse would silently read `py311` as
    3.1 and let a real divergence compare equal."""
    match = re.fullmatch(r"py([0-9])([0-9]+)", target)
    assert match is not None, (
        f"ruff target-version {target!r} does not match the expected "
        "pyMAJOR MINOR token shape"
    )
    major, minor = match.groups()
    return f"{major}.{minor}"


def _normalise_requires_python(specifier: str) -> str:
    """Map a `requires-python` specifier to a dotted version string.

    `requires-python` is a specifier, not a version: this accepts only the
    bare-lower-bound shape `>=MAJOR.MINOR` and raises on anything else --
    a comma-joined range such as `>=3.11,<4`, a compatible-release operator
    such as `~=3.11`, or an unprefixed version such as `3.11` -- because a
    naive prefix strip would let any of those three pass silently."""
    match = _REQUIRES_PYTHON_RE.match(specifier)
    assert match is not None, (
        f"requires-python {specifier!r} is not a bare lower-bound "
        "specifier of the form '>=MAJOR.MINOR' -- a comma-joined range, a "
        "compatible-release operator, or an unprefixed version must not be "
        "silently accepted"
    )
    return match.group(1)


def _parsed_pyproject() -> dict[str, str]:
    """Read `_PYPROJECT` (a module global, monkeypatchable), parse it with
    stdlib `tomllib`, and return the three pyproject-side floor statements
    keyed by source name, each already normalised to a dotted version.

    Raises `AssertionError` if the file is absent or any of the three keys
    it needs is missing -- an absent file or a missing key must never
    silently produce a mapping some caller compares as equal to itself.
    The fail-closed planted-file leg below monkeypatches `_PYPROJECT`
    before calling this same helper, so the real leg and the fail-closed
    leg share one code path."""
    assert _PYPROJECT.is_file(), (
        f"pyproject.toml not found at {_PYPROJECT} -- every downstream "
        "floor-agreement assertion would be vacuously true against a file "
        "that was never read"
    )
    with _PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)

    try:
        requires_python = data["project"]["requires-python"]
    except KeyError as exc:
        raise AssertionError(
            f"project.requires-python missing from {_PYPROJECT}"
        ) from exc
    try:
        ruff_target = data["tool"]["ruff"]["target-version"]
    except KeyError as exc:
        raise AssertionError(
            f"tool.ruff.target-version missing from {_PYPROJECT}"
        ) from exc
    try:
        mypy_version = data["tool"]["mypy"]["python_version"]
    except KeyError as exc:
        raise AssertionError(
            f"tool.mypy.python_version missing from {_PYPROJECT}"
        ) from exc

    return {
        "project.requires-python (pyproject.toml)": _normalise_requires_python(
            requires_python
        ),
        "tool.ruff.target-version (pyproject.toml)": _normalise_ruff_target(
            ruff_target
        ),
        "tool.mypy.python_version (pyproject.toml)": mypy_version,
    }


def _workflow_python_versions() -> dict[str, str]:
    """Read `_WORKFLOWS_DIR` (a module global, monkeypatchable), glob every
    `*.yml` file through `sorted()` for a byte-stable report order, extract
    every `python-version:` pin with a line regex, and return them keyed by
    `"<filename>:<line-number>"`.

    Raises `AssertionError` if the directory is absent or the total pin
    count is below `_WORKFLOW_PIN_FLOOR` -- an absent or emptied directory
    is the `check_permitted_claims.py` failure mode exactly: a
    directory-relative scan that resolved to the wrong place, found
    nothing, and exited 0. Two workflow files legitimately carry no pin at
    all, so only the total is asserted, never a per-file requirement. The
    fail-closed planted-file leg below monkeypatches `_WORKFLOWS_DIR`
    before calling this same helper."""
    assert _WORKFLOWS_DIR.is_dir(), (
        f"workflows directory not found at {_WORKFLOWS_DIR} -- an absent "
        "directory must be a hard failure, never a silent pass over zero "
        "pins"
    )

    pins: dict[str, str] = {}
    for workflow_file in sorted(_WORKFLOWS_DIR.glob("*.yml")):
        text = workflow_file.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            match = _PYTHON_VERSION_RE.match(line)
            if match is not None:
                pins[f"{workflow_file.name}:{line_number}"] = match.group(1)

    assert len(pins) >= _WORKFLOW_PIN_FLOOR, (
        f"only {len(pins)} python-version pin(s) found under "
        f"{_WORKFLOWS_DIR}, expected at least {_WORKFLOW_PIN_FLOOR} -- the "
        "workflow inventory may have been emptied or mis-globbed"
    )
    return pins


def _floor_statements() -> dict[str, str]:
    """Return every floor statement this gate asserts, keyed by a
    human-readable source name, each value already normalised to a bare
    `MAJOR.MINOR` string. This is what makes a disagreement message
    diagnostic -- a reader sees every source and its value in one place."""
    statements = dict(_parsed_pyproject())
    for source, version in _workflow_python_versions().items():
        statements[f"python-version (.github/workflows/{source})"] = version
    return statements


def _assert_floor_statements_agree(statements: dict[str, str]) -> None:
    """Assert every value in `statements` is identical, with a message
    naming every source and its value so a reader sees which one drifted
    without opening anything. Called by both the real leg below and its
    planted-file counterpart, so the planted leg proves this exact
    assertion code fails closed rather than a parallel one written to
    fail."""
    values = set(statements.values())
    assert len(values) == 1, (
        "the project's floor statements disagree after normalisation:\n"
        + "\n".join(
            f"  - {source}: {version!r}" for source, version in statements.items()
        )
    )


def test_all_four_floor_statements_name_the_same_version() -> None:
    """`requires-python`, ruff's `target-version`, mypy's `python_version`,
    and every CI `python-version:` pin must all normalise to the same
    version. On disagreement the message names every source and its value,
    so a reader sees which one drifted without opening anything."""
    _assert_floor_statements_agree(_floor_statements())


def test_ruff_target_minor_is_not_fixed_width() -> None:
    """`_normalise_ruff_target` must split after the single-digit major,
    not at a fixed two-character minor -- `py39` is 3.9, `py310` is 3.10,
    and `py311` is 3.11. A fixed-width parse would read `py311` as 3.1 and
    let a real divergence compare equal."""
    assert _normalise_ruff_target("py39") == "3.9"
    assert _normalise_ruff_target("py310") == "3.10"
    assert _normalise_ruff_target("py311") == "3.11"


def test_requires_python_is_a_bare_lower_bound() -> None:
    """`_normalise_requires_python` must accept the bare-lower-bound shape
    and raise on a comma-joined range, a compatible-release operator, and
    an unprefixed version -- a naive prefix strip would pass all three."""
    assert _normalise_requires_python(">=3.11") == "3.11"
    with pytest.raises(AssertionError):
        _normalise_requires_python(">=3.11,<4")
    with pytest.raises(AssertionError):
        _normalise_requires_python("~=3.11")
    with pytest.raises(AssertionError):
        _normalise_requires_python("3.11")


def test_workflow_pin_inventory_is_non_vacuous() -> None:
    """The real workflow pin inventory must hold at least
    `_WORKFLOW_PIN_FLOOR` entries -- otherwise the four-way agreement
    assertion above would be comparing against too few sources to mean
    anything."""
    pins = _workflow_python_versions()
    assert len(pins) >= _WORKFLOW_PIN_FLOOR


def test_gate_fails_closed_on_a_planted_pyproject_with_no_floor_statements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed RED demonstration: point `_PYPROJECT` at a planted file
    with no `[project]` and no `[tool.mypy]` table, and assert the shared
    helper raises rather than returning an empty mapping that would compare
    equal to itself."""
    planted = tmp_path / "pyproject.toml"
    planted.write_text('[tool.ruff]\ntarget-version = "py311"\n', encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_PYPROJECT", planted)
    with pytest.raises(AssertionError):
        _parsed_pyproject()


def test_gate_fails_closed_on_an_empty_planted_workflows_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed RED demonstration: point `_WORKFLOWS_DIR` at an empty
    `tmp_path` and assert the shared helper raises rather than silently
    passing over zero pins. An absent or emptied directory is the
    `check_permitted_claims.py` failure mode exactly."""
    monkeypatch.setattr(sys.modules[__name__], "_WORKFLOWS_DIR", tmp_path)
    with pytest.raises(AssertionError):
        _workflow_python_versions()


def test_a_disagreeing_planted_pyproject_names_all_four_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A planted pyproject whose three values disagree with each other, and
    with the real (untouched) workflow pins, must raise an
    `AssertionError` whose text names every one of the four floor-statement
    KINDS -- `requires-python`, ruff's `target-version`, mypy's
    `python_version`, and at least one CI pin -- proving the report is
    diagnostic rather than a bare boolean. Only `_PYPROJECT` is
    monkeypatched, so the workflow half is read from the real tree via the
    same `_floor_statements()` the real leg above calls."""
    planted = tmp_path / "pyproject.toml"
    planted.write_text(
        "[project]\n"
        'requires-python = ">=3.11"\n'
        "[tool.ruff]\n"
        'target-version = "py310"\n'
        "[tool.mypy]\n"
        'python_version = "3.9"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "_PYPROJECT", planted)
    statements = _floor_statements()
    with pytest.raises(AssertionError) as excinfo:
        _assert_floor_statements_agree(statements)
    message = str(excinfo.value)
    for source in statements:
        assert source in message
