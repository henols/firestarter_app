"""
Op-registration parity gate (v1.30 Phase 133 D-12, LEG-15).

Converts the fail-open registries P-23's census found into one fail-closed
gate: for every op string in `chip_test.py`'s vocabulary and every registry a
new op must join, this module asserts either MEMBERSHIP or an explicit,
reasoned exemption -- and fails when a pair is neither.

Coverage:
  1. `test_every_op_is_registered_or_exempt` -- the main leg: every
     (op, registry) pair across the live vocabulary and every policed
     registry is a member or carries a reasoned exemption.
  2. `test_exemption_empty_reason_fails` -- guard (a): an exemption whose
     reason is empty, whitespace-only, or `None` fails; the real,
     unmodified table does NOT (positive control).
  3. `test_stale_row_fails` -- guard (b): an exemption row naming an op or a
     registry that no longer exists fails, proven by planting one of each.
  4. `test_declared_registry_count_matches` -- guard (c): the measured
     registry counts must equal the declared constants, so an Nth registry
     added without being policed (or declared) fails.
  5. `test_non_registry_still_has_no_ops` -- the inversion guard: every
     declared non-registry is re-measured, via AST, to still carry zero op
     vocabulary. This is the genuinely valuable leg -- without it, a future
     phase's renderer that starts switching on op strings would silently
     inherit a permanent exemption.
  6. `test_altered_registry_copy_fails_parity_non_vacuous` -- non-vacuity:
     an in-memory copy of `_POLICED_REGISTRIES` with one real, unexempted op
     removed from a registry it currently belongs to MUST make the parity
     assertion fail.
  7. `test_sdp_ops_are_accounted_in_every_policed_registry` -- a targeted
     leg for this phase's own two ops, pinning the EXACT expected
     disposition (member vs. exempt) per pair, so a future change that
     silently flips one direction fails here even if the generic leg above
     would somehow still pass.

Why AST introspection, not a bare regex: op VALUES are hyphenated strings
("blank-check", "write-partial", "sdp-lock", "sdp-unlock") that also appear
as ordinary English inside this very module's docstrings and comments --
`_dispatch_step`'s own docstring contains the literal substring
"blank-check" in prose describing its arms. A text-level regex would count
that as a reference; walking `ast.Name` identifier nodes (`OP_BLANK_CHECK`,
never the bare string) does not, because a hyphen is not a valid Python
identifier character -- the value can appear in prose, but the identifier
that resolves to it cannot. The inversion guard's own zero-vocabulary
measurement (item 5 above) additionally excludes docstring `Constant` nodes
by AST position for the same reason, one level down.

No skip marker, no firmware-presence decorator: unlike its
`test_sdp_table_parity.py` template, every real op-keyed registry this
module polices lives inside `firestarter_app` itself (`chip_test.py`,
`diagnostic_report.py`, `cli_handlers.py`,
`tools/check_devtest_orchestrator.py`, `tools/parse_devtest_issue.py`) --
none of it is firmware-repo-dependent. This gate is host-and-engine-local
and therefore MUST run in standalone CI; it does not import the shared
sibling-repo-presence helper module and does not read any path under the
sibling `firestarter/` firmware repo.

MEASURED registry census (2026-08-04, re-run against this session's
working tree, not inherited from ROADMAP criterion 5's "eight previously
fail-open registries" -- that count is measurably wrong). **Updated 2026-08-04
(v1.30 Phase 134, plan 134-01): the op vocabulary is now 13 (9 + this
phase's own four SDP-leg ops), and a seventh policed registry
(`_SDP_LEG_OPS`) joins the set below:**

  - **7 policed registries** (a new op must join one of these, or carry a
    reasoned exemption): `_DESTRUCTIVE_OPS`, `_MULTI_RUN_OPS`, `_SDP_OPS`,
    `_SDP_LEG_OPS` (all four real op-keyed frozensets in `chip_test.py`, the
    last added by Phase 134); `_dispatch_step` (its dispatch arms,
    AST-derived); `derive_plan` (its `Step(op=...)` construction sites,
    AST-derived); `_dispatch_multi_run` (its inner run-loop branches,
    AST-derived -- a genuine op-keyed site P-23's original table omitted
    entirely).
  - **6 declared non-registries** (carry NO op vocabulary, or are keyed on
    a materially different axis -- re-measured, re-asserted every run by
    the inversion guard, never merely assumed): `_RAN_VERDICTS` /
    `count_applicable` (verdict-keyed, not op-keyed); `dedup_fingerprint`
    (generic over `StepResult.op`); the `diagnostic_report.py`
    `DiagnosticReport` renderer (`to_dict`/`render`/`_step_dict`, likewise
    generic); `tools/parse_devtest_issue.py` (measured to carry ZERO
    op-string constants at all -- P-23's row 8 counted it as a fail-open
    registry, which this session's measurement (133-CONTEXT.md correction
    2) found to be wrong: there is no allow-list to omit a new op from);
    `_ALWAYS_WRITES_NOTICE` (a fixed prose string, zero op vocabulary); and
    `check_devtest_orchestrator.py`'s `_HANDLER_FUNCTION_NAMES` (a
    DIFFERENT axis -- it holds CLI-handler FUNCTION names, never op
    strings).

  Net: of P-23's original ten-row table, 6 rows are real policed
  registries (one more than P-23 counted, since `_dispatch_multi_run`'s
  inner branches were missing from it), 3 rows carry no op vocabulary
  whatsoever, and 1 row (`_HANDLER_FUNCTION_NAMES`) is keyed on a different
  axis entirely. ROADMAP criterion 5's "eight previously fail-open
  registries" is therefore measurably wrong on both ends: it undercounts
  the real policed set by one and overcounts the declared-non-registry set
  by miscategorizing genuinely-empty rows as "fail-open registries" when
  they were never registries an op could be omitted from in the first
  place.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import firestarter.chip_test as chip_test_mod
import firestarter.cli_handlers as cli_handlers_mod
import firestarter.diagnostic_report as diagnostic_report_mod
import tools.check_devtest_orchestrator as check_devtest_orchestrator_mod
import tools.parse_devtest_issue as parse_devtest_issue_mod
from firestarter.chip_test import (
    OP_SDP_LOCK,
    OP_SDP_UNLOCK,
    OP_WRITE,
    OP_WRITE_BASELINE_A,
    OP_WRITE_BASELINE_B,
    OP_WRITE_INHIBITED,
    OP_WRITE_RESTORED,
)

# ---------------------------------------------------------------------------
# Op vocabulary and registry-constant identification (real truth, from the
# imported module -- never hardcoded values that could drift from source).
# ---------------------------------------------------------------------------

_OP_CONSTANT_NAMES: frozenset[str] = frozenset(
    name
    for name, value in vars(chip_test_mod).items()
    if name.startswith("OP_") and isinstance(value, str)
)

_ALL_OPS: frozenset[str] = frozenset(
    value
    for name, value in vars(chip_test_mod).items()
    if name.startswith("OP_") and isinstance(value, str)
)

# The real, op-keyed containers in chip_test.py. `_op_names_referenced_in`
# resolves a Name reference to one of these TRANSITIVELY to its members (the
# real, imported container), rather than re-deriving membership from source
# text a second time. `_SDP_LEG_STEP_ORDER` (v1.30 Phase 134, plan 134-03)
# joins this set: `derive_plan`'s own SDP-leg emission loop
# (`for sdp_op in _SDP_LEG_STEP_ORDER:`) never spells out the six OP_*
# identifiers directly, so without this entry the AST walk below would see
# zero op-vocabulary references inside `derive_plan` at all -- deriving,
# not restating, is exactly the discipline `_SDP_LEG_STEP_ORDER` itself
# documents (P-08).
_REGISTRY_CONSTANT_NAMES: frozenset[str] = frozenset(
    {
        "_DESTRUCTIVE_OPS",
        "_MULTI_RUN_OPS",
        "_SDP_OPS",
        "_SDP_LEG_OPS",
        "_SDP_LEG_STEP_ORDER",
    }
)

# The multi-word (hyphenated) op values -- currently four: "blank-check",
# "write-partial", "sdp-lock", "sdp-unlock". Deliberately NOT the five
# single-word op values ("id", "read", "write", "verify", "erase") -- those
# collide with ordinary English/generic identifiers and would false-positive
# a literal-string scan; a hyphenated value cannot appear as a Python
# identifier and is a safe literal-equality target.
_MULTIWORD_OP_VALUES: frozenset[str] = frozenset(v for v in _ALL_OPS if "-" in v)

assert len(_ALL_OPS) == 13, (
    f"measured {len(_ALL_OPS)} OP_* string constants in chip_test.py, "
    "expected 13 -- the census baked into this module's docstring and "
    "_POLICED_REGISTRIES/_OP_REGISTRY_EXEMPTIONS needs re-measuring"
)


def _module_source(module: Any) -> str:
    path = inspect.getsourcefile(module)
    assert path is not None, f"could not resolve a source file for {module!r}"
    return Path(path).read_text(encoding="utf-8")


_CHIP_TEST_SOURCE = _module_source(chip_test_mod)


# ---------------------------------------------------------------------------
# `_op_names_referenced_in` -- takes SOURCE AS A STRING PARAMETER (not a
# path), which is what lets this one helper build the real
# `_POLICED_REGISTRIES` entries below AND be reused directly against a
# synthetic source string in a unit test, without a second implementation of
# the walk (the `_referenced_underscore_helpers_in_dev_test` precedent,
# tests/test_check_devtest_orchestrator.py).
# ---------------------------------------------------------------------------


def _op_names_referenced_in(func_name: str, source: str) -> frozenset[str]:
    """AST-derive the set of op-string values a named function references.

    Parses `source`, locates the (first) `FunctionDef`/`AsyncFunctionDef`
    named `func_name`, and walks its body collecting:
      (a) every `ast.Name` id that is one of the module's `OP_*`
          identifiers -- resolved to that constant's real string VALUE
          (read from the imported `chip_test` module, i.e. real truth); and
      (b) every referenced registry-constant name (`_DESTRUCTIVE_OPS`,
          `_MULTI_RUN_OPS`, `_SDP_OPS`) -- resolved TRANSITIVELY to that
          constant's full member set (also read from the imported module),
          so an arm written as `if step.op in _MULTI_RUN_OPS:` counts as
          covering every member of `_MULTI_RUN_OPS`, not merely the four
          characters of the identifier itself.

    Raises `ValueError` if no such function is found -- returning an empty
    frozenset instead would make the corresponding registry vacuously
    "fully exempt", which is exactly the fail-open shape this gate exists
    to remove.
    """
    tree = ast.parse(source)
    func_node = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == func_name
        ),
        None,
    )
    if func_node is None:
        raise ValueError(
            f"no FunctionDef named {func_name!r} found in the given source "
            "-- refusing to return an empty frozenset, which would make "
            "this registry vacuously exempt from every op"
        )

    referenced: set[str] = set()
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Name):
            continue
        if node.id in _OP_CONSTANT_NAMES:
            referenced.add(getattr(chip_test_mod, node.id))
        elif node.id in _REGISTRY_CONSTANT_NAMES:
            # `.update(...)`, not `|=`: `_SDP_LEG_STEP_ORDER` is a TUPLE
            # (order is load-bearing, D-06), and `set |= tuple` raises
            # TypeError (`|=` requires another `set`) where `set.update()`
            # accepts any iterable -- measured, not assumed.
            referenced.update(getattr(chip_test_mod, node.id))
    return frozenset(referenced)


# ---------------------------------------------------------------------------
# `_POLICED_REGISTRIES` -- name -> frozenset of op strings it currently
# covers. Three built directly from the imported module's real frozensets;
# three built via `_op_names_referenced_in` over chip_test.py's real,
# on-disk source (re-read every run, never a frozen literal copy).
# ---------------------------------------------------------------------------

_POLICED_REGISTRIES: dict[str, frozenset[str]] = {
    "_DESTRUCTIVE_OPS": chip_test_mod._DESTRUCTIVE_OPS,
    "_MULTI_RUN_OPS": chip_test_mod._MULTI_RUN_OPS,
    "_SDP_OPS": chip_test_mod._SDP_OPS,
    "_SDP_LEG_OPS": chip_test_mod._SDP_LEG_OPS,
    "_dispatch_step": _op_names_referenced_in("_dispatch_step", _CHIP_TEST_SOURCE),
    "derive_plan": _op_names_referenced_in("derive_plan", _CHIP_TEST_SOURCE),
    "_dispatch_multi_run": _op_names_referenced_in(
        "_dispatch_multi_run", _CHIP_TEST_SOURCE
    ),
}

# Measured 2026-08-04 (v1.30 Phase 134, plan 134-01): 7 policed registries --
# one more than 133-06's 6, since `_SDP_LEG_OPS` (this phase's own frozenset,
# T-134-01) is now a live, policed registry.
_POLICED_REGISTRY_COUNT = 7


# ---------------------------------------------------------------------------
# `_DECLARED_NON_REGISTRIES` -- units that carry NO op vocabulary (or are
# keyed on a materially different axis), so no `(op, registry)` exemption is
# ever written against them. Re-measured every run by the inversion guard
# (test 5), never merely assumed true forever.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _NonRegistryLocator:
    """Where a declared non-registry's zero-op-vocabulary claim is checked.

    `unit_kind` scopes the inversion guard's measurement granularity:
      - "module": walk the whole parsed file.
      - "class": walk only the named class's body.
      - "function": walk only the named function's body.
      - "constant": inspect the named module attribute's real VALUE
        directly (a prose string or a frozenset of strings), never its
        source text -- there is no "function body" to scope to for a
        module-level constant.
    """

    module: Any
    unit_kind: str
    unit_name: str | None = None


_DECLARED_NON_REGISTRIES: dict[str, tuple[_NonRegistryLocator, str]] = {
    "_RAN_VERDICTS/count_applicable": (
        _NonRegistryLocator(chip_test_mod, "function", "count_applicable"),
        "not an op-keyed registry -- keyed on StepResult.verdict "
        "(_RAN_VERDICTS = {OK, BAD, marginal}), a materially different axis "
        "from op identity; count_applicable counts ran-vs-not-ran by "
        "verdict membership alone (SWEEP-05), so a new op needs nothing "
        "added here and is picked up automatically.",
    ),
    "dedup_fingerprint": (
        _NonRegistryLocator(diagnostic_report_mod, "function", "dedup_fingerprint"),
        "not an op-keyed registry -- generic over StepResult.op (Phase 121 "
        "D-06/D-08): it hashes whatever op string each result carries "
        "without comparing it against any specific OP_* constant, so a new "
        "op's result is folded into the hash automatically with nothing to "
        "register.",
    ),
    "diagnostic_report.py renderer": (
        _NonRegistryLocator(diagnostic_report_mod, "class", "DiagnosticReport"),
        "not an op-keyed registry -- to_dict()/render()/_step_dict() (RPT-01, "
        "D-01) read StepResult.op generically for display; none of them "
        "compares against a specific OP_* constant, so a new op renders "
        "with no code change here.",
    ),
    "tools/parse_devtest_issue.py": (
        _NonRegistryLocator(parse_devtest_issue_mod, "module", None),
        "not an op-keyed registry at all -- measured this session "
        "(133-CONTEXT.md correction 2): the module is a generic JSON-fence "
        "extractor with ZERO OP_* string constants anywhere in it. P-23's "
        "row 8 listed it as a fail-open registry ('a new op in a filed "
        "issue may not parse'); that listing is measured-wrong -- there is "
        "no allow-list here for a new op to be omitted from.",
    ),
    "_ALWAYS_WRITES_NOTICE": (
        _NonRegistryLocator(cli_handlers_mod, "constant", "_ALWAYS_WRITES_NOTICE"),
        "not an op-keyed registry -- a fixed prose notice string with zero "
        "op vocabulary; any future write-count correction the notice's "
        "wording needs is Phase 134's (P-08), not this parity gate's.",
    ),
    "_HANDLER_FUNCTION_NAMES": (
        _NonRegistryLocator(
            check_devtest_orchestrator_mod, "constant", "_HANDLER_FUNCTION_NAMES"
        ),
        "different axis, declared separately -- keyed on cli_handlers.py "
        "FUNCTION names (the dev_test handler plus its private co-located "
        "helpers), never on op strings. P-07's gap concerns a missing "
        "HANDLER helper, not a missing op, so it is accounted here rather "
        "than folded into the op-registration exemption table, keeping the "
        "two axes from collapsing into one.",
    ),
}

_DECLARED_NON_REGISTRY_COUNT = 6


# ---------------------------------------------------------------------------
# `_OP_REGISTRY_EXEMPTIONS` -- (op, registry_name) -> non-empty reason.
# Every non-member pair across the full 13-op x 7-registry grid is
# accounted for here (guard (c) proves the grid's registry side is
# exhaustive; this dict proves the membership side is). This is
# deliberately larger than the four SDP-specific groups the plan text calls
# out by name -- those four are THIS PHASE's real, newly-introduced
# omissions; the remaining rows are
# pre-existing structural non-memberships (e.g. a read-only op was never a
# candidate for the destructive-mutation registry) that this new gate is the
# first thing to ever write down explicitly. Measured, not inherited: see
# the module docstring's census.
# ---------------------------------------------------------------------------

_NOT_DESTRUCTIVE_REASON = (
    "{op} is a non-mutating read/inspection op, never a candidate for "
    "_DESTRUCTIVE_OPS membership -- the frozenset's own comment "
    "(chip_test.py, SWEEP-03) states it exists solely to gate write-shaped "
    "mutations behind the id-first destructive gate; {op}'s absence here "
    "is structural, not an omission."
)

_NOT_MULTI_RUN_REASON = (
    "{op} is not a destructive/verify op; _MULTI_RUN_OPS's marginal-on-"
    "disagreement policy (D-05/D-06) applies only to write/write-partial/"
    "erase/verify -- a single-run op has nothing to disagree across "
    "multiple runs about, so {op}'s absence here is structural."
)

_NOT_SDP_REASON = (
    "{op} is not an SDP lock/unlock emission; _SDP_OPS (v1.30 Phase 133 "
    "D-01) is a scoped allow-list built for exactly OP_SDP_LOCK/"
    "OP_SDP_UNLOCK. {op} dispatches via an earlier _dispatch_step arm and "
    "never evaluates the _SDP_OPS membership test at all (D-04's "
    "zero-added-branching-cost sentinel proves this mechanically), so its "
    "absence here is definitional, not an omission."
)

_NOT_ROUTED_TO_MULTI_RUN_REASON = (
    "{op} is not a destructive/verify op; _dispatch_step only routes "
    "_MULTI_RUN_OPS members into _dispatch_multi_run at all (D-04), so "
    "{op}'s dispatch never reaches this function's run loop -- its "
    "absence here is structural."
)

_NOT_SDP_LEG_REASON = (
    "{op} is not one of this phase's four SDP-leg ops; _SDP_LEG_OPS (v1.30 "
    "Phase 134 T-134-01) is a scoped allow-list built for exactly "
    "OP_WRITE_BASELINE_B/OP_WRITE_BASELINE_A/OP_WRITE_INHIBITED/"
    "OP_WRITE_RESTORED, so {op}'s absence here is definitional, not an "
    "omission."
)

_NOT_MULTI_RUN_SDP_LEG_REASON = (
    "D-03: the SDP leg's ops are single-run and fold their own read-back "
    "verification into one arm (D-07's two-baseline-ops design) -- there "
    "is no second run for {op} to disagree with, so _MULTI_RUN_OPS's "
    "marginal-on-disagreement policy does not apply to it."
)

_NOT_SDP_OPS_SDP_LEG_REASON = (
    "_SDP_OPS (v1.30 Phase 133 D-01) is a scoped allow-list built for "
    "exactly OP_SDP_LOCK/OP_SDP_UNLOCK, dispatched through "
    "_dispatch_sdp's frozen four-positional signature (133 D-01's forward "
    "contract, unchanged by this phase). {op} is write-shaped and needs a "
    "source pattern plus a read-back verification, which that signature "
    "cannot carry -- it dispatches through the ordinary write/verify path "
    "instead, so its absence from _SDP_OPS here is structural."
)

_NOT_ROUTED_TO_MULTI_RUN_SDP_LEG_REASON = (
    "structurally excluded: _dispatch_step only routes _MULTI_RUN_OPS "
    "members into _dispatch_multi_run (D-04), and {op} is not a "
    "_MULTI_RUN_OPS member (see the _MULTI_RUN_OPS exemption row above), "
    "so this function's run loop never sees it."
)

_OP_REGISTRY_EXEMPTIONS: dict[tuple[str, str], str] = {
    # --- _DESTRUCTIVE_OPS: non-mutating ops (structural), plus LEG-09's
    # deliberate asymmetry for sdp-unlock. ---
    ("id", "_DESTRUCTIVE_OPS"): _NOT_DESTRUCTIVE_REASON.format(op="id"),
    ("read", "_DESTRUCTIVE_OPS"): _NOT_DESTRUCTIVE_REASON.format(op="read"),
    ("blank-check", "_DESTRUCTIVE_OPS"): _NOT_DESTRUCTIVE_REASON.format(
        op="blank-check"
    ),
    ("verify", "_DESTRUCTIVE_OPS"): (
        "verify reads back and compares without itself mutating the chip "
        "(the write it validates is separately gated); it was never a "
        "candidate for _DESTRUCTIVE_OPS membership."
    ),
    (OP_SDP_UNLOCK, "_DESTRUCTIVE_OPS"): (
        "LEG-09's deliberate asymmetry: sdp_unlock is EXEMPT from the "
        "destructive gate so a gate closed after the lock can never skip "
        "the unlock and strand a locked part on a stranger's bench. "
        "OP_SDP_LOCK needs no such exemption -- 133-03 made it a member "
        "(D-11 forward-protection for Phase 134's derived leg)."
    ),
    # --- _MULTI_RUN_OPS: non-mutating ops (structural), plus D-03's
    # deliberate exclusion of both SDP ops. ---
    ("id", "_MULTI_RUN_OPS"): _NOT_MULTI_RUN_REASON.format(op="id"),
    ("read", "_MULTI_RUN_OPS"): _NOT_MULTI_RUN_REASON.format(op="read"),
    ("blank-check", "_MULTI_RUN_OPS"): _NOT_MULTI_RUN_REASON.format(op="blank-check"),
    (OP_SDP_LOCK, "_MULTI_RUN_OPS"): (
        "D-03: SDP emissions are single-run and explicitly excluded from "
        "_MULTI_RUN_OPS -- a lock/unlock's result cannot be read back at "
        "all on this family (Phase 117 D-05, Phase 119 D-12), so the "
        "marginal-on-disagreement policy is meaningless for it."
    ),
    (OP_SDP_UNLOCK, "_MULTI_RUN_OPS"): (
        "D-03: SDP emissions are single-run and explicitly excluded from "
        "_MULTI_RUN_OPS -- a lock/unlock's result cannot be read back at "
        "all on this family (Phase 117 D-05, Phase 119 D-12), so the "
        "marginal-on-disagreement policy is meaningless for it."
    ),
    # --- _SDP_OPS: every non-SDP op, structurally (D-01/D-04). ---
    ("id", "_SDP_OPS"): _NOT_SDP_REASON.format(op="id"),
    ("read", "_SDP_OPS"): _NOT_SDP_REASON.format(op="read"),
    ("blank-check", "_SDP_OPS"): _NOT_SDP_REASON.format(op="blank-check"),
    ("write", "_SDP_OPS"): _NOT_SDP_REASON.format(op="write"),
    ("write-partial", "_SDP_OPS"): _NOT_SDP_REASON.format(op="write-partial"),
    ("verify", "_SDP_OPS"): _NOT_SDP_REASON.format(op="verify"),
    ("erase", "_SDP_OPS"): _NOT_SDP_REASON.format(op="erase"),
    # --- _dispatch_multi_run: non-mutating ops (structural), plus D-03's
    # structural exclusion of both SDP ops (they never reach this loop). ---
    ("id", "_dispatch_multi_run"): _NOT_ROUTED_TO_MULTI_RUN_REASON.format(op="id"),
    ("read", "_dispatch_multi_run"): _NOT_ROUTED_TO_MULTI_RUN_REASON.format(op="read"),
    ("blank-check", "_dispatch_multi_run"): _NOT_ROUTED_TO_MULTI_RUN_REASON.format(
        op="blank-check"
    ),
    (OP_SDP_LOCK, "_dispatch_multi_run"): (
        "structurally excluded by D-03: _dispatch_step only routes "
        "_MULTI_RUN_OPS members into _dispatch_multi_run, and neither SDP "
        "op is a member, so this function's run loop never sees it."
    ),
    (OP_SDP_UNLOCK, "_dispatch_multi_run"): (
        "structurally excluded by D-03: _dispatch_step only routes "
        "_MULTI_RUN_OPS members into _dispatch_multi_run, and neither SDP "
        "op is a member, so this function's run loop never sees it."
    ),
    # --- _SDP_LEG_OPS: every non-SDP-leg op, structurally (v1.30 Phase 134
    # T-134-01, LEG-03) -- this phase's own scoped 4-member allow-list. ---
    ("id", "_SDP_LEG_OPS"): _NOT_SDP_LEG_REASON.format(op="id"),
    ("read", "_SDP_LEG_OPS"): _NOT_SDP_LEG_REASON.format(op="read"),
    ("blank-check", "_SDP_LEG_OPS"): _NOT_SDP_LEG_REASON.format(op="blank-check"),
    ("write", "_SDP_LEG_OPS"): _NOT_SDP_LEG_REASON.format(op="write"),
    ("write-partial", "_SDP_LEG_OPS"): _NOT_SDP_LEG_REASON.format(op="write-partial"),
    ("verify", "_SDP_LEG_OPS"): _NOT_SDP_LEG_REASON.format(op="verify"),
    ("erase", "_SDP_LEG_OPS"): _NOT_SDP_LEG_REASON.format(op="erase"),
    (OP_SDP_LOCK, "_SDP_LEG_OPS"): _NOT_SDP_LEG_REASON.format(op=OP_SDP_LOCK),
    (OP_SDP_UNLOCK, "_SDP_LEG_OPS"): _NOT_SDP_LEG_REASON.format(op=OP_SDP_UNLOCK),
    # --- _MULTI_RUN_OPS: this phase's four SDP-leg ops, single-run (D-03,
    # mirroring the pre-existing SDP-lock/unlock exclusion above). ---
    (OP_WRITE_BASELINE_B, "_MULTI_RUN_OPS"): _NOT_MULTI_RUN_SDP_LEG_REASON.format(
        op=OP_WRITE_BASELINE_B
    ),
    (OP_WRITE_BASELINE_A, "_MULTI_RUN_OPS"): _NOT_MULTI_RUN_SDP_LEG_REASON.format(
        op=OP_WRITE_BASELINE_A
    ),
    (OP_WRITE_INHIBITED, "_MULTI_RUN_OPS"): _NOT_MULTI_RUN_SDP_LEG_REASON.format(
        op=OP_WRITE_INHIBITED
    ),
    (OP_WRITE_RESTORED, "_MULTI_RUN_OPS"): _NOT_MULTI_RUN_SDP_LEG_REASON.format(
        op=OP_WRITE_RESTORED
    ),
    # --- _SDP_OPS: this phase's four SDP-leg ops cannot fit _dispatch_sdp's
    # frozen four-positional signature (structural, D-07). ---
    (OP_WRITE_BASELINE_B, "_SDP_OPS"): _NOT_SDP_OPS_SDP_LEG_REASON.format(
        op=OP_WRITE_BASELINE_B
    ),
    (OP_WRITE_BASELINE_A, "_SDP_OPS"): _NOT_SDP_OPS_SDP_LEG_REASON.format(
        op=OP_WRITE_BASELINE_A
    ),
    (OP_WRITE_INHIBITED, "_SDP_OPS"): _NOT_SDP_OPS_SDP_LEG_REASON.format(
        op=OP_WRITE_INHIBITED
    ),
    (OP_WRITE_RESTORED, "_SDP_OPS"): _NOT_SDP_OPS_SDP_LEG_REASON.format(
        op=OP_WRITE_RESTORED
    ),
    # --- _dispatch_multi_run: this phase's four SDP-leg ops never reach
    # this run loop (structural, since they are not _MULTI_RUN_OPS members
    # -- see above). ---
    (OP_WRITE_BASELINE_B, "_dispatch_multi_run"): (
        _NOT_ROUTED_TO_MULTI_RUN_SDP_LEG_REASON.format(op=OP_WRITE_BASELINE_B)
    ),
    (OP_WRITE_BASELINE_A, "_dispatch_multi_run"): (
        _NOT_ROUTED_TO_MULTI_RUN_SDP_LEG_REASON.format(op=OP_WRITE_BASELINE_A)
    ),
    (OP_WRITE_INHIBITED, "_dispatch_multi_run"): (
        _NOT_ROUTED_TO_MULTI_RUN_SDP_LEG_REASON.format(op=OP_WRITE_INHIBITED)
    ),
    (OP_WRITE_RESTORED, "_dispatch_multi_run"): (
        _NOT_ROUTED_TO_MULTI_RUN_SDP_LEG_REASON.format(op=OP_WRITE_RESTORED)
    ),
    # --- _dispatch_step: no exemptions needed here (v1.30 Phase 134, plan
    # 134-02). Arm 6 (`if step.op in _SDP_LEG_OPS: return
    # _dispatch_sdp_leg(...)`) now routes all four SDP-leg ops, so
    # `_op_names_referenced_in("_dispatch_step", ...)` resolves them as real
    # members via `_SDP_LEG_OPS`'s transitive membership -- the four
    # TEMPORARY rows plan 134-01 added here are discharged (removed) in this
    # same commit that adds the routing, per that plan's own instruction. ---
    # --- derive_plan: DISCHARGED (v1.30 Phase 134, plan 134-03). All six
    # SDP-leg ops (OP_SDP_LOCK/OP_SDP_UNLOCK plus this phase's own four
    # write-shaped ops) are now real `Step(op=...)` construction sites
    # inside `derive_plan` (D-06's six-step emission, LEG-01/02/04) -- the
    # six TEMPORARY/Phase-134-surface rows that used to exempt them here
    # are removed in this same commit, per plan 134-02's own forward note
    # and D-11's "Phase 134 discharges it". ---
}

# `_dispatch_step` needs ZERO exemptions for ANY op -- measured: all 13
# resolve into it (arms 1-4 cover id/blank-check/read plus every
# _MULTI_RUN_OPS member, arm 5 covers every _SDP_OPS member, and arm 6
# -- v1.30 Phase 134, plan 134-02 -- covers every _SDP_LEG_OPS member via
# `_dispatch_sdp_leg`). The four TEMPORARY rows plan 134-01 added here are
# discharged in this same plan's commit, restoring the "zero exemptions at
# all" shape `_dispatch_step` had before Phase 134 introduced any new op.
_dispatch_step_exempted_ops = {
    op for (op, reg) in _OP_REGISTRY_EXEMPTIONS if reg == "_dispatch_step"
}
assert _dispatch_step_exempted_ops == set(), (
    "_dispatch_step was measured to cover all 13 ops (9 pre-existing plus "
    "this phase's own 4 SDP-leg ops, routed via arm 6) with NO exemptions "
    "needed at all -- any exemption row against it means either the "
    "measurement changed (re-verify _POLICED_REGISTRIES['_dispatch_step']) "
    f"or a stray row was added in error. Exempted ops: "
    f"{_dispatch_step_exempted_ops!r}"
)

# Guard: exemptions must never reference a declared non-registry -- those
# are accounted by _DECLARED_NON_REGISTRIES and the inversion guard, not by
# exemption, so "not an omission at all" and "a deferred omission" never
# collapse into one bucket. Verified at import time (a module-level
# assertion, per the acceptance criteria), not merely in a test function.
_disjoint_violations = [
    (op, registry_name)
    for (op, registry_name) in _OP_REGISTRY_EXEMPTIONS
    if registry_name in _DECLARED_NON_REGISTRIES
]
assert not _disjoint_violations, (
    "_OP_REGISTRY_EXEMPTIONS must never reference a declared non-registry "
    "-- those are accounted by _DECLARED_NON_REGISTRIES and the inversion "
    f"guard, not by exemption. Violations: {_disjoint_violations}"
)


# ---------------------------------------------------------------------------
# The parity context -- carries WHY this matters into every failure message.
# ---------------------------------------------------------------------------

_PARITY_CONTEXT = (
    "Before Phase 121, an unmapped op fell through to operator.erase_eprom() "
    "and reported OK (RESEARCH Pitfall 1a). An op added to the vocabulary "
    "but missing from a registry it must join -- with no membership AND no "
    "reasoned exemption -- is that defect class returning. LEG-15 converts "
    "every such fail-open registry into one fail-closed gate."
)


# ---------------------------------------------------------------------------
# `_assert_op_parity` -- pure, takes the registry map as an ARGUMENT. This
# is what lets the non-vacuity leg (test 6) exercise the exact assertion
# code the real leg (test 1) uses, against an altered in-memory copy,
# rather than a re-implementation of the check.
# ---------------------------------------------------------------------------


def _assert_op_parity(
    registries: dict[str, frozenset[str]],
    exemptions: dict[tuple[str, str], str],
    ops: frozenset[str],
    context: str,
) -> None:
    """For every (op, registry) pair, assert membership or a reasoned
    exemption. Raises AssertionError naming every offending pair (not just
    the first) plus `context` when any pair is neither."""
    problems: list[str] = []
    for registry_name, members in registries.items():
        for op in ops:
            if op in members:
                continue
            reason = exemptions.get((op, registry_name))
            if reason is None or not reason.strip():
                problems.append(
                    f"op {op!r} is neither a member of registry "
                    f"{registry_name!r} nor covered by a reasoned exemption"
                )
    if problems:
        raise AssertionError(f"{context}\n" + "\n".join(f"  - {p}" for p in problems))


def _stale_exemption_rows(
    exemptions: dict[tuple[str, str], str],
    registries: dict[str, frozenset[str]],
    ops: frozenset[str],
) -> list[str]:
    """Guard (b): every exemption row's op must be in the live op
    vocabulary AND its registry must be a currently-policed registry name.

    A stale row -- naming an op or a registry that no longer exists (e.g.
    after a Phase-134 rename) -- would otherwise silently keep permitting an
    omission after the thing it was written for is gone.
    """
    problems: list[str] = []
    for op, registry_name in exemptions:
        if op not in ops:
            problems.append(
                f"stale exemption row: op {op!r} is not in the live op "
                "vocabulary -- a rename left a dead exemption that is no "
                "longer protecting anything real"
            )
        if registry_name not in registries:
            problems.append(
                f"stale exemption row: registry {registry_name!r} is not a "
                "currently-policed registry -- a rename or removal left a "
                "dead exemption that is no longer protecting anything real"
            )
    return problems


# ---------------------------------------------------------------------------
# The inversion guard's measurement machinery: re-derive, via AST, whether
# each declared non-registry still carries zero op vocabulary.
# ---------------------------------------------------------------------------


def _docstring_constant_ids(root: ast.AST) -> set[int]:
    """Return the `id()` of every Constant node that is a docstring (the
    first statement of a Module/ClassDef/FunctionDef/AsyncFunctionDef body),
    so the vocabulary scan below can exclude them -- a docstring describing
    an op in prose is not a vocabulary REFERENCE."""
    ids: set[int] = set()
    candidates: list[ast.AST] = [root] + [
        n
        for n in ast.walk(root)
        if isinstance(
            n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)
        )
    ]
    for node in candidates:
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            ids.add(id(body[0].value))
    return ids


def _count_op_vocabulary_references(root: ast.AST) -> int:
    """Count `OP_*` identifier references plus exact-equality multi-word
    op-value string literals under `root`, excluding docstrings."""
    docstring_ids = _docstring_constant_ids(root)
    count = 0
    for node in ast.walk(root):
        if isinstance(node, ast.Name) and node.id in _OP_CONSTANT_NAMES:
            count += 1
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in _MULTIWORD_OP_VALUES
            and id(node) not in docstring_ids
        ):
            count += 1
    return count


def _find_named_node(tree: ast.AST, kind: Any, name: str) -> ast.AST | None:
    return next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, kind) and getattr(n, "name", None) == name
        ),
        None,
    )


def _measure_op_vocabulary(locator: _NonRegistryLocator) -> int:
    """Re-measure, from the real on-disk source (or the real live value for
    a "constant" locator), how much op vocabulary `locator`'s declared unit
    currently carries. Zero is the claim every declared non-registry makes;
    this is how that claim gets re-checked every run instead of assumed."""
    if locator.unit_kind == "constant":
        assert locator.unit_name is not None
        value = getattr(locator.module, locator.unit_name)
        if isinstance(value, str):
            return sum(1 for mw in _MULTIWORD_OP_VALUES if mw in value)
        if isinstance(value, (frozenset, set, list, tuple)):
            return sum(
                1
                for element in value
                if isinstance(element, str) and element in _ALL_OPS
            )
        raise TypeError(
            f"unsupported constant type for {locator.unit_name!r}: {type(value)!r}"
        )

    source = _module_source(locator.module)
    tree = ast.parse(source)

    root: ast.AST | None
    if locator.unit_kind == "module":
        root = tree
    elif locator.unit_kind == "function":
        assert locator.unit_name is not None
        root = _find_named_node(
            tree, (ast.FunctionDef, ast.AsyncFunctionDef), locator.unit_name
        )
    elif locator.unit_kind == "class":
        assert locator.unit_name is not None
        root = _find_named_node(tree, ast.ClassDef, locator.unit_name)
    else:
        raise ValueError(f"unknown unit_kind {locator.unit_kind!r}")

    if root is None:
        raise ValueError(
            f"could not locate {locator.unit_kind} {locator.unit_name!r} in "
            f"{locator.module.__name__} -- this declared non-registry "
            "locator has rotted (the unit was renamed or removed)"
        )

    return _count_op_vocabulary_references(root)


# ---------------------------------------------------------------------------
# Test 1: the main leg
# ---------------------------------------------------------------------------


def test_every_op_is_registered_or_exempt() -> None:
    _assert_op_parity(
        _POLICED_REGISTRIES, _OP_REGISTRY_EXEMPTIONS, _ALL_OPS, _PARITY_CONTEXT
    )


# ---------------------------------------------------------------------------
# Test 2: guard (a) -- mandatory non-empty reason
# ---------------------------------------------------------------------------


def test_exemption_empty_reason_fails() -> None:
    # Positive control FIRST: the real, unmodified table does NOT raise.
    # Without this, the legs below could pass by always failing regardless
    # of input.
    _assert_op_parity(
        _POLICED_REGISTRIES, _OP_REGISTRY_EXEMPTIONS, _ALL_OPS, _PARITY_CONTEXT
    )

    sample_key = next(iter(_OP_REGISTRY_EXEMPTIONS))

    for bad_reason in ("", "   ", None):
        mutated = dict(_OP_REGISTRY_EXEMPTIONS)
        mutated[sample_key] = bad_reason  # type: ignore[assignment]
        with pytest.raises(AssertionError):
            _assert_op_parity(_POLICED_REGISTRIES, mutated, _ALL_OPS, _PARITY_CONTEXT)


# ---------------------------------------------------------------------------
# Test 3: guard (b) -- stale row
# ---------------------------------------------------------------------------


def test_stale_row_fails() -> None:
    # Positive control: the real table has no stale rows today.
    assert (
        _stale_exemption_rows(_OP_REGISTRY_EXEMPTIONS, _POLICED_REGISTRIES, _ALL_OPS)
        == []
    )

    stale = dict(_OP_REGISTRY_EXEMPTIONS)
    stale[("op-does-not-exist", "_DESTRUCTIVE_OPS")] = "planted stale op row"
    stale[(OP_SDP_LOCK, "_registry_does_not_exist")] = "planted stale registry row"

    problems = _stale_exemption_rows(stale, _POLICED_REGISTRIES, _ALL_OPS)
    assert len(problems) == 2, f"expected exactly 2 stale-row problems, got: {problems}"
    assert any("op-does-not-exist" in p for p in problems), problems
    assert any("_registry_does_not_exist" in p for p in problems), problems


# ---------------------------------------------------------------------------
# Test 4: guard (c) -- declared counts match measured counts
# ---------------------------------------------------------------------------


def test_declared_registry_count_matches() -> None:
    assert _POLICED_REGISTRY_COUNT == len(_POLICED_REGISTRIES), (
        f"_POLICED_REGISTRY_COUNT ({_POLICED_REGISTRY_COUNT}) != measured "
        f"{len(_POLICED_REGISTRIES)} registries -- an eleventh (or Nth) "
        "fail-open registry was added without being policed, inside the "
        "phase whose whole job was removing them."
    )
    assert _DECLARED_NON_REGISTRY_COUNT == len(_DECLARED_NON_REGISTRIES), (
        f"_DECLARED_NON_REGISTRY_COUNT ({_DECLARED_NON_REGISTRY_COUNT}) != "
        f"measured {len(_DECLARED_NON_REGISTRIES)} declared non-registries."
    )


# ---------------------------------------------------------------------------
# Test 5: the inversion guard
# ---------------------------------------------------------------------------


def test_non_registry_still_has_no_ops() -> None:
    problems: list[str] = []
    for label, (locator, _reason) in _DECLARED_NON_REGISTRIES.items():
        measured = _measure_op_vocabulary(locator)
        if measured != 0:
            problems.append(f"{label}: measured {measured} op-vocabulary reference(s)")

    assert not problems, (
        "A declared non-registry has acquired op vocabulary -- PROMOTE it "
        "to _POLICED_REGISTRIES, do not loosen this guard. A permanent "
        "exemption on a unit that starts switching on op strings is exactly "
        "the fail-open shape LEG-15 exists to remove.\n" + "\n".join(problems)
    )


# ---------------------------------------------------------------------------
# Test 6: non-vacuity
# ---------------------------------------------------------------------------


def test_altered_registry_copy_fails_parity_non_vacuous() -> None:
    """An in-memory copy of `_POLICED_REGISTRIES` with a real, unexempted op
    removed from a registry it currently belongs to MUST make the parity
    assertion fail -- proves the gate is capable of failing, not a vacuous
    always-pass check. `_assert_op_parity` takes the registry map as an
    argument, so this exercises the exact code the real leg (test 1) uses."""
    altered = dict(_POLICED_REGISTRIES)
    altered["_DESTRUCTIVE_OPS"] = frozenset(
        op for op in altered["_DESTRUCTIVE_OPS"] if op != OP_WRITE
    )
    assert altered["_DESTRUCTIVE_OPS"] != _POLICED_REGISTRIES["_DESTRUCTIVE_OPS"], (
        "Fixture setup error: removing OP_WRITE from the altered copy did "
        "not change it -- this fixture needs updating."
    )

    try:
        _assert_op_parity(altered, _OP_REGISTRY_EXEMPTIONS, _ALL_OPS, _PARITY_CONTEXT)
    except AssertionError:
        pass
    else:
        raise AssertionError(
            "Non-vacuity failure: removing OP_WRITE (a real shipped op with "
            "no exemption against _DESTRUCTIVE_OPS) from the in-memory "
            "registry copy did not make the parity assertion fail -- the "
            "parity gate is vacuous."
        )


# ---------------------------------------------------------------------------
# Test 7: targeted SDP-ops leg -- pins the EXACT expected disposition
# ---------------------------------------------------------------------------


def test_sdp_ops_are_accounted_in_every_policed_registry() -> None:
    """For each of OP_SDP_LOCK/OP_SDP_UNLOCK plus this phase's own four
    SDP-leg ops, and each policed registry, assert membership or a reasoned
    exemption, AND pin the specific expected disposition per pair -- so a
    future change that silently flips one from member to exempt (or back)
    fails here even if the generic leg (test 1) would somehow still pass.

    The four new ops' `_dispatch_step` rows are pinned True (v1.30 Phase
    134, plan 134-02 discharged that TEMPORARY exemption in the same commit
    that wired arm 6's routing). Their `derive_plan` rows are ALSO now
    pinned True (v1.30 Phase 134, plan 134-03 discharged that TEMPORARY
    exemption in the same commit that taught `derive_plan` to emit the SDP
    leg's six steps, D-06) -- mirroring `("derive_plan", OP_SDP_LOCK)` /
    `("derive_plan", OP_SDP_UNLOCK)`, which plan 134-03 flips from False to
    True in this same commit.
    """
    expected_membership = {
        ("_DESTRUCTIVE_OPS", OP_SDP_LOCK): True,
        ("_DESTRUCTIVE_OPS", OP_SDP_UNLOCK): False,
        ("_MULTI_RUN_OPS", OP_SDP_LOCK): False,
        ("_MULTI_RUN_OPS", OP_SDP_UNLOCK): False,
        ("_SDP_OPS", OP_SDP_LOCK): True,
        ("_SDP_OPS", OP_SDP_UNLOCK): True,
        ("_dispatch_step", OP_SDP_LOCK): True,
        ("_dispatch_step", OP_SDP_UNLOCK): True,
        ("derive_plan", OP_SDP_LOCK): True,
        ("derive_plan", OP_SDP_UNLOCK): True,
        ("_dispatch_multi_run", OP_SDP_LOCK): False,
        ("_dispatch_multi_run", OP_SDP_UNLOCK): False,
    }
    for new_op in (
        OP_WRITE_BASELINE_B,
        OP_WRITE_BASELINE_A,
        OP_WRITE_INHIBITED,
        OP_WRITE_RESTORED,
    ):
        expected_membership[("_DESTRUCTIVE_OPS", new_op)] = True
        expected_membership[("_MULTI_RUN_OPS", new_op)] = False
        expected_membership[("_SDP_OPS", new_op)] = False
        expected_membership[("_SDP_LEG_OPS", new_op)] = True
        expected_membership[("_dispatch_step", new_op)] = True
        expected_membership[("derive_plan", new_op)] = True
        expected_membership[("_dispatch_multi_run", new_op)] = False

    for (registry_name, op), should_be_member in expected_membership.items():
        is_member = op in _POLICED_REGISTRIES[registry_name]
        assert is_member == should_be_member, (
            f"{op!r} membership in {registry_name!r} flipped: expected "
            f"member={should_be_member}, measured member={is_member}"
        )
        if not is_member:
            reason = _OP_REGISTRY_EXEMPTIONS.get((op, registry_name))
            assert reason and reason.strip(), (
                f"{op!r} is absent from {registry_name!r} but carries no "
                "reasoned exemption"
            )

    # In particular, pin LEG-09's asymmetry explicitly:
    assert OP_SDP_LOCK in _POLICED_REGISTRIES["_DESTRUCTIVE_OPS"]
    assert OP_SDP_UNLOCK not in _POLICED_REGISTRIES["_DESTRUCTIVE_OPS"]
