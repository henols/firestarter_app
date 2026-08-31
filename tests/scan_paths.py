"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

D-11's single committed cross-repo scan-path inventory (BASE-02; Phase 123
Plan 08).

A-7 measured the underlying defect this inventory exists to prevent from
recurring: seven host test modules each derived "firmware absent" from an
independent scan-target proxy, so a firmware **rename** silently flipped a
gate leg PASS -> SKIP at exit 0 (`tests/fw_presence.py`, Phase 123 Plan 07,
fixes that half). This module is the OTHER half: a single, explicit,
committed list of every path this repo resolves into the sibling
`firestarter` firmware repo -- so a rename anywhere in that repo becomes
ONE named failure (`tests/test_scan_paths_resolve.py`) instead of N
anonymous skips, and so Phase 124's "manifest paths resolve" artifact
already exists rather than needing to be rebuilt from scratch.

**Deliberately explicit, never derived.** No wildcard pattern matching and
no directory walk of any kind. Deriving this list mechanically (e.g. "any
path containing the string `firestarter`") would silently re-create the
exact name-collision trap this module documents below, and would
re-introduce the seven-way duplication D-11 removes one layer down. Every entry here was read out of the actual
source of the module or tool that resolves it, not copied from a plan or a
research note without verification.

**Two populations, and they are asymmetric in size.**
  - `CROSS_REPO_TEST_PATHS` -- 8 paths (originally 6, resolved from the 7
    proxy-carrying `tests/` modules rekeyed by this same plan (Task 1/2);
    Phase 147 added `src/firestarter.cpp` making 7, Phase 149 Plan 05 added
    `src/json_parser.c` making 8 -- both additions landed without this prose
    figure being updated in lockstep until now).
  - `CROSS_REPO_TOOL_RESOLVERS` -- all 11 `tools/*.py` files RESEARCH found
    via `grep -ln 'firestarter"' tools/*.py`. Population B is where a
    rename actually bites hardest in absolute file count, but **verifying
    each of the 11 by reading its source (not by copying RESEARCH's list
    blindly) turned up a second instance of the exact same name-collision
    trap `_REAL_PINOUTS` demonstrates in `test_sdp_bus_config_drift.py`**:
    7 of the 11 files construct their default path with a SINGLE `".."`
    from `tools/` (`os.path.join(_HERE, "..", "firestarter", ...)`), which
    resolves into `firestarter_app/firestarter/` -- this project's OWN
    Python PACKAGE, not the sibling repo. Only a path built with TWO `".."`
    segments from `tools/` (`_HERE, "..", "..", "firestarter", ...`, or the
    `Path`-based equivalent one level further up from the app repo root)
    reaches the sibling. The `grep -ln 'firestarter"'` command that found
    these 11 files cannot distinguish the two shapes -- it matches the
    literal string `firestarter"` in EITHER. Every `CROSS_REPO_TOOL_RESOLVERS`
    entry below records which shape its file actually uses, and only the
    genuinely cross-repo paths (4 of the 11 files; the true cross-repo paths
    all coincide with paths already listed in `CROSS_REPO_TEST_PATHS`) feed
    `ALL_CROSS_REPO_PATHS`. The other 7 are still LISTED here (so a renamed
    or deleted tool is still caught by test 4 below), but their
    `cross_repo_paths` tuple is empty and their `note` records the same-repo
    finding.

**The `firestarter` name-collision trap, generalised.** `<app repo>/firestarter/`
is the Python package (this project's own source tree, one level below
`tools/` or `tests/`); `<app repo>/../firestarter/` is the sibling firmware
repo (two levels below `tools/`, or one level below the app repo root). Any
path built from this file must be checked against that distinction before
being added to `ALL_CROSS_REPO_PATHS` -- `SAME_REPO_LOOKALIKES` below is
where a look-alike is recorded instead, with the reason.

**C-8 scope statement -- this inventory is a SUPERSET of MERGE-07, not the
same set.** `test_gen_validation_header.py` contributes paths to
`CROSS_REPO_TEST_PATHS` (`validation_matrix.h`) but is absent from v1.22's
eleven-row MERGE-07 gate table (RESEARCH Correction C-8). Phase 124 MUST NOT
treat this inventory and MERGE-07's nine gates as the same list when it
consumes this module -- this inventory covers every cross-repo scan path in
the app repo, MERGE-07 covers a named subset of firmware-integration gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tests.fw_presence import FW_ROOT

# ---------------------------------------------------------------------------
# Population A: paths resolved from tests/ (the 7 proxy-carrying modules
# rekeyed by Phase 123 Plan 08, Task 1/2).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScanPathEntry:
    """One cross-repo path, relative to the sibling firmware repo root, and
    the test module(s) that resolve it."""

    fw_relative_path: str
    resolved_by: tuple[str, ...]


CROSS_REPO_TEST_PATHS: tuple[ScanPathEntry, ...] = (
    ScanPathEntry(
        "include/firestarter.h",
        (
            "test_revision_constants_parity.py",
            "test_check_is_memory_cmd_no_ifdef.py",
        ),
    ),
    ScanPathEntry(
        "src/proms/eeprom_28c.cpp",
        (
            "test_check_no_log_in_sdp_window.py",
            "test_sdp_table_parity.py",
        ),
    ),
    ScanPathEntry(
        "test/native/avr/test_dispatch/test_configure_memory.cpp",
        ("tools/wiki/dispatch_mirror.py (meta repo; relocated by 168-10)",),
    ),
    ScanPathEntry(
        "test/native/avr/_shared/sdp_bus_config.h",
        ("test_sdp_bus_config_drift.py",),
    ),
    ScanPathEntry(
        "test/native/avr/_shared/validation_matrix.h",
        ("test_gen_validation_header.py",),
    ),
    ScanPathEntry(
        "src/firestarter.cpp",
        ("test_cap03_ack_layout_parity.py",),
    ),
    ScanPathEntry(
        "src/json_parser.c",
        ("test_json_key_parity.py",),
    ),
)

# ---------------------------------------------------------------------------
# Population B: the 11 tools/*.py files RESEARCH found via
# `grep -ln 'firestarter"' tools/*.py`. Verified individually below -- see
# the module docstring for why 7 of the 11 are same-repo look-alikes despite
# matching that grep.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolResolverEntry:
    """One `tools/*.py` file that RESEARCH's grep found constructing a path
    containing the literal string `firestarter"`.

    `cross_repo_paths` is the (possibly empty) tuple of firmware-repo-relative
    paths this tool GENUINELY resolves into the sibling repo -- verified by
    reading the file's `__file__`-relative path construction, never assumed
    from the grep hit alone. An empty tuple means this file's `firestarter`
    reference resolves into the app's OWN package (a same-repo look-alike,
    like `_REAL_PINOUTS`), not the sibling; `note` records which.
    """

    tool: str
    cross_repo_paths: tuple[str, ...]
    note: str


CROSS_REPO_TOOL_RESOLVERS: tuple[ToolResolverEntry, ...] = (
    ToolResolverEntry(
        "check_dispatch.py",
        (),
        "`_DATA_DIR = os.path.join(_HERE, '..', 'firestarter', 'data')` -- ONE "
        "'..' from tools/ resolves into firestarter_app/firestarter/data, this "
        "project's OWN package data dir, not the sibling repo.",
    ),
    ToolResolverEntry(
        "build_db.py",
        (),
        "Same `_DATA_DIR` shape as check_dispatch.py -- ONE '..' from tools/, "
        "same-repo package data dir.",
    ),
    ToolResolverEntry(
        "gen_validation_header.py",
        ("test/native/avr/_shared/validation_matrix.h",),
        "`_TARGET_DEFAULT = _TOOLS_DIR.parent.parent / 'firestarter' / ...` -- "
        "TWO parents from tools/ (past the app repo root) -- genuinely "
        "resolves into the sibling firmware repo. Same path already listed "
        "in CROSS_REPO_TEST_PATHS (test_gen_validation_header.py).",
    ),
    ToolResolverEntry(
        "check_no_log_in_sdp_window.py",
        ("src/proms/eeprom_28c.cpp",),
        "`os.path.join(_HERE, '..', '..', 'firestarter', 'src', 'proms', "
        "'eeprom_28c.cpp')` -- TWO '..' from tools/ -- genuinely cross-repo. "
        "Same path already listed in CROSS_REPO_TEST_PATHS.",
    ),
    ToolResolverEntry(
        "check_no_community_support_status_write.py",
        (),
        "`_DEFAULT_DISP01_REPORT = os.path.join(_HERE, '..', 'firestarter', "
        "'diagnostic_report.py')` -- ONE '..' from tools/, resolves into the "
        "app's own package (firestarter_app/firestarter/diagnostic_report.py, "
        "verified to exist). The module's separate `_assert_host_only` guard "
        "(using a `meta_root` two levels above tools/) is a NEGATIVE assertion "
        "that a scan target does NOT resolve into the sibling repo -- not a "
        "scan-path resolver itself.",
    ),
    ToolResolverEntry(
        "check_devtest_orchestrator.py",
        (),
        "Three defaults (`_DEFAULT_CHIP_TEST`, `_DEFAULT_DEVTEST_HANDLER`, "
        "`_DEFAULT_DEVTEST_SUBMIT`) each built with ONE '..' from tools/ -- "
        "all three resolve into the app's own package (chip_test.py, "
        "cli_handlers.py, submit.py -- all verified to exist there), not the "
        "sibling repo. Same `_assert_host_only` negative-assertion shape as "
        "check_no_community_support_status_write.py.",
    ),
    ToolResolverEntry(
        "check_is_memory_cmd_no_ifdef.py",
        ("include/firestarter.h",),
        "`os.path.join(_HERE, '..', '..', 'firestarter', 'include', "
        "'firestarter.h')` -- TWO '..' from tools/ -- genuinely cross-repo. "
        "Same path already listed in CROSS_REPO_TEST_PATHS.",
    ),
    ToolResolverEntry(
        "check_sdp_capability_invariants.py",
        (),
        "`_DEFAULT_SDP_CAPABILITY_SRC = os.path.join(_HERE, '..', "
        "'firestarter', 'sdp_capability.py')` -- ONE '..' from tools/, "
        "resolves into the app's own package (verified to exist there).",
    ),
    ToolResolverEntry(
        "diff_db.py",
        (),
        "Same `_DATA_DIR` shape as check_dispatch.py -- ONE '..' from tools/, "
        "same-repo package data dir.",
    ),
    ToolResolverEntry(
        "gen_sdp_bus_config.py",
        ("test/native/avr/_shared/sdp_bus_config.h",),
        "`_TARGET_DEFAULT = _APP_ROOT.parent / 'firestarter' / ...` (where "
        "`_APP_ROOT = _TOOLS_DIR.parent`) -- TWO parents from tools/ -- "
        "genuinely cross-repo; same path already listed in "
        "CROSS_REPO_TEST_PATHS. This file ALSO defines "
        "`_PINOUTS_DEFAULT = _APP_ROOT / 'firestarter' / 'data' / "
        "'pinouts.json'` -- ONE parent, the SAME same-repo look-alike shape "
        "as `_REAL_PINOUTS` in test_sdp_bus_config_drift.py, a second "
        "occurrence of that exact trap (see SAME_REPO_LOOKALIKES below).",
    ),
    ToolResolverEntry(
        "audit_coverage_matrix.py",
        (),
        "Same `_DATA_DIR` shape as check_dispatch.py -- ONE '..' from tools/, "
        "same-repo package data dir.",
    ),
)

assert len(CROSS_REPO_TOOL_RESOLVERS) == 11, (
    f"expected exactly 11 tool-resolver entries (RESEARCH's "
    f"`grep -ln 'firestarter\"' tools/*.py` count), found "
    f"{len(CROSS_REPO_TOOL_RESOLVERS)}"
)

# ---------------------------------------------------------------------------
# The deduplicated union, as firmware-repo-relative strings.
#
# Every genuinely cross-repo tool path above coincides with a path already
# listed in CROSS_REPO_TEST_PATHS (the tools generate or read the exact
# files the paired tests scan), so this union is the same 8 paths as
# population A -- a real, verified finding, not an oversight: population B's
# CONTRIBUTION is coverage of an eleven-file surface where a rename or
# deletion of the resolving TOOL itself is caught (test 4 below), not eight
# additional distinct paths.
# ---------------------------------------------------------------------------

ALL_CROSS_REPO_PATHS: tuple[str, ...] = tuple(
    sorted(
        {entry.fw_relative_path for entry in CROSS_REPO_TEST_PATHS}
        | {
            path
            for tool_entry in CROSS_REPO_TOOL_RESOLVERS
            for path in tool_entry.cross_repo_paths
        }
    )
)


# ---------------------------------------------------------------------------
# Same-repo look-alikes: explicit exclusion list, with reasons, so a future
# mechanical "any path containing firestarter" sweep has somewhere to be
# told no.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SameRepoLookalike:
    """A path that names or contains the string `firestarter` but resolves
    INSIDE this app repo, not the sibling firmware repo."""

    app_relative_path: str
    reason: str


SAME_REPO_LOOKALIKES: tuple[SameRepoLookalike, ...] = (
    SameRepoLookalike(
        "firestarter_app/firestarter/data/pinouts.json",
        "`_REAL_PINOUTS` in test_sdp_bus_config_drift.py -- "
        "`_APP_DIR / 'firestarter'` is the Python PACKAGE (this project's own "
        "source tree), NOT the sibling repo (`_FA_DIR.parent / 'firestarter'`, "
        "four lines away in the same file). Must never be resolved through "
        "`fw_path` and must never appear in ALL_CROSS_REPO_PATHS.",
    ),
    SameRepoLookalike(
        "firestarter_app/firestarter/data/pinouts.json (via gen_sdp_bus_config.py's _PINOUTS_DEFAULT)",
        "A second, independent occurrence of the exact same trap: "
        "`_PINOUTS_DEFAULT = _APP_ROOT / 'firestarter' / 'data' / "
        "'pinouts.json'` in tools/gen_sdp_bus_config.py -- ONE parent from "
        "tools/, same-repo package data dir, even though the SAME FILE's "
        "`_TARGET_DEFAULT` two lines above genuinely IS cross-repo.",
    ),
    SameRepoLookalike(
        "firestarter_app/firestarter/diagnostic_report.py",
        "check_no_community_support_status_write.py's `_DEFAULT_DISP01_REPORT` "
        "-- ONE '..' from tools/, same-repo package file.",
    ),
    SameRepoLookalike(
        "firestarter_app/firestarter/chip_test.py",
        "check_devtest_orchestrator.py's `_DEFAULT_CHIP_TEST` -- ONE '..' "
        "from tools/, same-repo package file.",
    ),
    SameRepoLookalike(
        "firestarter_app/firestarter/cli_handlers.py",
        "check_devtest_orchestrator.py's `_DEFAULT_DEVTEST_HANDLER` -- ONE "
        "'..' from tools/, same-repo package file.",
    ),
    SameRepoLookalike(
        "firestarter_app/firestarter/submit.py",
        "check_devtest_orchestrator.py's `_DEFAULT_DEVTEST_SUBMIT` -- ONE "
        "'..' from tools/, same-repo package file.",
    ),
    SameRepoLookalike(
        "firestarter_app/firestarter/sdp_capability.py",
        "check_sdp_capability_invariants.py's `_DEFAULT_SDP_CAPABILITY_SRC` "
        "-- ONE '..' from tools/, same-repo package file.",
    ),
    SameRepoLookalike(
        "firestarter_app/firestarter/data (chip_database.json, pinouts.json)",
        "check_dispatch.py / build_db.py / diff_db.py / "
        "audit_coverage_matrix.py's `_DATA_DIR` -- ONE '..' from tools/, "
        "same-repo package data dir, despite each file matching RESEARCH's "
        "`grep -ln 'firestarter\"' tools/*.py`.",
    ),
)

assert SAME_REPO_LOOKALIKES, "SAME_REPO_LOOKALIKES must never be emptied"


def resolve_scan_path(fw_relative_path: str) -> Path:
    """Join `fw_relative_path` onto the sibling firmware repo root.

    Thin wrapper kept local to this module (rather than importing `fw_path`
    for this specific purpose) so `tests/test_scan_paths_resolve.py` can
    resolve every inventory entry uniformly, including tool-resolver paths
    that duplicate a test-path entry.
    """
    return FW_ROOT / fw_relative_path
