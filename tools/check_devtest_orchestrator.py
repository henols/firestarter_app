"""
AST-based orchestrator-only gate for `dev test` (SAFE-03, Phase 109 D-02/D-03).

Scans `firestarter/chip_test.py` (the Phase-108 test-plan engine),
`firestarter/cli_handlers.py` (the `dev_test` handler, scoped), and
`firestarter/submit.py` (the Phase-113 submission-flow module, in full) and
DENIES four violation classes that would break `dev test`'s
orchestrator-only contract:

  1. VPP-set call sites -- any call whose callee name/attribute sets or
     enables VPP (e.g. `set_vpp`, `enable_vpp`, `write_vpp`, `vpp_enable`, or
     any assignment target containing "vpp" alongside "set"/"enable"/"write").
     `dev test` must never touch the VPP boost regulator directly -- every
     write/erase/verify call routes through the existing `EpromOperator`
     methods (`write_eprom`, `erase_eprom`, `verify_eprom`, ...), which own
     VPP internally.
  2. Raw command-dict / wire-JSON construction -- a dict literal whose string
     keys intersect the wire-protocol key vocabulary (`cmd`, `algorithm`,
     `vpp_mv`, `bus-config`, `pin-count`, `chip-id`, `flags`, `pulse-delay`;
     see `firestarter_app/CLAUDE.md`'s wire-command example). `dev test` must
     never hand-assemble a firmware command -- it goes through
     `chip_resolver.resolve_chip` + `database.convert_to_programmer` only.
  3. `force=True` keyword pass-through, or any `"--force"` string literal.
     `dev test` never forces past a firmware/host refusal.
  4. Broad exception handlers (Phase 133 D-09/D-14) -- a bare `except:`, an
     `except Exception:`, an `except BaseException:`, or a tuple containing
     either. Bare `except:` is already caught by ruff's E722, but
     `except Exception:`/`except BaseException:` are caught by NOTHING in
     this repo today -- `BLE` is not in this project's ruff `select`
     (`["E", "F", "I", "UP"]`), so the `# noqa: BLE001` already sitting on
     `chip_test.py`'s `_sample` sampler handler is INERT. This bucket carries
     one narrow, reasoned exemption for that pre-existing handler
     (`_BROAD_EXCEPT_EXEMPTIONS`, D-14) -- a best-effort diagnostic hook
     invoked with an opaque caller-supplied callable, never a blanket
     allowance for the broad form.

The checker also asserts the firmware is untouched: this is a host-only
Python checker, so "zero new firmware dispatch entries" is satisfied by
construction (only `firestarter_app`-internal target paths are ever in
scope) -- that assertion is verified explicitly in `main()` rather than left
implicit.

This is a genuinely-populated AST walk (`ast.parse` + a fresh
`ast.NodeVisitor`), NOT a hollow declared-empty detector -- the exact
tech-debt fate this project incurred with v1.12's GATE-03 (a checker that
could never fail because it asserted nothing concrete). The paired pytest
(`tests/test_check_devtest_orchestrator.py`) proves this checker actually
flips to non-zero on a planted violation, injected via the
`FIRESTARTER_DEVTEST_SRC` / `FIRESTARTER_DEVTEST_HANDLER` env-overrides below
(mirrors `tools/check_dispatch.py`'s `FIRESTARTER_DB_FILE` seam) -- D-03's
anti-hollow contract.

Handler scan (Phase 112): the `@dev.command("test")` CLI handler landed as a
function inside `firestarter/cli_handlers.py` (a sibling of
`dev_validate_family`), not a standalone module of its own.
`FIRESTARTER_DEVTEST_HANDLER` (default: `_DEFAULT_DEVTEST_HANDLER`) points at
the real `cli_handlers.py` file and this checker actually scans it -- but
`cli_handlers.py` is a large, pre-existing multi-command module with 10
pre-existing, legitimate `-f`/`--force` flags on UNRELATED commands (`read`,
`write`, `verify`, `blank`, `erase`, `id`) that long predate Phase 112 and
are outside this handler's contract entirely. Scanning the WHOLE file would
make the gate permanently red on code this phase never touched -- a false
positive, not a real violation. Instead, `_scan_target_functions` narrows
the handler scan to `dev_test` plus its private co-located helpers
(`_verdict_code`, `_sanitize_chip_token`, `_is_uv_eprom`, `_chip_id_fields`,
`_is_interactive`, `_make_sampler` -- `_HANDLER_FUNCTION_NAMES` below), i.e.
exactly the new Phase-112 code, via an AST `FunctionDef`/`AsyncFunctionDef`
name filter over the parsed module -- never a brittle line-number range. The
`chip_test.py` leg is unaffected and still scans the ENTIRE file (it has, by
construction, zero pre-existing `--force` usage). A handler that exists on
disk but is silently skipped by this checker is the v1.12 hollow-GATE-03
failure mode (Phase 109 D-02/D-03) -- the missing-file tolerance in
`_scan_file` stays only for a nonexistent test-fixture path, never for the
real handler target, and `_scan_target_functions` still fails closed if
`dev_test` itself is ever renamed/removed without updating this checker.

Exit codes:
  0 -- the scanned source(s) contain zero VPP-set call sites, zero raw
       command-dict / wire-JSON literals, zero force=True / "--force"
       pass-throughs, and zero non-exempt broad exception handlers (PASS:
       line printed).
  1 -- at least one deny-list violation was found, the broad-except
       exemption table failed its own guards (empty reason / stale row), or
       nothing was scanned (FAIL: per-bucket summary printed, per-bucket
       capped at the first 20 entries).
"""

import ast
import os
import sys

# Module-top path constants (mirrors tools/check_dispatch.py:24-33's
# env-overridable path-constant idiom).
_HERE = os.path.dirname(__file__)
_DEFAULT_CHIP_TEST = os.path.join(_HERE, "..", "firestarter", "chip_test.py")

# Env-override seam (mirrors check_dispatch.py's FIRESTARTER_DB_FILE): lets
# the paired pytest point this checker at a deliberately-violating fixture
# file without editing the real, clean chip_test.py source (D-03).
FIRESTARTER_DEVTEST_SRC = os.environ.get("FIRESTARTER_DEVTEST_SRC", _DEFAULT_CHIP_TEST)

# The Phase-112 `@dev.command("test")` CLI handler -- lands as a function
# inside cli_handlers.py (sibling of dev_validate_family), not a standalone
# module. Scanning the whole file is intentional (see module docstring): the
# deny buckets must find ZERO hits anywhere in the host CLI.
_DEFAULT_DEVTEST_HANDLER = os.path.join(_HERE, "..", "firestarter", "cli_handlers.py")

# Env-override seam (mirrors FIRESTARTER_DEVTEST_SRC above): lets the paired
# pytest point this checker at a deliberately-violating handler-shaped
# fixture file without editing the real, clean cli_handlers.py (anti-hollow
# proof for the handler leg specifically).
FIRESTARTER_DEVTEST_HANDLER = os.environ.get(
    "FIRESTARTER_DEVTEST_HANDLER", _DEFAULT_DEVTEST_HANDLER
)

# The Phase-113 `submit.py` submission-flow module -- a fresh orchestrator
# module (like chip_test.py) with zero pre-existing force/VPP/wire-dict
# usage, so it is scanned IN FULL via _scan_file (not the scoped
# _scan_target_functions path reserved for the large pre-existing
# cli_handlers.py).
_DEFAULT_DEVTEST_SUBMIT = os.path.join(_HERE, "..", "firestarter", "submit.py")

# Env-override seam (mirrors FIRESTARTER_DEVTEST_SRC/HANDLER above): lets the
# paired pytest point this checker at a deliberately-violating submit-shaped
# fixture file without editing the real, clean submit.py (anti-hollow proof
# for the submit.py leg specifically).
FIRESTARTER_DEVTEST_SUBMIT = os.environ.get(
    "FIRESTARTER_DEVTEST_SUBMIT", _DEFAULT_DEVTEST_SUBMIT
)

# The `dev test` handler function plus its private, co-located helpers
# (cli_handlers.py:1659-1921) -- the exact new Phase-112 surface. Scanning
# ONLY these function bodies (rather than the whole cli_handlers.py module)
# avoids false-positive FAILs on the 10 pre-existing, legitimate `--force`
# flags belonging to unrelated commands (`read`/`write`/`verify`/`blank`/
# `erase`/`id`) that predate this phase. A handler-shaped test fixture
# (test_check_devtest_orchestrator.py) defines its top-level functions with
# these SAME names so the same name-filtered scan path exercises the
# anti-hollow proof.
#
# RESEARCH C-4 (Phase 121 Plan 09) proved this allow-list is not merely
# documentation: a violating helper placed in a function NOT named here
# passes this gate with `PASS ... EXIT=0` while the IDENTICAL violation
# placed inside `dev_test` itself trips `EXIT=1`. `_is_uv_eprom` sat in this
# set since Phase 112 pointing at nothing (a leftover speculative name) --
# Plan 121-09 landed the real handler-side UV predicate under that exact
# name, and added `_resolve_write_scope` alongside it. Every future helper
# added to the `dev test` surface MUST be listed here, or this gate silently
# under-covers exactly that new code -- `tests/test_check_devtest_orchestrator
# .py::test_handler_function_names_all_resolve_to_real_callables` makes this
# a permanently-enforced invariant rather than a one-off fix.
_HANDLER_FUNCTION_NAMES = frozenset(
    {
        "dev_test",
        "_verdict_code",
        "_overall_exit_code",
        "_dev_test_exit_code",
        "_sanitize_chip_token",
        "_is_uv_eprom",
        "_resolve_write_scope",
        "_chip_id_fields",
        "_is_interactive",
        "_make_sampler",
    }
)

# ---------------------------------------------------------------------------
# Deny-list vocabularies (D-02)
# ---------------------------------------------------------------------------

# VPP-set call sites: attribute/function names that set or enable VPP. There
# are ZERO such call sites in chip_test.py today (it composes only the
# existing EpromOperator methods) -- that is what makes the gate green.
_VPP_SET_NAMES = frozenset(
    {
        "set_vpp",
        "enable_vpp",
        "write_vpp",
        "vpp_enable",
        "set_voltage",
        "assert_vpp",
        "raise_vpp",
    }
)

# Raw command-dict / wire-JSON keys (see firestarter_app/CLAUDE.md's wire
# command example). A dict literal carrying >=2 of these string keys is
# treated as a raw wire dict under construction -- one incidental key
# (e.g. a coincidental "flags" key in an unrelated dict) is not enough to
# flag, but a wire-shaped dict always carries several of these together.
_WIRE_DICT_KEYS = frozenset(
    {
        "cmd",
        "algorithm",
        "vpp_mv",
        "bus-config",
        "pin-count",
        "chip-id",
        "flags",
        "pulse-delay",
        "memory-size",
    }
)
_WIRE_DICT_KEY_THRESHOLD = 2

# force=True / "--force" pass-through.
_FORCE_KEYWORD_NAME = "force"
_FORCE_CLI_FLAG = "--force"

# ---------------------------------------------------------------------------
# Broad-except deny bucket (Phase 133 D-09/D-14)
# ---------------------------------------------------------------------------
#
# Bare `except:` is already caught by ruff's E722 (this repo's `select` is
# ["E", "F", "I", "UP"]). `except Exception:` and `except BaseException:` are
# caught by NOTHING today -- `BLE` (flake8-blind-except) is not selected, so
# the inert BLE001-suppression comment already sitting on chip_test.py's
# `_sample` sampler handler suppresses a rule that never fires. This
# frozenset names the two identifiers that constitute "broad" for the
# single-class and tuple forms; the bare form (`node.type is None`) is
# classified separately in `visit_ExceptHandler` since it has no name to
# check.
_BROAD_EXCEPT_NAMES = frozenset({"Exception", "BaseException"})

# (file basename, enclosing function name) -> non-empty reason (D-14).
#
# Follows the house frozenset/name-map-with-rationale idiom
# (`_HANDLER_FUNCTION_NAMES` above is the in-file precedent;
# `_EXEMPT_FW_TO_HOST` in tests/test_revision_constants_parity.py is the
# second -- a frozen, deliberately-NOT-auto-derived name-PAIR map, never a
# skip-set). Matching is scoped to (basename, function) -- see
# `_OrchestratorDenyVisitor._is_exempt` -- so a planted fixture reproducing
# this shape under any OTHER filename or function name gets NO exemption and
# is flagged as a genuine violation, and the stale-row guard below
# (`_stale_exemption_row_violations`) fires only when a scanned file of this
# exact basename no longer defines this exact function.
#
# Exactly one row at this commit: `chip_test.py`'s `_sample` is a
# best-effort diagnostic hook invoked with an OPAQUE caller-supplied
# callable (the sampler) that may raise literally anything -- its
# swallow-all behaviour is its documented contract (see `_sample`'s own
# docstring), and `_make_sampler` (firestarter/cli_handlers.py) is live in
# production. Narrowing the handler instead of exempting it would change
# shipped production behaviour, which criterion 4 of Phase 133 forbids.
_BROAD_EXCEPT_EXEMPTIONS: dict[tuple[str, str], str] = {
    (os.path.basename(_DEFAULT_CHIP_TEST), "_sample"): (
        "D-14: _sample (firestarter/chip_test.py) is a best-effort "
        "diagnostic hook invoked with an opaque caller-supplied callable "
        "(the sampler) that may raise literally anything; its swallow-all "
        "behaviour is its documented contract, and narrowing it would "
        "change shipped production behaviour reachable through "
        "_make_sampler in cli_handlers.py, which criterion 4 forbids."
    ),
}


def _validate_exemption_table(table: dict[tuple[str, str], str]) -> list[str]:
    """Guard (a): every exemption row must carry a non-empty, non-whitespace
    reason. Returns a list of problem strings (empty when the table is
    clean).

    Deliberately PURE and argument-taking -- it reads no module global --
    so this guard can be proven entirely in-process, without a subprocess or
    an env-override seam: the env seams in this module exist only to
    retarget the AST *scan*, and this function does not scan anything, it
    only inspects the table it is handed.
    """
    problems: list[str] = []
    for (file, function), reason in table.items():
        if reason is None or not reason.strip():
            problems.append(
                f"broad-except exemption row ({file!r}, {function!r}) has an "
                "empty or missing reason -- an exemption without a reason is "
                "an unreasoned hole in the gate"
            )
    return problems


def _stale_exemption_row_violations(
    exemptions: dict[tuple[str, str], str], scanned_paths: list[str]
) -> list[str]:
    """Guard (b): fail when a scanned file's basename matches an exemption
    row's file but no longer defines a function of that row's name anywhere
    in the module.

    A rotted exemption -- one whose named function was renamed or removed --
    would otherwise silently keep permitting an omission (the hole stays
    open even though the thing it was cut for is gone). This is the same
    inversion as this file's own
    `test_handler_function_names_all_resolve_to_real_callables` precedent in
    the paired test module.

    A row whose file has no same-basename counterpart among `scanned_paths`
    in THIS run is not evaluated -- e.g. a test pointing
    `FIRESTARTER_DEVTEST_SRC` at a differently-named fixture never matches
    the `chip_test.py` row at all, so it cannot be stale by this check
    (it is instead a genuine broad-except violation if it plants the form,
    proven by the planted-RED legs). Wired here (over the actually-scanned
    paths), not inside `_validate_exemption_table`, because it needs the
    parsed source of the scanned files, not just the table.
    """
    violations: list[str] = []
    for (row_file, row_function), _reason in exemptions.items():
        matching = [p for p in scanned_paths if os.path.basename(p) == row_file]
        if not matching:
            continue
        found = False
        for path in matching:
            with open(path, encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=path)
            if any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == row_function
                for node in ast.walk(tree)
            ):
                found = True
                break
        if not found:
            violations.append(
                f"STALE exemption row ({row_file!r}, {row_function!r}): a "
                f"scanned file named {row_file!r} no longer defines a "
                f"function named {row_function!r} -- the exemption has "
                "rotted and is silently permitting an omission"
            )
    return violations


class _OrchestratorDenyVisitor(ast.NodeVisitor):
    """Walk a chip_test.py-shaped AST, collecting SAFE-03 deny-list hits.

    Populates four violation buckets during a single tree walk:
      - `vpp_set_violations`: `ast.Call` sites whose callee name/attribute is
        in `_VPP_SET_NAMES`.
      - `raw_wire_dict_violations`: `ast.Dict` literals whose string keys
        intersect `_WIRE_DICT_KEYS` at or above `_WIRE_DICT_KEY_THRESHOLD`.
      - `force_violations`: `ast.keyword(arg="force")` with a truthy
        constant value, or any string literal exactly equal to "--force".
      - `broad_except_violations`: a bare `except:`, an `except Exception:`,
        an `except BaseException:`, or a tuple containing either -- unless
        the innermost enclosing function is exempted by
        `_BROAD_EXCEPT_EXEMPTIONS` (D-14).

    Each violation is recorded as a human-readable `"line N: ..."` string so
    `main()` can print an actionable per-bucket FAIL: summary.
    """

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.vpp_set_violations: list[str] = []
        self.raw_wire_dict_violations: list[str] = []
        self.force_violations: list[str] = []
        self.broad_except_violations: list[str] = []
        # Enclosing-function-name stack (D-09) -- exists ONLY so
        # `visit_ExceptHandler` can consult a (file, function) exemption.
        # Empty when the current node sits at module level.
        self._function_stack: list[str] = []

    def _callee_name(self, func: ast.expr) -> str | None:
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
        return None

    def visit_Call(self, node: ast.Call) -> None:
        callee = self._callee_name(node.func)
        if callee is not None and callee in _VPP_SET_NAMES:
            self.vpp_set_violations.append(
                f"{self.filename}:{node.lineno}: VPP-set call `{callee}(...)`"
            )
        for kw in node.keywords:
            if kw.arg == _FORCE_KEYWORD_NAME and self._is_truthy_constant(kw.value):
                self.force_violations.append(
                    f"{self.filename}:{node.lineno}: force=True keyword pass-through"
                )
        self.generic_visit(node)

    def _is_truthy_constant(self, value: ast.expr) -> bool:
        return isinstance(value, ast.Constant) and bool(value.value)

    def visit_Dict(self, node: ast.Dict) -> None:
        keys: list[str] = []
        for k in node.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys.append(k.value)
        hit_count = len(_WIRE_DICT_KEYS.intersection(keys))
        if hit_count >= _WIRE_DICT_KEY_THRESHOLD:
            matched = sorted(_WIRE_DICT_KEYS.intersection(keys))
            self.raw_wire_dict_violations.append(
                f"{self.filename}:{node.lineno}: raw wire-dict literal "
                f"(keys matched: {matched})"
            )
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and node.value == _FORCE_CLI_FLAG:
            self.force_violations.append(
                f'{self.filename}:{node.lineno}: literal "--force" string'
            )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def _classify_broad_except(self, type_node: ast.expr | None) -> str | None:
        """Return a human-readable label for a broad handler `type_node`, or
        None when the handler is not broad.

        `node.type is None` -- a bare `except:` -- is classified by the
        caller directly (there is nothing to inspect here); this method only
        handles the single-class (`ast.Name`) and tuple (`ast.Tuple`) forms.
        """
        if isinstance(type_node, ast.Name) and type_node.id in _BROAD_EXCEPT_NAMES:
            return f"except {type_node.id}:"
        if isinstance(type_node, ast.Tuple):
            matched = [
                elt.id
                for elt in type_node.elts
                if isinstance(elt, ast.Name) and elt.id in _BROAD_EXCEPT_NAMES
            ]
            if matched:
                return f"except (...) tuple containing {', '.join(matched)}"
        return None

    def _is_exempt(self, enclosing_function: str | None) -> bool:
        if enclosing_function is None:
            return False
        basename = os.path.basename(self.filename)
        return (basename, enclosing_function) in _BROAD_EXCEPT_EXEMPTIONS

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            label = "bare except:"
        else:
            label = self._classify_broad_except(node.type)
        if label is not None:
            enclosing = self._function_stack[-1] if self._function_stack else None
            if not self._is_exempt(enclosing):
                self.broad_except_violations.append(
                    f"{self.filename}:{node.lineno}: broad exception handler ({label})"
                )
        self.generic_visit(node)


def _scan_file(path: str) -> _OrchestratorDenyVisitor | None:
    """Parse and walk `path`; return None if the file does not exist.

    Missing-file tolerance now exists ONLY for a pytest tmp_path fixture path
    injected via the env-override seams above -- both real targets
    (`chip_test.py`, `cli_handlers.py`) resolve to real files on disk in
    production, so the `scanned`-empty fail-closed guard in `main()` is what
    actually protects against a hollow scan.
    """
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=path)
    visitor = _OrchestratorDenyVisitor(path)
    visitor.visit(tree)
    return visitor


def _scan_target_functions(
    path: str, function_names: frozenset[str]
) -> _OrchestratorDenyVisitor | None:
    """Parse `path` but walk ONLY the named top-level function bodies.

    Returns `None` if the file does not exist (same missing-file tolerance
    as `_scan_file`) OR if the module parses but contains NONE of
    `function_names` -- the latter is deliberately treated as "nothing to
    scan" rather than a silent empty-pass, so `main()`'s scanned-empty
    fail-closed guard still fires if `dev_test` (or its helpers) is ever
    renamed/removed here without updating `_HANDLER_FUNCTION_NAMES` (D-02/D-03
    anti-hollow: a scoped scan that quietly matches zero functions is exactly
    as hollow as skipping the file outright).

    Used for the `cli_handlers.py` handler leg specifically: that module is a
    large, pre-existing multi-command file with legitimate `--force` flags on
    UNRELATED commands, so a whole-file scan would false-positive on code
    this phase never touched. `chip_test.py` is unaffected -- it is still
    scanned in full via `_scan_file` (zero pre-existing `--force` usage
    there, by construction).
    """
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=path)
    matched_any = False
    visitor = _OrchestratorDenyVisitor(path)
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in function_names
        ):
            matched_any = True
            visitor.visit(node)
    if not matched_any:
        return None
    return visitor


def _assert_host_only(path: str) -> str | None:
    """Assert `path` does not resolve into the firmware sub-repo (D-02).

    Returns an error string if the resolved path falls inside the sibling
    `firestarter/` firmware submodule (a peer of `firestarter_app/` in the
    meta-repo layout -- see /workspaces/CLAUDE.md), else None. This is the
    explicit "firmware is untouched" assertion: `dev test` is host-only
    Python, so no firmware repo file is ever a legitimate scan target.

    Deliberately permissive otherwise (e.g. a pytest tmp_path fixture used to
    inject a negative test via FIRESTARTER_DEVTEST_SRC is NOT a firmware path
    and must not be rejected here) -- this assertion targets the one concrete
    risk (a firmware-repo path sneaking into scope), not "path must live
    under firestarter_app".
    """
    meta_root = os.path.abspath(os.path.join(_HERE, "..", ".."))
    firmware_root = os.path.join(meta_root, "firestarter")
    resolved = os.path.abspath(path)
    if resolved == firmware_root or resolved.startswith(firmware_root + os.sep):
        return f"target path {resolved} resolves INTO the firmware sub-repo ({firmware_root})"
    return None


def _print_bucket(label: str, violations: list[str]) -> None:
    print(f"FAIL: {len(violations)} {label}:")
    for v in violations[:20]:
        print(f"  {v}")
    if len(violations) > 20:
        print(f"  ... and {len(violations) - 20} more")


def main() -> None:
    """Entry point: scan the orchestrator source(s), exit non-zero on any hit.

    Scans `FIRESTARTER_DEVTEST_SRC` (default: `firestarter/chip_test.py`) IN
    FULL, `FIRESTARTER_DEVTEST_HANDLER` (default: the real `firestarter/
    cli_handlers.py`, which now houses the landed `dev test` handler) SCOPED
    to just the `dev_test` function and its private co-located helpers
    (`_HANDLER_FUNCTION_NAMES`) -- `cli_handlers.py` is a large multi-command
    module with pre-existing, legitimate `--force` flags on unrelated
    commands that a whole-file scan would false-positive on -- and
    `FIRESTARTER_DEVTEST_SUBMIT` (default: the real `firestarter/submit.py`,
    the Phase-113 submission-flow module) IN FULL, like `chip_test.py` (a
    fresh module with zero pre-existing force/VPP/wire-dict usage). Collects
    VPP-set / raw-wire-dict / force / broad-except violations across all
    three scan results into four buckets; any non-empty bucket fails the
    build. All three targets resolve to real files in production -- the
    `scanned`-empty fail-closed guard below still fires if some future
    refactor moves any of them, or renames/removes `dev_test`, without
    updating this checker.

    Guard (a) runs FIRST, before any scanning: an exemption table with an
    empty or missing reason fails the build immediately (Phase 133 D-14).
    Guard (b) -- the stale-row check -- runs AFTER scanning, over the files
    actually found, because it needs their parsed source.
    """
    exemption_table_problems = _validate_exemption_table(_BROAD_EXCEPT_EXEMPTIONS)
    if exemption_table_problems:
        _print_bucket(
            "broad-except exemption table problem(s)", exemption_table_problems
        )
        sys.exit(1)

    targets = [
        FIRESTARTER_DEVTEST_SRC,
        FIRESTARTER_DEVTEST_HANDLER,
        FIRESTARTER_DEVTEST_SUBMIT,
    ]

    host_only_errors: list[str] = []
    for t in targets:
        err = _assert_host_only(t)
        if err:
            host_only_errors.append(err)

    vpp_set_violations: list[str] = []
    raw_wire_dict_violations: list[str] = []
    force_violations: list[str] = []
    broad_except_violations: list[str] = []
    scanned: list[str] = []

    full_scan_visitor = _scan_file(FIRESTARTER_DEVTEST_SRC)
    if full_scan_visitor is not None:
        scanned.append(FIRESTARTER_DEVTEST_SRC)
        vpp_set_violations.extend(full_scan_visitor.vpp_set_violations)
        raw_wire_dict_violations.extend(full_scan_visitor.raw_wire_dict_violations)
        force_violations.extend(full_scan_visitor.force_violations)
        broad_except_violations.extend(full_scan_visitor.broad_except_violations)

    handler_visitor = _scan_target_functions(
        FIRESTARTER_DEVTEST_HANDLER, _HANDLER_FUNCTION_NAMES
    )
    if handler_visitor is not None:
        scanned.append(FIRESTARTER_DEVTEST_HANDLER)
        vpp_set_violations.extend(handler_visitor.vpp_set_violations)
        raw_wire_dict_violations.extend(handler_visitor.raw_wire_dict_violations)
        force_violations.extend(handler_visitor.force_violations)
        broad_except_violations.extend(handler_visitor.broad_except_violations)

    submit_visitor = _scan_file(FIRESTARTER_DEVTEST_SUBMIT)
    if submit_visitor is not None:
        scanned.append(FIRESTARTER_DEVTEST_SUBMIT)
        vpp_set_violations.extend(submit_visitor.vpp_set_violations)
        raw_wire_dict_violations.extend(submit_visitor.raw_wire_dict_violations)
        force_violations.extend(submit_visitor.force_violations)
        broad_except_violations.extend(submit_visitor.broad_except_violations)

    if not scanned:
        print(
            "FAIL: no orchestrator source files found to scan "
            f"(checked: {targets}) -- the gate cannot vacuously pass with "
            "nothing scanned"
        )
        sys.exit(1)

    stale_exemption_violations = _stale_exemption_row_violations(
        _BROAD_EXCEPT_EXEMPTIONS, scanned
    )

    if (
        host_only_errors
        or vpp_set_violations
        or raw_wire_dict_violations
        or force_violations
        or broad_except_violations
        or stale_exemption_violations
    ):
        if host_only_errors:
            _print_bucket("host-only framing violation(s)", host_only_errors)
        if vpp_set_violations:
            _print_bucket("VPP-set call site(s)", vpp_set_violations)
        if raw_wire_dict_violations:
            _print_bucket(
                "raw command-dict / wire-JSON construction site(s)",
                raw_wire_dict_violations,
            )
        if force_violations:
            _print_bucket("force=True / --force pass-through site(s)", force_violations)
        if broad_except_violations:
            _print_bucket("broad exception handler(s)", broad_except_violations)
        if stale_exemption_violations:
            _print_bucket(
                "stale broad-except exemption row(s)", stale_exemption_violations
            )
        sys.exit(1)

    print(
        f"PASS: scanned {', '.join(os.path.relpath(s, _HERE) for s in scanned)}; "
        "0 VPP-set, 0 raw-wire-dict, 0 --force, 0 broad-except; "
        "firmware untouched (host-only, asserted)"
    )


if __name__ == "__main__":
    main()
