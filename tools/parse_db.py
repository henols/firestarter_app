"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.
"""

import xml.etree.ElementTree as ET
import json

PIN_COUNT_MASK = 0x7F000000
PLCC_MASK = 0xFF000000
ADAPTER_MASK = 0x000000FF
ICSP_MASK = 0x0000FF00
SMD_MASK = 0x80000000

PLCC32_ADAPTER = 0xFF000000
PLCC44_ADAPTER = 0xFD000000

HITACHI_MASK_PROM_MASK = 0x80


MP_ERASE_MASK = 0x00000010
MP_ID_MASK = 0x00000020
MP_SUPPORTED_PROGRAMMING = 0x00300000


#  { { "9", 0x10 },    { "9.5", 0x20 },
# 			     { "10", 0x30 },   { "11", 0x40 },
# 			     { "11.5", 0x50 }, { "12", 0x00 },
# 			     { "12.5", 0x60 }, { "13", 0x70 },
# 			     { "13.5", 0x80 }, { "14", 0x90 },
# 			     { "14.5", 0xa0 }, { "15.5", 0xb0 },
# 			     { "16", 0xc0 },   { "16.5", 0xd0 },
# 			     { "17", 0xe0 },   { "18", 0xf0 },

vpp_voltages = {
    16: "9",
    32: "9.5",
    48: "10",
    64: "11",
    80: "11.5",
    0: "12",
    96: "12.5",
    112: "13",
    128: "13.5",
    144: "14",
    160: "14.5",
    176: "15.5",
    192: "16",
    208: "16.5",
    224: "17",
    240: "18",
}

vcc_voltages = {
    0x01: "3.3",
    0x02: "4",
    0x03: "4.5",
    0x00: "5",
    0x04: "5.5",
    0x05: "6.5",
}


def get_pin_count(package_details):
    if (package_details & PLCC_MASK) == PLCC32_ADAPTER:
        return 0
    elif (package_details & PLCC_MASK) == PLCC44_ADAPTER:
        return 0
    return (package_details & PIN_COUNT_MASK) >> 24


types = {
    0x01: "memory",
    #    0x02:"mcu",
    #    0x03:"pld",
    0x04: "sram",
    #    0x05:"logic",
    #    0x06:"nand"
}


def read_verified(file_path):
    names = []
    with open(file_path, "r") as file:
        for line in file:
            # Remove any leading/trailing whitespace characters (like newlines)
            name = line.strip()
            # Add the name to the array if it's not empty
            if name:
                names.append(name)
    return names


def parse_xml_and_extract(filename):
    tree = ET.parse(filename)
    root = tree.getroot()

    all_data = {}  # Dictionary to store ICs grouped by manufacturer

    verified_list = read_verified("tools/verified.txt")
    seen = set()  # To track unique ICs by name and chip_id
    nr_ics = 0
    for database in root.findall(".//database"):
        for manufacturer in database.findall(".//manufacturer"):
            manufacturer_name = manufacturer.get("name")
            ics_list = all_data.get(manufacturer_name, [])

            for ic in manufacturer.findall(".//ic"):
                name = ic.get("name")
                if "@" in name:
                    name = name[: name.index("@")]
                elif "(TEST" in name or "(RW)" in name:
                    name = name[: name.index("(")]
                variant = int(ic.get("variant"), 16) & 0xFF
                pin_map = int(ic.get("pin_map"), 16)
                package_details = int(ic.get("package_details"), 16)
                pin_count = get_pin_count(package_details)
                adapter = package_details & ADAPTER_MASK
                icsp = (package_details & ICSP_MASK) >> 8
                identifier = (name, ic.get("chip_id"))
                type = types.get(int(ic.get("type"), 16), None)
                flags = int(ic.get("flags"), 16)
                can_erase = (flags & MP_ERASE_MASK) == MP_ERASE_MASK
                has_chip_id = (flags & MP_ID_MASK) == MP_ID_MASK
                voltages = int(ic.get("voltages"), 16)
                vdd = vcc_voltages.get((voltages >> 12) & 0x0F)
                vcc = vcc_voltages.get((voltages >> 8) & 0x0F)
                vpp_val = voltages & 0xFF
                vpp = vpp_voltages.get(vpp_val)
                verified = name in verified_list
                if (
                    type
                    and pin_count >= 24
                    and pin_count <= 32
                    and identifier not in seen
                    and not ("(TEST" in name or "(RW)" in name)
                    and not ("3.3" in name)
                    # and identifier  in seen
                    and not variant & HITACHI_MASK_PROM_MASK
                    and not package_details & SMD_MASK == SMD_MASK
                    and not icsp
                    # and not vpp == None
                ):

                    mem_size = int(ic.get("code_memory_size"), 16)
                    # p_id=ic.get("protocol_id")
                    # if not pin_count in variants:
                    #     variants[pin_count] = {}
                    # if not variant in variants[pin_count]:
                    #     variants[pin_count][variant] = {}
                    # if not p_id in variants[pin_count][variant]:
                    #     variants[pin_count][variant][p_id] = []
                    # variants[pin_count][variant][p_id].append(f"{name} - {type}")

                    seen.add(identifier)
                    nr_ics = nr_ics + 1
                    # print(ic.get("code_memory_size"))
                    # if  vpp == None:
                    #     print(f"{name} {variant} {pin_count} {mem_size}")
                    # SST39VF040
                    # if pin_count == 28:
                    #     if type == 1:
                    #         pin_map = 1
                    #     elif "27" in name and "64" in name:
                    #         pin_map = 1
                    #     elif "27" in name and "128" in name:
                    #         pin_map = 2
                    #     elif "27" in name and "256" in name:
                    #         pin_map = 3
                    #     elif "27" in name and "512" in name:
                    #         pin_map = 0
                    #     else:
                    #         pin_map = -1
                    # else:
                    #     pin_map = -1
                    ic_data = {
                        "name": name,
                        "pin-count": pin_count,
                        "adapter": adapter,
                        "can-erase": can_erase,
                        "has-chip-id": has_chip_id,
                        "chip-id": ic.get("chip_id"),
                        "variant": variant,
                        "protocol-id": ic.get("protocol_id"),
                        # "variant": ic.get("variant"),
                        "memory-size": hex(mem_size),
                        "type": type,
                        "voltages": {
                            # "raw": voltages,
                            "vdd": vdd,
                            "vcc": vcc,
                            "vpp": vpp,
                        },
                        "pulse-delay": ic.get("pulse_delay"),
                        "flags": ic.get("flags"),
                        "chip-info": ic.get("chip_info"),
                        # "pin-map": pin_map,
                        "package-details": ic.get("package_details"),
                        "verified": verified,
                        # "config": ic.get("config"),
                    }
                    ics_list.append(ic_data)

                else:
                    seen.add(identifier)

            if ics_list:
                all_data[manufacturer_name] = ics_list
    # for v in variants:
    #     print(v)
    # save_to_json(pin_maps, "pin_maps.json")
    # save_to_json(pin_maps_2, "pin_maps_2.json")
    # print(pin_maps)
    return all_data, nr_ics


def save_to_json(data, filename):
    with open(filename, "w") as file:
        json.dump(data, file, indent=4)


def main():

    xml_filename = "tools/infoic.xml"
    json_filename = "firestarter/data/database_generated.json"
    filtered_ics, nr_ics = parse_xml_and_extract(xml_filename)
    save_to_json(filtered_ics, json_filename)
    print(
        f"Data saved to {json_filename} with {len(filtered_ics)} manufacturer(s) having 24 or more pins. {nr_ics}"
    )


if __name__ == "__main__":
    main()
