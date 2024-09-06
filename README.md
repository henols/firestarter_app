# Firestarter EPROM progammer, python application
Firestarter is an application for the [Relatively-Universal-ROM-Programmer](https://github.com/AndersBNielsen/Relatively-Universal-ROM-Programmer)

Get one here [Relatively-Universal-ROM-Programmer](https://www.imania.dk/samlesaet-hobbyelektronik-og-ic-er-relatively-universal-rom-programmer.htm)

Arduino PlatformIO project is found here [Firestarter](https://github.com/henols/firestarter)

Firestarter in action [watch the video](https://youtu.be/JDHOKbyNnrE?si=0_iXKPZZwyyNGUTZ)

Anders S Nielsen creator of the Relatively-Universal-ROM-Programmer talks about Firestarter [watch the video](https://youtu.be/SZQ50XZlk5o?si=IKOqQUeG4Rms1cUs)

Support and discustions forum at [Discord](https://discord.com/invite/kmhbxAjQc3)

## Installation
To install the Firestarter Python program and the firmware on the Arduino, follow the steps below:

### Installing the Firestarter Python Program
Use the package manager [pip](https://pip.pypa.io/en/stable/) to install Firestarter.

``` bash
pip install firestarter 
```

This command installs the Firestarter application, which allows you to interact with EPROMs using the Relatively-Universal-ROM-Programmer.

### Installing the Firmware on the Arduino
To install the firmware on the Arduino, use the **fw** command with the ```--install``` option. This command installs the latest firmware version on the Arduino.

#### Options
* ```-p, --avrdude-path <path>```: Full path to avrdude (optional), set if avrdude is not found.
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

* ```-i, --install```: Try to install the latest firmware.
* ```-p, --avrdude-path <path>```: Full path to avrdude (optional), set if avrdude is not found.
* ```--port <port>```: Serial port name (optional).

#### Configuration
Handles configuration values.
```bash
firestarter config [options]
```

* ```-v, --vcc <voltage>```: Set Arduino VCC voltage.
* ```-r1, --r16 <resistance>```: Set R16 resistance, resistor connected to VPE.
* ```-r2, --r14r15 <resistance>```: Set R14/R15 resistance, resistors connected to GND.

### Example
To read an EPROM named W27C512 and save the output to output.bin:
``` bash
firestarter read W27C512 output.bin
```

To write a binary file input.bin to an EPROM named W27C512:
``` bash
firestarter write W27C512 input.bin
```

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



## Contributing
Pull requests are welcome. For major changes, please open an issue first
to discuss what you would like to change.

Please make sure to update tests as appropriate.

## License
[MIT](https://raw.githubusercontent.com/henols/firestarter_app/main/LICENSE)

