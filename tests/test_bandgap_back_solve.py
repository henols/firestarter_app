"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

`bandgap_from_measured_vcc`: the host back-solves the bandgap calibration.

The firmware did this until 3.3. These oracle values are the ones its native
tests used, so the host gives the same stored bandgap for the same board.
"""

import pytest

from firestarter.constants import (
    BANDGAP_ADC_FULL_SCALE,
    CONFIG_BANDGAP_MAX_MV,
    CONFIG_BANDGAP_MIN_MV,
)
from firestarter.hardware import bandgap_from_measured_vcc


@pytest.mark.parametrize(
    ("vcc_mv", "count", "bandgap_mv"),
    [(5090, 206, 1024), (4940, 228, 1100), (5120, 210, 1050)],
)
def test_three_measured_boards(vcc_mv: int, count: int, bandgap_mv: int) -> None:
    assert bandgap_from_measured_vcc(vcc_mv, count) == bandgap_mv


def test_the_full_scale_is_the_avr_10_bit_adc() -> None:
    assert BANDGAP_ADC_FULL_SCALE == 1024


@pytest.mark.parametrize(
    ("vcc_mv", "count"),
    [
        (509, 206),  # volts, not mV
        (50900, 206),  # 50.9 V
        (5090, 0),  # no reading: a board without calibration
        (0, 206),
        (5090, 1025),  # above full scale
    ],
)
def test_an_unusable_input_is_refused_rather_than_clamped(
    vcc_mv: int, count: int
) -> None:
    assert bandgap_from_measured_vcc(vcc_mv, count) is None


def test_both_band_edges_are_kept() -> None:
    low = bandgap_from_measured_vcc(CONFIG_BANDGAP_MIN_MV * 1024 // 206, 206)
    high = bandgap_from_measured_vcc(CONFIG_BANDGAP_MAX_MV * 1024 // 206, 206)
    assert (low, high) == (CONFIG_BANDGAP_MIN_MV, CONFIG_BANDGAP_MAX_MV)


def test_full_scale_itself_still_evaluates() -> None:
    assert bandgap_from_measured_vcc(1100, 1024) == 1100


def test_the_result_rounds_to_the_nearest_mv() -> None:
    # 4999 * 225 / 1024 = 1098.39 and 5001 * 225 / 1024 = 1098.83.
    assert bandgap_from_measured_vcc(4999, 225) == 1098
    assert bandgap_from_measured_vcc(5001, 225) == 1099
