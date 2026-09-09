"""HYG-02, asserted by test rather than by a sentence: the runtime
`dependencies` list in `pyproject.toml` is pinned to exactly six shipped
distributions, by exact set equality.

This module uses stdlib `tomllib` (available on Python 3.11+, matching the
app CI floor) deliberately: reaching for `toml` or `tomli` to parse
`pyproject.toml` would add a dependency in the very act of asserting that
no dependency was added, which is HYG-02's own claim.

`181-PATTERNS.md` (Phase 181's pattern map) found no precedent in this tree
for a test that parses `pyproject.toml` -- the shape here is an original
decision, not a copied one.
"""

from pathlib import Path

import pytest
import tomllib

_EXPECTED_RUNTIME_DISTRIBUTIONS = frozenset(
    {"click", "packaging", "pyserial", "requests", "rich", "tqdm"}
)
"""The six distributions HYG-02 names verbatim as what `pip install
firestarter` puts on a user's machine. The pin below is EQUALITY, never a
subset, so both a seventh distribution appearing and one of these six
disappearing must redden it."""


def _pyproject_path() -> Path:
    """Resolve `pyproject.toml` from this test file's own parents, never a
    directory-relative path. This project has a recorded checker
    (`check_permitted_claims.py`) whose directory-relative `_HERE` resolved
    to the wrong directory, scanned nothing, and exited 0 -- the same
    failure mode a fragile relative resolution here would invite."""
    return Path(__file__).resolve().parent.parent / "pyproject.toml"


def _distribution_name(requirement: str) -> str:
    """Reduce a requirement string to its bare distribution name by
    splitting on the first of `>`, `<`, `=`, `!`, `~`, `[` and `;`, then
    stripping whitespace -- so `pyserial>=3.5` and a future
    `pyserial>=3.5 ; python_version<"3.12"` both reduce to `pyserial`.
    Deliberately a name reducer, not a specifier parser: HYG-02 pins WHICH
    distributions ship, not which versions."""
    name = requirement
    for separator in (">", "<", "=", "!", "~", "[", ";"):
        name = name.split(separator, 1)[0]
    return name.strip()


def _parsed_runtime_dependencies() -> list[str]:
    """Parse `pyproject.toml` with stdlib `tomllib` and return the raw
    `project.dependencies` list, guarded against a mis-resolved path: the
    file must exist and the list must be non-empty before anything else is
    asserted about its contents."""
    path = _pyproject_path()
    assert path.is_file(), path
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    dependencies = data["project"]["dependencies"]
    assert dependencies, "project.dependencies parsed empty"
    return dependencies


def test_runtime_dependency_set_is_exactly_the_six_shipped_distributions():
    """The positive pin. Extras are deliberately NOT pinned: `dev`, `py32`
    and `test` may grow freely, because HYG-02 is about what
    `pip install firestarter` puts on a user's machine, not about the
    project's own development or optional tooling."""
    dependencies = _parsed_runtime_dependencies()
    names = [_distribution_name(dep) for dep in dependencies]
    assert len(names) == 6, (
        "a duplicate entry reducing to an existing name would hide behind "
        "a set comparison alone -- the parsed list length is an "
        "independent, second check"
    )
    assert set(names) == _EXPECTED_RUNTIME_DISTRIBUTIONS


def test_a_planted_seventh_dependency_reddens_the_pin():
    """A seventh distribution appearing in the parsed list must redden the
    equality pin. The anchor is asserted absent from the real list before
    mutating, and only an in-memory copy is mutated -- `pyproject.toml` is
    never touched."""
    dependencies = _parsed_runtime_dependencies()
    planted = "a-planted-seventh-distribution>=1.0"
    assert planted not in dependencies
    mutated = list(dependencies)
    mutated.append(planted)
    names = {_distribution_name(dep) for dep in mutated}
    with pytest.raises(AssertionError):
        assert names == _EXPECTED_RUNTIME_DISTRIBUTIONS


def test_a_planted_removal_reddens_the_pin():
    """Removing one of the six from the parsed list in memory must also
    redden the equality pin -- a subset assertion would survive this
    direction, and an equality assertion is what this proves. The removed
    entry is asserted present in the real list before mutating."""
    dependencies = _parsed_runtime_dependencies()
    removed_entry = next(
        dep for dep in dependencies if _distribution_name(dep) == "pyserial"
    )
    assert removed_entry in dependencies
    mutated = list(dependencies)
    mutated.remove(removed_entry)
    names = {_distribution_name(dep) for dep in mutated}
    with pytest.raises(AssertionError):
        assert names == _EXPECTED_RUNTIME_DISTRIBUTIONS


def test_an_empty_expected_set_fails_rather_than_passing_vacuously():
    """The separate, explicitly-named vacuity leg (never folded into the
    RED-drift tests above): comparing the real parsed set against an empty
    expected set must fail, not pass by coincidence."""
    dependencies = _parsed_runtime_dependencies()
    names = {_distribution_name(dep) for dep in dependencies}
    with pytest.raises(AssertionError):
        assert names == set()
