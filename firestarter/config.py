"""
Project Name: Firestarter
Copyright (c) 2025 Henrik Olsson

Permission is hereby granted under MIT license.

Configuration Management Module
"""

import json
import logging
import os
from typing import Any, Optional


# Define the home path and configuration file path.
# FIRESTARTER_CONFIG_DIR overrides the user config/override directory (default
# ~/.firestarter). Provides a deterministic isolation seam for tests/CI that
# invoke the CLI as a subprocess — mirroring EpromDatabase(skip_local_override=True)
# at the process boundary so a developer's local ~/.firestarter overrides do not
# leak into black-box CLI goldens. Unset → unchanged default behavior.
def get_config_dir() -> str:
    """User config/override directory, resolved at call time.

    Honors ``FIRESTARTER_CONFIG_DIR`` (default ``~/.firestarter``). Resolving at
    call time — rather than reading the import-time ``HOME_PATH`` constant — lets
    the env-var seam isolate the directory even when it is set after this module
    is imported (e.g. the ``dev test`` report default at ``<config dir>/reports``).
    """
    return os.environ.get("FIRESTARTER_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".firestarter"
    )


HOME_PATH = get_config_dir()
CONFIG_FILE_DEFAULT = "config.json"  # Default filename
# CONFIG_FILE = os.path.join(HOME_PATH, "config.json") # No longer used directly as a global fixed path for ConfigManager  # noqa: E501
DATABASE_FILE = os.path.join(HOME_PATH, "database.json")
PIN_MAP_FILE = os.path.join(HOME_PATH, "pin-maps.json")

logger = logging.getLogger("Config")


def get_local_database():
    """
    Loads the local user EPROM database override file.
    Returns:
        dict or None: The parsed JSON data if the file exists and is valid, otherwise None.
    """  # noqa: E501
    if os.path.exists(DATABASE_FILE):
        try:
            with open(DATABASE_FILE, "rt") as file:  # noqa: UP015
                return json.load(file)
        except json.JSONDecodeError:
            logger.error(f"Warning: Database file {DATABASE_FILE} is not a valid JSON.")
    return None


def get_local_pin_maps():
    """
    Loads the local user pin map override file.
    Returns:
        dict or None: The parsed JSON data if the file exists and is valid, otherwise None.
    """  # noqa: E501
    if os.path.exists(PIN_MAP_FILE):
        try:
            with open(PIN_MAP_FILE, "rt") as file:  # noqa: UP015
                return json.load(file)
        except json.JSONDecodeError:
            logger.error(f"Warning: Pin map file {PIN_MAP_FILE} is not a valid JSON.")
    return None


class ConfigManager:
    """
    Manages application configuration settings for Firestarter.
    It handles loading configuration from a JSON file, saving changes,
    and providing access to configuration values. Implemented as a singleton
    to ensure a single, consistent source of configuration throughout the
    application.
    It's a singleton per configuration file name.
    """

    _instances: dict[
        str, "ConfigManager"
    ] = {}  # Stores instances, keyed by config file path
    _initialized_configs: dict[
        str, bool
    ] = {}  # Tracks initialization status, keyed by config file path  # noqa: E501

    def __new__(cls, config_filename: Optional[str] = None, *args, **kwargs):
        actual_filename = config_filename or CONFIG_FILE_DEFAULT
        instance_key = os.path.join(HOME_PATH, actual_filename)

        if instance_key not in cls._instances:
            cls._instances[instance_key] = super(ConfigManager, cls).__new__(cls)  # noqa: UP008
        return cls._instances[instance_key]

    def __init__(self, config_filename: Optional[str] = None):
        actual_filename = config_filename or CONFIG_FILE_DEFAULT
        self.config_file_path = os.path.join(HOME_PATH, actual_filename)

        if self.config_file_path in ConfigManager._initialized_configs:
            return

        self._config: dict[str, Any] = {}
        # Keys set with persist=False. They live in _config so get_value sees
        # them for this invocation, but _save_config must exclude them: a later
        # persist=True write of an UNRELATED key (e.g. firmware.py caching
        # "avrdude-path" after a successful flash) dumps the whole dict, which
        # would otherwise make a one-shot `--port` stick forever and silently
        # retarget every later command at that port.
        self._transient_keys: set[str] = set()
        self._load_config()
        ConfigManager._initialized_configs[self.config_file_path] = True
        logger.debug(f"ConfigManager initialized for {self.config_file_path}.")

    def _load_config(self):
        """
        Loads the configuration from the configuration file.
        If the file doesn't exist, an empty configuration is used.
        """
        if os.path.exists(self.config_file_path):
            try:
                with open(self.config_file_path, "r") as file:  # noqa: UP015
                    self._config = json.load(file)
            except json.JSONDecodeError:
                logger.error(
                    f"Error: Configuration file {self.config_file_path} "
                    "is not a valid JSON. Resetting configuration."
                )
                self._config = {}
        else:
            self._config = {}

    def _save_config(self):
        """
        Saves the current configuration to the configuration file.
        Ensures the configuration directory exists.
        """
        if not os.path.exists(HOME_PATH):
            try:
                os.makedirs(HOME_PATH)
            except OSError as e:
                logger.error(
                    f"Error: Unable to create configuration directory {HOME_PATH}: {e}"
                )
                return
        persistable = {
            k: v for k, v in self._config.items() if k not in self._transient_keys
        }
        try:
            with open(self.config_file_path, "w") as f:
                json.dump(persistable, f, indent=4)
        except IOError as e:  # noqa: UP024
            logger.error(
                f"Error: Unable to save configuration to {self.config_file_path}: {e}"
            )

    def get_value(self, key, default=None):
        """
        Retrieves a value from the configuration.
        Args:
            key (str): The configuration key to retrieve.
            default: The default value to return if the key is not found.
        Returns:
            The value associated with the key or the default value.
        """
        return self._config.get(key, default)

    def set_value(self, key, value, persist=True):
        """
        Sets a value in the configuration.
        Args:
            key (str): The configuration key to set.
            value: The value to associate with the key.
            persist (bool): If True (default), save the configuration file to disk.
                If False, only update the in-memory value (used for CLI overrides
                that should not stick for future invocations).
        """
        if value is None:
            if persist:
                self.remove_key(key)
            else:
                self._config.pop(key, None)
                self._transient_keys.discard(key)
            return
        self._config[key] = value
        if persist:
            # An explicit persisted write promotes the key out of transient
            # status — otherwise `config port X` would be silently discarded
            # after a `--port Y` earlier in the same process.
            self._transient_keys.discard(key)
            self._save_config()
        else:
            self._transient_keys.add(key)

    def remember_port(self, port_name: str) -> None:
        """Record a port that just worked, for the next invocation's convenience.

        The single writer of the saved "port" key, so the rule cannot drift: a
        port the operator typed for THIS invocation is NEVER promoted into the
        saved config. `--port` is documented as applying to one invocation, yet
        two separate call sites used to persist it — the successful probe in
        `serial_comm` and the successful flash in `firmware` — so
        `~/.firestarter/config.json` silently acquired a `port` key from a
        one-off `--port` and retargeted every later command. Now that a typed
        port also RESTRICTS discovery, promoting it would strand them there too.

        `tests/test_fw_port_targeting_and_blind_install.py` carries a source-level
        tripwire asserting no production module writes the key directly.
        """
        self.set_value("port", port_name, persist=not self.is_transient("port"))

    def is_transient(self, key) -> bool:
        """True if `key` was set for this invocation only (persist=False).

        The discriminator between "the operator typed --port this run" and "a
        port remembered from a previous successful run". The first is a command
        and must be obeyed exactly; the second is a hint that has to yield to
        discovery, or replugging a board would strand every later invocation on
        a port that no longer exists.
        """
        return key in self._transient_keys

    def remove_key(self, key):
        """
        Removes a key from the configuration and saves the changes.
        Args:
            key (str): The configuration key to remove.
        """
        self._transient_keys.discard(key)
        if key in self._config:
            del self._config[key]
            self._save_config()

    def list_all(self):
        """
        Returns all configuration keys and values as a dictionary.
        """
        return self._config.copy()


# For testing or standalone execution
if __name__ == "__main__":
    cfg_manager = ConfigManager()
    print("Current Configuration:")
    for key, value in cfg_manager.list_all().items():
        print(f"{key}: {value}")
