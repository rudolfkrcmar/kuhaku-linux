#!/bin/sh
# Kūhaku's own package source: a signed apt repository, so Kūhaku computers update themselves
# with the system's updates (unattended-upgrades), the way every Linux distribution does.
#
#   os/apt-repo.sh <repo folder> <kuhaku_*.deb>...   add packages and sign the index again
#   os/apt-repo.sh --key                             make the signing key (once, on the
#                                                    maintainer's computer) and print its public part
#
# The folder is static files (served from https://www.kuhaku.space/apt/):
#   pool/main/kuhaku_<version>_all.deb   every released package (the newest three are kept)
#   dists/stable/{Release,InRelease,Release.gpg}, dists/stable/main/binary-{all,amd64}/Packages(.gz)
#   kuhaku-archive-keyring.gpg           the public key, for computers to trust
#
# The private key stays in the maintainer's GnuPG keyring; a copy for safe keeping is written to
# ~/.config/kuhaku/kuhaku-signing-key.asc (keep it somewhere safe: without it no update can be signed).
set -eu
KEY_NAME="Kūhaku packages <packages@kuhaku.space>"
HERE="$(cd "$(dirname "$0")" && pwd)"
KEYRING="$HERE/package/usr/share/keyrings/kuhaku-archive-keyring.gpg"

fingerprint() {
  gpg --batch --with-colons --list-secret-keys "$KEY_NAME" 2>/dev/null | awk -F: '/^fpr/ {print $10; exit}'
}

if [ "${1:-}" = --key ]; then
  if [ -z "$(fingerprint)" ]; then
    gpg --batch --pinentry-mode loopback --passphrase '' --quick-generate-key "$KEY_NAME" ed25519 sign never
  fi
  fpr="$(fingerprint)"
  mkdir -p "$(dirname "$KEYRING")" "$HOME/.config/kuhaku"
  gpg --batch --yes --export "$fpr" > "$KEYRING"
  ( umask 077; gpg --batch --yes --armor --pinentry-mode loopback --passphrase '' --export-secret-keys "$fpr" \
      > "$HOME/.config/kuhaku/kuhaku-signing-key.asc" )
  echo "Signing key $fpr"
  echo "Public part: $KEYRING (shipped in the package)"
  echo "Private part, for safe keeping: ~/.config/kuhaku/kuhaku-signing-key.asc"
  exit 0
fi

[ $# -ge 2 ] || { sed -n '2,12p' "$0"; exit 2; }
REPO="$1"; shift
fpr="$(fingerprint)"
[ -n "$fpr" ] || { echo "No signing key here: run os/apt-repo.sh --key first."; exit 1; }

mkdir -p "$REPO/pool/main" "$REPO/dists/stable/main/binary-all" "$REPO/dists/stable/main/binary-amd64"
for deb in "$@"; do cp "$deb" "$REPO/pool/main/"; done
# The newest three versions stay (a computer can always step back one), older ones go.
find "$REPO/pool/main" -name "kuhaku_*_all.deb" | sort -V | head -n -3 | xargs -r rm -f

cd "$REPO"
for arch in all amd64; do
  apt-ftparchive --arch "$arch" packages pool > "dists/stable/main/binary-$arch/Packages"
  gzip -9nkf "dists/stable/main/binary-$arch/Packages"
done
apt-ftparchive \
  -o APT::FTPArchive::Release::Origin=Kuhaku \
  -o APT::FTPArchive::Release::Label=Kuhaku \
  -o APT::FTPArchive::Release::Suite=stable \
  -o APT::FTPArchive::Release::Codename=stable \
  -o APT::FTPArchive::Release::Architectures="all amd64" \
  -o APT::FTPArchive::Release::Components=main \
  -o APT::FTPArchive::Release::Description="Kūhaku, a calm home for Claude" \
  release dists/stable > dists/stable/Release.new
mv dists/stable/Release.new dists/stable/Release
rm -f dists/stable/InRelease dists/stable/Release.gpg
gpg --batch --yes --local-user "$fpr" --clearsign -o dists/stable/InRelease dists/stable/Release
gpg --batch --yes --local-user "$fpr" --detach-sign --armor -o dists/stable/Release.gpg dists/stable/Release
gpg --batch --yes --export "$fpr" > kuhaku-archive-keyring.gpg
echo "Signed $(grep -c '^Package:' dists/stable/main/binary-all/Packages) package(s) in $REPO"
