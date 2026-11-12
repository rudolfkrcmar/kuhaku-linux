"""What the gestures share: how far a swipe must go, and the open windows, their apps, icons
and pictures, read from X11. Used by kuhaku-gestures.py and its overview (gestures_overview.py)."""

import os
import subprocess
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, Gtk

SWIPE = 1
UP, DOWN, LEFT, RIGHT = 1, 2, 3, 4
FINGERS = 3
DISTANCE = 55.0  # percent of Touchégg's travel that counts as a full swipe
COMMIT = 0.33  # let go past this and the swipe completes
SCALE = 1.0  # full-size snapshots: the sliding screens stay sharp
GAP = 18  # a dark seam between the two screens
UP_DISTANCE = 26.0  # swipe up this far (percent) and the cards are fully out
UP_COMMIT = 0.2
PID_FILE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "kuhaku-gestures.pid")
NAV_STEP = 30.0  # a longer sideways swipe in the overview moves one card per this much
ACCENT = {"day": (0.878, 0.188, 0.118), "night": (0.941, 0.439, 0.353)}


def night():
    hour = time.localtime().tm_hour
    return hour >= 20 or hour < 7


def paper():
    return (0.071,) * 3 if night() else (0.969, 0.957, 0.945)


def ui_scale():
    dpi = Gtk.Settings.get_default().get_property("gtk-xft-dpi")
    return max(0.75, min(2.0, dpi / 1024 / 96)) if dpi and dpi > 0 else 1.0


def describe(wins):
    """Class, name, type and state of several windows at once: one xprop each, in parallel."""
    procs = {
        w: subprocess.Popen(
            ["xprop", "-id", w, "WM_CLASS", "_NET_WM_NAME", "_NET_WM_WINDOW_TYPE", "_NET_WM_STATE"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        for w in wins
    }
    info = {}
    for w, proc in procs.items():
        d: dict = {}
        for line in proc.communicate()[0].splitlines():
            key, sep, val = line.partition(" = ")
            if sep:
                d[key.split("(")[0]] = val.strip()
        d["classes"] = [c.strip().strip('"') for c in d.get("WM_CLASS", "").split(",") if c.strip()]
        d["name"] = d.get("_NET_WM_NAME", "").strip('"')
        info[w] = d
    return info


APPS: dict | None = None
NIGHT_ICONS = os.path.expanduser("~/.local/share/kuhaku/icons/apps-night")


def app_for(classes):
    """Name and icon of the app a window belongs to, from its desktop file."""
    global APPS
    if APPS is None:
        found: dict = {}
        for a in Gio.AppInfo.get_all():
            wm = a.get_startup_wm_class() if hasattr(a, "get_startup_wm_class") else None
            entry = (a.get_name(), a.get_icon())
            if wm:
                found.setdefault(wm.lower(), entry)
            if a.get_id():
                found.setdefault(a.get_id()[:-8].lower(), entry)
        APPS = found
    for c in classes:
        if c.lower() in APPS:
            return APPS[c.lower()]
    return None


ICONS: dict = {}


def icon_surface(gicon, size):
    """An app icon as a cairo surface; Kūhaku's own apps have night versions."""
    if not gicon:
        return None
    key = (gicon.to_string(), size, night())
    if key not in ICONS:
        pix = None
        try:
            path = f"{NIGHT_ICONS}/{gicon.to_string()}.png"
            if night() and os.path.exists(path):
                pix = GdkPixbuf.Pixbuf.new_from_file_at_size(path, size, size)
            else:
                info = Gtk.IconTheme.get_default().lookup_by_gicon(gicon, size, Gtk.IconLookupFlags.FORCE_SIZE)
                pix = info.load_icon() if info else None
        except Exception:
            pix = None
        ICONS[key] = Gdk.cairo_surface_create_from_pixbuf(pix, 1, None) if pix else None
    return ICONS[key]


def rounded(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.5708, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
    cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    cr.arc(x + r, y + r, r, 3.1416, 4.7124)
    cr.close_path()


def out(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def prop(win, name):
    return out("xprop", "-id", win, name) if win else out("xprop", "-root", name)


def window_list(name):
    return [w.strip() for w in prop(None, name).split("#")[-1].split(",") if w.strip().startswith("0x")]


def is_home(win):
    return not win or win == "0x0" or "_NET_WM_WINDOW_TYPE_DESKTOP" in prop(win, "_NET_WM_WINDOW_TYPE")


def showing_desktop():
    return prop(None, "_NET_SHOWING_DESKTOP").strip().endswith("1")


def last_app():
    for win in reversed(window_list("_NET_CLIENT_LIST_STACKING")):
        t = prop(win, "_NET_WM_WINDOW_TYPE")
        state = prop(win, "_NET_WM_STATE")
        if "DESKTOP" in t or "DOCK" in t or "SKIP_TASKBAR" in state:
            continue
        return win
    return None


def apps():
    """Open app windows in the order they were opened."""
    found = []
    for win in window_list("_NET_CLIENT_LIST"):
        t = prop(win, "_NET_WM_WINDOW_TYPE")
        state = prop(win, "_NET_WM_STATE")
        if "DESKTOP" in t or "DOCK" in t or "SKIP_TASKBAR" in state:
            continue
        found.append(win)
    return found


def home_window():
    for win in window_list("_NET_CLIENT_LIST_STACKING"):
        if "_NET_WM_WINDOW_TYPE_DESKTOP" in prop(win, "_NET_WM_WINDOW_TYPE"):
            return win
    return None
