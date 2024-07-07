# Generic pin names for 24-pin, 28-pin, and 32-pin EPROMs
generic_pin_names = {
    24: [
        "A7",
        "A6",
        "A5",
        "A4",
        "A3",
        "A2",
        "A1",
        "A0",
        "D0",
        "D1",
        "D2",
        "GND",
        "D3",
        "D4",
        "D5",
        "D6",
        "D7",
        "CE",
        "A10",
        "OE",
        "A11",
        "A9",
        "A8",
        "VCC",
    ],
    28: [
        "NC",
        "NC",
        "A7",
        "A6",
        "A5",
        "A4",
        "A3",
        "A2",
        "A1",
        "A0",
        "D0",
        "D1",
        "D2",
        "GND",
        "D3",
        "D4",
        "D5",
        "D6",
        "D7",
        "CE",
        "A10",
        "OE/Vpp",
        "NC",
        "A9",
        "A8",
        "NC",
        "NC",
        "VCC",
    ],
    32: [
        "A18",
        "A16",
        "A15",
        "A12",
        "A7",
        "A6",
        "A5",
        "A4",
        "A3",
        "A2",
        "A1",
        "A0",
        "D0",
        "D1",
        "D2",
        "GND",
        "D3",
        "D4",
        "D5",
        "D6",
        "D7",
        "CE",
        "A10",
        "OE",
        "A11",
        "A9",
        "A8",
        "A13",
        "A14",
        "A17",
        "R/W",
        "VCC",
    ],
}


def print_eeprom(pin_count, pin_names):
    half = int(pin_count / 2)

    # Print top line with a dent in the middle
    print(" " * 8 + "-" * 5 + "v" + "-" * 5)

    # Print pins and labels
    for i in range(half):
        pin_left = pin_names[i]
        pin_right = pin_names[pin_count - i - 1]
        print(f"  {pin_left:<3} -| {i + 1:2}     {pin_count - i:2} |- {pin_right:<6}")
        # print(f"{i + 1:2} | {pin_left:<6}     {pin_right:<6} | {pin_count - i}")

    # Print the bottom line
    print(" " * 8 + "-" * 11)

def select_label(jp,l1,l2):
    if jp == 1:
        return l1
    if jp == 2:
        return l2
    return "NA"

def print_jumper_settings(jp1, jp2, jp3):
    jumper = [" ● ● ● ","(● ●)● ", " ●(● ●)"]

    jp1_label = select_label(jp1,"A13","VCC") 
    jp2_label = select_label(jp2,"A17","VCC") 
    jp3_label = select_label(jp3,"32pin","28pin") 
    print()
    print("         Jumper config")
    print(f"JP1   A13 [{jumper[jp1]}] 5V    : {jp1_label}")
    print(f"JP2   A17 [{jumper[jp2]}] 5V    : {jp2_label}")
    print(f"JP3 32pin [{jumper[jp3]}] 28pin : {jp3_label}")


# Function to print generic EPROM layout
def print_generic_eeprom(eprom):
    pin_count = eprom["pin-count"]
    if pin_count not in generic_pin_names:
        print(f"No generic layout available for {pin_count}-pin EPROM.")
        return
    print()

    pin_names = generic_pin_names[pin_count]
    oe_pin = int(pin_count / 2) + 8
    vpp_pin = 0
    if eprom["type"] == 4:
        pin_names[oe_pin - 1] = "OE"
    if "pin-map" in eprom:
        pin_map = eprom["pin-map"]

        if "rw-pin" in pin_map:
            pin_names[pin_map["rw-pin"] - 1] = "R/W"
        if "vpp-pin" in pin_map:
            if not oe_pin == pin_map["vpp-pin"]:
                vpp_pin = pin_map["vpp-pin"]
                pin_names[pin_map["vpp-pin"] - 1] = "Vpp"
                pin_names[oe_pin - 1] = "OE"

        if "address-bus-pins" in pin_map:
            i = 0
            for pin in pin_map["address-bus-pins"]:
                pin_names[pin - 1] = f"A{i}"
                i += 1
    else:
        print(f"No pin map available, layout is asumed.")
    print(f"       {pin_count}-DIP package")
    print_eeprom(pin_count, pin_names)
    jp1 = 0
    jp2 = 0
    jp3 = 0
    if pin_count == 24:
        jp1 = 2
    elif pin_count == 28:
        jp1 = 1
        jp2 = 2
        if vpp_pin:
            jp3 = 2
    elif pin_count == 32:
        jp1 = 1
        jp2 = 1
        if vpp_pin:
            jp3 = 1

    print_jumper_settings(jp1, jp2, jp3)
    print()
