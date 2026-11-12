#!/usr/bin/env python3
"""Teach Kūhaku your fingers: one quiet window, the red line, a dot for every touch.

Uses the system's fingerprint service (fprintd). Once a finger is known, the lock screen
opens with it as well as with the password. Several fingers make it much more reliable
(a wet, cold or sideways touch on one finger, the other hand…), so after each finger the
window offers the next one.

    kuhaku-finger.py              the next finger not yet known (right index first)
    kuhaku-finger.py FINGER       that finger (fprintd names, like left-index-finger)
    kuhaku-finger.py --list       the known fingers, as JSON
    kuhaku-finger.py --forget F   forget one finger (or "all")
"""
import json
import os
import pwd
import re
import subprocess
import sys
import threading
from pathlib import Path
from kuhaku_log import report  # faults go to the log instead of vanishing

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
USER = pwd.getpwuid(os.getuid()).pw_name
# The order fingers are offered in: the ones that reach the reader (right of the keyboard) easiest first.
ORDER = ["right-index-finger", "left-index-finger", "right-middle-finger", "right-thumb", "left-thumb",
         "left-middle-finger", "right-ring-finger", "left-ring-finger", "right-little-finger", "left-little-finger"]
NICE = {f: f.replace("-finger", "").replace("-", " ").replace("right ", "Right ").replace("left ", "Left ") for f in ORDER}


def reader():
    """(name, stages) of the fingerprint reader, or None."""
    try:
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        mgr = Gio.DBusProxy.new_sync(bus, 0, None, "net.reactivated.Fprint", "/net/reactivated/Fprint/Manager",
                                     "net.reactivated.Fprint.Manager", None)
        path = mgr.call_sync("GetDefaultDevice", None, 0, 3000, None).unpack()[0]
        dev = Gio.DBusProxy.new_sync(bus, 0, None, "net.reactivated.Fprint", path, "net.reactivated.Fprint.Device", None)
        name, stages = dev.get_cached_property("name"), dev.get_cached_property("num-enroll-stages")
        return (name.unpack() if name else "Fingerprint reader", stages.unpack() if stages else 0)
    except Exception:
        report()
        return None


def known():
    """The fingers fprintd knows for this person, in ORDER."""
    try:
        out = subprocess.run(["fprintd-list", USER], capture_output=True, text=True, timeout=8).stdout
    except Exception:
        report()
        return []
    got = set(re.findall(r"#\d+:\s*(\S+)", out))
    return [f for f in ORDER if f in got]


def next_finger():
    have = set(known())
    return next((f for f in ORDER if f not in have), None)


def forget(finger):
    argv = ["fprintd-delete", USER] + ([] if finger == "all" else ["-f", finger])
    try:
        return subprocess.run(argv, capture_output=True, timeout=15).returncode == 0
    except Exception:
        report()
        return False


class Window:
    def __init__(self, finger):
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, GLib, Gtk
        from kuhaku_unlock_style import window_css
        self.Gtk, self.GLib = Gtk, GLib
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), window_css(), Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self.stages = (reader() or ("", 0))[1] or 9
        self.win = Gtk.Window(title="Fingerprint")
        self.win.set_decorated(False)
        self.win.fullscreen()
        self.win.connect("destroy", self.quit)
        self.win.connect("key-press-event", lambda w, e: e.keyval == Gdk.KEY_Escape and w.destroy())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        box.set_size_request(min(900, Gdk.Screen.get_default().get_width() - 80), -1)
        self.which = Gtk.Label(label="", name="small", xalign=0)
        self.which.set_margin_start(20)
        box.pack_start(self.which, False, False, 0)
        row = Gtk.Box(spacing=18)
        line = Gtk.Box(name="line")
        line.set_size_request(2, 44)
        row.pack_start(line, False, False, 0)
        self.big = Gtk.Label(label="", xalign=0)
        row.pack_start(self.big, False, False, 0)
        box.pack_start(row, False, False, 0)
        self.dots = Gtk.Label(label="", name="dots", xalign=0)
        self.dots.set_margin_start(20)
        box.pack_start(self.dots, False, False, 0)
        self.small = Gtk.Label(label="", name="small", xalign=0)
        self.small.set_margin_start(20)
        self.small.set_line_wrap(True)
        box.pack_start(self.small, False, False, 0)
        self.buttons = Gtk.Box(spacing=34)
        self.buttons.set_margin_start(20)
        self.buttons.set_margin_top(26)
        box.pack_start(self.buttons, False, False, 0)
        self.win.add(box)
        self.win.show_all()
        self.proc = None
        self.start(finger)

    def say(self, big, small=None, passed=None):
        self.big.set_text(big)
        if small is not None:
            self.small.set_text(small)
        if passed is not None:
            self.dots.set_text("".join("●" if i < passed else "○" for i in range(self.stages)))
        return False

    def offer(self, choices):
        for c in self.buttons.get_children():
            c.destroy()
        for i, (label, fn) in enumerate(choices):
            b = self.Gtk.Button(label=label)
            if i == 0:
                b.get_style_context().add_class("primary")
            b.connect("clicked", lambda _b, f=fn: f())
            self.buttons.pack_start(b, False, False, 0)
        self.buttons.show_all()
        return False

    def start(self, finger):
        self.offer([])
        if not finger:
            self.which.set_text("")
            self.say("Every finger is known", "There is nothing more to add.", 0)
            self.offer([("Done", self.win.destroy)])
            return
        self.finger = finger
        n = len(known())
        self.which.set_text(f"{NICE.get(finger, finger)} · finger {n + 1}" if n else NICE.get(finger, finger))
        self.say("Touch the reader", "The power button. Lift and touch again after each dot, "
                 "a little differently each time: tip, middle, edges.", 0)
        threading.Thread(target=self.enroll, args=(finger,), daemon=True).start()

    def enroll(self, finger):
        GLib = self.GLib
        try:
            self.proc = subprocess.Popen(["fprintd-enroll", "-f", finger, USER], stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True)
        except FileNotFoundError:
            GLib.idle_add(self.say, "No fingerprint service", "Ask Claude to set up the fingerprint reader.")
            GLib.idle_add(self.offer, [("Close", self.win.destroy)])
            return
        passed, ended = 0, False
        for line in self.proc.stdout:
            if "enroll-stage-passed" in line:
                passed += 1
                GLib.idle_add(self.say, "Again", "Lift and touch again, a little differently.", passed)
            elif any(k in line for k in ("enroll-retry-scan", "enroll-swipe-too-short", "enroll-finger-not-centered",
                                          "enroll-remove-and-retry")):
                GLib.idle_add(self.say, "Once more", "Rest the finger flat and still, then lift it.", passed)
            elif "enroll-duplicate" in line:
                ended = True
                GLib.idle_add(self.say, "Already known", "That finger is already set up. Try another one.", 0)
                GLib.idle_add(self.offer, [("Another finger", lambda: self.start(next_finger())), ("Done", self.win.destroy)])
            elif "enroll-completed" in line:
                ended = True
                n = len(known())
                more = "One more finger makes it much more reliable." if n < 3 else "That is plenty. More is fine too."
                GLib.idle_add(self.say, "Done", f"{n} finger{'s' if n != 1 else ''} known. {more}", self.stages)
                GLib.idle_add(self.offer, [("Add another finger", lambda: self.start(next_finger())),
                                           ("Done", self.win.destroy)])
            elif any(k in line for k in ("enroll-failed", "enroll-disconnected", "enroll-data-full", "enroll-unknown-error")):
                ended = True
                GLib.idle_add(self.say, "That did not work", "The reader stopped. Try this finger again.", 0)
                GLib.idle_add(self.offer, [("Try again", lambda: self.start(finger)), ("Close", self.win.destroy)])
        rc = self.proc.wait()
        if not ended and rc != 0 and self.win:
            GLib.idle_add(self.say, "The reader is busy", "Close anything using it (or wait a moment) and try again.", 0)
            GLib.idle_add(self.offer, [("Try again", lambda: self.start(finger)), ("Close", self.win.destroy)])

    def quit(self, *_):
        self.win = None
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.Gtk.main_quit()


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--list":
        r = reader()
        print(json.dumps({"reader": r[0] if r else None, "fingers": known() if r else []}))
    elif arg == "--forget":
        f = sys.argv[2] if len(sys.argv) > 2 else ""
        sys.exit(0 if (f == "all" or f in ORDER) and forget(f) else 1)
    else:
        finger = arg if arg in ORDER else next_finger()
        Window(finger)
        from gi.repository import Gtk
        Gtk.main()
