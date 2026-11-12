"""The thin bar over apps: Kūhaku's way home and its corner of the world.

When an app is in front, a 34 px strip sits at the top of the screen (apps are kept
below it). On the right: the date, time, Wi‑Fi and battery, as on the home screen, then
the red dot (the open apps, as cards), the red line, which takes you home to Kūhaku (the app stays open), and a cross that
closes the app. Apps do not draw their own minimise and close: there is one of each. With Kūhaku in front, the bar steps away.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402
from kuhaku_log import report  # faults go to the log instead of vanishing

# Outside apps that should open filling the screen, as Kūhaku's own apps do (WM_CLASS parts).
FILL_CLASSES = ("com.anthropic.Claude", "\"slack\"", "io.tangencylabs.solidcraft")

H = 34
HERE = Path(__file__).resolve().parent
TABLET_MODE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "kuhaku-tablet-mode"


def tablet_mode():
    """Folded into a tablet (kuhaku-tablet.py says so): the bar knows."""
    try:
        return TABLET_MODE.read_text().strip() == "1"
    except OSError:
        return False


DAY = {"bg": (0.969, 0.957, 0.945), "fg": (0.067, 0.067, 0.067), "muted": (0.4, 0.4, 0.4), "accent": (0.878, 0.188, 0.118)}
NIGHT = {"bg": (0.071, 0.071, 0.071), "fg": (0.925, 0.925, 0.925), "muted": (0.6, 0.6, 0.6), "accent": (0.941, 0.439, 0.353)}


def scale():
    """Text size in Settings (Xft DPI) grows the bar with everything else."""
    dpi = Gtk.Settings.get_default().get_property("gtk-xft-dpi")
    return max(0.75, min(2.0, dpi / 1024 / 96)) if dpi and dpi > 0 else 1.0


def root_prop(name):
    try:
        return subprocess.run(["xprop", "-root", name], capture_output=True, text=True, timeout=2).stdout
    except Exception:
        report()
        return ""


class Corner:
    def __init__(self, theme_fn, own_xid_fn, battery_fn, wifi_fn, open_fn=None):
        self.theme_fn, self.own_xid_fn, self.battery_fn, self.wifi_fn = theme_fn, own_xid_fn, battery_fn, wifi_fn
        self.open_fn = open_fn  # opens a home-screen panel: calendar, light, wifi, battery
        self.zones = []
        self.battery, self.wifi, self.hover = None, None, None
        mon = Gdk.Display.get_default().get_primary_monitor() or Gdk.Display.get_default().get_monitor(0)
        self.geo = mon.get_geometry()
        w = self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        w.set_type_hint(Gdk.WindowTypeHint.DOCK)
        w.set_decorated(False)
        w.set_keep_above(True)
        w.stick()
        w.set_skip_taskbar_hint(True)
        w.set_skip_pager_hint(True)
        w.set_accept_focus(False)
        w.set_app_paintable(True)
        w.move(self.geo.x, self.geo.y)
        self.s = scale()
        w.set_size_request(self.geo.width, round(H * self.s))
        w.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        w.connect("draw", self.draw)
        w.connect("button-press-event", self.click)
        w.connect("motion-notify-event", lambda _w, e: self.set_hover(self.where(e.x)))
        w.connect("leave-notify-event", lambda *_: self.set_hover(None))
        w.connect("realize", self.reserve)
        # A new resolution: the bar follows the screen's width, so its controls stay in view.
        Gdk.Screen.get_default().connect("monitors-changed", lambda *_: GLib.timeout_add(300, self.refit))
        Gtk.Settings.get_default().connect("notify::gtk-xft-dpi", lambda *_: GLib.timeout_add(300, self.refit))
        self.shown = False
        self.tablet = tablet_mode()
        self.filled = set()
        GLib.timeout_add(800, self.poll)
        GLib.timeout_add_seconds(20, self.read_status)
        self.read_status()

    def reserve(self, _w):
        """Maximised apps begin below the bar, never under it."""
        xid = self.win.get_window().get_xid()
        g = self.geo
        subprocess.run(["xprop", "-id", str(xid), "-f", "_NET_WM_STRUT_PARTIAL", "32cccccccccccc", "-set",
                        "_NET_WM_STRUT_PARTIAL", f"0,0,{g.y + round(H * self.s)},0,0,0,0,0,{g.x},{g.x + g.width - 1},0,0"],
                       capture_output=True)

    def refit(self):
        mon = Gdk.Display.get_default().get_primary_monitor() or Gdk.Display.get_default().get_monitor(0)
        self.geo = mon.get_geometry()
        self.win.move(self.geo.x, self.geo.y)
        self.s = scale()
        self.win.set_size_request(self.geo.width, round(H * self.s))
        self.win.resize(self.geo.width, round(H * self.s))
        if self.win.get_window():
            self.reserve(self.win)
        self.win.queue_draw()
        return False

    def read_status(self):
        self.battery, self.wifi = self.battery_fn(), self.wifi_fn()
        self.win.queue_draw()
        return True

    def poll(self):
        """Shown when an app is in front; away when Kūhaku is."""
        out = root_prop("_NET_ACTIVE_WINDOW") + root_prop("_NET_SHOWING_DESKTOP")
        try:
            active = int(out.split("#")[1].split()[0].strip(","), 16)
        except Exception:
            active = 0
        own = self.own_xid_fn()
        mine = self.win.get_window().get_xid() if self.win.get_window() else -1
        # The "showing desktop" flag can stay set after an app opens; what is in front decides.
        want = bool(active) and active not in (own, mine)
        if active == mine:
            want = self.shown
        if want != self.shown:
            self.shown = want
            (self.win.show_all if want else self.win.hide)()
        tablet = tablet_mode()
        if tablet != self.tablet:
            self.tablet = tablet
            self.win.queue_draw()
        if want:
            self.win.queue_draw()
            self.fill(active)
        return True

    def fill(self, xid):
        """Outside apps that open as a small window (the Claude app, Slack) fill the screen, like ours."""
        if xid in self.filled:
            return
        self.filled.add(xid)
        try:
            cls = subprocess.run(["xprop", "-id", str(xid), "WM_CLASS"], capture_output=True, text=True, timeout=2).stdout
        except Exception:
            report()
            return
        if any(c in cls for c in FILL_CLASSES):
            subprocess.Popen(["wmctrl", "-i", "-r", hex(xid), "-b", "add,maximized_vert,maximized_horz"])

    # drawing --------------------------------------------------------------------------
    # Flat, right-aligned: date, time, Wi‑Fi, battery, then the red dot (open apps), the red line (home, the app stays
    # open) and the cross (close the app). Nothing on the left.
    def where(self, x):
        x /= self.s
        width = self.win.get_allocated_width() / self.s
        if width - 130 <= x < width - 104:
            return "apps"
        if width - 104 <= x < width - 60:
            return "home"
        if width - 60 <= x < width - 20:
            return "close"
        # Date, time, Wi‑Fi and battery open their panels, as on the home screen.
        return next((name for x0, x1, name in self.zones if x0 <= x < x1), None)

    def set_hover(self, h):
        if h != self.hover:
            self.hover = h
            self.win.queue_draw()

    def text(self, cr, s, x, size, color, right=False, weight=Pango.Weight.LIGHT):
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription.from_string(f"Inter {size}")
        fd.set_weight(weight)
        layout.set_font_description(fd)
        layout.set_text(s, -1)
        w, h = layout.get_pixel_size()
        cr.set_source_rgb(*color)
        cr.move_to(x - w if right else x, (H - h) / 2)
        PangoCairo.show_layout(cr, layout)
        return w

    def draw(self, _w, cr):
        c = NIGHT if self.theme_fn() == "night" else DAY
        cr.set_source_rgb(*c["bg"])
        cr.paint()
        cr.scale(self.s, self.s)  # drawn at 34 px and scaled with the text size
        width = self.win.get_allocated_width() / self.s
        mid = H / 2
        # Close: a thin cross, far right.
        x = width - 40
        cr.set_source_rgb(*(c["fg"] if self.hover == "close" else c["muted"]))
        cr.set_line_width(1.4)
        cr.set_line_cap(1)
        r = 4.5
        cr.move_to(x - r, mid - r); cr.line_to(x + r, mid + r)
        cr.move_to(x + r, mid - r); cr.line_to(x - r, mid + r)
        cr.stroke()
        # Home: the red line (longer on hover, as on the home screen).
        lw = 26 if self.hover == "home" else 20
        cr.set_source_rgb(*c["accent"])
        cr.rectangle(width - 66 - lw, mid - 1, lw, 2)
        cr.fill()
        # Open apps: the red dot, just before the red line; it opens the cards of what is open.
        cr.set_source_rgb(0.898, 0.282, 0.302)
        cr.arc(width - 117, mid, 3.9 if self.hover == "apps" else 3, 0, 6.2832)
        cr.fill()
        x = width - 142
        zones = []
        hot = lambda name, color: c["fg"] if self.hover == name else color
        # Battery: a flat bar, its track faint.
        b = self.battery
        if b:
            cr.set_source_rgba(*c["muted"], 0.18)
            cr.rectangle(x - 18, mid - 3, 18, 6)
            cr.fill()
            cr.set_source_rgb(*(c["accent"] if b["level"] <= 15 and not b["charging"] else hot("battery", c["muted"])))
            cr.rectangle(x - 18, mid - 3, max(1.5, 18 * b["level"] / 100), 6)
            cr.fill()
            zones.append((x - 27, x + 9, "battery"))
            x -= 36
        # Wi‑Fi: three flat lines, as on the home screen.
        on = self.wifi in ("on", "wired")
        cr.set_source_rgba(*hot("wifi", c["muted"]), 1 if on else 0.3)
        # The home screen's mark exactly (its 24-unit drawing at 16 px): lines 10, 6 and 2.7 px wide.
        for i, half in enumerate((5, 3, 1.35)):
            cr.rectangle(x - 7 - half, mid - 8 + 4.33 + i * 3, half * 2, 1)
        cr.fill()
        zones.append((x - 21, x + 7, "wifi"))
        x -= 34
        now = time.localtime()
        tw = self.text(cr, time.strftime("%H:%M", now), x, 9.5, c["fg"], right=True, weight=Pango.Weight.NORMAL)
        zones.append((x - tw - 9, x + 9, "light"))
        x -= tw + 18
        dw = self.text(cr, time.strftime("%a %-d %b", now), x, 9.5, hot("calendar", c["muted"]), right=True)
        zones.append((x - dw - 9, x + 9, "calendar"))
        x -= dw + 30
        self.zones = zones

    def click(self, _w, e):
        w = self.where(e.x)
        if w == "apps":
            from rundot import overview
            overview()
        elif w == "home":
            subprocess.Popen(["wmctrl", "-k", "on"])
        elif w == "close":
            subprocess.Popen(["wmctrl", "-c", ":ACTIVE:"])
        elif w and self.open_fn:
            self.open_fn(w)
        return True
