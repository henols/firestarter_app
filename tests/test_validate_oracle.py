"""Tests for the non-vacuous PASS oracle (HARN-03 / D-08) in dev validate-family.

Covers (all software-testable, no serial port required):
- Negative control: mismatch → FAIL (verdict 1), proving verify CAN fail
- uno328pb write/program cell → N/A, no write cycle attempted
- r1 precondition aborts out-of-band r1 before any cycle method runs
- Leonardo yields authoritative PASS; other boards yield advisory
- retry_count captured in cell
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from firestarter.cli_handlers import (
    _R1_HI,
    _R1_LO,
    _R1_TARGET,
    AppContext,
    _check_r1_precondition,
    _classify_sha_result,
    cli,
)
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.eprom_operations import EpromOperator
from firestarter.firmware import FirmwareManager
from firestarter.hardware import HardwareManager


def _make_app(port: str | None = None, r1: int | None = None) -> AppContext:
    """Construct AppContext; optionally set port and r1 in config."""
    config_manager = ConfigManager()
    config_manager.set_value("port", port, persist=False)
    if r1 is not None:
        config_manager.set_value("r1", r1, persist=False)
    else:
        # Clear any saved r1
        config_manager.set_value("r1", None, persist=False)
    return AppContext(
        db=EpromDatabase(skip_local_override=True),
        config_manager=config_manager,
        eprom_operator=Mock(spec=EpromOperator),
        hardware_manager=Mock(spec=HardwareManager),
        firmware_manager=Mock(spec=FirmwareManager),
        eprom_presenter=Mock(spec=EpromConsolePresenter),
    )


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Negative control (T-71-VACUOUS mitigation)
# ---------------------------------------------------------------------------


class TestNegativeControl:
    """Negative control proves verify CAN fail — a green cell is non-vacuous."""

    def test_classify_sha_mismatch_is_fail_on_leonardo(self) -> None:
        """A SHA mismatch on Leonardo yields FAIL (authoritative), not advisory."""
        source_sha = hashlib.sha256(b"correct-content").hexdigest()
        wrong_sha = hashlib.sha256(b"wrong-content").hexdigest()
        assert source_sha != wrong_sha
        result = _classify_sha_result(
            readback_sha=wrong_sha,
            source_sha=source_sha,
            board="leonardo",
        )
        assert result["verdict"] == "FAIL", (
            f"Leonardo SHA mismatch must be FAIL, got {result['verdict']!r}"
        )
        assert result["pass_type"] == "authoritative"

    def test_classify_sha_match_is_pass_on_leonardo(self) -> None:
        """A SHA match on Leonardo yields PASS (authoritative)."""
        sha = hashlib.sha256(b"matching-content").hexdigest()
        result = _classify_sha_result(
            readback_sha=sha,
            source_sha=sha,
            board="leonardo",
        )
        assert result["verdict"] == "PASS"
        assert result["pass_type"] == "authoritative"

    def test_negative_control_write_cycle_returns_fail(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """write_cycle_eprom returning 1 maps to FAIL in the artifact cell."""
        source = tmp_path / "source.bin"
        source.write_bytes(b"\xaa" * 64)

        operator = Mock(spec=EpromOperator)
        operator.write_cycle_eprom.return_value = 1  # deliberate FAIL

        config_manager = ConfigManager()
        config_manager.set_value("port", "/dev/ttyACM0", persist=False)
        config_manager.set_value("r1", None, persist=False)

        app = AppContext(
            db=EpromDatabase(skip_local_override=True),
            config_manager=config_manager,
            eprom_operator=operator,
            hardware_manager=Mock(spec=HardwareManager),
            firmware_manager=Mock(spec=FirmwareManager),
            eprom_presenter=Mock(spec=EpromConsolePresenter),
        )
        result = runner.invoke(
            cli,
            [
                "dev",
                "validate-family",
                "eprom",
                "--board",
                "leonardo",
                "--chip",
                "W27C512",
                "--source",
                str(source),
                "--output-dir",
                str(tmp_path),
            ],
            obj=app,
        )
        # Exit code is the verdict_int (1 = FAIL)
        assert result.exit_code == 1, (
            f"Expected exit 1 (FAIL), got {result.exit_code}. Output: {result.output}"
        )


# ---------------------------------------------------------------------------
# uno328pb hard N/A (T-71-UNO328 mitigation)
# ---------------------------------------------------------------------------


class TestUno328pbNA:
    """uno328pb write/program cells → N/A, no cycle method called."""

    def test_uno328pb_write_cell_is_na(self, runner: CliRunner, tmp_path: Path) -> None:
        """With board=uno328pb, all Tier-3 cells record N/A."""
        source = tmp_path / "source.bin"
        source.write_bytes(b"\xff" * 64)

        operator = Mock(spec=EpromOperator)
        config_manager = ConfigManager()
        config_manager.set_value("port", "/dev/ttyACM0", persist=False)
        config_manager.set_value("r1", None, persist=False)

        app = AppContext(
            db=EpromDatabase(skip_local_override=True),
            config_manager=config_manager,
            eprom_operator=operator,
            hardware_manager=Mock(spec=HardwareManager),
            firmware_manager=Mock(spec=FirmwareManager),
            eprom_presenter=Mock(spec=EpromConsolePresenter),
        )
        result = runner.invoke(
            cli,
            [
                "dev",
                "validate-family",
                "eprom",
                "--board",
                "uno328pb",
                "--chip",
                "W27C512",
                "--source",
                str(source),
                "--output-dir",
                str(tmp_path),
            ],
            obj=app,
        )
        # Exits 0 (N/A is not a failure)
        assert result.exit_code == 0, result.output

        import json

        data = json.loads((tmp_path / "validation-matrix.json").read_text())
        cells = data["cells"]
        assert cells, "Cells should be present"
        for cell in cells:
            assert cell["verdict"] == "N/A", (
                f"uno328pb cell must be N/A, got {cell['verdict']!r}"
            )

    def test_uno328pb_no_write_cycle_called(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """With board=uno328pb, write_cycle_eprom is never called."""
        source = tmp_path / "source.bin"
        source.write_bytes(b"\xff" * 64)

        operator = Mock(spec=EpromOperator)
        config_manager = ConfigManager()
        config_manager.set_value("port", "/dev/ttyACM0", persist=False)
        config_manager.set_value("r1", None, persist=False)

        app = AppContext(
            db=EpromDatabase(skip_local_override=True),
            config_manager=config_manager,
            eprom_operator=operator,
            hardware_manager=Mock(spec=HardwareManager),
            firmware_manager=Mock(spec=FirmwareManager),
            eprom_presenter=Mock(spec=EpromConsolePresenter),
        )
        runner.invoke(
            cli,
            [
                "dev",
                "validate-family",
                "eprom",
                "--board",
                "uno328pb",
                "--chip",
                "W27C512",
                "--source",
                str(source),
                "--output-dir",
                str(tmp_path),
            ],
            obj=app,
        )
        # write_cycle_eprom must NEVER be called for uno328pb
        operator.write_cycle_eprom.assert_not_called()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# r1 precondition (T-71-STALECAL mitigation)
# ---------------------------------------------------------------------------


class TestR1Precondition:
    """r1 out-of-band → abort before any cycle method runs."""

    def test_check_r1_precondition_accepts_target(self) -> None:
        """r1 == 270000 is within tolerance (identity check)."""
        assert _check_r1_precondition(_R1_TARGET) is True

    def test_check_r1_precondition_accepts_lo_boundary(self) -> None:
        """_R1_LO is the minimum accepted value."""
        assert _check_r1_precondition(_R1_LO) is True

    def test_check_r1_precondition_accepts_hi_boundary(self) -> None:
        """_R1_HI is the maximum accepted value."""
        assert _check_r1_precondition(_R1_HI) is True

    def test_check_r1_precondition_rejects_below_lo(self) -> None:
        """r1 below _R1_LO is rejected."""
        assert _check_r1_precondition(_R1_LO - 1) is False

    def test_check_r1_precondition_rejects_above_hi(self) -> None:
        """r1 above _R1_HI is rejected."""
        assert _check_r1_precondition(_R1_HI + 1) is False

    def test_r1_precondition_aborts_before_cycle(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Out-of-band r1 exits 2 (hw-error) before any cycle method runs."""
        source = tmp_path / "source.bin"
        source.write_bytes(b"\xff" * 64)

        operator = Mock(spec=EpromOperator)
        # r1 far out of band (e.g. 1000 = uncalibrated Uno default)
        app = _make_app(port="/dev/ttyACM0", r1=1000)
        app = AppContext(
            db=EpromDatabase(skip_local_override=True),
            config_manager=app.config_manager,
            eprom_operator=operator,
            hardware_manager=Mock(spec=HardwareManager),
            firmware_manager=Mock(spec=FirmwareManager),
            eprom_presenter=Mock(spec=EpromConsolePresenter),
        )
        result = runner.invoke(
            cli,
            [
                "dev",
                "validate-family",
                "eprom",
                "--board",
                "leonardo",
                "--chip",
                "W27C512",
                "--source",
                str(source),
                "--output-dir",
                str(tmp_path),
            ],
            obj=app,
        )
        # Must exit 2 (hw-error / abort)
        assert result.exit_code == 2, (
            f"Expected exit 2 (r1 abort), got {result.exit_code}. Output: {result.output}"
        )
        # write_cycle_eprom must NOT have been called
        operator.write_cycle_eprom.assert_not_called()  # type: ignore[attr-defined]

    def test_r1_in_band_allows_cycle(self, runner: CliRunner, tmp_path: Path) -> None:
        """r1 within tolerance band allows write_cycle_eprom to run."""
        source = tmp_path / "source.bin"
        source.write_bytes(b"\xff" * 64)

        operator = Mock(spec=EpromOperator)
        operator.write_cycle_eprom.return_value = 0  # type: ignore[attr-defined]
        # r1 = 270000 (exactly on target)
        config_manager = ConfigManager()
        config_manager.set_value("port", "/dev/ttyACM0", persist=False)
        config_manager.set_value("r1", _R1_TARGET, persist=False)

        app = AppContext(
            db=EpromDatabase(skip_local_override=True),
            config_manager=config_manager,
            eprom_operator=operator,
            hardware_manager=Mock(spec=HardwareManager),
            firmware_manager=Mock(spec=FirmwareManager),
            eprom_presenter=Mock(spec=EpromConsolePresenter),
        )
        runner.invoke(
            cli,
            [
                "dev",
                "validate-family",
                "eprom",
                "--board",
                "leonardo",
                "--chip",
                "W27C512",
                "--source",
                str(source),
                "--output-dir",
                str(tmp_path),
            ],
            obj=app,
        )
        operator.write_cycle_eprom.assert_called_once()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Leonardo authoritative vs advisory (T-71-WRONGBOARD mitigation)
# ---------------------------------------------------------------------------


class TestLeonardoAuthoritativePass:
    """Leonardo is the only authoritative-PASS board; others are advisory."""

    def test_leonardo_sha_match_is_authoritative_pass(self) -> None:
        """SHA match on Leonardo → verdict=PASS, pass_type=authoritative."""
        sha = hashlib.sha256(b"data").hexdigest()
        result = _classify_sha_result(
            readback_sha=sha, source_sha=sha, board="leonardo"
        )
        assert result["verdict"] == "PASS"
        assert result["pass_type"] == "authoritative"

    def test_other_board_sha_match_is_advisory(self) -> None:
        """SHA match on a non-Leonardo board → verdict=PASS, pass_type=advisory."""
        sha = hashlib.sha256(b"data").hexdigest()
        result = _classify_sha_result(readback_sha=sha, source_sha=sha, board="uno")
        assert result["verdict"] == "PASS"
        assert result["pass_type"] == "advisory"

    def test_other_board_sha_mismatch_is_advisory_not_fail(self) -> None:
        """SHA mismatch on a non-Leonardo board → verdict=advisory (not FAIL)."""
        sha_a = hashlib.sha256(b"data-a").hexdigest()
        sha_b = hashlib.sha256(b"data-b").hexdigest()
        result = _classify_sha_result(readback_sha=sha_a, source_sha=sha_b, board="uno")
        assert result["verdict"] == "advisory", (
            "Non-Leonardo mismatch must be advisory, not FAIL"
        )
        assert result["pass_type"] == "advisory"

    def test_write_cycle_pass_on_leonardo_is_authoritative(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """write_cycle_eprom returns 0 on leonardo → PASS in artifact."""
        source = tmp_path / "source.bin"
        source.write_bytes(b"\xff" * 64)

        operator = Mock(spec=EpromOperator)
        operator.write_cycle_eprom.return_value = 0  # type: ignore[attr-defined]
        config_manager = ConfigManager()
        config_manager.set_value("port", "/dev/ttyACM0", persist=False)
        config_manager.set_value("r1", None, persist=False)

        app = AppContext(
            db=EpromDatabase(skip_local_override=True),
            config_manager=config_manager,
            eprom_operator=operator,
            hardware_manager=Mock(spec=HardwareManager),
            firmware_manager=Mock(spec=FirmwareManager),
            eprom_presenter=Mock(spec=EpromConsolePresenter),
        )
        result = runner.invoke(
            cli,
            [
                "dev",
                "validate-family",
                "eprom",
                "--board",
                "leonardo",
                "--chip",
                "W27C512",
                "--source",
                str(source),
                "--output-dir",
                str(tmp_path),
            ],
            obj=app,
        )
        assert result.exit_code == 0, result.output

        import json

        data = json.loads((tmp_path / "validation-matrix.json").read_text())
        cells = data["cells"]
        assert cells
        assert cells[0]["verdict"] == "PASS"


# ---------------------------------------------------------------------------
# retry_count in emitted cell
# ---------------------------------------------------------------------------


class TestRetryCount:
    """retry_count is captured in the emitted artifact cell."""

    def test_retry_count_present_in_skip_deferred_cell(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """SKIP-deferred cells carry retry_count=0."""
        app = AppContext(
            db=EpromDatabase(skip_local_override=True),
            config_manager=ConfigManager(),
            eprom_operator=Mock(spec=EpromOperator),
            hardware_manager=Mock(spec=HardwareManager),
            firmware_manager=Mock(spec=FirmwareManager),
            eprom_presenter=Mock(spec=EpromConsolePresenter),
        )
        runner.invoke(
            cli,
            ["dev", "validate-family", "eprom", "--output-dir", str(tmp_path)],
            obj=app,
        )
        import json

        data = json.loads((tmp_path / "validation-matrix.json").read_text())
        for cell in data["cells"]:
            assert "retry_count" in cell, f"retry_count missing from {cell}"

    def test_retry_count_is_1_for_hardware_run(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """A hardware run (runs=1 write cycle) captures retry_count=1 in the cell."""
        source = tmp_path / "source.bin"
        source.write_bytes(b"\xff" * 64)

        operator = Mock(spec=EpromOperator)
        operator.write_cycle_eprom.return_value = 0  # type: ignore[attr-defined]
        config_manager = ConfigManager()
        config_manager.set_value("port", "/dev/ttyACM0", persist=False)
        config_manager.set_value("r1", None, persist=False)

        app = AppContext(
            db=EpromDatabase(skip_local_override=True),
            config_manager=config_manager,
            eprom_operator=operator,
            hardware_manager=Mock(spec=HardwareManager),
            firmware_manager=Mock(spec=FirmwareManager),
            eprom_presenter=Mock(spec=EpromConsolePresenter),
        )
        runner.invoke(
            cli,
            [
                "dev",
                "validate-family",
                "eprom",
                "--board",
                "leonardo",
                "--chip",
                "W27C512",
                "--source",
                str(source),
                "--output-dir",
                str(tmp_path),
            ],
            obj=app,
        )
        import json

        data = json.loads((tmp_path / "validation-matrix.json").read_text())
        cells = data["cells"]
        assert cells
        assert cells[0]["retry_count"] == 1
