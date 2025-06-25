#!/bin/sh
#BASE=/data/drivers/dbus-dlms-meter
BASE=$(dirname $(dirname $(realpath "$0")))

echo "Uninstall dbus-dlms-meter from $BASE"

rm -f /service/dbus-dlms-meter
rm -r $BASE
echo "Uninstall dbus-dlms-meter complete"
