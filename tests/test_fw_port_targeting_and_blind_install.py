"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Regression suite for three targeting defects found on the bench while exercising
every firmware-update path (debug session
`.planning/debug/fw-update-blocked-release-fw.md`, "SUPERSEDING FINDINGS").
They are independent of the CAP-02 waiver pinned in
`test_fw_update_path_gate.py`, and each was more dangerous than that one.

A — `--port` ORDERED the probe list instead of RESTRICTING it. When the named
port failed to answer, probing silently continued to the next port and the
caller was handed a different board's identity. `FirmwareManager` then combined
that identity with `port_to_use = port_override or connected_port`, so board A's
release asset was aimed at port B. Measured on the bench: with stable 2.0.6 on
/dev/ttyACM1 (which answers the handshake with `ERROR: Bad JSON`) and a
uno328pb on /dev/ttyUSB0, `fw --port /dev/ttyACM1 --install` downloaded
`firestarter_uno328pb.hex` and ran
`avrdude -p atmega328pb -c urclock -P /dev/ttyACM1`. Only avrdude's
part-signature check (0x1e950f is not m328pb) prevented a wrong-firmware flash;
two boards sharing an MCU and programmer would not get even that.

B — firmware too old to parse the current command framing can never be
identified, so it could never be updated. avrdude drives the BOOTLOADER, not the
firmware, so an install does not need the running image to be readable — but it
does need to know WHICH image to write, and with no identity `board_to_use`
collapses to the `--board` default. A blind install is therefore allowed only
when the operator names the board.

Measured framing boundary (bench, 2026-08-19): stable `2.0.6` AND pre-release
`3.0.0b4` both answer the handshake with `ERROR: Bad JSON`, so the probe returns
None and no identity is obtainable. `3.0.0b11` does NOT — it acks, just without
the CAP-02 identity tail, which is the *waiver* case in
`test_fw_update_path_gate.py` rather than this one. The boundary is the COBS
pivot at firmware b8, so the two defects are genuinely distinct populations:
pre-b8 firmware needs the blind install below, b8..b18 needs the waiver.

The blind path is proven on all three flash methods on real hardware: `arduino`
(uno, 2.0.6 → b19), `avr109` (leonardo, 2.0.6 → b19 — this one also exercises
Avrdude._trigger_reset's 1200-baud touch, which only runs for atmega32u4), and
`urclock` (uno328pb, from b4 — release lookup rate-limited mid-test, so that
board was restored with avrdude directly and the urclock blind leg is proven only
as far as the download boundary).

C — `set_value(..., persist=False)` was not honoured across a later persisted
write of an UNRELATED key. `_save_config` dumped the whole in-memory dict, so
`firmware.py` caching `avrdude-path` after a successful flash wrote the
one-shot `--port` to disk permanently. Observed live: `~/.firestarter/config.json`
gained `"port": "/dev/ttyUSB0"` from a `--port` that is documented as applying
to a single invocation, silently retargeting every later command.
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from firestarter.config import ConfigManager
from firestarter.constants import FLAG_FORCE
from firestarter.exceptions import ProgrammerNotFoundError
from firestarter.firmware import FirmwareManager
from firestarter.serial_comm import SerialCommunicator


def _fake_comports(*devices):
    """Build comports() entries that _list_potential_ports would accept."""
    entries = []
    for dev in devices:
        entry = MagicMock()
        entry.device = dev
        entry.manufacturer = "Arduino"
        entry.description = "USB Serial"
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# A — a named port restricts the search
# ---------------------------------------------------------------------------


class TestNamedPortRestrictsTheSearch:
    def test_named_port_is_the_only_candidate(self):
        """The defect: other ports were appended after the named one."""
        with patch(
            "serial.tools.list_ports.comports",
            return_value=_fake_comports("/dev/ttyACM0", "/dev/ttyACM1", "/dev/ttyUSB0"),
        ):
            ports = SerialCommunicator._list_potential_ports(
                "/dev/ttyACM1", restrict_to_preferred=True
            )
        assert ports == ["/dev/ttyACM1"], (
            "a named port must RESTRICT the search; listing other ports lets a "
            "different board answer for the one that was asked about"
        )

    def test_remembered_port_is_preferred_but_not_exclusive(self):
        """A port merely remembered from a previous run must yield to discovery.

        Otherwise replugging a board strands every later invocation on a port
        that no longer exists — the restriction above must not cost that.
        """
        with patch(
            "serial.tools.list_ports.comports",
            return_value=_fake_comports("/dev/ttyACM0", "/dev/ttyUSB0"),
        ):
            ports = SerialCommunicator._list_potential_ports(
                "/dev/ttyUSB0", restrict_to_preferred=False
            )
        assert ports[0] == "/dev/ttyUSB0", "the remembered port must still be first"
        assert "/dev/ttyACM0" in ports, "discovery must still reach the other ports"

    def test_no_named_port_still_enumerates(self):
        """Non-regression: discovery is unchanged when no port is named."""
        with patch(
            "serial.tools.list_ports.comports",
            return_value=_fake_comports("/dev/ttyACM0", "/dev/ttyUSB0"),
        ):
            ports = SerialCommunicator._list_potential_ports(None)
        assert ports == ["/dev/ttyACM0", "/dev/ttyUSB0"]

    def test_mute_named_port_never_probes_a_second_port(self, monkeypatch):
        """A port that does not answer must NOT fall through to another board."""
        probed = []

        def mock_probe(port_name, *_a, **_k):
            probed.append(port_name)
            return None  # "responded but not with OK" — e.g. Bad JSON on 2.x

        monkeypatch.setattr(SerialCommunicator, "_probe_port", mock_probe)
        monkeypatch.setattr(
            "serial.tools.list_ports.comports",
            lambda: _fake_comports("/dev/ttyACM1", "/dev/ttyUSB0"),
        )
        cfg = MagicMock()
        cfg.get_value.return_value = None
        # Production marks a typed --port transient; a bare Mock would answer
        # every call truthily, so state the mechanism explicitly.
        cfg.is_transient.return_value = True

        with pytest.raises(ProgrammerNotFoundError) as exc:
            SerialCommunicator.find_and_connect(
                {"state": 13}, cfg, preferred_port="/dev/ttyACM1"
            )

        assert probed == ["/dev/ttyACM1"], (
            f"probing continued past the named port: {probed}. That is how a "
            f"different board's identity reached the caller."
        )
        message = str(exc.value)
        assert "/dev/ttyACM1" in message, "the failure must name the port asked for"
        assert "--board" in message, (
            "the message must point at the one escape hatch for unidentifiable "
            "firmware, otherwise it is a dead end"
        )

    def test_remembered_port_still_falls_through_to_other_ports(self, monkeypatch):
        """The permissive half of the rule, end to end through find_and_connect."""
        probed = []

        def mock_probe(port_name, *_a, **_k):
            probed.append(port_name)
            return None

        monkeypatch.setattr(SerialCommunicator, "_probe_port", mock_probe)
        monkeypatch.setattr(
            "serial.tools.list_ports.comports",
            lambda: _fake_comports("/dev/ttyACM1", "/dev/ttyUSB0"),
        )
        cfg = MagicMock()
        cfg.get_value.return_value = "/dev/ttyUSB0"
        cfg.is_transient.return_value = False  # remembered, not typed this run

        with pytest.raises(ProgrammerNotFoundError) as exc:
            SerialCommunicator.find_and_connect({"state": 13}, cfg)

        assert probed == ["/dev/ttyUSB0", "/dev/ttyACM1"], (
            f"a remembered port must not strand discovery, got {probed}"
        )
        assert "on any port" in str(exc.value), (
            "the port-specific message belongs to the restricted path only"
        )

    def test_typed_port_is_not_promoted_into_the_saved_config(self, tmp_config_dir):
        """A working port must not persist a port meant for one invocation."""
        cm = ConfigManager(config_filename="t_remember_typed.json")
        cm.set_value("port", "/dev/ttyACM1", persist=False)  # what cli() does

        cm.remember_port("/dev/ttyACM1")

        cfg_path = os.path.join(tmp_config_dir, "t_remember_typed.json")
        on_disk = {}
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                on_disk = json.load(f)
        assert "port" not in on_disk, (
            "a typed --port was written to disk, which silently retargets and "
            "now also restricts every later invocation"
        )
        assert cm.get_value("port") == "/dev/ttyACM1", "still applies to THIS run"

    def test_discovered_port_is_still_remembered(self, tmp_config_dir):
        """Non-regression: the remember-the-working-port convenience survives."""
        cm = ConfigManager(config_filename="t_remember_found.json")

        cm.remember_port("/dev/ttyACM1")

        with open(os.path.join(tmp_config_dir, "t_remember_found.json")) as f:
            assert json.load(f)["port"] == "/dev/ttyACM1"

    def test_remember_port_is_the_only_writer_of_the_saved_port(self):
        """Source tripwire: the rule lived in two call sites and drifted.

        `serial_comm` (successful probe) and `firmware` (successful flash) each
        persisted the port independently. Fixing only the first left the leak
        live — the flash path still promoted a typed `--port`. Any new direct
        write of the key would reintroduce it, so pin `remember_port` as the sole
        writer.
        """
        pkg = Path(__file__).resolve().parents[1] / "firestarter"
        offenders = sorted(
            p.name
            for p in pkg.rglob("*.py")
            if 'set_value("port"' in p.read_text() and p.name != "cli_handlers.py"
        )
        assert offenders == ["config.py"], (
            f"{offenders} write the saved port directly; route them through "
            f"ConfigManager.remember_port so the typed-vs-remembered rule "
            f"cannot drift between call sites again"
        )


# ---------------------------------------------------------------------------
# B — blind install, gated on an explicit --board
# ---------------------------------------------------------------------------


def _manager_that_cannot_identify(monkeypatch):
    """A FirmwareManager whose identification step yields nothing."""
    fm = FirmwareManager(config_manager=MagicMock())
    monkeypatch.setattr(fm, "check_current_firmware", lambda **_kw: (None, None, None))
    monkeypatch.setattr(
        fm,
        "fetch_release_info",
        lambda channel="stable", version=None, board="uno": (
            "3.0.0b19",
            f"https://example.invalid/{board}.hex",
        ),
    )
    return fm


class TestBlindInstallRequiresExplicitBoard:
    @pytest.mark.parametrize(
        ("install_flag", "flags", "label"),
        [(True, 0, "--install"), (False, FLAG_FORCE, "--force")],
    )
    def test_refused_without_an_explicit_board(
        self, monkeypatch, caplog, install_flag, flags, label
    ):
        """Guessing the board could write the wrong image — refuse instead."""
        fm = _manager_that_cannot_identify(monkeypatch)
        monkeypatch.setattr(
            fm,
            "_download_firmware_file",
            lambda url: pytest.fail(f"{label} must not download without --board"),
        )
        monkeypatch.setattr(
            fm,
            "_install_firmware",
            lambda **kw: pytest.fail(f"{label} must not flash without --board"),
        )

        with caplog.at_level("ERROR"):
            ok = fm.manage_firmware_update(
                install_flag=install_flag,
                flags=flags,
                port_override="/dev/ttyACM1",
                board_explicit=False,
            )

        assert ok is False
        assert "cannot be chosen automatically" in caplog.text

    def test_allowed_with_an_explicit_board(self, monkeypatch):
        """Named board + unreadable firmware = the only way off 2.x firmware."""
        fm = _manager_that_cannot_identify(monkeypatch)
        installed = {}
        monkeypatch.setattr(
            fm, "_download_firmware_file", lambda url: "/tmp/fake_uno.hex"
        )
        monkeypatch.setattr(
            fm,
            "_install_firmware",
            lambda **kw: installed.update(kw) or True,
        )

        ok = fm.manage_firmware_update(
            install_flag=True,
            port_override="/dev/ttyACM1",
            board_override="leonardo",
            board_explicit=True,
        )

        assert ok is True
        assert installed["board"] == "leonardo", (
            "the operator's explicit board must select the image, since there is "
            "no detected board to take it from"
        )
        assert installed["target_port"] == "/dev/ttyACM1"

    def test_bare_check_still_refuses_without_install_intent(self, monkeypatch):
        """Non-regression: no install intent + no version is still an error."""
        fm = _manager_that_cannot_identify(monkeypatch)
        monkeypatch.setattr(
            fm,
            "_install_firmware",
            lambda **kw: pytest.fail("a bare check must never flash"),
        )
        ok = fm.manage_firmware_update(
            port_override="/dev/ttyACM1", board_explicit=True
        )
        assert ok is False


# ---------------------------------------------------------------------------
# C — transient config values must not reach the disk
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FIRESTARTER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("firestarter.config.HOME_PATH", str(tmp_path))
    ConfigManager._instances.clear()
    ConfigManager._initialized_configs.clear()
    yield str(tmp_path)
    ConfigManager._instances.clear()
    ConfigManager._initialized_configs.clear()


class TestTransientValuesNeverPersist:
    def test_unrelated_persisted_write_does_not_leak_the_transient_port(
        self, tmp_config_dir
    ):
        """The live defect: caching avrdude-path wrote the one-shot --port."""
        cm = ConfigManager(config_filename="t_transient_leak.json")
        cm.set_value("port", "/dev/ttyUSB0", persist=False)
        cm.set_value("avrdude-path", "/usr/bin/avrdude")  # persist=True default

        with open(os.path.join(tmp_config_dir, "t_transient_leak.json")) as f:
            on_disk = json.load(f)

        assert on_disk == {"avrdude-path": "/usr/bin/avrdude"}, (
            "a --port documented as applying to one invocation was written to "
            "disk, silently retargeting every later command"
        )
        assert cm.get_value("port") == "/dev/ttyUSB0", (
            "the value must still apply to THIS invocation"
        )

    def test_explicit_persisted_write_promotes_the_key(self, tmp_config_dir):
        """`config port X` after a `--port Y` must still be saved."""
        cm = ConfigManager(config_filename="t_transient_promote.json")
        cm.set_value("port", "/dev/ttyACM0", persist=False)
        cm.set_value("port", "/dev/ttyACM1", persist=True)

        with open(os.path.join(tmp_config_dir, "t_transient_promote.json")) as f:
            on_disk = json.load(f)
        assert on_disk["port"] == "/dev/ttyACM1"

    def test_removing_a_transient_key_clears_its_transient_mark(self, tmp_config_dir):
        """A key cleared then re-set persisted must not stay suppressed."""
        cm = ConfigManager(config_filename="t_transient_cleared.json")
        cm.set_value("port", "/dev/ttyACM0", persist=False)
        cm.set_value("port", None, persist=False)
        cm.set_value("port", "/dev/ttyACM1", persist=True)

        with open(os.path.join(tmp_config_dir, "t_transient_cleared.json")) as f:
            on_disk = json.load(f)
        assert on_disk["port"] == "/dev/ttyACM1"
