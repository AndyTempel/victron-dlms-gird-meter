import traceback
from pprint import pprint

import pkg_resources
from gurux_common.GXCommon import GXCommon
from gurux_common.IGXMediaListener import IGXMediaListener
from gurux_common.enums.TraceLevel import TraceLevel
from gurux_dlms.GXByteBuffer import GXByteBuffer
from gurux_dlms.GXDLMSTranslator import GXDLMSTranslator
from gurux_dlms.GXReplyData import GXReplyData
from gurux_dlms.enums import TranslatorOutputType
from gurux_dlms.enums.InterfaceType import InterfaceType
from gurux_dlms.secure import GXDLMSSecureClient
from gurux_serial.GXSerial import GXSerial

import config
from telegram_processor import TelegramProcessor


topic_dictionary = {
    # Total
    "ACTIVE_POWER_TOTAL": "/Ac/Power",
    "CURRENT_TOTAL": "/Ac/Current",
    "ACTIVE_ENERGY_IMPORT": "/Ac/Energy/Forward",
    "ACTIVE_ENERGY_EXPORT": "/Ac/Energy/Reverse",
    "FREQUENCY": "/Ac/Frequency",
    "SERIAL_NUMBER": "/Serial",
    # Phase 1
    "ACTIVE_POWER_TOTAL_L1": "/Ac/L1/Power",
    "CURRENT_L1": "/Ac/L1/Current",
    "VOLTAGE_L1": "/Ac/L1/Voltage",
    "ACTIVE_ENERGY_EXPORT_L1": "/Ac/L1/Energy/Reverse",
    "ACTIVE_ENERGY_IMPORT_L1": "/Ac/L1/Energy/Forward",
    # Phase 2
    "ACTIVE_POWER_TOTAL_L2": "/Ac/L2/Power",
    "CURRENT_L2": "/Ac/L2/Current",
    "VOLTAGE_L2": "/Ac/L2/Voltage",
    "ACTIVE_ENERGY_EXPORT_L2": "/Ac/L2/Energy/Reverse",
    "ACTIVE_ENERGY_IMPORT_L2": "/Ac/L2/Energy/Forward",
    # Phase 3
    "ACTIVE_POWER_TOTAL_L3": "/Ac/L3/Power",
    "CURRENT_L3": "/Ac/L3/Current",
    "VOLTAGE_L3": "/Ac/L3/Voltage",
    "ACTIVE_ENERGY_EXPORT_L3": "/Ac/L3/Energy/Reverse",
    "ACTIVE_ENERGY_IMPORT_L3": "/Ac/L3/Energy/Forward",
}

transform_multiply = {
    "/Ac/Energy/Forward": 0.001,
    "/Ac/Energy/Reverse": 0.001,
    "/Ac/L1/Energy/Forward": 0.001,
    "/Ac/L1/Energy/Reverse": 0.001,
    "/Ac/L2/Energy/Forward": 0.001,
    "/Ac/L2/Energy/Reverse": 0.001,
    "/Ac/L3/Energy/Forward": 0.001,
    "/Ac/L3/Energy/Reverse": 0.001,
}


class GXSettings:
	def __init__(self):
		self.client = GXDLMSSecureClient(True)
		if isinstance(config.AUTHENTICATION_KEY, str) and config.AUTHENTICATION_KEY:
			self.client.ciphering.authenticationKey = GXByteBuffer.hexToBytes(config.AUTHENTICATION_KEY)
		if isinstance(config.BLOCK_CIPHER_KEY, str) and config.BLOCK_CIPHER_KEY:
			self.client.ciphering.blockCipherKey = GXByteBuffer.hexToBytes(config.BLOCK_CIPHER_KEY)
		self.media = GXSerial(
			'/dev/%s' % config.TTY_INTERFACE,
			baudRate=config.SERIAL_BAUD_RATE,
			dataBits=config.BYTE_SIZE,
			stopBits=config.STOP_BITS,
			parity=config.PARITY,
		)
		self.trace = TraceLevel.INFO


# pylint: disable=no-self-argument
class DLMSListener(IGXMediaListener):
	def __init__(self, service_obj):
		self.settings = GXSettings()
		self.service_obj = service_obj
		self.dbusservice = service_obj._dbusservice

		# There might be several notify messages in GBT.
		self.notify = GXReplyData()
		self.client = self.settings.client
		self.translator = GXDLMSTranslator(type_=TranslatorOutputType.SIMPLE_XML)
		self.translator.comments = False
		if self.settings.client.ciphering.authenticationKey:
			self.translator.authenticationKey = (
				self.settings.client.ciphering.authenticationKey
			)
		if self.settings.client.ciphering.blockCipherKey:
			self.translator.blockCipherKey = (
				self.settings.client.ciphering.blockCipherKey
			)
		self.telegram_processor = TelegramProcessor.use_telegram(config.TELEGRAM_ID or 'si-sodo-reduxi')
		self.reply = GXByteBuffer()
		self.settings.media.trace = self.settings.trace
		print(self.settings.media)

		# Start to listen events from the media.
		self.settings.media.addListener(self)
		# Set EOP for the media.
		if self.settings.client.interfaceType == InterfaceType.HDLC:
			self.settings.media.eop = 0x7e
		try:
			print("Press any key to close the application.")
			# Open the connection.
			self.settings.media.open()
		except Exception as ex:
			print(ex)

	def send_to_dbus(self, data):
		payload = {}
		for key, value in data.items():
			if key in topic_dictionary:
				raw_topic = topic_dictionary[key]
				if raw_topic in transform_multiply:
					value *= transform_multiply[raw_topic]
				payload[raw_topic] = value
		with self.dbusservice as s:
			for path, value in payload.items():
				s[path] = value

	def onStop(self, sender):
		self.service_obj._loop.quit()

	def onError(self, sender, ex):
		"""
		Represents the method that will handle the error event of a Gurux
		component.

		sender :  The source of the event.
		ex : An Exception object that contains the event data.
		"""
		print("Error has occured. " + str(ex))

	@classmethod
	def printData(cls, value, offset):
		sb = ' ' * 2 * offset
		if isinstance(value, list):
			print(sb + "{")
			offset = offset + 1
			# Print received data.
			for it in value:
				cls.printData(it, offset)
			print(sb + "}")
			offset = offset - 1
		elif isinstance(value, bytearray):
			# Print value.
			print(sb + GXCommon.toHex(value))
		else:
			# Print value.
			print(sb + str(value))

	def onReceived(self, sender, e):
		"""Media component sends received data through this method.

		sender : The source of the event.
		e : Event arguments.
		"""
		if sender.trace == TraceLevel.VERBOSE:
			print("New data is received. " + GXCommon.toHex(e.data))
		# Data might come in fragments.
		self.reply.set(e.data)
		data = GXReplyData()
		try:
			if not self.client.getData(self.reply, data, self.notify):
				# If all data is received.
				if self.notify.complete:
					if not self.notify.isMoreData():
						# Show received data as XML.
						try:
							xml = self.translator.dataToXml(self.notify.data)
							if self.trace_level >= TraceLevel.INFO:
								print(xml)
								# Print received data.
								self.printData(self.notify.value, 0)
							try:
								self.onData(xml)
							except Exception as ex:
								print(ex)
								traceback.print_exc()

							# Example is sending list of push messages in first parameter.
							if isinstance(self.notify.value, list):
								objects = self.client.parsePushObjects(self.notify.value[0])
								# Remove first item because it's not needed anymore.
								objects.pop(0)
								Valueindex = 1
								for obj, index in objects:
									self.client.updateValue(obj, index, self.notify.value[Valueindex])
									Valueindex += 1
									# Print value
									print(str(obj.objectType) + " " + obj.logicalName + " " + str(index) + ": " + str(
										obj.getValues()[index - 1]))
							print("Server address:" + str(self.notify.serverAddress) + " Client Address:" + str(
								self.notify.clientAddress))
						except Exception:
							self.reply.position = 0
							xml = self.translator.messageToXml(self.reply)
							if self.trace_level >= TraceLevel.INFO:
								print(xml)
						self.notify.clear()
						self.reply.clear()
		except Exception as ex:
			print(ex)
			self.notify.clear()
			self.reply.clear()

	def onData(self, xml):
		"""
		Process received data.
		"""
		try:
			if self.telegram_processor is None:
				return
			try:
				payload = self.telegram_processor.process_xml(xml)
			except Exception as ex:
				print(ex)
				return
			if self.trace_level >= TraceLevel.INFO:
				pprint(payload)
			if len(self.mqtt.keys()) > 0:
				for _, mqtt_cli in self.mqtt.items():
					mqtt_cli.send_telegram(payload)
		except SystemExit:
			pass

	def onMediaStateChange(self, sender, e):
		"""Media component sends notification, when its state changes.
		sender : The source of the event.
		e : Event arguments.
		"""
		print("Media state changed. " + str(e))

	def onTrace(self, sender, e):
		"""Called when the Media is sending or receiving data.

		sender : The source of the event.
		e : Event arguments.
		"""
		print("trace:" + str(e))

	def onPropertyChanged(self, sender, e):
		"""
		Event is raised when a property is changed on a component.

		sender : The source of the event.
		e : Event arguments.
		"""
		print("Property {!r} has hanged.".format(str(e)))
