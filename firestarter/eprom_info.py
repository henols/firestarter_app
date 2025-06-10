"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

EPROM Information Module
"""

import json
import re
import logging

from firestarter.database import EpromDatabase # Changed import
from firestarter.ic_layout import EpromSpecBuilder # Import renamed class

logger = logging.getLogger("EpromConsolePresenter")

class EpromConsolePresenter:
    """
    Manages the retrieval, structuring, and console presentation of EPROM information.
    It uses EpromSpecBuilder to get detailed EPROM specifications,
    constructs display-specific elements like DIP layouts, and formats
    the output for the command-line interface.
    """
    def __init__(self, db_instance: EpromDatabase):
        self.db = db_instance
        self.spec_builder = EpromSpecBuilder(db_instance) # Instantiate renamed class

    def _json_output_formatted(self, data: dict) -> str:
        """
        Formats a dictionary as a pretty-printed JSON string with special formatting for lists of numbers.
        """
        json_str = json.dumps(data, indent=4)
        # Compact lists of numbers (e.g., bus configurations)
        json_str = re.sub(
            # Regex to find a list that contains numbers, commas, and whitespace.
            # Group 1: Opening bracket and any leading whitespace/newline.
            # Group 2: The actual content (numbers, commas, whitespace).
            # Group 3: Closing bracket and any trailing whitespace/newline.
            r"(\[)[\s\n]*([\d,\s\n]+?)[\s\n]*(\])",  # Adjusted regex
            lambda match: match.group(1) + ', '.join(re.findall(r'\d+', match.group(2))) + match.group(3),
            json_str
        )
        return json_str

    def _clean_config_for_export(self, raw_config: dict) -> dict:
        """
        Cleans and structures raw EPROM config for JSON export.
        """
        cleaned = {}
        # Define expected keys and their defaults or how to fetch them
        key_map = {
            "name": "Unknown",
            "pin-count": 0,
            "can-erase": False,
            "has-chip-id": False,
            # "chip-id": "0x0", # Only if has-chip-id is True
            # "pin-map": "default", # Handled below with variant
            "protocol-id": "0x0",
            "memory-size": "0x0",
            "type": "unknown", # String type from raw JSON
            "voltages": {},
            "pulse-delay": "0",
            "flags": "0x00",
            "verified": False,
        }
        for key, default_val in key_map.items():
            cleaned[key] = raw_config.get(key, default_val)

        if cleaned.get("has-chip-id"):
            cleaned["chip-id"] = raw_config.get("chip-id", "0x0")
        else: # Remove chip-id if not present
            if "chip-id" in cleaned: del cleaned["chip-id"]

        cleaned["pin-map"] = raw_config.get("pin-map", raw_config.get("variant", "default"))

        # Clean up voltages sub-dictionary if it exists
        if "voltages" in cleaned and isinstance(cleaned["voltages"], dict):
            cleaned["voltages"].pop("vdd", None) # Remove 'vdd' if present
            # 'vcc' is kept as per original logic, 'vpp' is also kept
        return cleaned

    def _create_dip_layout_structure(self, pin_count: int, pin_names: list) -> dict:
        """
        Creates the structured data for a DIP package layout using pin count and names.
        This logic was formerly in ICLayoutGenerator.
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

    def get_all_eproms_data(self, verified_only: bool = False) -> list[dict]:
        """
        Retrieves a list of EPROM data from the database,
        optionally filtered by verification status.
        Returns a list of EPROM data dictionaries.
        """
        eproms_data = self.db.get_eproms(verified=verified_only)
        if not eproms_data:
            logger.info("No EPROMs found matching the criteria.")
            return []
        return eproms_data

    def search_eproms_by_name(self, query: str) -> list[dict]:
        """
        Searches for EPROMs in the database by name and returns their data.
        Returns a list of EPROM data dictionaries.
        """
        # search_eprom in database.py takes `include_unverified`
        results = self.db.search_eprom(query, include_unverified=True)
        if not results:
            logger.info(f"No EPROMs found matching '{query}'.")
            return []
        return results

    def prepare_detailed_eprom_data(
        self,
        eprom_name: str, # For logging and titles
        eprom_details_full: dict | None, # Pre-fetched from db.get_eprom(name, full=True)
        eprom_data_for_programmer: dict | None, # Pre-fetched from db.get_eprom(name, full=False)
        raw_config_data: dict | None, # Pre-fetched from db.get_eprom_config()
        manufacturer: str | None, # Pre-fetched from db.get_eprom_config()
        include_export_config: bool = False
    ) -> dict | None:
        """
        Prepares a comprehensive data structure for a specific EPROM,
        ready for presentation. It fetches raw specifications, constructs
        display elements like the DIP layout, and optionally includes
        export or programmer-specific configurations.
        """
        if not eprom_details_full:
            logger.error(f"EPROM '{eprom_name}' not found in the database.")
            return None

        # Get base specifications from EpromSpecBuilder
        # eprom_details_full is the fully mapped data expected by build_specifications
        eprom_specifications = self.spec_builder.build_specifications(eprom_details_full)
        if not eprom_specifications:
            logger.error(f"Could not generate layout data for {eprom_name}.")
            return None # Should not happen if eprom_details_full was valid

        # Start with data from EpromSpecBuilder
        combined_data = eprom_specifications

        # EpromInfoProvider now takes pin_count and layout_pin_names
        # from ICLayoutGenerator's output to build the dip_layout structure.
        pin_count_from_ic = combined_data.get("pin_count")
        # Retrieve layout_pin_names (it's intermediate for this provider's structuring step)
        layout_pin_names_from_ic = combined_data.get("layout_pin_names")

        if pin_count_from_ic and layout_pin_names_from_ic:
            combined_data["dip_layout"] = self._create_dip_layout_structure(pin_count_from_ic, layout_pin_names_from_ic)
        # We can optionally remove "layout_pin_names" if it's not needed in the final output structure
        # combined_data.pop("layout_pin_names", None)

        if eprom_data_for_programmer:
            combined_data["programmer_config_json_str"] = self._json_output_formatted(eprom_data_for_programmer)

        if include_export_config:
            # raw_config_data and manufacturer are now passed in
            if raw_config_data and manufacturer:
                cleaned_raw_config = self._clean_config_for_export(raw_config_data)
                
                export_eprom_data = {manufacturer: [cleaned_raw_config]}
                
                export_config_data = {
                    "eprom_config_title": f"{cleaned_raw_config['name']} EPROM config (for database_overrides.json):",
                    "eprom_config_json_str": self._json_output_formatted(export_eprom_data)
                }

                pin_map_id = cleaned_raw_config.get("pin-map")
                pin_count = cleaned_raw_config.get("pin-count")
                if pin_map_id and pin_count:
                    pin_map_details = self.db.get_pin_map(pin_count, pin_map_id)
                    if pin_map_details:
                        export_pin_map_data = {str(pin_count): {str(pin_map_id): pin_map_details}}
                        export_config_data["pin_map_config_title"] = f"{eprom_name} Pin Map (for pin-maps.json):"
                        export_config_data["pin_map_config_json_str"] = self._json_output_formatted(export_pin_map_data)
                    else:
                        logger.warning(f"Pin map '{pin_map_id}' for {pin_count}-pin {eprom_name} not found for export.")
                combined_data["export_config"] = export_config_data
            else:
                logger.error(f"Could not retrieve raw config for {eprom_name} for export.")
        
        return combined_data

    def present_eprom_details(self, chip_data: dict | None, show_export_config: bool = False):
        """
        Formats and prints the structured chip data to the console.
        This method now incorporates the logic from the former print_structured_chip_data.
        """
        if not chip_data:
            # prepare_detailed_eprom_data already logs an error if chip not found
            return

        pos = 20 # For alignment
        logger.info(f"{'Eprom Info': <{pos}}{chip_data.get('verified_str','')}")
        logger.info(f"{'Name:': <{pos}}{chip_data.get('name')}")
        logger.info(f"{'Manufacturer:': <{pos}}{chip_data.get('manufacturer')}")
        logger.info(f"{'Number of pins:': <{pos}}{chip_data.get('pin_count')}")
        logger.info(f"{'Memory size': <{pos}}{chip_data.get('memory_size_hex')}")
        logger.info(f"{'Type:': <{pos}}{chip_data.get('type_str')}")
        if "can_erase_str" in chip_data:
            logger.info(f"{'Can be erased:': <{pos}}{chip_data.get('can_erase_str')}")
        logger.info(f"{'VCC:': <{pos}}{chip_data.get('vcc_str')}")
        if "vpp_str" in chip_data:
            logger.info(f"{'VPP:': <{pos}}{chip_data.get('vpp_str')}")
        if "chip_id_hex" in chip_data:
            logger.info(f"{'Chip ID:': <{pos}}{chip_data.get('chip_id_hex')}")
        logger.info(f"{'Pulse delay:': <{pos}}{chip_data.get('pulse_delay_us_str')}")

        if chip_data.get("dip_layout"):
            layout = chip_data["dip_layout"]
            logger.info("")
            logger.info(f"       {layout.get('title')}")
            logger.info(layout.get("dent"))
            for pair in layout.get("pin_pairs", []):
                logger.info(f"  {pair['left_name']:<3} -| {pair['left_num']:2}     {pair['right_num']:2} |- {pair['right_name']:<6}")
            logger.info(layout.get("bottom"))

        for key, jumper_data in chip_data.get("jumpers", {}).items():
            logger.info("")
            logger.info(f"Jumper config (Rev {key}):")
            for jp, data in jumper_data.items():
                logger.info(f"  {jp.upper()}: {data['display']} ({data['config_text']}, {data['pin_text']} = {data['selected_label']})")
        
        if chip_data.get("protocol_info"):
            protocol = chip_data["protocol_info"]
            logger.info("")
            logger.info(f"Protocol: {protocol['type']} (ID: {protocol['id_hex']})")
            if "description_points" in protocol:
                for point in protocol["description_points"]:
                    logger.info(f"  - {point}")
        
        if chip_data.get("flags_info"):
            flags = chip_data["flags_info"]
            logger.info("")
            logger.info(f"Flags: {flags['value_hex']}")
            if flags["properties"]:
                for prop in flags["properties"]:
                    logger.info(f"  - {prop}")

        if "programmer_config_json_str" in chip_data: # This was added by get_eprom_details_structured
            logger.debug(f"\nProgrammer Config JSON:\n{chip_data['programmer_config_json_str']}")

        if show_export_config and "export_config" in chip_data:
            export_details = chip_data["export_config"]
            logger.info(f"\n{export_details['eprom_config_title']}\n{export_details['eprom_config_json_str']}")
            if "pin_map_config_title" in export_details:
                 logger.info(f"\n{export_details['pin_map_config_title']}\n{export_details['pin_map_config_json_str']}")

# --- Helper function for printing EPROM list (for testing/CLI) ---
def print_eprom_list_table(eproms_data: list, spec_builder: EpromSpecBuilder):
    """Prints a list of EPROM data in a table format. For CLI/testing."""
    if not eproms_data:
        logger.info("No EPROMs to display.")
        return

    divider = f"+{'':-<14}+{'':-<18}+{'':-<6}+{'':-<12}+{'':-<13}+{'':-<5}+" # Adjusted type column width
    logger.info(divider)
    logger.info(
        f"| {'Name': <13}| {'Manufacturer': <17}| {'Pins': <5}| {'Chip ID': <11}| {'Type': <12}| {'VPP': <4}|"
    )
    logger.info(divider)
    for ic in eproms_data:
        chip_id_str = f"0x{ic.get('chip-id', 0):X}" if ic.get('chip-id') else ""
        vpp_str = f"{ic.get('vpp', '-')}v" if ic.get("type") == 1 else "- " # EPROM type
        type_str = spec_builder.get_chip_type_string(ic.get("type", 0))
        logger.info(
            f"| {ic.get('name', ''): <13}| {ic.get('manufacturer', ''): <17}|{ic.get('pin-count', 0): >5} | {chip_id_str: <11}| {type_str: <12}| {vpp_str: >4}|"
        )
    logger.info(divider)

# Standalone test function
def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # db.init_db() # Old way
    db_instance = EpromDatabase() # New way
    presenter = EpromConsolePresenter(db_instance)
    
    logger.info("\n--- Searching for '27C' ---")
    search_results = presenter.search_eproms_by_name("27C")
    if search_results:
        print_eprom_list_table(search_results, presenter.spec_builder)
    else:
        logger.info("No results for '27C'.")
    
    # logger.info("\n--- Listing all (first few if many) ---")
    # all_eproms = presenter.get_all_eproms_data()
    # if all_eproms:
    #     print_eprom_list_table(all_eproms[:10], presenter.spec_builder) # Print first 10

    logger.info("\n--- Info for 27C256 (with export) ---")
    eprom_name_test = "27C256"
    details_full = db_instance.get_eprom(eprom_name_test, full=True)
    data_prog = db_instance.get_eprom(eprom_name_test, full=False)
    raw_conf, manuf = db_instance.get_eprom_config(eprom_name_test)

    if details_full and data_prog and raw_conf:
        structured_details = presenter.prepare_detailed_eprom_data(eprom_name_test, details_full, data_prog, raw_conf, manuf, include_export_config=True)
        if structured_details:
            presenter.present_eprom_details(structured_details, show_export_config=True)


if __name__ == "__main__":
    main()
