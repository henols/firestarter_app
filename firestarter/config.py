"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Configuration Management Module
"""

import os
import json
import logging

# Define the home path and configuration file path
HOME_PATH = os.path.join(os.path.expanduser("~"), ".firestarter")
CONFIG_FILE = os.path.join(HOME_PATH, "config.json")
DATABASE_FILE = os.path.join(HOME_PATH, "database.json")
PIN_MAP_FILE = os.path.join(HOME_PATH, "pin-maps.json")

logger = logging.getLogger("Config")

def get_local_database():
    """
    Loads the local user EPROM database override file.
    Returns:
        dict or None: The parsed JSON data if the file exists and is valid, otherwise None.
    """
    if os.path.exists(DATABASE_FILE):
        try:
            with open(DATABASE_FILE, "rt") as file:
                return json.load(file)
        except json.JSONDecodeError:
            logger.error(
                f"Warning: Database file {DATABASE_FILE} is not a valid JSON."
            )
    return None

def get_local_pin_maps():
    """
    Loads the local user pin map override file.
    Returns:
        dict or None: The parsed JSON data if the file exists and is valid, otherwise None.
    """
    if os.path.exists(PIN_MAP_FILE):
        try:
            with open(PIN_MAP_FILE, "rt") as file:
                return json.load(file)
        except json.JSONDecodeError:
            logger.error(
                f"Warning: Pin map file {PIN_MAP_FILE} is not a valid JSON."
            )
    return None

class ConfigManager:
    """
    Manages application configuration settings for Firestarter.
    It handles loading configuration from a JSON file, saving changes,
    and providing access to configuration values. Implemented as a singleton
    to ensure a single, consistent source of configuration throughout the
    application.
    """
    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(ConfigManager, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        if ConfigManager._initialized:
            return
        self._config = {}
        self._load_config()
        ConfigManager._initialized = True
        logger.debug("ConfigManager initialized.")

    def _load_config(self):
        """
        Loads the configuration from the configuration file.
        If the file doesn't exist, an empty configuration is used.
        """
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as file:
                    self._config = json.load(file)
            except json.JSONDecodeError:
                logger.error(
                    f"Error: Configuration file {CONFIG_FILE} is not a valid JSON. Resetting configuration."
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
                logger.error(f"Error: Unable to create configuration directory {HOME_PATH}: {e}")
                return
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self._config, f, indent=4)
        except IOError as e:
            logger.error(f"Error: Unable to save configuration to {CONFIG_FILE}: {e}")

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

    def set_value(self, key, value):
        """
        Sets a value in the configuration and saves the configuration file.
        Args:
            key (str): The configuration key to set.
            value: The value to associate with the key.
        """
        if value is None:
            self.remove_key(key)
            return
        self._config[key] = value
        self._save_config()

    def remove_key(self, key):
        """
        Removes a key from the configuration and saves the changes.
        Args:
            key (str): The configuration key to remove.
        """
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
