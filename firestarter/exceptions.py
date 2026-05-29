"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Consolidated application exception hierarchy for the Firestarter host CLI.
AvrdudeNotFoundError and AvrdudeConfigNotFoundError stay in avr_tool.py
(different domain — avrdude binary discovery) per CONTEXT.md D-01/D-02.
"""


class SerialError(Exception):
    """Custom exception for serial communication errors."""

    pass


class SerialTimeoutError(SerialError):
    """Custom exception for serial timeouts."""

    pass


class ProgrammerNotFoundError(SerialError):
    """Custom exception when no programmer is found."""

    pass


class FirmwareOutdatedError(SerialError):
    """Custom exception for outdated firmware."""

    pass


class EpromOperationError(Exception):
    """Custom exception for EPROM operation failures."""

    pass


class HardwareOperationError(Exception):
    """Custom exception for hardware operation failures."""

    pass


class FirmwareOperationError(Exception):
    """Custom exception for firmware operation failures."""

    pass


class ChipNotFoundError(Exception):
    """Raised when a chip name cannot be resolved in the database.

    Wired in Phase 39 (chip_resolver.py).
    """

    pass
