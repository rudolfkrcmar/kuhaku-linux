"""The red dot, everywhere: while apps are open it sits in the bottom-left corner
above every window (where the home screen keeps its own dot), and opens a small
list to switch to an app, close it, or go home.

Hidden on the home screen itself (the page draws the same dot in the same place)
and while a window is full screen.
"""
import re
import subprocess

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkPixbuf, GdkX11, GLib, Gtk  # noqa: F401 (GdkX11 gives windows their XID)

SIZE = 44            # same box as .rundot on the home screen: left 44, bottom 36
LEFT, BOTTOM = 44, 36
THEMES = {
    "day":   {"bg": "#f7f4f1", "fg": "#111111", "muted": "#666666", "faint": "rgba(17,17,17,.07)", "accent": "#e0301e"},
    "night": {"bg": "#1b1b1b", "fg": "#ececec", "muted": "#9a9a9a", "faint": "rgba(236,236,236,.08)", "accent": "#f0705a"},
}
DOT = (0.898, 0.282, 0.302)   # #e5484d, as on the home screen


def overview():
    """Ask kuhaku-gestures.py to show the open apps as cards (as a swipe up does).
    False when it is not running, so the caller can fall back to the list."""
    import os
    import signal
    try:
        path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "kuhaku-gestures.pid")
        pid = int(open(path).read().strip())
        if "kuhaku-gestures" not in open(f"/proc/{pid}/cmdline").read():
            return False
        os.kill(pid, signal.SIGUSR1)
        return True
    except (OSError, ValueError):
        return False


def x(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout
    except Exception:
        return ""


class RunDot:
    def __init__(self, theme, home_xid):
        self.theme = theme              # callable -> "day" | "night"
        self.home_xid = home_xid        # callable -> int, the home screen's window
        self.windows = []
        self.list = None
        self.win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.win.set_app_paintable(True)
        self.win.set_accept_focus(False)
        self.win.set_keep_above(True)
        self.win.set_type_hint(Gdk.WindowTypeHint.DOCK)
        visual = self.win.get_screen().get_rgba_visual()
        if visual:
            self.win.set_visual(visual)
        self.win.set_default_size(SIZE, SIZE)
        self.win.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.hover = False
        self.win.connect("draw", self.draw)
        self.win.connect("button-press-event", lambda *a: overview() or self.toggle())
        self.win.connect("enter-notify-event", lambda *a: self.set_hover(True))
        self.win.connect("leave-notify-event", lambda *a: self.set_hover(False))
        self.place()
        Gdk.Screen.get_default().connect("monitors-changed", lambda *a: self.place())

    def place(self):
        geo = Gdk.Display.get_default().get_primary_monitor() or Gdk.Display.get_default().get_monitor(0)
        r = geo.get_geometry()
        self.win.move(r.x + LEFT, r.y + r.height - BOTTOM - SIZE)

    def set_hover(self, on):
        self.hover = on
        self.win.queue_draw()

    def draw(self, _w, cr):
        cr.set_operator(0)  # clear
        cr.paint()
        cr.set_operator(2)  # over
        r = 5.2 if (self.hover or self.list) else 4
        cr.set_source_rgb(*DOT)
        cr.arc(SIZE / 2, SIZE / 2, r, 0, 6.2832)
        cr.fill()
        # A hairline ring keeps it visible on red or busy pages.
        cr.set_source_rgba(1, 1, 1, .55)
        cr.set_line_width(1)
        cr.arc(SIZE / 2, SIZE / 2, r + .8, 0, 6.2832)
        cr.stroke()

    # Called every 1.5 s by Kūhaku with the open app windows.
    def update(self, windows):
        self.windows = windows
        active = re.search(r"0x[0-9a-f]+", x(["xprop", "-root", "_NET_ACTIVE_WINDOW"]))
        active = int(active.group(0), 16) if active else 0
        full = active and "FULLSCREEN" in x(["xprop", "-id", hex(active), "_NET_WM_STATE"])
        on_home = active in (0, self.home_xid()) or not any(int(w["xid"], 16) == active for w in windows)
        show = bool(windows) and not on_home and not full
        if show and not self.win.get_visible():
            self.place()
            self.win.show_all()
        elif not show and self.win.get_visible() and not self.list:
            self.win.hide()
        return True

    # The list ----------------------------------------------------------------------------------
    def toggle(self):
        if self.list:
            return self.close()
        t = THEMES.get(self.theme(), THEMES["day"])
        css = Gtk.CssProvider()
        css.load_from_data(f"""
          .runlist, .runlist * {{ font-family:Inter; }}
          .runlist {{ background:{t['bg']}; border-radius:18px; border:1px solid {t['faint']}; padding:18px 14px 14px; }}
          .runlist .h {{ color:{t['muted']}; font-size:9pt; letter-spacing:1.5px; margin:0 0 8px 12px; }}
          .runlist button {{ background:none; border:0; box-shadow:none; border-radius:10px; padding:7px 8px 7px 12px; color:{t['fg']}; }}
          .runlist button:hover {{ background:{t['faint']}; }}
          .runlist button label {{ font-weight:300; font-size:11pt; color:{t['fg']}; }}
          .runlist button.x {{ padding:4px 9px; }}
          .runlist button.x label {{ color:{t['muted']}; font-size:13pt; }}
          .runlist button.x:hover label {{ color:#e5484d; }}
          .runlist button.home label {{ color:{t['muted']}; }}
        """.encode())
        win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        win.set_decorated(False)
        win.set_keep_above(True)
        win.set_skip_taskbar_hint(True)
        win.set_skip_pager_hint(True)
        win.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
        visual = win.get_screen().get_rgba_visual()
        if visual:
            win.set_visual(visual)
            win.set_app_paintable(True)
        win.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.get_style_context().add_class("runlist")
        win.connect("draw", lambda w, cr: (cr.set_operator(0), cr.paint(), cr.set_operator(2)) and False)
        h = Gtk.Label(label="OPEN", xalign=0)
        h.get_style_context().add_class("h")
        box.pack_start(h, False, False, 0)
        for w in self.windows:
            row = Gtk.Box(spacing=4)
            b = Gtk.Button()
            inner = Gtk.Box(spacing=14)
            icon = self.icon(w.get("iconNight") if self.theme() == "night" else w.get("icon"))
            if icon:
                inner.pack_start(icon, False, False, 0)
            lab = Gtk.Label(label=(w.get("name") or w.get("title") or "")[:42], xalign=0)
            inner.pack_start(lab, True, True, 0)
            b.add(inner)
            b.set_size_request(280, -1)
            b.connect("clicked", lambda _b, xid=w["xid"]: self.go(["wmctrl", "-i", "-a", xid]))
            c = Gtk.Button(label="×")
            c.get_style_context().add_class("x")
            c.set_tooltip_text("Close")
            c.connect("clicked", lambda _b, xid=w["xid"]: self.go(["wmctrl", "-i", "-c", xid]))
            row.pack_start(b, True, True, 0)
            row.pack_start(c, False, False, 0)
            for el in (b, c, row):
                el.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            box.pack_start(row, False, False, 0)
        home = Gtk.Button(label="Home")
        home.get_child().set_xalign(0)
        home.get_style_context().add_class("home")
        home.connect("clicked", lambda _b: self.go(["wmctrl", "-k", "on"]))
        box.pack_start(home, False, False, 6)
        self.style(box, css)
        win.add(box)
        win.connect("focus-out-event", lambda *a: GLib.timeout_add(150, self.close) and False)
        win.connect("key-press-event", lambda _w, e: e.keyval == Gdk.KEY_Escape and self.close())
        self.list = win
        win.show_all()
        mon = Gdk.Display.get_default().get_primary_monitor() or Gdk.Display.get_default().get_monitor(0)
        r = mon.get_geometry()
        _, nat = win.get_preferred_size()
        win.move(r.x + 28, r.y + r.height - 92 - nat.height)
        win.present()
        self.win.queue_draw()
        return True

    def style(self, widget, css):
        widget.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if isinstance(widget, Gtk.Container):
            for ch in widget.get_children():
                self.style(ch, css)

    def icon(self, uri):
        if not uri or not uri.startswith("file://"):
            return None
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(GLib.filename_from_uri(uri)[0], 20, 20)
            return Gtk.Image.new_from_pixbuf(pb)
        except Exception:
            return None

    def go(self, cmd):
        subprocess.Popen(cmd, start_new_session=True)
        self.close()

    def close(self):
        if self.list:
            self.list.destroy()
            self.list = None
            self.win.queue_draw()
        return False
