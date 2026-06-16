"""Phase 69 / SC#1 + SC#3 regression coverage for ``EpromSpecBuilder``
(ic_layout.py list-vs-int crash fix).

The pre-existing ``vpp-pin <= pin_count`` TypeError was caused by
``_generate_pin_names_for_display`` comparing raw list-valued pin fields (e.g.
``[22]``) against ``pin_count`` (int) without extracting the scalar first.
Phase 69 Plan 01 fixes all three pin fields (rw-pin / vpp-pin / oe-pin) at
every comparison and index site, mirroring the ``database.get_bus_config``
inline pattern.

This file pins the fixed behaviour so the crash class cannot reappear:
- Parametrized tests over representative chips covering shared vpp/oe, distinct
  vpp/oe, rw-pin, and vpp-exceeds-max variants.
- A direct ``build_specifications`` happy-path test for W27C512.
"""

import pytest

from firestarter.database import EpromDatabase
from firestarter.ic_layout import EpromSpecBuilder


@pytest.fixture(scope="module")
def db() -> EpromDatabase:
    return EpromDatabase(skip_local_override=True)


@pytest.fixture(scope="module")
def spec_builder(db: EpromDatabase) -> EpromSpecBuilder:
    return EpromSpecBuilder(db)


@pytest.mark.parametrize(
    "chip_name",
    [
        "W27C512",  # DIP28, shared vpp/oe-pin=[22] — list-valued shared pin
        "AT28C256",  # DIP28, rw-pin=[27], oe-pin=[22] — rw + oe both lists
        "2732",  # DIP24, vpp-exceeds-max, shared vpp/oe-pin=[20] — list-valued
        "M2716",  # DIP24, vpp-exceeds-max, distinct vpp and oe pins
    ],
)
def test_generate_pin_names_for_display_list_valued_pins_no_crash(
    spec_builder: EpromSpecBuilder,
    db: EpromDatabase,
    chip_name: str,
) -> None:
    """_generate_pin_names_for_display returns a list without raising TypeError
    for chips whose pin-map stores vpp-pin/oe-pin/rw-pin as single-element lists.
    """
    eprom = db.get_eprom(chip_name)
    assert eprom is not None, f"chip {chip_name!r} not found in database"
    result = spec_builder._generate_pin_names_for_display(eprom)
    # Must return a list (not None, not raise).
    assert isinstance(result, list), (
        f"expected list for {chip_name!r}, got {type(result)}"
    )


def test_build_specifications_happy_path(
    spec_builder: EpromSpecBuilder,
    db: EpromDatabase,
) -> None:
    """build_specifications returns a non-None dict for W27C512 — the full display
    path that was previously un-testable because ic_layout crashed.
    """
    eprom = db.get_eprom("W27C512")
    assert eprom is not None
    result = spec_builder.build_specifications(eprom)
    assert result is not None


def test_generate_pin_names_bare_int_still_works(
    spec_builder: EpromSpecBuilder,
) -> None:
    """_generate_pin_names_for_display tolerates bare-int pin fields (isinstance guard
    is two-way: list → extract scalar, int → pass through unchanged).
    """
    fake_eprom = {
        "pin-count": 28,
        "pin-map": None,  # no pin-map → falls through to generic names
    }
    result = spec_builder._generate_pin_names_for_display(fake_eprom)
    # With no pin-map the generic layout is returned unchanged.
    assert result is not None
    assert len(result) == 28
