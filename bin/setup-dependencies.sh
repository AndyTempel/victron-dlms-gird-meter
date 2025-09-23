#!/bin/sh
BASE="$(dirname "$(dirname "$(realpath "$0")")")"
PREFIX="dbus-dlms-meter:"
SERIAL_ID_MODEL="KSOFTESS"
REBOOT_FLAG="/data/dlms-reboot.flag"
reboot_needed="no"

echo "$PREFIX Setup-dependencies started"

check_online() {
    url="https://vrm.victronenergy.com"
    attempts=0
    while [ "$attempts" -lt 60 ]; do
        if wget --spider --quiet --tries=1 --timeout=5 "$url"; then
            break
        else
            attempts=$((attempts + 1))
            echo "$(date) $PREFIX GX Device does not appear to be online, retrying in 10 seconds..."
            sleep 10
        fi
    done
    if [ "$attempts" -eq 60 ]; then
        echo "$(date) $PREFIX GX Device does not appear to be online, exiting..."
        exit 1
    fi
}

python_version_ge() {
    req="$1"
    major_req=$(printf '%s' "$req" | cut -d. -f1)
    minor_req=$(printf '%s' "$req" | cut -d. -f2)

    python_version=$(python --version 2>&1 | cut -d' ' -f2)
    python_major=$(printf '%s' "$python_version" | cut -d. -f1)
    python_minor=$(printf '%s' "$python_version" | cut -d. -f2)

    if [ "$python_major" -gt "$major_req" ] || { [ "$python_major" -eq "$major_req" ] && [ "$python_minor" -ge "$minor_req" ]; }; then
        return 0
    else
        return 1
    fi
}

ensure_opkg_installed() {
    pkg_name="$1"
    if [ "$opkg_updated" != "yes" ]; then
        check_online
        venus_version=$(head -n 1 "/opt/victronenergy/version")
        case "$venus_version" in
            *~*)
                echo "$PREFIX '$venus_version' of VenusOS is a beta version so using candidate opkg feed"
                /opt/victronenergy/swupdate-scripts/set-feed.sh candidate
                ;;
        esac
        echo "$PREFIX Updating opkg package list"
        opkg update
        opkg_updated="yes"
    fi

    echo "$PREFIX Checking to see if library $pkg_name is installed"
    if opkg list-installed | grep -q "^$pkg_name"; then
        echo "$PREFIX Library $pkg_name is already installed"
    else
        if ! opkg install "$pkg_name"; then
            echo "$PREFIX Failed to install $pkg_name"
            exit 1
        fi
        echo "$PREFIX Library $pkg_name installed successfully"
    fi
}

readonly=$(awk '$2 == "/" { print $4 }' /proc/mounts | grep -q 'ro' && echo "yes" || echo "no")
if [ "$readonly" = "yes" ]; then
    echo "$PREFIX Temporarily enable writing to root partition"
    mount -o remount,rw /
    remount="yes"
fi

if python_version_ge "3.11"; then
    echo "$PREFIX Python version is 3.11 or greater, need to ensure tomllib is installed"
    ensure_opkg_installed python3-tomllib
else
    echo "$PREFIX Python version is less than 3.11, not installing tomllib"
fi
readonly=$(awk '$2 == "/" { print $4 }' /proc/mounts | grep -q 'ro' && echo "yes" || echo "no")

echo "$PREFIX Checking to see if Python's Pip is installed"
if ! python -m pip --version; then
    ensure_opkg_installed python3-pip
fi

# ensure_opkg_installed python3-misc

echo "$PREFIX Pip install module dependencies"
check_online
python -m pip install dataclasses

# Optimized path for libxslt-dev and lxml to avoid slow opkg/lxml steps on repeat runs.
if opkg list-installed | grep -q "^libxslt-dev"; then
    echo "$PREFIX libxslt-dev already installed"
    if python -c "import lxml" >/dev/null 2>&1; then
        echo "$PREFIX lxml already installed; skipping lxml upgrade"
    else
        echo "$PREFIX lxml not installed; installing lxml wheel"
        python -m pip install --upgrade lxml --index-url https://piwheels.org/simple
    fi
else
    ensure_opkg_installed libxslt-dev
    python -m pip install --upgrade lxml --index-url https://piwheels.org/simple
fi

python -m pip install -r "$BASE"/requirements.txt

CUSTOM_UDEV_RULE_FILE="/etc/udev/rules.d/10-serial-starter-ignore.rules"
CUSTOM_UDEV_RULE='ACTION=="add", ENV{ID_BUS}=="usb", ENV{ID_MODEL}=="'"$SERIAL_ID_MODEL"'", ENV{VE_SERVICE}="ignore"'

echo "$PREFIX Checking for RS485 serial-starter disable rule"

if [ "$readonly" = "no" ]; then
    if [ ! -f "$CUSTOM_UDEV_RULE_FILE" ] || ! grep -qF "$CUSTOM_UDEV_RULE" "$CUSTOM_UDEV_RULE_FILE"; then
        echo "$PREFIX Writing udev override rule to $CUSTOM_UDEV_RULE_FILE"
        printf '%s\n' "$CUSTOM_UDEV_RULE" > "$CUSTOM_UDEV_RULE_FILE"
        udevadm control --reload-rules
    else
        echo "$PREFIX Udev override rule already present in $CUSTOM_UDEV_RULE_FILE"
    fi
fi

if [ "$readonly" = "no" ]; then
    # Detect correct /dev/ttyUSBx
    TTY_DEVICE=
    for dev in /dev/ttyUSB*; do
        if [ -e "$dev" ] && udevadm info --query=property --name="$dev" | grep -q "ID_MODEL=$SERIAL_ID_MODEL"; then
            TTY_DEVICE="$dev"
            break
        fi
    done

    if [ -n "$TTY_DEVICE" ]; then
        echo "$PREFIX Found matching RS485 device: $TTY_DEVICE"

        # Stop any serial-starter instance (clean exit)
        /opt/victronenergy/serial-starter/stop-tty.sh "$TTY_DEVICE"

        TTY_BASENAME=$(basename "$TTY_DEVICE")
        SYS_DEV="/sys/class/tty/$TTY_BASENAME/device"
        SERIAL_STARTER_STATUS_FILE="/data/var/lib/serial-starter/$TTY_BASENAME"

        # Function to check if serial-starter is ignoring the device
        is_serial_ignored() {
            [ -f "$SERIAL_STARTER_STATUS_FILE" ] && grep -qx "ignore" "$SERIAL_STARTER_STATUS_FILE"
        }

        # Step 1: Try /remove
        if [ -e "$SYS_DEV/remove" ]; then
            echo "$PREFIX Forcing USB rebind via /remove"
            printf '1\n' > "$SYS_DEV/remove"
            sleep 1
            udevadm trigger --action=add "$SYS_DEV"

        elif DEVICE_PATH=$(readlink -f "$SYS_DEV" | grep -oE "usb[0-9]+(/[0-9.-]+)+") && [ -e "/sys/bus/usb/drivers/usb/unbind" ]; then
            echo "$PREFIX Attempting USB driver rebind for $DEVICE_PATH"
            printf '%s\n' "$DEVICE_PATH" > /sys/bus/usb/drivers/usb/unbind
            sleep 1
            printf '%s\n' "$DEVICE_PATH" > /sys/bus/usb/drivers/usb/bind

        else
            echo "$PREFIX WARNING: Could not find /remove or resolve USB path for rebind"
        fi

        # Step 2: Check if device is now ignored
        if is_serial_ignored; then
            echo "$PREFIX SUCCESS: serial-starter is now ignoring $TTY_DEVICE"
            [ -e "$REBOOT_FLAG" ] && rm -f "$REBOOT_FLAG"
        else
            if [ ! -e "$REBOOT_FLAG" ]; then
                echo "$PREFIX REBOOT REQUIRED: serial-starter is still managing $TTY_DEVICE, rebooting once"
                reboot_needed="yes"
                : > "$REBOOT_FLAG"
            else
                echo "$PREFIX ERROR: serial-starter is still managing $TTY_DEVICE after reboot and rebind attempts"
                echo "$PREFIX Please unplug/replug the device manually"
            fi
        fi

    else
        echo "$PREFIX No ttyUSB device found for ID_MODEL=$SERIAL_ID_MODEL"
    fi
else
    echo "$PREFIX Drive is read-only, skipping udev rule and serial-starter handling"
fi

if [ "$remount" = "yes" ]; then
    echo "$PREFIX Setting root partition back to readonly"
    mount -o remount,ro /
fi

if [ "$reboot_needed" = "yes" ]; then
    reboot
fi

echo "$PREFIX Setup-dependencies complete"
