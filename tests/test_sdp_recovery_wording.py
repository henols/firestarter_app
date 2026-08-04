"""LEG-14's committed gate: a SCOPED pytest over named SDP recovery
constants (v1.30 Phase 134, plan 134-09, D-13).

Standalone in CI: no firmware-sibling marker, no test-capture-library
fixtures, no network, no hardware.

Why this cannot be a whole-report grep for the forbidden bulk-clear word
(measured, D-13): the report legitimately contains it in at least three
places --
  1. `derive_plan`'s protocol-0x0D NA reason (`chip_test.py:670-673`):
     "protocol 0x0D (28C family) has no erase operation; each page write
     auto-erases internally";
  2. the shipped single-word op string itself (`OP_ERASE = "erase"`,
     `chip_test.py:336`), reaching both the markdown table and the JSON
     block;
  3. `_ALWAYS_WRITES_NOTICE`'s own step enumeration ("the shipped
     write/verify/erase steps write twice per invocation") --
     `cli_handlers.py`.
A literal whole-report grep would go RED on all three, on correct text, and
would need exemptions on day one -- the identical shape to Phase 133's
D-14 `_sample` problem, where a new deny-rule fired on pre-existing clean
code.

So this module scans EXACTLY `firestarter.cli_handlers.
SDP_RECOVERY_CONSTANT_NAMES` (today: `_SDP_RECOVERY_LOUD`,
`_SDP_RECOVERY_NEUTRAL`) for all three rules below. `_ALWAYS_WRITES_NOTICE`
is checked SEPARATELY and ONLY for rule 1 (the "rewrite" recovery word) --
never for rules 2/3 -- because it is one of the three legitimate
"erase"-carrying exemplars named above; running the forbidden-word/
hyphenated-op rules against it would re-plant exactly the trap this
module exists to avoid.

Hand-off to Phase 137 (D-13): CLOSE-03's `tools/check_*.py` string-literal
scanner should EXTEND `SDP_RECOVERY_CONSTANT_NAMES` rather than duplicate
this pytest module. That scanner is explicitly NOT authored here.
"""

from __future__ import annotations

import re

import pytest

from firestarter import cli_handlers
from firestarter.chip_test import _SDP_LEG_OPS, _SDP_OPS

# ---------------------------------------------------------------------------
# Vocabulary constants
# ---------------------------------------------------------------------------

_REQUIRED_RECOVERY_WORD = "rewrite"

# The five-letter word for bulk clearing. Protocol 0x0D (the 28C family
# this leg targets) has NO bulk-clear operation at all -- naming one in
# recovery advice is not a style issue, it is actively wrong advice to a
# user holding a locked part.
_FORBIDDEN_RECOVERY_WORD = "erase"

# `_ALWAYS_WRITES_NOTICE` is scanned separately (see module docstring) --
# rule 1 only, never rules 2/3.
_RECOVERY_WORD_ONLY_CONSTANT_NAME = "_ALWAYS_WRITES_NOTICE"

# A silent shrink of `SDP_RECOVERY_CONSTANT_NAMES` below this floor is
# exactly the tampering T-134-36 names -- caught by
# `test_recovery_constant_count_has_not_silently_shrunk` below.
_MIN_RECOVERY_CONSTANT_COUNT = 2


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def _scan_recovery_constants(named_values: dict[str, str]) -> None:
    """LEG-14's gate. For each `(name, value)` in `named_values`, assert:

    1. `value` contains `_REQUIRED_RECOVERY_WORD` (case-insensitive) --
       the aborted-run recovery must be given in the word "rewrite";
    2. `value` does NOT contain `_FORBIDDEN_RECOVERY_WORD` (case-
       insensitive) as a standalone word -- protocol 0x0D has no
       bulk-clear operation, so naming one is wrong advice;
    3. `value` contains no hyphenated op literal from
       `chip_test._SDP_LEG_OPS | chip_test._SDP_OPS` as a substring --
       folds RESEARCH OQ-5's hazard into the same gate.

    FAILS CLOSED on a zero-symbol scan: an empty mapping raises rather
    than silently reporting clean -- exactly the unreachable-green shape
    this project has shipped twice (v1.23 P129/P130).

    Raises `AssertionError` naming EVERY offending `(name, rule)` pair,
    not just the first -- mirrors `_assert_op_parity`'s aggregate-then-
    raise shape (`tests/test_op_registration_parity.py`).

    Callers pass ONLY the named recovery constants here (`_real_scan_
    target()` below) -- never `_ALWAYS_WRITES_NOTICE`, which is checked
    separately for rule 1 alone (module docstring).
    """
    if not named_values:
        raise AssertionError(
            "zero-symbol scan: _scan_recovery_constants was called with "
            "an empty mapping -- a gate that scans nothing and exits "
            "clean is worse than no gate (the v1.23 P129/P130 "
            "unreachable-green shape)."
        )

    forbidden_ops = _SDP_LEG_OPS | _SDP_OPS
    problems: list[str] = []
    for name, value in named_values.items():
        lowered = value.lower()
        if _REQUIRED_RECOVERY_WORD not in lowered:
            problems.append(
                f"{name!r}: missing required recovery word {_REQUIRED_RECOVERY_WORD!r}"
            )
        if re.search(rf"\b{_FORBIDDEN_RECOVERY_WORD}\b", lowered):
            problems.append(
                f"{name!r}: contains forbidden bulk-clear word "
                f"{_FORBIDDEN_RECOVERY_WORD!r} -- protocol 0x0D has no "
                "bulk-clear operation; this is wrong advice"
            )
        hit_ops = sorted(op for op in forbidden_ops if op in lowered)
        if hit_ops:
            problems.append(f"{name!r}: contains hyphenated op literal(s) {hit_ops!r}")

    if problems:
        raise AssertionError(
            "LEG-14 recovery-wording scan found violation(s):\n"
            + "\n".join(f"  - {p}" for p in problems)
        )


def _resolve_named_constants(names: tuple[str, ...]) -> dict[str, str]:
    """Resolve each name in `names` to its live `str` attribute on
    `firestarter.cli_handlers`.

    Deliberately no default on `getattr` -- resolving a name that does
    not exist on the module MUST raise `AttributeError`, so a future
    rename cannot silently empty the scan set (T-134-36).
    """
    return {name: getattr(cli_handlers, name) for name in names}


def _real_scan_target() -> dict[str, str]:
    """The real, unmodified `SDP_RECOVERY_CONSTANT_NAMES` mapping this
    gate scans in production -- exactly the two named recovery
    constants, never `_ALWAYS_WRITES_NOTICE` (module docstring)."""
    return _resolve_named_constants(cli_handlers.SDP_RECOVERY_CONSTANT_NAMES)


# ---------------------------------------------------------------------------
# Test 1: the positive control -- MUST run first (see docstring)
# ---------------------------------------------------------------------------


def test_positive_control_real_constants_do_not_raise() -> None:
    """The gate over the REAL, unmodified `SDP_RECOVERY_CONSTANT_NAMES`
    constants must NOT raise.

    Opens this module deliberately (must be the first `def test_` in the
    file): without this positive control, every planted-violation leg
    below could pass vacuously by having `_scan_recovery_constants`
    always raise regardless of input -- proving nothing about the gate's
    actual discriminating power (`test_op_registration_parity.py`'s
    `test_exemption_empty_reason_fails` states the same reasoning for its
    own opening positive control).
    """
    _scan_recovery_constants(_real_scan_target())


# ---------------------------------------------------------------------------
# Test 2: _ALWAYS_WRITES_NOTICE -- rule 1 only, checked separately
# ---------------------------------------------------------------------------


def test_always_writes_notice_contains_required_recovery_word() -> None:
    """Rule 1 (the "rewrite" recovery word) applies to
    `_ALWAYS_WRITES_NOTICE` too -- but rules 2/3 deliberately do NOT
    (module docstring): this constant's own step enumeration legitimately
    contains the forbidden bulk-clear word ("the shipped write/verify/
    erase steps"), one of D-13's own three named exemplars. Running the
    full three-rule scan against it here would re-plant the exact trap
    this module exists to avoid.
    """
    value = getattr(cli_handlers, _RECOVERY_WORD_ONLY_CONSTANT_NAME)
    assert _REQUIRED_RECOVERY_WORD in value.lower(), (
        f"{_RECOVERY_WORD_ONLY_CONSTANT_NAME!r} must contain the required "
        f"recovery word {_REQUIRED_RECOVERY_WORD!r}"
    )


# ---------------------------------------------------------------------------
# Test 3/4: fail-closed legs (mirrors test_check_sdp_capability.py)
# ---------------------------------------------------------------------------


def test_fail_closed_on_zero_symbol_scan() -> None:
    """A zero-symbol scan (empty mapping) must RAISE, never pass. A gate
    that scans nothing and exits clean is exactly the defect this project
    shipped in v1.23 P129/P130."""
    with pytest.raises(AssertionError):
        _scan_recovery_constants({})


def test_fail_closed_on_missing_constant_name() -> None:
    """Resolving a name that does not exist on `firestarter.cli_handlers`
    must raise, so a future rename cannot silently empty the scan set."""
    with pytest.raises(AttributeError):
        _resolve_named_constants(("_DOES_NOT_EXIST_ON_CLI_HANDLERS",))


# ---------------------------------------------------------------------------
# Test 5/6: target-resolution legs
# ---------------------------------------------------------------------------


def test_recovery_constant_names_non_empty_and_resolve_to_non_empty_strings() -> None:
    """`SDP_RECOVERY_CONSTANT_NAMES` must be non-empty, and every name in
    it must resolve to a non-empty `str` attribute on
    `firestarter.cli_handlers`."""
    assert cli_handlers.SDP_RECOVERY_CONSTANT_NAMES, (
        "SDP_RECOVERY_CONSTANT_NAMES must not be empty -- an empty tuple "
        "would make this gate scan nothing."
    )
    for name in cli_handlers.SDP_RECOVERY_CONSTANT_NAMES:
        value = getattr(cli_handlers, name)
        assert isinstance(value, str) and value.strip(), (
            f"{name!r} must resolve to a non-empty str attribute on "
            "firestarter.cli_handlers"
        )


def test_recovery_constant_count_has_not_silently_shrunk() -> None:
    """A silent shrink of `SDP_RECOVERY_CONSTANT_NAMES` (e.g. a rename
    dropping one of the two forms) must be caught by a minimum-count
    assertion, not discovered later."""
    assert (
        len(cli_handlers.SDP_RECOVERY_CONSTANT_NAMES) >= _MIN_RECOVERY_CONSTANT_COUNT
    ), (
        f"SDP_RECOVERY_CONSTANT_NAMES shrank below "
        f"{_MIN_RECOVERY_CONSTANT_COUNT} members -- a silent rename/"
        "removal would otherwise narrow LEG-14's scan set unnoticed."
    )
