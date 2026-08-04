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
  11. Broad-except deny bucket (Phase 133 D-09): a planted fixture with a
      plain `except Exception:` (under a function name and file basename
      that match NEITHER the exemption row) flips the checker non-zero,
      asserting the broad-except bucket label specifically.
  12. Broad-except form coverage (D-09): parametrised over
      `except BaseException:`, a tuple handler containing `Exception`, and
      a bare `except:` -- each flips the checker non-zero and names the
      bucket, including the bare form even though ruff's E722 already
      catches it (the bucket must not have an assumed-closed hole).
  13. Exemption scoping proof (D-14): the real checker (no override) stays
      GREEN, AND a fixture reproducing the exact exempted shape under a
      DIFFERENT function name is flagged -- proving the exemption is scoped
      to the named function, not a global whitelist of the broad form.
  14. Stale-row guard (D-14 guard b): a fixture sharing the engine module's
      basename but with `_sample` renamed (derived from the real source,
      with an `altered != original` non-vacuity assertion) flips the
      checker non-zero and names the stale-row failure.
  15. Empty/whitespace-reason guard (D-14 guard a): the one in-process leg
      -- `_validate_exemption_table` is called directly with an
      empty-reason row, a whitespace-only-reason row, and (positive
      control) the real, unmodified table.
  16. Exemption-row resolution mirror (D-14): every row in
      `_BROAD_EXCEPT_EXEMPTIONS` is asserted to name a real function in a
      real default scan target -- the in-repo mirror of the stale-row
      guard, following `test_handler_function_names_all_resolve_to_real_
      callables`'s precedent.
"""

import ast
import importlib
import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

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
# measured live 2026-08-04 against the shipped cli_handlers.py. v1.30 Phase
# 134 plan 134-05 (D-14): `_verdict_code` is no longer called directly from
# `dev_test`'s own body -- the exit computation now calls `_overall_exit_code`
# (which itself calls `_verdict_code` internally), so the body-only
# derivation swaps one name for the other; the count stays six.
_EXPECTED_DEV_TEST_REFERENCED_HELPERS = {
    "_chip_id_fields",
    "_is_interactive",
    "_make_sampler",
    "_overall_exit_code",
    "_resolve_write_scope",
    "_sanitize_chip_token",
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


# ---------------------------------------------------------------------------
# Test 11 (Phase 131 Plan 04, GATE-10 non-vacuity proof): a synthetic
# dev_test referencing an unlisted helper is proven caught by the SAME
# derivation the real leg above uses.
# ---------------------------------------------------------------------------

# A synthetic module -- never committed as a tests/fixtures/ file, matching
# this module's existing planted-violation construction (inline strings).
# `_sdp_leg_probe` is the exact shape Phases 133/134 will add (a new
# `dev test` helper); `_decorator_only_helper` mirrors the real
# `_complete_eprom` shape -- referenced ONLY from dev_test's decorator list,
# never its body -- so this one fixture proves BOTH halves of correction
# F-04 at once: the body-referenced unlisted helper is caught, and the
# decorator-referenced one is excluded.
_SYNTHETIC_UNLISTED_HELPER_SOURCE = """
def _decorator_only_helper():
    pass


def _sdp_leg_probe():
    pass


@some_decorator(callback=_decorator_only_helper)
def dev_test():
    return _sdp_leg_probe()
"""


def test_derivation_flags_an_unlisted_helper_non_vacuous() -> None:
    """A synthetic `dev_test` body calling an unlisted helper is caught and
    named by the SAME derivation the real leg
    (test_every_helper_referenced_by_dev_test_is_listed) calls -- this is
    the anti-hollow pairing this module's own docstring makes mandatory
    (lines 4-11): a checker with no negative-fixture test is exactly the
    v1.12 GATE-03 failure mode.

    Asserting only that "something was flagged" is not enough -- an
    assertion that fires for the wrong reason is a defect class this
    project has already recorded twice (memory:
    reference_gate_authored_before_content_can_be_unreachable). So this leg
    makes three separate, named assertions: the body-referenced unlisted
    helper IS in the derived set; the decorator-referenced helper is NOT (the
    positive proof of correction F-04's decorator-list exclusion); and the
    omission list computed against `_HANDLER_FUNCTION_NAMES` names the
    body-referenced helper specifically, not merely non-emptily.

    Drives `_referenced_underscore_helpers_in_dev_test` directly -- the same
    helper the real leg calls -- rather than re-implementing the walk, so a
    passing suite proves the HELPER catches a real addition, not that this
    test does.
    """
    check_devtest_orchestrator = importlib.import_module(
        "tools.check_devtest_orchestrator"
    )

    derived = _referenced_underscore_helpers_in_dev_test(
        _SYNTHETIC_UNLISTED_HELPER_SOURCE
    )

    assert "_sdp_leg_probe" in derived, (
        f"the body-referenced helper _sdp_leg_probe was not found in the "
        f"derived set {sorted(derived)} -- the derivation failed to see a "
        f"real reference inside dev_test's body."
    )
    assert "_decorator_only_helper" not in derived, (
        f"_decorator_only_helper -- referenced ONLY from dev_test's "
        f"decorator list, never its body -- leaked into the derived set "
        f"{sorted(derived)}. The derivation must exclude decorator_list "
        f"(correction F-04)."
    )

    missing = sorted(derived - check_devtest_orchestrator._HANDLER_FUNCTION_NAMES)
    assert missing == ["_sdp_leg_probe"], (
        f"expected the omission list to name exactly ['_sdp_leg_probe'] "
        f"(the body-referenced helper this synthetic source deliberately "
        f"omits from the real _HANDLER_FUNCTION_NAMES), got {missing}. An "
        f"empty or differently-named omission list means the subset "
        f"comparison is not actually exercising the planted violation."
    )


# ---------------------------------------------------------------------------
# Test 11 (Phase 133 D-09): planted broad-except violation, anti-hollow proof
# for the fourth deny bucket.
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_broad_except(tmp_path: Path) -> None:
    """A real subprocess-level `except Exception:` MUST fail the gate.

    The fixture's basename ("planted_broad_except.py") and its function
    name ("orchestrate") match NEITHER of the exemption row's (basename,
    function) pair -- so this is a genuine, non-exempt broad-except site,
    proven via the same real-subprocess injection pattern as tests 2-5.
    """
    bad = tmp_path / "planted_broad_except.py"
    bad.write_text(
        "def orchestrate(op):\n"
        "    try:\n"
        "        return op.write_eprom('chip', {}, 'path')\n"
        "    except Exception:\n"
        "        return None\n"
    )
    result = _run_checker({"FIRESTARTER_DEVTEST_SRC": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted broad-except violation.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "broad exception handler" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Test 12 (Phase 133 D-09): every broad form, parametrised
# ---------------------------------------------------------------------------

_BROAD_EXCEPT_VARIANT_BODIES = {
    "base_exception": (
        "def orchestrate(op):\n"
        "    try:\n"
        "        return op.write_eprom('chip', {}, 'path')\n"
        "    except BaseException:\n"
        "        return None\n"
    ),
    "tuple_form": (
        "def orchestrate(op):\n"
        "    try:\n"
        "        return op.write_eprom('chip', {}, 'path')\n"
        "    except (ValueError, Exception):\n"
        "        return None\n"
    ),
    "bare_except": (
        "def orchestrate(op):\n"
        "    try:\n"
        "        return op.write_eprom('chip', {}, 'path')\n"
        "    except:\n"
        "        return None\n"
    ),
}


@pytest.mark.parametrize(
    "body",
    _BROAD_EXCEPT_VARIANT_BODIES.values(),
    ids=_BROAD_EXCEPT_VARIANT_BODIES.keys(),
)
def test_checker_exits_nonzero_on_planted_broad_except_variants(
    tmp_path: Path, body: str
) -> None:
    """`except BaseException:`, a tuple containing `Exception`, and a bare
    `except:` MUST all fail the gate and name the broad-except bucket.

    The bare form is included deliberately even though ruff's E722 already
    catches it elsewhere in CI -- this bucket must not carry an assumed-
    closed hole a reader would take on faith.
    """
    bad = tmp_path / "planted_broad_except_variant.py"
    bad.write_text(body)
    result = _run_checker({"FIRESTARTER_DEVTEST_SRC": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a planted broad-except variant.\n"
        f"body:\n{body}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "broad exception handler" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Test 13 (Phase 133 D-14): the exemption is scoped to the named function,
# not a blanket whitelist of the broad form.
# ---------------------------------------------------------------------------


def test_checker_exemption_keeps_clean_source_green(tmp_path: Path) -> None:
    """The real checker stays GREEN through the one exemption, AND a fixture
    reproducing the exact exempted shape under a DIFFERENT function name is
    still flagged.

    Without this leg, an exemption that accidentally matched the broad
    form globally (rather than the named (basename, function) pair) would
    pass every other leg in this module -- this is the one that would catch
    it.
    """
    clean_result = _run_checker()
    assert clean_result.returncode == 0, (
        f"checker exited {clean_result.returncode} on the real, clean "
        f"sources (expected the _sample exemption to keep this GREEN).\n"
        f"stdout:\n{clean_result.stdout}\nstderr:\n{clean_result.stderr}"
    )
    assert "PASS:" in clean_result.stdout

    bad = tmp_path / "planted_sample_shaped_but_unexempted.py"
    bad.write_text(
        "def _sample_but_not_the_real_one(sampler, phase):\n"
        "    if sampler is None:\n"
        "        return\n"
        "    try:\n"
        "        sampler(phase)\n"
        "    except Exception:\n"
        "        pass\n"
    )
    flagged_result = _run_checker({"FIRESTARTER_DEVTEST_SRC": str(bad)})
    assert flagged_result.returncode != 0, (
        f"checker exited 0 on the exempted shape under a DIFFERENT function "
        f"name -- the exemption is not scoped to the named function.\n"
        f"stdout:\n{flagged_result.stdout}\nstderr:\n{flagged_result.stderr}"
    )
    assert "FAIL:" in flagged_result.stdout
    assert "broad exception handler" in flagged_result.stdout.lower()


# ---------------------------------------------------------------------------
# Test 14 (Phase 133 D-14 guard b): stale-row guard
# ---------------------------------------------------------------------------


def test_checker_exemption_stale_row_fails(tmp_path: Path) -> None:
    """A fixture sharing the engine module's basename, but with `_sample`
    renamed, MUST fail the gate and name the stale-row problem.

    Derives the fixture from the REAL engine source with a mechanical
    rename applied (`def _sample(` -> `def _sample_renamed(`), asserting
    `altered != original` first -- if `_sample` ever moves or is renamed
    upstream, this replacement silently becomes a no-op and this leg would
    otherwise pass vacuously (proving nothing).
    """
    real_path = _FA_DIR / "firestarter" / "chip_test.py"
    original = real_path.read_text()
    altered = original.replace("def _sample(", "def _sample_renamed(")
    assert altered != original, (
        "the `def _sample(` -> `def _sample_renamed(` replacement did not "
        "change the source -- _sample may have moved, been renamed, or "
        "removed upstream; this leg would be vacuous against a no-op fixture"
    )

    bad = tmp_path / "chip_test.py"
    bad.write_text(altered)
    result = _run_checker({"FIRESTARTER_DEVTEST_SRC": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a fixture with a stale (renamed-away) "
        f"exemption target.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "stale" in result.stdout.lower(), (
        f"expected the stale-row message in stdout but got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Test 15 (Phase 133 D-14 guard a): the one in-process leg
# ---------------------------------------------------------------------------


def test_exemption_table_empty_reason_fails() -> None:
    """`_validate_exemption_table` rejects an empty-reason row and a
    whitespace-only-reason row, and (positive control) accepts the real,
    unmodified table.

    The ONLY in-process leg in this module: `_validate_exemption_table` is
    a pure function that reads no module global and involves no env seam,
    so import-time binding order (Pitfall 5 -- why every OTHER leg here
    shells out) is irrelevant to it.
    """
    check_devtest_orchestrator = importlib.import_module(
        "tools.check_devtest_orchestrator"
    )
    real_table = dict(check_devtest_orchestrator._BROAD_EXCEPT_EXEMPTIONS)

    empty_reason_table = {**real_table, ("fake_module.py", "_fake_fn"): ""}
    problems = check_devtest_orchestrator._validate_exemption_table(empty_reason_table)
    assert problems, "an empty-reason exemption row was not flagged"
    assert any("fake_module.py" in p and "_fake_fn" in p for p in problems), (
        f"expected the empty-reason problem to name the offending row, got: {problems}"
    )

    whitespace_reason_table = {**real_table, ("fake_module.py", "_fake_fn2"): "   "}
    problems_ws = check_devtest_orchestrator._validate_exemption_table(
        whitespace_reason_table
    )
    assert problems_ws, "a whitespace-only-reason exemption row was not flagged"

    # Positive control (T-133-24): without this, the guard could pass by
    # always returning a non-empty problem list regardless of input.
    clean_problems = check_devtest_orchestrator._validate_exemption_table(real_table)
    assert clean_problems == [], (
        f"the real, unmodified _BROAD_EXCEPT_EXEMPTIONS table failed "
        f"validation: {clean_problems}"
    )


# ---------------------------------------------------------------------------
# Test 16 (Phase 133 D-14): in-repo mirror of the stale-row guard
# ---------------------------------------------------------------------------


def test_exemption_table_rows_all_resolve() -> None:
    """Every row in `_BROAD_EXCEPT_EXEMPTIONS` names a real function in a
    real default scan target.

    The in-repo mirror of the stale-row guard (which only fires at gate
    runtime against whatever was actually scanned) -- following this file's
    own `test_handler_function_names_all_resolve_to_real_callables`
    precedent for the handler allow-list.
    """
    check_devtest_orchestrator = importlib.import_module(
        "tools.check_devtest_orchestrator"
    )
    default_targets = [
        check_devtest_orchestrator._DEFAULT_CHIP_TEST,
        check_devtest_orchestrator._DEFAULT_DEVTEST_HANDLER,
        check_devtest_orchestrator._DEFAULT_DEVTEST_SUBMIT,
    ]

    for (
        row_file,
        row_function,
    ), reason in check_devtest_orchestrator._BROAD_EXCEPT_EXEMPTIONS.items():
        assert reason and reason.strip(), (
            f"exemption row ({row_file!r}, {row_function!r}) has no reason "
            f"-- test_exemption_table_empty_reason_fails should already "
            f"catch this at the table level, but asserting it here too "
            f"keeps this leg self-contained."
        )
        matching = [t for t in default_targets if os.path.basename(t) == row_file]
        assert matching, (
            f"exemption row file {row_file!r} does not match any default "
            f"scan target's basename ({[os.path.basename(t) for t in default_targets]})"
        )
        found = False
        for path in matching:
            source = Path(path).read_text()
            tree = ast.parse(source, filename=path)
            if any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == row_function
                for node in ast.walk(tree)
            ):
                found = True
                break
        assert found, (
            f"exemption row ({row_file!r}, {row_function!r}) does not "
            f"resolve to a real function in any default scan target -- this "
            f"row is STALE (mirrors the stale-row guard the gate itself "
            f"enforces at runtime)."
        )
