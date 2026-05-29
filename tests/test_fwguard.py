"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Phase 6 Plan 04 — Host firmware-version refuse-guard acceptance suite.

Covers four paths through SerialCommunicator._probe_port's pre-v1.2 guard:

  1. v2.x firmware -> raises FirmwareOutdatedError with the locked message
     (LFW-05, LHOST-04 refuse path).
  2. v3.0.0 firmware -> no raise (LFW-05 accept path).
  3. FIRESTARTER_DEV_ALLOW_PRE_V12=1 -> bypass even on v2.x firmware
     (developer-only escape hatch for bench scripts during Phases 7-8).
  4. Malformed version string -> major defaults to 0 -> refuse fires
     (T-06-17 mitigation).

The tests short-circuit the real serial connection via unittest.mock.patch.object
per PATTERNS §"firestarter_app/tests/test_fwguard.py". An autouse class fixture
guarantees the escape-hatch env var is UNSET for every test that expects the
strict path, so the suite is hermetic against the developer's shell environment.
"""

from unittest.mock import MagicMock, patch

import pytest

from firestarter.serial_comm import FirmwareOutdatedError, SerialCommunicator


class TestFirmwareVersionGuard:
    """LFW-05 / LHOST-04 — host refuses pre-v1.2 firmware at probe time."""

    @pytest.fixture(autouse=True)
    def _clear_escape_hatch(self, monkeypatch):
        """Ensure the dev escape-hatch env var is unset for every test by default.

        Tests that explicitly want it set call `monkeypatch.setenv(...)` AFTER
        this autouse fixture has cleared it; the per-test setenv then overrides
        the delenv for the duration of that single test.
        """
        monkeypatch.delenv("FIRESTARTER_DEV_ALLOW_PRE_V12", raising=False)

    def test_refuse_pre_v3_firmware(self):
        """LFW-05 / LHOST-04 path: refuse. v2.0.11 firmware must trip the guard."""
        mock_msg = "FW: 2.0.11, HW: Rev2, Cmd: 0x0d"
        with (
            patch.object(
                SerialCommunicator, "expect_ack", return_value=(True, mock_msg)
            ),
            patch.object(SerialCommunicator, "send_json_command", return_value=42),
            patch.object(
                SerialCommunicator, "consume_remaining_input", return_value=None
            ),
            patch.object(SerialCommunicator, "disconnect", return_value=None),
            patch.object(SerialCommunicator, "__init__", lambda self, port, **k: None),
        ):
            with pytest.raises(FirmwareOutdatedError, match="pre-v1.2") as exc_info:
                SerialCommunicator._probe_port(
                    port_name="/dev/null",
                    baud_rate=250000,
                    command_to_send={"state": 1},
                    config_manager=MagicMock(),
                )
            # Locked-wording assertions: the exception must name the version it
            # saw and the concrete remedy command.
            assert "2.0.11" in str(exc_info.value)
            assert "firestarter fw --install" in str(exc_info.value)
            assert "v3.0.0 or later" in str(exc_info.value)

    def test_accept_v3_firmware(self):
        """LFW-05 / LHOST-04 path: accept. v3.0.0 firmware passes the guard."""
        mock_msg = "FW: 3.0.0, HW: Rev2, Cmd: 0x0d"
        with (
            patch.object(
                SerialCommunicator, "expect_ack", return_value=(True, mock_msg)
            ),
            patch.object(SerialCommunicator, "send_json_command", return_value=42),
            patch.object(
                SerialCommunicator, "consume_remaining_input", return_value=None
            ),
            patch.object(SerialCommunicator, "disconnect", return_value=None),
            patch.object(SerialCommunicator, "__init__", lambda self, port, **k: None),
        ):
            try:
                SerialCommunicator._probe_port(
                    port_name="/dev/null",
                    baud_rate=250000,
                    command_to_send={"state": 1},
                    config_manager=MagicMock(),
                )
            except FirmwareOutdatedError as exc:
                pytest.fail(f"v3.0.0 firmware should NOT trip the guard; got: {exc}")

    def test_dev_escape_hatch_env_var(self, monkeypatch):
        """LFW-05 / LHOST-04 path: escape-hatch. FIRESTARTER_DEV_ALLOW_PRE_V12=1 bypasses."""  # noqa: E501
        monkeypatch.setenv("FIRESTARTER_DEV_ALLOW_PRE_V12", "1")
        mock_msg = "FW: 2.0.11, HW: Rev2, Cmd: 0x0d"
        with (
            patch.object(
                SerialCommunicator, "expect_ack", return_value=(True, mock_msg)
            ),
            patch.object(SerialCommunicator, "send_json_command", return_value=42),
            patch.object(
                SerialCommunicator, "consume_remaining_input", return_value=None
            ),
            patch.object(SerialCommunicator, "disconnect", return_value=None),
            patch.object(SerialCommunicator, "__init__", lambda self, port, **k: None),
        ):
            try:
                SerialCommunicator._probe_port(
                    port_name="/dev/null",
                    baud_rate=250000,
                    command_to_send={"state": 1},
                    config_manager=MagicMock(),
                )
            except FirmwareOutdatedError as exc:
                pytest.fail(
                    f"Escape hatch should bypass the major-version refuse; got: {exc}"
                )

    def test_malformed_version_defaults_to_refuse(self):
        """LFW-05 / LHOST-04 path: malformed. Garbage version -> major=0 -> refuse (T-06-17)."""  # noqa: E501
        # Force the parser to reach a path where int(...split('.')[0]) raises:
        # `NOT_A_VERSION` matches the FW: regex's `[\d.x]+` zero-or-more pattern?
        # No — `[\d.x]+` would not capture `NOT_A_VERSION`. Use a string that
        # matches the regex but cannot parse as int: `x.x.x` (the version-regex
        # accepts 'x' literally; `int('x')` raises ValueError).
        mock_msg = "FW: x.x.x, HW: Rev2, Cmd: 0x0d"
        with (
            patch.object(
                SerialCommunicator, "expect_ack", return_value=(True, mock_msg)
            ),
            patch.object(SerialCommunicator, "send_json_command", return_value=42),
            patch.object(
                SerialCommunicator, "consume_remaining_input", return_value=None
            ),
            patch.object(SerialCommunicator, "disconnect", return_value=None),
            patch.object(SerialCommunicator, "__init__", lambda self, port, **k: None),
        ):
            with pytest.raises(FirmwareOutdatedError, match="pre-v1.2"):
                SerialCommunicator._probe_port(
                    port_name="/dev/null",
                    baud_rate=250000,
                    command_to_send={"state": 1},
                    config_manager=MagicMock(),
                )
