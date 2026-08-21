"""
Tests for tools/build_db.py::interpret_timing (Phase 148 Plan 03 -- DATA-02 (D-08)).

Defect class this closes: after the string/int schema collapse (this same
plan), a returned `0` from `interpret_timing` would otherwise carry two
different meanings -- "algorithm-controlled" (a protocol that never consumes
pulse-delay, 417 chips in the packaged DB) or "decode fault on a
0x07/0x08/0x0B chip whose pulse_delay could not be parsed as hex". D-08
closes that ambiguity by making the decode-fault path fatal (`ValueError`,
naming both the protocol and the offending raw value) instead of silently
defaulting to `0` -- so a returned `0` has exactly one meaning going forward.

Per 148-RESEARCH.md's "D-08 reachability" finding, the fatal branch is
PROVABLY DEAD against the pinned infoic.xml commit (a8efaedc): an exhaustive
scan of all 27,862 `<ic>` elements found 0 missing and 0 unparseable
`pulse_delay` values. That means a green `python3 tools/build_db.py` run
proves NOTHING about this branch -- this module is its ONLY coverage.

Coverage:
  1. `test_fatal_leg_none_raises_naming_protocol_and_value` -- `raw_hex=None`
     on protocol 0x07 raises ValueError naming both `0x07` and `None`.
  2. `test_fatal_leg_unparseable_string_raises_naming_protocol_and_value` --
     `raw_hex="zz"` on protocol 0x0B raises ValueError naming both `0x0b`
     and `'zz'`.
  3. `test_control_valid_hex_returns_parsed_int` (Property C / S-5) --
     `interpret_timing("64", 0x07)` returns the int `100` (0x64 decimal),
     proving the fatal legs above are not passing merely because the
     function raises unconditionally.
  4. `test_control_algorithm_controlled_protocol_returns_zero_int` --
     `interpret_timing("64", 0x0D)` returns the int `0` -- the
     algorithm-controlled sentinel, on a protocol that does not consume
     pulse-delay, even though the raw value itself parses cleanly.
  5. `test_return_type_is_always_int` (control) -- every non-raising return,
     across a protocol that consumes pulse-delay and one that does not, is
     of type `int`, never a string (`"100 us"` / `"Algorithm Controlled"`
     are the pre-migration shapes this test proves are gone).
"""

import pytest

from tools import build_db

# Canned inputs, measured against the pinned infoic.xml commit
# (a8efaedc236c1d9718bd28299dfbb99536b010ff) per 148-RESEARCH.md's exhaustive
# 27,862-element `<ic>` scan (0 missing, 0 unparseable pulse_delay) -- the
# reason the fatal branch below is unreachable by a real regen and this
# module is its only coverage.
_FATAL_LEG_PROTOCOL_NONE = 0x07
_FATAL_LEG_PROTOCOL_STRING = 0x0B
_VALID_HEX = "64"  # 0x64 == 100 decimal
_ALGORITHM_CONTROLLED_PROTOCOL = 0x0D  # EEPROM_POLL: does not consume pulse-delay


def test_fatal_leg_none_raises_naming_protocol_and_value():
    with pytest.raises(ValueError) as exc:
        build_db.interpret_timing(None, _FATAL_LEG_PROTOCOL_NONE)
    message = str(exc.value)
    assert "0x07" in message, message
    assert "None" in message, message


def test_fatal_leg_unparseable_string_raises_naming_protocol_and_value():
    with pytest.raises(ValueError) as exc:
        build_db.interpret_timing("zz", _FATAL_LEG_PROTOCOL_STRING)
    message = str(exc.value)
    assert "0x0b" in message, message
    assert "'zz'" in message, message


def test_control_valid_hex_returns_parsed_int():
    """Property C / S-5: proves the fatal legs above are not passing merely
    because interpret_timing raises unconditionally -- a valid hex string on
    a pulse-delay-consuming protocol must still decode and return cleanly."""
    result = build_db.interpret_timing(_VALID_HEX, _FATAL_LEG_PROTOCOL_NONE)
    assert result == 100, result
    assert isinstance(result, int), type(result)


def test_control_algorithm_controlled_protocol_returns_zero_int():
    result = build_db.interpret_timing(_VALID_HEX, _ALGORITHM_CONTROLLED_PROTOCOL)
    assert result == 0, result
    assert isinstance(result, int), type(result)


def test_return_type_is_always_int():
    pulse_consuming = build_db.interpret_timing(_VALID_HEX, _FATAL_LEG_PROTOCOL_NONE)
    algorithm_controlled = build_db.interpret_timing(
        _VALID_HEX, _ALGORITHM_CONTROLLED_PROTOCOL
    )
    assert isinstance(pulse_consuming, int), type(pulse_consuming)
    assert isinstance(algorithm_controlled, int), type(algorithm_controlled)
