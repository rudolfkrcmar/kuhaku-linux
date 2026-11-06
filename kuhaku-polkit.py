#!/usr/bin/env python3
"""Kūhaku's password window: when something needs your permission to change the system.

The home screen's language: the screen quietly dims, one line says what wants permission,
the red line waits for your password ("Password" until you type, then dots). Enter gives
permission, Escape declines. A wrong password says so under the line.

It is the session's polkit authentication agent (it replaces MATE's dialog). If it ever
cannot start, kuhaku-session falls back to MATE's agent, so permission can always be given.
"""
import os
import signal
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Polkit", "1.0")
gi.require_version("PolkitAgent", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Polkit, PolkitAgent  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from terminal import theme
except Exception:
    theme = lambda: "night"  # noqa: E731

COLORS = {"night": ("#121212", "#ececec", "#9a9a9a", "#f0705a"), "day": ("#f7f4f1", "#111111", "#666666", "#e0301e")}

# Plain words for what is being asked, where polkit's own message is technical.
FRIENDLY = {
    "org.freedesktop.policykit.exec": "Kūhaku needs your password to make a change to the system.",
    "org.freedesktop.packagekit": "Kūhaku needs your password to install or update software.",
    "org.freedesktop.NetworkManager": "Kūhaku needs your password to change the network.",
    "org.freedesktop.login1": "Kūhaku needs your password for this power action.",
}


class Prompt:
    def __init__(self, done, action_id, message, details, cookie, identities):
        self.done, self.cookie = done, cookie
        me = os.getuid()
        self.identity = next((i for i in identities if isinstance(i, Polkit.UnixUser) and i.get_uid() == me),
                             identities[0] if identities else None)
        bg, fg, muted, accent = COLORS["night" if theme() == "night" else "day"]
        css = Gtk.CssProvider()
        css.load_from_data(f"""
            window {{ background: {bg}; }}
            * {{ font-family: "Inter", sans-serif; }}
            #why {{ color: {fg}; font-size: 17px; font-weight: 300; }}
            #what {{ color: {muted}; font-size: 13px; font-weight: 300; }}
            #line {{ background: {accent}; min-width: 2px; }}
            entry {{ background: transparent; border: none; box-shadow: none; padding: 0; color: {fg};
                     caret-color: {muted}; font-size: 26px; font-weight: 200; letter-spacing: 2px; min-height: 36px; }}
            #hint {{ color: alpha({fg}, .35); font-size: 26px; font-weight: 200; }}
            #error {{ color: {accent}; font-size: 13px; font-weight: 300; }}
            #keys {{ color: {muted}; font-size: 12px; font-weight: 300; }}
        """.encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        w = self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        w.set_decorated(False)
        w.set_keep_above(True)
        w.set_skip_taskbar_hint(True)
        mon = Gdk.Display.get_default().get_primary_monitor() or Gdk.Display.get_default().get_monitor(0)
        g = mon.get_geometry()
        w.move(g.x, g.y)
        w.set_default_size(g.width, g.height)

        col = min(1160, g.width - 48)
        stage = Gtk.Fixed()
        w.add(stage)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_size_request(col, -1)
        stage.put(box, (g.width - col) // 2, int(g.height * 0.45) - 70)

        why = Gtk.Label(label=next((v for k, v in FRIENDLY.items() if action_id.startswith(k)), message), name="why", xalign=0)
        why.set_line_wrap(True)
        why.set_margin_start(20)
        box.pack_start(why, False, False, 0)
        program = details.lookup("program") if details else None
        what = Gtk.Label(label=program or message if program or message != why.get_text() else action_id, name="what", xalign=0)
        what.set_ellipsize(3)
        what.set_margin_start(20)
        what.set_margin_top(6)
        what.set_margin_bottom(28)
        box.pack_start(what, False, False, 0)

        row = Gtk.Box(spacing=18)
        line = Gtk.Box(name="line")
        line.set_size_request(2, 36)
        line.set_valign(Gtk.Align.CENTER)
        row.pack_start(line, False, False, 0)
        over = Gtk.Overlay()
        self.entry = Gtk.Entry(visibility=False, invisible_char="•", has_frame=False)
        self.entry.set_hexpand(True)
        self.entry.connect("activate", self.submit)
        self.entry.connect("changed", lambda e: self.hint.set_visible(not e.get_text()))
        over.add(self.entry)
        self.hint = Gtk.Label(label="Password", name="hint", xalign=0)
        self.hint.set_halign(Gtk.Align.START)
        over.add_overlay(self.hint)
        over.set_overlay_pass_through(self.hint, True)
        row.pack_start(over, True, True, 0)
        box.pack_start(row, False, False, 0)

        self.error = Gtk.Label(label="", name="error", xalign=0)
        self.error.set_margin_start(20)
        self.error.set_margin_top(14)
        box.pack_start(self.error, False, False, 0)
        keys = Gtk.Label(label="Enter to allow  ·  Esc to decline", name="keys", xalign=0)
        keys.set_margin_start(20)
        keys.set_margin_top(30)
        box.pack_start(keys, False, False, 0)

        w.connect("key-press-event", self.on_key)
        w.show_all()
        w.present()
        GLib.timeout_add(150, lambda: (self.grab(), False)[1])
        self.session = None
        self.start()

    def grab(self):
        self.win.present()
        self.entry.grab_focus()
        seat = Gdk.Display.get_default().get_default_seat()
        seat.grab(self.win.get_window(), Gdk.SeatCapabilities.KEYBOARD, False, None, None, None)

    def start(self):
        s = self.session = PolkitAgent.Session.new(self.identity, self.cookie)
        s.connect("request", lambda _s, prompt, echo: (self.entry.set_sensitive(True), self.entry.set_visibility(echo), self.entry.grab_focus()))
        s.connect("show-error", lambda _s, text: self.error.set_text(text))
        s.connect("completed", self.completed)
        s.initiate()

    def submit(self, entry):
        if self.session:
            self.entry.set_sensitive(False)
            self.session.response(entry.get_text())

    def completed(self, _s, gained):
        if gained:
            self.finish(True)
            return
        self.entry.set_text("")
        self.entry.set_sensitive(True)
        self.error.set_text("Wrong password")
        self.start()
        self.entry.grab_focus()

    def on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            if self.session:
                self.session.cancel()
            self.finish(False)
            return True
        return False

    def finish(self, ok):
        Gdk.Display.get_default().get_default_seat().ungrab()
        self.win.destroy()
        self.done(ok)


class Listener(PolkitAgent.Listener):
    """Answers polkit while the question is still open: the window runs in a nested loop and the
    reply goes back before this call returns (a reply sent later, from Python, crashes)."""
    busy = False

    def do_initiate_authentication(self, action_id, message, icon_name, details, cookie, identities, cancellable, callback, *user_data):
        task = Gio.Task.new(self, None, None, None)
        if self.busy:
            task.return_error(GLib.Error.new_literal(Polkit.error_quark(), "Busy", Polkit.Error.FAILED))
        else:
            self.busy = True
            result = {}
            loop = GLib.MainLoop()

            def done(ok):
                result["ok"] = ok
                loop.quit()
            Prompt(done, action_id, message, details, cookie, identities)
            loop.run()
            self.busy = False
            if result.get("ok"):
                task.return_boolean(True)
            else:
                task.return_error(GLib.Error.new_literal(Polkit.error_quark(), "Declined", Polkit.Error.CANCELLED))
        callback(self, task, *user_data)

    def do_initiate_authentication_finish(self, result):
        return result.propagate_boolean()


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    Gtk.init(None)
    listener = Listener()
    subject = Polkit.UnixSession.new_for_process_sync(os.getpid(), None)
    listener.handle = PolkitAgent.register_listener(listener, subject, "/org/freedesktop/PolicyKit1/AuthenticationAgent")
    Gtk.main()


if __name__ == "__main__":
    main()
