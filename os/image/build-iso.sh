#!/bin/sh
# Builds a Kūhaku install image (Debian 13 "trixie" + Kūhaku).
# Run as root:  sudo ./image/build-iso.sh   -> /var/tmp/kuhaku-iso/kuhaku-amd64.hybrid.iso
#
# The host can be anything Debian-like: a small Debian 13 build system is made in
# /var/tmp/kuhaku-iso/builder (outside the encrypted home) and live-build runs in it.
# Takes about an hour on a slow laptop and downloads ~1.5 GB. The image boots as a
# live system (try it from a USB stick without touching the disk) and has an
# installer in its boot menu.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC2012  # the newest package; its name is ours (no spaces)
DEB="$(ls -t "$HERE"/../out/kuhaku_*_all.deb | head -1)"
[ -f "$DEB" ] || { echo "Build the package first: ./build-deb.sh"; exit 1; }
W="${KUHAKU_WORK:-/var/tmp/kuhaku-iso}"
B="$W/builder"
mkdir -p "$W"

# 1. Debian's own debootstrap and keyring, unpacked (nothing is installed on the host).
T="$W/tools"
if [ ! -x "$T/usr/sbin/debootstrap" ]; then
  mkdir -p "$T/debs"
  P=http://deb.debian.org/debian/pool/main
  for d in d/debootstrap/debootstrap_1.0.141_all.deb d/debian-archive-keyring/debian-archive-keyring_2025.1_all.deb; do
    curl -fsSL -o "$T/debs/$(basename "$d")" "$P/$d"
    dpkg-deb -x "$T/debs/$(basename "$d")" "$T"
  done
fi

# 2. The build system.
if [ ! -x "$B/usr/bin/apt-get" ]; then
  DEBOOTSTRAP_DIR="$T/usr/share/debootstrap" "$T/usr/sbin/debootstrap" \
    --keyring="$T/usr/share/keyrings/debian-archive-keyring.gpg" trixie "$B" http://deb.debian.org/debian
fi
# The host's /sys and /dev are borrowed, made one-way (rslave) so that unmounting them here can
# never unmount anything on the host. (A lazy unmount of a shared rbind once tore down the host's
# cgroups and froze the whole computer.)
cleanup() { for m in dev sys proc; do umount -R "$B/$m" 2>/dev/null || umount -R -l "$B/$m" 2>/dev/null || true; done; }
trap cleanup EXIT
mount -t proc proc "$B/proc"
mount --rbind /sys "$B/sys"; mount --make-rslave "$B/sys"
mount --rbind /dev "$B/dev"; mount --make-rslave "$B/dev"
cp /etc/resolv.conf "$B/etc/resolv.conf"
chroot "$B" sh -c "apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q live-build curl ca-certificates"

# 3. The image recipe, run inside the build system.
# Fresh recipe each time, but keep the downloaded packages (saves ~1.5 GB and most of the hour).
[ -d "$B/build/cache" ] && mv "$B/build/cache" "$W/cache.keep"
rm -rf "$B/build"; mkdir -p "$B/build"
[ -d "$W/cache.keep" ] && mv "$W/cache.keep" "$B/build/cache"
cp "$DEB" "$B/build/"
cat > "$B/build/make.sh" <<'IN'
#!/bin/sh
set -e
cd /build
lb config \
  --distribution trixie --archive-areas "main contrib non-free non-free-firmware" \
  --debian-installer none \
  --iso-application "Kuhaku" --iso-volume "Kuhaku" --image-name kuhaku \
  --bootappend-live "boot=live components quiet splash username=admin live-config.username=admin live-config.user-fullname=Kuhaku" \
  --backports true \
  --apt-recommends false --memtest none --firmware-binary true --firmware-chroot true

# What the system is made of: Debian's base, Kūhaku (its package pulls in what it needs), kernel and firmware.
cat > config/package-lists/kuhaku.list.chroot <<'L'
linux-image-amd64
firmware-linux-free
firmware-brcm80211
firmware-misc-nonfree
intel-microcode
xserver-xorg-core
xserver-xorg-input-libinput
xserver-xorg-video-intel
network-manager
pipewire
pipewire-pulse
wireplumber
sudo
user-setup
console-setup
locales
L
# The newer kernel (7.x, needed for this MacBook's speakers) comes from trixie-backports after
# installation (apt install -t trixie-backports linux-image-amd64) once that collection is whole:
# on 4 Oct 2026 it was caught mid-update and no 7.x kernel had all its parts. The source is enabled.
# Live test system only: user "admin", password "admin" (set right after live-config makes the user).
mkdir -p config/includes.chroot/usr/lib/live/config
cat > config/includes.chroot/usr/lib/live/config/0031-kuhaku-admin <<'LC'
#!/bin/sh
echo "admin:admin" | chpasswd 2>/dev/null || true
LC
chmod +x config/includes.chroot/usr/lib/live/config/0031-kuhaku-admin
mkdir -p config/packages.chroot
cp /build/kuhaku_*_all.deb config/packages.chroot/
curl -fsSL -o config/packages.chroot/touchegg_2.0.18_amd64.deb \
  https://github.com/JoseExposito/touchegg/releases/download/2.0.18/touchegg_2.0.18_amd64.deb

# Claude: the desktop app from Anthropic's repository, added once the system has its
# certificates (Claude Code installs at first login).
echo ca-certificates >> config/package-lists/kuhaku.list.chroot
mkdir -p config/hooks/normal
# Anthropic's apt source for the Claude desktop app (the app is downloaded later, not shipped).
cat > config/hooks/normal/9000-claude-desktop.hook.chroot <<'HOOK'
#!/bin/sh
set -e
apt-get install -y ca-certificates curl
curl -fsSL -o /usr/share/keyrings/claude-desktop.asc https://downloads.claude.ai/claude-desktop/key.asc
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/claude-desktop.asc] https://downloads.claude.ai/claude-desktop/apt/stable stable main" \
  > /etc/apt/sources.list.d/claude-desktop.list
# Only the source: the Claude app itself is Anthropic's and is not ours to put in the image.
# kuhaku-claude-desktop.service fetches it from Anthropic on the person's own computer.
HOOK
chmod +x config/hooks/normal/9000-claude-desktop.hook.chroot
lb build
IN
chmod +x "$B/build/make.sh"
chroot "$B" /build/make.sh 2>&1 | tee "$W/build.log"
cp "$B"/build/*.iso "$W/"
ls -la "$W"/*.iso
