#!/bin/sh
# Kūhaku sleep on the MacBook8,1 (from pmgart/macbook8-1-debian-guide, sleep/):
#   light sleep (s2idle) when the lid closes, with keyboard and trackpad brought back on wake;
#   after 2 hours closed it hibernates (memory to the encrypted disk, power fully off), which
#   costs no battery and brings the speakers back too. Plus small power savings.
set -e
K="$(cd "$(dirname "$0")" && pwd)"
ROOTUUID=$(findmnt -no UUID /)
install -m 0755 "$K/system/sleep/mb81-applespi" /usr/lib/systemd/system-sleep/mb81-applespi
mkdir -p /etc/systemd/sleep.conf.d /etc/systemd/logind.conf.d
printf '[Sleep]\nSuspendState=s2idle\nHibernateMode=shutdown\nHibernateDelaySec=2h\n' > /etc/systemd/sleep.conf.d/kuhaku.conf
printf '[Login]\nHandleLidSwitch=suspend-then-hibernate\nHandleLidSwitchExternalPower=suspend-then-hibernate\nHandleLidSwitchDocked=ignore\n' > /etc/systemd/logind.conf.d/kuhaku.conf

# Room on the (encrypted) disk to hibernate into: as large as memory.
if [ ! -f /swapfile ]; then
  mem=$(awk '/MemTotal/ {print int($2/1024/1024)+1}' /proc/meminfo)
  fallocate -l "${mem}G" /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
fi
grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw,pri=-2 0 0' >> /etc/fstab
swapon --priority -2 /swapfile 2>/dev/null || true
OFF=$(filefrag -v /swapfile | awk '$1=="0:" {sub(/\.\./,"",$4); print $4+0; exit}')
echo "resume offset $OFF"
mkdir -p /etc/default/grub.d
echo "GRUB_CMDLINE_LINUX_DEFAULT=\"\$GRUB_CMDLINE_LINUX_DEFAULT mem_sleep_default=s2idle resume=UUID=$ROOTUUID resume_offset=$OFF\"" > /etc/default/grub.d/92-kuhaku-sleep.cfg
printf 'RESUME=UUID=%s\nRESUME_OFFSET=%s\n' "$ROOTUUID" "$OFF" > /etc/initramfs-tools/conf.d/resume

# Small power savings.
printf '# Kūhaku: fewer wakeups on battery.\nkernel.nmi_watchdog = 0\nvm.dirty_writeback_centisecs = 1500\n' > /etc/sysctl.d/80-kuhaku-power.conf
sysctl -q -p /etc/sysctl.d/80-kuhaku-power.conf

update-grub >/dev/null 2>&1
update-initramfs -u >/dev/null 2>&1
echo "done"
