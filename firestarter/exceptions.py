"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Consolidated application exception hierarchy for the Firestarter host CLI.
AvrdudeNotFoundError and AvrdudeConfigNotFoundError stay in avr_tool.py
(different domain — avrdude binary discovery).
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


class HardwareRevisionUnsupportedError(SerialError):
    """Raised when the attached shield revision cannot safely program the chip.

    Chips whose bus-config routes VPP to bus line 11 (socket pin 21 on the
    DIP24_2716 / DIP24_2532 pinouts) need the 3-position JP4 header introduced
    on RURP Rev 2.2. Driving them on an earlier shield is a chip-damage path,
    so the host refuses at connect time — before the operation-setup ack is
    answered and therefore before the firmware engages the VPP regulator.

    A SerialError subclass so callers already handling connect-time transport
    failures see it, but _probe_port catches it explicitly and re-raises rather
    than degrading it to "no programmer found" (an operator staring at a board
    that is plainly attached needs the real reason).

    `detected` carries the effective revision byte the firmware reported, or
    None when the firmware predates the CAP-02 ack and sent no revision at all.
    """

    def __init__(self, *args: object, detected: int | None = None) -> None:
        super().__init__(*args)
        self.detected = detected


class EpromOperationError(Exception):
    """Custom exception for EPROM operation failures."""

    def __init__(self, *args: object, error_code: int | None = None) -> None:
        super().__init__(*args)
        self.error_code = error_code


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


class PageSizeUnavailableError(EpromOperationError):
    """Raised when a protocol 0x05 write targets a chip with no recorded page size.

    Fired by page_size_gate.require_page_size before any wire dict reaches the
    transport and before any serial byte is emitted — the host will not drive
    hardware for a protocol 0x05 chip whose page size is unknown. A guessed
    page size on this protocol destroys data in both directions, so absent
    evidence is never treated as safe.
    """

    pass


class PageAlignmentError(EpromOperationError):
    """Raised when a protocol 0x05 write's start address or payload length is
    not a whole multiple of the chip's page size.

    Fired by page_size_gate.require_page_alignment before any wire dict
    reaches the transport and before any serial byte is emitted, because a
    protocol 0x05 page commit erases every byte of the touched page that was
    not loaded, and that erase cannot be undone once the load has started.
    """

    pass


class NegativeStartAddressError(EpromOperationError):
    """Raised when a write's start address parses as negative.

    Fired by write_blank_guard.require_non_negative_address, on every write
    family -- guarded or not -- before any wire dict reaches the transport
    and before any serial byte is emitted. Both the blank-guard's own region
    and --verify's region are derived from this same address, so a signed
    start would make the host compute a region it never actually wrote or
    read. A negative start is never treated as 0: that clamp is exactly what
    the firmware's own JSON parser does today (`simple_strtoul` consumes only
    `[0-9]`, so a leading `-` makes the address silently become 0), and it is
    the defect this refusal exists to catch on the host side instead of
    silently reproducing it.
    """

    pass


class HardwareOperationError(Exception):
    """Custom exception for hardware operation failures."""

    pass


class Pin1HazardRefusedError(HardwareOperationError):
    """Raised when a damage-capable operation on an affected part is refused.

    A part whose pin map places an address line at or above A19 on socket
    pin 1 shares that pin with the RURP shield's JP5-switched 12.75V
    programming rail. Every RURP Rev 2.x board reaches socket pin 1 through
    JP5 the same way, so this is not a shield-revision problem --
    `HardwareRevisionUnsupportedError` would be the wrong (and dishonest)
    base to reuse here. It subclasses `HardwareOperationError` instead.

    Raised either because the part is affected and no acknowledgement was
    given in this invocation, or because the bus configuration carries no
    evidence at all that socket pin 1 is safe -- fail-closed either way.
    `cli_handlers.map_typed_errors` renders this verbatim, ahead of the
    generic `HardwareOperationError` arm, so a caller reaching this refusal
    by any path -- CLI, `dev test`, or a future entry point -- sees the
    hazard text rather than a degraded generic hardware-error message.
    """

    pass


class FirmwareOperationError(Exception):
    """Custom exception for firmware operation failures."""

    pass


class FirmwareReleaseRefusedError(FirmwareOperationError):
    """Raised when this CLI refuses to flash a firmware release it cannot drive.

    Fired by fw_release_gate.require_installable_release. On the
    `--firmware-version` path it fires in cli_handlers.fw before any port opens
    and before any HTTP request. On every other path it fires in
    FirmwareManager.manage_firmware_update, after the release metadata fetch and
    before the download -- no byte reaches avrdude or the DFU backend.

    Raised when the release's feature version (major, minor) is above this
    CLI's, or when either version cannot be read. `--allow-newer-firmware`
    waives it; `--force` does not, because `--force` means "reinstall the same
    version" and is also a wire flag (FLAG_FORCE).

    A FirmwareOperationError, so cli_handlers.map_typed_errors renders it
    verbatim through the existing FirmwareOperationError arm. No new arm.
    """

    pass


class ChipNotFoundError(Exception):
    """Raised when a chip name cannot be resolved in the database.

    Wired in chip_resolver.py.
    """

    pass
