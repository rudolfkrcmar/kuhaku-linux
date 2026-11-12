"""What the touch keyboard draws: its layouts (letters, numbers, symbols, per language), the key
icons, and text set in place. Plain drawing code; the Keyboard window is in tablet_keyboard.py."""

import math

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

from tablet_base import (
    FONT,
    rounded,
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
