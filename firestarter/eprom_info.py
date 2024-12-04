
"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

EPROM Information Module
"""

try:
    from .database import get_eprom, search_eprom, get_eproms
    from .ic_layout import print_chip_info
    from .utils import verbose
    from .__init__ import __version__ as version
except ImportError:
    from database import get_eprom, search_eprom, get_eproms
    from ic_layout import print_chip_info
    from utils import verbose
    from __init__ import __version__ as version

def list_eproms(verified=False):
    """
    Lists EPROMs in the database.

    Args:
        verified (bool): If True, only lists verified EPROMs.
    """
    print("Listing EPROMs in the database:")
    eproms = get_eproms(verified)
    if not eproms:
        print("No EPROMs found.")
    for eprom in eproms:
        print(eprom)
    return 0

def search_eproms(query):
    """
    Searches for EPROMs in the database by name or properties.

    Args:
        query (str): Search text.
    """
    print(f"Searching for EPROMs with query: {query}")
    results = search_eprom(query, True)
    if not results:
        print("No matching EPROMs found.")
        return 1
    for result in results:
        print(result)
    return 0


def eprom_info(eprom_name):
    """
    Displays information about a specific EPROM.

    Args:
        eprom_name (str): Name of the EPROM.
    """
    if verbose():
        print(f"Firestarter version: {version}")
        print()

    eprom = get_eprom(eprom_name, True)
    if not eprom:
        print(f"EPROM {eprom_name} not found.")
        return 1

    print_chip_info(eprom)
    if verbose():
        print()
        print("Config:")
        print(eprom)
    return 0