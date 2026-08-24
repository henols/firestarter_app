"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

PY32F071 USB DFU firmware-install backend.

Talks to the PY32F071 **factory bootloader** that lives in system memory
(`0x1FFF0000`–`0x1FFF2F00`, USB on PA11/PA12 — Puya UM1504) over the standard
USB DFU class. No external flashing binary is involved: the transfer is
implemented here and runs on `pyusb`, which installs with the package
(`pip install 'firestarter[py32]'`).

Two dialects are supported, selected at runtime from the device's own
descriptors — nothing about the geometry is hardcoded:

* **DfuSe** (ST-style, `bcdDFUVersion == 0x011A`): erase-by-sector, an explicit
  address pointer, and data blocks numbered from 2. Sector geometry is read
  from the alt-setting mapping string (e.g. `@Internal Flash /0x08000000/64*002Kg`).
* **Plain DFU 1.1**: sequential blocks from 0, no address pointer, no erase.

**Not yet verified against silicon.** No PY32F071 board exists as of 2026-07-28,
so which dialect the Puya bootloader speaks — and what USB VID/PID it presents —
is unconfirmed. That is why discovery scans for the DFU *interface class* rather
than a hardcoded ID, and why `probe()` exists: run it first on real hardware and
it prints exactly what was found. See `doc/PY32F071-FIRMWARE-INSTALL.md`.
"""

from __future__ import annotations

import enum
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple  # noqa: UP035

logger = logging.getLogger("Py32Dfu")

# --------------------------------------------------------------------------
# USB DFU 1.1 wire constants (USB Device Class Specification for DFU, v1.1)
# --------------------------------------------------------------------------

USB_CLASS_APP_SPECIFIC = 0xFE
USB_SUBCLASS_DFU = 0x01
DFU_PROTOCOL_RUNTIME = 0x01  # application is running; DETACH to get to DFU mode
DFU_PROTOCOL_DFU_MODE = 0x02  # bootloader is running; ready to accept a download

# bRequest values.
DFU_DETACH = 0
DFU_DNLOAD = 1
DFU_UPLOAD = 2
DFU_GETSTATUS = 3
DFU_CLRSTATUS = 4
DFU_GETSTATE = 5
DFU_ABORT = 6

# bmRequestType: class request, interface recipient.
_OUT = 0x21  # host -> device
_IN = 0xA1  # device -> host

# bState values from DFU_GETSTATUS.
STATE_APP_IDLE = 0
STATE_APP_DETACH = 1
STATE_DFU_IDLE = 2
STATE_DNLOAD_SYNC = 3
STATE_DNBUSY = 4
STATE_DNLOAD_IDLE = 5
STATE_MANIFEST_SYNC = 6
STATE_MANIFEST = 7
STATE_MANIFEST_WAIT_RESET = 8
STATE_UPLOAD_IDLE = 9
STATE_ERROR = 10

_STATE_NAMES = {
    STATE_APP_IDLE: "appIDLE",
    STATE_APP_DETACH: "appDETACH",
    STATE_DFU_IDLE: "dfuIDLE",
    STATE_DNLOAD_SYNC: "dfuDNLOAD-SYNC",
    STATE_DNBUSY: "dfuDNBUSY",
    STATE_DNLOAD_IDLE: "dfuDNLOAD-IDLE",
    STATE_MANIFEST_SYNC: "dfuMANIFEST-SYNC",
    STATE_MANIFEST: "dfuMANIFEST",
    STATE_MANIFEST_WAIT_RESET: "dfuMANIFEST-WAIT-RESET",
    STATE_UPLOAD_IDLE: "dfuUPLOAD-IDLE",
    STATE_ERROR: "dfuERROR",
}

STATUS_OK = 0x00

# DFU functional descriptor (bDescriptorType 0x21, bLength 9).
_DFU_FUNCTIONAL_DESCRIPTOR = 0x21

# DFU 1.1 §4.1.3 Table 4.2 offset 2 (bmAttributes) -- bit 1: upload capable
# (bitCanUpload). HOST-03: `_verify_readback` reads this bit to decide
# whether a DFU_UPLOAD readback can even be attempted; `DfuInterface.attributes`
# was already parsed and stored by `_parse_functional_descriptor` before this
# constant existed, so this is a consumer of that field, not a new parser.
_DFU_BIT_CAN_UPLOAD = 0x02

# DfuSe (ST extension) commands, sent as a DNLOAD with wBlockNum == 0.
DFUSE_SET_ADDRESS = 0x21
DFUSE_ERASE_PAGE = 0x41
DFUSE_READ_UNPROTECT = 0x92

DFUSE_VERSION = 0x011A  # bcdDFUVersion that marks the ST dialect

# --------------------------------------------------------------------------
# PY32F071xB memory map (Puya UM1504 + PY32F071xB_FLASH.ld on the firmware
# branch). Used only as a safety envelope — geometry comes from the device.
#
# `FLASH_SIZE` is the physical part size (128 KiB) — kept verbatim,
# because an existing test writes `FLASH_SIZE + 1` bytes and expects a
# refusal. It is NOT what `_check_envelope` bounds on. The firmware's own
# linker script (`platform/py32f071/linker/PY32F071xB_FLASH.ld`) reserves
# only the bottom 120 KiB (`APP_REGION_SIZE`, ending at `APP_REGION_END`) for
# the application; the top `CONFIG_REGION_SIZE` (8 KiB, Sector 15) is the
# config-storage reservation (page 256 B / sector 8192 B per
# `platform/py32f071/CONFIG-STORAGE.md`). `BOOTLOADER` is currently a
# zero-length NAMED SEAM at the same origin as `FLASH` — giving it
# a length would move the application's ORIGIN, so the *lower* bound of the
# accepted span would move too, not just `APP_REGION_END`.
#
# `tests/test_py32_flash_map_host.py` is the fail-closed gate that keeps
# these four constants matching the linker script — it parses the script
# directly rather than trusting this comment.
# --------------------------------------------------------------------------

FLASH_BASE = 0x08000000
FLASH_SIZE = 128 * 1024  # physical part size — do not use for the envelope
APP_REGION_SIZE = 120 * 1024  # mirrors LENGTH(FLASH) in the linker script
APP_REGION_END = FLASH_BASE + APP_REGION_SIZE  # mirrors ORIGIN(CONFIG)
CONFIG_REGION_SIZE = 8 * 1024  # mirrors LENGTH(CONFIG), the Sector 15 reservation
DEFAULT_ERASE_PAGE_SIZE = 2048  # fallback when the device publishes no layout

# Blocks are numbered from 2 in DfuSe; 0 and 1 are reserved for commands.
_DFUSE_FIRST_BLOCK = 2

_DEFAULT_TRANSFER_SIZE = 1024
_STATUS_POLL_CEILING_S = 5.0  # clamp a hostile bwPollTimeout
_MANIFEST_GRACE_S = 2.0


class DfuError(Exception):
    """Base class for every failure in this module."""


class PyusbMissingError(DfuError):
    """pyusb (or its libusb backend) is not importable."""


class DfuDeviceNotFoundError(DfuError):
    """No USB device exposing a DFU interface was found."""


class DfuProtocolError(DfuError):
    """The device rejected a request or reported dfuERROR."""


class ImageError(DfuError):
    """The firmware image could not be read, or is not safe to write."""


# --------------------------------------------------------------------------
# Firmware image loading
# --------------------------------------------------------------------------


def load_image(path: str) -> Tuple[int, bytes]:  # noqa: UP006
    """Load a firmware image, returning ``(base_address, payload)``.

    Accepts Intel HEX (`.hex`) and raw binary (`.bin`). HEX carries its own load
    address; a raw binary is assumed to start at `FLASH_BASE`. Gaps inside a HEX
    file are padded with `0xFF` (erased-flash value), so the returned payload is
    always contiguous.

    Both extensions are accepted deliberately: which one the firmware release
    publishes for this board is not settled yet, and a DFU transfer does not
    care as long as the address is known.
    """
    lowered = path.lower()
    if lowered.endswith(".hex"):
        return parse_intel_hex(_read_text(path))
    with open(path, "rb") as handle:
        payload = handle.read()
    if not payload:
        raise ImageError(f"Firmware image is empty: {path}")
    return FLASH_BASE, payload


def _read_text(path: str) -> str:
    with open(path, encoding="ascii", errors="strict") as handle:
        return handle.read()


def parse_intel_hex(text: str) -> Tuple[int, bytes]:  # noqa: UP006
    """Parse Intel HEX into ``(base_address, contiguous_payload)``.

    Supports record types 00 (data), 01 (EOF), 02 (extended segment address),
    04 (extended linear address). Record type 05 (start linear address) is
    accepted and ignored — it carries an entry point, not data.
    """
    chunks: list[tuple[int, bytes]] = []
    upper = 0

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise ImageError(f"Intel HEX line {lineno} does not start with ':'")
        try:
            record = bytes.fromhex(line[1:])
        except ValueError as exc:
            raise ImageError(
                f"Intel HEX line {lineno} is not valid hex: {exc}"
            ) from exc
        if len(record) < 5:
            raise ImageError(f"Intel HEX line {lineno} is too short")

        count, offset_hi, offset_lo, rectype = (
            record[0],
            record[1],
            record[2],
            record[3],
        )
        data = record[4:-1]
        if len(data) != count:
            raise ImageError(
                f"Intel HEX line {lineno}: byte count {count} != payload {len(data)}"
            )
        if (sum(record[:-1]) + record[-1]) & 0xFF:
            raise ImageError(f"Intel HEX line {lineno}: checksum mismatch")

        offset = (offset_hi << 8) | offset_lo
        if rectype == 0x00:
            chunks.append((upper + offset, data))
        elif rectype == 0x01:
            break
        elif rectype == 0x04:
            if len(data) != 2:
                raise ImageError(f"Intel HEX line {lineno}: bad type-04 record")
            upper = ((data[0] << 8) | data[1]) << 16
        elif rectype == 0x02:
            if len(data) != 2:
                raise ImageError(f"Intel HEX line {lineno}: bad type-02 record")
            upper = ((data[0] << 8) | data[1]) << 4
        elif rectype == 0x05:
            continue
        else:
            raise ImageError(
                f"Intel HEX line {lineno}: unsupported record type 0x{rectype:02X}"
            )

    if not chunks:
        raise ImageError("Intel HEX file contains no data records")

    base = min(addr for addr, _ in chunks)
    end = max(addr + len(payload) for addr, payload in chunks)
    buffer = bytearray(b"\xff" * (end - base))
    for addr, payload in chunks:
        buffer[addr - base : addr - base + len(payload)] = payload
    return base, bytes(buffer)


# --------------------------------------------------------------------------
# DfuSe memory layout ("@Internal Flash /0x08000000/64*002Kg")
# --------------------------------------------------------------------------

_LAYOUT_SECTOR_RE = re.compile(r"(\d+)\s*\*\s*(\d+)\s*([BKM]?)", re.IGNORECASE)


@dataclass(frozen=True)
class SectorRange:
    """A run of equally sized erase sectors starting at ``address``."""

    address: int
    count: int
    size: int

    @property
    def end(self) -> int:
        return self.address + self.count * self.size


def parse_dfuse_layout(name: Optional[str]) -> List[SectorRange]:  # noqa: UP006,UP045
    """Parse a DfuSe alt-setting mapping string into erase sectors.

    Returns an empty list when ``name`` is absent or is not a mapping string,
    which is the caller's signal to fall back to a fixed page size.
    """
    if not name or not name.startswith("@"):
        return []
    fields = name.split("/")
    if len(fields) < 3:
        return []
    try:
        start = int(fields[1].strip(), 0)
    except ValueError:
        return []

    ranges: list[SectorRange] = []
    address = start
    for spec in fields[2].split(","):
        match = _LAYOUT_SECTOR_RE.search(spec)
        if not match:
            continue
        count = int(match.group(1))
        size = int(match.group(2))
        multiplier = {"": 1, "B": 1, "K": 1024, "M": 1024 * 1024}[
            match.group(3).upper()
        ]
        size *= multiplier
        if count <= 0 or size <= 0:
            continue
        ranges.append(SectorRange(address=address, count=count, size=size))
        address += count * size
    return ranges


def erase_addresses(
    layout: Sequence[SectorRange], base: int, length: int, fallback_page: int
) -> List[int]:  # noqa: UP006
    """Sector start addresses that must be erased to hold ``length`` bytes at ``base``.

    Falls back to a uniform ``fallback_page`` grid when the device published no
    usable layout.
    """
    if length <= 0:
        return []
    end = base + length

    if not layout:
        first = (base // fallback_page) * fallback_page
        return list(range(first, end, fallback_page))

    addresses: list[int] = []
    for sector_range in layout:
        if sector_range.end <= base or sector_range.address >= end:
            continue
        for index in range(sector_range.count):
            address = sector_range.address + index * sector_range.size
            if address + sector_range.size <= base or address >= end:
                continue
            addresses.append(address)
    if not addresses:
        raise ImageError(
            f"Image at 0x{base:08X} (+{length} bytes) falls outside the device's "
            f"published flash layout"
        )
    return addresses


# --------------------------------------------------------------------------
# Device discovery
# --------------------------------------------------------------------------


@dataclass
class DfuInterface:
    """A DFU interface located on a USB device, plus its functional parameters."""

    device: Any  # usb.core.Device — only .ctrl_transfer is required
    vendor_id: int
    product_id: int
    configuration: int
    interface: int
    alt_setting: int
    protocol: int
    name: Optional[str] = None  # noqa: UP045
    transfer_size: int = _DEFAULT_TRANSFER_SIZE
    dfu_version: int = 0x0100
    attributes: int = 0

    @property
    def is_dfuse(self) -> bool:
        """True when the device speaks the ST DfuSe dialect."""
        return self.dfu_version == DFUSE_VERSION or bool(
            self.name and self.name.startswith("@")
        )

    @property
    def usb_id(self) -> str:
        return f"{self.vendor_id:04x}:{self.product_id:04x}"

    def describe(self) -> str:
        dialect = "DfuSe" if self.is_dfuse else "DFU 1.1"
        return (
            f"{self.usb_id} alt {self.alt_setting} ({dialect}, "
            f"transfer {self.transfer_size} B, "
            f"bcdDFUVersion 0x{self.dfu_version:04X}): {self.name or '<no name>'}"
        )


def _require_usb() -> Any:
    """Import pyusb lazily so the dependency is only needed for DFU boards."""
    try:
        import usb.core  # noqa: PLC0415
        import usb.util  # noqa: PLC0415
    except ImportError as exc:
        raise PyusbMissingError(
            "USB firmware install needs pyusb. Install it with:\n"
            "    pip install 'firestarter[py32]'\n"
            "On Linux you also need libusb and permission to reach the device "
            "(a udev rule, or run as root); on Windows the DFU device needs a "
            "WinUSB driver."
        ) from exc
    return usb


def _parse_functional_descriptor(blob: bytes) -> Optional[Tuple[int, int, int]]:  # noqa: UP006,UP045
    """Pull ``(attributes, transfer_size, dfu_version)`` out of extra descriptors."""
    index = 0
    while index + 1 < len(blob):
        length = blob[index]
        if length < 2 or index + length > len(blob):
            break
        if blob[index + 1] == _DFU_FUNCTIONAL_DESCRIPTOR and length >= 9:
            body = blob[index : index + 9]
            attributes = body[2]
            transfer_size = body[5] | (body[6] << 8)
            dfu_version = body[7] | (body[8] << 8)
            return attributes, transfer_size, dfu_version
        index += length
    return None


def _extra_descriptor_bytes(holder: Any) -> bytes:
    raw = getattr(holder, "extra_descriptors", None) or b""
    return bytes(raw)


def find_dfu_interfaces(
    vendor_id: Optional[int] = None,  # noqa: UP045
    product_id: Optional[int] = None,  # noqa: UP045
) -> List[DfuInterface]:  # noqa: UP006
    """Enumerate every DFU interface currently attached.

    Discovery is by **interface class** (0xFE/0x01), not by VID/PID, because the
    USB ID the Puya bootloader presents is not confirmed yet. Pass
    ``vendor_id``/``product_id`` to narrow it once that is known.
    """
    usb = _require_usb()
    found: list[DfuInterface] = []

    kwargs: dict[str, int] = {}
    if vendor_id is not None:
        kwargs["idVendor"] = vendor_id
    if product_id is not None:
        kwargs["idProduct"] = product_id

    for device in usb.core.find(find_all=True, **kwargs):
        for configuration in device:
            for interface in configuration:
                if (
                    interface.bInterfaceClass != USB_CLASS_APP_SPECIFIC
                    or interface.bInterfaceSubClass != USB_SUBCLASS_DFU
                ):
                    continue

                functional = _parse_functional_descriptor(
                    _extra_descriptor_bytes(interface)
                ) or _parse_functional_descriptor(
                    _extra_descriptor_bytes(configuration)
                )
                if functional is None:
                    logger.debug(
                        "DFU interface on %04x:%04x publishes no functional "
                        "descriptor; assuming %d-byte transfers",
                        device.idVendor,
                        device.idProduct,
                        _DEFAULT_TRANSFER_SIZE,
                    )
                    attributes, transfer_size, dfu_version = (
                        0,
                        _DEFAULT_TRANSFER_SIZE,
                        0x0100,
                    )
                else:
                    attributes, transfer_size, dfu_version = functional

                name = None
                if interface.iInterface:
                    try:
                        name = usb.util.get_string(device, interface.iInterface)
                    except Exception:  # noqa: BLE001 — string descriptors are optional
                        name = None

                found.append(
                    DfuInterface(
                        device=device,
                        vendor_id=device.idVendor,
                        product_id=device.idProduct,
                        configuration=configuration.bConfigurationValue,
                        interface=interface.bInterfaceNumber,
                        alt_setting=interface.bAlternateSetting,
                        protocol=interface.bInterfaceProtocol,
                        name=name,
                        transfer_size=max(1, transfer_size),
                        dfu_version=dfu_version,
                        attributes=attributes,
                    )
                )
    return found


def dfu_device_present() -> bool:
    """Best-effort check used only to hint at ``--board py32f071``. Never raises.

    Counts only devices actually **in DFU mode**. Runtime DFU interfaces are
    common on unrelated peripherals, so hinting on those would send operators
    chasing a webcam.
    """
    try:
        return any(
            interface.protocol == DFU_PROTOCOL_DFU_MODE
            for interface in find_dfu_interfaces()
        )
    except Exception:  # noqa: BLE001 — a hint must never break the install path
        return False


# --------------------------------------------------------------------------
# The transfer itself
# --------------------------------------------------------------------------


class VerifyResult(enum.Enum):
    """The outcome of the post-write `DFU_UPLOAD` readback verification.

    `flash()` deliberately keeps returning `bool` regardless of which member
    ends up here -- that is a blast-radius choice: widening `flash()`'s
    return type would ripple into `_install_with_dfu` and every existing
    `assert flash(...) is True`. This enum is the only place the richer
    outcome is recorded.

    Exactly one of these four members is set on `Py32DfuFlasher` once a
    `flash()` call has completed the verification step; `verify_result is
    None` means `flash()` has not run yet (or raised before reaching it).

    * `VERIFIED` -- the full payload was read back over `DFU_UPLOAD` and
      compared byte-for-byte; it matched.
    * `SKIPPED_NO_UPLOAD` -- the device does not advertise `bitCanUpload`;
      a readback could not be attempted at all.
    * `SKIPPED_PLAIN_DFU` -- the device speaks plain DFU 1.1, where the host
      never chooses the load address, so a readback could not be compared
      to anything meaningful.
    * `MISMATCH` -- the readback ran and differs from the payload (including
      a truncated read). This is the only member that is *also* paired with
      a raised `DfuProtocolError` -- soft-fail (the two `SKIPPED_*` members)
      covers *could not verify* only, never *verified and it was wrong*.

    Honesty note: this is the first `enum` import anywhere in this codebase.
    The project's existing result-constant idiom (see `firmware.py`'s
    `FLASH_METHOD_*`) is module-level strings plus a dict router; D-10 named
    an enum specifically for this state, and that locked decision -- not a
    change of house style -- is why this class exists in that shape.
    """

    VERIFIED = enum.auto()
    SKIPPED_NO_UPLOAD = enum.auto()
    SKIPPED_PLAIN_DFU = enum.auto()
    MISMATCH = enum.auto()


class Py32DfuFlasher:
    """Writes a firmware image to a PY32F071 over its factory USB DFU bootloader."""

    def __init__(
        self,
        usb_id: Optional[str] = None,  # noqa: UP045
        base_address: int = FLASH_BASE,
        erase_page_size: int = DEFAULT_ERASE_PAGE_SIZE,
        leave: bool = True,
    ) -> None:
        self.vendor_id, self.product_id = _split_usb_id(usb_id)
        self.base_address = base_address
        self.erase_page_size = erase_page_size
        self.leave = leave
        self._interface: Optional[DfuInterface] = None  # noqa: UP045
        # HOST-03 / D-10: set by `_verify_readback` once `flash()` has run;
        # `None` means verification has not happened yet. See `VerifyResult`.
        self.verify_result: Optional[VerifyResult] = None  # noqa: UP045
        self.verify_reason: Optional[str] = None  # noqa: UP045

    # -- discovery ---------------------------------------------------------

    @property
    def _explicit_target(self) -> bool:
        """True when the operator named one device with ``--usb-id``."""
        return self.vendor_id is not None and self.product_id is not None

    def select_interface(self) -> DfuInterface:
        """Find the bootloader, and return the DFU-mode interface to talk to.

        Ambiguity is refused rather than guessed. DFU **runtime** interfaces
        (protocol 0x01) are common on unrelated consumer hardware — webcams,
        keyboards and audio interfaces routinely advertise one — and DETACHing or
        writing to one of those would poke a device that has nothing to do with
        Firestarter. So a runtime device is only touched when the operator named
        it explicitly with ``--usb-id``; likewise, more than one candidate is an
        error, never a coin flip.
        """
        interfaces = find_dfu_interfaces(self.vendor_id, self.product_id)
        if not interfaces:
            raise DfuDeviceNotFoundError(
                "No USB DFU device found. The PY32F071 must be in bootloader "
                "mode: strap BOOT0 high with nBOOT1 = 1 and power-cycle the "
                "board (see doc/PY32F071-FIRMWARE-INSTALL.md), or ask a running "
                "firmware to reboot into the bootloader."
            )

        for candidate in interfaces:
            logger.debug("Found DFU interface: %s", candidate.describe())

        dfu_mode = [i for i in interfaces if i.protocol == DFU_PROTOCOL_DFU_MODE]
        if len(dfu_mode) > 1 and not self._explicit_target:
            raise DfuDeviceNotFoundError(
                "More than one device is in USB DFU mode; refusing to guess. "
                "Pick one with --usb-id VID:PID:\n"
                + _bullet_list(i.describe() for i in dfu_mode)
            )
        if dfu_mode:
            self._interface = dfu_mode[0]
            return self._interface

        # Only runtime interfaces remain. Do NOT touch one unless it was named.
        if not self._explicit_target:
            raise DfuDeviceNotFoundError(
                "No device is in USB DFU mode. Devices advertising a DFU "
                "*runtime* interface were found, but these are usually unrelated "
                "peripherals (webcams, keyboards, audio interfaces) and will not "
                "be touched:\n"
                + _bullet_list(i.describe() for i in interfaces)
                + "\nPut the PY32F071 into its bootloader (strap BOOT0 high with "
                "nBOOT1 = 1 and power-cycle), or, if one of the devices above "
                "really is the target, name it with --usb-id VID:PID."
            )

        runtime = interfaces[0]
        logger.info(
            "Device %s is in DFU runtime mode; requesting DETACH into the bootloader.",
            runtime.usb_id,
        )
        self._detach(runtime)
        interfaces = find_dfu_interfaces(self.vendor_id, self.product_id)
        dfu_mode = [i for i in interfaces if i.protocol == DFU_PROTOCOL_DFU_MODE]
        if not dfu_mode:
            raise DfuDeviceNotFoundError(
                "Device did not re-appear in DFU mode after DETACH."
            )
        self._interface = dfu_mode[0]
        return self._interface

    def probe(self) -> List[str]:  # noqa: UP006
        """Describe every DFU interface on the bus, for the first bench session.

        This is the instrument that settles the two open unknowns: the USB ID the
        Puya bootloader presents, and whether it speaks DfuSe or plain DFU 1.1.
        """
        interfaces = find_dfu_interfaces(self.vendor_id, self.product_id)
        if not interfaces:
            return []
        lines = []
        for candidate in interfaces:
            mode = (
                "DFU mode"
                if candidate.protocol == DFU_PROTOCOL_DFU_MODE
                else f"runtime (protocol 0x{candidate.protocol:02X})"
            )
            lines.append(f"{candidate.describe()} [{mode}]")
            for sector in parse_dfuse_layout(candidate.name):
                lines.append(
                    f"    sectors: {sector.count} x {sector.size} B "
                    f"@ 0x{sector.address:08X}"
                )
        return lines

    # -- top level ---------------------------------------------------------

    def flash(self, image_path: str) -> bool:
        """Write ``image_path`` to flash. Returns True on success."""
        base, payload = load_image(image_path)
        if base == FLASH_BASE and self.base_address != FLASH_BASE:
            base = self.base_address
        self._check_envelope(base, len(payload))

        interface = self.select_interface()
        logger.info(
            "Flashing %d bytes to 0x%08X via USB DFU on %s",
            len(payload),
            base,
            interface.usb_id,
        )
        self._prepare()

        if interface.is_dfuse:
            finish_base, next_block = self._download_dfuse(interface, base, payload)
        else:
            logger.warning(
                "Device does not advertise DfuSe; using plain DFU 1.1 sequential "
                "download. The load address (0x%08X) is then decided by the "
                "bootloader, not by us.",
                base,
            )
            finish_base, next_block = self._download_plain(interface, payload)

        # HOST-03 / D-09..D-12: verify what was actually written before ever
        # leaving DFU mode. _verify_readback() raises DfuProtocolError on a
        # genuine MISMATCH (byte difference or truncated read); the two
        # SKIPPED_* outcomes are soft and never raise. Named unqualified
        # (_verify_readback(), not self._verify_readback()) for the same
        # reason the _finish() comment just below does.
        self._verify_readback(interface, base, payload)

        # _finish() leaves DFU mode and lets the device reset off
        # the bus, so it must be the LAST thing flash() does. Both download
        # strategies used to call _finish() themselves, as their own last
        # statement; it is hoisted to this single call site so the ordering
        # is structural rather than a convention a future edit could break.
        # A MISMATCH raises inside _verify_readback(), above this line,
        # so a bad image is never manifested -- the device is deliberately
        # left in DFU mode instead of being told to leave and reset.
        self._finish(finish_base, next_block, dfuse=interface.is_dfuse)

        logger.info("USB DFU download complete.")
        return True

    def _check_envelope(self, base: int, length: int) -> None:
        """Refuse an image reaching outside the application region.

        Bounded on `APP_REGION_END` (0x0801E000), not the 128 KiB physical
        part size — the top `CONFIG_REGION_SIZE` bytes are the firmware's
        reserved config storage and must never be reachable by an
        installed image, even though DfuSe erase is payload-scoped and a
        legitimate ≤120 KiB image would never touch it anyway.
        """
        if length == 0:
            raise ImageError("Refusing to flash an empty image.")
        if base < FLASH_BASE or base + length > APP_REGION_END:
            raise ImageError(
                f"Image spans 0x{base:08X}..0x{base + length:08X}, outside "
                f"the accepted application region (0x{FLASH_BASE:08X}.."
                f"0x{APP_REGION_END:08X}). The region above 0x{APP_REGION_END:08X} "
                "is reserved for the firmware's config storage."
            )

    # -- DFU primitives ----------------------------------------------------

    @property
    def _dev(self) -> Any:
        if self._interface is None:  # pragma: no cover — guarded by callers
            raise DfuProtocolError("No DFU interface selected.")
        return self._interface.device

    @property
    def _index(self) -> int:
        if self._interface is None:  # pragma: no cover — guarded by callers
            raise DfuProtocolError("No DFU interface selected.")
        return self._interface.interface

    def _detach(self, interface: DfuInterface) -> None:
        try:
            interface.device.ctrl_transfer(
                _OUT, DFU_DETACH, 1000, interface.interface, None
            )
        except Exception as exc:  # noqa: BLE001 — many stacks fault on DETACH
            logger.debug("DETACH raised (often expected): %s", exc)
        _sleep(1.0)

    def _get_status(self) -> Tuple[int, int, int]:  # noqa: UP006
        """Return ``(bStatus, poll_timeout_ms, bState)``."""
        raw = self._dev.ctrl_transfer(_IN, DFU_GETSTATUS, 0, self._index, 6)
        data = bytes(raw)
        if len(data) < 6:
            raise DfuProtocolError(f"Short DFU_GETSTATUS response: {len(data)} bytes")
        poll = data[1] | (data[2] << 8) | (data[3] << 16)
        return data[0], poll, data[4]

    def _clear_status(self) -> None:
        self._dev.ctrl_transfer(_OUT, DFU_CLRSTATUS, 0, self._index, None)

    def _abort(self) -> None:
        self._dev.ctrl_transfer(_OUT, DFU_ABORT, 0, self._index, None)

    def _prepare(self) -> None:
        """Drive the state machine to dfuIDLE before starting a download."""
        status, _, state = self._get_status()
        if state == STATE_ERROR or status != STATUS_OK:
            logger.debug(
                "Device in %s (status 0x%02X); clearing.",
                _STATE_NAMES.get(state, state),
                status,
            )
            self._clear_status()
            status, _, state = self._get_status()
        if state not in (STATE_DFU_IDLE, STATE_DNLOAD_IDLE):
            self._abort()
            status, _, state = self._get_status()
        if state != STATE_DFU_IDLE:
            raise DfuProtocolError(
                f"Device will not enter dfuIDLE (state "
                f"{_STATE_NAMES.get(state, state)}, status 0x{status:02X})."
            )

    def _dnload(self, block: int, data: bytes) -> None:
        self._dev.ctrl_transfer(_OUT, DFU_DNLOAD, block, self._index, data)

    def _wait_ready(self, what: str) -> None:
        """Poll GETSTATUS until the device leaves the busy state."""
        deadline_states = (STATE_DNLOAD_IDLE, STATE_DFU_IDLE, STATE_MANIFEST_WAIT_RESET)
        while True:
            status, poll_ms, state = self._get_status()
            if status != STATUS_OK or state == STATE_ERROR:
                self._clear_status()
                raise DfuProtocolError(
                    f"{what} failed: status 0x{status:02X}, state "
                    f"{_STATE_NAMES.get(state, state)}"
                )
            if state in deadline_states:
                return
            _sleep(min(poll_ms / 1000.0, _STATUS_POLL_CEILING_S))

    def _dfuse_command(self, command: int, address: Optional[int] = None) -> None:  # noqa: UP045
        payload = bytearray([command])
        if address is not None:
            payload += address.to_bytes(4, "little")
        self._dnload(0, bytes(payload))
        self._wait_ready(f"DfuSe command 0x{command:02X}")

    # -- HOST-03: post-write readback verification --------------------------

    def _read_back(self, interface: DfuInterface, base: int, length: int) -> bytes:
        """Read ``length`` bytes back from ``base`` over ``DFU_UPLOAD``.

        **Mock-only ceiling:** this sequence has never run against a
        PY32F071. No PCB exists as of this writing, no public evidence exists
        that any tool (dfu-util included) has ever driven a PY32 upload, and
        the DfuSe-vs-plain-DFU-1.1 fork this module implements is entirely
        untested against real silicon -- one of its two branches has never
        been the right one. Everything this method does is exercised only
        against `tests/test_py32_dfu.py`'s mock device.

        Issues `DFUSE_SET_ADDRESS` at `base` (the model used by
        `_download_dfuse`), then reads UPLOAD blocks numbered from
        `_DFUSE_FIRST_BLOCK`, requesting `min(interface.transfer_size,
        remaining)` bytes per block. Stops once `length` bytes have been
        collected, or the moment a block returns fewer bytes than requested
        -- a short read is the device saying it has no more to give. The
        return value may therefore be shorter than `length`; the caller
        (`_verify_readback`) decides what a short read means.
        """
        self._dfuse_command(DFUSE_SET_ADDRESS, base)
        collected = bytearray()
        block = _DFUSE_FIRST_BLOCK
        while len(collected) < length:
            remaining = length - len(collected)
            request_length = min(interface.transfer_size, remaining)
            data = self._dev.ctrl_transfer(
                _IN, DFU_UPLOAD, block, self._index, request_length
            )
            chunk = bytes(data)
            collected += chunk
            if len(chunk) < request_length:
                break
            block += 1
        return bytes(collected)

    def _verify_readback(
        self, interface: DfuInterface, base: int, payload: bytes
    ) -> None:
        """Read the just-written image back and compare it to ``payload``.

        Sets `self.verify_result` (and, for anything but a clean `VERIFIED`,
        `self.verify_reason`) in every branch, so a caller can inspect the
        outcome even on the branch that also raises. See `VerifyResult`'s
        docstring for what each member means and D-09..D-12 for why the
        branches are ordered this way.
        """
        if not interface.is_dfuse:
            # Plain DFU 1.1 never tells the host what address it
            # loaded the image at, so a readback here could not be compared
            # to anything meaningful. This converts flash()'s existing
            # runtime warning (logged just above, in the plain-DFU branch)
            # into a recorded fact instead of a new claim.
            self.verify_result = VerifyResult.SKIPPED_PLAIN_DFU
            self.verify_reason = "load address not under host control"
            return

        if not (interface.attributes & _DFU_BIT_CAN_UPLOAD):
            self.verify_result = VerifyResult.SKIPPED_NO_UPLOAD
            self.verify_reason = (
                "device does not advertise upload support (bitCanUpload = 0)"
            )
            return

        readback = self._read_back(interface, base, len(payload))

        if len(readback) < len(payload):
            self.verify_result = VerifyResult.MISMATCH
            self.verify_reason = (
                f"readback truncated: got {len(readback)} of {len(payload)} "
                f"bytes (first differing offset 0x{len(readback):08X})"
            )
            raise DfuProtocolError(
                f"DFU readback verification failed: {self.verify_reason}"
            )

        if readback != payload:
            offset = next(i for i in range(len(payload)) if readback[i] != payload[i])
            self.verify_result = VerifyResult.MISMATCH
            self.verify_reason = (
                f"byte mismatch at offset 0x{offset:08X}: expected "
                f"0x{payload[offset]:02X}, got 0x{readback[offset]:02X}"
            )
            raise DfuProtocolError(
                f"DFU readback verification failed: {self.verify_reason}"
            )

        self.verify_result = VerifyResult.VERIFIED
        self.verify_reason = None

    # -- download strategies ----------------------------------------------

    def _download_dfuse(
        self, interface: DfuInterface, base: int, payload: bytes
    ) -> Tuple[Optional[int], int]:  # noqa: UP006,UP045
        layout = parse_dfuse_layout(interface.name)
        if not layout:
            logger.warning(
                "Device published no DfuSe memory layout; erasing on a uniform "
                "%d-byte page grid.",
                self.erase_page_size,
            )
        sectors = erase_addresses(layout, base, len(payload), self.erase_page_size)
        logger.info("Erasing %d sector(s)...", len(sectors))
        for address in sectors:
            self._dfuse_command(DFUSE_ERASE_PAGE, address)

        chunk_size = interface.transfer_size
        total_blocks = (len(payload) + chunk_size - 1) // chunk_size
        logger.info(
            "Writing %d block(s) of up to %d bytes...", total_blocks, chunk_size
        )
        self._dfuse_command(DFUSE_SET_ADDRESS, base)

        block = _DFUSE_FIRST_BLOCK
        for offset in range(0, len(payload), chunk_size):
            self._dnload(block, payload[offset : offset + chunk_size])
            self._wait_ready(f"block {block}")
            block += 1

        return base, block

    def _download_plain(
        self, interface: DfuInterface, payload: bytes
    ) -> Tuple[Optional[int], int]:  # noqa: UP006,UP045
        chunk_size = interface.transfer_size
        block = 0
        for offset in range(0, len(payload), chunk_size):
            self._dnload(block, payload[offset : offset + chunk_size])
            self._wait_ready(f"block {block}")
            block += 1
        return None, block

    def _finish(self, base: Optional[int], next_block: int, dfuse: bool) -> None:  # noqa: UP045
        """End the download with a zero-length DNLOAD, then leave DFU mode.

        After a successful leave the device resets and disappears from the bus,
        so USB errors from this point on are expected and are *not* failures.
        """
        if dfuse and self.leave and base is not None:
            try:
                self._dfuse_command(DFUSE_SET_ADDRESS, base)
            except DfuError as exc:
                logger.debug("Set-address before leave failed: %s", exc)

        try:
            self._dnload(next_block, b"")
        except Exception as exc:  # noqa: BLE001 — device may reset immediately
            logger.debug("Zero-length DNLOAD raised (device may have reset): %s", exc)
            return

        try:
            status, poll_ms, state = self._get_status()
            _sleep(min(poll_ms / 1000.0, _MANIFEST_GRACE_S))
            if status != STATUS_OK and state == STATE_ERROR:
                raise DfuProtocolError(
                    f"Manifestation failed: status 0x{status:02X}, state "
                    f"{_STATE_NAMES.get(state, state)}"
                )
        except DfuProtocolError:
            raise
        except Exception as exc:  # noqa: BLE001 — reset during manifest is success
            logger.debug("Device left the bus during manifest (expected): %s", exc)


def _split_usb_id(usb_id: Optional[str]) -> Tuple[Optional[int], Optional[int]]:  # noqa: UP006,UP045
    """Parse a ``VID:PID`` string (hex, optional ``0x``) into two ints."""
    if not usb_id:
        return None, None
    parts = usb_id.split(":")
    if len(parts) != 2:
        raise DfuError(f"Malformed USB id {usb_id!r}; expected VID:PID, e.g. 1a86:8012")
    try:
        return int(parts[0], 16), int(parts[1], 16)
    except ValueError as exc:
        raise DfuError(f"Malformed USB id {usb_id!r}: {exc}") from exc


def _bullet_list(lines: Iterable[str]) -> str:
    """Render an iterable of strings as an indented bullet list."""
    return "\n".join(f"  - {line}" for line in lines)


def _sleep(seconds: float) -> None:
    """Indirection so tests can run the poll loops without real delays."""
    if seconds > 0:
        time.sleep(seconds)
