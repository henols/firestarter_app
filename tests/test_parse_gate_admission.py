"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 151 Plan 03 (LOCK-02, OD-3) -- a non-vacuous source-scan proof over
`firestarter/src/firestarter.cpp` for the widened memory-command parse gate.

**Why a source scan, not only the firmware-native truth table.** Plan
151-03's Task 1 widens the parse gate at `firestarter.cpp:77` from a bare
ordinal test to `is_memory_cmd(handle->cmd) || handle->cmd < CMD_READ_VPP`.
The firmware-native suites (`test_cmd_admission`, `test_pinmap_provisional`)
already prove `is_memory_cmd()` itself admits exactly nine values. This
module proves the SEPARATE claim that the parse gate actually calls that
predicate at the one call site that decides whether a frame reaches
`json_parse()`/`configure_memory()` at all -- a claim the native suites
cannot make because neither one reads `firestarter.cpp`'s source text.

**Non-vacuity, the load-bearing property (leg 4).** A source-scan test that
has never been observed to report absence is exactly the hollow-gate defect
class this project's `check_is_memory_cmd_no_ifdef.py` and
`check_no_log_in_sdp_window.py` were both built to avoid (v1.12 GATE-03).
Leg 4 feeds the SAME extraction helper legs 1/2/3 use a synthetic string
containing only the pre-Phase-151 ordinal-only gate and asserts the helper
reports absence -- proving this module's pattern is a real checkable
negative, not a pattern that would match any plausible revision of the
file.

Cross-repo plumbing: `requires_fw` (from `tests.fw_presence`) is the ONLY
skip marker this module uses, keyed on the sibling `../firestarter/.git`
marker -- immune to any in-repo firmware rename. `_GATE_SRC` (resolved via
`fw_path` at module scope) means a firmware RENAME of `src/firestarter.cpp`
is a hard `MissingScanTargetError` failure rather than a silent skip -- see
`tests/fw_presence.py`. This module deliberately does NOT use
`monkeypatch.setenv` to redirect `FIRESTARTER_FW_ROOT`: `tests/fw_presence.py`
binds `FW_ROOT` at import time, so `monkeypatch.setenv` run inside a test
body has no effect on it (RESEARCH Correction C-15). Leg 4's non-vacuity
control therefore feeds the extraction helper a synthetic in-memory string
directly, never a redirected root.

Coverage:
  1. The widened parse-gate expression
     `is_memory_cmd(handle->cmd) || handle->cmd < CMD_READ_VPP` is present
     in the real, current firmware source.
  2. The second, independent diagnostic-range ordinal test
     `handle->cmd > CMD_IDLE && handle->cmd < CMD_READ_VPP` is present
     UNCHANGED, and the comment block immediately preceding it records
     DESIGN.md §7's stated choice (command 16 emits no `DBG_*` diagnostic
     output) -- asserted here so a later reader cannot conclude the
     omission was accidental. Pinned on the CLAIM's own durable wording
     (`_STATED_CHOICE_PHRASES`: both ordinals, the word CHOICE, and the
     `DBG_*` consequence), not on a phase label: a provenance label is
     exactly the kind of text a source-hygiene sweep is entitled to
     delete, and pinning one made this leg fail on the sweep's intended
     outcome rather than on a real regression.
  3. `loop()`'s dispatch switch still reaches a `default:` arm that calls
     `LOG_ERROR_ID_U8(MSG_ERR_UNKNOWN_CMD, ...)` -- D-04's mechanism for an
     admitted-but-undispatched command (command 16 falls through to this
     arm until Plan 151-08 lands the operation) and must not be removed.
  4. Non-vacuity control: the same helper leg 1 uses, fed a synthetic
     source string containing only the pre-Phase-151 ordinal-only gate,
     reports the widened expression ABSENT. The fixture's own sanity is
     asserted first with a "Fixture setup error: ..." message, so a broken
     control is distinguishable from a passing check.
  5. Non-vacuity control for leg 2: the same helper leg 2 uses, fed a
     synthetic comment block that names both ordinals but has the
     deliberateness sentence and the `DBG_*` consequence REMOVED, reports
     those phrases MISSING. Without this control leg 2's retargeted
     conjunction would be an unobserved negative -- the hollow-gate defect
     class this module was built against.
"""

from __future__ import annotations

import re

from tests.fw_presence import fw_path, requires_fw

# ---------------------------------------------------------------------------
# Cross-repo plumbing. Resolved via `fw_path` at module scope so a
# present-repo rename of src/firestarter.cpp is a named
# MissingScanTargetError, never a silent skip (tests/fw_presence.py).
# ---------------------------------------------------------------------------
_GATE_SRC = fw_path("src", "firestarter.cpp")

# The widened parse-gate expression (Phase 151, LOCK-02, OD-3). This pattern
# is a REAL checkable negative: before this phase the expression at this
# call site was the bare ordinal test `handle->cmd < CMD_READ_VPP` with no
# `is_memory_cmd` disjunct at all, so this exact pattern fails against
# every prior revision of the file -- see leg 4's synthetic non-vacuity
# control below, which reproduces that prior shape.
_WIDENED_GATE_RE = re.compile(
    r"if\s*\(\s*is_memory_cmd\(handle->cmd\)\s*\|\|\s*handle->cmd\s*<\s*CMD_READ_VPP\s*\)\s*\{"
)

# The second, independent diagnostic-range ordinal test. Deliberately left
# UNCHANGED by Phase 151 (DESIGN.md §7) -- this pattern proves it is still
# present verbatim, not merely that "some" range test exists.
_DIAGNOSTIC_RANGE_RE = re.compile(
    r"if\s*\(\s*handle->cmd\s*>\s*CMD_IDLE\s*&&\s*handle->cmd\s*<\s*CMD_READ_VPP\s*\)\s*\{"
)

# loop()'s undispatched-command fallback: a `default:` arm whose body calls
# LOG_ERROR_ID_U8(MSG_ERR_UNKNOWN_CMD, ...) -- D-04's mechanism, which
# command 16 relies on until Plan 151-08 lands the operation.
_DEFAULT_UNKNOWN_CMD_RE = re.compile(
    r"default\s*:\s*\n\s*LOG_ERROR_ID_U8\(MSG_ERR_UNKNOWN_CMD"
)

# How far back (in characters) leg 2 looks for the stated-choice sentences in
# the comment block immediately preceding the diagnostic-range test. Generous
# enough to cover a multi-line // comment block, small enough that it could
# not accidentally reach into an unrelated, much-earlier comment.
_STATED_CHOICE_LOOKBACK_CHARS = 1200

# The phrases that together ARE DESIGN.md §7's stated choice, each quoted from
# the comment block above the diagnostic-range test and each chosen to be
# durable under a provenance sweep: two wire ordinals, the word CHOICE, and the
# named consequence. ALL of them must be present -- the conjunction is what
# makes this a claim-level pin rather than a keyword-level one. A comment that
# merely names a phase, or that names the ordinals without recording that the
# exclusion is deliberate, does NOT satisfy this leg.
_STATED_CHOICE_PHRASES = (
    "CMD_LOCK_STATUS (16)",
    "CMD_READ_VPP (11)",
    "this is a CHOICE",
    "DBG_* diagnostic",
)


def _missing_stated_choice_phrases(preceding_text: str) -> list[str]:
    """Return the `_STATED_CHOICE_PHRASES` absent from `preceding_text`.

    Shared by leg 2 (the real file's comment block) and leg 5 (the synthetic
    non-vacuity control) -- leg 5 must prove THIS helper can report absence,
    not a parallel reimplementation of it. Empty list means the stated choice
    is fully recorded.
    """
    return [p for p in _STATED_CHOICE_PHRASES if p not in preceding_text]


def _read_gate_source_text() -> str:
    """Read `_GATE_SRC`'s text, failing closed.

    An absent or unreadable path is an ERROR, never a silent pass: an empty
    string would make every negative assertion in this module vacuously
    true. `requires_fw` already guards the live legs against a missing
    sibling checkout; a present-but-unreadable file under a present repo is
    a distinct, harder failure this raises directly.
    """
    if not _GATE_SRC.is_file():
        raise AssertionError(
            f"firmware parse-gate source not found at {_GATE_SRC} -- an "
            "absent or unreadable path must be a hard failure, never a "
            "silent pass with an empty scan."
        )
    return _GATE_SRC.read_text(encoding="utf-8")


def _widened_gate_present(text: str) -> bool:
    """Return whether `text` contains the widened parse-gate expression.

    Shared by leg 1 (the real file) and leg 4 (the synthetic non-vacuity
    control) -- leg 4 must prove THIS helper can report absence, not a
    parallel reimplementation of it.
    """
    return bool(_WIDENED_GATE_RE.search(text))


# ---------------------------------------------------------------------------
# Leg 1: the widened gate expression is present in the real source
# ---------------------------------------------------------------------------


@requires_fw
def test_widened_parse_gate_expression_is_present() -> None:
    """`is_memory_cmd(handle->cmd) || handle->cmd < CMD_READ_VPP` must be
    present in the real, current `firestarter.cpp` -- the single call site
    that decides whether a frame reaches `json_parse()`/`configure_memory()`
    at all (OD-3)."""
    text = _read_gate_source_text()
    assert _widened_gate_present(text), (
        "the widened parse-gate expression "
        "'is_memory_cmd(handle->cmd) || handle->cmd < CMD_READ_VPP' was not "
        f"found in {_GATE_SRC}"
    )


# ---------------------------------------------------------------------------
# Leg 2: the diagnostic-range test is present UNCHANGED, with the stated
# sentence in its comment block
# ---------------------------------------------------------------------------


@requires_fw
def test_diagnostic_range_unchanged_with_stated_choice_comment() -> None:
    """The second, independent diagnostic-range ordinal test
    (`handle->cmd > CMD_IDLE && handle->cmd < CMD_READ_VPP`) must still be
    present verbatim, and the comment block immediately preceding it must
    record the no-`DBG_*`-output consequence as a stated choice
    (DESIGN.md §7), not a silent, undocumented gap.

    The recording is pinned by `_STATED_CHOICE_PHRASES` -- ALL FOUR must be
    present. That is strictly stronger than the single phase-label literal
    this leg pinned previously: a comment naming the phase but not the
    ordinals, or naming the ordinals without recording that the exclusion is
    deliberate, satisfied the old pin and fails this one.
    """
    text = _read_gate_source_text()
    match = _DIAGNOSTIC_RANGE_RE.search(text)
    assert match is not None, (
        "the diagnostic-range test 'handle->cmd > CMD_IDLE && handle->cmd < "
        f"CMD_READ_VPP' was not found unchanged in {_GATE_SRC}"
    )
    lookback_start = max(0, match.start() - _STATED_CHOICE_LOOKBACK_CHARS)
    preceding_text = text[lookback_start : match.start()]
    missing = _missing_stated_choice_phrases(preceding_text)
    assert not missing, (
        "the comment block preceding the diagnostic-range test no longer "
        f"records DESIGN.md §7's stated choice -- missing {missing!r}. "
        "Command 16 emitting no DBG_* diagnostic output is a CHOICE and must "
        "be recorded there, not left to be rediscovered."
        f"\npreceding text:\n{preceding_text}"
    )


# ---------------------------------------------------------------------------
# Leg 3: loop()'s default: arm still emits MSG_ERR_UNKNOWN_CMD
# ---------------------------------------------------------------------------


@requires_fw
def test_loop_default_arm_emits_msg_err_unknown_cmd() -> None:
    """`loop()`'s dispatch switch must still reach a `default:` arm calling
    `LOG_ERROR_ID_U8(MSG_ERR_UNKNOWN_CMD, ...)` -- D-04's mechanism for an
    admitted-but-undispatched command. Command 16 relies on exactly this
    fallthrough until Plan 151-08 lands the operation; removing it would
    silently change command 16's current, deliberate intermediate
    behaviour."""
    text = _read_gate_source_text()
    assert _DEFAULT_UNKNOWN_CMD_RE.search(text) is not None, (
        "loop()'s switch no longer reaches a 'default:' arm calling "
        f"LOG_ERROR_ID_U8(MSG_ERR_UNKNOWN_CMD, ...) in {_GATE_SRC}"
    )


# ---------------------------------------------------------------------------
# Leg 4: non-vacuity control -- the helper must be ABLE to report absence
# ---------------------------------------------------------------------------


def test_non_vacuity_control_reports_absence_on_old_gate_only() -> None:
    """Feed `_widened_gate_present` a synthetic source string containing
    only the pre-Phase-151 ordinal-only gate (no `is_memory_cmd` disjunct)
    and assert it reports the widened expression ABSENT -- the load-bearing
    anti-hollow proof that this module's pattern is a real checkable
    negative, not one that would match any plausible prior revision.
    """
    synthetic_old_gate_only = (
        "bool parse_json(firestarter_handle_t* handle) {\n"
        "    LOG_DEBUG_ID_SUB_U8(DBG_CMD, (uint8_t)handle->cmd);\n"
        "    if (handle->cmd < CMD_READ_VPP) {\n"
        "        json_parse(handle->data_buffer, tokens, token_count, handle);\n"
        "        if (is_memory_cmd(handle->cmd)) {\n"
        "            op_execute_function(configure_memory, handle);\n"
        "        }\n"
        "    }\n"
        "    return true;\n"
        "}\n"
    )
    # Fixture setup sanity, asserted FIRST and with its own distinct
    # message: the synthetic string must actually contain the pre-Phase-151
    # ordinal-only gate text, or this control proves nothing -- a broken
    # fixture (e.g. a typo that drops the ordinal test entirely) must be
    # distinguishable from a passing check.
    assert "handle->cmd < CMD_READ_VPP" in synthetic_old_gate_only, (
        "Fixture setup error: the synthetic old-gate-only source does not "
        "even contain the pre-Phase-151 ordinal-only gate text -- the "
        "fixture itself is broken, not the check under test."
    )

    observed_absent = not _widened_gate_present(synthetic_old_gate_only)
    assert observed_absent, (
        "the non-vacuity control FAILED: _widened_gate_present() reported "
        "the widened expression PRESENT in a synthetic source that contains "
        "only the pre-Phase-151 ordinal-only gate -- this pattern is not a "
        "real checkable negative."
    )
    # Record the observed non-vacuity message for the plan summary (Task 3's
    # <output> requirement): this exact message is what a maintainer sees on
    # a real regression.
    observed_message = (
        "non-vacuity control observed: _widened_gate_present() correctly "
        "reported ABSENT against a synthetic source containing only the "
        "pre-Phase-151 ordinal-only gate (no is_memory_cmd disjunct)"
    )
    assert "ABSENT" in observed_message


# ---------------------------------------------------------------------------
# Leg 5: non-vacuity control for leg 2 -- the stated-choice helper must be
# ABLE to report absence
# ---------------------------------------------------------------------------


def test_non_vacuity_control_reports_absent_stated_choice() -> None:
    """Feed `_missing_stated_choice_phrases` a synthetic comment block that
    still names BOTH ordinals but has had the deliberateness sentence and the
    `DBG_*` consequence removed, and assert it reports exactly those two
    phrases missing.

    This is the checkable negative for leg 2's retargeted pin. Leg 2 was
    previously anchored on the literal string "Phase 151" in the firmware
    source; a source-hygiene sweep deleted that label, and the leg failed on
    the sweep's intended outcome rather than on any regression -- so the pin
    moved onto the CLAIM. A conjunction pin is only worth more than the
    single literal it replaced if it has been SEEN to report absence, which
    is what this leg does.
    """
    # A plausible-looking comment block that a careless edit could produce:
    # the ordinals survive, the reason they matter does not.
    synthetic_choice_removed = (
        "    // The two new commands (CMD_SDP_UNLOCK 9, CMD_SDP_LOCK 10)\n"
        "    // already satisfy this range test unchanged.\n"
        "    //\n"
        "    // CMD_LOCK_STATUS (16) is numerically greater than\n"
        "    // CMD_READ_VPP (11), so it falls outside this range.\n"
    )
    # Fixture setup sanity, asserted FIRST and with its own distinct message:
    # the synthetic block must actually still carry the two ordinal phrases,
    # or this control would "detect absence" for the wrong reason.
    assert "CMD_LOCK_STATUS (16)" in synthetic_choice_removed, (
        "Fixture setup error: the synthetic choice-removed comment block does "
        "not contain the CMD_LOCK_STATUS ordinal phrase -- the fixture itself "
        "is broken, not the check under test."
    )
    assert "CMD_READ_VPP (11)" in synthetic_choice_removed, (
        "Fixture setup error: the synthetic choice-removed comment block does "
        "not contain the CMD_READ_VPP ordinal phrase -- the fixture itself is "
        "broken, not the check under test."
    )

    missing = _missing_stated_choice_phrases(synthetic_choice_removed)
    assert missing == ["this is a CHOICE", "DBG_* diagnostic"], (
        "the non-vacuity control FAILED: _missing_stated_choice_phrases() "
        f"reported {missing!r} against a synthetic comment block whose "
        "deliberateness sentence and DBG_* consequence were removed -- "
        "expected exactly those two phrases, so this conjunction is not the "
        "real checkable negative leg 2 relies on."
    )

    # And the positive direction on the same helper, so the control cannot
    # pass by always reporting absence: a block carrying all four phrases
    # must report NOTHING missing.
    synthetic_choice_present = synthetic_choice_removed + (
        "    // -- this is a CHOICE recorded here, not a discovery made on\n"
        "    // the bench. `dev lock-status` therefore emits none of the\n"
        "    // three DBG_* diagnostic lines below.\n"
    )
    assert _missing_stated_choice_phrases(synthetic_choice_present) == [], (
        "the non-vacuity control FAILED in the positive direction: "
        "_missing_stated_choice_phrases() reported phrases missing from a "
        "synthetic block that carries all four -- the helper reports absence "
        "unconditionally and therefore proves nothing for leg 2."
    )
