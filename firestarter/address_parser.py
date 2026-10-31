"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Address and size string parsing utilities.
"""


def parse_address(s: str | None) -> int | None:
    """Parse a hex or decimal address string.

    Returns None for None input. Raises ValueError on bad format.
    """
    if s is None:
        return None
    return int(s, 16) if "0x" in s.lower() else int(s)


# A size is written the same way as an address.
parse_size = parse_address
