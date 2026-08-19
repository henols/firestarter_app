"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Regression suite for the "a pre-release app cannot update pre-CAP-02 firmware"
deadlock (debug session `.planning/debug/fw-update-blocked-release-fw.md`).

Two independent defects are pinned here.

D1 — the firmware-update read path was blocked by the version gate it exists
to resolve. `SerialCommunicator._probe_port` refuses any ack that carries no
CAP-02 identity tail, and `FirmwareManager.manage_firmware_update` calls
`check_current_firmware` as its FIRST statement, which re-raises
`FirmwareOutdatedError`. Measured on the bench: `fw`, `fw --install`,
`fw --force`, `fw --install --pre` and `fw --firmware-version 3.0.0b19` all
aborted on two Uno-class boards running 3.0.0b11 firmware (ack body
`01 02 00 41` — no identity tail) with ZERO calls to the download or install
paths. Meanwhile the version was sitting in the very next ack as
`OK: FW: 3.0.0b11:uno`. The fix is an explicit, caller-declared
`allow_outdated_firmware` waiver, scoped to the version refusals only.

The pair of tests per case is the point: every waiver test has a twin that
proves the strict path is UNCHANGED without the flag. The waiver must never
become reachable from a chip operation, so a source-level tripwire below pins
the set of production call sites that pass it.

D2 — `_maybe_auto_route_to_pre` gated the beta-app pre-channel auto-route on
`--install`, so on a pre-release app bare `fw` compared against the newest
STABLE firmware (2.0.6) and reported "already up to date", and `fw --force`
resolved the stable asset — a DOWNGRADE to firmware this host cannot speak to.
"""

import contextlib
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from firestarter.exceptions import FirmwareOutdatedError
from firestarter.firmware import FirmwareManager
from firestarter.serial_comm import SerialCommunicator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _probe_with_identity(identity):
    """Patch out serial I/O and pin the CAP-02 firmware identity string.

    Same shape as tests/test_fwguard.py's helper — deliberately duplicated
    rather than imported so this file states its own preconditions.
    """
    with (
        patch.object(SerialCommunicator, "expect_ack", return_value=(True, "Ready")),
        patch.object(SerialCommunicator, "send_json_command", return_value=42),
        patch.object(SerialCommunicator, "consume_remaining_input", return_value=None),
        patch.object(SerialCommunicator, "disconnect", return_value=None),
        patch.object(SerialCommunicator, "firmware_identity", identity),
        patch.object(SerialCommunicator, "hw_revision", None),
        patch.object(SerialCommunicator, "port_name", "/dev/null", create=True),
        patch.object(SerialCommunicator, "__init__", lambda self, port, **k: None),
    ):
        yield


def _probe(**kwargs):
    """Run _probe_port with a plain non-gated command (no bus-config)."""
    return SerialCommunicator._probe_port(
        port_name="/dev/null",
        baud_rate=250000,
        command_to_send={"state": 1},
        config_manager=MagicMock(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# D1 — the waiver, and its twin proving the strict path is unchanged
# ---------------------------------------------------------------------------


class TestOutdatedFirmwareWaiverScope:
    @pytest.fixture(autouse=True)
    def _clear_escape_hatch(self, monkeypatch):
        monkeypatch.delenv("FIRESTARTER_DEV_ALLOW_PRE_V12", raising=False)

    def test_absent_identity_is_tolerated_on_the_update_path(self):
        """D1 core: pre-CAP-02 ack must NOT abort the firmware-update read."""
        with _probe_with_identity(None):
            comm = _probe(allow_outdated_firmware=True)
        assert comm is not None, (
            "the firmware-update read path must reach a usable connection on "
            "firmware whose ack carries no identity tail — that firmware is "
            "the subject of the update, not an error condition"
        )

    def test_absent_identity_still_refuses_without_the_waiver(self):
        """Twin: the chip-operation path must be byte-for-byte as strict."""
        with _probe_with_identity(None):
            with pytest.raises(FirmwareOutdatedError, match="did not report"):
                _probe()

    def test_absent_identity_still_refuses_when_waiver_explicitly_false(self):
        """Twin, explicit form — no default-argument ambiguity."""
        with _probe_with_identity(None):
            with pytest.raises(FirmwareOutdatedError, match="did not report"):
                _probe(allow_outdated_firmware=False)

    def test_pre_v3_identity_is_tolerated_on_the_update_path(self):
        """A 2.x board must be readable so it can be upgraded off 2.x."""
        with _probe_with_identity("2.0.11:uno"):
            comm = _probe(allow_outdated_firmware=True)
        assert comm is not None

    def test_pre_v3_identity_still_refuses_without_the_waiver(self):
        """Twin: LFW-05 / LHOST-04 refusal is untouched for chip operations."""
        with _probe_with_identity("2.0.11:uno"):
            with pytest.raises(FirmwareOutdatedError, match="pre-v1.2"):
                _probe()

    def test_waiver_does_not_touch_the_shield_revision_gate(self):
        """The waiver is scoped to VERSION refusals only.

        A command that routes VPP to bus line 11 on firmware that reports no
        revision must still be refused, waiver or not — that refusal is a
        chip-damage guard, not a version policy.
        """
        from firestarter.exceptions import HardwareRevisionUnsupportedError

        with _probe_with_identity(None):
            with pytest.raises(HardwareRevisionUnsupportedError):
                SerialCommunicator._probe_port(
                    port_name="/dev/null",
                    baud_rate=250000,
                    command_to_send={"bus-config": {"vpp-pin": 11}},
                    config_manager=MagicMock(),
                    allow_outdated_firmware=True,
                )


class TestWaiverPlumbing:
    def test_find_and_connect_defaults_to_the_strict_gate(self, monkeypatch):
        """Every caller that says nothing keeps the gate."""
        seen = {}

        def spy(**kwargs):
            seen.update(kwargs)
            return MagicMock()

        monkeypatch.setattr(
            SerialCommunicator, "_probe_port", staticmethod(lambda *a, **k: spy(**k))
        )
        monkeypatch.setattr(
            SerialCommunicator,
            "_list_potential_ports",
            staticmethod(lambda p, **_kw: ["/x"]),
        )
        SerialCommunicator.find_and_connect({"state": 1}, MagicMock())
        assert seen["allow_outdated_firmware"] is False

    def test_find_and_connect_forwards_the_waiver(self, monkeypatch):
        seen = {}

        def spy(**kwargs):
            seen.update(kwargs)
            return MagicMock()

        monkeypatch.setattr(
            SerialCommunicator, "_probe_port", staticmethod(lambda *a, **k: spy(**k))
        )
        monkeypatch.setattr(
            SerialCommunicator,
            "_list_potential_ports",
            staticmethod(lambda p, **_kw: ["/x"]),
        )
        SerialCommunicator.find_and_connect(
            {"state": 1}, MagicMock(), allow_outdated_firmware=True
        )
        assert seen["allow_outdated_firmware"] is True

    def test_check_current_firmware_requests_the_waiver(self, monkeypatch):
        """The updater is the one production caller that opts in."""
        seen = {}

        def mock_connect(*_args, **kwargs):
            seen.update(kwargs)
            comm = MagicMock()
            comm.port_name = "/dev/ttyACM1"
            comm.expect_ack.return_value = (True, "FW: 3.0.0b11:uno")
            return comm

        monkeypatch.setattr(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            mock_connect,
        )
        fm = FirmwareManager(config_manager=MagicMock())
        assert fm.check_current_firmware() == ("/dev/ttyACM1", "3.0.0b11", "uno")
        assert seen.get("allow_outdated_firmware") is True

    def test_only_the_updater_passes_the_waiver(self):
        """Source-level tripwire: the waiver must not spread.

        `allow_outdated_firmware=True` may appear in exactly one production
        module — firmware.py, in check_current_firmware. serial_comm.py owns
        the parameter itself. Any other production module gaining it means a
        chip-operation path has acquired the relaxation, which is the whole
        thing this fix must not do.
        """
        pkg = Path(__file__).resolve().parents[1] / "firestarter"
        offenders = sorted(
            p.name
            for p in pkg.rglob("*.py")
            if "allow_outdated_firmware=True" in p.read_text()
            and p.name not in ("serial_comm.py", "firmware.py")
        )
        assert offenders == [], (
            f"the outdated-firmware waiver leaked into {offenders}; it is only "
            f"legitimate on the firmware-update read path"
        )


class TestUpdateDecisionReachedOnPreCap02Firmware:
    """The end-to-end regression: the update decision must be REACHED."""

    def test_manage_firmware_update_reaches_the_download_boundary(self, monkeypatch):
        """No identity in the ack, version from the legacy text ack, then install.

        Drives the real check_current_firmware against a fake connection that
        reproduces the bench boards exactly: first ack "Ready" with
        firmware_identity None, second ack "FW: 3.0.0b11:uno".
        """
        downloads = []

        def mock_connect(*_args, **_kwargs):
            comm = MagicMock()
            comm.port_name = "/dev/ttyACM1"
            comm.firmware_identity = None
            comm.expect_ack.return_value = (True, "FW: 3.0.0b11:uno")
            return comm

        monkeypatch.setattr(
            "firestarter.serial_comm.SerialCommunicator.find_and_connect",
            mock_connect,
        )
        fm = FirmwareManager(config_manager=MagicMock())
        monkeypatch.setattr(
            fm,
            "fetch_release_info",
            lambda channel="stable", version=None, board="uno": (
                "3.0.0b19",
                f"https://example.invalid/{board}.hex",
            ),
        )
        monkeypatch.setattr(
            fm,
            "_download_firmware_file",
            lambda url: downloads.append(url) or None,
        )
        monkeypatch.setattr(
            fm,
            "_install_firmware",
            lambda **kw: pytest.fail("must stop at the download boundary"),
        )

        fm.manage_firmware_update(install_flag=True, channel="pre")

        assert downloads == ["https://example.invalid/uno.hex"], (
            "the update decision was not reached — this is the original "
            "deadlock: FirmwareOutdatedError raised before any release lookup"
        )

    def test_the_deadlock_shape_is_gone(self, monkeypatch):
        """manage_firmware_update must not propagate the gate's refusal.

        Pins the causal chain rather than the message: an ack with no identity
        must no longer produce FirmwareOutdatedError out of the update path.
        """
        monkeypatch.setattr(
            "firestarter.serial_comm.SerialCommunicator._list_potential_ports",
            staticmethod(lambda p, **_kw: ["/dev/null"]),
        )
        fm = FirmwareManager(config_manager=MagicMock())
        monkeypatch.setattr(
            fm, "fetch_release_info", lambda **kw: ("3.0.0b19", "https://x.invalid/u")
        )
        monkeypatch.setattr(fm, "_download_firmware_file", lambda url: None)

        with _probe_with_identity(None):
            with patch.object(
                SerialCommunicator,
                "expect_ack",
                return_value=(True, "FW: 3.0.0b11:uno"),
            ):
                # Not raising is the assertion. Return value is False because
                # the (stubbed) download fails, which is a later stage.
                fm.manage_firmware_update(install_flag=True, port_override="/dev/null")


# ---------------------------------------------------------------------------
# D2 — channel resolution on a pre-release app
# ---------------------------------------------------------------------------


class TestChannelAutoRouteIsNotGatedOnInstall:
    """A pre-release app must compare against, and install from, its own channel."""

    @pytest.fixture(autouse=True)
    def _isolate_version(self, monkeypatch):
        import firestarter as _pkg

        monkeypatch.setattr(_pkg, "__version__", _pkg.__version__)

    def _args(self, **over):
        from types import SimpleNamespace

        base = dict(install=False, pre=False, firmware_version=None, stable=False)
        base.update(over)
        return SimpleNamespace(**base)

    def test_bare_fw_auto_routes_on_a_pre_release_app(self, monkeypatch):
        """The status query must not measure a beta app against stable firmware."""
        import firestarter as _pkg
        from firestarter.cli_handlers import _maybe_auto_route_to_pre

        monkeypatch.setattr(_pkg, "__version__", "3.0.0b21")
        args = self._args(install=False)
        _maybe_auto_route_to_pre(args)
        assert args.pre is True, (
            "bare `fw` on a pre-release app resolved the stable channel, so it "
            "compared 3.0.0b17 against 2.0.6 and reported 'already up to date'"
        )

    def test_force_without_install_auto_routes(self, monkeypatch):
        """`fw --force` must not resolve a stable DOWNGRADE asset."""
        import firestarter as _pkg
        from firestarter.cli_handlers import _maybe_auto_route_to_pre

        monkeypatch.setattr(_pkg, "__version__", "3.0.0b21")
        args = self._args(install=False)
        _maybe_auto_route_to_pre(args)
        assert args.pre is True

    def test_install_still_auto_routes(self, monkeypatch):
        """Non-regression on the original D-21/D-22 behaviour."""
        import firestarter as _pkg
        from firestarter.cli_handlers import _maybe_auto_route_to_pre

        monkeypatch.setattr(_pkg, "__version__", "3.0.0b21")
        args = self._args(install=True)
        _maybe_auto_route_to_pre(args)
        assert args.pre is True

    def test_stable_app_never_auto_routes(self, monkeypatch):
        """D-23 preserved, now on the install=False path too."""
        import firestarter as _pkg
        from firestarter.cli_handlers import _maybe_auto_route_to_pre

        monkeypatch.setattr(_pkg, "__version__", "2.0.7")
        for install in (True, False):
            args = self._args(install=install)
            _maybe_auto_route_to_pre(args)
            assert args.pre is False

    def test_explicit_stable_opts_out_without_install(self, monkeypatch):
        """D-24 preserved on the widened condition."""
        import firestarter as _pkg
        from firestarter.cli_handlers import _maybe_auto_route_to_pre

        monkeypatch.setattr(_pkg, "__version__", "3.0.0b21")
        args = self._args(install=False, stable=True)
        _maybe_auto_route_to_pre(args)
        assert args.pre is False

    def test_explicit_pinned_version_opts_out_without_install(self, monkeypatch):
        import firestarter as _pkg
        from firestarter.cli_handlers import _maybe_auto_route_to_pre

        monkeypatch.setattr(_pkg, "__version__", "3.0.0b21")
        args = self._args(install=False, firmware_version="2.0.6")
        _maybe_auto_route_to_pre(args)
        assert args.pre is False

    def test_explicit_pre_does_not_double_log_without_install(
        self, monkeypatch, caplog
    ):
        import firestarter as _pkg
        from firestarter.cli_handlers import _maybe_auto_route_to_pre

        monkeypatch.setattr(_pkg, "__version__", "3.0.0b21")
        args = self._args(install=False, pre=True)
        with caplog.at_level(logging.INFO):
            _maybe_auto_route_to_pre(args)
        assert not any("beta app detected" in r.message.lower() for r in caplog.records)
