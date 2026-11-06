#!/usr/bin/env python3
"""Kūhaku's installer: four quiet screens instead of an installer program.

Welcome · you (name and one password) · how freely Claude may work · the drive · progress · done. The work is
done by /usr/sbin/kuhaku-install-to-disk (as root, through sudo in the live system).
Files carried from an old computer sit on the stick's KUHAKU-CARRY partition,
encrypted with the same password the person types here.
"""
import json
import os
import re
import subprocess
import threading
import unicodedata
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GLib, Gtk, WebKit2

HERE = Path(__file__).resolve().parent
CARRY_MNT = Path("/run/kuhaku-carry")


def sh(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def find_disk():
    """The computer's own drive: the largest disk that is not the stick we started from."""
    out = sh(["lsblk", "-J", "-b", "-d", "-o", "NAME,SIZE,TYPE,RM,TRAN,MODEL"]).stdout
    disks = [d for d in json.loads(out or '{"blockdevices":[]}')["blockdevices"]
             if d.get("type") == "disk" and not d.get("rm") and d.get("tran") != "usb"
             and not d["name"].startswith(("loop", "sr", "zram"))]
    if not disks:
        return None
    d = max(disks, key=lambda x: int(x.get("size") or 0))
    return {"dev": "/dev/" + d["name"], "size": int(d.get("size") or 0), "model": (d.get("model") or "").strip()}


def find_carry():
    """Mount the stick's carry partition (if any) and read who it belongs to."""
    dev = sh(["blkid", "-L", "KUHAKU-CARRY"]).stdout.strip()
    if not dev:
        return None
    if not (CARRY_MNT / "carry.tar.gpg").exists():
        sh(["sudo", "-n", "mkdir", "-p", str(CARRY_MNT)])
        sh(["sudo", "-n", "mount", "-o", "ro", dev, str(CARRY_MNT)])
    meta = CARRY_MNT / "about.json"
    if not (CARRY_MNT / "carry.tar.gpg").exists():
        return None
    try:
        about = json.loads(meta.read_text())
    except Exception:
        about = {}
    return {"path": str(CARRY_MNT / "carry.tar.gpg"), "name": about.get("name", ""), "user": about.get("user", ""),
            "size": (CARRY_MNT / "carry.tar.gpg").stat().st_size}


def password_opens_carry(password):
    check = CARRY_MNT / "check.gpg"
    if not check.exists():
        return True
    r = subprocess.run(["gpg", "--batch", "--quiet", "--pinentry-mode", "loopback", "--passphrase-fd", "0",
                        "--homedir", "/tmp/kuhaku-gpg", "-d", str(check)],
                       input=password, capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "kuhaku"


def find_timezone():
    """Where the computer is: the live system's own zone if someone set it, otherwise the zone
    of this internet connection (one HTTPS question, nothing about the person is sent), else UTC."""
    def real(z):
        return bool(z) and ".." not in z and re.fullmatch(r"[A-Za-z0-9_+\-/]+", z) and Path("/usr/share/zoneinfo", z).is_file()
    zone = sh(["timedatectl", "show", "-p", "Timezone", "--value"]).stdout.strip()
    if real(zone) and zone not in ("UTC", "Etc/UTC"):
        return zone
    for url in ("https://ipapi.co/timezone", "https://ipinfo.io/timezone"):
        try:
            zone = sh(["curl", "-fsS", "--max-time", "4", url]).stdout.strip()
        except Exception:
            zone = ""
        if real(zone):
            return zone
    return "UTC"


def short_name(full):
    s = unicodedata.normalize("NFKD", full.split()[0] if full.split() else "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9-]", "", s)
    return s or "me"


class Installer:
    def __init__(self):
        os.makedirs("/tmp/kuhaku-gpg", mode=0o700, exist_ok=True)
        self.win = Gtk.Window(title="Install Kūhaku")
        self.win.set_decorated(False)
        self.win.fullscreen()
        self.win.connect("destroy", Gtk.main_quit)
        ucm = WebKit2.UserContentManager()
        ucm.register_script_message_handler("install")
        ucm.connect("script-message-received::install", self.on_message)
        self.view = WebKit2.WebView.new_with_user_content_manager(ucm)
        self.view.set_background_color(Gdk.RGBA(0.969, 0.957, 0.945, 1))
        self.view.connect("context-menu", lambda *a: True)
        self.win.add(self.view)
        self.view.load_uri((HERE / "installer.html").as_uri())
        self.view.connect("load-changed", lambda v, e: e == WebKit2.LoadEvent.FINISHED and self.start())
        self.win.show_all()
        self.disk = self.carry = None
        self.zone = "UTC"

    def js(self, fn, data=None):
        self.view.evaluate_javascript(f"{fn}({json.dumps(data)})", -1, None, None, None, None, None)

    def start(self):
        def work():
            self.disk, self.carry = find_disk(), find_carry()
            self.zone = find_timezone()
            GLib.idle_add(self.js, "facts", {"disk": self.disk, "carry": self.carry, "zone": self.zone})
        threading.Thread(target=work, daemon=True).start()

    def on_message(self, _m, result):
        msg = json.loads(result.get_js_value().to_string())
        k = msg.get("type")
        if k == "close":
            self.win.destroy()
        elif k == "check":
            ok = len(msg.get("password", "")) >= 6
            why = "" if ok else "At least six characters."
            if ok and self.carry and not password_opens_carry(msg["password"]):
                ok, why = False, "Use the password you chose on your old system, so your things can come along."
            self.js("checked", {"ok": ok, "why": why, "user": short_name(msg.get("name", ""))})
        elif k == "install":
            threading.Thread(target=self.install, args=(msg,), daemon=True).start()
        elif k == "restart":
            subprocess.Popen(["systemctl", "reboot"])

    def install(self, msg):
        try:
            self._install(msg)
        except Exception as e:                      # never leave the screen breathing with nothing behind it
            GLib.idle_add(self.js, "failed", f"The installer stopped: {e}")

    def _install(self, msg):
        user = (self.carry or {}).get("user") or short_name(msg["name"])
        mode = msg.get("mode") if msg.get("mode") in ("bypassPermissions", "acceptEdits") else "bypassPermissions"
        argv = ["sudo", "-n", "/usr/sbin/kuhaku-install-to-disk", self.disk["dev"], user,
                msg["name"], mode, self.zone] + ([self.carry["path"]] if self.carry else [])
        log = open("/tmp/kuhaku-install.log", "w")
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        p.stdin.write(msg["password"] + "\n")
        p.stdin.close()
        failed = False
        for line in p.stdout:
            log.write(line); log.flush()
            line = line.rstrip("\n")
            if line.startswith("STEP "):
                n, text = line[5:].strip().split(" ", 1)
                GLib.idle_add(self.js, "step", {"n": int(n), "text": text})
            elif line.startswith("PCT "):
                try:
                    GLib.idle_add(self.js, "pct", float(line[4:]))
                except ValueError:
                    pass
                continue
            elif line.startswith("FAIL"):
                failed = True
                GLib.idle_add(self.js, "failed", line[5:].strip())
            GLib.idle_add(self.js, "detail", line)
        rc = p.wait()
        if rc == 0:
            GLib.idle_add(self.js, "done")
        elif not failed:
            GLib.idle_add(self.js, "failed", f"The installer stopped ({rc}). Nothing is lost on the stick; try again.")

if __name__ == "__main__":
    Installer()
    Gtk.main()
