"""The touch keyboard itself: layouts, key drawings, the Keyboard window and the folded-away pill."""

import math
import os
import sys
import threading
import time

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402
from tablet_base import (
    FONT,
    LANGS,
    THEMES,
    current_theme,
    draw_ink,
    ease,
    is_finger,
    log,
    pressure,
    recognize,
    rounded,
    save_settings,
)


def text_at(cr, s, px, color, cx, cy, weight=Pango.Weight.LIGHT, alpha=1.0, align="center"):
    layout = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription.from_string(FONT)
    fd.set_absolute_size(px * Pango.SCALE)
    fd.set_weight(weight)
    layout.set_font_description(fd)
    layout.set_text(s, -1)
    _ink, logical = layout.get_pixel_extents()
    tw, th = logical.width, logical.height
    x = cx - tw / 2 if align == "center" else (cx if align == "left" else cx - tw)
    cr.set_source_rgba(*color[:3], alpha)
    cr.move_to(x, cy - th / 2)
    PangoCairo.show_layout(cr, layout)
    return tw


# --- the keyboard ------------------------------------------------------------------------

ALTS = {
    "q": "1",
    "w": "2",
    "e": "3éěèêë",
    "r": "4ř",
    "t": "5ť",
    "y": "6ýÿ",
    "u": "7úůüûù",
    "i": "8íìîï",
    "o": "9óöôòøœ",
    "p": "0",
    "a": "áäàâåæ",
    "s": "šßś",
    "d": "ď",
    "f": "",
    "g": "ğ",
    "h": "",
    "j": "",
    "k": "",
    "l": "ľłĺ",
    "z": "žźż",
    "x": "",
    "c": "čçć",
    "v": "",
    "b": "",
    "n": "ňñń",
    "m": "",
    ".": "…!?;:",
    ",": "\"'„“”",
    "-": "–—_",
    "'": "‘’`\"",
    "?": "¿",
    "!": "¡",
    "€": "$£¥¢",
    "\"": "„“”«»",
}


def K(label, w=1.0, act=None, alts=None):
    return {
        "label": label,
        "w": w,
        "act": act or ("char", label),
        "alts": ALTS.get(label, "") if alts is None else alts,
    }


def row_bottom(layer_key):
    return [
        K("123" if layer_key == "abc" else "abc", 1.5, ("layer", "123" if layer_key == "abc" else "abc")),
        K("ctrl", 1, ("mod", "Control_L")),
        K("lang", 1, ("lang",)),
        K("space", 5, ("space",)),
        K("left", 1, ("key", "Left")),
        K("right", 1, ("key", "Right")),
        K("hide", 1, ("hide",)),
    ]


def layout(layer):
    if layer == "abc":
        return [
            [K(c) for c in "qwertyuiop"] + [K("back", 1.5, ("back",))],
            [K("", 0.5, ("none",))] + [K(c) for c in "asdfghjkl"] + [K("return", 2, ("key", "Return"))],
            [K("shift", 1.5, ("shift",))] + [K(c) for c in "zxcvbnm,."] + [K("shift", 1, ("shift",))],
            row_bottom("abc"),
        ]
    if layer == "123":
        return [
            [K(c) for c in "1234567890"] + [K("back", 1.5, ("back",))],
            [K("", 0.5, ("none",))] + [K(c) for c in "-/:;()€&@"] + [K("return", 2, ("key", "Return"))],
            [K("#+=", 1.5, ("layer", "sym"))] + [K(c) for c in ".,?!'\"_=*"] + [K("tab", 1, ("key", "Tab"))],
            row_bottom("123"),
        ]
    return [
        [K(c) for c in "[]{}#%^*+="] + [K("back", 1.5, ("back",))],
        [K("", 0.5, ("none",))] + [K(c) for c in "_\\|~<>$£¥"] + [K("return", 2, ("key", "Return"))],
        [K("123", 1.5, ("layer", "123"))] + [K(c) for c in ".,?!'\"`•°"] + [K("tab", 1, ("key", "Tab"))],
        row_bottom("sym"),
    ]


def icon(cr, name, cx, cy, s, color, alpha=1.0):
    """Thin line icons in one hand: shift, delete, return, hide, arrows, tab."""
    cr.save()
    cr.set_source_rgba(*color[:3], alpha)
    cr.set_line_width(max(1.4, s * 0.075))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    h = s / 2
    if name in ("shift", "shift-on", "caps"):
        cr.move_to(cx, cy - h * 0.8)
        cr.line_to(cx + h * 0.75, cy + h * 0.05)
        cr.line_to(cx + h * 0.35, cy + h * 0.05)
        cr.line_to(cx + h * 0.35, cy + h * 0.6)
        cr.line_to(cx - h * 0.35, cy + h * 0.6)
        cr.line_to(cx - h * 0.35, cy + h * 0.05)
        cr.line_to(cx - h * 0.75, cy + h * 0.05)
        cr.close_path()
        if name != "shift":
            cr.fill_preserve()
        cr.stroke()
        if name == "caps":
            cr.move_to(cx - h * 0.35, cy + h * 0.85)
            cr.line_to(cx + h * 0.35, cy + h * 0.85)
            cr.stroke()
    elif name == "back":
        cr.move_to(cx - h * 0.95, cy)
        cr.line_to(cx - h * 0.45, cy - h * 0.55)
        cr.line_to(cx + h * 0.9, cy - h * 0.55)
        cr.line_to(cx + h * 0.9, cy + h * 0.55)
        cr.line_to(cx - h * 0.45, cy + h * 0.55)
        cr.close_path()
        cr.stroke()
        d = h * 0.22
        cr.move_to(cx + h * 0.22 - d, cy - d)
        cr.line_to(cx + h * 0.22 + d, cy + d)
        cr.move_to(cx + h * 0.22 + d, cy - d)
        cr.line_to(cx + h * 0.22 - d, cy + d)
        cr.stroke()
    elif name == "return":
        cr.move_to(cx + h * 0.8, cy - h * 0.6)
        cr.line_to(cx + h * 0.8, cy + h * 0.15)
        cr.line_to(cx - h * 0.8, cy + h * 0.15)
        cr.stroke()
        cr.move_to(cx - h * 0.45, cy - h * 0.2)
        cr.line_to(cx - h * 0.8, cy + h * 0.15)
        cr.line_to(cx - h * 0.45, cy + h * 0.5)
        cr.stroke()
    elif name == "tab":
        cr.move_to(cx - h * 0.8, cy)
        cr.line_to(cx + h * 0.6, cy)
        cr.move_to(cx + h * 0.25, cy - h * 0.35)
        cr.line_to(cx + h * 0.6, cy)
        cr.line_to(cx + h * 0.25, cy + h * 0.35)
        cr.move_to(cx + h * 0.8, cy - h * 0.45)
        cr.line_to(cx + h * 0.8, cy + h * 0.45)
        cr.stroke()
    elif name in ("left", "right"):
        d = -1 if name == "left" else 1
        cr.move_to(cx - d * h * 0.25, cy - h * 0.45)
        cr.line_to(cx + d * h * 0.25, cy)
        cr.line_to(cx - d * h * 0.25, cy + h * 0.45)
        cr.stroke()
    elif name == "hide":
        for i, yy in enumerate((-0.55, -0.15)):
            for j in range(4 - i):
                xx = cx + (j - (3 - i) / 2) * h * 0.42
                cr.arc(xx, cy + yy * h, s * 0.035, 0, 2 * math.pi)
                cr.fill()
        cr.move_to(cx - h * 0.4, cy + h * 0.3)
        cr.line_to(cx, cy + h * 0.65)
        cr.line_to(cx + h * 0.4, cy + h * 0.3)
        cr.stroke()
    elif name == "pen":
        cr.move_to(cx - h * 0.7, cy + h * 0.7)
        cr.line_to(cx - h * 0.55, cy + h * 0.2)
        cr.line_to(cx + h * 0.45, cy - h * 0.8)
        cr.line_to(cx + h * 0.8, cy - h * 0.45)
        cr.line_to(cx - h * 0.2, cy + h * 0.55)
        cr.close_path()
        cr.stroke()
    elif name == "keys":
        rounded(cr, cx - h * 0.9, cy - h * 0.55, h * 1.8, h * 1.1, h * 0.2)
        cr.stroke()
        cr.move_to(cx - h * 0.45, cy + h * 0.2)
        cr.line_to(cx + h * 0.45, cy + h * 0.2)
        cr.stroke()
    cr.restore()


class Keyboard:
    def __init__(self, app):
        self.app = app
        self.layer = "abc"
        self.shift = 0  # 0 off, 1 next letter, 2 caps
        self.shift_tap = 0.0
        self.mods = set()
        self.mode = "keys"  # or "write": the pad
        self.touches = {}
        self.anims = []  # (kind, x, y, start)
        self.bubble = None  # (key, start, ending)
        self.alts = None  # {"key","items","rects","sel","touch"}
        self.last_chars = []  # (char, time), what this keyboard typed lately
        self.visible = False
        self.slide = 0.0  # 0 hidden .. 1 shown
        self.slide_from = 0.0
        self.slide_to = 0.0
        self.slide_t0 = 0.0
        self.tick_id = None
        self.repeat_id = None
        self.pad = {"strokes": [], "cur": None, "timer": None, "cands": [], "committed": "", "busy": False}

        self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.win.set_title("Kūhaku keyboard")
        self.win.set_wmclass("kuhaku-keyboard", "Kuhaku-keyboard")
        self.win.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.win.set_accept_focus(False)
        self.win.set_focus_on_map(False)
        self.win.set_keep_above(True)
        self.win.set_decorated(False)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_skip_pager_hint(True)
        self.win.stick()
        self.win.set_app_paintable(True)
        visual = self.win.get_screen().get_rgba_visual()
        if visual:
            self.win.set_visual(visual)
        self.area = Gtk.DrawingArea()
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.TOUCH_MASK
        )
        self.area.connect("draw", self.draw)
        self.area.connect("button-press-event", self.on_button)
        self.area.connect("button-release-event", self.on_button)
        self.area.connect("motion-notify-event", self.on_motion)
        self.area.connect("touch-event", self.on_touch)
        self.win.add(self.area)
        self.geometry()
        self.win.get_screen().connect("size-changed", lambda *_: self.geometry())

    # --- geometry --------------------------------------------------------------------------
    def geometry(self):
        display = Gdk.Display.get_default()
        mon = display.get_primary_monitor() or display.get_monitor(0)
        g = mon.get_geometry()
        self.mon = g
        portrait = g.height > g.width
        self.kh = round(max(52, min(92, (g.width if portrait else g.height) * (0.075 if portrait else 0.062))))
        self.gap = round(self.kh * 0.13)
        self.strip = round(self.kh * 0.74)
        self.body = self.strip + 4 * self.kh + 5 * self.gap
        self.margin = round(self.kh * 1.55)  # room above the keys for previews and accents
        self.W, self.H = g.width, self.body + self.margin
        self.keys_w = min(g.width - 2 * self.gap, round(self.kh * 22.5))
        self.keys_x = (g.width - self.keys_w) / 2
        self.win.resize(self.W, self.H)
        self.win.move(g.x, g.y + g.height - self.H)
        self.relayout()
        if self.visible:
            self.set_strut(True)
            self.set_input()

    def relayout(self):
        self.rects = []
        top = self.margin + self.strip + self.gap
        unit = (self.keys_w + self.gap) / 11.5
        for r, row in enumerate(layout(self.layer)):
            x = self.keys_x
            y = top + r * (self.kh + self.gap)
            for k in row:
                w = k["w"] * unit - self.gap
                if k["act"][0] != "none":
                    self.rects.append((k, x, y, w, self.kh))
                x += k["w"] * unit
        # the writing pad: the key area, with a narrow column of keys at the right
        col = 1.6 * unit - self.gap
        px = self.keys_x
        self.pad_rect = (px, top, self.keys_w - col - self.gap, 4 * self.kh + 3 * self.gap)
        cx = px + self.keys_w - col
        self.pad_keys = []
        for i, (lab, act) in enumerate(
            (("back", ("back",)), ("space", ("space",)), ("return", ("key", "Return")), ("keys", ("mode", "keys")))
        ):
            self.pad_keys.append((K(lab, 1, act, ""), cx, top + i * (self.kh + self.gap), col, self.kh))
        # the strip: Keys / Write on the left, pen and hide on the right
        sy = self.margin
        s = self.strip
        self.strip_buttons = [
            ("mode-keys", (self.keys_x, sy, s * 1.9, s)),
            ("mode-write", (self.keys_x + s * 1.9, sy, s * 1.9, s)),
            ("pen", (self.keys_x + self.keys_w - 2 * s, sy, s, s)),
            ("hide", (self.keys_x + self.keys_w - s, sy, s, s)),
        ]

    def set_input(self):
        gw = self.win.get_window()
        if not gw:
            return
        region = cairo.Region(cairo.RectangleInt(0, self.margin, self.W, self.body))
        if self.alts:
            x, y, w, h = self.alts["box"]
            region.union(cairo.RectangleInt(int(x), int(y), int(w) + 1, int(h) + 1))
        self.win.input_shape_combine_region(region)

    def set_strut(self, on):
        gw = self.win.get_window()
        if gw:
            self.app.props.strut(gw.get_xid(), self.body if on else 0, self.mon.x, self.mon.x + self.mon.width - 1)

    def body_rect(self):
        """Where the keyboard is on the screen, for the ink layer to leave alone."""
        if not self.visible:
            return None
        return (self.mon.x, self.mon.y + self.mon.height - self.body, self.mon.width, self.body)

    # --- showing ---------------------------------------------------------------------------
    def show(self):
        if self.visible:
            return
        log("keyboard up")
        self.visible = True
        self.theme = THEMES[current_theme()]
        self.geometry()
        self.win.show_all()
        self.set_input()
        self.set_strut(True)
        self.animate_slide(1.0)
        self.app.keyboard_changed()

    def hide(self):
        if not self.visible:
            return
        log("keyboard away")
        self.visible = False
        self.touches.clear()
        self.alts = None
        self.set_strut(False)
        self.animate_slide(0.0)
        self.app.keyboard_changed()

    def toggle(self):
        self.hide() if self.visible else self.show()

    def animate_slide(self, to):
        self.slide_from, self.slide_to, self.slide_t0 = self.slide, to, time.monotonic()
        self.start_tick()

    def start_tick(self):
        if not self.tick_id:
            self.tick_id = GLib.timeout_add(16, self.tick)

    def tick(self):
        now = time.monotonic()
        busy = False
        if self.slide != self.slide_to:
            t = (now - self.slide_t0) / (0.26 if self.slide_to else 0.2)
            self.slide = self.slide_from + (self.slide_to - self.slide_from) * ease(t)
            if t >= 1:
                self.slide = self.slide_to
                if not self.slide_to:
                    self.win.hide()
            busy = True
        self.anims = [a for a in self.anims if now - a[3] < 0.42]
        if self.anims:
            busy = True
        if self.bubble and self.bubble[2] and now - self.bubble[2] > 0.12:
            self.bubble = None
        if self.bubble:
            busy = True
        self.area.queue_draw()
        if not busy:
            self.tick_id = None
            return False
        return True

    # --- drawing ---------------------------------------------------------------------------
    def draw(self, _w, cr):
        t = self.theme if hasattr(self, "theme") else THEMES[current_theme()]
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        off = (1 - self.slide) * (self.body + 8)
        cr.translate(0, off)
        cr.push_group()
        # paper and one hairline
        cr.set_source_rgb(*t["bg"])
        cr.rectangle(0, self.margin, self.W, self.body + 8)
        cr.fill()
        cr.set_source_rgba(*t["line"])
        cr.rectangle(0, self.margin, self.W, 1)
        cr.fill()
        self.draw_strip(cr, t)
        if self.mode == "write":
            self.draw_pad(cr, t)
        else:
            for k, x, y, w, h in self.rects:
                self.draw_key(cr, t, k, x, y, w, h)
        now = time.monotonic()
        for kind, x, y, start in self.anims:
            p = (now - start) / 0.42
            cr.set_source_rgba(*t["accent"], 0.32 * (1 - p))
            cr.set_line_width(1.5)
            cr.arc(x, y, self.kh * (0.18 + 0.55 * ease(p)), 0, 2 * math.pi)
            cr.stroke()
        if self.bubble:
            self.draw_bubble(cr, t)
        if self.alts:
            self.draw_alts(cr, t)
        cr.pop_group_to_source()
        cr.paint_with_alpha(min(1.0, 0.25 + self.slide))
        return True

    def pressed_keys(self):
        return {id(tc["key"]) for tc in self.touches.values() if tc.get("key")}

    def key_label(self, k):
        lab = k["label"]
        if k["act"][0] == "char" and self.shift and len(lab) == 1:
            return lab.upper()
        return lab

    def draw_key(self, cr, t, k, x, y, w, h):
        act = k["act"][0]
        down = id(k) in self.pressed_keys()
        on = (act == "shift" and self.shift) or (act == "mod" and k["act"][1] in self.mods)
        fill = t["key"] if act in ("char", "space") else t["fn"]
        r = self.kh * 0.2
        if fill[3] > 0.3:  # paper keys lift a little off the paper
            cr.set_source_rgba(*t["shadow"])
            rounded(cr, x, y + 1.2, w, h, r)
            cr.fill()
        cr.set_source_rgba(*fill)
        rounded(cr, x, y, w, h, r)
        cr.fill()
        if down or on:
            cr.set_source_rgba(*t["accent"], 0.13 if down else 0.08)
            rounded(cr, x, y, w, h, r)
            cr.fill()
        color = t["accent"] if (down or on) else (t["fg"] if act == "char" else t["muted"])
        cx, cy = x + w / 2, y + h / 2
        lab = k["label"]
        if act == "char":
            text_at(cr, self.key_label(k), self.kh * 0.36, color, cx, cy)
            if k["alts"] and len(lab) == 1 and lab.isalpha() and k["alts"][0].isdigit():
                text_at(
                    cr, k["alts"][0], self.kh * 0.16, t["muted"], x + w - self.kh * 0.2, y + self.kh * 0.22, alpha=0.55
                )
        elif act == "space":
            lang = self.app.settings["language"]
            text_at(cr, LANGS.get(lang, lang), self.kh * 0.2, t["muted"], cx, cy, alpha=0.75)
        elif act == "lang":
            text_at(cr, self.app.settings["language"].upper(), self.kh * 0.2, color, cx, cy, weight=Pango.Weight.NORMAL)
        elif act == "shift":
            icon(
                cr,
                "caps" if self.shift == 2 else ("shift-on" if self.shift else "shift"),
                cx,
                cy,
                self.kh * 0.42,
                color,
            )
        elif act in ("back",) or lab in ("return", "tab", "left", "right", "hide", "keys"):
            icon(cr, lab if act != "back" else "back", cx, cy, self.kh * 0.42, color)
        else:
            text_at(cr, lab, self.kh * 0.21, color, cx, cy)

    def draw_strip(self, cr, t):
        s = self.strip
        cy = self.margin + s / 2 + 2
        for name, (x, y, w, h) in self.strip_buttons:
            if name.startswith("mode-"):
                mine = name[5:]
                on = self.mode == mine
                tw = text_at(
                    cr,
                    "Keys" if mine == "keys" else "Write",
                    s * 0.34,
                    t["fg"] if on else t["muted"],
                    x + w / 2,
                    cy,
                    alpha=1 if on else 0.8,
                )
                if on:
                    cr.set_source_rgb(*t["accent"])
                    cr.arc(x + w / 2 - tw / 2 - s * 0.2, cy, 2.6, 0, 2 * math.pi)
                    cr.fill()
            elif name == "pen":
                on = self.app.settings["pen_writing"]
                icon(cr, "pen", x + w / 2, cy, s * 0.42, t["accent"] if on else t["muted"], 1 if on else 0.6)
            elif name == "hide":
                icon(cr, "hide", x + w / 2, cy, s * 0.42, t["muted"])
        if self.mode == "write":
            cands = self.pad["cands"][:6]
            x0 = self.keys_x + s * 4.4
            x1 = self.keys_x + self.keys_w - 2.4 * s
            self.cand_rects = []
            if self.pad["busy"] and not cands:
                text_at(cr, "·  ·  ·", s * 0.34, t["muted"], (x0 + x1) / 2, cy, alpha=0.6)
            if cands:
                cw = (x1 - x0) / max(3, len(cands))
                for i, c in enumerate(cands):
                    cx = x0 + cw * (i + 0.5)
                    text_at(
                        cr,
                        c,
                        s * (0.4 if i == 0 else 0.34),
                        t["fg"] if i == 0 else t["muted"],
                        cx,
                        cy,
                        weight=Pango.Weight.LIGHT,
                    )
                    self.cand_rects.append((c, (x0 + cw * i, self.margin, cw, s)))

    def draw_pad(self, cr, t):
        x, y, w, h = self.pad_rect
        cr.set_source_rgba(*t["key"][:3], t["key"][3] * 0.7)
        rounded(cr, x, y, w, h, self.kh * 0.22)
        cr.fill()
        base = y + h * 0.68
        cr.set_source_rgba(*t["line"])
        cr.rectangle(x + self.kh * 0.6, base, w - self.kh * 1.2, 1)
        cr.fill()
        if not self.pad["strokes"] and not self.pad["cur"]:
            text_at(
                cr,
                "Write here, with the pen or a finger",
                self.kh * 0.24,
                t["muted"],
                x + w / 2,
                y + h * 0.4,
                alpha=0.55,
            )
        strokes = self.pad["strokes"] + ([self.pad["cur"]] if self.pad["cur"] else [])
        draw_ink(cr, strokes, t["accent"] if self.pad["busy"] else t["fg"], 0.9, max(2.2, self.kh * 0.045))
        for k, kx, ky, kw, kh in self.pad_keys:
            self.draw_key(cr, t, k, kx, ky, kw, kh)

    def draw_bubble(self, cr, t):
        k, _start, ending = self.bubble
        rect = next((r for r in self.rects if r[0] is k), None)
        if not rect:
            return
        _k, x, y, w, h = rect
        a = 1.0 if not ending else max(0.0, 1 - (time.monotonic() - ending) / 0.12)
        bw, bh = max(w * 1.25, self.kh * 1.15), self.kh * 1.35
        bx = min(max(x + w / 2 - bw / 2, 4), self.W - bw - 4)
        by = y - bh - self.gap * 0.5
        cr.set_source_rgba(*t["shadow"][:3], t["shadow"][3] * 1.6 * a)
        rounded(cr, bx, by + 2, bw, bh, self.kh * 0.28)
        cr.fill()
        cr.set_source_rgba(*t["bg"], a)
        rounded(cr, bx, by, bw, bh, self.kh * 0.28)
        cr.fill()
        cr.set_source_rgba(*t["line"][:3], t["line"][3] * a)
        cr.set_line_width(1)
        rounded(cr, bx + 0.5, by + 0.5, bw - 1, bh - 1, self.kh * 0.28)
        cr.stroke()
        text_at(
            cr,
            self.key_label(k),
            self.kh * 0.62,
            t["fg"],
            bx + bw / 2,
            by + bh * 0.46,
            weight=Pango.Weight.ULTRALIGHT,
            alpha=a,
        )
        cr.set_source_rgba(*t["accent"], a)
        cr.arc(bx + bw / 2, by + bh * 0.84, 2.4, 0, 2 * math.pi)
        cr.fill()

    def draw_alts(self, cr, t):
        al = self.alts
        x, y, w, h = al["box"]
        cr.set_source_rgba(*t["shadow"][:3], t["shadow"][3] * 1.6)
        rounded(cr, x, y + 2, w, h, self.kh * 0.25)
        cr.fill()
        cr.set_source_rgb(*t["bg"])
        rounded(cr, x, y, w, h, self.kh * 0.25)
        cr.fill()
        cr.set_source_rgba(*t["line"])
        cr.set_line_width(1)
        rounded(cr, x + 0.5, y + 0.5, w - 1, h - 1, self.kh * 0.25)
        cr.stroke()
        for i, (ch, (cx0, cy0, cw, chh)) in enumerate(zip(al["items"], al["rects"])):
            sel = i == al["sel"]
            if sel:
                cr.set_source_rgba(*t["accent"], 0.14)
                rounded(cr, cx0 + 3, cy0 + 3, cw - 6, chh - 6, self.kh * 0.18)
                cr.fill()
            text_at(
                cr,
                ch.upper() if self.shift else ch,
                self.kh * 0.38,
                t["accent"] if sel else t["fg"],
                cx0 + cw / 2,
                cy0 + chh / 2,
            )

    # --- input -----------------------------------------------------------------------------
    def on_touch(self, _w, ev):
        seq = ev.get_event_sequence()
        x, y = ev.x, ev.y - (1 - self.slide) * (self.body + 8)
        if ev.type == Gdk.EventType.TOUCH_BEGIN:
            if self.app.pen_recent(1.2):
                return True  # a hand resting while the pen writes: not a finger on a key
            self.press(seq, x, y, ev)
        elif ev.type == Gdk.EventType.TOUCH_UPDATE:
            self.move(seq, x, y, ev)
        elif ev.type in (Gdk.EventType.TOUCH_END, Gdk.EventType.TOUCH_CANCEL):
            self.release(seq, x, y, ev.type == Gdk.EventType.TOUCH_CANCEL)
        return True

    def on_button(self, _w, ev):
        if is_finger(ev) or ev.button != 1:
            return True
        if ev.type == Gdk.EventType.BUTTON_PRESS:
            self.press("pointer", ev.x, ev.y, ev)
        elif ev.type == Gdk.EventType.BUTTON_RELEASE:
            self.release("pointer", ev.x, ev.y)
        return True

    def on_motion(self, _w, ev):
        if "pointer" in self.touches and not is_finger(ev):
            self.move("pointer", ev.x, ev.y, ev)
        return True

    def hit(self, x, y, rects):
        best, bd = None, 1e9
        for r in rects:
            k, rx, ry, rw, rh = r
            g = self.gap / 2 + 1
            if rx - g <= x <= rx + rw + g and ry - g <= y <= ry + rh + g:
                d = abs(x - (rx + rw / 2)) + abs(y - (ry + rh / 2))
                if d < bd:
                    best, bd = k, d
        return best

    def press(self, sid, x, y, ev=None):
        now = time.monotonic()
        # the strip
        if self.margin <= y < self.margin + self.strip:
            for name, (bx, by, bw, bh) in self.strip_buttons:
                if bx <= x <= bx + bw:
                    self.strip_action(name)
                    return
            for c, (bx, by, bw, bh) in getattr(self, "cand_rects", []):
                if self.mode == "write" and bx <= x <= bx + bw:
                    self.pick_candidate(c)
                    return
            return
        if self.mode == "write":
            k = self.hit(x, y, self.pad_keys)
            px, py, pw, ph = self.pad_rect
            if not k and px <= x <= px + pw and py <= y <= py + ph:
                self.pad_press(x, y, ev)
                self.touches[sid] = {"pad": True}
                return
        else:
            k = self.hit(x, y, self.rects)
        if not k:
            return
        tc = {"key": k, "x0": x, "y0": y, "t0": now, "mode": None, "steps": 0}
        self.touches[sid] = tc
        self.anims.append(("ring", x, y, now))
        act = k["act"][0]
        if act == "char":
            self.bubble = (k, now, None)
            if k["alts"]:
                tc["timer"] = GLib.timeout_add(330, self.open_alts, sid)
        elif act == "back":
            self.backspace()
            tc["timer"] = GLib.timeout_add(420, self.start_repeat, sid)
        self.start_tick()

    def move(self, sid, x, y, ev=None):
        tc = self.touches.get(sid)
        if not tc:
            return
        if tc.get("pad"):
            self.pad_move(x, y, ev)
            return
        k = tc["key"]
        if tc["mode"] == "alts" and self.alts:
            for i, (ax, ay, aw, ah) in enumerate(self.alts["rects"]):
                if ax <= x <= ax + aw:
                    self.alts["sel"] = i
            if x < self.alts["rects"][0][0]:
                self.alts["sel"] = 0
            if x > self.alts["rects"][-1][0] + self.alts["rects"][-1][2]:
                self.alts["sel"] = len(self.alts["items"]) - 1
            self.area.queue_draw()
            return
        act = k["act"][0]
        if act == "space":
            dx = x - tc["x0"]
            step = self.kh * 0.22
            if tc["mode"] == "cursor" or abs(dx) > self.kh * 0.35:
                tc["mode"] = "cursor"
                n = int(dx / step)
                while tc["steps"] < n:
                    tc["steps"] += 1
                    self.app.typer.put("key", "Right")
                while tc["steps"] > n:
                    tc["steps"] -= 1
                    self.app.typer.put("key", "Left")
            return
        if act == "char" and tc["mode"] is None:
            # a finger that slides onto the next key means that key
            k2 = self.hit(x, y, self.rects)
            if k2 is not k and k2 and k2["act"][0] == "char":
                self.cancel_timer(tc)
                tc["key"] = k2
                self.bubble = (k2, time.monotonic(), None)
                if k2["alts"]:
                    tc["timer"] = GLib.timeout_add(330, self.open_alts, sid)
                self.area.queue_draw()

    def release(self, sid, x, y, cancelled=False):
        tc = self.touches.pop(sid, None)
        if not tc:
            return
        if tc.get("pad"):
            self.pad_release()
            return
        self.cancel_timer(tc)
        if self.repeat_id and tc["key"]["act"][0] == "back":
            GLib.source_remove(self.repeat_id)
            self.repeat_id = None
        k = tc["key"]
        if self.bubble and self.bubble[0] is k:
            self.bubble = (k, self.bubble[1], time.monotonic())
        if cancelled:
            self.alts = None
            self.set_input()
            return
        if tc["mode"] == "alts" and self.alts:
            ch = self.alts["items"][self.alts["sel"]]
            self.alts = None
            self.set_input()
            self.commit_char(ch)
        elif tc["mode"] == "cursor":
            pass
        else:
            self.activate(k)
        self.start_tick()

    def cancel_timer(self, tc):
        if tc.get("timer"):
            GLib.source_remove(tc["timer"])
            tc["timer"] = None

    def open_alts(self, sid):
        tc = self.touches.get(sid)
        if not tc:
            return False
        tc["timer"] = None
        k = tc["key"]
        rect = next((r for r in self.rects if r[0] is k), None)
        if not rect:
            return False
        digit = k["alts"][0].isdigit()
        items = list(k["alts"]) if digit else [k["label"]] + list(k["alts"])
        _k, x, y, w, h = rect
        cw = max(w, self.kh * 0.95)
        bw = cw * len(items)
        bx = min(max(x + w / 2 - cw / 2, 4), self.W - bw - 4)
        if bx + bw > x + w:
            bx = max(4, x + w - bw)
        by = y - self.kh * 1.25 - self.gap * 0.5
        bh = self.kh * 1.25
        self.alts = {
            "items": items,
            "rects": [(bx + i * cw, by, cw, bh) for i in range(len(items))],
            "box": (bx, by, bw, bh),
            "sel": 0 if digit else 1,
        }
        tc["mode"] = "alts"
        self.bubble = None
        self.set_input()
        self.area.queue_draw()
        return False

    def start_repeat(self, sid):
        tc = self.touches.get(sid)
        if not tc:
            return False
        tc["timer"] = None
        tc["repeat_t0"] = time.monotonic()

        def again():
            if sid not in self.touches:
                self.repeat_id = None
                return False
            held = time.monotonic() - tc["repeat_t0"]
            if held > 1.8:
                self.app.typer.put("key", "BackSpace", ("Control_L",))  # a word at a time
            else:
                self.backspace()
            self.anims.append(("ring", tc["x0"], tc["y0"], time.monotonic()))
            self.start_tick()
            return True

        self.repeat_id = GLib.timeout_add(75, again)
        return False

    # --- what keys do ----------------------------------------------------------------------
    def activate(self, k):
        act = k["act"]
        kind = act[0]
        if kind == "char":
            self.commit_char(k["label"])
        elif kind == "space":
            self.space()
        elif kind == "shift":
            now = time.monotonic()
            if self.shift == 1 and now - self.shift_tap < 0.38:
                self.shift = 2
            elif self.shift:
                self.shift = 0
            else:
                self.shift = 1
            self.shift_tap = now
        elif kind == "layer":
            self.layer = act[1]
            self.relayout()
        elif kind == "mod":
            self.mods ^= {act[1]}
        elif kind == "key":
            self.app.typer.put("key", act[1], tuple(self.mods))
            self.mods.clear()
            self.remember("\n" if act[1] == "Return" else "")
        elif kind == "lang":
            s = self.app.settings
            s["language"] = "cs" if s["language"] == "en" else "en"
            save_settings(s)
        elif kind == "hide":
            self.hide()
        elif kind == "mode":
            self.set_mode(act[1])
        self.area.queue_draw()

    def remember(self, ch):
        self.last_chars = (self.last_chars + [(ch, time.monotonic())])[-4:]

    def commit_char(self, ch):
        if self.shift:
            ch = ch.upper()
        if self.mods:
            self.app.typer.put("text", ch.lower(), tuple(self.mods))
            self.mods.clear()
        else:
            self.app.typer.put("text", ch)
        if self.shift == 1:
            self.shift = 0
        self.remember(ch)
        self.area.queue_draw()

    def space(self):
        now = time.monotonic()
        lc = self.last_chars
        if len(lc) >= 2 and lc[-1][0] == " " and now - lc[-1][1] < 0.7 and lc[-2][0][-1:].isalnum():
            self.app.typer.put("key", "BackSpace")
            self.app.typer.put("text", ". ")
            self.remember(".")
            self.remember(" ")
            self.shift = self.shift or 1
            return
        self.app.typer.put("text", " ", tuple(self.mods))
        self.mods.clear()
        if lc and lc[-1][0] in (".", "!", "?") and not self.shift:
            self.shift = 1
        self.remember(" ")

    def backspace(self):
        self.app.typer.put("key", "BackSpace", tuple(self.mods))
        self.last_chars = self.last_chars[:-1]

    def strip_action(self, name):
        if name == "hide":
            self.hide()
        elif name == "pen":
            s = self.app.settings
            s["pen_writing"] = not s["pen_writing"]
            save_settings(s)
            self.app.ink.cancel()
        elif name.startswith("mode-"):
            self.set_mode(name[5:])
        self.area.queue_draw()

    def set_mode(self, mode):
        self.mode = mode
        self.pad.update(strokes=[], cur=None, cands=[], committed="")
        self.area.queue_draw()

    # --- the writing pad -------------------------------------------------------------------
    def pad_press(self, x, y, ev):
        p = self.pad
        if p["timer"]:
            GLib.source_remove(p["timer"])
            p["timer"] = None
        if p["cands"] and not p["strokes"]:
            p["cands"] = []
        p["cur"] = [(x, y, 0, pressure(ev) if ev else 0.5)]
        p["t0"] = p.get("t0") if p["strokes"] else time.monotonic()
        self.area.queue_draw()

    def pad_move(self, x, y, ev):
        p = self.pad
        if p["cur"] is not None:
            p["cur"].append((x, y, (time.monotonic() - p["t0"]) * 1000, pressure(ev) if ev else 0.5))
            self.area.queue_draw()

    def pad_release(self):
        p = self.pad
        if os.environ.get("KT_DEBUG"):
            print("stroke", [(round(a[0]), round(a[1])) for a in (p["cur"] or [])][:40], file=sys.stderr, flush=True)
        if p["cur"]:
            p["strokes"].append(p["cur"])
        p["cur"] = None
        p["timer"] = GLib.timeout_add(650, self.pad_recognize)

    def pad_recognize(self):
        p = self.pad
        p["timer"] = None
        strokes = p["strokes"]
        if not strokes:
            return False
        p["busy"] = True
        x, y, w, h = self.pad_rect
        lang = self.app.settings["language"]

        def work():
            try:
                cands = recognize([[(a[0] - x, a[1] - y, a[2]) for a in s] for s in strokes], w, h, lang)
            except Exception:
                cands = []
            GLib.idle_add(done, cands)

        def done(cands):
            p["busy"] = False
            if os.environ.get("KT_DEBUG"):
                print("read", cands[:5], file=sys.stderr, flush=True)
            if p["strokes"] is not strokes:
                return False
            p["strokes"] = []
            p["cands"] = cands
            if cands:
                text = cands[0]
                if p["committed"] and p["committed"][-1:].isalnum() and text[:1].isalnum():
                    text = " " + text
                self.app.typer.put("text", text)
                p["committed"] = text
            self.area.queue_draw()
            return False

        threading.Thread(target=work, daemon=True).start()
        self.area.queue_draw()
        return False

    def pick_candidate(self, c):
        p = self.pad
        prev = p["committed"]
        lead = " " if prev.startswith(" ") else ""
        for _ in range(len(prev)):
            self.app.typer.put("key", "BackSpace")
        self.app.typer.put("text", lead + c)
        p["committed"] = lead + c
        self.area.queue_draw()


# --- the pill: the keyboard, folded away -------------------------------------------------


class Pill:
    def __init__(self, app):
        self.app = app
        self.size = 54
        self.win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.win.set_app_paintable(True)
        self.win.set_accept_focus(False)
        self.win.set_keep_above(True)
        visual = self.win.get_screen().get_rgba_visual()
        if visual:
            self.win.set_visual(visual)
        self.win.set_default_size(self.size, self.size)
        self.win.add_events(Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.TOUCH_MASK)
        self.win.connect("draw", self.draw)
        self.win.connect("button-release-event", lambda *_: (self.app.keyboard.show(), True)[1])
        self.win.connect("touch-event", self.on_touch)

    def on_touch(self, _w, ev):
        if ev.type == Gdk.EventType.TOUCH_END:
            self.app.keyboard.show()
        return True

    def show(self):
        display = Gdk.Display.get_default()
        g = (display.get_primary_monitor() or display.get_monitor(0)).get_geometry()
        self.win.move(g.x + g.width - self.size - 22, g.y + g.height - self.size - 22)
        self.win.show_all()

    def hide(self):
        self.win.hide()

    def draw(self, _w, cr):
        t = THEMES[current_theme()]
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        s = self.size
        cr.set_source_rgba(*t["shadow"])
        cr.arc(s / 2, s / 2 + 1.5, s / 2 - 3, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgb(*t["bg"])
        cr.arc(s / 2, s / 2, s / 2 - 3, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(*t["line"])
        cr.set_line_width(1)
        cr.arc(s / 2, s / 2, s / 2 - 3.5, 0, 2 * math.pi)
        cr.stroke()
        icon(cr, "keys", s / 2, s / 2 - 1, s * 0.36, t["fg"], 0.8)
        cr.set_source_rgb(*t["accent"])
        cr.arc(s / 2, s - 13, 2, 0, 2 * math.pi)
        cr.fill()
        return True
