"""
Dispatch-mirror invariant test — three-way bind (D-05 / D-06, Phase 88 Plan 04).

Proves the protocol→handler dispatch order agrees across all three representations:
  1. ``firestarter/doc/PROTOCOLS.md`` §0 table (doc leg — canonical source of truth).
  2. ``firestarter_app/tools/check_dispatch.dispatch()`` + ``_ALGO_MEM_TYPE`` (tool leg).
  3. ``firestarter/test/native/avr/test_dispatch/test_configure_memory.cpp`` (firmware leg).

A drift in ANY of the three representations trips this test immediately, so Phase 89
can refactor handler internals while the dispatch order stays pinned (D-05/D-06).

Coverage:
  - Full §0 table (12 rows, incl. SRAM 0x0E/0x27/0x28/0x29 and 0x34→not_implemented).
  - Phantom 0x35/0x39 are NOT in §0; they are absent from the doc parse and are
    excluded from the firmware-leg assertion (they route to not_implemented per host rule,
    matching check_dispatch's KNOWN_PROTOCOLS exclusion).
"""

import pathlib
import re
import shutil
import subprocess
import sys

import pytest

from tests.fw_presence import FW_ROOT, fw_path, requires_fw
from tools import check_dispatch

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

# Absolute path to the firestarter_app directory (independent of cwd).
_FA_DIR = pathlib.Path(__file__).parent.parent

# Sub-repo sibling: firestarter/doc/PROTOCOLS.md (the doc leg).
_PROTOCOLS_MD = fw_path("doc", "PROTOCOLS.md")

# Sub-repo sibling: native firmware dispatch test (the firmware leg).
_FW_DISPATCH_TEST = fw_path(
    "test", "native", "avr", "test_dispatch", "test_configure_memory.cpp"
)

# Repo presence (this module's compound proxy previously ANDed the existence
# of these two specific paths) is now decided ONCE in tests/fw_presence.py,
# keyed on the sibling repo's `.git` marker -- immune to a rename of either
# path above. `requires_fw` is the ONLY skip marker this module uses; both
# paths above are resolved through `fw_path`, so a present-repo-renamed
# target is a named `MissingScanTargetError`, never a silent skip
# (Phase 123 Plan 08, BASE-02/D-11 -- these two paths are also the D-11
# scan-path inventory's `test_dispatch_mirror.py` entries).

# ---------------------------------------------------------------------------
# §0 table parser (post-Phase-100 two-table layout)
# ---------------------------------------------------------------------------
#
# Phase 100 restructured the bucket table: the `.cpp` filename moved OUT of
# the bucket-table columns (column 3 is now the frozen `datasheets/` slug,
# e.g. `` `0x05-FLASH-AMD-STD` ``) and into a separate "Handler-family layer"
# table that maps handler-family → configure_*() → file.  So the doc leg is
# now a two-table join: bucket table gives hex → family, handler-family
# table gives family → file.

# Matches a bucket-table row:  | 0xNN | <count> | `<slug>` | `PROTO_*` | <name> | <handler-family...> | <phantom?> |
# We need column 1 (hex, group 1), column 6 (handler-family, group 2) — the
# family word is the FIRST whitespace-delimited token of that column (e.g.
# "5v_page (0x05 + phantoms 0x35/0x39)" -> "5v_page") — and column 7 (phantom?,
# group 3), used to exclude phantom rows (0x35/0x39) from the doc leg, since
# they are NOT in check_dispatch.KNOWN_PROTOCOLS and route to
# not_implemented on the tool leg (host-side exclusion, unrelated to naming).
_BUCKET_ROW_RE = re.compile(
    r"^\|\s*0x([0-9A-Fa-f]+)\s*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
)

# Matches a Handler-family layer row:  | <family> | `configure_*()` | `<file>.cpp` | <protocols> |
_FAMILY_ROW_RE = re.compile(
    r"^\|\s*([a-z0-9_-]+)\s*\|\s*`[a-z0-9_]+\(\)`\s*\|\s*`([a-z0-9_]+\.cpp)`\s*\|"
)

# Map: doc handler-file → check_dispatch handler-function name.
# These are the seven distinct handlers that appear across the §0 table.
DOC_FILE_TO_FUNC: dict[str, str] = {
    "flash_5v_page.cpp": "configure_flash_5v_page",
    "flash_nor_unlock.cpp": "configure_flash_nor_unlock",
    "eprom.cpp": "configure_eprom",
    "eeprom_28c.cpp": "configure_eeprom28c",
    "flash_intel.cpp": "configure_flash_intel",
    "sram.cpp": "configure_sram",
    "not_implemented.cpp": "not_implemented",
}

# Map: handler-family "not-implemented" label (bucket-table column, hyphenated)
# to its Handler-family-layer key ("not-implemented" too) — kept as an
# explicit alias table in case the two tables ever spell the family
# differently (defensive; currently identical).
_FAMILY_LABEL_ALIASES: dict[str, str] = {
    "not-implemented": "not-implemented",
}


def parse_protocols_md() -> dict[int, str]:
    """Parse the current (post-Phase-100) two-table PROTOCOLS.md layout.

    Returns a dict mapping ``{hex_int: handler_filename}`` for every row in
    the §0 canonical bucket table, composed via:
      1. bucket table:          hex -> handler-family (first token of col 6)
      2. Handler-family layer:  handler-family -> handler_filename

    This is the single source of truth for the doc leg of the three-way
    dispatch bind (D-06), re-pointed at the Phase-100 table structure where
    the `.cpp` filename no longer lives in the bucket table itself.
    """
    text = _PROTOCOLS_MD.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Step 1: hex -> family (first whitespace token of the handler-family column).
    # Phantom rows (phantom? column == "YES") are excluded — they are absent
    # from check_dispatch.KNOWN_PROTOCOLS and route to not_implemented on the
    # tool leg (host-side exclusion; matches the original test's documented
    # scope: "Phantom 0x35/0x39 are NOT in §0 ... excluded from the doc parse").
    hex_to_family: dict[int, str] = {}
    for line in lines:
        m = _BUCKET_ROW_RE.match(line)
        if m:
            phantom_col = m.group(3).strip().upper()
            if phantom_col == "YES":
                continue
            hex_id = int(m.group(1), 16)
            family_col = m.group(2).strip()
            family = family_col.split()[0] if family_col else ""
            family = _FAMILY_LABEL_ALIASES.get(family, family)
            hex_to_family[hex_id] = family

    # Step 2: family -> handler filename (Handler-family layer table).
    family_to_file: dict[str, str] = {}
    for line in lines:
        m = _FAMILY_ROW_RE.match(line)
        if m:
            family_to_file[m.group(1).strip()] = m.group(2).strip()

    # Step 3: compose hex -> handler filename.
    result: dict[int, str] = {}
    for hex_id, family in hex_to_family.items():
        handler_file = family_to_file.get(family)
        if handler_file is not None:
            result[hex_id] = handler_file
    return result


# ---------------------------------------------------------------------------
# Test 1: doc leg ↔ tool leg
# ---------------------------------------------------------------------------


@requires_fw
def test_dispatch_mirror_doc_matches_tool() -> None:
    """Every §0 protocol row must agree between PROTOCOLS.md and check_dispatch.dispatch().

    For each protocol hex in the §0 table:
      - Look up mem_type via check_dispatch._ALGO_MEM_TYPE (0 for unknown, e.g. 0x34).
      - Call check_dispatch.dispatch(hex, mem_type).
      - Assert the returned handler-function name equals DOC_FILE_TO_FUNC[handler_file].

    Assertion messages identify the hex and both sides so drift is immediately visible.
    Covers the full table (D-06): SRAM 0x0E/0x27/0x28/0x29 and 0x34→not_implemented
    are included, not just the five recompose families.
    """
    doc_table = parse_protocols_md()
    assert doc_table, (
        "parse_protocols_md() returned an empty table — check PROTOCOLS.md path"
    )

    for hex_id, handler_file in sorted(doc_table.items()):
        expected_func = DOC_FILE_TO_FUNC[handler_file]
        mem_type = check_dispatch._ALGO_MEM_TYPE.get(hex_id, 0)
        got_func = check_dispatch.dispatch(hex_id, mem_type)
        assert got_func == expected_func, (
            f"0x{hex_id:02X}: doc says {expected_func} but check_dispatch.dispatch() returned {got_func}"
        )


# ---------------------------------------------------------------------------
# Test 2: firmware leg — native test_dispatch enumerates every §0 protocol
# ---------------------------------------------------------------------------


@requires_fw
def test_dispatch_mirror_firmware_leg_enumerates_all_protocols() -> None:
    """Every §0 protocol that maps to a real handler must appear in the native dispatch test.

    Reads ``test_configure_memory.cpp`` and extracts all ``0x[0-9A-Fa-f]+`` tokens it
    references in function names and dispatch calls.  Asserts that every §0 protocol
    that routes to a non-not_implemented handler is present in that set.

    0x34 (→ not_implemented.cpp) is excluded: the firmware `configure_not_implemented()`
    arm covers all unrecognised non-zero protocols generically; there is no per-protocol
    positive dispatch test for 0x34 in the native suite.

    Phantom 0x35/0x39 are also excluded from the assertion (not in §0) — but they are
    already covered by the firmware test for forward-compat dispatch.

    A missing protocol in the firmware test means a routing arm lacks a native test,
    which would fail to catch a dispatch regression for that family.
    """
    fw_text = _FW_DISPATCH_TEST.read_text(encoding="utf-8")

    # Extract every hex literal that appears in the firmware test file.
    fw_hex_tokens: set[int] = {
        int(tok, 16) for tok in re.findall(r"0x([0-9A-Fa-f]+)", fw_text)
    }

    # §0 protocols that require a positive routing test (non-not_implemented handlers).
    doc_table = parse_protocols_md()
    real_handler_protocols = {
        hex_id
        for hex_id, handler_file in doc_table.items()
        if handler_file != "not_implemented.cpp"
    }

    missing = real_handler_protocols - fw_hex_tokens
    missing_str = ", ".join(f"0x{h:02X}" for h in sorted(missing))
    assert not missing, (
        f"firmware leg test_configure_memory.cpp does not enumerate §0 protocol(s): "
        f"{missing_str}"
        " — adding a §0 protocol without a native dispatch test trips this guard"
    )


# ---------------------------------------------------------------------------
# SWEEP-07 planted-violation controls. `test_dispatch_mirror_firmware_leg_
# enumerates_all_protocols`'s extraction is a SUPERSET membership test over
# every `0x[0-9A-Fa-f]+` token in the whole firmware-test file text, with NO
# comment stripping anywhere -- so it structurally cannot distinguish "a
# native dispatch test exists for this protocol" from "a comment mentions
# this protocol". RESEARCH.md's R3 measured no LIVE collision today (no §0
# protocol's only occurrence in test_configure_memory.cpp is a comment), so
# both legs below use a purely synthetic plant on protocol 0x10
# (flash_intel).
#
# The seam is the module-level constant `_FW_DISPATCH_TEST`, read INSIDE
# the test body above -- so `monkeypatch.setattr(sys.modules[__name__],
# "_FW_DISPATCH_TEST", fixture)` works, identically to the shape
# `test_json_key_parity.py` uses.
# ---------------------------------------------------------------------------

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"
_FIXTURE_DISPATCH_MISSING_HEX = _FIXTURES_DIR / "planted_dispatch_missing_hex.cpp"
_FIXTURE_DISPATCH_COMMENT_ONLY_HEX = (
    _FIXTURES_DIR / "planted_dispatch_comment_only_hex.cpp"
)


def _git_hash_object(path: pathlib.Path) -> str:
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


def _git_porcelain(path: pathlib.Path) -> str:
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


@requires_fw
def test_planted_missing_hex_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """SWEEP-07 RED control: `planted_dispatch_missing_hex.cpp` rewrites
    every real 0x10 (flash_intel) occurrence to 0xFF and mentions 0x10
    nowhere, not even in a comment. Calling the SAME live leg
    (`test_dispatch_mirror_firmware_leg_enumerates_all_protocols`), never a
    reimplementation, must raise AssertionError naming 0x10 as missing.
    """
    assert _FIXTURE_DISPATCH_MISSING_HEX.is_file(), (
        f"committed fixture missing: {_FIXTURE_DISPATCH_MISSING_HEX}"
    )
    real_source = _FW_DISPATCH_TEST
    before_sha = _git_hash_object(real_source)

    monkeypatch.setattr(
        sys.modules[__name__], "_FW_DISPATCH_TEST", _FIXTURE_DISPATCH_MISSING_HEX
    )
    with pytest.raises(AssertionError) as excinfo:
        test_dispatch_mirror_firmware_leg_enumerates_all_protocols()
    message = str(excinfo.value)
    assert "0x10" in message
    assert "does not enumerate" in message

    after_sha = _git_hash_object(real_source)
    assert before_sha == after_sha, (
        "the real test_configure_memory.cpp's git blob hash changed during "
        "this planted-violation run -- the plant must never touch the real "
        "file."
    )
    assert _git_porcelain(FW_ROOT) == "", (
        "the sibling firmware repo is not clean after this "
        "planted-violation run -- the plant must never write into the "
        "real firmware checkout."
    )


@requires_fw
def test_planted_comment_only_hex_is_NOT_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SWEEP-07 fail-OPEN control -- the finding, not a bug to fix.

    `planted_dispatch_comment_only_hex.cpp` carries the SAME rewrite as the
    sibling missing-hex fixture (every real 0x10 usage rewritten to 0xFF),
    PLUS one comment line mentioning 0x10. The live leg's extraction is a
    bare `0x[0-9A-Fa-f]+` regex over the WHOLE file text with NO comment
    stripping, so this comment alone satisfies the gate exactly as well as
    a real dispatch case would.

    Deliberately carries NO raises-expectation wrapper here -- the live leg
    must PASS, plainly, uncaught. A reader who "corrects" this leg to
    expect RED destroys the one committed proof that the gate cannot
    distinguish "a native dispatch test exists for this protocol" from "a
    comment mentions this protocol" (fail-open).
    """
    assert _FIXTURE_DISPATCH_COMMENT_ONLY_HEX.is_file(), (
        f"committed fixture missing: {_FIXTURE_DISPATCH_COMMENT_ONLY_HEX}"
    )
    real_source = _FW_DISPATCH_TEST
    before_sha = _git_hash_object(real_source)

    monkeypatch.setattr(
        sys.modules[__name__], "_FW_DISPATCH_TEST", _FIXTURE_DISPATCH_COMMENT_ONLY_HEX
    )
    # No raises-expectation wrapper: this call must complete without
    # raising. The GREEN is the finding.
    test_dispatch_mirror_firmware_leg_enumerates_all_protocols()

    after_sha = _git_hash_object(real_source)
    assert before_sha == after_sha, (
        "the real test_configure_memory.cpp's git blob hash changed during "
        "this planted-violation run -- the plant must never touch the real "
        "file."
    )
    assert _git_porcelain(FW_ROOT) == "", (
        "the sibling firmware repo is not clean after this "
        "planted-violation run -- the plant must never write into the "
        "real firmware checkout."
    )
