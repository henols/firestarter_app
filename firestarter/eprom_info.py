"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

EPROM Information Module
"""

import json
import re
import logging

import firestarter.database as db
from firestarter.ic_layout import print_chip_info, get_chip_type

logger = logging.getLogger("EPROMInfo")


def list_eproms(verified=False):
    """
    Lists EPROMs in the database.

    Args:
        verified (bool): If True, only lists verified EPROMs.
    """
    logger.info("Listing EPROMs in the database:")
    eproms = db.get_eproms(verified)
    if not eproms:
        logger.error("No EPROMs found.")
    format_eproms(eproms)
    return 0


def search_eproms(query):
    """
    Searches for EPROMs in the database by name or properties.

    Args:
        query (str): Search text.
    """
    logger.info(f"Searching for EPROMs with query: {query}")
    results = db.search_eprom(query, True)
    if not results:
        logger.error("No matching EPROMs found.")
        return 1
    format_eproms(results)
    return 0


def format_eproms(eproms):
    divider = f"+{'':-<14}+{'':-<18}+{'':-<6}+{'':-<12}+{'':-<14}+{'':-<5}+"
    logger.info(divider)
    logger.info(
        f"| {'Name': <13}| {'Manufacturer': <17}| {'Pins': <5}| {'Chip ID': <11}| {'Type': <13}| {'VPP': <4}|"
    )
    logger.info(divider)
    for ic in eproms:
        
        chip_id = f"{ic['chip-id']:}" if "chip-id" in ic and not ic["chip-id"] == "0x00000000" else ""
        vpp = "- "
        if ic["type"] == 1 :
            vpp = f"{ic['vpp']}v" if "vpp" in ic else "- "
        type = get_chip_type(ic["type"])
        logger.info(
            f"| {ic['name']: <13}| {ic['manufacturer']: <17}|{ic['pin-count']: >5} | {chip_id: <10} | {type: <12} | {vpp: >3} |"
        )
    logger.info(divider)


def eprom_info(eprom_name, export=False):
    """
    Displays information about a specific EPROM.

    Args:
        eprom_name (str): Name of the EPROM.
    """

    eprom = db.get_eprom(eprom_name, True)
    if not eprom:
        logger.error(f"EPROM {eprom_name} not found.")
        return 1

    print_chip_info(eprom)

    logger.debug("")
    logger.debug("Config sent to Firestarter:")
    logger.debug(json_output(db.get_eprom(eprom_name)))

    if export:

        config, manufacturer = db.get_eprom_config(eprom_name)
        #  clean config
        config = clean_config(config)
        logger.info(f"{config['name']} config:")
        root = {manufacturer: [config]}
        logger.info(json_output(root))

        pin_map = db.get_pin_map(config["pin-count"], config["pin-map"])
        if pin_map:
            root = {config["pin-count"]: {config["pin-map"]: pin_map}}

            logger.info("")
            logger.info(f"{eprom_name} pin map:")
            logger.info(json_output(root))
        else:
            logger.error(f"Pin map for {eprom_name} not found.")
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
        new_config["chip-id"] = (
            config["chip-id"] if "chip-id" in config else "bad value"
        )

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
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    db.init_db()
    chip_name = "test"
    search_eproms(chip_name)


if __name__ == "__main__":
    main()
