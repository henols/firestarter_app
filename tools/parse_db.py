"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.
"""

import xml.etree.ElementTree as ET
import json
import requests
from parse_helper import *


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


def parse_xml_and_extract(root):

    all_data = {}  # Dictionary to store ICs grouped by manufacturer

    verified_list = read_verified("tools/verified.txt")

    nr_ics = 0
    pin_maps = {}
    for database in root.findall("database"):
        if database.get("type") != "INFOIC2PLUS":
            continue
        for manufacturer in database.findall("manufacturer"):
            manufacturer_name = manufacturer.get("name")
            # ics_list = all_data.get(manufacturer_name, {})
            ics_list = all_data.get(manufacturer_name, [])
            # if len(ics_list) == 0:
            #     all_data[manufacturer_name] = ics_list
            for ic in manufacturer.findall("ic"):
                ic_data = build_ic_config(ic)
                if ic_data:
                    names = ic.get("name")
                    for name in names.split(","):
                        ic_data["name"] = name[
                            : name.index("@") if "@" in name else len(name)
                        ]
                        ic_data["verified"] = name in verified_list

                        # ics_list[name] =  ic_data.copy()
                        ics_list.append(ic_data.copy())
                        nr_ics = nr_ics + 1

                        pm = pin_maps.get(ic_data["pin-map-org"] & 0xFF, None)
                        if not pm:
                            pm = {
                                "pmu": [],
                            }
                            pin_maps[ic_data["pin-map-org"] & 0xFF] = pm

                        if pm["pmu"].count(ic_data["pin-map-org"] >> 2 & 0xFF) == 0:
                            pm["pmu"].append(ic_data["pin-map-org"] >> 2 & 0xFF)

                            # pin_maps[ic_data["pin-map-org"]>> 2 & 0xFF] = pm

                            # count(ic_data["pin-map-org"] & 0xFF) == 0:
                            # pin_maps.append(ic_data["pin-map-org"] & 0xFF)

            if manufacturer_name not in all_data and len(ics_list) > 0:
                all_data[manufacturer_name] = ics_list

    return all_data, nr_ics, pin_maps


def build_ic_config(ic):
    if not get_type_name(int(ic.get("type"), 16)):
        return None
    pin_count = get_pin_count(int(ic.get("package_details"), 16))
    if not pin_count in [24, 28, 32]:
        return None

    protocol_id = int(ic.get("protocol_id"), 16)
    if not get_protocol_info(protocol_id):
        return None

    # variant = int(ic.get("variant"), 16) & 0xFF
    # if variant & HITACHI_MASK_PROM_MASK:
    #     explain_ic(ic)
    #     return None

    return create_ic_config(ic)


def save_to_json(data, filename):
    with open(filename, "w") as file:
        json.dump(data, file, indent=4)


def get_infoic_root_file():
    filename = "tools/infoic.xml"
    tree = ET.parse(filename)
    return tree.getroot()


def get_infoic_root_url():
    url = "https://gitlab.com/DavidGriffith/minipro/-/raw/master/infoic.xml?ref_type=heads"

    try:
        # Download the XML file
        response = requests.get(url)
        response.raise_for_status()  # ensure we got a successful response
        xml_content = response.content
    except Exception as e:
        print(f"Error downloading the XML file: {e}")
        return None

    try:
        # Parse the XML content
        root = ET.fromstring(xml_content)
    except Exception as e:
        print(f"Error parsing XML content: {e}")
        return None

    return root


def main():

    json_filename = "firestarter/data/database_generated.json"

    # root = get_infoic_root_file()
    root = get_infoic_root_url()
    if root is None:
        return

    filtered_ics, nr_ics, pin_maps = parse_xml_and_extract(root)
    save_to_json(filtered_ics, json_filename)
    print(
        f"Data saved to {json_filename}, {nr_ics} ICs found from {len(filtered_ics)} manufacturers"
    )
    print(pin_maps)


if __name__ == "__main__":
    main()
