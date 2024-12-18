"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

EPROM Information Module
"""

import json
import re

try:
    from .database import (
        get_eprom,
        search_eprom,
        get_eproms,
        get_eprom_config,
        get_pin_map,
        init_db,
    )
    from .ic_layout import print_chip_info
    from .utils import verbose
    from .__init__ import __version__ as version

except ImportError:
    from database import (
        get_eprom,
        search_eprom,
        get_eproms,
        get_eprom_config,
        get_pin_map,
        init_db,
    )
    from ic_layout import print_chip_info
    from utils import verbose
    from __init__ import __version__ as version


def list_eproms(verified=False):
    """
    Lists EPROMs in the database.

    Args:
        verified (bool): If True, only lists verified EPROMs.
    """
    print("Listing EPROMs in the database:")
    eproms = get_eproms(verified)
    if not eproms:
        print("No EPROMs found.")
    for eprom in eproms:
        print(eprom)
    return 0


def search_eproms(query):
    """
    Searches for EPROMs in the database by name or properties.

    Args:
        query (str): Search text.
    """
    print(f"Searching for EPROMs with query: {query}")
    results = search_eprom(query, True)
    if not results:
        print("No matching EPROMs found.")
        return 1
    for result in results:
        print(result)
    return 0


def eprom_info(eprom_name, export=False):
    """
    Displays information about a specific EPROM.

    Args:
        eprom_name (str): Name of the EPROM.
    """

    eprom = get_eprom(eprom_name, True)
    if not eprom:
        print(f"EPROM {eprom_name} not found.")
        return 1

    print_chip_info(eprom)

    if verbose():
        print()
        print("Config sent to Firestarter:")
        print(json_output(get_eprom(eprom_name)))

    if export:
        if verbose():
            print()

        config, manufacturer = get_eprom_config(eprom_name)
        #  clean config
        config = clean_config(config)
        print(f"{config['name']} config:")
        root = {manufacturer: [config]}
        print(json_output(root))

        pin_map = get_pin_map(config["pin-count"], config["pin-map"])
        if pin_map:
            root = {config["pin-count"]: {config["pin-map"]: pin_map}}

            print()
            print(f"{eprom_name} pin map:")
            print(json_output(root))
        else:
            print(f"Pin map for {eprom_name} not found.")
            return 1

    return 0


def json_output(data):
    json_output = json.dumps(data, indent=4)
    json_output = re.sub(
        r"(\[\n\s*)([\d,\s]+)(\n\s*\])",
        lambda match: match.group(1)
        + match.group(2).replace("\n", "").replace(" ", "").replace(",", ", ")
        + match.group(3),
        json_output,
    )
    return json_output


def clean_config(config):
    new_config = {}
    new_config["name"] = config["name"] if "name" in config else "bad value"
    new_config["pin-count"] = (
        config["pin-count"] if "pin-count" in config else "bad value"
    )
    new_config["can-erase"] = (
        config["can-erase"] if "can-erase" in config else "bad value"
    )
    new_config["has-chip-id"] = (
        config["has-chip-id"] if "has-chip-id" in config else "bad value"
    )
    if config["has-chip-id"]:
        new_config["chip-id"] = config["chip-id"] if "chip-id" in config else "bad value"
        
    if "pin-map" in config:
        new_config["pin-map"] = config["pin-map"]
    else:
        new_config["pin-map"] = (
            config["variant"] if "variant" in config else "bad value"
        )

    new_config["protocol-id"] = (
        config["protocol-id"] if "protocol-id" in config else "bad value"
    )
    new_config["memory-size"] = (
        config["memory-size"] if "memory-size" in config else "bad value"
    )
    new_config["type"] = config["type"] if "type" in config else "bad value"
    new_config["voltages"] = config["voltages"] if "voltages" in config else "bad value"
    new_config["pulse-delay"] = (
        config["pulse-delay"] if "pulse-delay" in config else "bad value"
    )
    new_config["flags"] = config["flags"] if "flags" in config else "0x00"
    new_config["verified"] = config["verified"] if "verified" in config else False

    if "voltages" in new_config:
        config["voltages"].pop("vdd") if "vdd" in config["voltages"] else "bad value"
        config["voltages"].pop("vcc") if "vcc" in config["voltages"] else "bad value"
    return new_config


def main():
    init_db()
    chip_name = "test"
    eprom_info(chip_name, export=True)


if __name__ == "__main__":
    main()
