"""
Tests for the validation matrix spec schema validation (HARN-02 / D-01).

Covers:
- validate_spec() accepts a valid spec without raising
- validate_spec() raises ValueError on schema violations
- The shipped validation_matrix_spec.json parses and enumerates all 6 families
"""

import json
import sys
from pathlib import Path

import pytest

# Add tools directory to sys.path so we can import gen_validation_header
_TOOLS_DIR = Path(__file__).parent.parent / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from gen_validation_header import validate_spec  # noqa: E402

SPEC_PATH = _TOOLS_DIR / "validation_matrix_spec.json"

EXPECTED_HANDLERS = {
    "configure_eprom",
    "configure_eeprom28c",
    "configure_flash_nor_unlock",
    "configure_flash_5v_page",
    "configure_flash_intel",
    "configure_sram",
}


def _minimal_valid_spec() -> dict:
    """Return a minimal valid spec for parametric negative-case testing."""
    return {
        "schema_version": 1,
        "families": [
            {
                "id": "eprom",
                "handler": "configure_eprom",
                "protocols": [7, 8, 11],
                "rep_chip": "W27C512",
                "tier1": {"suite": "test_val_eprom", "assertions": []},
                "tier2": {"test_module": "test_val_wire_eprom", "commands": []},
                "tier3": {
                    "test_chip": "W27C512",
                    "boards": ["leonardo"],
                    "skip_boards": [],
                },
            }
        ],
    }


class TestValidateSpecPositive:
    def test_valid_spec_does_not_raise(self) -> None:
        """validate_spec() on a well-formed spec returns without raising."""
        spec = _minimal_valid_spec()
        validate_spec(spec)  # must not raise

    def test_valid_spec_with_full_6_families(self) -> None:
        """validate_spec() accepts the full 6-family spec."""
        with open(SPEC_PATH, encoding="utf-8") as f:
            spec = json.load(f)
        validate_spec(spec)  # must not raise


class TestValidateSpecNegative:
    def test_missing_schema_version_raises(self) -> None:
        spec = _minimal_valid_spec()
        del spec["schema_version"]
        with pytest.raises(ValueError, match="schema_version"):
            validate_spec(spec)

    def test_schema_version_not_int_raises(self) -> None:
        spec = _minimal_valid_spec()
        spec["schema_version"] = "1"
        with pytest.raises(ValueError, match="schema_version"):
            validate_spec(spec)

    def test_missing_families_raises(self) -> None:
        spec = _minimal_valid_spec()
        del spec["families"]
        with pytest.raises(ValueError, match="families"):
            validate_spec(spec)

    def test_empty_families_raises(self) -> None:
        spec = _minimal_valid_spec()
        spec["families"] = []
        with pytest.raises(ValueError, match="families"):
            validate_spec(spec)

    def test_family_missing_handler_raises(self) -> None:
        spec = _minimal_valid_spec()
        del spec["families"][0]["handler"]
        with pytest.raises(ValueError, match="handler"):
            validate_spec(spec)

    def test_family_missing_id_raises(self) -> None:
        spec = _minimal_valid_spec()
        del spec["families"][0]["id"]
        with pytest.raises(ValueError, match="'id'"):
            validate_spec(spec)

    def test_family_missing_protocols_raises(self) -> None:
        spec = _minimal_valid_spec()
        del spec["families"][0]["protocols"]
        with pytest.raises(ValueError, match="protocols"):
            validate_spec(spec)

    def test_protocols_not_list_raises(self) -> None:
        spec = _minimal_valid_spec()
        spec["families"][0]["protocols"] = 7
        with pytest.raises(ValueError, match="protocols"):
            validate_spec(spec)

    def test_protocols_with_string_elements_raises(self) -> None:
        spec = _minimal_valid_spec()
        spec["families"][0]["protocols"] = ["0x07"]
        with pytest.raises(ValueError, match="protocol"):
            validate_spec(spec)


class TestShippedSpec:
    def test_spec_file_exists(self) -> None:
        """The shipped spec file must exist at tools/validation_matrix_spec.json."""
        assert SPEC_PATH.exists(), f"Spec not found: {SPEC_PATH}"

    def test_spec_has_exactly_6_families(self) -> None:
        with open(SPEC_PATH, encoding="utf-8") as f:
            spec = json.load(f)
        assert len(spec["families"]) == 6

    def test_spec_enumerates_all_6_handlers(self) -> None:
        with open(SPEC_PATH, encoding="utf-8") as f:
            spec = json.load(f)
        handlers = {f["handler"] for f in spec["families"]}
        assert handlers == EXPECTED_HANDLERS

    def test_protocol_ids_are_integers(self) -> None:
        """Protocol IDs must be integers (decimal), not hex strings."""
        with open(SPEC_PATH, encoding="utf-8") as f:
            spec = json.load(f)
        for fam in spec["families"]:
            for proto in fam["protocols"]:
                assert isinstance(proto, int), (
                    f"Family {fam['id']!r}: protocol {proto!r} is not an int"
                )

    def test_protocol_ids_cover_expected_set(self) -> None:
        """Union of all host-spec protocol IDs must cover the known 11 decimal values.

        flash4's 0x35/0x39 (53/57) were trimmed from the host spec in 71-08 (CR-02):
        firmware + the Tier-1 native suite still cover {0x05,0x35,0x39}, but zero DB
        chips carry 0x35/0x39 so the host never dispatches them — the host spec lists
        only 0x05 for flash4. See validation_matrix_spec.json `protocols_note`.
        """
        # Decimal equivalents of the host-spec family protocol IDs:
        # 0x07=7, 0x08=8, 0x0B=11, 0x0D=13, 0x06=6, 0x05=5,
        # 0x10=16, 0x0E=14, 0x27=39, 0x28=40, 0x29=41
        expected = {7, 8, 11, 13, 6, 5, 16, 14, 39, 40, 41}
        with open(SPEC_PATH, encoding="utf-8") as f:
            spec = json.load(f)
        actual: set[int] = set()
        for fam in spec["families"]:
            actual.update(fam["protocols"])
        assert expected == actual

    def test_spec_filename_uses_underscores(self) -> None:
        """Authored spec uses underscores + singular (distinct from hyphenated emitted artifact)."""
        assert SPEC_PATH.name == "validation_matrix_spec.json"
