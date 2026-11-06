#!/bin/bash
# Publishes Kūhaku: the dev repository (private, everyday work) -> the public repository.
#
#   os/release.sh             check, copy, build the package, make a GitHub release
#   os/release.sh --dry-run   only the privacy check and the package build
#   KUHAKU_ISO=path/to.iso os/release.sh   also attach an install image
#
# Each release is one new commit in the public repository, with none of the dev history.
# Before anything leaves this computer it is checked for secrets and personal words.
set -euo pipefail
cd "$(dirname "$0")/.."
DEV="$(pwd)"
PUBLIC_REPO="${KUHAKU_PUBLIC_REPO:-rudolfkrcmar/kuhaku-linux}"
PUB="${KUHAKU_PUBLIC_DIR:-$HOME/.cache/kuhaku-public}"
VERSION="$(cat VERSION)"
DRY=; [ "${1:-}" = "--dry-run" ] && DRY=1
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "Kūhaku $VERSION"
[ -z "$(git status --porcelain)" ] || { echo "Not yet committed (and so not published):"; git status --short; }

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
  [ -n "$hits" ] && { echo "\"$w\" appears in:"; echo "$hits" | sed "s#$STAGE/#  #"; bad=1; }
done
[ "$bad" = 0 ] || { echo; echo "Nothing was published."; exit 1; }
echo "clean"

say "Package"
OUT=$(KUHAKU_RELEASE=1 KUHAKU_SRC="$STAGE" bash os/build-deb.sh | tail -1)
DEB="$DEV/os/$OUT"; ls -la "$DEB"
[ -n "$DRY" ] && { echo "Dry run: nothing published."; exit 0; }

say "Public repository"
gh release view "v$VERSION" -R "$PUBLIC_REPO" >/dev/null 2>&1 && { echo "v$VERSION is already released; raise the number in VERSION."; exit 1; }
[ -d "$PUB/.git" ] || gh repo clone "$PUBLIC_REPO" "$PUB" -- -q 2>/dev/null
git -C "$PUB" rev-parse -q --verify HEAD >/dev/null || git -C "$PUB" checkout -q -B main
git -C "$PUB" config user.name "$(git config user.name)"; git -C "$PUB" config user.email "$(git config user.email)"
git -C "$PUB" pull -q --ff-only 2>/dev/null || true
find "$PUB" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$STAGE/." "$PUB/"
git -C "$PUB" add -A
git -C "$PUB" commit -q -m "Kūhaku $VERSION"
git -C "$PUB" tag -a "v$VERSION" -m "Kūhaku $VERSION"
git -C "$PUB" push -q origin HEAD --tags

say "GitHub release"
cp "$DEB" "$STAGE/kuhaku_all.deb"   # a stable name too, so "latest/download/kuhaku_all.deb" always works
files=("$DEB" "$STAGE/kuhaku_all.deb")
if [ -n "${KUHAKU_ISO:-}" ]; then
  cp "$KUHAKU_ISO" "$STAGE/kuhaku-amd64.iso"; files+=("$STAGE/kuhaku-amd64.iso")
  (cd "$STAGE" && sha256sum kuhaku-amd64.iso kuhaku_all.deb > SHA256SUMS); files+=("$STAGE/SHA256SUMS")
fi
gh release create "v$VERSION" -R "$PUBLIC_REPO" --title "Kūhaku $VERSION" \
  --notes "Install instructions: https://github.com/$PUBLIC_REPO/blob/main/INSTALL.md" "${files[@]}"
echo; echo "Released: https://github.com/$PUBLIC_REPO/releases/tag/v$VERSION"
