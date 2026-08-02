"""
BASE-03's skip census + D-10's reason allow-list (Phase 123 Plan 09).

**This module carries no skip marker of any kind.** No `pytest.mark.skipif`,
no `pytest.skip()`, no conditional early return that would be reported as a
SKIP by pytest (the one conditional `return` below, in test 1, is a plain
early exit that still reports PASSED -- see that test's docstring). This
module exists to detect a condition (an unrecognised skip reappearing, or a
firmware-absent skip firing while the sibling repo IS present) that would
otherwise silence it, so any real skip path here would be self-defeating.
Mirrors the discipline `tests/test_sdp_bus_config_drift.py` already documents
for `test_bad_pinout_fails_closed_and_writes_nothing`, which "runs
unconditionally (no FW_ABSENT skip) ... since it never touches the committed
header" -- same underlying reason: a test whose job is to prove a gate can
fail must not itself be gateable by the condition it is proving.

**Mechanism.** Every assertion below reads the result of running the FULL
host suite (minus this module) as a **subprocess**
(`[sys.executable, "-m", "pytest", "tests/", "-rs", "-q",
"--ignore=tests/test_skip_census.py"]`), parsing pytest's `-rs` short-summary
skip lines out of captured stdout. This is deliberately NOT a report hook and
NOT an in-process pytest invocation: `tests/fw_presence.py`'s own docstring
records that `FW_REPO_PRESENT`, `FW_ABSENT_REASON` and every `@requires_fw`
skipif binding are frozen at IMPORT time, so an in-process re-run could not
see a different environment even if one existed -- and running the suite
inside itself would recurse without the explicit `--ignore` below, which is
why a cheap `--collect-only` run PROVES that argument took effect before the
expensive full run is even attempted (see `_run_child_suite` below): a
silently-failed deselect would otherwise recurse until the process died, and
the failure mode here is a clear assertion message instead of a
hung/killed process.

**Liveness signal is the collect-only count, NOT the run's final summary
line.** Measured this session: pytest 9.1.1 in this environment
intermittently omits the trailing `"N passed in Xs"` summary line from
captured (non-interactive) stdout under `-q` -- reproduced with and without
`--ignore`, with both file-redirected and piped capture, on both a two-file
and the full ~1145-test selection; the `SKIPPED [...]` short-summary lines
tests 1/2 depend on were NOT affected by this, only the trailing completion
line was. Depending on that line for liveness would make this census exactly
as flaky as D-10 rejects a pinned count for being. `--collect-only -q`'s
per-file `"tests/test_foo.py: N"` counts (summed by
`_parse_collected_count`) were reproducibly identical (1145) across three
repeated invocations and are used instead -- collection succeeding is exactly
the liveness property test 3 needs to prove.

Only ONE full-suite subprocess is ever run per test session (cached via
`functools.lru_cache` in `_run_child_suite`, shared by tests 1-3) -- at
roughly 40-50s, that cost must not be paid more than once per module.

Tests (one named function each):
  1. `test_no_skip_claims_firmware_absent_while_marker_present` -- BASE-03's
     literal assertion: if the sibling repo IS present and any reported skip
     reason equals `FW_ABSENT_REASON`, FAIL naming the offending test
     location(s) and reason.
  2. `test_every_skip_reason_is_allow_listed` -- any reported skip reason not
     recognised by `ALLOWED_SKIP_REASONS` FAILs, naming the offending
     location(s)/reason(s) and pointing at this module as the place to add a
     deliberate entry.
  3. `test_census_child_run_is_live` -- asserts the child run actually
     collected a non-zero number of tests (via the collect-only count, see
     above), so a collection regression cannot silence tests 1 and 2 by
     producing an empty report that vacuously satisfies both (Pitfall 4).
  4. `test_parser_recognises_a_real_skip` -- runs a tiny, throwaway,
     unconditionally-skipped pytest file in `tmp_path` and asserts the SAME
     parser used by tests 1-3 extracts its reason text. Without this, a
     parser that matched nothing would make tests 1 and 2 vacuously green --
     the exact hollow shape this phase exists to remove.
  5. `test_no_pinned_skip_count` -- an executable statement of D-10's intent:
     asserts, by scanning this module's own source text, that no assertion
     compares a total-skip count to an integer literal. D-10 rejected a
     pinned count because the measured census moved from 3 to 0 between two
     sessions on the same tree with no code change (123-RESEARCH.md
     §"Skip census -- measured today") -- a pinned count would be flaky and
     would get bumped reflexively until it meant nothing.

Note for the reader: with a programmer board attached, some tests in this
suite behave differently (a recorded, known environment artifact) -- that
variability is the concrete reason `ALLOWED_SKIP_REASONS` keys on reasons,
not a total count.
"""

from __future__ import annotations

import functools
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tests.fw_presence import FW_ABSENT_REASON, FW_REPO_PRESENT

_APP_DIR = Path(__file__).parent.parent
_THIS_MODULE = "tests/test_skip_census.py"
_IGNORE_ARG = f"--ignore={_THIS_MODULE}"

# ---------------------------------------------------------------------------
# D-10's allow-list. Each entry documents the ONE condition under which that
# reason is legitimate. `FW_ABSENT_REASON` is imported from tests.fw_presence
# rather than re-typed as a literal (so the two constants can never drift);
# the remaining three entries key on the other legitimate skip reasons this
# host suite already carries for reasons unrelated to firmware presence.
# Matching is by PREFIX (`str.startswith`), not exact equality, because two
# of the four embed an interpolated path and cannot be pinned as a literal.
# Adding a fifth legitimate skip reason requires a DELIBERATE edit here --
# that deliberateness is D-10's second purpose (the allow-list doubles as
# documentation of every legitimate skip reason).
# ---------------------------------------------------------------------------
ALLOWED_SKIP_REASONS: frozenset[str] = frozenset(
    {
        # BASE-03's whole assertion: legitimate ONLY when the sibling
        # ../firestarter/.git marker is genuinely absent (test 1 below
        # enforces the "while present" half of that split). Imported, never
        # re-typed as a literal -- tests/fw_presence.py is the ONE canonical
        # source (D-09's rekey of all seven proxy-carrying modules onto it).
        FW_ABSENT_REASON,
        # tests/test_characterization.py's run_firestarter(): legitimate
        # only when the `firestarter` CLI entry point is not found on PATH
        # (e.g. `pip install -e .` was not run in this environment). CI
        # installs the package before running tests, so this should not
        # fire there.
        "firestarter entry point not found on PATH",
        # tests/test_audit_coverage_matrix.py: legitimate only when this
        # sub-repo is checked out standalone with no meta-repo `.planning/`
        # directory one level up (e.g. GitHub Actions cloning only
        # firestarter_app) -- documented in-source as an intentional
        # standalone-CI guard. Path is interpolated, hence a prefix match.
        "meta-repo ledger not available at",
        # tests/test_variant_decode_evidence_stability.py: legitimate only
        # when the meta-repo bench EVIDENCE.json artifact is absent (same
        # standalone-checkout class as the entry above). Path is
        # interpolated, hence a prefix match.
        "EVIDENCE.json not found at",
    }
)


def _reason_is_allowed(reason: str) -> bool:
    return any(reason.startswith(prefix) for prefix in ALLOWED_SKIP_REASONS)


# ---------------------------------------------------------------------------
# Parser A: pytest `-rs` short-summary skip lines look like
#   SKIPPED [1] tests/test_foo.py:12: some reason text
# (verified this session against this project's own pytest/pyproject config;
# this shape was NOT affected by the trailing-summary-line flakiness recorded
# in the module docstring above).
# ---------------------------------------------------------------------------
_SKIP_LINE_PATTERN = re.compile(
    r"^SKIPPED \[\d+\] (?P<location>\S+): (?P<reason>.*)$", re.MULTILINE
)


def _parse_skip_entries(stdout: str) -> tuple[tuple[str, str], ...]:
    """Extract `(location, reason)` pairs from pytest `-rs` stdout."""
    return tuple(
        (m.group("location"), m.group("reason"))
        for m in _SKIP_LINE_PATTERN.finditer(stdout)
    )


# ---------------------------------------------------------------------------
# Parser B: pytest `--collect-only -q` stdout. Two shapes observed depending
# on how many files/tests were collected: a per-FILE count
# ("tests/test_foo.py: 7", pytest's shape for this project's ~90-file "tests/"
# tree) or, for a small collection, one node-id-per-line
# ("tests/test_foo.py::test_bar"). An aggregate "N tests collected in Xs"
# trailer is handled as a last-resort fallback. Summed/counted, never
# compared against a pinned literal (D-10; test 5 asserts that).
# ---------------------------------------------------------------------------
_PER_FILE_COLLECTED_PATTERN = re.compile(r"^(\S+\.py): (\d+)$", re.MULTILINE)
_PER_TEST_NODE_ID_PATTERN = re.compile(r"^\S+\.py::\S+$", re.MULTILINE)
_AGGREGATE_COLLECTED_PATTERN = re.compile(r"(\d+) tests? collected", re.IGNORECASE)


def _parse_collected_count(collect_only_stdout: str) -> int:
    """Parse `--collect-only -q` stdout into a total collected-item count,
    tolerant of the shapes described above."""
    per_file = _PER_FILE_COLLECTED_PATTERN.findall(collect_only_stdout)
    if per_file:
        return sum(int(n) for _f, n in per_file)
    node_ids = _PER_TEST_NODE_ID_PATTERN.findall(collect_only_stdout)
    if node_ids:
        return len(node_ids)
    aggregate = _AGGREGATE_COLLECTED_PATTERN.search(collect_only_stdout)
    if aggregate:
        return int(aggregate.group(1))
    return 0


@dataclass(frozen=True)
class _ChildRunResult:
    stdout: str
    skip_entries: tuple[tuple[str, str], ...]
    total_collected: int


@functools.lru_cache(maxsize=1)
def _run_child_suite() -> _ChildRunResult:
    """Run the full host suite (minus this module) as a subprocess, exactly
    once per test session, and return the parsed result.

    Before paying for the (roughly 40-50s) full run, a cheap `--collect-only`
    run both (a) proves `_IGNORE_ARG` actually took effect -- naming the
    concrete failure mode the module docstring warns about (a silently-failed
    deselect recursing this module into itself until the process died)
    rather than silently trusting the argument was honoured -- and (b)
    supplies the liveness count `test_census_child_run_is_live` needs,
    because the full run's own trailing summary line was measured to be
    unreliable in this environment (module docstring).
    """
    collect = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/",
            "--collect-only",
            "-q",
            _IGNORE_ARG,
        ],
        cwd=str(_APP_DIR),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert _THIS_MODULE not in collect.stdout, (
        f"{_IGNORE_ARG} did not take effect -- {_THIS_MODULE} still appears "
        f"in the child's collected test list, which would recurse this "
        f"module into itself on the full run below:\n{collect.stdout}"
    )
    total_collected = _parse_collected_count(collect.stdout)

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-rs", "-q", _IGNORE_ARG],
        cwd=str(_APP_DIR),
        capture_output=True,
        text=True,
        timeout=180,
    )
    skip_entries = _parse_skip_entries(result.stdout)
    return _ChildRunResult(
        stdout=result.stdout, skip_entries=skip_entries, total_collected=total_collected
    )


# ---------------------------------------------------------------------------
# Test 1: BASE-03's literal assertion
# ---------------------------------------------------------------------------


def test_no_skip_claims_firmware_absent_while_marker_present() -> None:
    """If the sibling `../firestarter/.git` marker IS present, no test in the
    child run may report a skip reason equal to `FW_ABSENT_REASON` -- that
    would be exactly the fail-open proxy behaviour this milestone removed.

    `FW_REPO_PRESENT` / `FW_ABSENT_REASON` are imported from
    `tests.fw_presence` and used for NOTHING else in this module -- the
    census must not inherit any behaviour from that helper beyond reading the
    marker (module docstring's mechanism note).

    When the sibling repo is genuinely absent in this environment, this test
    takes the early `return` below -- a plain conditional exit, NOT a
    `pytest.skip()` call, so pytest still reports this test PASSED, not
    SKIPPED. A firmware-absent skip is honest in that case; it is
    `ALLOWED_SKIP_REASONS` (via `FW_ABSENT_REASON`) and test 2 below that
    would still fail if it appeared unexpectedly.
    """
    if not FW_REPO_PRESENT:
        return
    result = _run_child_suite()
    offenders = [
        (location, reason)
        for location, reason in result.skip_entries
        if reason == FW_ABSENT_REASON
    ]
    assert not offenders, (
        "BASE-03 VIOLATION: the sibling firmware repo IS present, but the "
        f"following test(s) reported a firmware-absent skip anyway: "
        f"{offenders}"
    )


# ---------------------------------------------------------------------------
# Test 2: every reported skip reason is allow-listed
# ---------------------------------------------------------------------------


def test_every_skip_reason_is_allow_listed() -> None:
    """Any skip reason reported by the child run that is not recognised by
    `ALLOWED_SKIP_REASONS` FAILs the census -- an unrecognised skip is
    exactly the "silent, undocumented, new skip" D-10's allow-list exists to
    surface. Add a deliberate, commented entry to `ALLOWED_SKIP_REASONS`
    above if a new skip is genuinely legitimate."""
    result = _run_child_suite()
    unrecognised = [
        (location, reason)
        for location, reason in result.skip_entries
        if not _reason_is_allowed(reason)
    ]
    assert not unrecognised, (
        "found skip reason(s) not recognised by "
        "tests/test_skip_census.py's ALLOWED_SKIP_REASONS -- if this skip is "
        f"genuinely legitimate, add a deliberate commented entry: "
        f"{unrecognised}"
    )


# ---------------------------------------------------------------------------
# Test 3: the census is live
# ---------------------------------------------------------------------------


def test_census_child_run_is_live() -> None:
    """The child run must have actually collected a non-zero number of tests
    -- otherwise a collection regression (e.g. a broken conftest.py) could
    silence tests 1 and 2 by producing an empty report that vacuously
    satisfies both (Pitfall 4 -- the census must assert its own liveness).

    Uses the `--collect-only` count captured by `_run_child_suite`, not the
    full run's trailing summary line -- see the module docstring's
    "Liveness signal" section for why the latter is unreliable here.
    """
    result = _run_child_suite()
    assert result.total_collected > 0, (
        "the child suite's collect-only run reported zero collected tests -- "
        "a collection regression could silence tests 1 and 2 by producing an "
        f"empty report that trivially satisfies both.\nstdout:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Test 4: the parser recognises a real skip
# ---------------------------------------------------------------------------


def test_parser_recognises_a_real_skip(tmp_path: Path) -> None:
    """Run a tiny, throwaway, unconditionally-skipped pytest file in
    `tmp_path` and assert the SAME parser used by tests 1-3
    (`_parse_skip_entries`) extracts its reason. Without this, a parser that
    silently matched nothing would make tests 1 and 2 vacuously green -- the
    exact hollow shape this phase exists to remove."""
    marker_reason = "synthetic probe reason for census parser test"
    probe = tmp_path / "test_probe_skip.py"
    probe.write_text(
        "import pytest\n\n"
        f'@pytest.mark.skip(reason="{marker_reason}")\n'
        "def test_probe() -> None:\n"
        "    assert True\n"
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(probe), "-rs", "-q"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    skip_entries = _parse_skip_entries(result.stdout)
    assert skip_entries, (
        "parser found NO skip entries in a run containing exactly one "
        f"unconditionally-skipped test -- parser is vacuously blind.\n"
        f"stdout:\n{result.stdout}"
    )
    reasons = [reason for _location, reason in skip_entries]
    assert any(marker_reason in reason for reason in reasons), (
        f"parser did not extract the expected reason text ({marker_reason!r}); "
        f"got: {reasons}"
    )


# ---------------------------------------------------------------------------
# Test 5: no pinned skip count (D-10)
# ---------------------------------------------------------------------------

# Deliberately narrow: only flags an equality/inequality comparison between
# something naming "skip" and a bare integer literal, on the SAME source
# line -- the precise "total skips must equal N" shape D-10 rejects. Scoped
# to a single line (never crossing a newline) so it cannot false-positive on
# unrelated nearby code.
#
# Self-match note: this pattern's own raw-string literal below textually
# contains "skip" within 40 characters of a literal "==" (it is, after all, a
# regex FOR that exact shape) -- so scanning this module's raw source text
# unmodified would make this test trip on its own pattern definition, not on
# a real pinned-count assertion. `_source_excluding_own_pattern_definition`
# below excises exactly this definition (identified by unique start/end
# markers, not a line count) before the scan runs.
_PINNED_SKIP_COUNT_PATTERN = re.compile(
    r"skip\w*[^\n]{0,40}(?:==|!=)\s*\d+|(?:==|!=)\s*\d+[^\n]{0,40}skip\w*",
    re.IGNORECASE,
)

_SELF_PATTERN_DEF_START = "_PINNED_SKIP_COUNT_PATTERN = re.compile("
_SELF_PATTERN_DEF_END = "re.IGNORECASE,\n)"


def _source_excluding_own_pattern_definition() -> str:
    """This module's own source text, with `_PINNED_SKIP_COUNT_PATTERN`'s
    definition (identified by the unique start/end marker strings above)
    excised -- see the self-match note above `_PINNED_SKIP_COUNT_PATTERN`."""
    source = Path(__file__).read_text(encoding="utf-8")
    start = source.index(_SELF_PATTERN_DEF_START)
    end = source.index(_SELF_PATTERN_DEF_END, start) + len(_SELF_PATTERN_DEF_END)
    return source[:start] + source[end:]


def test_no_pinned_skip_count() -> None:
    """D-10, as an executable statement of intent: this module must contain
    no assertion comparing a total skip count to an integer literal.

    D-10 rejected a pinned count because the measured census moved from 3 to
    0 between two sessions on the same tree with no code change
    (123-RESEARCH.md §"Skip census -- measured today (BASE-03, D-10)") --
    the 3 residual skips that session saw were themselves known
    environment-dependent artifacts. A pinned count here would be flaky and
    would get bumped reflexively until it meant nothing; `ALLOWED_SKIP_REASONS`
    (tests 1/2 above) is the stable mechanism instead.
    """
    source = _source_excluding_own_pattern_definition()
    matches = _PINNED_SKIP_COUNT_PATTERN.findall(source)
    assert not matches, (
        f"found a pinned-skip-count-shaped comparison in this module: "
        f"{matches} -- D-10 rejected pinning a total skip count; use "
        "ALLOWED_SKIP_REASONS instead"
    )
