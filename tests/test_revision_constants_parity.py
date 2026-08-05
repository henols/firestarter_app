"""
Project Name: Firestarter
Copyright (c) 2025 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 34 Plan 05 — RURP_HARDWARE_REVISIONS Python parity gate.

Hard pytest parity assertion enforcing the byte values of the Python
`REVISION_*` block in `firestarter/constants.py` against the firmware
enum at `firestarter/include/rurp_shield.h:25-31` (Phase 34 D-08
single-atomic-commit substrate; VALIDATION Dim 3 / Dim 6 stronger
coverage — Wave 0 optional toggle activated).

If a future firmware-side enum drift sneaks in without a matching
Python update, this test FAILs at pytest time so the cross-repo
invariant is enforced at commit-test time (not at runtime in the
field).

Phase 36 Plan 02 — Extend to COMMAND_*/FLAG_*/CTRL_* blocks (TEST-04).

Adds three `skipif`-guarded functions that assert all COMMAND_*, FLAG_*,
and CTRL_* Python constants against their hard-coded firmware-header
literals. The `skipif` guard keys on `firestarter/include/firestarter.h`
existence — if that header is absent the firmware checkout is not present
and the new assertions skip cleanly (host-only milestone; CI may not have
the firmware sub-repo). CTRL_* mirrors `firestarter/include/rurp_pinout.h`
(not `firestarter.h`); the same `firestarter.h` proxy covers both headers
since they live alongside each other in the firmware checkout (RESEARCH
Open Question 1, resolved).

Phase 120 Plan 07 — Rebuild the COMMAND_*/FLAG_* legs into a real
header-parsing two-way gate (HOST-03).

The two `skipif`-guarded functions this plan replaces
(`test_command_values_match_firmware`, `test_flag_values_match_firmware`)
were 100% hollow with respect to firmware drift: they asserted hardcoded
Python literals with the corresponding `firestarter.h` define named only
in a trailing comment, and never actually read the header. That is
precisely why `CMD_SDP_UNLOCK 9` / `CMD_SDP_LOCK 10` landed in Phase 119
unnoticed by this file. This plan replaces both legs with a real,
bidirectional, header-parsing gate:

  - `_strip_comments` + a depth-tracking `#define` extractor read
    `firestarter/include/firestarter.h` (via the `FIRMWARE_HEADER` path
    constant below, which now doubles as BOTH the skipif-guard proxy AND
    the fixture-injection seam the planted-violation legs below
    monkeypatch) and yield every `CMD_*`/`FLAG_*` define, its raw value
    token, and its preprocessor nesting depth. Every match is yielded —
    never filtered by whether its value parses as an integer, because
    that would silently exempt `CMD_FRAME_MAX` (whose value is the macro
    `DATA_BUFFER_SIZE`, not a literal).
  - `_EXEMPT_FW_TO_HOST` is a frozen, deliberately-NOT-auto-derived
    four-entry name-PAIR map (never a skip-set) covering the only four
    firmware names with no direct 1:1 `CMD_` → `COMMAND_` correspondence:
    `CMD_IDLE` (no host counterpart at all — firmware-internal state),
    `CMD_FRAME_MAX` (has its own dedicated gate, `test_cmd_frame_max_parity`,
    below), and the `#ifdef DEV_TOOLS`-conditional pair `CMD_DEV_ADDRESS` /
    `CMD_DEV_REGISTER` — the latter is also NAME-MISMATCHED: firmware is
    singular, the host is plural (`COMMAND_DEV_REGISTERS`, which has
    callers and must never be renamed to satisfy this gate).
  - `test_every_firmware_cmd_define_maps_two_way_to_constants_py` and
    `test_every_firmware_flag_define_maps_two_way_to_constants_py` assert
    BOTH directions: every firmware define maps to a host constant of the
    same value (or a named exemption), AND every host constant traces back
    to a firmware define — so a host constant with no firmware backing
    also fails the gate, not only the reverse.
  - `test_every_firmware_cmd_has_a_command_names_entry` (D-13) is a
    SEPARATE leg from value parity: it asserts every non-exempt CMD_*'s
    mapped host constant is also a key in `COMMAND_NAMES`, since
    `COMMAND_NAMES[cmd]` is dereferenced by `_setup_operation`
    (`eprom_operations.py:329`) and again by `_operation_context`
    (`:405`) — a missing entry is a `KeyError` at operation setup, not a
    cosmetic display gap. [Corrected 2026-08-03, RETIRE-08/D-11: the
    original `301`/`377` citation had staled; function names now lead,
    with the line number alongside.]
  - `test_conditionally_compiled_defines_are_exactly_the_dev_tools_pair`
    turns "these two are `#ifdef DEV_TOOLS`-conditional" from an assumption
    living only in a comment into a machine-checked fact over the parsed
    depth values.
  - Five further legs (`test_planted_*`, `test_missing_command_names_entry_is_detected`,
    `test_gate_fails_closed_on_an_unreadable_header_path`) prove the gate
    can actually fail: three isolated planted-violation fixtures under
    `tests/fixtures/` (one drift, one host-missing, one firmware-missing —
    three separate files rather than one three-drift file, because a
    fixture failing for two reasons at once could not prove which check
    fired), a `monkeypatch.delitem` on `COMMAND_NAMES` distinguishing the
    crash path from the drift path, and a fail-closed leg proving an
    unreadable header path is an ERROR, never a silent pass with an empty
    define set (an empty set would make every downstream assertion
    vacuously true).

**Known, explained, residual gap — split by BASE-02 (Phase 123 Plan 08):**
in host-only CI, the shared repo-presence marker (`tests/fw_presence.py`,
keyed on `../firestarter/.git`) is absent and every header-reading leg
above skips — so a host-only PR does NOT catch a missing `COMMAND_NAMES`
entry or a firmware/host value drift by itself. Splitting `COMMAND_NAMES`
coverage into its own always-on test was considered and declined, in
favour of keeping ONE gate with the shared `requires_fw` skip retained
(host-only CI must stay green). The cost of that choice is recorded here
rather than silently carried. BASE-02 changed WHAT the skip is keyed on
(the repo marker, immune to any in-repo firmware rename) but not THAT it
skips in host-only CI — a present repo with a renamed scan target is now a
hard failure instead, never a silent skip. The three planted-violation legs
and the fail-closed leg below partially offset the host-only-CI gap: they
read files under `tests/fixtures/` or a `tmp_path`, which are always
present regardless of firmware-checkout presence, so those four legs do NOT
skip in host-only CI even though they cannot exercise the REAL header
there.
"""

import re
import sys
from pathlib import Path

import pytest

from firestarter import constants
from firestarter.constants import (
    REVISION_0,
    REVISION_1,
    REVISION_2_0,
    REVISION_2_1,
    REVISION_2_2,
    REVISION_2_3,
    REVISION_UNKNOWN,
)
from tests.fw_presence import fw_path, requires_fw

# ---------------------------------------------------------------------------
# Firmware-checkout presence guard (Phase 36 TEST-04 extension; rekeyed onto
# the shared `tests/fw_presence.py` helper by Phase 123 Plan 08, BASE-02).
#
# Repo presence is now decided ONCE, in `fw_presence.py`, keyed on
# `../firestarter/.git` -- immune to any in-repo firmware rename. `requires_fw`
# is the ONLY skip marker this module uses. When present, rurp_pinout.h is
# always alongside firestarter.h (same include/ directory), so the one
# `fw_path` target below covers both headers (RESEARCH Open Question 1,
# resolved).
#
# Phase 120 Plan 07: FIRMWARE_HEADER now doubles as a SECOND seam beyond the
# repo-presence gate above -- it is the fixture-injection point the
# planted-violation legs below `monkeypatch.setattr` to point the rebuilt
# gate at a committed fixture under tests/fixtures/ instead of the real,
# untouched firestarter.h. Resolved via `fw_path` so a present-repo-renamed
# header is a named `MissingScanTargetError`, never a silent skip.
# ---------------------------------------------------------------------------
FIRMWARE_HEADER = fw_path("include", "firestarter.h")


def test_revision_byte_values_match_firmware_enum():
    """Assert each REVISION_* byte value matches the firmware enum at
    `firestarter/include/rurp_shield.h:25-31` (post-Plan-02 HEAD). This is
    the Phase 34 D-08 cross-repo parity invariant — drift on either side
    fails the gate at pytest time."""
    assert REVISION_0 == 0x00
    assert REVISION_1 == 0x01
    assert REVISION_2_0 == 0x02
    assert REVISION_2_1 == 0x03
    assert REVISION_2_2 == 0x04
    assert REVISION_2_3 == 0x05  # NEW Phase 34
    assert REVISION_UNKNOWN == 0xFE  # NEW Phase 34
    # 0xFF is reserved as the EEPROM-override-absent sentinel — NOT a REVISION_ value.


# ---------------------------------------------------------------------------
# Phase 120 Plan 07 (HOST-03) — real header-parsing two-way parity gate.
# ---------------------------------------------------------------------------

_MISSING = object()

# Frozen four-entry firmware -> host name-PAIR map (never a skip-set).
# Deliberately NOT auto-derived: the whole point of this gate is to catch an
# unreviewed drift rather than mirror it, so adding a fifth exemption must be
# a deliberate edit to this dict literal.
#   - CMD_IDLE: firmware-internal state; no shipped host path emits cmd 0
#     (Phase 119 D-01 / RESEARCH F-B2). No host counterpart at all.
#   - CMD_FRAME_MAX: CMD_-prefixed but not a command code, and its value is
#     the macro DATA_BUFFER_SIZE, not a literal -- it has its own dedicated
#     gate (test_cmd_frame_max_parity, below), so this leg checks only that
#     the host `CMD_FRAME_MAX` constant exists, never its value.
#   - CMD_DEV_ADDRESS: conditionally compiled (#ifdef DEV_TOOLS in firmware);
#     host defines COMMAND_DEV_ADDRESS unconditionally.
#   - CMD_DEV_REGISTER: conditionally compiled AND NAME-MISMATCHED --
#     firmware is singular, host is plural (COMMAND_DEV_REGISTERS). Do NOT
#     rename the host constant to match: it has callers, and a naive
#     `CMD_X` -> `COMMAND_X` map would misreport this as a real gap and
#     invite exactly that wrong "fix".
_EXEMPT_FW_TO_HOST: dict[str, str | None] = {
    "CMD_IDLE": None,
    "CMD_FRAME_MAX": "CMD_FRAME_MAX",
    "CMD_DEV_ADDRESS": "COMMAND_DEV_ADDRESS",
    "CMD_DEV_REGISTER": "COMMAND_DEV_REGISTERS",
}

_DEFINE_PATTERN = re.compile(
    r"^[ \t]*#[ \t]*define[ \t]+((?:CMD|FLAG)_[A-Za-z0-9_]+)[ \t]+(\S+)",
    re.MULTILINE,
)
_PP_OPEN_PATTERN = re.compile(r"^[ \t]*#[ \t]*(if|ifdef|ifndef)\b")
_PP_CLOSE_PATTERN = re.compile(r"^[ \t]*#[ \t]*endif\b")


def _strip_comments(text: str) -> str:
    """Blank out `//` and `/* */` comment spans, preserving both string
    length and newline positions.

    This is LOAD-BEARING here, not hygiene: firestarter.h's comment block
    above `CMD_SDP_UNLOCK` (lines 50-60) literally contains the strings
    `constants.py CMD_SDP_*`, `COMMAND_NAMES` and `#ifdef DEV_TOOLS`, and
    the block above `FLAG_SKIP_SDP_UNLOCK` (:141-147) contains
    `--skip-sdp-unlock / constants.py`. A scan over uncleaned text would
    match those comment strings as if they were real defines or real
    preprocessor conditionals.
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        two = text[i : i + 2]
        if two == "//":
            j = text.find("\n", i)
            if j == -1:
                j = n
            out.append(" " * (j - i))
            i = j
        elif two == "/*":
            j = text.find("*/", i + 2)
            if j == -1:
                j = n
            else:
                j += 2
            out.append("".join(c if c == "\n" else " " for c in text[i:j]))
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


_GUARD_IFNDEF_PATTERN = re.compile(r"^[ \t]*#[ \t]*ifndef[ \t]+(\w+)")
_GUARD_DEFINE_PATTERN = re.compile(r"^[ \t]*#[ \t]*define[ \t]+(\w+)\b")


def _find_header_guard_line_indices(lines: list[str]) -> tuple[int, int] | None:
    """Detect the classic `#ifndef GUARD` / `#define GUARD` header-guard
    idiom wrapping the WHOLE file, and return its `(open_idx, close_idx)`
    line indices, or `None` if the file does not start with that shape.

    `firestarter.h` opens with `#ifndef __FIRESTARTER_H__` /
    `#define __FIRESTARTER_H__` and its LAST line is the matching `#endif`
    -- this universal boilerplate wraps every real define in the file at
    nesting depth +1, which would make `test_conditionally_compiled_defines_
    are_exactly_the_dev_tools_pair`'s "depth > 0" assertion vacuously true
    for every define, not just the two DEV_TOOLS ones. The fixtures under
    tests/fixtures/ are plain snippets with no header guard, so this never
    fires for them -- only the real header shape is recognised.
    """
    first_idx = next(
        (i for i, line in enumerate(lines) if line.lstrip().startswith("#")), None
    )
    if first_idx is None:
        return None
    m = _GUARD_IFNDEF_PATTERN.match(lines[first_idx])
    if m is None:
        return None
    guard_name = m.group(1)

    next_idx = None
    for j in range(first_idx + 1, len(lines)):
        if not lines[j].strip():
            continue
        next_idx = j
        break
    if next_idx is None:
        return None
    m2 = _GUARD_DEFINE_PATTERN.match(lines[next_idx])
    if m2 is None or m2.group(1) != guard_name:
        return None

    last_idx = next(
        (i for i in range(len(lines) - 1, -1, -1) if lines[i].lstrip().startswith("#")),
        None,
    )
    if last_idx is None or not _PP_CLOSE_PATTERN.match(lines[last_idx]):
        return None

    return first_idx, last_idx


def _extract_defines(text: str) -> list[tuple[str, str, int]]:
    """Walk comment-stripped `text` line by line, tracking preprocessor
    nesting depth (`#if`/`#ifdef`/`#ifndef` increment, `#endif` decrements,
    `#else`/`#elif` do NOT change depth -- they stay inside the same
    conditional block), and yield `(name, raw_value, depth)` for every line
    matching a `#define` of a name beginning `CMD_` or `FLAG_`.

    Every match is yielded, unconditionally -- never filtered by whether
    `raw_value` parses as an integer, because that would silently exempt
    `CMD_FRAME_MAX` (value: the macro `DATA_BUFFER_SIZE`).

    The whole-file header-guard idiom (see `_find_header_guard_line_indices`)
    is treated as depth-neutral boilerplate, never counted as a real
    conditional-compilation block -- otherwise every define in the file
    would sit at depth >= 1 regardless of any real `#ifdef`.
    """
    cleaned = _strip_comments(text)
    lines = cleaned.splitlines()
    guard = _find_header_guard_line_indices(lines)
    guard_open_idx, guard_close_idx = guard if guard is not None else (None, None)

    depth = 0
    results: list[tuple[str, str, int]] = []
    for idx, line in enumerate(lines):
        if idx == guard_open_idx or idx == guard_close_idx:
            continue  # whole-file header guard -- depth-neutral boilerplate
        if _PP_OPEN_PATTERN.match(line):
            depth += 1
            continue
        if _PP_CLOSE_PATTERN.match(line):
            depth -= 1
            continue
        m = _DEFINE_PATTERN.match(line)
        if m:
            results.append((m.group(1), m.group(2), depth))
    return results


def _host_name(fw_name: str) -> str:
    """Map a NON-EXEMPT firmware define name to its host counterpart name.

    Exempt names are handled by the caller directly via
    `_EXEMPT_FW_TO_HOST` -- this helper only implements the two blanket
    rules: `CMD_` -> `COMMAND_`, and `FLAG_*` unchanged (the two sides use
    identical `FLAG_*` names).
    """
    if fw_name.startswith("CMD_"):
        return "COMMAND_" + fw_name[len("CMD_") :]
    return fw_name


def _read_header_text() -> str:
    """Read `FIRMWARE_HEADER`'s text, failing closed.

    An absent or unreadable header path is an ERROR, never a silent pass:
    returning an empty define set would make every downstream two-way
    assertion vacuously true (T-120-23). This is also the seam the planted-
    violation legs (below) exercise by `monkeypatch.setattr`-ing
    `FIRMWARE_HEADER` at module scope before calling any `_check_*` helper.
    """
    if not FIRMWARE_HEADER.is_file():
        raise AssertionError(
            f"firmware header not found at {FIRMWARE_HEADER} -- an absent "
            "or unreadable header must be a hard failure, never a silent "
            "pass with an empty define set"
        )
    return FIRMWARE_HEADER.read_text(encoding="utf-8")


def _host_command_constants() -> dict[str, int]:
    return {
        name: value
        for name, value in vars(constants).items()
        if name.startswith("COMMAND_")
        and name != "COMMAND_NAMES"
        and isinstance(value, int)
    }


def _host_flag_constants() -> dict[str, int]:
    return {
        name: value
        for name, value in vars(constants).items()
        if name.startswith("FLAG_") and isinstance(value, int)
    }


def _check_cmd_two_way() -> None:
    """Bidirectional CMD_* parity check body.

    Factored out so both the real gate leg and the planted-violation legs
    (Task 3) exercise the EXACT SAME code path, rather than a parallel
    reimplementation duplicating (and potentially diverging from) the real
    logic.

    Collects every discrepancy before raising once, so a real failure names
    every offending pair rather than stopping at the first one.
    """
    header_text = _read_header_text()
    defines = _extract_defines(header_text)
    cmd_defines = [(n, v, d) for (n, v, d) in defines if n.startswith("CMD_")]
    host_cmds = _host_command_constants()

    errors: list[str] = []
    expected_host_names: set[str] = set()

    for name, raw_value, _depth in cmd_defines:
        exempt = name in _EXEMPT_FW_TO_HOST
        mapped = _EXEMPT_FW_TO_HOST[name] if exempt else _host_name(name)

        if exempt and mapped is None:
            derived = "COMMAND_" + name[len("CMD_") :]
            if getattr(constants, derived, _MISSING) is not _MISSING:
                errors.append(
                    f"{name} is exempt (no host counterpart expected) but "
                    f"host constant {derived} exists"
                )
            continue

        expected_host_names.add(mapped)
        host_value = getattr(constants, mapped, _MISSING)
        if host_value is _MISSING:
            errors.append(f"{name} has no host constant {mapped} in constants.py")
            continue

        if name == "CMD_FRAME_MAX":
            # Value is the macro DATA_BUFFER_SIZE, not a literal -- has its
            # own dedicated gate (test_cmd_frame_max_parity).
            continue

        try:
            fw_value = int(raw_value, 0)
        except ValueError:
            errors.append(
                f"{name} = {raw_value!r} is not an integer literal and is "
                "not the exempted CMD_FRAME_MAX -- update _EXEMPT_FW_TO_HOST "
                "if this is deliberate"
            )
            continue

        if host_value != fw_value:
            errors.append(
                f"{name} = {fw_value} (firmware) != {mapped} = {host_value} (host)"
            )

    # Reverse direction: every host COMMAND_* constant must trace back to
    # some extracted firmware CMD_* define -- a host constant with no
    # firmware define also fails.
    for host_name in sorted(host_cmds):
        if host_name not in expected_host_names:
            errors.append(
                f"host constant {host_name} has no corresponding firmware "
                "CMD_* define in firestarter.h"
            )

    assert not errors, "CMD_* two-way parity failures:\n" + "\n".join(
        f"  - {e}" for e in errors
    )


def _check_flag_two_way() -> None:
    """Bidirectional FLAG_* parity check body, plus a machine-checked
    count/max-value invariant: exactly nine FLAG_* defines on each side,
    maximum value 0x100 on each side.

    `CTRL_VPP_VPE_DROP_ENABLE = 0x100` is a control-register bit in a
    SEPARATE namespace (mirror of rurp_pinout.h, its own parity leg is
    `test_ctrl_values_match_firmware` below) -- a FLAG_*-scoped extractor
    never sees it, so the two 0x100s are never conflated here.
    """
    header_text = _read_header_text()
    defines = _extract_defines(header_text)
    flag_defines = [(n, v, d) for (n, v, d) in defines if n.startswith("FLAG_")]
    host_flags = _host_flag_constants()

    errors: list[str] = []
    expected_host_names: set[str] = set()

    for name, raw_value, _depth in flag_defines:
        mapped = _host_name(name)  # FLAG_* names are unchanged host-side
        expected_host_names.add(mapped)
        host_value = host_flags.get(mapped, _MISSING)
        if host_value is _MISSING:
            errors.append(f"{name} has no host constant {mapped} in constants.py")
            continue
        try:
            fw_value = int(raw_value, 0)
        except ValueError:
            errors.append(f"{name} = {raw_value!r} is not an integer literal")
            continue
        if host_value != fw_value:
            errors.append(
                f"{name} = {hex(fw_value)} (firmware) != "
                f"{mapped} = {hex(host_value)} (host)"
            )

    for host_name in sorted(host_flags):
        if host_name not in expected_host_names:
            errors.append(
                f"host constant {host_name} has no corresponding firmware "
                "FLAG_* define in firestarter.h"
            )

    if len(flag_defines) != 9:
        errors.append(
            f"expected exactly nine firmware FLAG_* defines, found "
            f"{len(flag_defines)}: {sorted(n for n, _v, _d in flag_defines)}"
        )
    if len(host_flags) != 9:
        errors.append(
            f"expected exactly nine host FLAG_* constants, found "
            f"{len(host_flags)}: {sorted(host_flags)}"
        )

    int_fw_values = []
    for _n, v, _d in flag_defines:
        try:
            int_fw_values.append(int(v, 0))
        except ValueError:
            pass
    if int_fw_values and max(int_fw_values) != 0x100:
        errors.append(
            f"firmware FLAG_* max is {hex(max(int_fw_values))}, expected 0x100"
        )
    if host_flags and max(host_flags.values()) != 0x100:
        errors.append(
            f"host FLAG_* max is {hex(max(host_flags.values()))}, expected 0x100"
        )

    assert not errors, "FLAG_* two-way parity failures:\n" + "\n".join(
        f"  - {e}" for e in errors
    )


def _check_command_names_coverage() -> None:
    """D-13's leg: every NON-EXEMPT firmware CMD_*'s mapped host constant
    must also be a key in `COMMAND_NAMES`, not merely a `constants.py`
    module attribute. This closes the crash path as well as the
    value-drift path: `COMMAND_NAMES[cmd]` is dereferenced by
    `_setup_operation` (`eprom_operations.py:329`) and again by
    `_operation_context` (`:405`), so a missing entry is a `KeyError` at
    operation setup, not a cosmetic display gap. [Corrected 2026-08-03,
    RETIRE-08/D-11: was `301`/`377`, which had staled.]
    """
    header_text = _read_header_text()
    defines = _extract_defines(header_text)
    cmd_defines = [(n, v, d) for (n, v, d) in defines if n.startswith("CMD_")]
    host_cmds = _host_command_constants()

    errors: list[str] = []
    for name, _raw_value, _depth in cmd_defines:
        if name in _EXEMPT_FW_TO_HOST:
            continue  # non-exempt only, per HOST-03's scope
        mapped = _host_name(name)
        host_value = host_cmds.get(mapped)
        if host_value is None:
            # Already reported by the two-way leg -- nothing new here.
            continue
        if host_value not in constants.COMMAND_NAMES:
            errors.append(
                f"{mapped} (value {host_value}, firmware {name}) has no "
                "COMMAND_NAMES entry -- COMMAND_NAMES[cmd] is dereferenced "
                "by _setup_operation (eprom_operations.py:329) and "
                "_operation_context (:405), so this is a KeyError at "
                "operation setup, not a cosmetic gap"
            )

    assert not errors, "COMMAND_NAMES coverage failures:\n" + "\n".join(
        f"  - {e}" for e in errors
    )


@requires_fw
def test_every_firmware_cmd_define_maps_two_way_to_constants_py() -> None:
    """Two-way CMD_* parity: every firmware `CMD_*` define in
    firestarter.h maps to a `constants.py` COMMAND_* constant of the same
    value (or a named exemption in `_EXEMPT_FW_TO_HOST`), and every host
    COMMAND_* constant traces back to a firmware CMD_* define. Replaces the
    pre-rebuild hollow leg that asserted host literals with the firmware
    define named only in a trailing comment and never read the header
    (T-120-22 / T-120-25)."""
    _check_cmd_two_way()


@requires_fw
def test_every_firmware_flag_define_maps_two_way_to_constants_py() -> None:
    """Two-way FLAG_* parity, plus a machine-checked count/max-value
    invariant: exactly nine FLAG_* defines on each side, maximum 0x100 on
    each side. See `_check_flag_two_way`'s docstring for why
    `CTRL_VPP_VPE_DROP_ENABLE`'s separate 0x100 is never conflated with
    this one (T-120-22)."""
    _check_flag_two_way()


@requires_fw
def test_every_firmware_cmd_has_a_command_names_entry() -> None:
    """D-13's leg: every non-exempt firmware CMD_* must have a
    `COMMAND_NAMES` entry, not merely a `constants.py` constant. This
    closes the crash path as well as the value-drift path --
    `COMMAND_NAMES[cmd]` is dereferenced by `_setup_operation`
    (`eprom_operations.py:329`) and again by `_operation_context`
    (`:405`), so a missing entry is a `KeyError` at operation setup, not a
    cosmetic display gap (T-120-24). [Corrected 2026-08-03, RETIRE-08/D-11:
    was `301`/`377`, which had staled.]"""
    _check_command_names_coverage()


@requires_fw
def test_conditionally_compiled_defines_are_exactly_the_dev_tools_pair() -> None:
    """Turns "these two are #ifdef DEV_TOOLS-conditional" from an
    assumption living only in a comment into a machine-checked fact: the
    set of extracted CMD_*/FLAG_* define names found at preprocessor
    nesting depth greater than zero must equal exactly
    `{CMD_DEV_ADDRESS, CMD_DEV_REGISTER}`. This is what would have flagged
    Phase 119's placement of `CMD_SDP_UNLOCK` / `CMD_SDP_LOCK` OUTSIDE the
    `#ifdef DEV_TOOLS` block as a deliberate choice rather than luck."""
    header_text = _read_header_text()
    defines = _extract_defines(header_text)
    conditional_names = {n for n, _v, d in defines if d > 0}
    assert conditional_names == {"CMD_DEV_ADDRESS", "CMD_DEV_REGISTER"}, (
        "expected conditionally-compiled CMD_*/FLAG_* defines to be exactly "
        f"{{CMD_DEV_ADDRESS, CMD_DEV_REGISTER}}, found "
        f"{sorted(conditional_names)}"
    )


@requires_fw
def test_ctrl_values_match_firmware():
    """Assert each CTRL_* Python constant matches the hard-coded literal from
    `firestarter/include/rurp_pinout.h` (not `firestarter.h`).

    CTRL_* mirrors the HARDWARE_REVISION wide-layout branch of rurp_pinout.h
    Section 2 — the branch active when `#ifdef HARDWARE_REVISION` is defined.
    The `firestarter.h` skipif proxy is sufficient: rurp_pinout.h lives in the
    same `firestarter/include/` directory and is present whenever firestarter.h
    is present (RESEARCH Open Question 1, resolved).

    Phase 36 TEST-04 / D-11 extension — widens GATE-1.8c to the full
    control-register-bit surface (CTRL_* block in constants.py mirrors
    rurp_pinout.h per CLAUDE.md sync rule).
    """
    from firestarter.constants import (
        CTRL_ADDRESS_LINE_16,
        CTRL_ADDRESS_LINE_17,
        CTRL_ADDRESS_LINE_18,
        CTRL_READ_WRITE,
        CTRL_VPE_ENABLE,
        CTRL_VPP_A9_ENABLE,
        CTRL_VPP_P1_ENABLE,
        CTRL_VPP_REGULATOR_ENABLE,
        CTRL_VPP_VPE_DROP_ENABLE,
    )

    # HARDWARE_REVISION wide-layout branch (rurp_pinout.h §2 #else branch):
    assert CTRL_ADDRESS_LINE_16 == 0x001  # CTRL_ADDRESS_LINE_16 (wide layout)
    assert CTRL_VPP_A9_ENABLE == 0x002  # CTRL_VPP_A9_ENABLE
    assert CTRL_VPE_ENABLE == 0x004  # CTRL_VPE_ENABLE
    assert CTRL_VPP_P1_ENABLE == 0x008  # CTRL_VPP_P1_ENABLE
    assert CTRL_ADDRESS_LINE_17 == 0x010  # CTRL_ADDRESS_LINE_17
    assert CTRL_ADDRESS_LINE_18 == 0x020  # CTRL_ADDRESS_LINE_18
    assert CTRL_READ_WRITE == 0x040  # CTRL_READ_WRITE
    assert CTRL_VPP_REGULATOR_ENABLE == 0x080  # CTRL_VPP_REGULATOR_ENABLE
    assert (
        CTRL_VPP_VPE_DROP_ENABLE == 0x100
    )  # CTRL_VPP_VPE_DROP_ENABLE (wide layout, differs from legacy 0x01)


@requires_fw
def test_cmd_frame_max_parity() -> None:
    """Assert host CMD_FRAME_MAX == firmware Uno DATA_BUFFER_SIZE floor (512).

    Design decision D-07: firmware defines CMD_FRAME_MAX via a board-parameterized
    macro:

        #define CMD_FRAME_MAX DATA_BUFFER_SIZE

    On Uno/uno328pb: DATA_BUFFER_SIZE == 512 (the compile-time default in
    firestarter.h).
    On Leonardo: DATA_BUFFER_SIZE may be 1024 via platformio.ini build_flags;
    CMD_FRAME_MAX on that board would be 1024. BUT 512 is the binding minimum —
    a command frame >512 B is not a legitimate use case in v1.10, and the host
    must never send a frame larger than the Uno floor.

    Host hardcodes CMD_FRAME_MAX = 512 in firestarter/constants.py. This is
    ACCEPTED for v1.10 (D-07 acceptance decision — not a bug to fix).

    The `requires_fw` decorator keys on the shared repo-presence marker (same
    one used by the other parity tests in this file). When the firmware
    checkout is absent (host-only CI), this test skips cleanly.
    """
    from firestarter.constants import CMD_FRAME_MAX

    assert CMD_FRAME_MAX == 512  # == Uno DATA_BUFFER_SIZE floor (D-07)


@requires_fw
def test_max_27c020_size_parity() -> None:
    """Assert host MAX_27C020_SIZE == firmware MAX_27C020_SIZE (IN-02).

    The <=256K (262144 byte) size boundary for 0x08 (EPROM_QUICK) 32-pin
    parts — where pin 31 (A18 on DIP32_STD) is structurally unused and safe
    to repurpose as DIP32_27C020's PGM/RW strobe — is duplicated across the
    host (`firestarter/constants.py`, imported by `tools/build_db.py`'s
    `resolve_pinout_key` size gate) and the firmware
    (`firestarter/include/firestarter.h #define MAX_27C020_SIZE 262144`).

    A divergence between the two is a hardware-damage A18 risk (T-98-18):
    chips above the boundary (512K AM27C040, 1M AM27C080) legitimately use
    pin 31 = A18 and MUST stay on DIP32_STD — if the host and firmware
    boundaries disagree, a chip could be pinout-scoped one way host-side and
    gated another way firmware-side. This test FAILs at pytest time on
    divergence, matching the existing CTRL_*/FLAG_* parity discipline.

    The `requires_fw` decorator keys on the shared repo-presence marker (same
    one used by the other parity tests in this file) — it skips cleanly when
    the firmware sub-repo checkout is absent.
    """
    from firestarter.constants import MAX_27C020_SIZE

    assert MAX_27C020_SIZE == 262144  # firestarter.h #define MAX_27C020_SIZE 262144


# ---------------------------------------------------------------------------
# Planted-violation and fail-closed legs (Phase 120 Plan 07, Task 3).
#
# None of the five legs below carry the `requires_fw` skip on the same basis:
# three of them (value-drift / host-missing / fw-missing) read a fixture
# file under tests/fixtures/, which is always present in the repo regardless
# of whether the firmware sub-repo checkout exists, and the fourth
# (fail-closed) reads a deliberately-nonexistent tmp_path. This partially
# offsets the residual host-only-CI skip gap: a host-only PR still
# exercises the checker's failure modes even though it cannot exercise them
# against the REAL header. The fifth (COMMAND_NAMES delitem) DOES read the
# real header and DOES carry the `requires_fw` skip, since it is not fixture-driven.
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_FIXTURE_VALUE_DRIFT = _FIXTURES_DIR / "planted_constants_value_drift.h"
_FIXTURE_HOST_MISSING = _FIXTURES_DIR / "planted_constants_host_missing.h"
_FIXTURE_FW_MISSING = _FIXTURES_DIR / "planted_constants_fw_missing.h"


def test_planted_value_drift_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """planted_constants_value_drift.h (CMD_VERIFY = 106, real value 6) must
    trip the two-way CMD_* leg's underlying check -- and ONLY that check,
    proving leg isolation. Calls the SAME `_check_cmd_two_way` helper the
    real leg calls, not a parallel reimplementation."""
    assert _FIXTURE_VALUE_DRIFT.is_file(), (
        f"committed fixture missing: {_FIXTURE_VALUE_DRIFT}"
    )
    monkeypatch.setattr(sys.modules[__name__], "FIRMWARE_HEADER", _FIXTURE_VALUE_DRIFT)
    with pytest.raises(AssertionError) as excinfo:
        _check_cmd_two_way()
    message = str(excinfo.value)
    assert "CMD_VERIFY = 106" in message
    assert "COMMAND_VERIFY = 6" in message
    assert "has no host constant" not in message


def test_planted_host_missing_define_is_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """planted_constants_host_missing.h (adds CMD_DEBUG_DUMP, no host
    counterpart) must trip the forward-direction "no host constant" report
    -- and must NOT report a value drift, proving leg isolation."""
    assert _FIXTURE_HOST_MISSING.is_file(), (
        f"committed fixture missing: {_FIXTURE_HOST_MISSING}"
    )
    monkeypatch.setattr(sys.modules[__name__], "FIRMWARE_HEADER", _FIXTURE_HOST_MISSING)
    with pytest.raises(AssertionError) as excinfo:
        _check_cmd_two_way()
    message = str(excinfo.value)
    assert "CMD_DEBUG_DUMP" in message
    assert "has no host constant" in message
    assert "!=" not in message


def test_planted_firmware_missing_flag_is_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """planted_constants_fw_missing.h (deletes FLAG_VPE_AS_VPP) must trip
    the reverse-direction "no firmware define" report naming the host
    FLAG_* constant -- and must NOT report a value drift."""
    assert _FIXTURE_FW_MISSING.is_file(), (
        f"committed fixture missing: {_FIXTURE_FW_MISSING}"
    )
    monkeypatch.setattr(sys.modules[__name__], "FIRMWARE_HEADER", _FIXTURE_FW_MISSING)
    with pytest.raises(AssertionError) as excinfo:
        _check_flag_two_way()
    message = str(excinfo.value)
    assert "FLAG_VPE_AS_VPP" in message
    assert "no corresponding firmware" in message
    assert "!=" not in message


@requires_fw
def test_missing_command_names_entry_is_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinguishes a missing NAME ENTRY from a missing CONSTANT -- the
    crash path from the drift path. Deletes one of this phase's own two new
    SDP entries from `COMMAND_NAMES` (via `monkeypatch.delitem`, reverted
    automatically after the test) and asserts the COMMAND_NAMES-coverage
    check raises naming that command."""
    monkeypatch.delitem(constants.COMMAND_NAMES, constants.COMMAND_SDP_UNLOCK)
    with pytest.raises(AssertionError) as excinfo:
        _check_command_names_coverage()
    assert "COMMAND_SDP_UNLOCK" in str(excinfo.value)


def test_command_names_dereferences_both_sdp_commands() -> None:
    """RETIRE-04: `firestarter dev sdp`'s removal (Phase 132) deletes the
    only *host-surface* caller of `COMMAND_SDP_UNLOCK`/`COMMAND_SDP_LOCK`,
    which makes their `COMMAND_NAMES` entries LOOK cosmetic. They are not:
    the operation layer dereferences `COMMAND_NAMES[cmd]` at two points
    during setup -- `_setup_operation` (`eprom_operations.py:329`) and
    `_operation_context` (`eprom_operations.py:405`) -- so a dropped entry
    is a `KeyError` at operation setup, not a display gap.

    This test performs that exact dereference for BOTH commands,
    unconditionally (no `requires_fw` skip, unlike
    `test_missing_command_names_entry_is_detected` above), so a regression
    is caught in every CI run including host-only CI, where the firmware
    checkout is absent. See the matching comment above
    `COMMAND_SDP_UNLOCK`/`COMMAND_SDP_LOCK` in `constants.py`, which names
    this test by name -- the two halves reference each other.
    """
    # Removing a constant outright (not just its COMMAND_NAMES entry) must
    # also fail this test, or the dereferences below could pass vacuously
    # against a stale value.
    assert constants.COMMAND_SDP_UNLOCK == 9
    assert constants.COMMAND_SDP_LOCK == 10

    assert constants.COMMAND_SDP_UNLOCK in constants.COMMAND_NAMES, (
        "COMMAND_NAMES has no entry for COMMAND_SDP_UNLOCK "
        f"({constants.COMMAND_SDP_UNLOCK}) -- _setup_operation "
        "(eprom_operations.py:329) and _operation_context "
        "(eprom_operations.py:405) both dereference COMMAND_NAMES[cmd] at "
        "operation setup, so a dropped entry is a KeyError there, not a "
        "cosmetic display gap."
    )
    assert constants.COMMAND_NAMES[constants.COMMAND_SDP_UNLOCK], (
        "COMMAND_NAMES[COMMAND_SDP_UNLOCK] is present but falsy/empty -- "
        "_setup_operation (eprom_operations.py:329) and _operation_context "
        "(eprom_operations.py:405) both dereference this mapping at "
        "operation setup and would surface an empty operation name there."
    )

    assert constants.COMMAND_SDP_LOCK in constants.COMMAND_NAMES, (
        "COMMAND_NAMES has no entry for COMMAND_SDP_LOCK "
        f"({constants.COMMAND_SDP_LOCK}) -- _setup_operation "
        "(eprom_operations.py:329) and _operation_context "
        "(eprom_operations.py:405) both dereference COMMAND_NAMES[cmd] at "
        "operation setup, so a dropped entry is a KeyError there, not a "
        "cosmetic display gap."
    )
    assert constants.COMMAND_NAMES[constants.COMMAND_SDP_LOCK], (
        "COMMAND_NAMES[COMMAND_SDP_LOCK] is present but falsy/empty -- "
        "_setup_operation (eprom_operations.py:329) and _operation_context "
        "(eprom_operations.py:405) both dereference this mapping at "
        "operation setup and would surface an empty operation name there."
    )


def test_gate_fails_closed_on_an_unreadable_header_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable/absent header path must be an ERROR, never a silent
    pass -- an empty define set would make every downstream assertion
    vacuously true. Points FIRMWARE_HEADER at a path that does not exist
    under tmp_path and asserts the read helper raises rather than
    returning an empty define set."""
    missing = tmp_path / "does_not_exist.h"
    monkeypatch.setattr(sys.modules[__name__], "FIRMWARE_HEADER", missing)
    with pytest.raises(AssertionError, match="firmware header not found"):
        _check_cmd_two_way()
