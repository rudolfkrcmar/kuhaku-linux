#!/usr/bin/env python3
"""Kūhaku for the convertible: a touch keyboard.

Fold the screen all the way back and Kūhaku's own keyboard rises from the
bottom of the screen; open it into a laptop again and it goes away. Apps make
room for it (it reserves its height the way a panel does).

The keyboard: paper keys in light type, a large preview over each key,
hold a key for its accents and digits (Czech first), shift twice for caps,
two spaces for a full stop, drag along the space bar to move the cursor,
hold delete to keep deleting. "Write" turns the keys into a writing pad.

"Write" sends only the ink strokes to Google's handwriting service to read them.

Tablet mode comes from the "Intel HID switches" device; kuhaku-tablet-fix.sh
lets the signed-in person read it. `kuhaku-tablet.py toggle` shows or hides the
keyboard by hand (also: kill -USR1 <pid>).
"""
import ctypes
import ctypes.util
import fcntl
import json
import math
import os
import queue
import signal
import struct
import sys
import threading
import time
import urllib.request
from pathlib import Path

X11 = ctypes.CDLL("libX11.so.6")
X11.XInitThreads()  # before GTK opens its own connection

import cairo  # noqa: E402
import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk, Pango, PangoCairo  # noqa: E402,F401

HOME = Path.home()
CONFIG_DIR = HOME / ".config" / "kuhaku"
TABLET_CONFIG = CONFIG_DIR / "tablet.json"
RUN = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
PID_FILE = RUN / "kuhaku-tablet.pid"
MODE_FILE = RUN / "kuhaku-tablet-mode"        # "1" while folded: the bars offer Notes then
NOTES_FRONT = RUN / "kuhaku-notes-front"
PASSWORD_FRONT = RUN / "kuhaku-password-front"   # the lock or the password window is up (its pid)      # Notes is in front: its paper takes the pen itself
FONT = "Inter Variable"

DEFAULTS = {"language": "en", "pen_writing": True, "keyboard_in_laptop_mode": False}
LANGS = {"en": "English", "cs": "Čeština"}

THEMES = {
    "day": {"bg": (0.969, 0.957, 0.945), "fg": (0.067, 0.067, 0.067), "muted": (0.42, 0.42, 0.42),
            "key": (1, 1, 1, 0.78), "fn": (0.067, 0.067, 0.067, 0.05), "line": (0.067, 0.067, 0.067, 0.09),
            "accent": (0.878, 0.188, 0.118), "shadow": (0, 0, 0, 0.06)},
    "night": {"bg": (0.071, 0.071, 0.071), "fg": (0.925, 0.925, 0.925), "muted": (0.6, 0.6, 0.6),
              "key": (1, 1, 1, 0.075), "fn": (1, 1, 1, 0.035), "line": (1, 1, 1, 0.09),
              "accent": (0.941, 0.439, 0.353), "shadow": (0, 0, 0, 0.35)},
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


# --- X11: typing, clicking, struts, the pen -------------------------------------------------

class XEventCookie(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("extension", ctypes.c_int), ("evtype", ctypes.c_int),
                ("cookie", ctypes.c_uint), ("data", ctypes.c_void_p)]


class XIRawEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("extension", ctypes.c_int), ("evtype", ctypes.c_int),
                ("time", ctypes.c_ulong), ("deviceid", ctypes.c_int), ("sourceid", ctypes.c_int),
                ("detail", ctypes.c_int), ("flags", ctypes.c_int)]


class XIEventMask(ctypes.Structure):
    _fields_ = [("deviceid", ctypes.c_int), ("mask_len", ctypes.c_int), ("mask", ctypes.POINTER(ctypes.c_ubyte))]


class XIDeviceInfo(ctypes.Structure):
    _fields_ = [("deviceid", ctypes.c_int), ("name", ctypes.c_char_p), ("use", ctypes.c_int),
                ("attachment", ctypes.c_int), ("enabled", ctypes.c_int), ("num_classes", ctypes.c_int),
                ("classes", ctypes.c_void_p)]


class XFixesCursorImage(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short), ("width", ctypes.c_ushort),
                ("height", ctypes.c_ushort), ("xhot", ctypes.c_ushort), ("yhot", ctypes.c_ushort),
                ("cursor_serial", ctypes.c_ulong), ("pixels", ctypes.c_void_p), ("atom", ctypes.c_ulong),
                ("name", ctypes.c_char_p)]


VP = ctypes.c_void_p
X11.XOpenDisplay.restype = VP
X11.XOpenDisplay.argtypes = [ctypes.c_char_p]
X11.XDefaultRootWindow.restype = ctypes.c_ulong
X11.XDefaultRootWindow.argtypes = [VP]
X11.XFlush.argtypes = [VP]
X11.XSync.argtypes = [VP, ctypes.c_int]
X11.XKeysymToKeycode.restype = ctypes.c_ubyte
X11.XKeysymToKeycode.argtypes = [VP, ctypes.c_ulong]
X11.XStringToKeysym.restype = ctypes.c_ulong
X11.XStringToKeysym.argtypes = [ctypes.c_char_p]
X11.XDisplayKeycodes.argtypes = [VP, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
X11.XGetKeyboardMapping.restype = ctypes.POINTER(ctypes.c_ulong)
X11.XGetKeyboardMapping.argtypes = [VP, ctypes.c_ubyte, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
X11.XChangeKeyboardMapping.argtypes = [VP, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_ulong), ctypes.c_int]
X11.XFree.argtypes = [VP]
X11.XInternAtom.restype = ctypes.c_ulong
X11.XInternAtom.argtypes = [VP, ctypes.c_char_p, ctypes.c_int]
X11.XChangeProperty.argtypes = [VP, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                ctypes.c_void_p, ctypes.c_int]
X11.XQueryExtension.argtypes = [VP, ctypes.c_char_p] + [ctypes.POINTER(ctypes.c_int)] * 3
X11.XNextEvent.argtypes = [VP, ctypes.c_void_p]
X11.XGetEventData.argtypes = [VP, ctypes.POINTER(XEventCookie)]
X11.XFreeEventData.argtypes = [VP, ctypes.POINTER(XEventCookie)]

XTST = ctypes.CDLL("libXtst.so.6")
XTST.XTestFakeKeyEvent.argtypes = [VP, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
XTST.XTestFakeButtonEvent.argtypes = [VP, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
XTST.XTestFakeMotionEvent.argtypes = [VP, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]

XI = ctypes.CDLL("libXi.so.6")
XI.XIQueryVersion.argtypes = [VP, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
XI.XISelectEvents.argtypes = [VP, ctypes.c_ulong, ctypes.POINTER(XIEventMask), ctypes.c_int]
XI.XIQueryDevice.restype = ctypes.POINTER(XIDeviceInfo)
XI.XIQueryDevice.argtypes = [VP, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
XI.XIFreeDeviceInfo.argtypes = [ctypes.POINTER(XIDeviceInfo)]

try:
    XFIXES = ctypes.CDLL("libXfixes.so.3")
    XFIXES.XFixesGetCursorImage.restype = ctypes.POINTER(XFixesCursorImage)
    XFIXES.XFixesGetCursorImage.argtypes = [VP]
except OSError:
    XFIXES = None

KS = {"BackSpace": 0xff08, "Tab": 0xff09, "Return": 0xff0d, "Escape": 0xff1b, "Delete": 0xffff,
      "Left": 0xff51, "Up": 0xff52, "Right": 0xff53, "Down": 0xff54, "Home": 0xff50, "End": 0xff57,
      "Shift_L": 0xffe1, "Control_L": 0xffe3, "Alt_L": 0xffe9, "Super_L": 0xffeb}
TEXT_CURSORS = {"xterm", "text", "ibeam", "IBeam"}
POINTER_CURSORS = {"left_ptr", "default", "arrow", "top_left_arrow", "hand1", "hand2", "pointer"}


def keysym_for(ch):
    if ch == "\n":
        return KS["Return"]
    if ch == "\t":
        return KS["Tab"]
    cp = ord(ch)
    if 0x20 <= cp <= 0x7e or 0xa0 <= cp <= 0xff:
        return cp
    return 0x01000000 | cp


class Typer:
    """Sends keys and clicks through XTest, on its own connection and thread, in order."""

    def __init__(self):
        self.q = queue.Queue()
        self.dpy = X11.XOpenDisplay(None)
        self.spare = []          # keycodes with nothing on them: borrowed for characters the layout lacks
        self.borrowed = {}       # keysym -> keycode
        self.last = 0
        threading.Thread(target=self.run, daemon=True).start()

    def put(self, *item):
        self.q.put(item)

    def mapping(self):
        if getattr(self, "_table", None) and time.monotonic() - self._table[0] < 3:
            return self._table[1]
        lo, hi = ctypes.c_int(), ctypes.c_int()
        X11.XDisplayKeycodes(self.dpy, ctypes.byref(lo), ctypes.byref(hi))
        per = ctypes.c_int()
        n = hi.value - lo.value + 1
        syms = X11.XGetKeyboardMapping(self.dpy, lo.value, n, ctypes.byref(per))
        table = {}
        for i in range(n):
            table[lo.value + i] = [syms[i * per.value + j] for j in range(per.value)]
        X11.XFree(syms)
        self._table = (time.monotonic(), table)
        return table

    def find(self, ks):
        """(keycode, needs shift) for a keysym on the current layout, else a borrowed spare key."""
        table = self.mapping()
        for level in (0, 1):
            for kc, syms in table.items():
                if len(syms) > level and syms[level] == ks:
                    return kc, level == 1
        if ks in self.borrowed:
            return self.borrowed[ks], False
        if not self.spare:
            self.spare = [kc for kc, syms in sorted(table.items(), reverse=True)
                          if kc > 8 and all(s == 0 for s in syms)][:24]
        if not self.spare:
            return None, False
        used = set(self.borrowed.values())
        free = [kc for kc in self.spare if kc not in used]
        if not free:
            self.release_borrowed()
            free = list(self.spare)
        kc = free[0]
        arr = (ctypes.c_ulong * 2)(ks, ks)
        X11.XChangeKeyboardMapping(self.dpy, kc, 2, arr, 1)
        X11.XSync(self.dpy, 0)
        self._table = None
        time.sleep(0.04)   # let apps take in the new key before it is pressed
        self.borrowed[ks] = kc
        return kc, False

    def release_borrowed(self):
        for kc in self.borrowed.values():
            arr = (ctypes.c_ulong * 2)(0, 0)
            X11.XChangeKeyboardMapping(self.dpy, kc, 2, arr, 1)
        self.borrowed = {}
        self._table = None
        X11.XSync(self.dpy, 0)

    def tap(self, kc, mods=()):
        held = [X11.XKeysymToKeycode(self.dpy, KS[m]) for m in mods]
        for m in held:
            XTST.XTestFakeKeyEvent(self.dpy, m, 1, 0)
        XTST.XTestFakeKeyEvent(self.dpy, kc, 1, 0)
        XTST.XTestFakeKeyEvent(self.dpy, kc, 0, 0)
        for m in reversed(held):
            XTST.XTestFakeKeyEvent(self.dpy, m, 0, 0)
        X11.XFlush(self.dpy)

    def type_char(self, ch, mods=()):
        kc, shift = self.find(keysym_for(ch))
        if kc:
            self.tap(kc, tuple(mods) + (("Shift_L",) if shift else ()))
            time.sleep(0.004)

    def cursor_name(self):
        if not XFIXES:
            return ""
        img = XFIXES.XFixesGetCursorImage(self.dpy)
        if not img:
            return ""
        name = (img.contents.name or b"").decode(errors="ignore")
        X11.XFree(img)
        return name

    def run(self):
        while True:
            try:
                item = self.q.get(timeout=6)
            except queue.Empty:
                if self.borrowed:
                    self.release_borrowed()
                continue
            kind = item[0]
            try:
                if kind == "text":
                    for ch in item[1]:
                        self.type_char(ch, item[2] if len(item) > 2 else ())
                elif kind == "key":
                    kc = X11.XKeysymToKeycode(self.dpy, KS.get(item[1], 0) or X11.XStringToKeysym(item[1].encode()))
                    if kc:
                        self.tap(kc, item[2] if len(item) > 2 else ())
                elif kind == "click":
                    _, x, y = item
                    XTST.XTestFakeMotionEvent(self.dpy, -1, int(x), int(y), 0)
                    XTST.XTestFakeButtonEvent(self.dpy, 1, 1, 0)
                    XTST.XTestFakeButtonEvent(self.dpy, 1, 0, 0)
                    X11.XFlush(self.dpy)
                elif kind == "write":
                    # Ink turned into text: into the box under where it was written, if that is a box.
                    _, x, y, text = item
                    XTST.XTestFakeMotionEvent(self.dpy, -1, int(x), int(y), 0)
                    X11.XSync(self.dpy, 0)
                    time.sleep(0.09)
                    name = self.cursor_name()
                    if name in TEXT_CURSORS or name not in POINTER_CURSORS:
                        XTST.XTestFakeButtonEvent(self.dpy, 1, 1, 0)
                        XTST.XTestFakeButtonEvent(self.dpy, 1, 0, 0)
                        X11.XSync(self.dpy, 0)
                        time.sleep(0.12)
                    for ch in text:
                        self.type_char(ch)
                elif kind == "scroll":
                    _, x, y, button, n = item
                    XTST.XTestFakeMotionEvent(self.dpy, -1, int(x), int(y), 0)
                    for _ in range(n):
                        XTST.XTestFakeButtonEvent(self.dpy, button, 1, 0)
                        XTST.XTestFakeButtonEvent(self.dpy, button, 0, 0)
                    X11.XFlush(self.dpy)
                elif kind == "call":
                    item[1]()
            except Exception as e:
                print("kuhaku-tablet: typing:", e, file=sys.stderr)


class Props:
    """Window properties set from the main thread (struts for the keyboard)."""

    def __init__(self):
        self.dpy = X11.XOpenDisplay(None)

    def strut(self, xid, bottom, x0, x1):
        if not self.dpy or not xid:
            return
        cardinal = 6
        partial = (ctypes.c_long * 12)(0, 0, 0, bottom, 0, 0, 0, 0, 0, 0, x0 if bottom else 0, x1 if bottom else 0)
        plain = (ctypes.c_long * 4)(0, 0, 0, bottom)
        X11.XChangeProperty(self.dpy, xid, X11.XInternAtom(self.dpy, b"_NET_WM_STRUT_PARTIAL", 0), cardinal, 32, 0,
                            partial, 12)
        X11.XChangeProperty(self.dpy, xid, X11.XInternAtom(self.dpy, b"_NET_WM_STRUT", 0), cardinal, 32, 0, plain, 4)
        X11.XFlush(self.dpy)


class PenWatcher:
    """Notices the pen near the screen (it reports while it hovers), from XInput2 raw events."""
    RAW_KEY, RAW_PRESS, RAW_RELEASE, RAW_MOTION, HIERARCHY = 13, 15, 16, 17, 11

    def __init__(self, on_event):
        self.on_event = on_event
        threading.Thread(target=self.run, daemon=True).start()

    def pens(self, dpy):
        n = ctypes.c_int()
        info = XI.XIQueryDevice(dpy, 0, ctypes.byref(n))
        ids = set()
        for i in range(n.value):
            name = (info[i].name or b"").decode(errors="ignore").lower()
            if ("pen" in name or "stylus" in name or "eraser" in name) and "finger" not in name \
                    and "xtest" not in name:
                ids.add(info[i].deviceid)
        self.keyboards = set()
        for i in range(n.value):
            name = (info[i].name or b"").decode(errors="ignore").lower()
            if "at translated" in name or "i8042" in name:
                self.keyboards.add(info[i].deviceid)
        XI.XIFreeDeviceInfo(info)
        return ids

    def run(self):
        dpy = X11.XOpenDisplay(None)
        if not dpy:
            return
        op, ev, err = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        if not X11.XQueryExtension(dpy, b"XInputExtension", ctypes.byref(op), ctypes.byref(ev), ctypes.byref(err)):
            return
        major, minor = ctypes.c_int(2), ctypes.c_int(2)
        XI.XIQueryVersion(dpy, ctypes.byref(major), ctypes.byref(minor))
        bits = (ctypes.c_ubyte * 3)()
        for t in (self.RAW_KEY, self.RAW_PRESS, self.RAW_RELEASE, self.RAW_MOTION, self.HIERARCHY):
            bits[t >> 3] |= 1 << (t & 7)
        mask = XIEventMask(1, 3, bits)  # XIAllMasterDevices: raw events name the device they came from
        XI.XISelectEvents(dpy, X11.XDefaultRootWindow(dpy), ctypes.byref(mask), 1)
        X11.XFlush(dpy)
        pens = self.pens(dpy)
        if os.environ.get("KT_DEBUG"):
            print("pens", pens, file=sys.stderr, flush=True)
        buf = (ctypes.c_byte * 256)()
        cookie = ctypes.cast(buf, ctypes.POINTER(XEventCookie)).contents
        last_post = last_key = 0.0
        while True:
            X11.XNextEvent(dpy, buf)
            if cookie.type != 35 or cookie.extension != op.value:
                continue
            if not X11.XGetEventData(dpy, ctypes.byref(cookie)):
                continue
            try:
                if cookie.evtype == self.HIERARCHY:
                    pens = self.pens(dpy)
                    continue
                raw = ctypes.cast(cookie.data, ctypes.POINTER(XIRawEvent)).contents
                if cookie.evtype == self.RAW_KEY:
                    # the built-in keyboard only types when the computer is a laptop
                    if raw.sourceid in self.keyboards and time.monotonic() - last_key > 5:
                        last_key = time.monotonic()
                        GLib.idle_add(self.on_event, "typed")
                    continue
                if raw.sourceid not in pens and raw.deviceid not in pens:
                    continue
                now = time.monotonic()
                if cookie.evtype == self.RAW_MOTION:
                    if now - last_post > 0.08:
                        last_post = now
                        GLib.idle_add(self.on_event, "near")
                else:
                    GLib.idle_add(self.on_event, "down" if cookie.evtype == self.RAW_PRESS else "up")
            finally:
                X11.XFreeEventData(dpy, ctypes.byref(cookie))


class Hinge:
    """Laptop or tablet from the two motion sensors, one in the lid and one in the base.

    Both turn about the hinge, so the angle between what each feels of gravity, around
    the hinge, follows how far the screen is opened. The angle while the built-in keyboard
    is being typed on is "laptop" (learned and kept); folded far from it is "tablet".
    """
    STATE = HOME / ".local/state/kuhaku/tablet-hinge.json"
    FOLD = 80        # degrees from the laptop angle that count as folded back
    BACK = 55        # and back under this is a laptop again

    def __init__(self, on_change):
        self.on_change = on_change
        self.sensors = []
        for d in sorted(Path("/sys/bus/iio/devices").glob("iio:device*")):
            try:
                if (d / "name").read_text().strip() == "accel_3d":
                    self.sensors.append(d)
            except OSError:
                pass
        self.ok = len(self.sensors) == 2
        self.ref = load_json(self.STATE, {"laptop": None}).get("laptop")
        self.tablet = None
        self.votes = 0
        self.last = None
        if self.ok:
            GLib.timeout_add(700, self.poll)

    def read(self, d):
        try:
            return [int((d / f"in_accel_{a}_raw").read_text()) for a in "xyz"]
        except (OSError, ValueError):
            return None

    def angle(self):
        a, b = self.read(self.sensors[0]), self.read(self.sensors[1])
        if not a or not b:
            return None
        # the hinge is each sensor's x axis: only the y/z part turns with the screen
        for v in (a, b):
            if math.hypot(v[1], v[2]) < 0.6 * math.sqrt(sum(c * c for c in v)) or not any(v):
                return None     # standing on its side: the hinge points down, no reading
        return math.degrees(math.atan2(a[1], a[2]) - math.atan2(b[1], b[2])) % 360

    @staticmethod
    def apart(x, y):
        d = abs(x - y) % 360
        return min(d, 360 - d)

    def typed(self):
        """The built-in keyboard is in use: this is how the hinge sits as a laptop."""
        ang = self.angle()
        if ang is None:
            return
        self.ref = ang if self.ref is None else (self.ref + ((ang - self.ref + 180) % 360 - 180) * 0.3) % 360
        try:
            self.STATE.parent.mkdir(parents=True, exist_ok=True)
            self.STATE.write_text(json.dumps({"laptop": round(self.ref, 1)}))
        except OSError:
            pass
        if self.tablet:
            self.tablet, self.votes = False, 0
            self.on_change(False)

    def poll(self):
        ang = self.angle()
        if ang is None:
            return True
        self.last = ang
        if self.ref is None:
            self.ref = ang          # it starts life as a laptop
            return True
        d = self.apart(ang, self.ref)
        want = d > self.FOLD if not self.tablet else d > self.BACK
        if want != bool(self.tablet):
            self.votes += 1
            if self.votes >= 2:      # the same for well over a second, not a bump
                self.tablet, self.votes = want, 0
                self.on_change(want)
        else:
            self.votes = 0
        return True


class TabletSwitch:
    """Laptop or tablet: SW_TABLET_MODE from the hinge switch, read straight from the device."""
    EV_SW, SW_TABLET_MODE = 5, 1
    settle_id = None

    def settle(self, value):
        """Act on the switch once it has stayed put for a moment."""
        if self.settle_id:
            GLib.source_remove(self.settle_id)

        def now():
            self.settle_id = None
            self.on_change(value)
            return False
        self.settle_id = GLib.timeout_add(600, now)
        return False

    def __init__(self, on_change):
        self.on_change = on_change
        self.path = None
        for dev in sorted(Path("/sys/class/input").glob("event*")):
            try:
                name = (dev / "device/name").read_text().strip()
                caps = (dev / "device/capabilities/sw").read_text().split()
            except OSError:
                continue
            if caps and int(caps[-1], 16) & (1 << self.SW_TABLET_MODE):
                self.path = Path("/dev/input") / dev.name
                self.name = name
                break
        self.readable = bool(self.path) and os.access(self.path, os.R_OK)
        if self.readable:
            threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        size = struct.calcsize("llHHi")
        try:
            with open(self.path, "rb", buffering=0) as f:
                state = (ctypes.c_ubyte * 8)()
                eviocgsw = (2 << 30) | (8 << 16) | (ord("E") << 8) | 0x1b
                fcntl.ioctl(f, eviocgsw, state)
                GLib.idle_add(self.on_change, bool(state[0] & (1 << self.SW_TABLET_MODE)))
                while True:
                    data = f.read(size)
                    if len(data) < size:
                        break
                    _s, _u, etype, code, value = struct.unpack("llHHi", data)
                    if etype == self.EV_SW and code == self.SW_TABLET_MODE:
                        log("switch", value)
                        GLib.idle_add(self.settle, bool(value))
        except OSError as e:
            print("kuhaku-tablet: tablet switch:", e, file=sys.stderr)


# --- handwriting -------------------------------------------------------------------------

def recognize(strokes, width, height, lang):
    """Candidates for some ink: strokes are lists of (x, y, t_ms)."""
    ink = [[[round(p[0]) for p in s], [round(p[1]) for p in s], [int(p[2]) for p in s]] for s in strokes if s]
    body = {"options": "enable_pre_space", "requests": [{
        "writing_guide": {"writing_area_width": int(width), "writing_area_height": int(height)},
        "ink": ink, "language": lang}]}
    req = urllib.request.Request(
        "https://inputtools.google.com/request?ime=handwriting&app=kuhaku&cs=1&oe=UTF-8",
        json.dumps(body).encode(), {"Content-Type": "application/json", "User-Agent": "Kuhaku"})
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
        for a, b in zip(s, s[1:]):
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
        return 0.5


def is_finger(event):
    dev = event.get_source_device()
    return bool(dev) and dev.get_source() == Gdk.InputSource.TOUCHSCREEN


# --- the keyboard ------------------------------------------------------------------------

ALTS = {
    "q": "1", "w": "2", "e": "3éěèêë", "r": "4ř", "t": "5ť", "y": "6ýÿ", "u": "7úůüûù", "i": "8íìîï",
    "o": "9óöôòøœ", "p": "0", "a": "áäàâåæ", "s": "šßś", "d": "ď", "f": "", "g": "ğ", "h": "", "j": "",
    "k": "", "l": "ľłĺ", "z": "žźż", "x": "", "c": "čçć", "v": "", "b": "", "n": "ňñń", "m": "",
    ".": "…!?;:", ",": "\"'„“”", "-": "–—_", "'": "‘’`\"", "?": "¿", "!": "¡", "€": "$£¥¢", "\"": "„“”«»",
}


def K(label, w=1.0, act=None, alts=None):
    return {"label": label, "w": w, "act": act or ("char", label),
            "alts": ALTS.get(label, "") if alts is None else alts}


def row_bottom(layer_key):
    return [K("123" if layer_key == "abc" else "abc", 1.5, ("layer", "123" if layer_key == "abc" else "abc")),
            K("ctrl", 1, ("mod", "Control_L")), K("lang", 1, ("lang",)), K("space", 5, ("space",)),
            K("left", 1, ("key", "Left")), K("right", 1, ("key", "Right")), K("hide", 1, ("hide",))]


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
        self.shift = 0              # 0 off, 1 next letter, 2 caps
        self.shift_tap = 0.0
        self.mods = set()
        self.mode = "keys"          # or "write": the pad
        self.touches = {}
        self.anims = []             # (kind, x, y, start)
        self.bubble = None          # (key, start, ending)
        self.alts = None            # {"key","items","rects","sel","touch"}
        self.last_chars = []        # (char, time), what this keyboard typed lately
        self.visible = False
        self.slide = 0.0            # 0 hidden .. 1 shown
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
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                             Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.TOUCH_MASK)
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
        self.margin = round(self.kh * 1.55)      # room above the keys for previews and accents
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
        for i, (lab, act) in enumerate((("back", ("back",)), ("space", ("space",)), ("return", ("key", "Return")),
                                       ("keys", ("mode", "keys")))):
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
        if fill[3] > 0.3:   # paper keys lift a little off the paper
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
                text_at(cr, k["alts"][0], self.kh * 0.16, t["muted"], x + w - self.kh * 0.2, y + self.kh * 0.22,
                        alpha=0.55)
        elif act == "space":
            lang = self.app.settings["language"]
            text_at(cr, LANGS.get(lang, lang), self.kh * 0.2, t["muted"], cx, cy, alpha=0.75)
        elif act == "lang":
            text_at(cr, self.app.settings["language"].upper(), self.kh * 0.2, color, cx, cy,
                    weight=Pango.Weight.NORMAL)
        elif act == "shift":
            icon(cr, "caps" if self.shift == 2 else ("shift-on" if self.shift else "shift"), cx, cy, self.kh * 0.42,
                 color)
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
                tw = text_at(cr, "Keys" if mine == "keys" else "Write", s * 0.34,
                             t["fg"] if on else t["muted"], x + w / 2, cy, alpha=1 if on else 0.8)
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
                    text_at(cr, c, s * (0.4 if i == 0 else 0.34), t["fg"] if i == 0 else t["muted"], cx, cy,
                            weight=Pango.Weight.LIGHT)
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
            text_at(cr, "Write here, with the pen or a finger", self.kh * 0.24, t["muted"], x + w / 2, y + h * 0.4,
                    alpha=0.55)
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
        text_at(cr, self.key_label(k), self.kh * 0.62, t["fg"], bx + bw / 2, by + bh * 0.46,
                weight=Pango.Weight.ULTRALIGHT, alpha=a)
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
            text_at(cr, ch.upper() if self.shift else ch, self.kh * 0.38, t["accent"] if sel else t["fg"],
                    cx0 + cw / 2, cy0 + chh / 2)

    # --- input -----------------------------------------------------------------------------
    def on_touch(self, _w, ev):
        seq = ev.get_event_sequence()
        x, y = ev.x, ev.y - (1 - self.slide) * (self.body + 8)
        if ev.type == Gdk.EventType.TOUCH_BEGIN:
            if self.app.pen_recent(1.2):
                return True     # a hand resting while the pen writes: not a finger on a key
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
        self.alts = {"items": items, "rects": [(bx + i * cw, by, cw, bh) for i in range(len(items))],
                     "box": (bx, by, bw, bh), "sel": 0 if digit else 1}
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
                self.app.typer.put("key", "BackSpace", ("Control_L",))   # a word at a time
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


# --- the pen anywhere --------------------------------------------------------------------

def notes_in_front():
    try:
        pid = int(NOTES_FRONT.read_text())
        return Path(f"/proc/{pid}").exists()
    except (OSError, ValueError):
        return False


class Ink:
    """A clear sheet over the whole screen while the pen is near: what is written on it becomes type."""

    def __init__(self, app):
        self.app = app
        self.strokes = []
        self.cur = None
        self.state = "off"          # off, ready (shown), writing, reading, placing
        self.hide_id = self.recog_id = self.hold_id = None
        self.suspended_until = 0.0
        self.held = False
        self.result = None
        self.anim_t0 = 0.0
        self.tick_id = None
        self.win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.win.set_app_paintable(True)
        self.win.set_accept_focus(False)
        self.win.set_keep_above(True)
        visual = self.win.get_screen().get_rgba_visual()
        if visual:
            self.win.set_visual(visual)
        self.win.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                            Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.TOUCH_MASK)
        self.win.connect("draw", self.draw)
        self.win.connect("touch-event", self.on_touch)
        self.fingers = {}
        GLib.timeout_add(1000, self.watchdog)
        self.win.connect("button-press-event", self.on_press)
        self.win.connect("motion-notify-event", self.on_motion)
        self.win.connect("button-release-event", self.on_release)
        self.win.connect("realize", self.on_realize)
        self.fit()
        self.win.get_screen().connect("size-changed", lambda *_: self.fit())

    def on_realize(self, *_):
        gw = self.win.get_window()
        cur = Gdk.Cursor.new_from_name(gw.get_display(), "pencil") or Gdk.Cursor.new_from_name(gw.get_display(), "crosshair")
        if cur:
            gw.set_cursor(cur)

    def fit(self):
        s = self.win.get_screen()
        self.W, self.H = s.get_width(), s.get_height()
        self.win.move(0, 0)
        self.win.resize(self.W, self.H)

    def shape(self):
        region = cairo.Region(cairo.RectangleInt(0, 0, self.W, self.H))
        kb = self.app.keyboard.body_rect()
        if kb:
            region.subtract(cairo.RectangleInt(*[int(v) for v in kb]))
        self.win.input_shape_combine_region(region)

    # --- the pen comes and goes ------------------------------------------------------------
    def pen(self, what):
        now = time.monotonic()
        if self.state == "off" and notes_in_front():
            return False
        if what == "up" and self.held:
            self.held = False
            self.suspended_until = now + 0.8
            return False
        if not self.app.settings["pen_writing"] or now < self.suspended_until or self.held:
            if now < self.suspended_until and what == "near":
                self.suspended_until = max(self.suspended_until, now + 0.6)   # hovering on as a pointer
            return False
        if os.environ.get("KT_DEBUG") and self.state == "off":
            print("pen near", file=sys.stderr, flush=True)
        if self.state == "off":
            self.theme = THEMES[current_theme()]
            self.fit()
            self.shape()
            self.win.show()
            self.state = "ready"
        self.keep()
        return False

    def keep(self):
        if self.hide_id:
            GLib.source_remove(self.hide_id)
        self.hide_id = GLib.timeout_add(1500, self.idle_hide)

    def watchdog(self):
        """Never let the clear sheet stay over the screen without the pen: a lost pen-up
        (or anything else odd) would otherwise leave every tap and click caught on it."""
        if self.state != "off" and not self.app.pen_recent(4) and self.state not in ("reading", "placing"):
            log("ink: pen gone, sheet cleared", self.state)
            self.cancel()
        if self.state in ("reading", "placing") and not self.app.pen_recent(15):
            self.cancel()
        return True

    def on_touch(self, _w, ev):
        seq = ev.get_event_sequence()
        t = ev.type
        if t == Gdk.EventType.TOUCH_BEGIN:
            # while the pen is near (or there is writing on the sheet) every touch is the hand
            palm = self.app.pen_recent(1.2) or self.strokes or self.cur is not None
            self.fingers[seq] = None if palm else (ev.x_root, ev.y_root, time.monotonic())
        elif t == Gdk.EventType.TOUCH_UPDATE:
            f = self.fingers.get(seq)
            if f and abs(ev.x_root - f[0]) + abs(ev.y_root - f[1]) > 18:
                # a finger scrolling or dragging: get out of its way for the next touch
                self.fingers[seq] = None
                if not self.strokes:
                    self.off()
        elif t in (Gdk.EventType.TOUCH_END, Gdk.EventType.TOUCH_CANCEL):
            f = self.fingers.pop(seq, None)
            if f and t == Gdk.EventType.TOUCH_END and not self.strokes and time.monotonic() - f[2] < 0.5:
                self.pass_tap(ev.x_root, ev.y_root)
        return True

    def idle_hide(self):
        self.hide_id = None
        if self.state == "ready" and not self.strokes and not self.cur:
            self.off()
        return False

    def off(self):
        self.win.hide()
        self.state = "off"
        Gdk.Display.get_default().sync()

    def cancel(self):
        for attr in ("recog_id", "hold_id"):
            if getattr(self, attr):
                GLib.source_remove(getattr(self, attr))
                setattr(self, attr, None)
        self.strokes, self.cur, self.result = [], None, None
        self.off()

    def pass_tap(self, x, y):
        """A tap goes through to whatever is under it."""
        self.off()
        self.suspended_until = time.monotonic() + 0.35
        self.app.typer.put("click", x, y)

    # --- writing ---------------------------------------------------------------------------
    def on_press(self, _w, ev):
        if ev.button != 1:
            return True
        if is_finger(ev):
            self.finger = (ev.x_root, ev.y_root, time.monotonic())
            return True
        self.finger = None
        if self.recog_id:
            GLib.source_remove(self.recog_id)
            self.recog_id = None
        if self.state in ("reading", "placing"):
            return True
        if not self.strokes:
            self.t0 = time.monotonic()
            self.hold_id = GLib.timeout_add(480, self.on_hold, ev.x_root, ev.y_root)
        self.state = "writing"
        self.cur = [(ev.x_root, ev.y_root, (time.monotonic() - self.t0) * 1000, pressure(ev))]
        self.win.queue_draw()
        return True

    def on_motion(self, _w, ev):
        self.keep()
        if self.cur is None or is_finger(ev):
            return True
        last = self.cur[-1]
        if abs(ev.x_root - last[0]) + abs(ev.y_root - last[1]) < 1.2:
            return True
        self.cur.append((ev.x_root, ev.y_root, (time.monotonic() - self.t0) * 1000, pressure(ev)))
        if self.hold_id and abs(ev.x_root - self.cur[0][0]) + abs(ev.y_root - self.cur[0][1]) > 9:
            GLib.source_remove(self.hold_id)
            self.hold_id = None
        x0, y0 = min(last[0], ev.x_root) - 12, min(last[1], ev.y_root) - 12
        self.win.queue_draw_area(int(x0), int(y0), int(abs(last[0] - ev.x_root) + 24),
                                 int(abs(last[1] - ev.y_root) + 24))
        return True

    def on_hold(self, x, y):
        """The pen held still: it is a pointer until it is lifted away."""
        self.hold_id = None
        if self.cur and len(self.strokes) == 0:
            self.cur = None
            self.held = True
            self.off()
        return False

    def on_release(self, _w, ev):
        if ev.button != 1:
            return True
        if is_finger(ev):
            f = getattr(self, "finger", None)
            if f and not self.strokes and abs(ev.x_root - f[0]) + abs(ev.y_root - f[1]) < 14:
                self.pass_tap(ev.x_root, ev.y_root)
            else:
                self.off() if not self.strokes else None
            return True
        if self.hold_id:
            GLib.source_remove(self.hold_id)
            self.hold_id = None
        if self.cur is None:
            return True
        s = self.cur
        self.cur = None
        span = max(abs(p[0] - s[0][0]) + abs(p[1] - s[0][1]) for p in s)
        quick = (s[-1][2] - s[0][2]) < 350
        if not self.strokes and span < 10 and quick:
            self.state = "ready"
            self.pass_tap(s[0][0], s[0][1])
            return True
        if not self.strokes and len(s) > 3:
            # one long straight line is a gesture, not writing: up or down scrolls what is under it
            dx, dy = s[-1][0] - s[0][0], s[-1][1] - s[0][1]
            dist = math.hypot(dx, dy)
            length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(s, s[1:]))
            if dist > 0 and length / dist < 1.18:
                if abs(dy) > 140 and abs(dy) > 2.5 * abs(dx):
                    self.off()
                    self.suspended_until = time.monotonic() + 0.3
                    self.app.typer.put("scroll", s[0][0], s[0][1], 4 if dy > 0 else 5, max(1, int(abs(dy) / 40)))
                    return True
                if abs(dx) > 260 and abs(dx) > 4 * abs(dy):
                    self.state = "ready"
                    self.win.queue_draw()
                    return True
        self.strokes.append(s)
        self.recog_id = GLib.timeout_add(820, self.read)
        return True

    def bbox(self):
        pts = [p for s in self.strokes for p in s]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def read(self):
        self.recog_id = None
        if not self.strokes:
            return False
        self.state = "reading"
        self.anim_t0 = time.monotonic()
        self.start_tick()
        strokes = self.strokes
        x0, y0, x1, y1 = self.bbox()
        lang = self.app.settings["language"]

        def work():
            try:
                cands = recognize([[(p[0] - x0 + 20, p[1] - y0 + 20, p[2]) for p in s] for s in strokes],
                                  x1 - x0 + 40, y1 - y0 + 40, lang)
            except Exception as e:
                print("kuhaku-tablet: reading the ink:", e, file=sys.stderr)
                cands = None
            GLib.idle_add(self.place, strokes, cands)
        threading.Thread(target=work, daemon=True).start()
        self.win.queue_draw()
        return False

    def place(self, strokes, cands):
        log("ink read", len(strokes), "strokes ->", (cands or ["(nothing)" if cands is not None else "(offline)"])[0])
        if os.environ.get("KT_DEBUG"):
            print("ink read", (cands or [])[:5], file=sys.stderr, flush=True)
        if strokes is not self.strokes:
            return False
        x0, y0, x1, y1 = self.bbox()
        self.state = "placing"
        self.anim_t0 = time.monotonic()
        self.result = (cands[0].strip() if cands else None, x0, y0, x1, y1, cands is None)
        self.start_tick()
        GLib.timeout_add(520 if cands else 900, self.finish)
        return False

    def finish(self):
        res = self.result
        self.strokes, self.result = [], None
        self.state = "ready"
        self.off()
        if res and res[0]:
            text, x0, y0, x1, y1, _ = res
            self.suspended_until = time.monotonic() + 0.5
            self.app.typer.put("write", x0 + min(14, (x1 - x0) / 4), (y0 + y1) / 2, text)
        return False

    def start_tick(self):
        if not self.tick_id:
            self.tick_id = GLib.timeout_add(16, self.tick)

    def tick(self):
        if self.strokes:
            x0, y0, x1, y1 = self.bbox()
            self.win.queue_draw_area(int(x0) - 40, int(y0) - 40, int(x1 - x0) + 80 + int(max(0, (y1 - y0) * 0.62 * 12)),
                                     int(y1 - y0) + 120)
        else:
            self.win.queue_draw()
        if self.state in ("reading", "placing"):
            return True
        self.tick_id = None
        return False

    def draw(self, _w, cr):
        t = getattr(self, "theme", THEMES["day"])
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        width = 3.0
        strokes = self.strokes + ([self.cur] if self.cur else [])
        if self.state == "reading":
            # the ink breathes in the accent while it is being read
            p = 0.5 + 0.5 * math.sin((time.monotonic() - self.anim_t0) * 6)
            draw_ink(cr, strokes, t["fg"], 0.85 - 0.25 * p, width)
            draw_ink(cr, strokes, t["accent"], 0.15 + 0.35 * p, width)
        elif self.state == "placing" and self.result:
            text, x0, y0, x1, y1, offline = self.result
            p = ease((time.monotonic() - self.anim_t0) / 0.42)
            draw_ink(cr, strokes, t["accent"] if text else t["muted"], 0.8 * (1 - p), width * (1 + 0.6 * p))
            if text:
                size = max(16, min(64, (y1 - y0) * 0.62))
                layout_x = x0
                cr.save()
                cr.translate(0, (1 - p) * 8)
                text_at(cr, text, size, t["fg"], layout_x, (y0 + y1) / 2, weight=Pango.Weight.LIGHT, alpha=p,
                        align="left")
                cr.restore()
            elif offline:
                text_at(cr, "No connection to read handwriting", 15, t["muted"], (x0 + x1) / 2, y1 + 24,
                        alpha=min(1, p * 2))
        else:
            draw_ink(cr, strokes, t["fg"], 0.88, width)
        return True


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


# --- together ----------------------------------------------------------------------------

class App:
    def __init__(self):
        self.settings = settings()
        self.tablet = False
        self.asked_up = False
        self.last_pen = 0.0
        self.password_up = self.was_visible = False
        GLib.timeout_add(400, self.watch_password)
        self.typer = Typer()
        self.props = Props()
        self.keyboard = Keyboard(self)
        self.ink = Ink(self)
        self.pill = Pill(self)
        self.pen = PenWatcher(self.pen_event)
        self.switch = TabletSwitch(self.set_tablet)
        # Without the switch (not readable, or none), the hinge angle from the motion sensors.
        self.hinge = Hinge(self.set_tablet) if not self.switch.readable else None
        if os.environ.get("KT_DEBUG"):
            print("switch", self.switch.path, self.switch.readable, "hinge",
                  self.hinge and self.hinge.ok, file=sys.stderr, flush=True)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self.toggle_signal)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR2, self.tablet_signal)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.quit)
        # Other apps may ask for the keyboard (SIGWINCH) and put it away again (SIGHUP).
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGWINCH, self.show_signal)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGHUP, self.hide_signal)
        GLib.timeout_add_seconds(60, self.refresh_theme)
        self.write_mode()

    def pen_event(self, what):
        if what != "typed":
            self.last_pen = time.monotonic()
        if what == "typed":
            if self.hinge:
                self.hinge.typed()
            return False
        return False        # the pen is a pen: no writing anywhere (only the keyboard's Write pad)

    def refresh_theme(self):
        self.keyboard.theme = THEMES[current_theme()]
        self.keyboard.area.queue_draw()
        return True

    def toggle_signal(self):
        self.keyboard.toggle()
        return True

    def show_signal(self):
        if not self.keyboard.visible:
            self.keyboard.show()
            self.asked_up = True
        return True

    def hide_signal(self):
        # Only takes back a keyboard it was asked to bring up: the keyboard that came up with
        # the fold stays until it is folded back or put away by hand (no flicking on and off).
        if self.keyboard.visible and self.asked_up:
            self.keyboard.hide()
        self.asked_up = False
        return True

    def pen_recent(self, seconds):
        return time.monotonic() - self.last_pen < seconds

    def watch_password(self):
        """Kūhaku's lock or password window is up: it has keys of its own, so this keyboard steps away."""
        up = False
        try:
            pid = int(PASSWORD_FRONT.read_text())
            up = Path(f"/proc/{pid}").exists()
        except (OSError, ValueError):
            pass
        if up and not self.password_up:
            self.password_up, self.was_visible = True, self.keyboard.visible
            self.keyboard.hide()
            self.pill.hide()
            self.ink.cancel()
        elif not up and self.password_up:
            self.password_up = False
            if self.was_visible and self.tablet:
                self.keyboard.show()
            else:
                self.keyboard_changed()
        return True

    def write_mode(self):
        try:
            MODE_FILE.write_text("1" if self.tablet else "0")
        except OSError:
            pass

    def tablet_signal(self):
        self.set_tablet(not self.tablet)
        return True

    def set_tablet(self, on):
        if on == self.tablet:
            return False
        log("tablet" if on else "laptop")
        self.tablet = on
        self.asked_up = False
        self.ink.cancel()
        self.write_mode()
        if on:
            self.keyboard.show()
        else:
            self.keyboard.hide()
            self.pill.hide()
        return False

    def keyboard_changed(self):
        if self.keyboard.visible or self.password_up:
            self.pill.hide()
        elif self.tablet:
            self.pill.show()
        if self.ink.state != "off":
            self.ink.shape()

    def quit(self):
        self.tablet = False
        self.write_mode()
        self.keyboard.set_strut(False)
        self.typer.put("call", self.typer.release_borrowed)
        time.sleep(0.1)
        Gtk.main_quit()
        return False


def running_pid():
    try:
        pid = int(PID_FILE.read_text())
        if "kuhaku-tablet" in Path(f"/proc/{pid}/cmdline").read_text():
            return pid
    except Exception:
        pass
    return None


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    pid = running_pid()
    if cmd in ("toggle", "tablet", "show", "hide") and (pid or cmd != "show"):
        if pid:
            os.kill(pid, {"toggle": signal.SIGUSR1, "tablet": signal.SIGUSR2,
                          "show": signal.SIGWINCH, "hide": signal.SIGHUP}[cmd])
        return
    if pid and pid != os.getpid():
        print("kuhaku-tablet is already running", file=sys.stderr)
        return
    PID_FILE.write_text(str(os.getpid()))
    App()
    if cmd == "show":
        GLib.idle_add(lambda: os.kill(os.getpid(), signal.SIGUSR1) and False)
    Gtk.main()


if __name__ == "__main__":
    main()
