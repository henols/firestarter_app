"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson
Permission is hereby granted under MIT license.

EPROM and pin-map database: loads the base JSON plus user overrides, merges
them, answers queries, and translates a generic EPROM pinout into the RURP's
hardware-specific bus configuration.

`EpromDatabase` is a singleton -- it initialises once and later instantiations
return the same instance.
"""  # noqa: E501

import json
import logging
import os
from pathlib import Path
from typing import Any

from firestarter.config import get_local_database, get_local_pin_maps
from firestarter.constants import FLAG_CAN_ERASE

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

# Module-level constants
types = {"memory": 0x01, "flash": 0x03, "sram": 0x04}
ROM_CE = 0x100
ROM_OE = 0x101
# pin_conversions: RURP board-wiring layer.
# Maps DIP socket pin number → RURP bus line number (hardware-specific).
# This is DISTINCT from pinouts.json (loaded as self.pin_maps), which maps
# chip pin function → DIP socket pin number (chip-specific).
# They COMPOSE in get_bus_config(): pinouts.json gives function→socket-pin,
# pin_conversions gives socket-pin→bus-line. There is ONE source of truth
# per layer, not duplication.
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


def format_mv(mv: int) -> str:
    """Render a millivolt integer as the project's one human-facing voltage string.

    This is the single definition of the millivolt-to-human render used by every
    display call site (`ic_layout.py`'s `vcc_str`/`vpp_str` and `eprom_info.py`'s
    `vpp_str`). The one-decimal lowercase-`v` format (`f"{mv / 1000:.1f}v"`) is
    byte-identical to the pre-Phase-148 string-schema output — it takes an
    `int` because the numeric convention is enforced upstream in `_map_data`
    rather than tolerated here.
    """
    return f"{mv / 1000:.1f}v"


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
    configurations. Each call to EpromDatabase() returns a fresh instance;
    pass skip_local_override=True to skip loading ~/.firestarter/database.json
    and ~/.firestarter/pinmaps.json (useful in tests for deterministic results).
    """

    def __init__(self, skip_local_override: bool = False):
        self.proms: dict[str, Any] = {}
        self.pin_maps: dict[str, Any] = {}
        self._initialize_database_core(skip_local_override=skip_local_override)
        logger.debug("EpromDatabase initialized.")

    def _initialize_database_core(self, skip_local_override: bool = False):
        """
        Loads and merges EPROM and pin map data.
        When skip_local_override=True, only the packaged chip_database.json and
        pinouts.json are loaded; ~/.firestarter user overrides are skipped.
        """
        self.proms = _read_config_file("chip_database.json")

        if not skip_local_override:
            # Load and merge local user EPROM database (~/.firestarter/database.json).
            # Per-user corrections live there — the shipped chip_database.json is
            # generated from upstream infoic.xml via tools/build_db.py and should
            # not be hand-edited.
            local_db = get_local_database()
            if local_db:
                self.proms = self._merge_databases(self.proms, local_db)

        # Load base pin maps
        self.pin_maps = _read_config_file("pinouts.json")

        if not skip_local_override:
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
        """  # noqa: E501
        for key, sub_map in manual_pin_map.items():
            if key not in pin_maps_base:
                # Add new top-level key entirely if it doesn't exist
                pin_maps_base[key] = sub_map
            else:
                # Replace sub-objects in the existing key
                for sub_key, sub_value in sub_map.items():
                    pin_maps_base[key][sub_key] = (
                        sub_value  # Replace existing or add new
                    )
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
                        f"Pin {pin} not in pin_conversions for {pins}-pin EPROM during bus config."  # noqa: E501
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
                        continue  # No dedicated VPP pin; firmware defaults vpp_line=0xFF (VPE path)  # noqa: E501
                    map_config[pin_func] = resolved
                else:
                    logger.warning(
                        f"Pin function '{pin_func}' with pin number {pin_to_check} not in pin_conversions for {pins}-pin EPROM."  # noqa: E501
                    )

        if "static-high-pins" in pin_map_data and pins in pin_conversions:
            static_high = []
            for pin in pin_map_data["static-high-pins"]:
                if pin in pin_conversions[pins]:
                    static_high.append(pin_conversions[pins][pin])
                else:
                    logger.warning(
                        f"static-high-pin {pin} not in pin_conversions for {pins}-pin EPROM."  # noqa: E501
                    )
            if static_high:
                map_config["static-high"] = static_high

        return map_config

    def get_adapter_table(self, pin_count: int, pinout_key: str) -> list:
        """
        Returns [(pin_number, signal_name), ...] for every physical DIP pin 1..pin_count.
        Derived directly from pinouts.json. Returns [] if the pinout key is unknown.
        Used to display adapter wiring via `firestarter info --adapter`.
        """  # noqa: E501
        pin_map_data = self.get_pin_map(pin_count, pinout_key)
        if not pin_map_data:
            return []

        pin_signals: dict[int, str] = {}

        def _assign(pins_val, signal):
            for p in pins_val if isinstance(pins_val, list) else [pins_val]:
                if p in pin_signals and signal not in pin_signals[p]:
                    pin_signals[p] = pin_signals[p] + "/" + signal
                else:
                    pin_signals[p] = signal

        _assign(pin_map_data.get("vcc-pin", []), "VCC")
        _assign(pin_map_data.get("gnd-pin", []), "GND")
        _assign(pin_map_data.get("ce-pin", []), "CE")
        _assign(pin_map_data.get("oe-pin", []), "OE")
        _assign(pin_map_data.get("pgm-pin", []), "PGM")
        _assign(
            pin_map_data.get("vpp-pin", []), "VPP"
        )  # may append "/VPP" to OE if shared

        for i, p in enumerate(pin_map_data.get("address-bus-pins", [])):
            if p not in pin_signals:
                pin_signals[p] = f"A{i}"

        for i, p in enumerate(pin_map_data.get("data-bus-pins", [])):
            if p not in pin_signals:
                pin_signals[p] = f"D{i}"

        return [(p, pin_signals.get(p, "NC")) for p in range(1, pin_count + 1)]

    def map_chip_record(self, ic: dict, manufacturer: str) -> dict:
        """Public alias for `_map_data` — stable surface for callers outside this module.

        The `id` command in cli_handlers.py previously reached into
        `db._map_data` directly to render `search_chip_id` results. That coupled the CLI
        to a private name; this thin wrapper decouples the surface without changing
        behaviour. Future refactors can rework `_map_data`'s signature freely as long
        as this public alias keeps the (ic, manufacturer) -> dict contract.
        """
        return self._map_data(ic, manufacturer)

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

        # Read algorithm integer directly — set by build_db.py from upstream protocol_id
        protocol_id = programming.get("algorithm", 0)

        # The new DB doesn't have the raw flags, so we infer what we can
        info_flags = 0
        if programming.get("chip_id_check"):
            info_flags |= 0x00000020  # Has Readable Chip ID
        if electrical.get("type") in ("EEPROM", "Flash/EEPROM"):
            info_flags |= 0x00000010  # Can be electrically erased

        # Carry the raw electrical.type string through so the
        # list/search view (print_eprom_list_table) can reach the same ground-truth
        # field that the info view (build_specifications) uses, via the shared
        # resolve_type_label helper.  Key "electrical-type" consumed by eprom_info.py.
        # Direct indexing, never `.get(key, 0)` — a stale string-schema
        # `~/.firestarter/database.json` override missing `vcc_mv`/`vpp_mv`/
        # `pulse_duration_us` must raise `KeyError` loudly here rather than
        # silently resolving to `0`. `pulse-delay: 0` now means
        # "algorithm-controlled", so a silently-defaulted `0` would program a
        # 0x07 chip with no pulse at all.
        data = {
            "name": ic.get("part_number"),
            "manufacturer": manufacturer,
            "memory-size": electrical.get("size_bytes", 0),
            "pin-count": pin_count,
            "vpp_mv": electrical["vpp_mv"],
            "vcc_mv": electrical["vcc_mv"],
            "pulse-delay": programming["pulse_duration_us"],
            "verified": bool(ic.get("verified", False)),
            "info-flags": info_flags,
            "flags": 0,
            "protocol-id": protocol_id,
            "pin-map": pinout_key,
            "electrical-type": electrical.get("type", ""),
        }

        chip_id_val = programming.get("chip_id_value")
        if chip_id_val:
            data["chip-id"] = int(chip_id_val, 16)

        # PGSZ-01 / CR-01: carry per-chip page_size when present. Set by
        # build_db.py either for a datasheet-curated [CITED:] chip or, as
        # for a chip whose OWN upstream protocol_id is 0x0D
        # (algorithm 13 / EEPROM_POLL only). This guard is a TRUTHINESS
        # test (`if page_size_val:`), not a presence test -- a page_size of
        # 0 is therefore silently dropped and unreachable on the wire from
        # this host. Chips absent from both sources omit the field and
        # ride the firmware's own named AT28C page-size floor constant
        # (algorithm 13 only; other algorithms' handlers never consume this
        # key). The internal dict key is page_size (underscore); the WIRE
        # key is page-size (hyphen, JSON_KEY_PAGE_SIZE in constants.py) --
        # deliberately distinct spellings for the same English word.
        page_size_val = programming.get("page_size")
        if page_size_val:
            data["page_size"] = int(page_size_val)

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
        """  # noqa: E501
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

        Matches against `part_number` directly OR against any of the
        comma-separated alias names within it. Upstream infoic.xml encodes
        chip aliases as comma-separated lists (e.g.,
        "AT28C256,AT28C256E,AT28HC256"), which build_db.py preserves in
        the part_number field. Without alias-aware lookup, queries like
        `firestarter info AT28C256` failed despite the chip being in the DB.

        Also handles infoic.xml's parenthetical mode annotations: many
        chip names carry "(RW)", "(TEST)", "(RW3.3V)" etc. (e.g.,
        "DS1245AB(RW),DS1245Y(RW)"). A plain query like "DS1245AB"
        is normalized to match against the paren-stripped alias.
        """
        import re

        def _strip_paren(s):
            # "DS1245AB(RW)" -> "DS1245AB"; preserves the canonical chip name.
            return re.sub(r"\([^)]*\)", "", s).strip().lower()

        query = chip_name.lower()
        query_stripped = _strip_paren(chip_name)
        for manufacturer, ics in self.proms.items():
            for ic_config in ics:
                part_number = ic_config.get("part_number", "")
                if query == part_number.lower():
                    return ic_config, manufacturer
                # Alias match: split on comma + match individual aliases.
                # Try exact alias first, then paren-stripped alias.
                if "," in part_number or "(" in part_number:
                    aliases = [a.strip().lower() for a in part_number.split(",")]
                    if query in aliases:
                        return ic_config, manufacturer
                    aliases_stripped = [_strip_paren(a) for a in part_number.split(",")]
                    if query_stripped and query_stripped in aliases_stripped:
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

        # vpp_mv is the sole VPP source (integer millivolts from build_db.py) —
        # the legacy string-schema volts-key fallback is gone; `_map_data`
        # always sets `vpp_mv` via direct indexing.
        vpp_mv = full_eprom_data["vpp_mv"]

        # Keys to keep from the full data
        programmer_data = {
            "memory-size": full_eprom_data.get("memory-size", 0),
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

        # PGSZ-03 / CR-01: emit page-size wire field only when the DB supplies a
        # page_size (curated or provenance-keyed for an
        # upstream-native 0x0D row) -- emit-when-present, mirrors chip-id.
        # This guard is also a TRUTHINESS test (`.get(...)` is truthy-checked,
        # not `"page_size" in full_eprom_data`), so a page_size of 0 is
        # silently dropped -- 0 is an unreachable wire value from this host.
        # Absent chips send nothing; firmware (algorithm 13 / 0x0D only)
        # falls back to its own named AT28C page-size floor constant.
        if full_eprom_data.get("page_size"):
            programmer_data["page-size"] = full_eprom_data["page_size"]

        # FLAG_CAN_ERASE is set directly from electrical.type rather than from a
        # synthetic info-flags round-trip, so the derivation reads the same canonical
        # field and cannot drift. A missing key degrades to flag-clear.
        #
        # Algorithm 5 (flash4) is the ONLY exclusion, and it is a HARDWARE-SAFETY one:
        # flash4 auto-erases per page during the page write, and setting the flag
        # routes the firmware into an erase that asserts the VPP regulator on a
        # 5V-only chip. 12V on a 5V part. This argument is live, not retired.
        #
        # Algorithm 13 was once excluded too, for an unrelated reason -- the firmware
        # had no 28C erase, so the flag was a false capability claim. It now has one
        # (the AN-0544B software chip erase), and the standalone `erase` command's
        # refusal gate does read the flag, so it is not firmware-inert there either.
        #
        # READ THIS BEFORE RE-CLEARING THE FLAG for algorithm 13: the old policy was
        # correct given its premise, and only the premise changed. Re-clearing it
        # without first showing the firmware erase arm is gone re-reverses a reversal;
        # it does not fix a bug. The two exclusions were never the same argument and
        # must not be collapsed into one.
        #
        # Restoring the flag does NOT make `write` erase implicitly -- erase stays a
        # standalone step.
        simple_flags = 0
        algo = programmer_data["algorithm"]  # already computed above from protocol-id
        if full_eprom_data.get("electrical-type", "") in ("EEPROM", "Flash/EEPROM"):
            if algo not in (5,):
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
                            f"Invalid chip-id format for {ic_config.get('part_number', 'Unknown EPROM')}: {programming.get('chip_id_value')}"  # noqa: E501
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
        if pin_count and not variant is None:  # noqa: E714
            print("\n--- Pin Map ---")
            pin_map_details = db.get_pin_map(pin_count, variant)
            print(json.dumps(pin_map_details, indent=2))
            print("\n--- Bus Config ---")
            bus_config_details = db.get_bus_config(pin_count, variant)
            print(json.dumps(bus_config_details, indent=2))


if __name__ == "__main__":
    main()
