"""The touch keyboard's shared ground: settings, theme, small drawing helpers and handwriting."""

import ctypes
import json
import math
import os
import time
import urllib.request
from pathlib import Path

X11 = ctypes.CDLL("libX11.so.6")
X11.XInitThreads()  # before GTK opens its own connection

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk

from kuhaku_log import report  # faults go to the log instead of vanishing

HOME = Path.home()
CONFIG_DIR = HOME / ".config" / "kuhaku"
TABLET_CONFIG = CONFIG_DIR / "tablet.json"
RUN = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
PID_FILE = RUN / "kuhaku-tablet.pid"
MODE_FILE = RUN / "kuhaku-tablet-mode"  # "1" while folded: the bars offer Notes then
NOTES_FRONT = RUN / "kuhaku-notes-front"
PASSWORD_FRONT = (
    RUN / "kuhaku-password-front"
)  # the lock or the password window is up (its pid)      # Notes is in front: its paper takes the pen itself
FONT = "Inter Variable"

DEFAULTS = {"language": "en", "pen_writing": True, "keyboard_in_laptop_mode": False}
LANGS = {"en": "English", "cs": "Čeština"}

THEMES = {
    "day": {
        "bg": (0.969, 0.957, 0.945),
        "fg": (0.067, 0.067, 0.067),
        "muted": (0.42, 0.42, 0.42),
        "key": (1, 1, 1, 0.78),
        "fn": (0.067, 0.067, 0.067, 0.05),
        "line": (0.067, 0.067, 0.067, 0.09),
        "accent": (0.878, 0.188, 0.118),
        "shadow": (0, 0, 0, 0.06),
    },
    "night": {
        "bg": (0.071, 0.071, 0.071),
        "fg": (0.925, 0.925, 0.925),
        "muted": (0.6, 0.6, 0.6),
        "key": (1, 1, 1, 0.075),
        "fn": (1, 1, 1, 0.035),
        "line": (1, 1, 1, 0.09),
        "accent": (0.941, 0.439, 0.353),
        "shadow": (0, 0, 0, 0.35),
    },
}


LOG = HOME / ".local/state/kuhaku/tablet.log"


def log(*parts):
    """A short record of changes (laptop/tablet, keyboard), kept small."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if LOG.exists() and LOG.stat().st_size > 200_000:
            LOG.write_text(LOG.read_text()[-50_000:])
        with LOG.open("a") as f:
            f.write(time.strftime("%H:%M:%S ") + " ".join(str(x) for x in parts) + "\n")
    except OSError:
        pass


def load_json(path, default):
    try:
        return {**default, **json.loads(Path(path).read_text())}
    except Exception:
        report()
        return dict(default)


def settings():
    return load_json(TABLET_CONFIG, DEFAULTS)


def save_settings(s):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TABLET_CONFIG.write_text(json.dumps(s, indent=2) + "\n")


def current_theme():
    """The same day and night as Kūhaku itself (its config and state)."""
    cfg = load_json(CONFIG_DIR / "config.json", {"theme": "auto", "night_from": 20, "night_until": 7})
    state = load_json(HOME / ".local/state/kuhaku/state.json", {})
    choice = state.get("theme") or cfg.get("theme")
    if choice in ("day", "night"):
        return choice
    h = time.localtime().tm_hour
    return "night" if (h >= cfg["night_from"] or h < cfg["night_until"]) else "day"


def ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def rounded(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


# --- handwriting -------------------------------------------------------------------------


def recognize(strokes, width, height, lang):
    """Candidates for some ink: strokes are lists of (x, y, t_ms)."""
    ink = [[[round(p[0]) for p in s], [round(p[1]) for p in s], [int(p[2]) for p in s]] for s in strokes if s]
    body = {
        "options": "enable_pre_space",
        "requests": [
            {
                "writing_guide": {"writing_area_width": int(width), "writing_area_height": int(height)},
                "ink": ink,
                "language": lang,
            }
        ],
    }
    req = urllib.request.Request(
        "https://inputtools.google.com/request?ime=handwriting&app=kuhaku&cs=1&oe=UTF-8",
        json.dumps(body).encode(),
        {"Content-Type": "application/json", "User-Agent": "Kuhaku"},
    )
    with urllib.request.urlopen(req, timeout=6) as r:
        data = json.loads(r.read())
    if data[0] != "SUCCESS":
        return []
    return data[1][0][1]


def draw_ink(cr, strokes, color, alpha, width):
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    for s in strokes:
        if not s:
            continue
        if len(s) == 1:
            cr.set_source_rgba(*color[:3], alpha)
            cr.arc(s[0][0], s[0][1], width * 0.6, 0, 2 * math.pi)
            cr.fill()
            continue
        for a, b in zip(s, s[1:], strict=False):
            p = (a[3] + b[3]) / 2 if len(a) > 3 else 0.5
            cr.set_line_width(width * (0.55 + 0.9 * p))
            cr.set_source_rgba(*color[:3], alpha)
            cr.move_to(a[0], a[1])
            cr.line_to(b[0], b[1])
            cr.stroke()


def pressure(event):
    try:
        r = event.get_axis(Gdk.AxisUse.PRESSURE)
        ok, p = r if isinstance(r, tuple) else (r is not None, r)
        return p if ok and 0 < p <= 1 else 0.5
    except Exception:
        report()
        return 0.5


def is_finger(event):
    dev = event.get_source_device()
    return bool(dev) and dev.get_source() == Gdk.InputSource.TOUCHSCREEN
