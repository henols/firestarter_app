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

Six anti-vacuity legs prove the gates are gates, not claims: one plants a
third `operator.read_eprom(...)` call site into an in-memory copy of the
source and asserts the census reports three; one asserts an emptied
expected enclosing-function allow-list fails rather than passing
vacuously; one plants a divergence term into `_dispatch_read`'s verdict
expression and asserts the verdict-source pin reddens; one plants a
second `_operation_context` item into `read_eprom`'s `with` header and
asserts the one-connect pin reddens; one plants a stray direct connect
call into `read_eprom`'s body and asserts the hardened one-connect pin
reddens while the pre-hardening claim still holds (WR-01, Phase 180, plan
180-04); and one plants a `last_ok` reassignment after `_dispatch_read`'s
run loop and asserts the hardened verdict-shape pin reddens while the
pre-hardening verdict-name pin still holds (WR-02, Phase 180, plan
180-04). All six operate on strings only; none writes a file.

Two further pins close PRUNE-08 (Phase 180, plan 180-01), each hardened in
plan 180-04: a structural assertion that `_dispatch_read`'s `verdict=`
expression resolves to exactly `last_ok` and the two verdict constants --
never anything derived from the read-vs-read divergence comparison (D-06
leg 3, roadmap criterion 3) -- extended to assert that `last_ok` is
assigned exactly twice inside `_dispatch_read`, once from a `True`
constant and once from the whole-device read inside its run loop; and a
structural, static pin (it proves the code's shape, not a runtime trace)
that one `operator.read_eprom` call costs exactly one full connect,
because `_operation_context` connects through
`_setup_operation`/`find_and_connect` on entry and disconnects inside a
non-empty `finally` block on exit (Ruling 1, D-02's structural half),
extended to count every connect-shaped call anywhere in `read_eprom`'s
body, not only the ones opened by its `with` header.
"""

import ast
import inspect
import pathlib

import pytest

from firestarter import chip_test as ct
from firestarter import eprom_operations as eo
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


_VERDICT_ANCHOR = "        verdict=VERDICT_OK if last_ok else VERDICT_BAD,\n"


def _verdict_expression_names(source: str) -> list[str]:
    """Return the sorted `ast.Name` ids reachable from `_dispatch_read`'s
    `StepResult(...)` `verdict=` keyword value.

    Extraction is unambiguous: `_dispatch_read` contains exactly one
    `StepResult(...)` call, so there is exactly one `verdict` keyword to
    resolve.
    """
    tree = ast.parse(source)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_dispatch_read"
    )
    call = next(
        c
        for c in ast.walk(fn)
        if isinstance(c, ast.Call)
        and isinstance(c.func, ast.Name)
        and c.func.id == "StepResult"
    )
    kw = next(k for k in call.keywords if k.arg == "verdict")
    return sorted({n.id for n in ast.walk(kw.value) if isinstance(n, ast.Name)})


def test_read_verdict_expression_reads_only_the_last_full_read_result():
    """Pins D-06 leg 3 / roadmap criterion 3, hardened in plan 180-04
    (WR-02). `tests/test_chip_test.py`'s `test_read_step_last_run_failure_yields_bad`
    and `test_read_step_first_run_failure_with_passing_last_run_yields_ok`
    already prove this behaviourally through the real `run_plan`. Two
    claims, both static, are what this pin proves that those two cannot:
    the `verdict=` expression resolves to exactly the three-name set
    `VERDICT_BAD`, `VERDICT_OK`, `last_ok` and nothing derived from the
    read-vs-read divergence comparison; and within `_dispatch_read`,
    `last_ok` is assigned exactly twice -- once from a `True` constant and
    once from the whole-device read inside the run loop -- so a later
    rebinding is caught structurally too. Its ceiling: both claims are
    static, neither observes a run, and neither says anything about what
    `read_eprom` itself returns.
    """
    source = _engine_source()
    assert _verdict_expression_names(source) == [
        "VERDICT_BAD",
        "VERDICT_OK",
        "last_ok",
    ]
    shape = _last_ok_assignment_shape(source)
    assert shape["tags"] == ["const_true", "read_eprom_call"]
    assert shape["for_loop_count"] == 1
    assert shape["read_assign_in_for_loop"] is True


def test_a_planted_divergence_term_in_the_verdict_reddens_the_pin():
    """Anti-vacuity leg (D-07) for the verdict-source pin. Plants the
    literal counter-example roadmap criterion 3 names -- a verdict
    expression that also consults the read-vs-read divergence term --
    into an in-memory copy of `_dispatch_read`'s verdict line, and asserts
    that the pin's own equality claim raises `AssertionError` against it.
    Strings only; no fixture file is written.
    """
    source = _engine_source()
    assert source.count(_VERDICT_ANCHOR) == 1
    mutated_anchor = (
        "        verdict=VERDICT_OK if last_ok and not divergence else VERDICT_BAD,\n"
    )
    mutant = source.replace(_VERDICT_ANCHOR, mutated_anchor, 1)
    mutant_names = _verdict_expression_names(mutant)
    assert mutant_names == [
        "VERDICT_BAD",
        "VERDICT_OK",
        "divergence",
        "last_ok",
    ]
    with pytest.raises(AssertionError):
        assert mutant_names == ["VERDICT_BAD", "VERDICT_OK", "last_ok"]


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


_CONTEXT_ANCHOR = "            size_str,\n        ) as (cmd_data, _, op_name):\n"


def _operations_source() -> str:
    source = pathlib.Path(eo.__file__).read_text(encoding="utf-8")
    assert len(source) > 1000
    return source


def _read_eprom_connect_shape(source: str) -> dict[str, object]:
    """Parse `source` and return the four structural clauses the
    one-connect-per-read premise rests on: how many `_operation_context`
    items `read_eprom`'s `with` header opens, whether `_operation_context`
    connects through `_setup_operation`/`find_and_connect` on entry,
    whether `_disconnect_programmer` is called from within a non-empty
    `finalbody` -- scanning the `finalbody` nodes specifically, not the
    whole function, is what makes clause 3 a real pin rather than a
    decorative one -- and, hardened in plan 180-04 (WR-01),
    `connect_route_calls`: every `ast.Call` anywhere in `read_eprom`'s own
    body whose attribute is `_operation_context`, `_setup_operation` or
    `find_and_connect`, scoped to `read_eprom`'s own `FunctionDef` node
    rather than the module -- scoping to the module would count
    `_operation_context`'s own legitimate `_setup_operation` call as a
    stray and make the pin false at HEAD.
    """
    tree = ast.parse(source)
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    read_eprom = functions["read_eprom"]
    context_count = sum(
        1
        for with_node in ast.walk(read_eprom)
        if isinstance(with_node, ast.With)
        for item in with_node.items
        if isinstance(item.context_expr, ast.Call)
        and isinstance(item.context_expr.func, ast.Attribute)
        and item.context_expr.func.attr == "_operation_context"
    )
    connect_route_calls = sum(
        1
        for n in ast.walk(read_eprom)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr
        in ("_operation_context", "_setup_operation", "find_and_connect")
    )
    operation_context = functions["_operation_context"]
    setup_operation = functions["_setup_operation"]
    calls_setup_operation = any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "_setup_operation"
        for n in ast.walk(operation_context)
    )
    calls_find_and_connect = any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "find_and_connect"
        for n in ast.walk(setup_operation)
    )
    disconnect_in_finalbody = False
    for try_node in ast.walk(operation_context):
        if isinstance(try_node, ast.Try) and try_node.finalbody:
            for final_stmt in try_node.finalbody:
                for sub in ast.walk(final_stmt):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "_disconnect_programmer"
                    ):
                        disconnect_in_finalbody = True
    return {
        "context_count": context_count,
        "connect_route_calls": connect_route_calls,
        "connect_chain": calls_setup_operation and calls_find_and_connect,
        "disconnect_in_finalbody": disconnect_in_finalbody,
    }


def test_one_read_eprom_call_costs_exactly_one_connect():
    """Pins Ruling 1 / D-02's structural half: one `read_eprom` call pays
    exactly one full connect, which is the premise the connect arithmetic
    in `180-PRUNE-08-CLOSURE.md` rests on -- an N-block sample therefore
    pays N connects, not one. Hardened in plan 180-04 (WR-01):
    `connect_route_calls` extends the claim from the `with` header alone
    to every connect-shaped call anywhere in `read_eprom`'s body, so a
    stray direct connect call outside the `with` header is now caught
    statically too.

    Honest ceiling, stated plainly: this is a static pin. It proves the
    code is shaped so one call opens one context that connects and
    disconnects; it does not prove at runtime that exactly one serial open
    occurred, and it says nothing about the retry-across-ports walk inside
    `find_and_connect`. That ceiling is sufficient because the closing
    argument needs a statement about shape, not about a runtime trace.
    """
    shape = _read_eprom_connect_shape(_operations_source())
    assert shape["context_count"] == 1
    assert shape["connect_route_calls"] == 1
    assert shape["connect_chain"] is True
    assert shape["disconnect_in_finalbody"] is True


def test_a_planted_second_operation_context_in_read_eprom_reddens_the_pin():
    """Anti-vacuity leg (D-07) for the one-connect pin. Plants the literal
    counter-example the connect arithmetic cares about -- a read that pays
    two connects instead of one -- by extending `read_eprom`'s `with`
    header with a second `_operation_context` item bound to a throwaway
    name, and asserts the mutated clause-1 count reddens the pin's claim.
    Strings only; no fixture file is written.
    """
    source = _operations_source()
    assert source.count(_CONTEXT_ANCHOR) == 1
    mutated_anchor = (
        "            size_str,\n"
        "        ) as (cmd_data, _, op_name), self._operation_context(\n"
        "            eprom_name, eprom_data_dict, cmd\n"
        "        ) as _extra_context:\n"
    )
    mutant = source.replace(_CONTEXT_ANCHOR, mutated_anchor, 1)
    mutant_shape = _read_eprom_connect_shape(mutant)
    assert mutant_shape["context_count"] == 2
    with pytest.raises(AssertionError):
        assert mutant_shape["context_count"] == 1


_STRAY_CONNECT_ANCHOR = (
    '            actual_output_file = output_file or f"{eprom_name.upper()}.bin"\n'
)


def test_a_planted_stray_setup_operation_call_in_read_eprom_reddens_the_hardened_pin():
    """Anti-vacuity leg (D-07) for WR-01. Plants a direct
    `self._setup_operation(...)` call into `read_eprom`'s body, outside
    its `with` header, so the mutant is invisible to `context_count` --
    the pre-hardening pin's whole claim, unmoved -- and visible only to
    the body-wide `connect_route_calls` count the hardened pin adds.
    Strings only; no fixture file is written.
    """
    source = _operations_source()
    assert source.count(_STRAY_CONNECT_ANCHOR) == 1
    mutated_anchor = _STRAY_CONNECT_ANCHOR + (
        "            self._setup_operation(eprom_name, eprom_data_dict, COMMAND_READ)\n"
    )
    mutant = source.replace(_STRAY_CONNECT_ANCHOR, mutated_anchor, 1)
    mutant_shape = _read_eprom_connect_shape(mutant)
    assert mutant_shape["context_count"] == 1
    assert mutant_shape["connect_route_calls"] == 2
    with pytest.raises(AssertionError):
        assert mutant_shape["connect_route_calls"] == 1


_LAST_OK_REASSIGN_ANCHOR = '    reason = "read runs diverged" if diverged else ""\n'


def _last_ok_assignment_shape(source: str) -> dict[str, object]:
    """Parse `source` and return `_dispatch_read`'s assignment shape for
    `last_ok`: one `tags` entry per node targeting `last_ok`, in source
    order, plus whether the run loop is where the read-eprom assignment
    lives.

    `tags` classifies each assignment node: `const_true` for a plain
    `ast.Assign` whose value is the constant `True`, `read_eprom_call` for
    an `ast.Assign` whose value is a call to `.read_eprom(...)`, and
    `other` for anything else -- an augmented assignment, an annotated
    assignment, a walrus, or a reassignment to any other value. `other` is
    what makes the list a closed claim: any third assignment, any
    augmented assignment and any walrus on `last_ok` changes the list and
    reddens a pin asserting it.
    """
    tree = ast.parse(source)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_dispatch_read"
    )
    targets: list[tuple[int, int, ast.AST]] = []
    for n in ast.walk(fn):
        target: ast.AST | None = None
        if isinstance(n, ast.Assign) and len(n.targets) == 1:
            target = n.targets[0]
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
            target = n.target
        if (
            isinstance(target, ast.Name)
            and target.id == "last_ok"
            and isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.NamedExpr))
        ):
            targets.append((n.lineno, n.col_offset, n))
    targets.sort(key=lambda t: (t[0], t[1]))

    def _tag(node: ast.AST) -> str:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and node.value.value is True
        ):
            return "const_true"
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "read_eprom"
        ):
            return "read_eprom_call"
        return "other"

    tagged = [(_tag(node), node) for _, _, node in targets]
    tags = [tag for tag, _ in tagged]
    for_loops = [n for n in ast.walk(fn) if isinstance(n, ast.For)]
    read_assign_node = next(
        (node for tag, node in tagged if tag == "read_eprom_call"), None
    )
    read_assign_in_for_loop = read_assign_node is not None and any(
        any(sub is read_assign_node for sub in ast.walk(for_loop))
        for for_loop in for_loops
    )
    return {
        "tags": tags,
        "for_loop_count": len(for_loops),
        "read_assign_in_for_loop": read_assign_in_for_loop,
    }


def test_a_planted_last_ok_reassignment_before_the_return_reddens_the_hardened_pin():
    """Anti-vacuity leg (D-07) for WR-02. Plants a `last_ok` rebinding
    after `_dispatch_read`'s `reason` assignment, past the run loop, so
    the mutant is invisible to `_verdict_expression_names` -- the
    pre-hardening pin's whole claim, unmoved -- and visible only to the
    hardened `_last_ok_assignment_shape`'s `tags`. Strings only; no
    fixture file is written.
    """
    source = _engine_source()
    assert source.count(_LAST_OK_REASSIGN_ANCHOR) == 1
    mutated_anchor = _LAST_OK_REASSIGN_ANCHOR + (
        "    last_ok = last_ok and not divergence\n"
    )
    mutant = source.replace(_LAST_OK_REASSIGN_ANCHOR, mutated_anchor, 1)
    assert _verdict_expression_names(mutant) == [
        "VERDICT_BAD",
        "VERDICT_OK",
        "last_ok",
    ]
    mutant_shape = _last_ok_assignment_shape(mutant)
    assert mutant_shape["tags"] == ["const_true", "read_eprom_call", "other"]
    with pytest.raises(AssertionError):
        assert mutant_shape["tags"] == ["const_true", "read_eprom_call"]
