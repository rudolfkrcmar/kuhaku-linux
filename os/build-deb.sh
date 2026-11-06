#!/bin/sh
# Builds kuhaku_<version>_all.deb from the Kūhaku in this repository (the folder above os/).
#   ./build-deb.sh            -> out/kuhaku_<version>_all.deb
set -e
cd "$(dirname "$0")"
SRC="${KUHAKU_SRC:-$(cd .. && pwd)}"
# The release number in VERSION; a dev build adds the date of its last change, so it sorts after
# the release it grew from and before the next one (0.2.0 < 0.2.0+dev202611061200 < 0.2.1).
VERSION="$(cat "$SRC/VERSION")"
[ -n "${KUHAKU_RELEASE:-}" ] || VERSION="$VERSION+dev$(git -C "$SRC" log -1 --format=%cd --date=format:%Y%m%d%H%M 2>/dev/null || date +%Y%m%d%H%M)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -a package/. "$STAGE/"
rm "$STAGE/DEBIAN/control.in"
sed "s/@VERSION@/$VERSION/" package/DEBIAN/control.in > "$STAGE/DEBIAN/control"
# The app, without what belongs to one person (browser data, dictation venv, old archives).
mkdir -p "$STAGE/usr/share/kuhaku/app"
rsync -a --exclude .git --exclude /os --exclude venv --exclude browser-data --exclude archive --exclude backup \
  --exclude __pycache__ --exclude 'system/home' --exclude 'system/packages' --exclude mado-chromium --exclude VISION-jobs.md --exclude mac "$SRC/" "$STAGE/usr/share/kuhaku/app/"
# Home pieces every user gets: launchers, app entries, icons, theme, file defaults.
mkdir -p "$STAGE/usr/share/kuhaku/home"
cp -a "$SRC/system/home/." "$STAGE/usr/share/kuhaku/home/"
rm -rf "$STAGE/usr/share/kuhaku/home/.claude" "$STAGE/usr/share/kuhaku/home/src" "$STAGE/usr/share/kuhaku/home/.config/autostart" \
       "$STAGE/usr/share/kuhaku/home/.config/kuhaku/mail.json" "$STAGE/usr/share/kuhaku/home/.config/kuhaku/mado-certificates.json" \
       "$STAGE/usr/share/kuhaku/home/.config/systemd" "$STAGE/usr/share/kuhaku/home/.config/pipewire" "$STAGE/usr/share/kuhaku/home/.config/wireplumber"
# The hidden-app entries stay: they keep XFCE's own tools out of the Apps list.
# Entries that belong to this machine's own apps (the Claude app installs its own).
rm -f "$STAGE/usr/share/kuhaku/home/.local/share/applications/com.anthropic.Claude.desktop" \
      "$STAGE/usr/share/kuhaku/home/.local/share/applications/claude-code-url-handler.desktop" \
      "$STAGE/usr/share/kuhaku/home/.local/share/applications/mimeinfo.cache"
# Everything readable by every user (a file only its owner could read once broke the first start).
chmod -R u+rwX,go+rX,go-w "$STAGE/usr" "$STAGE/etc"
# Nothing from Mint: the login look must not lean on Mint's theme or icons.
sed -i '/Mint-Y/d' "$STAGE/usr/share/kuhaku/app/login/Kuhaku/gtk-3.0/gtk.css"
sed -i 's/^icon-theme-name=.*/icon-theme-name=Adwaita/' "$STAGE/usr/share/kuhaku/app/login/lightdm-gtk-greeter.conf" "$STAGE/usr/share/kuhaku/app/system/root/etc/lightdm/lightdm-gtk-greeter.conf"
if grep -rIl "Mint-Y\|linuxmint" "$STAGE/usr" --exclude-dir=lib --exclude-dir=vendor | grep -v "browser.py\|browser_webkit.py\|kuhaku-revert"; then
  echo "Mint leftovers above"; exit 1
fi
mkdir -p out
dpkg-deb --root-owner-group -Zxz --build "$STAGE" "out/kuhaku_${VERSION}_all.deb" >/dev/null
echo "out/kuhaku_${VERSION}_all.deb"
