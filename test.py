def display_jumper_configuration(config):
    # Define the pin labels and their positions
    jumpers = {
        "j1": {"5v": "(● ●)● ", "a17": " ●(● ●)"},
        "j2": {"5v": "(● ●) ●", "a13": " ● ● ●"},
        "j3": {"32pin": "(●●) ●", "28pin": "● (●●)"},
    }

    # Parse the input configuration
    config_dict = {}
    for conf in config.split(","):
        jumper, setting = conf.split("=")
        config_dict[jumper.strip()] = setting.strip()

    # Display the jumper settings
    for jumper, setting in config_dict.items():
        if jumper in jumpers and setting in jumpers[jumper]:
            print(f"{jumper}: {jumpers[jumper][setting]}")
        else:
            print(f"Invalid configuration: {jumper}={setting}")


# Example usage:
configuration = "j1=5v,j2=a13,j3=32pin"
display_jumper_configuration(configuration)
