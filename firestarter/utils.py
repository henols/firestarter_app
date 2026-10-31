"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Utility Functions Module
"""

import re
from typing import Any


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


def print_to(msg: str, *, console: Any = None) -> None:
    """Print `msg` on `console` (a rich Console) or, without one, on stdout."""
    if console is not None:
        console.print(msg)
    else:
        print(msg)
