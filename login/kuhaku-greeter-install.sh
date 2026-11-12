#!/bin/sh
# Install Kūhaku's greeter next to the standard one (not yet switched on).
set -e
K="$(cd "$(dirname "$0")" && pwd)"
DEBIAN_FRONTEND=noninteractive apt-get install -y -q gir1.2-lightdm-1 >/dev/null
install -d /usr/local/share/kuhaku-greeter
install -m 0644 "$K/kuhaku-greeter.py" /usr/local/share/kuhaku-greeter/kuhaku-greeter.py
cat > /usr/local/sbin/kuhaku-greeter <<'W'
#!/bin/sh
# Kūhaku's greeter; if it cannot start, the standard one, so the computer always opens.
start=$(date +%s)
python3 /usr/local/share/kuhaku-greeter/kuhaku-greeter.py && exit 0
[ $(( $(date +%s) - start )) -lt 15 ] && exec /usr/sbin/lightdm-gtk-greeter
exit 1
W
chmod 0755 /usr/local/sbin/kuhaku-greeter
printf '[Desktop Entry]\nName=Kūhaku\nComment=Kūhaku login\nExec=/usr/local/sbin/kuhaku-greeter\nType=Application\n' > /usr/share/xgreeters/kuhaku-greeter.desktop
echo installed
