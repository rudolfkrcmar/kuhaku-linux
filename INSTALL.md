# Installing Kūhaku

Kūhaku is free and open source (MIT). It is an independent project, not made by or affiliated with Anthropic or the Debian project. You need a Claude account that includes Claude Code (for example Claude Pro or Max) to talk to Claude; Kūhaku itself costs nothing.

There are two ways in:

- **A. A new computer, or one you are happy to erase:** start it from a USB stick and install Kūhaku as the whole system.
- **B. A computer already running Debian 13 or Linux Mint:** add Kūhaku as a session next to what you have.

## A. Install from a USB stick (erases the computer's drive)

**You need:** a 64-bit Intel or AMD computer, a USB stick of 8 GB or more, an internet connection (Wi‑Fi is fine), and about 20 minutes.

1. **Download** `kuhaku-amd64.iso` from the [latest release](https://github.com/rudolfkrcmar/kuhaku-linux/releases/latest).
   To check it arrived whole, compare its checksum with `SHA256SUMS` in the same release (`sha256sum kuhaku-amd64.iso`).
2. **Write it to the stick.** This erases the stick.
   - Any system: [balenaEtcher](https://etcher.balena.io): choose the file, choose the stick, Flash.
   - Windows: [Rufus](https://rufus.ie), in "DD image" mode.
   - Linux: GNOME Disks → the stick → ⋮ → Restore Disk Image; or `sudo dd if=kuhaku-amd64.iso of=/dev/sdX bs=4M status=progress conv=fsync` (check `sdX` with `lsblk` first: the wrong letter erases the wrong drive).
3. **Start the computer from the stick.**
   - Most PCs: press the boot menu key while it starts (often F12, F11, F9 or Esc) and pick the USB stick.
   - Turn off **Secure Boot** in the firmware settings first (usually F2 or Del at start). Kūhaku's boot loader is not signed yet.
   - Intel Macs: hold Option (⌥) while starting and pick "EFI Boot".
4. **Look around, or install.** Kūhaku starts from the stick without touching the computer. To install, follow the screens:
   your name and one password, how freely Claude may work, your programs, the drive, and the copying.
   - The password unlocks the encrypted drive when the computer starts. It is the only password you type; there is no separate login.
   - **"Anything, without asking" (recommended):** Claude edits files, runs commands, changes settings and installs software on its own, and is told to ask before deleting or anything that cannot be undone. **"Careful":** Claude reads and edits files but runs no commands. You can switch in Settings later.
   - **Programs:** two lists, *Made for Kūhaku* (Mado, Tsukuru, Tegami, Miru, Kansei) and *Made by others* (the Claude app, Firefox, LibreOffice, GIMP, VLC, Blender, FreeCAD and more). Programs by others are fetched from their makers once the computer is online. Claude can add or remove any of them later.
   - **Everything on the computer's drive is replaced.** Copy what you want to keep first.
5. **Restart**, take out the stick, type your password once.
6. **Sign in to Claude.** At the first start Kūhaku installs Claude Code from Anthropic and opens its sign-in. After that, just type what you want.

## B. Add Kūhaku to Debian 13 or Linux Mint

1. In a terminal, run:
   ```sh
   curl -fsSL https://www.kuhaku.space/install.sh | sh
   ```
   It downloads the latest package and installs it with apt. Or by hand: download `kuhaku_all.deb` from the [latest release](https://github.com/rudolfkrcmar/kuhaku-linux/releases/latest) and run `sudo apt install ./kuhaku_all.deb`.
2. Log out. On the login screen choose the **Kūhaku** session, and log in.
3. Kūhaku sets itself up in `~/.local/share/kuhaku`, installs Claude Code and opens its sign-in.

To go back, choose your old session on the login screen. To remove it: `sudo apt remove kuhaku`, and delete `~/.local/share/kuhaku` if you like.

## Updates

The system (Debian) updates itself quietly. Kūhaku updates come as new releases; install the new `.deb` the same way, or ask Claude in Kūhaku to do it.

## Something wrong?

Open an [issue](https://github.com/rudolfkrcmar/kuhaku-linux/issues). The installer keeps its log in `/var/log/kuhaku-install.log`.
