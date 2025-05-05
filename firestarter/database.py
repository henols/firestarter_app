"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.
"""

import os
import json
import logging

from pathlib import Path

from firestarter.config import get_local_database, get_local_pin_maps

types = {"memory": 0x01, "flash": 0x03, "sram": 0x04}
ROM_CE = 100

# eprom pins to rurp conversion
pin_conversions = {
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
        21: 11,
        22: 9,
        23: 8,
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
        21: 10,
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
        23: 10,
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


def read_config(filename):
    path = Path(os.path.dirname(__file__))
    filepath = path / Path("data") / filename

    with filepath.open("rt") as file:
        config = json.load(file)
    return config


inited = False


def init_db():
    global inited
    if inited:
        return
    inited = True
    global proms
    global pin_maps
    proms = read_config("database_generated.json")
    proms_man = read_config("database_overrides.json")
    proms = merge_databases(proms, proms_man)
    local_db = get_local_database()
    if local_db:
        proms = merge_databases(proms, local_db)
    pin_maps = read_config("pin-maps.json")
    local_pin_maps = get_local_pin_maps()
    if local_pin_maps:
        pin_maps = merge_pin_maps(pin_maps, local_pin_maps)


def merge_databases(db, manual_db):
    for key, manual_items in manual_db.items():
        if key in db:
            # Update or add items for an existing key
            existing_names = {item["name"]: item for item in db[key]}
            for manual_item in manual_items:
                if manual_item["name"] in existing_names:
                    # Replace existing item
                    existing_names[manual_item["name"]].update(manual_item)
                else:
                    # Add new item
                    db[key].append(manual_item)
        else:
            # Add entirely new key
            db[key] = manual_items
    return db


def merge_pin_maps(pin_maps, manual_pin_map):
    for key, sub_map in manual_pin_map.items():
        if key not in pin_maps:
            # Add new top-level key entirely if it doesn't exist
            pin_maps[key] = sub_map
        else:
            # Replace sub-objects in the existing key
            for sub_key, sub_value in sub_map.items():
                pin_maps[key][sub_key] = sub_value  # Replace existing or add new
    return pin_maps


def get_pin_map(pins, pin_map):
    pins_key = str(pins)
    pin_map_key = str(pin_map)
    if pins_key in pin_maps:
        if pin_map_key in pin_maps[pins_key]:
            pin_map = pin_maps[pins_key][pin_map_key]
            return pin_map if not "holder" in pin_map else None

    return None


def get_bus_config(pins, variant):
    if pins == 28 and variant == 16:
        return None
    pin_map = get_pin_map(pins, variant)
    if not pin_map:
        return None
    map = {}
    bus = []
    for pin in pin_map["address-bus-pins"]:
        bus.append(pin_conversions[pins][pin])
    map["bus"] = bus

    if "rw-pin" in pin_map:
        if pin_map["rw-pin"] in pin_conversions[pins]:
            map["rw-pin"] = pin_conversions[pins][pin_map["rw-pin"]]
        # else:
        #     logger.info(
        #         f"RW pin {pin_map['rw-pin']} not found in pin conversion for {pins} pins EPROMs"
        #     )

    if "oe-pin" in pin_map:
        if pin_map["oe-pin"] in pin_conversions[pins]:
            map["oe-pin"] = pin_conversions[pins][pin_map["oe-pin"]]
        # else:
        #     logger.info(
        #         f"OE pin {pin_map['oe-pin']} not found in pin conversion for {pins} pins EPROMs"
        #     )

    if "vpp-pin" in pin_map:
        if pin_map["vpp-pin"] in pin_conversions[pins]:
            map["vpp-pin"] = pin_conversions[pins][pin_map["vpp-pin"]]
        # else:
        #     logger.info(
        #         f"Vpp pin {pin_map['vpp-pin']} not found in pin conversion for {pins} pins EPROMs"
        #     )

    return map


def map_data(ic, manufacturer):
    pin_count = ic["pin-count"]
    vpp = 0
    vcc = 0
    if "voltages" in ic:
        voltages = ic["voltages"]
        if "vpp" in voltages and voltages["vpp"]:

            vpp = int(voltages["vpp"])
        if "vcc" in voltages and voltages["vcc"]:
            vcc = float(voltages["vcc"])
    pin_map = ic["pin-map"] if "pin-map" in ic else ic["variant"]
    ic_type = types.get(ic["type"])
    protocol_id = int(ic["protocol-id"], 16)
    flags = int(ic["flags"], 16) if "flags" in ic else 0
    type = 4
    if ic_type == 1:
        if protocol_id == 0x06:
            type = 3
        elif protocol_id == 0x05:
            type = 2
        elif flags & 0x08:
            type = 1

    data = {
        "name": ic["name"],
        "manufacturer": manufacturer,
        "memory-size": int(ic["memory-size"], 16),
        "can-erase": bool(ic["can-erase"]),
        "type": type,
        "pin-count": pin_count,
        "vpp": vpp,
        "vcc": vcc,
        "pulse-delay": int(ic["pulse-delay"], 16),
        "verified": ic["verified"] if "verified" in ic else False,
        "flags": flags,
        "protocol-id": int(ic["protocol-id"], 16),
        "pin-map": pin_map,
    }
    if "chip-id" in ic:
        data["chip-id"] = int(ic["chip-id"], 16)

    bus_config = get_bus_config(pin_count, pin_map)

    if bus_config:
        data["bus-config"] = bus_config
    return data


def get_eproms(verified=None):
    selected_proms = []
    for manufacturer in proms:
        for ic in proms[manufacturer]:
            if (
                verified == None
                or not verified
                or (verified and "verified" in ic and ic["verified"])
            ):
                # ic["manufacturer"] = manufacturer
                selected_proms.append(map_data(ic, manufacturer))
    return selected_proms


def get_eprom_config(chip_name):
    for manufacturer in proms:
        for ic in proms[manufacturer]:
            if chip_name.lower() == ic["name"].lower():
                return ic, manufacturer
    return None, None


def get_eprom(chip_name, full=False):
    config, manufacturer = get_eprom_config(chip_name)
    if config:
        data = map_data(config, manufacturer)
        if not full:
            if "manufacturer" in data:
                data.pop("manufacturer")
            if "verified" in data:
                data.pop("verified")
            if "pin-map" in data:
                data.pop("pin-map")
            if "name" in data:
                data.pop("name")
            if "flags" in data:
                data.pop("flags")
            if "protocol-id" in data:
                data.pop("protocol-id")
            if "variant" in data:
                data.pop("variant")
            if "pin-map" in data:
                data.pop("pin-map")
            if "vcc" in data:
                data.pop("vcc")
        return data
    return None


def search_eprom(chip_name, all=True):
    selected_proms = []
    for manufacturer in proms:
        for ic in proms[manufacturer]:
            if chip_name.lower() in ic["name"].lower():
                if ("verified" in ic and ic["verified"]) or all:

                    # ic["manufacturer"] = manufacturer
                    selected_proms.append(map_data(ic, manufacturer))
    return selected_proms


def search_chip_id(chip_id):
    selected_proms = []
    for manufacturer in proms:
        for ic in proms[manufacturer]:
            if ic["has-chip-id"] and int(ic["chip-id"], 16) == chip_id:
                ic["manufacturer"] = manufacturer
                selected_proms.append(ic)
    return selected_proms


def main():
    init_db()
    chip_name = "TMS2764"

    config, manufacturer = get_eprom_config(chip_name)
    if config == None:
        print(f"Prom {chip_name} not found")
        return
    # manufacturer = ""
    print(f"Found {config['name']} from {manufacturer}")
    print(config)

    pin_count = config["pin-count"]
    variant = config["pin-map"]

    print("--------------------")
    print(get_pin_map(pin_count, variant))
    bus = get_bus_config(pin_count, variant)
    print("---------- bus map ----------")
    print(bus)


if __name__ == "__main__":
    main()
