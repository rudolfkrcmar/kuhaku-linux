#!/bin/bash
# Kūhaku on a fresh system: speakers and the login screen. Run as root (pkexec).
#   Speakers: the MacBook8,1 TDM speaker driver (DKMS, rebuilt on kernel updates)
#   plus its PipeWire EQ chain. Driver: github.com/omnidecker/macbook8.1-speaker-driver
#   Login: one red line, no name (a quiet name list only with several people).
set -eu
U="${SUDO_USER:-$(id -nu "${PKEXEC_UID:-1000}")}"
K=/home/$U/.local/share/kuhaku
log=/var/log/kuhaku-system-fix.log
exec > >(tee "$log") 2>&1

echo "=== login screen ==="
mkdir -p /usr/local/share/fonts/KuhakuLogin /var/lib/lightdm/.config/gtk-3.0 /var/lib/lightdm/.config/fontconfig /usr/share/themes/Kuhaku/gtk-3.0
cp $K/login/KuhakuName.ttf $K/login/KuhakuText.ttf /usr/local/share/fonts/KuhakuLogin/
fc-cache -f /usr/local/share/fonts >/dev/null
cp $K/login/Kuhaku/gtk-3.0/gtk.css /usr/share/themes/Kuhaku/gtk-3.0/gtk.css
cp $K/login/user-gtk.css /var/lib/lightdm/.config/gtk-3.0/gtk.css
people=$(awk -F: '$3>=1000 && $3<60000 && $7 !~ /(nologin|false)$/' /etc/passwd | wc -l)
[ "$people" -gt 1 ] && cat $K/login/user-multi.css >> /var/lib/lightdm/.config/gtk-3.0/gtk.css
cp $K/login/fonts.conf /var/lib/lightdm/.config/fontconfig/fonts.conf
chown -R lightdm:lightdm /var/lib/lightdm/.config
cp $K/login/lightdm-gtk-greeter.conf /etc/lightdm/lightdm-gtk-greeter.conf

echo "=== speakers: build tools ==="
export DEBIAN_FRONTEND=noninteractive
# The installer's own stick stays listed as a package source; it is gone now.
sed -i 's|^deb \[trusted=yes\] file:/run/live/medium|# &|' /etc/apt/sources.list
apt-get update -q || true
apt-get install -y -q build-essential "linux-headers-$(uname -r)" linux-headers-amd64 alsa-utils

echo "=== speakers: remove the first attempt (a driver for newer kernels) ==="
/usr/sbin/dkms remove macbook12-audio/0.1 --all 2>/dev/null || true
rm -rf /usr/src/macbook12-audio-0.1
systemctl disable --now mb81-resume-recover.service 2>/dev/null || true
rm -f /etc/systemd/system/mb81-resume-recover.service /usr/local/bin/mb81-resume-recover /usr/local/bin/efi_recover.py \
      /etc/systemd/user/mb81-jack-switch.service /usr/local/bin/mb81-jack-switch /etc/modprobe.d/mb81-singlecmd.conf
systemctl daemon-reload

echo "=== speakers: MacBook8,1 driver for Debian 6.12 (pmgart/macbook8-1-debian-guide) ==="
# Built as the user (kuhaku/system/sound/guide/audio/build/<kernel>); this only installs it.
bash $K/system/sound/guide/audio/scripts/install-driver.sh
update-initramfs -u
echo "=== done: restart to hear the speakers ==="
