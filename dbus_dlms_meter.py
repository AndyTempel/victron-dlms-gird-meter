#!/usr/bin/env python3

"""
A class to put a simple service on the dbus, according to victron standards, with constantly updating
paths. See example usage below. It is used to generate dummy data for other processes that rely on the
dbus. See files in dbus_vebus_to_pvinverter/test and dbus_vrm/test for other usage examples.

To change a value while testing, without stopping your dummy script and changing its initial value, write
to the dummy data via the dbus. See example.

https://github.com/victronenergy/dbus_vebus_to_pvinverter/tree/master/test
"""
import logging
import os
import platform
import sys

from gi.repository import GLib

from dlms_listener import DLMSListener

logging.basicConfig(level=logging.INFO)
# our own packages
AppDir = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(1, os.path.join(AppDir, "ext", "velib_python"))
from vedbus import VeDbusService

try:
    import config
except ImportError:
    logging.critical("config.py not found, create it in the same directory as this script.")
    raise FileNotFoundError("config.py not found")


class DbusDlmsMeterService(object):
    def __init__(
        self,
        servicename,
        deviceinstance,
        paths,
        productname="DLMS Grid Meter",
        connection="RS485 ttyUSB0",
        productid=0,
    ):
        self._dbusservice = VeDbusService(servicename, register=False)
        self._paths = paths
        self._loop = None  # will be set by the listener

        logging.debug("%s /DeviceInstance = %d" % (servicename, deviceinstance))

        # Create the mandatory objects
        self._dbusservice.add_mandatory_paths(
            __file__,
            platform.python_version(),
            connection,
            deviceinstance,
            productid=41392,
            productname=productname,
            firmwareversion=0.1,
            hardwareversion=0,
            connected=1,
        )
        self._dbusservice.add_path("/CustomName", "Generic DLMS Grid Meter")
        self._dbusservice.add_path("/Role", "grid")
        self._dbusservice.add_path("/Serial", "DLMS0000000." + config.TTY_INTERFACE)

        for path, settings in self._paths.items():
            self._dbusservice.add_path(
                path,
                settings["initial"],
                writeable=True,
                onchangecallback=self._handlechangedvalue,
            )

        self._dbusservice.register()

        # Gurux DLMS Meter Service
        logging.info("DbusDlmsMeterService initialized with service name: %s" % servicename)
        self._listener = DLMSListener(self)

    def _update(self):
        with self._dbusservice as s:
            for path, settings in self._paths.items():
                if "update" in settings:
                    update = settings["update"]
                    if callable(update):
                        s[path] = update(path, s[path])
                    else:
                        s[path] += update
                    logging.debug("%s: %s" % (path, s[path]))
        return True

    def _handlechangedvalue(self, path, value):
        logging.debug("someone else updated %s to %s" % (path, value))
        return True  # accept the change

    def exit_listener(self):
        logging.info("Exiting listener")
        self._listener.settings.media.close()
        self._listener.settings.media.removeListener(self._listener)


def main():
    logging.basicConfig(level=logging.DEBUG)

    from dbus.mainloop.glib import DBusGMainLoop

    # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
    DBusGMainLoop(set_as_default=True)

    _kwh = lambda p, v: (str(round(v, 2)) + "KWh")  # noqa: E731
    _a = lambda p, v: (str(round(v, 1)) + "A")  # noqa: E731
    _w = lambda p, v: (str(round(v, 1)) + "W")  # noqa: E731
    _v = lambda p, v: (str(round(v, 1)) + "V")  # noqa: E731
    _hz = lambda p, v: (str(round(v, 2)) + "Hz")  # noqa: E731
    _pf = lambda p, v: (str(round(v, 3)))  # noqa: E731

    dlms = DbusDlmsMeterService(
        servicename="com.victronenergy.grid." + config.TTY_INTERFACE,
        deviceinstance=0,
        paths={
            "/Ac/Energy/Forward": {
                "initial": 0,
                "textformat": _kwh,
            },  # energy bought from the grid
            "/Ac/Energy/Reverse": {
                "initial": 0,
                "textformat": _kwh,
            },  # energy sold to the grid
            "/Ac/Power": {"initial": 0, "textformat": _w},
            "/Ac/Current": {"initial": 0, "textformat": _a},
            "/Ac/Frequency": {"initial": 0, "textformat": _hz},
            "/Ac/PowerFactor": {"initial": 1, "textformat": _pf},  # power factor total
            "/Ac/L1/Voltage": {"initial": 0, "textformat": _v},
            "/Ac/L2/Voltage": {"initial": 0, "textformat": _v},
            "/Ac/L3/Voltage": {"initial": 0, "textformat": _v},
            "/Ac/L1/Current": {"initial": 0, "textformat": _a},
            "/Ac/L2/Current": {"initial": 0, "textformat": _a},
            "/Ac/L3/Current": {"initial": 0, "textformat": _a},
            "/Ac/L1/Power": {"initial": 0, "textformat": _w},
            "/Ac/L2/Power": {"initial": 0, "textformat": _w},
            "/Ac/L3/Power": {"initial": 0, "textformat": _w},
            "/Ac/L1/PowerFactor": {
                "initial": 1,
                "textformat": _pf,
            },  # power factor phase 1
            "/Ac/L2/PowerFactor": {
                "initial": 1,
                "textformat": _pf,
            },  # power factor phase 2
            "/Ac/L3/PowerFactor": {
                "initial": 1,
                "textformat": _pf,
            },  # power factor phase 3
        },
    )

    logging.info("Connected to dbus, and switching over to GLib.MainLoop() (= event based)")
    mainloop = GLib.MainLoop()
    dlms._loop = mainloop  # pass the mainloop to the listener
    logging.info("starting GLib.MainLoop")
    mainloop.run()
    logging.info("GLib.MainLoop was shut down")
    logging.info("exiting listener")
    try:
        dlms.exit_listener()
    except Exception as e:
        logging.error("Error while exiting listener: %s", e)
        logging.exception(e)

    sys.exit(0xFF)  # reaches this only on error


if __name__ == "__main__":
    main()
