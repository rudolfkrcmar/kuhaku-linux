"""Kūhaku's system services against a real X server and session bus (CI runs them under Xvfb).

They never take over the live computer's jobs: keys use a combination nobody uses, XSETTINGS
another selection, notifications another bus name, and no card is put on screen.
"""

import json
import os
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.util import load_file

HAVE_X = bool(os.environ.get("DISPLAY"))
if HAVE_X:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gio, GLib, Gtk

    import kuhaku_x11 as x11

    kk = load_file("kuhaku-keys.py")
    kn = load_file("kuhaku-notify.py")
    kp = load_file("kuhaku-powerd.py")


def decode_xsettings(blob):
    _order, _serial, count = struct.unpack_from("<B3xII", blob, 0)
    at, out = 12, {}
    for _ in range(count):
        typ, n = struct.unpack_from("<BxH", blob, at)
        at += 4
        name = blob[at : at + n].decode()
        at += n + (-n % 4) + 4
        if typ == 0:
            out[name] = struct.unpack_from("<i", blob, at)[0]
            at += 4
        else:
            ln = struct.unpack_from("<I", blob, at)[0]
            at += 4
            out[name] = blob[at : at + ln].decode()
            at += ln + (-ln % 4)
    return out


def wait_for(cond, seconds=4.0, spin=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if spin:
            spin()
        if cond():
            return True
        time.sleep(0.02)
    return False


@unittest.skipUnless(HAVE_X, "needs an X display")
class Keys(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kuhaku-x11-"))
        self.hit = self.tmp / "hit"
        cfg = self.tmp / "desktop.json"
        cfg.write_text(json.dumps({"dpi": 120, "keys": {"<Primary><Alt><Shift>F12": f"touch {self.hit}"}}))
        for patch in (
            mock.patch.object(kk, "CONFIG", cfg),
            mock.patch.object(kk, "KEYS", {}),
            mock.patch.object(kk.Keys, "pointers", lambda self: None),  # the real touchpad stays as it is
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.keys = kk.Keys(selection="_XSETTINGS_S_KUHAKU_TEST")

    def test_a_grabbed_key_runs_its_command(self):
        threading.Thread(target=self.keys.run, daemon=True).start()
        dpy = x11.open_display()
        time.sleep(0.3)
        x11.press(dpy, "Control_L", "Alt_L", "Shift_L", "F12")
        self.assertTrue(wait_for(self.hit.exists), "the key's command did not run")

    def test_apps_are_told_the_text_size_and_font(self):
        atom = x11.X.XInternAtom(self.keys.dpy, b"_XSETTINGS_SETTINGS", 0)
        dpy = x11.open_display()
        sel = x11.X.XInternAtom(dpy, b"_XSETTINGS_S_KUHAKU_TEST", 0)
        owner = x11.X.XGetSelectionOwner(dpy, sel)
        self.assertEqual(owner, self.keys.win)
        got = decode_xsettings(x11.window_property(dpy, owner, atom))
        self.assertEqual(got["Xft/DPI"], 120 * 1024)
        self.assertEqual(got["Gtk/FontName"], "Inter 10")


@unittest.skipUnless(HAVE_X, "needs an X display")
class Idle(unittest.TestCase):
    def test_a_key_press_resets_the_idle_time(self):
        idle = kp.Idle()
        x11.press(x11.open_display(), "Shift_L")
        time.sleep(0.2)
        self.assertLess(idle.seconds(), 2)


@unittest.skipUnless(HAVE_X, "needs an X display and a session bus")
class Notifications(unittest.TestCase):
    def setUp(self):
        self.name = f"org.kuhaku.TestNotifications.p{os.getpid()}.{self._testMethodName}"
        self.log = Path(tempfile.mkdtemp(prefix="kuhaku-notices-")) / "notices.sqlite"
        for patch in (
            mock.patch.object(kn, "LOG", self.log),
            mock.patch.object(Gtk.Window, "show_all", lambda self: None),  # nothing on screen
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.server = kn.Server(name=self.name)
        self.addCleanup(self.server.stop)
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.spin = lambda: GLib.MainContext.default().iteration(False)
        self.assertTrue(wait_for(lambda: self.server.conn is not None, spin=self.spin), "no bus name")

    def call(self, method, args, reply):
        box = []
        self.bus.call(
            self.name, "/org/freedesktop/Notifications", "org.freedesktop.Notifications", method, args,
            GLib.VariantType(reply) if reply else None, Gio.DBusCallFlags.NONE, 3000, None,
            lambda b, r: box.append(b.call_finish(r)),
        )  # fmt: skip
        self.assertTrue(wait_for(lambda: box, spin=self.spin), method + " got no answer")
        return box[0].unpack() if reply else None

    def test_server_information(self):
        self.assertEqual(self.call("GetServerInformation", None, "(ssss)")[0], "Kūhaku")
        self.assertIn("actions", self.call("GetCapabilities", None, "(as)")[0])

    def test_a_notice_shows_is_kept_and_closes(self):
        args = GLib.Variant(
            "(susssasa{sv}i)", ("Tegami", 0, "", "New mail", "Hello <b>there</b>", ["default", "Open"], {}, -1)
        )
        nid = self.call("Notify", args, "(u)")[0]
        self.assertIn(nid, self.server.cards)
        import sqlite3

        rows = sqlite3.connect(str(self.log)).execute("SELECT app_name, summary, body FROM notifications").fetchall()
        self.assertEqual(rows, [("Tegami", "New mail", "Hello there")])
        self.call("CloseNotification", GLib.Variant("(u)", (nid,)), None)
        self.assertNotIn(nid, self.server.cards)

    def test_replacing_keeps_the_number_and_the_list_short(self):
        first = self.call("Notify", GLib.Variant("(susssasa{sv}i)", ("Kiroku", 0, "", "Saving", "", [], {}, -1)), "(u)")
        again = self.call(
            "Notify", GLib.Variant("(susssasa{sv}i)", ("Kiroku", first[0], "", "Saved", "", [], {}, -1)), "(u)"
        )
        self.assertEqual(first, again)
        self.assertEqual(len(self.server.cards), 1)
