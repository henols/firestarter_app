"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson
Permission is hereby granted under MIT license.

This module handles the EPROM and pin map database for the Firestarter application.
It is responsible for:
- Loading EPROM definitions from JSON files (a base set and user overrides).
- Loading pin map configurations from JSON files (base and user overrides).
- Merging these data sources.
- Providing functions to query EPROM information, search for EPROMs,
  and retrieve specific configurations.
- Translating generic EPROM pinouts to the RURP (Relatively Universal ROM Programmer) hardware-specific bus configuration.

The database is initialized once when the EpromDatabase class is first instantiated.
Subsequent instantiations will return the same initialized instance.

Key data structures:
- `self.proms`: A dictionary storing EPROM data, keyed by manufacturer.
- `self.pin_maps`: A dictionary storing pin map configurations, keyed by pin count and then by pin map variant/name.

Module-level constants:
- `pin_conversions`: A hardcoded dictionary mapping standard EPROM pin numbers
  (for 24, 28, 32-pin DIP packages) to the RURP's internal address/control lines.
"""

import os
import json
import logging
from pathlib import Path
from firestarter.config import get_local_database, get_local_pin_maps
from firestarter.constants import *

PROTOCOL_MAP = {
    0x05: "FLASH_AMD_STD",
    0x06: "FLASH_AMD_ALT",
    0x07: "EPROM_STD",
    0x08: "EPROM_QUICK",
    0x0B: "EPROM_LEGACY",
    0x0D: "EEPROM_POLL",
    0x10: "FLASH_INTEL",
    0x28: "SRAM_STD",
}

# Algorithm integer (upstream protocol_id from infoic.xml) → firmware mem_type integer.
# Firmware dispatches on protocol first; mem_type is kept consistent for fallback paths.
_ALGO_MEM_TYPE = {
    0x05: 5,   # FLASH_AMD_STD     → TYPE_FLASH_TYPE_4
    0x06: 3,   # FLASH_AMD_ALT     → TYPE_FLASH_TYPE_3
    0x07: 1,   # EPROM_STD         → TYPE_EPROM
    0x08: 1,   # EPROM_QUICK       → TYPE_EPROM
    0x0B: 1,   # EPROM_LEGACY      → TYPE_EPROM
    0x0D: 1,   # EEPROM_POLL       → TYPE_EPROM (firmware dispatches on protocol prefix)
    0x0E: 4,   # SRAM_32PIN        → TYPE_SRAM
    0x10: 1,   # FLASH_INTEL       → TYPE_EPROM (firmware dispatches on protocol prefix)
    0x27: 4,   # SRAM_24PIN        → TYPE_SRAM
    0x28: 4,   # SRAM_STD          → TYPE_SRAM
    0x29: 4,   # SRAM_512K_1M      → TYPE_SRAM
    0x35: 5,   # FLASH_EEPROM_LIKE → TYPE_FLASH_TYPE_4
    0x39: 5,   # FLASH_INTEL_ALT   → TYPE_FLASH_TYPE_4 (no DB chips; future-proofed)
}

# Module-level constants
types = {"memory": 0x01, "flash": 0x03, "sram": 0x04}
ROM_CE = 0x100
ROM_OE = 0x101
# eprom pins to rurp conversion
pin_conversions = {
    # Maps EPROM pin number to RURP hardware line number
    24: {
        1: 7,
        2: 6,
        3: 5,
        4: 4,
        5: 3,
        6: 2,
        7: 1,
        8: 0,
        18: ROM_CE,
        19: 10,
        20: ROM_OE,
        21: 11,
        22: 9,
        23: 8,
        # Pin 24 (VCC) sits at DIP32 socket position 28 = bus line 13 on the RURP
        # shield. Bus line 13 must be driven HIGH to supply VCC to the DIP24 chip.
        24: 13,
    },
    28: {
        1: 15,
        2: 12,
        3: 7,
        4: 6,
        5: 5,
        6: 4,
        7: 3,
        8: 2,
        9: 1,
        10: 0,
        20: ROM_CE,
        21: 10,
        22: ROM_OE,
        23: 11,
        24: 9,
        25: 8,
        26: 13,
        27: 14,
    },
    32: {
        1: 21,
        2: 16,
        3: 15,
        4: 12,
        5: 7,
        6: 6,
        7: 5,
        8: 4,
        9: 3,
        10: 2,
        11: 1,
        12: 0,
        22: ROM_CE,
        23: 10,
        24: ROM_OE,
        26: 9,
        27: 8,
        25: 11,
        28: 13,
        29: 14,
        30: 20,
        31: 22,
    },
}

logger = logging.getLogger("Database")


def _read_config_file(filename: str) -> dict:
    """
    Reads a JSON configuration file from the 'data' subdirectory.
    Helper function for use within this module.
    """
    path = Path(os.path.dirname(__file__))
    filepath = path / "data" / filename
    try:
        with filepath.open("rt") as file:
            config = json.load(file)
        return config
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {filepath}")
        return {}
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON from file: {filepath}")
        return {}


class EpromDatabase:
    """
    Manages the EPROM and pin map database for the Firestarter application.
    It loads, merges, and provides access to EPROM definitions and pin map
    configurations. Implemented as a singleton to ensure a single, consistent
    database instance throughout the application.
    """

    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(EpromDatabase, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        if EpromDatabase._initialized:
            return

        self.proms = {}
        self.pin_maps = {}
        self._initialize_database_core()
        EpromDatabase._initialized = True
        logger.debug("EpromDatabase initialized.")

    def _initialize_database_core(self):
        """
        Loads and merges EPROM and pin map data.
        """
        # Load base EPROMs and overrides
        base_proms = _read_config_file(
            "chip_database.json"
        )  # Use only the new database
        override_proms = None  # //_read_config_file("database_overrides.json")
        self.proms = (
            self._merge_databases(base_proms, override_proms)
            if override_proms
            else base_proms
        )

        # Load and merge local user EPROM database
        local_db = get_local_database()
        if local_db:
            self.proms = self._merge_databases(
                self.proms, local_db
            )  # This might need adjustment for new format

        # Load base pin maps
        self.pin_maps = _read_config_file("pinouts.json")

        # Load and merge local user pin maps
        local_pin_maps = get_local_pin_maps()
        if local_pin_maps:
            self.pin_maps = self._merge_pin_maps(self.pin_maps, local_pin_maps)

    def _merge_databases(self, db: dict, manual_db: dict) -> dict:
        """
        Merges two EPROM database dictionaries. `manual_db` takes precedence.
        Modifies and returns the `db` dictionary.
        """
        for key, manual_items in manual_db.items():
            if key in db:
                # In new format, part_number is the key
                existing_names = {item["part_number"]: item for item in db[key]}
                for manual_item in manual_items:
                    if (
                        manual_item.get("name") in existing_names
                    ):  # Overrides use 'name'
                        # Replace existing item
                        existing_names[manual_item["name"]].update(
                            manual_item
                        )  # This is a shallow update
                    else:
                        # Add new item
                        db[key].append(
                            manual_item
                        )  # This might not merge correctly if format differs
            else:
                # Add entirely new key
                db[key] = manual_items
        return db

    def _merge_pin_maps(self, pin_maps_base: dict, manual_pin_map: dict) -> dict:
        """
        Merges two pin map configuration dictionaries. `manual_pin_map` takes precedence.
        Modifies and returns the `pin_maps_base` dictionary.
        """
        for key, sub_map in manual_pin_map.items():
            if key not in pin_maps_base:
                # Add new top-level key entirely if it doesn't exist
                pin_maps_base[key] = sub_map
            else:
                # Replace sub-objects in the existing key
                for sub_key, sub_value in sub_map.items():
                    pin_maps_base[key][
                        sub_key
                    ] = sub_value  # Replace existing or add new
        return pin_maps_base

    def get_pin_map(self, pins: int, pin_map_id: str):
        """
        Retrieves a specific pin map configuration.
        """
        if pin_map_id in self.pin_maps:
            return self.pin_maps[pin_map_id].get("pins")
        return None

    def get_bus_config(self, pins: int, variant: str):
        """
        Generates the RURP-specific bus configuration from a generic pin map.
        """
        # The variant is now the pinout key, e.g., "DIP28_27512"
        pin_map_data = self.get_pin_map(pins, variant)
        if not pin_map_data:
            return None

        map_config = {}
        bus = []
        if "address-bus-pins" in pin_map_data and pins in pin_conversions:
            for pin in pin_map_data["address-bus-pins"]:
                if pin in pin_conversions[pins]:
                    bus.append(pin_conversions[pins][pin])
                else:
                    logger.warning(
                        f"Pin {pin} not in pin_conversions for {pins}-pin EPROM during bus config."
                    )
            map_config["bus"] = bus
        else:
            logger.warning(
                f"Missing 'address-bus-pins' or pin_conversions for {pins}-pin EPROM."
            )
            return None  # Cannot form bus without address pins

        # Handle pins that can be a single value or a list
        for pin_func in ["rw-pin", "vpp-pin"]:
            if pin_func in pin_map_data:
                pin_val = pin_map_data[pin_func]
                # The value can be a list (e.g., [22]) or a single int.
                # We'll take the first element if it's a list.
                pin_to_check = pin_val[0] if isinstance(pin_val, list) else pin_val

                if pin_to_check in pin_conversions.get(pins, {}):
                    resolved = pin_conversions[pins][pin_to_check]
                    if pin_func == "vpp-pin" and resolved in (ROM_CE, ROM_OE):
                        continue  # No dedicated VPP pin; firmware defaults vpp_line=0xFF (VPE path)
                    map_config[pin_func] = resolved
                else:
                    logger.warning(
                        f"Pin function '{pin_func}' with pin number {pin_to_check} not in pin_conversions for {pins}-pin EPROM."
                    )

        if "static-high-pins" in pin_map_data and pins in pin_conversions:
            static_high = []
            for pin in pin_map_data["static-high-pins"]:
                if pin in pin_conversions[pins]:
                    static_high.append(pin_conversions[pins][pin])
                else:
                    logger.warning(
                        f"static-high-pin {pin} not in pin_conversions for {pins}-pin EPROM."
                    )
            if static_high:
                map_config["static-high"] = static_high

        return map_config

    def get_adapter_table(self, pin_count: int, pinout_key: str) -> list:
        """
        Returns [(pin_number, signal_name), ...] for every physical DIP pin 1..pin_count.
        Derived directly from pinouts.json. Returns [] if the pinout key is unknown.
        Used to display adapter wiring via `firestarter info --adapter`.
        """
        pin_map_data = self.get_pin_map(pin_count, pinout_key)
        if not pin_map_data:
            return []

        pin_signals = {}

        def _assign(pins_val, signal):
            for p in (pins_val if isinstance(pins_val, list) else [pins_val]):
                if p in pin_signals and signal not in pin_signals[p]:
                    pin_signals[p] = pin_signals[p] + "/" + signal
                else:
                    pin_signals[p] = signal

        _assign(pin_map_data.get("vcc-pin", []), "VCC")
        _assign(pin_map_data.get("gnd-pin", []), "GND")
        _assign(pin_map_data.get("ce-pin", []), "CE")
        _assign(pin_map_data.get("oe-pin", []), "OE")
        _assign(pin_map_data.get("pgm-pin", []), "PGM")
        _assign(pin_map_data.get("vpp-pin", []), "VPP")  # may append "/VPP" to OE if shared

        for i, p in enumerate(pin_map_data.get("address-bus-pins", [])):
            if p not in pin_signals:
                pin_signals[p] = f"A{i}"

        for i, p in enumerate(pin_map_data.get("data-bus-pins", [])):
            if p not in pin_signals:
                pin_signals[p] = f"D{i}"

        return [(p, pin_signals.get(p, "NC")) for p in range(1, pin_count + 1)]

    def _map_data(self, ic: dict, manufacturer: str) -> dict:
        """
        Transforms raw EPROM data from the JSON structure into a more processed
        and usable dictionary format for the application.

        This includes converting string hex values to integers, determining EPROM type,
        extracting voltages, and attaching the RURP-specific bus configuration. This now
        works with the new 'chip_database.json' format.
        """
        electrical = ic.get("electrical", {})
        programming = ic.get("programming", {})
        pin_count = electrical.get("pin_count")
        pinout_key = ic.get("pinout")

        vpp = 0
        vcc = 0
        vpp_str = electrical.get("vpp", "0").replace("V", "")
        vcc_str = electrical.get("vcc", "0").replace("V", "")
        try:
            vpp = float(vpp_str)
        except (ValueError, TypeError):
            None
            # logger.warning(f"Invalid VPP value for {ic.get('part_number')}: {vpp_str}")
        try:
            vcc = float(vcc_str)
        except (ValueError, TypeError):
            None
            # logger.warning(f"Invalid VCC value for {ic.get('part_number')}: {vcc_str}")
        vpp_mv = electrical.get("vpp_mv", 0)

        # Read algorithm integer directly — set by build_db.py from upstream protocol_id
        protocol_id = programming.get("algorithm", 0)

        # Derive mem_type from algorithm (D3). Fall back to electrical.type substring
        # only when algorithm is absent / 0 (legacy user-override DB entries).
        if protocol_id and protocol_id in _ALGO_MEM_TYPE:
            determined_type = _ALGO_MEM_TYPE[protocol_id]
        else:
            type_str = electrical.get("type", "")
            determined_type = 1  # Default to EPROM
            if "Flash" in type_str:
                determined_type = 2  # Generic Flash (legacy fallback only)
            elif "SRAM" in type_str:
                determined_type = 4

        # The new DB doesn't have the raw flags, so we infer what we can
        info_flags = 0
        if programming.get("chip_id_check"):
            info_flags |= 0x00000020  # Has Readable Chip ID
        if electrical.get("type") == "Flash/EEPROM":
            info_flags |= 0x00000010  # Can be electrically erased

        data = {
            "name": ic.get("part_number"),
            "manufacturer": manufacturer,
            "memory-size": electrical.get("size_bytes", 0),
            "type": determined_type,
            "pin-count": pin_count,
            "vpp_volts": vpp,
            "vpp_mv": vpp_mv,
            "vcc": vcc,
            "pulse-delay": 0,  # Not directly available in new format, may need parsing from string
            "verified": bool(ic.get("verified", False)),
            "info-flags": info_flags,
            "flags": 0,
            "protocol-id": protocol_id,
            "pin-map": pinout_key,
        }

        chip_id_val = programming.get("chip_id_value")
        if chip_id_val:
            data["chip-id"] = int(chip_id_val, 16)

        if pin_count and pinout_key:
            bus_config = self.get_bus_config(pin_count, pinout_key)
            if bus_config:
                data["bus-config"] = bus_config
        return data

    def get_eproms(self, verified=None) -> list:
        """
        Retrieves a list of all EPROMs from the database.

        Args:
            verified (bool, optional): If True, only returns EPROMs marked as "verified".
                                    If False or None, returns all EPROMs. Defaults to None.

        Returns:
            list: A list of dictionaries, where each dictionary represents an EPROM's data.
        """
        selected_proms = []
        for manufacturer, ics in self.proms.items():
            for ic_config in ics:
                is_verified_in_db = bool(ic_config.get("verified", False))
                if (
                    verified is None
                    or (verified and is_verified_in_db)
                    or (not verified)
                ):  # Corrected logic for verified filter
                    selected_proms.append(self._map_data(ic_config, manufacturer))
        return selected_proms

    def get_eprom_config(self, chip_name: str):
        """
        Retrieves the raw configuration data for a specific EPROM by its name.
            Returns (config_dict, manufacturer_str) or (None, None).
        """
        for manufacturer, ics in self.proms.items():
            for ic_config in ics:
                if chip_name.lower() == ic_config.get("part_number", "").lower():
                    return ic_config, manufacturer
        return None, None

    def get_eprom(self, chip_name: str):
        """
        Retrieves processed data for a specific EPROM.
        This version always returns the "full" data, which includes detailed information
        but removes the simple 'flags' key (used for programmer communication).
        """
        config, manufacturer = self.get_eprom_config(chip_name)
        if config:
            data = self._map_data(config, manufacturer)
            # if not data:
            #     # Prune fields for concise output
            #     keys_to_pop = [
            #         "manufacturer",
            #         "verified",
            #         "pin-map",
            #         "name",
            #         "protocol-id",
            #         "vcc",
            #         "info-flags",
            #     ]
            #     for key in keys_to_pop:
            #         if key in data:
            #             data.pop(key)
            # else:
            #     if "flags" in data:
            #         data.pop("flags")
            return data
        return None

    def convert_to_programmer(self, full_eprom_data: dict) -> dict:
        """
        Converts the full EPROM data structure (from get_eprom)
        into the concise format suitable for sending to the programmer.
        """
        if not full_eprom_data:
            return {}

        # Use vpp_mv directly when available (integer millivolts from build_db.py)
        vpp_mv = full_eprom_data.get("vpp_mv") or int(full_eprom_data.get("vpp_volts", 0) * 1000)

        # Keys to keep from the full data
        programmer_data = {
            "memory-size": full_eprom_data.get("memory-size", 0),
            "type": full_eprom_data.get("type", 0),
            "algorithm": full_eprom_data.get("protocol-id", 0),
            "pin-count": full_eprom_data.get("pin-count", 0),
            "vpp_mv": vpp_mv,
            "pulse-delay": full_eprom_data.get("pulse-delay", 0),
            # 'chip-id' is optional
        }

        if "chip-id" in full_eprom_data:
            programmer_data["chip-id"] = full_eprom_data["chip-id"]

        if "bus-config" in full_eprom_data:
            programmer_data["bus-config"] = full_eprom_data["bus-config"]

        # Calculate the simple 'flags' key for the programmer
        # Inferring from mapped 'type': Type 2 (Flash 2) and Type 3 (Flash 3) are electrically erasable.
        # New requirement: FLAG_CAN_ERASE should be set if info-flags has the 0x00000010 bit.
        simple_flags = 0
        if (
            full_eprom_data.get("info-flags", 0) & 0x00000010
        ):  # Check for "Can be electrically erased" bit
            simple_flags |= FLAG_CAN_ERASE  # FLAG_CAN_ERASE is 0x02
        programmer_data["flags"] = simple_flags

        return programmer_data

    def search_eprom(
        self, chip_name_query: str, include_unverified: bool = True
    ) -> list:
        """
        Searches for EPROMs where `chip_name_query` is part of the EPROM's name.
        `include_unverified`: If True, includes all text matches.
                              If False, includes only verified text matches.
        """
        selected_proms = []
        for manufacturer, ics in self.proms.items():
            for ic_config in ics:
                if chip_name_query.lower() in ic_config.get("part_number", "").lower():
                    is_verified_in_db = bool(ic_config.get("verified", False))
                    if (
                        include_unverified or is_verified_in_db
                    ):  # 'verified' is not in new DB, needs adding or logic change
                        selected_proms.append(self._map_data(ic_config, manufacturer))
        return selected_proms

    def search_chip_id(self, chip_id_val: int) -> list:
        """
        Searches for EPROMs that match a given chip ID.
        Returns a list of raw EPROM data dictionaries with 'manufacturer' added.
        """
        selected_proms = []
        for manufacturer, ics in self.proms.items():
            for ic_config in ics:
                programming = ic_config.get("programming", {})
                if programming.get("chip_id_check") and programming.get(
                    "chip_id_value"
                ):
                    try:
                        if int(programming.get("chip_id_value"), 16) == chip_id_val:
                            # Return a copy of the raw config with manufacturer added
                            ic_copy = ic_config.copy()
                            ic_copy["manufacturer"] = manufacturer
                            selected_proms.append(ic_copy)
                    except ValueError:
                        logger.warning(
                            f"Invalid chip-id format for {ic_config.get('part_number', 'Unknown EPROM')}: {programming.get('chip_id_value')}"
                        )
        return selected_proms


def main():  # Test function
    """
    Main function for standalone testing or demonstration of the database module.
    """
    logging.basicConfig(
        level=logging.DEBUG, format="[%(levelname)s:%(name)s:%(lineno)d] %(message)s"
    )
    db = EpromDatabase()  # Initializes the database

    chip_name = "W27C512"
    print(f"\n--- Getting EPROM config for: {chip_name} ---")
    config, manufacturer = db.get_eprom_config(chip_name)
    if config is None:
        print(f"Prom {chip_name} not found")
    else:
        print(f"Found {config.get('name')} from {manufacturer}")
        print(json.dumps(config, indent=2))

    print(f"\n--- Getting full EPROM data for: {chip_name} ---")
    full_data = db.get_eprom(chip_name)
    if full_data:
        print(json.dumps(full_data, indent=2))
    else:
        print(f"EPROM {chip_name} not found.")

    print(f"\n--- Getting concise EPROM data for: {chip_name} ---")
    full_data_for_conversion = db.get_eprom(chip_name)
    concise_data = None
    if full_data_for_conversion:
        concise_data = db.convert_to_programmer(full_data_for_conversion)
    if concise_data:
        print(json.dumps(concise_data, indent=2))
    else:
        print(f"EPROM {chip_name} not found.")

    # print("\n--- Listing all EPROMs (first 5) ---")
    # all_eproms = db.get_eproms()
    # for eprom_data in all_eproms[:5]:
    #     print(f"  - {eprom_data['name']} by {eprom_data['manufacturer']}")

    # print("\n--- Searching for '27C256' ---")
    # search_results = db.search_eprom("27C256")
    # for res in search_results:
    #     print(f"  - {res['name']} by {res['manufacturer']}")

    # Example: Test get_pin_map and get_bus_config if config was found
    if config:
        variant = None
        pin_count = config.get("pin-count")
        variant = config.get("pin-map", config.get("variant"))
        if pin_count and not variant is None:
            print("\n--- Pin Map ---")
            pin_map_details = db.get_pin_map(pin_count, variant)
            print(json.dumps(pin_map_details, indent=2))
            print("\n--- Bus Config ---")
            bus_config_details = db.get_bus_config(pin_count, variant)
            print(json.dumps(bus_config_details, indent=2))


if __name__ == "__main__":
    main()
