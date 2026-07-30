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

Carries the FW_ABSENT-shaped skip marker keyed on the presence of
eeprom_28c.cpp, so this module skips cleanly in standalone firestarter_app
CI -- the one place in this phase where that marker is correct (it must NOT
be present in test_sdp_db_invariant.py).
"""

import os
import re
from contextlib import contextmanager
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_EEPROM_28C_CPP = _REPO_ROOT / "firestarter" / "src" / "proms" / "eeprom_28c.cpp"
_FLASH_UTILS_H = _REPO_ROOT / "firestarter" / "include" / "flash_utils.h"

# The firmware sub-repo may be absent in standalone CI (firestarter_app
# checked out alone). Mirrors the FW_ABSENT skip pattern in
# test_revision_constants_parity.py / test_gen_validation_header.py.
_FW_ABSENT = not _EEPROM_28C_CPP.exists()
_requires_fw = pytest.mark.skipif(
    _FW_ABSENT,
    reason="firestarter firmware checkout absent (eeprom_28c.cpp)",
)

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


@_requires_fw
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


@_requires_fw
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


@_requires_fw
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


def test_altered_temp_copy_fails_parity_non_vacuous(tmp_path: Path) -> None:
    """An altered temp copy of eeprom_28c.cpp (one pair's byte flipped) MUST
    make the parity assertion fail -- proves the gate is capable of failing,
    not a vacuous always-pass check."""
    if _FW_ABSENT:
        pytest.skip("firestarter firmware checkout absent (eeprom_28c.cpp)")

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
