#!/bin/sh
#BASE=/data/drivers/dbus-dlms-meter
BASE=$(dirname $(dirname $(realpath "$0")))

echo "dbus-dlms-meter: Setup in $BASE started"
cd $BASE

./bin/setup-dependencies.sh

echo "dbus-dlms-meter: Set up Victron module libraries"
rm -fr $BASE/ext/dbus-mqtt $BASE/ext/velib_python
# ln -s /opt/victronenergy/dbus-mqtt $BASE/ext
mkdir -p $BASE/ext
ln -s /opt/victronenergy/dbus-digitalinputs/ext/velib_python $BASE/ext/velib_python

echo "dbus-dlms-meter: Set up device service to autorun on restart"
chmod +x $BASE/dbus_dlms_meter.py
# Use awk to inject correct BASE path into the run script
awk -v base=$BASE '{gsub(/\$\{BASE\}/,base);}1' $BASE/bin/service/run.tmpl >$BASE/bin/service/run
chmod -R a+rwx $BASE/bin/service
rm -f /service/dbus-dlms-meter
ln -s $BASE/bin/service /service/dbus-dlms-meter

echo "dbus-dlms-meter: Adding device service to /data/rc.local"

CMD="$BASE/bin/setup-dependencies.sh 2>&1"
if ! grep -s -q "$CMD" /data/rc.local; then
    echo "$CMD" >> /data/rc.local
fi

CMD="ln -s $BASE/bin/service /service/dbus-dlms-meter"
if ! grep -s -q "$CMD" /data/rc.local; then
    echo "$CMD" >> /data/rc.local
fi

# comment out lines that match different versions of dbus-dlms-meter 
# by a) ignoring lines that start with comments, b) only selecting lines that contain dbus-mqtt0-devices, c) ignore any that match the current BASE path
awk -v BASE="$BASE/" '/^[^#]/ && /dbus-dlms-meter/ && $0 !~ BASE f{$0 = "# " $0}{print}' /data/rc.local > /data/rc.local.tmp
mv /data/rc.local.tmp /data/rc.local
chmod +x /data/rc.local

echo "dbus-dlms-meter: Setup complete"
