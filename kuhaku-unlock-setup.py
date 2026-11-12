#!/usr/bin/env python3
"""Set up unlocking, one quiet step at a time: fingers first (three is reliable), then the face.

Each step opens the window that does the work (kuhaku-finger.py, kuhaku-face.py enroll) and
comes back here when it closes. Opened from Settings → Unlock, the Apps page ("Unlock"),
or by asking Claude.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from kuhaku_unlock_style import window_css  # noqa: E402
from kuhaku_log import report  # faults go to the log instead of vanishing

WANT = 3


def fingers():
    try:
        out = subprocess.run([sys.executable, str(HERE / "kuhaku-finger.py"), "--list"],
                             capture_output=True, text=True, timeout=15).stdout
        d = json.loads(out or "{}")
        return d.get("reader"), d.get("fingers") or []
    except Exception:
        report()
        return None, []


def face():
    try:
        out = subprocess.run([sys.executable, str(HERE / "kuhaku-face.py"), "status"],
                             capture_output=True, text=True, timeout=15).stdout
        return json.loads(out or "{}")
    except Exception:
        report()
        return {}


class Setup:
    def __init__(self):
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), window_css(),
                                                 Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self.win = Gtk.Window(title="Unlock")
        self.win.set_decorated(False)
        self.win.fullscreen()
        self.win.connect("destroy", Gtk.main_quit)
        self.win.connect("key-press-event", lambda w, e: e.keyval == Gdk.KEY_Escape and w.destroy())
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                           valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.box.set_size_request(760, -1)
        self.win.add(self.box)
        self.win.show_all()
        self.intro()

    # --- one screen: a line, a sentence, a few quiet buttons -------------------------------
    def screen(self, step, title, text, buttons):
        for c in self.box.get_children():
            self.box.remove(c)
        if step:
            s = Gtk.Label(label=step.upper(), name="small", xalign=0)
            self.box.pack_start(s, False, False, 0)
        row = Gtk.Box(spacing=18)
        line = Gtk.Box(name="line")
        line.set_size_request(2, 44)
        row.pack_start(line, False, False, 0)
        row.pack_start(Gtk.Label(label=title, xalign=0, wrap=True), False, False, 0)
        self.box.pack_start(row, False, False, 0)
        t = Gtk.Label(label=text, name="small", xalign=0, wrap=True)
        t.set_max_width_chars(70)
        t.set_margin_start(20)
        self.box.pack_start(t, False, False, 0)
        bar = Gtk.Box(spacing=32)
        bar.set_margin_start(20)
        bar.set_margin_top(18)
        for i, (label, fn) in enumerate(buttons):
            b = Gtk.Button(label=label)
            if i == 0:
                b.get_style_context().add_class("primary")
            b.connect("clicked", lambda _b, f=fn: f())
            bar.pack_start(b, False, False, 0)
        self.box.pack_start(bar, False, False, 0)
        self.box.show_all()

    def run(self, argv, then):
        """Step aside while another window does its work, then come back."""
        self.win.hide()

        def wait():
            p = subprocess.Popen([sys.executable] + argv)

            def check():
                if p.poll() is None:
                    return True
                self.win.show_all()
                self.win.fullscreen()
                then()
                return False
            GLib.timeout_add(500, check)
            return False
        GLib.idle_add(wait)

    # --- the steps ------------------------------------------------------------------------
    def intro(self):
        self.screen("Unlock", "Open this computer with a touch or a look.",
                    "First a few fingers on the reader in the power button, then your face with the infrared "
                    "camera above the screen. About three minutes. Your password always works too, and it is "
                    "still the one asked when the computer starts.",
                    [("Begin", self.fingers), ("Not now", self.win.destroy)])

    def fingers(self):
        reader, known = fingers()
        if not reader:
            self.screen("Step 1 of 2 · Fingers", "No fingerprint reader answered.",
                        "Ask Claude to look at the fingerprint service. You can still set up your face.",
                        [("Go on to the face", self.face), ("Close", self.win.destroy)])
            return
        n = len(known)
        if n >= WANT:
            self.screen("Step 1 of 2 · Fingers", f"{n} fingers known.",
                        "That is reliable: if one finger is dry or at an angle, another works.",
                        [("Go on to the face", self.face), ("Add another finger", self.teach)])
            return
        left = WANT - n
        text = ("Touch the power button gently, lift, touch again, about nine times per finger, a little "
                "differently each time. Don't press it down." if n == 0 else
                f"{n} known. {left} more make{'s' if left == 1 else ''} it reliable: a dry or tilted finger still works.")
        self.screen("Step 1 of 2 · Fingers", "Teach a finger." if n == 0 else "Another finger.", text,
                    [("Teach a finger", self.teach), ("Skip to the face", self.face)])

    def teach(self):
        self.run([str(HERE / "kuhaku-finger.py")], self.fingers)

    def face(self):
        f = face()
        if not f.get("camera") or not f.get("ready"):
            self.screen("Step 2 of 2 · Face", "The infrared camera did not answer.",
                        "Slide the little cover above the screen open, then try again.",
                        [("Try again", self.face), ("Finish without face", self.done)])
            return
        if f.get("enrolled"):
            self.screen("Step 2 of 2 · Face", "Your face is known.",
                        "Learn it again if you change glasses or it misses you often.",
                        [("Finish", self.done), ("Learn again", self.learn_face)])
            return
        self.screen("Step 2 of 2 · Face", "Now your face.",
                    "Slide the camera cover open, sit as you usually do and look at the screen. Turn your head a "
                    "little when asked. Only measurements are kept, never pictures, and the camera is on only "
                    "while the lock screen is up. A convenience: your password and fingers are stronger.",
                    [("Set up face", self.learn_face), ("Skip", self.done)])

    def learn_face(self):
        self.run([str(HERE / "kuhaku-face.py"), "enroll"], self.after_face)

    def after_face(self):
        if face().get("enrolled"):
            self.done()
        else:
            self.screen("Step 2 of 2 · Face", "The face was not learned.",
                        "More light on your face, or the camera cover open, usually helps.",
                        [("Try again", self.learn_face), ("Finish without face", self.done)])

    def done(self):
        _, known = fingers()
        has_face = bool(face().get("enrolled"))
        ways = ["your password"] + ([f"{len(known)} finger{'s' if len(known) != 1 else ''}"] if known else []) \
            + (["your face"] if has_face else [])
        self.screen("Done", "Unlocking is set up.",
                    "The lock screen now opens with " + ", ".join(ways[:-1]) + (" or " if len(ways) > 1 else "")
                    + ways[-1] + ". Change it any time in Settings, under Unlock.",
                    [("Close", self.win.destroy)])


if __name__ == "__main__":
    Setup()
    Gtk.main()
