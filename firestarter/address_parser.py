"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Address and size string parsing utilities.
"""

from typing import Optional  # noqa: UP035


def parse_address(s: Optional[str]) -> Optional[int]:
    """Parse a hex or decimal address string.

    Returns None for None input. Raises ValueError on bad format.
    """
    if s is None:
        return None
    return int(s, 16) if "0x" in s.lower() else int(s)


def parse_size(s: Optional[str]) -> Optional[int]:
    """Parse a hex or decimal size string.

    Returns None for None input. Raises ValueError on bad format.
    """
    if s is None:
        return None
    return int(s, 16) if "0x" in s.lower() else int(s)
