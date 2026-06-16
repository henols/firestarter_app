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


class ProtocolNotImplementedError(EpromOperationError):
    """Raised when firmware reports a protocol is not yet implemented (id 0xBB)."""

    pass


class ChipNotImplementedError(EpromOperationError):
    """Raised when the host refuses a program-capable operation on a non-supported chip.

    Fired by chip_resolver.resolve_chip when the chip's support_status is not
    "supported" (covers all three non-supported statuses: protocol-not-implemented,
    adapter-required, vpp-exceeds-max).  The guard fires BEFORE any wire dict is
    built or serial byte emitted — the host will not drive hardware for a
    non-supported chip.

    This is distinct from ProtocolNotImplementedError, which is the firmware-side
    0xBB response ("protocol recognized but not yet implemented in firmware").
    ChipNotImplementedError is a HOST-SIDE refusal covering all support_status
    non-supported cases, not a firmware response.
    """

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
