"""Phase 42 / ERR-03 coverage lift for ``ConfigManager`` get/set/persist
+ override file resolution (D-14.4).

ConfigManager is a per-config-file-path singleton; tests use unique filenames
under ``tmp_path`` so each test instantiates a fresh singleton bucket.
"""

import json
import os
from typing import Iterator  # noqa: UP035
from unittest.mock import patch

import pytest

from firestarter.config import ConfigManager


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch) -> Iterator[str]:
    """Redirect ``HOME_PATH`` (where ConfigManager writes config.json) to
    ``tmp_path`` for the duration of the test. Resets ConfigManager's
    singleton cache so each test instantiates fresh.
    """
    monkeypatch.setattr("firestarter.config.HOME_PATH", str(tmp_path))
    # Wipe singleton state between tests so the tmp_path-rooted file is loaded fresh.
    ConfigManager._instances.clear()
    ConfigManager._initialized_configs.clear()
    yield str(tmp_path)


def test_config_manager_get_default_value(tmp_config_dir: str) -> None:
    """get_value returns the supplied default for keys not in the config file."""
    cm = ConfigManager(config_filename="t_get_default.json")
    assert cm.get_value("nonexistent_key", "default_val") == "default_val"
    assert cm.get_value("nonexistent_key") is None


def test_config_manager_set_persist_true(tmp_config_dir: str) -> None:
    """set_value with persist=True writes to disk; a fresh ConfigManager
    bucket sees the value on reload."""
    cm = ConfigManager(config_filename="t_persist_true.json")
    cm.set_value("port", "/dev/ttyACM0", persist=True)

    # File exists with the value persisted as JSON.
    cfg_path = os.path.join(tmp_config_dir, "t_persist_true.json")
    assert os.path.exists(cfg_path)
    with open(cfg_path) as f:
        on_disk = json.load(f)
    assert on_disk["port"] == "/dev/ttyACM0"

    # Reset singleton + re-instantiate; the value reloads from disk.
    ConfigManager._instances.clear()
    ConfigManager._initialized_configs.clear()
    cm2 = ConfigManager(config_filename="t_persist_true.json")
    assert cm2.get_value("port") == "/dev/ttyACM0"


def test_config_manager_set_persist_false(tmp_config_dir: str) -> None:
    """set_value with persist=False only updates memory; the disk file
    is NOT created (or stays empty).
    """
    cm = ConfigManager(config_filename="t_persist_false.json")
    cm.set_value("port", "/dev/ttyACM1", persist=False)

    # In-memory value is set
    assert cm.get_value("port") == "/dev/ttyACM1"

    # File does NOT exist (or is empty) — persist=False means no disk write.
    cfg_path = os.path.join(tmp_config_dir, "t_persist_false.json")
    assert not os.path.exists(cfg_path)


def test_config_manager_remove_key_persists(tmp_config_dir: str) -> None:
    """remove_key (called via set_value(value=None, persist=True)) deletes
    the key from disk."""
    cm = ConfigManager(config_filename="t_remove_key.json")
    cm.set_value("port", "/dev/ttyACM0", persist=True)
    assert cm.get_value("port") == "/dev/ttyACM0"

    cm.set_value("port", None, persist=True)
    assert cm.get_value("port") is None

    # Reload from disk: the key should be gone.
    ConfigManager._instances.clear()
    ConfigManager._initialized_configs.clear()
    cm2 = ConfigManager(config_filename="t_remove_key.json")
    assert cm2.get_value("port") is None


def test_config_manager_list_all_returns_copy(tmp_config_dir: str) -> None:
    """list_all returns a copy — mutating it does not affect the underlying config."""
    cm = ConfigManager(config_filename="t_list_all.json")
    cm.set_value("port", "/dev/ttyACM0", persist=False)
    cm.set_value("baud", 250000, persist=False)

    snapshot = cm.list_all()
    assert snapshot == {"port": "/dev/ttyACM0", "baud": 250000}

    snapshot["port"] = "MUTATED"
    # Original cm is untouched
    assert cm.get_value("port") == "/dev/ttyACM0"


def test_config_manager_singleton_per_filename(tmp_config_dir: str) -> None:
    """Two ConfigManager() calls with the same config_filename return the
    same instance (singleton); with different filenames they differ."""
    cm1 = ConfigManager(config_filename="t_singleton_a.json")
    cm2 = ConfigManager(config_filename="t_singleton_a.json")
    cm3 = ConfigManager(config_filename="t_singleton_b.json")

    assert cm1 is cm2  # same filename -> same instance
    assert cm1 is not cm3  # different filename -> different instance


def test_config_manager_invalid_json_resets_to_empty(tmp_config_dir: str) -> None:
    """A corrupted config file is treated as empty rather than crashing."""
    cfg_path = os.path.join(tmp_config_dir, "t_invalid.json")
    with open(cfg_path, "w") as f:
        f.write("{ this is not valid json ")

    ConfigManager._instances.clear()
    ConfigManager._initialized_configs.clear()
    cm = ConfigManager(config_filename="t_invalid.json")
    # Corrupted file loads as empty dict — get_value returns default.
    assert cm.get_value("any_key") is None


def test_get_local_database_returns_none_when_missing(tmp_config_dir: str) -> None:
    """get_local_database returns None when ~/.firestarter/database.json is absent."""
    from firestarter.config import get_local_database

    with patch(
        "firestarter.config.DATABASE_FILE", os.path.join(tmp_config_dir, "no.json")
    ):
        assert get_local_database() is None


def test_get_local_pin_maps_returns_none_when_missing(tmp_config_dir: str) -> None:
    """get_local_pin_maps returns None when ~/.firestarter/pin-maps.json is absent."""
    from firestarter.config import get_local_pin_maps

    with patch(
        "firestarter.config.PIN_MAP_FILE", os.path.join(tmp_config_dir, "no.json")
    ):
        assert get_local_pin_maps() is None


def test_get_local_database_returns_parsed_json(tmp_config_dir: str) -> None:
    """get_local_database returns the parsed JSON when the file exists."""
    from firestarter.config import get_local_database

    db_path = os.path.join(tmp_config_dir, "db.json")
    with open(db_path, "w") as f:
        json.dump({"manuf": [{"name": "X"}]}, f)
    with patch("firestarter.config.DATABASE_FILE", db_path):
        out = get_local_database()
    assert out == {"manuf": [{"name": "X"}]}


def test_get_local_database_returns_none_on_invalid_json(tmp_config_dir: str) -> None:
    """get_local_database returns None when the file is corrupted."""
    from firestarter.config import get_local_database

    db_path = os.path.join(tmp_config_dir, "bad.json")
    with open(db_path, "w") as f:
        f.write("{ bad json ")
    with patch("firestarter.config.DATABASE_FILE", db_path):
        assert get_local_database() is None


def test_get_local_pin_maps_returns_parsed_json(tmp_config_dir: str) -> None:
    """get_local_pin_maps returns parsed JSON when the file exists."""
    from firestarter.config import get_local_pin_maps

    pm_path = os.path.join(tmp_config_dir, "pm.json")
    with open(pm_path, "w") as f:
        json.dump({"24": {"default": {}}}, f)
    with patch("firestarter.config.PIN_MAP_FILE", pm_path):
        out = get_local_pin_maps()
    assert out == {"24": {"default": {}}}


def test_get_local_pin_maps_returns_none_on_invalid_json(tmp_config_dir: str) -> None:
    """get_local_pin_maps returns None when the file is corrupted."""
    from firestarter.config import get_local_pin_maps

    pm_path = os.path.join(tmp_config_dir, "bad_pm.json")
    with open(pm_path, "w") as f:
        f.write("{ bad ")
    with patch("firestarter.config.PIN_MAP_FILE", pm_path):
        assert get_local_pin_maps() is None
