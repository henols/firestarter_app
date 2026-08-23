"""
Parity gate closing RESEARCH F6's `EEPROM_SDP_DISABLE` transcription gap
(Phase 116 Open Question 3).

`EEPROM_SDP_DISABLE` (firestarter/src/proms/eeprom_28c.cpp) is `const` at
namespace scope in a .cpp -- internal linkage, so no test translation unit can
link it directly. `flash_utils.h`'s `FLASH_DISABLE_WRITE_PROTECTION` declares
the identical six {address, byte} pairs in a *header*, so the firmware trace
suites can #include and drive that copy directly with zero transcription.
This module guards the one remaining risk: that the two in-tree copies drift
apart.

Coverage:
  1. Parity: the shipped `EEPROM_SDP_DISABLE` table and `flash_utils.h`'s
     `FLASH_DISABLE_WRITE_PROTECTION` table are the same ordered list of
     exactly 6 {address, byte} pairs.
  2. Erase-vs-unlock hazard: the parsed unlock table's terminal byte differs
     from `FLASH_ERASE`'s terminal byte while the first five pairs are
     identical -- the one-nibble chip-erase hazard this milestone's FIX-05
     cares about.
  3. Non-vacuous proof: an altered temp copy of eeprom_28c.cpp (one pair's
     byte flipped) makes the parity assertion fail -- proves the gate is
     capable of failing, not a vacuous always-pass check.
  4. Fail-closed seam: pointing FIRESTARTER_SDP_SRC at a nonexistent path is
     always an error, never a silent pass.

Extraction is brace-scoped (locate the named declaration, then read pairs
until the matching closing brace), not a bare file-wide regex -- this file's
own source has a non-initializer call site using the same literal bytes
(`eeprom28c_wait_for_write(handle, 0x5555, 0x20)` is a function call, not an
array initializer) that a loose pattern would false-positive on (the
Phase-109 SAFE-02 / Phase-110 lessons recorded in STATE.md).

Carries the shared `tests.fw_presence.requires_fw` skip marker, keyed on the
sibling firestarter repo's `.git` presence (Phase 123 Plan 08, BASE-02), so
this module skips cleanly in standalone firestarter_app CI -- the one place
in this phase where that marker is correct (it must NOT be present in
test_sdp_db_invariant.py). Test 3's non-vacuity leg
(`test_altered_temp_copy_fails_parity_non_vacuous`) also carries this
decorator now: the whole test body depends on the real, committed
eeprom_28c.cpp, so the guard that used to be a bare inline
`if _FW_ABSENT: pytest.skip(...)` inside the test body -- invisible to a
decorator grep, and the one guard a decorator-only rekey pass would leave
behind -- is now the same `@requires_fw` decorator every other leg in this
module uses.
"""

import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.fw_presence import FW_REPO_PRESENT, FW_ROOT, fw_path, requires_fw

# eeprom_28c.cpp and flash_utils.h live in the SIBLING firestarter repo.
# Resolved through the shared `fw_path` helper -- repo presence is decided
# ONCE in tests/fw_presence.py, keyed on the sibling's `.git` marker.
# `requires_fw` is the ONLY skip marker this module uses (D-11 -- scoped to
# this file only; test_sdp_db_invariant.py must never carry this skip).
_EEPROM_28C_CPP = fw_path("src", "proms", "eeprom_28c.cpp")
_FLASH_UTILS_H = fw_path("include", "flash_utils.h")

_PARITY_CONTEXT = (
    "The firmware trace suites #include flash_utils.h and drive "
    "FLASH_DISABLE_WRITE_PROTECTION on the assumption it equals the shipped "
    "EEPROM_SDP_DISABLE table (eeprom_28c.cpp is internal-linkage and cannot "
    "be linked from a test TU). A divergence invalidates every Phase-116 "
    "trace claim."
)

_HAZARD_CONTEXT = (
    "One-nibble chip-erase hazard: the 0x0D unlock table's first five bytes "
    "must be byte-identical to FLASH_ERASE's first five bytes (both tables "
    "share the same unlock-sequence prefix), but the terminal byte MUST "
    "differ (0x20 disable vs 0x10 erase) -- a single flipped nibble in "
    "either table would silently turn an SDP-disable into a chip erase."
)

# ---------------------------------------------------------------------------
# Fail-closed env-override seam (mirrors check_dispatch.py's
# FIRESTARTER_DB_FILE idiom). Default is the real, unmodified source; a
# missing or unreadable override path is always an error, never a silent
# pass. Exists only so the non-vacuity test below can plant an altered
# fixture without touching the real, clean eeprom_28c.cpp.
# ---------------------------------------------------------------------------


def _sdp_src_path() -> Path:
    override = os.environ.get("FIRESTARTER_SDP_SRC")
    path = Path(override) if override else _EEPROM_28C_CPP
    if not path.is_file():
        raise FileNotFoundError(
            f"FIRESTARTER_SDP_SRC points at a missing/unreadable file: {path}"
        )
    return path


@contextmanager
def _env_override(name: str, value: str):
    """Temporarily set an environment variable, restoring the prior value
    (or absence) on exit -- used to plant/withdraw FIRESTARTER_SDP_SRC
    without leaking state into other tests."""
    old_value = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if old_value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old_value


# ---------------------------------------------------------------------------
# Brace-scoped {address, byte} pair extraction
# ---------------------------------------------------------------------------

_PAIR_RE = re.compile(r"\{\s*(0[xX][0-9A-Fa-f]+)\s*,\s*(0[xX][0-9A-Fa-f]+)\s*\}")


def _extract_byte_flip_pairs(source_text: str, decl_name: str) -> list[tuple[int, int]]:
    """Extract the ordered {address, byte} pairs initializing the named
    byte_flip_t array declaration `decl_name` in C++ source text.

    Locates the declaration by name, then walks brace depth from the
    opening `{` of its initializer list to the matching closing `}` --
    never a bare file-wide regex, which would false-positive on this
    project's own non-initializer usages of the same literal bytes.

    The bracket group accepts BOTH an implicit extent (`NAME[] = {`, the
    shape at v1.22 Phase 116) and an explicit one (`NAME[6] = {`, the shape
    Phase 117 needed once `EEPROM_SDP_DISABLE` was given external linkage --
    a C++ `extern` declaration cannot name an incomplete array type). The
    trailing `=` is still required, so this matches only the initializer and
    never the bare `extern const byte_flip_t NAME[6];` declaration.
    """
    decl_pattern = re.compile(rf"\b{re.escape(decl_name)}\s*\[\s*\d*\s*\]\s*=\s*")
    match = decl_pattern.search(source_text)
    if not match:
        raise ValueError(f"Declaration {decl_name!r} not found in source text")

    brace_start = source_text.index("{", match.end())
    depth = 0
    i = brace_start
    while i < len(source_text):
        if source_text[i] == "{":
            depth += 1
        elif source_text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    else:
        raise ValueError(f"Unbalanced braces for {decl_name!r} initializer")

    body = source_text[brace_start + 1 : i]
    pairs = [(int(addr, 16), int(byte, 16)) for addr, byte in _PAIR_RE.findall(body)]
    if not pairs:
        raise ValueError(f"No {{address, byte}} pairs found for {decl_name!r}")
    return pairs


def _assert_pairs_equal(
    a: list[tuple[int, int]], b: list[tuple[int, int]], context: str
) -> None:
    assert a == b, f"{context}\nleft={a}\nright={b}"


# ---------------------------------------------------------------------------
# Test 1: parity between the two in-tree unlock tables
# ---------------------------------------------------------------------------


@requires_fw
def test_eeprom_sdp_disable_matches_flash_disable_write_protection() -> None:
    sdp_pairs = _extract_byte_flip_pairs(
        _sdp_src_path().read_text(encoding="utf-8"), "EEPROM_SDP_DISABLE"
    )
    flash_pairs = _extract_byte_flip_pairs(
        _FLASH_UTILS_H.read_text(encoding="utf-8"),
        "FLASH_DISABLE_WRITE_PROTECTION",
    )
    assert len(sdp_pairs) == 6, (
        f"EEPROM_SDP_DISABLE must have exactly 6 pairs, found {len(sdp_pairs)}"
    )
    assert len(flash_pairs) == 6, (
        "FLASH_DISABLE_WRITE_PROTECTION must have exactly 6 pairs, found "
        f"{len(flash_pairs)}"
    )
    _assert_pairs_equal(sdp_pairs, flash_pairs, _PARITY_CONTEXT)


# ---------------------------------------------------------------------------
# Test 2: erase-vs-unlock terminal-byte distinction (FIX-04/FIX-05 precursor)
# ---------------------------------------------------------------------------


@requires_fw
def test_unlock_table_terminal_byte_differs_from_erase_terminal_byte() -> None:
    unlock_pairs = _extract_byte_flip_pairs(
        _sdp_src_path().read_text(encoding="utf-8"), "EEPROM_SDP_DISABLE"
    )
    erase_pairs = _extract_byte_flip_pairs(
        _FLASH_UTILS_H.read_text(encoding="utf-8"), "FLASH_ERASE"
    )
    assert len(unlock_pairs) == len(erase_pairs) == 6, (
        "Both tables must have exactly 6 pairs to compare terminal bytes: "
        f"unlock={len(unlock_pairs)} erase={len(erase_pairs)}"
    )
    assert unlock_pairs[:5] == erase_pairs[:5], (
        f"{_HAZARD_CONTEXT}\nFirst five pairs diverged: "
        f"unlock={unlock_pairs[:5]} erase={erase_pairs[:5]}"
    )
    assert unlock_pairs[-1] != erase_pairs[-1], (
        f"{_HAZARD_CONTEXT}\nTerminal pairs are identical: "
        f"unlock={unlock_pairs[-1]} erase={erase_pairs[-1]} -- this would "
        "make an SDP-disable command indistinguishable from a chip erase."
    )


# ---------------------------------------------------------------------------
# Test 2b: EEPROM_SDP_ENABLE three-way parity (Plan 119-06, LOCK-05)
# ---------------------------------------------------------------------------

_ENABLE_CONTEXT = (
    "Second, independent, source-text oracle for D-10's three-way AA-55-A0 "
    "identity: the firmware guard in test_sdp_harness.cpp "
    "(test_lock05_three_way_enable_table_identity, LOCK-05, plan 119-06) proves "
    "EEPROM_SDP_ENABLE, FLASH_ENABLE_WRITE_PROTECTION and FLASH_ENABLE_WRITE are "
    "byte-identical AND three distinct objects at link time. This test proves the "
    "byte-identity half again, independently, by parsing the same three "
    "declarations as source text instead. Two oracles with different failure "
    "modes is the point -- a source-text refactor that broke the firmware "
    "guard's linkage would still be caught here, and vice versa. This also "
    "keeps the host gate's coverage in step with the firmware, which is "
    "CORRECTION-4 item 4's spirit."
)


@requires_fw
def test_eeprom_sdp_enable_matches_flash_enable_write_and_write_protection() -> None:
    """Second, independent, source-text oracle for D-10's three-way AA-55-A0
    identity (Plan 119-06, closing LOCK-05).

    The firmware guard in test_sdp_harness.cpp is a link-time comparison of
    the actual PRODUCTION objects (EEPROM_SDP_ENABLE, FLASH_ENABLE_WRITE_PROTECTION,
    FLASH_ENABLE_WRITE) plus their pairwise pointer distinctness. This test
    re-proves the byte-identity half by parsing the same three declarations
    as source text -- a different failure mode than the firmware guard's: a
    text-level divergence that somehow still linked identically, or a
    firmware refactor that changed the declaration syntax without changing
    the linked bytes, would be caught by ONE of the two oracles but not
    necessarily the other.
    """
    enable_pairs = _extract_byte_flip_pairs(
        _sdp_src_path().read_text(encoding="utf-8"), "EEPROM_SDP_ENABLE"
    )
    write_protection_pairs = _extract_byte_flip_pairs(
        _FLASH_UTILS_H.read_text(encoding="utf-8"),
        "FLASH_ENABLE_WRITE_PROTECTION",
    )
    write_pairs = _extract_byte_flip_pairs(
        _FLASH_UTILS_H.read_text(encoding="utf-8"), "FLASH_ENABLE_WRITE"
    )

    assert len(enable_pairs) == 3, (
        f"EEPROM_SDP_ENABLE must have exactly 3 pairs, found {len(enable_pairs)}"
    )
    assert len(write_protection_pairs) == 3, (
        "FLASH_ENABLE_WRITE_PROTECTION must have exactly 3 pairs, found "
        f"{len(write_protection_pairs)}"
    )
    assert len(write_pairs) == 3, (
        f"FLASH_ENABLE_WRITE must have exactly 3 pairs, found {len(write_pairs)}"
    )

    _assert_pairs_equal(
        enable_pairs,
        write_protection_pairs,
        f"{_ENABLE_CONTEXT}\nDiverging pair: EEPROM_SDP_ENABLE vs FLASH_ENABLE_WRITE_PROTECTION.",
    )
    _assert_pairs_equal(
        enable_pairs,
        write_pairs,
        f"{_ENABLE_CONTEXT}\nDiverging pair: EEPROM_SDP_ENABLE vs FLASH_ENABLE_WRITE.",
    )

    assert enable_pairs[-1] == (0x5555, 0xA0), (
        f"EEPROM_SDP_ENABLE's last pair must be (0x5555, 0xA0), found {enable_pairs[-1]}"
    )


# ---------------------------------------------------------------------------
# Test 3: non-vacuous proof
# ---------------------------------------------------------------------------


@requires_fw
def test_altered_temp_copy_fails_parity_non_vacuous(tmp_path: Path) -> None:
    """An altered temp copy of eeprom_28c.cpp (one pair's byte flipped) MUST
    make the parity assertion fail -- proves the gate is capable of failing,
    not a vacuous always-pass check.

    Was a bare inline `if _FW_ABSENT: pytest.skip(...)` guard inside the test
    body -- invisible to a decorator grep and precisely the shape a
    decorator-only rekey pass would leave behind (Phase 123 Plan 08). Now the
    same `@requires_fw` decorator every other leg in this module uses: the
    whole test body reads the real, committed eeprom_28c.cpp, so it needs the
    sibling repo present exactly like the other legs.
    """
    original = _EEPROM_28C_CPP.read_text(encoding="utf-8")
    altered = original.replace("{0x5555, 0x20}", "{0x5555, 0x21}", 1)
    assert altered != original, (
        "Fixture setup error: the byte replacement did not apply -- "
        "eeprom_28c.cpp's EEPROM_SDP_DISABLE terminal pair text changed "
        "shape and this fixture needs updating."
    )
    fixture_path = tmp_path / "eeprom_28c_altered.cpp"
    fixture_path.write_text(altered, encoding="utf-8")

    with _env_override("FIRESTARTER_SDP_SRC", str(fixture_path)):
        sdp_pairs = _extract_byte_flip_pairs(
            _sdp_src_path().read_text(encoding="utf-8"), "EEPROM_SDP_DISABLE"
        )
    flash_pairs = _extract_byte_flip_pairs(
        _FLASH_UTILS_H.read_text(encoding="utf-8"),
        "FLASH_DISABLE_WRITE_PROTECTION",
    )

    try:
        _assert_pairs_equal(sdp_pairs, flash_pairs, _PARITY_CONTEXT)
    except AssertionError:
        pass
    else:
        raise AssertionError(
            "Non-vacuity failure: altering one byte in the temp fixture did "
            "not make the parity assertion fail -- the parser or the "
            "parity gate is vacuous."
        )


# ---------------------------------------------------------------------------
# Test 4: fail-closed seam
# ---------------------------------------------------------------------------


def test_missing_override_path_fails_closed() -> None:
    """Pointing FIRESTARTER_SDP_SRC at a nonexistent path MUST raise, never
    silently fall back to the real source or pass."""
    with _env_override("FIRESTARTER_SDP_SRC", "/nonexistent/path/does-not-exist.cpp"):
        with pytest.raises(FileNotFoundError):
            _sdp_src_path()


# ---------------------------------------------------------------------------
# SWEEP-07 planted-violation controls (D-06). RESEARCH.md's R3 proved this
# module's `_extract_byte_flip_pairs` is comment-blind twice over: a comment
# spelling the initializer FORM above the real declaration wins the
# first-match regex race (mis-anchor), and a bare `}` inside a comment
# terminates the raw brace-depth walk early (comment-borne brace). Worse:
# spelling the initializer form with the CORRECT bytes above a REAL
# declaration whose terminal byte had been corrupted from 0xA0 (SDP lock) to
# 0x10 (chip erase) made this module's own five pre-existing legs report "5
# passed" -- a green run from this gate is worthless as evidence until these
# controls exist.
#
# The two fixture-only legs below (misanchor, comment-brace) carry NO
# `@requires_fw`: they compare the extraction against a hardcoded,
# already-pinned-elsewhere expected value (the same triple this module's own
# `test_eeprom_sdp_enable_matches_flash_enable_write_and_write_protection`
# hardcodes at `enable_pairs[-1] == (0x5555, 0xA0)`), never against the
# sibling firmware repo's flash_utils.h, so they stay live in an
# absent-firmware run. The third (anchoring) leg reads the REAL
# eeprom_28c.cpp to prove the extraction's slice is anchored on the real
# declaration -- like every other leg in this module that reads the real
# source, it carries `@requires_fw`.
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_FIXTURE_SDP_COMMENT_MISANCHOR = _FIXTURES_DIR / "planted_sdp_comment_misanchor.cpp"
_FIXTURE_SDP_COMMENT_BRACE = _FIXTURES_DIR / "planted_sdp_comment_brace.cpp"

# The known-correct EEPROM_SDP_ENABLE pairs -- already pinned elsewhere in
# this module (see the hardcoded `enable_pairs[-1] == (0x5555, 0xA0)` check
# above) -- used as the comparison target so these two legs never need to
# read the sibling firmware repo's flash_utils.h at all.
_EXPECTED_ENABLE_PAIRS = [(0x5555, 0xAA), (0x2AAA, 0x55), (0x5555, 0xA0)]


def _git_hash_object(path: Path) -> str:
    """Resolve `git` fail-closed and hash-object `path` inside FW_ROOT.
    Copied from `tests/test_json_key_parity.py` / `tests/test_cap03_ack_layout_parity.py`
    (not reinvented) per house practice -- see 154-PATTERNS.md.
    """
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


def _strip_comments(text: str) -> str:
    """Strip `//` line comments and `/* ... */` block comments, replacing
    each stripped span with whitespace of the SAME SHAPE (a newline stays a
    newline, everything else becomes a single space) so any position offset
    computed against the result still lines up with the original file.
    Copied structurally from
    `test_cap03_ack_layout_parity.py::_strip_comments` (not reinvented) per
    154-PATTERNS.md's instruction -- that stripper is itself "copied
    structurally from firestarter/tests/test_ack_layout_source_contract_v143.py".
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            out.append("  ")
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            if i < n:
                out.append("  ")
                i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def test_planted_comment_misanchor_is_detected() -> None:
    """SWEEP-07 / D-06 plant 1: two comment lines above the real
    EEPROM_SDP_ENABLE declaration spell the initializer FORM with
    obviously-wrong bytes (0x1111/0x2222/0x3333). `_extract_byte_flip_pairs`
    takes the FIRST regex match in the file, so it must extract the WRONG
    pairs from inside the comment. Comparing them against the known-correct
    table via `_assert_pairs_equal` (the SAME helper the live leg calls,
    never a reimplementation) must raise AssertionError naming a diverging
    pair.
    """
    assert _FIXTURE_SDP_COMMENT_MISANCHOR.is_file(), (
        f"committed fixture missing: {_FIXTURE_SDP_COMMENT_MISANCHOR}"
    )
    before_sha = _git_hash_object(_EEPROM_28C_CPP) if FW_REPO_PRESENT else None

    with _env_override("FIRESTARTER_SDP_SRC", str(_FIXTURE_SDP_COMMENT_MISANCHOR)):
        enable_pairs = _extract_byte_flip_pairs(
            _sdp_src_path().read_text(encoding="utf-8"), "EEPROM_SDP_ENABLE"
        )

    with pytest.raises(AssertionError) as excinfo:
        _assert_pairs_equal(
            enable_pairs,
            _EXPECTED_ENABLE_PAIRS,
            f"{_ENABLE_CONTEXT}\nDiverging pair: EEPROM_SDP_ENABLE (extracted, "
            "comment-mis-anchored) vs the known-correct real-table pairs.",
        )
    message = str(excinfo.value)
    assert "Diverging pair" in message
    assert "(4369, 17)" in message  # 0x1111, 0x11 -- the comment's bogus values
    # Leg isolation: the sibling plant's distinguishing phrase is absent.
    assert "must have exactly 3 pairs, found 1" not in message

    if FW_REPO_PRESENT:
        after_sha = _git_hash_object(_EEPROM_28C_CPP)
        assert before_sha == after_sha, (
            "the real eeprom_28c.cpp's git blob hash changed during this "
            "planted-violation run -- the plant must never touch the real "
            "file."
        )
        assert _git_porcelain(FW_ROOT) == "", (
            "the sibling firmware repo is not clean after this "
            "planted-violation run -- the plant must never write into the "
            "real firmware checkout."
        )


def test_planted_comment_brace_break_is_detected() -> None:
    """SWEEP-07 / D-06 plant 2: one comment line inserted INSIDE the real
    EEPROM_SDP_ENABLE initializer body contains a bare `}` in its prose.
    The raw `{`/`}` depth walk in `_extract_byte_flip_pairs` terminates
    early on that comment's brace, so only 1 of the real 3 pairs is
    extracted -- reproducing this module's own length-check message shape
    (`test_eeprom_sdp_enable_matches_flash_enable_write_and_write_protection`'s
    `len(enable_pairs) == 3` assertion, copied verbatim here rather than
    reimplemented, since there is no separate helper for that check).
    """
    assert _FIXTURE_SDP_COMMENT_BRACE.is_file(), (
        f"committed fixture missing: {_FIXTURE_SDP_COMMENT_BRACE}"
    )
    before_sha = _git_hash_object(_EEPROM_28C_CPP) if FW_REPO_PRESENT else None

    with _env_override("FIRESTARTER_SDP_SRC", str(_FIXTURE_SDP_COMMENT_BRACE)):
        enable_pairs = _extract_byte_flip_pairs(
            _sdp_src_path().read_text(encoding="utf-8"), "EEPROM_SDP_ENABLE"
        )

    with pytest.raises(AssertionError) as excinfo:
        assert len(enable_pairs) == 3, (
            f"EEPROM_SDP_ENABLE must have exactly 3 pairs, found {len(enable_pairs)}"
        )
    message = str(excinfo.value)
    assert "must have exactly 3 pairs, found 1" in message
    # Leg isolation: the sibling plant's distinguishing phrase is absent.
    assert "Diverging pair" not in message

    if FW_REPO_PRESENT:
        after_sha = _git_hash_object(_EEPROM_28C_CPP)
        assert before_sha == after_sha, (
            "the real eeprom_28c.cpp's git blob hash changed during this "
            "planted-violation run -- the plant must never touch the real "
            "file."
        )
        assert _git_porcelain(FW_ROOT) == "", (
            "the sibling firmware repo is not clean after this "
            "planted-violation run -- the plant must never write into the "
            "real firmware checkout."
        )


@requires_fw
def test_extracted_slice_is_anchored_on_the_real_declaration() -> None:
    """SWEEP-07's added assertion, closing the silent-green path RESEARCH.md's
    R3 found (two comment lines spelling the initializer form with the
    CORRECT bytes above a REAL declaration whose terminal byte had been
    corrupted from 0xA0 to 0x10 made all five of this module's pre-existing
    legs report "5 passed"). This is an ADDED assertion in a new leg, not a
    change to `_extract_byte_flip_pairs` itself -- hardening the live
    extraction would be a behaviour change to a gate, which this phase's
    no-code-changes constraint excludes; filed as a follow-on.

    Proves the byte offset the live (raw-text, no stripping) extraction
    walks starts INSIDE the real EEPROM_SDP_ENABLE declaration's span,
    located by running the same offset-preserving `_strip_comments` this
    repo already uses over the real source text (comments blank to
    same-shape whitespace, so a mis-anchored comment above the real
    declaration disappears from the stripped text and the real declaration
    is what the stripped-text search finds). Proves the negative case too:
    against `planted_sdp_comment_misanchor.cpp`, the SAME check raises,
    because there the raw-text extraction anchor lands inside the planted
    comment -- outside the real declaration's comment-stripped span.

    Reads the real, committed eeprom_28c.cpp (unlike the two fixture-only
    legs above), so this leg carries `@requires_fw` like every other leg in
    this module that reads the real source.
    """
    before_sha = _git_hash_object(_EEPROM_28C_CPP)

    decl_pattern = re.compile(r"\bEEPROM_SDP_ENABLE\s*\[\s*\d*\s*\]\s*=\s*")

    def _decl_span(raw_text: str) -> tuple[int, int]:
        """Locate EEPROM_SDP_ENABLE's declaration in COMMENT-STRIPPED text
        and return its (start, end) span -- start of the declaration match,
        end of the matching closing brace of its initializer body."""
        stripped = _strip_comments(raw_text)
        match = decl_pattern.search(stripped)
        assert match is not None, (
            "EEPROM_SDP_ENABLE declaration not found in comment-stripped text"
        )
        brace_start = stripped.index("{", match.end())
        depth = 0
        i = brace_start
        while i < len(stripped):
            if stripped[i] == "{":
                depth += 1
            elif stripped[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        else:
            raise ValueError("unbalanced braces while spanning the real declaration")
        return match.start(), i + 1

    def _raw_extraction_anchor(raw_text: str) -> int:
        """The offset the LIVE `_extract_byte_flip_pairs` actually anchors
        on: the first `{` after the FIRST (raw-text, unstripped) regex
        match -- reproducing its own two-step lookup, never its own
        pair-parsing logic."""
        match = decl_pattern.search(raw_text)
        assert match is not None, "EEPROM_SDP_ENABLE not found in raw text"
        return raw_text.index("{", match.end())

    # Positive case: the real source's own extraction anchor falls inside
    # its own comment-stripped declaration span.
    real_text = _EEPROM_28C_CPP.read_text(encoding="utf-8")
    real_span = _decl_span(real_text)
    real_anchor = _raw_extraction_anchor(real_text)
    assert real_span[0] <= real_anchor < real_span[1], (
        f"real EEPROM_SDP_ENABLE's own extraction anchor {real_anchor} falls "
        f"outside its own comment-stripped span {real_span} -- this should "
        "never happen against unmodified source; investigate the stripper "
        "or the span computation before trusting this leg's negative case."
    )

    # Negative case: against the mis-anchor plant, the SAME check raises,
    # because the raw-text anchor lands inside the planted comment -- the
    # plant's own (real, unmodified) declaration further down the file is
    # what the stripped-text span describes, and the raw anchor is NOT in it.
    assert _FIXTURE_SDP_COMMENT_MISANCHOR.is_file(), (
        f"committed fixture missing: {_FIXTURE_SDP_COMMENT_MISANCHOR}"
    )
    plant_text = _FIXTURE_SDP_COMMENT_MISANCHOR.read_text(encoding="utf-8")
    plant_span = _decl_span(plant_text)
    plant_anchor = _raw_extraction_anchor(plant_text)

    with pytest.raises(AssertionError) as excinfo:
        assert plant_span[0] <= plant_anchor < plant_span[1], (
            "comment mis-anchor detected: extraction anchor does not start "
            "inside the real declaration's comment-stripped span"
        )
    assert "comment mis-anchor detected" in str(excinfo.value)

    after_sha = _git_hash_object(_EEPROM_28C_CPP)
    assert before_sha == after_sha, (
        "the real eeprom_28c.cpp's git blob hash changed during this "
        "planted-violation run -- the plant must never touch the real file."
    )
    assert _git_porcelain(FW_ROOT) == "", (
        "the sibling firmware repo is not clean after this planted-violation "
        "run -- the plant must never write into the real firmware checkout."
    )
