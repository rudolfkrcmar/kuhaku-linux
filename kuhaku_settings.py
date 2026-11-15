"""Kūhaku's Settings page: reading every preference for the page, writing one back, and updating the system.

A part of the Kuhaku window class (kuhaku.py), kept here by subject."""

import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib

import claude_accounts as accounts
from kuhaku_base import Home
from kuhaku_config import ANSWERS_FILE, CONFIG_FILE, HERE, HOME
from kuhaku_log import report
from kuhaku_system import (
    PAD_KEYS,
    POWER_KEYS,
    read_battery,
    read_displays,
    read_power,
    read_time,
    read_trackpad,
    read_unlock,
    run,
    write_display,
    write_power,
    write_time,
    write_trackpad,
)

# Formats covered by patents in some countries: never shipped, added only when the person chooses.
CODECS = ("gstreamer1.0-plugins-bad", "gstreamer1.0-plugins-ugly", "gstreamer1.0-libav")
KEEP_CODECS = (
    "{ for p in " + " ".join(CODECS) + "; do dpkg-query -W -f='${Status}' $p 2>/dev/null | grep -q 'ok installed'"
    " && apt-mark manual $p >/dev/null || true; done; } && "
)

# The first start, for people who added Kūhaku without its installer: the installer's programs by
# others, from Debian (each with the package names it may have; Mint and Debian differ a little).
PROGRAMS = (
    ("firefox", "Firefox", "Mozilla", "A second web browser.", ("firefox-esr", "firefox")),
    ("thunderbird", "Thunderbird", "Mozilla", "Mail and calendar.", ("thunderbird",)),
    ("libreoffice", "LibreOffice", "The Document Foundation", "Documents, spreadsheets, slides.", ("libreoffice",)),
    ("gimp", "GIMP", "The GIMP team", "Photos and pictures.", ("gimp",)),
    ("inkscape", "Inkscape", "The Inkscape project", "Drawings and vector graphics.", ("inkscape",)),
    ("krita", "Krita", "KDE", "Painting.", ("krita",)),
    ("vlc", "VLC", "VideoLAN", "Plays any video or music.", ("vlc",)),
    ("audacity", "Audacity", "Audacity team", "Records and edits sound.", ("audacity",)),
    ("blender", "Blender", "Blender Foundation", "3D models and animation.", ("blender",)),
    ("freecad", "FreeCAD", "FreeCAD project", "3D design for making things.", ("freecad",)),
)


def installed(packages):
    """Which of these packages are installed."""
    _, out, _ = run(["dpkg-query", "-W", "-f=${Package} ${Status}\\n", *packages])
    return {line.split()[0] for line in out.splitlines() if line.endswith("ok installed")}


def available(names):
    """The first of these package names the package sources know (empty when none)."""
    for name in names:
        rc, out, _ = run(["apt-cache", "policy", name])
        if rc == 0 and "Candidate:" in out and "Candidate: (none)" not in out:
            return name
    return ""


def welcome_due(state):
    """Shown once, at the very first start, only to people who did not come through the installer
    (it asked all of this already), and never to a home already in use."""
    return not state.get("welcomed") and not Path("/etc/kuhaku/permission-mode").exists() and not ANSWERS_FILE.exists()


def codecs_added():
    _, out, _ = run(["dpkg-query", "-W", "-f=${Status}\\n", *CODECS])
    return out.count("ok installed") == len(CODECS)


def output_name(sink, plain=False):
    """A sound output in plain words: "Laptop speakers", "Screen 2 (HDMI)", not the sound chip's long name."""
    desc = sink.get("description") or sink["name"]
    card = (sink.get("properties") or {}).get("device.description", "")
    part = desc[len(card) :].strip() if card and desc.startswith(card) and desc != card else desc
    part = part.replace(" (EQ)", "").replace("Built-in Audio ", "")
    if part in ("Speaker", "Speakers", "Analog Stereo", "Speaker + Headphones"):
        part = "Laptop speakers"
    m = re.fullmatch(r"HDMI / DisplayPort (\d+) Output", part)
    if m:
        part = f"Screen {m.group(1)} (HDMI / DisplayPort)"
    return part + " (plain)" if plain else part


class Settings(Home):
    # --- Kūhaku settings page --------------------------------------------
    def read_prefs(self):
        _, sinks_json, _ = run(["pactl", "-f", "json", "list", "sinks"])
        _, default_sink, _ = run(["pactl", "get-default-sink"])
        outputs = []
        try:
            sinks = json.loads(sinks_json)
            # A tuned chain (a PipeWire filter in front of the speakers) plays through the raw device:
            # that one is offered as the plain version.
            targets = (str((k.get("properties") or {}).get("filter.smart.target", "")) for k in sinks)
            tuned = set(re.findall(r'node\.name = "([^"]+)"', " ".join(targets)))
            for k in sinks:
                if "Raw" in k.get("description", "") or k["name"] in ("mb81_speakers", "mb81_speakers_eq_out"):
                    continue
                active = k["name"] == default_sink.strip()
                ports = {pt.get("name"): pt.get("availability") for pt in k.get("ports") or []}
                if not active and ports.get(k.get("active_port")) == "not available":
                    continue  # an HDMI or headphone socket with nothing plugged in
                outputs.append({"id": k["name"], "name": output_name(k, k["name"] in tuned), "active": active})
        except Exception:
            report()

        def percent(cmd):
            _, out, _ = run(cmd)
            nums = [int(x) for x in re.findall(r"(\d+)%", out)]
            return round(sum(nums) / len(nums)) if nums else 0

        bright = None
        bl = next(iter(sorted(Path("/sys/class/backlight").glob("*"))), None)
        if bl:
            try:
                bright = round(100 * int((bl / "brightness").read_text()) / int((bl / "max_brightness").read_text()))
            except Exception:
                report()
        _, profile, _ = run(["powerprofilesctl", "get"])
        mem = disk = None
        try:
            total = re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text())
            mem = round(int(total.group(1)) / 1048576) if total else None
            st = os.statvfs(str(HOME))
            disk = [round(st.f_bavail * st.f_frsize / 1e9), round(st.f_blocks * st.f_frsize / 1e9)]
        except Exception:
            report()
        os_name = re.search(r'PRETTY_NAME="([^"]+)"', Path("/etc/os-release").read_text())
        cpu = re.search(r"model name\s*:\s*(.+)", Path("/proc/cpuinfo").read_text())
        try:
            model = Path("/sys/class/dmi/id/product_name").read_text().strip()
        except Exception:
            model = ""
        bat = read_battery()
        updated = None
        for stamp in ("/var/lib/apt/periodic/unattended-upgrades-stamp", "/var/lib/apt/periodic/update-success-stamp"):
            try:
                updated = max(updated or 0, Path(stamp).stat().st_mtime)
            except OSError:
                pass
        return {
            "updates": {
                "auto": Path("/etc/apt/apt.conf.d/52kuhaku-unattended").exists(),
                "checked": updated,
                "running": bool(getattr(self, "updating", None)),
            },
            "codecs": {"added": codecs_added(), "running": bool(getattr(self, "adding_codecs", None))},
            "theme": self.state.get("theme") or "auto",
            "volume": percent(["pactl", "get-sink-volume", "@DEFAULT_SINK@"]),
            "mic": percent(["pactl", "get-source-volume", "@DEFAULT_SOURCE@"]),
            "outputs": outputs,
            "brightness": bright,
            "profile": profile.strip() or None,
            "permission_mode": self.config.get("permission_mode") or "default",
            "model": self.config.get("model") or "default",
            "dictation_model": self.config.get("dictation_model") or "auto",
            "mic_button": self.config.get("mic_button") or "talk",
            "displays": read_displays(),
            "power": read_power(),
            "time": read_time(),
            "accounts": accounts.listing(),
            "account_for": accounts.uses(),
            "ai_sets": accounts.sets_listing(),
            "ai_signing": sorted(getattr(self, "signing", set())),
            "ai_local": accounts.local_status(),
            "trackpad": read_trackpad(),
            "unlock": read_unlock(),
            "about": {
                "computer": model,
                "system": os_name.group(1) if os_name else "Linux",
                "processor": re.sub(r"\s+", " ", cpu.group(1)).replace("(TM)", "").replace("(R)", "") if cpu else "",
                "memory": mem,
                "disk": disk,
                "kernel": os.uname().release,
                "battery": bat["level"] if bat else None,
            },
        }

    def write_pref(self, key, value):
        if key == "volume":
            run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"])
            run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{max(0, min(100, int(value)))}%"])
        elif key == "mic":
            run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{max(0, min(100, int(value)))}%"])
        elif key == "output":
            run(["pactl", "set-default-sink", str(value)])
        elif key == "brightness":
            run([str(HERE / "kuhaku-brightness"), "set", str(max(5, min(100, int(value))))])
        elif key == "display":
            write_display(value)
        elif key == "profile":
            run(["powerprofilesctl", "set", str(value)])
        elif key in POWER_KEYS:
            write_power(key, value)
        elif key in ("time_auto", "time_zone", "time_set"):
            write_time(key, value)
            GLib.idle_add(self.push_status)
        elif key in PAD_KEYS:
            write_trackpad(key, value)
        elif key == "account":
            GLib.idle_add(lambda: self.switch_account(str(value)) and False)
        elif key == "home_set":
            ident = accounts.ensure(str(value), "claude")
            GLib.idle_add(lambda: self.switch_account(ident) and False)
        elif key in ("account_mail", "account_dev"):
            accounts.set_for(key[8:], str(value or ""))
        elif key == "local_model":
            accounts.set_local_model(value)
        elif key == "theme":
            self.state["theme"] = None if value == "auto" else value
            self.save_state()
            GLib.idle_add(self.push_theme)
        elif key == "update-now":
            self.update_now()
        elif key == "add-codecs":
            self.add_codecs()
        elif key == "welcome-programs":
            self.add_programs([str(x) for x in (value or []) if isinstance(x, str)])
        elif key == "welcome-signin":
            GLib.idle_add(lambda: self.sign_in(accounts.active()["id"]) and False)
        elif key == "welcome-done":
            self.state["welcomed"] = True
            self.save_state()
        elif key in ("permission_mode", "model", "dictation_model", "mic_button"):
            self.config[key] = None if value == "default" else value
            CONFIG_FILE.write_text(json.dumps(self.config, indent=2) + "\n")
            if key == "dictation_model" and self.whisper:
                self.whisper.terminate()
                self.whisper = None
            if key == "mic_button":
                self.send("setMicMode", self.config.get(key) or "talk")

    def update_now(self):
        """Everything up to date now (asks for the password once); the OSD says when it is done."""
        if getattr(self, "updating", None):
            return

        def work():
            r = subprocess.run(
                [
                    "pkexec",
                    "sh",
                    "-c",
                    "DEBIAN_FRONTEND=noninteractive apt-get update -q && "
                    "DEBIAN_FRONTEND=noninteractive apt-get -y -q full-upgrade && "
                    + KEEP_CODECS  # added by the person: autoremove must not take them
                    + "apt-get -y -q autoremove",
                ],
                capture_output=True,
                text=True,
            )
            self.updating = None
            msg = (
                "Up to date"
                if r.returncode == 0
                else ("Cancelled" if r.returncode in (126, 127) else "Could not finish")
            )
            GLib.idle_add(self.note, "Updates", msg)

        self.updating = threading.Thread(target=work, daemon=True)
        self.updating.start()
        self.note("Updates", "Updating…")

    def add_codecs(self):
        """More video formats (H.264, AAC…) from Debian, only when the person asks (asks for the password once)."""
        if getattr(self, "adding_codecs", None):
            return

        def work():
            r = subprocess.run(
                [
                    "pkexec",
                    "sh",
                    "-c",
                    "DEBIAN_FRONTEND=noninteractive apt-get update -q && "
                    "DEBIAN_FRONTEND=noninteractive apt-get install -y -q " + " ".join(CODECS),
                ],
                capture_output=True,
                text=True,
            )
            self.adding_codecs = None
            msg = "Added" if r.returncode == 0 else ("Cancelled" if r.returncode in (126, 127) else "Could not finish")
            GLib.idle_add(self.note, "Video formats", msg)

        self.adding_codecs = threading.Thread(target=work, daemon=True)
        self.adding_codecs.start()
        self.note("Video formats", "Adding…")

    def read_welcome(self):
        """What the first-start page shows: Claude, how freely it works, video formats, programs."""
        acct = accounts.active()
        has_claude = Path(self.claude).exists() or bool(shutil.which("claude"))
        have = installed([n for *_, names in PROGRAMS for n in names])
        return {
            "claude": "ready" if accounts.signed_in(acct) else ("signin" if has_claude else "waiting"),
            "permission_mode": self.config.get("permission_mode") or "default",
            "codecs": {"added": codecs_added(), "running": bool(getattr(self, "adding_codecs", None))},
            "programs": [
                {"id": i, "name": n, "by": by, "about": about, "installed": bool(have & set(names))}
                for i, n, by, about, names in PROGRAMS
            ],
            "adding": bool(getattr(self, "adding_programs", None)),
        }

    def add_programs(self, ids):
        """Programs by others chosen on the first-start page, from the system's package sources
        (asks for the password once)."""
        wanted = [names for i, *_, names in PROGRAMS if i in ids]
        if not wanted or getattr(self, "adding_programs", None):
            return

        def work():
            names = [n for n in (available(ns) for ns in wanted) if n]
            rc = 1
            if names:  # names only from PROGRAMS, never from the page
                rc = subprocess.run(
                    [
                        "pkexec",
                        "sh",
                        "-c",
                        "DEBIAN_FRONTEND=noninteractive apt-get update -q && "
                        "DEBIAN_FRONTEND=noninteractive apt-get install -y -q " + " ".join(names),
                    ],
                    capture_output=True,
                ).returncode
            self.adding_programs = None
            msg = "Added" if rc == 0 else ("Cancelled" if rc in (126, 127) else "Could not finish")
            GLib.idle_add(self.note, "Programs", msg)

        self.adding_programs = threading.Thread(target=work, daemon=True)
        self.adding_programs.start()
        self.note("Programs", "Adding…")
