"""
Project Name: Firestarter
Copyright (c) 2025 Henrik Olsson

Permission is hereby granted under MIT license.
IC Layout Generation Module
"""

import logging
from typing import Optional, List, Dict
from firestarter.database import EpromDatabase # Changed import

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
        24: ["A7", "A6", "A5", "A4", "A3", "A2", "A1", "A0", "D0", "D1", "D2", "GND",
             "D3", "D4", "D5", "D6", "D7", "CE", "NC", "OE", "NC", "NC", "NC", "VCC"],
        28: ["NC", "NC", "A7", "A6", "A5", "A4", "A3", "A2", "A1", "A0", "D0", "D1", "D2", "GND",
             "D3", "D4", "D5", "D6", "D7", "CE", "NC", "OE/VPP", "NC", "NC", "NC", "NC", "NC", "VCC"],
        32: ["NC", "NC", "NC", "NC", "A7", "A6", "A5", "A4", "A3", "A2", "A1", "A0", "D0", "D1", "D2", "GND",
             "D3", "D4", "D5", "D6", "D7", "CE", "NC", "OE", "NC", "NC", "NC", "NC", "NC", "NC", "R/W(WE)", "VCC"],
    }

    def __init__(self, db_instance: EpromDatabase):
        self.db = db_instance

    def _select_jumper_label(self, jp_setting: int, label1: str, label2: str) -> str:
        if jp_setting == 1: return label1
        if jp_setting == 2: return label2
        return "NA"

    def _get_rev1_jumper_settings_data(self, jp1: int, jp2: int, jp3: int) -> dict:
        """Generates structured data for Rev 0.1 & 1.0 jumper settings."""
        jumper_display = [" ● ● ● ", " ●(● ●)", "(● ●)● "] # 0: N/A, 1: Pos1, 2: Pos2
        jp1_label = self._select_jumper_label(jp1, "A13", "VCC")
        jp2_label = self._select_jumper_label(jp2, "A17", "VCC")
        jp3_label = self._select_jumper_label(jp3, "32pin", "28pin")
        return  {"0.1 & 1.0":
         {   "jp1": {"config_text": "5V", "display": jumper_display[jp1], "pin_text": "A13", "selected_label": jp1_label},
            "jp2": {"config_text": "5V", "display": jumper_display[jp2], "pin_text": "A17", "selected_label": jp2_label},
            "jp3": {"config_text": "28pin", "display": jumper_display[jp3], "pin_text": "32pin", "selected_label": jp3_label},
         }}

    def _get_rev2_jumper_settings_data(self, jp4: int) -> dict:
        """Generates structured data for Rev 2.0 & 2.1 jumper settings."""
        jp4_label = self._select_jumper_label(jp4, "Open", "Closed") # Assuming 1=Open, 2=Closed
        jumper_display = [" N/A   ", " ● ●   ", "(● ●)  "] # 0: N/A, 1: Open, 2: Closed
        return  {"2.0 & 2.1":{
            "jp4": {"config_text": "28pin", "display": jumper_display[jp4], "pin_text": "32pin", "selected_label": jp4_label},
        }}

    def _get_rev2_2_jumper_settings_data(self, jp5: int) -> dict:
        """Generates structured data for Rev 2.2 jumper settings."""
        jp5_label = self._select_jumper_label(jp5, "Open", "Closed") # Assuming 1=Open, 2=Closed
        jumper_display = [" N/A   ", " ● ●   ", "(● ●)  "] # 0: N/A, 1: Open, 2: Closed
        return {"2.2":{
            "jp5": {"config_text": "28pin", "display": jumper_display[jp5], "pin_text": "32pin", "selected_label": jp5_label},
        }}

    def get_chip_type_string(self, chip_type_int: int) -> str:
        type_map = {1: "EPROM", 2: "Flash type 2", 3: "Flash type 3", 4: "SRAM"}
        return type_map.get(chip_type_int, f"Unknown ({chip_type_int})")

    def _interpret_flags(self, flags: int) -> list[str]:
        """
        Interpret the flags value and return a list of properties.
        """
        properties = []
        flag_definitions = [
            (0x00000010, "Can be electrically erased"),
            (0x00000020, "Has Readable Chip ID"),
            (0x00000080, "Is Electrically Erasable or Writable (EEPROM/Flash/SRAM)"),
            (0x00000200, "Supports Boot Block Features"),
            (0x00001000, "Data Memory Addressing"),
            (0x00002000, "Data Bus Width"), # This seems more like a category than a boolean flag
            (0x00004000, "Software Data Protection (SDP) before Erase/Program"),
            (0x00008000, "Software Data Protection (SDP) after Erase/Program"),
            # (0x00300000, "Supported Programming Modes"), # This is a multi-bit field
            # (0x03000000, "Data Organization"), # This is a multi-bit field
        ]
        for bitmask, description in flag_definitions:
            if flags & bitmask:
                properties.append(description)
        return properties

    def _get_protocol_info_structured(self, protocol_id: int) -> dict | None:
        """Returns structured protocol information."""
        protocol_info_data = [
            (0x05, "EEPROM/Flash", ("EEPROM/Flash with write enable sequence, software commands", "Requires specific software commands for programming/erasure", "Operates at standard voltage levels")),
            (0x06, "Flash Memory", ("Standard Flash memory programming protocol", "Uses command sequences for programming/erasure", "Operates at standard voltage levels")),
            (0x07, "EEPROM", ("EEPROM programming protocol for 28-pin devices", "Byte-wise programming, no high voltage required", "May include software data protection")),
            (0x08, "EPROM", ("EPROM programming protocol requiring high programming voltage", "Uses VPP (typically 12.5V or higher) for programming", "Follows EPROM programming algorithms")),
            (0x0B, "EPROM/EEPROM", ("Programming protocol for older 24-pin devices", "May require VPP for programming", "Smaller capacity devices")),
            (0x0D, "EEPROM", ("Programming protocol for large EEPROMs", "Supports byte-wise programming", "May require specific write sequences")),
            (0x0E, "SRAM", ("SRAM with battery backup or additional features", "Standard SRAM access protocols", "32-pin devices")),
            (0x10, "Flash Memory", ("Intel-compatible Flash memory programming protocol", "Requires specific command sequences", "Operates at standard voltage levels")),
            (0x11, "Flash Memory", ("Firmware Hub (FWH) programming protocol", "Used in BIOS chips", "Requires specific interfaces and commands")),
            (0x27, "SRAM", ("Standard SRAM access protocol for 24-pin devices", "2Kb SRAM devices", "Simple read/write operations")),
            (0x28, "SRAM", ("Standard SRAM access protocol for 28-pin devices", "8Kb SRAM devices", "Simple read/write operations")),
            (0x29, "SRAM", ("Standard SRAM access protocol for 32-pin devices", "512Kb to 1Mb SRAM devices", "Simple read/write operations")),
            (0x2A, "NVRAM", ("Non-volatile SRAM with built-in battery", "Requires special handling for battery-backed operation", "32-pin devices")),
            (0x2C, "NVRAM", ("Non-volatile SRAM (Timekeeping RAM)", "May include real-time clock features", "Standard SRAM access protocol")),
            (0x2E, "NVRAM", ("High-capacity non-volatile SRAM", "512Kb and larger sizes", "Requires specific protocols for access")),
            (0x35, "Flash Memory", ("Flash memory with EEPROM-like interface", "Requires specific write sequences", "May include software data protection")),
            (0x39, "Flash Memory", ("Advanced Flash memory programming protocol", "Uses command sequences similar to Intel algorithms", "Operates at standard voltage levels")),
            (0x3C, "Flash Memory", ("Common Flash memory protocol for 4Mb devices", "Uses standard command sequences for programming", "May operate at lower voltages (3.3V)")),
        ]
        for pid, ptype, desc_tuple in protocol_info_data:
            if pid == protocol_id:
                return {
                    "id_hex": f"0x{pid:02X}",
                    "type": ptype,
                    "description_points": list(desc_tuple)
                }
        return None

    def _generate_pin_names_for_display(self, eprom_data: dict) -> Optional[List[str]]:
        pin_count = eprom_data.get("pin-count")
        if pin_count not in self._generic_pin_names_map:
            logger.error(f"No generic layout available for {pin_count}-pin EPROM.")
            return None
        
        # Start with a copy of the generic names
        pin_names = list(self._generic_pin_names_map[pin_count])
        
        # Default OE pin position (example for 24-pin, adjust if needed for others)
        # This logic was a bit specific in the original, might need generalization
        # For 24-pin, OE is pin 20 (index 19). For 28-pin, OE/VPP is pin 22 (index 21). For 32-pin, OE is pin 24 (index 23).
        # Let's assume a generic OE position if not overridden by pin map.
        # This part of the original logic was a bit hardcoded and might need review for all chip types.
        # For simplicity, we'll rely on the pin_map to override.

        pin_map_id = eprom_data.get("pin-map")
        pin_map_details = self.db.get_pin_map(pin_count, pin_map_id) if not pin_map_id is None else None

        if pin_map_details:
            if "rw-pin" in pin_map_details and pin_map_details["rw-pin"] <= pin_count:
                pin_names[pin_map_details["rw-pin"] - 1] = "R/W(WE)"
            if "vpp-pin" in pin_map_details and pin_map_details["vpp-pin"] <= pin_count:
                pin_names[pin_map_details["vpp-pin"] - 1] = "VPP"
                # If VPP is defined, and there's an OE pin, ensure OE is also labeled if it's different
                if "oe-pin" in pin_map_details and pin_map_details["oe-pin"] != pin_map_details["vpp-pin"] and pin_map_details["oe-pin"] <= pin_count:
                     pin_names[pin_map_details["oe-pin"] - 1] = "OE"
            elif "oe-pin" in pin_map_details and pin_map_details["oe-pin"] <= pin_count: # Only OE, no separate VPP
                pin_names[pin_map_details["oe-pin"] - 1] = "OE"

            if "address-bus-pins" in pin_map_details:
                for i, pin_num in enumerate(pin_map_details["address-bus-pins"]):
                    if pin_num <= pin_count:
                        pin_names[pin_num - 1] = f"A{i}"
        else:
            logger.warning(f"No specific pin map '{pin_map_id}' found for {pin_count}-pin {eprom_data.get('name', 'EPROM')}. Displaying generic layout.")
        
        return pin_names

    def _build_dip_layout_data_from_names(self, pin_count: int, pin_names: list) -> dict:
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
            layout_data["pin_pairs"].append({
                "left_name": pin_left, "left_num": i + 1,
                "right_num": pin_count - i, "right_name": pin_right,
            })
        return layout_data

    def build_specifications(self, eprom_data: dict) -> Optional[Dict]:
        """
        Builds a dictionary containing comprehensive technical specifications
        for the given EPROM data. This includes basic properties, pin names for layout,
        jumper settings, protocol information, and flag interpretations.
        `eprom_data` should be the fully mapped data from `EpromDatabase.get_eprom(..., full=True)`.
        """
        if not eprom_data:
            logger.error("No EPROM data provided to display.")
            return None

        output_data = {
            "name": eprom_data.get('name', 'N/A'),
            "manufacturer": eprom_data.get('manufacturer', 'N/A'),
            "pin_count": eprom_data.get('pin-count', 'N/A'),
            "memory_size_hex": hex(eprom_data.get('memory-size', 0)),
            "type_str": self.get_chip_type_string(eprom_data.get('type', 0)),
            "vcc_str": f"{eprom_data.get('vcc', 'N/A')}v",
            "pulse_delay_us_str": f"{eprom_data.get('pulse-delay', 'N/A')}µS",
            "verified_str": "" if eprom_data.get("verified", False) else "-- NOT VERIFIED --",
            "dip_layout": None, # Will store the structured DIP layout data
            "jumpers":{},
            "protocol_info": None,
            "flags_info": None,
        }

        chip_type_str = self.get_chip_type_string(eprom_data.get('type', 0))
        output_data["type_str"] = chip_type_str

        if eprom_data.get("type") == 1: # EPROM
            output_data["can_erase_str"] = "true" if eprom_data.get('flags', 0) & 0x00000010 else "false"

        if eprom_data.get("flags", 0) & 0x00000008: # Assumes this flag means VPP is relevant
            output_data["vpp_str"] = f"{eprom_data.get('vpp', 'N/A')}v"

        if "chip-id" in eprom_data:
            output_data["chip_id_hex"] = hex(eprom_data.get('chip-id', 0))

        # Generate DIP layout data
        pin_count = eprom_data.get("pin-count")
        if pin_count:
            display_pin_names = self._generate_pin_names_for_display(eprom_data)
            if display_pin_names:
                output_data["dip_layout"] = self._build_dip_layout_data_from_names(pin_count, display_pin_names)

                # Determine jumper settings based on pin count and VPP presence
                jp1, jp2, jp3_rev01, jp4_rev2 = 0, 0, 0, 0 # Default to N/A or first position
                has_vpp_pin_on_map = False
                pin_map_details = self.db.get_pin_map(pin_count, eprom_data.get("pin-map"))
                if pin_map_details and "vpp-pin" in pin_map_details:
                    has_vpp_pin_on_map = True
                
                if pin_count == 24:
                    jp1 = 2 # VCC
                elif pin_count == 28:
                    jp1 = 1 # A13
                    jp2 = 2 # VCC
                    if has_vpp_pin_on_map: jp3_rev01 = 2 # 28pin (if VPP is used, implies 28pin mode for VPP)
                    jp4_rev2 = 2 if has_vpp_pin_on_map else 1 # Closed if VPP, Open otherwise
                elif pin_count == 32:
                    jp1 = 1 # A13
                    jp2 = 1 # A17
                    if has_vpp_pin_on_map: jp3_rev01 = 1 # 32pin
                    jp4_rev2 = 2 if has_vpp_pin_on_map else 1
                output_data["jumpers"].update( self._get_rev1_jumper_settings_data(jp1, jp2, jp3_rev01))
                output_data["jumpers"].update( self._get_rev2_jumper_settings_data(jp4_rev2))
                # output_data["jumpers"].update( self._get_rev2_2_jumper_settings_data(jp4_rev2))

        protocol_id = eprom_data.get("protocol-id")
        if protocol_id is not None:
            output_data["protocol_info"] = self._get_protocol_info_structured(protocol_id)

        flags = eprom_data.get("info-flags")
        if flags is not None:
            properties = self._interpret_flags(flags)
            output_data["flags_info"] = {
                "value_hex": f"0x{flags:08X}",
                "properties": properties
            }
        return output_data


def main(): # Test function
    import json
    logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s:%(name)s:%(lineno)d] %(message)s")
    db_instance = EpromDatabase()
    spec_builder = EpromSpecBuilder(db_instance)

    chip_name = "AT28C256" # A chip with a known pin map
    chip_name = "2732" # A chip with a known pin map
    eprom_details = db_instance.get_eprom(chip_name)
    if not eprom_details:
        logger.error(f"EPROM {chip_name} not found in the database.")
        return 1
    
    logger.info(f"\n--- Generating structured data for {chip_name} ---")
    structured_data = spec_builder.build_specifications(eprom_details)
    if structured_data:
        # For testing, just log the raw structure. Printing is now EpromInfoProvider's job.
        logger.info(f"Generated data for {chip_name}: {json.dumps(structured_data, indent=2)}")

    logger.info(f"\n--- Testing get_chip_type_string ---")
    logger.info(f"Type 1: {spec_builder.get_chip_type_string(1)}")
    logger.info(f"Type 5: {spec_builder.get_chip_type_string(5)}")

    logger.info(f"\n--- Testing flag interpretation (example flags) ---")
    example_flags = 0x000000B0 # Has ID, Elec. Erasable, Can be Elec. Erased
    interpreted = spec_builder._interpret_flags(example_flags)
    logger.info(f"Flags 0x{example_flags:08X}: {interpreted}")

    logger.info(f"\n--- Testing protocol info (example protocol ID) ---")
    protocol_data = spec_builder._get_protocol_info_structured(0x08) # EPROM
    if protocol_data:
        logger.info(f"Protocol Data: {protocol_data}")
    

if __name__ == "__main__":
    main()
