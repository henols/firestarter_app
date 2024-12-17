# Firestarter EPROM progammer, python application
Firestarter is an application for the [Relatively-Universal-ROM-Programmer](https://github.com/AndersBNielsen/Relatively-Universal-ROM-Programmer).

Get one here [Relatively-Universal-ROM-Programmer](https://www.imania.dk/samlesaet-hobbyelektronik-og-ic-er-relatively-universal-rom-programmer.htm).

Arduino PlatformIO project is found here [Firestarter](https://github.com/henols/firestarter).

Firestarter in action [watch the video](https://youtu.be/JDHOKbyNnrE?si=0_iXKPZZwyyNGUTZ).

Anders S Nielsen creator of the Relatively-Universal-ROM-Programmer talks about Firestarter [watch the video](https://youtu.be/SZQ50XZlk5o?si=IKOqQUeG4Rms1cUs).

Support and discustions forum at [Discord](https://discord.com/invite/kmhbxAjQc3).

## Table of Content
- [Instralation](#Installation)
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

## Installation
To install the Firestarter Python program and the firmware on the Arduino, follow the steps below:

### Installing the Firestarter Python Program
Use the package manager [pip](https://pip.pypa.io/en/stable/) to install Firestarter.

``` bash
pip install firestarter 
```

This command installs the Firestarter application, which allows you to interact with EPROMs using the Relatively-Universal-ROM-Programmer.

### Installing the Firmware on the Arduino

First, a few things to remember. 
1. Don't attach the Programmer shield while power is on.
2. Don't turn on power if the controller (Arduino) isn't programmed with appropriate firmware.
3. Don't insert a ROM in the socket until you're ready to write it. Don't leave a ROM in the socket during reset or programming.

To install the firmware on the Arduino, use the **fw** command with the ```--install``` option. This command installs the latest firmware version on the Arduino.

Firestarter is using **avrdude** to install the firmware.
**avrdude** is avalible as a separate [installer](https://github.com/avrdudes/avrdude) or via Arduino IDE.

#### Options
* ```-p, --avrdude-path <path>```: Full path to avrdude (optional), set if avrdude is not found.
* ```-c, --avrdude-config-path <path>```: Full path to avrdude config (optional), set if avrdude version is 6.3 or not found.
* ```--port <port>```: Serial port name (optional), set if the Arduino is not found.
### Description
The ```fw --install``` command installs the latest firmware on the Arduino. The process typically involves the following steps:

1. **Locate avrdude**: The command locates the avrdude tool, which is used to upload the firmware to the Arduino. You can specify the path to ```avrdude``` using the ```--avrdude-path``` option if it is not found automatically.
2. **Identify Serial Port**: The command identifies the serial port to which the Arduino is connected. You can specify the port using the ```--port``` option.
3. **Upload Firmware**: The command uploads the latest firmware to the Arduino using ```avrdude```.

### Example
To install the firmware on the Arduino, you can run:
``` bash
firestarter fw --install
```


## Usage
Firestarter provides several commands to interact with EPROMs using the Relatively-Universal-ROM-Programmer.
Available commands: read, write, blank, erase, list, search, info, vpp, vcc, fw, config

### General Usage

```bash
firestarter [options] {command}
```
  * ```-h, --help```: Show help message
  * ```-v, --verbose```: Enable verbose mode
  * ```--version```: Show the Firestarter version and exit.

### Commands

#### Read
Reads the content from an EPROM.
```bash
firestarter read <eprom> [output_file]
```
* ```<eprom>```: The name of the EPROM.
* ```[output_file]```: (Optional) Output file name, defaults to ```<EPROM_NAME>.bin```.
##### Description
The read command reads the data from the specified EPROM and optionally saves it to a file. The process typically involves the following steps:

1. **Identify EPROM:** The command identifies the specified EPROM.
2. **Read Data:** The data from the EPROM is read.
3. **Save Data:** If an output file is specified, the read data is saved to that file. If no output file is specified, the data is saved to a file named ```<EPROM_NAME>.bin```.

#### Write
Writes a binary file to an EPROM.
```bash
firestarter write <eprom> <input_file> [options]
```
* ```<eprom>```: The name of the EPROM.
* ```<input_file>```: Input file name.
##### Options
* ```-b, --ignore-blank-check```: Ignore blank check before write (and skip erase).
* ```-f, --force```: Force write, even if the VPP or chip ID don't match.
* ```-a, --address <address>```: Write start address in decimal or hexadecimal.
##### Description
The write command writes the contents of a specified binary file to the EPROM. The process typically involves the following steps:

1. **Blank Check:** By default, the command checks if the EPROM is blank before writing. This can be skipped using the ```--ignore-blank-check``` option.
2. **Erase:** If the EPROM is not blank, it may need to be erased before writing. This step is also skipped if ```--ignore-blank-check``` is used.
3. **Write:** The binary data from the input file is written to the EPROM starting at the specified address (if provided).
4. **Verification:** The written data is verified to ensure it matches the input file.

#### Blank 
Checks if an EPROM is blank.
```bash
firestarter blank <eprom>
```

* ```<eprom>```: The name of the EPROM.

#### Erase
Erases an EPROM, if supported.
```bash
firestarter erase <eprom>
```
* ```<eprom>```: The name of the EPROM.
#### List
Lists all EPROMs in the database.
```bash
firestarter list [options]
```

* ```-v, --verified```: Only shows verified EPROMs.

#### Search
Searches for EPROMs in the database.
```bash
firestarter search <text>
```

* ```<text>```: Text to search for.

#### Info
Displays information about an EPROM.
```bash
firestarter info <eprom>
```

* ```<eprom>```: EPROM name.
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
```bash
firestarter vpp
```

#### VCC
Displays the VCC voltage.
```bash
firestarter vcc
```

#### Firmware
Checks or installs the firmware version.
```bash
firestarter fw [options]
```

* ```-i, --install```: Try to install the latest firmware. (Do this *without* a chip in the socket or without the shield attached)
* ```-p, --avrdude-path <path>```: Full path to avrdude (optional), set if avrdude is not found.
* ```--port <port>```: Serial port name (optional).

#### Configuration
Handles configuration values.
```bash
firestarter config [options]
```

* ```-rev, <revision>```: WARNING Overrides hardware revision (0-2), only use with HW mods. -1 disables override.
* ```-r1, --r16 <resistance>```: Set R16 resistance, resistor connected to VPE.
* ```-r2, --r14r15 <resistance>```: Set R14/R15 resistance, resistors connected to GND.

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
VPP:            12
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

To overide an **Eprom** configuration extract the configuration for the inteded **Eprom** with the info config command, ex: `firestarter info W27C512 -c` and the configuration will be printed.

W27C512 config:
```json
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
To overide the `pulse-delay` copy the configuration into the `database.json` in the **.firestarter** folder and remove all fields that arent relevant to the change exept for the manufacturer name and the **Eprom** name, ex:
```json
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
By adding a file named `pin-maps.json` in the **.firestarter** folder under the home directory, it is possible to overide a **pin map** or add new **pin map** that arent existing.

To overide or add a new **pin map** use the info config command, ex: `firestarter info W27C512 -c` to get a starting point.
W27C512 pin map:
```json
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
- `vpp-pin` Optional, if the **Eprom** needs progaming voltage (Vpp) on pin 1 `vpp-pin` must be set to 1.

### Contributing to the database
Please report back new och changed configurations to Firestarter.
Create an [Issue](https://github.com/henols/firestarter_app/issues) where you describe the added **Eprom** or the changes that are done and add the json configurations from the database.json and pin-maps.json that are made.


## Contributing
Pull requests are welcome. For major changes, please open an issue first
to discuss what you would like to change.

Please make sure to update tests as appropriate.

## License
[MIT](https://raw.githubusercontent.com/henols/firestarter_app/main/LICENSE)

