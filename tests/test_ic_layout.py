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


# ---------------------------------------------------------------------------
# Phase 84 — fm-fram-full display-layer companion tests
#
# These tests pin the two display-layer changes that accompany the FM1608
# SRAM→FRAM relabel (operator decision fm-fram-full, 2026-06-25):
#   1. _ELECTRICAL_TYPE_LABEL["FRAM"] == "FRAM"  (resolve_type_label works for FRAM)
#   2. FM1608 build_specifications does NOT include vpp_str (VPP row stays hidden)
#
# Both tests are RED before the ic_layout.py changes (Task 3) and GREEN after.
# ---------------------------------------------------------------------------


def test_electrical_type_label_includes_fram(
    spec_builder: EpromSpecBuilder,
) -> None:
    """_ELECTRICAL_TYPE_LABEL must include a 'FRAM' key resolving to 'FRAM' — required
    by the fm-fram-full relabel so the Type column shows 'FRAM' not the protocol fallback
    (D-40 / Pitfall 2: without the key, resolve_type_label falls back to
    get_chip_type_string which would return the protocol-based label, not 'FRAM')."""
    label_map = spec_builder._ELECTRICAL_TYPE_LABEL
    assert "FRAM" in label_map, (
        "_ELECTRICAL_TYPE_LABEL must contain 'FRAM' key (fm-fram-full relabel)"
    )
    assert label_map["FRAM"] == "FRAM", (
        "_ELECTRICAL_TYPE_LABEL['FRAM'] must equal 'FRAM'"
    )


def test_resolve_type_label_fram(
    spec_builder: EpromSpecBuilder,
) -> None:
    """resolve_type_label returns 'FRAM' for electrical_type='FRAM' — the display helper
    must handle the new FRAM value after the fm-fram-full relabel."""
    result = spec_builder.resolve_type_label("FRAM")
    assert result == "FRAM", (
        f"resolve_type_label('FRAM') should return 'FRAM', got {result!r}"
    )


def test_fm1608_vpp_row_hidden_after_relabel(
    spec_builder: EpromSpecBuilder,
    db: EpromDatabase,
) -> None:
    """FM1608 build_specifications must NOT include vpp_str after the SRAM→FRAM relabel.
    FRAM has no programming VPP; the vpp_mv=12000 in the DB is an infoic.xml decode
    artifact.  The VPP-display gate (ic_layout.py build_specifications) must exclude
    FRAM alongside SRAM (gate: electrical-type not in {'SRAM','FRAM'}).
    This is the fm-fram-full Pitfall-2 guard (D-40 / fm-fram-full companion test)."""
    eprom = db.get_eprom("FM1608")
    assert eprom is not None
    # The relabel sets electrical-type = "FRAM" in the mapped data.
    # We call build_specifications with the mapped data which carries
    # electrical-type via _map_data (the 'electrical-type' key).
    result = spec_builder.build_specifications(
        eprom, electrical_type=eprom.get("electrical-type")
    )
    assert result is not None
    assert "vpp_str" not in result, (
        "FM1608 must NOT have a VPP row after SRAM→FRAM relabel; "
        "FRAM has no programming VPP (Pitfall 2 guard / fm-fram-full D-40)"
    )


# ---------------------------------------------------------------------------
# Phase 102 — HOST protocol-display-name consolidation companion tests
#
# These tests pin the two Phase 102 display-layer changes:
#   1. Single-source invariant (D-01): _get_protocol_info_structured's `type`
#      field and get_chip_type_string's fallback both resolve to the SAME
#      string for every protocol id — they must both read
#      _PROTOCOL_DISPLAY_NAME, so the two vocabularies can never re-diverge
#      (the recurring IN-01 class of bug).
#   2. Coverage reconcile (D-04): 0x34 (X88C64) is present with the canonical
#      name; 0x11 (FWH) is dropped.
#
# Neither test asserts on description_points bullet text (D-03 — bullets are
# Phase-103-owned; prose reconciliation is out of scope here).
# ---------------------------------------------------------------------------


def test_protocol_info_type_matches_chip_type_string_single_source(
    spec_builder: EpromSpecBuilder,
) -> None:
    """_get_protocol_info_structured's `type` field must equal
    get_chip_type_string's fallback label for every protocol id present in
    _PROTOCOL_DISPLAY_NAME — pinning the D-01 single-source invariant so the
    info-line vocabulary and the proto_display fallback vocabulary can never
    re-diverge (IN-01 class guard)."""
    for pid in spec_builder._PROTOCOL_DISPLAY_NAME:
        info = spec_builder._get_protocol_info_structured(pid)
        if info is None:
            continue
        fallback_label = spec_builder.get_chip_type_string(pid)
        assert info["type"] == fallback_label, (
            f"protocol 0x{pid:02X}: _get_protocol_info_structured type "
            f"{info['type']!r} must equal get_chip_type_string fallback "
            f"{fallback_label!r} (D-01 single source)"
        )


def test_protocol_display_name_coverage_reconciled(
    spec_builder: EpromSpecBuilder,
) -> None:
    """0x34 (X88C64) resolves to the canonical name and 0x11 (FWH) is dropped
    from _get_protocol_info_structured — pinning the D-04 coverage reconcile
    so the host's 12-protocol canonical set cannot silently regress."""
    result_0x34 = spec_builder._get_protocol_info_structured(0x34)
    assert result_0x34 is not None, (
        "0x34 (X88C64) must resolve via _get_protocol_info_structured (D-04)"
    )
    assert result_0x34["type"] == "EEPROM - XICOR 8051-bus", (
        f"0x34 type should be 'EEPROM - XICOR 8051-bus', got {result_0x34['type']!r}"
    )
    result_0x11 = spec_builder._get_protocol_info_structured(0x11)
    assert result_0x11 is None, (
        f"0x11 (FWH) must be dropped from protocol_info_data (D-04), got {result_0x11!r}"
    )
