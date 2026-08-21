"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 149 Plan 05 (PGSZ-03, D-18) -- the cross-repo JSON-key parity gate
constants.py:145's "Firmware sync" note has claimed, without enforcement,
since the note was first written. `firestarter/src/json_parser.c` gained the
`page-size` wire key in plan 04; this module is what turns that comment into
a machine-checked fact.

Two properties make this gate worth more than a string comparison. First,
the page-size assertion proves the key is DISPATCHED, not merely declared --
a PROGMEM string that never appears in `key_parsers[]` exists on the wire
and never matches anything a host sends, which is the hole a naive presence
check misses. Second, the two planted-violation legs below carry NO
`requires_fw` decorator: they read committed fixtures under
`tests/fixtures/`, always present regardless of whether the sibling firmware
checkout exists, so they stay live and exercise the gate's failure modes
even in app CI, which has no sibling checkout at all.

Cross-repo plumbing: `requires_fw` (from `tests.fw_presence`) is the ONLY
skip marker this module uses, keyed on the sibling `../firestarter/.git`
marker -- immune to any in-repo firmware rename. `FIRMWARE_PARSER_SOURCE`
(resolved via `fw_path` at module scope) doubles as the fixture-injection
seam the two planted-violation legs `monkeypatch.setattr`. Resolving it at
module scope is what makes a firmware RENAME of `src/json_parser.c` a hard
`MissingScanTargetError` failure rather than a silent skip -- see
`tests/fw_presence.py:117-140`.

Direction asymmetry (locked at planning time, D-18). Python -> firmware is
asserted TOTALLY: all 3 `JSON_KEY_*` constants in `firestarter.constants`
must appear as PROGMEM key strings in the firmware. Firmware -> Python is
asserted with a named exemption tuple, `_EXEMPT_FIRMWARE_KEYS`, covering the
8 firmware keys that legitimately have no Python constant (bus-config
fields, algorithm dispatch, and the vpp_mv/pin-count/chip-id trio the host
already sends by other means). The exemption tuple's own completeness is
asserted too (`test_the_exemption_tuple_is_complete_and_has_no_stale_entries`)
so a new firmware key must be deliberately classified -- either given a
Python constant or added to the exemption tuple with a reason -- rather than
silently falling through either side.

This module never imports from `firestarter/tests/` (a different repository,
not even importable from here) or from any other gate module in this repo --
the extraction logic below is its own independent re-derivation of this
plan's own gate specification.

Honest non-claim: a GREEN run of this gate proves the two repos agree on a
KEY STRING and that the string is wired into the firmware's dispatch table.
It proves nothing about the field the C getter stores into, nothing about
which handler reads that field, and nothing about hardware -- see
`149-PAGE-SIZE.md` §"Cross-repo parity evidence (plan 05)" for the full
statement of this gate's ceiling.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import firestarter.constants as constants
from firestarter.constants import JSON_KEY_PAGE_SIZE
from tests.fw_presence import FW_REPO_PRESENT, FW_ROOT, fw_path, requires_fw

_HERE = Path(__file__).resolve().parent
_FIXTURES_DIR = _HERE / "fixtures"

# ---------------------------------------------------------------------------
# Cross-repo plumbing. FIRMWARE_PARSER_SOURCE doubles as the fixture-
# injection seam the planted-violation legs below `monkeypatch.setattr`.
# Resolved via `fw_path` so a present-repo rename of src/json_parser.c is a
# named MissingScanTargetError, never a silent skip.
# ---------------------------------------------------------------------------
FIRMWARE_PARSER_SOURCE = fw_path("src", "json_parser.c")

_FIXTURE_KEY_STRING_DRIFT = _FIXTURES_DIR / "planted_json_parser_key_string_drift.c"
_FIXTURE_UNDISPATCHED_KEY = _FIXTURES_DIR / "planted_json_parser_undispatched_key.c"

# The 8 firmware PROGMEM keys that legitimately have no `JSON_KEY_*` Python
# constant. A firmware key added later must be DELIBERATELY classified,
# either by gaining a Python constant or by being added here with a reason
# -- `test_the_exemption_tuple_is_complete_and_has_no_stale_entries` below is
# what forces that choice rather than letting a new key silently fall
# through unclassified.
_EXEMPT_FIRMWARE_KEYS = frozenset(
    {
        "memory-size",  # get_memory_size -> handle->mem_size; no host-side named constant
        "address",  # get_address -> handle->address
        "flags",  # get_flags -> handle->ctrl_flags
        "chip-id",  # get_chip_id -> handle->chip_id
        "pin-count",  # get_pin_count -> handle->pins
        "pulse-delay",  # get_delay -> handle->pulse_delay
        "vpp_mv",  # get_vpp_mv -> handle->vpp_mv
        "algorithm",  # get_algorithm -> handle->protocol (the primary dispatch key)
    }
)

# ---------------------------------------------------------------------------
# Extraction regexes. Both tolerate arbitrary whitespace around the array
# brackets and before PROGMEM -- `key_read_strobe[]` carries aligned extra
# spaces in the real source, and a regex requiring exactly one space would
# silently extract 10 of 11 keys and weaken every downstream assertion.
# ---------------------------------------------------------------------------
_KEY_STRING_RE = re.compile(
    r'const\s+char\s+(?P<ident>\w+)\s*\[\s*\]\s*PROGMEM\s*=\s*"(?P<key>[^"]*)"\s*;'
)
_KEY_PARSERS_TABLE_RE = re.compile(
    r"key_parsers\s*\[\s*\]\s*PROGMEM\s*=\s*\{(?P<body>.*?)\};", re.DOTALL
)
_DISPATCH_IDENT_RE = re.compile(r"\bkey_\w+\b")


def _read_firmware_parser_source_text() -> str:
    """Read `FIRMWARE_PARSER_SOURCE`'s text, failing closed.

    An absent or unreadable path is an ERROR, never a silent pass: an empty
    extraction would make every negative assertion in this module
    vacuously true. This is also the seam the planted-violation legs
    exercise by `monkeypatch.setattr`-ing `FIRMWARE_PARSER_SOURCE` at
    module scope before calling any `_check_*`/`_extract_*` helper.
    """
    if not FIRMWARE_PARSER_SOURCE.is_file():
        raise AssertionError(
            f"firmware parser source not found at {FIRMWARE_PARSER_SOURCE} -- "
            "an absent or unreadable path must be a hard failure, never a "
            "silent pass with an empty extraction."
        )
    return FIRMWARE_PARSER_SOURCE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Shared helpers. Each is zero-argument and re-reads/re-extracts fresh from
# `FIRMWARE_PARSER_SOURCE` (mirroring test_revision_constants_parity.py's
# `_check_cmd_two_way` / `_check_flag_two_way`), so the SAME helper serves
# both the live legs and the planted-violation legs -- never a parallel
# reimplementation.
# ---------------------------------------------------------------------------


def _extract_key_map() -> dict[str, str]:
    """Return {identifier: key_string} for every PROGMEM key-string
    declaration in the current `FIRMWARE_PARSER_SOURCE`."""
    source_text = _read_firmware_parser_source_text()
    return {
        m.group("ident"): m.group("key") for m in _KEY_STRING_RE.finditer(source_text)
    }


def _extract_dispatch_identifiers() -> set[str]:
    """Return every `key_*` identifier appearing inside the `key_parsers[]`
    initializer body (`re.DOTALL` captures the full body across many
    lines)."""
    source_text = _read_firmware_parser_source_text()
    m = _KEY_PARSERS_TABLE_RE.search(source_text)
    if m is None:
        return set()
    return set(_DISPATCH_IDENT_RE.findall(m.group("body")))


def _check_page_size_key_present_and_dispatched() -> None:
    """The central D-18 assertion: `JSON_KEY_PAGE_SIZE` appears among the
    extracted key VALUES, and the identifier bound to it also appears
    inside the `key_parsers[]` dispatch body. Both halves in one check,
    because a string that exists but is never dispatched is the specific
    hole this gate closes. The live leg and both planted fixtures call this
    SAME helper -- never a parallel reimplementation.
    """
    key_map = _extract_key_map()
    ident_for_page_size = None
    for ident, key in key_map.items():
        if key == JSON_KEY_PAGE_SIZE:
            ident_for_page_size = ident
            break
    if ident_for_page_size is None:
        raise AssertionError(
            "no PROGMEM key string equal to JSON_KEY_PAGE_SIZE "
            f"({JSON_KEY_PAGE_SIZE!r}) was found in {FIRMWARE_PARSER_SOURCE} "
            f"-- extracted key strings: {sorted(key_map.values())!r}"
        )
    dispatched = _extract_dispatch_identifiers()
    if ident_for_page_size not in dispatched:
        raise AssertionError(
            f"the page-size key string {JSON_KEY_PAGE_SIZE!r} is declared as "
            f"{ident_for_page_size!r} but that identifier does not appear "
            "inside the key_parsers[] dispatch body -- a PROGMEM string that "
            "is declared but never dispatched exists on the wire and never "
            "matches anything a host sends."
        )


def _check_every_dispatched_identifier_is_declared() -> None:
    """The reverse of the check above: every `key_*` identifier appearing
    in `key_parsers[]` must have a PROGMEM declaration -- catches a table
    row referring to a string that was renamed away.
    """
    key_map = _extract_key_map()
    dispatched = _extract_dispatch_identifiers()
    undeclared = sorted(dispatched - set(key_map))
    assert not undeclared, (
        "the following identifier(s) appear inside key_parsers[] but have "
        f"no PROGMEM key-string declaration: {undeclared} -- a table row "
        "referring to a string that was renamed away."
    )


def _discover_json_key_constants() -> dict[str, str]:
    """Every `JSON_KEY_*` string constant on `firestarter.constants`,
    discovered by introspection rather than a hardcoded name list -- a
    fourth constant must be noticed here, not silently unchecked.
    """
    return {
        name: value
        for name, value in vars(constants).items()
        if name.startswith("JSON_KEY_") and isinstance(value, str)
    }


# ---------------------------------------------------------------------------
# Tests -- live legs. Decorated with `@requires_fw` because each reads the
# real firmware tree.
# ---------------------------------------------------------------------------


@requires_fw
def test_scan_targets_are_non_vacuous() -> None:
    """A regex matching nothing would make every downstream assertion in
    this module trivially satisfiable. Assert the extracted key map is
    non-empty BEFORE any property is asserted, with a message saying the
    regex drifted rather than the source.
    """
    key_map = _extract_key_map()
    assert key_map, (
        f"extracted zero PROGMEM key strings from {FIRMWARE_PARSER_SOURCE} "
        "-- the extraction regex likely drifted from the source shape, not "
        "that the firmware source carries no keys."
    )


@requires_fw
def test_page_size_key_string_matches_constants_py() -> None:
    """`JSON_KEY_PAGE_SIZE` appears among the extracted key values, and the
    identifier bound to it also appears inside the `key_parsers[]` body --
    declared AND dispatched, closing constants.py:145's previously
    unenforced "Firmware sync" note.
    """
    _check_page_size_key_present_and_dispatched()


@requires_fw
def test_every_json_key_constant_maps_to_a_firmware_key() -> None:
    """Python -> firmware, TOTAL over all `JSON_KEY_*` constants discovered
    by introspecting `firestarter.constants` -- never a hardcoded name
    list, so a fourth constant is noticed rather than silently unchecked.
    """
    json_keys = _discover_json_key_constants()
    assert len(json_keys) == 3, (
        "expected exactly 3 JSON_KEY_* constants in firestarter.constants, "
        f"found {len(json_keys)}: {sorted(json_keys)} -- a new constant "
        "must be noticed here, not silently unmapped."
    )
    firmware_keys = set(_extract_key_map().values())
    errors = [
        f"{name} = {value!r} has no matching firmware PROGMEM key string"
        for name, value in sorted(json_keys.items())
        if value not in firmware_keys
    ]
    assert not errors, "Python -> firmware parity failures:\n" + "\n".join(
        f"  - {e}" for e in errors
    )


@requires_fw
def test_every_firmware_key_maps_back_or_is_named_exempt() -> None:
    """Firmware -> Python, with `_EXEMPT_FIRMWARE_KEYS` covering the keys
    that legitimately have no Python constant. Offenders list, then assert
    empty, naming every offender.
    """
    firmware_keys = set(_extract_key_map().values())
    json_key_values = set(_discover_json_key_constants().values())
    errors = [
        key
        for key in sorted(firmware_keys)
        if key not in json_key_values and key not in _EXEMPT_FIRMWARE_KEYS
    ]
    assert not errors, (
        "firmware key(s) with no Python JSON_KEY_* constant and no named "
        f"exemption: {errors} -- classify deliberately, either by adding a "
        "Python constant or adding this key to _EXEMPT_FIRMWARE_KEYS with a "
        "reason."
    )


@requires_fw
def test_the_exemption_tuple_is_complete_and_has_no_stale_entries() -> None:
    """Every member of `_EXEMPT_FIRMWARE_KEYS` is actually present in the
    firmware source (no stale exemptions), and the exemption set plus the
    Python-mapped set together cover every extracted firmware key exactly
    -- what stops the exemption tuple becoming a dumping ground.
    """
    firmware_keys = set(_extract_key_map().values())
    stale = sorted(_EXEMPT_FIRMWARE_KEYS - firmware_keys)
    assert not stale, (
        f"the following exempted key(s) are no longer present in the "
        f"firmware source: {stale} -- a stale exemption hides a key that no "
        "longer exists; remove it."
    )
    json_key_values = set(_discover_json_key_constants().values())
    covered = _EXEMPT_FIRMWARE_KEYS | json_key_values
    assert covered == firmware_keys, (
        "the exemption set plus the Python-mapped set do not exactly cover "
        f"the extracted firmware key set. covered={sorted(covered)!r} "
        f"extracted={sorted(firmware_keys)!r} -- every firmware key must be "
        "EITHER Python-mapped OR named-exempt, with no third bucket."
    )


@requires_fw
def test_every_dispatched_identifier_has_a_declared_key_string() -> None:
    """The reverse of the page-size leg above: every `key_*` identifier
    appearing in `key_parsers[]` has a PROGMEM declaration.
    """
    _check_every_dispatched_identifier_is_declared()


@requires_fw
def test_gate_fails_closed_on_an_unreadable_firmware_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nonexistent path under a present repo must raise, not skip.
    `fw_path` raising `MissingScanTargetError` is the rename detector; this
    leg proves the module's OWN read helper fails closed too, the same
    property, one layer in.
    """
    missing = tmp_path / "does_not_exist.c"
    monkeypatch.setattr(sys.modules[__name__], "FIRMWARE_PARSER_SOURCE", missing)
    with pytest.raises(AssertionError, match="firmware parser source not found"):
        _check_page_size_key_present_and_dispatched()


_NEEDLE_SKIP_CALL = "pytest" + ".skip"
_NEEDLE_SKIPIF_MARKER = "mark" + ".skipif"
_NEEDLE_DEPENDENCY_SKIP_CALL = "importor" + "skip"


@requires_fw
def test_this_module_cannot_be_silently_skipped() -> None:
    """`requires_fw` is the ONLY skip marker this module uses; assert no
    other skip-bypass call, hand-authored skip-marker decorator, or
    dependency-skip call exists anywhere in this module's own source.
    """
    own_text = Path(__file__).read_text(encoding="utf-8")
    assert _NEEDLE_SKIP_CALL not in own_text, (
        "expected no " + _NEEDLE_SKIP_CALL + " call anywhere in this module "
        "-- a missing or empty scan target must FAIL, never SKIP."
    )
    assert _NEEDLE_SKIPIF_MARKER not in own_text, (
        "expected no hand-authored skip-marker decorator anywhere in this "
        "module (requires_fw is imported, never redefined here)."
    )
    assert ("pytest." + _NEEDLE_DEPENDENCY_SKIP_CALL) not in own_text, (
        "expected no pytest." + _NEEDLE_DEPENDENCY_SKIP_CALL + " call "
        "anywhere in this module -- a missing dependency must FAIL, never "
        "SKIP."
    )


# ---------------------------------------------------------------------------
# Tests -- D-18 planted violations. NO `requires_fw` on either: both read a
# committed fixture under tests/fixtures/, always present regardless of
# whether the sibling firmware checkout exists, so both stay live in an
# absent-firmware run (the property this gate's own D-18 precondition
# requires -- see the module docstring).
# ---------------------------------------------------------------------------


def _git_hash_object(path: Path) -> str:
    """Resolve `git` fail-closed and hash-object `path` inside FW_ROOT.
    Copied from `tests/test_cap03_ack_layout_parity.py` (not reinvented)
    per this plan's own instruction.
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


def test_planted_key_string_drift_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-18's first plant: `planted_json_parser_key_string_drift.c` spells
    the page-size PROGMEM string with an underscore ("page_size") instead
    of the wire's hyphen ("page-size") -- Pitfall 10's exact shape: the
    internal database key and the wire key differ by one character, and a
    firmware string written against the wrong one dispatches on a key the
    host never sends. Calls the SAME `_check_page_size_key_present_and_dispatched`
    helper the live leg (`test_page_size_key_string_matches_constants_py`)
    calls, never a parallel reimplementation.
    """
    assert _FIXTURE_KEY_STRING_DRIFT.is_file(), (
        f"committed fixture missing: {_FIXTURE_KEY_STRING_DRIFT}"
    )
    # V12 ceremony: capture the REAL firmware source (never the fixture)
    # BEFORE any monkeypatch, so the "after" comparison below proves this
    # plant never touched it.
    real_source = FIRMWARE_PARSER_SOURCE
    before_sha = _git_hash_object(real_source) if FW_REPO_PRESENT else None

    monkeypatch.setattr(
        sys.modules[__name__], "FIRMWARE_PARSER_SOURCE", _FIXTURE_KEY_STRING_DRIFT
    )
    with pytest.raises(AssertionError) as excinfo:
        _check_page_size_key_present_and_dispatched()
    message = str(excinfo.value)
    assert "page_size" in message
    assert JSON_KEY_PAGE_SIZE in message
    # Leg isolation: the OTHER plant's distinguishing phrase must be absent.
    assert "does not appear inside the key_parsers" not in message

    if FW_REPO_PRESENT:
        after_sha = _git_hash_object(real_source)
        assert before_sha == after_sha, (
            "the real firmware parser source's git blob hash changed during "
            "this planted-violation run -- the plant must never touch the "
            "real file."
        )
        assert _git_porcelain(FW_ROOT) == "", (
            "the sibling firmware repo is not clean after this "
            "planted-violation run -- the plant must never write into the "
            "real firmware checkout."
        )


def test_planted_undispatched_key_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-18's second plant: `planted_json_parser_undispatched_key.c` spells
    the page-size PROGMEM string correctly but OMITS its `key_parsers[]`
    row -- the declared-but-unwired hole: the string exists, a naive
    presence check passes, and the key is never dispatched at runtime.
    Calls the SAME `_check_page_size_key_present_and_dispatched` helper the
    live leg calls.
    """
    assert _FIXTURE_UNDISPATCHED_KEY.is_file(), (
        f"committed fixture missing: {_FIXTURE_UNDISPATCHED_KEY}"
    )
    real_source = FIRMWARE_PARSER_SOURCE
    before_sha = _git_hash_object(real_source) if FW_REPO_PRESENT else None

    monkeypatch.setattr(
        sys.modules[__name__], "FIRMWARE_PARSER_SOURCE", _FIXTURE_UNDISPATCHED_KEY
    )
    with pytest.raises(AssertionError) as excinfo:
        _check_page_size_key_present_and_dispatched()
    message = str(excinfo.value)
    assert "does not appear inside the key_parsers" in message
    # Leg isolation: the OTHER plant's distinguishing phrase must be absent.
    assert "no PROGMEM key string equal to JSON_KEY_PAGE_SIZE" not in message

    if FW_REPO_PRESENT:
        after_sha = _git_hash_object(real_source)
        assert before_sha == after_sha, (
            "the real firmware parser source's git blob hash changed during "
            "this planted-violation run -- the plant must never touch the "
            "real file."
        )
        assert _git_porcelain(FW_ROOT) == "", (
            "the sibling firmware repo is not clean after this "
            "planted-violation run -- the plant must never write into the "
            "real firmware checkout."
        )
