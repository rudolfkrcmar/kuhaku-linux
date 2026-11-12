#!/usr/bin/env python3
"""Kūhaku gestures: a three-finger swipe slides through Kūhaku and your open apps.

The screens sit in a row like Mac spaces: Kūhaku first, then every open app in
the order it was opened. Swipe left for the next one, right for the previous.
Listens to the Touchégg daemon (unix:abstract=touchegg) and follows the
fingers live; let go early and the screens slide back. The real windows are
switched under the animation at the start, so you land on the real screen.
At either end of the row the screen only gives a little and springs back.

Swipe up with three or four fingers and every open screen lies as a small card
along the bottom, Kūhaku first (see Overview).
"""

import os
import signal
import subprocess
import time

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkX11, Gio, GLib, Gtk

from gestures_common import (
    COMMIT,
    DISTANCE,
    FINGERS,
    GAP,
    LEFT,
    PID_FILE,
    RIGHT,
    SCALE,
    SWIPE,
    UP,
    apps,
    is_home,
    last_app,
    prop,
    showing_desktop,
)
from gestures_overview import (
    Overview,
)
from kuhaku_log import report  # faults go to the log instead of vanishing


class Deck:
    def __init__(self):
        screen = Gdk.Screen.get_default()
        self.w, self.h = screen.get_width(), screen.get_height()
        self.shots = {}  # last look at each screen: "kuhaku" or a window id
        self.edge = False
        self.front = self.back = None
        self.active = False
        self.dir = 1
        self.p = 0.0
        self.target = 0.0
        self.anim = None
        self.done = None

        self.win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.win.set_app_paintable(True)
        self.win.move(0, 0)
        self.win.resize(self.w, self.h)
        self.win.connect("draw", self.draw)
        screen.connect("size-changed", self.refit)

        self.ov = Overview(self)
        self.gesture = None
        self.seen = None
        self.seen_at = 0.0

        self.bus = None
        self.connect()
        GLib.timeout_add(1500, self.remember)
        GLib.timeout_add_seconds(2, self.watch)

    # --- Touchégg --------------------------------------------------------
    def connect(self):
        try:
            self.bus = Gio.DBusConnection.new_for_address_sync(
                "unix:abstract=touchegg", Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT, None, None
            )
        except GLib.Error:
            GLib.timeout_add_seconds(3, lambda: self.connect() and False)
            return
        for name, cb in (
            ("OnGestureBegin", self.on_begin),
            ("OnGestureUpdate", self.on_update),
            ("OnGestureEnd", self.on_end),
        ):
            self.bus.signal_subscribe(
                None, "io.github.joseexposito.Touchegg", name, None, None, Gio.DBusSignalFlags.NONE, cb
            )
        self.bus.connect("closed", lambda *a: GLib.timeout_add_seconds(3, lambda: self.connect() and False))

    @staticmethod
    def args(params):
        kind, direction, pct, fingers, _dev, _t = params.unpack()
        return kind, direction, pct, fingers

    def on_begin(self, _c, _s, _p, _i, _n, params):
        kind, direction, pct, fingers = self.args(params)
        self.gesture = None
        if kind != SWIPE:
            return
        if self.ov.visible or (direction == UP and fingers in (3, 4) and not self.anim and not self.win.get_visible()):
            self.gesture = "overview"
            self.ov.begin(direction, pct)
            return
        if fingers != FINGERS or direction not in (LEFT, RIGHT) or self.anim:
            return
        row = ["kuhaku"] + apps()
        active = self.active_window()
        self.was_showing = showing_desktop()
        if is_home(active) or (self.was_showing and active not in row):
            here = "kuhaku"
        else:
            here = active if active in row else (last_app() or "kuhaku")
        self.origin = here
        self.dir = 1 if direction == RIGHT else -1
        i = row.index(here) - self.dir
        self.edge = not 0 <= i < len(row)
        self.dest = None if self.edge else row[i]
        self.front = self.snapshot()
        self.shots[here] = self.front
        self.back = None if self.edge else self.shots.get(self.dest)
        self.p = self.target = 0.0
        self.active = True
        self.win.show_all()
        self.win.get_window().raise_()
        if not self.edge:
            # Switch the real windows now, hidden under the animation, and photograph
            # the destination so the incoming screen is what you will land on.
            GLib.timeout_add(30, self.switch)
            GLib.timeout_add(160, self.photograph)
        self.on_update(None, None, None, None, None, params)

    def go(self, where, leaving):
        if where == "kuhaku":
            # The show-desktop flag can be left on after an app was raised over
            # it; reset it so "on" really reveals Kūhaku.
            if showing_desktop():
                subprocess.run(["wmctrl", "-k", "off"])
            subprocess.run(["wmctrl", "-k", "on"])
            return
        if showing_desktop():
            subprocess.run(["wmctrl", "-k", "off"])
        subprocess.run(["wmctrl", "-i", "-a", where])

    def switch(self):
        self.go(self.dest, self.origin)
        return False

    def switch_back(self):
        if not self.edge:
            self.go(self.origin, self.dest)

    def photograph(self):
        if not self.win.get_visible() or not self.dest:
            return False
        img = self.snapshot(None if self.dest == "kuhaku" else self.dest)
        if img:
            self.back = self.shots[self.dest] = img
            self.win.queue_draw()
        return False

    def on_update(self, _c, _s, _p, _i, _n, params):
        if self.gesture == "overview":
            self.ov.update(self.args(params)[2])
            return
        if not self.active:
            return
        _k, _d, pct, _f = self.args(params)
        p = max(0.0, min(1.0, pct / DISTANCE))
        # At the end of the row the screen only gives a little.
        self.target = 0.06 * (1 - (1 - p) ** 2) if self.edge else p
        if not self.anim:
            self.anim = GLib.timeout_add(16, self.step)

    def on_end(self, _c, _s, _p, _i, _n, params):
        if self.gesture == "overview":
            self.gesture = None
            self.ov.end()
            return
        if not self.active:
            return
        self.active = False
        commit = self.target >= COMMIT and not self.edge
        self.target = 1.0 if commit else 0.0
        self.done = self.finish if commit else self.cancel
        if not self.anim:
            self.anim = GLib.timeout_add(16, self.step)

    # --- motion ----------------------------------------------------------
    def step(self):
        diff = self.target - self.p
        self.p += diff * (0.35 if self.active else 0.22)
        settled = abs(diff) < 0.004
        if settled:
            self.p = self.target
        self.win.queue_draw()
        if settled and not self.active:
            self.anim = None
            done, self.done = self.done, None
            if done:
                done()
            return False
        return True

    def finish(self):
        self.settle(self.dest, 4)
        GLib.timeout_add(60, self.hide)
        GLib.timeout_add(900, self.remember)

    def settle(self, where, tries):
        """Apps sometimes pull focus back right after a switch (Electron does);
        check for a moment and put the chosen screen in front again."""

        left = tries

        def check():
            nonlocal left
            active = self.active_window()
            ok = (showing_desktop() and is_home(active)) if where == "kuhaku" else active == where
            if not ok:
                self.go(where, None)
            left -= 1
            return left > 0 and not ok

        if where:
            GLib.timeout_add(150, check)

    def cancel(self):
        self.switch_back()
        GLib.timeout_add(120, self.hide)

    def hide(self):
        self.win.hide()
        self.front = self.back = None
        return False

    def remember(self):
        """Keep a fresh look at whatever screen is in front, between swipes."""
        if not self.win.get_visible() and not self.ov.visible:
            self.seen_at = time.monotonic()
            active = self.active_window()
            key = "kuhaku" if showing_desktop() or is_home(active) else active
            self.shots[key] = self.snapshot()
            live = set(apps()) | {"kuhaku"}
            for k in list(self.shots):
                if k not in live:
                    del self.shots[k]
        return False

    def watch(self):
        """Look at each screen soon after it comes to the front (and now and then
        after that), so the overview has a picture even of apps hidden later."""
        try:
            win = Gdk.Screen.get_default().get_active_window()
            xid = win.get_xid() if win else 0
        except Exception:
            xid = 0
        if xid != self.seen or time.monotonic() - self.seen_at > 20:
            self.seen = xid
            GLib.timeout_add(700, self.remember)
            self.seen_at = time.monotonic()
        return True

    def refit(self, screen):
        """A new resolution: the swipe covers the new screen, not the old one."""
        self.w, self.h = screen.get_width(), screen.get_height()
        self.shots = {}
        self.win.resize(self.w, self.h)
        self.ov.refit()

    # --- pictures --------------------------------------------------------
    def active_window(self):
        return prop(None, "_NET_ACTIVE_WINDOW").split("#")[-1].split(",")[0].strip()

    def snapshot(self, xid=None):
        """The whole screen, or one window's own pixels (the compositor keeps
        them even while the window is covered by the animation)."""
        try:
            if xid:
                win = GdkX11.X11Window.foreign_new_for_display(Gdk.Display.get_default(), int(xid, 16))
                x, y, w, h = win.get_geometry()
                ox, oy = win.get_origin()[1:]
                pix = Gdk.pixbuf_get_from_window(win, 0, 0, w, h)
            else:
                ox = oy = 0
                pix = Gdk.pixbuf_get_from_window(Gdk.get_default_root_window(), 0, 0, self.w, self.h)
        except Exception:
            report()
            return None
        if not pix:
            return None
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, int(self.w * SCALE), int(self.h * SCALE))
        cr = cairo.Context(surf)
        night = time.localtime().tm_hour >= 20 or time.localtime().tm_hour < 7
        cr.set_source_rgb(*((0.071,) * 3 if night else (0.969, 0.957, 0.945)))
        cr.paint()
        cr.scale(SCALE, SCALE)
        Gdk.cairo_set_source_pixbuf(cr, pix, ox, oy)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint()
        return surf

    def screen(self, cr, surface, x):
        cr.save()
        cr.rectangle(x, 0, self.w, self.h)
        cr.clip()
        if surface:
            cr.set_source_surface(surface, x, 0)
        else:
            night = time.localtime().tm_hour >= 20 or time.localtime().tm_hour < 7
            cr.set_source_rgb(*((0.071,) * 3 if night else (0.969, 0.957, 0.945)))
        cr.paint()
        cr.restore()

    def draw(self, _w, cr):
        # Whole pixels only, so nothing is resampled and the text stays crisp.
        shift = round(self.p * (self.w + GAP)) * self.dir
        cr.set_source_rgb(0, 0, 0)
        cr.paint()
        self.screen(cr, self.front, shift)
        self.screen(cr, self.back, shift - self.dir * (self.w + GAP))
        return True


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    deck = Deck()
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, deck.ov.toggle)
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    Gtk.main()
