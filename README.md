# Kūhaku (空白)

*"Blank space."* A calm, full screen home for Claude on Linux.

Kūhaku replaces the usual desktop with one quiet screen: you say what you want, Claude does it, and the answer comes back in large, light type. Around it sits a small set of apps in the same spare design:

- **Mado** (窓, window): the web, quietly. A browser on Qt WebEngine (Chromium), with a WebKitGTK fallback.
- **Tegami** (手紙, letter): mail that works offline.
- **Miru** (見る, to look): files and previews, with Dropbox and Google Drive: only the folders you choose, kept in sync both ways (Kumo, 雲).
- **Tsukuru** (作る, to make): a code editor for making things with Claude.
- **Kansei** (管制, mission control): everything the computer is doing, live.
- **Kiroku** (記録, the record): every job an AI has done here, with model, time, tokens, cost and energy.
- **Terminal**: a shell in the same design.

It runs as an X11 session on Debian 13 (or Linux Mint) with xfwm4, and is written in Python with GTK/WebKit and Qt WebEngine views.

## Status

Personal project, early and changing quickly. Some parts are tuned for specific hardware (the 12" MacBook8,1: speakers, sleep, Bluetooth). Those live in `system/` and are only used on that machine.

## Installing

The easiest way is **[kuhaku.space/download](https://www.kuhaku.space/download/)**. See also **[INSTALL.md](INSTALL.md)**: a USB stick that installs Kūhaku as the whole system, or a package that adds it to Debian 13 or Linux Mint. Downloads are on the [releases page](https://github.com/rudolfkrcmar/kuhaku-linux/releases/latest).

`os/` holds how the package and the install image are built (`os/build-deb.sh`, `os/image/build-iso.sh`).

## How freely Claude works

Kūhaku is made for Claude to simply do things: by default it edits files, runs commands, changes settings and installs software without stopping to ask (Claude Code's `bypassPermissions` mode). It is told to ask first before deleting anything or doing what cannot be undone. This is the recommended way to use Kūhaku.

The installer asks which you want. **Careful** lets Claude read, search and edit files but not run commands. You can switch any time in Settings, under "May do".

Under that freedom sits a safety net, `kuhaku-guard.py`, a Claude Code hook that stops only what cannot be undone, whoever asked for it (you, or words hidden in an email or web page): wiping a disk or the encryption keys, deleting your home folder, its main folders or a whole project outright (Claude moves them to the Trash instead), force-pushing over a main branch, sending keys, passwords, tokens or browser data over the network, and adding a login key. Everything else stays free.

## Developing

`dev/check` compiles everything, runs the tests in `tests/` and loads every page off-screen to catch script errors; `dev/check quick` (no pages) runs before each commit. Best-effort code never hides a fault: it calls `report()` from `kuhaku_log.py`, which writes it once to `~/.local/state/kuhaku/logs/<app>.log`.

## Privacy

This repository holds code and shared settings only. Mail accounts, Claude notes, browser profiles and package lists stay on your computer; they are listed in `.gitignore`, and `kuhaku-backup` writes your home folder and user name as `$HOME` and `USER`.

## License

MIT, see [LICENSE](LICENSE). Bundled components keep their own licenses, listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Names

Kūhaku is an independent project. It is not made, endorsed or supported by Anthropic. Claude and Claude Code are trademarks of Anthropic, and Kūhaku uses them only to say what it works with. Claude Code and the Claude app are Anthropic's own software under Anthropic's terms; Kūhaku does not include them, but downloads them from Anthropic on your computer when you first start it. Kūhaku is based on Debian and is not affiliated with the Debian project.
