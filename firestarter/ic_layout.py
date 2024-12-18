try:
    from .utils import verbose
    from .database import get_pin_map
except ImportError:
    from utils import verbose
    from database import get_pin_map


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


def select_label(jp, l1, l2):
    if jp == 1:
        return l1
    if jp == 2:
        return l2
    return "NA"


def print_jumper_settings(jp1, jp2, jp3):
    jumper = [" ● ● ● ", " ●(● ●)", "(● ●)● "]

    jp1_label = select_label(jp1, "A13", "VCC")
    jp2_label = select_label(jp2, "A17", "VCC")
    jp3_label = select_label(jp3, "32pin", "28pin")
    print()
    print("    Jumper config (Rev 0 & 1 only)")
    print(f"JP1    5V [{jumper[jp1]}] A13   : {jp1_label}")
    print(f"JP2    5V [{jumper[jp2]}] A17   : {jp2_label}")
    print(f"JP3 28pin [{jumper[jp3]}] 32pin : {jp3_label}")


def print_jumper_settings_jp3_mod(jp3):
    jp3_label = select_label(jp3, "Open", "Closed")
    jumper = [" N/A ", " ● ● ", "(● ●)"]
    print()
    print("    Jumper config (JP4 on Rev 2)")
    print(f"JP4 (Rev 2)    [{jumper[jp3]}] : {jp3_label}")


def print_chip_info(eprom):
    verified = ""
    if not eprom["verified"]:
        verified = "\t-- NOT VERIFIED --"

    print(f"Eprom Info {verified}")
    print(f"Name:\t\t{eprom['name']}")
    print(f"Manufacturer:\t{eprom['manufacturer']}")
    print(f"Number of pins:\t{eprom['pin-count']}")
    print(f"Memory size:\t{hex(eprom['memory-size'])}")
    if eprom["type"] == 1:
        print(f"Type:\t\tEPROM")
        print(f"Can be erased:\t{eprom['can-erase']}")
        if "chip-id" in eprom:
            print(f"Chip ID:\t{hex(eprom['chip-id'])}")
    elif eprom["type"] == 4:
        print(f"Type:\t\tSRAM")
    elif eprom["type"] == 2:
        print(f"Type:\t\tFlash Memory type 2")
    elif eprom["type"] == 3:
        print(f"Type:\t\tFlash Memory type 3")
    if "flags" in eprom:
        if eprom["flags"] & 0x00000008:
            print(f"VPP:\t\t{eprom['vpp']}")
    if "chip-id" in eprom:  
        print(f"Chip ID:\t{hex(eprom['chip-id'])}")
    print(f"Pulse delay:\t{eprom['pulse-delay']}µS")
    print_generic_eeprom(eprom)

    if verbose():
        # print(protocol_info(eprom["protocol-id"]))
        # print()
        print(f"Protocol: {hex(eprom['protocol-id'])}")
        print()

        # Interpret the flags
        if "flags" in eprom:
            properties = interpret_flags(eprom["flags"])
            # Output the results
            print(f"Flags Value: 0x{eprom['flags']:08X}")
            if properties:
                print("Interpreted IC Properties:")
                for prop in properties:
                    print(f" - {prop}")


def interpret_flags(flags):
    """
    Interpret the flags value and return a list of properties.

    Args:
        flags (int): The flags value as an integer.

    Returns:
        List[str]: A list of interpreted properties.
    """
    properties = []

    # Define the bit masks and their meanings
    flag_definitions = [
        # (0x00000008, "Requires VPP (High Programming Voltage) ?"),
        (0x00000010, "Can be electrically erased"),
        (0x00000020, "Has Readable Chip ID"),
        # (0x00000040, "Uses EPROM Programming Algorithm ?"),
        (0x00000080, "Is Electrically Erasable or Writable (EEPROM/Flash/SRAM)"),
        (0x00000200, "Supports Boot Block Features"),
        # (0x00000400, "Supports OTP (One-Time Programmable) Memory ?"),
        # (0x00000800, "Supports Data Memory Addressing ?"),
        (0x00001000, "Data Memory Addressing"),
        (0x00002000, "Data Bus Width"),
        (0x00004000, "Software Data Protection (SDP) before Erase/Program"),
        (0x00008000, "Software Data Protection (SDP) after Erase/Program"),
        # (0x00008000, "Requires Specific Write Sequence or Hardware Protection ?"),
        # (0x00400000, "Supports Block Locking or Sector Protection ?"),
        (0x00300000, "Supported Programming Modes"),
        (0x03000000, "Data Organization"),
    ]

    # Check each flag definition
    for bitmask, description in flag_definitions:
        if flags & bitmask:
            properties.append(description)

    # # Handle combined flags for advanced features
    # if (flags & 0x0000C000) == 0x0000C000:
    #     properties.append(" -> Advanced Write Protection Mechanisms")

    # if (flags & 0x00000090) == 0x00000090:
    #     properties.append(" -> Electrically Erasable with Write Enable Sequence")

    # if (flags & 0x000000E8) == 0x000000E8:
    #     properties.append(
    #         " -> EEPROM with Special Programming Algorithm and Electrically Erasable"
    #     )

    # if (flags & 0x00004278) == 0x00004278:
    #     properties.append(
    #         " -> Flash Memory with Advanced Protection Features (Block Locking, SDP, etc.)"
    #     )

    # if (flags & 0x0040C078) == 0x0040C078:
    #     properties.append(
    #         " -> Flash Memory with Block Locking and Advanced Write Protection"
    #     )

    return properties


def protocol_info(protocol_id):
    protocol_info = [
        (
            0x05,
            "EEPROM/Flash",
            (
                "EEPROM/Flash with write enable sequence, software commands",
                "Requires specific software commands for programming/erasure",
                "Operates at standard voltage levels",
            ),
        ),
        (
            0x06,
            "Flash Memory",
            (
                "Standard Flash memory programming protocol",
                "Uses command sequences for programming/erasure",
                "Operates at standard voltage levels",
            ),
        ),
        (
            0x07,
            "EEPROM",
            (
                "EEPROM programming protocol for 28-pin devices",
                "Byte-wise programming, no high voltage required",
                "May include software data protection",
            ),
        ),
        (
            0x08,
            "EPROM",
            (
                "EPROM programming protocol requiring high programming voltage",
                "Uses VPP (typically 12.5V or higher) for programming", 
                "Follows EPROM programming algorithms",
            ),
        ),
        (
            0x0B,
            "EPROM/EEPROM",
            (
                "Programming protocol for older 24-pin devices",
                "May require VPP for programming",
                "Smaller capacity devices",
            ),
        ),
        (
            0x0D,
            "EEPROM",
            (
                "Programming protocol for large EEPROMs",
                "Supports byte-wise programming",
                "May require specific write sequences",
            ),
        ),
        (
            0x0E,
            "SRAM",
            (
                "SRAM with battery backup or additional features",
                "Standard SRAM access protocols",
                "32-pin devices",
            ),
        ),
        (
            0x10,
            "Flash Memory",
            (
                "Intel-compatible Flash memory programming protocol",
                "Requires specific command sequences",
                "Operates at standard voltage levels",
            ),
        ),
        (
            0x11,
            "Flash Memory",
            (
                "Firmware Hub (FWH) programming protocol",
                "Used in BIOS chips",
                "Requires specific interfaces and commands",
            ),
        ),
        (
            0x27,
            "SRAM",
            (
                "Standard SRAM access protocol for 24-pin devices",
                "2Kb SRAM devices",
                "Simple read/write operations",
            ),
        ),
        (
            0x28,
            "SRAM",
            (
                "Standard SRAM access protocol for 28-pin devices",
                "8Kb SRAM devices",
                "Simple read/write operations",
            ),
        ),
        (
            0x29,
            "SRAM",
            (
                "Standard SRAM access protocol for 32-pin devices",
                "512Kb to 1Mb SRAM devices",
                "Simple read/write operations",
            ),
        ),
        (
            0x2A,
            "NVRAM",
            (
                "Non-volatile SRAM with built-in battery",
                "Requires special handling for battery-backed operation",
                "32-pin devices",
            ),
        ),
        (
            0x2C,
            "NVRAM",
            (
                "Non-volatile SRAM (Timekeeping RAM)",
                "May include real-time clock features",
                "Standard SRAM access protocol",
            ),
        ),
        (
            0x2E,
            "NVRAM",
            (
                "High-capacity non-volatile SRAM",
                "512Kb and larger sizes",
                "Requires specific protocols for access",
            ),
        ),
        (
            0x35,
            "Flash Memory",
            (
                "Flash memory with EEPROM-like interface",
                "Requires specific write sequences",
                "May include software data protection",
            ),
        ),
        (
            0x39,
            "Flash Memory",
            (
                "Advanced Flash memory programming protocol",
                "Uses command sequences similar to Intel algorithms",
                "Operates at standard voltage levels",
            ),
        ),
        (
            0x3C,
            "Flash Memory",
            (
                "Common Flash memory protocol for 4Mb devices",
                "Uses standard command sequences for programming",
                "May operate at lower voltages (3.3V)",
            ),
        ),
    ]
    for id, type, description in protocol_info:
        if id == protocol_id:
            return f"Protocol: {type} (0x{id:02X})\nDescription:\n - {description[0]}\n - {description[1]}\n - {description[2]}"

    return None


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
        
    pin_map = get_pin_map(pin_count, eprom["pin-map"])
    if pin_map:
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
        print(f"No pin map available, layout is assumed.")
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
    print_jumper_settings_jp3_mod(jp3)
    print()
