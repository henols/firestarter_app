"""Phase 42 / ERR-03 fallback coverage lift for ``EpromConsolePresenter`` (D-14
fallback per CONTEXT — eprom_info.py at 19% is the largest gap).

Targets the pure helper methods (``_json_output_formatted``,
``_prepare_export_configuration_data``) and the
not-found path through ``prepare_detailed_eprom_data``. The full
``prepare_detailed_eprom_data`` happy path is NOT exercised here because it
triggers the pre-existing ic_layout ``vpp-pin <= pin_count`` TypeError (the
list-vs-int bug pinned by Phase 36 ``test_info_known_chip_stderr`` snapshot).
That snapshot is the GATE-1.8b witness; the bug is deferred to v1.9.
"""

import copy
import json

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


def test_export_is_the_shipped_row_under_its_manufacturer(
    db: EpromDatabase, presenter: EpromConsolePresenter
) -> None:
    """`info -c` exports the database row unchanged, in the 3.x schema."""
    raw, manufacturer = db.get_eprom_config("W27C512")
    out = presenter._prepare_export_configuration_data(raw, manufacturer, "W27C512")
    exported = json.loads(out["eprom_config_json_str"])
    assert exported == {manufacturer: [raw]}
    assert "database.json" in out["eprom_config_title"]
    assert "Unknown" not in out["eprom_config_json_str"]


def test_export_carries_the_rows_pin_map(
    db: EpromDatabase, presenter: EpromConsolePresenter
) -> None:
    raw, manufacturer = db.get_eprom_config("W27C512")
    out = presenter._prepare_export_configuration_data(raw, manufacturer, "W27C512")
    pin_map = json.loads(out["pin_map_config_json_str"])
    assert pin_map == {raw["pinout"]: db.pin_maps[raw["pinout"]]}


def test_export_does_not_alias_the_database(
    db: EpromDatabase, presenter: EpromConsolePresenter
) -> None:
    raw, manufacturer = db.get_eprom_config("W27C512")
    before = copy.deepcopy(raw)
    presenter._prepare_export_configuration_data(raw, manufacturer, "W27C512")
    assert raw == before


def test_exported_row_replaces_the_shipped_row_as_an_override() -> None:
    """An exported row, edited and loaded as an override, wins over the shipped row.

    The merge used to key override rows on "name", which no 3.x row has, so
    the edited row was appended and get_eprom_config still returned the
    shipped one.
    """
    local = EpromDatabase(skip_local_override=True)
    raw, manufacturer = local.get_eprom_config("W27C512")
    edited = copy.deepcopy(raw)
    edited["programming"]["pulse_duration_us"] = 4321
    rows_before = len(local.proms[manufacturer])

    local._merge_databases(local.proms, {manufacturer: [edited]})

    got, _ = local.get_eprom_config("W27C512")
    assert got["programming"]["pulse_duration_us"] == 4321
    assert len(local.proms[manufacturer]) == rows_before


def test_override_keyed_by_name_replaces_the_shipped_row() -> None:
    """3.1.0 matched override rows on `name`, and the wiki documents that key.

    An override file written that way must keep working.
    """
    local = EpromDatabase(skip_local_override=True)
    raw, manufacturer = local.get_eprom_config("W27C512")
    rows_before = len(local.proms[manufacturer])
    override = {
        "name": raw["part_number"],
        "programming": dict(raw["programming"], pulse_duration_us=4321),
    }

    local._merge_databases(local.proms, {manufacturer: [override]})

    got, _ = local.get_eprom_config("W27C512")
    assert got["programming"]["pulse_duration_us"] == 4321
    assert len(local.proms[manufacturer]) == rows_before


def test_override_with_a_new_part_number_is_added() -> None:
    local = EpromDatabase(skip_local_override=True)
    raw, manufacturer = local.get_eprom_config("W27C512")
    new_row = copy.deepcopy(raw)
    new_row["part_number"] = "MYTEST27C512"
    rows_before = len(local.proms[manufacturer])

    local._merge_databases(local.proms, {manufacturer: [new_row]})

    assert len(local.proms[manufacturer]) == rows_before + 1
    assert local.get_eprom_config("MYTEST27C512")[0] is not None
    assert local.get_eprom_config("W27C512")[0]["part_number"] == raw["part_number"]


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


def test_prepare_detailed_eprom_data_happy_path(
    db: EpromDatabase,
    presenter: EpromConsolePresenter,
) -> None:
    """prepare_detailed_eprom_data returns non-None for W27C512 after ic_layout fix.

    Phase 69 Plan 01 fixed the ic_layout list-vs-int crash; this test pins the
    happy path that was previously un-testable (would always raise TypeError in
    _generate_pin_names_for_display). W27C512 has a list-valued shared vpp/oe-pin
    so it exercises the exact scalar-extraction path that was broken.
    """
    eprom = db.get_eprom("W27C512")
    assert eprom is not None
    bus_config = db.convert_to_programmer(eprom)
    raw_config, manufacturer = db.get_eprom_config("W27C512")
    result = presenter.prepare_detailed_eprom_data(
        "W27C512",
        eprom,
        bus_config,
        raw_config,
        manufacturer,
    )
    assert result is not None
