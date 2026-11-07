#!/bin/sh
# Lets the signed-in person read the laptop/tablet switch, so Kūhaku's keyboard
# comes up when the screen is folded back. Run once with sudo (or pkexec).
set -e
DIR="$(dirname "$(readlink -f "$0")")"
install -m 644 "$DIR/system/root/etc/udev/rules.d/70-kuhaku-tablet.rules" /etc/udev/rules.d/70-kuhaku-tablet.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=input --action=change
echo "Tablet switch readable."
