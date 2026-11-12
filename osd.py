"""Kūhaku's on-screen feedback for the volume and brightness keys.

Kūhaku watches the volume (pactl subscribe) and the backlight itself, so any
change, from the keys or anywhere else, shows a small calm card above
everything for a moment, then fades away.
"""
import math
import re
import subprocess
import os
import threading
import time
from pathlib import Path

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo
from kuhaku_log import report  # faults go to the log instead of vanishing
from kuhaku_log import report  # faults go to the log instead of vanishing

BRIGHT_MARK = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "kuhaku-brightness-asked"
W, H = 380, 104          # volume, brightness: label, number and a line
PW, PH = 220, 132        # charger: just the percentage
THEMES = {
    "day":   {"bg": (0.969, 0.957, 0.945), "fg": (0.067, 0.067, 0.067), "muted": (0.4, 0.4, 0.4),
              "faint": (0.067, 0.067, 0.067, 0.08), "accent": (0.878, 0.188, 0.118)},
    "night": {"bg": (0.071, 0.071, 0.071), "fg": (0.925, 0.925, 0.925), "muted": (0.604, 0.604, 0.604),
              "faint": (0.925, 0.925, 0.925, 0.10), "accent": (0.941, 0.439, 0.353)},
}


def read_volume():
    try:
        vol = subprocess.run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                             capture_output=True, text=True, timeout=2).stdout
        mute = subprocess.run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"],
                              capture_output=True, text=True, timeout=2).stdout
        levels = [int(x) for x in re.findall(r"(\d+)%", vol)]
        return (round(sum(levels) / len(levels)) if levels else 0, "yes" in mute)
    except Exception:
        report()
        return None


def read_mic_mute():
    try:
        out = subprocess.run(["pactl", "get-source-mute", "@DEFAULT_SOURCE@"],
                             capture_output=True, text=True, timeout=2).stdout
        return "yes" in out if out else None
    except Exception:
        report()
        return None


class Osd:
    def __init__(self, theme_fn):
        self.theme_fn = theme_fn
        self.kind, self.level, self.muted = "Volume", 0, False
        self.hide_id = self.fade_id = None
        self.alpha = 0.0

        self.win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.win.set_app_paintable(True)
        self.win.set_accept_focus(False)
        self.win.set_keep_above(True)
        self.win.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        visual = self.win.get_screen().get_rgba_visual()
        if visual:
            self.win.set_visual(visual)
        self.win.set_default_size(W, H)
        self.win.connect("draw", self.draw)
        self.win.realize()
        # Clicks pass straight through.
        self.win.input_shape_combine_region(cairo.Region())

        self.volume = read_volume()
        self.mic_muted = read_mic_mute()
        threading.Thread(target=self.watch_volume, daemon=True).start()
        self.backlight = next(iter(sorted(Path("/sys/class/backlight").glob("*"))), None)
        self.bright = self.read_brightness()
        self.mains = next((p for p in Path("/sys/class/power_supply").glob("*")
                           if (p / "type").exists() and (p / "type").read_text().strip() == "Mains"), None)
        self.battery = next((p for p in sorted(Path("/sys/class/power_supply").glob("*"))
                             if (p / "type").exists() and (p / "type").read_text().strip() == "Battery"), None)
        self.plugged = self.read_plugged()
        GLib.timeout_add(150, self.poll_brightness)
        GLib.timeout_add(700, self.poll_power)

    # --- sources ------------------------------------------------------------
    def watch_volume(self):
        try:
            proc = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE, text=True)
        except FileNotFoundError:
            return
        for line in proc.stdout:
            if "on sink #" in line or "on server" in line:
                vol = read_volume()
                if vol and vol != self.volume:
                    first = self.volume is None
                    self.volume = vol
                    if not first:
                        GLib.idle_add(self.show, "Volume", vol[0], vol[1])
            if "on source #" in line or "on server" in line:
                m = read_mic_mute()
                if m is not None and m != self.mic_muted:
                    first = self.mic_muted is None
                    self.mic_muted = m
                    if not first:
                        GLib.idle_add(self.message, "Microphone", "Muted" if m else "On")

    def read_brightness(self):
        try:
            b = self.backlight
            return round(100 * int((b / "brightness").read_text()) / int((b / "max_brightness").read_text()))
        except Exception:
            report()
            return None

    def poll_brightness(self):
        if self.backlight:
            b = self.read_brightness()
            if b is not None and b != self.bright:
                self.bright = b
                # Only a change the person made (keys, the settings slider) is shown. The screen
                # dimming by itself when idle or unplugged is not news and must not interrupt.
                try:
                    asked = time.time() - BRIGHT_MARK.stat().st_mtime < 3
                except OSError:
                    asked = False
                if asked:
                    self.show("Brightness", b, False)
        return True

    def read_plugged(self):
        try:
            return (self.mains / "online").read_text().strip() == "1"
        except Exception:
            report()
            return None

    def battery_level(self):
        b = self.battery
        try:
            for now, full in (("energy_now", "energy_full"), ("charge_now", "charge_full")):
                if (b / now).exists():
                    return min(100, round(100 * int((b / now).read_text()) / int((b / full).read_text())))
            return int((b / "capacity").read_text())
        except Exception:
            report()
            return 0

    def poll_power(self):
        p = self.read_plugged()
        if p is not None and p != self.plugged:
            self.plugged = p
            self.show("Charging" if p else "On battery", self.battery_level(), False, power=True)
        return True

    # --- showing --------------------------------------------------------------
    def message(self, title, detail):
        """A word and a quiet line, e.g. Screenshot / Saved to Pictures."""
        self.detail = detail
        self.show(title, 0, False, power="message")

    def show(self, kind, level, muted, power=False):
        self.kind, self.level, self.muted, self.power = kind, level, muted, power
        w, h = (PW, PH) if power else (W, H)
        self.size = (w, h)
        # Text size in Settings (Xft DPI) grows the notice with everything else.
        dpi = Gtk.Settings.get_default().get_property("gtk-xft-dpi")
        self.s = max(0.75, min(2.0, dpi / 1024 / 96)) if dpi and dpi > 0 else 1.0
        w, h = round(w * self.s), round(h * self.s)
        display = Gdk.Display.get_default()
        mon = display.get_primary_monitor() or display.get_monitor(0)
        g = mon.get_geometry()
        self.win.resize(w, h)
        self.win.move(g.x + (g.width - w) // 2, g.y + int(g.height * 0.80) - h // 2)
        if self.fade_id:
            GLib.source_remove(self.fade_id)
            self.fade_id = None
        self.alpha = 1.0
        self.win.set_opacity(1.0)
        self.win.show()
        self.win.queue_draw()
        if self.hide_id:
            GLib.source_remove(self.hide_id)
        self.hide_id = GLib.timeout_add(2400 if power else 1400, self.fade)
        return False

    def fade(self):
        self.hide_id = None

        def step():
            self.alpha -= 0.08
            if self.alpha <= 0:
                self.win.hide()
                self.fade_id = None
                return False
            self.win.set_opacity(self.alpha)
            return True
        self.fade_id = GLib.timeout_add(16, step)
        return False

    def draw(self, _w, cr):
        t = THEMES[self.theme_fn()]
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.scale(getattr(self, "s", 1.0), getattr(self, "s", 1.0))

        CW, CH = getattr(self, "size", (W, H))
        r, x, y, w, h = 22, 0.5, 0.5, CW - 1, CH - 1
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgba(*t["bg"], 0.97)
        cr.fill_preserve()
        cr.set_source_rgba(*t["faint"])
        cr.set_line_width(1)
        cr.stroke()

        def text(s, size, color, weight, right=None):
            layout = PangoCairo.create_layout(cr)
            fd = Pango.FontDescription.from_string(f"Inter Variable {size}")
            fd.set_weight(weight)
            layout.set_font_description(fd)
            layout.set_text(s, -1)
            tw, th = layout.get_pixel_size()
            cr.set_source_rgb(*color)
            cr.move_to(right - tw if right else 32, 50 - th)
            PangoCairo.show_layout(cr, layout)

        if getattr(self, "power", False) == "message":
            def centred(s, size, color, weight, top):
                layout = PangoCairo.create_layout(cr)
                fd = Pango.FontDescription.from_string(f"Inter Variable {size}")
                fd.set_weight(weight)
                layout.set_font_description(fd)
                layout.set_text(s, -1)
                tw, _th = layout.get_pixel_size()
                cr.set_source_rgb(*color)
                cr.move_to((CW - tw) / 2, top)
                PangoCairo.show_layout(cr, layout)
                return tw
            tw = centred(self.kind, 17, t["fg"], Pango.Weight.LIGHT, 36)
            centred(self.detail, 11, t["muted"], Pango.Weight.LIGHT, 74)
            if self.kind == "Recording" and "stop" in self.detail:
                cr.set_source_rgb(*t["accent"])
                cr.arc((CW - tw) / 2 - 14, 49, 3.5, 0, 2 * math.pi)
                cr.fill()
            return True
        if getattr(self, "power", False):
            # The number alone, and one quiet word under it.
            def centred(s, size, color, weight, top):
                layout = PangoCairo.create_layout(cr)
                fd = Pango.FontDescription.from_string(f"Inter Variable {size}")
                fd.set_weight(weight)
                layout.set_font_description(fd)
                layout.set_text(s, -1)
                tw, _th = layout.get_pixel_size()
                cr.set_source_rgb(*color)
                cr.move_to((CW - tw) / 2, top)
                PangoCairo.show_layout(cr, layout)
                return tw
            centred(f"{self.level}%", 30, t["fg"], Pango.Weight.ULTRALIGHT, 22)
            tw = centred(self.kind, 11, t["muted"], Pango.Weight.LIGHT, 86)
            if self.kind == "Charging":
                cr.set_source_rgb(*t["accent"])
                cr.arc((CW - tw) / 2 - 11, 94, 3, 0, 2 * math.pi)
                cr.fill()
            return True

        label = "Muted" if self.muted else self.kind
        text(label, 11, t["muted"] if not self.muted else t["fg"], Pango.Weight.LIGHT)
        if not self.muted:
            text(f"{self.level}%", 17, t["fg"], Pango.Weight.LIGHT, right=W - 32)

        bx, by, bw = 32, 66, W - 64
        cr.set_source_rgba(*t["faint"])
        cr.rectangle(bx, by, bw, 2)
        cr.fill()
        fill = 0 if self.muted else bw * min(100, self.level) / 100
        cr.set_source_rgb(*t["accent"])
        cr.rectangle(bx, by, fill, 2)
        cr.fill()
        if not self.muted:
            cr.arc(bx + fill, by + 1, 3.5, 0, 2 * math.pi)
            cr.fill()
        return True
