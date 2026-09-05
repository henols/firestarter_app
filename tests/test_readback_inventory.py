"""
Machine census of the `dev test` engine's device-read call sites (PRUNE-04,
Phase 177, plan 177-03).

The requirement closes as *measured-empty within the engine*: `chip_test.py`
holds exactly two `operator.read_eprom(...)` call sites, and both are
name-and-excluded rather than converted -- the fingerprint read-back by D-1
(a verify returns a bool and one mismatch address, while
`classify_fingerprint` needs the whole mismatch distribution), and the SDP
leg because its `_read_region` read-back IS the verdict, never decoration.
`eprom_operations.write_cycle_eprom` is the one genuine read-whole-device-
to-compare-a-held-buffer site in the package -- it lives outside this
engine, in `EpromOperator` itself, calling `_run_state_machine` directly
rather than `operator.read_eprom`, so it never appears in this census; it
is named and excluded separately in
`.planning/phases/177-evidence-gated-read-back/177-READBACK-INVENTORY.md`.

This module parses with `ast`, never `grep`. A grep census fails open on a
rename or a reformatted call, and this project has a recorded checker whose
directory-relative path resolution scanned nothing and exited 0 -- the same
failure mode a naive text search invites here. The module path is resolved
from `firestarter.chip_test.__file__`, never from this test file's own
directory, and the parsed source is asserted non-empty so the census
cannot pass on a file it never actually read.

Two anti-vacuity legs prove the gate is a gate, not a claim: one plants a
third `operator.read_eprom(...)` call site into an in-memory copy of the
source and asserts the census reports three, and one asserts an emptied
expected enclosing-function allow-list fails rather than passing
vacuously. Both operate on strings only; neither writes a file.
"""

import ast
import inspect
import pathlib

import pytest

from firestarter import chip_test as ct
from firestarter.eprom_operations import EpromOperator

_TARGET_ATTR = "read_eprom"
_TARGET_OWNER = "operator"

_PLANTED_ANCHOR = (
    "            last_ok = operator.read_eprom(name, eprom_data, "
    "output_file=out_path)\n"
)


def _read_eprom_census(source: str) -> tuple[int, set[str]]:
    """Parse `source` and return `(call_site_count, enclosing_function_names)`
    for every `operator.read_eprom(...)` call.

    Each call site is attributed to its innermost enclosing `FunctionDef` by
    comparing line-number containment across every function in the tree and
    picking the one with the smallest span -- correct regardless of `ast.walk`
    traversal order, which does not guarantee an outer-before-inner visit.
    """
    tree = ast.parse(source)
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    sites = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == _TARGET_ATTR
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == _TARGET_OWNER
    ]
    enclosing: set[str] = set()
    for call in sites:
        containing = [
            fn
            for fn in functions
            if fn.lineno <= call.lineno <= (fn.end_lineno or fn.lineno)
        ]
        innermost = (
            min(containing, key=lambda fn: (fn.end_lineno or fn.lineno) - fn.lineno)
            if containing
            else None
        )
        enclosing.add(innermost.name if innermost is not None else "?")
    return len(sites), enclosing


def _assert_census(
    source: str, *, expected_count: int, expected_enclosing: set[str]
) -> None:
    count, enclosing = _read_eprom_census(source)
    assert count == expected_count
    assert enclosing == expected_enclosing


def _engine_source() -> str:
    source = pathlib.Path(ct.__file__).read_text(encoding="utf-8")
    assert len(source) > 1000
    return source


def test_engine_has_exactly_two_read_eprom_call_sites():
    _assert_census(
        _engine_source(),
        expected_count=2,
        expected_enclosing={"_dispatch_read", "_read_region"},
    )


def test_a_planted_third_call_site_reddens_the_census():
    source = _engine_source()
    assert source.count(_PLANTED_ANCHOR) == 1
    mutated = source.replace(_PLANTED_ANCHOR, _PLANTED_ANCHOR + _PLANTED_ANCHOR, 1)
    count, enclosing = _read_eprom_census(mutated)
    assert count == 3
    assert enclosing == {"_dispatch_read", "_read_region"}


def test_an_empty_enclosing_allow_list_fails_rather_than_passing_vacuously():
    with pytest.raises(AssertionError):
        _assert_census(_engine_source(), expected_count=2, expected_enclosing=set())


def test_verify_eprom_signature_names_the_replacement_primitive():
    params = list(inspect.signature(EpromOperator.verify_eprom).parameters)[1:]
    assert params == [
        "eprom_name",
        "eprom_data_dict",
        "input_file_path",
        "operation_flags",
        "address_str",
    ]


def test_read_region_docstring_still_pins_the_one_slice_site():
    assert "ONE place" in (ct._read_region.__doc__ or "")


def test_dispatch_sdp_leg_docstring_still_pins_the_verdict():
    assert "verdict" in (ct._dispatch_sdp_leg.__doc__ or "").lower()
