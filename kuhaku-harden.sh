#!/bin/sh
# Kūhaku: speakers survive kernel updates; compressed swap in memory (zram).
set -e
S="$(cd "$(dirname "$0")" && pwd)/system/sound"
install -d -m 0755 /usr/local/share/kuhaku-speakers
install -m 0644 "$S/guide/audio/driver/mb81-speakers.patch" "$S/guide/audio/driver/Makefile" /usr/local/share/kuhaku-speakers/
install -m 0755 "$S/kuhaku-speakers-rebuild" /etc/kernel/postinst.d/zz-kuhaku-speakers
DEBIAN_FRONTEND=noninteractive apt-get install -y -q zram-tools
printf '# Kūhaku: compressed swap in memory, so a full memory slows down instead of closing apps.\nALGO=zstd\nPERCENT=50\nPRIORITY=100\n' > /etc/default/zramswap
systemctl restart zramswap
echo "done"
