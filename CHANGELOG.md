# Changelog

What changed in each release of Kūhaku. Versions follow [semantic versioning](https://semver.org); the
number lives in `VERSION`, and each release is a git tag (`v0.3.0`).

## Unreleased

### Fixed
- The volume and brightness display no longer logs a fault on every check on computers without a battery or backlight.
- Tsukuru ignores a new-file request when no project is open, instead of failing.

### For developers
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
