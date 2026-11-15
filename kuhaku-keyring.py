#!/usr/bin/env python3
"""Kūhaku's keyring window: when your saved passwords (the keyring) need your password.

Kūhaku logs in by itself, so the login keyring stays locked until something needs a saved
password. GNOME's prompter (gcr-prompter) then opened a grey dialog; this takes its place
on the session bus (org.gnome.keyring.SystemPrompter) and asks in the home screen's
language, like the polkit window: the screen dims, one line says why, the red line waits.
Enter unlocks, Escape cancels.

If this is not running, D-Bus starts GNOME's prompter instead, so a keyring can always be
opened. The password reaches gnome-keyring encrypted (keyring_exchange.py), as from GNOME's.
"""

import os
import signal
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keyring_exchange
import touchkeys
from kuhaku_log import report  # faults go to the log instead of vanishing

COLORS = ("#121212", "#ececec", "#9a9a9a", "#f0705a")  # always dark, like every password screen

NAME = "org.gnome.keyring.SystemPrompter"
PATH = "/org/gnome/keyring/Prompter"
PROMPTER = "org.gnome.keyring.internal.Prompter"
CALLBACK = "org.gnome.keyring.internal.Prompter.Callback"
XML = f"""<node><interface name="{PROMPTER}">
  <method name="BeginPrompting"><arg type="o" name="callback" direction="in"/></method>
  <method name="PerformPrompt">
    <arg type="o" name="callback" direction="in"/><arg type="s" name="type" direction="in"/>
    <arg type="a{{sv}}" name="properties" direction="in"/><arg type="s" name="exchange" direction="in"/>
  </method>
  <method name="StopPrompting"><arg type="o" name="callback" direction="in"/></method>
</interface></node>"""

# Plain words where gnome-keyring's are technical.
FRIENDLY = (
    ("did not get unlocked when you logged in", "Your saved passwords are locked. Your password opens them."),
    ("wants access to the keyring", "An app wants your saved passwords. Your password opens them."),
    ("create a new keyring", "Choose a password to keep these saved passwords safe."),
    ("change the password", "Choose a new password for your saved passwords."),
)


def plain(text):
    return (text or "").replace("_", "")


class Window:
    """The dimmed screen for one prompt; reused when the keyring asks again (a wrong password)."""

    hint: Gtk.Label

    def __init__(self, answer):
        self.answer = answer  # answer(reply, password_or_None)
        self.kind, self.props, self.first = "password", {}, None
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

        self.why = Gtk.Label(name="why", xalign=0)
        self.why.set_line_wrap(True)
        self.why.set_margin_start(20)
        box.pack_start(self.why, False, False, 0)
        self.what = Gtk.Label(name="what", xalign=0)
        self.what.set_ellipsize(3)
        self.what.set_margin_start(20)
        self.what.set_margin_top(6)
        self.what.set_margin_bottom(28)
        box.pack_start(self.what, False, False, 0)

        self.row = Gtk.Box(spacing=18)
        line = Gtk.Box(name="line")
        line.set_size_request(2, 36)
        line.set_valign(Gtk.Align.CENTER)
        self.row.pack_start(line, False, False, 0)
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
        self.row.pack_start(over, True, True, 0)
        box.pack_start(self.row, False, False, 0)

        self.error = Gtk.Label(label="", name="error", xalign=0)
        self.error.set_margin_start(20)
        self.error.set_margin_top(14)
        box.pack_start(self.error, False, False, 0)
        self.keys_hint = Gtk.Label(name="keys", xalign=0)
        self.keys_hint.set_margin_start(20)
        self.keys_hint.set_margin_top(30)
        box.pack_start(self.keys_hint, False, False, 0)

        touchkeys.front(True)
        self.keys = touchkeys.TouchKeys(self.entry, self.submit)
        kw = min(1100, g.width - 40)
        self.keys.set_size_request(kw, -1)
        stage.put(self.keys, g.x + (g.width - kw) // 2, g.y + g.height - 300)
        w.connect("key-press-event", self.on_key)
        w.show_all()
        self.closed = False

    def ask(self, kind, props):
        """Show (or show again) the question; props are gnome-keyring's, all of them so far."""
        self.kind, self.props, self.first = kind, props, None
        description, message = props.get("description", ""), props.get("message", "")
        why = next((v for k, v in FRIENDLY if k in description), description or message)
        self.why.set_text(why)
        self.what.set_text(plain(props.get("title") or message))
        self.error.set_text(props.get("warning", ""))
        go = plain(props.get("continue-label")) or ("Unlock" if kind == "password" else "Continue")
        self.keys_hint.set_text(f"Enter to {go.lower()}  ·  Esc to cancel")
        asking = kind == "password"
        self.row.set_visible(asking)
        self.keys.set_visible(asking)
        self.hint.set_text("New password" if props.get("password-new") else "Password")
        self.entry.set_text("")
        self.entry.set_sensitive(True)
        self.win.present()
        GLib.timeout_add(150, lambda: (self.grab(), False)[1])

    def grab(self):
        if self.closed:
            return
        self.win.present()
        if self.kind == "password":
            self.entry.grab_focus()
        seat = Gdk.Display.get_default().get_default_seat()
        seat.grab(self.win.get_window(), Gdk.SeatCapabilities.KEYBOARD, False, None, None, None)

    def submit(self, *_):
        if not self.entry.get_sensitive():
            return
        if self.kind != "password":
            self.reply("yes", None)
            return
        text = self.entry.get_text()
        if self.props.get("password-new"):
            if self.first is None:
                self.first = text
                self.entry.set_text("")
                self.hint.set_text("Once more")
                self.error.set_text("")
                return
            if text != self.first:
                self.first = None
                self.entry.set_text("")
                self.hint.set_text("New password")
                self.error.set_text("The two didn't match, try again")
                return
        elif not text:
            return
        self.reply("yes", text)

    def reply(self, reply, password):
        self.entry.set_sensitive(False)
        self.error.set_text("")
        self.answer(reply, password)

    def on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            if self.entry.get_sensitive() or self.kind != "password":
                self.reply("no", None)
            return True
        if ev.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.kind != "password":
            self.submit()
            return True
        return False

    def close(self):
        if self.closed:
            return
        self.closed = True
        Gdk.Display.get_default().get_default_seat().ungrab()
        touchkeys.front(False)
        self.win.destroy()


class Prompter:
    """gcr's prompter protocol, spoken directly: gnome-keyring begins, asks (perhaps again), stops."""

    def __init__(self, bus):
        self.bus = bus
        self.current: tuple[str, str] | None = None  # (sender, callback path)
        self.exchange: keyring_exchange.Exchange | None = None
        self.window: Window | None = None
        self.props: dict = {}
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        bus.register_object(PATH, node.interfaces[0], self.on_call, None, None)

    def call_back(self, method, args):
        if not self.current:
            return
        sender, path = self.current
        self.bus.call(
            sender, path, CALLBACK, method, args, None, Gio.DBusCallFlags.NO_AUTO_START, -1, None, self.called, method
        )

    def called(self, bus, result, method):
        try:
            bus.call_finish(result)
        except GLib.Error as e:
            report(f"keyring prompt {method}: {e.message}")

    def ready(self, reply, password=None):
        if not self.current or not self.exchange:
            return
        try:
            exchange = self.exchange.send(password)
        except Exception:
            report()
            reply, exchange = "no", self.exchange.send()
        # gnome-keyring asks "a blank password?" when a new password's strength is 0. Values go
        # wrapped twice (a variant in a variant), as gcr sends them.
        strength = GLib.Variant("v", GLib.Variant("i", 1 if password else 0))
        props = {"password-strength": strength} if password is not None else {}
        self.call_back("PromptReady", GLib.Variant("(sa{sv}s)", (reply, props, exchange)))

    def on_call(self, _bus, sender, _path, _iface, method, params, invocation):
        try:
            if method == "BeginPrompting":
                (callback,) = params.unpack()
                if self.current:
                    invocation.return_dbus_error("org.gnome.keyring.Prompter.Busy", "Already prompting")
                    return
                self.current, self.props = (sender, callback), {}
                self.exchange = keyring_exchange.Exchange()
                invocation.return_value(None)
                self.ready("")  # ready to be asked, with our half of the exchange
            elif method == "PerformPrompt":
                callback, kind, props, exchange = params.unpack()
                if self.current != (sender, callback) or not self.exchange:
                    invocation.return_dbus_error("org.gnome.keyring.Prompter.NotPrompting", "Not prompting")
                    return
                invocation.return_value(None)
                self.exchange.receive(exchange)
                self.props.update(props)
                if self.window is None or self.window.closed:
                    self.window = Window(self.ready)
                self.window.ask(kind, self.props)
            elif method == "StopPrompting":
                (callback,) = params.unpack()
                invocation.return_value(None)
                if self.current == (sender, callback):
                    self.stop()
        except Exception:
            report()
            if self.current:
                self.stop()

    def stop(self):
        if self.window:
            self.window.close()
            self.window = None
        if self.current:
            self.call_back("PromptDone", None)
        self.current, self.exchange = None, None

    def vanished(self, name):
        """The keyring went away mid-prompt: close the window."""
        if self.current and self.current[0] == name:
            if self.window:
                self.window.close()
                self.window = None
            self.current, self.exchange = None, None


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    if not keyring_exchange.available():
        sys.exit("kuhaku-keyring: no libcrypto; GNOME's prompter stays in charge")
    Gtk.init(None)
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    prompter = Prompter(bus)

    def owner_changed(_bus, _sender, _path, _iface, _signal, params):
        name, _old, new = params.unpack()
        if not new:
            prompter.vanished(name)

    bus.signal_subscribe(
        "org.freedesktop.DBus",
        "org.freedesktop.DBus",
        "NameOwnerChanged",
        "/org/freedesktop/DBus",
        None,
        Gio.DBusSignalFlags.NONE,
        owner_changed,
    )
    # Take the name from GNOME's prompter if it is open now, or wait in line until it quits
    # (it does after ten idle seconds); either way the next keyring question comes here.
    Gio.bus_own_name_on_connection(
        bus, NAME, Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT | Gio.BusNameOwnerFlags.REPLACE, None, None
    )
    Gtk.main()


if __name__ == "__main__":
    main()
