#!/usr/bin/env python3
"""Kūhaku's power: the screen, sleep, the lid and the battery, in place of xfce4-power-manager.

  Screen off  after the minutes chosen in Settings without a touch (X's own DPMS), unless an
              app asks it to stay on (a video playing, through org.freedesktop.ScreenSaver).
  Sleep       after the minutes chosen without a touch, unless something holds it off: Claude
              working (keepawake.py, through org.freedesktop.PowerManagement.Inhibit), a video.
  The lid     Sleep (the system does it), Lock, or Screen off, as chosen. While Claude works the
              lid only turns the screen off; if it is still closed when the work ends, it sleeps.
  Power key   left to Kūhaku's menu (kuhaku-keys.py), never the system's instant power off.
  Battery     a quiet card when it runs low, and sleep before it runs out.

The choices live under "power" in ~/.config/kuhaku/desktop.json; Settings writes them.
"""

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from gi.repository import Gio, GLib

from kuhaku_log import report  # faults go to the log instead of vanishing

CONFIG = Path.home() / ".config" / "kuhaku" / "desktop.json"
LID_SAVED = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "kuhaku" / "lid" / "saved"
DEFAULTS = {"screen_off": 10, "sleep_after": 0, "lid": 1}  # minutes, minutes (0 never), 1 sleep / 3 lock / 0 screen off
LOW, CRITICAL = 10, 4  # battery percent: a card, then sleep
LOGIN1 = "org.freedesktop.login1"
UPOWER = "org.freedesktop.UPower"

PM_XML = """<node><interface name="org.freedesktop.PowerManagement.Inhibit">
  <method name="Inhibit"><arg direction="in" type="s"/><arg direction="in" type="s"/><arg direction="out" type="u"/></method>
  <method name="UnInhibit"><arg direction="in" type="u"/></method>
  <method name="HasInhibit"><arg direction="out" type="b"/></method>
  <signal name="HasInhibitChanged"><arg type="b"/></signal>
</interface></node>"""
SS_XML = """<node><interface name="org.freedesktop.ScreenSaver">
  <method name="Inhibit"><arg direction="in" type="s"/><arg direction="in" type="s"/><arg direction="out" type="u"/></method>
  <method name="UnInhibit"><arg direction="in" type="u"/></method>
  <method name="SimulateUserActivity"/>
  <method name="GetActive"><arg direction="out" type="b"/></method>
</interface></node>"""


# --- how long since the last touch (the X screensaver extension) -----------------------
class XssInfo(ctypes.Structure):
    _fields_ = [
        ("window", ctypes.c_ulong),
        ("state", ctypes.c_int),
        ("kind", ctypes.c_int),
        ("til_or_since", ctypes.c_ulong),
        ("idle", ctypes.c_ulong),
        ("eventMask", ctypes.c_ulong),
    ]


class Idle:
    def __init__(self):
        self.x = ctypes.CDLL("libX11.so.6")
        self.xss = ctypes.CDLL("libXss.so.1")
        self.x.XOpenDisplay.restype = ctypes.c_void_p
        self.x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x.XDefaultRootWindow.restype = ctypes.c_ulong
        self.x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(XssInfo)
        self.xss.XScreenSaverQueryInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(XssInfo)]
        self.dpy = self.x.XOpenDisplay(None)
        if not self.dpy:
            sys.exit("kuhaku-powerd: no display")
        self.root = self.x.XDefaultRootWindow(self.dpy)
        self.info = self.xss.XScreenSaverAllocInfo()

    def seconds(self):
        self.xss.XScreenSaverQueryInfo(self.dpy, self.root, self.info)
        return self.info.contents.idle / 1000


def xset(*args):
    try:
        subprocess.run(["xset", *args], timeout=5, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        report()


def notify(title, body):
    try:
        subprocess.Popen(["notify-send", "-a", "Power", "-u", "critical", title, body])
    except OSError:
        report()


def load():
    try:
        cfg = json.loads(CONFIG.read_text())
    except FileNotFoundError:
        cfg = {}  # kuhaku-keys.py writes it at its first start
    except Exception:
        report()
        cfg = {}
    if "power" not in cfg:
        cfg["power"] = migrate()
        try:
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")
        except OSError:
            report()
    return {**DEFAULTS, **(cfg.get("power") or {})}


def migrate():
    """First start: the choices xfce4-power-manager kept (the lid as the person chose it,
    not as it was set while Claude worked)."""
    out = dict(DEFAULTS)

    def q(prop):
        try:
            r = subprocess.run(
                ["xfconf-query", "-c", "xfce4-power-manager", "-p", "/xfce4-power-manager/" + prop],
                capture_output=True,
                text=True,
                timeout=3,
            )
            return int(r.stdout.strip()) if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None  # no power manager here before: the defaults stand

    for key, prop in (("screen_off", "dpms-on-battery-off"), ("sleep_after", "inactivity-on-battery")):
        v = q(prop)
        if v is not None:
            out[key] = v if key == "screen_off" or v > 14 else 0
    try:
        out["lid"] = int(LID_SAVED.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        v = q("lid-action-on-battery")
        if v is not None:
            out["lid"] = v
    return out


class Power:
    def __init__(self):
        self.idle = Idle()
        self.cfg = load()
        self.mtime = self.config_mtime()
        self.holders: dict[int, tuple[str, str]] = {}  # cookie -> (sender, "sleep" or "screen")
        self.next_cookie = 1
        self.lid_fd = None
        self.key_fd = None
        self.slept_at = -1e9
        self.warned = False
        self.system = Gio.bus_get_sync(Gio.BusType.SYSTEM)
        self.session = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.key_fd = self.inhibit("handle-power-key", "Kūhaku's power menu")
        self.apply()
        self.serve("org.freedesktop.PowerManagement", ["/org/freedesktop/PowerManagement/Inhibit"], PM_XML, "sleep")
        self.serve("org.freedesktop.ScreenSaver", ["/org/freedesktop/ScreenSaver", "/ScreenSaver"], SS_XML, "screen")
        self.session.signal_subscribe(
            "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged", None, None, 0, self.gone
        )
        self.system.signal_subscribe(
            UPOWER, "org.freedesktop.DBus.Properties", "PropertiesChanged", "/org/freedesktop/UPower", None, 0,
            lambda *_: self.on_lid(),
        )  # fmt: skip
        self.lid_was = self.lid_closed()
        GLib.timeout_add_seconds(15, self.tick)

    # --- the system: logind and UPower ---
    def inhibit(self, what, why):
        try:
            r, fds = self.system.call_with_unix_fd_list_sync(
                LOGIN1, "/org/freedesktop/login1", f"{LOGIN1}.Manager", "Inhibit",
                GLib.Variant("(ssss)", (what, "Kūhaku", why, "block")), GLib.VariantType("(h)"),
                Gio.DBusCallFlags.NONE, -1, None, None,
            )  # fmt: skip
            return fds.get(r.unpack()[0])
        except Exception:
            report()
            return None

    def login1(self, method, *args):
        try:
            sig = "(" + "b" * len(args) + ")"
            self.system.call_sync(
                LOGIN1, "/org/freedesktop/login1", f"{LOGIN1}.Manager", method,
                GLib.Variant(sig, args) if args else None, None, Gio.DBusCallFlags.NONE, 10000, None,
            )  # fmt: skip
        except Exception:
            report()

    def can(self, what):
        try:
            r = self.system.call_sync(
                LOGIN1, "/org/freedesktop/login1", f"{LOGIN1}.Manager", "Can" + what, None,
                GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 5000, None,
            )  # fmt: skip
            return r.unpack()[0] == "yes"
        except Exception:
            report()
            return False

    def sleep(self):
        self.slept_at = time.monotonic()
        self.login1("Suspend", False)

    def upower(self, path, prop, default=None):
        try:
            r = self.system.call_sync(
                UPOWER, path, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (UPOWER if path == "/org/freedesktop/UPower" else f"{UPOWER}.Device", prop)),
                GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 5000, None,
            )  # fmt: skip
            return r.unpack()[0]
        except GLib.Error:
            return default  # no UPower or no such device (a desktop without a battery)

    def lid_closed(self):
        return bool(self.upower("/org/freedesktop/UPower", "LidIsClosed", False))

    # --- what holds things off ---
    def held(self, kind=None):
        return any(k == kind or kind is None for _, k in self.holders.values())

    def serve(self, name, paths, xml, kind):
        iface = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
        for path in paths:
            self.session.register_object(path, iface, lambda *a, kind=kind: self.call(kind, *a), None, None)
        Gio.bus_own_name_on_connection(self.session, name, Gio.BusNameOwnerFlags.REPLACE, None, None)

    def call(self, kind, _conn, sender, _path, _iface, method, params, inv):
        try:
            if method == "Inhibit":
                cookie = self.next_cookie
                self.next_cookie += 1
                self.holders[cookie] = (sender, kind)
                inv.return_value(GLib.Variant("(u)", (cookie,)))
                self.apply()
            elif method == "UnInhibit":
                self.holders.pop(params.unpack()[0], None)
                inv.return_value(None)
                self.apply()
                self.after_work()
            elif method == "HasInhibit":
                inv.return_value(GLib.Variant("(b)", (self.held(),)))
            elif method == "GetActive":
                inv.return_value(GLib.Variant("(b)", (False,)))
            elif method == "SimulateUserActivity":
                xset("dpms", "force", "on")
                inv.return_value(None)
            else:
                inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
        except Exception as e:
            report()
            inv.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(e))

    def gone(self, _c, _s, _p, _i, _sig, params):
        """A program that held something off ended without letting go: its holds end too."""
        name, _old, new = params.unpack()
        if new or not name.startswith(":"):
            return
        mine = [c for c, (s, _k) in self.holders.items() if s == name]
        for c in mine:
            self.holders.pop(c)
        if mine:
            self.apply()
            self.after_work()

    # --- applying the choices ---
    def apply(self):
        c = self.cfg
        off = int(c.get("screen_off") or 0) * 60
        if off and not self.held("screen"):
            xset("s", "off")
            xset("dpms", str(off), str(off), str(off))
            xset("+dpms")
        else:
            xset("s", "off")
            xset("-dpms")
        # The lid: the system sleeps on its own, unless Kūhaku answers it (lock, screen off, or
        # Claude still working).
        want_lid = int(c.get("lid", 1)) != 1 or self.held("sleep")
        if want_lid and self.lid_fd is None:
            self.lid_fd = self.inhibit("handle-lid-switch", "The lid as chosen in Kūhaku")
        elif not want_lid and self.lid_fd is not None:
            os.close(self.lid_fd)
            self.lid_fd = None

    def on_lid(self):
        closed = self.lid_closed()
        if closed == self.lid_was:
            return
        self.lid_was = closed
        if not closed:
            xset("dpms", "force", "on")
            return
        if self.lid_fd is None:
            return  # the system puts it to sleep
        if int(self.cfg.get("lid", 1)) == 3:
            subprocess.Popen(["loginctl", "lock-session"])
        xset("dpms", "force", "off")

    def after_work(self):
        """The work that kept the lid from sleeping ended: if the lid is shut and sleep is the
        choice, sleep now."""
        if not self.held("sleep") and int(self.cfg.get("lid", 1)) == 1 and self.lid_closed():
            GLib.timeout_add_seconds(
                3, lambda: (self.lid_closed() and not self.held("sleep") and self.sleep(), False)[1]
            )

    def config_mtime(self):
        try:
            return CONFIG.stat().st_mtime
        except FileNotFoundError:
            return None

    def tick(self):
        try:
            m = self.config_mtime()
            if m != self.mtime:
                self.mtime, self.cfg = m, load()
                self.apply()
            after = int(self.cfg.get("sleep_after") or 0) * 60
            now = time.monotonic()
            if after and not self.held() and self.idle.seconds() >= after and now - self.slept_at > after:
                self.sleep()
            self.battery()
        except Exception:
            report()
        return True

    def battery(self):
        dev = "/org/freedesktop/UPower/devices/DisplayDevice"
        if not self.upower(dev, "IsPresent", False):
            return
        pct = self.upower(dev, "Percentage", 100.0)
        discharging = self.upower(dev, "State", 0) == 2
        if not discharging or pct > LOW:
            self.warned = False
            return
        if pct <= CRITICAL and time.monotonic() - self.slept_at > 120:
            notify("Battery empty", "Going to sleep now. Plug in to carry on.")
            if self.can("Hibernate"):
                self.slept_at = time.monotonic()
                self.login1("Hibernate", False)
            else:
                self.sleep()
        elif not self.warned:
            self.warned = True
            notify("Battery low", f"{round(pct)}% left. Plug in soon.")


def main():
    if "--help" in sys.argv:
        print(__doc__)
        return
    Power()
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
