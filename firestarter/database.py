"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.
"""

import os
import json
from pathlib import Path

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


def read_config(filename):
    path = Path(os.path.dirname(__file__))
    filepath = path / Path("data") / filename

    with filepath.open("rt") as file:
        config = json.load(file)
    return config


def init_db():
    global proms
    global pin_maps
    proms_filename = "database.json"
    proms = read_config(proms_filename)
    pin_maps = read_config("pin-maps.json")


def get_bus_config(pins, variant):
    pins_key = str(pins)
    variant_key = str(variant)
    if pins_key in pin_maps:
        if variant_key in pin_maps[pins_key]:
            pin_map = pin_maps[pins_key][variant_key]
            if "holder" in pin_map:
                return None, None
            if pins == 28 and variant == 16:
                return None, pin_map
            map = {}
            bus = []
            for pin in pin_map["address-bus-pins"]:
                bus.append(pin_conversions[pins][pin])
            map["bus"] = bus

            if "rw-pin" in pin_map:
                map["rw-pin"] = pin_conversions[pins][pin_map["rw-pin"]]

            if "vpp-pin" in pin_map:
                map["vpp-pin"] = pin_conversions[pins][pin_map["vpp-pin"]]

            return map, pin_map
    return None, None


def map_data(ic, manufacturer):
    pin_count = ic["pin-count"]
    vpp = 0
    if not ic["voltages"]["vpp"] == None:
        vpp = int(ic["voltages"]["vpp"])
    variant = ic["variant"]
    ic_type = types.get(ic["type"])
    protocol_id = int(ic["protocol-id"], 16)
    flags = int(ic["flags"], 16)
    type = 4
    if ic_type == 1:
        if protocol_id == 0x06:
            type = 3
        elif protocol_id == 0x05:
            type = 2
        elif flags & 0x08:
            type = 1
    # if pin_count == 24 and variant in [0, 1, 16]:
    #     type = 1
    # elif pin_count == 28 and variant in [16, 17, 19]:
    #     type = 1
    # elif pin_count == 32 and variant in [0, 1, 2]:
    #     type = 1

    data = {
        "name": ic["name"],
        "manufacturer": manufacturer,
        "memory-size": int(ic["memory-size"], 16),
        "can-erase": bool(ic["can-erase"]),
        "type": type,
        "pin-count": pin_count,
        "vpp": vpp,
        "pulse-delay": int(ic["pulse-delay"], 16),
        "verified": ic["verified"],
        "flags": flags,
        "protocol-id": int(ic["protocol-id"], 16),
    }
    chip_id = int(ic["chip-id"], 16)
    if chip_id != 0:
        data["chip-id"] = chip_id

    # print(ic["pin-map"])
    bus_config, pin_map = get_bus_config(pin_count, variant)

    if bus_config:
        data["bus-config"] = bus_config
    if pin_map:
        data["pin-map"] = pin_map
    return data


def get_eproms(verified):
    selected_proms = []
    for manufacturer in proms:
        for ic in proms[manufacturer]:
            if (
                verified == None
                or not verified
                or (verified and "verified" in ic and ic["verified"])
            ):
                selected_proms.append(ic["name"])
    return selected_proms


def get_eprom(chip_name, full=False):
    for manufacturer in proms:
        for ic in proms[manufacturer]:
            if chip_name.lower() == ic["name"].lower():
                data = map_data(ic, manufacturer)
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
                return data
    return None


def search_eprom(chip_name, all):
    selected_proms = []
    for manufacturer in proms:
        for ic in proms[manufacturer]:
            if chip_name.lower() in ic["name"].lower():
                if ("verified" in ic and ic["verified"]) or all:
                    selected_proms.append(
                        ic["name"]
                        + "\tfrom "
                        + manufacturer
                        + " pins: "
                        + str(ic["pin-count"])
                    )
    return selected_proms


def search_chip_id(chip_id):
    selected_proms = []
    for manufacturer in proms:
        for ic in proms[manufacturer]:
            if ic["has-chip-id"] and int(ic["chip-id"], 16) == chip_id:
                selected_proms.append(
                    ic["name"]
                    + "\tfrom "
                    + manufacturer
                    + " pins: "
                    + str(ic["pin-count"])
                )
    return selected_proms

 
def main():
    init_db()
    chip_name = "TMS2764"

    prom = get_eprom(chip_name)
    if prom == None:
        print(f"Prom {chip_name} not found")
        return
    # manufacturer = ""
    print(f"Found {prom['name']} from {prom['manufacturer']}")
    print(prom)


if __name__ == "__main__":
    main()
