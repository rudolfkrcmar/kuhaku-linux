#!/usr/bin/env python3
"""Screenshots the Mac way, in Kūhaku's quiet style.

    kuhaku-shot.py full | area | full-clip | area-clip | edit <file>

full   Cmd+Shift+3   the whole screen
area   Cmd+Shift+4   drag a crosshair over a part; Space picks a window; Esc cancels
*-clip (+ Ctrl)      copied instead of saved

Saved shots go to ~/Pictures/Screenshots and appear as a small picture in the
bottom-right corner for a few seconds: click it to mark it up (pen, arrow, box,
text, highlight, crop), or let it go.
"""

import math
import re
import subprocess
import sys
import time
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo
from kuhaku_log import report  # faults go to the log instead of vanishing

HOME = Path.home()
FOLDER = HOME / "Pictures" / "Screenshots"
STATE = HOME / ".local/state/kuhaku/state.json"
ACCENT = (0.878, 0.188, 0.118)
INK = {"red": ACCENT, "black": (0.067, 0.067, 0.067), "white": (1, 1, 1), "yellow": (1.0, 0.83, 0.2)}


def theme():
    try:
        import json

        t = json.loads(STATE.read_text()).get("theme")
        if t in ("day", "night"):
            return t
    except Exception:
        report()
    h = time.localtime().tm_hour
    return "night" if (h >= 20 or h < 7) else "day"


def grab_screen():
    root = Gdk.get_default_root_window()
    return Gdk.pixbuf_get_from_window(root, 0, 0, root.get_width(), root.get_height())


def new_path():
    FOLDER.mkdir(parents=True, exist_ok=True)
    return FOLDER / f"Screenshot {time.strftime('%Y-%m-%d at %H.%M.%S')}.png"


def windows_top_first():
    """Normal app windows under the pointer order: (x, y, w, h) with their frames."""
    out = []
    try:
        ids = re.findall(
            r"0x[0-9a-f]+",
            subprocess.run(
                ["xprop", "-root", "_NET_CLIENT_LIST_STACKING"], capture_output=True, text=True, timeout=2
            ).stdout,
        )
    except Exception:
        report()
        return out
    for xid in reversed(ids):
        try:
            info = subprocess.run(["xwininfo", "-id", xid], capture_output=True, text=True, timeout=2).stdout
            types = subprocess.run(
                ["xprop", "-id", xid, "_NET_WM_WINDOW_TYPE", "_NET_WM_STATE", "_NET_FRAME_EXTENTS"],
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout
        except Exception:
            report()
            continue
        if "DESKTOP" in types or "DOCK" in types or "HIDDEN" in types or "IsViewable" not in info:
            continue
        g = {
            k: int(v)
            for k, v in re.findall(r"(Absolute upper-left X|Absolute upper-left Y|Width|Height):\s+(-?\d+)", info)
        }
        ext = re.search(r"_NET_FRAME_EXTENTS\(CARDINAL\) = (\d+), (\d+), (\d+), (\d+)", types)
        l, r, t, b = (int(v) for v in ext.groups()) if ext else (0, 0, 0, 0)
        out.append(
            (g["Absolute upper-left X"] - l, g["Absolute upper-left Y"] - t, g["Width"] + l + r, g["Height"] + t + b)
        )
    return out


# --- choosing an area -------------------------------------------------------------------------
class Picker(Gtk.Window):
    """The frozen screen, dimmed; drag to choose, Space for a window, Esc to cancel."""

    def __init__(self, shot, done):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.shot, self.done = shot, done
        self.surface = Gdk.cairo_surface_create_from_pixbuf(shot, 1, None)
        self.start = self.end = None
        self.pointer = (0, 0)
        self.window_mode = False
        self.wins = None
        self.set_app_paintable(True)
        self.move(0, 0)
        self.resize(shot.get_width(), shot.get_height())
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
        )
        self.connect("draw", self.draw)
        self.connect("button-press-event", self.press)
        self.connect("button-release-event", self.release)
        self.connect("motion-notify-event", self.motion)
        self.connect("key-press-event", self.key)
        self.connect("map-event", lambda *a: GLib.timeout_add(30, self.grab) and False)
        self.show_all()

    def grab(self):
        gw = self.get_window()
        gw.set_cursor(Gdk.Cursor.new_from_name(gw.get_display(), "crosshair"))
        seat = Gdk.Display.get_default().get_default_seat()
        for _ in range(20):
            if seat.grab(gw, Gdk.SeatCapabilities.ALL, True, None, None, None, None) == Gdk.GrabStatus.SUCCESS:
                break
            time.sleep(0.05)
        return False

    def finish(self, rect):
        Gdk.Display.get_default().get_default_seat().ungrab()
        self.hide()
        self.done(rect)
        self.destroy()

    def rect(self):
        if self.window_mode:
            px, py = self.pointer
            if self.wins is None:
                self.wins = windows_top_first()
            for x, y, w, h in self.wins:
                if x <= px < x + w and y <= py < y + h:
                    return (max(0, x), max(0, y), w, h)
            return (0, 0, self.shot.get_width(), self.shot.get_height())
        if not self.start or not self.end:
            return None
        (a, b), (c, d) = self.start, self.end
        return (min(a, c), min(b, d), abs(c - a), abs(d - b))

    def draw(self, _w, cr):
        cr.set_source_surface(self.surface, 0, 0)
        cr.paint()
        r = self.rect()
        cr.set_source_rgba(0, 0, 0, 0.32)
        if r:
            x, y, w, h = r
            cr.rectangle(0, 0, self.shot.get_width(), self.shot.get_height())
            cr.rectangle(x, y, w, h)
            cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            cr.fill()
            cr.set_source_rgb(*ACCENT) if self.window_mode else cr.set_source_rgba(1, 1, 1, 0.9)
            cr.set_line_width(2 if self.window_mode else 1)
            cr.rectangle(x + 0.5, y + 0.5, w - 1, h - 1)
            cr.stroke()
            if not self.window_mode:
                self.label(cr, f"{w} × {h}", x + w + 10, y + h + 8)
        else:
            cr.paint()
            px, py = self.pointer
            self.label(cr, f"{px}, {py}", px + 14, py + 14)

    def label(self, cr, text, x, y):
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription("Inter 9"))
        layout.set_text(text, -1)
        tw, th = layout.get_pixel_size()
        x = min(x, self.shot.get_width() - tw - 16)
        y = min(y, self.shot.get_height() - th - 10)
        cr.set_source_rgba(0.07, 0.07, 0.07, 0.82)
        cr.rectangle(x, y, tw + 12, th + 6)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(x + 6, y + 3)
        PangoCairo.show_layout(cr, layout)

    def press(self, _w, e):
        if e.button == 3:
            return self.finish(None)
        if self.window_mode:
            return self.finish(self.rect())
        self.start = self.end = (int(e.x_root), int(e.y_root))
        self.queue_draw()

    def motion(self, _w, e):
        self.pointer = (int(e.x_root), int(e.y_root))
        if self.start:
            self.end = self.pointer
        self.queue_draw()

    def release(self, _w, e):
        if self.window_mode or not self.start:
            return
        r = self.rect()
        if r and r[2] > 3 and r[3] > 3:
            self.finish(r)
        else:
            self.start = self.end = None
            self.queue_draw()

    def key(self, _w, e):
        if e.keyval == Gdk.KEY_Escape:
            self.finish(None)
        elif e.keyval == Gdk.KEY_space and not self.start:
            self.window_mode = not self.window_mode
            gw = self.get_window()
            gw.set_cursor(Gdk.Cursor.new_from_name(gw.get_display(), "default" if self.window_mode else "crosshair"))
            self.queue_draw()
        return True


# --- the little picture in the corner ---------------------------------------------------------------
class Thumb(Gtk.Window):
    def __init__(self, path, pixbuf):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.path = path
        w = 220
        h = max(60, round(pixbuf.get_height() * w / max(1, pixbuf.get_width())))
        if h > 200:
            h, w = 200, round(pixbuf.get_width() * 200 / max(1, pixbuf.get_height()))
        self.small = pixbuf.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.set_default_size(w + 8, h + 8)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("draw", self.draw)
        self.connect("button-press-event", self.click)
        mon = Gdk.Display.get_default().get_primary_monitor() or Gdk.Display.get_default().get_monitor(0)
        r = mon.get_geometry()
        self.move(r.x + r.width - w - 8 - 36, r.y + r.height - h - 8 - 36)
        self.show_all()
        self.timer = GLib.timeout_add_seconds(6, self.leave)

    def draw(self, _w, cr):
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        Gdk.cairo_set_source_pixbuf(cr, self.small, 4, 4)
        cr.paint()
        cr.set_source_rgba(0, 0, 0, 0.15)
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, self.small.get_width() + 7, self.small.get_height() + 7)
        cr.stroke()

    def click(self, _w, e):
        GLib.source_remove(self.timer)
        self.destroy()
        if e.button == 3:  # right click: throw it away
            self.path.unlink(missing_ok=True)
            Gtk.main_quit()
            return
        Editor(self.path)

    def leave(self):
        self.destroy()
        Gtk.main_quit()
        return False


# --- marking it up ----------------------------------------------------------------------------------
class Editor(Gtk.Window):
    TOOLS = [
        ("pen", "Pen"),
        ("arrow", "Arrow"),
        ("box", "Box"),
        ("text", "Text"),
        ("mark", "Highlight"),
        ("crop", "Crop"),
    ]

    def __init__(self, path):
        super().__init__(title="Screenshot")
        self.path = Path(path)
        self.pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(self.path))
        self.surface = Gdk.cairo_surface_create_from_pixbuf(self.pixbuf, 1, None)
        self.items, self.cur, self.tool, self.ink = [], None, "arrow", "red"
        self.crop = None
        self.entry = None
        self.night = theme() == "night"
        bg, fg, muted = ("#121212", "#ececec", "#9a9a9a") if self.night else ("#f7f4f1", "#111111", "#666666")
        css = Gtk.CssProvider()
        css.load_from_data(
            f"""
          window {{ background:{bg}; }}
          .bar {{ padding:10px 24px; }}
          .bar button {{ background:none; border:0; box-shadow:none; border-radius:0; padding:6px 10px; color:{muted}; font-family:Inter; font-weight:300; }}
          .bar button label {{ color:{muted}; font-family:Inter; font-weight:300; }}
          .bar button:hover label, .bar button.on label {{ color:{fg}; }}
          .bar button.on {{ box-shadow: inset 0 -1.5px #e0301e; }}
          .bar button.done label {{ color:{fg}; }}
          .bar .ink {{ min-width:14px; min-height:14px; padding:0; margin:0 4px; border-radius:99px; }}
          .bar .ink.on {{ box-shadow: 0 0 0 2px {bg}, 0 0 0 3.5px {muted}; }}
          .bar .name {{ color:{muted}; font-family:Inter; font-weight:300; margin-right:18px; }}
          entry.textin {{ background:rgba(255,255,255,.9); color:#111; border:0; border-radius:0; font-family:Inter; font-size:18pt; }}
        """.encode()
        )
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.set_decorated(False)
        self.maximize()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar = Gtk.Box(spacing=2)
        bar.get_style_context().add_class("bar")
        name = Gtk.Label(label="Mark up")
        name.get_style_context().add_class("name")
        bar.pack_start(name, False, False, 0)
        self.tool_buttons = {}
        for key, label in self.TOOLS:
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, k=key: self.set_tool(k))
            bar.pack_start(b, False, False, 0)
            self.tool_buttons[key] = b
        sep = Gtk.Box()
        sep.set_size_request(18, 1)
        bar.pack_start(sep, False, False, 0)
        self.ink_buttons = {}
        for key, rgb in INK.items():
            b = Gtk.Button()
            b.get_style_context().add_class("ink")
            p = Gtk.CssProvider()
            p.load_from_data(
                (
                    "button { background:rgb(%d,%d,%d); border:1px solid rgba(0,0,0,.18); }"
                    % tuple(round(c * 255) for c in rgb)
                ).encode()
            )
            b.get_style_context().add_provider(p, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
            b.set_valign(Gtk.Align.CENTER)
            b.connect("clicked", lambda _b, k=key: self.set_ink(k))
            bar.pack_start(b, False, False, 0)
            self.ink_buttons[key] = b
        for label, fn, cls in (
            ("Done", self.save_close, "done"),
            ("Copy", self.copy, ""),
            ("Delete", self.delete, ""),
            ("Undo", self.undo, ""),
        ):
            b = Gtk.Button(label=label)
            if cls:
                b.get_style_context().add_class(cls)
            b.connect("clicked", lambda _b, f=fn: f())
            bar.pack_end(b, False, False, 0)
        box.pack_start(bar, False, False, 0)
        self.overlay = Gtk.Overlay()
        self.area = Gtk.DrawingArea()
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.area.connect("draw", self.draw)
        self.area.connect("button-press-event", self.press)
        self.area.connect("motion-notify-event", self.motion)
        self.area.connect("button-release-event", self.release)
        self.overlay.add(self.area)
        self.fixed = Gtk.Fixed()
        self.fixed.set_halign(Gtk.Align.START)
        self.fixed.set_valign(Gtk.Align.START)
        self.overlay.add_overlay(self.fixed)
        self.overlay.set_overlay_pass_through(self.fixed, True)
        box.pack_start(self.overlay, True, True, 0)
        self.add(box)
        self.connect("key-press-event", self.key)
        self.connect("destroy", lambda *a: Gtk.main_quit())
        self.set_tool("arrow")
        self.set_ink("red")
        self.show_all()

    # view <-> image coordinates
    def fit(self):
        aw, ah = self.area.get_allocated_width(), self.area.get_allocated_height()
        iw, ih = self.pixbuf.get_width(), self.pixbuf.get_height()
        s = min((aw - 80) / iw, (ah - 60) / ih, 1.0)
        return s, (aw - iw * s) / 2, (ah - ih * s) / 2

    def to_img(self, x, y):
        s, ox, oy = self.fit()
        return ((x - ox) / s, (y - oy) / s)

    def set_tool(self, k):
        self.commit_text()
        self.tool = k
        for key, b in self.tool_buttons.items():
            ctx = b.get_style_context()
            ctx.add_class("on") if key == k else ctx.remove_class("on")

    def set_ink(self, k):
        self.ink = k
        for key, b in self.ink_buttons.items():
            ctx = b.get_style_context()
            ctx.add_class("on") if key == k else ctx.remove_class("on")

    def paint(self, cr, items, crop_preview=True):
        cr.set_source_surface(self.surface, 0, 0)
        cr.paint()
        for it in items:
            rgb = INK[it["ink"]]
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            if it["tool"] == "pen":
                cr.set_source_rgb(*rgb)
                cr.set_line_width(4)
                for i, (x, y) in enumerate(it["pts"]):
                    cr.line_to(x, y) if i else cr.move_to(x, y)
                cr.stroke()
            elif it["tool"] == "mark":
                cr.set_source_rgba(*rgb, 0.35)
                cr.set_line_width(22)
                cr.set_line_cap(cairo.LINE_CAP_SQUARE)
                for i, (x, y) in enumerate(it["pts"]):
                    cr.line_to(x, y) if i else cr.move_to(x, y)
                cr.stroke()
            elif it["tool"] == "box":
                (a, b), (c, d) = it["pts"][0], it["pts"][-1]
                cr.set_source_rgb(*rgb)
                cr.set_line_width(4)
                cr.rectangle(min(a, c), min(b, d), abs(c - a), abs(d - b))
                cr.stroke()
            elif it["tool"] == "arrow":
                (a, b), (c, d) = it["pts"][0], it["pts"][-1]
                if math.hypot(c - a, d - b) < 4:
                    continue
                ang = math.atan2(d - b, c - a)
                cr.set_source_rgb(*rgb)
                cr.set_line_width(5)
                cr.move_to(a, b)
                cr.line_to(c - 14 * math.cos(ang), d - 14 * math.sin(ang))
                cr.stroke()
                cr.move_to(c, d)
                for da in (2.6, -2.6):
                    cr.line_to(c + 26 * math.cos(ang + da), d + 26 * math.sin(ang + da))
                cr.close_path()
                cr.fill()
            elif it["tool"] == "text":
                layout = PangoCairo.create_layout(cr)
                layout.set_font_description(Pango.FontDescription("Inter Medium 22"))
                layout.set_text(it["text"], -1)
                x, y = it["pts"][0]
                if it["ink"] != "white":  # a soft halo keeps text readable on any picture
                    cr.set_source_rgba(1, 1, 1, 0.85)
                    cr.move_to(x, y)
                    PangoCairo.layout_path(cr, layout)
                    cr.set_line_width(5)
                    cr.stroke()
                cr.set_source_rgb(*rgb)
                cr.move_to(x, y)
                PangoCairo.show_layout(cr, layout)

    def draw(self, _w, cr):
        s, ox, oy = self.fit()
        cr.translate(ox, oy)
        cr.scale(s, s)
        cr.set_source_rgba(0, 0, 0, 0.12)
        cr.rectangle(-1 / s, -1 / s, self.pixbuf.get_width() + 2 / s, self.pixbuf.get_height() + 2 / s)
        cr.fill()
        self.paint(cr, self.items + ([self.cur] if self.cur and self.cur["tool"] != "crop" else []))
        c = self.cur["pts"] if self.cur and self.cur["tool"] == "crop" else self.crop
        if c:
            (a, b), (x2, y2) = c[0], c[-1]
            cr.set_source_rgba(0, 0, 0, 0.45)
            cr.rectangle(0, 0, self.pixbuf.get_width(), self.pixbuf.get_height())
            cr.rectangle(min(a, x2), min(b, y2), abs(x2 - a), abs(y2 - b))
            cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            cr.fill()

    def press(self, _w, e):
        if e.button != 1:
            return
        self.commit_text()
        p = self.to_img(e.x, e.y)
        if self.tool == "text":
            return self.start_text(e.x, e.y, p)
        self.cur = {"tool": self.tool, "ink": self.ink, "pts": [p]}

    def motion(self, _w, e):
        if self.cur:
            p = self.to_img(e.x, e.y)
            if self.cur["tool"] in ("pen", "mark"):
                self.cur["pts"].append(p)
            else:
                self.cur["pts"] = [self.cur["pts"][0], p]
            self.area.queue_draw()

    def release(self, _w, e):
        if not self.cur:
            return
        if self.cur["tool"] == "crop":
            (a, b), (c, d) = self.cur["pts"][0], self.cur["pts"][-1]
            self.crop = [(a, b), (c, d)] if abs(c - a) > 8 and abs(d - b) > 8 else None
        else:
            self.items.append(self.cur)
        self.cur = None
        self.area.queue_draw()

    def start_text(self, vx, vy, p):
        self.entry = Gtk.Entry()
        self.entry.get_style_context().add_class("textin")
        self.entry.set_placeholder_text("Text")
        self.entry.set_width_chars(18)
        self.entry_pos = p
        self.fixed.put(self.entry, int(vx), int(vy))
        self.overlay.set_overlay_pass_through(self.fixed, False)
        self.entry.show()
        self.entry.grab_focus()
        self.entry.connect("activate", lambda *a: self.commit_text())

    def commit_text(self):
        if not self.entry:
            return
        t = self.entry.get_text().strip()
        if t:
            self.items.append({"tool": "text", "ink": self.ink, "pts": [self.entry_pos], "text": t})
        self.entry.destroy()
        self.entry = None
        self.overlay.set_overlay_pass_through(self.fixed, True)
        self.area.queue_draw()

    def undo(self):
        if self.crop:
            self.crop = None
        elif self.items:
            self.items.pop()
        self.area.queue_draw()

    def render(self):
        w, h = self.pixbuf.get_width(), self.pixbuf.get_height()
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        self.paint(cairo.Context(surf), self.items)
        if self.crop:
            (a, b), (c, d) = self.crop
            x, y = max(0, int(min(a, c))), max(0, int(min(b, d)))
            cw, ch = min(w - x, int(abs(c - a))), min(h - y, int(abs(d - b)))
            out = cairo.ImageSurface(cairo.FORMAT_ARGB32, cw, ch)
            cr = cairo.Context(out)
            cr.set_source_surface(surf, -x, -y)
            cr.paint()
            surf = out
        return surf

    def save_close(self):
        self.commit_text()
        if self.items or self.crop:
            self.render().write_to_png(str(self.path))
        self.destroy()

    def copy(self):
        self.commit_text()
        tmp = Path("/tmp/kuhaku-shot-copy.png")
        self.render().write_to_png(str(tmp))
        clip_image(GdkPixbuf.Pixbuf.new_from_file(str(tmp)), keep_running=False)
        if self.items or self.crop:
            self.render().write_to_png(str(self.path))
        self.hide()
        hold_clipboard()

    def delete(self):
        self.path.unlink(missing_ok=True)
        self.destroy()

    def key(self, _w, e):
        cmd = e.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD4_MASK)
        if self.entry:
            if e.keyval == Gdk.KEY_Escape:
                self.entry.set_text("")
                self.commit_text()
                return True
            return False
        if e.keyval == Gdk.KEY_Escape or e.keyval == Gdk.KEY_Return:
            self.save_close()
        elif cmd and e.keyval in (Gdk.KEY_z, Gdk.KEY_Z):
            self.undo()
        elif cmd and e.keyval in (Gdk.KEY_c, Gdk.KEY_C):
            self.copy()
        elif e.keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
            self.delete()
        return True


# --- clipboard -----------------------------------------------------------------------------
def clip_image(pixbuf, keep_running=True):
    cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    cb.set_image(pixbuf)
    cb.store()
    if keep_running:
        hold_clipboard()


def hold_clipboard():
    """Without a clipboard manager the picture lives as long as this process: stay until
    something else is copied (or ten minutes pass)."""
    cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    GLib.timeout_add(400, lambda: cb.connect("owner-change", lambda *a: Gtk.main_quit()) and False)
    GLib.timeout_add_seconds(600, Gtk.main_quit)


def main():
    how = sys.argv[1] if len(sys.argv) > 1 else "full"
    if how == "edit" and len(sys.argv) > 2:
        Editor(sys.argv[2])
        Gtk.main()
        return
    clip = how.endswith("-clip")
    time.sleep(0.12)
    shot = grab_screen()

    def done(rect):
        if not rect:
            Gtk.main_quit()
            return
        x, y, w, h = rect
        x, y = max(0, x), max(0, y)
        w, h = min(w, shot.get_width() - x), min(h, shot.get_height() - y)
        if w <= 0 or h <= 0:
            Gtk.main_quit()
            return
        part = shot.new_subpixbuf(x, y, w, h).copy()
        play_sound()
        if clip:
            clip_image(part)
            return
        path = new_path()
        part.savev(str(path), "png", [], [])
        Thumb(path, part)

    if how.startswith("area"):
        Picker(shot, done)
    else:
        GLib.idle_add(lambda: done((0, 0, shot.get_width(), shot.get_height())) and False)
    Gtk.main()


def play_sound():
    for f in (
        "/usr/share/sounds/freedesktop/stereo/camera-shutter.oga",
        "/usr/share/sounds/freedesktop/stereo/screen-capture.oga",
    ):
        if Path(f).exists():
            subprocess.Popen(["pw-play", f], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return


if __name__ == "__main__":
    main()
