# Kūhaku OS

Kūhaku as its own system: Debian 13 plus Kūhaku's session, apps, login and boot screens, and a four-screen installer.

    ./build-deb.sh               # the Kūhaku package, from the app in the folder above
    sudo ./image/build-iso.sh    # an install image (live system + installer)
    ./image/test-vm.sh           # boot the image in QEMU; always test before writing a stick

The installer asks for a name and one password (it encrypts the drive, is the account password, and opens files carried from an old computer), and how freely Claude may work. The time zone comes from the computer, or from its internet connection, otherwise UTC.
