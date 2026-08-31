"""
GATE-02: AST invariant checker over
`firestarter/protection_readability.py`.

Scans exactly one target file, resolved via
`FIRESTARTER_PROTECTION_READABILITY_SRC` (default: the real
`firestarter/protection_readability.py`, mirrored by
`_DEFAULT_PROTECTION_READABILITY_SRC` below -- the same env-override seam
shape as `tools/check_sdp_capability_invariants.py`'s
`FIRESTARTER_SDP_CAPABILITY_SRC`, `tools/check_devtest_orchestrator.py`'s
`FIRESTARTER_DEVTEST_SRC`, and `tools/check_is_memory_cmd_no_ifdef.py`'s
`FIRESTARTER_CMD_ADMISSION_SRC` -- four instances of the same convention now).
The `_HERE`-relative default is safe here because `tools/` is a fixed
sibling of `firestarter/`, so the resolved target is not phase-relative --
this is **not** the `check_permitted_claims.py` failure mode, where `_HERE`
resolves to the *checking phase's own directory* and a cross-phase reuse of
the checker silently scans nothing and exits 0. `tools/` never moves
relative to `firestarter/` when this gate is reused from a later phase.

The module holds a three-axis, hand-curated table (readability,
mechanism, permanence) sourced from the wiki page `Lockable PROMs`. Nothing
structurally prevents a future edit from widening the curated readable-token
set back into inference, or from adding a permit path not dominated by a
membership test. This gate denies four violation classes to make each
mechanically impossible to land unnoticed:

  Class 1 -- permit-by-default. State the concrete consequence: a permit
  reaching a family whose protection state is not readable would let a
  downstream layer render a state claim the silicon never supplied, which is
  what this gate exists to prevent.
    (a) generalised from `check_sdp_capability_invariants.py`'s Class 1(a).
        That gate flags a `return` of a tuple literal whose first element is
        the constant `True`, only when undominated by a membership test.
        Here, flag a `return` of a tuple literal whose first element is a
        string `Constant` belonging to `_SILICON_ONLY_TOKENS` --
        `"protected"` / `"unprotected"`, the two class tokens producible
        only by a response-consuming function -- and flag it
        UNCONDITIONALLY, dominated or not. Unlike the analog's `True`,
        these two tokens must never be returned from this pure module at
        all, because `protection_gate_for_entry`'s signature accepts no
        device response -- there is no legitimate dominated case to exempt
        .
    (b) any bare exception handler (`except:` with no exception type)
        anywhere in the module, which could swallow a refusal-shaped error
        into a silent permit -- copied unchanged from the analog. This rule
        carries more weight here than the analog's docstring implies: this
        repository's ruff `select` list is `[E, F, I, UP]`, which does not
        include `BLE001`, so every `# noqa: BLE001` in this codebase is
        inert and a broad catch on a refusal path is gated by nothing else
        that ruff would otherwise supply.

  Class 2 -- widenable token sets. Protects the two curated frozensets from
  drifting back into inference. `_TOKEN_SET_NAMES` parameterises the
  analog's single `_TOKEN_SET_NAME` string into a 2-tuple
  (`DOCUMENTED_READABLE_TOKENS`, `DOCUMENTED_NOT_READABLE_TOKENS`), and the
  analog's `_module_level_token_set_bindings` /
  `_is_string_literal_display` / `_is_clean_frozenset_of_literals_call`
  logic is applied per name:
    (a) either name bound anywhere other than exactly once at module level;
    (b) either binding's value being anything other than a direct
        `frozenset(...)` call whose sole positional argument is a
        set/list/tuple display of string literals only (never a
        comprehension, a generator expression, a call, or a bare `Name`);
    (c) any augmented assignment, `.union(...)`, `.add(...)`,
        `.update(...)`, or `|=` targeting either name, anywhere in the
        scanned file.
  The fail-closed symbol guard checks a binding count PER name; the PASS
  line reports the resolved relpath and the per-name binding counts, so a
  zero-symbol scan on EITHER name is a failure, never a silent pass.

  Class 3 -- the reporting axes, DELIBERATELY WEAKER. `MECHANISM_BY_TOKEN`
  and `PERMANENCE_BY_TOKEN` are literal dict displays consumed only by
  prose and do not gate answering anywhere -- the read/refuse decision keys
  decision only on the readability axis (Class 2), never on mechanism or
  permanence. This rule is stated here, in words, as weaker by design: each
  of the two mappings is checked only for (i) exactly one module-level
  binding and (ii) that binding being an `ast.Dict` literal whose every key
  and every value is a string `Constant` -- no Class-1-style dominance
  analysis is attempted over either mapping, and no check confirms their
  keys are drawn from the curated token sets. `protection_readability.py`'s
  own comment above the two mappings makes the same weaker-by-design claim
  independently, in prose a human reads without running this gate.

  Class 4 -- the ambiguity record is not empty. `AMBIGUOUS_DOC_CITATIONS`
  must be bound exactly once at module level to an `ast.Dict` literal with
  at least one key. An empty record would mean the C-17 documentation
  disagreement (`lockable-proms.md`'s bare `W29C020` vs. its own
  restatements) had been silently resolved away rather than recorded, which
  is precisely what the tiebreak rule forbids.

Anti-hollow contract (the discipline that closed this project's v1.12
hollow-checker debt -- a checker that could never fail because it
asserted nothing concrete): this is a genuinely-populated `ast.parse` +
`ast.NodeVisitor` walk, never a declared-empty detector. It is paired with
`tests/test_check_protection_readability.py`, which plants a REAL
subprocess-level violation per class via two committed fixtures --
`tests/fixtures/planted_protection_permit_by_default.py` (Class 1) and
`tests/fixtures/planted_protection_widenable_tokenset.py` (Class 2) --
injected via the `FIRESTARTER_PROTECTION_READABILITY_SRC` env-override --
never an in-process synthetic -- and asserts a clean fixture routed through
the same seam still passes, isolating the planted violations as the true
cause of any failure. The gate also fails closed on a missing target path,
on unparsable source, and on a zero-symbol scan for EITHER gated token-set
name -- no degenerate input is ever silently reported as a PASS.

Exit codes:
  0 -- the scanned source contains zero Class 1, zero Class 2, zero Class 3
       and zero Class 4 violations, and both `_TOKEN_SET_NAMES` members are
       each bound exactly once (PASS: line printed, naming the resolved
       target path and both binding counts).
  1 -- at least one violation was found (FAIL: per-class summary printed),
       OR the resolved target path does not exist (ERROR: printed to
       stderr), OR the target does not parse as Python (ERROR: printed to
       stderr). Never a silent pass on a degenerate input.
"""

from __future__ import annotations

import ast
import os
import sys

# Module-top path constants (mirrors
# tools/check_sdp_capability_invariants.py:76-79's env-overridable
# path-constant idiom).
_HERE = os.path.dirname(__file__)
_DEFAULT_PROTECTION_READABILITY_SRC = os.path.join(
    _HERE, "..", "firestarter", "protection_readability.py"
)

# Env-override seam: lets the paired pytest point this checker at a
# deliberately-violating fixture file without editing the real, clean
# protection_readability.py. This seam is FAIL-CLOSED -- a path that does
# not exist is an ERROR, never a silent pass (see main()).
FIRESTARTER_PROTECTION_READABILITY_SRC = os.environ.get(
    "FIRESTARTER_PROTECTION_READABILITY_SRC", _DEFAULT_PROTECTION_READABILITY_SRC
)

# The two symbols Class 2 is scoped to (Option A, 151-DESIGN.md's
# recommendation: a set-per-readability-state instead of one three-tuple
# dict, so the existing literal-frozenset-only machinery applies to both
# names for free).
_TOKEN_SET_NAMES: tuple[str, ...] = (
    "DOCUMENTED_READABLE_TOKENS",
    "DOCUMENTED_NOT_READABLE_TOKENS",
)

# The two output classes that require a real silicon read and must
# therefore be structurally unreachable from this pure module.
_SILICON_ONLY_TOKENS: frozenset[str] = frozenset({"protected", "unprotected"})

# Class 2(c) method names that widen/mutate a frozenset in place.
_WIDENING_METHOD_NAMES = frozenset({"union", "add", "update"})

# Class 3's two reporting-only mappings, checked by the deliberately weaker
# rule (exactly-once binding to a literal str->str ast.Dict; no dominance
# analysis, no key-provenance check).
_REPORTING_DICT_NAMES: tuple[str, ...] = ("MECHANISM_BY_TOKEN", "PERMANENCE_BY_TOKEN")

# Class 4's non-empty ambiguity record.
_AMBIGUOUS_DICT_NAME = "AMBIGUOUS_DOC_CITATIONS"


def _silicon_only_token_constant(node: ast.expr) -> str | None:
    """Return the string value if `node` is a `Constant` string member of
    `_SILICON_ONLY_TOKENS`, else `None`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if node.value in _SILICON_ONLY_TOKENS:
            return node.value
    return None


def _is_membership_test_against_token_set(node: ast.expr) -> bool:
    """True if `node` is a `Compare` using `In`/`NotIn` with a comparator
    naming one of `_TOKEN_SET_NAMES` (either side)."""
    if not isinstance(node, ast.Compare):
        return False
    if not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
        return False
    candidates = [node.left, *node.comparators]
    return any(isinstance(c, ast.Name) and c.id in _TOKEN_SET_NAMES for c in candidates)


class _SiliconOnlyReturnVisitor(ast.NodeVisitor):
    """Walk a single function body IN SOURCE ORDER, recording the line of
    any membership test against a gated token-set name and the line/token
    of any tuple-literal return whose first element is a
    `_SILICON_ONLY_TOKENS` member -- Class 1(a), generalised from the
    analog's `_PermitByDefaultVisitor`
    (`check_sdp_capability_invariants.py:112-157`).

    Source-order tracking reuses the analog's shape verbatim: a
    `(lineno, kind)` event list resolved by a single ascending-lineno scan,
    never `ast.walk`'s BFS order. UNLIKE the analog, a prior membership test
    does not exempt a `permit_return` event here -- `member_test` events are
    still collected (structural parity with the analog, and so a later rule
    could reuse them), but `resolve()` flags every `permit_return`
    unconditionally, dominated or not: unlike the analog's `True`,
    `protected`/`unprotected` must never be returned from this pure module
    at all, because its signature accepts no device response.
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
        if isinstance(value, ast.Tuple) and len(value.elts) >= 1:
            token = _silicon_only_token_constant(value.elts[0])
            if token is not None:
                self._events.append((node.lineno, f"permit_return:{token}"))
        self.generic_visit(node)

    def resolve(self) -> list[str]:
        violations: list[str] = []
        for lineno, kind in sorted(self._events, key=lambda e: e[0]):
            if kind.startswith("permit_return:"):
                token = kind.split(":", 1)[1]
                violations.append(
                    f"{self.filename}:{lineno}: tuple return starting with "
                    f"silicon-only class token {token!r} is forbidden here "
                    "UNCONDITIONALLY, dominated or not, because this pure "
                    "module's signature accepts no device response and "
                    "protected/unprotected must never be returned from it "
                    "at all (Class 1a)"
                )
        return violations


def _find_permit_by_default_violations(tree: ast.Module, filename: str) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visitor = _SiliconOnlyReturnVisitor(filename)
            visitor.visit(node)
            violations.extend(visitor.resolve())
    return violations


def _find_bare_except_violations(tree: ast.Module, filename: str) -> list[str]:
    """Class 1(b): any `ast.ExceptHandler` with `type is None` anywhere in
    the module -- a bare `except:` could swallow a refusal-shaped error
    into a silent permit. `# noqa: BLE001` on such a handler does not save
    it: this repository's ruff `select` is `[E, F, I, UP]`, so `BLE001` is
    never selected and the noqa is inert."""
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            violations.append(
                f"{filename}:{node.lineno}: bare `except:` handler could "
                "swallow a refusal into a silent permit (Class 1b) -- any "
                "`# noqa: BLE001` here is inert because this repository's "
                "ruff select list is [E, F, I, UP]"
            )
    return violations


def _module_level_bindings(
    tree: ast.Module, name: str
) -> list[ast.Assign | ast.AnnAssign | ast.AugAssign]:
    """Every top-level (module-body) statement that binds `name` -- `Assign`,
    `AnnAssign`, or `AugAssign` -- in source order."""
    bindings: list[ast.Assign | ast.AnnAssign | ast.AugAssign] = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    bindings.append(stmt)
                    break
        elif isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name) and stmt.target.id == name:
                bindings.append(stmt)
        elif isinstance(stmt, ast.AugAssign):
            if isinstance(stmt.target, ast.Name) and stmt.target.id == name:
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
    reference."""
    if not isinstance(value, ast.Call):
        return False
    if not (isinstance(value.func, ast.Name) and value.func.id == "frozenset"):
        return False
    if value.keywords or len(value.args) != 1:
        return False
    return _is_string_literal_display(value.args[0])


def _find_widenable_tokenset_violations(
    tree: ast.Module, filename: str
) -> tuple[list[str], dict[str, int]]:
    """Class 2, per gated name: returns `(violations, binding_counts)`.
    `binding_counts` maps each of `_TOKEN_SET_NAMES` to its module-level
    binding count, reported in the PASS line and used by the fail-closed
    symbol guard."""
    violations: list[str] = []
    counts: dict[str, int] = {}
    all_bindings: list[ast.Assign | ast.AnnAssign | ast.AugAssign] = []

    for name in _TOKEN_SET_NAMES:
        bindings = _module_level_bindings(tree, name)
        counts[name] = len(bindings)
        all_bindings.extend(bindings)

        if len(bindings) != 1:
            violations.append(
                f"{filename}: {name} bound {len(bindings)} time(s) at "
                "module level (expected exactly 1) -- the gate cannot "
                "vacuously pass when its subject symbol is not found "
                "exactly once (widenable-token-set, Class 2a)"
            )
            continue

        (binding,) = bindings
        if isinstance(binding, ast.AugAssign):
            # An AugAssign as the SOLE binding means the name is rebound
            # in-place with no prior definition -- Class 2(c) on its own,
            # and there is no "shape" of a prior frozenset(...) to check.
            violations.append(
                f"{filename}:{binding.lineno}: {name} rebound via augmented "
                "assignment with no prior module-level binding "
                "(widenable-token-set, Class 2c)"
            )
        else:
            value = binding.value
            if value is not None and not _is_clean_frozenset_of_literals_call(value):
                violations.append(
                    f"{filename}:{binding.lineno}: {name} is not bound "
                    "from a direct frozenset(...) call over a set/list/"
                    "tuple display of string literals only "
                    "(widenable-token-set, Class 2b)"
                )

    # Class 2(c): any augmented assignment, `.union(...)`, `.add(...)`, or
    # `.update(...)` targeting either gated name, anywhere in the file (not
    # just at module level -- e.g. inside a function).
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in _TOKEN_SET_NAMES
            and node not in all_bindings
        ):
            violations.append(
                f"{filename}:{node.lineno}: augmented assignment (`|=` or "
                f"similar) targeting {node.target.id} "
                "(widenable-token-set, Class 2c)"
            )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                node.func.attr in _WIDENING_METHOD_NAMES
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in _TOKEN_SET_NAMES
            ):
                violations.append(
                    f"{filename}:{node.lineno}: `{node.func.value.id}."
                    f"{node.func.attr}(...)` widens/mutates the token set "
                    "in place (widenable-token-set, Class 2c)"
                )

    return violations, counts


def _is_literal_str_to_str_dict(value: ast.expr) -> bool:
    """Class 3's deliberately weaker shape check: `value` is an `ast.Dict`
    whose every key and every value is a string `Constant`. No dominance
    analysis, no key-provenance check against the curated token sets --
    that weakening is intentional (answering keys only on readability,
    never on mechanism or permanence) and is stated in this gate's module
    docstring."""
    if not isinstance(value, ast.Dict):
        return False
    for key, val in zip(value.keys, value.values):
        if key is None or not (
            isinstance(key, ast.Constant) and isinstance(key.value, str)
        ):
            return False
        if not (isinstance(val, ast.Constant) and isinstance(val.value, str)):
            return False
    return True


def _find_reporting_dict_violations(tree: ast.Module, filename: str) -> list[str]:
    """Class 3: `MECHANISM_BY_TOKEN` / `PERMANENCE_BY_TOKEN`, each bound
    exactly once at module level to a literal str->str dict display."""
    violations: list[str] = []
    for name in _REPORTING_DICT_NAMES:
        bindings = _module_level_bindings(tree, name)
        if len(bindings) != 1:
            violations.append(
                f"{filename}: {name} bound {len(bindings)} time(s) at "
                "module level (expected exactly 1) -- Class 3 is weaker "
                "than Class 2 but still requires exactly-once binding"
            )
            continue
        (binding,) = bindings
        value = (
            binding.value if isinstance(binding, (ast.Assign, ast.AnnAssign)) else None
        )
        if value is not None and not _is_literal_str_to_str_dict(value):
            violations.append(
                f"{filename}:{binding.lineno}: {name} is not a literal "
                "dict whose every key and value is a string constant "
                "(Class 3 -- weaker rule: no dominance analysis is applied "
                "to this reporting-only mapping, by design, because D-06 "
                "keys the read/refuse decision only on readability)"
            )
    return violations


def _find_ambiguous_citations_violations(tree: ast.Module, filename: str) -> list[str]:
    """Class 4: `AMBIGUOUS_DOC_CITATIONS` bound exactly once at module level
    to a non-empty `ast.Dict` literal."""
    violations: list[str] = []
    bindings = _module_level_bindings(tree, _AMBIGUOUS_DICT_NAME)
    if len(bindings) != 1:
        violations.append(
            f"{filename}: {_AMBIGUOUS_DICT_NAME} bound {len(bindings)} "
            "time(s) at module level (expected exactly 1) -- Class 4"
        )
        return violations
    (binding,) = bindings
    value = binding.value if isinstance(binding, (ast.Assign, ast.AnnAssign)) else None
    if not isinstance(value, ast.Dict) or len(value.keys) == 0:
        violations.append(
            f"{filename}:{binding.lineno}: {_AMBIGUOUS_DICT_NAME} is empty "
            "or not a Dict literal (Class 4) -- an empty record would mean "
            "the C-17 documentation disagreement had been silently "
            "resolved away rather than recorded"
        )
    return violations


def _print_bucket(label: str, violations: list[str]) -> None:
    print(f"FAIL: {len(violations)} {label}:")
    for v in violations[:20]:
        print(f"  {v}")
    if len(violations) > 20:
        print(f"  ... and {len(violations) - 20} more")


def main() -> int:
    """Entry point: resolve `FIRESTARTER_PROTECTION_READABILITY_SRC` and
    scan it.

    Prints a PASS: line (naming the resolved target path and both gated
    binding counts) and returns 0 when the scanned source contains zero
    Class 1, zero Class 2, zero Class 3 and zero Class 4 violations, and
    both `_TOKEN_SET_NAMES` members are each bound exactly once. Prints a
    FAIL: summary per class and returns 1 on any violation. Prints an
    ERROR: message to stderr and returns 1 (fail-closed) if the resolved
    target does not exist, or does not parse as Python.
    """
    path = FIRESTARTER_PROTECTION_READABILITY_SRC
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

    class1_violations = _find_permit_by_default_violations(tree, path)
    class1_violations.extend(_find_bare_except_violations(tree, path))
    class2_violations, counts = _find_widenable_tokenset_violations(tree, path)
    class3_violations = _find_reporting_dict_violations(tree, path)
    class4_violations = _find_ambiguous_citations_violations(tree, path)

    if class1_violations or class2_violations or class3_violations or class4_violations:
        if class1_violations:
            _print_bucket("Class 1 (permit-by-default) violation(s)", class1_violations)
        if class2_violations:
            _print_bucket(
                "Class 2 (widenable-token-set) violation(s)", class2_violations
            )
        if class3_violations:
            _print_bucket("Class 3 (reporting-mapping) violation(s)", class3_violations)
        if class4_violations:
            _print_bucket("Class 4 (ambiguity-record) violation(s)", class4_violations)
        return 1

    counts_str = ", ".join(f"{name}={counts[name]}" for name in _TOKEN_SET_NAMES)
    print(
        f"PASS: scanned {os.path.relpath(path, _HERE)}; "
        "0 Class 1, 0 Class 2, 0 Class 3, 0 Class 4 violations; "
        f"bound exactly once each: {counts_str}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
