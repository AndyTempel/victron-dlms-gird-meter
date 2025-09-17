# dbus-dlms-meter

## Overview

This driver reads DLMS Push messages from Grid meters via RS485 converter, interprets the data, and posts it to the DBus service on Victron Energy systems. It enables integration of DLMS-compatible meters into the Victron ecosystem.

## Features

- Reads data from DLMS Grid meters via RS485 serial converter
- Interprets DLMS Push messages (no polling required)
- Augments meter data with additional information
- Posts data to the DBus as a grid service
- Compatible with Victron Energy GX devices

## Installation

1. SSH into your Venus device (as root)

If you haven't enabled root access via SSH, follow the instructions here: https://www.victronenergy.com/live/ccgx:root_access.

2. Download and extract the repository

```bash
mkdir -p /data/drivers
cd /data/drivers
git clone https://github.com/AndyTempel/dbus-dlms-meter.git
cd dbus-dlms-meter
```

3. Configure the service

```bash
cp config.py.example config.py
nano config.py
```

Edit the configuration file to match your setup (serial port, authentication keys, etc.)

4. Install service

```bash
./bin/setup.sh
```

5. Reboot the device

```bash
reboot
```

## Configuration

Copy `config.py.example` to `config.py` and edit the following parameters:

```python
# Serial interface configuration
TTY_INTERFACE = 'ttyUSB0'  # Change to your serial port
SERIAL_BAUD_RATE = 38400   # Default baud rate for most DLMS meters
BYTE_SIZE = 8
PARITY = Parity.NONE       # No parity
STOP_BITS = StopBits.ONE   # One stop bit

# DLMS security settings
AUTHENTICATION_KEY = ''     # Optional: Set your authentication key
BLOCK_CIPHER_KEY = ''       # Optional: Set your encryption key
TELEGRAM_ID = 'si-sodo-reduxi'  # File for telegram definitions
```

## USB serial adapter identification (recommended)

To make sure serial-starter ignores your RS485 adapter reliably, set the FTDI Product Description of your USB–RS485 adapter to a known value, and match that in the setup script.

- Recommended value: KSOFTESS (this repository expects this by default).
- How to set it: On Windows, use FT_Prog to program the adapter and set the Product Description field to KSOFTESS. Apply the changes and replug the adapter.
- Script reference: bin/setup-dependencies.sh uses SERIAL_ID_MODEL to generate a udev rule and to detect the correct tty device. Default is:
  - SERIAL_ID_MODEL="KSOFTESS"
- If you choose a different description, update both places in the script accordingly:
  - The SERIAL_ID_MODEL variable near the top
  - The CUSTOM_UDEV_RULE that includes ENV{ID_MODEL}=="..."
- Verify on device:
  - Run: `udevadm info --query=property --name=/dev/ttyUSB0 | grep ^ID_MODEL=` and check it matches your chosen value.
  - Re-run `./bin/setup.sh` after changing the description or script to refresh the udev rule and serial handling.

## How it Works

The service performs the following functions:

1. **Serial Connection**: Establishes a connection to the configured serial port where the RS485 converter is attached.

2. **DLMS Push Listening**: Listens for DLMS Push messages from the grid meter. These are spontaneous messages sent by the meter without polling.

3. **Message Processing**: Decodes and interprets the DLMS messages according to the DLMS/COSEM protocol.

4. **DBus Registration**: Registers as a grid service on the DBus using the com.victronenergy.grid interface.

5. **Data Publishing**: Publishes the interpreted meter data to the appropriate DBus paths for consumption by the Victron system.

## Supported Meters

This service has been tested with the following DLMS-compatible meters:
- Iskraemeco AM550

Most DLMS meters need to have a converter to RS485, such as the [Reduxi Converter for AM550](https://support.reduxi.eu/hc/en-us/articles/13592127131409-Reduxi-Converter-for-Iskraemeco-AM550-meter).

## Troubleshooting

### Service not starting

Check the service status:
```bash
svstat /service/dbus-dlms-meter
```

### Serial communication issues

1. Verify the correct TTY interface in your config.py
2. Check cable connections to your RS485 converter
3. Ensure the baud rate matches your meter's configuration
4. Set the FTDI Product Description of your USB RS485 adapter using FT_Prog to "KSOFTESS" (default expected here). If you pick a different value, update SERIAL_ID_MODEL and the CUSTOM_UDEV_RULE in bin/setup-dependencies.sh accordingly.
5. If the issue persists, add a rule to a serial-starter rules file `/etc/udev/rules.d/serial-starter.rules`
   Example rule: ```ACTION=="add", ENV{ID_BUS}=="usb", ENV{ID_MODEL}=="KSOFTESS",                   ENV{VE_SERVICE}="ignore"```



### View service logs

```bash
tail -f /var/log/dbus-dlms-meter/current | tai64nlocal
```

### Common errors

- **Permission denied**: Make sure the TTY device has proper permissions
- **No data received**: Check the meter configuration and ensure it's set to send Push messages
- **Decoding errors**: Verify your authentication and cipher keys are correct

## For Developers

### Dependencies

- Python 3.x
- gurux-dlms (for DLMS protocol)
- Victron DBus libraries
- uv dependency management

### Testing

```bash
cd /data/drivers/dbus-dlms-meter
python -m unittest discover tests
```

## License

MIT License (MIT)

## Acknowledgments

- Victron Energy for their DBus API documentation
- DLMS User Association for protocol specifications
- Gurux for the gurux-dlms library
