import requests
import xml.etree.ElementTree as ET

FLAG_ERASE_MASK = 0x00000010
FLAG_ID_MASK = 0x00000020
MP_SUPPORTED_PROGRAMMING = 0x00300000


PIN_COUNT_MASK = 0x7F000000
PLCC_MASK = 0xFF000000
ADAPTER_MASK = 0x000000FF
ICSP_MASK = 0x0000FF00
SMD_MASK = 0x80000000


PLCC32_ADAPTER = 0xFF000000
PLCC44_ADAPTER = 0xFD000000

HITACHI_MASK_PROM_MASK = 0x80

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


protocol_descriptions_ = {
    0x05: "JEDEC Flash algorithm (basic)",
    0x06: "JEDEC Flash algorithm (AMD enhanced)",
    0x07: "Standard CMOS EPROM / EEPROM (13V VPP, shorter pulses)",
    0x08: "Atmel Page-mode / Fast EPROM",
    0x0B: "Early EPROM algorithm (high VCC, long pulses)",
    0x10: "Intel/Atmel 28F Flash algorithm",
}

protocol_descriptions = {
    0x05: "Atmel Flash/EEPROM command set",
    0x06: "AMD/Spansion Flash command set",
    0x07: "28-pin standard EPROM/EEPROM protocol",
    0x08: "32-pin EPROM protocol with WE/PGM pin handling",
    0x0B: "24-pin legacy EPROM programming (high VPP, fixed pulses)",
    0x0D: "32-pin Flash programming algorithm?",
    0x0E: "32-pin Nonvolatile SRAM?",
    0x10: "Intel ETOX Flash programming algorithm",
    0x11: "CFI-compliant or Intel-variant protocol",
    0x27: "Direct-write NVSRAM protocol 24-pin?",
    0x28: "Direct-write NVSRAM protocol 28-pin?",
    0x29: "Direct-write NVSRAM protocol 32-pin?",
}

FLAG_ERASE_MASK = 0x00000010
FLAG_ID_MASK = 0x00000020
MP_SUPPORTED_PROGRAMMING = 0x00300000


flags_meaning = {
    0x08: "Requires VPP high voltage for programming",
    FLAG_ERASE_MASK: "Electrically erasable (Flash/EEPROM)",
    FLAG_ID_MASK: "Supports chip ID (autoselect mode)",
    0x40: "Requires elevated VCC during programming",  # Standard Programming Enable / Parallel Memory
    0x80: "Self-timed write (internal timing, EEPROM/NVRAM)",
    0x0100: "Extended Addressing Mode?",
    0xC000: "Supports page-mode programming (EEPROM)",
    0x8000: "Upper page-mode programming (EEPROM)?",
    0x4000: "Lower page-mode programming (EEPROM)?",
    0x00400000: "Requires software unlock sequence before programming",
}

chip_info_meaning_ = {
    0x0006: "Standard EPROM pulse/verify routine",
    0x0000: "Default handling (no special polling required)",
    0x00E3: "AMD Flash toggle-bit & data polling (DQ6/DQ7 monitoring)?",
    0x00E4: "AMD Flash toggle-bit & data polling (DQ6/DQ7 monitoring)",
}

chip_info_meaning = {
    0x0006: "Generic EPROM classification",
    0x0000: "Generic Flash/EEPROM classification",
    0x00E3: "Specific AMD flash command set variant (0x00E3)",
    0x00E4: "Specific AMD flash command set variant (0x00E4)",
}
types = {
    0x01: "memory",
    #    0x02:"mcu",
    #    0x03:"pld",
    0x04: "sram",
    #    0x05:"logic",
    #    0x06:"nand"
}


def get_chip_info(chip_info):
    return chip_info_meaning.get(chip_info)


def get_flag_info(flag):
    return flags_meaning.get(flag)


def get_protocol_info(protocol):
    return protocol_descriptions.get(protocol)


def get_type_name(type):
    return types.get(type)


def create_ic_config(ic):
    package_details = int(ic.get("package_details"), 16)
    pin_count = get_pin_count(package_details)
    adapter = package_details & ADAPTER_MASK

    flags = int(ic.get("flags"), 16)
    can_erase = (flags & FLAG_ERASE_MASK) == FLAG_ERASE_MASK

    has_chip_id = (flags & FLAG_ID_MASK) == FLAG_ID_MASK
    chip_id = ic.get("chip_id")

    variant = int(ic.get("variant"), 16) & 0xFF
    variant_str = f"0x{variant:02X}"
    protocol_id = ic.get("protocol_id")
    mem_size = int(ic.get("code_memory_size"), 16)

    ic_type = get_type_name(int(ic.get("type"), 16))

    vdd, vcc, vpp = get_voltages(int(ic.get("voltages"), 16))

    pulse_delay = int(ic.get("pulse_delay"),16)
    flags_str = f"0x{flags:08X}"
    chip_info = ic.get("chip_info")
    pin_map = int(ic.get("pin_map"), 16)
    package_details = ic.get("package_details")

    return {
        "name": "-",
        "pin-count": pin_count,
        "adapter": adapter,
        "can-erase": can_erase,
        "has-chip-id": has_chip_id,
        "chip-id": chip_id,
        "variant": variant_str,
        "protocol-id": protocol_id,
        "memory-size": hex(mem_size),
        "type": ic_type,
        "voltages": {
            "vcc": vcc,
            "vpp": vpp,
        },
        "pulse-delay": pulse_delay,
        "flags": flags_str,
        "chip-info": chip_info,
        "package-details": package_details,
        "pin-map-org": pin_map,
    }


def get_voltages(voltages):
    vdd = vcc_voltages.get((voltages >> 12) & 0x0F)
    vcc = vcc_voltages.get((voltages >> 8) & 0x0F)
    vpp = vpp_voltages.get(voltages & 0xFF)

    return vdd, vcc, vpp


def get_pin_count(package_details):
    # Determine pin count from package_details (upper 8 bits)
    if package_details >= 0x01000000:  # if high byte is present
        return (package_details >> 24) & 0xFF
    return None


def decode_pin_map(pin_map_val, package_val):
    """
    Decode the pin_map and package_details values into package type, pin count,
    adapter requirement, and layout code.
    Returns a tuple: (package_type_code, pin_count, adapter_needed, layout_code)
    """
    # Split pin_map into upper and lower bytes
    package_type_code = (pin_map_val >> 8) & 0xFF
    layout_code = pin_map_val & 0xFF
    # Adapter needed if package_type_code is non-zero
    adapter_needed = package_type_code != 0
    return package_type_code, adapter_needed, layout_code


def explan_package_details(ic):

    pin_map_str = ic.get("pin_map")  # pin_map as string (hex format like 0x0016)
    pkg_detail_str = ic.get("package_details") or ic.get(
        "package_detail"
    )  # some versions might use package_detail

    # Convert hex strings to integers (if present)
    try:
        pin_map_val = int(pin_map_str, 16) if pin_map_str else None
    except ValueError:
        pin_map_val = None
    try:
        package_val = int(pkg_detail_str, 16) if pkg_detail_str else None
    except ValueError:
        package_val = None

    # Prepare output lines
    if pin_map_str or pkg_detail_str:
        # Print the raw values if available
        pm_display = pin_map_str if pin_map_str else "N/A"
        pkg_display = pkg_detail_str if pkg_detail_str else "N/A"
        print(f"Pin map: {pm_display}, Package details: {pkg_display}")
    else:
        print("(No pin_map or package_details data available)")

    # Decode if possible
    if pin_map_val is not None and package_val is not None:
        pin_count = get_pin_count(package_val)
        pkg_code, adapter_needed, layout_code = decode_pin_map(pin_map_val, package_val)
        # Format the decoded info
        # Package type code (upper bits of pin_map)
        print(f"Decoded:")
        print(f"- Package type code (upper bits of pin_map): 0x{pkg_code:02X}", end="")
        if pkg_code == 0:
            print(" (DIP – no adapter)")
        else:
            print(" (Adapter required)")
        # Pin count and adapter info
        if pin_count is not None:
            adapter_text = "Adapter needed" if adapter_needed else "No adapter needed"
            print(f"  - Pin count: {pin_count} pins ({adapter_text})")
        else:
            # If pin_count couldn't be determined
            print(f"  - Pin count: N/A")
        # Layout mapping code (lower bits of pin_map)
        print(f"  - Layout code (lower bits of pin_map): 0x{layout_code:02X}")
    else:
        # If we cannot decode (missing values)
        print(f"Decoded: (Insufficient data to decode pin map)")
    print()  # blank line between entries for readability


def explain_ic(ic_data):
    package_details = int(ic_data.get("package_details"), 16)
    # pin_count = get_pin_count(package_details)

    print(f"IC Name: {ic_data.get('name')}")
    # print(f"Pin Count: {pin_count} pins")

    # Protocol
    protocol = int(ic_data.get("protocol_id"), 16)
    proto_desc = get_protocol_info(protocol) or f"Unknown protocol (0x{protocol:02X})"

    print(f"Protocol: {proto_desc}")

    # Variant
    variant = ic_data.get("variant")
    print(
        f"Variant: {variant} (Sub-protocol selector, adjusts voltage/timing based on chip family)"
    )

    # Flags
    flags = int(ic_data.get("flags"), 16)
    print(f"Flags: 0x{flags:08X}")
    for bitmask, description in flags_meaning.items():
        if flags & bitmask:
            print(f"  - {description} (0x{(flags & bitmask):08X})")

    # Chip Info
    chip_info = int(ic_data.get("chip_info"), 16)
    chip_info_desc = get_chip_info(chip_info) or f"Unknown chip-info (0x{chip_info:04X}"

    print(f"Chip Info: {chip_info_desc}")

    # Voltages
    # voltages = ic_data.get('voltages', {})

    voltages = int(ic_data.get("voltages"), 16)
    vdd, vcc, vpp = get_voltages(voltages)
    print(f"Voltages: VDD={vdd}V, VCC={vcc}V, VPP={vpp}V")

    # Memory Size
    mem_size = int(ic_data.get("code_memory_size"), 16)
    print(f"Memory Size: {mem_size} bytes ({mem_size // 1024} KB)")

    # Chip ID
    if flags & FLAG_ID_MASK:
        print(f"Chip ID supported: Yes (ID = {ic_data.get('chip_id')})")
    else:
        print("Chip ID supported: No")

    explan_package_details(ic_data)
    print()
    print(" --- ")
    print()
