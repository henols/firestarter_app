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

CAP-02 UPDATE: the version no longer arrives as the "FW: <ver>" text line of a
dedicated CMD_FW_VERSION probe — that probe is retired. It now rides in the
MSG_OK_READY ack and is decoded into `SerialCommunicator.firmware_identity`.
These tests therefore patch that attribute (class-level, so patch.object works)
instead of stuffing a version into expect_ack's message string. The GUARD
POLICY under test is unchanged: _validate_firmware_version still receives the
same "<major>.<minor>.<patch>" shape, because _probe_port strips the ":<board>"
suffix with the same [\\d.x]+ extraction the old regex performed.

The tests short-circuit the real serial connection via unittest.mock.patch.object
per PATTERNS §"firestarter_app/tests/test_fwguard.py". An autouse class fixture
guarantees the escape-hatch env var is UNSET for every test that expects the
strict path, so the suite is hermetic against the developer's shell environment.
"""

import contextlib
from unittest.mock import MagicMock, patch

import pytest

from firestarter.serial_comm import FirmwareOutdatedError, SerialCommunicator


@contextlib.contextmanager
def _probe_with_identity(identity):
    """Patch out serial I/O and pin the CAP-02 firmware identity string.

    `identity` mirrors the real wire value: "<version>:<board>", e.g.
    "3.0.0:uno". Passing the board suffix is deliberate — it keeps these tests
    honest about _probe_port having to strip it before the version policy runs.
    """
    with (
        patch.object(SerialCommunicator, "expect_ack", return_value=(True, "Ready")),
        patch.object(SerialCommunicator, "send_json_command", return_value=42),
        patch.object(SerialCommunicator, "consume_remaining_input", return_value=None),
        patch.object(SerialCommunicator, "disconnect", return_value=None),
        patch.object(SerialCommunicator, "firmware_identity", identity),
        patch.object(SerialCommunicator, "__init__", lambda self, port, **k: None),
    ):
        yield


def _probe():
    """Run _probe_port with a plain non-gated command (no bus-config)."""
    return SerialCommunicator._probe_port(
        port_name="/dev/null",
        baud_rate=250000,
        command_to_send={"state": 1},
        config_manager=MagicMock(),
    )


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
        with _probe_with_identity("2.0.11:uno"):
            with pytest.raises(FirmwareOutdatedError, match="pre-v1.2") as exc_info:
                _probe()
            # Locked-wording assertions: the exception must name the version it
            # saw and the concrete remedy command.
            assert "2.0.11" in str(exc_info.value)
            assert "firestarter fw --install" in str(exc_info.value)
            assert "v3.0.0 or later" in str(exc_info.value)

    def test_accept_v3_firmware(self):
        """LFW-05 / LHOST-04 path: accept. v3.0.0 firmware passes the guard."""
        with _probe_with_identity("3.0.0:uno"):
            try:
                _probe()
            except FirmwareOutdatedError as exc:
                pytest.fail(f"v3.0.0 firmware should NOT trip the guard; got: {exc}")

    def test_dev_escape_hatch_env_var(self, monkeypatch):
        """LFW-05 / LHOST-04 path: escape-hatch. FIRESTARTER_DEV_ALLOW_PRE_V12=1 bypasses."""  # noqa: E501
        monkeypatch.setenv("FIRESTARTER_DEV_ALLOW_PRE_V12", "1")
        with _probe_with_identity("2.0.11:uno"):
            try:
                _probe()
            except FirmwareOutdatedError as exc:
                pytest.fail(
                    f"Escape hatch should bypass the major-version refuse; got: {exc}"
                )

    def test_malformed_version_defaults_to_refuse(self):
        """LFW-05 / LHOST-04 path: malformed. Garbage version -> major=0 -> refuse (T-06-17)."""  # noqa: E501
        # `x.x.x` matches the version-extraction pattern `[\d.x]+` (which accepts
        # 'x' literally) but cannot parse as int, so _validate_firmware_version
        # falls to major=0 and the pre-v1.2 refuse fires.
        with _probe_with_identity("x.x.x:uno"):
            with pytest.raises(FirmwareOutdatedError, match="pre-v1.2"):
                _probe()

    def test_absent_identity_refuses(self):
        """CAP-02: firmware that sends no identity in the ack must be refused.

        Pre-CAP-02 firmware emits the 2-byte MSG_OK_READY, leaving
        firmware_identity None. That is the case the retired CMD_FW_VERSION
        probe used to cover, so it needs an explicit refuse test now: the host
        must NOT treat "no version reported" as "version fine".
        """
        with _probe_with_identity(None):
            with pytest.raises(FirmwareOutdatedError, match="did not report"):
                _probe()
