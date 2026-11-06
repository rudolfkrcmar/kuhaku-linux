#!/bin/sh
# Kūhaku unlock screen: only the red line (the disk password is typed beside it).
# One person: the computer then logs in by itself. Several people: the login screen
# shows the name above the line, and a click on it chooses someone else.
set -e
K="$(cd "$(dirname "$0")" && pwd)"
mkdir -p /usr/share/plymouth/themes/kuhaku
cp $K/boot/kuhaku.plymouth $K/boot/kuhaku.script $K/boot/line.png /usr/share/plymouth/themes/kuhaku/
rm -f /usr/share/plymouth/themes/kuhaku/logo.png
install -m 0755 $K/boot/kuhaku-font.hook /etc/initramfs-tools/hooks/kuhaku-font
people=$(awk -F: '$3>=1000 && $3<60000 && $7 !~ /(nologin|false)$/' /etc/passwd | wc -l)
if [ "$people" -gt 1 ]; then
  mv -f /etc/lightdm/lightdm.conf.d/95-kuhaku-autologin.conf /etc/lightdm/95-kuhaku-autologin.conf.off 2>/dev/null || true
  cat $K/login/user-gtk.css $K/login/user-multi.css > /var/lib/lightdm/.config/gtk-3.0/gtk.css
else
  cp $K/login/user-gtk.css /var/lib/lightdm/.config/gtk-3.0/gtk.css
fi
chown -R lightdm:lightdm /var/lib/lightdm/.config
update-initramfs -u
echo done
