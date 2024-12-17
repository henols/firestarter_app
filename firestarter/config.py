"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Configuration Management Module
"""

import os
import json

# Define the home path and configuration file path
HOME_PATH = os.path.join(os.path.expanduser("~"), ".firestarter")
CONFIG_FILE = os.path.join(HOME_PATH, "config.json")
DATABASE_FILE = os.path.join(HOME_PATH, "database.json")
PIN_MAP_FILE = os.path.join(HOME_PATH, "pin-maps.json")

# Global configuration dictionary
config = {}


def open_config():
    """
    Loads the configuration from the configuration file.
    If the file doesn't exist, an empty configuration is used.
    """
    global config
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as file:
                config = json.load(file)
        except json.JSONDecodeError:
            print(
                f"Error: Configuration file {CONFIG_FILE} is not a valid JSON. Resetting configuration."
            )
            config = {}


def save_config():
    """
    Saves the current configuration to the configuration file.
    Ensures the configuration directory exists.
    """
    if not os.path.exists(HOME_PATH):
        try:
            os.makedirs(HOME_PATH)
        except OSError as e:
            print(f"Error: Unable to create configuration directory {HOME_PATH}: {e}")
            return
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
    except IOError as e:
        print(f"Error: Unable to save configuration to {CONFIG_FILE}: {e}")

def get_local_database():
    if os.path.exists(DATABASE_FILE):
        try:
            with open(DATABASE_FILE, "rt") as file:
                return json.load(file)
        except json.JSONDecodeError:
            print(
                f"Warning: Database file {DATABASE_FILE} is not a valid JSON."
            )
    return None

def get_local_pin_maps():
    if os.path.exists(PIN_MAP_FILE):
        try:
            with open(PIN_MAP_FILE, "rt") as file:
                return json.load(file)
        except json.JSONDecodeError:
            print(
                f"Warning: Pin map file {PIN_MAP_FILE} is not a valid JSON."
            )
    return None

def get_config_value(key, default=None):
    """
    Retrieves a value from the configuration.
    Args:
        key (str): The configuration key to retrieve.
        default: The default value to return if the key is not found.
    Returns:
        The value associated with the key or the default value.
    """
    return config.get(key, default)


def set_config_value(key, value):
    """
    Sets a value in the configuration and saves the configuration file.
    Args:
        key (str): The configuration key to set.
        value: The value to associate with the key.
    """
    if value is None:
        remove_config_key(key)
        return
    config[key] = value
    save_config()


def remove_config_key(key):
    """
    Removes a key from the configuration and saves the changes.
    Args:
        key (str): The configuration key to remove.
    """
    if key in config:
        del config[key]
        save_config()


def list_config():
    """
    Lists all configuration keys and values.
    """
    if not config:
        print("No configuration values set.")
    else:
        print("Current Configuration:")
        for key, value in config.items():
            print(f"{key}: {value}")


# For testing or standalone execution
if __name__ == "__main__":
    open_config()
    list_config()
