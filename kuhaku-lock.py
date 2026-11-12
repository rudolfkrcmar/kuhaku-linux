#!/usr/bin/env python3
"""Kūhaku's lock screen: the home screen's red line and "Password", nothing else.

It replaces light-locker, which hands the screen over to the login screen on another
display and showed a large padlock ("This session is locked") whenever that handover
failed, with no way out. This lock lives inside the session itself: one window above
everything, holding the keyboard and mouse, until your password is typed. There is
no handover, so nothing can get stuck between two screens. Should this program ever
crash, the window goes with it and the session is simply open again.

Run without arguments it waits: it locks before the computer sleeps (holding sleep
back until the lock is on screen) and whenever the session is asked to lock
(`loginctl lock-session`, the menu's Lock). `kuhaku-lock.py lock` locks now.
"""
import os
import pwd
import shutil
import signal
import subprocess
import sys
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import touchkeys  # noqa: E402
try:
    from terminal import theme
except Exception:
    theme = lambda: "night"  # noqa: E731

COLORS = {"night": ("#121212", "#ececec", "#9a9a9a", "#f0705a"), "day": ("#f7f4f1", "#111111", "#666666", "#e0301e")}
USER = pwd.getpwuid(os.getuid()).pw_name
LOGIN1 = "org.freedesktop.login1"


def password_ok(password):
    """The same check the system's own lock screens use (pam_unix's helper), for this user only."""
    try:
        p = subprocess.run(["/usr/sbin/unix_chkpwd", USER, "nullok"], input=password.encode() + b"\0",
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        return p.returncode == 0
    except Exception:
        return False


def finger_enrolled():
    """A fingerprint reader with at least one of this person's fingers on it (fprintd)."""
    if not shutil.which("fprintd-verify") or not shutil.which("fprintd-list"):
        return False
    try:
        out = subprocess.run(["fprintd-list", USER], capture_output=True, text=True, timeout=5).stdout
        return " - #" in out
    except Exception:
        return False


class Finger:
    """Waits for a known finger on the reader while the lock is up; the password always works too.
    The reader is asked again after every miss (and after a short rest if it is busy or just woke)."""

    def __init__(self, on_match, on_miss):
        self.on_match, self.on_miss = on_match, on_miss
        self.proc = None
        self.stopped = False
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        import time
        # Keeps asking as long as the lock is up: after a sleep the reader needs a moment to come back.
        while not self.stopped:
            try:
                self.proc = subprocess.Popen(["fprintd-verify", USER], stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, text=True)
                out = self.proc.communicate()[0]
            except Exception:
                return
            if self.stopped:
                return
            if "verify-match" in out:
                GLib.idle_add(self.on_match)
                return
            if "verify-no-match" in out:
                GLib.idle_add(self.on_miss)
            else:
                time.sleep(2)                 # busy, unplugged or still waking: rest a moment

    def stop(self):
        self.stopped = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


FACE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kuhaku-face.py")


def face_enrolled():
    return os.path.exists(os.path.join(os.path.dirname(FACE), "face", "embeddings.npy"))


class Face:
    """Looks for the enrolled face for a few seconds, in its own process (the camera closes with it).
    look() again (a key, the mouse, waking from sleep) when it has given up."""

    def __init__(self, on_match):
        self.on_match = on_match
        self.proc = None
        self.stopped = False
        self.look()

    def look(self):
        if self.stopped or (self.proc and self.proc.poll() is None):
            return
        try:
            self.proc = subprocess.Popen(["python3", FACE, "verify", "10"], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
        except Exception:
            return
        proc = self.proc
        threading.Thread(target=lambda: proc.wait() == 0 and not self.stopped and GLib.idle_add(self.on_match),
                         daemon=True).start()

    def stop(self):
        self.stopped = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


class Lock:
    def __init__(self, on_shown=None):
        self.on_shown = on_shown
        bg, fg, muted, accent = COLORS["night" if theme() == "night" else "day"]
        self.css = Gtk.CssProvider()
        self.css.load_from_data((f"""
            #lock {{ background: {bg}; }}
            #lock * {{ font-family: "Inter", sans-serif; }}
            #lock #line {{ background: {accent}; min-width: 2px; }}
            #lock entry {{ background: transparent; border: none; box-shadow: none; border-radius: 0; padding: 0;
                     color: {fg}; caret-color: {muted}; font-size: 26px; font-weight: 200; letter-spacing: 2px; min-height: 36px; }}
            #lock entry selection {{ background: alpha({fg}, .15); }}
            #lock #hint {{ color: alpha({fg}, .35); font-size: 26px; font-weight: 200; }}
            #lock #error {{ color: {accent}; font-size: 13px; font-weight: 300; }}
        """ + touchkeys.css(fg, muted, accent)).encode())
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(screen, self.css, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        # A popup window: no window manager in between, above every app, the bar and the dock.
        w = self.win = Gtk.Window(type=Gtk.WindowType.POPUP, name="lock")
        w.move(0, 0)
        w.resize(screen.get_width(), screen.get_height())
        w.set_can_focus(True)

        mon = Gdk.Display.get_default().get_primary_monitor() or Gdk.Display.get_default().get_monitor(0)
        g = mon.get_geometry()
        col = min(1160, g.width - 48)
        stage = Gtk.Fixed()
        w.add(stage)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_size_request(col, -1)
        stage.put(box, g.x + (g.width - col) // 2, g.y + int(g.height * 0.45))

        row = Gtk.Box(spacing=18)
        line = Gtk.Box(name="line")
        line.set_size_request(2, 36)
        line.set_valign(Gtk.Align.CENTER)
        row.pack_start(line, False, False, 0)
        over = Gtk.Overlay()
        self.entry = Gtk.Entry(visibility=False, invisible_char="•", has_frame=False)
        self.entry.set_hexpand(True)
        self.entry.connect("activate", self.submit)
        self.entry.connect("changed", lambda e: (self.hint.set_visible(not e.get_text()), self.error.set_text("") if e.get_text() else None))
        over.add(self.entry)
        self.finger = None
        self.face = None
        ways = ["Password"] + (["finger"] if finger_enrolled() else []) + (["face"] if face_enrolled() else [])
        hint = ways[0] if len(ways) == 1 else ", ".join(ways[:-1]) + " or " + ways[-1]
        self.hint = Gtk.Label(label=hint, name="hint", xalign=0)
        self.hint.set_halign(Gtk.Align.START)
        over.add_overlay(self.hint)
        over.set_overlay_pass_through(self.hint, True)
        row.pack_start(over, True, True, 0)
        box.pack_start(row, False, False, 0)

        self.error = Gtk.Label(label="", name="error", xalign=0)
        self.error.set_margin_start(20)
        self.error.set_margin_top(14)
        box.pack_start(self.error, False, False, 0)

        # Folded into a tablet: keys of its own (this screen holds the keyboard and mouse).
        touchkeys.front(True)
        self.keys = touchkeys.TouchKeys(self.entry, self.submit)
        kw = min(1100, g.width - 40)
        self.keys.set_size_request(kw, -1)
        stage.put(self.keys, g.x + (g.width - kw) // 2, g.y + g.height - 300)

        w.connect("key-press-event", self.on_key)
        w.connect("map-event", lambda *_: GLib.timeout_add(120, self.shown))
        w.show_all()
        self.grabbed = False
        GLib.timeout_add(100, self.grab)
        if "face" in hint:
            self.face = Face(self.close)
            w.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
            w.connect("button-press-event", lambda *_: self.wake())
            w.connect("motion-notify-event", lambda *_: self.wake())
        if "finger" in hint:
            self.finger = Finger(self.close, lambda: (self.win and self.error.set_text("Not this finger"), False)[1])

    def shown(self):
        if self.on_shown:
            self.on_shown()
            self.on_shown = None
        return False

    def grab(self):
        """Hold the keyboard and mouse; another app may hold them a moment (an open menu), so keep trying."""
        if not self.win.get_window():
            return False
        self.win.get_window().raise_()
        seat = Gdk.Display.get_default().get_default_seat()
        if seat.grab(self.win.get_window(), Gdk.SeatCapabilities.ALL, True, None, None, None) == Gdk.GrabStatus.SUCCESS:
            self.grabbed = True
            self.entry.grab_focus()
            # Stay on top of anything that appears later (a notice, a new window).
            GLib.timeout_add(1000, self.keep_on_top)
            return False
        return True

    def keep_on_top(self):
        if not self.win.get_window():
            return False
        self.win.get_window().raise_()
        return True

    def wake(self):
        """Someone is here (a key, the mouse, back from sleep): look for the face again if it gave up."""
        if self.face:
            self.face.look()
        return False

    def on_key(self, _w, ev):
        self.wake()
        if ev.keyval == Gdk.KEY_Escape:
            self.entry.set_text("")
            return True
        return False

    def submit(self, entry):
        text = entry.get_text()
        if not text:
            return
        entry.set_sensitive(False)

        def check():
            ok = password_ok(text)
            GLib.idle_add(self.checked, ok)
        threading.Thread(target=check, daemon=True).start()

    def checked(self, ok):
        if ok:
            self.close()
            return False
        self.entry.set_text("")
        self.entry.set_sensitive(True)
        self.entry.grab_focus()
        self.error.set_text("Wrong password")
        return False

    def close(self):
        if not self.win:
            return False
        if self.finger:
            self.finger.stop()
        if self.face:
            self.face.stop()
        Gdk.Display.get_default().get_default_seat().ungrab()
        touchkeys.front(False)
        Gtk.StyleContext.remove_provider_for_screen(Gdk.Screen.get_default(), self.css)
        self.win.destroy()
        self.win = None


class Watcher:
    """Locks before sleep and when the session is asked to lock."""

    def __init__(self):
        self.lock = None
        self.inhibit_fd = None
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.bus.signal_subscribe(LOGIN1, f"{LOGIN1}.Manager", "PrepareForSleep", "/org/freedesktop/login1",
                                  None, Gio.DBusSignalFlags.NONE, self.on_sleep)
        session = self.session_path()
        for name in ("Lock", "Unlock"):
            self.bus.signal_subscribe(LOGIN1, f"{LOGIN1}.Session", name, session,
                                      None, Gio.DBusSignalFlags.NONE, self.on_session)
        self.inhibit()

    def session_path(self):
        sid = os.environ.get("XDG_SESSION_ID")
        call = ("GetSession", GLib.Variant("(s)", (sid,))) if sid else ("GetSessionByPID", GLib.Variant("(u)", (os.getpid(),)))
        r = self.bus.call_sync(LOGIN1, "/org/freedesktop/login1", f"{LOGIN1}.Manager", call[0], call[1],
                               GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, -1, None)
        return r.unpack()[0]

    def inhibit(self):
        """Ask the system to wait (briefly) before sleeping, so the lock is on screen first."""
        if self.inhibit_fd is not None:
            return
        try:
            r, fds = self.bus.call_with_unix_fd_list_sync(
                LOGIN1, "/org/freedesktop/login1", f"{LOGIN1}.Manager", "Inhibit",
                GLib.Variant("(ssss)", ("sleep", "Kūhaku", "Lock the screen first", "delay")),
                GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, -1, None, None)
            self.inhibit_fd = fds.get(r.unpack()[0])
        except Exception:
            self.inhibit_fd = None

    def release(self):
        if self.inhibit_fd is not None:
            os.close(self.inhibit_fd)
            self.inhibit_fd = None

    def on_sleep(self, *args):
        going = args[5].unpack()[0]
        if going:
            self.show(on_shown=self.release)
        else:
            self.inhibit()
            if self.lock and self.lock.win:          # back from sleep: look for the face again
                GLib.timeout_add(1500, self.lock.wake)

    def on_session(self, *args):
        if args[4] == "Lock":
            self.show()

    def show(self, on_shown=None):
        if self.lock and self.lock.win:
            if on_shown:
                on_shown()
            return
        self.lock = Lock(on_shown=on_shown)
        # Never hold sleep back for long, whatever happens.
        if on_shown:
            GLib.timeout_add(1500, lambda: (self.release(), False)[1])


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    if sys.argv[1:] == ["lock"]:
        # Ask the session to lock; the watcher shows the screen. Without a watcher, lock here.
        if subprocess.run(["pgrep", "-f", "kuhaku-lock.py$"], stdout=subprocess.DEVNULL).returncode == 0:
            subprocess.run(["loginctl", "lock-session"])
            return
        Gtk.init(None)
        lock = Lock()
        GLib.timeout_add(500, lambda: Gtk.main_quit() if not lock.win else True)
        Gtk.main()
        return
    # One watcher per session: a second one (a second start of the session script) waits here
    # instead of locking the screen twice.
    import fcntl
    hold = open(os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "kuhaku-lock.watch"), "w")
    fcntl.flock(hold, fcntl.LOCK_EX)
    Gtk.init(None)
    Watcher()
    Gtk.main()


if __name__ == "__main__":
    main()
