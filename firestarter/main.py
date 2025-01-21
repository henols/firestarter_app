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

try:
    from .config import open_config
    from .utils import set_verbose
    from .__init__ import __version__ as version
    from .eprom_operations import (
        read,
        write,
        erase,
        check_chip_id,
        blank_check,
        verify,
        dev_read,
        build_flags,
    )
    from .eprom_info import list_eproms, search_eproms, eprom_info
    from .database import init_db
    from .firmware import firmware
    from .hardware import (
        hardware,
        config,
        read_vpe,
        read_vpp,
        dev_address,
        dev_registers,
    )
except ImportError:
    from config import open_config
    from utils import set_verbose
    from __init__ import __version__ as version
    from eprom_operations import (
        read,
        write,
        erase,
        check_chip_id,
        blank_check,
        verify,
        dev_read,
        build_flags,
    )

    from eprom_info import list_eproms, search_eproms, eprom_info
    from database import init_db
    from firmware import firmware
    from hardware import (
        hardware,
        config,
        read_vpe,
        read_vpp,
        dev_address,
        dev_registers,
    )


def create_read_args(parser):
    read_parser = parser.add_parser("read", help="Reads the content from an EPROM.")
    read_parser.add_argument("eprom", type=str, help="The name of the EPROM.")
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
    write_parser.add_argument("eprom", type=str, help="The name of the EPROM.")
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
        "-v", "--vpe-as-vpp", action="store_true", help="Use VPE as VPP voltage"
    )
    write_parser.add_argument("input_file", type=str, help="Input file name")


def create_verify_args(parser):
    verify_parser = parser.add_parser(
        "verify", help="Verifies the content of an EPROM."
    )
    verify_parser.add_argument("eprom", type=str, help="The name of the EPROM.")
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
    blank_check_parser.add_argument("eprom", type=str, help="The name of the EPROM.")
    blank_check_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force, even if the VPP or chip id doesn't match.",
    )


def create_erase_parser(parser):
    erase_parser = parser.add_parser("erase", help="Erase an EPROM, if supported.")
    erase_parser.add_argument("eprom", type=str, help="The name of the EPROM.")
    erase_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force, even if the VPP or chip id doesn't match.",
    )


def create_id_args(parser):
    id_parser = parser.add_parser("id", help="Checks an EPROM, if supported.")
    id_parser.add_argument("eprom", type=str, help="The name of the EPROM.")
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


def create_info_args(parser):
    info_parser = parser.add_parser("info", help="EPROM info.")
    info_parser.add_argument("eprom", type=str, help="EPROM name.")
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
    read_parser.add_argument("eprom", type=str, help="The name of the EPROM.")
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
    addr_parser = subparsers.add_parser(
        "addr", help="Direct access to address lines and control register."
    )


def build_arg_flags(args):
    ignore_blank_check = (
        args.ignore_blank_check if "ignore_blank_check" in args else False
    )
    force = args.force if "force" in args else False
    vpe_as_vpp = args.vpe_as_vpp if "vpe_as_vpp" in args else False
    return build_flags(ignore_blank_check, force, vpe_as_vpp)


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

    if len(sys.argv) == 1:
        parser.print_help()
        return 1

    args = parser.parse_args()

    init_db()
    open_config()
    set_verbose(args.verbose)
    if args.verbose:
        print(f"Firestarter version: {version}")

    # Command dispatch
    if args.command == "list":
        return list_eproms(args.verified)
    elif args.command == "info":
        return eprom_info(args.eprom, args.config)
    elif args.command == "search":
        return search_eproms(args.text)
    elif args.command == "read":

        return read(
            args.eprom,
            args.output_file,
            flags=build_arg_flags(args),
            address=args.address,
            size=args.size,
        )
    elif args.command == "write":
        return write(
            args.eprom,
            args.input_file,
            address=args.address,
            flags=build_arg_flags(args),
        )
    elif args.command == "verify":
        return verify(
            args.eprom,
            args.input_file,
            address=args.address,
            flags=build_arg_flags(args),
        )
    elif args.command == "blank":
        return blank_check(args.eprom, flags=build_arg_flags(args))
    elif args.command == "erase":
        return erase(args.eprom, flags=build_arg_flags(args))
    elif args.command == "id":
        return check_chip_id(args.eprom, flags=build_arg_flags(args))
    elif args.command == "vpe":
        return read_vpe(args.timeout)
    elif args.command == "vpp":
        return read_vpp(args.timeout)
    elif args.command == "fw":
        return firmware(
            args.install,
            avrdude_path=args.avrdude_path,
            avrdude_config_path=args.avrdude_config_path,
            port=args.port,
            board=args.board,
        )
    elif args.command == "hw":
        return hardware()
    elif args.command == "config":
        return config(args.rev, args.r16, args.r14r15)
    elif args.command == "dev":
        if args.dev_command == "read":
            return dev_read(
                args.eprom,
                address=args.address,
                size=args.size,
                force=args.force,
            )
        elif args.dev_command == "reg":
            return dev_registers(args.msb, args.lsb, args.ctrl_reg)
        elif args.dev_command == "addr":
            if args.write != args.read:
                print("Specify either read or write flag")
                return 0
            return dev_address(args.eprom, args.address, args.ctrl_reg, args.read)
    return 0


def exit_gracefully(signum, frame):
    print("\n")
    print("Prosess interrupted.")
    sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
