# Changelog

What changed in each release of Kūhaku. Versions follow [semantic versioning](https://semver.org); the
number lives in `VERSION`, and each release is a git tag (`v0.3.0`).

## Unreleased

### New
- **Settings, redrawn.** Settings now look like the rest of Kūhaku's design: the places on the left in three groups (You, Computer, System) under **Find a setting**, which finds any setting by name and opens nothing else (Enter asks Claude when nothing matches); one place at a time on the right, with a title, one line on how things stand, and each setting as a name, a grey hint and its control on one right edge. Switches are a square, levels a bar beneath their name, long choices a list. New places: **Battery** (the charge with the tick where it will stop, Protect the battery, Fill for a trip, the last hours and the battery's health, all in Settings now), **Network** (Wi‑Fi on or off, then networks, VPN and Bluetooth), **Storage** and **About**. Nothing was taken away.
- **Notices, Privacy, and Language and time in Settings.** Notices: quiet for an hour, until 7:00 or always (urgent ones still show, everything waits in the list), cards without their words, how long a card stays, and any app quieted on its own. Privacy: lock when the screen goes off, lock now, the fingerprints, what the AI may do and its safety net, whether it may look through your phone, and forgetting the kept answers or the phone's last picture. Language and time: keyboard layouts (Alt+Shift moves between them) beside the clock and time zone.
- **One click back to what Claude showed you.** When Claude opens a page, a file, a folder or an app while it works (in Mado, Miru or any other app), or makes a new file in your home folder, its answer ends with quiet links to them, so you can open them again later in the same app without asking.
- **Earlier messages as a chat.** Answers still fade away on their own, but a history button beside the line (and above an answer) shows the tab's whole conversation as one quiet chat, oldest first, newest at the bottom, also while Claude is still working (beside the + for the next message). Esc goes back and never stops the work.
- **Comments on web pages, for Claude.** Mado's Developer mode has **Comment** (Ctrl+Alt+C), opened and closed by its icon like the tools: open, point at anything on a page, a site of yours or any other, click, and write what should change. A red pin stays on the spot. The open comments are listed under the bar, which counts a site's open comments and hands them all to Claude on the home screen in one click; Claude finds the project, makes the changes and marks each done, and the pins go. Ask later instead ("do my Mado comments") and it works the same way. The page's own scripts never see the comments.
- **Developer tools fold away.** Their close button now works (Developer mode stays), the bar shows and hides them, and puts them under the page or beside it.
- **Kūhaku updates itself.** Its own signed package source (`https://www.kuhaku.space/apt/`): new versions of Kūhaku now arrive with the system's other updates, checked against Kūhaku's key, instead of being downloaded by hand.
- **Any AI on the home screen.** The home screen can ask OpenAI (Codex), Gemini, Grok or a model on this computer instead of Claude: Settings → AI → Home uses, or First steps, or one tab at a time. Their steps show the same way Claude's do.
- **Adding pictures for Claude, the Kūhaku way.** The picture button opens a calm picker over the home screen instead of the system's file dialog: recent screenshots (or Pictures, Downloads, Desktop) as a grid, newest first. Space looks closer, arrows move, click or X chooses several, Enter adds. Other walks your home folder in the same picker; the system's file dialog is gone.
- **Any file for Claude, not only pictures.** The home screen's add button now offers every kind of file: recent files from Downloads, Documents, Desktop and Pictures, or any folder through Other, with Space showing a PDF's first page or a text's first lines. Pictures are still copied into Pictures/ForClaude; other files are named in the line where they are.
- **No more system file dialogs.** Attaching files in Tegami and uploading on web pages in Mado now open Kūhaku's own picker, the same calm grid as Add a picture: recent files, Downloads, Documents, Desktop, Pictures and Other to walk your home folder. Space looks closer: pictures large, a PDF's first page, a text file's first lines. Outside Kūhaku, Tegami keeps the desktop's own dialog.
- **Grok.** xAI's Grok Build joins Claude, OpenAI and Gemini in Settings → AI, for the home screen, Mail and Tsukuru.
- **Copilot.** GitHub's Copilot CLI joins them too: one subscription, several models, often already there through work or school.
- **First steps.** People who add Kūhaku to Debian or Mint without its installer get one quiet page at the first start: sign in to Claude, and to OpenAI (Codex) or Gemini if they like, how freely Claude works, and video formats. OpenAI's and Google's tools are fetched from npm the first time they are chosen (also from Settings → AI, where signing in to them used to fail when they were missing). Other programs: just ask. It shows once, and stays in Settings → Updates → First steps.

- **Battery care.** Plugged in, the battery stops at 80% (the firmware holds the limit, on ThinkPads and most other laptops). It fills to 100% when you will need it: before a trip in your calendar (a flight, a train, a hotel, days somewhere), while you travel (charging on a network that is not one of your usual places), and shortly before you usually head off, which it learns. The battery panel has **Care**, **Full once** (until you next head off) and **Always full**. A gentle card at 20% says it is a good moment to plug in.
- **The keyring asks in Kūhaku's words.** When your saved passwords are locked (Kūhaku logs in by itself, so they stay locked until something needs one), the password is asked on Kūhaku's dark screen with the red line, not in GNOME's grey dialog. The password travels to the keyring encrypted, as before. Unlocking the screen with your password now opens them too, so most days nothing asks at all.
- **A way out if the home screen freezes.** It restarts itself after about 20 seconds without answering, and **Ctrl+Alt+Backspace** restarts it at once. Open apps are not touched.

### Changed
- Kūhaku no longer ships media formats covered by patents in some countries (H.264, H.265, AAC and others: the GStreamer bad, ugly and libav plugins). You add them yourself if you choose, with **More video formats** in the installer or **Settings → Updates → Video formats**. Computers that already have them keep them. Without them, screen recordings are saved as WebM (VP8) instead of MP4.
- Mado runs on a newer engine: Qt WebEngine 6.10 from Debian's backports (Chromium 134 with security fixes to Chromium 144), up from 6.8 (Chromium 122). The package pins only this engine to backports, and unattended upgrades now include backports so its fixes arrive by themselves.
- Claude is told the real system it runs on (read from the system), not "Linux Mint".

### Fixed
- Opening the calendar with a large Google Calendar connected (a busy work calendar) froze the whole screen for many seconds, long enough for the window manager to offer to force-quit Kūhaku. Feeds are now read once per download, in a separate low-priority process, and the panel opens at once.
- The calendar panel shows calendars connected while it was closed, and no longer reopens on a half-filled "paste the address" form.
- A calendar's name is read from the feed's header, so connecting one is quick too.
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
