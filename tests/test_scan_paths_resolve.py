"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

The single resolving test for D-11's cross-repo scan-path inventory
(BASE-02; Phase 123 Plan 08).

Four tests, deliberately small, iterating the union in `tests/scan_paths.py`
rather than re-deriving anything:

  1. Every path in `ALL_CROSS_REPO_PATHS` resolves to an existing file when
     the sibling firmware repo is present -- guarded by the shared
     `requires_fw` marker. On failure, names EVERY missing path in one
     assertion message with its resolving module(s), not just the first --
     that message is the artifact Phase 124's MERGE-07 consumes.
  2. The inventory is non-vacuous: the union's length is at least a
     hardcoded floor equal to what actually ships, so an emptied or
     mis-globbed inventory fails rather than passing silently.
  3. No entry is a same-repo look-alike: every resolved path lies outside
     this app repo, and `SAME_REPO_LOOKALIKES` is non-empty with none of its
     entries appearing in the union.
  4. Population B is covered: all 11 tool files named in
     `CROSS_REPO_TOOL_RESOLVERS` exist in `tools/`, so a renamed or deleted
     tool is caught rather than silently dropping its paths from the
     inventory.
"""

from __future__ import annotations

from pathlib import Path

from tests.fw_presence import requires_fw
from tests.scan_paths import (
    ALL_CROSS_REPO_PATHS,
    CROSS_REPO_TEST_PATHS,
    CROSS_REPO_TOOL_RESOLVERS,
    SAME_REPO_LOOKALIKES,
    resolve_scan_path,
)

# Floor equal to what actually ships (measured at plan time): 6 population-A
# test paths, the deduplicated union of population A + the genuinely
# cross-repo subset of population B. An emptied or mis-globbed inventory
# must fail this, not pass silently.
_FLOOR = 6

_APP_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOLS_DIR = _APP_REPO_ROOT / "tools"


def _resolvers_for(fw_relative_path: str) -> tuple[str, ...]:
    """Every test module and/or tool file that resolves `fw_relative_path`,
    for a failure message that names the fix, not just the symptom."""
    resolvers: list[str] = []
    for entry in CROSS_REPO_TEST_PATHS:
        if entry.fw_relative_path == fw_relative_path:
            resolvers.extend(entry.resolved_by)
    for tool_entry in CROSS_REPO_TOOL_RESOLVERS:
        if fw_relative_path in tool_entry.cross_repo_paths:
            resolvers.append(tool_entry.tool)
    return tuple(resolvers)


@requires_fw
def test_all_cross_repo_paths_resolve() -> None:
    """Every entry in ALL_CROSS_REPO_PATHS must resolve to an existing file
    when the sibling firmware repo is present.

    Collects EVERY missing path before asserting once, naming each missing
    path with its resolving module(s) -- a rename anywhere in the firmware
    repo becomes one named failure here, never an anonymous skip
    elsewhere (A-7's measured defect).
    """
    missing: list[str] = []
    for fw_relative_path in ALL_CROSS_REPO_PATHS:
        resolved = resolve_scan_path(fw_relative_path)
        if not resolved.exists():
            resolvers = _resolvers_for(fw_relative_path)
            missing.append(
                f"{fw_relative_path} (resolved: {resolved}) -- resolved by: "
                f"{', '.join(resolvers) or 'unknown'}"
            )

    assert not missing, (
        "The following cross-repo scan path(s) do not resolve -- the "
        "firmware repo IS present, so this is a rename or move, not an "
        "absence. Update the path in tests/scan_paths.py (or the resolving "
        "module/tool) to match:\n" + "\n".join(f"  - {m}" for m in missing)
    )


def test_inventory_is_non_vacuous() -> None:
    """The union must be at least `_FLOOR` entries long -- an emptied or
    mis-globbed inventory fails here rather than passing silently, since
    test 1 above would otherwise vacuously pass over zero paths."""
    assert len(ALL_CROSS_REPO_PATHS) >= _FLOOR, (
        f"ALL_CROSS_REPO_PATHS has only {len(ALL_CROSS_REPO_PATHS)} entries, "
        f"expected at least {_FLOOR} -- the inventory may have been emptied "
        "or mis-derived."
    )
    assert len(CROSS_REPO_TEST_PATHS) >= _FLOOR, (
        f"CROSS_REPO_TEST_PATHS has only {len(CROSS_REPO_TEST_PATHS)} "
        f"entries, expected at least {_FLOOR}."
    )


def test_no_entry_is_a_same_repo_lookalike() -> None:
    """No resolved cross-repo path may lie inside this app repo, and
    SAME_REPO_LOOKALIKES must be non-empty with none of its own entries
    appearing in the union -- the mechanical version of Pitfall 7."""
    assert SAME_REPO_LOOKALIKES, "SAME_REPO_LOOKALIKES must never be emptied"

    for fw_relative_path in ALL_CROSS_REPO_PATHS:
        resolved = resolve_scan_path(fw_relative_path).resolve()
        # Containment, not a substring match. This previously asserted
        # `"firestarter_app" not in str(resolved)`, which conflates "is
        # inside the app repo" with "the app repo's NAME appears anywhere
        # in the path". Those differ the moment an ancestor directory is
        # also called `firestarter_app` -- exactly GitHub Actions' default
        # `work/<repo>/<repo>` layout, where the sibling firmware checkout
        # at `/home/runner/work/firestarter_app/firestarter` is NOT in the
        # app repo yet contains its name. That false positive failed the
        # beta-release build in Phase 130; locally it never fired, because
        # the devcontainer's `/workspaces/firestarter` has no such ancestor.
        assert not resolved.is_relative_to(_APP_REPO_ROOT), (
            f"{fw_relative_path} resolves to {resolved}, which lies "
            f"INSIDE the app repo ({_APP_REPO_ROOT}) -- this is a "
            "same-repo look-alike (package vs. sibling repo name "
            "collision) and must be moved to SAME_REPO_LOOKALIKES, not "
            "ALL_CROSS_REPO_PATHS."
        )

    lookalike_paths = {entry.app_relative_path for entry in SAME_REPO_LOOKALIKES}
    overlap = lookalike_paths & set(ALL_CROSS_REPO_PATHS)
    assert not overlap, (
        f"SAME_REPO_LOOKALIKES entries also appear in ALL_CROSS_REPO_PATHS: "
        f"{sorted(overlap)}"
    )


def test_all_eleven_tool_resolvers_exist() -> None:
    """Every tool file named in CROSS_REPO_TOOL_RESOLVERS must exist in
    tools/, so a renamed or deleted tool is caught rather than silently
    dropping its paths from the inventory -- population B coverage."""
    assert len(CROSS_REPO_TOOL_RESOLVERS) == 11, (
        f"expected exactly 11 tool-resolver entries, found "
        f"{len(CROSS_REPO_TOOL_RESOLVERS)}"
    )
    missing_tools = [
        tool_entry.tool
        for tool_entry in CROSS_REPO_TOOL_RESOLVERS
        if not (_TOOLS_DIR / tool_entry.tool).is_file()
    ]
    assert not missing_tools, (
        f"the following tools named in CROSS_REPO_TOOL_RESOLVERS no longer "
        f"exist in {_TOOLS_DIR}: {missing_tools}"
    )
