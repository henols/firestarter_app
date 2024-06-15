#!/usr/bin/env python
"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.
"""

import sys
import time
import serial
import serial.tools.list_ports
import os
import json
import argparse

try:
    from . import database as db
except ImportError:
    import database as db

HOME_PATH = os.path.join(os.path.expanduser("~"), ".firestarter")
HINT_FILE = os.path.join(HOME_PATH, "programmer_hint")

BAUD_RATE = "115200"

STATE_READ = 1
STATE_WRITE = 2
STATE_ERASE = 3
STATE_CHECK_BLANK = 4
STATE_READ_VPE = 10
STATE_READ_VPP = 11
STATE_READ_VCC = 12
STATE_VERSION = 13
STATE_CONFIG = 14


def check_port(port, data):
    try:
        if verbose:
            print(f"Check port: {port}")

        ser = serial.Serial(
            port,
            BAUD_RATE,
            timeout=1.0,
            # inter_byte_timeout=0.1,
        )
        ser.write(data.encode("ascii"))
        ser.flush()

        if wait_for_response(ser)[0] == "OK":
            return ser
    except (OSError, serial.SerialException):
        pass

    return None


def find_programmer(data):
    if verbose:
        print("Config data:")
        print(data)
    # Check the hint file first
    if os.path.exists(HINT_FILE):
        with open(HINT_FILE, "r") as f:
            hinted_port = f.read().strip()
            serial_port = check_port(hinted_port, data)
            if serial_port:
                return serial_port

    # If the hinted port didn't work or wasn't available, search all ports
    ports = serial.tools.list_ports.comports()
    for port in ports:
        if "Arduino" in port.manufacturer or "FTDI" in port.manufacturer:
            serial_port = check_port(port.device, data)
            if serial_port:
                if not os.path.exists(HOME_PATH):
                    # Directory does not exist, create it
                    os.makedirs(HOME_PATH)
                # Update the hint file with the working port
                with open(HINT_FILE, "w") as f:
                    f.write(port.device)
                return serial_port
    return None


def wait_for_response(ser):
    while True:
        # time.sleep(1)
        byte_array = ser.readline()
        res = read_filterd_bytes(byte_array)
        if res and len(res) > 0:
            if "OK:" in res:
                msg = res[res.rfind("OK:") :]
                write_feedback(msg)
                return "OK", msg[3:].strip()
            elif "WARN:" in res:
                msg = res[res.rfind("WARN:") :]
                write_feedback(msg)
                return "WARN", msg[5:].strip()
            elif "ERROR:" in res:
                msg = res[res.rfind("ERROR:") :]
                write_feedback(msg)
                return "ERROR", msg[6:].strip()
            elif "DATA:" in res:
                msg = res[res.rfind("DATA:") :]
                write_feedback(msg)
                return "DATA", msg[5:].strip()
            elif "INFO:" in res:
                write_feedback(res[res.rfind("INFO:") :])
            # else:
            #     print(res)
        # elif len(byte_array) > 0:
        #     print(len(byte_array))


def write_feedback(msg):
    if verbose:
        print(msg)


def print_progress(p, from_address, to_address):
    if verbose:
        print(f"{p}%, address: 0x{from_address:X} - 0x{to_address:X} ")
    else:
        print(f"\r{p}%, address: 0x{from_address:X} - 0x{to_address:X} ", end="")


def read_filterd_bytes(byte_array):
    res = [b for b in byte_array if 32 <= b <= 126]
    if res:
        return "".join([chr(b) for b in res])
    else:
        return None


def list_eproms(all):
    if not all:
        print("Verified EPROMS in the database.")
    for ic in db.get_eproms(all):
        print(ic)


def search_eproms(text, all):
    print(f"Searching for: {text}")
    # if not all:
    #     print("Verified EPROMS in the database.")

    for ic in db.search_eprom(text, True):
        print(ic)


def eprom_info(name):
    eprom = db.get_eprom(name)
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
        if eprom["has-chip-id"]:
            print(f"Chip ID:\t{hex(eprom['chip-id'])}")
        print(f"VPP:\t\t{eprom['vpp']}")
    elif eprom["type"] == 2:
        print(f"Type:\t\tSRAM")
    print(f"Pulse delay:\t{eprom['pulse-delay']}µS")


def read_voltage(state):
    data = {}
    data["state"] = state
    data = json.dumps(data)
    # print(data)
    ser = find_programmer(data)
    if not ser:
        print("No programmer found")
        return
    ser.write("OK\n".encode("ascii"))
    type = "VPE"
    if state == STATE_READ_VCC:
        type = "VCC"
    if state == STATE_READ_VPP:
        type = "VPP"

    print(f"Reading {type} voltage")
    while (t := wait_for_response(ser))[0] == "DATA":
        print(f"\r{t[1]}", end="")
        ser.write("OK\n".encode("ascii"))


def firmware(file_name=None):
    data = {}
    data["state"] = STATE_VERSION
    data = json.dumps(data)
    # print(data)
    ser = find_programmer(data)
    if not ser:
        print("No programmer found")
        return
    ser.write("OK\n".encode("ascii"))
    print("Reading version")
    r, version = wait_for_response(ser)
    if r == "OK":
        print(f"Firmware version: {version}")
    else:
        print(r)


def config(vcc=None, r1=None, r2=None):
    data = {}
    data["state"] = STATE_CONFIG
    if vcc:
        data["vcc"] = vcc
    if r1:
        data["r1"] = r1
    if r2:
        data["r2"] = r2
    data = json.dumps(data)
    # print(data)
    ser = find_programmer(data)
    if not ser:
        print("No programmer found")
        return
    ser.write("OK\n".encode("ascii"))
    print("Reading configuration")
    r, version = wait_for_response(ser)
    if r == "OK":
        print(f"Config: {version}")
    else:
        print(r)


def read_chip(eprom, force, output_file, port=None):
    data = db.get_eprom(eprom)
    data.pop("name")
    data.pop("manufacturer")
    data.pop("verified")
    data["state"] = STATE_READ
    mem_size = data["memory-size"]
    data = json.dumps(data)
    # print(data)

    ser = find_programmer(data)
    if not ser:
        print("No programmer found")
        return
    print(f"Reading chip: {eprom}")
    if force:
        print("Force option is enabled.")
    if not output_file:
        output_file = f"{eprom}.bin"
    print(f"Output will be saved to: {output_file}")
    bytes_read = 0
    try:
        ser.write("OK\n".encode("ascii"))

        output_file = open(output_file, "wb")
        start_time = time.time()

        while True:
            match (t := wait_for_response(ser))[0]:

                case "DATA":
                    serial_data = ser.read(256)
                    output_file.write(serial_data)
                    bytes_read += 256
                    p = int(bytes_read / mem_size * 100)
                    print_progress(p, bytes_read - 256, bytes_read)
                    ser.write("OK\n".encode("ascii"))
                    ser.flush()
                case "OK":
                    print()
                    print("Finished reading data")
                    break
                case _:
                    print(f"Error reading data {t[1]}")
                    print(wait_for_response(ser)[1])
                    return

        end_time = time.time()
        # Calculate total duration
        total_duration = end_time - start_time
        print(f"File recived in {total_duration:.2f} seconds")

    except Exception as e:
        print("Error:", e)
    finally:
        output_file.close()
        ser.close()


def write_chip(eprom, force, input_file, port=None, address=None):
    data = db.get_eprom(eprom)
    if not data:
        print(f"Eprom {eprom} not found.")
        return
    if not os.path.exists(input_file):
        print(f"File {input_file} not found.")
        return
    file_size = os.path.getsize(input_file)
    data.pop("name")
    data.pop("manufacturer")
    data.pop("verified")
    # data["has-chip-id"] = False
    # data["can-erase"] = False
    if address:
        data["address"] = int(address, 0)

    data["state"] = STATE_WRITE
    json_data = json.dumps(data)
    mem_size = data["memory-size"]
    if not mem_size == file_size:
        print(f"The file size dont match the memory size")

    ser = find_programmer(json_data)
    if not ser:
        print("No programmer found")
        return

    print(f"Writing to chip: {eprom}")
    if force:
        print("Force option is enabled.")
    print(f"Reading from input file: {input_file}")
    bytes_sent = 0
    block_size = 256
    # Open the file to send
    with open(input_file, "rb") as f:
        start_time = time.time()
        print(f"Sending file {input_file} with block size {block_size} bytes")

        # Read the file and send in blocks
        while True:
            data = f.read(block_size)
            if not data:
                print("End of file reached")
                break
            # Send the data block
            ser.write((len(data) - 1).to_bytes(1))
            sent = ser.write(data)
            ser.flush()
            resp, info = wait_for_response(ser)
            if resp == "OK":
                bytes_sent += sent
                p = int(bytes_sent / mem_size * 100)
                print_progress(p, bytes_sent - 256, bytes_sent)
            elif resp == "ERROR":
                print()
                print(f"Error writing: {info}")
                return
            if bytes_sent == mem_size:
                break

    end_time = time.time()
    # Calculate total duration
    total_duration = end_time - start_time
    print(f"File sent in {total_duration:.2f} seconds")

    print("\nFile sent successfully!")


def main():
    global verbose

    parser = argparse.ArgumentParser(description="EPROM programer.")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose mode"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Read command
    read_parser = subparsers.add_parser("read", help="Reads the content from an EPROM.")
    read_parser.add_argument("eprom", type=str, help="The name of the EPROM.")
    read_parser.add_argument(
        "-f", "--force", action="store_true", help="Force the read operation"
    )
    read_parser.add_argument(
        "output_file", nargs="?", type=str, help="Output file name (optional)"
    )
    read_parser.add_argument(
        "-p", "--port", type=str, help="Serial port name (optional)"
    )

    # Write command
    write_parser = subparsers.add_parser(
        "write", help="Writes a binary file to an EPROM."
    )
    write_parser.add_argument("eprom", type=str, help="The name of the EPROM.")
    write_parser.add_argument(
        "-f", "--force", action="store_true", help="Force the write operation"
    )
    write_parser.add_argument(
        "-a", "--address", type=str, help="Address in dec/hex to start to write at"
    )
    write_parser.add_argument(
        "-p", "--port", type=str, help="Serial port name (optional)"
    )
    write_parser.add_argument("input_file", type=str, help="Input file name")

    # List command
    list_parser = subparsers.add_parser(
        "list", help="Lists all EPROMs in the database."
    )
    list_parser.add_argument(
        "-a", "--all", action="store_true", help="Includes non verifed EPROMS"
    )

    # Search command
    search_parser = subparsers.add_parser(
        "search", help="Searches EPROMs in the database."
    )
    search_parser.add_argument(
        "-a", "--all", action="store_true", help="Includes non verifed EPROMS"
    )
    search_parser.add_argument("text", type=str, help="Text to search for")

    # Info command
    info_parser = subparsers.add_parser("info", help="EPROM info.")
    info_parser.add_argument("eprom", type=str, help="EPROM name.")

    vpe_parser = subparsers.add_parser("vpe", help="VPE voltage.")
    vpp_parser = subparsers.add_parser("vpp", help="VPP voltage.")
    vcc_parser = subparsers.add_parser("vcc", help="VCC voltage.")

    fw_parser = subparsers.add_parser("fw", help="FIRMWARE version.")
    config_parser = subparsers.add_parser(
        "config", help="Handles CONFIGURATION values."
    )
    config_parser.add_argument(
        "-v", "--vcc", type=float, help="Set Arduino VCC voltage."
    )
    config_parser.add_argument(
        "-r1", "--r16", type=int, help="Set R16 resistance, resistor connected to VPE"
    )
    config_parser.add_argument(
        "-r2",
        "--r14r15",
        type=int,
        help="Set R14/R15 resistance, resistor connected to GND",
    )
    if len(sys.argv) == 1:
        args = parser.parse_args([ "--help"])
    else:
        args = parser.parse_args()

    verbose = args.verbose
    db.init()

    if args.command == "list":
        list_eproms(args.all)
    elif args.command == "info":
        eprom_info(args.eprom)
    elif args.command == "search":
        search_eproms(args.text, args.all)
    elif args.command == "read":
        read_chip(args.eprom, args.force, args.output_file, port=None)
    elif args.command == "write":
        write_chip(
            args.eprom, args.force, args.input_file, port=None, address=args.address
        )
    elif args.command == "vpe":
        read_voltage(STATE_READ_VPE)
    elif args.command == "vpp":
        read_voltage(STATE_READ_VPP)
    elif args.command == "vcc":
        read_voltage(STATE_READ_VCC)
    elif args.command == "fw":
        firmware()
    elif args.command == "config":
        config(args.vcc, args.r16, args.r14r15)


if __name__ == "__main__":
    main()
