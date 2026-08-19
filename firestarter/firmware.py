"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Firmware Management Module
"""

import logging
import os
import re
import time
from typing import List, Literal, Optional, Tuple, TypedDict  # noqa: UP035

import requests
from packaging.version import InvalidVersion, Version

# Add this line with the other imports
from rich.prompt import Confirm

from firestarter.avr_tool import (
    Avrdude,
    AvrdudeConfigNotFoundError,
    AvrdudeNotFoundError,
)
from firestarter.channel import beta_only_message, is_board_available
from firestarter.config import ConfigManager
from firestarter.constants import (
    COMMAND_FW_VERSION,
    FIRESTARTER_RELEASE_BY_TAG_URL,
    FIRESTARTER_RELEASE_URL,
    FIRESTARTER_RELEASES_URL,
    FLAG_FORCE,
)
from firestarter.exceptions import (
    FirmwareOperationError,  # wired by the USB DFU install path (was D-01 orphan)
    FirmwareOutdatedError,
    ProgrammerNotFoundError,
    SerialError,
)
from firestarter.serial_comm import SerialCommunicator

logger = logging.getLogger("Firmware")

# Phase 18: FIRMWARE_VERSION_RE validates consumer-side --firmware-version input.
# Superset of Phase 15's BETA_VERSION_RE (which validates publisher-side input).
# D-07: accepts stable (X.Y.Z) and pre-release (X.Y.ZbN, X.Y.ZrcN) forms.
# Note: anchor with \Z (not $) — $ matches before a trailing \n in Python,
# letting "3.1.0\n" sneak through and corrupt the URL template downstream.
# Fixed 2026-05-20 per Phase 18 code review CR-02.
FIRMWARE_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+((b|rc)[0-9]+)?\Z")

# Used by _fetch_all_releases to follow pagination Link headers.
_LINK_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


class ReleaseInfo(TypedDict):
    """Structured release entry returned by list_releases (D-12 schema)."""

    version: str  # tag_name from GitHub API
    tag: str  # raw tag_name (same as version for firmware releases)
    channel: str  # "stable" or "prerelease"
    published: str  # ISO-8601 from published_at field
    asset_url: str  # browser_download_url for the board-matching .hex asset


HOME_PATH = os.path.join(os.path.expanduser("~"), ".firestarter")

# ---------------------------------------------------------------------------
# Board → install method
#
# AVR boards are flashed by avrdude over their serial bootloader. PY32F071 has
# no AVR bootloader and no avrdude support: it is flashed over USB DFU by
# firestarter/py32_dfu.py, which needs no external binary. Unknown boards
# default to avrdude so adding another AVR variant needs no change here.
# ---------------------------------------------------------------------------

FLASH_METHOD_AVRDUDE = "avrdude"
FLASH_METHOD_DFU = "dfu"

_BOARD_FLASH_METHODS = {
    "uno": FLASH_METHOD_AVRDUDE,
    "uno328pb": FLASH_METHOD_AVRDUDE,
    "leonardo": FLASH_METHOD_AVRDUDE,
    "py32f071": FLASH_METHOD_DFU,
}

# Methods that talk to the chip over USB directly and therefore need no serial
# port. A board sitting in its DFU bootloader exposes no CDC port at all, so
# manage_firmware_update must not demand one.
_PORTLESS_FLASH_METHODS = frozenset({FLASH_METHOD_DFU})


# HOST-01 / D-17 — accepted deviation, not a defect to fix.
#
# The milestone plan prescribed extracting a flasher-strategy class hierarchy
# (one strategy per install mechanism) when the py32 DFU install path landed.
# What actually shipped is the small board -> method lookup above plus this
# `flash_method()` router. That is a deliberate, accepted deviation: it does
# the same dispatch job with far less new surface, and `_install_with_avrdude`
# below is left completely untouched — rewriting it into a strategy object is
# explicitly out of scope for Phase 127 (and for whatever lands next).
#
# The pending todo `avrdude-mcu-detection-fallback` was reviewed during Phase
# 127 and deliberately not folded into this router, precisely because it
# targets `_install_with_avrdude`, the frozen function this deviation protects.
#
# See `.planning/phases/127-host-dfu-installer/127-NONREGRESSION.md` for the
# phase evidence artifact carrying this same record.
def flash_method(board: Optional[str]) -> str:
    """Return the install method for a board name (case-insensitive)."""
    return _BOARD_FLASH_METHODS.get((board or "").lower(), FLASH_METHOD_AVRDUDE)


def asset_candidates(board: str) -> List[str]:  # noqa: UP006
    """Release-asset filenames to accept for a board, most preferred first.

    Every board — AVR and DFU alike — publishes `firestarter_<board>.hex`, so
    that is what a release lookup prefers. Intel HEX is also the safer of the two
    for a DFU write: it carries its own load address, which `load_image()` reads
    and the flash-envelope guard then validates, whereas a raw `.bin` can only be
    *assumed* to start at `FLASH_BASE`.

    DFU boards additionally accept a raw `.bin`, which is what a local CMake
    build produces alongside the hex, so a developer can flash an unreleased
    image without converting it.
    """
    if flash_method(board) == FLASH_METHOD_DFU:
        return [f"firestarter_{board}.hex", f"firestarter_{board}.bin"]
    return [f"firestarter_{board}.hex"]


def _asset_label(board: str) -> str:
    """Human-readable asset name(s) for log messages."""
    names = asset_candidates(board)
    return " or ".join(repr(name) for name in names)


def _pick_asset(assets: object, board: str) -> Optional[str]:
    """Resolve the download URL of the first matching asset, else None."""
    if not isinstance(assets, list):
        return None
    by_name = {}
    for asset in assets:
        if isinstance(asset, dict):
            by_name[asset.get("name")] = asset.get("browser_download_url")
    for name in asset_candidates(board):
        url = by_name.get(name)
        if url:
            return url
    return None


class FirmwareManager:
    """
    Manages firmware-related operations for the EPROM programmer.
    This includes checking the current firmware version on the device,
    fetching information about the latest available release, comparing versions,
    downloading firmware files, and orchestrating the installation process
    using an Avrdude utility wrapper.
    """

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager

    def check_current_firmware(
        self,
        preferred_port: str | None = None,
        flags: int = 0,
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:  # noqa: UP006
        """
        Checks the currently installed firmware version on the programmer.
        Returns: (port_name, current_version, board_name) or (None, None, None) on failure.

        ``allow_outdated_firmware=True`` is passed deliberately and is the
        whole point of this method existing separately from every other
        connect. Outdated firmware is this path's SUBJECT, not an error
        condition: the version gate in ``_probe_port`` refuses firmware whose
        ack predates the CAP-02 identity tail, and applying that refusal here
        deadlocked the updater — `fw`, `fw --install` and `fw --force` all
        aborted before the update decision, on firmware whose version was
        sitting in the very next ack ("FW: <version>:<board>", parsed below).
        The waiver is scoped to the version refusals only; the shield-revision
        gate is untouched, and the chip-operation paths still refuse because
        they never pass this argument.
        """  # noqa: E501
        logger.info("Reading current firmware version...")
        command_dict = {"state": COMMAND_FW_VERSION}
        if flags:
            command_dict["flags"] = flags
        comm = None
        try:
            comm = SerialCommunicator.find_and_connect(
                command_dict,
                self.config_manager,
                preferred_port=preferred_port,
                allow_outdated_firmware=True,
            )
            # find_and_connect gets the initial OK from the programmer.
            # The firmware then executes the fw_version command and sends a second OK with the payload.  # noqa: E501
            is_ok, msg = comm.expect_ack()

            # Firmware emits the legacy text line "OK: FW: <version>:<board>"
            # (LFW-05). _parse_response_line strips the "OK:" prefix, so the
            # payload reaching us here is "FW: <version>:<board>". Strip the
            # secondary "FW:" tag before splitting on the colons.
            payload = None
            if is_ok and msg:
                payload = msg[3:].lstrip() if msg.startswith("FW:") else msg

            if payload and ":" in payload:
                # Identity is "<version>:<board>[:<buf>[:<maxchunk>]]". Only the
                # version and board are user-facing / release-relevant; <buf> and
                # <maxchunk> are wire-negotiation fields (Phase 53/54) that must
                # not leak into the printout or the firmware-release asset lookup
                # (firestarter_<board>.hex). Take only the first two fields.
                parts = payload.split(":")
                current_version = parts[0].strip()
                board_name = parts[1].strip()

                logger.info(
                    f"Current firmware version: {current_version}, for controller: {board_name} on port {comm.port_name}"  # noqa: E501
                )
                return comm.port_name, current_version, board_name
            else:
                logger.error(
                    f"Failed to read firmware version: Invalid response from programmer: '{msg}'"  # noqa: E501
                )
                return None, None, None
        except FirmwareOutdatedError:
            raise  # Phase 6 (LHOST-04): surface lockstep refuse to operator (do NOT swallow)  # noqa: E501
        except (ProgrammerNotFoundError, SerialError) as e:
            logger.error(f"Failed to read firmware version: {e}")
            return None, None, None
        finally:
            if comm:
                comm.disconnect()

    def fetch_latest_release_info(
        self, board: str = "uno"
    ) -> Tuple[Optional[str], Optional[str]]:  # noqa: UP006
        """
        Fetches the latest firmware version and download URL for the specified board.
        Returns: (latest_version_str, download_url_str) or (None, None) on failure.

        Stable-only path; use fetch_release_info for general channel selection.
        """
        logger.debug(f"Fetching latest firmware release for board: {board}...")
        try:
            response = requests.get(FIRESTARTER_RELEASE_URL, timeout=10)
            response.raise_for_status()  # Raise an exception for HTTP errors
            release_data = response.json()
            latest_version = release_data.get("tag_name")
            download_url = _pick_asset(release_data.get("assets", []), board)

            if not latest_version or not download_url:
                logger.error(
                    f"Could not find firmware version or URL for board '{board}' in the latest release."  # noqa: E501
                )
                return None, None

            logger.debug(
                f"Latest firmware version for {board}: {latest_version}, URL: {download_url}"  # noqa: E501
            )
            return latest_version, download_url
        except requests.RequestException as e:
            logger.error(f"Failed to fetch latest firmware release information: {e}")
            return None, None

    def _compare_versions(
        self, current_version_str: str | None, latest_version_str: str | None
    ) -> bool:
        """Compares two version strings using PEP 440 ordering via packaging.version.Version.

        Returns True if current >= latest. Handles pre-release strings (e.g., '3.1.0b1',
        '3.1.0rc2', '2.0.7_dev') correctly. Returns False on unparseable input.
        """  # noqa: E501
        if not current_version_str or not latest_version_str:
            return False  # Cannot compare if one is missing
        try:
            return Version(current_version_str) >= Version(latest_version_str)
        except InvalidVersion:
            logger.warning(
                f"Could not parse version strings for comparison: '{current_version_str}', '{latest_version_str}'"  # noqa: E501
            )
            return False  # Treat as not up-to-date if parsing fails

    def _fetch_all_releases(self, max_pages: int = 5) -> list:
        """Paginate GET /releases via Link: rel="next" headers. Cap at max_pages (D-04).

        Returns a flat list of all release dicts from all pages fetched.
        Logs INFO when the cap is hit so operators know truncation occurred.
        """
        url = FIRESTARTER_RELEASES_URL
        all_releases = []
        pages_fetched = 0

        while url and pages_fetched < max_pages:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            page_data = response.json()
            all_releases.extend(page_data)
            pages_fetched += 1

            # Follow Link: rel="next" header per GitHub pagination spec.
            link_header = response.headers.get("Link", "")
            match = _LINK_NEXT_RE.search(link_header)
            url = match.group(1) if match else None

        if url and pages_fetched >= max_pages:
            logger.info(
                f"Firmware release list capped at {max_pages} pages "
                f"({max_pages * 30} releases). More releases may exist."
            )

        return all_releases

    def fetch_release_info(
        self,
        channel: Literal["stable", "pre", "pinned"] = "stable",
        version: Optional[str] = None,
        board: str = "uno",
    ) -> Tuple[Optional[str], Optional[str]]:  # noqa: UP006
        """Router: returns (resolved_version, download_url) or (None, None) on failure.

        channel='stable'  → delegates to fetch_latest_release_info (D-15 back-compat shim).
        channel='pre'     → paginates /releases, filters prerelease=True, sorts by PEP 440
                            descending, takes highest; falls back to stable if none (D-05).
        channel='pinned'  → fetches /releases/tags/{version} directly (D-09).
        """  # noqa: E501
        if channel == "stable":
            return self.fetch_latest_release_info(board=board)

        elif channel == "pinned":
            if not version:
                logger.error(
                    "fetch_release_info(channel='pinned') requires a version string."
                )
                return None, None
            url = FIRESTARTER_RELEASE_BY_TAG_URL.format(tag=version)
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                release_data = response.json()
            except requests.RequestException as e:
                logger.error(
                    f"Failed to fetch firmware release for tag {version!r}: {e}"
                )
                return None, None

            download_url = _pick_asset(release_data.get("assets", []), board)
            if not download_url:
                logger.error(
                    f"Release {version!r} has no asset {_asset_label(board)} for board {board!r}."  # noqa: E501
                )
                return None, None
            return release_data.get("tag_name"), download_url

        elif channel == "pre":
            try:
                all_releases = self._fetch_all_releases()
            except requests.RequestException as e:
                logger.error(f"Failed to fetch releases for pre-release channel: {e}")
                return None, None

            # Filter: prerelease=True AND draft=False, skip unparseable tags.
            candidates = []
            for r in all_releases:
                if not r.get("prerelease") or r.get("draft"):
                    continue
                tag = r.get("tag_name", "")
                try:
                    candidates.append((Version(tag), r))
                except InvalidVersion:
                    logger.warning(f"Skipping release with unparseable tag: {tag!r}")

            if not candidates:
                logger.info(
                    "No pre-release firmware available — falling back to stable "
                    "(matches pip --pre semantics)."
                )
                return self.fetch_latest_release_info(board=board)

            # Sort descending by PEP 440 version, take highest.
            candidates.sort(key=lambda t: t[0], reverse=True)
            _, picked = candidates[0]

            download_url = _pick_asset(picked.get("assets", []), board)
            if not download_url:
                logger.error(
                    f"Pre-release {picked.get('tag_name')!r} has no asset "
                    f"{_asset_label(board)} for board {board!r}."
                )
                return None, None
            return picked.get("tag_name"), download_url

        else:
            logger.error(
                f"Unknown firmware channel {channel!r}; expected 'stable', 'pre', or 'pinned'."  # noqa: E501
            )
            return None, None

    def list_releases(
        self,
        channel_filter: Literal["all", "pre", "stable"] = "all",
        board: str = "uno",
    ) -> List[ReleaseInfo]:  # noqa: UP006
        """Enumerate available firmware releases sorted by PEP 440 version descending.

        Omits draft releases and releases without a board-matching .hex asset.
        Omits releases whose tag_name cannot be parsed by packaging.version.Version.

        channel_filter='all'    → include stable + prerelease (D-13).
        channel_filter='pre'    → prerelease only.
        channel_filter='stable' → stable only.

        Returns a flat list of ReleaseInfo dicts (D-12 schema: version, tag, channel,
        published, asset_url).
        """
        try:
            all_releases = self._fetch_all_releases()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch releases for list: {e}")
            return []

        out: List[ReleaseInfo] = []  # noqa: UP006
        for r in all_releases:
            # Skip drafts.
            if r.get("draft"):
                continue

            is_pre = bool(r.get("prerelease"))

            # Apply channel filter.
            if channel_filter == "pre" and not is_pre:
                continue
            if channel_filter == "stable" and is_pre:
                continue

            # Skip unparseable tags.
            tag = r.get("tag_name", "")
            try:
                Version(tag)
            except InvalidVersion:
                logger.warning(f"Skipping release with unparseable tag: {tag!r}")
                continue

            # Resolve board-matching asset.
            asset_url = _pick_asset(r.get("assets", []), board)
            if not asset_url:
                continue  # Silently omit releases without the board asset (D-11).

            out.append(
                ReleaseInfo(
                    version=tag,
                    tag=tag,
                    channel="prerelease" if is_pre else "stable",
                    published=r.get("published_at") or "",
                    asset_url=asset_url,
                )
            )

        out.sort(key=lambda entry: Version(entry["version"]), reverse=True)
        return out

    def _download_firmware_file(self, url: str) -> Optional[str]:
        """Downloads firmware from the URL and saves it to a temporary local path."""
        logger.info(f"Downloading firmware from {url}...")
        start_time = time.time()
        try:
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()

            if not os.path.exists(HOME_PATH):
                os.makedirs(HOME_PATH)

            # Extract filename from URL or use a default
            filename = (
                url.split("/")[-1]
                if url.split("/")[-1]
                else "firestarter_downloaded.hex"
            )
            firmware_path = os.path.join(HOME_PATH, filename)

            with open(firmware_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info(
                f"Firmware downloaded to: {firmware_path} ({time.time() - start_time:.2f}s)"  # noqa: E501
            )
            return firmware_path
        except requests.RequestException as e:
            logger.error(f"Error downloading firmware: {e}")
            return None
        except IOError as e:  # noqa: UP024
            logger.error(f"Error saving downloaded firmware: {e}")
            return None

    def _install_with_avrdude(
        self,
        hex_file_path: str,
        board: str,
        avrdude_path_override: Optional[str],
        avrdude_config_override: Optional[str],
        target_port: Optional[str],
    ) -> bool:
        """Internal method to perform the Avrdude flashing process."""
        start_time = time.time()

        partno, programmer_id, baud_rate = (
            "atmega328p",
            "arduino",
            115200,
        )  # Defaults for uno
        if board.lower() == "leonardo":
            partno, programmer_id, baud_rate = ("atmega32u4", "avr109", 57600)
        elif board.lower() == "uno328pb":
            # Phase 21 D-10 hand-off: ATmega328PB signature 0x1E 0x95 0x16 differs
            # from 328P's 0x1E 0x95 0x0F -- partno must be "atmega328pb" exactly,
            # else avrdude aborts on signature mismatch. programmer_id "urclock"
            # matches MiniCore's stock Urclock bootloader on operator's 328PB-Uno
            # (bench-validated 2026-05-21 — "arduino" was the initial guess from
            # Phase 23 CONTEXT D-02 but the bootloader rejected it; this is the
            # documented 1-line contingency swap).
            partno, programmer_id, baud_rate = ("atmega328pb", "urclock", 115200)

        # Determine port: use target_port if provided, else try saved port, else scan.
        # This logic might be better in SerialCommunicator._list_potential_ports if generalized.  # noqa: E501
        ports_to_try = []
        if target_port:
            ports_to_try.append(target_port)
        else:
            saved_port = self.config_manager.get_value("port")
            if saved_port:
                ports_to_try.append(saved_port)
            # Add scanned ports if no specific/saved port works
            # This part is tricky as avrdude needs a specific port.
            # For now, let's assume if target_port is None, we use the saved port or fail.  # noqa: E501
            # A more robust solution would involve listing ports and trying.
            # The original code iterated `find_comports()`.
            # For simplicity here, we'll prioritize `target_port` then configured port.
            # If neither, this will likely fail unless Avrdude can auto-detect (unlikely for all setups).  # noqa: E501
            # Let's assume `target_port` will be the one identified by `check_current_firmware` if not overridden.  # noqa: E501

        if (
            not ports_to_try and not target_port
        ):  # If target_port was None and no saved port
            logger.error(
                "No specific port provided for Avrdude and no port saved in config. Please specify a port."  # noqa: E501
            )
            # Could attempt to list ports here, but Avrdude needs one.
            # For now, require a port to be known.
            # The `firmware_check` in original code returned the port.
            # The calling method `manage_firmware_update` should pass this port.
            if (
                not target_port
            ):  # This check is redundant if the logic above is followed.
                logger.error("Target port for Avrdude is unknown.")
                return False
            ports_to_try.append(target_port)  # Should be set by caller

        avrdude_path = avrdude_path_override or self.config_manager.get_value(
            "avrdude-path"
        )
        avrdude_config_path = avrdude_config_override or self.config_manager.get_value(
            "avrdude-config-path"
        )

        for port_to_flash in ports_to_try:  # Usually, this will be a single port.
            logger.info(
                f"Attempting to flash firmware to {board} on port {port_to_flash} using Avrdude..."  # noqa: E501
            )
            try:
                avrdude = Avrdude(
                    partno=partno,
                    programmer_id=programmer_id,
                    baud_rate=baud_rate,
                    port=port_to_flash,
                    avrdude_path=avrdude_path,
                    avrdude_config_path=avrdude_config_path,
                )

                stderr_output, return_code = avrdude.flash_firmware(hex_file_path)
                if return_code == 0:
                    logger.info(
                        f"Firmware successfully updated on {port_to_flash} ({time.time() - start_time:.2f}s)"  # noqa: E501
                    )
                    self.config_manager.set_value(
                        "port", port_to_flash
                    )  # Save successful port
                    if (
                        avrdude_path_override is None
                    ):  # Only save if not overridden by user for this run
                        self.config_manager.set_value("avrdude-path", avrdude.command)
                    if avrdude_config_override is None and avrdude.config:
                        self.config_manager.set_value(
                            "avrdude-config-path", str(avrdude.config.absolute())
                        )
                    return True
                else:
                    logger.error(
                        f"Firmware update failed on port {port_to_flash}. Avrdude stderr:"  # noqa: E501
                    )
                    for line in stderr_output.splitlines():
                        logger.error(f"  {line}")
            except (AvrdudeNotFoundError, AvrdudeConfigNotFoundError) as e:
                logger.error(f"Avrdude setup error for port {port_to_flash}: {e}")
            except (
                Exception
            ) as e:  # Catch other potential errors from Avrdude instantiation/execution
                logger.error(
                    f"An unexpected error occurred with Avrdude on port {port_to_flash}: {e}"  # noqa: E501
                )

        logger.error("Firmware installation failed on all attempted ports.")
        return False

    def _install_with_dfu(
        self,
        image_path: str,
        board: str,
        usb_id: Optional[str] = None,
    ) -> bool:
        """Install firmware over the board's USB DFU bootloader.

        No external flashing binary is involved — the DFU transfer is
        implemented in `firestarter/py32_dfu.py` on top of pyusb. The board must
        already be in bootloader mode; see `doc/PY32F071-FIRMWARE-INSTALL.md` for
        the three ways to get it there.
        """
        # Channel gate enforced here, not only in the CLI: this is the single
        # choke point every DFU install passes through, including library callers
        # that never touch Click.
        if not is_board_available(board):
            raise FirmwareOperationError(beta_only_message(board))

        # Imported lazily so a missing pyusb only affects DFU boards.
        from firestarter.py32_dfu import DfuError, Py32DfuFlasher, VerifyResult

        start_time = time.time()
        logger.info(f"Installing firmware on {board} over USB DFU...")
        try:
            flasher = Py32DfuFlasher(usb_id=usb_id)
            ok = flasher.flash(image_path)
        except DfuError as e:
            # DfuError carries an operator-actionable message (how to enter the
            # bootloader, or how to install pyusb) — surface it as the module's
            # own error type rather than leaking a USB-layer exception.
            #
            # HOST-03 / D-11: a genuine MISMATCH never reaches this `if ok:`
            # branch below — _verify_readback() raises DfuProtocolError (a
            # DfuError), which lands here and is converted to
            # FirmwareOperationError, which the CLI's map_typed_errors
            # decorator turns into a ClickException and exit 1. Do not
            # "helpfully" downgrade a mismatch to a warning below: soft-fail
            # is reserved for *could not verify*, never *verified and it was
            # wrong*.
            raise FirmwareOperationError(str(e)) from e

        elapsed = time.time() - start_time
        if ok:
            if flasher.verify_result is VerifyResult.VERIFIED:
                logger.info(
                    f"Firmware successfully updated on {board} and verified "
                    f"via DFU_UPLOAD readback ({elapsed:.2f}s)"
                )
            else:
                # SKIPPED_NO_UPLOAD or SKIPPED_PLAIN_DFU — the write completed
                # but could not be verified. Say so instead of reporting a
                # bare success.
                logger.warning(
                    f"Could not verify the write on {board}: {flasher.verify_reason}"
                )
                logger.info(
                    f"Firmware written but NOT verified on {board} ({elapsed:.2f}s)"
                )
        return ok

    @staticmethod
    def _hint_dfu_board() -> None:
        """If a USB DFU device is on the bus, say so — never raise.

        A PY32F071 in bootloader mode presents no serial port, so the generic
        "cannot determine port" error is misleading if the operator forgot
        `--board py32f071`.
        """
        try:
            from firestarter.py32_dfu import dfu_device_present

            if dfu_device_present():
                logger.info(
                    "A USB DFU device is attached. If this is a PY32F071 board in "
                    "bootloader mode, install with: firestarter fw --install "
                    "--board py32f071"
                )
        except Exception:  # noqa: BLE001 — a hint must never break the flow
            pass

    @staticmethod
    def probe_dfu(usb_id: Optional[str] = None) -> List[str]:  # noqa: UP006
        """Describe attached DFU devices. Raises FirmwareOperationError on failure.

        This is the instrument for the first bench session with real PY32F071
        silicon: it reports the USB ID the bootloader presents, whether it speaks
        DfuSe or plain DFU 1.1, its transfer size and its erase-sector geometry —
        the facts this module currently has to discover at runtime because no
        board exists to confirm them.
        """
        from firestarter.channel import is_prerelease_build

        if not is_prerelease_build():
            raise FirmwareOperationError(beta_only_message("py32f071"))

        from firestarter.py32_dfu import DfuError, Py32DfuFlasher

        try:
            return Py32DfuFlasher(usb_id=usb_id).probe()
        except DfuError as e:
            raise FirmwareOperationError(str(e)) from e

    def _install_firmware(
        self,
        hex_file_path: str,
        board: str,
        avrdude_path_override: Optional[str],
        avrdude_config_override: Optional[str],
        target_port: Optional[str],
        usb_id: Optional[str] = None,
    ) -> bool:
        """Dispatch to the flasher for this board.

        The AVR path is unchanged and still owns its own port/profile handling;
        only the choice of flasher moved out of `manage_firmware_update`.
        """
        if flash_method(board) == FLASH_METHOD_DFU:
            return self._install_with_dfu(hex_file_path, board, usb_id=usb_id)
        return self._install_with_avrdude(
            hex_file_path=hex_file_path,
            board=board,
            avrdude_path_override=avrdude_path_override,
            avrdude_config_override=avrdude_config_override,
            target_port=target_port,
        )

    def manage_firmware_update(
        self,
        install_flag: bool = False,
        avrdude_path_override: Optional[str] = None,
        avrdude_config_override: Optional[str] = None,
        port_override: Optional[str] = None,
        board_override: Optional[str] = "uno",
        flags: int = 0,
        channel: Literal["stable", "pre", "pinned"] = "stable",
        pinned_version: Optional[str] = None,
        usb_id: Optional[str] = None,
        board_explicit: bool = False,
    ) -> bool:
        """
        Manages the firmware update process: checks version, prompts user, and installs if needed.
        Returns True if an operation (check or install) was successful in some sense, False on major failure.

        channel='stable'  → uses /releases/latest (default, INST-01 non-regression).
        channel='pre'     → selects highest pre-release (INST-02).
        channel='pinned'  → uses exact tag from pinned_version (INST-03).
        """  # noqa: E501
        connected_port, current_version, current_board = self.check_current_firmware(
            preferred_port=port_override, flags=flags
        )

        # Use board detected from firmware if available, else use CLI override or default  # noqa: E501
        # Resolved BEFORE the port check: a DFU board sitting in its bootloader
        # answers no identity query and exposes no serial port, so the board is
        # what decides whether a port is required at all.
        board_to_use = current_board or board_override

        # A detected board silently wins over --board. That is right when --board
        # was left at its default, and dangerous when it was not: asking for
        # --board py32f071 while an AVR programmer happens to be attached would
        # retarget the install at the AVR board and flash it. Refuse the conflict
        # instead of picking a side.
        if (
            board_explicit
            and current_board
            and board_override
            and current_board.lower() != board_override.lower()
        ):
            logger.error(
                f"--board {board_override} was requested but the programmer on "
                f"{connected_port} identifies as {current_board}. Refusing to "
                f"install: unplug the {current_board} board, or drop --board to "
                f"target it."
            )
            return False

        # Use the port where firmware was checked, or CLI override for flashing
        port_to_use = port_override or connected_port
        method = flash_method(board_to_use)
        if not port_to_use and method not in _PORTLESS_FLASH_METHODS:
            logger.error(
                "Cannot determine port for programmer. Please specify with --port."
            )
            self._hint_dfu_board()
            return False

        latest_version, download_url = self.fetch_release_info(
            channel=channel, version=pinned_version, board=board_to_use
        )

        force_install = flags & FLAG_FORCE
        if not current_version and not install_flag and not force_install:
            logger.error(
                "Could not determine current firmware version. Use --install or --force to proceed with installation."  # noqa: E501
            )
            return False  # Failed to get current version and no intent to install

        is_up_to_date = False
        if current_version and latest_version:
            is_up_to_date = self._compare_versions(current_version, latest_version)

        if is_up_to_date and not force_install:
            logger.info(
                f"Firmware is already up to date (version {current_version} for {board_to_use}). Use --force to reinstall."  # noqa: E501
            )
            return True  # Successfully checked, no update needed

        # Determine if installation should proceed
        should_install_now = False
        if force_install:
            logger.info(f"Forcing firmware installation for {board_to_use}.")
            should_install_now = True
        elif install_flag:
            if not is_up_to_date:
                logger.info(
                    f"Proceeding with firmware update for {board_to_use} (current: {current_version}, latest: {latest_version})."  # noqa: E501
                )
                should_install_now = True
            else:  # install_flag is true, but already up-to-date
                logger.info(
                    f"Firmware for {board_to_use} is already version {latest_version}. Use --force to reinstall."  # noqa: E501
                )
                # No installation needed unless forced
        elif (
            not is_up_to_date and current_version and latest_version
        ):  # No flags, but update available
            if Confirm.ask(
                f"New firmware {latest_version} available for {board_to_use} (current: {current_version}). Update now?",  # noqa: E501
                default=False,
            ):
                should_install_now = True
            else:
                logger.info("Firmware update cancelled by user.")
        elif not current_version and (
            install_flag or force_install
        ):  # No current version, but user wants to install
            logger.info(
                f"No current firmware version detected. Proceeding with installation of latest version for {board_to_use}."  # noqa: E501
            )
            should_install_now = True

        if should_install_now:
            if not download_url or not latest_version:
                logger.error(
                    f"Cannot install: latest firmware URL or version for {board_to_use} is not available."  # noqa: E501
                )
                return False

            hex_file = self._download_firmware_file(download_url)
            if not hex_file:
                logger.error("Firmware download failed. Installation aborted.")
                return False

            install_success = self._install_firmware(
                hex_file_path=hex_file,
                board=board_to_use,
                avrdude_path_override=avrdude_path_override,
                avrdude_config_override=avrdude_config_override,
                target_port=port_to_use,
                usb_id=usb_id,
            )
            # Clean up downloaded file
            if os.path.exists(hex_file):
                try:
                    os.remove(hex_file)
                    logger.debug(f"Cleaned up downloaded firmware: {hex_file}")
                except OSError as e:
                    logger.warning(
                        f"Could not remove temporary firmware file {hex_file}: {e}"
                    )
            return install_success

        return True  # No installation performed, but process completed as expected
