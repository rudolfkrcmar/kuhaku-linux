#!/bin/sh
# Kūhaku system pieces that need root: boot screen, login look, boot menu name.
set -e
S="$(cd "$(dirname "$0")" && pwd)"
# Boot screen
mkdir -p /usr/share/plymouth/themes/kuhaku
cp $S/boot/* /usr/share/plymouth/themes/kuhaku/
update-alternatives --install /usr/share/plymouth/themes/default.plymouth default.plymouth /usr/share/plymouth/themes/kuhaku/kuhaku.plymouth 200
update-alternatives --set default.plymouth /usr/share/plymouth/themes/kuhaku/kuhaku.plymouth
# Login screen
mkdir -p /usr/local/share/kuhaku-login /var/lib/lightdm/.config/gtk-3.0
cp $S/login/kuhaku-background.png /usr/local/share/kuhaku-login/
cp $S/login/lightdm-gtk-greeter.conf /etc/lightdm/lightdm-gtk-greeter.conf
cp $S/login/user-gtk.css /var/lib/lightdm/.config/gtk-3.0/gtk.css
cp $S/login/Kuhaku/gtk-3.0/gtk.css /usr/share/themes/Kuhaku/gtk-3.0/gtk.css
chown -R lightdm:lightdm /var/lib/lightdm/.config
# Boot menu entries say Kūhaku (menu is hidden; this names it if shown)
mkdir -p /etc/default/grub.d
echo 'GRUB_DISTRIBUTOR="Kuhaku"' > /etc/default/grub.d/90-kuhaku.cfg
update-grub >/dev/null 2>&1 || true
update-initramfs -u >/dev/null
