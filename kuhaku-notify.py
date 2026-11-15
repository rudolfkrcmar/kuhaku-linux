#!/usr/bin/env python3
"""Kūhaku's notifications: the notices apps send, shown as small calm cards.

It answers the standard desktop notification calls (org.freedesktop.Notifications on the
session bus), so notify-send, the browser, mail, the battery and every app reach it. A
card shows the app, a title and a line or two, in Kūhaku's day or night look; it leaves
on its own after a few seconds (an urgent one stays until clicked). Clicking a card does
what the app offered for it; buttons appear for any other choices.

Every notice is also kept (notices.sqlite) for the home screen's list (F9).
"""

import os
import re
import sqlite3
import sys
import time
from html import unescape
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from kuhaku_log import report  # faults go to the log instead of vanishing
from kuhaku_sun import theme

LOG = Path.home() / ".local" / "state" / "kuhaku" / "notices.sqlite"
KEEP = 200  # notices kept for the list
WIDTH = 360
MARGIN = 20  # from the screen's edge
TOP = 64  # under Kūhaku's bar
GAP = 10
SHOW_MS = 6000

IFACE = """
<node>
  <interface name="org.freedesktop.Notifications">
    <method name="GetCapabilities"><arg direction="out" type="as"/></method>
    <method name="Notify">
      <arg direction="in" type="s" name="app_name"/>
      <arg direction="in" type="u" name="replaces_id"/>
      <arg direction="in" type="s" name="app_icon"/>
      <arg direction="in" type="s" name="summary"/>
      <arg direction="in" type="s" name="body"/>
      <arg direction="in" type="as" name="actions"/>
      <arg direction="in" type="a{sv}" name="hints"/>
      <arg direction="in" type="i" name="expire_timeout"/>
      <arg direction="out" type="u"/>
    </method>
    <method name="CloseNotification"><arg direction="in" type="u" name="id"/></method>
    <method name="GetServerInformation">
      <arg direction="out" type="s"/><arg direction="out" type="s"/>
      <arg direction="out" type="s"/><arg direction="out" type="s"/>
    </method>
    <signal name="NotificationClosed"><arg type="u"/><arg type="u"/></signal>
    <signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
  </interface>
</node>
"""
# Why a card closed, as the standard numbers it.
EXPIRED, DISMISSED, CLOSED = 1, 2, 3

COLORS = {
    "day": {
        "sheet": "#ffffff",
        "fg": "#111111",
        "muted": "#666666",
        "faint": "rgba(17,17,17,.08)",
        "accent": "#e0301e",
    },
    "night": {
        "sheet": "#1b1b1b",
        "fg": "#ececec",
        "muted": "#9a9a9a",
        "faint": "rgba(236,236,236,.08)",
        "accent": "#f0705a",
    },
}


def css(t):
    c = COLORS[t]
    return f"""
    .card {{ background:{c["sheet"]}; border:1px solid {c["faint"]}; border-radius:14px; padding:14px 18px 16px; }}
    .card:hover {{ border-color:{c["muted"]}; }}
    .card.urgent {{ border-color:{c["accent"]}; }}
    .card label {{ font-family:'Inter Variable',Inter,sans-serif; color:{c["fg"]}; }}
    .app {{ font-size:12px; font-weight:300; }}
    .card label.app {{ color:{c["muted"]}; }}
    .title {{ font-size:14px; font-weight:400; }}
    .body {{ font-size:13px; font-weight:300; }}
    .card label.body {{ color:{c["muted"]}; }}
    .card button {{ background:transparent; background-image:none; box-shadow:none; border:1px solid {c["faint"]};
                    border-radius:8px; padding:5px 12px; color:{c["fg"]}; font-family:'Inter Variable',Inter,sans-serif;
                    font-weight:300; font-size:13px; }}
    .card button:hover {{ background:{c["faint"]}; }}
    progressbar trough {{ background:{c["faint"]}; min-height:2px; border:none; border-radius:1px; }}
    progressbar progress {{ background:{c["accent"]}; min-height:2px; border:none; border-radius:1px; }}
    """


def plain(text):
    """Bodies may carry a little markup (<b>, <a>); the card shows only the words."""
    return unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


# --- the kept list ------------------------------------------------------------------
def open_log():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(LOG), timeout=2)
    db.execute(
        "CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY, timestamp INTEGER, "
        "app_name TEXT, summary TEXT, body TEXT, is_read INTEGER DEFAULT 0)"
    )
    return db


def keep(app, title, body):
    try:
        db = open_log()
        db.execute(
            "INSERT INTO notifications (timestamp, app_name, summary, body) VALUES (?, ?, ?, ?)",
            (int(time.time()), app, title, body),
        )
        db.execute(
            "DELETE FROM notifications WHERE id NOT IN (SELECT id FROM notifications ORDER BY id DESC LIMIT ?)",
            (KEEP,),
        )
        db.commit()
        db.close()
    except Exception:
        report()


# --- the cards ----------------------------------------------------------------------
class Card:
    def __init__(self, server, nid):
        self.server, self.id = server, nid
        self.timer = None
        self.win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.win.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.win.set_keep_above(True)
        self.win.set_accept_focus(False)
        self.win.set_app_paintable(True)
        visual = self.win.get_screen().get_rgba_visual()
        if visual:
            self.win.set_visual(visual)
        self.box = Gtk.EventBox()
        self.box.connect("button-release-event", self.clicked)
        self.box.connect("enter-notify-event", lambda *_: self.hold())
        self.box.connect("leave-notify-event", lambda *_: self.later())
        self.win.add(self.box)
        self.inner = None

    def fill(self, app, title, body, actions, urgent, value, timeout):
        if self.inner:
            self.box.remove(self.inner)
        self.actions = actions
        self.urgent = urgent
        self.timeout = timeout
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        col.get_style_context().add_class("card")
        if urgent:
            col.get_style_context().add_class("urgent")
        col.set_size_request(WIDTH, -1)
        for text, cls, lines in ((app, "app", 1), (title, "title", 2), (body, "body", 4)):
            if not text:
                continue
            lab = Gtk.Label(label=text, xalign=0)
            lab.get_style_context().add_class(cls)
            lab.set_line_wrap(True)
            lab.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            lab.set_max_width_chars(44)
            lab.set_lines(lines)
            lab.set_ellipsize(Pango.EllipsizeMode.END)
            col.pack_start(lab, False, False, 0)
        if value is not None:
            bar = Gtk.ProgressBar()
            bar.set_fraction(max(0, min(100, value)) / 100)
            bar.set_margin_top(8)
            col.pack_start(bar, False, False, 0)
        others = [(k, label) for k, label in actions if k != "default"]
        if others:
            row = Gtk.Box(spacing=8)
            row.set_margin_top(10)
            for k, label in others[:3]:
                b = Gtk.Button(label=label)
                b.connect("clicked", lambda _b, k=k: self.invoke(k))
                row.pack_start(b, False, False, 0)
            col.pack_start(row, False, False, 0)
        self.inner = col
        self.box.add(col)
        self.win.show_all()
        self.later()

    def hold(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None

    def later(self):
        """Leaves after a while; an urgent card (or one asked to stay) waits for a click."""
        self.hold()
        if self.urgent or self.timeout == 0:
            return
        ms = self.timeout if self.timeout and self.timeout > 0 else SHOW_MS
        self.timer = GLib.timeout_add(ms, lambda: (self.server.close(self.id, EXPIRED), False)[1])

    def clicked(self, _w, ev):
        if ev.button == 1 and any(k == "default" for k, _ in self.actions):
            self.invoke("default")
        else:
            self.server.close(self.id, DISMISSED)
        return True

    def invoke(self, key):
        self.server.signal("ActionInvoked", GLib.Variant("(us)", (self.id, key)))
        self.server.close(self.id, DISMISSED)

    def height(self):
        return self.win.get_preferred_height()[1]


class Server:
    def __init__(self):
        self.cards: dict[int, Card] = {}
        self.next_id = 1
        self.conn = None
        self.provider = Gtk.CssProvider()
        self.look = None
        self.restyle()
        GLib.timeout_add_seconds(60, lambda: (self.restyle(), True)[1])
        node = Gio.DBusNodeInfo.new_for_xml(IFACE)
        Gio.bus_own_name(
            Gio.BusType.SESSION,
            "org.freedesktop.Notifications",
            Gio.BusNameOwnerFlags.REPLACE | Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT,
            lambda conn, _name: self.register(conn, node.interfaces[0]),
            None,
            lambda _c, _n: Gtk.main_quit(),  # someone else holds the name: the session starts us again
        )

    def restyle(self):
        t = theme()
        if t != self.look:
            self.look = t
            self.provider.load_from_data(css(t).encode())
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def register(self, conn, iface):
        self.conn = conn
        conn.register_object("/org/freedesktop/Notifications", iface, self.call, None, None)

    def signal(self, name, params):
        if self.conn:
            self.conn.emit_signal(None, "/org/freedesktop/Notifications", "org.freedesktop.Notifications", name, params)

    def call(self, _conn, _sender, _path, _iface, method, params, inv):
        try:
            if method == "GetCapabilities":
                inv.return_value(GLib.Variant("(as)", (["actions", "body", "body-markup", "persistence"],)))
            elif method == "GetServerInformation":
                inv.return_value(GLib.Variant("(ssss)", ("Kūhaku", "Kūhaku", "1.0", "1.2")))
            elif method == "CloseNotification":
                self.close(params.unpack()[0], CLOSED)
                inv.return_value(None)
            elif method == "Notify":
                inv.return_value(GLib.Variant("(u)", (self.notify(*params.unpack()),)))
            else:
                inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
        except Exception as e:
            report()
            inv.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(e))

    def notify(self, app, replaces, _icon, summary, body, actions, hints, timeout):
        nid = replaces if replaces in self.cards else self.next_id
        if nid == self.next_id:
            self.next_id += 1
        title, text = plain(summary), plain(body)
        pairs = list(zip(actions[::2], actions[1::2], strict=False))
        urgent = hints.get("urgency") == 2
        value = hints.get("value")
        if not hints.get("transient") and replaces not in self.cards:
            keep(app, title, text)
        card = self.cards.get(nid) or Card(self, nid)
        self.cards[nid] = card
        card.fill(app, title, text, pairs, urgent, value if isinstance(value, int) else None, timeout)
        self.place()
        return nid

    def close(self, nid, reason):
        card = self.cards.pop(nid, None)
        if not card:
            return
        card.hold()
        card.win.destroy()
        self.signal("NotificationClosed", GLib.Variant("(uu)", (nid, reason)))
        self.place()

    def place(self):
        """Newest on top, at the top right, under the bar."""
        display = Gdk.Display.get_default()
        mon = display.get_primary_monitor() or display.get_monitor(0)
        area = mon.get_workarea()
        y = area.y + TOP
        for nid in sorted(self.cards, reverse=True):
            card = self.cards[nid]
            card.win.move(area.x + area.width - WIDTH - MARGIN, y)
            y += card.height() + GAP


def main():
    if "--help" in sys.argv:
        print(__doc__)
        return
    os.environ.setdefault("GDK_BACKEND", "x11")
    Server()
    Gtk.main()


if __name__ == "__main__":
    main()
