"""While Claude works in the background, the computer does not fall asleep from being idle.

Asks the power manager (org.freedesktop.PowerManagement.Inhibit) to hold off idle sleep;
the hold ends when the work ends, or by itself if the program exits. Closing the lid
still puts the computer to sleep: that is a choice the person made, and the work simply
continues after waking. On a Mac, caffeinate does the same.
"""
import os
import subprocess
import sys
import threading

MAC = sys.platform == "darwin"
if not MAC:
    from gi.repository import Gio, GLib

_lock = threading.Lock()
_count = 0
_cookie = None


def _call(method, args):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    return bus.call_sync("org.freedesktop.PowerManagement", "/org/freedesktop/PowerManagement/Inhibit",
                         "org.freedesktop.PowerManagement.Inhibit", method, args, None, 0, 3000, None)


def hold(why="Claude is working"):
    global _count, _cookie
    with _lock:
        _count += 1
        if _count == 1:
            try:
                if MAC:     # held until released, or until this program ends
                    _cookie = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
                else:
                    _cookie = _call("Inhibit", GLib.Variant("(ss)", ("Kūhaku", why))).unpack()[0]
            except Exception:
                _cookie = None


def release():
    global _count, _cookie
    with _lock:
        _count = max(0, _count - 1)
        if _count == 0 and _cookie is not None:
            try:
                if MAC:
                    _cookie.terminate()
                else:
                    _call("UnInhibit", GLib.Variant("(u)", (_cookie,)))
            except Exception:
                pass
            _cookie = None


class Awake:
    """with Awake(): ...  keeps the computer awake for the block."""
    def __init__(self, why="Claude is working"):
        self.why = why

    def __enter__(self):
        hold(self.why)

    def __exit__(self, *exc):
        release()
