"""
GATE-01: AST invariant checker over `firestarter/sdp_capability.py`
(Phase 121 Plan 03, D-14).

Scans exactly one target file, resolved via `FIRESTARTER_SDP_CAPABILITY_SRC`
(default: the real `firestarter/sdp_capability.py`, mirrored by
`_DEFAULT_SDP_CAPABILITY_SRC` below -- the same env-override seam shape as
`tools/check_devtest_orchestrator.py`'s `FIRESTARTER_DEVTEST_SRC` and
`tools/check_is_memory_cmd_no_ifdef.py`'s `FIRESTARTER_CMD_ADMISSION_SRC`).

Phase 120 derived a static, fail-closed, name-keyed SDP-capability partition
(43 ALLOW / 41 REFUSE) from `infoic.xml`'s `INFOIC2PLUS` `flags` bit 15 and
landed it as `SDP_CAPABLE_TOKENS`, a `frozenset` of string literals. Nothing
structurally prevents a future edit from widening that allow-list back into
inference, or from adding a permit path not dominated by a membership test
against it. This gate denies two violation classes to make both mechanically
impossible to land unnoticed:

  Class 1 -- permit-by-default. Protects silicon: a permit reaching a part
  with no SDP command decoder stores the SDP command bytes as DATA at the
  bus-truncated magic addresses (see `sdp_capability.py`'s `REASON_NOT_CAPABLE`
  wording) rather than being recognised as a command -- this is the concrete
  hardware consequence a permit-by-default predicate would cause.
    (a) any `return` of a tuple literal whose first element is the constant
        `True`, when that return is not lexically dominated -- earlier in the
        same function body, by line number -- by a membership test (`in` /
        `not in`) against the name `SDP_CAPABLE_TOKENS`;
    (b) any bare exception handler (`except:` with no exception type)
        anywhere in the module, which could swallow a refusal-shaped error
        into a silent permit.

  Class 2 -- widenable allow-set. Protects the derived 43/41 partition from
  drifting back into inference:
    (a) `SDP_CAPABLE_TOKENS` bound anywhere other than exactly once at module
        level;
    (b) that single binding's value being anything other than a direct
        `frozenset(...)` call whose sole argument is a set/list/tuple display
        of string literals only (never a comprehension, a generator
        expression, a call, or a bare name reference);
    (c) any augmented assignment, `.union(...)`, `.add(...)`, `.update(...)`,
        or `|=` targeting `SDP_CAPABLE_TOKENS`, anywhere in the scanned file.

Anti-hollow contract (the discipline that closed this project's v1.12
GATE-03 hollow-checker debt -- a checker that could never fail because it
asserted nothing concrete): this is a genuinely-populated `ast.parse` +
`ast.NodeVisitor` walk, never a declared-empty detector. It is paired with
`tests/test_check_sdp_capability.py`, which plants a REAL subprocess-level
violation per class (`tests/fixtures/planted_permit_by_default.py`,
`tests/fixtures/planted_widenable_allowset.py`), injected via the
`FIRESTARTER_SDP_CAPABILITY_SRC` env-override -- never an in-process
synthetic -- and asserts a clean fixture routed through the same seam still
passes, isolating the planted violations as the true cause of any failure.
The gate also fails closed on a missing target path and on a zero-symbol
scan (`SDP_CAPABLE_TOKENS` not found exactly once) -- neither degenerate
input is ever silently reported as a PASS.

Exit codes:
  0 -- the scanned source contains zero permit-by-default violations, zero
       widenable-allow-set violations, and `SDP_CAPABLE_TOKENS` is bound
       exactly once (PASS: line printed, naming the resolved target path).
  1 -- at least one deny-list violation was found (FAIL: per-class summary
       printed), OR the resolved target path does not exist (ERROR: printed
       to stderr), OR the target does not parse as Python (ERROR: printed to
       stderr). Never a silent pass on a degenerate input.
"""

from __future__ import annotations

import ast
import os
import sys

# Module-top path constants (mirrors tools/check_devtest_orchestrator.py:80-86
# and tools/check_is_memory_cmd_no_ifdef.py:82-94's env-overridable
# path-constant idiom).
_HERE = os.path.dirname(__file__)
_DEFAULT_SDP_CAPABILITY_SRC = os.path.join(
    _HERE, "..", "firestarter", "sdp_capability.py"
)

# Env-override seam: lets the paired pytest point this checker at a
# deliberately-violating fixture file without editing the real, clean
# sdp_capability.py (D-14 anti-hollow contract). This seam is FAIL-CLOSED --
# a path that does not exist is an ERROR, never a silent pass (see main()).
FIRESTARTER_SDP_CAPABILITY_SRC = os.environ.get(
    "FIRESTARTER_SDP_CAPABILITY_SRC", _DEFAULT_SDP_CAPABILITY_SRC
)

# The one symbol this gate is scoped to.
_TOKEN_SET_NAME = "SDP_CAPABLE_TOKENS"

# Class 2(c) method names that widen/mutate a frozenset in place.
_WIDENING_METHOD_NAMES = frozenset({"union", "add", "update"})


def _is_true_constant(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _is_membership_test_against_token_set(node: ast.expr) -> bool:
    """True if `node` is a `Compare` using `In`/`NotIn` with a comparator
    naming `SDP_CAPABLE_TOKENS` (either side -- `x in SDP_CAPABLE_TOKENS` or,
    defensively, `SDP_CAPABLE_TOKENS` itself compared)."""
    if not isinstance(node, ast.Compare):
        return False
    if not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
        return False
    candidates = [node.left, *node.comparators]
    return any(isinstance(c, ast.Name) and c.id == _TOKEN_SET_NAME for c in candidates)


class _PermitByDefaultVisitor(ast.NodeVisitor):
    """Walk a single function body IN SOURCE ORDER, recording the line of the
    first `SDP_CAPABLE_TOKENS` membership test seen, then flagging any
    tuple-literal `(True, ...)` return that occurs before that line (or when
    no such membership test exists anywhere in the function) -- Class 1(a).

    Source-order tracking is done by collecting `(lineno, kind)` events
    across the WHOLE function subtree (comprehension `ifs` are `Compare`
    nodes too, so they are covered by the same generic walk) and then
    resolving dominance with a single ascending-lineno scan, per D-14's
    "ordered walk ... then compare line numbers" instruction -- this avoids
    depending on `ast.walk`'s BFS traversal order, which is not source order.
    """

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self._events: list[tuple[int, str]] = []

    def visit_Compare(self, node: ast.Compare) -> None:
        if _is_membership_test_against_token_set(node):
            self._events.append((node.lineno, "member_test"))
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        value = node.value
        if (
            isinstance(value, ast.Tuple)
            and len(value.elts) >= 1
            and _is_true_constant(value.elts[0])
        ):
            self._events.append((node.lineno, "permit_return"))
        self.generic_visit(node)

    def resolve(self) -> list[str]:
        violations: list[str] = []
        seen_member_test = False
        for lineno, kind in sorted(self._events, key=lambda e: e[0]):
            if kind == "member_test":
                seen_member_test = True
            elif kind == "permit_return" and not seen_member_test:
                violations.append(
                    f"{self.filename}:{lineno}: tuple return starting with "
                    "`True` is not dominated by a membership test against "
                    f"{_TOKEN_SET_NAME} (permit-by-default, D-14 Class 1a)"
                )
        return violations


def _find_permit_by_default_violations(tree: ast.Module, filename: str) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visitor = _PermitByDefaultVisitor(filename)
            visitor.visit(node)
            violations.extend(visitor.resolve())
    return violations


def _find_bare_except_violations(tree: ast.Module, filename: str) -> list[str]:
    """Class 1(b): any `ast.ExceptHandler` with `type is None` anywhere in
    the module -- a bare `except:` could swallow a refusal-shaped error into
    a silent permit."""
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            violations.append(
                f"{filename}:{node.lineno}: bare `except:` handler wrapping "
                "predicate work could swallow a refusal into a silent "
                "permit (D-14 Class 1b)"
            )
    return violations


def _module_level_token_set_bindings(
    tree: ast.Module,
) -> list[ast.Assign | ast.AnnAssign | ast.AugAssign]:
    """Every top-level (module-body) statement that binds `SDP_CAPABLE_TOKENS`
    -- `Assign`, `AnnAssign`, or `AugAssign` -- in source order. An `AugAssign`
    counts as a second binding event: it rebinds the name, which is exactly
    what "bound anywhere other than exactly once" (D-14 Class 2a) means."""
    bindings: list[ast.Assign | ast.AnnAssign | ast.AugAssign] = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == _TOKEN_SET_NAME:
                    bindings.append(stmt)
                    break
        elif isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name) and stmt.target.id == _TOKEN_SET_NAME:
                bindings.append(stmt)
        elif isinstance(stmt, ast.AugAssign):
            if isinstance(stmt.target, ast.Name) and stmt.target.id == _TOKEN_SET_NAME:
                bindings.append(stmt)
    return bindings


def _is_string_literal_display(value: ast.expr) -> bool:
    """True if `value` is a `Set`/`List`/`Tuple` display whose every element
    is a string-`Constant` literal -- the only shape Class 2(b) permits as
    the single `frozenset(...)` argument."""
    if not isinstance(value, (ast.Set, ast.List, ast.Tuple)):
        return False
    return all(
        isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        for elt in value.elts
    )


def _is_clean_frozenset_of_literals_call(value: ast.expr) -> bool:
    """Class 2(b)'s permitted shape: a direct `frozenset(<display>)` call
    with exactly one positional argument, no keywords, and that argument a
    set/list/tuple display of string literals only -- rejects a
    comprehension, a generator expression, a call, or a bare `Name`
    reference, per D-14."""
    if not isinstance(value, ast.Call):
        return False
    if not (isinstance(value.func, ast.Name) and value.func.id == "frozenset"):
        return False
    if value.keywords or len(value.args) != 1:
        return False
    return _is_string_literal_display(value.args[0])


def _find_widenable_allowset_violations(
    tree: ast.Module, filename: str
) -> tuple[list[str], int]:
    """Class 2: returns `(violations, binding_count)`. `binding_count` is
    reported in the PASS line and used by the fail-closed symbol guard."""
    violations: list[str] = []
    bindings = _module_level_token_set_bindings(tree)
    count = len(bindings)

    if count != 1:
        violations.append(
            f"{filename}: {_TOKEN_SET_NAME} bound {count} time(s) at module "
            "level (expected exactly 1) -- the gate cannot vacuously pass "
            "when its subject symbol is not found exactly once "
            "(widenable-allow-set, D-14 Class 2a)"
        )
    else:
        (binding,) = bindings
        if isinstance(binding, ast.AugAssign):
            # An AugAssign as the SOLE binding means the name is rebound
            # in-place with no prior definition -- Class 2(c) on its own,
            # and there is no "shape" of a prior frozenset(...) to check.
            violations.append(
                f"{filename}:{binding.lineno}: {_TOKEN_SET_NAME} rebound via "
                "augmented assignment with no prior module-level binding "
                "(widenable-allow-set, D-14 Class 2c)"
            )
        else:
            value = binding.value
            if value is not None and not _is_clean_frozenset_of_literals_call(value):
                violations.append(
                    f"{filename}:{binding.lineno}: {_TOKEN_SET_NAME} is not "
                    "bound from a direct frozenset(...) call over a "
                    "set/list/tuple display of string literals only "
                    "(widenable-allow-set, D-14 Class 2b)"
                )

    # Class 2(c): any augmented assignment, `.union(...)`, `.add(...)`, or
    # `.update(...)` targeting SDP_CAPABLE_TOKENS, anywhere in the file (not
    # just at module level -- e.g. inside a function).
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == _TOKEN_SET_NAME
            and node not in bindings
        ):
            violations.append(
                f"{filename}:{node.lineno}: augmented assignment (`|=` or "
                f"similar) targeting {_TOKEN_SET_NAME} (widenable-allow-set, "
                "D-14 Class 2c)"
            )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                node.func.attr in _WIDENING_METHOD_NAMES
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == _TOKEN_SET_NAME
            ):
                violations.append(
                    f"{filename}:{node.lineno}: `{_TOKEN_SET_NAME}."
                    f"{node.func.attr}(...)` widens/mutates the allow-set "
                    "in place (widenable-allow-set, D-14 Class 2c)"
                )

    return violations, count


def _print_bucket(label: str, violations: list[str]) -> None:
    print(f"FAIL: {len(violations)} {label}:")
    for v in violations[:20]:
        print(f"  {v}")
    if len(violations) > 20:
        print(f"  ... and {len(violations) - 20} more")


def main() -> int:
    """Entry point: resolve `FIRESTARTER_SDP_CAPABILITY_SRC` and scan it.

    Prints a PASS: line (naming the resolved target path) and returns 0 when
    the scanned source contains zero permit-by-default violations, zero
    widenable-allow-set violations, and `SDP_CAPABLE_TOKENS` is bound exactly
    once. Prints a FAIL: summary and returns 1 on any violation. Prints an
    ERROR: message to stderr and returns 1 (fail-closed) if the resolved
    target does not exist, or does not parse as Python.
    """
    path = FIRESTARTER_SDP_CAPABILITY_SRC
    if not os.path.isfile(path):
        print(f"ERROR: source file not found: {path}", file=sys.stderr)
        return 1

    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        print(f"ERROR: could not read source file {path}: {e}", file=sys.stderr)
        return 1

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        print(f"ERROR: {path} does not parse as Python: {e}", file=sys.stderr)
        return 1

    permit_by_default_violations = _find_permit_by_default_violations(tree, path)
    permit_by_default_violations.extend(_find_bare_except_violations(tree, path))
    widenable_allowset_violations, binding_count = _find_widenable_allowset_violations(
        tree, path
    )

    if permit_by_default_violations or widenable_allowset_violations:
        if permit_by_default_violations:
            _print_bucket(
                "permit-by-default violation(s)", permit_by_default_violations
            )
        if widenable_allowset_violations:
            _print_bucket(
                "widenable-allow-set violation(s)", widenable_allowset_violations
            )
        return 1

    print(
        f"PASS: scanned {os.path.relpath(path, _HERE)}; "
        "0 permit-by-default, 0 widenable-allow-set; "
        f"{_TOKEN_SET_NAME} bound exactly {binding_count} time"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
