"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Utility Functions Module
"""

import re

# Global verbose flag
_verbose = False


def extract_hex_to_decimal(input_string):
    """
    Extracts a hexadecimal number from a string and converts it to decimal.

    Args:
        input_string (str): The string containing a hexadecimal number.

    Returns:
        int: The decimal representation of the hexadecimal number, or None if not found.
    """
    match = re.search(r"0[xX][0-9a-fA-F]+", input_string)
    if match:
        hex_number = match.group()  # Extract the matched hexadecimal number
        return int(hex_number, 16)  # Convert to decimal
    return None


def is_valid_hex_string(hex_string):
    """
    Validates if a given string is a valid hexadecimal representation.

    Args:
        hex_string (str): The string to validate.

    Returns:
        bool: True if valid hexadecimal, False otherwise.
    """
    return bool(re.fullmatch(r"0[xX][0-9a-fA-F]+", hex_string))


def format_size(size_in_bytes):
    """
    Formats a file size in bytes into a human-readable string.

    Args:
        size_in_bytes (int): File size in bytes.

    Returns:
        str: Human-readable file size.
    """
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_in_bytes < 1024:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024


def time_formatter(seconds):
    """
    Formats a duration in seconds into a human-readable format.

    Args:
        seconds (float): Time in seconds.

    Returns:
        str: Formatted time string (e.g., "1m 20s").
    """
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s" if h else f"{m}m {s}s" if m else f"{s}s"


# For testing or standalone execution
if __name__ == "__main__":
    # Test extract_hex_to_decimal
    print(extract_hex_to_decimal("Error at 0x1A2B3C"))  # Expected: 1715004

    # Test print_progress_bar
    import time


    # Test is_valid_hex_string
    print(is_valid_hex_string("0x1A2B3C"))  # Expected: True
    print(is_valid_hex_string("123ABC"))  # Expected: False

    # Test format_size
    print(format_size(1024))  # Expected: "1.00 KB"
    print(format_size(1048576))  # Expected: "1.00 MB"

    # Test time_formatter
    print(time_formatter(3665))  # Expected: "1h 1m 5s"
