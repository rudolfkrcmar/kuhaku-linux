#!/usr/bin/env python3
"""Kūhaku's keys, and the few settings every app reads from the desktop.

One small program, started by kuhaku-session, doing what xfsettingsd used to:

  Keys       The keys Kūhaku answers from anywhere (volume, brightness, capture, the Fn row,
             Super+Space for apps, the power key), grabbed from X directly. More can be
             added under "keys" in ~/.config/kuhaku/desktop.json ("<Super>e": "miru").
  Apps' look The XSETTINGS every GTK and Qt app asks for: the text size (DPI, chosen in
             Settings), the font and how it is drawn, the theme and icon names.
  Pointers   The touchpad and mouse settings (tap to click, natural scrolling, speed),
             applied to each device as it appears.

desktop.json is read again whenever it changes; Settings writes it.
"""

import ctypes
import json
import os
import select
import shlex
import struct
import subprocess
import sys
import time
from pathlib import Path

from kuhaku_log import report  # faults go to the log instead of vanishing

HERE = Path(__file__).resolve().parent
HOME = Path.home()
CONFIG = HOME / ".config" / "kuhaku" / "desktop.json"
D = str(HERE)

# The keys Kūhaku answers, as xfsettingsd once spelled them. Commands run detached.
KEYS = {
    "<Primary><Alt>BackSpace": f"{D}/kuhaku-watchdog restart",  # the way out if Kūhaku freezes
    "XF86AudioRaiseVolume": f"{D}/kuhaku-volume up",
    "XF86AudioLowerVolume": f"{D}/kuhaku-volume down",
    "XF86AudioMute": f"{D}/kuhaku-volume mute",
    "XF86AudioMicMute": f"{D}/kuhaku-volume mic",
    "XF86MonBrightnessUp": f"{D}/kuhaku-brightness up",
    "XF86MonBrightnessDown": f"{D}/kuhaku-brightness down",
    # The ThinkPad Fn row: F7 display, F8 airplane mode, F9 notices, F10 talk with Claude,
    # F11 lock, F12 (star) the browser. F9 to F11 arrive as Launch1-3
    # (remapped in /etc/udev/hwdb.d/61-kuhaku-thinkpad.hwdb; X cannot see their own codes).
    "<Super>space": f"{D}/kuhaku-key apps",
    "<Super>p": f"{D}/kuhaku-key display",
    "XF86Display": f"{D}/kuhaku-key display",
    "XF86RFKill": f"{D}/kuhaku-airplane",
    "XF86WLAN": f"{D}/kuhaku-airplane",
    "XF86Launch1": f"{D}/kuhaku-key notices",
    "XF86Launch2": f"{D}/kuhaku-key talk",
    "XF86Launch3": f"python3 {D}/kuhaku-lock.py lock",
    "XF86Favorites": f"{HOME}/.local/bin/mado",
    "XF86WWW": f"{HOME}/.local/bin/mado",
    "XF86HomePage": f"{HOME}/.local/bin/mado",
    "XF86Mail": f"{HOME}/.local/bin/tegami",
    "<Super>e": f"{HOME}/.local/bin/miru",
    "<Primary><Alt>t": f"{HOME}/.local/bin/kuhaku-terminal",
    "<Primary><Alt>l": f"python3 {D}/kuhaku-lock.py lock",
    # The power key (and Ctrl+Alt+Delete): Kūhaku's menu, not a log-out dialog.
    "XF86PowerOff": f"{D}/kuhaku-power",
    "<Primary><Alt>Delete": f"{D}/kuhaku-power",
    # The Print key: pick a part of the screen, like Cmd+Shift+4. Shift+Print the whole
    # screen, Ctrl+Print copies the part instead of saving it.
    "Print": f"{D}/kuhaku-capture area",
    "<Shift>Print": f"{D}/kuhaku-capture full",
    "<Primary>Print": f"{D}/kuhaku-capture area-clip",
    "<Alt>Print": f"{D}/kuhaku-capture area",
}
# Mac-style capture: Cmd+Shift+3/4/5 (Cmd is Ctrl on a Mac, see kuhaku-start), Ctrl+Shift and
# Win+Shift on other computers, with Ctrl added to copy instead. Both keysym spellings are
# bound, as the shifted one differs between layouts.
for _key, _act in (("numbersign", "full"), ("3", "full"), ("dollar", "area"), ("4", "area")):
    for _mods in ("<Primary><Shift>", "<Super><Shift>"):
        KEYS[_mods + _key] = f"{D}/kuhaku-capture {_act}"
    KEYS["<Primary><Shift><Super>" + _key] = f"{D}/kuhaku-capture {_act}-clip"
for _key in ("percent", "5"):
    for _mods in ("<Primary><Shift>", "<Super><Shift>"):
        KEYS[_mods + _key] = f"{D}/kuhaku-capture record"
for _key in ("asciicircum", "6"):  # a part of the screen: drag a crosshair, then it records that
    for _mods in ("<Primary><Shift>", "<Super><Shift>"):
        KEYS[_mods + _key] = f"{D}/kuhaku-capture record-area"

# What apps are told. Text size comes from desktop.json ("dpi").
XSETTINGS = {
    "Gdk/WindowScalingFactor": 1,
    "Net/ThemeName": "Adwaita",
    "Net/IconThemeName": "Adwaita",
    "Gtk/FontName": "Inter 10",
    "Gtk/MonospaceFontName": "JetBrains Mono 10",
    "Gtk/CursorThemeName": "",
    "Gtk/CursorThemeSize": 0,
    "Gtk/DecorationLayout": ":",
    "Gtk/DialogsUseHeader": 0,
    "Net/CursorBlink": 1,
    "Net/CursorBlinkTime": 1200,
    "Net/DoubleClickTime": 400,
    "Net/DoubleClickDistance": 5,
    "Net/DndDragThreshold": 8,
    "Net/EnableEventSounds": 0,
    "Net/EnableInputFeedbackSounds": 0,
    # Text drawn as Kūhaku's pages were designed with: no hinting, no colour fringes.
    "Xft/Antialias": -1,  # -1: as fontconfig says
    "Xft/Hinting": -1,
    "Xft/HintStyle": "hintnone",
    "Xft/RGBA": "none",
}

MODS = {"Shift": 1, "Control": 4, "Primary": 4, "Ctrl": 4, "Alt": 8, "Mod1": 8, "Super": 64, "Mod4": 64}
LOCKS = (0, 2, 16, 18)  # Caps Lock and Num Lock must not stop a key


from kuhaku_x11 import (
    CLIENT_MESSAGE,
    ERROR_HANDLER,
    KEY_PRESS,
    MAPPING_NOTIFY,
    SELECTION_CLEAR,
    Event,
    X,
    Xi,
    ul,
    vp,
)


def parse(spec):
    """'<Primary><Shift>4' -> (modifier mask, keysym name)."""
    mask, rest = 0, spec
    while rest.startswith("<") and ">" in rest:
        name, rest = rest[1:].split(">", 1)
        mask |= MODS.get(name, 0)
    return mask, rest


def same(name):
    return "".join(c for c in name.lower() if c.isalnum())


def encode_xsettings(values, serial):
    """The XSETTINGS wire format: a header, then each setting (int or string), 4-byte aligned."""

    def pad(b):
        return b + b"\0" * (-len(b) % 4)

    out = struct.pack("<B3xII", 0, serial, len(values))
    for name, v in sorted(values.items()):
        n = name.encode()
        if isinstance(v, int):
            out += struct.pack("<BxH", 0, len(n)) + pad(n) + struct.pack("<Ii", 0, v)
        else:
            s = str(v).encode()
            out += struct.pack("<BxH", 1, len(n)) + pad(n) + struct.pack("<II", 0, len(s)) + pad(s)
    return out


def load():
    try:
        return json.loads(CONFIG.read_text())
    except FileNotFoundError:
        return migrate()
    except Exception:
        report()
        return {}


def migrate():
    """First start: keep the text size and pointer settings xfsettingsd had (if it ran here)."""
    cfg: dict = {"pointers": {}}

    def q(*args):
        try:
            return subprocess.run(["xfconf-query", *args], capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            return ""  # no xfconf on this computer: nothing to bring along

    dpi = q("-c", "xsettings", "-p", "/Xft/DPI").strip()
    if dpi.isdigit() and int(dpi) > 0:
        cfg["dpi"] = int(dpi)
    for line in q("-c", "pointers", "-lv").splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2 or "/Properties/" not in parts[0]:
            continue
        dev, prop = parts[0].strip("/").split("/Properties/")
        val = parts[1].strip().replace(",", ".")
        try:
            num = float(val) if "." in val else int(val)
        except ValueError:
            continue
        cfg["pointers"].setdefault(dev.replace("_", " "), {})[prop.replace("_", " ")] = num
    try:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")
    except OSError:
        report()
    return cfg


class Keys:
    def __init__(self, selection="_XSETTINGS_S0"):
        # The XSETTINGS selection: screen 0's in use; tests pass another so they never take over.
        self.selection = selection
        self.dpy = X.XOpenDisplay(None)
        if not self.dpy:
            sys.exit("kuhaku-keys: no display")
        # A key another program holds already is skipped, not fatal.
        self.on_error = ERROR_HANDLER(lambda _d, _e: 0)
        X.XSetErrorHandler(self.on_error)
        self.root = X.XDefaultRootWindow(self.dpy)
        self.bound: dict[tuple[int, int], str] = {}
        self.cfg: dict = {}
        self.mtime = None
        self.serial = 0
        self.devices: set = set()
        self.win = X.XCreateSimpleWindow(self.dpy, self.root, -1, -1, 1, 1, 0, 0, 0)
        self.atom = {n: X.XInternAtom(self.dpy, n.encode(), 0) for n in (selection, "_XSETTINGS_SETTINGS", "MANAGER")}
        self.reload()

    # --- desktop.json ---
    def reload(self):
        try:
            m = CONFIG.stat().st_mtime
        except FileNotFoundError:
            m = None
        if m == self.mtime and self.cfg:
            return
        self.mtime = m
        self.cfg = load()
        self.grab()
        self.publish()
        self.devices = set()  # every pointer gets its settings again
        self.pointers()

    # --- keys ---
    def grab(self):
        for (code, mask), _ in self.bound.items():
            for lock in LOCKS:
                X.XUngrabKey(self.dpy, code, mask | lock, self.root)
        self.bound = {}
        for spec, cmd in {**KEYS, **(self.cfg.get("keys") or {})}.items():
            mask, name = parse(spec)
            sym = X.XStringToKeysym(name.encode())
            code = X.XKeysymToKeycode(self.dpy, sym) if sym else 0
            if not code or not cmd:
                continue
            self.bound[(code, mask)] = cmd
            for lock in LOCKS:
                X.XGrabKey(self.dpy, code, mask | lock, self.root, 0, 1, 1)  # GrabModeAsync
        X.XSync(self.dpy, 0)

    def pressed(self, ev):
        mask = ev.state & (1 | 4 | 8 | 64)
        cmd = self.bound.get((ev.keycode, mask))
        if cmd:
            try:
                subprocess.Popen(
                    ["setsid", "-f", *shlex.split(cmd)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).wait(timeout=5)
            except Exception:
                report()

    # --- XSETTINGS ---
    def publish(self):
        values = dict(XSETTINGS)
        dpi = self.cfg.get("dpi")
        if isinstance(dpi, int) and dpi > 0:
            values["Xft/DPI"] = dpi * 1024
        self.serial += 1
        data = encode_xsettings(values, self.serial)
        a = self.atom
        X.XChangeProperty(self.dpy, self.win, a["_XSETTINGS_SETTINGS"], a["_XSETTINGS_SETTINGS"], 8, 0, data, len(data))
        if X.XGetSelectionOwner(self.dpy, a[self.selection]) != self.win:
            X.XSetSelectionOwner(self.dpy, a[self.selection], self.win, 0)
            ev = Event()
            ev.client.type = CLIENT_MESSAGE
            ev.client.window = self.root
            ev.client.message_type = a["MANAGER"]
            ev.client.format = 32
            ev.client.data[:] = [0, a[self.selection], self.win, 0, 0]
            X.XSendEvent(self.dpy, self.root, 0, 1 << 17, ctypes.byref(ev))  # StructureNotifyMask
        X.XFlush(self.dpy)

    # --- pointers ---
    def pointers(self):
        n = ctypes.c_int()
        devs = Xi.XIQueryDevice(self.dpy, 0, ctypes.byref(n))  # every device
        if not devs:
            return
        try:
            now = {(devs[i].deviceid, (devs[i].name or b"").decode(errors="ignore")) for i in range(n.value)}
            for did, name in now - self.devices:
                self.apply(did, name)
            self.devices = now
        finally:
            Xi.XIFreeDeviceInfo(devs)
        X.XFlush(self.dpy)

    def apply(self, did, name):
        # Names compared by their letters and digits: older settings wrote "TPPS2 Elan TrackPoint"
        # for "TPPS/2 Elan TrackPoint".
        key = same(name)
        wanted = {}
        for dev, props in (self.cfg.get("pointers") or {}).items():
            if same(dev) == key and isinstance(props, dict):
                wanted.update(props)
        if "touchpad" in name.lower():
            wanted.setdefault("libinput Tapping Enabled", 1)  # tap to click, as people expect
        for prop, value in wanted.items():
            atom = X.XInternAtom(self.dpy, prop.encode(), 1)
            if not atom:
                continue
            typ, fmt, items, after, data = ul(), ctypes.c_int(), ul(), ul(), vp()
            got = Xi.XIGetProperty(
                self.dpy, did, atom, 0, 1, 0, 0,
                ctypes.byref(typ), ctypes.byref(fmt), ctypes.byref(items), ctypes.byref(after), ctypes.byref(data),
            )  # fmt: skip
            if data.value:
                X.XFree(data)
            if got != 0 or not typ.value:
                continue  # this device has no such setting
            if fmt.value == 8:
                buf: ctypes.Array = (ctypes.c_ubyte * 1)(int(value))
            elif fmt.value == 32 and isinstance(value, float):
                buf = (ctypes.c_float * 1)(value)
            elif fmt.value == 32:
                buf = (ctypes.c_int32 * 1)(int(value))
            else:
                continue
            Xi.XIChangeProperty(self.dpy, did, atom, typ.value, fmt.value, 0, buf, 1)

    # --- the loop ---
    def run(self):
        fd = X.XConnectionNumber(self.dpy)
        ev = Event()
        last = 0.0
        while True:
            while X.XPending(self.dpy):
                X.XNextEvent(self.dpy, ctypes.byref(ev))
                if ev.type == KEY_PRESS:
                    self.pressed(ev.key)
                elif ev.type == MAPPING_NOTIFY:  # another keyboard layout: the keys move
                    X.XRefreshKeyboardMapping(ctypes.byref(ev))
                    self.grab()
                elif ev.type == SELECTION_CLEAR:
                    sys.exit("kuhaku-keys: another settings manager took over")
            select.select([fd], [], [], 2)
            if time.monotonic() - last >= 2:
                last = time.monotonic()
                self.reload()
                self.pointers()


def main():
    if "--help" in sys.argv:
        print(__doc__)
        return
    if "--print" in sys.argv:  # what would be bound, for a look
        for k, v in KEYS.items():
            print(f"{k:32} {v}")
        return
    os.environ.setdefault("DISPLAY", ":0")
    Keys().run()


if __name__ == "__main__":
    main()
