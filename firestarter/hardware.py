import time

try:
    from .serial_comm import find_programmer, wait_for_response, consume_response
except ImportError:
    from serial_comm import find_programmer, wait_for_response, consume_response

STATE_READ_VPP = 11
STATE_READ_VPE = 12
STATE_CONFIG = 14
STATE_HW_VERSION = 15


def hardware():
    """
    Reads the hardware revision of the programmer.

    Returns:
        int: 0 if successful, 1 otherwise.
    """
    print("Reading hardware revision...")
    data = {"state": STATE_HW_VERSION}

    ser = find_programmer(data)
    if not ser:
        return 1

    try:
        resp, version = wait_for_response(ser)
        if resp == "OK":
            print(f"Hardware revision: {version}")
        else:
            print(f"Failed to read hardware revision. {resp}: {version}")
            return 1
    finally:
        consume_response(ser)
        ser.close()
    return 0


def config(rev=None, r1=None, r2=None):
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
    try:
        # ser.write("OK".encode("ascii"))
        r, version = wait_for_response(ser)
        if r == "OK":
            print(f"Config: {version}")
        else:
            # print(r)
            return 1
    finally:
        consume_response(ser)
        ser.close()
    return 0


def read_vpp(timeout=None):
    """
    Reads the VPP voltage from the programmer.
    """
    read_voltage(STATE_READ_VPP, timeout)


def read_vpe(timeout=None):
    """
    Reads the VPE voltage from the programmer.
    """
    read_voltage(STATE_READ_VPE, timeout)


def read_voltage(state, timeout=None):
    data = {}
    data["state"] = state

    type = "VPE"
    if state == STATE_READ_VPP:
        type = "VPP"
    print(f"Reading {type} voltage")
    ser = find_programmer(data)
    if not ser:
        return 1
    try:
        resp, info = wait_for_response(ser)
        if not resp == "OK":
            print()
            print(f"Error reading {type} voltage: {info}")
            return 1
        ser.write("OK".encode("ascii"))
        ser.flush()

        if timeout:
            start = time.time()

        while (t := wait_for_response(ser))[0] == "DATA":
            print(f"\r{t[1]}", end="")
            if timeout and time.time() > start + timeout:
                print()
                return 0
            ser.write("OK".encode("ascii"))
            ser.flush()
    except Exception as e:
        print(f"Error while reading {type} voltage: {e}")
    finally:
        consume_response(ser)
        ser.close()
    return 1
