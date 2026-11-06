#!/bin/sh
# Kūhaku login: light single-weight fonts, fontconfig rule and CSS for the lightdm user. Run as root.
set -e
S="$(cd "$(dirname "$0")" && pwd)"
mkdir -p /usr/local/share/fonts/KuhakuLogin /var/lib/lightdm/.config/gtk-3.0 /var/lib/lightdm/.config/fontconfig
cp $S/KuhakuName.ttf $S/KuhakuText.ttf /usr/local/share/fonts/KuhakuLogin/
fc-cache -f /usr/local/share/fonts >/dev/null
cp $S/user-gtk.css /var/lib/lightdm/.config/gtk-3.0/gtk.css
cp $S/fonts.conf /var/lib/lightdm/.config/fontconfig/fonts.conf
chown -R lightdm:lightdm /var/lib/lightdm/.config
cp $S/lightdm-gtk-greeter.conf /etc/lightdm/lightdm-gtk-greeter.conf
cp $S/90-kuhaku-greeter.conf /etc/lightdm/lightdm.conf.d/90-kuhaku-greeter.conf
