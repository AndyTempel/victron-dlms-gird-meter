import logging
import os.path
import subprocess
import time
import traceback

from gurux_common.enums import MediaState
from gurux_common.enums.TraceLevel import TraceLevel
from gurux_common.GXCommon import GXCommon
from gurux_common.IGXMediaListener import IGXMediaListener
from gurux_common.io import BaudRate, Parity, StopBits
from gurux_dlms.enums import TranslatorOutputType  # noqa: E402
from gurux_dlms.enums.InterfaceType import InterfaceType
from gurux_dlms.GXByteBuffer import GXByteBuffer
from gurux_dlms.GXDLMSTranslator import GXDLMSTranslator
from gurux_dlms.GXReplyData import GXReplyData
from gurux_dlms.secure import GXDLMSSecureClient
from gurux_serial.GXSerial import GXSerial

import config
from telegram_processor import TelegramProcessor

logging.basicConfig(
    level=logging.WARNING,  # Reduce from DEBUG/INFO to WARNING
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def debug_log(message, *args):
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        if args:
            logging.debug(message % args)
        else:
            logging.debug(message)


# Pre-allocate dictionary with size hints
topic_dictionary = dict.fromkeys(
    [
        "ACTIVE_POWER_TOTAL",
        "CURRENT_TOTAL",
        "ACTIVE_ENERGY_IMPORT",
        "ACTIVE_ENERGY_EXPORT",
        "FREQUENCY",
        "SERIAL_NUMBER",
        "ACTIVE_POWER_TOTAL_L1",
        "CURRENT_L1",
        "VOLTAGE_L1",
        "ACTIVE_ENERGY_EXPORT_L1",
        "ACTIVE_ENERGY_IMPORT_L1",
        "ACTIVE_POWER_TOTAL_L2",
        "CURRENT_L2",
        "VOLTAGE_L2",
        "ACTIVE_ENERGY_EXPORT_L2",
        "ACTIVE_ENERGY_IMPORT_L2",
        "ACTIVE_POWER_TOTAL_L3",
        "CURRENT_L3",
        "VOLTAGE_L3",
        "ACTIVE_ENERGY_EXPORT_L3",
        "ACTIVE_ENERGY_IMPORT_L3",
        "POWER_FACTOR_TOTAL",
        "POWER_FACTOR_L1",
        "POWER_FACTOR_L2",
        "POWER_FACTOR_L3",
    ]
)

# Assign values using dict.update for efficiency
topic_dictionary.update(
    {
        # Total
        "ACTIVE_POWER_TOTAL": "/Ac/Power",
        "CURRENT_TOTAL": "/Ac/Current",
        "ACTIVE_ENERGY_IMPORT": "/Ac/Energy/Forward",
        "ACTIVE_ENERGY_EXPORT": "/Ac/Energy/Reverse",
        "FREQUENCY": "/Ac/Frequency",
        "SERIAL_NUMBER": "/Serial",
        "POWER_FACTOR_TOTAL": "/Ac/PowerFactor",
        # Phase 1
        "ACTIVE_POWER_TOTAL_L1": "/Ac/L1/Power",
        "CURRENT_L1": "/Ac/L1/Current",
        "VOLTAGE_L1": "/Ac/L1/Voltage",
        "ACTIVE_ENERGY_EXPORT_L1": "/Ac/L1/Energy/Reverse",
        "ACTIVE_ENERGY_IMPORT_L1": "/Ac/L1/Energy/Forward",
        "POWER_FACTOR_L1": "/Ac/L1/PowerFactor",
        # Phase 2
        "ACTIVE_POWER_TOTAL_L2": "/Ac/L2/Power",
        "CURRENT_L2": "/Ac/L2/Current",
        "VOLTAGE_L2": "/Ac/L2/Voltage",
        "ACTIVE_ENERGY_EXPORT_L2": "/Ac/L2/Energy/Reverse",
        "ACTIVE_ENERGY_IMPORT_L2": "/Ac/L2/Energy/Forward",
        "POWER_FACTOR_L2": "/Ac/L2/PowerFactor",
        # Phase 3
        "ACTIVE_POWER_TOTAL_L3": "/Ac/L3/Power",
        "CURRENT_L3": "/Ac/L3/Current",
        "VOLTAGE_L3": "/Ac/L3/Voltage",
        "ACTIVE_ENERGY_EXPORT_L3": "/Ac/L3/Energy/Reverse",
        "ACTIVE_ENERGY_IMPORT_L3": "/Ac/L3/Energy/Forward",
        "POWER_FACTOR_L3": "/Ac/L3/PowerFactor",
    }
)

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
            self.client.ciphering.authenticationKey = GXByteBuffer.hexToBytes(
                config.AUTHENTICATION_KEY
            )
        if isinstance(config.BLOCK_CIPHER_KEY, str) and config.BLOCK_CIPHER_KEY:
            self.client.ciphering.blockCipherKey = GXByteBuffer.hexToBytes(
                config.BLOCK_CIPHER_KEY
            )
        self.media = GXSerial(
            "/dev/%s" % config.TTY_INTERFACE,
            baudRate=config.SERIAL_BAUD_RATE,
            dataBits=config.BYTE_SIZE,
            stopBits=config.STOP_BITS,
            parity=config.PARITY,
        )
        self.trace = TraceLevel.INFO


class GXSerialCustom(GXSerial):
    """
    Manage BlockingIOError exceptions in a custom way.
    """

    def __init__(
        self,
        port,
        baudRate=BaudRate.BAUD_RATE_9600,
        dataBits=8,
        parity=Parity.NONE,
        stopBits=StopBits.ONE,
    ):
        super().__init__(port, baudRate, dataBits, stopBits, parity)
        self.__blocked_count = 0

    def __readThread(self):
        # pylint: disable=broad-except, bare-except
        while not self.__closing.isSet():
            try:
                data = self.__h.read()
                if data is not None:
                    self.__handleReceivedData(data, self.__portName)
                    # Give some time before read next bytes.  In this way we are
                    # not reading data one byte at the time.
                    time.sleep(0.01)
            except BlockingIOError:
                # Execute command that disabled serial-starter for our port.
                self.__blocked_count += 1
                try:
                    subprocess.run(
                        [
                            "/bin/bash",
                            "/opt/victronenergy/serial-starter/stop-tty.sh",
                            config.TTY_INTERFACE,
                        ],
                        check=True,
                    )
                except subprocess.CalledProcessError as e:
                    logging.error(
                        "Failed to stop serial-starter for %s: %s",
                        config.TTY_INTERFACE,
                        e,
                    )
                if self.__blocked_count > 10:
                    script = os.path.join(os.getcwd(), "bin", "restart.sh")
                    if os.path.exists(script):
                        # Fire the script (don't wait for it to finish) to restart this driver.
                        subprocess.Popen(
                            ["/bin/bash", str(script)],
                            start_new_session=True,
                            cwd=os.path.dirname(os.getcwd()),
                        )
            except Exception as ex:
                # If serial port is removed.
                if not self.isOpen():
                    self.__closing.set()
                    self.__notifyMediaStateChange(MediaState.CLOSED)
                    self.__bytesSent = 0
                    self.__syncBase.exception = ex
                    self.__syncBase.resetReceivedSize()
                    self.__syncBase.setReceived()
                if not self.__closing.isSet():
                    traceback.print_exc()


# pylint: disable=no-self-argument
class DLMSListener(IGXMediaListener):
    def __init__(self, service_obj):
        self.settings = GXSettings()
        self.service_obj = service_obj
        self.dbusservice = service_obj._dbusservice
        self.trace_level = self.settings.trace

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
        self.telegram_processor = TelegramProcessor.use_telegram(
            config.TELEGRAM_ID or "si-sodo-reduxi"
        )
        self.reply = GXByteBuffer()
        self.settings.media.trace = self.settings.trace
        logging.info(str(self.settings.media))

        # Start to listen events from the media.
        self.settings.media.addListener(self)
        # Set EOP for the media.
        if self.settings.client.interfaceType == InterfaceType.HDLC:
            self.settings.media.eop = 0x7E
        try:
            # Open the connection.
            self.settings.media.open()
            logging.info("Media opened successfully.")
        except Exception as ex:
            logging.exception(ex)

    def send_to_dbus(self, data):
        # Pre-allocate a single dictionary with expected capacity
        updates = {}
        for key, value in data["data"].items():
            if key in topic_dictionary:
                raw_topic = topic_dictionary[key]
                if raw_topic in transform_multiply:
                    value *= transform_multiply[raw_topic]
                updates[raw_topic] = value

        # Single D-Bus transaction with all updates
        if updates:
            with self.dbusservice as s:
                for path, value in updates.items():
                    s[path] = value

    def onStop(self, sender):
        logging.info("Stopping DLMS listener.")
        self.service_obj._loop.quit()

    def onError(self, sender, ex):
        """
        Represents the method that will handle the error event of a Gurux
        component.

        sender :  The source of the event.
        ex : An Exception object that contains the event data.
        """
        logging.error("Error has occured. " + str(ex))

    @classmethod
    def printData(cls, value, offset):
        sb = " " * 2 * offset
        if isinstance(value, list):
            logging.debug(sb + "{")
            offset = offset + 1
            # Print received data.
            for it in value:
                cls.printData(it, offset)
            logging.debug(sb + "}")
            offset = offset - 1
        elif isinstance(value, bytearray):
            # Print value.
            logging.debug(sb + GXCommon.toHex(value))
        else:
            # Print value.
            logging.debug(sb + str(value))

    def onReceived(self, sender, e):
        # Add received data to buffer
        self.reply.set(e.data)
        data = GXReplyData()

        try:
            if not self.client.getData(self.reply, data, self.notify):
                # Only process if complete and no more data expected
                if self.notify.complete and not self.notify.isMoreData():
                    # Skip verbose logging in production
                    if (
                        self.trace_level >= TraceLevel.INFO
                        and logging.getLogger().isEnabledFor(logging.DEBUG)
                    ):
                        xml = self.translator.dataToXml(self.notify.data)
                        logging.debug(xml)
                    else:
                        xml = self.translator.dataToXml(self.notify.data)

                    try:
                        self.onData(xml)
                    except Exception as ex:
                        logging.warning("Error processing data: %s", ex)

                    # Clear buffers for reuse
                    self.notify.clear()
                    self.reply.clear()
        except Exception as ex:
            logging.error("Error in data reception: %s", ex)
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
                logging.info(ex)
                return
            if self.trace_level > TraceLevel.INFO:
                logging.info(payload)
            try:
                self.send_to_dbus(payload)
            except Exception as ex:
                logging.error("Error sending data to D-Bus: " + str(ex))
                logging.error(traceback.format_exc())
        except SystemExit:
            pass

    def onMediaStateChange(self, sender, e):
        """Media component sends notification, when its state changes.
        sender : The source of the event.
        e : Event arguments.
        """
        logging.info("Media state changed. " + str(e))

    def onTrace(self, sender, e):
        """Called when the Media is sending or receiving data.

        sender : The source of the event.
        e : Event arguments.
        """
        logging.info("trace:" + str(e))

    def onPropertyChanged(self, sender, e):
        """
        Event is raised when a property is changed on a component.

        sender : The source of the event.
        e : Event arguments.
        """
        logging.info("Property {!r} has hanged.".format(str(e)))
