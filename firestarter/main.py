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
    from .utils import set_verbose
    from .__init__ import __version__ as version
    from .eprom_operations import read, write, erase, check_chip_id, blank_check, verify
    from .eprom_info import list_eproms, search_eproms, eprom_info
    from .database import init_db
    from .firmware import firmware
    from .hardware import hardware, config, read_vpe, read_vpp
except ImportError:
    from config import open_config
    from utils import set_verbose
    from __init__ import __version__ as version
    from eprom_operations import read, write, erase, check_chip_id, blank_check, verify
    from eprom_info import list_eproms, search_eproms, eprom_info
    from database import init_db
    from firmware import firmware
    from hardware import hardware, config, read_vpe, read_vpp


def main():
    signal.signal(signal.SIGINT, exit_gracefully)

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
    read_parser.add_argument(
        "-a", "--address", type=str, help="Read start address in dec/hex"
    )
    read_parser.add_argument(
        "-s", "--size", type=str, help="Size of the data to read in dec/hex"
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
    write_parser.add_argument(
        "-v", "--vpe-as-vpp", action="store_true", help="Use VPE as VPP voltage"
    )
    write_parser.add_argument("input_file", type=str, help="Input file name")

    # Verify command
    verify_parser = subparsers.add_parser(
        "verify", help="Verifies the content of an EPROM."
    )
    verify_parser.add_argument("eprom", type=str, help="The name of the EPROM.")
    verify_parser.add_argument(
        "-a", "--address", type=str, help="Verify start address in dec/hex"
    )
    verify_parser.add_argument("input_file", type=str, help="Input file name")

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
    info_parser.add_argument(
        "-c", "--config", action="store_true", help="Show EPROM config."
    )
    # Voltage commands
    vpp_parser = subparsers.add_parser("vpp", help="VPP voltage.")
    vpp_parser.add_argument("-t", "--timeout", type=int, help=argparse.SUPPRESS)
    vpe_parser = subparsers.add_parser("vpe", help="VPE voltage.")
    vpe_parser.add_argument("-t", "--timeout", type=int, help=argparse.SUPPRESS)

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
            force=args.force,
            address=args.address,
            size=args.size,
        )
    elif args.command == "write":
        return write(
            args.eprom,
            args.input_file,
            address=args.address,
            ignore_blank_check=args.ignore_blank_check,
            force=args.force,
            vpe_as_vpp=args.vpe_as_vpp,
        )
    elif args.command == "verify":
        return verify(
            args.eprom,
            args.input_file,
            address=args.address,
        )
    elif args.command == "blank":
        return blank_check(args.eprom)
    elif args.command == "erase":
        return erase(args.eprom)
    elif args.command == "id":
        return check_chip_id(args.eprom)
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
    return 0


def exit_gracefully(signum, frame):
    print("\n")
    print("Prosess interrupted.")
    sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
