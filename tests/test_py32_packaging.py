"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

This module carries three independent, non-vacuous, fail-closed-proven gate
families, kept together because each is a small textual source-scan over a
file that a future refactor could silently drift without any test noticing:

  1. **Packaging** (Plan 127-02 -- HOST-07 / D-19): the `[py32]` extra's
     pyusb floor, raised to `>=1.3.1,<2`.
  2. **The D-17 deviation record** (Plan 127-02 -- HOST-01): the
     accepted-deviation comment Plan 127-01 Task 2 recorded at
     `flash_method()`.
  3. **Documentation parity** (Plan 127-10 -- D-15 / D-13): the install
     doc's flash-map figure and pyusb floor must not silently outlive the
     host constants and `pyproject.toml` entry they describe -- Phase 129
     is expected to move the flash map, and this is what turns that move
     into a red test here instead of a stale doc there.

**Why a regex scan, not a TOML parse.** tomllib is py3.11+ and this
project's declared floor is py3.9 (ruff target-version = "py39", mypy
python_version = "3.9"); tomli is not a dependency this project carries and
this plan adds none. tests/test_revision_constants_parity.py already scans
source text for exactly this class of gate -- a #define extractor over a C
header -- so a regex scan over pyproject.toml's `py32 = [` block follows the
same repo idiom rather than introducing a new dependency for one file.

**Non-vacuity (research finding A-7).** A scan that finds nothing must
never read as a pass. Every gate below asserts its scan target was located
at all -- the `py32 = [` block, `def flash_method(`, and the install doc's
§3 heading -- before comparing anything the scan found. Each gate's
assertion body is factored into a helper the real leg and a fail-closed
planted-file leg both call, the same shape tests/test_revision_constants_parity.py
uses for its own header-reading gate: the fail-closed legs monkeypatch this
module's path constant (`_PYPROJECT` / `_FIRMWARE_PY` / `_INSTALL_DOC`)
before invoking the shared helper, proving the gate can genuinely fail
rather than only by inspection.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from firestarter import py32_dfu

_APP_DIR = Path(__file__).parent.parent
_PYPROJECT = _APP_DIR / "pyproject.toml"
_FIRMWARE_PY = _APP_DIR / "firestarter" / "firmware.py"
_INSTALL_DOC = _APP_DIR / "doc" / "PY32F071-FIRMWARE-INSTALL.md"

# D-19: pyusb 1.3.1 is the current release at plan time (Requires-Python
# >=3.9.0, satisfiable on this project's py39 floor); <2 refuses a future
# major that could reorder ctrl_transfer's parameters. Written
# independently of pyproject.toml -- never derived from the file under test.
_EXPECTED_PYUSB_SPEC = "pyusb>=1.3.1,<2"

_PY32_BLOCK_RE = re.compile(r"^py32\s*=\s*\[(.*?)\]", re.DOTALL | re.MULTILINE)
_TEST_BLOCK_RE = re.compile(r"^test\s*=\s*\[(.*?)\]", re.DOTALL | re.MULTILINE)
_REQUIREMENT_RE = re.compile(r'"([^"]*)"')

# D-17's five phrases (HOST-01) -- "accepted deviation" doubles as the
# proximity-checked phrase below.
_D17_PHRASES = (
    "accepted deviation",
    "D-17",
    "HOST-01",
    "_install_with_avrdude",
    "avrdude-mcu-detection-fallback",
)
_D17_PROXIMITY_PHRASE = "accepted deviation"
_FLASH_METHOD_DEF = "def flash_method("
_D17_PROXIMITY_LINES = 25

# D-15: the install doc's non-vacuity anchor -- the same
# section header the doc's own text calls "§3".
_INSTALL_DOC_SECTION_3_HEADING = "## 3. What the host does during an install"

# the doc must name all three non-VERIFIED outcomes using the
# exact words the flasher/CLI actually print (or the flasher's own attribute
# name), so a future edit cannot quietly drop the honest half of the
# install's outcome vocabulary.
_READBACK_OUTCOME_PHRASES = (
    "bitCanUpload",
    "load address not under host control",
    "written but NOT verified",
)


def _py32_extra_requirements(text: str) -> list[str]:
    """Extract the quoted requirement strings inside the `py32 = [ ... ]`
    block. Returns an empty list -- never raises -- when the block is
    absent, so the non-vacuity assertion in each caller is the thing that
    reports the failure, not a stack trace out of this helper."""
    match = _PY32_BLOCK_RE.search(text)
    if match is None:
        return []
    return _REQUIREMENT_RE.findall(match.group(1))


def _read_py32_requirements() -> list[str]:
    """Read `_PYPROJECT` (a module global, monkeypatchable) and return the
    non-vacuity-checked list of py32 extra requirement strings.

    Raises `AssertionError` if the `py32 = [` block is absent or empty --
    the fail-closed planted-file leg below exercises exactly this raise by
    monkeypatching `_PYPROJECT` before calling this same helper, so the
    real gate leg and the fail-closed leg share one code path."""
    text = _PYPROJECT.read_text(encoding="utf-8")
    requirements = _py32_extra_requirements(text)
    assert requirements, (
        f"'py32 = [' block not found (or found empty) in {_PYPROJECT} -- "
        "every downstream assertion about its contents would be vacuously "
        "true (research finding A-7)"
    )
    return requirements


def _read_d17_record() -> None:
    """Read `_FIRMWARE_PY` (a module global, monkeypatchable) and assert
    D-17's accepted-deviation record is present and held in proximity to
    `def flash_method(`.

    Raises `AssertionError` on any of: the function not being located at
    all (non-vacuity), a missing phrase, or the 'accepted deviation' phrase
    drifting more than `_D17_PROXIMITY_LINES` lines away from the function
    it describes. The fail-closed planted-file leg below monkeypatches
    `_FIRMWARE_PY` before calling this same helper."""
    text = _FIRMWARE_PY.read_text(encoding="utf-8")
    lines = text.splitlines()

    def_idx = next(
        (i for i, line in enumerate(lines) if _FLASH_METHOD_DEF in line), None
    )
    assert def_idx is not None, (
        f"{_FLASH_METHOD_DEF!r} not found in {_FIRMWARE_PY} -- the "
        "proximity assertion below would be vacuously true against a "
        "function that was never located (research finding A-7)"
    )

    missing = [phrase for phrase in _D17_PHRASES if phrase not in text]
    assert not missing, (
        f"D-17's accepted-deviation record is missing phrase(s) {missing!r} "
        f"in {_FIRMWARE_PY}"
    )

    window_start = max(0, def_idx - _D17_PROXIMITY_LINES)
    window = "\n".join(lines[window_start:def_idx])
    assert _D17_PROXIMITY_PHRASE in window, (
        f"{_D17_PROXIMITY_PHRASE!r} phrase found somewhere in the file but "
        f"not within {_D17_PROXIMITY_LINES} lines preceding "
        f"{_FLASH_METHOD_DEF!r} -- the record must not drift away from the "
        "function it describes"
    )


def test_py32_block_is_non_vacuous() -> None:
    """`_py32_extra_requirements` over the real pyproject.toml must return
    a non-empty list -- otherwise the floor-equality gate below would be
    comparing against nothing (research finding A-7)."""
    requirements = _read_py32_requirements()
    assert requirements


def test_py32_floor_is_exactly_the_expected_spec() -> None:
    """A silent revert to pyusb>=1.2.1, a dropped upper bound, or an added
    second requirement in the py32 extra must all fail this gate."""
    requirements = _read_py32_requirements()
    assert requirements == [_EXPECTED_PYUSB_SPEC], (
        f"py32 extra requirements {requirements!r} != [{_EXPECTED_PYUSB_SPEC!r}]"
    )


def test_pyusb_absent_from_the_test_extra() -> None:
    """D-02's two-leg design as an assertion: if pyusb ever migrates into
    the primary `test` extra, the entire pyusb-absent proof set (Plan
    127-07) becomes vacuous with nothing going red on its own."""
    text = _PYPROJECT.read_text(encoding="utf-8")
    match = _TEST_BLOCK_RE.search(text)
    assert match is not None, f"'test = [' block not found in {_PYPROJECT}"
    test_requirements = _REQUIREMENT_RE.findall(match.group(1))
    assert not any(req.lower().startswith("pyusb") for req in test_requirements), (
        f"pyusb found in the primary test extra: {test_requirements!r} -- "
        "this would make the entire pyusb-absent proof set (Plan 127-07) "
        "vacuous with nothing going red"
    )


def test_py32_gate_fails_closed_on_a_planted_file_with_no_py32_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed RED demonstration: point `_PYPROJECT` at a planted file
    containing no `py32 = [` block and assert the shared helper raises
    rather than silently passing on an empty match set (research finding
    A-7)."""
    planted = tmp_path / "pyproject.toml"
    planted.write_text('[project]\nname = "x"\n', encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_PYPROJECT", planted)
    with pytest.raises(AssertionError, match=r"py32 = \["):
        _read_py32_requirements()


def test_d17_record_phrases_present_and_proximate_to_flash_method() -> None:
    """D-17's five phrases must all be present in firmware.py, and the
    'accepted deviation' phrase must occur within `_D17_PROXIMITY_LINES`
    lines preceding `def flash_method(` -- so the record cannot drift away
    from the function it describes."""
    _read_d17_record()


def test_d17_gate_fails_closed_on_a_planted_file_lacking_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed RED demonstration: point `_FIRMWARE_PY` at a planted
    file that defines `flash_method` but carries none of D-17's phrases,
    and assert the shared helper raises."""
    planted = tmp_path / "firmware.py"
    planted.write_text(
        "def flash_method(board):\n    return 'avrdude'\n", encoding="utf-8"
    )
    monkeypatch.setattr(sys.modules[__name__], "_FIRMWARE_PY", planted)
    with pytest.raises(AssertionError):
        _read_d17_record()


# --------------------------------------------------------------------------
# D-15, D-13: doc-vs-constant parity gate. The install doc's
# flash-map figure and pyusb floor must not be able to disagree with the
# code they describe without a red test here -- including when Phase 129
# moves the map.
# --------------------------------------------------------------------------


def _read_install_doc() -> str:
    """Read `_INSTALL_DOC` (a module global, monkeypatchable) and return its
    text, non-vacuity-checked against the presence of the doc's own §3
    heading.

    Raises `AssertionError` if the file is absent, empty, or missing the §3
    heading -- the fail-closed planted-file leg below exercises exactly
    this raise, so the real gate leg and the fail-closed leg share one code
    path (research finding A-7)."""
    assert _INSTALL_DOC.exists(), (
        f"{_INSTALL_DOC} does not exist -- every downstream assertion about "
        "its contents would be vacuously true (research finding A-7)"
    )
    text = _INSTALL_DOC.read_text(encoding="utf-8")
    assert text, f"{_INSTALL_DOC} is empty"
    assert _INSTALL_DOC_SECTION_3_HEADING in text, (
        f"{_INSTALL_DOC_SECTION_3_HEADING!r} not found in {_INSTALL_DOC} -- "
        "the address/outcome assertions below would be vacuously true "
        "against a doc section that was never actually located "
        "(research finding A-7)"
    )
    return text


def _assert_doc_states_app_region_end() -> None:
    """Assert the install doc's application-region-end figure matches
    `py32_dfu.APP_REGION_END` exactly. The expectation is built with
    `f"0x{...:08X}"` rather than written as a literal, so this follows the
    constant automatically when Phase 129 moves the map."""
    text = _read_install_doc()
    expected = f"0x{py32_dfu.APP_REGION_END:08X}"
    assert expected in text, (
        f"doc figure and host constant have diverged: expected the "
        f"application-region-end figure {expected!r} "
        f"(py32_dfu.APP_REGION_END == 0x{py32_dfu.APP_REGION_END:08X}) inside "
        f"{_INSTALL_DOC}, but it was not found"
    )


def _assert_doc_states_flash_base() -> None:
    """Assert the install doc's flash-base figure matches
    `py32_dfu.FLASH_BASE` exactly, built the same way as
    `_assert_doc_states_app_region_end`."""
    text = _read_install_doc()
    expected = f"0x{py32_dfu.FLASH_BASE:08X}"
    assert expected in text, (
        f"doc figure and host constant have diverged: expected the flash "
        f"base figure {expected!r} (py32_dfu.FLASH_BASE == "
        f"0x{py32_dfu.FLASH_BASE:08X}) inside {_INSTALL_DOC}, but it was "
        "not found"
    )


def test_install_doc_is_non_vacuous() -> None:
    """`_read_install_doc` over the real install doc must return non-empty
    text containing the §3 heading -- otherwise every parity assertion
    below would be comparing against nothing (research finding A-7)."""
    text = _read_install_doc()
    assert text


def test_install_doc_app_region_end_matches_host_constant() -> None:
    """The doc's application-region-end figure (§3 step 5) must not
    silently outlive `py32_dfu.APP_REGION_END` -- a Phase-129 map move
    turns this red instead of leaving the doc stale."""
    _assert_doc_states_app_region_end()


def test_install_doc_flash_base_matches_host_constant() -> None:
    """The doc's flash-base figure must likewise track
    `py32_dfu.FLASH_BASE`."""
    _assert_doc_states_flash_base()


def test_install_doc_documents_all_three_readback_outcomes() -> None:
    """All three non-VERIFIED readback outcomes must be named in the doc
    using the words the flasher/CLI actually use, so a future edit cannot
    quietly drop the honest half of the install's outcome vocabulary."""
    text = _read_install_doc()
    missing = [phrase for phrase in _READBACK_OUTCOME_PHRASES if phrase not in text]
    assert not missing, (
        f"install doc is missing readback outcome phrase(s) {missing!r} in "
        f"{_INSTALL_DOC}"
    )


def test_install_doc_pyusb_floor_matches_pyproject() -> None:
    """The `[py32]` extra's requirement string(s) in `pyproject.toml` must
    also appear in the install doc -- one comparison, both sources, so the
    two cannot drift apart."""
    requirements = _read_py32_requirements()
    text = _read_install_doc()
    for requirement in requirements:
        assert requirement in text, (
            f"pyproject.toml's py32 extra requirement {requirement!r} is "
            f"not stated in {_INSTALL_DOC} -- the doc and pyproject.toml "
            "have drifted"
        )


def test_install_doc_address_parity_fails_closed_on_a_planted_file_missing_the_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed RED demonstration: point `_INSTALL_DOC` at a planted file
    that carries the §3 heading (so non-vacuity passes) but lacks the
    application-region-end figure, and assert the parity helper raises."""
    planted = tmp_path / "PY32F071-FIRMWARE-INSTALL.md"
    planted.write_text(
        f"{_INSTALL_DOC_SECTION_3_HEADING}\nNo address in this planted file.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "_INSTALL_DOC", planted)
    with pytest.raises(AssertionError):
        _assert_doc_states_app_region_end()
