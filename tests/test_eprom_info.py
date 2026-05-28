"""Phase 42 / ERR-03 fallback coverage lift for ``EpromConsolePresenter`` (D-14
fallback per CONTEXT — eprom_info.py at 19% is the largest gap).

Targets the pure helper methods (``_json_output_formatted``,
``_clean_config_for_export``, ``_prepare_export_configuration_data``) and the
not-found path through ``prepare_detailed_eprom_data``. The full
``prepare_detailed_eprom_data`` happy path is NOT exercised here because it
triggers the pre-existing ic_layout ``vpp-pin <= pin_count`` TypeError (the
list-vs-int bug pinned by Phase 36 ``test_info_known_chip_stderr`` snapshot).
That snapshot is the GATE-1.8b witness; the bug is deferred to v1.9.
"""

import pytest

from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter


@pytest.fixture(scope="module")
def db() -> EpromDatabase:
    return EpromDatabase(skip_local_override=True)


@pytest.fixture(scope="module")
def presenter(db: EpromDatabase) -> EpromConsolePresenter:
    return EpromConsolePresenter(db)


def test_prepare_detailed_returns_none_for_missing_eprom(
    presenter: EpromConsolePresenter,
) -> None:
    """prepare_detailed_eprom_data returns None when eprom_details is None."""
    result = presenter.prepare_detailed_eprom_data(
        "MISSING_CHIP",
        None,
        None,
        None,
        None,
    )
    assert result is None


def test_json_output_formatted_compacts_number_lists(
    presenter: EpromConsolePresenter,
) -> None:
    """_json_output_formatted compacts lists of integers onto a single line."""
    payload = {"bus-config": {"data": [1, 2, 3, 4, 5]}}
    out = presenter._json_output_formatted(payload)
    assert "[1, 2, 3, 4, 5]" in out


def test_clean_config_for_export_strips_vdd(
    presenter: EpromConsolePresenter,
) -> None:
    """_clean_config_for_export drops the 'vdd' voltage but keeps 'vcc' / 'vpp'."""
    raw = {
        "name": "Test",
        "voltages": {"vdd": 5.0, "vcc": 5.0, "vpp": 12.0},
        "has-chip-id": True,
        "chip-id": "0x42",
    }
    cleaned = presenter._clean_config_for_export(raw)
    assert "vdd" not in cleaned["voltages"]
    assert cleaned["voltages"]["vcc"] == 5.0
    assert cleaned["voltages"]["vpp"] == 12.0
    assert cleaned["chip-id"] == "0x42"


def test_clean_config_without_chip_id_strips_chip_id_key(
    presenter: EpromConsolePresenter,
) -> None:
    """When has-chip-id is False the chip-id key is removed."""
    raw = {
        "name": "Test",
        "voltages": {},
        "has-chip-id": False,
    }
    cleaned = presenter._clean_config_for_export(raw)
    assert "chip-id" not in cleaned


def test_clean_config_falls_back_to_variant_pin_map(
    presenter: EpromConsolePresenter,
) -> None:
    """When pin-map is absent, pin-map falls back to 'variant', then to 'default'."""
    # Variant fallback
    raw = {"name": "X", "voltages": {}, "has-chip-id": False, "variant": "v1"}
    cleaned = presenter._clean_config_for_export(raw)
    assert cleaned["pin-map"] == "v1"

    # Default fallback when neither is present
    raw_default = {"name": "Y", "voltages": {}, "has-chip-id": False}
    cleaned_default = presenter._clean_config_for_export(raw_default)
    assert cleaned_default["pin-map"] == "default"


def test_prepare_export_configuration_with_missing_inputs_returns_none(
    presenter: EpromConsolePresenter,
) -> None:
    """_prepare_export_configuration_data returns None when raw_config / manufacturer is missing."""
    assert presenter._prepare_export_configuration_data(None, "X", "name") is None
    assert presenter._prepare_export_configuration_data({}, None, "name") is None


def test_present_eprom_details_none_returns_early(
    presenter: EpromConsolePresenter, capsys
) -> None:
    """present_eprom_details with None chip_data short-circuits without crashing."""
    presenter.present_eprom_details(None)
    # No assertion on output — just that it does not raise.
