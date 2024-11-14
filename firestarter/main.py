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
import requests
import signal

try:
    from .__init__ import __version__ as version
    from . import database as db
    from . import ic_layout as ic
    from .avr_tool import Avrdude
except ImportError:
    from __init__ import __version__ as version
    import database as db
    from avr_tool import Avrdude
    import ic_layout as ic


BAUD_RATE = "115200"

STATE_READ = 1
STATE_WRITE = 2
STATE_ERASE = 3
STATE_CHECK_BLANK = 4

STATE_READ_VPP = 11
STATE_READ_VPE = 12
STATE_FW_VERSION = 13
STATE_CONFIG = 14
STATE_HW_VERSION = 15

FIRESTARTER_RELEASE_URL = (
    "https://api.github.com/repos/henols/firestarter/releases/latest"
)

HOME_PATH = os.path.join(os.path.expanduser("~"), ".firestarter")
CONFIG_FILE = os.path.join(HOME_PATH, "config.json")

BUFFER_SIZE = 512


def open_config():
    global config
    config = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as file:
            config = json.load(file)


def save_config():
    if not os.path.exists(HOME_PATH):
        os.makedirs(HOME_PATH)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)


def check_port(port, data):
    try:
        if verbose:
            print(f"Check port: {port}")

        ser = serial.Serial(
            port,
            BAUD_RATE,
            timeout=1.0,
        )
        time.sleep(2)
        ser.write(data.encode("ascii"))
        ser.flush()

        res, msg = wait_for_response(ser)
        if res == "OK":
            if verbose:
                print(f"Programmer: {msg}")
            return ser
        else:
            print(f"{res} - {msg}")
    except (OSError, serial.SerialException):
        pass

    return None


def find_comports(port=None):
    ports = []
    if not port == None:
        ports.append(port)
        return ports
    if "port" in config.keys():
        ports.append(config["port"])

    serial_ports = serial.tools.list_ports.comports()
    for port in serial_ports:
        if (
            port.manufacturer
            and ("Arduino" in port.manufacturer or "FTDI" in port.manufacturer)
            and not port.device in ports
        ):
            ports.append(port.device)
    return ports


def find_programmer(data, port=None):
    if "manufacturer" in data:
        data.pop("manufacturer")
    if "verified" in data:
        data.pop("verified")
    if "pin-map" in data:
        data.pop("pin-map")
    if "name" in data:
        data.pop("name")
    if "flags" in data:
        data.pop("flags")
    if "protocol-id" in data:
        data.pop("protocol-id")

    if verbose:
        data["verbose"] = True
        print("Config data:")
        print(data)

    json_data = json.dumps(data, separators=(",", ":"))

    ports = find_comports(port)
    for port in ports:
        serial_port = check_port(port, json_data)

        if serial_port:
            config["port"] = port
            save_config()
            if verbose:
                print(f"Found programmer at port: {port}")
            else:
                print(f"Connected to programmer")
            return serial_port
    print("No programmer found")
    return None


def wait_for_response(ser):
    timeout = time.time()
    while True:
        # time.sleep(1)
        if ser.in_waiting > 0:
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
                timeout = time.time()

        if timeout + 2 < time.time():
            return "TIMEOUT_ERROR", "Timeout (expecting response)"


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


def list_eproms(verified):
    if not verified:
        print("Verified EPROMs in the database.")
    for ic in db.get_eproms(verified):
        print(ic)


def search_eproms(text):
    print(f"Searching for: {text}")
    # if not all:
    #     print("Verified EPROMs in the database.")

    for ic in db.search_eprom(text, True):
        print(ic)


def eprom_info(name):
    eprom = db.get_eprom(name)
    if not eprom:
        print(f"Eprom {name} not found.")
        return

    ic.print_chip_info(eprom, verbose)
    if verbose:
        print()
        print("Config:")
        print(eprom)

def read_voltage(state):
    data = {}
    data["state"] = state

    type = "VPE"
    if state == STATE_READ_VPP:
        type = "VPP"
    print(f"Reading {type} voltage")
    ser = find_programmer(data)
    if not ser:
        return 1
    resp, info = wait_for_response(ser)
    if not resp == "OK":
        print()
        print(f"Error reading {type} voltage: {info}")
        return 1
    ser.write("OK".encode("ascii"))

    while (t := wait_for_response(ser))[0] == "DATA":
        print(f"\r{t[1]}", end="")
        ser.write("OK".encode("ascii"))
    ser.close()


def hardware():
    # if not install:
    data = {}
    data["state"] = STATE_HW_VERSION
    print("Reading hardware revision")

    ser = find_programmer(data)
    if not ser:
        return 1

    r, version = wait_for_response(ser)
    ser.close()

    if r == "OK":
        print(f"Hardware revision: {version}")
    else:
        print(f"{r}: {version}")
        return 1
    return 0


def firmware(install, avrdude_path, port):
    latest, selected_port, url = firmware_check(port)
    if not latest and not install and not url:
        return 1
    if not latest and install:
        if not url:
            latest_version, url = latest_firmware()
            print(f"Trying to install firmware version: {latest_version}")
        if not selected_port:
            selected_port = port
        return install_firmware(url, avrdude_path, selected_port)
    return 0


def firmware_check(port=None):
    data = {}
    data["state"] = STATE_FW_VERSION
    print("Reading firmware version")

    ser = find_programmer(data, port)
    if not ser:
        return False, None, None
    ser.write("OK".encode("ascii"))
    r, version = wait_for_response(ser)
    ser.close()

    if r == "OK":
        print(f"Firmware version: {version}")
    else:
        print(r)
        return False, None, None
    latest_version, url = latest_firmware()
    major, minor, patch = version.split(".")

    major_l, minor_l, patch_l = latest_version.split(".")

    if (
        int(major_l) > int(major)
        or (int(major_l) == int(major) and int(minor_l) > int(minor))
        or (
            int(major_l) == int(major)
            and int(minor_l) == int(minor)
            and int(patch_l) > int(patch)
        )
    ):
        print(f"New version available: {latest_version}")
        return False, ser.portstr, url
    else:
        print("You have the latest version")
        return True, None, None


def install_firmware(url, avrdude_path, preferred_port=None):

    if preferred_port:
        ports = [preferred_port]
    else:
        ports = find_comports()
        if len(ports) == 0:
            print("No Arduino found")
            return 1

    for port in ports:
        try:
            if not avrdude_path:
                if "avrdude-path" in config.keys():
                    avrdude_path = config["avrdude-path"]

            a = Avrdude(
                partno="ATmega328P",
                programmer_id="arduino",
                baud_rate="115200",
                port=port,
                avrdudePath=avrdude_path,
            )
        except FileNotFoundError:
            print("Avrdude not found")
            print("Full path to avrdude needs to be provided --avrdude-path")
            return 1

        output, error, returncode = a.testConnection()
        if returncode == 0:
            print(f"Found programmer at port: {port}")
            print("Downloading firmware...")
            response = requests.get(url)
            if not response.status_code == 200:
                print("Error downloading firmware")
                return
            if not os.path.exists(HOME_PATH):
                os.makedirs(HOME_PATH)
            firmware_path = os.path.join(HOME_PATH, "firmware.hex")
            with open(firmware_path, "wb") as f:
                f.write(response.content)
            print("Firmware downloaded")
            print("Installing firmware")
            output, error, returncode = a.flashFirmware(firmware_path)
            if returncode == 0:
                print("Firmware updated")
            else:
                print("Error updating firmware")
                print(str(error, "ascii"))
                return 1
            if avrdude_path:
                config["avrdude-path"] = avrdude_path
                save_config()
            if preferred_port:
                config["port"] = preferred_port
                save_config()
            return 0
        else:
            print(f"Error connecting to programmer at port: {port}")
            print(str(error, "ascii"))
            continue

    print("Please reset the programmer to start the update")
    return 0


def latest_firmware():
    response = requests.get(FIRESTARTER_RELEASE_URL)
    if not response.status_code == 200:
        return None, None
    latest = response.json()
    latest_version = latest["tag_name"]
    for asset in latest["assets"]:
        if "firmware.hex" in asset["name"]:
            return latest_version, asset["browser_download_url"]
    return None, None


def rurp_config(rev=None, r1=None, r2=None):
    data = {}
    data["state"] = STATE_CONFIG
    if not rev == None:
        if rev == -1:
            print("Disabling hardware revision override")
            rev = 0xFF
        data["rev"] = rev
    if r1:
        data["r1"] = r1
    if r2:
        data["r2"] = r2
    print("Reading configuration")

    ser = find_programmer(data)
    if not ser:
        return 1
    ser.write("OK".encode("ascii"))
    r, version = wait_for_response(ser)
    if r == "OK":
        print(f"Config: {version}")
    else:
        print(r)
        return 1
    return 0


def read_chip(
    eprom,
    output_file,
    force=False,
):
    data = db.get_eprom(eprom)
    if not data:
        print(f"Eprom {eprom} not found.")
        return 1
    eprom = data.pop("name")

    if force:
        data["force"] = True

    data["state"] = STATE_READ
    print(f"Reading chip: {eprom}")

    ser = find_programmer(data)
    if not ser:
        return 1

    mem_size = data["memory-size"]
    if not output_file:
        output_file = f"{eprom}.bin"
    print(f"Output will be saved to: {output_file}")
    bytes_read = 0
    try:
        ser.write("OK".encode("ascii"))
        ser.flush()
        output_file = open(output_file, "wb")
        start_time = time.time()

        while True:
            resp, info = wait_for_response(ser)
            if resp == "DATA":
                serial_data = ser.read(BUFFER_SIZE)
                output_file.write(serial_data)
                bytes_read += BUFFER_SIZE
                p = int(bytes_read / mem_size * 100)
                print_progress(p, bytes_read - BUFFER_SIZE, bytes_read)
                ser.write("OK".encode("ascii"))
                ser.flush()
            elif resp == "OK":
                print()
                print("Finished reading data")
                break
            elif resp == "WARN":
                print(f"Warning: {info}")
            else:
                print()
                print(f"Error reading data {info}")
                r, i = wait_for_response(ser)
                print(i)
                return 1

        end_time = time.time()
        # Calculate total duration
        total_duration = end_time - start_time
        print(f"Data received in {total_duration:.2f} seconds")

    except Exception as e:
        print("Error:", e)
    finally:
        output_file.close()
        ser.close()
    return 0


def write_chip(
    eprom,
    input_file,
    address=None,
    ignore_blank_check=False,
    force=False,
):
    data = db.get_eprom(eprom)
    if not data:
        print(f"Eprom {eprom} not found.")
        return 1
    if not os.path.exists(input_file):
        print(f"File {input_file} not found.")
        return 1
    file_size = os.path.getsize(input_file)
    eprom = data.pop("name")

    if address:
        if "0x" in address:
            data["address"] = int(address, 16)
        else:
            data["address"] = int(address)

    if ignore_blank_check:
        data["skip-erase"] = True
        data["blank-check"] = False

    if force:
        data["force"] = True

    data["state"] = STATE_WRITE

    start_time = time.time()
    print(f"Writing to chip: {eprom}")

    ser = find_programmer(data)
    if not ser:
        return 1

    mem_size = data["memory-size"]
    if not mem_size == file_size:
        print(f"The file size dont match the memory size")

    print(f"Reading from input file: {input_file}")
    bytes_sent = 0

    # Open the file to send
    with open(input_file, "rb") as f:

        print(f"Sending file {input_file} in blocks of {BUFFER_SIZE} bytes")

        # Read the file and send in blocks
        while True:
            data = f.read(BUFFER_SIZE)
            if not data:
                ser.write(int(0).to_bytes(2, byteorder="big"))
                ser.flush()
                resp, info = wait_for_response(ser)
                print("\nEnd of file reached")
                print(info)
                break

            ser.write(len(data).to_bytes(2, byteorder="big"))
            ser.flush()
            resp, info = wait_for_response(ser)
            if resp == "ERROR":
                print()
                print(f"Error writing: {info}")
                return 1
            sent = ser.write(data)
            ser.flush()

            while (t := wait_for_response(ser))[0] != "OK" and t[0] != "ERROR":
                if t[0] == "WARN":
                    print(f"Warning: {t[1]}")
            resp, info = t
            if resp == "OK":
                bytes_sent += sent
                p = int(bytes_sent / file_size * 100)
                print_progress(p, bytes_sent - BUFFER_SIZE, bytes_sent)
            elif resp == "ERROR":
                print()
                print(f"Error writing: {info}")
                return 1
            if bytes_sent == mem_size:
                break

    # Calculate total duration
    total_duration = time.time() - start_time
    print()
    print(f"File sent successfully in {total_duration:.2f} seconds")
    return 0


def erase(eprom):
    data = db.get_eprom(eprom)
    if not data:
        print(f"Eprom {eprom} not found.")
        return 1
    eprom = data.pop("name")
    if not data["can-erase"]:
        print(f"{eprom} can't be erased.")
        return 1
    data["state"] = STATE_ERASE

    print(f"Erasing: {eprom}")
    ser = find_programmer(data)
    if not ser:
        return 1

    resp, info = wait_for_response(ser)
    if resp == "OK":
        print(f"{eprom} erased {info}")
    elif resp == "ERROR":
        print()
        print(f"Error: {info}")
        return 1
    return 0


def blank_check(eprom):
    data = db.get_eprom(eprom)
    if not data:
        print(f"Eprom {eprom} not found.")
        return 1
    eprom = data.pop("name")
    data["state"] = STATE_CHECK_BLANK

    print(f"Blank checking: {eprom}")
    ser = find_programmer(data)
    if not ser:
        return 1

    resp, info = wait_for_response(ser)
    if resp == "OK":
        print(f"{eprom} is blank {info}")
    elif resp == "ERROR":
        print()
        print(f"Error: {info}")
        return 1
    return 0


def main():
    global verbose

    parser = argparse.ArgumentParser(
        description="EPROM programer for Arduino UNO and Relatively-Universal-ROM-Programmer shield."
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
        help="Force read, even if the chip id don't match.",
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
        help="Igonre blank check before write (and skip erase).",
    )
    write_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force write, even if the VPP or chip id don't match.",
    )
    write_parser.add_argument(
        "-a", "--address", type=str, help="Write start address in dec/hex"
    )
    write_parser.add_argument("input_file", type=str, help="Input file name")

    blank_check_parser = subparsers.add_parser(
        "blank", help="Checks if a EPROM is blank."
    )
    blank_check_parser.add_argument("eprom", type=str, help="The name of the EPROM.")

    erase_parser = subparsers.add_parser("erase", help="Erase a EPROM, if supported.")
    erase_parser.add_argument("eprom", type=str, help="The name of the EPROM.")

    #  List command
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

    vpp_parser = subparsers.add_parser("vpp", help="VPP voltage.")
    vpe_parser = subparsers.add_parser("vpe", help="VPE voltage.")

    hw_parser = subparsers.add_parser("hw", help="Hardware revision.")

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
        args = parser.parse_args(["--help"])
    else:
        args = parser.parse_args()

    open_config()

    verbose = args.verbose

    db.init()

    if args.command == "list":
        res = list_eproms(args.verified)
    elif args.command == "info":
        res = eprom_info(args.eprom)
    elif args.command == "search":
        res = search_eproms(args.text)
    elif args.command == "read":
        res = read_chip(
            args.eprom,
            args.output_file,
            force=args.force,
        )
    elif args.command == "write":
        res = write_chip(
            args.eprom,
            args.input_file,
            address=args.address,
            ignore_blank_check=args.ignore_blank_check,
            force=args.force,
        )
    elif args.command == "blank":
        res = blank_check(args.eprom)
    elif args.command == "erase":
        res = erase(args.eprom)
    elif args.command == "vpe":
        res = read_voltage(STATE_READ_VPE)
    elif args.command == "vpp":
        res = read_voltage(STATE_READ_VPP)
    elif args.command == "vpe":
        res = read_voltage(STATE_READ_VPE)
    elif args.command == "fw":
        res = firmware(args.install, args.avrdude_path, args.port)
    elif args.command == "hw":
        res = hardware()
    elif args.command == "config":
        res = rurp_config(args.rev, args.r16, args.r14r15)
    return res


def exit_gracefully(signum, frame):
    # restore the original signal handler as otherwise evil things will happen
    # in raw_input when CTRL+C is pressed, and our signal handler is not re-entrant
    signal.signal(signal.SIGINT, original_sigint)

    sys.exit(1)


if __name__ == "__main__":
    # store the original SIGINT handler
    original_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, exit_gracefully)
    sys.exit(main())
