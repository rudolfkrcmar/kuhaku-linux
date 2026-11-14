# Changelog

What changed in each release of Kūhaku. Versions follow [semantic versioning](https://semver.org); the
number lives in `VERSION`, and each release is a git tag (`v0.3.0`).

## Unreleased

### Changed
- Mado runs on a newer engine: Qt WebEngine 6.10 from Debian's backports (Chromium 134 with security fixes to Chromium 144), up from 6.8 (Chromium 122). The package pins only this engine to backports, and unattended upgrades now include backports so its fixes arrive by themselves.
- Claude is told the real system it runs on (read from the system), not "Linux Mint".

### Fixed
- Mado and the other apps stay quiet in the log about settings and saved tabs that do not exist yet.

## 0.4.0 — 2026-11-14

### New
- **Kūhaku's eye.** On request, Claude can look through the phone's camera: a small page served only on this computer and shared to your own devices with Tailscale (`kuhaku-eye.py setup`). It takes a picture only when asked.
- Night follows the sun: the auto theme turns dark at sunset and light at sunrise, from the configured place or the time zone, instead of fixed hours.

### Fixed
- Finger unlock accepts every enrolled finger, not only the first, and asks the reader anew after sleep.
- The bar steps away when the app in front is fullscreen; Spotify fills the screen like the other apps.
- Websites in Mado get a white canvas at night, as their makers expect, instead of Kūhaku's dark one.
- Battery power is read on batteries that report energy rather than charge (`power_now`).
- Tegami takes a letter with a malformed date in its stride.
- The logs stay quiet about settings files that do not exist yet (a first run is not a fault), so real faults stand out.
- The Bluetooth panel no longer shows a MacBook-only hint on other computers.
- The screenshot keys work again: the home screen's command line is reopened if it goes missing, and one bad command no longer stops it.
- The volume card comes back after the sound server restarts (after sleep or a new headset).
- The charging bolt shows beside the battery on the home screen when plugged in.
- Right-click menus in Tegami and Mado wear Kūhaku's look and stay readable at night.
- Notices from the power manager and apps appear in Kūhaku's look.
- The volume and brightness display no longer logs a fault on every check on computers without a battery or backlight.
- Tsukuru ignores a new-file request when no project is open, instead of failing.

### Changed
- Settings is divided into categories: a quiet list on the left, one category at a time.
- The Print key (between AltGr and Ctrl on ThinkPads) picks a part of the screen; Shift+Print takes it all, Ctrl+Print copies a part.
- Sound outputs have plain names ("Laptop speakers", "Screen 1"), and sockets with nothing plugged in are hidden.
- On battery the computer switches to power saver by itself; plugged in, the mode chosen before comes back.

### Removed
- Face unlock. A flat picture from the infrared camera, without depth or liveness, can be fooled by a photo; fingers and the password remain.

### For developers
- Every shell script passes ShellCheck, in `dev/check` and on GitHub. Paths are quoted throughout (the install scripts broke on a folder with a space); the few places that split words on purpose say so.
- No Python file is over 1,000 lines: mail's core, its assistant, Miru, Mado, Tsukuru, the touch keyboard and the gestures are divided by subject (see ARCHITECTURE.md, "The apps, file by file").
- Every Python file is type-checked by mypy (was five). The home screen's parts declare what they share in `kuhaku_base.Home`.
- Licenses are checked: `tests/test_licenses.py` keeps every bundled library and vendored folder listed in `THIRD_PARTY_NOTICES.md`, with its version and license text, under an allowed license. CodeMirror and WirePlumber now ship their license texts; the GPL text the LGPL libraries build on is in `lib/LICENSES/`.
- GitHub: issue forms, a pull request checklist, `SECURITY.md` with private reporting, Dependabot for the CI actions, and main protected from deletion and force-pushes.

## 0.3.0 — 2026-11-12

### New
- **Dropbox and Google Drive in Miru (Kumo).** Sign in from Miru, tick the folders to keep on this computer; only those are copied, and kept the same both ways with `rclone bisync`. Several accounts of each.
- **Claude's safety net (`kuhaku-guard.py`).** A Claude Code hook that stops only what cannot be undone or would send secrets away — wiping disks, deleting whole folders outright (they go to the Trash instead), force-pushing main, sending keys or cookies — whoever asked, including text hidden in a page or email.
- Voice conversation on the home screen; the microphone can talk with Claude or dictate offline (Whisper, best model for the computer).
- ThinkPad Fn keys, notifications panel, microphone mute, the lid while Claude works.
- Battery shows a bolt while plugged in; the sign-in window closes itself once signed in.

### Fixed
- The computer could fall asleep while Claude worked (`keepawake` lost its power-manager call).
- The guard could be bypassed with `sudo -u`, `nice -n` or `bash -c`, and missed the whole `~/.ssh` folder and `/dev/tcp`; harmless key commands were taken for sending keys.
- Miru: a cloud folder did not refresh after a sync; copying a folder named like `v1.0` gave `v1 2.0`.
- Adding a second account of the same kind returned one that was never listed.
- Password screens stay dark in daytime; Tegami's buttons are readable at night.

### For developers
- `dev/check`: ruff format and lint, mypy on the core modules, 208 unit tests, a script-error check of every page, and 19 UI tests that click through the pages and compare screenshots. Runs before every commit and on GitHub Actions.
- `dev/install`: the project is the only place Kūhaku changes; installing is one step and never overwrites changes made in the running copy (`--adopt` brings them back).
- Errors are never swallowed silently: `kuhaku_log.report()` writes each fault once to `~/.local/state/kuhaku/logs/`.
- Large files divided by subject: the home screen (`kuhaku_config`, `kuhaku_claude`, `kuhaku_panels`, `kuhaku_voice`, `kuhaku_settings`, `kuhaku_system`), its page (`home/`), Miru's page, the touch keyboard (`tablet_*`), mail (`tegami_store`, `tegami_sync`). Messages from the pages go through lookup tables.
- `ARCHITECTURE.md`, `CONTRIBUTING.md`, this changelog.

## 0.2.3 and earlier

See the git history (`git log v0.2.3`).
