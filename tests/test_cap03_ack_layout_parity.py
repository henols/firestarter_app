"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 144 Plan 02 (TEST-07, D-17/D-18) -- the cross-repo CAP-03 byte-layout
parity gate BF-1's absence made necessary.

This is the standing gate two prior modules explicitly hand off to Phase 144
/ TEST-07 by name, rather than a third copy of either side. Quoting
firestarter/tests/test_ack_layout_source_contract_v143.py's own module
docstring verbatim: "It does NOT perform a live cross-repo comparison
against firestarter_app/firestarter/serial_comm.py's decoder -- that
standing gate is handed to Phase 144 / TEST-07 (143-RESEARCH.md Open
Question 4)." firestarter_app/tests/test_hw_revision_gate.py's
`_cap03_params` docstring makes the identical hand-off from the host side:
it is "the closest thing this repo has to a cross-repo wire-layout parity
assertion; nothing else in either repo compares the two sides". This module
is exactly that comparison -- the firmware's `MSG_OK_READY` pack order
`[buffer_size u16 BE][hw_revision u8][ver_len u8][ver bytes][write_budget_s
u16 BE]` (src/firestarter.cpp, inside `init_programmer_framed`) asserted
against the host's `_decode_id_frame` offsets (serial_comm.py), including the
COMPUTED `ver_end` the CAP-03 budget is read at -- never a fixed index.

Defect class this closes: BF-1. A wire-layout change on ONE side of this
two-repo protocol, with nothing comparing the two sides, made a v1.31
firmware build unreachable by the v1.31 host for three milestones before
anyone noticed (143-RESEARCH.md's BF-1 finding; `tests/test_fwguard.py`'s
`test_absent_identity_refuses` asserts the resulting refusal on purpose).
Neither existing gate catches a re-occurrence of that shape on the CAP-03
field specifically -- this module does.

Cross-repo plumbing: `requires_fw` (from `tests.fw_presence`) is the ONLY
skip marker this module uses, keyed on the sibling `../firestarter/.git`
marker -- immune to any in-repo firmware rename. `FIRMWARE_ACK_SOURCE`
(resolved via `fw_path`) doubles as the fixture-injection seam the two
planted-violation legs below `monkeypatch.setattr`. Those two legs
deliberately carry NO `requires_fw` decorator: they read committed fixtures
under tests/fixtures/, which are always present regardless of whether the
sibling firmware checkout exists, so they stay live and exercise the gate's
failure modes even in an absent-firmware run (the property
test_revision_constants_parity.py documents at :713-725, and D-16 measures
for that module specifically). No new environment seam is introduced here --
`FIRESTARTER_FW_ROOT` already overrides the firmware root, and
`tests/fw_presence.py:66-76` states why the marker name itself stays
hardcoded rather than becoming a second overridable knob.

Honest non-claim (F-10) -- read this before treating a GREEN run as more
than it is: this gate proves the two sides agree on LAYOUT, not on BOUNDS.
The firmware clamps `_vlen` (the version-string length) to `<= 32` and sizes
`_ready[4 + 32 + 2]` accordingly (src/firestarter.cpp:192-194); the host's
`_decode_id_frame` applies NO upper bound of its own on `params_bytes[3]`
and relies only on the runtime guard `ver_end <= len(params_bytes)`
(serial_comm.py:411). That asymmetry is safe, not a defect -- the
firmware-side clamp is what keeps the wire bounded, and the host-side guard
degrades a truncated tail to "no identity" rather than a partial string --
but it means this module's GREEN must never be read as "the host
independently proves the 32-byte ceiling too". It does not, and is not
designed to.

This module never imports from firestarter/tests/ (a different repository,
not even importable from here) or from any other gate module in this repo --
the extraction logic below is its own independent re-derivation, on both
sides of the wire, of this plan's own gate specification.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import firestarter.serial_comm
from tests.fw_presence import FW_REPO_PRESENT, FW_ROOT, fw_path, requires_fw

_HERE = Path(__file__).resolve().parent
_APP_REPO_ROOT = _HERE.parent

# ---------------------------------------------------------------------------
# Cross-repo plumbing. FIRMWARE_ACK_SOURCE doubles as the fixture-injection
# seam the planted-violation legs below `monkeypatch.setattr`. Resolved via
# `fw_path` so a present-repo rename of src/firestarter.cpp is a named
# MissingScanTargetError, never a silent skip.
# ---------------------------------------------------------------------------
FIRMWARE_ACK_SOURCE = fw_path("src", "firestarter.cpp")

# The host decoder is resolved from the imported module's own __file__, so a
# future host-side move of serial_comm.py is followed automatically rather
# than needing this constant hand-updated.
HOST_DECODER_SOURCE = Path(firestarter.serial_comm.__file__)

_FIXTURES_DIR = _HERE / "fixtures"
_FIXTURE_LITERAL_INDEX = _FIXTURES_DIR / "planted_cap03_literal_index.cpp"
_FIXTURE_TRUNCATED_LENGTH = _FIXTURES_DIR / "planted_cap03_truncated_length.cpp"

_WIRE_LAYOUT_COMMENT = (
    "[buffer_size u16 BE][hw_revision u8][ver_len u8][ver bytes][write_budget_s u16 BE]"
)


def _read_firmware_ack_source_text() -> str:
    """Read `FIRMWARE_ACK_SOURCE`'s text, failing closed.

    An absent or unreadable path is an ERROR, never a silent pass: an empty
    extraction would make every negative assertion in this module vacuously
    true. This is also the seam the planted-violation legs exercise by
    `monkeypatch.setattr`-ing `FIRMWARE_ACK_SOURCE` at module scope before
    calling any `_check_*` helper.
    """
    if not FIRMWARE_ACK_SOURCE.is_file():
        raise AssertionError(
            f"firmware ack source not found at {FIRMWARE_ACK_SOURCE} -- an "
            "absent or unreadable path must be a hard failure, never a "
            "silent pass with an empty extraction."
        )
    return FIRMWARE_ACK_SOURCE.read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """Strip `//` line comments and `/* ... */` block comments, replacing
    each stripped span with whitespace of the SAME SHAPE (a newline stays a
    newline, everything else becomes a single space) so any position offset
    computed against the result still lines up with the original file.
    Copied structurally from
    firestarter/tests/test_ack_layout_source_contract_v143.py's own
    `_strip_comments` -- only the firmware side ever needs this: the host
    side is Python, whose `#` comments never collide with any pattern this
    module searches for.
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


# ---------------------------------------------------------------------------
# Firmware-side pack-site patterns (src/firestarter.cpp:190-208).
# ---------------------------------------------------------------------------
_READY_DECL_RE = re.compile(r"uint8_t\s+_ready\s*\[\s*4\s*\+\s*32\s*\+\s*2\s*\]\s*;")
_BYTE0_RE = re.compile(
    r"_ready\[0\]\s*=\s*\(uint8_t\)\(\(\(uint16_t\)DATA_BUFFER_SIZE"
    r"\s*>>\s*8\)\s*&\s*0xFF\)\s*;"
)
_BYTE1_RE = re.compile(
    r"_ready\[1\]\s*=\s*\(uint8_t\)\(\(uint16_t\)DATA_BUFFER_SIZE\s*&\s*0xFF\)\s*;"
)
_BYTE2_IFDEF_RE = re.compile(
    r"#ifdef\s+HARDWARE_REVISION\s*"
    r"_ready\[2\]\s*=\s*\(uint8_t\)rurp_get_hardware_revision\(\)\s*;\s*"
    r"#else\s*"
    r"_ready\[2\]\s*=\s*0xFE\s*;\s*"
    r"#endif"
)
_BYTE3_RE = re.compile(r"_ready\[3\]\s*=\s*_vlen\s*;")
_MEMCPY_RE = re.compile(r"memcpy\(\s*_ready\s*\+\s*4\s*,\s*_ver\s*,\s*_vlen\s*\)\s*;")
_BUDGET_HI_RE = re.compile(
    r"_ready\[4\s*\+\s*_vlen\]\s*=\s*\(uint8_t\)\(\(_budget\s*>>\s*8\)\s*&\s*0xFF\)\s*;"
)
_BUDGET_LO_RE = re.compile(
    r"_ready\[4\s*\+\s*_vlen\s*\+\s*1\]\s*=\s*\(uint8_t\)\(_budget\s*&\s*0xFF\)\s*;"
)
_EMIT_LENGTH_RE = re.compile(
    r"LOG_OK_ID_BYTES\(\s*MSG_OK_READY\s*,\s*_ready\s*,\s*"
    r"\(uint8_t\)\(4\s*\+\s*_vlen\s*\+\s*2\)\s*\)\s*;"
)
_EMIT_ANY_RE = re.compile(
    r"LOG_OK_ID_BYTES\(\s*MSG_OK_READY\s*,\s*_ready\s*,\s*(\(uint8_t\)\([^)]*\))\s*\)"
)
_READY_BARE_INDEX_RE = re.compile(r"_ready\[\s*(\d+)\s*\]")

# ---------------------------------------------------------------------------
# Host-side decode-site patterns (firestarter/serial_comm.py, inside
# `_decode_id_frame`).
# ---------------------------------------------------------------------------
_DECODE_ID_FRAME_DEF_RE = re.compile(
    r"^([ \t]*)def _decode_id_frame\(\s*self\s*,\s*frame_len:\s*int\s*,\s*"
    r"body:\s*bytes\s*\)\s*->\s*Optional\[LogMessage\]\s*:\s*$",
    re.MULTILINE,
)
_HOST_PARAMS_SLICE_RE = re.compile(r"params_bytes\s*=\s*body\[1:-1\]")
_HOST_BUFFER_READ_RE = re.compile(
    r'struct\.unpack\(\s*">H"\s*,\s*params_bytes\[:2\]\s*\)'
)
_HOST_HWREV_RE = re.compile(r"self\.hw_revision\s*=\s*params_bytes\[2\]")
_HOST_VEREND_RE = re.compile(r"ver_end\s*=\s*4\s*\+\s*params_bytes\[3\]")
_HOST_IDENTITY_SLICE_RE = re.compile(r"params_bytes\[4:ver_end\]")
_HOST_BUDGET_READ_RE = re.compile(
    r'struct\.unpack\(\s*">H"\s*,\s*params_bytes\[\s*ver_end\s*:\s*ver_end\s*\+\s*2\s*\]\s*\)'
)
_HOST_BARE_INDEX_RE = re.compile(r"params_bytes\[(\d+)\]")


def _match_site(pattern: "re.Pattern[str]", text: str) -> tuple[str | None, int | None]:
    """Return `(matched_text, start_offset)` for the first match of
    `pattern` in `text`, or `(None, None)` if absent. A tuple, never a bare
    `re.Match`, so a missing site prints cleanly (`None`) in an assertion
    message instead of repr-ing a Match object.
    """
    m = pattern.search(text)
    if m is None:
        return (None, None)
    return (m.group(0), m.start())


def _match_group1_site(
    pattern: "re.Pattern[str]", text: str
) -> tuple[str | None, int | None]:
    """Like `_match_site`, but returns the pattern's first CAPTURING group
    rather than the whole match -- used only for `_EMIT_ANY_RE`, so a
    failure message quotes just the emitted-length expression
    (`(uint8_t)(...)`, e.g.) rather than the entire `LOG_OK_ID_BYTES(...)`
    call it sits inside.
    """
    m = pattern.search(text)
    if m is None:
        return (None, None)
    return (m.group(1), m.start(1))


def _extract_ready_pack_sites(text: str) -> dict[str, Any]:
    """Independently re-derive the firmware-side CAP-01/02/03 pack facts
    from `text` (the raw contents of either the real `src/firestarter.cpp`
    or a committed planted fixture -- both shapes are accepted, since
    neither fixture wraps the pack block in `init_programmer_framed`'s own
    function signature).

    The wire-layout comment is searched for in the RAW text (it lives
    inside a C++ comment, so it would be blanked by `_strip_comments`);
    every other fact is searched for in the comment-stripped text.
    """
    wire_layout_comment_present = _WIRE_LAYOUT_COMMENT in text
    stripped = _strip_comments(text)
    return {
        "wire_layout_comment_present": wire_layout_comment_present,
        "ready_decl": _match_site(_READY_DECL_RE, stripped),
        "byte0": _match_site(_BYTE0_RE, stripped),
        "byte1": _match_site(_BYTE1_RE, stripped),
        "byte2_block": _match_site(_BYTE2_IFDEF_RE, stripped),
        "byte3": _match_site(_BYTE3_RE, stripped),
        "memcpy": _match_site(_MEMCPY_RE, stripped),
        "budget_hi": _match_site(_BUDGET_HI_RE, stripped),
        "budget_lo": _match_site(_BUDGET_LO_RE, stripped),
        "emit_length": _match_site(_EMIT_LENGTH_RE, stripped),
        "emit_length_observed": _match_group1_site(_EMIT_ANY_RE, stripped),
        "bare_index_over_3": [
            int(n) for n in _READY_BARE_INDEX_RE.findall(stripped) if int(n) > 3
        ],
    }


def _extract_decode_id_frame_body(text: str) -> str:
    """Locate `_decode_id_frame`'s FIRST definition in `text` (the real
    `serial_comm.py`) and return its body via Python indentation (there are
    no braces to match), so a budget-reading line written anywhere ELSE in
    the module cannot satisfy the checks below.

    Only the FIRST definition is used, deliberately: `serial_comm.py` also
    defines `FaultInjectingSerialCommunicator._decode_id_frame`, a dev-only
    one-shot corrupt-and-delegate override (XACT-02 / Phase 53 Plan 02) that
    calls `super()._decode_id_frame(...)` and carries none of the
    CAP-01/02/03 decode logic itself -- the base `SerialCommunicator`
    definition, found first in the file, is the one with the facts this
    module extracts.
    """
    lines = text.splitlines()
    def_line_idx = None
    def_indent = None
    for i, line in enumerate(lines):
        m = _DECODE_ID_FRAME_DEF_RE.match(line)
        if m is not None:
            def_line_idx = i
            def_indent = len(m.group(1))
            break
    assert def_line_idx is not None, (
        "expected at least 1 definition of `_decode_id_frame` in "
        f"{HOST_DECODER_SOURCE}, found 0 -- the CAP-03 decode facts can "
        "only be extracted if there is a body to extract them from."
    )
    assert def_indent is not None  # set in lockstep with def_line_idx above
    body_lines: list[str] = []
    for line in lines[def_line_idx + 1 :]:
        if line.strip() == "":
            body_lines.append(line)
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= def_indent:
            break
        body_lines.append(line)
    return "\n".join(body_lines)


def _extract_host_offsets(text: str) -> dict[str, Any]:
    """Independently re-derive the host-side CAP-03 decode facts from
    `_decode_id_frame`'s body in `text` (the real `serial_comm.py`). Never
    imports or reuses the firmware-side extraction logic above -- the two
    sides are read by two independent parses, which is the whole point of a
    parity gate.
    """
    body = _extract_decode_id_frame_body(text)
    return {
        "params_bytes_slice": _match_site(_HOST_PARAMS_SLICE_RE, body),
        "buffer_read": _match_site(_HOST_BUFFER_READ_RE, body),
        "hw_revision_read": _match_site(_HOST_HWREV_RE, body),
        "ver_end_assignment": _match_site(_HOST_VEREND_RE, body),
        "identity_slice": _match_site(_HOST_IDENTITY_SLICE_RE, body),
        "budget_read": _match_site(_HOST_BUDGET_READ_RE, body),
        "bare_index_over_3": [
            int(n) for n in _HOST_BARE_INDEX_RE.findall(body) if int(n) > 3
        ],
    }


# ---------------------------------------------------------------------------
# Check helpers. Each is zero-argument and re-reads/re-extracts both sides
# fresh (mirroring test_revision_constants_parity.py's `_check_cmd_two_way` /
# `_check_flag_two_way`), so the SAME helper serves both the live leg and the
# planted-violation legs -- never a parallel reimplementation.
# ---------------------------------------------------------------------------
_INDEX_IDENTITY_TABLE = (
    (0, "byte0", "buffer_read"),
    (1, "byte1", "buffer_read"),
    (2, "byte2_block", "hw_revision_read"),
    (3, "byte3", "ver_end_assignment"),
)


def _check_index_identity() -> None:
    """For k in 0..3, a firmware pack site at that byte offset must
    correspond to a host read site at the same offset. k=0 and k=1 both
    correspond to the host's single big-endian u16 read
    (`struct.unpack('>H', params_bytes[:2])`), since the host decodes both
    bytes together rather than one at a time; k=2 is the hw_revision byte;
    k=3 is the ver_len byte the host's `ver_end` is computed from.
    """
    fw = _extract_ready_pack_sites(_read_firmware_ack_source_text())
    host = _extract_host_offsets(HOST_DECODER_SOURCE.read_text(encoding="utf-8"))
    errors: list[str] = []
    for index, fw_key, host_key in _INDEX_IDENTITY_TABLE:
        fw_text, _fw_pos = fw[fw_key]
        host_text, _host_pos = host[host_key]
        if fw_text is None or host_text is None:
            errors.append(
                f"index {index}: firmware site {fw_key!r} = {fw_text!r}; "
                f"host site {host_key!r} = {host_text!r}"
            )
    assert not errors, (
        "CAP-03 index-identity failures (firmware _ready[k] vs host "
        "params_bytes[k], byte offsets 0-3):\n" + "\n".join(f"  - {e}" for e in errors)
    )


def _check_budget_offset_is_computed() -> None:
    """The central CAP-03 assertion (BF-1's shape): the firmware must write
    the budget at the COMPUTED offset `4 + _vlen` / `4 + _vlen + 1`, and the
    host must read it at the COMPUTED `ver_end` -- never a fixed literal on
    either side.
    """
    fw = _extract_ready_pack_sites(_read_firmware_ack_source_text())
    host = _extract_host_offsets(HOST_DECODER_SOURCE.read_text(encoding="utf-8"))
    errors: list[str] = []
    if fw["budget_hi"][0] is None or fw["budget_lo"][0] is None:
        errors.append(
            "firmware does not write the CAP-03 budget at the computed "
            "offset '_ready[4 + _vlen]' / '_ready[4 + _vlen + 1]' -- found "
            "bare-literal _ready[N] index(es) with N > 3 instead: "
            f"{fw['bare_index_over_3']!r}. BF-1's shape: a wire layout "
            "changed on one side with nothing comparing the two -- the "
            "budget MUST be written at the computed offset '4 + _vlen', "
            "never a literal."
        )
    if host["ver_end_assignment"][0] is None:
        errors.append(
            "host does not compute 'ver_end = 4 + params_bytes[3]' -- the "
            "budget offset must be read at a COMPUTED offset, never a "
            "fixed index."
        )
    if host["budget_read"][0] is None:
        errors.append(
            "host does not read the CAP-03 budget at the computed offset "
            "'params_bytes[ver_end : ver_end + 2]'."
        )
    assert not errors, "CAP-03 budget-offset failures:\n" + "\n".join(
        f"  - {e}" for e in errors
    )


def _check_emitted_length_includes_budget() -> None:
    """Silent-capability-loss check: the byte-blob emit's length argument
    must be `(uint8_t)(4 + _vlen + 2)`. Omitting the `+ 2` still PACKS the
    budget bytes into `_ready` but never emits them -- a SILENT capability
    loss, not a loud one: the host's `write_block_budget_s` attribute would
    stay `None` forever with no error anywhere.
    """
    fw = _extract_ready_pack_sites(_read_firmware_ack_source_text())
    if fw["emit_length"][0] is not None:
        return
    observed_text, _observed_pos = fw["emit_length_observed"]
    observed = (
        observed_text
        if observed_text is not None
        else "<no LOG_OK_ID_BYTES(MSG_OK_READY, ...) emit found>"
    )
    raise AssertionError(
        "firmware's MSG_OK_READY byte-blob emit does not include the two "
        "CAP-03 budget bytes in its emitted length -- expected "
        f"'(uint8_t)(4 + _vlen + 2)', observed {observed!r}. Omitting the "
        "'+ 2' silently truncates the budget off the wire and leaves the "
        "host's write_block_budget_s attribute None forever -- a SILENT "
        "capability loss, never a loud one."
    )


# ---------------------------------------------------------------------------
# Tests -- CAP-03 layout parity (Coverage 1-6).
# ---------------------------------------------------------------------------


@requires_fw
def test_firmware_pack_order_comment_matches_the_wire_layout() -> None:
    """Coverage 1 -- the firmware source states the CAP-01/02/03 order in a
    comment, and the five pack sites appear in the SAME positional order in
    the code: buffer_size, hw_revision, ver_len, ver bytes (memcpy), then
    the write_budget_s bytes.
    """
    raw_text = _read_firmware_ack_source_text()
    assert _WIRE_LAYOUT_COMMENT in raw_text, (
        f"expected the wire-layout order comment {_WIRE_LAYOUT_COMMENT!r} "
        f"verbatim in {FIRMWARE_ACK_SOURCE} -- if the comment's wording "
        "changed, this module's own understanding of the wire layout may "
        "have gone stale too."
    )
    fw = _extract_ready_pack_sites(raw_text)
    ordered_sites = (
        ("buffer_size (CAP-01)", fw["byte0"]),
        ("hw_revision (CAP-02)", fw["byte2_block"]),
        ("ver_len (CAP-02)", fw["byte3"]),
        ("ver bytes / memcpy (CAP-02)", fw["memcpy"]),
        ("write_budget_s (CAP-03)", fw["budget_hi"]),
    )
    for label, (site_text, _pos) in ordered_sites:
        assert site_text is not None, (
            f"pack site {label!r} not found in {FIRMWARE_ACK_SOURCE}"
        )
    positions = [(label, pos) for label, (_text, pos) in ordered_sites]
    for (label_a, pos_a), (label_b, pos_b) in zip(positions, positions[1:]):
        assert pos_a < pos_b, (
            f"pack site {label_a!r} (offset {pos_a}) does not appear before "
            f"{label_b!r} (offset {pos_b}) in {FIRMWARE_ACK_SOURCE} -- the "
            "code order must match the documented wire-layout comment "
            f"{_WIRE_LAYOUT_COMMENT!r}."
        )


@requires_fw
def test_firmware_and_host_agree_on_indices_zero_through_three() -> None:
    """Coverage 2."""
    _check_index_identity()


@requires_fw
def test_budget_is_written_and_read_at_the_computed_offset() -> None:
    """Coverage 3 -- the central assertion."""
    _check_budget_offset_is_computed()


@requires_fw
def test_both_sides_use_big_endian_for_both_u16_fields() -> None:
    """Coverage 4."""
    fw = _extract_ready_pack_sites(_read_firmware_ack_source_text())
    host = _extract_host_offsets(HOST_DECODER_SOURCE.read_text(encoding="utf-8"))
    errors: list[str] = []
    if fw["byte0"][0] is None or fw["byte1"][0] is None:
        errors.append(
            "firmware buffer_size pack (bytes 0-1) is missing its '>> 8' / "
            "'& 0xFF' big-endian shape"
        )
    if fw["budget_hi"][0] is None or fw["budget_lo"][0] is None:
        errors.append(
            "firmware budget pack is missing its '>> 8' / '& 0xFF' big-endian shape"
        )
    if host["buffer_read"][0] is None:
        errors.append("host buffer_size read is missing struct.unpack('>H', ...)")
    if host["budget_read"][0] is None:
        errors.append("host budget read is missing struct.unpack('>H', ...)")
    assert not errors, "big-endian parity failures:\n" + "\n".join(
        f"  - {e}" for e in errors
    )


@requires_fw
def test_emitted_length_includes_the_two_budget_bytes() -> None:
    """Coverage 5."""
    _check_emitted_length_includes_budget()


@requires_fw
def test_host_uses_no_bare_integer_index_above_three_to_reach_the_budget() -> None:
    """Coverage 6 -- mirrors test_ack_layout_source_contract_v143.py:372-386's
    firmware-side rule on the host side: within `_decode_id_frame`, no bare
    integer subscript greater than 3 may be used to reach the budget bytes.
    """
    host = _extract_host_offsets(HOST_DECODER_SOURCE.read_text(encoding="utf-8"))
    assert host["bare_index_over_3"] == [], (
        "found a bare-integer params_bytes[N] index with N > 3 inside "
        f"_decode_id_frame's body: {host['bare_index_over_3']} -- a fixed "
        "index works for one identity-string length and silently misreads "
        "the next; anything past the variable-length identity tail must be "
        "expressed via the COMPUTED ver_end, never a literal."
    )


# ---------------------------------------------------------------------------
# Tests -- self-protection (Coverage 7-9).
# ---------------------------------------------------------------------------

_FIRMWARE_FACT_KEYS = (
    "ready_decl",
    "byte0",
    "byte1",
    "byte2_block",
    "byte3",
    "memcpy",
    "budget_hi",
    "budget_lo",
    "emit_length",
)
_HOST_FACT_KEYS = (
    "params_bytes_slice",
    "buffer_read",
    "hw_revision_read",
    "ver_end_assignment",
    "identity_slice",
    "budget_read",
)
_FIRMWARE_FACT_FLOOR = 9  # hardcoded literal -- len(_FIRMWARE_FACT_KEYS)
_HOST_FACT_FLOOR = 6  # hardcoded literal -- len(_HOST_FACT_KEYS)


@requires_fw
def test_scan_targets_are_non_vacuous() -> None:
    """Coverage 7 -- structural self-check, two halves. A silently-empty
    extraction on EITHER side would make every negative assertion above
    pass vacuously.
    """
    assert FIRMWARE_ACK_SOURCE.is_file(), (
        f"{FIRMWARE_ACK_SOURCE} does not exist -- a missing scan target "
        "must FAIL, never silently pass."
    )
    assert FIRMWARE_ACK_SOURCE.stat().st_size > 0, f"{FIRMWARE_ACK_SOURCE} is empty"

    fw = _extract_ready_pack_sites(_read_firmware_ack_source_text())
    assert fw["wire_layout_comment_present"], (
        f"the wire-layout comment is absent from {FIRMWARE_ACK_SOURCE} -- "
        "the current scan target may be stale or wrong."
    )
    fw_found = sum(1 for key in _FIRMWARE_FACT_KEYS if fw[key][0] is not None)
    assert fw_found == _FIRMWARE_FACT_FLOOR, (
        f"expected all {_FIRMWARE_FACT_FLOOR} firmware pack-site facts to "
        f"be found in {FIRMWARE_ACK_SOURCE}, found {fw_found} -- an "
        "incomplete extraction would make every negative assertion in this "
        "module pass vacuously."
    )

    assert HOST_DECODER_SOURCE.is_file(), f"{HOST_DECODER_SOURCE} does not exist"
    assert HOST_DECODER_SOURCE.resolve().is_relative_to(_APP_REPO_ROOT), (
        f"{HOST_DECODER_SOURCE} resolves outside the app repo root "
        f"({_APP_REPO_ROOT}) -- a naive future copy of this module into "
        "another directory must fail loudly here, not scan nothing and "
        "exit 0."
    )
    host = _extract_host_offsets(HOST_DECODER_SOURCE.read_text(encoding="utf-8"))
    host_found = sum(1 for key in _HOST_FACT_KEYS if host[key][0] is not None)
    assert host_found == _HOST_FACT_FLOOR, (
        f"expected all {_HOST_FACT_FLOOR} host decode-site facts to be "
        f"found in {HOST_DECODER_SOURCE}, found {host_found} -- an "
        "incomplete extraction would make every negative assertion in this "
        "module pass vacuously."
    )


def test_gate_fails_closed_on_an_unreadable_firmware_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Coverage 8 -- an unreadable/absent firmware ack-source path must be
    an ERROR, never a silent pass -- an empty extraction would make every
    negative assertion in this module vacuously true. No `requires_fw`: this
    leg never reads the real firmware file, so it must stay live even in an
    absent-firmware run.
    """
    missing = tmp_path / "does_not_exist.cpp"
    monkeypatch.setattr(sys.modules[__name__], "FIRMWARE_ACK_SOURCE", missing)
    with pytest.raises(AssertionError, match="firmware ack source not found"):
        _check_index_identity()


_NEEDLE_SKIP_CALL = "pytest" + ".skip"
_NEEDLE_SKIPIF_MARKER = "mark" + ".skipif"
_NEEDLE_DEPENDENCY_SKIP_CALL = "importor" + "skip"

_ALL_SELF_CHECK_NEEDLES = (
    ("a pytest skip call", _NEEDLE_SKIP_CALL),
    ("a pytest skipif marker", _NEEDLE_SKIPIF_MARKER),
    ("a pytest dependency-skip call", _NEEDLE_DEPENDENCY_SKIP_CALL),
)


def test_this_module_cannot_be_silently_skipped() -> None:
    """Coverage 9 -- this module's own source contains no runtime
    skip-bypass call, no hand-authored skip-marker decorator, and no
    dependency-skip call anywhere. The only skip marker this module ever
    uses is the imported `requires_fw`, keyed on real firmware-repo
    presence -- never a bespoke one redefined here.
    """
    own_text = Path(__file__).read_text(encoding="utf-8")
    assert _NEEDLE_SKIP_CALL not in own_text, (
        "expected no " + _NEEDLE_SKIP_CALL + " call anywhere in this module "
        "-- a missing or empty scan target must FAIL, never SKIP."
    )
    assert _NEEDLE_SKIPIF_MARKER not in own_text, (
        "expected no @pytest." + _NEEDLE_SKIPIF_MARKER + " decorator "
        "hand-authored anywhere in this module (requires_fw is imported, "
        "never redefined here)."
    )
    assert ("pytest." + _NEEDLE_DEPENDENCY_SKIP_CALL) not in own_text, (
        "expected no pytest." + _NEEDLE_DEPENDENCY_SKIP_CALL + " call "
        "anywhere in this module -- a missing dependency must FAIL, never "
        "SKIP."
    )


def test_own_needles_do_not_appear_verbatim_in_this_module() -> None:
    """Coverage 9's twin -- the concatenation-built needle self-check: each
    needle built by concatenation above must appear NOWHERE verbatim in this
    module's own source, or a future "simplification" could silently
    un-concatenate one and this discipline would stop being machine-checked.
    """
    own_text = Path(__file__).read_text(encoding="utf-8")
    for label, needle in _ALL_SELF_CHECK_NEEDLES:
        assert needle not in own_text, (
            f"the concatenation-built needle for {label} appears verbatim "
            "in this module's own source -- rebuild it from at least two "
            "literal pieces so this gate cannot match itself."
        )


# ---------------------------------------------------------------------------
# Tests -- D-18 planted violations. NO `requires_fw` on either: both read a
# committed fixture under tests/fixtures/, always present regardless of
# whether the sibling firmware checkout exists, so both stay live in an
# absent-firmware run.
# ---------------------------------------------------------------------------


def _git_hash_object(path: Path) -> str:
    """Resolve `git` fail-closed and hash-object `path` inside FW_ROOT.
    Reimplemented here (not imported from tests/test_py32_flash_map_host.py
    or any other gate module) -- see this module's own docstring.
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


def test_planted_literal_index_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-18's first plant: planted_cap03_literal_index.cpp writes the
    CAP-03 budget at the literal indices 13/14 instead of the computed
    offset `4 + _vlen` / `4 + _vlen + 1` -- BF-1's exact shape. Calls the
    SAME `_check_budget_offset_is_computed` helper the live leg
    (test_budget_is_written_and_read_at_the_computed_offset) calls, never a
    parallel reimplementation.
    """
    assert _FIXTURE_LITERAL_INDEX.is_file(), (
        f"committed fixture missing: {_FIXTURE_LITERAL_INDEX}"
    )
    # V12 ceremony: capture the REAL firmware source (never the fixture)
    # BEFORE any monkeypatch, so the "after" comparison below proves this
    # plant never touched it.
    real_ack_source = FIRMWARE_ACK_SOURCE
    before_sha = _git_hash_object(real_ack_source) if FW_REPO_PRESENT else None

    monkeypatch.setattr(
        sys.modules[__name__], "FIRMWARE_ACK_SOURCE", _FIXTURE_LITERAL_INDEX
    )
    with pytest.raises(AssertionError) as excinfo:
        _check_budget_offset_is_computed()
    message = str(excinfo.value)
    assert "13" in message
    assert "4 + _vlen" in message
    # Leg isolation: the OTHER plant's distinguishing phrase must be absent.
    assert "capability loss" not in message

    if FW_REPO_PRESENT:
        after_sha = _git_hash_object(real_ack_source)
        assert before_sha == after_sha, (
            "the real firmware ack source's git blob hash changed during "
            "this planted-violation run -- the plant must never touch the "
            "real file."
        )
        assert _git_porcelain(FW_ROOT) == "", (
            "the sibling firmware repo is not clean after this "
            "planted-violation run -- the plant must never write into the "
            "real firmware checkout."
        )


def test_planted_truncated_emitted_length_is_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-18's second plant: planted_cap03_truncated_length.cpp packs the
    CAP-03 budget bytes correctly but omits them from the emitted length --
    a silent capability loss. Calls the SAME
    `_check_emitted_length_includes_budget` helper the live leg
    (test_emitted_length_includes_the_two_budget_bytes) calls.
    """
    assert _FIXTURE_TRUNCATED_LENGTH.is_file(), (
        f"committed fixture missing: {_FIXTURE_TRUNCATED_LENGTH}"
    )
    real_ack_source = FIRMWARE_ACK_SOURCE
    before_sha = _git_hash_object(real_ack_source) if FW_REPO_PRESENT else None

    monkeypatch.setattr(
        sys.modules[__name__], "FIRMWARE_ACK_SOURCE", _FIXTURE_TRUNCATED_LENGTH
    )
    with pytest.raises(AssertionError) as excinfo:
        _check_emitted_length_includes_budget()
    message = str(excinfo.value)
    assert "(uint8_t)(4 + _vlen)" in message
    assert "+ 2" in message
    # Leg isolation: the OTHER plant's distinguishing phrase must be absent.
    assert "BF-1" not in message

    if FW_REPO_PRESENT:
        after_sha = _git_hash_object(real_ack_source)
        assert before_sha == after_sha, (
            "the real firmware ack source's git blob hash changed during "
            "this planted-violation run -- the plant must never touch the "
            "real file."
        )
        assert _git_porcelain(FW_ROOT) == "", (
            "the sibling firmware repo is not clean after this "
            "planted-violation run -- the plant must never write into the "
            "real firmware checkout."
        )
