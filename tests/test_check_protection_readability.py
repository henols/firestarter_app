"""
Tests for check_protection_readability_invariants.py (Phase 151, GATE-02,
LOCK-01).

This is the mandatory anti-hollow pairing for GATE-02: a checker tool with
no negative-fixture test is exactly the failure mode this project incurred
with v1.12's GATE-03 (a declared-empty detector that could never fail
because nothing concrete was asserted). Every planted-violation test below
injects a REAL subprocess-level violation via the
`FIRESTARTER_PROTECTION_READABILITY_SRC` env-override -- never an
in-process synthetic, never an in-process pytest env-patching fixture --
so a passing test suite proves the checker itself (not the test) fails the
build on a real violation.

Coverage:
  1. Clean-pass baseline: the checker exits 0 on the real, unmodified
     `firestarter/protection_readability.py`.
  2. Non-vacuous by path: the checker's exported default-target constant
     resolves to a file that actually exists on disk.
  3. The clean-pass PASS: line names the resolved target's basename AND a
     binding count for EACH of the two gated names -- not just one.
  4. Class 1 (permit-by-default) planted violation flips the checker
     non-zero and names Class 1.
  5. Class 2 (widenable-token-set) planted violation flips the checker
     non-zero, names Class 2, and names BOTH gated symbols -- proving the
     parameterisation itself is exercised, not only the first name.
  6. The Class 1 fixture's output ALSO reports the bare exception handler,
     proving both halves of Class 1 are individually caught.
  7. Env-override seam sanity: a CLEAN fixture routed through the same seam
     still passes -- isolates legs 4/5/6 as caused by the violations, not
     by the seam itself.
  8. Fail-closed on a missing target path: ERROR: on stderr, non-zero exit.
  9. Fail-closed on a zero-symbol scan, run for BOTH gated names in turn --
     the gate must not vacuously pass when either subject symbol is absent.
  10. Class 3 is weaker, and known to be: a non-literal MECHANISM_BY_TOKEN
      value fails (proving the rule is a real checkable negative), while a
      MECHANISM_BY_TOKEN whose key is absent from both gated sets still
      passes (proving the rule is weaker in exactly the claimed way, not
      accidentally strong).
  11. Class 4 requires AMBIGUOUS_DOC_CITATIONS to be non-empty.

Does NOT modify `tests/test_protection_resolution.py` or
`tests/test_check_sdp_capability.py` -- both stay exactly as they are;
this file adds to the protection-readability test surface, it does not
duplicate either.
"""

import os
import subprocess
import sys
from pathlib import Path

from tools.check_protection_readability_invariants import (
    _DEFAULT_PROTECTION_READABILITY_SRC,
)

# Absolute path to the firestarter_app directory (cwd-independent), mirrors
# tests/test_check_sdp_capability.py:52.
_FA_DIR = Path(__file__).parent.parent

_CLASS1_FIXTURE = (
    _FA_DIR / "tests" / "fixtures" / "planted_protection_permit_by_default.py"
)
_CLASS2_FIXTURE = (
    _FA_DIR / "tests" / "fixtures" / "planted_protection_widenable_tokenset.py"
)


def _run_checker(
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_overrides or {})}
    return subprocess.run(
        [sys.executable, "tools/check_protection_readability_invariants.py"],
        cwd=str(_FA_DIR),
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Leg 1: clean-pass baseline
# ---------------------------------------------------------------------------


def test_checker_exits_zero_on_clean_source() -> None:
    """python tools/check_protection_readability_invariants.py must exit 0
    on the real, unmodified firestarter/protection_readability.py."""
    result = _run_checker()
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on clean source.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout, (
        f"Expected 'PASS:' in output but got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Leg 2: non-vacuous by path
# ---------------------------------------------------------------------------


def test_default_target_resolves_to_an_existing_file() -> None:
    """GATE-02's exported default-target constant must resolve to a file
    that actually exists on disk -- a gate aimed at a missing/renamed path
    passes vacuously, the documented cross-repo hollow-gate failure mode.
    Asserted here as its own leg, not merely inferred from leg 1 passing."""
    assert Path(_DEFAULT_PROTECTION_READABILITY_SRC).is_file(), (
        f"GATE-02's default target {_DEFAULT_PROTECTION_READABILITY_SRC!r} "
        "does not exist on disk -- the gate would be scanning nothing."
    )


# ---------------------------------------------------------------------------
# Leg 3: the PASS line names the scanned file and BOTH binding counts
# ---------------------------------------------------------------------------


def test_pass_line_names_the_scanned_file_and_both_counts() -> None:
    """The clean-pass PASS: line must name protection_readability.py's
    basename AND a binding count for each of the two gated names -- a
    three-axis subject has two gated symbols, and the PASS line must not
    silently report only one of them."""
    result = _run_checker()
    assert result.returncode == 0, (
        f"checker exited {result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "protection_readability.py" in result.stdout, (
        f"Expected the PASS: line to name protection_readability.py but "
        f"got:\n{result.stdout}"
    )
    assert "DOCUMENTED_READABLE_TOKENS=" in result.stdout, (
        f"Expected a binding count for DOCUMENTED_READABLE_TOKENS but got:\n"
        f"{result.stdout}"
    )
    assert "DOCUMENTED_NOT_READABLE_TOKENS=" in result.stdout, (
        f"Expected a binding count for DOCUMENTED_NOT_READABLE_TOKENS but "
        f"got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Leg 4: Class 1 (permit-by-default) planted violation
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_permit_by_default() -> None:
    """A real subprocess-level permit-by-default predicate (Class 1) MUST
    fail the gate."""
    result = _run_checker(
        {"FIRESTARTER_PROTECTION_READABILITY_SRC": str(_CLASS1_FIXTURE)}
    )
    assert result.returncode != 0, (
        f"checker exited 0 on the planted permit-by-default fixture.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "Class 1" in result.stdout


# ---------------------------------------------------------------------------
# Leg 5: Class 2 (widenable-token-set) planted violation, both symbols named
# ---------------------------------------------------------------------------


def test_checker_exits_nonzero_on_planted_widenable_tokenset() -> None:
    """A real subprocess-level widenable-token-set violation (Class 2) MUST
    fail the gate, and the failure output must name BOTH gated symbols --
    the fixture plants one violation on each, exercising the
    parameterisation itself rather than only the first name."""
    result = _run_checker(
        {"FIRESTARTER_PROTECTION_READABILITY_SRC": str(_CLASS2_FIXTURE)}
    )
    assert result.returncode != 0, (
        f"checker exited 0 on the planted widenable-token-set fixture.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "FAIL:" in result.stdout
    assert "Class 2" in result.stdout
    assert "DOCUMENTED_READABLE_TOKENS" in result.stdout
    assert "DOCUMENTED_NOT_READABLE_TOKENS" in result.stdout


# ---------------------------------------------------------------------------
# Leg 6: the Class 1 fixture also reports the bare-except violation
# ---------------------------------------------------------------------------


def test_planted_permit_by_default_also_reports_bare_except() -> None:
    """The Class 1 fixture plants BOTH halves of Class 1 (an unconditional
    class-token return AND a bare `except:`). Both must be individually
    reported, so one violation cannot mask the other."""
    result = _run_checker(
        {"FIRESTARTER_PROTECTION_READABILITY_SRC": str(_CLASS1_FIXTURE)}
    )
    assert result.returncode != 0, (
        f"checker exited 0 on the planted permit-by-default fixture.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "except" in result.stdout.lower(), (
        f"Expected the bare-except violation to also be reported but got:\n"
        f"{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Leg 7: env-override seam sanity (proves the injection path itself works)
# ---------------------------------------------------------------------------


def test_env_override_points_at_a_clean_fixture_still_passes(tmp_path: Path) -> None:
    """A CLEAN fixture injected via the env-override must still pass.

    Proves the env-override seam is a faithful re-target (not itself the
    source of the non-zero exits in legs 4-6) -- both gated token sets
    bound once from a clean literal frozenset, both reporting mappings
    well-formed, and a non-empty ambiguity record, routed through the same
    seam, produces PASS:.
    """
    clean = tmp_path / "planted_clean.py"
    clean.write_text(
        "DOCUMENTED_READABLE_TOKENS = frozenset({'W29C020C'})\n"
        "DOCUMENTED_NOT_READABLE_TOKENS = frozenset({'W29C020'})\n"
        "MECHANISM_BY_TOKEN = {'W29C020C': 'boot_block_lockout'}\n"
        "PERMANENCE_BY_TOKEN = {'W29C020C': 'permanent'}\n"
        "AMBIGUOUS_DOC_CITATIONS = {'W29C020': 'clean control record'}\n"
        "\n"
        "\n"
        "def readability_for_token(token):\n"
        "    if token in DOCUMENTED_READABLE_TOKENS:\n"
        "        return 'documented-readable'\n"
        "    if token in DOCUMENTED_NOT_READABLE_TOKENS:\n"
        "        return 'documented-not-readable'\n"
        "    return 'undocumented'\n"
    )
    result = _run_checker({"FIRESTARTER_PROTECTION_READABILITY_SRC": str(clean)})
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on a clean env-override "
        f"fixture.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout


# ---------------------------------------------------------------------------
# Leg 8: fail-closed on a missing target path
# ---------------------------------------------------------------------------


def test_fail_closed_on_missing_target(tmp_path: Path) -> None:
    """A nonexistent FIRESTARTER_PROTECTION_READABILITY_SRC path must
    ERROR to stderr and exit non-zero -- never a silent PASS."""
    missing = tmp_path / "does-not-exist.py"
    result = _run_checker({"FIRESTARTER_PROTECTION_READABILITY_SRC": str(missing)})
    assert result.returncode != 0, (
        f"checker exited 0 on a missing target path.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "ERROR:" in result.stderr, (
        f"Expected 'ERROR:' on stderr but got:\nstderr:\n{result.stderr}"
    )
    assert str(missing) in result.stderr


# ---------------------------------------------------------------------------
# Leg 9: fail-closed on a zero-symbol scan, for BOTH gated names
# ---------------------------------------------------------------------------


def test_fail_closed_on_zero_symbol_scan_missing_readable_tokens(
    tmp_path: Path,
) -> None:
    """A syntactically valid fixture with NO `DOCUMENTED_READABLE_TOKENS`
    binding at all must still fail the gate -- it must never be reported
    as PASS just because one of the two subject symbols is absent."""
    no_symbol = tmp_path / "planted_no_readable_tokens.py"
    no_symbol.write_text(
        "DOCUMENTED_NOT_READABLE_TOKENS = frozenset({'W29C020'})\n"
        "MECHANISM_BY_TOKEN = {}\n"
        "PERMANENCE_BY_TOKEN = {}\n"
        "AMBIGUOUS_DOC_CITATIONS = {'W29C020': 'x'}\n"
    )
    result = _run_checker({"FIRESTARTER_PROTECTION_READABILITY_SRC": str(no_symbol)})
    assert result.returncode != 0, (
        f"checker exited 0 on a fixture missing DOCUMENTED_READABLE_TOKENS.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" not in result.stdout
    assert "DOCUMENTED_READABLE_TOKENS" in result.stdout


def test_fail_closed_on_zero_symbol_scan_missing_not_readable_tokens(
    tmp_path: Path,
) -> None:
    """The same fail-closed guard, on the OTHER gated name -- neither name
    is the sole guarded symbol."""
    no_symbol = tmp_path / "planted_no_not_readable_tokens.py"
    no_symbol.write_text(
        "DOCUMENTED_READABLE_TOKENS = frozenset({'W29C020C'})\n"
        "MECHANISM_BY_TOKEN = {}\n"
        "PERMANENCE_BY_TOKEN = {}\n"
        "AMBIGUOUS_DOC_CITATIONS = {'W29C020C': 'x'}\n"
    )
    result = _run_checker({"FIRESTARTER_PROTECTION_READABILITY_SRC": str(no_symbol)})
    assert result.returncode != 0, (
        f"checker exited 0 on a fixture missing "
        f"DOCUMENTED_NOT_READABLE_TOKENS.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "PASS:" not in result.stdout
    assert "DOCUMENTED_NOT_READABLE_TOKENS" in result.stdout


# ---------------------------------------------------------------------------
# Leg 10: Class 3 is weaker, and known to be -- proven both directions
# ---------------------------------------------------------------------------


def test_class3_non_literal_mechanism_dict_fails(tmp_path: Path) -> None:
    """A MECHANISM_BY_TOKEN bound from a function CALL (not a literal Dict)
    must fail Class 3 -- proving the weaker rule is still a real checkable
    negative, not a rule that accepts anything."""
    bad = tmp_path / "planted_class3_non_literal.py"
    bad.write_text(
        "DOCUMENTED_READABLE_TOKENS = frozenset({'W29C020C'})\n"
        "DOCUMENTED_NOT_READABLE_TOKENS = frozenset({'W29C020'})\n"
        "\n"
        "\n"
        "def _build():\n"
        "    return {'W29C020C': 'boot_block_lockout'}\n"
        "\n"
        "\n"
        "MECHANISM_BY_TOKEN = _build()\n"
        "PERMANENCE_BY_TOKEN = {'W29C020C': 'permanent'}\n"
        "AMBIGUOUS_DOC_CITATIONS = {'W29C020': 'x'}\n"
    )
    result = _run_checker({"FIRESTARTER_PROTECTION_READABILITY_SRC": str(bad)})
    assert result.returncode != 0, (
        f"checker exited 0 on a MECHANISM_BY_TOKEN bound from a call.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Class 3" in result.stdout


def test_class3_key_absent_from_gated_sets_still_passes(tmp_path: Path) -> None:
    """The deliberate weakening, proven in the direction that matters: a
    MECHANISM_BY_TOKEN whose only key is a token that is a member of
    NEITHER gated token set must still PASS -- Class 3 does not check key
    provenance against the curated sets, and this leg proves that rule is
    weaker in exactly the claimed way rather than accidentally strong."""
    weaker = tmp_path / "planted_class3_weaker_but_valid.py"
    weaker.write_text(
        "DOCUMENTED_READABLE_TOKENS = frozenset({'W29C020C'})\n"
        "DOCUMENTED_NOT_READABLE_TOKENS = frozenset({'W29C020'})\n"
        "MECHANISM_BY_TOKEN = {'SOME_TOKEN_IN_NEITHER_GATED_SET': "
        "'boot_block_lockout'}\n"
        "PERMANENCE_BY_TOKEN = {'W29C020C': 'permanent'}\n"
        "AMBIGUOUS_DOC_CITATIONS = {'W29C020': 'x'}\n"
    )
    result = _run_checker({"FIRESTARTER_PROTECTION_READABILITY_SRC": str(weaker)})
    assert result.returncode == 0, (
        f"checker exited {result.returncode} on a MECHANISM_BY_TOKEN key "
        "absent from both gated sets -- Class 3 should not be checking key "
        f"provenance.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PASS:" in result.stdout


# ---------------------------------------------------------------------------
# Leg 11: Class 4 requires a non-empty ambiguity record
# ---------------------------------------------------------------------------


def test_class4_empty_ambiguous_citations_fails(tmp_path: Path) -> None:
    """An empty AMBIGUOUS_DOC_CITATIONS must fail the gate -- an empty
    record would mean the C-17 documentation disagreement had been
    silently resolved away rather than recorded."""
    empty = tmp_path / "planted_class4_empty.py"
    empty.write_text(
        "DOCUMENTED_READABLE_TOKENS = frozenset({'W29C020C'})\n"
        "DOCUMENTED_NOT_READABLE_TOKENS = frozenset({'W29C020'})\n"
        "MECHANISM_BY_TOKEN = {}\n"
        "PERMANENCE_BY_TOKEN = {}\n"
        "AMBIGUOUS_DOC_CITATIONS = {}\n"
    )
    result = _run_checker({"FIRESTARTER_PROTECTION_READABILITY_SRC": str(empty)})
    assert result.returncode != 0, (
        f"checker exited 0 on an empty AMBIGUOUS_DOC_CITATIONS.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Class 4" in result.stdout
    assert "C-17" in result.stdout
