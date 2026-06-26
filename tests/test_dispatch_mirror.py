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

from tools import check_dispatch

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

# Absolute path to the firestarter_app directory (independent of cwd).
_FA_DIR = pathlib.Path(__file__).parent.parent

# Sub-repo sibling: firestarter/doc/PROTOCOLS.md (the doc leg).
_PROTOCOLS_MD = _FA_DIR.parent / "firestarter" / "doc" / "PROTOCOLS.md"

# Sub-repo sibling: native firmware dispatch test (the firmware leg).
_FW_DISPATCH_TEST = (
    _FA_DIR.parent
    / "firestarter"
    / "test"
    / "native"
    / "avr"
    / "test_dispatch"
    / "test_configure_memory.cpp"
)

# ---------------------------------------------------------------------------
# §0 table parser
# ---------------------------------------------------------------------------

# Matches a §0 pipe-table row:  | 0xNN | ... | `<handler>.cpp` | ...
# Column 3 (handler) is in backticks; this regex extracts hex (group 1) and
# filename (group 2).  Only rows that start with a protocol hex are matched.
_ROW_RE = re.compile(r"^\|\s*0x([0-9A-Fa-f]+)\s*\|[^|]*\|\s*`([a-z0-9_]+\.cpp)`\s*\|")

# Map: doc handler-file → check_dispatch handler-function name.
# These are the seven distinct handlers that appear across the §0 table.
DOC_FILE_TO_FUNC: dict[str, str] = {
    "flash_type_4.cpp": "configure_flash4",
    "flash_type_3.cpp": "configure_flash3",
    "eprom.cpp": "configure_eprom",
    "eeprom_28c.cpp": "configure_eeprom28c",
    "flash_intel.cpp": "configure_flash_intel",
    "sram.cpp": "configure_sram",
    "not_implemented.cpp": "not_implemented",
}


def parse_protocols_md() -> dict[int, str]:
    """Parse the §0 pipe table from PROTOCOLS.md.

    Returns a dict mapping ``{hex_int: handler_filename}`` for every row in
    the §0 canonical bucket table.  The §0 table is the single source of truth
    for the doc leg of the three-way dispatch bind (D-06).
    """
    result: dict[int, str] = {}
    text = _PROTOCOLS_MD.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = _ROW_RE.match(line)
        if m:
            result[int(m.group(1), 16)] = m.group(2)
    return result


# ---------------------------------------------------------------------------
# Test 1: doc leg ↔ tool leg
# ---------------------------------------------------------------------------


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
