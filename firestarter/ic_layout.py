"""
Project Name: Firestarter
Copyright (c) 2025 Henrik Olsson

Permission is hereby granted under MIT license.
IC Layout Generation Module
"""

import logging
from typing import Dict, List, Optional  # noqa: UP035

from firestarter.database import EpromDatabase  # Changed import

logger = logging.getLogger("EpromSpecBuilder")


class EpromSpecBuilder:
    """
    Builds a comprehensive dictionary of technical specifications for an EPROM.
    This includes its fundamental properties, pin assignments, relevant programmer
    jumper settings, communication protocol details, and flag interpretations.
    The output is a structured data object intended for further processing or display.
    """

    # Generic pin names for 24-pin, 28-pin, and 32-pin EPROMs
    _generic_pin_names_map = {
        24: [
            "A7",
            "A6",
            "A5",
            "A4",
            "A3",
            "A2",
            "A1",
            "A0",
            "D0",
            "D1",
            "D2",
            "GND",
            "D3",
            "D4",
            "D5",
            "D6",
            "D7",
            "CE",
            "NC",
            "OE",
            "NC",
            "NC",
            "NC",
            "VCC",
        ],
        28: [
            "NC",
            "NC",
            "A7",
            "A6",
            "A5",
            "A4",
            "A3",
            "A2",
            "A1",
            "A0",
            "D0",
            "D1",
            "D2",
            "GND",
            "D3",
            "D4",
            "D5",
            "D6",
            "D7",
            "CE",
            "NC",
            "OE/VPP",
            "NC",
            "NC",
            "NC",
            "NC",
            "NC",
            "VCC",
        ],
        32: [
            "NC",
            "NC",
            "NC",
            "NC",
            "A7",
            "A6",
            "A5",
            "A4",
            "A3",
            "A2",
            "A1",
            "A0",
            "D0",
            "D1",
            "D2",
            "GND",
            "D3",
            "D4",
            "D5",
            "D6",
            "D7",
            "CE",
            "NC",
            "OE",
            "NC",
            "NC",
            "NC",
            "NC",
            "NC",
            "NC",
            "R/W(WE)",
            "VCC",
        ],
    }

    def __init__(self, db_instance: EpromDatabase):
        self.db = db_instance

    @staticmethod
    def _first_pin(pin_field: list) -> int:
        """Extract a scalar pin number from a single-element list pin field.

        Pin map fields like vpp-pin, oe-pin, and rw-pin are stored as
        single-element lists in pinouts.json (e.g. [22]).  This helper
        returns the first element so the caller can use it as an integer
        index or in arithmetic comparisons.
        """
        return pin_field[0]

    def _select_jumper_label(self, jp_setting: int, label1: str, label2: str) -> str:
        if jp_setting == 1:
            return label1
        if jp_setting == 2:
            return label2
        return "NA"

    def _get_rev1_jumper_settings_data(self, jp1: int, jp2: int, jp3: int) -> dict:
        """Generates structured data for Rev 0.1 & 1.0 jumper settings."""
        jumper_display = [" ● ● ● ", " ●(● ●)", "(● ●)● "]  # 0: N/A, 1: Pos1, 2: Pos2
        jp1_label = self._select_jumper_label(jp1, "A13", "VCC")
        jp2_label = self._select_jumper_label(jp2, "A17", "VCC")
        jp3_label = self._select_jumper_label(jp3, "32pin", "28pin")
        return {
            "0.1 & 1.0": {
                "jp1": {
                    "config_text": "5V",
                    "display": jumper_display[jp1],
                    "pin_text": "A13",
                    "selected_label": jp1_label,
                },
                "jp2": {
                    "config_text": "5V",
                    "display": jumper_display[jp2],
                    "pin_text": "A17",
                    "selected_label": jp2_label,
                },
                "jp3": {
                    "config_text": "28pin",
                    "display": jumper_display[jp3],
                    "pin_text": "32pin",
                    "selected_label": jp3_label,
                },
            }
        }

    def _get_rev2_jumper_settings_data(self, jp4: int) -> dict:
        """Generates structured data for Rev 2.0 & 2.1 jumper settings."""
        jp4_label = self._select_jumper_label(
            jp4, "Open", "Closed"
        )  # Assuming 1=Open, 2=Closed
        jumper_display = [" N/A   ", " ● ●   ", "(● ●)  "]  # 0: N/A, 1: Open, 2: Closed
        return {
            "2.0 & 2.1": {
                "jp4": {
                    "config_text": "28pin",
                    "display": jumper_display[jp4],
                    "pin_text": "32pin",
                    "selected_label": jp4_label,
                },
            }
        }

    def _get_rev2_2_jumper_settings_data(self, jp5: int) -> dict:
        """Generates structured data for Rev 2.2 jumper settings."""
        jp5_label = self._select_jumper_label(
            jp5, "Open", "Closed"
        )  # Assuming 1=Open, 2=Closed
        jumper_display = [" N/A   ", " ● ●   ", "(● ●)  "]  # 0: N/A, 1: Open, 2: Closed
        return {
            "2.2": {
                "jp5": {
                    "config_text": "28pin",
                    "display": jumper_display[jp5],
                    "pin_text": "32pin",
                    "selected_label": jp5_label,
                },
            }
        }

    def get_chip_type_string(self, protocol_id: int | None = None) -> str:
        """Return a user-facing chip-type label.

        When protocol_id is supplied, use it to look up the protocol-based
        display label. The protocol-based labels are aligned with the
        algorithm-family names in firestarter/CLAUDE.md so the displayed
        type matches the firmware dispatch path the chip actually takes.
        Falls back to the bare string "Unknown" when the protocol is absent
        or unrecognized.
        """
        if protocol_id is not None:
            # 0x35 (ITE EC MCU, 0 DB chips) and 0x39 (phantom, 0 DB chips) removed
            # in Phase 57 (DEC-05); no DB chip uses either protocol. Firmware still
            # dispatches both → configure_flash4 for forward-compat (memory.cpp:89);
            # host routes them to not_implemented (excluded from KNOWN_PROTOCOLS).
            if protocol_id in self._PROTOCOL_DISPLAY_NAME:
                return self._PROTOCOL_DISPLAY_NAME[protocol_id]
        return "Unknown"

    def _interpret_flags(self, flags: int) -> list[str]:
        """Interpret the info-flags value and return a list of properties.

        Only two bits are derivable from the current chip_database.json pipeline:
          0x10 — electrically erasable (set for EEPROM and Flash/EEPROM families)
          0x20 — provides readable manufacturer/device ID (set when chip_id_check=True)

        All other bits (0x08, 0x40, 0x80, 0x200, 0x4000, 0x8000, 0x400000) are not
        produced by _map_data from the current DB and are omitted to avoid misleading
        output.  Re-add them if a future DB revision carries those signals.
        """
        properties = []
        flag_definitions = [
            (0x00000010, "Electrically erasable"),
            (0x00000020, "Provides readable manufacturer/device ID"),
        ]
        for bitmask, description in flag_definitions:
            if flags & bitmask:
                properties.append(description)
        return properties

    def _get_protocol_info_structured(self, protocol_id: int) -> dict | None:
        """Returns structured protocol information."""
        protocol_info_data = [
            (
                0x05,
                "EEPROM/Flash",
                (
                    "EEPROM/Flash with write enable sequence, software commands",
                    "Requires specific software commands for programming/erasure",
                    "Operates at standard voltage levels",
                ),
            ),
            (
                0x06,
                "Flash Memory",
                (
                    "Standard Flash memory programming protocol",
                    "Uses command sequences for programming/erasure",
                    "Operates at standard voltage levels",
                ),
            ),
            (
                0x07,
                "EPROM/EEPROM",
                (
                    "JEDEC 28-pin EPROM algorithm (also covers compatible 28C parts)",
                    "Requires VPP on OE/VPP pin and byte-program style pulses",
                    "Vendors may enable software data protection/unlock cycles",
                ),
            ),
            (
                0x08,
                "Large EPROM",
                (
                    "High-voltage EPROM algorithm for 32-pin devices",
                    "Uses ≥12 V VPP and EPROM-style timing",
                    "Covers classic 27C010/020/040 and EPROM-like 28C oddballs (Linkage/PTC)",  # noqa: E501
                ),
            ),
            (
                0x0B,
                "Legacy EPROM/EEPROM",
                (
                    "Programming protocol for older 24-pin devices",
                    "Shares pins between OE/VPP so high voltage is common",
                    "Targets small capacity 2716/2732/28C04/16 era parts",
                ),
            ),
            (
                0x0D,
                "EEPROM",
                (
                    "Programming protocol for large EEPROMs",
                    "Supports byte-wise programming",
                    "May require specific write sequences",
                ),
            ),
            (
                0x0E,
                "SRAM",
                (
                    "SRAM with battery backup or additional features",
                    "Standard SRAM access protocols",
                    "32-pin devices",
                ),
            ),
            (
                0x10,
                "Flash Memory",
                (
                    "Intel-compatible Flash memory programming protocol",
                    "Requires specific command sequences",
                    "Operates at standard voltage levels",
                ),
            ),
            (
                0x27,
                "SRAM",
                (
                    "Standard SRAM access protocol for 24-pin devices",
                    "2Kb SRAM devices",
                    "Simple read/write operations",
                ),
            ),
            (
                0x28,
                "SRAM",
                (
                    "Standard SRAM access protocol for 28-pin devices",
                    "8Kb SRAM devices",
                    "Simple read/write operations",
                ),
            ),
            (
                0x29,
                "SRAM",
                (
                    "Standard SRAM access protocol for 32-pin devices",
                    "512Kb to 1Mb SRAM devices",
                    "Simple read/write operations",
                ),
            ),
            (
                # Phase 102 D-04: added — X88C64 (1 DB chip) can surface in
                # `info`. Bullet is a minimal, non-minipro-heritage placeholder
                # (Phase 102 D-03/D-05: name-only scope, prose reconciliation
                # deferred to Phase 103 DOC-01 — see SUMMARY for the exact text
                # flagged as Phase-103-owned).
                0x34,
                "EEPROM - XICOR 8051-bus",
                (
                    "XICOR 8051-multiplexed bus; not implemented on RURP (FUT-01)",
                    "",
                    "",
                ),
            ),
        ]
        for pid, _ptype, desc_tuple in protocol_info_data:
            if pid == protocol_id:
                return {
                    "id_hex": f"0x{pid:02X}",
                    "type": self._PROTOCOL_DISPLAY_NAME.get(pid, _ptype),
                    "description_points": list(desc_tuple),
                }
        return None

    def _generate_pin_names_for_display(self, eprom_data: dict) -> Optional[List[str]]:  # noqa: UP006
        pin_count = eprom_data.get("pin-count")
        if pin_count not in self._generic_pin_names_map:
            logger.error(f"No generic layout available for {pin_count}-pin EPROM.")
            return None

        # Start with a copy of the generic names
        pin_names = list(self._generic_pin_names_map[pin_count])

        # Default OE pin position (example for 24-pin, adjust if needed for others)
        # This logic was a bit specific in the original, might need generalization
        # For 24-pin, OE is pin 20 (index 19). For 28-pin, OE/VPP is pin 22 (index 21). For 32-pin, OE is pin 24 (index 23).  # noqa: E501
        # Let's assume a generic OE position if not overridden by pin map.
        # This part of the original logic was a bit hardcoded and might need review for all chip types.  # noqa: E501
        # For simplicity, we'll rely on the pin_map to override.

        pin_map_id = eprom_data.get("pin-map")
        pin_map_details = (
            self.db.get_pin_map(pin_count, pin_map_id)
            if not pin_map_id is None  # noqa: E714
            else None
        )

        if pin_map_details:
            # Single-pin fields in pinouts.json are stored as single-element
            # lists (e.g. "vpp-pin": [22]).  Extract scalars before comparison.
            rw_pin = (
                self._first_pin(pin_map_details["rw-pin"])
                if "rw-pin" in pin_map_details
                else None
            )  # noqa: E501
            vpp_pin = (
                self._first_pin(pin_map_details["vpp-pin"])
                if "vpp-pin" in pin_map_details
                else None
            )  # noqa: E501
            oe_pin = (
                self._first_pin(pin_map_details["oe-pin"])
                if "oe-pin" in pin_map_details
                else None
            )  # noqa: E501
            if rw_pin is not None and rw_pin <= pin_count:
                pin_names[rw_pin - 1] = "R/W(WE)"
            if vpp_pin is not None and vpp_pin <= pin_count:
                pin_names[vpp_pin - 1] = "VPP"
                # If VPP is defined, and there's an OE pin, ensure OE is also labeled if it's different  # noqa: E501
                if oe_pin is not None and oe_pin != vpp_pin and oe_pin <= pin_count:
                    pin_names[oe_pin - 1] = "OE"
            elif oe_pin is not None and oe_pin <= pin_count:  # Only OE, no separate VPP
                pin_names[oe_pin - 1] = "OE"

            if "address-bus-pins" in pin_map_details:
                for i, pin_num in enumerate(pin_map_details["address-bus-pins"]):
                    if pin_num <= pin_count:
                        pin_names[pin_num - 1] = f"A{i}"
        else:
            logger.warning(
                f"No specific pin map '{pin_map_id}' found for {pin_count}-pin {eprom_data.get('name', 'EPROM')}. Displaying generic layout."  # noqa: E501
            )

        return pin_names

    def _build_dip_layout_data_from_names(
        self, pin_count: int, pin_names: list
    ) -> dict:
        """
        Creates the structured data for a DIP package layout using pin count and names.
        """
        half = pin_count // 2
        layout_data = {
            "title": f"{pin_count}-DIP package",
            "dent": " " * 8 + "-" * 5 + "v" + "-" * 5,
            "pin_pairs": [],
            "bottom": " " * 8 + "-" * 11,
        }
        for i in range(half):
            pin_left = pin_names[i]
            pin_right = pin_names[pin_count - 1 - i]
            layout_data["pin_pairs"].append(
                {
                    "left_name": pin_left,
                    "left_num": i + 1,
                    "right_num": pin_count - i,
                    "right_name": pin_right,
                }
            )
        return layout_data

    # Canonical protocol display names (Phase 102 D-01 single source). Both the
    # get_chip_type_string fallback path (proto_display, legacy user-override
    # entries lacking electrical.type) and _get_protocol_info_structured's
    # `type` field (the `firestarter info` "Protocol:" line) read from this ONE
    # dict — preventing the two vocabularies from re-diverging (the recurring
    # IN-01 class of bug). Values are ASCII-normalized copies of
    # firestarter/doc/PROTOCOLS.md column-2 canonical names (Phase 102 D-02:
    # em-dash "—" / en-dash "–" rendered as ASCII "-" for terminal/pipe/grep
    # safety — a documented punctuation deviation from the doc, recorded for
    # Phase 103's divergence log). 0x34 added / 0x11 dropped / 0x35+0x39 stay
    # excluded per Phase 102 D-04 (full coverage reconcile vs the 12-protocol
    # canonical DB set).
    _PROTOCOL_DISPLAY_NAME = {
        0x05: "Flash - 5V page-write (EEPROM-like)",
        0x06: "Flash - AMD/SST unlock-sequence NOR",
        0x07: "EPROM - 28-pin UV/EE, 13V VPP",
        0x08: "EPROM - 32-pin UV/EE, 13V VPP",
        0x0B: "EPROM - 24-pin legacy, 12-25V direct-VPE",
        0x0D: "EEPROM - 5V parallel, SDP + DQ7 poll",
        0x0E: "SRAM - 32-pin battery-backed NVRAM",
        0x10: "Flash - Intel 28F command-register, 12V VPP mandatory",
        0x27: "SRAM - 24-pin async, 5V",
        0x28: "SRAM/FRAM - 28-pin",
        0x29: "SRAM - 32-pin large battery-backed NVRAM, 512K-1M",
        0x34: "EEPROM - XICOR 8051-bus",
    }

    # Curated map from electrical.type DB ground truth to display label (D-01).
    # These are the distinct values present in chip_database.json.
    # Falls back to get_chip_type_string (protocol-based) when electrical_type
    # is absent or empty (legacy user-override entries without electrical.type).
    # Phase 84 fm-fram-full: "FRAM" added so FM1608 displays "FRAM" (not the
    # protocol-based fallback).  CAN_ERASE is unaffected (FRAM ∉ {EEPROM,
    # Flash/EEPROM} in database.py:605).
    _ELECTRICAL_TYPE_LABEL = {
        "EEPROM": "EEPROM",
        "Flash/EEPROM": "Flash/EEPROM",
        "FRAM": "FRAM",
        "SRAM": "SRAM",
        "UV-EPROM": "UV-EPROM",
    }

    def resolve_type_label(
        self,
        electrical_type: Optional[str],  # noqa: UP006
        protocol_id: Optional[int] = None,  # noqa: UP006
    ) -> str:
        """Return the user-facing chip-type display label (D-04 single source of truth).

        Looks up ``electrical_type`` in ``_ELECTRICAL_TYPE_LABEL`` (the curated
        ground-truth map from the DB ``electrical.type`` field).  When
        ``electrical_type`` is absent or empty — e.g. legacy user-override DB
        entries that predate the ``electrical.type`` field (D-05 fallback) — falls
        back to the protocol-based label via ``get_chip_type_string``.

        Both ``build_specifications`` (info view) and ``print_eprom_list_table``
        (list/search view) call this helper so the label is computed in exactly one
        place, preventing future info-vs-list divergence (IN-01 fix).

        Args:
            electrical_type: Raw ``electrical.type`` string from the DB record
                (e.g. ``"EEPROM"``, ``"UV-EPROM"``, ``"Flash/EEPROM"``, ``"SRAM"``).
                Pass ``None`` or ``""`` for legacy entries.
            protocol_id: The mapped ``protocol-id`` integer — used by the fallback
                for more precise disambiguation.

        Returns:
            A non-empty display label string (never raises).
        """
        etype = electrical_type or ""
        if etype in self._ELECTRICAL_TYPE_LABEL:
            return self._ELECTRICAL_TYPE_LABEL[etype]
        return self.get_chip_type_string(protocol_id)

    def build_specifications(  # noqa: UP006
        self,
        eprom_data: dict,
        electrical_type: Optional[str] = None,  # noqa: UP006
    ) -> Optional[Dict]:  # noqa: UP006
        """Build a dictionary of comprehensive technical specifications for the EPROM.

        This includes basic properties, pin names for layout, jumper settings,
        protocol information, and flag interpretations.

        ``eprom_data`` should be the fully mapped data from
        ``EpromDatabase.get_eprom(name)``.

        ``electrical_type`` is the raw ``electrical.type`` string from the DB record
        (e.g. ``"EEPROM"``, ``"UV-EPROM"``, ``"Flash/EEPROM"``, ``"SRAM"``).  When
        provided it is used as the sole source of the Type label (D-01) and the
        "Can be erased" derivation (D-02).  Pass ``None`` for legacy user-override
        entries that do not carry ``electrical.type``.
        """
        if not eprom_data:
            logger.error("No EPROM data provided to display.")
            return None

        # D-01/D-04: type label via single shared helper (resolve_type_label).
        # Falls back to protocol-based label when electrical_type is absent/empty.
        etype = electrical_type or ""
        chip_type_str = self.resolve_type_label(
            electrical_type,
            eprom_data.get("protocol-id"),
        )

        # D-05: verified_str marker removed entirely (no marker shown).
        # The presenter reads chip_data.get("verified_str", "") so omitting the
        # key is safe and produces no visible marker.
        output_data = {
            "name": eprom_data.get("name", "N/A"),
            "manufacturer": eprom_data.get("manufacturer", "N/A"),
            "pin_count": eprom_data.get("pin-count", "N/A"),
            "memory_size_hex": hex(eprom_data.get("memory-size", 0)),
            "type_str": chip_type_str,
            "vcc_str": f"{eprom_data.get('vcc', 'N/A')}v",
            "dip_layout": None,  # Will store the structured DIP layout data
            "jumpers": {},
            "protocol_info": None,
            "flags_info": None,
        }

        # D-02: "Can be erased" derived from electrical.type, NOT protocol_id.
        # EEPROM/Flash/EEPROM → electrically erasable; UV-EPROM → UV-only;
        # SRAM → omit row (volatile); absent/unknown → omit row (safe fallback).
        if etype in ("EEPROM", "Flash/EEPROM"):
            output_data["can_erase_str"] = "yes (electrically erasable)"
        elif etype == "UV-EPROM":
            output_data["can_erase_str"] = "no (UV erase only)"
        # SRAM and absent/unknown: no can_erase_str row

        # D-07-VPP: gate on vpp_mv > 0, not the always-zero flags & 0x08.
        # Coerce defensively: user-override entries may supply vpp_mv as a string.
        # Exclude SRAM and FRAM: volatile/no-program-VPP; vpp_mv=12000 is an
        # upstream infoic.xml decode artifact for SRAM/FRAM entries, not a real VPP.
        # Phase 84 fm-fram-full: FRAM added alongside SRAM (Pitfall-2 guard).
        try:
            _vpp_mv = int(eprom_data.get("vpp_mv", 0) or 0)
        except (TypeError, ValueError):
            _vpp_mv = 0
        if etype not in {"SRAM", "FRAM"} and _vpp_mv > 0:
            output_data["vpp_str"] = f"{eprom_data.get('vpp_volts', 'N/A')}v"

        # Chip ID: always render a row, but show "-" when the chip has no
        # real/readable ID — i.e. the key is absent, or it is a 0x00000000
        # placeholder from a chip_id_check=false entry (e.g. SRAM/FRAM such as
        # FM1608). A genuine chip ID is always non-zero.
        chip_id = eprom_data.get("chip-id")
        output_data["chip_id_hex"] = hex(chip_id) if chip_id else "-"

        # Pulse delay: omit the row when 0 / algorithm-controlled (no fixed
        # programming pulse to report, e.g. SRAM/FRAM).
        _pulse_delay = eprom_data.get("pulse-delay", 0) or 0
        if _pulse_delay:
            output_data["pulse_delay_us_str"] = f"{_pulse_delay}µS"

        # Generate DIP layout data
        pin_count = eprom_data.get("pin-count")
        if pin_count:
            display_pin_names = self._generate_pin_names_for_display(eprom_data)
            if display_pin_names:
                output_data["dip_layout"] = self._build_dip_layout_data_from_names(
                    pin_count, display_pin_names
                )

                # Determine jumper settings based on pin count and VPP presence
                jp1, jp2, jp3_rev01, jp4_rev2 = (
                    0,
                    0,
                    0,
                    0,
                )  # Default to N/A or first position
                has_vpp_pin_on_map = False
                pin_map_details = self.db.get_pin_map(
                    pin_count, eprom_data.get("pin-map")
                )
                if pin_map_details and "vpp-pin" in pin_map_details:
                    has_vpp_pin_on_map = True

                if pin_count == 24:
                    jp1 = 2  # VCC
                elif pin_count == 28:
                    jp1 = 1  # A13
                    jp2 = 2  # VCC
                    if has_vpp_pin_on_map:
                        jp3_rev01 = (
                            2  # 28pin (if VPP is used, implies 28pin mode for VPP)
                        )
                    jp4_rev2 = (
                        2 if has_vpp_pin_on_map else 1
                    )  # Closed if VPP, Open otherwise
                elif pin_count == 32:
                    jp1 = 1  # A13
                    jp2 = 1  # A17
                    if has_vpp_pin_on_map:
                        jp3_rev01 = 1  # 32pin
                    jp4_rev2 = 2 if has_vpp_pin_on_map else 1
                output_data["jumpers"].update(
                    self._get_rev1_jumper_settings_data(jp1, jp2, jp3_rev01)
                )
                output_data["jumpers"].update(
                    self._get_rev2_jumper_settings_data(jp4_rev2)
                )
                # output_data["jumpers"].update( self._get_rev2_2_jumper_settings_data(jp4_rev2))  # noqa: E501

        protocol_id = eprom_data.get("protocol-id")
        if protocol_id is not None:
            output_data["protocol_info"] = self._get_protocol_info_structured(
                protocol_id
            )

        flags = eprom_data.get("info-flags")
        if flags is not None:
            properties = self._interpret_flags(flags)
            output_data["flags_info"] = {
                "value_hex": f"0x{flags:08X}",
                "properties": properties,
            }
        return output_data


def main():  # Test function
    import json

    logging.basicConfig(
        level=logging.DEBUG, format="[%(levelname)s:%(name)s:%(lineno)d] %(message)s"
    )
    db_instance = EpromDatabase()
    spec_builder = EpromSpecBuilder(db_instance)

    chip_name = "AT28C256"  # A chip with a known pin map
    chip_name = "2732"  # A chip with a known pin map
    eprom_details = db_instance.get_eprom(chip_name)
    if not eprom_details:
        logger.error(f"EPROM {chip_name} not found in the database.")
        return 1

    logger.info(f"\n--- Generating structured data for {chip_name} ---")
    structured_data = spec_builder.build_specifications(eprom_details)
    if structured_data:
        # For testing, just log the raw structure. Printing is now EpromInfoProvider's job.  # noqa: E501
        logger.info(
            f"Generated data for {chip_name}: {json.dumps(structured_data, indent=2)}"
        )

    logger.info(f"\n--- Testing get_chip_type_string ---")  # noqa: F541
    logger.info(
        f"Protocol 0x08 (known): {spec_builder.get_chip_type_string(0x08)}"
    )
    logger.info(
        f"Protocol 0x99 (unknown): {spec_builder.get_chip_type_string(0x99)}"
    )

    logger.info(f"\n--- Testing flag interpretation (example flags) ---")  # noqa: F541
    example_flags = 0x000000B0  # Has ID, Elec. Erasable, Can be Elec. Erased
    interpreted = spec_builder._interpret_flags(example_flags)
    logger.info(f"Flags 0x{example_flags:08X}: {interpreted}")

    logger.info(f"\n--- Testing protocol info (example protocol ID) ---")  # noqa: F541
    protocol_data = spec_builder._get_protocol_info_structured(0x08)  # EPROM
    if protocol_data:
        logger.info(f"Protocol Data: {protocol_data}")


if __name__ == "__main__":
    main()
