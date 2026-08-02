"""Deliberately-violating fixture for tools/check_no_exists_proxy.py (D-09's
recurrence lint against the module-level absence-proxy idiom, Phase 123
Plan 09).

This file must never be imported -- it exists only as AST-scan input for the
paired pytest (`tests/test_check_no_exists_proxy.py`). It lives in the
ruff-excluded `tests/fixtures/` directory and must be unreachable from
`tools/check_no_exists_proxy.py`'s default target list (a literal, top-level
`tests/*.py` enumeration that never reaches into `tests/fixtures/`) -- the
same discipline every other `tests/fixtures/planted_*` file in this project
observes relative to its own checker's default scan targets.

Plants THREE things:

  1. `SIMPLE_ABSENCE_PROXY` -- the simple module-level absence-proxy shape
     (`not <path>.exists()`), the exact idiom A-7 found in five of the seven
     modules Phase 123 Plan 08 rekeyed.
  2. `COMPOUND_ABSENCE_PROXY` -- the compound shape, a `not` over a boolean
     combination of two `.exists()` calls -- the exact shape
     `tests/test_dispatch_mirror.py` used before its own rekey.
  3. `legitimate_in_function_check` -- a path-existence check used for
     ordinary control flow INSIDE a function body. This must NOT be
     flagged; its presence here is what proves the lint discriminates by
     scope (module-level vs. function-body) rather than rejecting every
     mention of `.exists()` in the file.

Never imports anything from the `firestarter` package -- it is scannable
standalone, exactly like the real modules this lint scans.
"""

from pathlib import Path

_SOME_NONEXISTENT_PATH = Path("/nonexistent/some_firmware_file")
_OTHER_NONEXISTENT_PATH = Path("/nonexistent/other_firmware_file")

# PLANTED VIOLATION (simple shape): a module-level absence proxy.
SIMPLE_ABSENCE_PROXY = not _SOME_NONEXISTENT_PATH.exists()

# PLANTED VIOLATION (compound shape): a module-level absence proxy over a
# boolean combination of two `.exists()` calls -- mirrors the exact shape
# test_dispatch_mirror.py used before its Phase 123 Plan 08 rekey (it ANDed
# the existence of two firmware-repo paths together before negating).
COMPOUND_ABSENCE_PROXY = not (
    _SOME_NONEXISTENT_PATH.exists() and _OTHER_NONEXISTENT_PATH.exists()
)


def legitimate_in_function_check(path: Path) -> bool:
    """A path-existence check used for ordinary control flow INSIDE a
    function body -- must NOT be reported by the lint. Proves the lint
    discriminates by scope (module-level assignment vs. function-body
    control flow), not by mere presence of the substring "exists"."""
    if not path.exists():
        return False
    return True
