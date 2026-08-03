"""
Tests for check_devtest_orchestrator.py (SAFE-03, Phase 109 D-02/D-03).

This is the mandatory anti-hollow pairing for the SAFE-03 gate: a checker
tool with no negative-fixture test is exactly the failure mode this project
incurred with v1.12's GATE-03 (a declared-empty detector that could never
fail because nothing concrete was asserted). Every planted-violation test
below injects a REAL subprocess-level violation via the
`FIRESTARTER_DEVTEST_SRC` / `FIRESTARTER_DEVTEST_HANDLER` env-overrides --
never an in-process synthetic -- so a passing test suite proves the checker
itself (not the test) fails the build on a real violation.

Coverage:
  1. Clean-pass baseline: the checker exits 0 on the current, real
     chip_test.py (post-109-01/109-02 source).
  2. Planted VPP-set violation: a temp fixture calling `op.set_vpp(...)`
     flips the checker to a non-zero exit with a FAIL: summary.
  3. Planted raw-wire-dict violation: a temp fixture returning a dict literal
     carrying >=2 wire-protocol keys flips the checker non-zero.
  4. Planted --force violation: a temp fixture passing `force=True` flips
     the checker non-zero.
  5. Planted "--force" string-literal violation: a temp fixture containing
     the bare CLI flag string flips the checker non-zero.
  6. Env-override seam sanity: a clean fixture injected via
     FIRESTARTER_DEVTEST_SRC still passes.
  7. Handler-shaped planted violation (Phase 112, anti-hollow for the
     `dev_test` handler leg specifically): a fixture defining a `dev_test`
     function containing a forbidden op, injected via
     FIRESTARTER_DEVTEST_HANDLER, flips the checker non-zero -- AND the real,
     clean `cli_handlers.py` (which the checker now actually scans, scoped to
     the `dev_test` function + its private helpers) still passes.
  8. submit.py-shaped planted violation (Phase 113, anti-hollow for the
     THIRD full-scan leg specifically): a fixture with a forbidden op
     injected via FIRESTARTER_DEVTEST_SUBMIT flips the checker non-zero --
     AND a clean fixture through the same env-override still passes -- AND
     the real, clean `submit.py` still passes with the PASS line naming it.
  9. GATE-10 (Phase 131 Plan 04, D-15/F-04): a body-only AST derivation --
     `_referenced_underscore_helpers_in_dev_test` -- collects every
     module-level `_`-prefixed function referenced from `dev_test`'s BODY
     statements only (never its decorator list) and asserts that set is a
     SUBSET of `_HANDLER_FUNCTION_NAMES`, naming any omission. This converts
     the allow-list's additive fail-open (a partial name match scans
     successfully and silently omits a new, unlisted helper) into an
     additive fail-closed. Direction matters: this leg proves every
     *referenced* helper is *listed*; test 9 above
     (`test_handler_function_names_all_resolve_to_real_callables`) proves
     every *listed* name is *real*. Together they are bidirectional.
  10. GATE-10 non-vacuity proof: a synthetic module source defines a
      `dev_test` whose BODY calls an unlisted helper and whose DECORATOR
      references a *different* unlisted helper. The same derivation helper
      the real leg (9 above) calls is asserted to name the body-referenced
      helper as an omission and to EXCLUDE the decorator-referenced one --
      proving the decorator-list exclusion (F-04) positively rather than by
      assumption, and proving the derivation is not vacuously empty.
"""

import ast
import importlib
import inspect
import os
import subprocess
import sys
from pathlib import Path

# Absolute path to the firestarter_app directory (cwd-independent), mirrors
# tests/test_check_dispatch_invariants.py:22.
_FA_DIR = Path(__file__).parent.parent


def _run_checker(
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_overrides or {})}
    return subprocess.run(
        [sys.executable, "tools/check_devtest_orchestrator.py"],
        cwd=str(_FA_DIR),
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# GATE-10 (Phase 131 Plan 04, D-15/F-04): body-only AST derivation
# ---------------------------------------------------------------------------
#
# Shared by BOTH the real leg (test_every_helper_referenced_by_dev_test_is_
# listed, against the shipped cli_handlers.py) and the non-vacuity leg
# (test_derivation_flags_an_unlisted_helper_non_vacuous, against a synthetic
# source string) -- taking `source: str` rather than a path is what lets a
# single helper serve both, so the non-vacuity leg exercises the exact code
# the real leg does, not a re-implementation of the walk.


def _referenced_underscore_helpers_in_dev_test(source: str) -> set[str]:
    """Body-only AST derivation of every module-level `_`-prefixed helper
    referenced from `dev_test`'s body.

    Deliberately walks `dev_test.body` statement-by-statement rather than
    `ast.walk(dev_test_node)` on the whole `FunctionDef` -- correction F-04.
    A whole-node walk includes `decorator_list`, and the real
    `cli_handlers.py` decorates `dev_test` with
    `@click.argument("chip", shell_complete=_complete_eprom)`: `_complete_eprom`
    is a shell-completion callback shared by 15 unrelated commands, not a
    `dev test` helper, and is not (and must never be) in
    `_HANDLER_FUNCTION_NAMES`. Walking the whole node would inject that name
    and make the subset leg red for a locator reason, not a substantive one.

    Raises ValueError if no module-level `dev_test` FunctionDef is found --
    returning an empty set instead would make the caller's subset assertion
    vacuously true, which is exactly the fail-open this derivation exists to
    remove.
    """
    tree = ast.parse(source)

    module_level_underscore_funcs = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("_")
    }

    dev_test_node = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "dev_test"
        ),
        None,
    )
    if dev_test_node is None:
        raise ValueError(
            "no module-level `dev_test` FunctionDef found in the given "
            "source -- refusing to return an empty set, which would make "
            "the subset assertion vacuously true"
        )

    referenced: set[str] = set()
    for stmt in dev_test_node.body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Name):
                referenced.add(sub.id)
            elif isinstance(sub, ast.Attribute):
                referenced.add(sub.attr)

    return referenced & module_level_underscore_funcs


# ---------------------------------------------------------------------------
# Test 1: clean-pass baseline
# ---------------------------------------------------------------------------


def test_checker_exits_zero_on_clean_source() -> None:
    """python tools/check_devtest_orchestrator.py must exit 0 on the real,
    clean chip_test.py (post-109-01/109-02 source: routes every op through
    resolve_chip, sets no VPP, builds no raw wire dict, passes no --force).
    """
    result = _run_checker()
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on clean source.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout, (
        f"Expected 'PASS:' in output but got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Test 2: planted VPP-set violation (anti-hollow contract, D-03)
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_vpp_set(tmp_path: Path) -> None:
    """A real subprocess-level VPP-set call site MUST fail the gate.

    This is the anti-hollow proof (D-03): the fixture is written to disk and
    the checker is pointed at it via the FIRESTARTER_DEVTEST_SRC env-override
    (mirrors check_dispatch.py's FIRESTARTER_DB_FILE seam) -- a real
    subprocess-level violation, not an in-process synthetic.
    """
    bad = tmp_path / "planted_vpp_set.py"
    bad.write_text(
        "def orchestrate(op):\n"
        "    op.set_vpp(12000)\n"
        "    return op.write_eprom('chip', {}, 'path')\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_SRC": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted VPP-set violation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "VPP-set" in result.stdout


# ---------------------------------------------------------------------------
# Test 3: planted raw-wire-dict violation
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_raw_wire_dict(tmp_path: Path) -> None:
    """A real subprocess-level raw wire-dict literal MUST fail the gate."""
    bad = tmp_path / "planted_raw_wire_dict.py"
    bad.write_text(
        "def build_command():\n    return {'cmd': 2, 'algorithm': 7, 'vpp_mv': 12000}\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_SRC": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted raw-wire-dict violation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "wire" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Test 4: planted force=True keyword violation
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_force_true(tmp_path: Path) -> None:
    """A real subprocess-level force=True keyword pass-through MUST fail the gate."""
    bad = tmp_path / "planted_force_true.py"
    bad.write_text(
        "def orchestrate(op):\n    return op.erase_eprom('chip', {}, force=True)\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_SRC": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted force=True violation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "force" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Test 5: planted "--force" CLI-flag string-literal violation
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_force_flag_string(tmp_path: Path) -> None:
    """A real subprocess-level '--force' string literal MUST fail the gate."""
    bad = tmp_path / "planted_force_flag.py"
    bad.write_text("ARGS = ['dev', 'test', 'chip', '--force']\n")
    result = _run_checker({"FIRESTARTER_DEVTEST_SRC": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted --force string-literal violation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "force" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Test 6: env-override seam sanity (proves the injection path itself works)
# ---------------------------------------------------------------------------


def test_env_override_points_at_a_clean_fixture_still_passes(tmp_path: Path) -> None:
    """A CLEAN fixture injected via the env-override must still pass.

    Proves the env-override seam is a faithful re-target (not itself the
    source of the non-zero exit in tests 2-5) -- a clean fixture routed
    through the same seam produces PASS:, isolating the violations above as
    the true cause of the non-zero exits.
    """
    clean = tmp_path / "planted_clean.py"
    clean.write_text(
        "def orchestrate(op, eprom_data):\n"
        "    return op.write_eprom('chip', eprom_data, 'path')\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_SRC": str(clean)})
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on a clean env-override fixture.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout


# ---------------------------------------------------------------------------
# Test 7: handler-shaped planted violation (Phase 112 anti-hollow proof)
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_handler_violation(tmp_path: Path) -> None:
    """A handler-shaped fixture with a forbidden op MUST fail the gate.

    Mimics the real `dev_test` handler's shape (a `dev_test`-named function
    calling into an operator) but plants a VPP-set call site inside it. This
    is the anti-hollow proof for the HANDLER leg specifically (Phase-109
    D-02/D-03): the fixture is written to disk and injected via
    FIRESTARTER_DEVTEST_HANDLER (a real subprocess-level violation, not an
    in-process synthetic) -- if the checker silently skipped the handler
    scan (or scanned the wrong function names), this would incorrectly pass.
    """
    bad = tmp_path / "planted_handler_violation.py"
    bad.write_text(
        "def dev_test(app, chip, destructive, output_dir, assume_yes):\n"
        "    app.hardware_manager.set_vpp(12000)\n"
        "    return app.eprom_operator.write_eprom(chip, {}, 'path')\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_HANDLER": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted handler-shaped VPP-set violation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "VPP-set" in result.stdout


def test_checker_exits_nonzero_on_planted_handler_force_violation(
    tmp_path: Path,
) -> None:
    """A handler-shaped fixture passing force=True MUST fail the gate.

    Second handler-leg planted-violation shape (force pass-through rather
    than VPP-set) -- proves the scoped handler scan catches more than one
    deny bucket, not just the one the first fixture happens to hit.
    """
    bad = tmp_path / "planted_handler_force.py"
    bad.write_text(
        "def dev_test(app, chip, destructive, output_dir, assume_yes):\n"
        "    return app.eprom_operator.erase_eprom(chip, {}, force=True)\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_HANDLER": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted handler-shaped force=True violation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "force" in result.stdout.lower()


def test_checker_exits_zero_on_real_handler_now_in_scope() -> None:
    """The clean-pass baseline, re-asserted with the handler leg in scope.

    Load-bearing proof (Phase 112): the real, shipped `cli_handlers.py`
    (scoped to `dev_test` + its private helpers) is orchestrator-only --
    this is the same invocation as test_checker_exits_zero_on_clean_source
    but stated explicitly so a future reader sees the handler-in-scope
    assertion is deliberate, not incidental.
    """
    result = _run_checker()
    assert result.returncode == 0, (
        f"checker exited {result.returncode} with the real handler in scope.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout
    assert "cli_handlers.py" in result.stdout, (
        f"Expected the PASS: line to name cli_handlers.py (handler actually "
        f"scanned, not skipped) but got:\n{result.stdout}"
    )


def test_env_override_points_at_a_clean_handler_fixture_still_passes(
    tmp_path: Path,
) -> None:
    """A CLEAN handler-shaped fixture injected via the env-override still passes.

    Proves the FIRESTARTER_DEVTEST_HANDLER seam is a faithful re-target (not
    itself the source of the non-zero exit in the tests above) -- a clean
    fixture defining `dev_test` (with no forbidden ops) routed through the
    same seam produces PASS:, isolating the violations above as the true
    cause of the non-zero exits.
    """
    clean = tmp_path / "planted_handler_clean.py"
    clean.write_text(
        "def dev_test(app, chip, destructive, output_dir, assume_yes):\n"
        "    return app.eprom_operator.write_eprom(chip, {}, 'path')\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_HANDLER": str(clean)})
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on a clean handler env-override "
        f"fixture.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout


# ---------------------------------------------------------------------------
# Test 8: submit.py third full-scan leg (Phase 113, anti-hollow proof)
# ---------------------------------------------------------------------------


def test_checker_exits_zero_on_real_submit_and_pass_line_names_it() -> None:
    """The real, clean `submit.py` passes, and the PASS: line names it --
    proving the third leg actually ran (was not silently skipped, the
    v1.12 hollow-GATE-03 failure mode)."""
    result = _run_checker()
    assert result.returncode == 0, (
        f"checker exited {result.returncode} with the submit.py leg in scope.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout
    assert "submit.py" in result.stdout, (
        f"Expected the PASS: line to name submit.py (leg actually scanned, "
        f"not skipped) but got:\n{result.stdout}"
    )


def test_checker_exits_nonzero_on_planted_submit_vpp_set_violation(
    tmp_path: Path,
) -> None:
    """A submit-shaped fixture with a real VPP-set call site, injected via
    FIRESTARTER_DEVTEST_SUBMIT, MUST fail the gate (anti-hollow proof for
    the new leg, T-113-01)."""
    bad = tmp_path / "planted_submit_vpp_set.py"
    bad.write_text(
        "def submit_report(op, report, chip, saved_json_path):\n"
        "    op.set_vpp(12000)\n"
        "    return None\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_SUBMIT": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted submit-shaped VPP-set violation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "VPP-set" in result.stdout


def test_checker_exits_nonzero_on_planted_submit_force_violation(
    tmp_path: Path,
) -> None:
    """A submit-shaped fixture passing force=True, injected via
    FIRESTARTER_DEVTEST_SUBMIT, MUST fail the gate."""
    bad = tmp_path / "planted_submit_force.py"
    bad.write_text(
        "def submit_report(op, report, chip, saved_json_path):\n"
        "    return op.erase_eprom(chip, {}, force=True)\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_SUBMIT": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted submit-shaped force=True violation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "force" in result.stdout.lower()


def test_env_override_points_at_a_clean_submit_fixture_still_passes(
    tmp_path: Path,
) -> None:
    """A CLEAN submit-shaped fixture injected via the env-override still
    passes -- proves the FIRESTARTER_DEVTEST_SUBMIT seam is a faithful
    re-target (not itself the source of the non-zero exit in the two tests
    above), isolating the planted violations as the true cause."""
    clean = tmp_path / "planted_submit_clean.py"
    clean.write_text(
        "def submit_report(report, chip, saved_json_path):\n    return None\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_SUBMIT": str(clean)})
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on a clean submit env-override "
        f"fixture.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout


# ---------------------------------------------------------------------------
# Test 9 (Phase 121 Plan 09, RESEARCH C-4): allow-list completeness leg
# ---------------------------------------------------------------------------


def test_handler_function_names_all_resolve_to_real_callables() -> None:
    """Every name in `_HANDLER_FUNCTION_NAMES` MUST resolve to a real
    callable in `firestarter.cli_handlers` -- turns the pre-Plan-121-09
    `_is_uv_eprom` dangling entry (present in the allow-list since Phase 112,
    pointing at nothing) into a permanently-enforced invariant rather than a
    one-off fix. A name that stops resolving here means a helper was
    renamed/removed without updating this gate -- exactly the silent
    under-coverage RESEARCH C-4 proved is possible."""
    check_devtest_orchestrator = importlib.import_module(
        "tools.check_devtest_orchestrator"
    )
    from firestarter import cli_handlers

    missing = [
        name
        for name in check_devtest_orchestrator._HANDLER_FUNCTION_NAMES
        if not callable(getattr(cli_handlers, name, None))
    ]
    assert missing == [], (
        f"_HANDLER_FUNCTION_NAMES contains name(s) with no matching callable "
        f"in cli_handlers: {missing}"
    )


def test_handler_function_names_contains_the_new_uv_scope_helpers() -> None:
    """`_resolve_write_scope` and `_is_uv_eprom` (this plan's two new
    handler helpers) are both named in the allow-list -- the mandatory task
    RESEARCH C-4 called out, not merely avoiding a gate trip."""
    check_devtest_orchestrator = importlib.import_module(
        "tools.check_devtest_orchestrator"
    )
    assert "_is_uv_eprom" in check_devtest_orchestrator._HANDLER_FUNCTION_NAMES
    assert "_resolve_write_scope" in check_devtest_orchestrator._HANDLER_FUNCTION_NAMES


# ---------------------------------------------------------------------------
# Test 10 (Phase 131 Plan 04, GATE-10 / D-15, correction F-04): derived-subset
# leg -- converts the allow-list's additive fail-open into an additive
# fail-closed.
# ---------------------------------------------------------------------------

# The six real names dev_test's BODY (never its decorator list) references,
# measured live 2026-08-03 against the shipped cli_handlers.py.
_EXPECTED_DEV_TEST_REFERENCED_HELPERS = {
    "_chip_id_fields",
    "_is_interactive",
    "_make_sampler",
    "_resolve_write_scope",
    "_sanitize_chip_token",
    "_verdict_code",
}


def test_every_helper_referenced_by_dev_test_is_listed() -> None:
    """Every module-level `_`-prefixed helper referenced from `dev_test`'s
    BODY is a member of `_HANDLER_FUNCTION_NAMES`.

    The checker's own comment (cli_handlers.py-adjacent,
    check_devtest_orchestrator.py:134-137) already states the obligation in
    prose: "Every future helper added to the `dev test` surface MUST be
    listed here, or this gate silently under-covers exactly that new code."
    This leg converts that prose into a mechanical, permanently-enforced
    invariant -- the exact conversion GATE-10 exists to make.

    Direction matters, and is deliberately asymmetric with test 9 above
    (test_handler_function_names_all_resolve_to_real_callables): THIS leg
    proves every *referenced* helper is *listed*; test 9 proves every
    *listed* name is *real*. Together they are bidirectional; neither alone
    is. The assertion here is a SUBSET, never an equality, because
    `_default_uv_write_confirm` and `_is_uv_eprom` are legitimately listed
    but not referenced from `dev_test`'s body (they are called from other
    handler-side helpers) -- an equality assertion would be red for the
    opposite reason on day one.
    """
    check_devtest_orchestrator = importlib.import_module(
        "tools.check_devtest_orchestrator"
    )
    from firestarter import cli_handlers

    source = inspect.getsource(cli_handlers)
    derived = _referenced_underscore_helpers_in_dev_test(source)

    # Non-vacuity guard (T-131-22): a helper that silently returns `{}` --
    # because dev_test moved, was renamed, or the walk broke -- would satisfy
    # a subset assertion trivially. Assert real content BEFORE comparing.
    assert derived, (
        "_referenced_underscore_helpers_in_dev_test returned an empty set "
        "against the real cli_handlers.py -- this would make the subset "
        "assertion below vacuously true. dev_test may have moved, been "
        "renamed, or the AST walk broke."
    )
    assert len(derived) >= 6, (
        f"_referenced_underscore_helpers_in_dev_test returned only "
        f"{len(derived)} name(s) ({sorted(derived)}) against the real "
        f"cli_handlers.py -- expected at least 6. A shrinking derived set "
        f"is itself suspicious even though the subset check below would "
        f"still pass."
    )

    assert derived == _EXPECTED_DEV_TEST_REFERENCED_HELPERS, (
        f"the body-only derivation returned {sorted(derived)}, expected "
        f"exactly {sorted(_EXPECTED_DEV_TEST_REFERENCED_HELPERS)}. If this "
        f"is a legitimate new dev_test helper, list it in "
        f"_HANDLER_FUNCTION_NAMES (tools/check_devtest_orchestrator.py) and "
        f"update this expected set in the same commit."
    )

    missing = sorted(derived - check_devtest_orchestrator._HANDLER_FUNCTION_NAMES)
    assert missing == [], (
        f"dev_test's body references helper(s) NOT listed in "
        f"_HANDLER_FUNCTION_NAMES: {missing}. Add them to the allow-list in "
        f"tools/check_devtest_orchestrator.py -- do NOT widen the checker's "
        f"scan target to compensate."
    )

    assert "_complete_eprom" not in derived, (
        "_complete_eprom (dev_test's shell_complete= decorator argument, "
        "shared by 15 unrelated commands) leaked into the derived set -- "
        "the derivation must walk dev_test's BODY statements only, never "
        "its decorator_list (correction F-04)."
    )
