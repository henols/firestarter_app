"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 148 Plan 08 -- DATA-03, DATA-04 (D-07, D-09, D-16)

Defect class this closes: a coercion layer (string-suffix parsing like
`.replace("V", "")` or a bespoke `_parse_pulse_duration`/`vpp_volts` reader)
or a per-chip guess table keyed on part number, returning under a new name
after the phase that deleted it has shipped. Three such part-number-keyed
guess tables were deliberately deleted in Phase 70 -- the discipline this
module encodes exists precisely because the pattern comes back, usually
under a plausible-looking new identifier, once nobody is watching for it.

Coverage:
  1. test_database_py_contains_no_forbidden_coercion_token -- a whole-file
     scan of firestarter/database.py (732 lines, small and focused) for
     `_parse_pulse_duration`, `.replace("V"` and `vpp_volts`, parametrized
     so a failure names exactly which token was found.
  2. test_audit_coverage_matrix_contains_no_parse_pulse_us -- a whole-file
     scan of tools/audit_coverage_matrix.py (1942 lines -- too large for a
     generic-token scan, but `parse_pulse_us` is a distinctive
     project-local identifier, so scanning for that exact token is safe).
  3. test_page_size_by_part_has_exactly_two_entries -- imports
     tools.build_db and asserts `len(_PAGE_SIZE_BY_PART) == 2` (no source
     parsing needed; the constant is a real dict at import time).
  4. test_build_db_has_no_new_module_level_part_keyed_dict -- an ast walk
     of tools/build_db.py's top-level statements (`tree.body` only, NOT a
     recursive `ast.walk`) collecting every module-level Dict-valued
     Assign/AnnAssign name, asserting the set found today is exactly the
     five names already live in the tree: `_PAGE_SIZE_BY_PART` (the one
     flagged, deliberately-kept part-number-keyed exception -- see its own
     "DO NOT author [ASSUMED] values" comment) plus `PROTOCOL_MAP`,
     `VPP_MV`, `NMOS_TRUE_VPP_MV` and `VCC_VOLTAGES` (the non-part-keyed --
     or, in NMOS_TRUE_VPP_MV's case, already-known-and-cited -- decode
     tables enumerated from the live source, not guessed). Scoping this to
     `tree.body` rather than `ast.walk` is what keeps this test from firing
     on `_AT28C_DIP24_NAMES` (build_db.py:594) -- that set literal (not
     even a Dict) is a LOCAL variable nested inside a `for` loop several
     indent levels deep, addressing a pre-existing, unrelated Phase 76/D-03
     physical-adapter classification, not a module-level construct at all.
     A genuinely new module-level part-keyed dict, by construction, always
     shows up in `tree.body` and this test catches it.
  5. test_scan_helper_detects_planted_forbidden_tokens -- non-vacuity leg:
     drives the exact same `_find_forbidden_tokens` helper tests 1 and 2
     call, against a synthetic source string containing all four forbidden
     tokens, proving the helper is capable of reporting a violation rather
     than only ever having been observed to pass.

Read this before extending: the module-level dict enumeration in test 4 is
a closed list, not a wildcard filter. If a legitimate new module-level dict
is ever added to build_db.py, this test WILL go red -- that is by design
(DATA-04 requires a human decision, not a silent grandfather-in), and the
fix is to add the new name to `_KNOWN_MODULE_LEVEL_DICT_NAMES` in an
explicit, reviewed commit that states why the new dict is not a per-chip
guess table.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_APP_ROOT = _HERE.parent

_DATABASE_PY = _APP_ROOT / "firestarter" / "database.py"
_AUDIT_COVERAGE_MATRIX_PY = _APP_ROOT / "tools" / "audit_coverage_matrix.py"
_BUILD_DB_PY = _APP_ROOT / "tools" / "build_db.py"

# Forbidden tokens naming the deleted coercion layer -- DATA-03's own
# closure requires the deletion be permanent, under any name. Each token is
# distinctive enough that a whole-file scan cannot fail
# vacuously-in-the-other-direction (no unrelated code in either target file
# legitimately uses any of these three exact shapes).
_DATABASE_PY_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "_parse_pulse_duration",
    'replace("V"',
    "vpp_volts",
)

# Distinctive project-local identifier; audit_coverage_matrix.py (1942
# lines) is too large for a generic-token scan, but this exact name cannot
# appear by accident.
_AUDIT_COVERAGE_MATRIX_FORBIDDEN_TOKEN = "parse_pulse_us"

# DATA-04: the module-level dict names live in tools/build_db.py TODAY,
# enumerated from the live source (not guessed). `_PAGE_SIZE_BY_PART` is
# the one flagged, deliberately-kept part-number-keyed exception (PGSZ-01 /
# CR-01, "DO NOT author [ASSUMED] values") -- it must never gain a sibling
# under a different name. The other four are the non-part-keyed (or
# already-reviewed-and-cited) decode tables that legitimately live at
# module level: `PROTOCOL_MAP` (int-keyed protocol_id -> algorithm string),
# `VPP_MV` (int-keyed VPP nibble -> millivolts), `NMOS_TRUE_VPP_MV`
# (string-keyed by part number, but a small, cited, three-entry NMOS VPP
# correction table that predates this phase and is not a per-chip *guess*
# table -- each entry carries a datasheet-sourced VPP override, same
# citation discipline as `_PAGE_SIZE_BY_PART`), and `VCC_VOLTAGES`
# (int-keyed VCC nibble -> millivolts, the very decode table Phase 148
# proved is faithful and does not edit).
_KNOWN_MODULE_LEVEL_DICT_NAMES: frozenset[str] = frozenset(
    {
        "_PAGE_SIZE_BY_PART",
        "PROTOCOL_MAP",
        "VPP_MV",
        "NMOS_TRUE_VPP_MV",
        "VCC_VOLTAGES",
    }
)


def _find_forbidden_tokens(source_text: str, tokens: tuple[str, ...]) -> list[str]:
    """Return the subset of `tokens` present in `source_text`, in the order
    given. Shared by the real-file scans (tests 1/2) and the non-vacuity
    leg (test 5) -- the non-vacuity leg must prove THIS helper can fail,
    not a parallel reimplementation of it."""
    return [token for token in tokens if token in source_text]


def _top_level_dict_constant_names(source_text: str) -> set[str]:
    """ast-parse `source_text` and collect the name of every top-level
    (`tree.body`-only, NOT `ast.walk`) `Assign`/`AnnAssign` whose value is a
    dict literal. Deliberately does NOT recurse into function bodies,
    `for`/`if`/`with` blocks, etc. -- that scoping is exactly what keeps
    this helper from firing on `_AT28C_DIP24_NAMES`, a local variable
    nested inside a `for` loop deep in `main()`, addressing an unrelated
    pre-existing Phase 76/D-03 physical-adapter classification."""
    tree = ast.parse(source_text)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Dict):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


# ---------------------------------------------------------------------------
# Test 1 (DATA-03): the coercion layer stays deleted from database.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    _DATABASE_PY_FORBIDDEN_TOKENS,
    ids=[repr(t) for t in _DATABASE_PY_FORBIDDEN_TOKENS],
)
def test_database_py_contains_no_forbidden_coercion_token(token: str) -> None:
    """Parametrized so a failure names exactly which token returned, rather
    than one monolithic assertion hiding which one."""
    source = _DATABASE_PY.read_text(encoding="utf-8")
    found = _find_forbidden_tokens(source, (token,))
    assert not found, (
        f"firestarter/database.py contains the forbidden token {token!r} -- "
        f"DATA-03 requires the deleted coercion layer to stay deleted under "
        f"any name."
    )


# ---------------------------------------------------------------------------
# Test 2 (DATA-03): parse_pulse_us stays deleted from audit_coverage_matrix.py
# ---------------------------------------------------------------------------


def test_audit_coverage_matrix_contains_no_parse_pulse_us() -> None:
    source = _AUDIT_COVERAGE_MATRIX_PY.read_text(encoding="utf-8")
    found = _find_forbidden_tokens(source, (_AUDIT_COVERAGE_MATRIX_FORBIDDEN_TOKEN,))
    assert not found, (
        "tools/audit_coverage_matrix.py contains the forbidden token "
        f"{_AUDIT_COVERAGE_MATRIX_FORBIDDEN_TOKEN!r} -- DATA-03 requires "
        "parse_pulse_us to stay deleted under any name (Phase 148 Plan 05)."
    )


# ---------------------------------------------------------------------------
# Test 3 (DATA-04): _PAGE_SIZE_BY_PART has exactly 2 entries
# ---------------------------------------------------------------------------


def test_page_size_by_part_has_exactly_two_entries() -> None:
    """Imports the live constant (no network fetch, no source parsing) --
    DATA-04's flagged exception must not silently grow a third sibling
    entry."""
    from tools import build_db

    assert len(build_db._PAGE_SIZE_BY_PART) == 2, (
        "build_db._PAGE_SIZE_BY_PART must have exactly 2 entries "
        f"(PGSZ-01/CR-01); found {sorted(build_db._PAGE_SIZE_BY_PART)}."
    )


# ---------------------------------------------------------------------------
# Test 4 (DATA-04): no new module-level part-keyed dict in build_db.py
# ---------------------------------------------------------------------------


def test_build_db_has_no_new_module_level_part_keyed_dict() -> None:
    source = _BUILD_DB_PY.read_text(encoding="utf-8")
    found = _top_level_dict_constant_names(source)
    unexpected = found - _KNOWN_MODULE_LEVEL_DICT_NAMES
    missing = _KNOWN_MODULE_LEVEL_DICT_NAMES - found
    assert not unexpected, (
        f"tools/build_db.py has a NEW module-level dict constant "
        f"{sorted(unexpected)!r} not in the known set "
        f"{sorted(_KNOWN_MODULE_LEVEL_DICT_NAMES)!r}. A new module-level "
        f"dict in the chip database generator is the exact shape DATA-04 "
        f"and the devtest-rootcause skill forbid: a per-chip lookup table "
        f"keyed on part number, silently substituting for a principled "
        f"decode. Three such tables were deliberately deleted in Phase 70. "
        f"`_PAGE_SIZE_BY_PART` is a flagged, deliberately-kept exception "
        f"and must not be extended or given an undeclared sibling -- if "
        f"{sorted(unexpected)!r} is legitimate, add it to "
        f"_KNOWN_MODULE_LEVEL_DICT_NAMES in an explicit, reviewed commit "
        f"stating why it is not a guess table."
    )
    assert not missing, (
        f"tools/build_db.py is missing previously-known module-level dict "
        f"constant(s) {sorted(missing)!r} -- update "
        f"_KNOWN_MODULE_LEVEL_DICT_NAMES if this is an intentional removal."
    )


# ---------------------------------------------------------------------------
# Test 5: non-vacuity -- the scan helper can actually fail
# ---------------------------------------------------------------------------


def test_scan_helper_detects_planted_forbidden_tokens() -> None:
    """Drives the SAME `_find_forbidden_tokens` helper tests 1 and 2 call,
    against a synthetic source string, proving the helper is capable of
    reporting a violation and is not a vacuous always-pass check."""
    synthetic_source = (
        "def legacy_parse(value):\n"
        '    """Reintroduces the deleted coercion layer under new cover."""\n'
        "    result = _parse_pulse_duration(value)\n"
        '    cleaned = value.replace("V", "")\n'
        "    vpp_volts = float(cleaned)\n"
        "    audit_val = parse_pulse_us(vpp_volts)\n"
        "    return result, vpp_volts, audit_val\n"
    )
    all_tokens = _DATABASE_PY_FORBIDDEN_TOKENS + (
        _AUDIT_COVERAGE_MATRIX_FORBIDDEN_TOKEN,
    )
    found = _find_forbidden_tokens(synthetic_source, all_tokens)
    assert found == list(all_tokens), (
        f"non-vacuity leg failed: _find_forbidden_tokens should have "
        f"reported all four planted tokens {list(all_tokens)!r}, got "
        f"{found!r} -- the scan helper itself is not capable of failing, "
        f"which means tests 1 and 2 above prove nothing."
    )


def test_dict_scan_helper_detects_planted_module_level_dict() -> None:
    """Second non-vacuity leg, for test 4's helper: a synthetic module
    source with a new top-level part-keyed dict must be reported as
    `unexpected` by `_top_level_dict_constant_names`, proving that helper
    can fail too."""
    synthetic_source = (
        "PROTOCOL_MAP = {1: 'x'}\n_FOO_BY_PART: dict = {\n    \"W29C040\": 1,\n}\n"
    )
    found = _top_level_dict_constant_names(synthetic_source)
    assert "_FOO_BY_PART" in found, (
        "non-vacuity leg failed: _top_level_dict_constant_names should "
        "have found the planted '_FOO_BY_PART' module-level dict, got "
        f"{found!r} -- the AST-walk helper itself is not capable of "
        "failing, which means test 4 above proves nothing."
    )
    unexpected = found - _KNOWN_MODULE_LEVEL_DICT_NAMES
    assert "_FOO_BY_PART" in unexpected
