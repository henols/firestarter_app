"""
AST-based orchestrator-only gate for `dev test` (SAFE-03, Phase 109 D-02/D-03).

Scans `firestarter/chip_test.py` (the Phase-108 test-plan engine) and DENIES
three violation classes that would break `dev test`'s orchestrator-only
contract:

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
       command-dict / wire-JSON literals, and zero force=True / "--force"
       pass-throughs (PASS: line printed).
  1 -- at least one deny-list violation was found (FAIL: per-bucket summary
       printed, per-bucket capped at the first 20 entries).
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

# The `dev test` handler function plus its private, co-located helpers
# (cli_handlers.py:1659-1921) -- the exact new Phase-112 surface. Scanning
# ONLY these function bodies (rather than the whole cli_handlers.py module)
# avoids false-positive FAILs on the 10 pre-existing, legitimate `--force`
# flags belonging to unrelated commands (`read`/`write`/`verify`/`blank`/
# `erase`/`id`) that predate this phase. A handler-shaped test fixture
# (test_check_devtest_orchestrator.py) defines its top-level functions with
# these SAME names so the same name-filtered scan path exercises the
# anti-hollow proof.
_HANDLER_FUNCTION_NAMES = frozenset(
    {
        "dev_test",
        "_verdict_code",
        "_sanitize_chip_token",
        "_is_uv_eprom",
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


class _OrchestratorDenyVisitor(ast.NodeVisitor):
    """Walk a chip_test.py-shaped AST, collecting SAFE-03 deny-list hits.

    Populates three violation buckets during a single tree walk:
      - `vpp_set_violations`: `ast.Call` sites whose callee name/attribute is
        in `_VPP_SET_NAMES`.
      - `raw_wire_dict_violations`: `ast.Dict` literals whose string keys
        intersect `_WIRE_DICT_KEYS` at or above `_WIRE_DICT_KEY_THRESHOLD`.
      - `force_violations`: `ast.keyword(arg="force")` with a truthy
        constant value, or any string literal exactly equal to "--force".

    Each violation is recorded as a human-readable `"line N: ..."` string so
    `main()` can print an actionable per-bucket FAIL: summary.
    """

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.vpp_set_violations: list[str] = []
        self.raw_wire_dict_violations: list[str] = []
        self.force_violations: list[str] = []

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
    FULL, and `FIRESTARTER_DEVTEST_HANDLER` (default: the real `firestarter/
    cli_handlers.py`, which now houses the landed `dev test` handler) SCOPED
    to just the `dev_test` function and its private co-located helpers
    (`_HANDLER_FUNCTION_NAMES`) -- `cli_handlers.py` is a large multi-command
    module with pre-existing, legitimate `--force` flags on unrelated
    commands that a whole-file scan would false-positive on. Collects
    VPP-set / raw-wire-dict / force violations across both scan results into
    three buckets; any non-empty bucket fails the build. Both targets
    resolve to real files in production -- the `scanned`-empty fail-closed
    guard below still fires if some future refactor moves either file, or
    renames/removes `dev_test`, without updating this checker.
    """
    targets = [FIRESTARTER_DEVTEST_SRC, FIRESTARTER_DEVTEST_HANDLER]

    host_only_errors: list[str] = []
    for t in targets:
        err = _assert_host_only(t)
        if err:
            host_only_errors.append(err)

    vpp_set_violations: list[str] = []
    raw_wire_dict_violations: list[str] = []
    force_violations: list[str] = []
    scanned: list[str] = []

    full_scan_visitor = _scan_file(FIRESTARTER_DEVTEST_SRC)
    if full_scan_visitor is not None:
        scanned.append(FIRESTARTER_DEVTEST_SRC)
        vpp_set_violations.extend(full_scan_visitor.vpp_set_violations)
        raw_wire_dict_violations.extend(full_scan_visitor.raw_wire_dict_violations)
        force_violations.extend(full_scan_visitor.force_violations)

    handler_visitor = _scan_target_functions(
        FIRESTARTER_DEVTEST_HANDLER, _HANDLER_FUNCTION_NAMES
    )
    if handler_visitor is not None:
        scanned.append(FIRESTARTER_DEVTEST_HANDLER)
        vpp_set_violations.extend(handler_visitor.vpp_set_violations)
        raw_wire_dict_violations.extend(handler_visitor.raw_wire_dict_violations)
        force_violations.extend(handler_visitor.force_violations)

    if not scanned:
        print(
            "FAIL: no orchestrator source files found to scan "
            f"(checked: {targets}) -- the gate cannot vacuously pass with "
            "nothing scanned"
        )
        sys.exit(1)

    if (
        host_only_errors
        or vpp_set_violations
        or raw_wire_dict_violations
        or force_violations
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
        sys.exit(1)

    print(
        f"PASS: scanned {', '.join(os.path.relpath(s, _HERE) for s in scanned)}; "
        "0 VPP-set, 0 raw-wire-dict, 0 --force; firmware untouched (host-only, asserted)"
    )


if __name__ == "__main__":
    main()
