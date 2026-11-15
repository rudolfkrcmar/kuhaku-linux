"""X11 for Kūhaku's small system services, straight from the system's libraries (ctypes).

One place for the C signatures: kuhaku-keys.py (keys, XSETTINGS, pointers) and kuhaku-powerd.py
(how long since the last touch) share them, and tests/test_x11.py runs them against a real X
server. Every function used is declared here with its exact argument and result types; ctypes
would otherwise guess int and cut 64-bit values in half.
"""

import ctypes

X = ctypes.CDLL("libX11.so.6")
Xi = ctypes.CDLL("libXi.so.6")
Xss = ctypes.CDLL("libXss.so.1")
vp, ul = ctypes.c_void_p, ctypes.c_ulong
X.XOpenDisplay.restype = vp
X.XOpenDisplay.argtypes = [ctypes.c_char_p]
X.XDefaultRootWindow.restype = ul
X.XDefaultRootWindow.argtypes = [vp]
X.XInternAtom.restype = ul
X.XInternAtom.argtypes = [vp, ctypes.c_char_p, ctypes.c_int]
X.XStringToKeysym.restype = ul
X.XStringToKeysym.argtypes = [ctypes.c_char_p]
X.XKeysymToKeycode.restype = ctypes.c_ubyte
X.XKeysymToKeycode.argtypes = [vp, ul]
X.XGrabKey.argtypes = [vp, ctypes.c_int, ctypes.c_uint, ul, ctypes.c_int, ctypes.c_int, ctypes.c_int]
X.XUngrabKey.argtypes = [vp, ctypes.c_int, ctypes.c_uint, ul]
X.XCreateSimpleWindow.restype = ul
X.XCreateSimpleWindow.argtypes = [
    vp,
    ul,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
    ctypes.c_uint,
    ctypes.c_uint,
    ul,
    ul,
]
X.XSetSelectionOwner.argtypes = [vp, ul, ul, ul]
X.XGetSelectionOwner.restype = ul
X.XGetSelectionOwner.argtypes = [vp, ul]
X.XChangeProperty.argtypes = [vp, ul, ul, ul, ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
X.XSendEvent.argtypes = [vp, ul, ctypes.c_int, ctypes.c_long, vp]
X.XSelectInput.argtypes = [vp, ul, ctypes.c_long]
X.XConnectionNumber.argtypes = [vp]
X.XPending.argtypes = [vp]
X.XNextEvent.argtypes = [vp, vp]
X.XFlush.argtypes = [vp]
X.XSync.argtypes = [vp, ctypes.c_int]
X.XFree.argtypes = [vp]
X.XRefreshKeyboardMapping.argtypes = [vp]
X.XCloseDisplay.argtypes = [vp]
X.XGetWindowProperty.argtypes = [
    vp, ul, ul, ctypes.c_long, ctypes.c_long, ctypes.c_int, ul,
    ctypes.POINTER(ul), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ul), ctypes.POINTER(ul),
    ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
]  # fmt: skip
ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, vp, vp)


class XIDeviceInfo(ctypes.Structure):
    _fields_ = [
        ("deviceid", ctypes.c_int),
        ("name", ctypes.c_char_p),
        ("use", ctypes.c_int),
        ("attachment", ctypes.c_int),
        ("enabled", ctypes.c_int),
        ("num_classes", ctypes.c_int),
        ("classes", vp),
    ]


Xi.XIQueryDevice.restype = ctypes.POINTER(XIDeviceInfo)
Xi.XIQueryDevice.argtypes = [vp, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
Xi.XIFreeDeviceInfo.argtypes = [vp]
Xi.XIChangeProperty.argtypes = [vp, ctypes.c_int, ul, ul, ctypes.c_int, ctypes.c_int, vp, ctypes.c_int]
Xi.XIGetProperty.argtypes = [
    vp, ctypes.c_int, ul, ctypes.c_long, ctypes.c_long, ctypes.c_int, ul,
    ctypes.POINTER(ul), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ul), ctypes.POINTER(ul), ctypes.POINTER(vp),
]  # fmt: skip

KEY_PRESS, CLIENT_MESSAGE, SELECTION_CLEAR, MAPPING_NOTIFY = 2, 33, 29, 34


class KeyEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ul),
        ("send_event", ctypes.c_int),
        ("display", vp),
        ("window", ul),
        ("root", ul),
        ("subwindow", ul),
        ("time", ul),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("x_root", ctypes.c_int),
        ("y_root", ctypes.c_int),
        ("state", ctypes.c_uint),
        ("keycode", ctypes.c_uint),
        ("same_screen", ctypes.c_int),
    ]


class ClientMessage(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ul),
        ("send_event", ctypes.c_int),
        ("display", vp),
        ("window", ul),
        ("message_type", ul),
        ("format", ctypes.c_int),
        ("data", ctypes.c_long * 5),
    ]


class Event(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("key", KeyEvent), ("client", ClientMessage), ("pad", ctypes.c_long * 24)]


class XssInfo(ctypes.Structure):
    _fields_ = [
        ("window", ul),
        ("state", ctypes.c_int),
        ("kind", ctypes.c_int),
        ("til_or_since", ul),
        ("idle", ul),
        ("eventMask", ul),
    ]


Xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(XssInfo)
Xss.XScreenSaverQueryInfo.argtypes = [vp, ul, ctypes.POINTER(XssInfo)]


def open_display():
    """The X display named by $DISPLAY, or None."""
    return X.XOpenDisplay(None) or None


def keycode(dpy, name):
    """The key that types this keysym name ("F12", "XF86AudioMute") here, or 0."""
    sym = X.XStringToKeysym(name.encode())
    return X.XKeysymToKeycode(dpy, sym) if sym else 0


def press(dpy, *names):
    """Presses and releases keys as if typed (XTest): modifiers first, released in reverse."""
    # XTest only for typing on purpose (tests), so the services never need it to start.
    xtst = ctypes.CDLL("libXtst.so.6")
    xtst.XTestFakeKeyEvent.argtypes = [vp, ctypes.c_uint, ctypes.c_int, ul]
    codes = [keycode(dpy, n) for n in names]
    for c in codes:
        xtst.XTestFakeKeyEvent(dpy, c, 1, 0)
    for c in reversed(codes):
        xtst.XTestFakeKeyEvent(dpy, c, 0, 0)
    X.XFlush(dpy)


def window_property(dpy, win, atom):
    """A window property's raw bytes (format 8), or b""."""
    typ, fmt, n, after = ul(), ctypes.c_int(), ul(), ul()
    data = ctypes.POINTER(ctypes.c_ubyte)()
    X.XGetWindowProperty(
        dpy, win, atom, 0, 1 << 20, 0, 0,
        ctypes.byref(typ), ctypes.byref(fmt), ctypes.byref(n), ctypes.byref(after), ctypes.byref(data),
    )  # fmt: skip
    if not data:
        return b""
    out = bytes(data[: n.value])
    X.XFree(data)
    return out
