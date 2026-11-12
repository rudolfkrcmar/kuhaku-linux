#!/usr/bin/env python3
"""Kūhaku for the convertible: a touch keyboard.

Fold the screen all the way back and Kūhaku's own keyboard rises from the
bottom of the screen; open it into a laptop again and it goes away. Apps make
room for it (it reserves its height the way a panel does).

The keyboard: paper keys in light type, a large preview over each key,
hold a key for its accents and digits (Czech first), shift twice for caps,
two spaces for a full stop, drag along the space bar to move the cursor,
hold delete to keep deleting. "Write" turns the keys into a writing pad.

"Write" sends only the ink strokes to Google's handwriting service to read them.

Tablet mode comes from the "Intel HID switches" device; kuhaku-tablet-fix.sh
lets the signed-in person read it. `kuhaku-tablet.py toggle` shows or hides the
keyboard by hand (also: kill -USR1 <pid>).
"""

import os
import signal
import sys
import time
from pathlib import Path

import tablet_base  # noqa: F401  first: it starts Xlib's threads before GTK opens the display

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import GLib, Gtk  # noqa: E402
from kuhaku_log import report  # faults go to the log instead of vanishing
from tablet_base import MODE_FILE, PASSWORD_FRONT, PID_FILE, THEMES, current_theme, log, settings
from tablet_x11 import Hinge, PenWatcher, Props, TabletSwitch, Typer
from tablet_keyboard import Keyboard, Pill
from tablet_ink import Ink


# --- together ----------------------------------------------------------------------------


class App:
    def __init__(self):
        self.settings = settings()
        self.tablet = False
        self.asked_up = False
        self.last_pen = 0.0
        self.password_up = self.was_visible = False
        GLib.timeout_add(400, self.watch_password)
        self.typer = Typer()
        self.props = Props()
        self.keyboard = Keyboard(self)
        self.ink = Ink(self)
        self.pill = Pill(self)
        self.pen = PenWatcher(self.pen_event)
        self.switch = TabletSwitch(self.set_tablet)
        # Without the switch (not readable, or none), the hinge angle from the motion sensors.
        self.hinge = Hinge(self.set_tablet) if not self.switch.readable else None
        if os.environ.get("KT_DEBUG"):
            print(
                "switch",
                self.switch.path,
                self.switch.readable,
                "hinge",
                self.hinge and self.hinge.ok,
                file=sys.stderr,
                flush=True,
            )
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self.toggle_signal)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR2, self.tablet_signal)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.quit)
        # Other apps may ask for the keyboard (SIGWINCH) and put it away again (SIGHUP).
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGWINCH, self.show_signal)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGHUP, self.hide_signal)
        GLib.timeout_add_seconds(60, self.refresh_theme)
        self.write_mode()

    def pen_event(self, what):
        if what != "typed":
            self.last_pen = time.monotonic()
        if what == "typed":
            if self.hinge:
                self.hinge.typed()
            return False
        return False  # the pen is a pen: no writing anywhere (only the keyboard's Write pad)

    def refresh_theme(self):
        self.keyboard.theme = THEMES[current_theme()]
        self.keyboard.area.queue_draw()
        return True

    def toggle_signal(self):
        self.keyboard.toggle()
        return True

    def show_signal(self):
        if not self.keyboard.visible:
            self.keyboard.show()
            self.asked_up = True
        return True

    def hide_signal(self):
        # Only takes back a keyboard it was asked to bring up: the keyboard that came up with
        # the fold stays until it is folded back or put away by hand (no flicking on and off).
        if self.keyboard.visible and self.asked_up:
            self.keyboard.hide()
        self.asked_up = False
        return True

    def pen_recent(self, seconds):
        return time.monotonic() - self.last_pen < seconds

    def watch_password(self):
        """Kūhaku's lock or password window is up: it has keys of its own, so this keyboard steps away."""
        up = False
        try:
            pid = int(PASSWORD_FRONT.read_text())
            up = Path(f"/proc/{pid}").exists()
        except (OSError, ValueError):
            pass
        if up and not self.password_up:
            self.password_up, self.was_visible = True, self.keyboard.visible
            self.keyboard.hide()
            self.pill.hide()
            self.ink.cancel()
        elif not up and self.password_up:
            self.password_up = False
            if self.was_visible and self.tablet:
                self.keyboard.show()
            else:
                self.keyboard_changed()
        return True

    def write_mode(self):
        try:
            MODE_FILE.write_text("1" if self.tablet else "0")
        except OSError:
            pass

    def tablet_signal(self):
        self.set_tablet(not self.tablet)
        return True

    def set_tablet(self, on):
        if on == self.tablet:
            return False
        log("tablet" if on else "laptop")
        self.tablet = on
        self.asked_up = False
        self.ink.cancel()
        self.write_mode()
        if on:
            self.keyboard.show()
        else:
            self.keyboard.hide()
            self.pill.hide()
        return False

    def keyboard_changed(self):
        if self.keyboard.visible or self.password_up:
            self.pill.hide()
        elif self.tablet:
            self.pill.show()
        if self.ink.state != "off":
            self.ink.shape()

    def quit(self):
        self.tablet = False
        self.write_mode()
        self.keyboard.set_strut(False)
        self.typer.put("call", self.typer.release_borrowed)
        time.sleep(0.1)
        Gtk.main_quit()
        return False


def running_pid():
    try:
        pid = int(PID_FILE.read_text())
        if "kuhaku-tablet" in Path(f"/proc/{pid}/cmdline").read_text():
            return pid
    except Exception:
        report()
    return None


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    pid = running_pid()
    if cmd in ("toggle", "tablet", "show", "hide") and (pid or cmd != "show"):
        if pid:
            os.kill(
                pid,
                {"toggle": signal.SIGUSR1, "tablet": signal.SIGUSR2, "show": signal.SIGWINCH, "hide": signal.SIGHUP}[
                    cmd
                ],
            )
        return
    if pid and pid != os.getpid():
        print("kuhaku-tablet is already running", file=sys.stderr)
        return
    PID_FILE.write_text(str(os.getpid()))
    App()
    if cmd == "show":
        GLib.idle_add(lambda: os.kill(os.getpid(), signal.SIGUSR1) and False)
    Gtk.main()


if __name__ == "__main__":
    main()
