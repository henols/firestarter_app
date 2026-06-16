"""CliRunner tests for `dev validate-family` subcommand (71-06 Task 1).

Covers:
- No-hardware SKIP-deferred exit 0 (D-06)
- Artifact emitted with SKIP-deferred Tier-3 cells
- Artifact named validation-matrix.json (hyphen, not underscore — Pitfall 4)
- 'all' family argument emits cells for all 6 families
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from firestarter.cli_handlers import AppContext, cli
from firestarter.config import ConfigManager
from firestarter.database import EpromDatabase
from firestarter.eprom_info import EpromConsolePresenter
from firestarter.eprom_operations import EpromOperator
from firestarter.firmware import FirmwareManager
from firestarter.hardware import HardwareManager


def make_app_context(port: str | None = None, **overrides: object) -> AppContext:
    """Construct a minimal AppContext for CLI tests.

    If port is given, sets it in ConfigManager (no-persist); otherwise the
    config has no port set, which triggers the SKIP-deferred path.
    """
    config_manager = overrides.pop("config_manager", None)
    if config_manager is None:
        config_manager = ConfigManager()
        if port:
            config_manager.set_value("port", port, persist=False)
        else:
            # Ensure no port is set (clear any saved value)
            config_manager.set_value("port", None, persist=False)
    db = overrides.pop("db", None)
    if db is None:
        db = EpromDatabase(skip_local_override=True)
    return AppContext(
        db=db,
        config_manager=config_manager,
        eprom_operator=overrides.pop("eprom_operator", Mock(spec=EpromOperator)),
        hardware_manager=overrides.pop("hardware_manager", Mock(spec=HardwareManager)),
        firmware_manager=overrides.pop("firmware_manager", Mock(spec=FirmwareManager)),
        eprom_presenter=overrides.pop(
            "eprom_presenter", Mock(spec=EpromConsolePresenter)
        ),
    )


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


class TestSkipDeferredPath:
    """No-hardware invocation (D-06): exit 0, emit SKIP-deferred artifact."""

    def test_no_hardware_exits_0(self, runner: CliRunner, tmp_path: Path) -> None:
        """dev validate-family eprom with no port/board/chip/source exits 0."""
        app = make_app_context()
        result = runner.invoke(
            cli,
            ["dev", "validate-family", "eprom", "--output-dir", str(tmp_path)],
            obj=app,
        )
        assert result.exit_code == 0, result.output

    def test_artifact_emitted_on_skip(self, runner: CliRunner, tmp_path: Path) -> None:
        """validation-matrix.json is emitted even with no hardware."""
        app = make_app_context()
        runner.invoke(
            cli,
            ["dev", "validate-family", "eprom", "--output-dir", str(tmp_path)],
            obj=app,
        )
        artifact = tmp_path / "validation-matrix.json"
        assert artifact.exists(), "validation-matrix.json must be emitted"

    def test_artifact_cells_are_skip_deferred(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Tier-3 cells have SKIP-deferred or N/A verdict when no hardware.

        Boards in the tier3.boards list get SKIP-deferred; boards in
        tier3.skip_boards (e.g. uno328pb) get N/A.
        """
        app = make_app_context()
        runner.invoke(
            cli,
            ["dev", "validate-family", "eprom", "--output-dir", str(tmp_path)],
            obj=app,
        )
        artifact = tmp_path / "validation-matrix.json"
        data = json.loads(artifact.read_text())
        tier3_cells = [c for c in data["cells"] if c.get("tier") == 3]
        assert tier3_cells, "Should have Tier-3 cells"
        deferred_verdicts = {"SKIP-deferred", "N/A"}
        for cell in tier3_cells:
            assert cell["verdict"] in deferred_verdicts, (
                f"Expected SKIP-deferred or N/A, got {cell['verdict']!r} for {cell}"
            )
        # At least one SKIP-deferred cell must be present (not all N/A)
        skip_cells = [c for c in tier3_cells if c["verdict"] == "SKIP-deferred"]
        assert skip_cells, "At least one SKIP-deferred cell expected"

    def test_artifact_schema_fields_present(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Emitted JSON has top-level generated/harness_version/cells fields."""
        app = make_app_context()
        runner.invoke(
            cli,
            ["dev", "validate-family", "eprom", "--output-dir", str(tmp_path)],
            obj=app,
        )
        artifact = tmp_path / "validation-matrix.json"
        data = json.loads(artifact.read_text())
        assert "generated" in data
        assert "harness_version" in data
        assert "cells" in data

    def test_cell_has_required_schema_fields(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Each cell carries family/board/tier/verdict/evidence_sha."""
        app = make_app_context()
        runner.invoke(
            cli,
            ["dev", "validate-family", "eprom", "--output-dir", str(tmp_path)],
            obj=app,
        )
        data = json.loads((tmp_path / "validation-matrix.json").read_text())
        for cell in data["cells"]:
            assert "family" in cell, f"cell missing 'family': {cell}"
            assert "board" in cell, f"cell missing 'board': {cell}"
            assert "tier" in cell, f"cell missing 'tier': {cell}"
            assert "verdict" in cell, f"cell missing 'verdict': {cell}"

    def test_md_artifact_emitted(self, runner: CliRunner, tmp_path: Path) -> None:
        """validation-matrix.md is also emitted alongside the JSON."""
        app = make_app_context()
        runner.invoke(
            cli,
            ["dev", "validate-family", "eprom", "--output-dir", str(tmp_path)],
            obj=app,
        )
        assert (tmp_path / "validation-matrix.md").exists(), (
            "validation-matrix.md must be emitted"
        )

    def test_all_families_emits_all_cells(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """'all' family argument emits Tier-3 cells for all 6 families."""
        app = make_app_context()
        runner.invoke(
            cli,
            ["dev", "validate-family", "all", "--output-dir", str(tmp_path)],
            obj=app,
        )
        data = json.loads((tmp_path / "validation-matrix.json").read_text())
        families = {c["family"] for c in data["cells"]}
        expected = {"eprom", "eeprom28c", "flash3", "flash4", "flash_intel", "sram"}
        assert expected <= families, f"Expected all 6 families, got {families}"


class TestArtifactNaming:
    """Artifact filename is validation-matrix.json (hyphen), never underscore (Pitfall 4)."""

    def test_artifact_named_with_hyphen(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Emitted file is named validation-matrix.json (hyphen-separated)."""
        app = make_app_context()
        runner.invoke(
            cli,
            ["dev", "validate-family", "eprom", "--output-dir", str(tmp_path)],
            obj=app,
        )
        # Hyphenated name must exist
        assert (tmp_path / "validation-matrix.json").exists()

    def test_authored_spec_not_written(self, runner: CliRunner, tmp_path: Path) -> None:
        """Runner never writes validation_matrix_spec.json (the authored spec)."""
        app = make_app_context()
        runner.invoke(
            cli,
            ["dev", "validate-family", "eprom", "--output-dir", str(tmp_path)],
            obj=app,
        )
        # Underscore-named authored spec must NOT be written by the runner
        assert not (tmp_path / "validation_matrix_spec.json").exists(), (
            "Runner must not write the authored spec file"
        )


class TestWriteCycleComposition:
    """Verify the handler composes write_cycle_eprom (not re-implemented)."""

    def test_write_cycle_called_with_hardware(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """With port/board/chip/source, write_cycle_eprom is called (not re-implemented)."""
        source = tmp_path / "source.bin"
        source.write_bytes(b"\xff" * 64)
        operator = Mock(spec=EpromOperator)
        operator.write_cycle_eprom.return_value = 0  # type: ignore[attr-defined]
        # ConfigManager with port set
        config_manager = ConfigManager()
        config_manager.set_value("port", "/dev/ttyACM0", persist=False)
        app = make_app_context(
            config_manager=config_manager,
            eprom_operator=operator,
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
        operator.write_cycle_eprom.assert_called_once()

    def test_no_write_cycle_called_without_hardware(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Without port, write_cycle_eprom is never called (SKIP-deferred path)."""
        operator = Mock(spec=EpromOperator)
        app = make_app_context(eprom_operator=operator)
        runner.invoke(
            cli,
            ["dev", "validate-family", "eprom", "--output-dir", str(tmp_path)],
            obj=app,
        )
        operator.write_cycle_eprom.assert_not_called()
