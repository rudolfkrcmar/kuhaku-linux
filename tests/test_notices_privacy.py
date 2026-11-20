"""Settings' Notices, Privacy and keyboard layouts: what is stored, and what the daemons make of it."""

import json
import time
import unittest
from unittest import mock

import kuhaku_system as ks
from tests.util import TempDirCase, load_file

kn = load_file("kuhaku-notify.py")
kp = load_file("kuhaku-powerd.py")


class Quiet(unittest.TestCase):
    def test_quiet_choices(self):
        now = time.mktime((2026, 11, 20, 22, 0, 0, 0, 0, -1))
        self.assertEqual(ks.quiet_until("off", now), 0)
        self.assertEqual(ks.quiet_until("always", now), -1)
        self.assertEqual(ks.quiet_until("hour", now), int(now + 3600))
        self.assertEqual(time.localtime(ks.quiet_until("morning", now))[2:4], (21, 7))
        early = time.mktime((2026, 11, 20, 5, 0, 0, 0, 0, -1))
        self.assertEqual(time.localtime(ks.quiet_until("morning", early))[2:4], (20, 7))

    def test_who_gets_a_card(self):
        now = 1000
        self.assertTrue(kn.shows({}, "Mail", False, now))
        self.assertFalse(kn.shows({"quiet_until": -1}, "Mail", False, now))
        self.assertFalse(kn.shows({"quiet_until": 2000}, "Mail", False, now))
        self.assertTrue(kn.shows({"quiet_until": 500}, "Mail", False, now))
        self.assertFalse(kn.shows({"muted": ["Mail"]}, "Mail", False, now))
        self.assertTrue(kn.shows({"muted": ["Mail"], "quiet_until": -1}, "Power", True, now))


class Stored(TempDirCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(ks, "DESKTOP", self.tmp / "desktop.json")
        p.start()
        self.addCleanup(p.stop)

    def test_mute_and_unmute(self):
        ks.write_notice("mute", "Mail|1")
        ks.write_notice("mute", "Power|1")
        ks.write_notice("mute", "Mail|0")
        self.assertEqual(ks.read_notice_prefs()["muted"], ["Power"])
        ks.write_notice("previews", "0")
        ks.write_notice("show_s", "99")  # not a choice: ignored
        n = ks.read_notice_prefs()
        self.assertEqual((n["previews"], n["show_s"]), (0, 6))

    def test_eye_off(self):
        self.assertEqual(ks.read_privacy()["eye"], 1)
        ks.write_privacy("eye", "0")
        self.assertEqual(json.loads((self.tmp / "desktop.json").read_text())["privacy"], {"eye": 0})

    def test_layouts(self):
        with mock.patch.object(ks, "run", return_value=(0, "layout:     us\n", "")) as run:
            ks.write_layouts("add", "cz")
            ks.write_layouts("add", "xx")  # unknown: ignored
            self.assertEqual(ks.read_layouts(), ["us", "cz"])
            ks.write_layouts("first", "cz")
            self.assertEqual(ks.read_layouts(), ["cz", "us"])
            ks.write_layouts("remove", "us")
            ks.write_layouts("remove", "cz")  # the last one stays
            self.assertEqual(ks.read_layouts(), ["cz"])
            self.assertIn(
                ["setxkbmap", "-layout", "cz,us", "-option", "", "-option", ks.SWITCH_LAYOUT],
                [c.args[0] for c in run.call_args_list],
            )


class LockWhenOff(unittest.TestCase):
    def power(self, idle, lock_off=1, screen_off=10):
        p = kp.Power.__new__(kp.Power)
        p.cfg = {**kp.DEFAULTS, "lock_off": lock_off, "screen_off": screen_off}
        p.holders, p.locked_idle = {}, False
        p.idle = mock.Mock(seconds=mock.Mock(return_value=idle))
        return p

    def test_locks_once_per_idle_spell(self):
        p = self.power(601)
        with mock.patch.object(kp.subprocess, "Popen") as popen:
            p.lock_when_off()
            p.lock_when_off()
            self.assertEqual(popen.call_count, 1)
            p.idle.seconds.return_value = 3
            p.lock_when_off()
            p.idle.seconds.return_value = 700
            p.lock_when_off()
            self.assertEqual(popen.call_count, 2)

    def test_off_by_default(self):
        with mock.patch.object(kp.subprocess, "Popen") as popen:
            self.power(9999, lock_off=0).lock_when_off()
            self.power(9999, screen_off=0).lock_when_off()
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
