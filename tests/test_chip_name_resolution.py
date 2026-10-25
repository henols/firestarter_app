"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Which database row a chip name selects.

A part number can name rows of several manufacturers, and a mode-annotated
name such as "DS1220(TEST)" shares its stripped form with "DS1220(RW)". The
rows program differently (VPP, algorithm, chip ID, pulse width), so every row
must be reachable by some name, an exact name must select its own row, and an
unqualified name that also matches other rows must say so.
"""

import logging

import pytest

from firestarter.chip_resolver import resolve_chip
from firestarter.cli_handlers import _canonical_part_number
from firestarter.database import EpromDatabase, qualified_chip_name

_DB = EpromDatabase(skip_local_override=True)


def _rows():
    for manufacturer, chips in _DB.proms.items():
        for chip in chips:
            yield manufacturer, chip


def _wire(manufacturer, chip):
    return _DB.convert_to_programmer(_DB.map_chip_record(chip, manufacturer))


def test_every_alias_of_every_row_has_a_name_that_selects_that_row_alone():
    unreachable = []
    for manufacturer, chip in _rows():
        for alias in chip["part_number"].split(","):
            name = qualified_chip_name(alias, chip, manufacturer)
            matches = _DB.find_chip_configs(name)
            if len(matches) != 1 or matches[0][0] is not chip:
                unreachable.append(name)
    assert not unreachable


def test_a_full_comma_joined_part_number_still_selects_its_row():
    """Callers such as the dev test plan corpus pass the whole field."""
    missing = [
        chip["part_number"]
        for _, chip in _rows()
        if "," in chip["part_number"]
        and _DB.get_eprom_config(chip["part_number"])[0] is None
    ]
    assert not missing


def test_an_annotated_name_selects_its_own_row():
    """'DS1220(TEST)' must not select the '(RW)' row that the stripped name
    'DS1220' matches first."""
    wrong = []
    for manufacturer, chip in _rows():
        for alias in (a.strip() for a in chip["part_number"].split(",")):
            selected, _ = _DB.get_eprom_config(alias)
            if alias.lower() not in (
                a.strip().lower() for a in selected["part_number"].split(",")
            ):
                wrong.append(f"{alias} -> {selected['part_number']}")
    assert not wrong


def test_an_annotated_name_was_shadowed_before_this_rule():
    # Non-vacuity: the database has a stripped-name collision for the rule
    # above to resolve.
    assert len(_DB.find_chip_configs("DS1220")) > 1
    assert _DB.get_eprom_config("DS1220(TEST)")[0]["part_number"] == "DS1220(TEST)"


def test_an_unqualified_name_keeps_the_first_row_in_database_order():
    """Names that already matched exactly select the same row as before."""
    for manufacturer, chip in _rows():
        for alias in (a.strip() for a in chip["part_number"].split(",")):
            first = next(
                (m, c)
                for m, c in _rows()
                if alias.lower()
                in (a.strip().lower() for a in c["part_number"].split(","))
            )
            selected, selected_manufacturer = _DB.get_eprom_config(alias)
            assert (selected_manufacturer, selected) == first, alias


def test_the_manufacturer_qualifier_ignores_case_and_refuses_a_stranger():
    assert _DB.get_eprom_config("m2732a@st")[1] == "ST"
    assert _DB.get_eprom_config("M2732A@NOBODY") == (None, None)


def test_a_qualified_name_programs_with_that_manufacturers_row():
    st_chip = next(c for c in _DB.proms["ST"] if c["part_number"] == "M2732A")
    default = resolve_chip("M2732A", db=_DB)
    qualified = resolve_chip("M2732A@ST", db=_DB)
    assert qualified == _wire("ST", st_chip)
    assert qualified["vpp_mv"] != default["vpp_mv"]


def test_an_ambiguous_name_warns_and_names_each_alternative(caplog):
    with caplog.at_level(logging.WARNING, logger="ChipResolver"):
        resolve_chip("M2732A", db=_DB)
    text = caplog.text
    assert "Using 'M2732A@INTEL'" in text
    assert "'M2732A@ST'" in text
    assert "'M2732A@SGS-THOMSON'" in text


@pytest.mark.parametrize("name", ["W27C512", "M2732A@ST", "DS1220(TEST)"])
def test_a_name_that_selects_one_programming_warns_nothing(name, caplog):
    with caplog.at_level(logging.WARNING, logger="ChipResolver"):
        resolve_chip(name, db=_DB)
    assert caplog.text == ""


def test_alternatives_leave_out_rows_that_program_the_same():
    """A name matching several rows with one wire dict needs no choice."""
    for name in ("M2732A", "27C256", "DS1220"):
        chosen = _wire(*reversed(_DB.get_eprom_config(name)))
        for alternative in _DB.ambiguous_alternatives(name):
            chip, manufacturer = _DB.get_eprom_config(alternative)
            assert _wire(manufacturer, chip) != chosen, alternative


def test_the_report_name_leaves_out_the_qualifier():
    assert _canonical_part_number("M2732A", "M2732A@ST") == "M2732A"
