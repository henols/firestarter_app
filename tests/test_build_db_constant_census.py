"""
Tests for a part-specific-constant census over tools/build_db.py's own source
text (Phase 197 Plan 05 -- OVR-06).

OVR-06 asks whether any part-specific constant survives in the generator,
with success satisfied either by an empty answer or by a named list where
every survivor carries a reason no alternative exists. A source-scanning
gate that greps for a specific pattern fails OPEN the moment a rename makes
that pattern match nothing -- this project has already been bitten by that
failure mode. This module instead asserts against a frozen allow-list, and
proves the detector underneath it is capable of failing rather than merely
claimed to cover.

The detector, find_part_specific_constants(), takes the source text and the
allow-list as parameters and reads neither from disk itself -- every test
below can therefore feed it synthetic source and a synthetic allow-list, and
the capable-of-failing and adjacency legs depend on exactly that.

Shape: at most 16 characters, fully matched by up to six ASCII letters, then
two to five ASCII digits, then up to eight further ASCII letters or digits.
That shape admits the literals this phase's earlier plans deleted from this
same file (AT28C16, M2716, FM1608, ...) and excludes what is not a part
number: a pinout key carries an underscore, a URL carries a colon and
slashes, a filename carries a dot, a format specifier does not open with a
letter run followed immediately by digits in this shape.

Coverage:
  TestDetectorCapability
    1. test_planted_literal_is_reported -- a planted part-number-shaped
       literal in synthetic source is reported by the detector, proving it
       is capable of failing rather than merely claimed to cover.
    2. test_allow_list_matches_exactly_not_by_containment -- a synthetic
       literal that contains an allow-listed string as a prefix is still
       reported; matching is exact string equality, never substring
       containment.
    3. test_empty_allow_list_still_reports -- an empty allow-list does not
       make the detector early-return or pass vacuously; it still reports
       a violating literal fed via synthetic source.
    4. test_output_is_ascending_sorted -- the detector's return value is
       sorted ascending for any input order.
  TestRealSourceCensus
    5. test_real_source_matches_frozen_allow_list_exactly -- running the
       detector against the live tools/build_db.py, with an empty
       allow-list, surfaces exactly the survivors frozen in
       tests/golden/build_db_part_specific_constants.json and nothing
       else -- proving the golden is neither stale nor padded.
  TestGoldenContract
    6. test_golden_length_is_exact -- the golden's constants list is pinned
       at an exact count.
    7. test_golden_entries_all_carry_a_reason -- every entry in the golden
       carries a non-empty reason; an entry without one is an undecided
       hole, not a named constant.
"""

import ast
import json
import re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_APP_ROOT = _HERE.parent
_GEN_PATH = _APP_ROOT / "tools" / "build_db.py"
_GOLDEN_PATH = _HERE / "golden" / "build_db_part_specific_constants.json"

_MAX_LEN = 16
_SHAPE = re.compile(r"^[A-Za-z]{1,6}[0-9]{2,5}[A-Za-z0-9]{0,8}$")


def find_part_specific_constants(source_text, allow_list):
    """Ast-walk source_text, collect every string ast.Constant matching the
    module's part-number shape, subtract allow_list by exact string
    equality, and return the remainder as an ascending sorted list.

    Takes the source text and the allow-list as explicit parameters and
    reads nothing else from disk -- the capable-of-failing and adjacency
    tests below depend on being able to feed this synthetic input.
    """
    tree = ast.parse(source_text)
    shaped = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if len(value) <= _MAX_LEN and _SHAPE.match(value):
                shaped.add(value)
    survivors = shaped - set(allow_list)
    return sorted(survivors)


def _load_golden():
    with open(_GOLDEN_PATH, encoding="utf-8") as f:
        return json.load(f)


def _golden_allow_list(golden):
    return {entry["value"] for entry in golden["constants"]}


class TestDetectorCapability:
    def test_planted_literal_is_reported(self):
        source = 'PLANTED_NAMES = {"AT28C16"}\n'
        result = find_part_specific_constants(source, set())
        assert result == ["AT28C16"], result

    def test_allow_list_matches_exactly_not_by_containment(self):
        source = 'PLANTED_NAMES = {"AT28C16EXTRA"}\n'
        result = find_part_specific_constants(source, {"AT28C16"})
        assert result == ["AT28C16EXTRA"], result

    def test_empty_allow_list_still_reports(self):
        source = 'PLANTED_NAMES = {"FM1608"}\n'
        result = find_part_specific_constants(source, set())
        assert result == ["FM1608"], result

    def test_output_is_ascending_sorted(self):
        source = 'PLANTED_NAMES = {"ZEBRA99", "ALPHA12", "MBM27128"}\n'
        result = find_part_specific_constants(source, set())
        assert result == sorted(result), result
        assert result == ["ALPHA12", "MBM27128", "ZEBRA99"], result


class TestRealSourceCensus:
    def test_real_source_matches_frozen_allow_list_exactly(self):
        source = _GEN_PATH.read_text(encoding="utf-8")
        golden = _load_golden()
        survivors = find_part_specific_constants(source, set())
        expected = sorted(_golden_allow_list(golden))
        assert survivors == expected, survivors


class TestGoldenContract:
    def test_golden_length_is_exact(self):
        golden = _load_golden()
        assert len(golden["constants"]) == 1, golden["constants"]

    def test_golden_entries_all_carry_a_reason(self):
        golden = _load_golden()
        for entry in golden["constants"]:
            assert entry.get("reason"), entry
