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
from gi.repository import Gdk, Gio, GLib, Gtk, Polkit, PolkitAgent

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import touchkeys
from kuhaku_log import report  # faults go to the log instead of vanishing

# Always dark, whatever the time of day: a password window that floods the screen white
# is startling, and the dark one reads the same morning or night.
COLORS = ("#121212", "#ececec", "#9a9a9a", "#f0705a")

# Plain words for what is being asked, where polkit's own message is technical.
FRIENDLY = {
    "org.freedesktop.policykit.exec": "Kūhaku needs your password to make a change to the system.",
    "org.freedesktop.packagekit": "Kūhaku needs your password to install or update software.",
    "org.freedesktop.NetworkManager": "Kūhaku needs your password to change the network.",
    "org.freedesktop.login1": "Kūhaku needs your password for this power action.",
}


def log(*a):
    try:
        import time

        with open(os.path.join(GLib.get_user_runtime_dir(), "kuhaku-polkit.log"), "a") as f:
            f.write(time.strftime("%H:%M:%S ") + " ".join(map(str, a)) + "\n")
    except Exception:
        report()


class Prompt:
    def __init__(self, done, action_id, message, details, cookie, identities):
        self.done, self.cookie = done, cookie
        me = os.getuid()
        self.identity = next(
            (i for i in identities if isinstance(i, Polkit.UnixUser) and i.get_uid() == me),
            identities[0] if identities else None,
        )
        bg, fg, muted, accent = COLORS
        css = Gtk.CssProvider()
        css.load_from_data(
            (
                f"""
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
        """
                + touchkeys.css(fg, muted, accent)
            ).encode()
        )
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

        why = Gtk.Label(
            label=next((v for k, v in FRIENDLY.items() if action_id.startswith(k)), message), name="why", xalign=0
        )
        why.set_line_wrap(True)
        why.set_margin_start(20)
        box.pack_start(why, False, False, 0)
        program = details.get("program") if details else None
        what = Gtk.Label(
            label=program or message if program or message != why.get_text() else action_id, name="what", xalign=0
        )
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

        # Folded into a tablet: keys of its own (this screen holds the keyboard and mouse).
        touchkeys.front(True)
        self.keys = touchkeys.TouchKeys(self.entry, self.submit)
        kw = min(1100, g.width - 40)
        self.keys.set_size_request(kw, -1)
        stage.put(self.keys, g.x + (g.width - kw) // 2, g.y + g.height - 300)

        w.connect("key-press-event", self.on_key)
        w.show_all()
        w.present()
        GLib.timeout_add(150, lambda: (self.grab(), False)[1])
        self.session = None
        self.retired = []
        password_only(False)
        self.start()

    def grab(self):
        self.win.present()
        self.entry.grab_focus()
        seat = Gdk.Display.get_default().get_default_seat()
        seat.grab(self.win.get_window(), Gdk.SeatCapabilities.KEYBOARD, False, None, None, None)

    def start(self):
        # A fresh PAM conversation. The previous session is kept alive until this one replaces it:
        # dropping it from inside its own "completed" signal frees it mid-emission and crashes.
        self.asked = False  # PAM has asked for the password
        self.typed = None  # Enter pressed before PAM asked: answer as soon as it does
        if self.session:
            self.retired.append(self.session)
        s = self.session = PolkitAgent.Session.new(self.identity, self.cookie)
        s.connect("request", self.on_request)
        s.connect("show-error", self.on_error)
        s.connect("show-info", self.on_info)
        log("session start")
        s.connect("completed", self.completed)
        s.initiate()

    def on_info(self, s, text):
        log("show-info", text)
        if s is self.session and "finger" in text.lower():
            self.fingering = True
            self.hint.set_text("Password, or touch the reader")

    def on_error(self, s, text):
        log("show-error", text)
        if s is not self.session:
            return
        if "finger" in text.lower():
            self.error.set_text("Not a known finger, try again")
        else:
            self.error.set_text(text)

    def on_request(self, s, prompt, echo):
        self.fingering = False
        self.hint.set_text("Password")
        log("request", repr(prompt), "echo", echo, "current", s is self.session, "queued", self.typed is not None)
        if s is not self.session:
            return
        self.entry.set_visibility(echo)
        if self.typed is not None:
            text, self.typed = self.typed, None
            s.response(text)
            return
        self.asked = True
        self.entry.set_sensitive(True)
        self.entry.grab_focus()

    def submit(self, entry):
        text = entry.get_text()
        log("submit", "len", len(text), "asked", self.asked)
        if not text or not self.session or getattr(self, "finished", False):
            return
        self.entry.set_sensitive(False)
        if self.asked:
            self.asked = False
            self.session.response(text)
        elif getattr(self, "fingering", False):
            # Waiting for a finger, but the password was typed: ask again, for the password only.
            self.fingering = False
            password_only(True)
            old = self.session
            self.start()
            self.typed = text
            old.cancel()
        else:
            self.typed = text

    def completed(self, s, gained):
        log("completed", gained, "current", s is self.session)
        if s is not self.session or getattr(self, "finished", False):
            return
        if gained:
            GLib.idle_add(lambda: (self.finish(True), False)[1])
            return

        def again():
            if getattr(self, "finished", False):
                return False
            self.entry.set_text("")
            self.entry.set_sensitive(True)
            self.error.set_text("Wrong password, try again")
            self.start()
            self.entry.grab_focus()
            return False

        GLib.idle_add(again)

    def on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            GLib.idle_add(lambda: (self.finish(False), False)[1])
            return True
        return False

    def cancel(self):
        self.finish(False)
        return False

    def finish(self, ok):
        log("finish", ok)
        if getattr(self, "finished", False):
            return
        self.finished = True
        password_only(False)
        Gdk.Display.get_default().get_default_seat().ungrab()
        touchkeys.front(False)
        self.win.destroy()
        self.done(ok)


# A finger works too: the system first waits for a known finger on the reader (pam_fprintd in
# /etc/pam.d/polkit-1). Typing the password instead and pressing Enter skips that wait: this
# file asks the system for the password only (/usr/local/lib/kuhaku/password-only checks it).
def password_only(on):
    path = os.path.join(GLib.get_user_runtime_dir(), "kuhaku-password-only")
    try:
        if on:
            open(path, "w").close()
        else:
            os.unlink(path)
    except OSError:
        pass


AGENT_PATH = "/org/freedesktop/PolicyKit1/AuthenticationAgent"
AGENT_XML = """<node><interface name="org.freedesktop.PolicyKit1.AuthenticationAgent">
  <method name="BeginAuthentication">
    <arg type="s" name="action_id" direction="in"/><arg type="s" name="message" direction="in"/>
    <arg type="s" name="icon_name" direction="in"/><arg type="a{ss}" name="details" direction="in"/>
    <arg type="s" name="cookie" direction="in"/><arg type="a(sa{sv})" name="identities" direction="in"/>
  </method>
  <method name="CancelAuthentication"><arg type="s" name="cookie" direction="in"/></method>
</interface></node>"""


class Agent:
    """polkit's authentication agent, spoken directly over D-Bus.

    (PolkitAgent.Listener subclassed from Python crashed whenever a question was declined: the
    async reply crosses the Python binding badly. Here each question is a D-Bus call we answer
    ourselves when the window closes; PolkitAgent.Session still runs the PAM conversation.
    polkitd grants only what its own helper verified, so the reply itself grants nothing.)"""

    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.current = None  # (cookie, invocation, prompt)
        node = Gio.DBusNodeInfo.new_for_xml(AGENT_XML)
        self.bus.register_object(AGENT_PATH, node.interfaces[0], self.on_call, None, None)
        session = Polkit.UnixSession.new_for_process_sync(os.getpid(), None)
        self.bus.call_sync(
            "org.freedesktop.PolicyKit1",
            "/org/freedesktop/PolicyKit1/Authority",
            "org.freedesktop.PolicyKit1.Authority",
            "RegisterAuthenticationAgent",
            GLib.Variant(
                "((sa{sv})ss)",
                (
                    ("unix-session", {"session-id": GLib.Variant("s", session.get_session_id())}),
                    os.environ.get("LANG", "C"),
                    AGENT_PATH,
                ),
            ),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        log("agent registered for session", session.get_session_id())

    def on_call(self, _bus, _sender, _path, _iface, method, params, invocation):
        if method == "BeginAuthentication":
            action_id, message, _icon, details, cookie, identities = params.unpack()
            if self.current:
                invocation.return_dbus_error("org.freedesktop.PolicyKit1.Error.Failed", "Another request is open")
                return
            ids = []
            for kind, props in identities:
                if kind == "unix-user" and "uid" in props:
                    ids.append(Polkit.UnixUser.new(props["uid"]))
                elif kind == "unix-group" and "gid" in props:
                    ids.append(Polkit.UnixGroup.new(props["gid"]))
            log("begin", action_id)

            def done(ok, cookie=cookie, invocation=invocation):
                if not self.current or self.current[0] != cookie:
                    return
                self.current = None
                log("reply", "ok" if ok else "declined")
                if ok:
                    invocation.return_value(None)
                else:
                    invocation.return_dbus_error("org.freedesktop.PolicyKit1.Error.Cancelled", "Declined")

            self.current = (cookie, invocation, None)
            prompt = Prompt(done, action_id, message, details, cookie, ids)
            if self.current and self.current[0] == cookie:
                self.current = (cookie, invocation, prompt)
        elif method == "CancelAuthentication":
            (cookie,) = params.unpack()
            log("cancel requested")
            cur = self.current
            invocation.return_value(None)
            if cur and cur[0] == cookie and cur[2]:
                GLib.idle_add(cur[2].cancel)


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    Gtk.init(None)
    agent = Agent()  # noqa: F841 (kept alive by the main loop)
    Gtk.main()


if __name__ == "__main__":
    main()
