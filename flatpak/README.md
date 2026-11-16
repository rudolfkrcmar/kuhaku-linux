# Kūhaku apps on any Linux

Each app here can be installed on its own, outside Kūhaku, as a Flatpak. They run on GTK 4 and
WebKitGTK 6 (`kuhaku_webapp.py`), so X11 and Wayland, GNOME, KDE, Mint or Fedora all work.
Outside Kūhaku an app keeps the desktop's own window frame, follows its light or dark setting and
opens links in the default browser.

    flatpak install flathub org.gnome.Sdk//49 org.gnome.Platform//49
    flatpak-builder --user --install --force-clean build flatpak/tegami/space.kuhaku.Tegami.yml
    flatpak run space.kuhaku.Tegami

Ready: Tegami and Miru (build them as above, with flatpak/miru/space.kuhaku.Miru.yml).
Next, in this order: Kiroku, Kansei, Tsukuru (needs VTE for GTK 4), Mado.

Tools that belong to the computer, not the sandbox, run on the host through flatpak-spawn:
Claude Code for Tegami (tegami/claude.sh), udisksctl for Miru (common/host.sh). Cloud folders
(Kumo, rclone) are not in the Flatpak yet.
