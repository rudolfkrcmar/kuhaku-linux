#!/bin/bash
# Publishes a Kūhaku release, from this repository:
#
#   os/release.sh             check, build, tag, GitHub release, signed package source
#   os/release.sh --dry-run   check and build only; nothing leaves this computer
#   KUHAKU_ISO=path/to.iso os/release.sh   also attach an install image
#
# Before: raise VERSION, and move CHANGELOG.md's "Unreleased" notes under "## <version> — <date>".
# The release is the commit at HEAD, tagged v<version>. Its package goes to the GitHub release and
# into Kūhaku's signed apt repository (os/apt-repo.sh) at https://www.kuhaku.space/apt/, from where
# every Kūhaku computer installs it with its other updates.
set -euo pipefail
cd "$(dirname "$0")/.."
DEV="$(pwd)"
REPO="${KUHAKU_REPO:-rudolfkrcmar/kuhaku-linux}"
SITE="${KUHAKU_SITE_DIR:-$HOME/Projects/kuhaku-site}"
VERSION="$(cat VERSION)"
DRY=; [ "${1:-}" = "--dry-run" ] && DRY=1
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
stop() { echo "$*"; echo "Nothing was published."; exit 1; }

say "Kūhaku $VERSION"
[ -z "$(git status --porcelain --untracked-files=no)" ] || { git status --short --untracked-files=no; [ -n "$DRY" ] || stop "Commit first: a release is the commit at HEAD."; }
git rev-parse -q --verify "refs/tags/v$VERSION" >/dev/null && [ -z "$DRY" ] && stop "v$VERSION is already tagged; raise the number in VERSION."
NOTES="$(awk -v v="$VERSION" '$0 ~ "^## "v"( |$)" {on=1; next} on && /^## / {exit} on' CHANGELOG.md)"
if [ -z "$NOTES" ]; then
  [ -n "$DRY" ] || stop "CHANGELOG.md needs a \"## $VERSION — $(date +%F)\" section."
  echo "(CHANGELOG.md has no \"## $VERSION\" section yet)"
fi

say "Privacy check"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
git archive HEAD | tar -x -C "$STAGE"
bad=0
# 1. Secrets, by their shape.
if grep -rIlE '(gh[pousr]_[A-Za-z0-9]{30,}|sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}|AIza[0-9A-Za-z_-]{30,}|GOCSPX-[A-Za-z0-9_-]{10,}|ya29\.[A-Za-z0-9_-]{20,}|xox[bpas]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)' "$STAGE"; then
  echo "^ these files look like they hold a key or token"; bad=1; fi
# 2. Things that must never be in the repository.
for p in browser-data mado-chromium system/home/.claude system/packages system/home/src mac; do
  [ -e "$STAGE/$p" ] && { echo "$p is in the repository"; bad=1; }
done
find "$STAGE/system/home/.config/kuhaku" -type f ! -name config.json 2>/dev/null | grep . && { echo "^ personal Kūhaku settings"; bad=1; }
# 3. Your own words: home folder, user name, and anything listed (one per line, not in git) in
#    ~/.config/kuhaku/release-private-words (addresses, employer, names of people…).
words=("$HOME" "/home/$(id -un)/")
[ -f "$HOME/.config/kuhaku/release-private-words" ] && while IFS= read -r w; do [ -n "$w" ] && words+=("$w"); done < "$HOME/.config/kuhaku/release-private-words"
for w in "${words[@]}"; do
  hits=$(grep -rIilF -- "$w" "$STAGE" | grep -v -e "/LICENSE$" -e "/lib/" || true)
  [ -n "$hits" ] && { echo "\"$w\" appears in:"; echo "${hits//"$STAGE/"/  }"; bad=1; }
done
[ "$bad" = 0 ] || stop ""
echo "clean"

say "Package"
rm -f "$DEV"/os/out/kuhaku_*.deb
KUHAKU_RELEASE=1 KUHAKU_SRC="$STAGE" bash os/build-deb.sh >/dev/null
DEB="$(ls "$DEV"/os/out/kuhaku_*.deb)"; ls -la "$DEB"
dpkg-deb -I "$DEB" | grep -q "Version: $VERSION$" || stop "The package is not version $VERSION."

say "Signed package source (a trial copy)"
TRY="$(mktemp -d)"
[ -d "$SITE/apt/pool" ] && cp -a "$SITE/apt/." "$TRY/"
os/apt-repo.sh "$TRY" "$DEB"
[ -n "$DRY" ] && { rm -rf "$TRY"; echo; echo "Dry run: nothing published."; exit 0; }

say "Tag and GitHub release"
git tag -a "v$VERSION" -m "Kūhaku $VERSION"
git push -q origin "v$VERSION"
cp "$DEB" "$STAGE/kuhaku_all.deb"   # a stable name too, so "latest/download/kuhaku_all.deb" always works
files=("$DEB" "$STAGE/kuhaku_all.deb")
if [ -n "${KUHAKU_ISO:-}" ]; then
  cp "$KUHAKU_ISO" "$STAGE/kuhaku-amd64.iso"; files+=("$STAGE/kuhaku-amd64.iso")
fi
# shellcheck disable=SC2046  # the image only when there is one
(cd "$STAGE" && sha256sum kuhaku_all.deb $([ -n "${KUHAKU_ISO:-}" ] && echo kuhaku-amd64.iso) > SHA256SUMS); files+=("$STAGE/SHA256SUMS")
gh release create "v$VERSION" -R "$REPO" --title "Kūhaku $VERSION" --notes "$NOTES

Install: https://github.com/$REPO/blob/main/INSTALL.md · Kūhaku computers update themselves from https://www.kuhaku.space/apt/
Licenses and source code of everything in the install image: https://github.com/$REPO/blob/main/THIRD_PARTY_NOTICES.md#the-install-image" "${files[@]}"

say "Package source on kuhaku.space"
rm -rf "$SITE/apt"; mkdir -p "$SITE/apt"; cp -a "$TRY/." "$SITE/apt/"; rm -rf "$TRY"
git -C "$SITE" add apt
git -C "$SITE" commit -q -m "Kūhaku $VERSION in the package source"
git -C "$SITE" push -q
(cd "$SITE" && railway up --detach >/dev/null)
echo; echo "Released: https://github.com/$REPO/releases/tag/v$VERSION (the package source updates within minutes)"
