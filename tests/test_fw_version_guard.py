"""SERIAL-02 / D-05 / D-01 — Pure-policy unit tests for
SerialCommunicator._validate_firmware_version.

The staticmethod takes a version_str + bool and never reads os.environ
(D-02). These tests exercise the policy directly — no serial mock, no
_probe_port involvement, no patching of expect_ack/send_json_command/
consume_remaining_input/disconnect/__init__. The complementary file
test_fwguard.py covers the integration path through _probe_port; both
coexist.

Matrix is the corrected D-05 set per RESEARCH §1 (the
'"2.9.9" + allow_pre_v12=True -> passes' row) and RESEARCH §7 Option A
(the '"3.0.0-dev" -> passes' row — alpha-suffix strip).
"""

import pytest

from firestarter.serial_comm import FirmwareOutdatedError, SerialCommunicator


class TestValidateFirmwareVersion:
    """Unit tests for the pure-policy version-guard @staticmethod."""

    @pytest.fixture(autouse=True)
    def _clear_escape_hatch(self, monkeypatch):
        """Ensure the dev escape-hatch env var is unset for every test by default.

        Defensive — even though _validate_firmware_version does NOT read the
        env (D-02), the autouse fixture keeps the test class hermetic against
        the developer's shell environment. Test_no_env_read explicitly sets
        the env to prove the staticmethod ignores it.
        """
        monkeypatch.delenv("FIRESTARTER_DEV_ALLOW_PRE_V12", raising=False)

    # ---- Accept paths (no raise) ----

    def test_v3_zero_zero_passes(self):
        """'3.0.0' -> None. Normal accept: major=3, Branch A skipped, 2.0.0 floor OK."""
        SerialCommunicator._validate_firmware_version("3.0.0")

    def test_v3_minor_segment_passes(self):
        """'3.5.2' -> None. Normal accept path."""
        SerialCommunicator._validate_firmware_version("3.5.2")

    def test_single_segment_passes(self):
        """'3' -> None. Single-segment version handled by tuple-compare via
        _is_version_sufficient ('x'-replace + split path)."""
        SerialCommunicator._validate_firmware_version("3")

    def test_alpha_suffix_passes(self):
        """'3.0.0-dev' -> None. RESEARCH §7 Option A: trailing alpha suffix
        is stripped before parsing. INTENTIONAL BEHAVIOR FIX flagged in
        Task 40-01-01 commit; production wire behavior unchanged because
        the _probe_port regex r'FW:\\s*([\\d.x]+)' already strips '-dev'."""
        SerialCommunicator._validate_firmware_version("3.0.0-dev")

    def test_v29_with_allow_passes(self):
        """'2.9.9' + allow_pre_v12=True -> None.

        CORRECTION from CONTEXT.md D-05 per RESEARCH §1 — passes (not raises).
        major=2 < 3 but allow_pre_v12=True skips Branch A;
        _is_version_sufficient('2.9.9', '2.0.0') is True so Branch B also
        skipped.
        """
        SerialCommunicator._validate_firmware_version("2.9.9", allow_pre_v12=True)

    # ---- Branch A — pre-v1.2 refuse ----

    def test_v29_raises(self):
        """'2.9.9' -> Branch A. major=2 < 3 AND not allow_pre_v12 -> raise."""
        with pytest.raises(FirmwareOutdatedError, match="pre-v1.2") as exc_info:
            SerialCommunicator._validate_firmware_version("2.9.9")
        assert "v3.0.0 or later" in str(exc_info.value)
        assert "firestarter fw --install" in str(exc_info.value)

    def test_pre_v12_raises(self):
        """'1.0.0' -> Branch A. major=1 < 3 -> raise."""
        with pytest.raises(FirmwareOutdatedError, match="pre-v1.2") as exc_info:
            SerialCommunicator._validate_firmware_version("1.0.0")
        assert "v3.0.0 or later" in str(exc_info.value)
        assert "firestarter fw --install" in str(exc_info.value)

    def test_unparseable_raises(self):
        """'abc' -> Branch A. int('abc') -> ValueError -> major=0 -> raise."""
        with pytest.raises(FirmwareOutdatedError, match="pre-v1.2") as exc_info:
            SerialCommunicator._validate_firmware_version("abc")
        assert "v3.0.0 or later" in str(exc_info.value)
        assert "firestarter fw --install" in str(exc_info.value)

    def test_empty_string_raises(self):
        """'' -> Branch A. int('') -> ValueError -> major=0 -> raise."""
        with pytest.raises(FirmwareOutdatedError, match="pre-v1.2") as exc_info:
            SerialCommunicator._validate_firmware_version("")
        assert "v3.0.0 or later" in str(exc_info.value)
        assert "firestarter fw --install" in str(exc_info.value)

    # ---- Branch B — 2.0.0 floor (allow_pre_v12 bypasses Branch A only) ----

    def test_pre_v12_bypass_floor(self):
        """'1.0.0' + allow_pre_v12=True -> Branch B.

        D-02 / D-05 invariant: allow_pre_v12 bypasses ONLY the major<3 check,
        NOT the 2.0.0 floor. _is_version_sufficient('1.0.0', '2.0.0') is
        False so Branch B raises.
        """
        with pytest.raises(FirmwareOutdatedError, match="outdated") as exc_info:
            SerialCommunicator._validate_firmware_version(
                "1.0.0", allow_pre_v12=True
            )
        assert "2.0.0 or higher" in str(exc_info.value)
        assert "firestarter fw --install" in str(exc_info.value)

    # ---- D-02 invariant — staticmethod ignores os.environ ----

    def test_no_env_read(self, monkeypatch):
        """D-02: the staticmethod NEVER reads FIRESTARTER_DEV_ALLOW_PRE_V12.

        Override the autouse delenv with setenv('FIRESTARTER_DEV_ALLOW_PRE_V12',
        '1') and call with allow_pre_v12 defaulted to False — the staticmethod
        must still raise Branch A, proving the env-var has no effect on the
        pure policy. Env-var I/O lives in _probe_port only.
        """
        monkeypatch.setenv("FIRESTARTER_DEV_ALLOW_PRE_V12", "1")
        with pytest.raises(FirmwareOutdatedError, match="pre-v1.2"):
            SerialCommunicator._validate_firmware_version("1.0.0")
