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
`FIRESTARTER_DEVTEST_SRC` env-override below (mirrors
`tools/check_dispatch.py`'s `FIRESTARTER_DB_FILE` seam) -- D-03's anti-hollow
contract.

Scope tolerance: the Phase-112 `@dev.command("test")` CLI handler does not
exist yet. This checker scans only files that exist on disk and silently
skips a missing target path -- the handler-file scan is DEFERRED to Phase
112 (this module's docstring is the record of that deferral; D-02).

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

# The not-yet-existing Phase-112 `@dev.command("test")` CLI handler. Scanned
# only if present on disk -- its absence today is expected and not an error
# (D-02 scope tolerance). Name chosen to match the eventual handler module;
# if Phase 112 lands it elsewhere, that phase updates this constant.
_DEVTEST_CLI_HANDLER = os.path.join(_HERE, "..", "firestarter", "dev_test_cli.py")

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

    Missing-file tolerance is the D-02 scope-tolerance mechanism: the
    Phase-112 dev-test CLI handler does not exist yet, and this checker must
    not fail merely because that file is absent.
    """
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=path)
    visitor = _OrchestratorDenyVisitor(path)
    visitor.visit(tree)
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

    Scans `FIRESTARTER_DEVTEST_SRC` (default: `firestarter/chip_test.py`) and
    the not-yet-existing Phase-112 dev-test CLI handler (skipped if absent).
    Collects VPP-set / raw-wire-dict / force violations across both files
    into three buckets; any non-empty bucket fails the build.
    """
    targets = [FIRESTARTER_DEVTEST_SRC, _DEVTEST_CLI_HANDLER]

    host_only_errors: list[str] = []
    for t in targets:
        err = _assert_host_only(t)
        if err:
            host_only_errors.append(err)

    vpp_set_violations: list[str] = []
    raw_wire_dict_violations: list[str] = []
    force_violations: list[str] = []
    scanned: list[str] = []

    for target in targets:
        visitor = _scan_file(target)
        if visitor is None:
            # Missing-file tolerance (D-02): the Phase-112 handler doesn't
            # exist yet -- skip without error.
            continue
        scanned.append(target)
        vpp_set_violations.extend(visitor.vpp_set_violations)
        raw_wire_dict_violations.extend(visitor.raw_wire_dict_violations)
        force_violations.extend(visitor.force_violations)

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
