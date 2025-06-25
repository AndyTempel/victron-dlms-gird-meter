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

# our own packages
AppDir = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(1, os.path.join(AppDir, 'ext', 'velib_python'))
from vedbus import VeDbusService

try:
	import config
except ImportError:
	logging.critical("config.py not found, create it in the same directory as this script.")
	raise FileNotFoundError("config.py not found")


class DbusDlmsMeterService(object):
	def __init__(self, servicename, deviceinstance, paths, productname='DLMS Grid Meter', connection='DLMS Push RS485',
				 productid=0):
		self._dbusservice = VeDbusService(servicename)
		self._paths = paths
		self._loop = None  # will be set by the listener

		logging.debug("%s /DeviceInstance = %d" % (servicename, deviceinstance))

		# Create the management objects, as specified in the ccgx dbus-api document
		self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
		self._dbusservice.add_path('/Mgmt/ProcessVersion',
								   'Unkown version, and running on Python ' + platform.python_version())
		self._dbusservice.add_path('/Mgmt/Connection', connection)

		# Create the mandatory objects
		self._dbusservice.add_path('/DeviceInstance', deviceinstance)
		# self._dbusservice.add_path('/ProductId', 16) # value used in ac_sensor_bridge.cpp of dbus-cgwacs
		# self._dbusservice.add_path('/ProductId', 0xFFFF) # id assigned by Victron Support from SDM630v2.py
		# self._dbusservice.add_path('/ProductId', 45069) # found on https://www.sascha-curth.de/projekte/005_Color_Control_GX.html#experiment - should be an ET340 Engerie Meter
		self._dbusservice.add_path('/ProductId',
								   0)  # id 0xB023 needs to be assigned by Victron Support current value for testing
		self._dbusservice.add_path('/DeviceType',
								   345)  # found on https://www.sascha-curth.de/projekte/005_Color_Control_GX.html#experiment - should be an ET340 Engerie Meter
		self._dbusservice.add_path('/ProductName', productname)
		self._dbusservice.add_path('/CustomName', 'Generic DLMS Grid Meter')
		self._dbusservice.add_path('/Latency', None)
		self._dbusservice.add_path('/FirmwareVersion', 1.0)
		self._dbusservice.add_path('/HardwareVersion', 0)
		self._dbusservice.add_path('/Connected', 1)
		self._dbusservice.add_path('/Role', 'grid')
		self._dbusservice.add_path('/Position', 0)  # normaly only needed for pvinverter
		self._dbusservice.add_path('/Serial', 'DLMS0000000.' + config.TTY_INTERFACE)
		self._dbusservice.add_path('/UpdateIndex', 0)

		for path, settings in self._paths.items():
			self._dbusservice.add_path(
				path, settings['initial'], writeable=True, onchangecallback=self._handlechangedvalue)

		self._dbusservice.register()

		# Gurux DLMS Meter Service
		self._listener = DLMSListener(self)

	def _update(self):
		with self._dbusservice as s:
			for path, settings in self._paths.items():
				if 'update' in settings:
					update = settings['update']
					if callable(update):
						s[path] = update(path, s[path])
					else:
						s[path] += update
					logging.debug("%s: %s" % (path, s[path]))
		return True

	def _handlechangedvalue(self, path, value):
		logging.debug("someone else updated %s to %s" % (path, value))
		return True  # accept the change

	def exit_listner(self):
		logging.info("Exiting listener")
		self._listener.settings.media.close()
		self._listener.settings.media.removeListener(self._listener)


def main():
	logging.basicConfig(level=logging.DEBUG)

	from dbus.mainloop.glib import DBusGMainLoop
	# Have a mainloop, so we can send/receive asynchronous calls to and from dbus
	DBusGMainLoop(set_as_default=True)

	_kwh = lambda p, v: (str(round(v, 2)) + 'KWh')  # noqa: E731
	_a = lambda p, v: (str(round(v, 1)) + 'A')  # noqa: E731
	_w = lambda p, v: (str(round(v, 1)) + 'W')  # noqa: E731
	_v = lambda p, v: (str(round(v, 1)) + 'V')  # noqa: E731
	_hz = lambda p, v: (str(round(v, 2)) + 'Hz')  # noqa: E731

	dlms = DbusDlmsMeterService(
		servicename='com.victronenergy.grid.' + config.TTY_INTERFACE,
		deviceinstance=0,
		paths={
			'/Ac/Energy/Forward': {'initial': 0, 'textformat': _kwh},  # energy bought from the grid
			'/Ac/Energy/Reverse': {'initial': 0, 'textformat': _kwh},  # energy sold to the grid

			'/Ac/Power': {'initial': 0, 'textformat': _w},
			'/Ac/Current': {'initial': 0, 'textformat': _a},
			'/Ac/Voltage': {'initial': 0, 'textformat': _v},
			'/Ac/Frequency': {'initial': 0, 'textformat': _hz},

			'/Ac/L1/Voltage': {'initial': 0, 'textformat': _v},
			'/Ac/L2/Voltage': {'initial': 0, 'textformat': _v},
			'/Ac/L3/Voltage': {'initial': 0, 'textformat': _v},
			'/Ac/L1/Current': {'initial': 0, 'textformat': _a},
			'/Ac/L2/Current': {'initial': 0, 'textformat': _a},
			'/Ac/L3/Current': {'initial': 0, 'textformat': _a},
			'/Ac/L1/Power': {'initial': 0, 'textformat': _w},
			'/Ac/L2/Power': {'initial': 0, 'textformat': _w},
			'/Ac/L3/Power': {'initial': 0, 'textformat': _w},
			'/Ac/L1/Energy/Forward': {'initial': 0, 'textformat': _kwh},
			'/Ac/L2/Energy/Forward': {'initial': 0, 'textformat': _kwh},
			'/Ac/L3/Energy/Forward': {'initial': 0, 'textformat': _kwh},
			'/Ac/L1/Energy/Reverse': {'initial': 0, 'textformat': _kwh},
			'/Ac/L2/Energy/Reverse': {'initial': 0, 'textformat': _kwh},
			'/Ac/L3/Energy/Reverse': {'initial': 0, 'textformat': _kwh},
		})

	logging.info('Connected to dbus, and switching over to GLib.MainLoop() (= event based)')
	mainloop = GLib.MainLoop()
	dlms._loop = mainloop  # pass the mainloop to the listener
	logging.info('starting GLib.MainLoop')
	mainloop.run()
	logging.info('GLib.MainLoop was shut down')
	logging.info('exiting listener')
	try:
		dlms.exit_listner()
	except Exception as e:
		logging.error("Error while exiting listener: %s", e)
		logging.exception(e)

	sys.exit(0xFF)  # reaches this only on error


if __name__ == "__main__":
	main()
