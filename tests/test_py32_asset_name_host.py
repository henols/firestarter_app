"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 128 Plan 09 -- D-08(b) and D-09.

Requirements: REL-04 (cross-repo binding slice only -- the in-workflow
string-equality and SDK-SHA assertions (Plan 128-06) and the rehearsal-run
observed evidence (Plan 128-10) are the other halves; this module does NOT
close REL-04 on its own -- REL-04 is not marked complete in REQUIREMENTS.md
from this plan).

Decisions covered:
  - D-08(b): the binding that actually connects the two repos lives HERE, in
    `firestarter_app`. The firmware-side check (128-06) is a transcription of
    a value it has no way to verify -- the firmware repo cannot import the
    host package. This module is the exact sibling of the Phase 127 flash-map
    gate (`tests/test_py32_flash_map_host.py`).
  - D-09: a THREE-way equality -- the name CMake actually emits
    (`platform/py32f071/CMakeLists.txt`'s `HEX_FILE`), the literal the
    firmware workflow transcribes (`beta-build.yml`'s REL-04 assertion step),
    and `asset_candidates("py32f071")[0]` -- with a SEPARATE non-vacuity
    assertion per parse, so a rename that makes a regex miss fails loudly
    instead of passing on two empty strings. Binds through
    `tests/fw_presence.py`'s `@requires_fw` / `fw_path()` /
    `MissingScanTargetError`, never a hand-built relative path or a local
    presence proxy. `FW_ABSENT_REASON` is already entry 1 in
    `ALLOWED_SKIP_REASONS` (`tests/test_skip_census.py`) and is RESOLVED --
    confirmed by running that module, not by re-deriving it; no new
    skip-census entry is added here.
  - D-19: this is the phase's SECOND and LAST commit. Every firmware commit
    landed first (128-01..128-08); the recorded HEAD `0de57da` is the tree
    this module's planted-mutation test asserts stays clean
    (`_git_porcelain(FW_ROOT) == ""`, research finding F-16). A dirty
    firmware tree would make this module red for a reason that has nothing
    to do with filenames -- Pitfall 7.

Coverage:
  1. test_cmake_parse_is_non_vacuous -- runs first, parses the real
     CMakeLists, compares nothing.
  2. test_workflow_parse_is_non_vacuous -- the same for the real workflow. A
     separate test from 1, because D-09 requires one non-vacuity assertion
     per parse -- these are two independent regexes.
  3. test_cmake_emitted_name_matches_workflow_literal.
  4. test_workflow_literal_matches_asset_candidates -- also asserts
     `asset_candidates("py32f071")` has length 2 with the `.hex` candidate
     first, so a reordering of the host's preference list is caught rather
     than silently changing which name is bound.
  5. test_all_three_names_are_equal -- the explicit three-way, all three
     guards run first.
  6. test_planted_mutated_cmake_name_is_detected -- the planted-mutation RED.
     Proves the parity check can actually fail, not merely pass by
     construction; the real file's blob SHA and FW_ROOT's porcelain status
     are asserted unchanged afterwards.
  7. test_cmake_parser_empty_input_trips_non_vacuity_guard -- pure RED, no
     firmware sibling needed.
  8. test_workflow_parser_empty_input_trips_non_vacuity_guard -- pure RED.
  9. test_workflow_parser_raises_on_two_distinct_candidates -- proves the
     workflow parser refuses to guess between two candidates rather than
     silently picking one.
  10. test_shape_guard_rejects_whitespace_and_uppercase -- proves the shape
      regex (`^firestarter_[a-z0-9_]+\\.hex$`) does real work beyond a bare
      non-empty check (Pitfall 8).

F-8 ceiling, stated plainly: neither app CI workflow
(`.github/workflows/ci.yml`, `beta-release.yml`) checks out the firmware
sibling, so every `@requires_fw` leg in this module SKIPS in app CI -- only
tests 7-10 above actually run there. This binding is enforced by a local run
(observed PASS-not-SKIP, per T-128-23/A-7) and by developer discipline, NOT
by app CI. Claiming CI enforcement would be false.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from firestarter.firmware import asset_candidates
from tests.fw_presence import FW_ROOT, fw_path, requires_fw

# ---------------------------------------------------------------------------
# D-09 / A-7: the path is resolved through `fw_path()` for each of the two
# firmware-side targets -- a hand-built relative path out of `tests/` is
# deliberately never constructed here. `fw_path` raises
# `MissingScanTargetError` when the sibling repo is present but the named
# file is not, so a later rename of either target becomes a hard failure at
# call time, never a silent skip (research finding A-7: a firmware rename
# previously flipped five gate legs PASS -> SKIP at exit 0 with a false
# "firmware absent" reason). `@requires_fw` -- imported from
# tests/fw_presence.py, which reuses `FW_ABSENT_REASON` -- is the ONLY skip
# marker this module uses, and it fires only when the sibling repo itself is
# genuinely absent (no `../firestarter/.git` marker), never on a
# present-but-renamed scan target.
# ---------------------------------------------------------------------------
_CMAKELISTS = fw_path("platform", "py32f071", "CMakeLists.txt")
_BETA_BUILD = fw_path(".github", "workflows", "beta-build.yml")

# The shape every resolved name must match. Load-bearing beyond a bare
# non-empty check (Pitfall 8): rejects whitespace captures and anything not
# lowercase-underscore, so a parse that half-matched (e.g. trailing
# whitespace from a stray regex group) cannot slip past the guard either.
_NAME_SHAPE_RE = re.compile(r"^firestarter_[a-z0-9_]+\.hex$")


def _parse_emitted_hex_name(text: str) -> str:
    """Return the `firestarter_*.hex` basename CMake actually emits, parsed
    from the `HEX_FILE` assignment in `platform/py32f071/CMakeLists.txt`
    source text. Returns "" on no match -- does not raise here, so the RED
    tests can exercise the non-vacuity guard separately."""
    m = re.search(r'set\(\s*HEX_FILE\s+"[^"\n]*/([^"/]+\.hex)"\s*\)', text)
    return m.group(1) if m else ""


def _parse_workflow_literal(text: str) -> str:
    """Return the transcribed `firestarter_*.hex` literal assigned to the
    REL-04 assertion step's `EXPECTED` shell variable in `beta-build.yml`
    source text.

    Collects ALL `EXPECTED=firestarter_*.hex` matches (there are other
    `EXPECTED=`/`EXPECTED_VERSION=` assignments in this file for the REL-01
    version-string assertion -- that one never assigns a bare
    `firestarter_*.hex` value, so it is not captured here). Raises rather
    than guessing when more than one DISTINCT `firestarter_*.hex` candidate
    matches -- a workflow restructure that introduces a second candidate
    must fail loudly, never have this parser silently pick the first.
    Returns "" when there are zero matches.
    """
    matches = re.findall(r"\bEXPECTED=(firestarter_[A-Za-z0-9_]*\.hex)\b", text)
    distinct = sorted(set(matches))
    if len(distinct) > 1:
        raise AssertionError(
            f"workflow parse found {len(distinct)} distinct "
            f"firestarter_*.hex EXPECTED= candidates: {distinct!r} -- "
            "refusing to guess which one binds REL-04. A workflow "
            "restructure introduced a second candidate; resolve which one "
            "is the real transcription before this parser can proceed."
        )
    return distinct[0] if distinct else ""


def _assert_non_vacuous_name(value: str, source: str) -> None:
    """Non-vacuity guard (research finding A-7), run BEFORE any value is
    compared: a parse that found nothing (or captured whitespace) must be an
    `AssertionError`, never a silent pass -- an empty (or shape-invalid)
    value would make every downstream comparison VACUOUSLY TRUE. The exact
    phrase `vacuously true` is load-bearing: the RED tests match on it."""
    assert value and _NAME_SHAPE_RE.match(value), (
        f"parsed value {value!r} from {source} -- expected a name matching "
        f"^firestarter_[a-z0-9_]+\\.hex$. A parse that found nothing (or "
        "captured whitespace) would make every downstream comparison "
        "vacuously true (research finding A-7)."
    )


def _git_hash_object(path: Path) -> str:
    """Resolve `git` fail-closed and hash-object `path` inside FW_ROOT."""
    git_bin = shutil.which("git")
    assert git_bin is not None, (
        "`git` binary not found on PATH. This must FAIL the suite, never "
        "be silently skipped."
    )
    result = subprocess.run(
        [git_bin, "-C", str(FW_ROOT), "hash-object", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _git_porcelain(path: Path) -> str:
    """Resolve `git` fail-closed and return `git status --porcelain` for
    `path`. Empty output means a clean tree (D-19 / F-16's precondition)."""
    git_bin = shutil.which("git")
    assert git_bin is not None, (
        "`git` binary not found on PATH. This must FAIL the suite, never "
        "be silently skipped."
    )
    result = subprocess.run(
        [git_bin, "-C", str(path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


class TestPy32AssetNameParity:
    """D-09's live legs. Every method carries `@requires_fw`. Every method
    re-reads and re-parses (no caching across tests) and calls the
    non-vacuity guard before comparing anything."""

    @requires_fw
    def test_cmake_parse_is_non_vacuous(self) -> None:
        """Coverage 1 -- runs first and separately, before any value
        comparison."""
        name = _parse_emitted_hex_name(_CMAKELISTS.read_text())
        _assert_non_vacuous_name(name, str(_CMAKELISTS))

    @requires_fw
    def test_workflow_parse_is_non_vacuous(self) -> None:
        """Coverage 2 -- the same for the workflow. Two separate tests,
        because D-09 requires one non-vacuity assertion per parse and these
        are two independent regexes; a single shared guard would leave one
        parse unproven."""
        name = _parse_workflow_literal(_BETA_BUILD.read_text())
        _assert_non_vacuous_name(name, str(_BETA_BUILD))

    @requires_fw
    def test_cmake_emitted_name_matches_workflow_literal(self) -> None:
        """Coverage 3."""
        cmake_name = _parse_emitted_hex_name(_CMAKELISTS.read_text())
        workflow_name = _parse_workflow_literal(_BETA_BUILD.read_text())
        _assert_non_vacuous_name(cmake_name, str(_CMAKELISTS))
        _assert_non_vacuous_name(workflow_name, str(_BETA_BUILD))
        assert cmake_name == workflow_name, (
            f"CMake-emitted name {cmake_name!r} != workflow transcription "
            f"{workflow_name!r}"
        )

    @requires_fw
    def test_workflow_literal_matches_asset_candidates(self) -> None:
        """Coverage 4 -- compares against `asset_candidates("py32f071")[0]`,
        and additionally asserts `asset_candidates("py32f071")` has length 2
        with the `.hex` first, so a reordering of the host's preference list
        is caught rather than silently changing which name is bound."""
        workflow_name = _parse_workflow_literal(_BETA_BUILD.read_text())
        _assert_non_vacuous_name(workflow_name, str(_BETA_BUILD))
        candidates = asset_candidates("py32f071")
        assert len(candidates) == 2, (
            f"expected asset_candidates('py32f071') to have length 2, got "
            f"{len(candidates)}: {candidates!r}"
        )
        assert candidates[0].endswith(".hex"), (
            f"expected the .hex candidate first in asset_candidates('py32f071'), "
            f"got {candidates!r}"
        )
        assert workflow_name == candidates[0], (
            f"workflow transcription {workflow_name!r} != "
            f"asset_candidates('py32f071')[0] {candidates[0]!r}"
        )

    @requires_fw
    def test_all_three_names_are_equal(self) -> None:
        """Coverage 5 -- the explicit three-way, with all three guards run
        first."""
        cmake_name = _parse_emitted_hex_name(_CMAKELISTS.read_text())
        workflow_name = _parse_workflow_literal(_BETA_BUILD.read_text())
        host_name = asset_candidates("py32f071")[0]
        _assert_non_vacuous_name(cmake_name, str(_CMAKELISTS))
        _assert_non_vacuous_name(workflow_name, str(_BETA_BUILD))
        _assert_non_vacuous_name(host_name, "asset_candidates('py32f071')[0]")
        assert cmake_name == workflow_name == host_name, (
            f"three-way parity failed: cmake={cmake_name!r} "
            f"workflow={workflow_name!r} host={host_name!r}"
        )

    @requires_fw
    def test_planted_mutated_cmake_name_is_detected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Coverage 6 -- the planted-mutation RED. Capture `real_path` BEFORE
        any monkeypatch; hash the real file; produce a mutated copy with a
        deliberately different hex basename; write the plant under
        `tmp_path`; monkeypatch this module's own `_CMAKELISTS` constant to
        point at it; assert the three-way comparison now fails; then assert
        the real file's blob SHA is unchanged and the firmware repo's
        porcelain status is still empty -- proving the plant never touched
        the read-only sibling repo.

        Deliberate, stated deviation from the 127 analog: this test lives in
        the `@requires_fw` class, not the no-marker RED class where the 127
        module's equivalent
        (`test_planted_mutated_config_origin_is_detected`) lives. It reads
        the real firmware file and hashes it inside `FW_ROOT`, so without the
        sibling repo it would be a hard error rather than an honest skip --
        and F-8 records that the sibling is always absent in app CI. Marking
        it here is the accurate classification, and it adds no new skip
        reason since `FW_ABSENT_REASON` is already allow-listed. Flagged as a
        surprise in the SUMMARY: the 127 module's equivalent test carries no
        marker and may therefore behave differently in app CI -- a finding
        for Phase 130 to note, not something to fix here.
        """
        real_path = _CMAKELISTS  # captured BEFORE any monkeypatch
        before_blob = _git_hash_object(real_path)
        real_text = real_path.read_text()

        mutated_text = real_text.replace(
            'set(HEX_FILE "${CMAKE_CURRENT_BINARY_DIR}/firestarter_py32f071.hex")',
            'set(HEX_FILE "${CMAKE_CURRENT_BINARY_DIR}/firestarter_mutated999.hex")',
        )
        assert mutated_text != real_text, (
            "planted mutation did not actually differ from the real text "
            "-- the replacement target string was not found (the real "
            "CMakeLists.txt's formatting may have changed)"
        )

        planted_path = tmp_path / "planted-CMakeLists.txt"
        planted_path.write_text(mutated_text)
        monkeypatch.setattr(sys.modules[__name__], "_CMAKELISTS", planted_path)

        cmake_name = _parse_emitted_hex_name(_CMAKELISTS.read_text())
        workflow_name = _parse_workflow_literal(_BETA_BUILD.read_text())
        _assert_non_vacuous_name(cmake_name, str(_CMAKELISTS))
        _assert_non_vacuous_name(workflow_name, str(_BETA_BUILD))
        assert cmake_name != workflow_name, (
            "expected the planted mutation to break parity, but "
            f"{cmake_name!r} == {workflow_name!r}"
        )

        after_blob = _git_hash_object(real_path)
        assert after_blob == before_blob, (
            "the planted mutation touched the REAL CMakeLists.txt -- it "
            "must only ever be written under tmp_path"
        )
        assert _git_porcelain(FW_ROOT) == "", (
            "the firmware repo's working tree is no longer clean after the "
            "planted-copy test -- it is a read-only input to this phase"
        )


class TestPy32AssetNameFailsClosedOnBadInput:
    """The pure RED demonstrations (D-09). None carries `@requires_fw` --
    these need no firmware sibling and are therefore the only legs of this
    module that actually run in app CI (F-8)."""

    def test_cmake_parser_empty_input_trips_non_vacuity_guard(self) -> None:
        """Coverage 7 -- the CMake parser over synthetic text with no
        `HEX_FILE` returns "", and the guard raises with `vacuously true`."""
        name = _parse_emitted_hex_name("cmake_minimum_required(VERSION 3.20)\n")
        assert name == ""
        with pytest.raises(AssertionError, match="vacuously true"):
            _assert_non_vacuous_name(name, "synthetic cmake text")

    def test_workflow_parser_empty_input_trips_non_vacuity_guard(self) -> None:
        """Coverage 8 -- the same for the workflow parser."""
        name = _parse_workflow_literal("name: some workflow\non: push\n")
        assert name == ""
        with pytest.raises(AssertionError, match="vacuously true"):
            _assert_non_vacuous_name(name, "synthetic workflow text")

    def test_workflow_parser_raises_on_two_distinct_candidates(self) -> None:
        """Coverage 9 -- the workflow parser over synthetic text containing
        two DISTINCT candidate names raises, naming both. This is what stops
        the parser from silently picking one when the workflow is
        restructured."""
        text = (
            "step1: EXPECTED=firestarter_py32f071.hex\n"
            "step2: EXPECTED=firestarter_other_board.hex\n"
        )
        with pytest.raises(AssertionError, match="distinct"):
            _parse_workflow_literal(text)

    def test_shape_guard_rejects_whitespace_and_uppercase(self) -> None:
        """Coverage 10 -- the shape guard rejects a captured value with
        surrounding whitespace and one with an uppercase segment, proving
        `^firestarter_[a-z0-9_]+\\.hex$` is doing work beyond a bare
        non-empty check (Pitfall 8: a guard that merely checks `is not None`
        is itself vacuous)."""
        with pytest.raises(AssertionError, match="vacuously true"):
            _assert_non_vacuous_name(
                "firestarter_py32f071.hex ", "synthetic (trailing space)"
            )
        with pytest.raises(AssertionError, match="vacuously true"):
            _assert_non_vacuous_name(
                "firestarter_PY32F071.hex", "synthetic (uppercase)"
            )
