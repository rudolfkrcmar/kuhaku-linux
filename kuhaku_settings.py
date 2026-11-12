"""Kūhaku's Settings page: reading every preference for the page, writing one back, and updating the system.

A part of the Kuhaku window class (kuhaku.py), kept here by subject."""

import json
import os
import re
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
from kuhaku_config import CONFIG_FILE, HERE, HOME
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


class Settings(Home):
    # --- Kūhaku settings page --------------------------------------------
    def read_prefs(self):
        _, sinks_json, _ = run(["pactl", "-f", "json", "list", "sinks"])
        _, default_sink, _ = run(["pactl", "get-default-sink"])
        outputs = []
        try:
            for k in json.loads(sinks_json):
                if "Raw" in k.get("description", "") or k["name"] in ("mb81_speakers", "mb81_speakers_eq_out"):
                    continue
                outputs.append(
                    {
                        "id": k["name"],
                        "name": k.get("description", k["name"]).replace(" (EQ)", "").replace("Built-in Audio ", ""),
                        "active": k["name"] == default_sink.strip(),
                    }
                )
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
                    "DEBIAN_FRONTEND=noninteractive apt-get -y -q full-upgrade && apt-get -y -q autoremove",
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
