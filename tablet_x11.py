"""The touch keyboard and X11: typing and clicking (XTest), struts, the pen (XInput2), the hinge and tablet switch."""

import ctypes
import ctypes.util
import fcntl
import json
import math
import os
import queue
import struct
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import GLib

from tablet_base import HOME, X11, load_json, log

# --- X11: typing, clicking, struts, the pen -------------------------------------------------


class XEventCookie(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("extension", ctypes.c_int),
        ("evtype", ctypes.c_int),
        ("cookie", ctypes.c_uint),
        ("data", ctypes.c_void_p),
    ]


class XIRawEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("extension", ctypes.c_int),
        ("evtype", ctypes.c_int),
        ("time", ctypes.c_ulong),
        ("deviceid", ctypes.c_int),
        ("sourceid", ctypes.c_int),
        ("detail", ctypes.c_int),
        ("flags", ctypes.c_int),
    ]


class XIEventMask(ctypes.Structure):
    _fields_ = [("deviceid", ctypes.c_int), ("mask_len", ctypes.c_int), ("mask", ctypes.POINTER(ctypes.c_ubyte))]


class XIDeviceInfo(ctypes.Structure):
    _fields_ = [
        ("deviceid", ctypes.c_int),
        ("name", ctypes.c_char_p),
        ("use", ctypes.c_int),
        ("attachment", ctypes.c_int),
        ("enabled", ctypes.c_int),
        ("num_classes", ctypes.c_int),
        ("classes", ctypes.c_void_p),
    ]


class XFixesCursorImage(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_short),
        ("y", ctypes.c_short),
        ("width", ctypes.c_ushort),
        ("height", ctypes.c_ushort),
        ("xhot", ctypes.c_ushort),
        ("yhot", ctypes.c_ushort),
        ("cursor_serial", ctypes.c_ulong),
        ("pixels", ctypes.c_void_p),
        ("atom", ctypes.c_ulong),
        ("name", ctypes.c_char_p),
    ]


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
X11.XChangeProperty.argtypes = [
    VP,
    ctypes.c_ulong,
    ctypes.c_ulong,
    ctypes.c_ulong,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_int,
]
X11.XQueryExtension.argtypes = [VP, ctypes.c_char_p, *[ctypes.POINTER(ctypes.c_int)] * 3]
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
    XFIXES = None  # type: ignore[assignment]  # no XFixes: the cursor is not drawn

KS = {
    "BackSpace": 0xFF08,
    "Tab": 0xFF09,
    "Return": 0xFF0D,
    "Escape": 0xFF1B,
    "Delete": 0xFFFF,
    "Left": 0xFF51,
    "Up": 0xFF52,
    "Right": 0xFF53,
    "Down": 0xFF54,
    "Home": 0xFF50,
    "End": 0xFF57,
    "Shift_L": 0xFFE1,
    "Control_L": 0xFFE3,
    "Alt_L": 0xFFE9,
    "Super_L": 0xFFEB,
}
TEXT_CURSORS = {"xterm", "text", "ibeam", "IBeam"}
POINTER_CURSORS = {"left_ptr", "default", "arrow", "top_left_arrow", "hand1", "hand2", "pointer"}


def keysym_for(ch):
    if ch == "\n":
        return KS["Return"]
    if ch == "\t":
        return KS["Tab"]
    cp = ord(ch)
    if 0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF:
        return cp
    return 0x01000000 | cp


class Typer:
    """Sends keys and clicks through XTest, on its own connection and thread, in order."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self._table: tuple[float, dict[int, list]] | None = None
        self.dpy = X11.XOpenDisplay(None)
        self.spare = []  # keycodes with nothing on them: borrowed for characters the layout lacks
        self.borrowed = {}  # keysym -> keycode
        self.last = 0
        threading.Thread(target=self.run, daemon=True).start()

    def put(self, *item):
        self.q.put(item)

    def mapping(self):
        if self._table and time.monotonic() - self._table[0] < 3:
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
            self.spare = [
                kc for kc, syms in sorted(table.items(), reverse=True) if kc > 8 and all(s == 0 for s in syms)
            ][:24]
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
        time.sleep(0.04)  # let apps take in the new key before it is pressed
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
        X11.XChangeProperty(
            self.dpy, xid, X11.XInternAtom(self.dpy, b"_NET_WM_STRUT_PARTIAL", 0), cardinal, 32, 0, partial, 12
        )
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
            if ("pen" in name or "stylus" in name or "eraser" in name) and "finger" not in name and "xtest" not in name:
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
    FOLD = 80  # degrees from the laptop angle that count as folded back
    BACK = 55  # and back under this is a laptop again

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
                return None  # standing on its side: the hinge points down, no reading
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
            self.ref = ang  # it starts life as a laptop
            return True
        d = self.apart(ang, self.ref)
        want = d > self.FOLD if not self.tablet else d > self.BACK
        if want != bool(self.tablet):
            self.votes += 1
            if self.votes >= 2:  # the same for well over a second, not a bump
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
        self.path: Path | None = None
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
        self.readable = self.path is not None and os.access(self.path, os.R_OK)
        if self.readable:
            threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        if self.path is None:
            return
        size = struct.calcsize("llHHi")
        try:
            with open(self.path, "rb", buffering=0) as f:
                state = (ctypes.c_ubyte * 8)()
                eviocgsw = (2 << 30) | (8 << 16) | (ord("E") << 8) | 0x1B
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
