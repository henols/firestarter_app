"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

The single cross-repo firmware-presence probe (BASE-02, D-09, D-11, D-12;
Phase 123 Plan 07).

A-7 measured the current defect: seven host test modules each derive
"firmware absent" from an independent `not <some file>.exists()` proxy keyed
on a *specific scan target* (a header, a doc, a .cpp). A firmware **rename**
of any one of those targets flips that module's gate legs PASS -> SKIP at
exit 0, with a false "firmware absent" reason -- and moving firmware files
around is this milestone's entire premise. Left alone, that means the very
act of doing this milestone's work silently disarms its own regression
gates.

This module splits the proxy in two:

  1. **Repo presence** is decided ONCE, here, keyed on `../firestarter/.git`
     -- a marker no in-repo rename can move, because it lives one level
     above every file the seven modules scan.
  2. **A missing scan target under a present repo** is no longer a skip. It
     is a hard failure (`MissingScanTargetError`) naming the resolved path,
     because a present-but-mismatched path means the scan target itself
     needs updating (or the D-11 scan-path inventory does) -- not that the
     firmware checkout is missing.

Only the seven modules' constants and markers are meant to be replaced by
this module (that mechanical rekey is Phase 123 Plan 08's job, not this
plan's); this module is the thing the rekey substitutes in, built and
proven standalone first.

**Import-time binding -- read this before writing a test against this
module.** `FW_ROOT`, `FW_REPO_MARKER`, `FW_REPO_PRESENT`, `FW_ABSENT_REASON`
and `requires_fw` are all evaluated once, at import (the `FIRESTARTER_FW_ROOT`
env seam below is read at module scope, and `pytest.mark.skipif`
binds at collection). `monkeypatch.setenv` runs *after* import and
collection have already happened, so it has **no effect** on any of these
names. A test that needs a different `FW_ROOT` must invoke pytest (or a
checker script) in a **subprocess**, with `FIRESTARTER_FW_ROOT` set in the
child process's environment -- never an in-process monkeypatch, never a
direct import of this module under a patched environment (RESEARCH
Correction C-15).

**The `firestarter` name-collision trap.** `<app repo>/firestarter/` is the
Python *package* (this project's own source tree); `<app repo>/../firestarter/`
is the *sibling firmware repo*. Both spellings appear within a few lines of
each other in `tests/test_sdp_bus_config_drift.py`. `FW_ROOT` in this module
is always the **sibling repo** -- resolved by walking up from this file's
location, past the app repo root, then down into a sibling `firestarter`
directory. Any future path added to this module must be verified to resolve
*outside* the app repo (i.e. its resolved string must never contain
`firestarter_app`).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# The one seam: only the ROOT path is overridable, never the marker name.
# ---------------------------------------------------------------------------
#
# Layout: <app repo>/tests/fw_presence.py -> two parents up is the app repo
# root; its sibling `firestarter` directory is the firmware repo root. The
# read below is the ONLY environment lookup in this module -- the marker
# name stays hardcoded as `.git` on purpose. Making the marker name
# overridable too would be one more knob that can be set wrong in a real
# run; RESEARCH's D-12 "Mechanism 1" (committed tree + runtime-materialised
# marker) exists precisely so that indirection is never needed in
# production.
_APP_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_FW_ROOT = _APP_REPO_ROOT.parent / "firestarter"

FW_ROOT: Path = Path(os.environ.get("FIRESTARTER_FW_ROOT", str(_DEFAULT_FW_ROOT)))

# The repo-presence marker. A worktree or submodule checkout stores `.git`
# as a FILE, not a directory (this project's own `../firestarter` checkout
# is currently a directory, but both forms are reachable in this project's
# history) -- so this probes existence, never `.is_dir()`.
FW_REPO_MARKER: Path = FW_ROOT / ".git"

FW_REPO_PRESENT: bool = FW_REPO_MARKER.exists()

# ONE canonical reason string, shared by every caller. Specific enough to be
# unambiguous in a `pytest -rs` report (names the exact marker path probed);
# stable enough to be a Phase 123-09 allow-list key (does not embed a
# per-module scan target, so all seven modules collapse to one entry
# instead of five near-duplicate strings).
FW_ABSENT_REASON: str = (
    f"firestarter firmware checkout absent (no {FW_REPO_MARKER} marker)"
)

# The ONLY skip marker any cross-repo test may use after Phase 123-08's
# rekey. A module must never author its own `pytest.mark.skipif` keyed on a
# scan-target proxy again.
requires_fw = pytest.mark.skipif(not FW_REPO_PRESENT, reason=FW_ABSENT_REASON)


class MissingScanTargetError(Exception):
    """Raised when the firmware repo IS present but a named path under it
    is not.

    This is the hard-failure half of the BASE-02 split: under a present
    repo, a missing scan target means the scan target (or the D-11 scan-path
    inventory) needs to be updated to match a real rename -- it must never
    be silently downgraded to a skip, because that is exactly the fail-open
    behaviour A-7 measured and this milestone exists to remove.
    """


def fw_path(*parts: str) -> Path:
    """Join `parts` onto `FW_ROOT` and return the resolved path.

    - If the firmware repo is present (`FW_REPO_PRESENT`) and the resulting
      path does not exist, raises `MissingScanTargetError` naming the
      resolved absolute path, the repo marker that proved the repo present,
      and the instruction to update the path (or the scan-path inventory)
      rather than deleting the gate -- the same "name the fix" shape used
      by `tools/check_no_log_in_sdp_window.py`'s brace-resolution errors.
    - If the firmware repo is absent, returns the path WITHOUT raising: the
      caller is expected to be behind `requires_fw` (or an equivalent
      absence check) already, and raising here too would turn an honest
      skip into a collection-time error.
    """
    resolved = FW_ROOT.joinpath(*parts)
    if FW_REPO_PRESENT and not resolved.exists():
        raise MissingScanTargetError(
            f"{resolved} does not exist, but the firmware repo IS present "
            f"(marker found at {FW_REPO_MARKER}). This scan target was "
            "renamed or moved -- update this path (or the cross-repo "
            "scan-path inventory) rather than deleting or skipping this "
            "gate."
        )
    return resolved
