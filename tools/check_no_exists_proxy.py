#!/usr/bin/env python3
"""D-09's recurrence lint: forbids the module-level absence-proxy idiom from
reappearing in this host test suite (Phase 123 Plan 09).

**What this forbids, stated precisely.** A module-level assignment whose
value is a boolean negation (`not ...`) of a path-existence call, or of a
boolean combination of such calls -- the exact shape that made a firmware
**rename** look like a firmware **absence** (RESEARCH's A-7 finding, the
defect Phase 123 Plans 07/08 removed from all seven proxy-carrying modules).
Uses the `ast` module, never a regex: an AST walk over module-level
assignments looking for a unary `not` whose operand contains a call to an
`exists` attribute anywhere in its subtree is precise, whereas a regex over
source text would both miss reformatted variants and fire inside comments or
docstrings that merely *mention* the idiom (as this very docstring does).
Both shapes are caught:

  - the simple shape: `FW_ABSENT = not some_path.exists()`
  - the compound shape: `FW_ABSENT = not (a.exists() and b.exists())` --
    exactly what `tests/test_dispatch_mirror.py` used before its Phase 123
    Plan 08 rekey onto `tests/fw_presence.py`.

**What this must NOT forbid.** A path-existence check inside a FUNCTION
BODY, used for ordinary control flow (e.g. `if not resolved.exists(): raise
...`, or `assert not tmp_out.exists()`), is fine -- only the module-level
absence-CONSTANT shape is the defect. This checker only ever inspects a
module's TOP-LEVEL statements (`ast.Module.body`); it never descends into a
`FunctionDef`/`ClassDef` body, so an in-function existence check is
structurally invisible to it regardless of what it says. This boundary is
verified against the real, post-rekey `tests/` tree (test 1 in the paired
pytest) and against a fixture-planted legitimate in-function check that must
NOT be reported (test 4) -- do not widen this scan into function bodies; that
would turn this into an unusable lint that fires on every ordinary
existence check in the codebase.

**Env seam, list-valued, no default.** `FIRESTARTER_PROXY_LINT_TARGETS` is
read at module scope with `os.environ.get(...)` and NO default, so `None`
(the variable is absent from the environment) and `""` (present but empty)
stay distinguishable -- mirrors `tools/check_no_community_support_status_write.py`'s
`FIRESTARTER_DISP01_REPORT` seam and (in this same repo)
`.planning/phases/122-.../check_permitted_claims.py`'s
`FIRESTARTER_CLAIMSCAN_TARGETS` seam. Values are split on `os.pathsep`;
empty segments are dropped.

**Three-level precedence, `is not None` load-bearing.** Positional argv wins;
else the env seam if `is not None` (never truthiness -- an explicitly empty
value must resolve to zero targets, never a silent fall-back to defaults);
else the explicit, non-pattern default target list below.

**Explicit non-pattern default target list.** `_DEFAULT_TARGETS` below is a
literal, committed enumeration of this project's top-level `tests/*.py`
modules -- NEVER a glob, NEVER a directory walk. `tests/fixtures/` (which
deliberately contains `tests/fixtures/planted_no_exists_proxy.py`, this
gate's own anti-hollow proof) is a subdirectory and is therefore already
unreachable from a literal top-level enumeration; the discipline is kept
explicit anyway (rather than relying on glob non-recursion) because that is
this project's established house style for every checker's default target
list (`check_permitted_claims.py`'s five-file list carries the identical
rationale comment) -- a future edit that turned this into a wildcard-expanded
or tree-walked set would be exactly the kind of change that could someday
reach into `fixtures/` by accident. Adding a new `tests/*.py` module requires
a deliberate addition here; that is the maintenance cost of never silently
missing (or silently including) a file.

**Two guards, never-vacuous first.** The zero-targets guard is placed ABOVE
the missing-target guard (RESEARCH's recommended hoist over v1.22's
ordering): the missing-target guard is vacuously satisfied by an empty
target list (`[t for t in [] if not os.path.isfile(t)]` is always `[]`), so
placing it first would let an explicitly-emptied seam slip through as a
silent PASS. Observable behaviour for every non-empty case is identical;
the ordering is the hardening.

**Output.** `PASS:` names every file scanned (the anti-skip line -- a
checker that can silently skip its own scan is exactly the class of bug this
project's other checkers are written to avoid). `FAIL:` is bucketed with a
20-row cap, naming the file, the line number, and the offending constant
name, and states the fix (key on the repo marker via `tests/fw_presence.py`
instead).

**Anti-hollow contract.** This checker's committed pairing is
`tests/test_check_no_exists_proxy.py` (the paired pytest) and
`tests/fixtures/planted_no_exists_proxy.py` (the committed, deliberately
violating fixture, injected via the `FIRESTARTER_PROXY_LINT_TARGETS` seam
above) -- the mandatory anti-hollow contract every source-scanning gate in
this project carries, tracing back to the tech debt this project incurred
with v1.12's GATE-03 (a checker that could never fail because it asserted
nothing concrete).

**Explicit non-claim (load-bearing).** A green run of this gate proves this
ONE specific idiom (a module-level `not <path>.exists()`-shaped constant) is
absent from the scanned files. It does NOT prove no other fail-open shape
exists anywhere in the codebase -- this gate must never be reported, in any
SUMMARY or ledger entry, as a general absence-of-fail-open-defects proof.

Exit codes:
  0 -- every resolved target exists on disk, was parsed successfully, and
       contains zero module-level absence-proxy violations (a `PASS:` line
       naming every scanned file is printed).
  1 -- a resolved target is missing from disk (fail-closed), OR zero targets
       resolved (never-vacuous), OR at least one module-level absence-proxy
       violation was found (a bucketed `FAIL:` summary is printed).
  2 -- a resolved target could not be read, or could not be parsed as valid
       Python (a syntax error in a scanned file is a tool/configuration
       error, never a silent pass) -- an `ERROR:` message is printed to
       stderr.
"""

from __future__ import annotations

import ast
import os
import sys

# Module-top path constants (mirrors tools/check_no_log_in_sdp_window.py's
# `_HERE`-anchored idiom). tools/ -> app root is ONE parent up.
_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_ROOT = os.path.dirname(_HERE)

# ---------------------------------------------------------------------------
# Explicit, non-pattern default target list -- see the module docstring's
# "Explicit non-pattern default target list" section for the rationale.
# Every top-level tests/*.py module as of Phase 123 Plan 09. tests/fixtures/
# and tests/golden/ are subdirectories and are therefore never reachable from
# this literal enumeration.
# ---------------------------------------------------------------------------
_DEFAULT_TARGETS = [
    os.path.join(_APP_ROOT, rel)
    for rel in (
        "tests/__init__.py",
        "tests/conftest.py",
        "tests/fw_presence.py",
        "tests/scan_paths.py",
        "tests/test_address_parser.py",
        "tests/test_audit_coverage_matrix.py",
        "tests/test_boot_block_hint.py",
        "tests/test_bug_characterization.py",
        "tests/test_build_db_inclusion.py",
        "tests/test_characterization.py",
        "tests/test_check_devtest_orchestrator.py",
        "tests/test_check_dispatch_invariants.py",
        "tests/test_check_is_memory_cmd_no_ifdef.py",
        "tests/test_check_mypy_watermark.py",
        "tests/test_check_no_community_support_status_write.py",
        "tests/test_check_no_exists_proxy.py",
        "tests/test_check_no_log_in_sdp_window.py",
        "tests/test_check_sdp_capability.py",
        "tests/test_chip_resolver.py",
        "tests/test_chip_test.py",
        "tests/test_cli_handlers.py",
        "tests/test_cobs.py",
        "tests/test_codec.py",
        "tests/test_codec_format_message.py",
        "tests/test_config.py",
        "tests/test_consistency_check.py",
        "tests/test_coverage_floor_v18.py",
        "tests/test_database_conversion.py",
        "tests/test_decoder.py",
        "tests/test_dev_sdp_cmd.py",
        "tests/test_dev_test_cmd.py",
        "tests/test_diagnostic_report.py",
        "tests/test_diff_db_gate.py",
        "tests/test_dispatch_mirror.py",
        "tests/test_eprom_database.py",
        "tests/test_eprom_info.py",
        "tests/test_eprom_operations.py",
        "tests/test_error_code_seam.py",
        "tests/test_even_block.py",
        "tests/test_extra_chips_supplement.py",
        "tests/test_firmware_install.py",
        "tests/test_frame_vectors.py",
        "tests/test_fw_presence.py",
        "tests/test_fw_version_guard.py",
        "tests/test_fwguard.py",
        "tests/test_gen_test_image.py",
        "tests/test_gen_validation_header.py",
        "tests/test_hardware.py",
        "tests/test_ic_layout.py",
        "tests/test_logging_utils.py",
        "tests/test_matrix_artifact.py",
        "tests/test_matrix_schema.py",
        "tests/test_parse_devtest_issue.py",
        "tests/test_protocol_not_implemented.py",
        "tests/test_protocol_not_implemented_production_path.py",
        "tests/test_provenance.py",
        "tests/test_revision_constants_parity.py",
        "tests/test_scan_paths_resolve.py",
        "tests/test_sdp_bus_config_drift.py",
        "tests/test_sdp_capability.py",
        "tests/test_sdp_db_invariant.py",
        "tests/test_sdp_table_parity.py",
        "tests/test_serial_characterization.py",
        "tests/test_serial_comm.py",
        "tests/test_skip_census.py",
        "tests/test_submit.py",
        "tests/test_update_version.py",
        "tests/test_utils.py",
        "tests/test_val_wire_5v_page.py",
        "tests/test_val_wire_eeprom28c.py",
        "tests/test_val_wire_eprom.py",
        "tests/test_val_wire_flash_intel.py",
        "tests/test_val_wire_nor_unlock.py",
        "tests/test_val_wire_sram.py",
        "tests/test_validate_family_cmd.py",
        "tests/test_validate_oracle.py",
        "tests/test_variant_decode_evidence_stability.py",
        "tests/test_write_skip_erase_0x0d.py",
        "tests/test_write_skip_sdp_unlock.py",
    )
]

# Env-override seam: lets the paired pytest point this checker at
# deliberately-violating fixtures under tests/fixtures/ without editing any
# real test module. `os.environ.get(...)` with NO default is deliberate --
# it must return `None` when the variable is absent from the environment,
# and the (possibly empty) raw string when present, so `resolve_targets`
# below can tell "absent -> use defaults" apart from "present-but-empty ->
# zero targets, never a silent fall-back to defaults". Values are split on
# `os.pathsep`; empty segments are dropped.
FIRESTARTER_PROXY_LINT_TARGETS = os.environ.get("FIRESTARTER_PROXY_LINT_TARGETS")


def resolve_targets(argv: list[str]) -> list[str]:
    """Resolve the scan target list.

    Precedence: explicit positional `argv` paths win; else the
    `FIRESTARTER_PROXY_LINT_TARGETS` env seam if the variable is present in
    `os.environ` (checked via `is not None`, not truthiness -- an explicitly
    empty value must resolve to zero targets, never a silent fall-back to
    defaults); else `_DEFAULT_TARGETS`.
    """
    if argv:
        return list(argv)
    if FIRESTARTER_PROXY_LINT_TARGETS is not None:
        return [p for p in FIRESTARTER_PROXY_LINT_TARGETS.split(os.pathsep) if p]
    return list(_DEFAULT_TARGETS)


# ---------------------------------------------------------------------------
# The AST scan itself.
# ---------------------------------------------------------------------------


def _contains_exists_call(node: ast.AST) -> bool:
    """True if `node`'s subtree contains a call to an attribute named
    `exists` anywhere (e.g. `p.exists()`) -- used to detect both the simple
    shape (`not p.exists()`, where `node` IS the call) and the compound shape
    (`not (a.exists() and b.exists())`, where `node` is a `BoolOp` containing
    two such calls)."""
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "exists"
        ):
            return True
    return False


def find_absence_proxy_violations(
    tree: ast.Module, path: str
) -> list[tuple[str, int, str]]:
    """Scan `tree`'s MODULE-LEVEL statements only (`tree.body`, never
    descending into a `FunctionDef`/`ClassDef` body -- see the module
    docstring's "What this must NOT forbid" section) for an assignment whose
    value is a boolean negation (`not ...`) of an expression containing at
    least one `.exists()` call anywhere in its subtree.

    Returns a list of `(path, line_number, constant_name)` tuples, one per
    violating module-level assignment (empty on a clean file).
    """
    violations: list[tuple[str, int, str]] = []
    for node in tree.body:
        assignments: list[tuple[str, ast.Assign | ast.AnnAssign]] = []
        if isinstance(node, ast.Assign):
            assignments = [
                (target.id, node)
                for target in node.targets
                if isinstance(target, ast.Name)
            ]
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and isinstance(node.target, ast.Name)
        ):
            assignments = [(node.target.id, node)]

        for name, assign_node in assignments:
            value = assign_node.value
            if (
                value is not None
                and isinstance(value, ast.UnaryOp)
                and isinstance(value.op, ast.Not)
                and _contains_exists_call(value.operand)
            ):
                violations.append((path, assign_node.lineno, name))
    return violations


def _print_bucket(label: str, violations: list[str]) -> None:
    print(f"FAIL: {len(violations)} {label}:")
    for v in violations[:20]:
        print(f"  {v}")
    if len(violations) > 20:
        print(f"  ... and {len(violations) - 20} more")


def main(argv: list[str]) -> int:
    """Entry point: resolve targets, AST-scan each, exit non-zero on any
    violation.

    Guard order is load-bearing (never-vacuous BEFORE missing-target -- see
    the module docstring): an explicitly-emptied target list must fail
    before the missing-target guard, which is vacuously satisfied by an
    empty list, gets a chance to silently pass it through.
    """
    targets = resolve_targets(argv)

    if not targets:
        # Reached only when the env seam is explicitly set to the empty
        # string (or argv resolves to an empty list some other way) -- the
        # missing-target guard below is vacuously satisfied by an empty
        # list, so THIS is the real never-vacuous guard and it must run
        # first.
        print(
            "FAIL: no scan targets resolved -- the gate cannot vacuously "
            "pass with nothing scanned"
        )
        return 1

    missing = [t for t in targets if not os.path.isfile(t)]
    if missing:
        print(
            "FAIL: scan target(s) not found on disk -- the gate cannot "
            f"vacuously pass with a target silently skipped: {missing}"
        )
        return 1

    all_violations: list[tuple[str, int, str]] = []
    scanned: list[str] = []
    for t in targets:
        try:
            with open(t, encoding="utf-8") as f:
                source = f.read()
        except OSError as e:
            print(f"ERROR: could not read scan target {t}: {e}", file=sys.stderr)
            return 2
        try:
            tree = ast.parse(source, filename=t)
        except SyntaxError as e:
            print(f"ERROR: syntax error parsing {t}: {e}", file=sys.stderr)
            return 2
        scanned.append(t)
        all_violations.extend(find_absence_proxy_violations(tree, t))

    if all_violations:
        _print_bucket(
            "module-level absence-proxy violation(s)",
            [
                f"{path}:{lineno}: {name} = not <...>.exists() -- key on the "
                "repo marker via tests/fw_presence.py instead"
                for path, lineno, name in all_violations
            ],
        )
        return 1

    print(
        f"PASS: scanned {len(scanned)} file(s) for the module-level "
        f"absence-proxy idiom: "
        f"{', '.join(os.path.relpath(s, _APP_ROOT) for s in scanned)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
