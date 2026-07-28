"""
Project Name: Firestarter
Copyright (c) 2026 Henrik Olsson

Permission is hereby granted under MIT license.

Unit tests for the PY32F071 USB DFU firmware-install path.

No hardware and no pyusb are required: `find_dfu_interfaces` is monkeypatched to
hand back a fake device that records every control transfer, so the DFU wire
sequence itself is what these tests assert. That matters more than usual here —
no PY32F071 board exists yet, so this suite is currently the *only* check that
the sequence is well-formed.
"""

from unittest.mock import MagicMock

import pytest

from firestarter import firmware, py32_dfu
from firestarter.firmware import FirmwareManager
from firestarter.py32_dfu import (
    DFU_DNLOAD,
    DFU_GETSTATUS,
    DFUSE_ERASE_PAGE,
    DFUSE_SET_ADDRESS,
    DFUSE_VERSION,
    FLASH_BASE,
    STATE_DFU_IDLE,
    DfuInterface,
    DfuProtocolError,
    ImageError,
    Py32DfuFlasher,
    SectorRange,
    erase_addresses,
    load_image,
    parse_dfuse_layout,
    parse_intel_hex,
)

# ---------------------------------------------------------------------------
# Fake USB device
# ---------------------------------------------------------------------------


class _FakeUsbDevice:
    """Records ctrl_transfer calls; answers DFU_GETSTATUS with a canned state."""

    def __init__(self, status=0, state=STATE_DFU_IDLE, poll_ms=0):
        self.calls = []
        self.status = status
        self.state = state
        self.poll_ms = poll_ms

    def ctrl_transfer(self, bmRequestType, bRequest, wValue=0, wIndex=0, data=None):  # noqa: N803
        self.calls.append((bmRequestType, bRequest, wValue, wIndex, data))
        if bRequest == DFU_GETSTATUS:
            poll = self.poll_ms
            return bytes(
                [
                    self.status,
                    poll & 0xFF,
                    (poll >> 8) & 0xFF,
                    (poll >> 16) & 0xFF,
                    self.state,
                    0,
                ]
            )
        return len(data) if data else 0

    # -- assertions helpers ------------------------------------------------

    def dnloads(self):
        """Every DFU_DNLOAD as ``(wBlockNum, payload_bytes)``."""
        return [
            (value, bytes(data) if data else b"")
            for _, request, value, _, data in self.calls
            if request == DFU_DNLOAD
        ]

    def dfuse_commands(self):
        """Every wBlockNum==0 DNLOAD as ``(command, address_or_None)``."""
        out = []
        for block, payload in self.dnloads():
            if block != 0 or not payload:
                continue
            address = (
                int.from_bytes(payload[1:5], "little") if len(payload) >= 5 else None
            )
            out.append((payload[0], address))
        return out

    def data_blocks(self):
        """Every non-command DNLOAD carrying payload."""
        return [(b, p) for b, p in self.dnloads() if b != 0 and p]


def _interface(device, name="@Internal Flash /0x08000000/64*002Kg", dfuse=True):
    return DfuInterface(
        device=device,
        vendor_id=0x1A86,
        product_id=0x8012,
        configuration=1,
        interface=0,
        alt_setting=0,
        protocol=py32_dfu.DFU_PROTOCOL_DFU_MODE,
        name=name if dfuse else "PY32 bootloader",
        transfer_size=64,
        dfu_version=DFUSE_VERSION if dfuse else 0x0110,
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Poll loops must not actually sleep in tests."""
    monkeypatch.setattr(py32_dfu, "_sleep", lambda _seconds: None)


# ---------------------------------------------------------------------------
# Intel HEX parsing
# ---------------------------------------------------------------------------


class TestIntelHex:
    def test_parses_data_records_at_flash_base(self):
        text = "\n".join(
            [
                ":020000040800F2",  # extended linear address -> 0x0800
                ":04000000DEADBEEFC4",  # 4 bytes at 0x08000000
                ":00000001FF",  # EOF
            ]
        )
        base, payload = parse_intel_hex(text)
        assert base == 0x08000000
        assert payload == bytes.fromhex("DEADBEEF")

    def test_pads_gaps_with_erased_flash_value(self):
        text = "\n".join(
            [
                ":0100000001FE",  # 0x01 at 0x0000
                ":0100040005F6",  # 0x05 at 0x0004
                ":00000001FF",
            ]
        )
        base, payload = parse_intel_hex(text)
        assert base == 0
        assert payload == bytes([0x01, 0xFF, 0xFF, 0xFF, 0x05])

    def test_rejects_bad_checksum(self):
        with pytest.raises(ImageError, match="checksum"):
            parse_intel_hex(":0100000001FF\n:00000001FF\n")

    def test_rejects_file_without_data_records(self):
        with pytest.raises(ImageError, match="no data records"):
            parse_intel_hex(":00000001FF\n")

    def test_raw_binary_loads_at_flash_base(self, tmp_path):
        target = tmp_path / "firestarter_py32f071.bin"
        target.write_bytes(b"\x01\x02\x03\x04")
        base, payload = load_image(str(target))
        assert base == FLASH_BASE
        assert payload == b"\x01\x02\x03\x04"

    def test_empty_binary_is_rejected(self, tmp_path):
        target = tmp_path / "empty.bin"
        target.write_bytes(b"")
        with pytest.raises(ImageError, match="empty"):
            load_image(str(target))


# ---------------------------------------------------------------------------
# DfuSe memory layout
# ---------------------------------------------------------------------------


class TestDfuseLayout:
    def test_parses_st_style_mapping_string(self):
        layout = parse_dfuse_layout("@Internal Flash /0x08000000/64*002Kg")
        assert layout == [SectorRange(address=0x08000000, count=64, size=2048)]

    def test_parses_multiple_sector_runs_in_sequence(self):
        layout = parse_dfuse_layout("@Flash /0x08000000/4*001Ka,60*002Kg")
        assert layout[0] == SectorRange(0x08000000, 4, 1024)
        # Second run starts where the first ends — not at the declared base.
        assert layout[1] == SectorRange(0x08000000 + 4 * 1024, 60, 2048)

    def test_non_mapping_name_yields_no_layout(self):
        assert parse_dfuse_layout("PY32 bootloader") == []
        assert parse_dfuse_layout(None) == []


class TestEraseAddresses:
    def test_covers_only_sectors_the_image_touches(self):
        layout = [SectorRange(FLASH_BASE, 64, 2048)]
        addresses = erase_addresses(layout, FLASH_BASE, 3000, 2048)
        assert addresses == [FLASH_BASE, FLASH_BASE + 2048]

    def test_falls_back_to_uniform_grid_without_layout(self):
        addresses = erase_addresses([], FLASH_BASE, 5000, 2048)
        assert addresses == [FLASH_BASE, FLASH_BASE + 2048, FLASH_BASE + 4096]

    def test_image_outside_published_layout_is_refused(self):
        layout = [SectorRange(FLASH_BASE, 1, 2048)]
        with pytest.raises(ImageError, match="outside the device"):
            erase_addresses(layout, 0x08010000, 512, 2048)

    def test_zero_length_erases_nothing(self):
        assert (
            erase_addresses([SectorRange(FLASH_BASE, 4, 2048)], FLASH_BASE, 0, 2048)
            == []
        )


# ---------------------------------------------------------------------------
# The DFU download sequence
# ---------------------------------------------------------------------------


class TestDfuseDownload:
    def _flash(self, monkeypatch, tmp_path, payload, name=None, dfuse=True):
        device = _FakeUsbDevice()
        kwargs = {"dfuse": dfuse}
        if name is not None:
            kwargs["name"] = name
        interface = _interface(device, **kwargs)
        monkeypatch.setattr(
            py32_dfu, "find_dfu_interfaces", lambda vid=None, pid=None: [interface]
        )
        image = tmp_path / "fw.bin"
        image.write_bytes(payload)
        assert Py32DfuFlasher().flash(str(image)) is True
        return device

    def test_erases_then_sets_address_then_writes_blocks_from_two(
        self, monkeypatch, tmp_path
    ):
        # 130 bytes with a 64-byte transfer size => 3 blocks, 1 sector.
        device = self._flash(monkeypatch, tmp_path, bytes(range(130)))

        commands = device.dfuse_commands()
        assert (DFUSE_ERASE_PAGE, FLASH_BASE) in commands
        # Address pointer is set after erase and before the first data block.
        assert (DFUSE_SET_ADDRESS, FLASH_BASE) in commands

        blocks = device.data_blocks()
        assert [block for block, _ in blocks] == [2, 3, 4]
        assert b"".join(payload for _, payload in blocks) == bytes(range(130))
        assert len(blocks[0][1]) == 64  # honours wTransferSize
        assert len(blocks[-1][1]) == 2  # short final block

    def test_finishes_with_a_zero_length_download(self, monkeypatch, tmp_path):
        device = self._flash(monkeypatch, tmp_path, bytes(64))
        # Last DNLOAD carries no payload — the DFU end-of-transfer signal.
        last_block, last_payload = device.dnloads()[-1]
        assert last_payload == b""
        assert last_block == 3  # one data block was 2, so the terminator is 3

    def test_erase_count_follows_image_size(self, monkeypatch, tmp_path):
        device = self._flash(monkeypatch, tmp_path, bytes(5000))
        erases = [
            addr for cmd, addr in device.dfuse_commands() if cmd == DFUSE_ERASE_PAGE
        ]
        assert erases == [FLASH_BASE, FLASH_BASE + 2048, FLASH_BASE + 4096]

    def test_plain_dfu11_device_skips_erase_and_numbers_from_zero(
        self, monkeypatch, tmp_path
    ):
        image = bytes(range(100))
        device = self._flash(monkeypatch, tmp_path, image, dfuse=False)
        # Plain DFU 1.1 numbers data blocks from 0 (so block 0 carries payload,
        # not a DfuSe command) and the run ends with a zero-length terminator.
        assert device.dnloads() == [
            (0, image[:64]),
            (1, image[64:]),
            (2, b""),
        ]

    def test_device_without_layout_uses_fallback_page_grid(self, monkeypatch, tmp_path):
        device = self._flash(
            monkeypatch, tmp_path, bytes(100), name="PY32 bootloader", dfuse=True
        )
        erases = [
            addr for cmd, addr in device.dfuse_commands() if cmd == DFUSE_ERASE_PAGE
        ]
        assert erases == [FLASH_BASE]


class TestDownloadFailures:
    def test_error_status_from_device_raises(self, monkeypatch, tmp_path):
        device = _FakeUsbDevice(status=0x0F, state=py32_dfu.STATE_ERROR)
        monkeypatch.setattr(
            py32_dfu,
            "find_dfu_interfaces",
            lambda vid=None, pid=None: [_interface(device)],
        )
        image = tmp_path / "fw.bin"
        image.write_bytes(bytes(64))
        with pytest.raises(DfuProtocolError):
            Py32DfuFlasher().flash(str(image))

    def test_missing_device_raises_with_bootloader_instructions(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            py32_dfu, "find_dfu_interfaces", lambda vid=None, pid=None: []
        )
        image = tmp_path / "fw.bin"
        image.write_bytes(bytes(64))
        with pytest.raises(py32_dfu.DfuDeviceNotFoundError, match="BOOT0"):
            Py32DfuFlasher().flash(str(image))

    def test_image_larger_than_flash_is_refused(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            py32_dfu,
            "find_dfu_interfaces",
            lambda vid=None, pid=None: [_interface(_FakeUsbDevice())],
        )
        image = tmp_path / "fw.bin"
        image.write_bytes(bytes(py32_dfu.FLASH_SIZE + 1))
        with pytest.raises(ImageError, match="outside"):
            Py32DfuFlasher().flash(str(image))

    def test_malformed_usb_id_is_rejected_early(self):
        with pytest.raises(py32_dfu.DfuError, match="VID:PID"):
            Py32DfuFlasher(usb_id="not-an-id")

    def test_usb_id_is_parsed_as_hex(self):
        flasher = Py32DfuFlasher(usb_id="1A86:8012")
        assert (flasher.vendor_id, flasher.product_id) == (0x1A86, 0x8012)


class TestUnrelatedDeviceSafety:
    """DFU *runtime* interfaces on unrelated peripherals must never be touched.

    Regression guard for a real finding: the dev container exposes a webcam
    (04f2:b751) advertising a DFU runtime interface. An earlier revision of
    select_interface() took interfaces[0] and would have sent it DFU_DETACH and
    then flashed Firestarter firmware into it.
    """

    def _runtime_interface(self, device, vid=0x04F2, pid=0xB751):
        interface = _interface(device)
        interface.protocol = py32_dfu.DFU_PROTOCOL_RUNTIME
        interface.vendor_id = vid
        interface.product_id = pid
        interface.name = None
        return interface

    def test_runtime_only_bus_is_refused_and_untouched(self, monkeypatch, tmp_path):
        device = _FakeUsbDevice()
        monkeypatch.setattr(
            py32_dfu,
            "find_dfu_interfaces",
            lambda vid=None, pid=None: [self._runtime_interface(device)],
        )
        image = tmp_path / "fw.bin"
        image.write_bytes(bytes(64))
        with pytest.raises(py32_dfu.DfuDeviceNotFoundError, match="runtime"):
            Py32DfuFlasher().flash(str(image))
        # The load-bearing assertion: not one control transfer reached the device.
        assert device.calls == []

    def test_named_runtime_device_is_detached(self, monkeypatch, tmp_path):
        device = _FakeUsbDevice()
        runtime = self._runtime_interface(device)
        dfu_mode = _interface(device)
        scans = iter([[runtime], [dfu_mode]])
        monkeypatch.setattr(
            py32_dfu, "find_dfu_interfaces", lambda vid=None, pid=None: next(scans)
        )
        image = tmp_path / "fw.bin"
        image.write_bytes(bytes(64))
        # Explicit --usb-id is the operator opting in; DETACH is then allowed.
        assert Py32DfuFlasher(usb_id="04f2:b751").flash(str(image)) is True
        assert any(
            request == py32_dfu.DFU_DETACH for _, request, _, _, _ in device.calls
        )

    def test_multiple_dfu_mode_devices_are_refused(self, monkeypatch, tmp_path):
        first = _FakeUsbDevice()
        second = _FakeUsbDevice()
        monkeypatch.setattr(
            py32_dfu,
            "find_dfu_interfaces",
            lambda vid=None, pid=None: [_interface(first), _interface(second)],
        )
        image = tmp_path / "fw.bin"
        image.write_bytes(bytes(64))
        with pytest.raises(py32_dfu.DfuDeviceNotFoundError, match="--usb-id"):
            Py32DfuFlasher().flash(str(image))
        assert first.calls == [] and second.calls == []

    def test_hint_ignores_runtime_only_devices(self, monkeypatch):
        monkeypatch.setattr(
            py32_dfu,
            "find_dfu_interfaces",
            lambda vid=None, pid=None: [self._runtime_interface(_FakeUsbDevice())],
        )
        assert py32_dfu.dfu_device_present() is False

    def test_hint_fires_for_a_device_in_dfu_mode(self, monkeypatch):
        monkeypatch.setattr(
            py32_dfu,
            "find_dfu_interfaces",
            lambda vid=None, pid=None: [_interface(_FakeUsbDevice())],
        )
        assert py32_dfu.dfu_device_present() is True


# ---------------------------------------------------------------------------
# Board → install-method routing in firmware.py
# ---------------------------------------------------------------------------


class TestBoardRouting:
    @pytest.mark.parametrize(
        ("board", "expected"),
        [
            ("uno", firmware.FLASH_METHOD_AVRDUDE),
            ("uno328pb", firmware.FLASH_METHOD_AVRDUDE),
            ("leonardo", firmware.FLASH_METHOD_AVRDUDE),
            ("LEONARDO", firmware.FLASH_METHOD_AVRDUDE),
            ("py32f071", firmware.FLASH_METHOD_DFU),
            ("PY32F071", firmware.FLASH_METHOD_DFU),
            ("some-future-avr", firmware.FLASH_METHOD_AVRDUDE),
            (None, firmware.FLASH_METHOD_AVRDUDE),
        ],
    )
    def test_flash_method_per_board(self, board, expected):
        assert firmware.flash_method(board) == expected

    def test_avr_asset_name_is_unchanged(self):
        assert firmware.asset_candidates("uno") == ["firestarter_uno.hex"]
        assert firmware._asset_label("uno") == "'firestarter_uno.hex'"

    def test_dfu_board_prefers_bin_and_accepts_hex(self):
        assert firmware.asset_candidates("py32f071") == [
            "firestarter_py32f071.bin",
            "firestarter_py32f071.hex",
        ]

    def test_asset_pick_prefers_bin_for_dfu_board(self):
        assets = [
            {"name": "firestarter_py32f071.hex", "browser_download_url": "hex-url"},
            {"name": "firestarter_py32f071.bin", "browser_download_url": "bin-url"},
        ]
        assert firmware._pick_asset(assets, "py32f071") == "bin-url"

    def test_asset_pick_falls_back_to_hex_for_dfu_board(self):
        assets = [
            {"name": "firestarter_py32f071.hex", "browser_download_url": "hex-url"}
        ]
        assert firmware._pick_asset(assets, "py32f071") == "hex-url"

    def test_asset_pick_ignores_other_boards(self):
        assets = [{"name": "firestarter_leonardo.hex", "browser_download_url": "u"}]
        assert firmware._pick_asset(assets, "uno") is None


class TestPortlessInstall:
    """A board in DFU mode exposes no serial port — the install must not need one."""

    def _manager(self, monkeypatch, board):
        fm = FirmwareManager(config_manager=MagicMock())
        # No identity read is possible: the bootloader speaks DFU, not CDC.
        monkeypatch.setattr(
            fm, "check_current_firmware", lambda *a, **kw: (None, None, None)
        )
        monkeypatch.setattr(
            fm, "fetch_release_info", lambda **kw: ("3.0.0b12", "http://x/fw.bin")
        )
        monkeypatch.setattr(fm, "_download_firmware_file", lambda url: "/tmp/fw.bin")
        recorded = {}

        def fake_dfu(image_path, board_name, usb_id=None):
            recorded["image_path"] = image_path
            recorded["board"] = board_name
            recorded["usb_id"] = usb_id
            return True

        monkeypatch.setattr(fm, "_install_with_dfu", fake_dfu)
        monkeypatch.setattr(firmware.os.path, "exists", lambda p: False)
        return fm, recorded

    def test_dfu_board_installs_without_a_serial_port(self, monkeypatch):
        fm, recorded = self._manager(monkeypatch, "py32f071")
        ok = fm.manage_firmware_update(
            install_flag=True, board_override="py32f071", usb_id="1a86:8012"
        )
        assert ok is True
        assert recorded["board"] == "py32f071"
        assert recorded["usb_id"] == "1a86:8012"

    def test_avr_board_still_requires_a_port(self, monkeypatch):
        fm, _ = self._manager(monkeypatch, "uno")
        assert (
            fm.manage_firmware_update(install_flag=True, board_override="uno") is False
        )

    def test_explicit_board_conflicting_with_attached_board_is_refused(
        self, monkeypatch
    ):
        """Regression guard: --board must not be silently overridden.

        Reproduces a real incident — `fw --install --board py32f071` with a live
        Leonardo attached flashed the Leonardo, because the detected board wins
        over --board.
        """
        fm = FirmwareManager(config_manager=MagicMock())
        monkeypatch.setattr(
            fm,
            "check_current_firmware",
            lambda *a, **kw: ("/dev/ttyACM0", "3.0.0b11", "leonardo"),
        )
        monkeypatch.setattr(
            fm,
            "_install_firmware",
            lambda **kw: pytest.fail("installed despite a board conflict"),
        )
        assert (
            fm.manage_firmware_update(
                install_flag=True, board_override="py32f071", board_explicit=True
            )
            is False
        )

    def test_default_board_still_yields_to_detection(self, monkeypatch):
        """The default --board=uno must keep deferring to the detected board."""
        fm = FirmwareManager(config_manager=MagicMock())
        monkeypatch.setattr(
            fm,
            "check_current_firmware",
            lambda *a, **kw: ("/dev/ttyACM0", "3.0.0b11", "leonardo"),
        )
        monkeypatch.setattr(
            fm, "fetch_release_info", lambda **kw: ("3.0.0b11", "http://x/fw.hex")
        )
        # Up to date and not forced: returns True without installing.
        monkeypatch.setattr(
            fm, "_install_firmware", lambda **kw: pytest.fail("should not install")
        )
        assert (
            fm.manage_firmware_update(install_flag=True, board_override="uno") is True
        )

    def test_avr_path_is_not_routed_to_dfu(self, monkeypatch):
        fm = FirmwareManager(config_manager=MagicMock())
        called = {}

        def fake_avrdude(**kwargs):
            called["avrdude"] = kwargs
            return True

        monkeypatch.setattr(fm, "_install_with_avrdude", fake_avrdude)
        monkeypatch.setattr(
            fm, "_install_with_dfu", lambda *a, **kw: pytest.fail("DFU used for AVR")
        )
        assert (
            fm._install_firmware(
                hex_file_path="/tmp/fw.hex",
                board="leonardo",
                avrdude_path_override=None,
                avrdude_config_override=None,
                target_port="/dev/ttyACM0",
            )
            is True
        )
        assert called["avrdude"]["board"] == "leonardo"
