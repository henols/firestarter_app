"""
Tests for tools/build_db.py::load_datasheet_overrides and
::apply_datasheet_override (Phase 197 Plan 02 -- OVR-01 / OVR-03 / D-03 / D-04).

Reachability argument, restated word-for-word from
tests/test_build_db_interpret_timing.py because it applies identically here:
against a correct override file none of the fail-closed branches below ever
fires, so a green `python tools/build_db.py` run proves NOTHING about them --
this module is their ONLY coverage. `tools/` sits outside `ruff check`,
`ruff format --check` and mypy, and `--cov=firestarter` excludes it from
coverage; the `from tools import build_db` import below is the only thing
that puts this loader's behaviour under CI at all.

Coverage:
  1. `test_happy_path_substitutes_named_field_only` -- a matching override
     whose recorded prior equals the live decode substitutes the new value
     and leaves every other path in the decoded view untouched.
  2. `test_stale_recorded_prior_raises` -- a recorded prior that does not
     match the live decode raises `ValueError` naming the row key, the field
     path, the recorded value and the live value.
  3. `test_unknown_field_path_raises` -- a field path outside the six-path
     decoded view raises `ValueError` naming the row key and the offending
     path.
  4. `test_noop_override_raises` -- `was` equal to `is` raises `ValueError`
     naming the row key, the field path and the value.
  5. `test_empty_overrides_is_a_control_noop` -- the control that proves the
     three raisers above are not unconditional: an empty overrides mapping
     leaves the decoded view byte-for-byte identical.
  6. `test_absent_file_returns_empty_mapping` -- `load_datasheet_overrides`
     on a path that does not exist returns an empty mapping rather than
     raising.
  7. `test_case_differing_key_matches_no_row` -- a manufacturer or alias
     differing only in letter case matches no row, so the override is
     silently not applied (OVR-03/encoding).
  8. `TestShippedOverrideFileContract` -- the shipped
     tools/datasheet_overrides.json parses, every entry's `datasheet` is
     either a repository-relative path that exists and is git-tracked, or
     the exact token `UNSOURCED` with a non-empty `note`, and the number of
     `UNSOURCED` entries is pinned as an exact count.

Plan 197-05 adds the MBM27C4001 no-op-is-refused proof and pins the total entry count:
  17. `test_mbm27c4001_noop_entry_raises` -- a synthetic override entry for
      FUJITSU/MBM27C4001 with `was` equal to `is` (100) raises `ValueError`,
      proving the rule that keeps this row out of the shipped file is itself
      covered rather than merely obeyed.
  18. `test_total_entry_count_is_pinned_exactly` -- the shipped file holds
      exactly 9 top-level entries.

Plan 197-03 adds the two whole-file legs, the sort gate and the type check:
  9. `test_unmatched_override_key_raises_after_the_loop_not_inside_it` --
     a key matching no row's alias set produces no event from
     `apply_datasheet_override` itself; only the separate
     `check_all_override_keys_consumed` call, made after the decode loop,
     raises `ValueError` naming the unmatched key.
  10. `test_duplicate_target_raises_naming_both_keys` -- two override keys
      resolving to the same row (`INTEL/M2732` and `INTEL/M2732A`) and the
      same field path raise `ValueError` naming both keys.
  11. `test_unsorted_file_raises_naming_first_out_of_order_key` -- a
      top-level key order that is not ascending raises `ValueError` naming
      the offending key and the key expected in its place.
  12. `test_type_mismatch_float_vs_int_raises_without_coercion` -- a JSON
      float `was` against a decoded integer raises `ValueError` rather than
      comparing equal.
  13. `test_boolean_does_not_satisfy_integer_type_check` -- a JSON `true`
      `was` against a decoded integer raises `ValueError`, proving `bool`
      does not satisfy an integer type check via `==`.
  14. `test_missing_datasheet_key_raises` -- an entry missing `datasheet`
      raises `ValueError` naming the entry key.
  15. `test_missing_or_empty_fields_raises` -- an entry with an empty
      `fields` map raises `ValueError` naming the entry key.
  16. `test_well_formed_multi_entry_file_loads_without_raising` -- the
      control that proves the five new legs are not unconditional: a
      correctly-shaped multi-entry file loads cleanly.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from tools import build_db

_HERE = Path(__file__).resolve().parent
_APP_ROOT = _HERE.parent

_ROW_KEY = "FUJITSU/MBM27C1000"
_ALIAS_SET = {"MBM27C1000P", "MBM27C1000"}

_DECODED_TEMPLATE = {
    "electrical.size_bytes": 131072,
    "electrical.pin_count": 32,
    "electrical.vpp_mv": 12500,
    "electrical.vcc_mv": 5000,
    "electrical.vdd_mv": 6500,
    "programming.pulse_duration_us": 100,
}


def _decoded():
    return dict(_DECODED_TEMPLATE)


def test_happy_path_substitutes_named_field_only():
    overrides = {
        _ROW_KEY: {"fields": {"programming.pulse_duration_us": {"was": 100, "is": 500}}}
    }
    decoded = _decoded()
    build_db.apply_datasheet_override(overrides, "FUJITSU", _ALIAS_SET, decoded)
    assert decoded["programming.pulse_duration_us"] == 500, decoded
    untouched = {
        k: v for k, v in decoded.items() if k != "programming.pulse_duration_us"
    }
    expected_untouched = {
        k: v
        for k, v in _DECODED_TEMPLATE.items()
        if k != "programming.pulse_duration_us"
    }
    assert untouched == expected_untouched, untouched


def test_stale_recorded_prior_raises():
    overrides = {
        _ROW_KEY: {"fields": {"programming.pulse_duration_us": {"was": 999, "is": 500}}}
    }
    decoded = _decoded()
    with pytest.raises(ValueError) as exc:
        build_db.apply_datasheet_override(overrides, "FUJITSU", _ALIAS_SET, decoded)
    message = str(exc.value)
    assert _ROW_KEY in message, message
    assert "programming.pulse_duration_us" in message, message
    assert "999" in message, message
    assert "100" in message, message


def test_unknown_field_path_raises():
    overrides = {_ROW_KEY: {"fields": {"programming.algorithm": {"was": 7, "is": 8}}}}
    decoded = _decoded()
    with pytest.raises(ValueError) as exc:
        build_db.apply_datasheet_override(overrides, "FUJITSU", _ALIAS_SET, decoded)
    message = str(exc.value)
    assert _ROW_KEY in message, message
    assert "programming.algorithm" in message, message


def test_noop_override_raises():
    overrides = {
        _ROW_KEY: {"fields": {"programming.pulse_duration_us": {"was": 100, "is": 100}}}
    }
    decoded = _decoded()
    with pytest.raises(ValueError) as exc:
        build_db.apply_datasheet_override(overrides, "FUJITSU", _ALIAS_SET, decoded)
    message = str(exc.value)
    assert _ROW_KEY in message, message
    assert "programming.pulse_duration_us" in message, message
    assert "100" in message, message


def test_empty_overrides_is_a_control_noop():
    decoded = _decoded()
    result = build_db.apply_datasheet_override({}, "FUJITSU", _ALIAS_SET, decoded)
    assert result == _DECODED_TEMPLATE, result


def test_absent_file_returns_empty_mapping():
    missing = _APP_ROOT / "tools" / "does-not-exist-197.json"
    assert not missing.exists(), missing
    result = build_db.load_datasheet_overrides(str(missing))
    assert result == {}, result


def test_case_differing_key_matches_no_row():
    overrides = {
        _ROW_KEY: {"fields": {"programming.pulse_duration_us": {"was": 100, "is": 500}}}
    }
    decoded = _decoded()
    build_db.apply_datasheet_override(overrides, "fujitsu", {"mbm27c1000"}, decoded)
    assert decoded == _DECODED_TEMPLATE, decoded


def test_unmatched_override_key_raises_after_the_loop_not_inside_it():
    overrides = {
        _ROW_KEY: {
            "datasheet": "UNSOURCED",
            "note": "test fixture, no row will ever match this alias set",
            "fields": {"programming.pulse_duration_us": {"was": 100, "is": 500}},
        }
    }
    decoded = _decoded()
    consumed = set()
    result = build_db.apply_datasheet_override(
        overrides, "NOBODY", {"NOTHING"}, decoded, consumed_keys=consumed
    )
    assert result == _DECODED_TEMPLATE, result
    assert consumed == set(), consumed
    with pytest.raises(ValueError) as exc:
        build_db.check_all_override_keys_consumed(overrides, consumed)
    message = str(exc.value)
    assert _ROW_KEY in message, message


def test_duplicate_target_raises_naming_both_keys():
    overrides = {
        "INTEL/M2732": {
            "datasheet": "UNSOURCED",
            "note": "test fixture",
            "fields": {"electrical.vpp_mv": {"was": 18000, "is": 25000}},
        },
        "INTEL/M2732A": {
            "datasheet": "UNSOURCED",
            "note": "test fixture",
            "fields": {"electrical.vpp_mv": {"was": 18000, "is": 21000}},
        },
    }
    decoded = _decoded()
    decoded["electrical.vpp_mv"] = 18000
    with pytest.raises(ValueError) as exc:
        build_db.apply_datasheet_override(
            overrides, "INTEL", {"M2732", "M2732A"}, decoded
        )
    message = str(exc.value)
    assert "'INTEL/M2732'" in message, message
    assert "'INTEL/M2732A'" in message, message


def test_unsorted_file_raises_naming_first_out_of_order_key(tmp_path):
    override_file = tmp_path / "overrides.json"
    entry = {
        "datasheet": "UNSOURCED",
        "note": "test fixture",
        "fields": {"electrical.vpp_mv": {"was": 18000, "is": 25000}},
    }
    override_file.write_text(
        json.dumps({"ZEBRA/ALPHA": entry, "ALPHA/ZEBRA": entry}), encoding="utf-8"
    )
    with pytest.raises(ValueError) as exc:
        build_db.load_datasheet_overrides(str(override_file))
    message = str(exc.value)
    assert "ZEBRA/ALPHA" in message, message
    assert "ALPHA/ZEBRA" in message, message


def test_type_mismatch_float_vs_int_raises_without_coercion():
    overrides = {
        _ROW_KEY: {
            "fields": {"programming.pulse_duration_us": {"was": 100.0, "is": 500}}
        }
    }
    decoded = _decoded()
    with pytest.raises(ValueError) as exc:
        build_db.apply_datasheet_override(overrides, "FUJITSU", _ALIAS_SET, decoded)
    message = str(exc.value)
    assert _ROW_KEY in message, message
    assert "programming.pulse_duration_us" in message, message
    assert "100.0" in message, message


def test_boolean_does_not_satisfy_integer_type_check():
    overrides = {_ROW_KEY: {"fields": {"electrical.pin_count": {"was": True, "is": 2}}}}
    decoded = _decoded()
    decoded["electrical.pin_count"] = 1
    with pytest.raises(ValueError) as exc:
        build_db.apply_datasheet_override(overrides, "FUJITSU", _ALIAS_SET, decoded)
    message = str(exc.value)
    assert _ROW_KEY in message, message
    assert "electrical.pin_count" in message, message


def test_missing_datasheet_key_raises(tmp_path):
    override_file = tmp_path / "overrides.json"
    override_file.write_text(
        json.dumps(
            {
                _ROW_KEY: {
                    "fields": {"programming.pulse_duration_us": {"was": 100, "is": 500}}
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        build_db.load_datasheet_overrides(str(override_file))
    message = str(exc.value)
    assert _ROW_KEY in message, message
    assert "datasheet" in message, message


def test_missing_or_empty_fields_raises(tmp_path):
    override_file = tmp_path / "overrides.json"
    override_file.write_text(
        json.dumps({_ROW_KEY: {"datasheet": "UNSOURCED", "note": "x", "fields": {}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        build_db.load_datasheet_overrides(str(override_file))
    message = str(exc.value)
    assert _ROW_KEY in message, message
    assert "fields" in message, message


def test_well_formed_multi_entry_file_loads_without_raising(tmp_path):
    override_file = tmp_path / "overrides.json"
    override_file.write_text(
        json.dumps(
            {
                "ALPHA/ONE": {
                    "datasheet": "UNSOURCED",
                    "note": "x",
                    "fields": {"electrical.vpp_mv": {"was": 18000, "is": 25000}},
                },
                "ZEBRA/TWO": {
                    "datasheet": "UNSOURCED",
                    "note": "y",
                    "fields": {
                        "programming.pulse_duration_us": {"was": 100, "is": 500}
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    result = build_db.load_datasheet_overrides(str(override_file))
    assert set(result) == {"ALPHA/ONE", "ZEBRA/TWO"}, result


_DATASHEET_OVERRIDES_FILE = Path(
    os.environ.get(
        "FIRESTARTER_DATASHEET_OVERRIDES_FILE",
        str(_APP_ROOT / "tools" / "datasheet_overrides.json"),
    )
)

_UNSOURCED = "UNSOURCED"
_EXPECTED_UNSOURCED_COUNT = 18
_EXPECTED_ENTRY_COUNT = 22

_MBM27C4001_ALIAS_SET = {"MBM27C4001"}


def test_mbm27c4001_noop_entry_raises():
    overrides = {
        "FUJITSU/MBM27C4001": {
            "datasheet": "datasheets/MBM27C4001.pdf",
            "note": "test fixture: MBM27C4001's own datasheet confirms 100 is already correct",
            "fields": {"programming.pulse_duration_us": {"was": 100, "is": 100}},
        }
    }
    decoded = _decoded()
    decoded["programming.pulse_duration_us"] = 100
    with pytest.raises(ValueError) as exc:
        build_db.apply_datasheet_override(
            overrides, "FUJITSU", _MBM27C4001_ALIAS_SET, decoded
        )
    message = str(exc.value)
    assert "FUJITSU/MBM27C4001" in message, message
    assert "programming.pulse_duration_us" in message, message
    assert "100" in message, message


def _is_git_tracked(repo_root, relative_path):
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "--error-unmatch", relative_path],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


class TestShippedOverrideFileContract:
    def test_shipped_file_parses(self):
        with open(_DATASHEET_OVERRIDES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict), type(data)

    def test_every_datasheet_value_is_a_tracked_path_or_unsourced_with_note(self):
        with open(_DATASHEET_OVERRIDES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        for row_key, entry in data.items():
            datasheet = entry.get("datasheet")
            note = entry.get("note", "")
            if datasheet == _UNSOURCED:
                assert note, f"{row_key}: datasheet is {_UNSOURCED!r} but note is empty"
                continue
            candidate = _APP_ROOT / datasheet
            assert candidate.exists(), (
                f"{row_key}: datasheet path {datasheet!r} does not exist "
                f"under {_APP_ROOT}"
            )
            assert _is_git_tracked(_APP_ROOT, datasheet), (
                f"{row_key}: datasheet path {datasheet!r} exists but is not "
                f"git-tracked in {_APP_ROOT}"
            )

    def test_unsourced_count_is_pinned_exactly(self):
        with open(_DATASHEET_OVERRIDES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        unsourced = [k for k, v in data.items() if v.get("datasheet") == _UNSOURCED]
        assert len(unsourced) == _EXPECTED_UNSOURCED_COUNT, unsourced

    def test_total_entry_count_is_pinned_exactly(self):
        with open(_DATASHEET_OVERRIDES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == _EXPECTED_ENTRY_COUNT, sorted(data)
