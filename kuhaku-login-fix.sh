#!/bin/sh
# Kūhaku login: show the people on this computer (Debian hides them by default, which
# leaves a bare "username" box). One person: their name stays hidden, only the line for
# the password. Several: their names, chosen with a click.
set -e
K="$(cd "$(dirname "$0")" && pwd)"
cat > /etc/lightdm/lightdm.conf.d/91-kuhaku-users.conf <<'CONF'
[Seat:*]
greeter-session=kuhaku-greeter
greeter-hide-users=false
greeter-show-manual-login=false
greeter-allow-guest=false
CONF
cp "$K/login/lightdm-gtk-greeter.conf" /etc/lightdm/lightdm-gtk-greeter.conf
people=$(awk -F: '$3>=1000 && $3<60000 && $7 !~ /(nologin|false)$/' /etc/passwd | wc -l)
mkdir -p /var/lib/lightdm/.config/gtk-3.0
if [ "$people" -gt 1 ]; then cat "$K/login/user-gtk.css" "$K/login/user-multi.css"; else cat "$K/login/user-gtk.css"; fi > /var/lib/lightdm/.config/gtk-3.0/gtk.css
chown -R lightdm:lightdm /var/lib/lightdm/.config
sh "$K/login/kuhaku-greeter-install.sh"
echo "done"
