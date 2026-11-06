#!/usr/bin/env python3
"""Kūhaku's login and unlock screen (a LightDM greeter).

The home screen's prompt and nothing else: the red line where the home screen has it,
"Password" beside it until you type, dots while you type. One person on this computer:
no name is shown. Several: their names sit quietly above the line, a click chooses.
A wrong password says so under the line, in the accent colour.

Installed by kuhaku-login-fix.sh. If it ever fails to start, the wrapper falls back to
the standard greeter, so the computer can always be opened.
"""
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("LightDM", "1")
from gi.repository import Gdk, GLib, Gtk, LightDM

BG, FG, MUTED, ACCENT = "#121212", "#ececec", "#9a9a9a", "#f0705a"

CSS = f"""
window, #stage {{ background: {BG}; }}
* {{ font-family: "Inter", sans-serif; }}
#line {{ background: {ACCENT}; min-width: 2px; }}
entry {{ background: transparent; border: none; box-shadow: none; border-radius: 0; padding: 0;
         color: {FG}; caret-color: {MUTED}; font-size: 26px; font-weight: 200; letter-spacing: 2px; min-height: 36px; }}
entry selection {{ background: rgba(236,236,236,.15); }}
#hint {{ color: rgba(236,236,236,.35); font-size: 26px; font-weight: 200; }}
#error {{ color: {ACCENT}; font-size: 13px; font-weight: 300; }}
.person {{ background: none; border: none; box-shadow: none; padding: 0; margin: 0; color: {MUTED};
           font-size: 15px; font-weight: 300; min-height: 0; }}
.person:hover {{ color: {FG}; }}
.person.on {{ color: {FG}; }}
.person label {{ border-bottom: 1.5px solid transparent; padding-bottom: 3px; }}
.person.on label {{ border-bottom-color: {ACCENT}; }}
"""


class Greeter:
    def __init__(self):
        self.greeter = LightDM.Greeter()
        self.greeter.connect("show-prompt", self.on_prompt)
        self.greeter.connect("show-message", self.on_message)
        self.greeter.connect("authentication-complete", self.on_done)
        self.greeter.connect_to_daemon_sync()

        users = LightDM.UserList.get_instance()
        self.people = [u for u in users.get_users()]
        hint = self.greeter.get_select_user_hint()
        self.user = next((u.get_name() for u in self.people if u.get_name() == hint), None) \
            or next((u.get_name() for u in self.people if u.get_logged_in()), None) \
            or (self.people[0].get_name() if self.people else None)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        screen = Gdk.Screen.get_default()
        root = screen.get_root_window()
        root.set_cursor(Gdk.Cursor.new_for_display(Gdk.Display.get_default(), Gdk.CursorType.LEFT_PTR))
        geo = Gdk.Display.get_default().get_primary_monitor() or Gdk.Display.get_default().get_monitor(0)
        g = geo.get_geometry()

        self.win = Gtk.Window()
        self.win.set_decorated(False)
        self.win.move(0, 0)
        self.win.resize(screen.get_width(), screen.get_height())
        stage = Gtk.Fixed(name="stage")
        self.win.add(stage)

        # Where the home screen draws its line: a 1160 px column, 45 % down the primary screen.
        col = min(1160, g.width - 48)
        x = g.x + (g.width - col) // 2
        y = g.y + int(g.height * 0.45)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_size_request(col, -1)
        stage.put(box, x, y - (44 if len(self.people) > 1 else 0))

        self.names = Gtk.Box(spacing=26)
        self.names.set_margin_bottom(20)
        self.names.set_margin_start(20)
        if len(self.people) > 1:
            for u in self.people:
                b = Gtk.Button(label=u.get_display_name() or u.get_name())
                b.get_style_context().add_class("person")
                b.set_relief(Gtk.ReliefStyle.NONE)
                b.set_can_focus(False)
                b.user = u.get_name()
                b.connect("clicked", lambda b: self.choose(b.user))
                self.names.pack_start(b, False, False, 0)
            box.pack_start(self.names, False, False, 0)

        row = Gtk.Box(spacing=18)
        line = Gtk.Box(name="line")
        line.set_size_request(2, 36)
        line.set_valign(Gtk.Align.CENTER)
        row.pack_start(line, False, False, 0)
        over = Gtk.Overlay()
        self.entry = Gtk.Entry(visibility=False, invisible_char="•", has_frame=False, width_chars=30)
        self.entry.set_hexpand(True)
        self.entry.connect("activate", self.on_enter)
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

        self.win.connect("key-press-event", self.on_key)
        self.win.show_all()
        self.win.present()
        self.choose(self.user)

    # authentication ------------------------------------------------------------------
    def choose(self, name):
        self.user = name
        for b in self.names.get_children():
            ctx = b.get_style_context()
            (ctx.add_class if b.user == name else ctx.remove_class)("on")
        self.error.set_text("")
        self.entry.set_text("")
        self.entry.set_sensitive(True)
        self.entry.grab_focus()
        if self.greeter.get_in_authentication():
            self.greeter.cancel_authentication()
        self.greeter.authenticate(name)

    def on_prompt(self, _g, text, kind):
        self.entry.set_sensitive(True)
        self.entry.set_visibility(kind != LightDM.PromptType.SECRET)
        self.entry.grab_focus()

    def on_message(self, _g, text, kind):
        if kind == LightDM.MessageType.ERROR:
            self.error.set_text(text)

    def on_enter(self, entry):
        if not self.greeter.get_in_authentication():
            self.choose(self.user)
            return
        self.entry.set_sensitive(False)
        self.greeter.respond(entry.get_text())

    def on_done(self, _g):
        if self.greeter.get_is_authenticated():
            self.greeter.start_session_sync(None)   # the person's own session (Kūhaku)
            return
        self.choose(self.user)
        self.error.set_text("Wrong password")

    def on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.entry.set_text("")
        return False


if __name__ == "__main__":
    Greeter()
    Gtk.main()
