#!/usr/bin/env python
"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.
"""

"""
Main CLI Handler for Firestarter Project
"""

import sys
import argparse
import signal

try:
    from .config import open_config
    from .__init__ import __version__ as version
    from .eprom_ops import (
        list_eproms,
        search_eproms,
        eprom_info,
        read_chip,
        write_chip,
        erase,
        check_chip_id,
        blank_check,
        read_vpe_voltage,
        read_vpp_voltage,
    )
    from .database import init_db
    from .firmware import firmware, hardware, rurp_config
except ImportError:
    from config import open_config
    from __init__ import __version__ as version
    from eprom_ops import (
        list_eproms,
        search_eproms,
        eprom_info,
        read_chip,
        write_chip,
        erase,
        check_chip_id,
        blank_check,
        read_vpe_voltage,
        read_vpp_voltage,
    )
    from database import init_db
    from firmware import firmware, hardware, rurp_config


def main():
    global verbose

    parser = argparse.ArgumentParser(
        description="EPROM programmer for Arduino UNO and Relatively-Universal-ROM-Programmer shield."
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

    # Read command
    read_parser = subparsers.add_parser("read", help="Reads the content from an EPROM.")
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
        help="Force read, even if the chip id doesn't match.",
    )

    # Write command
    write_parser = subparsers.add_parser(
        "write", help="Writes a binary file to an EPROM."
    )
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
        help="Force write, even if the VPP or chip id doesn't match.",
    )
    write_parser.add_argument(
        "-a", "--address", type=str, help="Write start address in dec/hex"
    )
    write_parser.add_argument("input_file", type=str, help="Input file name")

    blank_check_parser = subparsers.add_parser(
        "blank", help="Checks if an EPROM is blank."
    )
    blank_check_parser.add_argument("eprom", type=str, help="The name of the EPROM.")

    erase_parser = subparsers.add_parser("erase", help="Erase an EPROM, if supported.")
    erase_parser.add_argument("eprom", type=str, help="The name of the EPROM.")

    id_parser = subparsers.add_parser("id", help="Checks an EPROM, if supported.")
    id_parser.add_argument("eprom", type=str, help="The name of the EPROM.")

    # List command
    list_parser = subparsers.add_parser("list", help="List all EPROMs in the database.")
    list_parser.add_argument(
        "-v", "--verified", action="store_true", help="Only shows verified EPROMs"
    )

    # Search command
    search_parser = subparsers.add_parser(
        "search", help="Search for EPROMs in the database."
    )
    search_parser.add_argument("text", type=str, help="Text to search for")

    # Info command
    info_parser = subparsers.add_parser("info", help="EPROM info.")
    info_parser.add_argument("eprom", type=str, help="EPROM name.")

    # Voltage commands
    vpp_parser = subparsers.add_parser("vpp", help="VPP voltage.")
    vpe_parser = subparsers.add_parser("vpe", help="VPE voltage.")

    # Hardware command
    hw_parser = subparsers.add_parser("hw", help="Hardware revision.")

    # Firmware command
    fw_parser = subparsers.add_parser("fw", help="Firmware version.")
    fw_parser.add_argument(
        "-i",
        "--install",
        action="store_true",
        help="Try to install the latest firmware.",
    )
    fw_parser.add_argument(
        "-p",
        "--avrdude-path",
        type=str,
        help="Full path to avrdude (optional), set if avrdude is not found.",
    )
    fw_parser.add_argument("--port", type=str, help="Serial port name (optional)")

    config_parser = subparsers.add_parser(
        "config", help="Handles CONFIGURATION values."
    )
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

    if len(sys.argv) == 1:
        parser.print_help()
        return 1

    args = parser.parse_args()

    init_db()
    # Configuration and Verbose
    open_config()
    verbose = args.verbose

    # Command dispatch
    if args.command == "list":
        list_eproms(args.verified)
    elif args.command == "info":
        eprom_info(args.eprom)
    elif args.command == "search":
        search_eproms(args.text)
    elif args.command == "read":
        read_chip(args.eprom, args.output_file, force=args.force)
    elif args.command == "write":
        write_chip(
            args.eprom,
            args.input_file,
            address=args.address,
            ignore_blank_check=args.ignore_blank_check,
            force=args.force,
        )
    elif args.command == "blank":
        blank_check(args.eprom)
    elif args.command == "erase":
        erase(args.eprom)
    elif args.command == "id":
        check_chip_id(args.eprom)
    elif args.command == "vpe":
        read_vpe_voltage()
    elif args.command == "vpp":
        read_vpp_voltage()
    elif args.command == "fw":
        firmware(args.install, args.avrdude_path, args.port)
    elif args.command == "hw":
        hardware()
    elif args.command == "config":
        rurp_config(args.rev, args.r16, args.r14r15)
    return 0


def exit_gracefully(signum, frame):
    sys.exit(1)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, exit_gracefully)
    sys.exit(main())
