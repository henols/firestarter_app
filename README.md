# Firestarter EPROM programmer, python application
Firestarter is an application for the [Relatively-Universal-ROM-Programmer](https://github.com/AndersBNielsen/Relatively-Universal-ROM-Programmer).


Get one here [Relatively-Universal-ROM-Programmer](https://www.imania.dk/samlesaet-hobbyelektronik-og-ic-er-relatively-universal-rom-programmer.htm).

Arduino PlatformIO project is found here [Firestarter](https://github.com/henols/firestarter).

Firestarter in action [watch the video](https://youtu.be/JDHOKbyNnrE?si=0_iXKPZZwyyNGUTZ).

Anders S Nielsen creator of the Relatively-Universal-ROM-Programmer talks about Firestarter [watch the video](https://youtu.be/SZQ50XZlk5o?si=IKOqQUeG4Rms1cUs).

Support and discussions forum at [Discord](https://discord.com/invite/kmhbxAjQc3).

## Table of Content
- [Installation](#Installation)
  - [Installing the Firestarter Python Program](#installing-the-firestarter-python-program)
  - [Installing the Firmware on the Arduino](#installing-the-firmware-on-the-arduino)
- [Usage](#usage)
  - [General Usage](#general-usage)
  - [Commands](#commands)
    - [Read](#read)
    - [Write](#write)
    - [Blank](#blank)
    - [Erase](#erase)
    - [Id](#id)
    - [Info](#info)
    - [Vpp](#vpp)
    - [Vpe](#vpe)
    - [Hw](#hardware)
    - [Fw](#firmware)
    - [Config](#configuration)

- [Examples](#examples)
  - [Read](#read-1)
  - [Write](#write-1)
  - [Info](#info-1)
- [Override and adding to the database](#override-and-adding-to-the-database)
  - [Eprom Configuration](#eprom-configuration)
  - [Pin Layout](#pin-layout)
  - [Contributing to the database](#contributing-to-the-database)
- [Handling EPROMs with Non-Standard Pin Layout (Adapters)](#handling-eproms-with-non-standard-pin-layout-adapters)
  - [Configuration Steps](#configuration-steps)
  - [Using the Adapter Configuration](#using-the-adapter-configuration)
- [Contributing](#contributing)
- [License](#license)

## Installation
To install the Firestarter Python program and the firmware on the Arduino, follow the steps below:

### Installing the Firestarter Python Program
Use the package manager [pip](https://pip.pypa.io/en/stable/) to install Firestarter.

``` bash
pip install firestarter 
```

This command installs the Firestarter application, which allows you to interact with EPROMs using the Relatively-Universal-ROM-Programmer.

#### Auto complete
How to enable auto complete for firestarter, see: [auto complete](autocomplete.md)

### Installing the Firmware on the Arduino

First, a few things to remember. 
1. Don't attach the Programmer shield while power is on.
2. Don't turn on power if the controller (Arduino) isn't programmed with appropriate firmware.
3. Don't insert a ROM in the socket until you're ready to write it. Don't leave a ROM in the socket during reset or programming.

To install the firmware on the Arduino, use the **fw** command with the `--install` option. This command installs the latest firmware version on the Arduino.

Firestarter is using **avrdude** to install the firmware.
**avrdude** is avalible as a separate [installer](https://github.com/avrdudes/avrdude) or via Arduino IDE.

#### Options
* `-p, --avrdude-path <path>`: Full path to avrdude (optional), set if avrdude is not found.
* `-c, --avrdude-config-path <path>`: Full path to avrdude config file (optional), may be needed if avrdude cannot find its default configuration (e.g., with older versions like 6.3 or non-standard installations).
* `--port <port>`: Serial port name (optional), set if the Arduino is not found.
* `-b, --board <board>`: Microcontroller board (optional), defaults to *uno*, other supported boards *leonardo*.
* `-f, --force`: Will install firmware even if the version is the same.
### Description
The `fw --install` command installs the latest firmware on the Arduino. The process typically involves the following steps:

1. **Locate avrdude**: The command locates the avrdude tool, which is used to upload the firmware to the Arduino. You can specify the path to `avrdude` using the `--avrdude-path` option if it is not found automatically.
2. **Identify Serial Port**: The command identifies the serial port to which the Arduino is connected. You can specify the port using the `--port` option.
3. **Upload Firmware**: The command uploads the latest firmware to the Arduino using `avrdude`.

### Example
To install the firmware on the **Arduino UNO**, you can run:
``` bash
firestarter fw --install
```

To install the firmware on the **Arduino Leonardo**, you can run:
``` bash
firestarter fw --install --board leonardo
```


## Usage
Firestarter provides several commands to interact with EPROMs using the Relatively-Universal-ROM-Programmer.
Available commands: read, write, blank, erase, list, search, info, vpp, vcc, fw, config

### General Usage

``` bash
firestarter [options] {command}
```
  * `-h, --help`: Show help message
  * `-v, --verbose`: Enable verbose mode
  * `--version`: Show the Firestarter version and exit.

### Commands

#### Read
Reads the content from an EPROM.
``` bash
firestarter read <eprom> [output_file]
```
* `<eprom>`: The name of the EPROM.
* `[output_file]`: (Optional) Output file name, defaults to `<EPROM_NAME>.bin`.
##### Description
The read command reads the data from the specified EPROM and optionally saves it to a file. The process typically involves the following steps:

1. **Identify EPROM:** The command identifies the specified EPROM.
2. **Read Data:** The data from the EPROM is read.
3. **Save Data:** If an output file is specified, the read data is saved to that file. If no output file is specified, the data is saved to a file named `<EPROM_NAME>.bin`.

#### Write
Writes a binary file to an EPROM.
``` bash
firestarter write <eprom> <input_file> [options]
```
* `<eprom>`: The name of the EPROM.
* `<input_file>`: Input file name.
##### Options
* `-b, --ignore-blank-check`: Ignore blank check before write (and skip erase).
* `-f, --force`: Force write, even if the VPP or chip ID don't match.
* `-a, --address <address>`: Write start address in decimal or hexadecimal.
##### Description
The write command writes the contents of a specified binary file to the EPROM. The process typically involves the following steps:

1. **Blank Check:** By default, the command checks if the EPROM is blank before writing. This can be skipped using the `--ignore-blank-check` option.
2. **Erase:** If the EPROM is not blank, it may need to be erased before writing. This step is also skipped if `--ignore-blank-check` is used.
3. **Write:** The binary data from the input file is written to the EPROM starting at the specified address (if provided).
4. **Verification:** The written data is verified to ensure it matches the input file.

#### Blank 
Checks if an EPROM is blank.
``` bash
firestarter blank <eprom>
```

* `<eprom>`: The name of the EPROM.

#### Erase
Erases an EPROM, if supported.
``` bash
firestarter erase <eprom>
```
* `<eprom>`: The name of the EPROM.
#### List
Lists all EPROMs in the database.
``` bash
firestarter list [options]
```

* `-v, --verified`: Only shows verified EPROMs.

#### Search
Searches for EPROMs in the database.
``` bash
firestarter search <text>
```

* `<text>`: Text to search for.

#### Info
Displays information about an EPROM.
``` bash
firestarter info <eprom>
```

* `<eprom>`: EPROM name.
##### Options
* `-c, --config`: Show EPROM configuration data in JSON format.

##### Description
The info command retrieves and displays detailed information about the specified EPROM. This information typically includes:

* EPROM name
* Manufacturer
* Device ID
* Memory size
* Supported voltages
* Pin configuration
* Any other relevant technical specifications

#### VPP
Displays the VPP voltage.
``` bash
firestarter vpp
```

#### VCC
Displays the VCC voltage.
``` bash
firestarter vcc
```

#### Firmware
Checks or installs the firmware version.
``` bash
firestarter fw [options]
```

* `-i, --install`: Try to install the latest firmware. (Do this *without* a chip in the socket or without the shield attached)
* `-p, --avrdude-path <path>`: Full path to avrdude (optional), set if avrdude is not found.
* `--port <port>`: Serial port name (optional).
* `-c, --avrdude-config-path <path>`: Full path to avrdude config file (optional).
* `-b, --board <board>`: Microcontroller board (optional, defaults to *uno*).
* `-f, --force`: Install firmware even if the version matches.

#### Configuration
Shows current configuration values. Use options to set specific values.
``` bash
firestarter config [options]
```

* `-rev, <revision>`: WARNING Overrides hardware revision (0-2), only use with HW mods. -1 disables override.
* `-r1, --r16 <resistance>`: Set R16 resistance, resistor connected to VPE.
* `-r2, --r14r15 <resistance>`: Set R14/R15 resistance, resistors connected to GND.

## Examples
### Read
To read an EPROM named W27C512 and save the output to output.bin:
``` bash
firestarter read W27C512 output.bin
```
### Write
To write a binary file input.bin to an EPROM named W27C512:
``` bash
firestarter write W27C512 input.bin
```
### Info
To get information about an EPROM named W27C512:
``` bash
firestarter info W27C512
```
This command will output detailed information about the W27C512 EPROM, showing package layout and the jumper configuration for the RURP shield.
``` text
Eprom Info 
Name:           W27C512
Manufacturer:   WINBOND
Number of pins: 28
Memory size:    0x10000
Type:           EPROM
Can be erased:  True
Chip ID:        0xda08
VPP:            12v
Pulse delay:    100µS

       28-DIP package
        -----v-----
  A15 -|  1     28 |- VCC   
  A12 -|  2     27 |- A14   
  A7  -|  3     26 |- A13   
  A6  -|  4     25 |- A8    
  A5  -|  5     24 |- A9    
  A4  -|  6     23 |- A11   
  A3  -|  7     22 |- OE/Vpp
  A2  -|  8     21 |- A10   
  A1  -|  9     20 |- CE    
  A0  -| 10     19 |- D7    
  D0  -| 11     18 |- D6    
  D1  -| 12     17 |- D5    
  D2  -| 13     16 |- D4    
  GND -| 14     15 |- D3    
        -----------

        Jumper config
JP1    5V [ ●(● ●)] A13   : A13
JP2    5V [(● ●)● ] A17   : VCC
JP3 28pin [ ● ● ● ] 32pin : NA
```

## Override and adding to the database
### Eprom Configuration
By adding a file named `database.json` in the **.firestarter** folder under the home directory, it is possible to overide **Eprom** configurations or add new **Eproms** in the Firestarter database.

To overide an **Eprom** configuration extract the configuration for the intended **Eprom** with the info config command, ex: `firestarter info W27C512 -c` and the configuration will be printed.

W27C512 config:
``` json
{
    "WINBOND": [
        {
            "name": "W27C512",
            "pin-count": 28,
            "can-erase": true,
            "has-chip-id": true,
            "chip-id": "0x0000da08",
            "pin-map": 16,
            "protocol-id": "0x07",
            "memory-size": "0x10000",
            "type": "memory",
            "voltages": {
                "vpp": "12"
            },
            "pulse-delay": "0x0064",
            "flags": "0x00000078",
            "verified": true
        }
    ]
}
```
To overide the `pulse-delay` copy the configuration into the `database.json` in the **.firestarter** folder and remove all fields that aren't relevant to the change except for the manufacturer name and the **Eprom** name, ex:
``` json
{
    "WINBOND": [
        {
            "name": "W27C512",
            "pulse-delay": "0x0064"
        }
    ]
}
```

### Pin layout
By adding a file named `pin-maps.json` in the **.firestarter** folder under the home directory, it is possible to overide a **pin map** or add new **pin map** that aren't existing.

To overide or add a new **pin map** use the info config command, ex: `firestarter info W27C512 -c` to get a starting point.
W27C512 pin map:
``` json
{
    "28": {
        "16": {
            "address-bus-pins": [
                10, 9, 8, 7, 6, 5, 4, 3, 25, 24, 21, 23, 2, 26, 27, 1
            ]
        }
    }
}
```
The top level key (`"28"`) refers to the number of pins for the **Eprom** and the sub-key (`"16"`) is the `pin-map` in the configuration data for an **Eprom**.
Valid fields:
- `address-bus-pins` is an array with the pin numbers from the **Eprom** pin layout, starts with least significant address pin (A0) and added in order to the most significant address pin (A0,A2,...Ax).
- `rw-pin` Optional, if a read/write or a program enable pin is pressent `rw-pin` must be set to the pin layout number.
- `vpp-pin` Optional, if the **Eprom** needs programming voltage (Vpp) on pin 1 `vpp-pin` must be set to 1.

### Contributing to the database
Please report back new or changed configurations to Firestarter.
Create an [Issue](https://github.com/henols/firestarter_app/issues) where you describe the added **Eprom** or the changes that are done and add the json configurations from the database.json and pin-maps.json that are made.

## Handling EPROMs with Non-Standard Pin Layout (Adapters)

Some EPROMs, especially older 24-pin types or those adapted from other packages, require the programming voltage (VPP) or have or not using the OE (Output Enable) pin on a pin different from the standard locations the Firestarter hardware shield expects.

*   **Standard 28/32-pin Sockets:** Firestarter typically applies VPP via **Pin 1** or on the **OE (Output Enable) Pin (20)**.
*   **Standard 24-pin Sockets (using specific protocols):** Might not have the **OE (Output Enable)** pin (e.g., Pin 20 on a 24-pin EPROM, which maps to Pin 22 on the 28-pin ZIF socket), wich Firestarter expects it to be connected to.

When an EPROM's VPP pin doesn't match these expectations (like the M2716 needing VPP on its Pin 21) or the **OE** is omitted, you need two things:

1.  **A Physical Hardware Adapter:** This adapter sits between the Firestarter shield's socket and the EPROM. It reroutes the VPP signal or the OE pin from the pin where Firestarter *outputs* it (e.g., Pin 1 of the 28-pin socket) to the pin where the EPROM *needs* it (e.g., Pin 21 of the M2716). It also maps all other necessary pins correctly.
2.  **A Custom Software Configuration:** You need to tell Firestarter about this new "virtual" EPROM definition that uses the adapter. This involves defining the EPROM's characteristics and, crucially, the *new pin mapping* as seen by the programmer through the adapter.

### Configuration Steps:

The configuration requires adding entries to two files in your `.firestarter` user directory: `database.json` (for the EPROM definition) and `pin-maps.json` (for the physical pin connections of the adapter).

#### Step 1: Define the Adapter Setup in `database.json`

1.  **Find Base Configuration:** Get the original JSON configuration for your EPROM (e.g., `firestarter info M2716 -c`).
2.  **Create New Entry:** In your user `database.json` file, add a new entry under the appropriate manufacturer (e.g., "INTEL").
3.  **Assign Unique Name:** Give it a descriptive name indicating it uses an adapter (e.g., `M2716-adapter`).
4.  **Set `pin-count`:** Use the pin count of the *adapter's socket* that plugs into the programmer (usually 28 or 32). For the M2716 adapter example, this is `28`.
5.  **Assign Unique `pin-map` ID:** Create a *new, unique identifier* for this adapter's pin mapping. This ID links this definition to the corresponding map in `pin-maps.json`. In the example, `2716` is used.
6.  **Copy/Verify Parameters:** Copy essential parameters like `protocol-id`, `memory-size`, `type`, `voltages` (ensure VPP voltage is correct!), `pulse-delay`, and `flags` from the original EPROM definition. Adjust `can-erase` and `has-chip-id` as needed for the specific chip.

**Example (`database.json` entry for M2716-adapter):**

```json
{
    "INTEL": [
        {
            "name": "M2716-adapter",  // Unique name for the adapter setup
            "pin-count": 28,          // Pin count of the adapter socket plugging into the programmer
            "can-erase": false,
            "has-chip-id": false,
            "pin-map": 2716,          // Unique ID linking to pin-maps.json
            "protocol-id": "0x0b",    // Copied from original M2716
            "memory-size": "0x800",   // Copied from original M2716
            "type": "memory",
            "voltages": {
                "vpp": "25"           // VPP required by the M2716
            },
            "pulse-delay": "0x01f4",  // Copied from original M2716
            "flags": "0x00000048",    // Copied from original M2716
            "verified": false
        }
    ]
}
```

#### Step 2: Define the Adapter Pin Mapping in `pin-maps.json`
1. **Find/Create Section:** In your user pin-maps.json, find or create the section corresponding to the `pin-count` of your adapter socket (e.g., `"28"`).
2. **Add New Map Entry:** Add a new key using the exact unique *`pin-map`* ID you created in `database.json` (e.g., `"2716"`).
3. **Define `address-bus-pins`:** List the programmer socket pins (the pins on the 28-pin ZIF socket in this example) that are connected to the EPROM's address lines (A0, A1, A2...) through the adapter. The order must be A0 first, then A1, A2, etc.
4. **Define vpp-pin (Crucial for VPP Rerouting):**
    * If your adapter routes VPP from the programmer's Pin 1 (standard 28/32-pin VPP output) to the EPROM's actual VPP pin, add `"vpp-pin": 1`. This tells Firestarter to control VPP using its standard VPP circuitry connected to Pin 1 of its socket. **This is the case in the M2716-adapter example.**
    * If your adapter routes VPP from the programmer's **OE Pin** (Pin 22 on the 28-pin socket) to the EPROM's actual VPP pin, you would typically omit the `vpp-pin` entry here. Firestarter would likely use the OE pin for VPP control based on the `protocol-id` (like `0x0b`).
5. **Define `rw-pin` (If Applicable):** If the EPROM has a Write Enable (WE) or Program (PGM) pin, add `rw-pin` and set its value to the programmer socket pin connected to the EPROM's WE/PGM pin via the adapter.

**Example (`pin-maps.json` entry for M2716-adapter):**

*(This adapter routes VPP from the programmer's Pin 1 to the EPROM's Pin 21)*
```json
{
    "28": {
        "2716": { // Matches the pin-map ID from database.json
            "address-bus-pins": [
                10, 9, 8, 7, 6, 5, 4, 3, 25, 24, 21 // Programmer socket pins for A0-A10 via adapter
            ],
            "vpp-pin": 1 // Tells Firestarter to use Pin 1 on its socket for VPP control
        }
        // ... other pin maps for 28-pin sockets ...
    }
    // ... other pin counts like "24", "32" ...
}
```
### Using the Adapter Configuration
After saving these changes to your user `database.json` and `pin-maps.json` files, you can now use the adapter definition directly with Firestarter:
```bash
firestarter write M2716-adapter my_rom_file.bin
firestarter read M2716-adapter backup.bin
firestarter blank M2716-adapter
```
Firestarter will use the `M2716-adapter` definition, look up the `pin-map` ID `2716` under the `28`-pin section in `pin-maps.json`, and correctly control the address lines and apply VPP using Pin 1 of its own socket, which the physical adapter then routes to the correct pin on the M2716 EPROM.

## Contributing
Pull requests are welcome. For major changes, please open an issue first
to discuss what you would like to change.

Please make sure to update tests as appropriate.

## License
[MIT](https://raw.githubusercontent.com/henols/firestarter_app/main/LICENSE)

