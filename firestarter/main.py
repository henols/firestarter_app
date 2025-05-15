#!/usr/bin/env python
"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Main CLI Handler for Firestarter Project
"""

import sys
import argparse
import signal
import logging
import platform
import argcomplete
from argcomplete.completers import BaseCompleter

from firestarter.config import ConfigManager  # Refactored
from firestarter.constants import *
from firestarter import __version__ as version
from firestarter.eprom_operations import EpromOperator, build_flags 
from firestarter.eprom_info import EpromConsolePresenter # Refactored
from firestarter.database import EpromDatabase  # Refactored
from firestarter.firmware import FirmwareManager  # Refactored
from firestarter.hardware import HardwareManager  # Refactored

logger = logging.getLogger("Firestarter")

# Import helper printing functions that would ideally be in a dedicated cli_display module
from firestarter.eprom_info import print_eprom_list_table


class EpromCompleter(BaseCompleter):
    def __init__(self):
        db_instance = EpromDatabase()  # Initialize/get instance
        self.allowed_eproms = allowed_eproms()

    def __call__(self, prefix, **kwargs):
        return [c for c in self.allowed_eproms]


def allowed_eproms():
    # Load or define your allowed eprom list (cache if necessary)
    db_instance = EpromDatabase()
    allowed_eproms_data = db_instance.get_eproms(False)  # e.g., from a file or constant
    names = []
    for eprom in allowed_eproms_data:
        names.append(eprom["name"])
    return names


def eprom_validator(eprom, prefix):
    return eprom.lower().startswith(prefix.lower())


def add_eprom_completer(parser):
    # Add the completer to the parser
    eprom = parser.add_argument(
        "eprom",
        type=str,
        help="The name of the EPROM.",
    )
    eprom.completer = EpromCompleter()


def create_read_args(parser):
    read_parser = parser.add_parser("read", help="Reads the content from an EPROM.")
    add_eprom_completer(read_parser)
    read_parser.add_argument(
        "output_file",
        nargs="?",
        type=str,
        help="Output file name (optional), defaults to the EPROM_NAME.bin",
    )
    read_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force, even if the chip id doesn't match.",
    )
    read_parser.add_argument(
        "-a", "--address", type=str, help="Read start address in dec/hex"
    )
    read_parser.add_argument(
        "-s", "--size", type=str, help="Size of the data to read in dec/hex"
    )


def create_write_args(parser):
    write_parser = parser.add_parser("write", help="Writes a binary file to an EPROM.")
    add_eprom_completer(write_parser)
    write_parser.add_argument(
        "-b",
        "--ignore-blank-check",
        action="store_true",
        help="Ignore blank check before write (and skip erase).",
    )
    write_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force, even if the VPP or chip id doesn't match.",
    )
    write_parser.add_argument(
        "-a", "--address", type=str, help="Write start address in dec/hex"
    )
    write_parser.add_argument(
        "--vpe-as-vpp", action="store_true", help="Use VPE as VPP voltage"
    )
    write_parser.add_argument("input_file", type=str, help="Input file name")


def create_verify_args(parser):
    verify_parser = parser.add_parser(
        "verify", help="Verifies the content of an EPROM."
    )
    add_eprom_completer(verify_parser)
    verify_parser.add_argument(
        "-a", "--address", type=str, help="Verify start address in dec/hex"
    )
    verify_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force, even if the VPP or chip id doesn't match.",
    )
    verify_parser.add_argument("input_file", type=str, help="Input file name")


def create_blank_check_args(parser):
    blank_check_parser = parser.add_parser("blank", help="Checks if an EPROM is blank.")
    add_eprom_completer(blank_check_parser)
    blank_check_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force, even if the VPP or chip id doesn't match.",
    )


def create_erase_parser(parser):
    erase_parser = parser.add_parser("erase", help="Erase an EPROM, if supported.")
    add_eprom_completer(erase_parser)
    erase_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force, even if the VPP or chip id doesn't match.",
    )
    erase_parser.add_argument(
        "-b",
        "--blank-check",
        action="store_false",
        default=True,
        dest="ignore_blank_check",
        help="Do a blank check after erase.",
    )


def create_id_args(parser):
    id_parser = parser.add_parser("id", help="Checks an EPROM, if supported.")
    add_eprom_completer(id_parser)
    id_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force, even if the VPP is not correct.",
    )


def create_voltage_args(parser):
    vpp_parser = parser.add_parser("vpp", help="VPP voltage.")
    vpp_parser.add_argument("-t", "--timeout", type=int, help=argparse.SUPPRESS)

    vpe_parser = parser.add_parser("vpe", help="VPE voltage.")
    vpe_parser.add_argument("-t", "--timeout", type=int, help=argparse.SUPPRESS)


def create_firnware_args(parser):
    fw_parser = parser.add_parser("fw", help="Firmware version.")
    fw_parser.add_argument(
        "-i",
        "--install",
        action="store_true",
        help="Try to install the latest firmware.",
    )
    fw_parser.add_argument(
        "-b",
        "--board",
        type=str,
        default="uno",
        choices=["uno", "leonardo", ],
        help="Microcontroller board (optional), defaults to 'uno'.",
    )
    fw_parser.add_argument(
        "-p",
        "--avrdude-path",
        type=str,
        help="Full path to avrdude (optional), set if avrdude is not found.",
    )
    fw_parser.add_argument(
        "-c",
        "--avrdude-config-path",
        type=str,
        help="Full path to avrdude config (optional), set if avrdude version is 6.3 or not found.",
    )
    fw_parser.add_argument("--port", type=str, help="Serial port name (optional)")
    fw_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Will install firmware even if the version is the same.",
    )


def create_info_args(parser):
    info_parser = parser.add_parser("info", help="EPROM info.")
    add_eprom_completer(info_parser)
    info_parser.add_argument(
        "-c", "--config", action="store_true", help="Show EPROM config."
    )


def create_list_args(parser):
    list_parser = parser.add_parser("list", help="List all EPROMs in the database.")
    list_parser.add_argument(
        "-v", "--verified", action="store_true", help="Only shows verified EPROMs"
    )


def create_search_args(parser):
    search_parser = parser.add_parser(
        "search", help="Search for EPROMs in the database."
    )
    search_parser.add_argument("text", type=str, help="Text to search for")


def create_config_args(parser):
    config_parser = parser.add_parser("config", help="Handles CONFIGURATION values.")
    config_parser.add_argument(
        "--rev",
        type=float,
        help="WARNING Overrides hardware revision (0-2), only use with HW mods. -1 disables override.",
    )
    config_parser.add_argument(
        "-r1", "--r16", type=int, help="Set R16 resistance, resistor connected to VPE"
    )
    config_parser.add_argument(
        "-r2",
        "--r14r15",
        type=int,
        help="Set R14/R15 resistance, resistors connected to GND",
    )


def create_dev_args(parser):
    dev_parser = parser.add_parser(
        "dev", help="Debug command for development purposes."
    )

    subparsers = dev_parser.add_subparsers(dest="dev_command", required=True)

    read_parser = subparsers.add_parser(
        "read", help="Reads the content from an EPROM and prints data to console."
    )
    add_eprom_completer(read_parser)
    read_parser.add_argument(
        "-a", "--address", type=str, help="Read start address in dec/hex"
    )
    read_parser.add_argument(
        "-s", "--size", type=str, help="Size of the data to read in dec/hex"
    )
    read_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force read, even if the chip id doesn't match.",
    )
    reg_parser = subparsers.add_parser(
        "reg", help="Direct access to registers: MSB, LSB and control register."
    )
    reg_parser.add_argument("msb", type=str, help="MSB in dec/hex")
    reg_parser.add_argument("lsb", type=str, help="LSB in dec/hex")
    reg_parser.add_argument("ctrl", type=str, help="Control register in dec/hex")
    create_oe_ce_args(reg_parser)

    addr_parser = subparsers.add_parser(
        "addr", help="Direct access to address lines and control register."
    )
    add_eprom_completer(addr_parser)
    addr_parser.add_argument("address", type=str, help="Address in dec/hex")
    create_oe_ce_args(addr_parser)


def create_oe_ce_args(parser):
    oe_group = parser.add_argument_group(
        "Output enable", description="Controls OE pin, defaults to output enable."
    ).add_mutually_exclusive_group()
    # oe_group.add_argument(
    #     "-o", "--output-enable", action="store_true", help="Output, pulls OE pin low."
    # )

    oe_group.add_argument(
        "-i", "--input-enable", action="store_true", help="Input, pulls OE pin high."
    )

    ce_group = parser.add_argument_group(
        "Chip enable", description="Controls CE pin, defaults to chip enable."
    ).add_mutually_exclusive_group()
    # ce_group.add_argument(
    #     "-e", "--chip-enable", action="store_true", help="Enable, pulls CE pin low."
    # )

    ce_group.add_argument(
        "-d", "--chip-disable", action="store_true", help="Disable, pulls CE pin high."
    )


def build_arg_flags(args):
    ignore_blank_check = (
        args.ignore_blank_check if "ignore_blank_check" in args else False
    )

    force = args.force if "force" in args else False
    vpe_as_vpp = args.vpe_as_vpp if "vpe_as_vpp" in args else False
    flags = build_flags(ignore_blank_check, force, vpe_as_vpp)

    if "input_enable" in args:
        flags |= 0 if args.input_enable else FLAG_OUTPUT_ENABLE
    if "chip_disable" in args:
        flags |= 0 if args.chip_disable else FLAG_CHIP_ENABLE

    return flags


def main():
    signal.signal(signal.SIGINT, exit_gracefully)

    parser = argparse.ArgumentParser(
        description="EPROM programmer for Arduino and Relatively-Universal-ROM-Programmer shield."
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose mode"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"Firestarter version: {version}",
        help="Show the Firestarter version and exit.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    create_read_args(subparsers)
    create_write_args(subparsers)
    create_verify_args(subparsers)
    create_erase_parser(subparsers)
    create_blank_check_args(subparsers)
    create_id_args(subparsers)

    create_search_args(subparsers)
    create_list_args(subparsers)
    create_info_args(subparsers)

    create_voltage_args(subparsers)
    hw_parser = subparsers.add_parser("hw", help="Hardware revision.")
    create_firnware_args(subparsers)
    create_config_args(subparsers)
    create_dev_args(subparsers)

    argcomplete.autocomplete(parser, validator=eprom_validator)

    if len(sys.argv) == 1:
        parser.print_help()
        return 1

    args = parser.parse_args()

    # Initialize ConfigManager (Singleton)
    config_manager = ConfigManager()

    # Setup logging based on verbosity
    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="[%(levelname)s:%(name)s:%(lineno)d] %(message)s",
        )
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Initialize EpromDatabase (Singleton)
    db_instance = EpromDatabase()
    # Initialize EpromOperator
    eprom_operator = EpromOperator(db_instance)
    # Initialize HardwareManager
    hardware_manager = HardwareManager()
    # Initialize FirmwareManager
    firmware_manager = FirmwareManager(config_manager)
    # Initialize EpromInfoProvider
    eprom_presenter = EpromConsolePresenter(db_instance)

    logger.debug(f"Firestarter version: {version}")
    logger.debug(f"Running on Python: { platform.python_version()}")
    logger.debug(f"Platform: {platform.system()} {platform.release()}")
    logger.debug(f"Architecture: {platform.architecture()[0]}")
    logger.debug(f"OS: {platform.platform()}")

    # Command dispatch
    if args.command == "list":
        eprom_data_list = eprom_presenter.get_all_eproms_data(args.verified)
        if eprom_data_list:
            print_eprom_list_table(
                eprom_data_list, eprom_presenter.spec_builder
            )
            return 0
        return 1
    elif args.command == "info":
        details = eprom_presenter.prepare_detailed_eprom_data(
            args.eprom, include_export_config=args.config
        )
        if details:
            # EpromInfoProvider now handles displaying, including the export config if requested by args.config
            eprom_presenter.present_eprom_details(details, show_export_config=args.config)
            return 0
        return 1
    elif args.command == "search":
        search_results = eprom_presenter.search_eproms_by_name(args.text)
        if search_results:
            print_eprom_list_table(search_results, eprom_presenter.spec_builder)
            return 0
        return 1
    elif args.command == "read":
        return (
            1
            if not eprom_operator.read_eprom(
                args.eprom,
                args.output_file,
                operation_flags=build_arg_flags(args),
                address_str=args.address,
                size_str=args.size,
            )
            else 0
        )
    elif args.command == "write":
        return (
            1
            if not eprom_operator.write_eprom(
                args.eprom,
                args.input_file,
                address_str=args.address,
                operation_flags=build_arg_flags(args),
            )
            else 0
        )
    elif args.command == "verify":
        return (
            1
            if not eprom_operator.verify_eprom(
                args.eprom,
                args.input_file,
                address_str=args.address,
                operation_flags=build_arg_flags(args),
            )
            else 0
        )
    elif args.command == "blank":
        return (
            1
            if not eprom_operator.check_eprom_blank(
                args.eprom, operation_flags=build_arg_flags(args)
            )
            else 0
        )
    elif args.command == "erase":
        return (
            1
            if not eprom_operator.erase_eprom(
                args.eprom, operation_flags=build_arg_flags(args)
            )
            else 0
        )
    elif args.command == "id":
        return (
            1
            if not eprom_operator.check_eprom_id(
                args.eprom, operation_flags=build_arg_flags(args)
            )
            else 0
        )
    elif args.command == "vpe":
        return 1 if not hardware_manager.read_vpe_voltage(args.timeout) else 0
    elif args.command == "vpp":
        return 1 if not hardware_manager.read_vpp_voltage(args.timeout) else 0
    elif args.command == "fw":
        return (
            1
            if not firmware_manager.manage_firmware_update(
                install_flag=args.install,
                avrdude_path_override=args.avrdude_path,
                avrdude_config_override=args.avrdude_config_path,
                cli_port_override=args.port,
                cli_board_override=args.board,
                force_install=args.force,
            )
            else 0
        )
    elif args.command == "hw":
        return 1 if not hardware_manager.get_hardware_revision() else 0
    elif args.command == "config":
        return (
            1
            if not hardware_manager.set_hardware_config(args.rev, args.r16, args.r14r15)
            else 0
        )
    elif args.command == "dev":
        if args.dev_command == "read":
            return (
                1
                if not eprom_operator.dev_read_eprom(
                    args.eprom,
                    address_str=args.address,
                    size_str=args.size,
                    operation_flags=build_arg_flags(args),
                )
                else 0
            )
        elif args.dev_command == "reg":
            return (
                1
                if not eprom_operator.dev_set_registers(
                    args.msb, args.lsb, args.ctrl, flags=build_arg_flags(args)
                )
                else 0
            )
        elif args.dev_command == "addr":
            return (
                1
                if not eprom_operator.dev_set_address_mode(
                    args.eprom, args.address, operation_flags=build_arg_flags(args)
                )
                else 0
            )
    return 0


def exit_gracefully(signum, frame):
    logger.warning("\nProsess interrupted.")
    sys.exit(1)


if __name__ == "__main__":
    if sys.version_info < (3, 9):
        sys.exit(
            "Error: Firestarter requires Python 3.9 or higher. Please update your Python version."
        )

    sys.exit(main())
