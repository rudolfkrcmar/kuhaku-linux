# Kūhaku apps on any Linux

Each app here can be installed on its own, outside Kūhaku, as a Flatpak. They run on GTK 4 and
WebKitGTK 6 (`kuhaku_webapp.py`), so X11 and Wayland, GNOME, KDE, Mint or Fedora all work.
Outside Kūhaku an app keeps the desktop's own window frame, follows its light or dark setting and
opens links in the default browser.

    flatpak install flathub org.gnome.Sdk//49 org.gnome.Platform//49
    flatpak-builder --user --install --force-clean build flatpak/tegami/space.kuhaku.Tegami.yml
    flatpak run space.kuhaku.Tegami

Ready: Tegami. Next, in this order: Miru, Kiroku, Kansei, Tsukuru (needs VTE for GTK 4), Mado.
