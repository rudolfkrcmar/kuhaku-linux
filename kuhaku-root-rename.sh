#!/bin/sh
# One-off: moves the system-wide pieces from the old name to Kūhaku. Run as root.
set -e
K="$(cd "$(dirname "$0")" && pwd)"
# Login screen
rm -rf /usr/local/share/pure-login /usr/share/themes/Pure /usr/local/share/fonts/PureLogin
mkdir -p /usr/local/share/kuhaku-login /usr/share/themes/Kuhaku /usr/local/share/fonts/KuhakuLogin
cp "$K/login/kuhaku-background.png" /usr/local/share/kuhaku-login/
cp -r "$K/login/Kuhaku/gtk-3.0" /usr/share/themes/Kuhaku/
cp "$K/login/KuhakuName.ttf" "$K/login/KuhakuText.ttf" /usr/local/share/fonts/KuhakuLogin/
fc-cache -f /usr/local/share/fonts >/dev/null
cp "$K/login/lightdm-gtk-greeter.conf" /etc/lightdm/lightdm-gtk-greeter.conf
rm -f /etc/lightdm/lightdm.conf.d/90-pure-greeter.conf
cp "$K/login/90-kuhaku-greeter.conf" /etc/lightdm/lightdm.conf.d/
mkdir -p /var/lib/lightdm/.config/gtk-3.0 /var/lib/lightdm/.config/fontconfig
cp "$K/login/user-gtk.css" /var/lib/lightdm/.config/gtk-3.0/gtk.css
cp "$K/login/fonts.conf" /var/lib/lightdm/.config/fontconfig/fonts.conf
chown -R lightdm:lightdm /var/lib/lightdm/.config
# Daily Claude app update
systemctl disable --now pure-claude-update.timer 2>/dev/null || true
rm -f /etc/systemd/system/pure-claude-update.service /etc/systemd/system/pure-claude-update.timer /etc/systemd/system/timers.target.wants/pure-claude-update.timer
sh "$K/kuhaku-install-claude"
# Keyboard fix
if [ -f /etc/modprobe.d/pure-applespi.conf ]; then
  sed 's/^# Pure:/# Kūhaku:/' /etc/modprobe.d/pure-applespi.conf > /etc/modprobe.d/kuhaku-applespi.conf
  rm -f /etc/modprobe.d/pure-applespi.conf
fi
# Boot screen files
cp "$K/boot"/* /usr/share/plymouth/themes/kuhaku/
